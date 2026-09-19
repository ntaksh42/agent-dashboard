# Claude Code / Codex の hook から呼ばれ、セッション状態を OneDrive に書き出す。
# 出力: %OneDrive%\agent-dashboard\<PC名>\<tool>-<session_id>.json
# エージェントの動作を妨げないよう、何が起きても何も出力せず exit 0 する。
param([Parameter(Mandatory)][ValidateSet('claude', 'codex')][string]$Tool)

function Shorten([string]$s, [int]$n) {
    if (-not $s) { return $null }
    $s = ($s -replace '\s+', ' ').Trim()
    if ($s.Length -gt $n) { $s.Substring(0, $n) + '...' } else { $s }
}

function Describe-Tool($name, $in) {
    $detail = $null
    foreach ($k in 'file_path', 'path', 'command', 'pattern', 'url', 'query', 'description', 'prompt') {
        $v = $in.$k
        if ($v) { $detail = if ($v -is [array]) { $v -join ' ' } else { [string]$v }; break }
    }
    # apply_patch 等はパッチ本文ではなく対象ファイル名を出す
    if ($detail -match '\*\*\* (?:Add|Update|Delete) File: (\S+)') { $detail = $Matches[1] }
    Shorten "$name $detail" 160
}

try {
    [Console]::InputEncoding = [Text.Encoding]::UTF8
    $e = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (-not $e.session_id) { exit 0 }

    $dir = Join-Path $env:OneDrive "agent-dashboard\$env:COMPUTERNAME"
    $null = New-Item -ItemType Directory -Force $dir
    $path = Join-Path $dir "$Tool-$($e.session_id).json"
    # 非同期 hook が同じセッションのファイルを同時に更新しないよう直列化する
    $mutex = New-Object Threading.Mutex($false, "Local\agent-dashboard-$($e.session_id)")
    try { $null = $mutex.WaitOne(5000) } catch [Threading.AbandonedMutexException] { }
    $now = (Get-Date).ToUniversalTime().ToString('o')

    $s = if (Test-Path $path) { Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json } else {
        [pscustomobject]@{
            pc = $env:COMPUTERNAME; tool = $Tool; session_id = $e.session_id; cwd = $null; branch = $null
            started_at = $now; first_prompt = $null; last_prompt = $null; activity = $null; state = 'idle'; updated_at = $now
        }
    }
    if ($e.cwd) { $s.cwd = $e.cwd }

    switch ($e.hook_event_name) {
        'SessionStart' {
            $s.state = 'idle'
            # 終了済みの古いファイルを掃除する（増え続けないように）
            Get-ChildItem $dir -Filter *.json | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) } | Remove-Item -Force
        }
        'UserPromptSubmit' {
            $p = Shorten $e.prompt 200
            if (-not $s.first_prompt) { $s.first_prompt = $p }
            $s.last_prompt = $p
            $s.activity = $null
            $s.state = 'running'
            if ($e.cwd) { $s.branch = git -C $e.cwd branch --show-current 2>$null }
        }
        'PreToolUse' { $s.state = 'running'; $s.activity = Describe-Tool $e.tool_name $e.tool_input }
        'PostToolUse' { $s.state = 'running' }
        'PermissionRequest' { $s.state = 'waiting'; $s.activity = Describe-Tool $e.tool_name $e.tool_input }
        { $_ -in 'Stop', 'Interrupt' } { $s.state = 'idle' }
        'SessionEnd' { $s.state = 'ended' }
    }
    $s.updated_at = $now

    # 読み手が書きかけのファイルを読まないよう、一時ファイル経由で置き換える
    $tmp = "$path.tmp"
    [IO.File]::WriteAllText($tmp, ($s | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
    Move-Item -Force $tmp $path
} catch { } finally { if ($mutex) { try { $mutex.ReleaseMutex() } catch { } } }
exit 0
