# Claude Auto Studio

Claude Code CLIを使う、Windows向けの小さなデスクトップ開発GUIです。既存のAI-Development-Studioとは独立しています。

## 必要なもの

- Windows 10/11
- Python 3.10以上
- Claude Code CLIをインストールしてログイン済みであること（`claude` がPATH上にあること）
- Goose Controllerを利用する場合は、ADS側のGoose、Ollama、ADS用Python環境が用意されていること

## 起動

`Start_Claude_Auto_Studio.bat` をダブルクリックしてください。初回は専用の`.venv`を作成し、PySide6をインストールします。ネットワーク制限のある環境では、先に `python -m pip install -r requirements.txt` を実行してください。

または、既存のPython環境で `python app.py` を実行します。

## 使い方

1. 「プロジェクトを開く」で作業フォルダーを選ぶ
2. バックエンドで「Claude Code」または「Goose Controller」を選ぶ
3. Claudeの場合、未ログインなら「Claudeにログイン」から認証し、Auto / Ask / Planを選ぶ
4. Gooseの場合、「Goose接続設定」でController URL、Client ID、Bearer Tokenを入力する
5. 下部の入力欄に作業内容を書き、Ctrl+Enterで送信
6. 左のファイル一覧、右のGit差分で変更を確認

同じフォルダーでは会話セッションを再開します。新しい会話を始めるときは「新規会話」を押してください。Stopは実行中のCLIプロセスを終了します。

## ChatGPT Webから指示を渡す

1. Auto Studioを起動し、「ChatGPT Web連携」を押してペアリングコードを発行する
2. Chrome/Edgeの拡張機能管理ページでデベロッパーモードを有効にし、「パッケージ化されていない拡張機能を読み込む」で `chatgpt_extension` フォルダーを選ぶ
3. 拡張機能の詳細からオプションを開き、Auto Studioのペアリングコードを保存する
4. ChatGPT Webを再読み込みする
5. 各回答の下に出るボタン、またはページ右下の固定ボタンを押す（固定ボタンは最新の回答を送る）
6. Auto Studioの入力欄で内容を確認して送信する

### ChatGPTとAuto Studioの自動往復

1. 拡張機能をペアリングし、Auto Studioで作業プロジェクトを選んでGoose Controller / Qwenの接続を確認する
2. Auto Studio上部の「ChatGPT自動往復」を有効にする（既定では無効）
3. ChatGPTに、実行する指示を必ず次の専用マーカーで囲むよう依頼する

   ```text
   [AUTO_STUDIO_TASK]
   選択したプロジェクトを調べ、依頼内容を分析してください。
   [/AUTO_STUDIO_TASK]
   ```

4. 拡張機能がマーカー内の文章だけをローカルAuto Studioへ渡し、Goose/Qwenの結果を `[AUTO_STUDIO_RESULT]...[/AUTO_STUDIO_RESULT]` で囲んで同じChatGPT会話へ自動送信する
5. 続きの作業が必要なときは、ChatGPTに次の指示も同じタスクマーカーで囲むよう依頼する

自動処理はChatGPTの全回答を無差別に実行しません。Auto Studioのチェックボックスが有効で、かつ回答に有効なタスクマーカーがあるときだけ動きます。1つの会話につき最大5件で停止します。返答を挿入するとき、入力欄に既存の下書きがある場合は上書きせず待機します。自動往復は現在接続されたGoose Controllerを利用し、Gooseの現行実装は読み取り専用のプロジェクト解析で、ソースファイルの直接編集は行いません。

拡張コードを更新した後は `chrome://extensions` で拡張の再読み込みボタンを押し、ChatGPT Webのタブも再読み込みしてください。

アプリは127.0.0.1:8769だけで待ち受けます。拡張機能から受け取った内容はキューに入り、ローカルアプリで人が「指示を送信」を押すまで処理されません。初回にブラウザーからローカル接続の許可を求められた場合は許可してください。ペアリングコードはハッシュだけをアプリ設定に保存し、拡張機能側のコードはChrome/Edgeのローカル拡張ストレージに保存します。コードを再発行すると既存コードは無効になります。

## 権限と安全性

このGUIはClaude Codeの権限判定や分類器を再実装しません。選択したモードを公式CLIに渡し、Claude Codeが利用可能なAuto Mode、権限ルール、認証、ツール設定をそのまま使います。CLIがAuto Modeを提供しない場合は、エラーが会話欄に表示されます。Autoは許可された環境でのみ選択してください。

起動時にOllamaを確認し、必要なら起動してからADS Goose Controllerへ自動接続します。モデルは `qwen2.5-coder:7b`、Controllerはポート8768で起動します。モデルがない場合は、PowerShellで `ollama pull qwen2.5-coder:7b` を実行してください（モデル配布サイズは約4.7GBです）。既定のADS配置が見つかればControllerが停止中の場合に起動し、専用Client ID `claude-auto-studio` のBearer TokenがWindows Credential Managerにあるか確認します。なければADS管理CLIで最長24時間のTokenを発行してCredential Managerへ保存します。Tokenはアプリの設定ファイルに書き込みません。異なる配置を使う場合は `ADS_PROJECT_ROOT` 環境変数でADSルートを指定してください。外部Controllerを手動設定する場合はHTTPSのみ許可します。ローカルControllerを使うときは、選択中のプロジェクトから最大30ファイル・合計60,000バイトまでのテキストコードを、依頼内容と一緒にローカルGoose/Ollamaへ送ります。秘密鍵、`.env`、仮想環境、Git履歴などは対象外です。Gooseバックエンドの応答はチャットに返され、ファイルは直接編集しません。Auto/Ask/Planのモード選択はClaude Code側に適用されます。

ChatGPT Webとの連携はブラウザー拡張を介します。拡張サービスワーカーが許可されたホスト権限でループバック上のローカルブリッジと通信し、ペアリングコードで認証します。自動往復は明示的なアプリ内オプトインとタスクマーカーを必要とします。

このアプリはプロジェクトのファイルを直接編集せず、会話・承認・作業をClaude Code CLIに委ねます。CLI起動時の作業ディレクトリは選択したプロジェクトです。

## 現在の範囲

チャット、Claudeセッション継続、Auto/Ask/Planモード、Goose Controller接続、プロジェクトファイル表示、Git差分確認を備えた独立GUIです。Claude CodeのターミナルUIや全機能を複製するものではなく、利用できる機能はインストール済みCLIとアカウント設定に従います。
