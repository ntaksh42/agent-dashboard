# Agent Dashboard 実用化に必要な仕様対応

## 実装完了記録（2026-09-20）

P0/P1 の実装、設定画面からの起点フォルダ選択・構造検査、schema v3 のデータ検証、hook 遷移 fixture、10 台・30 session の受け入れ fixture を完了した。自動検証は Python 21 件、PowerShell 構文解析、ブラウザ JavaScript 構文解析、HTTP API の設定プレビューまで通過している。

実機 2 台での OneDrive 同期・PC 停止・実際の承認画面を含む受け入れシナリオは、対象端末を用意した運用時に下記の「受け入れシナリオ」で実施する。これは実機環境依存の運用確認であり、未実装機能ではない。

この文書の以降の「現状」「問題」は着手時の評価記録である。現在の契約と動作は `docs/architecture.md`、導入手順は `README.md` を正とする。

調査日: 2026-09-19  
対象: `E:\agent-dashboard` の現在の作業ツリー  
評価軸: 複数 Windows PC 上の Claude Code / Codex を、日常的に見落としなく状態監視できること

## 結論

現状は基本コンセプトを確認できる試作としては成立しているが、実用にはまだ不足がある。最優先は機能追加ではなく、**画面に出る状態を信用できること**である。

特に次の4点が実用化の必須条件になる。

1. hook イベントから状態への変換を正確にする
2. PC・ユーザー・親セッション・サブエージェントを混同しない
3. 「停止」「同期遅延」「hook 故障」を画面上で区別できる
4. 利用者が対応すべきものを、重要度順に見落とさず把握できる

## 現状でできていること

- PC ごとの online / stale / offline 表示
- Claude Code / Codex の実行中・承認待ち・入力待ち・エラー・終了表示
- 5秒ごとの自動更新
- project、branch、activity の表示
- prompt、command、絶対 path を API に出さない allowlist
- 終了セッションと24時間以上更新されないセッションの非表示
- OneDrive 内の一部 JSON が壊れていても、全体を停止せず読み飛ばす
- dark mode、可変幅レイアウト

Python の既存テスト8件は成功している。ただし、テスト対象は主に JSON 読込と設定保存であり、実用性を左右する hook の状態遷移と画面挙動は未検証である。

## 必須仕様（P0）

### 1. 状態モデルを確定する

現在の `running / waiting / idle / error / ended / stale` は大枠では妥当だが、イベントとの対応が不完全である。まず状態遷移表を正式な仕様として定義する。

| 現象 | 期待する表示 | 現状の問題 |
| --- | --- | --- |
| prompt 送信後 | 実行中 | 対応済み |
| tool 実行中 | 実行中 + tool 名 | 対応済み |
| tool 成功後 | 実行中、直前の実行中表示を解除 | `activity` が残り、完了した tool を「実行中」と表示し続ける (`hook/dashboard-hook.ps1:59`) |
| tool 失敗 | tool 失敗を明示 | state は `running` のままで、「エラー」集計にも入らない (`hook/dashboard-hook.ps1:60`) |
| 承認待ち | 承認待ち | 対応済みだが Codex/Claude の実機確認が必要 |
| 承認拒否 | 拒否を一定時間明示した後、実行中または入力待ち | Claude のみイベント登録。Codex での遷移が未定義 |
| turn 完了 | 入力待ち | 対応済み |
| API/応答失敗 | エラー | Claude の `StopFailure` のみ。Codex の失敗表現が未定義 |
| CLI 中断 | 入力待ちまたは中断 | Codex のみ `Interrupt` を登録。Claude の中断時挙動を確認する必要がある |
| session 終了 | 終了 | 対応済みだが、強制終了・クラッシュではイベントが来ない |
| 更新途絶 | 状態不明 | 5分で stale 化するが、理由は表示されない |

対応項目:

- [x] `PostToolUse` で `activity` を消す。
- [x] tool 失敗を `error` にするか、「実行継続中の直近エラー」という別属性にするか決める。turn 全体の失敗と tool 単体の失敗を同じ状態にしない。
- [x] 承認拒否、中断、rate limit、認証失敗、ネットワーク失敗、利用者キャンセルの表示仕様を決める。
- [x] 一時イベントを何秒間表示するか決める。次イベントまで残すだけでは、古い「拒否」「失敗」が現在状態に見える。
- [x] 端末 offline 時は、その端末の session を `running` や `waiting` のまま見せず、「最終確認時は実行中」のように確度を落として表示する。
- [x] PC 時計が未来の場合は online 扱いにせず、時計ずれとして警告する。現在は未来時刻を age 0 に丸める (`server/server.py:138`)。
- [x] すべての遷移を表形式で `docs/architecture.md` に固定し、イベント列にない遷移を実装しない。

完了条件: Claude Code / Codex の各イベント列を入力したテストで、画面上の状態と activity が遷移表どおりになる。

### 2. 監視対象の識別単位を正しくする

現在は `COMPUTERNAME` を PC の一意キーとしているため、同じ PC を複数 Windows ユーザーが使うと同じフォルダと `host.json` を共有し、互いの状態を上書きする。また、同名 project やサブエージェントを区別できない。

- [x] 監視単位を `端末 + Windows ユーザー` とするか、「1 PC 1ユーザーのみ対応」と明記する。一般利用なら前者を推奨する。
- [x] 内部 ID と表示名を分ける。内部 ID は衝突しない値、表示名は利用者が変更できる PC 名とする。
- [x] 同じ project の複数 session を区別できる短い session 表示を追加する。生の session ID 全体は不要。
- [x] project 名が同じ別 path を区別する方法を決める。機密性を保つなら、利用者が付ける alias または path の非可逆短縮 ID を使う。
- [x] git worktree、detached HEAD、git 管理外 directory の表示仕様を決める。
- [x] branch は prompt 送信時だけでなく、表示に影響する変更後にも更新する。現状は turn 中の checkout が反映されない (`hook/dashboard-hook.ps1:50-53`)。
- [x] `session_id` をファイル名と mutex 名へ直接使わず、安全な内部 ID に変換する (`hook/dashboard-hook.ps1:34-35`)。

完了条件: 同じ PC の複数ユーザー、同じ project の複数 session、同名 project、worktree を画面上で誤認しない。

### 3. サブエージェントを親セッションと混同しない

Claude Code/Codex の hook はサブエージェントでも発火し得る。現状は `agent_id` / `agent_type` を見ず、同じ session file を更新するため、サブエージェントの Stop や tool が親 session の状態を上書きする可能性がある。

仕様は次のどちらかに固定する。

- 推奨: サブエージェントのイベントを無視し、親 session の状態だけを表示する。
- 代案: 親 session card 内にサブエージェント数と状態を集約する。

独立 card として全サブエージェントを並べる方式は、監視対象が増えて要確認事項を見落としやすいため初期仕様にはしない。

対応項目:

- [x] hook input の `agent_id` / `agent_type` の有無を判定する。
- [x] 親のみ表示する場合、サブエージェントのイベントで親の `idle / ended` を更新しない。
- [x] 初期仕様では subagent を集約・保存せず、親セッションだけを表示する。
- [x] Claude Code / Codex それぞれで、親とサブエージェントが同時実行する fixture を用意する。

完了条件: サブエージェントの終了によって、実行中の親 session が入力待ちや終了へ変わらない。

### 4. 「端末停止」と「監視機能の故障」を区別する

現在の heartbeat は PC の生存ではなく、ログオン中ユーザーの scheduled task と OneDrive 書込みが動作したことを表す。offline の原因は複数あるため、単に「オフライン」と断定すると判断を誤る。

最低限、次を別の表示にする。

| 表示 | 判定 |
| --- | --- |
| 正常 | heartbeat が新しく、hook 設定も有効 |
| hook 未設定 | heartbeat は新しいが対象 tool の hook がない |
| heartbeat 遅延 | 3〜10分更新なし |
| 監視不能 | 10分以上更新なし。PC停止、ログオフ、task失敗、OneDrive停止のいずれか |
| データ異常 | JSON を読めない、schema 不一致、時刻異常 |

- [x] UI の「offline」を「監視不能」へ変更するか、原因を断定できない注記を常時表示する。
- [x] `configured` を単なる文字列検索ではなく、対象 event と command path が正しく登録されているかで判定する (`hook/dashboard-heartbeat.ps1:7-10`)。
- [x] Codex は hook の trust/enabled 状態まで確認できる方法があるか調査し、取得できない場合は「設定あり・動作未確認」と表示する。
- [x] host と session の schema 不一致を `data_errors` の総数だけでなく、該当 PC 単位で表示する。
- [x] 最後に正常な hook event を受け取った時刻と heartbeat 時刻を分けて表示する。
- [x] OneDrive 同期遅延とローカル heartbeat 停止は現構成では完全に区別できないことを仕様上明示する。

完了条件: 利用者が画面だけで「作業への対応が必要」「hook 導入が必要」「監視自体を確認すべき」を区別できる。

### 5. 要確認項目を見落とさない画面にする

現在の要確認欄は承認待ち・エラー・host stale/offline を列挙するが、優先順位と重複排除がない。

- [x] 要確認の優先度を固定する: `承認待ち > session/turn エラー > 監視不能 > heartbeat 遅延 > データ異常`。
- [x] 要確認を発生時刻順に並べる。
- [x] 同一 PC/session の重複 notice は1件にまとめる。
- [x] card を選ぶと、状態理由、最終更新、PC、tool、project、branch を確認できる詳細表示を追加する。
- [x] 承認待ちや新規エラーを見逃さないよう、ブラウザ通知または音を opt-in で追加する。権限拒否時は画面内通知だけで動作する。
- [x] 同じ事象を更新のたびに再通知しないよう event ID または状態変化を記録する。
- [x] stale/offline が大量にある場合でも、現在の承認待ちが画面上部に残るようにする。
- [x] browser tab title は承認待ち数とエラー数を分ける。現在は合計値だけである (`server/index.html:39`)。

完了条件: 10台・30 session 程度の fixture で、最重要の承認待ちとエラーをスクロールせず発見できる。

### 6. データ契約を厳密にする

- [x] host/session の JSON Schema 相当を定義する。
- [x] `schema_version` を必須にし、未知の version を黙って読まない。
- [x] `pc`、`project`、`branch`、`activity`、`session_id` の型と最大長を定義する。
- [x] JSON 1ファイルの最大 size と、PC/session の最大件数を決める。
- [x] path 上の PC ID と JSON 内の PC ID が異なる場合はデータ異常にする。
- [x] 同一 session の競合コピー、重複 host、重複 session ID の採用規則を決める。
- [x] `tools.claude` / `tools.codex` が object でない場合も host 全体を失わず、その tool だけ異常扱いにする (`server/server.py:139-147`)。
- [x] 表示に使っていない生の `session_id` は API から除くか、短い表示 ID に変換する (`server/server.py:17`)。
- [x] `started_at` を表示・sort・所要時間に使わないなら保存項目から外す。使うなら API 契約へ含める。

完了条件: 不正型、過大値、未知 schema、重複、競合コピーを入力しても、正常データを巻き込まず PC 単位の異常として表示する。

## 実用性を高める仕様（P1）

### 7. 一覧性と絞り込み

- [x] PC の並び順を、要対応あり → 稼働中 → 入力待ち → 監視不能 → session なし、とする。現在は PC 名順 (`server/server.py:180`)。
- [x] `全件 / 要確認 / 稼働中 / 入力待ち / 監視不能` の filter を追加する。
- [x] PC 名、project、branch の部分一致検索を追加する。
- [x] Claude / Codex の tool filter を追加する。
- [x] filter 中の件数と全件数を表示する。
- [x] filter、通知設定、表示密度を browser local storage に保存する。監視データ自体は保存しない。

検索・filter は PC や session が少ない利用者には不要なので、P0 の状態精度を先に完成させる。

### 8. セッションの時間情報

- [x] 「何分前」だけでなく、hover/detail で絶対時刻を表示する。
- [x] session の開始時刻と経過時間を表示するか選択可能にする。
- [x] stale 判定までの残り時間は不要だが、stale 化した時刻は詳細で確認できるようにする。
- [x] browser の時刻と server 生成時刻の差が大きい場合、閲覧 PC の時計ずれを警告する。
- [x] 終了 session の30分表示を固定値にするか設定値にするか決める。初期仕様では固定値でよい。

### 9. 画面の信頼性

- [x] 最終取得成功時刻と最終取得試行時刻を分ける。
- [x] fetch 失敗時、古い card 全体に「この表示は古い」と明示する。現在は右上の文言だけが変わる (`server/index.html:50`)。
- [x] HTTP non-2xx を成功扱いしない (`server/index.html:50`, `server/index.html:57`)。
- [x] 連続失敗時の過剰 request を避けるため、最大間隔付き backoff を入れる。
- [x] 手動再読込ボタンを追加する。
- [x] browser が background の場合も必要な精度で更新するか、復帰時に即時 refresh する。
- [x] 設定変更後は、新しい root の読込成功を確認してから dialog を閉じる。

### 10. 初回設定と空状態

- [x] 初回画面で「閲覧 PC」「監視対象 PC」の手順を分けて示す。
- [x] root 未設定、空 folder、host のみ、session のみ、壊れた data の空状態を別メッセージにする。
- [x] folder を text 入力だけでなく選択できるようにする。browser だけでは native folder picker が制限されるため、viewer launcher または OS dialog が必要になる。
- [x] 設定 folder に期待する構造があるかを検査し、任意の既存 folder をそのまま受理しない。
- [x] 設定変更前に検出 PC 数と読取エラー数を preview する。

### 11. 表示名と情報量

- [x] PC に任意の表示名を付けられるようにする。元の `COMPUTERNAME` は詳細に残す。
- [x] project 名が取れない場合に、単なる「不明」ではなく原因を区別する: cwd なし、root、取得エラー。
- [x] branch が空の場合に、git 管理外、detached HEAD、未取得を区別する。
- [x] tool 未導入と、導入済みだが現在 session なしを視覚的に区別する。
- [x] activity は定型文だけに限定し、tool 名が機密になり得る場合の非表示設定を検討する。

## 対象外として明記すべき仕様

次は現構成で対応しない。暗黙に期待されないよう README と画面に明記する。

- PC や agent の遠隔操作、承認操作
- prompt、回答、command、変更 file の閲覧
- 秒単位のリアルタイム保証
- PC の電源断、ログオフ、scheduled task 故障、OneDrive 停止の完全な原因判定
- ログオンしていない利用者 context の監視
- 同じ OneDrive を共有しない PC の集約
- cloud session の監視（ローカル user hook が動かない実行形態）
- browser を開いていない状態での確実な通知
- macOS / Linux

## 受け入れシナリオ

仕様完成の判定には、最低限次を通す。

1. 2台の PC で Claude Code と Codex を同時実行し、4 session を誤りなく識別できる。
2. 同一 project の session を2つ起動し、どちらが承認待ちか判別できる。
3. tool 成功後に「実行中」の tool 名が残らない。
4. tool 失敗と turn 全体の失敗を別の表示として判別できる。
5. 承認待ち → 拒否 → 再実行 → turn 完了が定義どおり遷移する。
6. サブエージェントが終了しても親 session の状態が変わらない。
7. PC を停止した場合、session が実行中のまま残らず監視不能になる。
8. hook 設定を削除した場合、PC は online のまま hook 未設定と表示される。
9. OneDrive 同期を停止した場合、heartbeat 遅延から監視不能へ遷移する。
10. 壊れた JSON、未知 schema、未来時刻が、正常な PC を巻き込まず data 異常として表示される。
11. 10台・30 session 中の承認待ちとエラーを初期画面で即座に発見できる。
12. server との通信が切れた場合、全 card が古い情報であることを誤認しない。

## 推奨実装順

1. 状態遷移表を確定し、hook fixture test を作る。
2. tool 完了後の activity、失敗、拒否、中断、offline 時の状態を修正する。
3. 端末 + ユーザー + session + subagent の識別規則を確定する。
4. heartbeat と hook 正常性を分離して表示する。
5. 要確認の優先順位、詳細表示、状態変化通知を実装する。
6. schema 検証と競合規則を実装する。
7. 10台・30 session の fixture で一覧性を確認し、必要な filter だけ追加する。
8. 上記12件の受け入れシナリオを実機で確認する。

## 参照した仕様

- [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks)
- [Codex Hooks](https://developers.openai.com/ja-JP/docs/hooks)

公式仕様では Claude Code と Codex の hook event やサブエージェント時の挙動が完全には同一でない。共通の event 名だけで同じ状態になると仮定せず、tool ごとの変換規則を持つ必要がある。
