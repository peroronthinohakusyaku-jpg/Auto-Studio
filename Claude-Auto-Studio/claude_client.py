"""Claude Code CLI worker."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class ClaudeWorker(QThread):
    output = Signal(str)
    done = Signal(int)

    def __init__(self, cwd: Path, prompt: str, mode: str, session_id: str | None):
        super().__init__()
        self.cwd = cwd
        self.prompt = prompt
        self.mode = mode
        self.session_id = session_id
        self.proc: subprocess.Popen[str] | None = None

    def run(self) -> None:
        claude = shutil.which("claude")
        if not claude:
            self.output.emit("Claude Code CLIが見つかりません。インストール後、`claude` がPATHにあることを確認してください。")
            self.done.emit(127)
            return

        args = [
            claude,
            "-p",
            self.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            self.mode,
        ]
        if self.session_id:
            args += ["--resume", self.session_id]

        try:
            self.proc = subprocess.Popen(
                args,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
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
            session_id = data.get("session_id")
            if session_id:
                self.output.emit("\x00SESSION:" + session_id)
        elif kind == "assistant":
            message = data.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    self.output.emit(block["text"])
                elif block.get("type") == "tool_use":
                    self.output.emit(f"[実行] {block.get('name', 'tool')}")
        elif kind == "result":
            session_id = data.get("session_id")
            if session_id:
                self.output.emit("\x00SESSION:" + session_id)
            if data.get("is_error"):
                self.output.emit("\n[エラー] " + str(data.get("result", "Claude Codeでエラーが発生しました")))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
