# dashboard-hook.ps1 を OneDrive の配布フォルダに置き、Claude Code / Codex の hook 設定に登録する。
# 何度実行しても同じ結果になる。-Uninstall で hook 設定から外す。
#   開発PC: リポジトリの hook\install.ps1 を実行（配布フォルダへコピーも行う）
#   他のPC: %OneDrive%\agent-dashboard-bin\install.ps1 を実行
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$bin = Join-Path $env:OneDrive 'agent-dashboard-bin'
$data = Join-Path $env:OneDrive 'agent-dashboard'
$hook = Join-Path $bin 'dashboard-hook.ps1'

function Set-Hooks([string]$path, [string]$tool, [string[]]$events, [string[]]$asyncEvents) {
    $cfg = if (Test-Path $path) {
        Copy-Item $path "$path.bak" -Force
        Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } else { [pscustomobject]@{} }
    if (-not $cfg.PSObject.Properties['hooks']) { $cfg | Add-Member hooks ([pscustomobject]@{}) }

    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$hook`" -Tool $tool"
    foreach ($ev in $events) {
        # 既存の dashboard-hook エントリを除き、それ以外の hook は残す
        $list = @($cfg.hooks.$ev | Where-Object { $_ -and -not ($_.hooks | Where-Object { $_.command -like '*dashboard-hook.ps1*' }) })
        if (-not $Uninstall) {
            $h = [ordered]@{ type = 'command'; command = $cmd }
            if ($ev -in $asyncEvents) { $h.async = $true }
            $list += [pscustomobject]@{ hooks = @([pscustomobject]$h) }
        }
        if ($list.Count) { $cfg.hooks | Add-Member $ev $list -Force } else { $cfg.hooks.PSObject.Properties.Remove($ev) }
    }
    $null = New-Item -ItemType Directory -Force (Split-Path $path)
    [IO.File]::WriteAllText($path, ($cfg | ConvertTo-Json -Depth 32), (New-Object Text.UTF8Encoding $false))
    Write-Host "updated: $path"
}

if (-not $Uninstall) {
    $null = New-Item -ItemType Directory -Force $bin, $data
    if ((Resolve-Path $PSScriptRoot).Path -ne (Resolve-Path $bin).Path) {
        Copy-Item (Join-Path $PSScriptRoot 'dashboard-hook.ps1'), $PSCommandPath $bin -Force
        Write-Host "published: $bin"
    }
    # ファイル オンデマンドでクラウドのみにならないよう、常にこのデバイスに保持する
    attrib +P -U $bin /S /D
    attrib +P -U $data /S /D
}

$common = 'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'SessionEnd'
# Stop / SessionEnd は直後にプロセスが終わり得るため同期実行にする（非同期だと打ち切られる）
Set-Hooks (Join-Path $HOME '.claude\settings.json') 'claude' $common ($common | Where-Object { $_ -notin 'Stop', 'SessionEnd' })
Set-Hooks (Join-Path $HOME '.codex\hooks.json') 'codex' ($common + 'Interrupt') @()

if (-not $Uninstall) { Write-Host 'Codex: 初回は codex を起動して /hooks から dashboard-hook を信頼してください。' }
