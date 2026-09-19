# Agent Dashboard を OneDrive 配下へ配布し、Claude Code / Codex の hook と heartbeat を導入する。
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

if (-not $env:OneDrive) { throw 'OneDrive 環境変数が見つかりません。OneDrive を起動・同期してから実行してください。' }
$bin = Join-Path $env:OneDrive 'agent-dashboard-bin'
$data = Join-Path $env:OneDrive 'agent-dashboard'
$hook = Join-Path $bin 'dashboard-hook.ps1'
$heartbeat = Join-Path $bin 'dashboard-heartbeat.ps1'
$taskName = 'Agent Dashboard Heartbeat'

function Write-JsonAtomically([string]$path, $value) {
    $tmp = "$path.$PID.tmp"
    [IO.File]::WriteAllText($tmp, ($value | ConvertTo-Json -Depth 32), (New-Object Text.UTF8Encoding $false))
    Move-Item -Force $tmp $path
}

function Remove-DashboardHandlers($groups) {
    $kept = @()
    foreach ($group in @($groups)) {
        if (-not $group) { continue }
        $handlers = @($group.hooks | Where-Object { $_ -and $_.command -notlike '*dashboard-hook.ps1*' })
        if (-not $handlers.Count) { continue }
        $copy = [ordered]@{}
        foreach ($property in $group.PSObject.Properties) {
            $copy[$property.Name] = if ($property.Name -eq 'hooks') { $handlers } else { $property.Value }
        }
        $kept += [pscustomobject]$copy
    }
    return $kept
}

function Set-Hooks([string]$path, [string]$tool, [string[]]$events, [string[]]$asyncEvents) {
    $cfg = if (Test-Path $path) {
        $backup = "$path.agent-dashboard.bak"
        if (-not (Test-Path $backup)) { Copy-Item $path $backup }
        Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } else { [pscustomobject]@{} }
    if (-not $cfg.PSObject.Properties['hooks']) { $cfg | Add-Member hooks ([pscustomobject]@{}) }

    $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$hook`" -Tool $tool"
    foreach ($eventName in $events) {
        $groups = @(Remove-DashboardHandlers $cfg.hooks.$eventName)
        if (-not $Uninstall) {
            $handler = [ordered]@{ type = 'command'; command = $command }
            if ($eventName -in $asyncEvents) { $handler.async = $true }
            $groups += [pscustomobject]@{ hooks = @([pscustomobject]$handler) }
        }
        if ($groups.Count) { $cfg.hooks | Add-Member $eventName $groups -Force } else { $cfg.hooks.PSObject.Properties.Remove($eventName) }
    }
    $null = New-Item -ItemType Directory -Force (Split-Path $path)
    Write-JsonAtomically $path $cfg
    Write-Host "updated: $path"
}

function Install-Heartbeat {
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$heartbeat`""
    & schtasks.exe /Create /TN $taskName /SC MINUTE /MO 1 /TR $taskCommand /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'heartbeat タスクの作成に失敗しました。' }
    & $heartbeat
}

function Remove-Heartbeat {
    & schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
}

if ($Uninstall) {
    Set-Hooks (Join-Path $HOME '.claude\settings.json') 'claude' @('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PermissionRequest', 'PermissionDenied', 'Stop', 'StopFailure', 'SessionEnd') @()
    Set-Hooks (Join-Path $HOME '.codex\hooks.json') 'codex' @('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'Interrupt', 'SessionEnd') @()
    Remove-Heartbeat
    Write-Host 'Agent Dashboard の hook と heartbeat を解除しました。既存の状態 JSON は保持しています。'
    exit 0
}

$null = New-Item -ItemType Directory -Force $bin, $data
if ((Resolve-Path $PSScriptRoot).Path -ne (Resolve-Path $bin).Path) {
    Copy-Item (Join-Path $PSScriptRoot 'dashboard-common.ps1'), (Join-Path $PSScriptRoot 'dashboard-hook.ps1'), (Join-Path $PSScriptRoot 'dashboard-heartbeat.ps1'), $PSCommandPath $bin -Force
    Write-Host "published: $bin"
}
attrib +P -U $bin /S /D
attrib +P -U $data /S /D

$claudeEvents = 'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PermissionRequest', 'PermissionDenied', 'Stop', 'StopFailure', 'SessionEnd'
$codexEvents = 'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'Interrupt', 'SessionEnd'
Set-Hooks (Join-Path $HOME '.claude\settings.json') 'claude' $claudeEvents ($claudeEvents | Where-Object { $_ -notin 'Stop', 'SessionEnd' })
Set-Hooks (Join-Path $HOME '.codex\hooks.json') 'codex' $codexEvents ($codexEvents | Where-Object { $_ -notin 'Stop', 'SessionEnd' })
Install-Heartbeat
Write-Host 'Codex: 初回は codex を起動して /hooks から dashboard-hook を信頼してください。'
