# Claude Code / Codex の hook から呼ばれ、最小限のセッション状態を OneDrive に書き出す。
# プロンプト、実行コマンド、絶対パスは同期しない。
param([Parameter(Mandatory)][ValidateSet('claude', 'codex')][string]$Tool)
. $PSScriptRoot\dashboard-common.ps1

function Set-ProjectMetadata($state, [string]$cwd) {
    if (-not $cwd) {
        $state.project = $null; $state.project_id = $null; $state.project_status = 'cwd_missing'
        $state.branch = $null; $state.branch_status = 'unavailable'
        return
    }
    $trimmed = $cwd.TrimEnd('\', '/')
    $project = Split-Path $trimmed -Leaf
    $state.project = Limit-Text $project 96
    $state.project_id = "project-$(Get-HashId $cwd 10)"
    $state.project_status = if ($project) { 'available' } else { 'cwd_root' }
    $branch = (& git -C $cwd branch --show-current 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -eq 0 -and $branch) {
        $state.branch = Limit-Text $branch 128; $state.branch_status = 'branch'
        return
    }
    $insideGit = (& git -C $cwd rev-parse --is-inside-work-tree 2>$null | Select-Object -First 1)
    $state.branch = $null
    $state.branch_status = if ($insideGit -eq 'true') { 'detached' } else { 'non_git' }
}

function Clear-Activity($state) {
    $state.activity = $null
    $state.activity_expires_at = $null
}

function Set-TransientActivity($state, [string]$activity, [string]$now) {
    $state.activity = $activity
    $state.activity_expires_at = (Get-Date $now).ToUniversalTime().AddSeconds(30).ToString('o')
}

function Update-HookHealth([string]$directory, $dashboardHost, [string]$now) {
    $mutex = New-Object Threading.Mutex($false, "Local\agent-dashboard-hook-health-$($dashboardHost.host_id)")
    $locked = $false
    try {
        $locked = $mutex.WaitOne(5000)
        if (-not $locked) { return }
        Write-DashboardJson (Join-Path $directory 'hook.json') ([ordered]@{ schema_version = 3; host_id = $dashboardHost.host_id; last_hook_at = $now })
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

try {
    [Console]::InputEncoding = [Text.Encoding]::UTF8
    $event = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (-not $event.session_id -or -not $env:OneDrive) { exit 0 }
    if ($event.agent_id -or $event.agent_type) { exit 0 }

    $dashboardHost = Get-DashboardHost
    $dir = Get-DashboardDataDirectory $dashboardHost
    $null = New-Item -ItemType Directory -Force $dir
    $sessionKey = Get-HashId "$Tool`n$($event.session_id)"
    $path = Join-Path $dir "$Tool-$sessionKey.json"
    $mutex = New-Object Threading.Mutex($false, "Local\agent-dashboard-$Tool-$sessionKey")
    $locked = $false
    try {
        $locked = $mutex.WaitOne(5000)
        if (-not $locked) { exit 0 }
        $now = Get-DashboardNow
        $state = if (Test-Path $path) { Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json } else {
            [pscustomobject]@{
                schema_version = 3; host_id = $dashboardHost.host_id; pc = $dashboardHost.pc; user = $dashboardHost.user; tool = $Tool
                session = $sessionKey.Substring(0, 8); project = $null; project_id = $null; project_status = 'cwd_missing'
                branch = $null; branch_status = 'unavailable'; activity = $null; activity_expires_at = $null
                state = 'idle'; started_at = $now; updated_at = $now
            }
        }

        # SessionEnd は終端状態。遅延した非同期イベントで復帰させない。
        if ($state.state -eq 'ended' -and $event.hook_event_name -ne 'SessionStart') { exit 0 }
        Set-ProjectMetadata $state $event.cwd

        switch ($event.hook_event_name) {
            'SessionStart' { $state.state = 'idle'; Clear-Activity $state }
            'UserPromptSubmit' { $state.state = 'running'; Clear-Activity $state }
            'PreToolUse' { $state.state = 'running'; Clear-Activity $state; $state.activity = 'tool_running' }
            'PostToolUse' { $state.state = 'running'; Clear-Activity $state }
            'PostToolUseFailure' { $state.state = 'running'; Set-TransientActivity $state 'tool_failed' $now }
            'PermissionRequest' { $state.state = 'waiting'; Clear-Activity $state; $state.activity = 'approval_pending' }
            'PermissionDenied' { $state.state = 'running'; Set-TransientActivity $state 'approval_denied' $now }
            'StopFailure' { $state.state = 'error'; $state.activity = 'turn_failed'; $state.activity_expires_at = $null }
            'Stop' { $state.state = 'idle'; Clear-Activity $state }
            'Interrupt' { $state.state = 'idle'; Set-TransientActivity $state 'interrupted' $now }
            'SessionEnd' { $state.state = 'ended'; Clear-Activity $state }
        }
        $state.updated_at = $now
        Write-DashboardJson $path $state
        Update-HookHealth $dir $dashboardHost $now
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
} catch { }
exit 0
