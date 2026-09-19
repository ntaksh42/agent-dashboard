# Agent Dashboard の hook / heartbeat で共有する識別子と JSON 出力の関数。

function Limit-Text([object]$value, [int]$maxLength) {
    if ($null -eq $value) { return $null }
    $text = ([string]$value).Trim()
    if ($text.Length -gt $maxLength) { return $text.Substring(0, $maxLength) }
    return $text
}

function Get-HashId([string]$value, [int]$length = 16) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($value)
        $hash = -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
        return $hash.Substring(0, [Math]::Min($length, $hash.Length))
    } finally {
        $sha.Dispose()
    }
}

function Get-DashboardHost {
    $user = Limit-Text $env:USERNAME 64
    try { $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value } catch { $sid = $user }
    $pc = Limit-Text $env:COMPUTERNAME 64
    return [ordered]@{
        host_id = "host-$(Get-HashId "$pc`n$sid")"
        pc = $pc
        user = $user
    }
}

function Get-DashboardDataDirectory($dashboardHost) {
    Join-Path $env:OneDrive "agent-dashboard\$($dashboardHost.host_id)"
}

function Write-DashboardJson([string]$path, $value) {
    $temporary = "$path.$PID.tmp"
    [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json -Compress -Depth 16), (New-Object Text.UTF8Encoding $false))
    Move-Item -Force $temporary $path
}

function Get-DashboardNow {
    (Get-Date).ToUniversalTime().ToString('o')
}
