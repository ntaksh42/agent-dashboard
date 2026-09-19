# agent-dashboard

複数 PC 上の Claude Code / Codex のセッション状態を、OneDrive 経由で 1 台に集めて表示する。
設計は `docs/design.html` を参照。

## セットアップ

前提: 全 PC で同じ OneDrive アカウントが同期されていること。

### 各 PC（監視される側）

```powershell
powershell -ExecutionPolicy Bypass -File "$env:OneDrive\agent-dashboard-bin\install.ps1"
```

- Claude Code: `~/.claude/settings.json` に hook を追加（元のファイルは `settings.json.bak`）
- Codex: `~/.codex/hooks.json` に hook を追加。初回は `codex` を起動して `/hooks` から信頼する
- 外すとき: `install.ps1 -Uninstall`

hook スクリプトを変更したら、このリポジトリの `hook\install.ps1` を実行すると配布フォルダ
（`%OneDrive%\agent-dashboard-bin`）が更新され、各 PC には OneDrive 経由で反映される。

`.ps1` は PowerShell 5.1 で実行するため UTF-8（BOM 付き）で保存すること。

### 閲覧 PC

```powershell
py -3 server\server.py        # http://localhost:8765/
```
