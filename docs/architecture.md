# Agent Dashboard 運用設計

## 目的と完了条件

複数の Windows 端末で動く Claude Code と Codex を、閲覧端末のブラウザで安全に一覧する。運用上の完了条件は、導入済み端末がセッションの有無にかかわらず表示され、承認待ち・エラー・通信断を見分けられることとする。

## 構成

```text
Claude Code / Codex hook ─┐
                           ├─ %OneDrive%\agent-dashboard\<host-id>\*.json ─ OneDrive ─ server.py ─ localhost browser
Task Scheduler (60秒) ────┘                         host.json / hook.json
```

- hook は親セッション状態だけを書き込む。Claude Code / Codex の subagent event は `agent_id` または `agent_type` を持つため無視する。通常イベントは非同期、停止と終了は同期で実行する。
- `Agent Dashboard Heartbeat` はログオン中の利用者コンテキストで1分ごとに `host.json` を更新する。OneDrive を使うため、サービスアカウントや管理者権限は不要。
- `server.py` は設定画面で指定した起点フォルダを読むだけで、127.0.0.1 以外には待ち受けない。初期値は `%OneDrive%\agent-dashboard` で、設定は閲覧 PC の `%LOCALAPPDATA%\Agent Dashboard\settings.json` に保存する。

## データ契約

すべての同期 JSON は `schema_version: 3` を必須とし、未知の version は読まない。1ファイルは 32 KiB 以下、最大 100 host・host あたり最大 100 session とする。host directory 名、`host.json`、session JSON の `host_id` は一致しなければデータ異常にする。

`host.json`:

```json
{
  "schema_version": 3,
  "host_id": "host-7d8a9c0e1f2a3b4c",
  "pc": "DEV-PC01",
  "user": "alice",
  "last_heartbeat_at": "2026-09-19T08:00:00.0000000Z",
  "tools": {
    "claude": { "configured": true, "trust": "not_applicable" },
    "codex": { "configured": true, "trust": "unverified" }
  }
}
```

`hook.json` は `host_id` と `last_hook_at` だけを保存する。heartbeat と最後に正常な hook event を分けて表示する。

session JSON は `host_id`, `pc`, `user`, `tool`, `session`, `project`, `project_id`, `project_status`, `branch`, `branch_status`, `activity`, `activity_expires_at`, `state`, `started_at`, `updated_at` だけを保存する。`host_id` は Windows SID と PC 名から生成する非可逆 ID、`session` は元の session ID の短いハッシュ、`project_id` は cwd の短いハッシュである。これにより同じ PC の別ユーザー、同名 project、同一 project の複数 session を区別する。

`project_status` は `available` / `cwd_missing` / `cwd_root` / `cwd_error`、`branch_status` は `branch` / `detached` / `non_git` / `unavailable` のいずれかである。git worktree は通常の branch 表示と非可逆 `project_id` の組み合わせで区別する。

同じ `(tool, session)` を名乗る複数ファイルは、ファイル名順で最初の1件だけを採用し、残りを当該 host のデータ異常として表示する。host ID が重複する directory は host ID 不一致として採用しない。

保存しない情報:

- ユーザープロンプト本文
- 実行コマンド、URL、検索語、パッチ本文
- 絶対作業パス、元の session ID、Windows SID

この制約により、OneDrive 共有範囲へ意図しない業務・秘密情報が残るリスクを下げる。プロジェクト名やブランチ名にも機密性がある組織では、hook の保存項目をさらに削る。

旧 schema の状態ファイルは移行せず、サーバーが PC 単位のデータ異常として表示する。再度インストーラーを実行して v3 の hook を配布する。

## 状態モデル

| 状態 | 意味 | 発生源 |
| --- | --- | --- |
| `running` | プロンプトまたはツール実行後で、turn 終了前 | UserPromptSubmit / PreToolUse / PostToolUse |
| `waiting` | 利用者のツール承認を待つ | PermissionRequest |
| `idle` | turn 完了・中断後 | Stop / Interrupt |
| `error` | turn が応答エラーで終了 | Claude Code StopFailure |
| `ended` | CLI セッション終了 | SessionEnd |
| `stale` | 5分以上セッション更新がないため真の状態を保証できない | 閲覧サーバが派生 |

`ended` は終端状態とし、遅れて完了した非同期 hook で `running` に戻さない。tool 失敗は `state: running` と `activity: tool_failed`（30秒）、承認拒否は `state: running` と `activity: approval_denied`（30秒）であり、turn 全体の `error` とは混同しない。`PostToolUse` は必ず activity を消す。`StopFailure` の `turn_failed` は次の prompt または停止まで残す。Claude Code が渡す API failure code は `failure_reason` に allowlist 化して保存し、`rate_limit`、認証、混雑、server error 等を画面で区別する。利用者キャンセルは `Interrupt` または `tool_interrupted` として30秒表示する。

| event | Claude Code | Codex | 遷移 |
| --- | --- | --- | --- |
| `SessionStart` | 登録 | 登録 | `idle`、activity を消す |
| `UserPromptSubmit` | 登録 | 登録 | `running`、activity を消す |
| `PreToolUse` | 登録 | 登録 | `running`、`tool_running` |
| `PostToolUse` | 登録 | 登録 | `running`、activity を消す |
| `PostToolUseFailure` | 登録 | 未登録 | `running`、`tool_failed` を30秒 |
| `PermissionRequest` | 登録 | 登録 | `waiting`、`approval_pending` |
| `PermissionDenied` | 登録 | 未登録 | `running`、`approval_denied` を30秒 |
| `StopFailure` | 登録 | 未登録 | `error`、`turn_failed` |
| `Stop` | 登録 | 登録 | `idle`、activity を消す |
| `Interrupt` | 未登録 | 登録 | `idle`、`interrupted` を30秒 |
| `SessionEnd` | 登録 | 登録 | `ended`、activity を消す |

Codex の trust 状態は hook JSON だけから取得できないため、`configured: true / trust: unverified` と表示する。利用者は `/hooks` で trust を確認する。

要確認は `承認待ち > turn エラー > 監視不能 > heartbeat 遅延 > データ異常 > hook 未設定` の優先度で表示し、同一優先度内は新しい発生時刻を先にする。同じ host / tool / session / 状態の通知は1件にまとめる。

終了していないが24時間以上更新がないセッションは、過去のクラッシュや旧セッションによる一覧の肥大化を避けるため画面から非表示にする。状態ファイル自体は削除しない。

## 端末状態としきい値

heartbeat の時刻を閲覧端末で評価する。

| 状態 | 最終 heartbeat |
| --- | --- |
| `healthy` | 3分以内 |
| `heartbeat_delayed` | 3分超10分以内 |
| `unmonitorable` | 10分超。PC停止、ログオフ、task失敗、OneDrive停止のいずれか |
| `clock_skew` | 閲覧 PC より1分超未来 |
| `data_error` | JSON 不正、schema不一致、ID不一致など |

これは OneDrive の数十秒程度の同期遅延を見込んだ運用上のしきい値である。全端末で時刻同期を有効にする。`heartbeat_delayed` / `unmonitorable` / `clock_skew` の host の session は `stale` として確度を落とす。OneDrive が止まった端末は `unmonitorable` と同じ見え方になるため、「端末の電源断」と「同期障害」は画面だけでは区別できない。

## 導入・更新・解除

- 配布元で `hook/install.ps1` を実行し、OneDrive の配布フォルダを更新する。
- 各端末で配布済み `install.ps1` を実行する。既存 hook グループの他ハンドラは保持し、Dashboard のコマンドハンドラだけを更新する。
- 初回のみ `.agent-dashboard.bak` を保存する。解除は他の利用者変更を失わないよう、Dashboard エントリと scheduled task だけを削除する。
- Codex は非管理 hook を信頼する必要がある。導入確認には `/hooks` を使う。
- 閲覧画面右上の「設定」から起点フォルダを変更できる。存在するフォルダだけを受け付け、保存直後から読み取り先を切り替える。`--dir` は起動時だけの上書き指定であり、画面から保存した値が次回起動時の標準になる。

## 障害時の見方

- `データ異常`: schema、型、ID、時刻、または JSON size が契約に合わない。該当 PC の JSON と hook version を確認する。
- `heartbeat 遅延` / `監視不能`: 対象端末のログオン、OneDrive 同期、タスクスケジューラの最終実行結果を確認する。PC 停止と同期障害は区別できない。
- `時計ずれ`: 対象端末または閲覧 PC の時刻同期を確認する。
- `読み取りエラー`: OneDrive 同期中の JSON、手動編集、または壊れた旧ファイルの可能性がある。次回同期で解消しない場合にファイルを調べる。
- Codex が表示されない: `/hooks` で trust 状態と `~/.codex/hooks.json` を確認する。

## 検証

Python の API テストは次を固定する。

```powershell
py -3 -m unittest server\test_server.py
```

実機受け入れでは、Claude Code と Codex の各々で「プロンプト送信、承認要求、承認拒否、ツール失敗、停止、端末ログオフ」を2台以上で確認する。OneDrive 同期停止時に10分で `監視不能` 表示になることも確認する。

## 制約と将来の移行条件

この設計は Windows、ログオン中の利用者、同一 OneDrive アカウントに限定される。更新の即時性は OneDrive に依存し、数秒単位の保証はしない。

即時通知、端末への操作、組織横断のアクセス制御、ログオンしていない端末の監視が必要になった時点で、認証済みの中央 collector API と常駐エージェントへ移行する。その場合も `host` と `session` のスキーマを API の入力契約として引き継ぐ。
