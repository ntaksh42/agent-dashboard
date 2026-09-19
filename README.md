# Agent Dashboard

複数の Windows PC 上で動く Claude Code / Codex の状態を、OneDrive 経由で 1 台に集約して表示するダッシュボードです。設計と運用上の制約は [docs/architecture.md](docs/architecture.md) を参照してください。

## 表示する情報

- 端末ごとの 正常 / heartbeat 遅延 / 監視不能 / 時計ずれ / データ異常
- Claude Code / Codex ごとの 実行中 / 承認待ち / 入力待ち / エラー / 状態不明
- プロジェクト名、git ブランチ、ツール種別だけ

プロンプト本文、実行コマンド本文、絶対パスは OneDrive に保存・表示しません。

## 初回セットアップ

前提は、すべての端末が同じ OneDrive アカウントを同期している Windows PC であることです。

1. 配布元 PC でリポジトリから次を一度実行します。

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\hook\install.ps1
   ```

   `%OneDrive%\agent-dashboard-bin` に hook とインストーラーが配布されます。

2. 監視したい各 PC で、OneDrive の同期完了後に次を実行します。

   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:OneDrive\agent-dashboard-bin\install.ps1"
   ```

   Claude Code / Codex の hook を登録し、`Agent Dashboard Heartbeat` というユーザーのタスクスケジューラ項目を作成します。heartbeat は1分ごとに端末の生存と hook 導入状態だけを更新します。

3. Codex を使う端末では、初回に `codex` を起動し、`/hooks` から dashboard hook を信頼します。

4. 閲覧 PC で起動します。

   ```powershell
   py -3 server\server.py
   ```

   `http://localhost:8765/` を開き、右上の「設定」から状態 JSON の起点フォルダを指定します。初期値は `%OneDrive%\agent-dashboard` です。設定は閲覧 PC の `%LOCALAPPDATA%\Agent Dashboard\settings.json` に保存され、次回起動時にも使われます。

   一時的に別のフォルダを使う場合は、起動時に `--dir` を指定できます。設定画面で保存した内容は以後の起動に使われます。

   ```powershell
   py -3 server\server.py --dir 'D:\shared\agent-dashboard'
   ```

## 更新・解除

- hook を変更した場合は、配布元 PC で `hook\install.ps1` を再実行します。各端末へのスクリプト同期後も、設定内容を変更した場合は Codex の `/hooks` で信頼状態を確認してください。
- 解除する場合は対象 PC で `install.ps1 -Uninstall` を実行します。hook と heartbeat だけを外し、収集済み JSON は残します。
- インストーラーは初回実行時に設定ファイルの `.agent-dashboard.bak` を作ります。解除はこのバックアップを復元せず、Dashboard の hook だけを除去するため、その後の利用者設定を保持します。

## 制約

このダッシュボードは状態を確認するだけであり、PC や agent の遠隔操作・承認操作、prompt / 回答 / command / 変更ファイルの閲覧はできません。OneDrive 同期を使うため秒単位の更新保証はなく、`監視不能` は PC 電源断、ログオフ、scheduled task 故障、OneDrive 停止を区別しません。

Windows のログオン中ユーザー、同一 OneDrive アカウントを同期する PC、ローカルで動く Claude Code / Codex hook のみが対象です。macOS / Linux、cloud session、ブラウザを閉じた状態での確実な通知は対象外です。

`.ps1` は PowerShell 5.1 互換の UTF-8 BOM 付きで管理します。
