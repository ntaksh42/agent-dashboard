# Agent Dashboard 運用設計

## 目的と完了条件

複数の Windows 端末で動く Claude Code と Codex を、閲覧端末のブラウザで安全に一覧する。運用上の完了条件は、導入済み端末がセッションの有無にかかわらず表示され、承認待ち・エラー・通信断を見分けられることとする。

## 構成

```text
Claude Code / Codex hook ─┐
                           ├─ %OneDrive%\agent-dashboard\<PC>\*.json ─ OneDrive ─ server.py ─ localhost browser
Task Scheduler (60秒) ────┘                       host.json
```

- hook はセッション状態のみを書き込む。Claude Code の通常イベントは非同期、停止と終了は同期で実行する。
- `Agent Dashboard Heartbeat` はログオン中の利用者コンテキストで1分ごとに `host.json` を更新する。OneDrive を使うため、サービスアカウントや管理者権限は不要。
- `server.py` は設定画面で指定した起点フォルダを読むだけで、127.0.0.1 以外には待ち受けない。初期値は `%OneDrive%\agent-dashboard` で、設定は閲覧 PC の `%LOCALAPPDATA%\Agent Dashboard\settings.json` に保存する。

## データ契約

`host.json`:

```json
{
  "schema_version": 2,
  "pc": "DEV-PC01",
  "last_seen_at": "2026-09-19T08:00:00.0000000Z",
  "tools": { "claude": { "configured": true }, "codex": { "configured": true } }
}
```

セッション JSON は `pc`, `tool`, `session_id`, `project`, `branch`, `activity`, `state`, `started_at`, `updated_at` だけを保存する。サーバ API はこのうち表示に必要な項目だけを返す。

保存しない情報:

- ユーザープロンプト本文
- 実行コマンド、URL、検索語、パッチ本文
- 絶対作業パス

この制約により、OneDrive 共有範囲へ意図しない業務・秘密情報が残るリスクを下げる。プロジェクト名やブランチ名にも機密性がある組織では、hook の保存項目をさらに削る。

v1 の状態ファイルが残っている端末では、heartbeat が次回実行時に `cwd`、プロンプト、旧 `activity` を削除して v2 へ移行する。

## 状態モデル

| 状態 | 意味 | 発生源 |
| --- | --- | --- |
| `running` | プロンプトまたはツール実行後で、turn 終了前 | UserPromptSubmit / PreToolUse / PostToolUse |
| `waiting` | 利用者のツール承認を待つ | PermissionRequest |
| `idle` | turn 完了・中断後 | Stop / Interrupt |
| `error` | turn が応答エラーで終了 | Claude Code StopFailure |
| `ended` | CLI セッション終了 | SessionEnd |
| `stale` | 5分以上セッション更新がないため真の状態を保証できない | 閲覧サーバが派生 |

`ended` は終端状態とし、遅れて完了した非同期 hook で `running` に戻さない。Claude Code の拒否・ツール失敗も記録対象に含める。Codex で共通でないイベントは登録せず、公式にサポートされる共通 lifecycle event のみを利用する。

終了していないが24時間以上更新がないセッションは、過去のクラッシュや旧セッションによる一覧の肥大化を避けるため画面から非表示にする。状態ファイル自体は削除しない。

## 端末状態としきい値

heartbeat の時刻を閲覧端末で評価する。

| 状態 | 最終 heartbeat |
| --- | --- |
| `online` | 3分以内 |
| `stale` | 3分超10分以内 |
| `offline` | 10分超 |
| `unknown` | 旧形式のセッションはあるが heartbeat がない |

これは OneDrive の数十秒程度の同期遅延を見込んだ運用上のしきい値である。全端末で時刻同期を有効にする。OneDrive が止まった端末は offline と同じ見え方になるため、「端末の電源断」と「同期障害」は画面だけでは区別できない。

## 導入・更新・解除

- 配布元で `hook/install.ps1` を実行し、OneDrive の配布フォルダを更新する。
- 各端末で配布済み `install.ps1` を実行する。既存 hook グループの他ハンドラは保持し、Dashboard のコマンドハンドラだけを更新する。
- 初回のみ `.agent-dashboard.bak` を保存する。解除は他の利用者変更を失わないよう、Dashboard エントリと scheduled task だけを削除する。
- Codex は非管理 hook を信頼する必要がある。導入確認には `/hooks` を使う。
- 閲覧画面右上の「設定」から起点フォルダを変更できる。存在するフォルダだけを受け付け、保存直後から読み取り先を切り替える。`--dir` は起動時だけの上書き指定であり、画面から保存した値が次回起動時の標準になる。

## 障害時の見方

- `未確認`: heartbeat 未導入、または旧データだけが残っている。対象端末でインストーラーを実行する。
- `stale` / `offline`: 対象端末のログオン、OneDrive 同期、タスクスケジューラの最終実行結果を確認する。
- `読み取りエラー`: OneDrive 同期中の JSON、手動編集、または壊れた旧ファイルの可能性がある。次回同期で解消しない場合にファイルを調べる。
- Codex が表示されない: `/hooks` で trust 状態と `~/.codex/hooks.json` を確認する。

## 検証

Python の API テストは次を固定する。

```powershell
py -3 -m unittest server\test_server.py
```

実機受け入れでは、Claude Code と Codex の各々で「プロンプト送信、承認要求、承認拒否、ツール失敗、停止、端末ログオフ」を2台以上で確認する。OneDrive 同期停止時に10分で offline 表示になることも確認する。

## 制約と将来の移行条件

この設計は Windows、ログオン中の利用者、同一 OneDrive アカウントに限定される。更新の即時性は OneDrive に依存し、数秒単位の保証はしない。

即時通知、端末への操作、組織横断のアクセス制御、ログオンしていない端末の監視が必要になった時点で、認証済みの中央 collector API と常駐エージェントへ移行する。その場合も `host` と `session` のスキーマを API の入力契約として引き継ぐ。
