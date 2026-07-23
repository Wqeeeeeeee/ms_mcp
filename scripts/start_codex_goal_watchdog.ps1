param(
    [int]$IntervalMinutes = 20,
    [string]$Workspace = "",
    [string]$ThreadId = $env:CODEX_THREAD_ID,
    [string]$Model = "gpt-5.4-mini",
    [int]$MinimumThreadQuietMinutes = 30
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\CodexMsMcpGoalWatchdog20Min", [ref]$createdNew)
if (-not $createdNew) {
    exit 0
}

$logDir = Join-Path $Workspace "workspace\codex_watchdog"
$statusLog = Join-Path $logDir "watchdog-status.log"
$pidPath = Join-Path $logDir "daemon.pid"
$runner = Join-Path $Workspace "scripts\codex_goal_watchdog.ps1"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Set-Content -LiteralPath $pidPath -Value $PID -Encoding ASCII
"$(Get-Date -Format o) daemon_started: pid=$PID interval_minutes=$IntervalMinutes mode=quiet_gated_transactional_workspace_write quiet_minutes=$MinimumThreadQuietMinutes model=$Model" |
    Add-Content -LiteralPath $statusLog -Encoding UTF8

try {
    while ($true) {
        Start-Sleep -Seconds ($IntervalMinutes * 60)
        try {
            & $runner -Workspace $Workspace -ThreadId $ThreadId -Model $Model -MinimumThreadQuietMinutes $MinimumThreadQuietMinutes
        }
        catch {
            "$(Get-Date -Format o) watchdog_iteration_failed: $($_.Exception.Message)" |
                Add-Content -LiteralPath $statusLog -Encoding UTF8
        }
    }
}
finally {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
