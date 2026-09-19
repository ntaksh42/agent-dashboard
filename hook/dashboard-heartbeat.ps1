# タスクスケジューラから毎分実行し、端末の生存と hook 導入状態を更新する。
. $PSScriptRoot\dashboard-common.ps1
try {
    if (-not $env:OneDrive) { exit 0 }
    $dashboardHost = Get-DashboardHost
    $dir = Get-DashboardDataDirectory $dashboardHost
    $null = New-Item -ItemType Directory -Force $dir

    function Test-DashboardHook([string]$path, [string]$tool, [string[]]$events) {
        if (-not (Test-Path $path)) { return $false }
        try {
            $config = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($event in $events) {
                $found = @($config.hooks.$event | ForEach-Object { $_.hooks } | Where-Object {
                    $_ -and $_.type -eq 'command' -and $_.command -match 'dashboard-hook\.ps1' -and $_.command -match "-Tool\s+$tool(?:\s|$)"
                })
                if (-not $found.Count) { return $false }
            }
            return $true
        } catch { return $false }
    }

    # 旧バージョンが残したプロンプト・コマンド・絶対パスを、この端末のフォルダから一度だけ除去する。
    Get-ChildItem $dir -Filter '*.json' | Where-Object { $_.Name -ne 'host.json' } | ForEach-Object {
        try {
            $session = Get-Content $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($session.schema_version -ge 3) { return }
            foreach ($name in 'cwd', 'first_prompt', 'last_prompt') { $session.PSObject.Properties.Remove($name) }
            if ($session.PSObject.Properties['activity']) { $session.activity = $null }
            $session | Add-Member schema_version 3 -Force
            $tmp = "$($_.FullName).$PID.tmp"
            [IO.File]::WriteAllText($tmp, ($session | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
            Move-Item -Force $tmp $_.FullName
        } catch { }
    }

    $now = Get-DashboardNow
    $hostState = [ordered]@{
        schema_version = 3
        host_id = $dashboardHost.host_id
        pc = $dashboardHost.pc
        user = $dashboardHost.user
        last_heartbeat_at = $now
        tools = [ordered]@{
            claude = [ordered]@{ configured = (Test-DashboardHook (Join-Path $HOME '.claude\settings.json') 'claude' @('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PermissionRequest', 'PermissionDenied', 'Stop', 'StopFailure', 'SessionEnd')); trust = 'not_applicable' }
            codex = [ordered]@{ configured = (Test-DashboardHook (Join-Path $HOME '.codex\hooks.json') 'codex' @('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'Interrupt', 'SessionEnd')); trust = 'unverified' }
        }
    }
    $path = Join-Path $dir 'host.json'
    Write-DashboardJson $path $hostState
} catch { }
exit 0
