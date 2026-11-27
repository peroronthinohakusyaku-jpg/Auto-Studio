from __future__ import annotations

import json
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget,
)
from goose_client import DEFAULT_GOOSE_URL, GOOSE_MODEL, GooseAutoConnectWorker, GooseWorker
from chatgpt_bridge import ChatGPTBridge


APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "studio_state.json"


class ClaudeWorker(QThread):
    output = Signal(str)
    done = Signal(int)

    def __init__(self, cwd: Path, prompt: str, mode: str, session_id: str | None):
        super().__init__()
        self.cwd, self.prompt, self.mode, self.session_id = cwd, prompt, mode, session_id
        self.proc: subprocess.Popen[str] | None = None

    def run(self) -> None:
        claude = shutil.which("claude")
        if not claude:
            self.output.emit("Claude Code CLIが見つかりません。インストール後、`claude` がPATHにあることを確認してください。")
            self.done.emit(127)
            return
        args = [claude, "-p", self.prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", self.mode]
        if self.session_id:
            args += ["--resume", self.session_id]
        try:
            self.proc = subprocess.Popen(
                args, cwd=str(self.cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._consume(line)
            self.done.emit(self.proc.wait())
        except Exception as exc:
            self.output.emit(f"起動エラー: {exc}")
            self.done.emit(1)

    def _consume(self, line: str) -> None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                self.output.emit(line.rstrip())
            return
        kind = data.get("type")
        if kind == "system" and data.get("subtype") == "init":
            sid = data.get("session_id")
            if sid:
                self.output.emit("\x00SESSION:" + sid)
        elif kind == "assistant":
            message = data.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    self.output.emit(block["text"])
                elif block.get("type") == "tool_use":
                    self.output.emit(f"[実行] {block.get('name', 'tool')}")
        elif kind == "result":
            sid = data.get("session_id")
            if sid:
                self.output.emit("\x00SESSION:" + sid)
            if data.get("is_error"):
                self.output.emit("\n[エラー] " + str(data.get("result", "Claude Codeでエラーが発生しました")))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class PromptBox(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Claude Auto Studio")
        self.resize(1320, 850)
        self.project: Path | None = None
        self.session_id: str | None = None
        self.worker: ClaudeWorker | None = None
        self.goose_worker: GooseWorker | None = None
        self.goose_connect_worker: GooseAutoConnectWorker | None = None
        self.backend = "claude"
        self.goose_token = ""
        self.goose_session_id: str | None = None
        self.claude_logged_in = False
        self.chatgpt_task_pending = False
        self.chatgpt_task_in_flight = False
        self.chatgpt_result_text: list[str] = []
        self.chatgpt_bridge = None
        self.chatgpt_bridge_error = ""
        self.login_process: subprocess.Popen | None = None
        self._build_ui()
        self._load_state()
        self.auto_handoff.setChecked(self.auto_handoff_enabled)
        self._style()
        try:
            self.chatgpt_bridge = ChatGPTBridge()
            self.chatgpt_bridge.set_token_hash(self.chatgpt_pairing_hash)
            self.chatgpt_bridge.set_auto_enabled(self.auto_handoff_enabled)
            self.chatgpt_bridge.start()
        except OSError as exc:
            self.chatgpt_bridge_error = str(exc)
        if self.project and self.project.exists():
            self._show_project()
        self.handoff_timer = QTimer(self)
        self.handoff_timer.timeout.connect(self._poll_chatgpt_handoff)
        self.handoff_timer.start(500)
        QTimer.singleShot(300, self._check_cli)
        QTimer.singleShot(900, self.auto_connect_goose)

    def _build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        brand = QLabel("◈  CLAUDE AUTO STUDIO")
        brand.setObjectName("brand")
        self.project_label = QLabel("プロジェクトを選択してください")
        self.project_label.setObjectName("projectLabel")
        self.mode = QComboBox()
        self.mode.addItem("Auto", "auto")
        self.mode.addItem("Ask", "default")
        self.mode.addItem("Plan", "plan")
        self.mode.setToolTip("Claude Code CLIに渡す権限モード")
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Claude Code", "claude")
        self.backend_combo.addItem("Goose · Qwen 2.5 Coder 7B", "goose")
        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        self.goose_button = QPushButton("Goose接続設定")
        self.goose_button.clicked.connect(self._goose_button_clicked)
        self.open_button = QPushButton("プロジェクトを開く")
        self.open_button.clicked.connect(self.open_project)
        self.new_button = QPushButton("新規会話")
        self.new_button.clicked.connect(self.new_session)
        self.login_button = QPushButton("Claudeにログイン")
        self.login_button.clicked.connect(self.start_login)
        self.chatgpt_button = QPushButton("ChatGPT Web連携")
        self.chatgpt_button.clicked.connect(self.configure_chatgpt_bridge)
        self.auto_handoff = QCheckBox("ChatGPT自動往復")
        self.auto_handoff.setToolTip("専用マーカー付きのChatGPT指示をGooseで処理し、結果を同じChatGPTタブへ送信します。")
        self.auto_handoff.toggled.connect(self._set_auto_handoff_enabled)
        header.addWidget(brand)
        header.addSpacing(20)
        header.addWidget(self.project_label, 1)
        header.addWidget(QLabel("モード"))
        header.addWidget(self.mode)
        header.addWidget(self.backend_combo)
        header.addWidget(self.goose_button)
        header.addWidget(self.login_button)
        header.addWidget(self.chatgpt_button)
        header.addWidget(self.auto_handoff)
        header.addWidget(self.new_button)
        header.addWidget(self.open_button)
        outer.addLayout(header)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._sidebar())
        split.addWidget(self._chat_panel())
        split.addWidget(self._diff_panel())
        split.setSizes([235, 760, 325])
        outer.addWidget(split, 1)
        self.setCentralWidget(root)

    def _sidebar(self):
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        title = QLabel("PROJECT FILES")
        title.setObjectName("sectionLabel")
        self.files = QListWidget()
        self.files.itemDoubleClicked.connect(self._open_file)
        hint = QLabel("ファイルをダブルクリックして開く")
        hint.setObjectName("hint")
        layout.addWidget(title)
        layout.addWidget(self.files, 1)
        layout.addWidget(hint)
        return panel

    def _chat_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 16)
        top = QHBoxLayout()
        title = QLabel("作業チャット")
        title.setObjectName("panelTitle")
        self.status = QLabel("待機中")
        self.status.setObjectName("status")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.status)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setObjectName("transcript")
        self.transcript.setPlaceholderText("プロジェクトを選び、Claudeへの依頼を入力してください。")
        self.prompt = PromptBox()
        self.prompt.setObjectName("prompt")
        self.prompt.setPlaceholderText("例：このプロジェクトの構成を調べ、ログイン画面の入力検証を改善してください。\nCtrl+Enterで送信")
        self.prompt.setFixedHeight(115)
        self.prompt.submit.connect(self.send_prompt)
        actions = QHBoxLayout()
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_work)
        self.send_button = QPushButton("指示を送信  ↗")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_prompt)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        actions.addWidget(self.send_button)
        layout.addLayout(top)
        layout.addWidget(self.transcript, 1)
        layout.addWidget(self.prompt)
        layout.addLayout(actions)
        return panel

    def _diff_panel(self):
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        top = QHBoxLayout()
        title = QLabel("CHANGES")
        title.setObjectName("sectionLabel")
        refresh = QPushButton("更新")
        refresh.clicked.connect(self.refresh_diff)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(refresh)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setObjectName("diff")
        self.diff.setPlaceholderText("Gitの変更差分がここに表示されます")
        layout.addLayout(top)
        layout.addWidget(self.diff, 1)
        return panel

    def _style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101113; color: #e9e7e4; font-family: 'Segoe UI'; font-size: 10pt; }
            #brand { color: #f3a778; font-size: 12pt; font-weight: 700; letter-spacing: 1px; padding: 13px 8px; }
            #projectLabel { color: #b6b2ad; }
            #sidePanel { background: #151619; border-left: 1px solid #28292d; border-right: 1px solid #28292d; }
            #sectionLabel { color: #a8a5a0; font-size: 9pt; font-weight: 700; letter-spacing: 1px; }
            #panelTitle { font-size: 13pt; font-weight: 600; }
            #hint { color: #777; font-size: 9pt; }
            #status { color: #a8a5a0; }
            QPlainTextEdit, QListWidget, QLineEdit, QComboBox { background: #1a1b1f; border: 1px solid #303136; border-radius: 7px; padding: 9px; selection-background-color: #75472f; }
            #transcript { font-size: 10.5pt; padding: 15px; }
            #prompt { background: #1a1b1f; font-size: 10.5pt; }
            #diff { font-family: Consolas, monospace; font-size: 9pt; }
            QPushButton { background: #242529; border: 1px solid #38393e; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #303136; }
            QPushButton:disabled { color: #65656a; }
            #sendButton { background: #d7865a; border-color: #d7865a; color: #171311; font-weight: 700; padding: 9px 18px; }
            #sendButton:hover { background: #e69b70; }
            QComboBox { min-width: 90px; }
        """)

    def _check_cli(self):
        cli = shutil.which("claude")
        if not cli:
            self.claude_logged_in = False
            self.status.setText("CLI未検出")
            self.login_button.setEnabled(False)
            return
        try:
            result = subprocess.run([cli, "auth", "status", "--text"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=8,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0:
                self.claude_logged_in = True
                self.status.setText("Claude Code ログイン済み")
                self.login_button.setText("アカウント")
            else:
                self.claude_logged_in = False
                self.status.setText("Claude Code 未ログイン")
                self.login_button.setText("Claudeにログイン")
        except (OSError, subprocess.TimeoutExpired):
            self.claude_logged_in = False
            self.status.setText("CLIの状態を確認できません")

    def _backend_changed(self):
        self.backend = self.backend_combo.currentData()
        self.mode.setEnabled(self.backend == "claude")
        self.login_button.setVisible(self.backend == "claude")
        self.goose_button.setVisible(self.backend == "goose")
        if self.backend == "goose":
            self.status.setText(f"Goose Controller接続済み / {GOOSE_MODEL}" if self.goose_token else "Goose Controller接続を待機中")
        else:
            self.status.setText("Claude Code")

    def auto_connect_goose(self):
        if self.goose_connect_worker and self.goose_connect_worker.isRunning():
            return
        self.goose_button.setText("Goose自動接続中…")
        self.goose_button.setEnabled(False)
        self.goose_connect_worker = GooseAutoConnectWorker(self)
        self.goose_connect_worker.connected.connect(self._on_goose_connected)
        self.goose_connect_worker.failed.connect(self._on_goose_connect_failed)
        self.goose_connect_worker.finished.connect(lambda: self.goose_button.setEnabled(True))
        self.goose_connect_worker.start()

    def _goose_button_clicked(self):
        if self.goose_token:
            self.configure_goose()
        else:
            self.auto_connect_goose()

    def _on_goose_connected(self, url, client_id, token, tools):
        self.goose_url = url
        self.goose_client_id = client_id
        self.goose_token = token
        self.goose_button.setText("Goose接続済み")
        self.status.setText(f"Goose Controller接続済み / {GOOSE_MODEL}（{len(tools)} tools）")
        if not self.claude_logged_in:
            self.backend_combo.setCurrentIndex(1)
        self._save_state()
        if self.auto_handoff_enabled and self.chatgpt_task_pending:
            QTimer.singleShot(100, self._dispatch_chatgpt_task)

    def _on_goose_connect_failed(self, message):
        self.goose_button.setText("Goose再接続")
        if not self.claude_logged_in:
            self.backend_combo.setCurrentIndex(1)
        self.status.setText(message)

    def configure_chatgpt_bridge(self):
        if not self.chatgpt_bridge:
            QMessageBox.warning(self, "連携サーバーを起動できません", "localhost:8769を使用できません。ほかのAuto Studioが起動していないか確認してください。")
            return
        code = secrets.token_urlsafe(32)
        self.chatgpt_pairing_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        self.chatgpt_bridge.set_token_hash(self.chatgpt_pairing_hash)
        self._save_state()
        dialog = QDialog(self)
        dialog.setWindowTitle("ChatGPT Web連携")
        dialog.setMinimumWidth(600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("1. Chrome/Edgeで拡張機能ページを開き、デベロッパーモードから『パッケージ化されていない拡張機能を読み込む』を選びます。"))
        folder = QLineEdit(str(APP_DIR / "chatgpt_extension"))
        folder.setReadOnly(True)
        layout.addWidget(folder)
        layout.addWidget(QLabel("2. 拡張機能の詳細 → 拡張機能のオプションを開き、以下のコードを登録します。"))
        code_input = QLineEdit(code)
        code_input.setReadOnly(True)
        code_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(code_input)
        buttons_row = QHBoxLayout()
        copy_button = QPushButton("ペアリングコードをコピー")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(code))
        done = QPushButton("閉じる")
        done.clicked.connect(dialog.accept)
        buttons_row.addWidget(copy_button)
        buttons_row.addStretch()
        buttons_row.addWidget(done)
        layout.addLayout(buttons_row)
        note = QLabel("通常は回答のボタンから手動で渡します。自動往復を使う場合は『ChatGPT自動往復』を有効にし、ChatGPTに [AUTO_STUDIO_TASK]...[/AUTO_STUDIO_TASK] の形式で指示を出すよう依頼してください。Gooseの結果は同じChatGPTタブへ送信されます。コードを再発行すると古い拡張機能コードは無効になります。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)
        dialog.exec()

    def _poll_chatgpt_handoff(self):
        if (not self.chatgpt_bridge or self.prompt.toPlainText().strip() or
                (self.worker and self.worker.isRunning()) or
                (self.goose_worker and self.goose_worker.isRunning())):
            return
        envelope = self.chatgpt_bridge.get_task_nowait()
        if envelope is None:
            return
        task = envelope.get("text", "") if isinstance(envelope, dict) else str(envelope)
        if not task:
            return
        self.prompt.setPlainText(task)
        self.chatgpt_task_pending = True
        self.status.setText("ChatGPT Webから指示を受信しました")
        self._append("\n[ChatGPT Webから指示を受信しました。]\n")
        if self.auto_handoff_enabled:
            self.backend_combo.setCurrentIndex(1)
            self._dispatch_chatgpt_task()

    def _set_auto_handoff_enabled(self, enabled):
        self.auto_handoff_enabled = bool(enabled)
        if self.chatgpt_bridge:
            self.chatgpt_bridge.set_auto_enabled(self.auto_handoff_enabled)
        self._save_state()
        if self.auto_handoff_enabled and self.chatgpt_task_pending:
            self._dispatch_chatgpt_task()

    def _dispatch_chatgpt_task(self):
        if not self.auto_handoff_enabled or not self.chatgpt_task_pending:
            return
        if not self.goose_token:
            self.status.setText("Qwen / Goose接続後に保留中のChatGPT指示を実行します")
            return
        self.backend_combo.setCurrentIndex(1)
        self.send_prompt()

    def _return_chatgpt_result(self, text):
        if not self.chatgpt_task_in_flight or not self.chatgpt_bridge:
            self.chatgpt_task_in_flight = False
            self.chatgpt_result_text.clear()
            return
        body = str(text or "処理は完了しましたが、返答がありませんでした。")
        if len(body) > 45000:
            body = body[:45000] + "\n（返答が長いため末尾を省略しました）"
        result = "[AUTO_STUDIO_RESULT]\n" + body + "\n[/AUTO_STUDIO_RESULT]"
        queued = self.chatgpt_bridge.queue_reply(result)
        self.status.setText("結果をChatGPT Webへ返送しました" if queued else "ChatGPTへの返答キューが満杯です")
        self.chatgpt_task_in_flight = False
        self.chatgpt_result_text.clear()

    def configure_goose(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Goose Controller接続設定")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        url = QLineEdit(self.goose_url)
        url.setPlaceholderText(DEFAULT_GOOSE_URL)
        client_id = QLineEdit(self.goose_client_id)
        token = QLineEdit(self.goose_token)
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText("Bearer Token（この起動中のみメモリに保持）")
        form.addRow("Controller URL", url)
        form.addRow("Client ID", client_id)
        form.addRow("Bearer Token", token)
        layout.addLayout(form)
        note = QLabel("Tokenは設定ファイルに保存しません。Controllerはサーバー側で設定されたプロジェクトを使用します。HTTP接続はlocalhostのみ許可されます。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                from goose_client import validate_url
                validate_url(url.text())
                if not client_id.text().strip():
                    raise ValueError("Client IDを入力してください。")
                self.goose_url = url.text().strip()
                self.goose_client_id = client_id.text().strip()
                self.goose_token = token.text().strip()
                self._save_state()
                self.status.setText("Goose設定を保存しました（Tokenはメモリ保持）")
            except ValueError as exc:
                QMessageBox.warning(self, "設定を確認してください", str(exc))
    def start_login(self):
        cli = shutil.which("claude")
        if not cli:
            QMessageBox.warning(self, "CLI未検出", "Claude Code CLIをインストールし、`claude` をPATHに追加してください。")
            return
        if self.login_process and self.login_process.poll() is None:
            QMessageBox.information(self, "ログイン画面", "Claude Codeのログイン用コンソールはすでに開いています。")
            return
        try:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            self.login_process = subprocess.Popen([cli, "auth", "login"], cwd=str(self.project or Path.home()), creationflags=flags)
            self.status.setText("ログイン用コンソールで認証してください")
            self.login_button.setText("ログイン確認")
            QTimer.singleShot(1500, self._poll_login)
        except OSError as exc:
            QMessageBox.critical(self, "ログインを開始できません", str(exc))

    def _poll_login(self):
        if self.login_process and self.login_process.poll() is None:
            QTimer.singleShot(1500, self._poll_login)
            return
        self._check_cli()

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "作業するプロジェクトを選択")
        if folder:
            self.project = Path(folder)
            self.session_id = None
            self.goose_session_id = None
            self.transcript.clear()
            self._show_project()
            self._save_state()

    def _show_project(self):
        self.project_label.setText(str(self.project))
        self.files.clear()
        if not self.project:
            return
        ignored = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
        try:
            for path in sorted(self.project.rglob("*")):
                if any(part in ignored for part in path.relative_to(self.project).parts):
                    continue
                if path.is_file():
                    rel = path.relative_to(self.project)
                    self.files.addItem(str(rel))
                    if self.files.count() >= 1500:
                        self.files.addItem("… (1500件まで表示)")
                        break
        except OSError:
            pass
        self.refresh_diff()
        if self.auto_handoff_enabled and self.chatgpt_task_pending:
            QTimer.singleShot(100, self._dispatch_chatgpt_task)

    def _open_file(self, item):
        if not self.project or item.text().startswith("…"):
            return
        path = (self.project / item.text()).resolve()
        if self.project.resolve() not in path.parents:
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            QMessageBox.warning(self, "ファイルを開けません", str(exc))

    def new_session(self):
        self.session_id = None
        self.goose_session_id = None
        self.transcript.clear()
        self._save_state()
        self._append("新しい会話を開始します。")

    def send_prompt(self):
        if ((self.worker and self.worker.isRunning()) or
                (self.goose_worker and self.goose_worker.isRunning())):
            return
        if not self.project:
            QMessageBox.information(self, "プロジェクト未選択", "先に作業フォルダーを選択してください。")
            return
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            return
        is_chatgpt_task = self.chatgpt_task_pending
        if is_chatgpt_task and self.auto_handoff_enabled:
            if not self.goose_token:
                self.status.setText("Goose接続後に保留中のChatGPT指示を実行します")
                return
            self.backend_combo.setCurrentIndex(1)
        if self.backend == "claude" and not self.claude_logged_in:
            if self.goose_token:
                self.backend_combo.setCurrentIndex(1)
            elif self.goose_connect_worker and self.goose_connect_worker.isRunning():
                self.status.setText("ローカルQwen接続中です。完了後に再送してください。")
                return
            else:
                self.start_login()
                return
        if self.backend == "goose" and not self.goose_token:
            if self.goose_connect_worker and self.goose_connect_worker.isRunning():
                self.status.setText("Goose / Qwenの準備が終わるまでお待ちください。依頼文は保持しています。")
            else:
                QMessageBox.information(self, "Goose未接続", "「Goose再接続」を押すか、画面の案内に従ってローカルモデルを準備してください。依頼文は保持しています。")
            return
        self.prompt.clear()
        self.chatgpt_task_in_flight = is_chatgpt_task
        self.chatgpt_task_pending = False
        self.chatgpt_result_text.clear()
        self._append("\nあなた\n" + prompt + "\n\n" + ("Goose\n" if self.backend == "goose" else "Claude\n"))
        if self.backend == "goose":
            self.goose_worker = GooseWorker(self.goose_url, self.goose_token, prompt, self.goose_session_id, self.project)
            self.goose_worker.completed.connect(self._on_goose_done)
            self.goose_worker.failed.connect(self._on_goose_error)
            self.goose_worker.start()
            self.status.setText("Gooseが作業中…")
            self.stop_button.setEnabled(False)
            self.send_button.setEnabled(False)
            return
        self.worker = ClaudeWorker(self.project, prompt, self.mode.currentData(), self.session_id)
        self.worker.output.connect(self._on_output)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self.status.setText("Claudeが作業中…")
        self.stop_button.setEnabled(True)
        self.send_button.setEnabled(False)

    def _on_output(self, text):
        if text.startswith("\x00SESSION:"):
            self.session_id = text.split(":", 1)[1]
            self._save_state()
        else:
            if self.chatgpt_task_in_flight:
                self.chatgpt_result_text.append(text)
            self._append(text + ("\n" if not text.endswith("\n") else ""))

    def _on_done(self, code):
        self.status.setText("完了" if code == 0 else f"終了コード {code}")
        self.stop_button.setEnabled(False)
        self.send_button.setEnabled(True)
        self.refresh_diff()
        self._show_files_only()
        if self.chatgpt_task_in_flight:
            result = "\n".join(self.chatgpt_result_text).strip()
            if code != 0:
                result = (result + "\n" if result else "") + f"Claude Codeが終了コード {code} で終了しました。"
            self._return_chatgpt_result(result)

    def _show_files_only(self):
        if not self.project:
            return
        self.files.clear()
        try:
            for path in sorted(self.project.rglob("*")):
                if any(p in {".git", ".venv", "node_modules", "__pycache__", "dist", "build"} for p in path.relative_to(self.project).parts):
                    continue
                if path.is_file():
                    self.files.addItem(str(path.relative_to(self.project)))
                    if self.files.count() >= 1500:
                        break
        except OSError:
            pass

    def stop_work(self):
        if self.worker:
            self.worker.stop()
            self.status.setText("停止中…")

    def _on_goose_done(self, session_id, answer):
        self.goose_session_id = session_id
        self._append(answer + "\n")
        self.status.setText("Goose応答完了")
        self.send_button.setEnabled(True)
        self._save_state()
        if self.chatgpt_task_in_flight:
            self._return_chatgpt_result(answer)

    def _on_goose_error(self, message):
        self._append("[Gooseエラー] " + message + "\n")
        self.status.setText("Goose接続エラー")
        self.send_button.setEnabled(True)
        if self.chatgpt_task_in_flight:
            self._return_chatgpt_result("Goose処理エラー: " + message)

    def refresh_diff(self):
        if not self.project:
            return
        try:
            p = subprocess.run(["git", "diff", "--", "."], cwd=str(self.project), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
            status = subprocess.run(["git", "status", "--short"], cwd=str(self.project), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
            if p.returncode == 0 and status.returncode == 0:
                content = p.stdout.strip()
                untracked = [x for x in status.stdout.splitlines() if "??" in x]
                if untracked:
                    content += ("\n\n" if content else "") + "未追跡ファイル:\n" + "\n".join(untracked)
                self.diff.setPlainText(content or "変更はありません")
            else:
                self.diff.setPlainText("Gitリポジトリではないか、gitを実行できません。")
        except Exception as exc:
            self.diff.setPlainText(f"差分を取得できません: {exc}")

    def _append(self, text):
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()
        self._save_state()

    def _save_state(self):
        try:
            STATE_FILE.write_text(json.dumps({"project": str(self.project) if self.project else None, "session_id": self.session_id, "transcript": self.transcript.toPlainText(), "goose_url": self.goose_url, "goose_client_id": self.goose_client_id, "goose_session_id": self.goose_session_id, "chatgpt_pairing_hash": self.chatgpt_pairing_hash, "auto_handoff_enabled": self.auto_handoff_enabled}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load_state(self):
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.project = Path(state["project"]) if state.get("project") else None
            self.session_id = state.get("session_id")
            self.goose_url = state.get("goose_url", DEFAULT_GOOSE_URL)
            self.goose_client_id = state.get("goose_client_id", "claude-auto-studio")
            self.goose_session_id = state.get("goose_session_id")
            self.chatgpt_pairing_hash = state.get("chatgpt_pairing_hash", "")
            self.auto_handoff_enabled = bool(state.get("auto_handoff_enabled", False))
            self.transcript.setPlainText(state.get("transcript", ""))
        except (OSError, ValueError, KeyError):
            self.goose_url = DEFAULT_GOOSE_URL
            self.goose_client_id = "claude-auto-studio"
            self.goose_session_id = None
            self.chatgpt_pairing_hash = ""
            self.auto_handoff_enabled = False
        if not hasattr(self, "goose_url"):
            self.goose_url = DEFAULT_GOOSE_URL
            self.goose_client_id = "claude-auto-studio"
            self.goose_session_id = None
            self.chatgpt_pairing_hash = ""
            self.auto_handoff_enabled = False

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1800)
        if self.goose_worker and self.goose_worker.isRunning():
            self.goose_token = ""
            self.goose_worker.wait(1800)
        self.goose_token = ""
        self._save_state()
        if self.chatgpt_bridge:
            self.chatgpt_bridge.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = Studio()
    window.show()
    sys.exit(app.exec())
