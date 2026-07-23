param(
    [string]$ThreadId = $env:CODEX_THREAD_ID,
    [string]$Workspace = "",
    [string]$Model = "gpt-5.4-mini",
    [int]$MinimumThreadQuietMinutes = 30,
    [switch]$IgnoreThreadActivity,
    [switch]$ResumeOriginalThread,
    [switch]$ValidationOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
if ($ResumeOriginalThread -and [string]::IsNullOrWhiteSpace($ThreadId)) {
    throw "ThreadId or CODEX_THREAD_ID is required with ResumeOriginalThread."
}
$logDir = Join-Path $Workspace "workspace\codex_watchdog"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$statusLog = Join-Path $logDir "watchdog-status.log"
$eventLog = Join-Path $logDir "run-$timestamp.jsonl"
$messageLog = Join-Path $logDir "run-$timestamp.txt"
$fallbackLog = Join-Path $logDir "run-$timestamp-fallback.jsonl"
$fallbackMessageLog = Join-Path $logDir "run-$timestamp-fallback.txt"
$freshLog = Join-Path $logDir "run-$timestamp-fresh.jsonl"
$freshMessageLog = Join-Path $logDir "run-$timestamp-fresh.txt"
$postValidationLog = Join-Path $logDir "run-$timestamp-validation.log"

$iterationCreatedNew = $false
$iterationMutex = New-Object System.Threading.Mutex(
    $true,
    "Local\CodexMsMcpGoalWatchdogIteration",
    [ref]$iterationCreatedNew
)
if (-not $iterationCreatedNew) {
    "$(Get-Date -Format o) skipped: another watchdog iteration is already active" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    $iterationMutex.Dispose()
    exit 0
}

$workspaceRoot = [System.IO.Path]::GetFullPath($Workspace).TrimEnd("\")
$runningProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$threadSession = $null
$threadSessionRoot = Join-Path $env:USERPROFILE ".codex\sessions"
if (
    -not [string]::IsNullOrWhiteSpace($ThreadId) -and
    (Test-Path -LiteralPath $threadSessionRoot)
) {
    $threadSession = Get-ChildItem -LiteralPath $threadSessionRoot -Recurse -File -Filter "*$ThreadId*.jsonl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
}

$busyValidation = $runningProcesses |
    Where-Object {
        $_.CommandLine -and
        (
            $_.CommandLine -like "*pytest*" -or
            $_.CommandLine -like "*compileall*"
        ) -and
        (
            $_.CommandLine -like "*$workspaceRoot*" -or
            ($_.ExecutablePath -and $_.ExecutablePath -like "$workspaceRoot\*")
        )
    }

if ($busyValidation) {
    $pids = ($busyValidation | ForEach-Object { "$($_.ProcessId):$($_.Name)" }) -join ", "
    "$(Get-Date -Format o) skipped: validation still running in workspace ($pids)" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    $iterationMutex.ReleaseMutex()
    $iterationMutex.Dispose()
    exit 0
}

if (
    -not $DryRun -and
    -not $ValidationOnly -and
    -not $IgnoreThreadActivity -and
    $MinimumThreadQuietMinutes -gt 0 -and
    $null -ne $threadSession
) {
    $threadQuietMinutes = ((Get-Date).ToUniversalTime() - $threadSession.LastWriteTimeUtc).TotalMinutes
    if ($threadQuietMinutes -lt $MinimumThreadQuietMinutes) {
        "$(Get-Date -Format o) skipped: active goal thread quiet_minutes=$([math]::Round($threadQuietMinutes, 2)) required_minutes=$MinimumThreadQuietMinutes session=$($threadSession.FullName)" |
            Add-Content -LiteralPath $statusLog -Encoding UTF8
        $iterationMutex.ReleaseMutex()
        $iterationMutex.Dispose()
        exit 0
    }
}

$matchingResume = $runningProcesses |
    Where-Object {
        $_.CommandLine -and
        (
            $_.Name -eq "codex.exe" -or
            $_.Name -eq "node.exe"
        ) -and
        $_.CommandLine -like "*codex*exec*" -and
        (
            (
                -not [string]::IsNullOrWhiteSpace($ThreadId) -and
                $_.CommandLine -like "*resume*$ThreadId*"
            ) -or
            $_.CommandLine -like "*-C*$Workspace*"
        )
    }

if ($matchingResume) {
    "$(Get-Date -Format o) skipped: an existing Codex resume process is already handling $ThreadId" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    $iterationMutex.ReleaseMutex()
    $iterationMutex.Dispose()
    exit 0
}

$prompt = @"
Continue working toward the active Materials Studio MCP goal in the current worktree. Inspect current evidence first. If progress is blocked, diagnose the blocker, try safe in-scope alternatives, and continue implementation rather than only reporting status. If the current state is healthy, make one narrow concrete improvement toward natural-language semiconductor modeling, same-window real-time hot-loading, multi-view diagnostic export, or evidence-based normality checks. Do not launch or open Materials Studio unless the user explicitly requested a live GUI hot-load, and always preserve the single-window policy. Do not use material_studio_run_script unless explicitly requested. Keep the active goal open unless a requirement-by-requirement completion audit proves it complete.

The watchdog already checked for pytest/compileall, competing codex exec processes, and recent activity in the primary goal thread. Do not run Get-Process, Get-CimInstance, tasklist, ps, or other process-enumeration commands. Inspect files, test logs, MCP status tools, and project artifacts instead.

Do not run pytest or compileall and do not create pytest temp/cache directories. The parent watchdog runs a fixed validation suite with the normal workspace token after this child exits; report the most relevant additional test command in your final message when needed.
"@

function Get-WorkspaceTransactionFiles {
    $transactionRoots = @(".codex", "config", "docs", "examples", "scripts", "src", "tests")
    $files = @()
    foreach ($relativeRoot in $transactionRoots) {
        $rootPath = Join-Path $workspaceRoot $relativeRoot
        if (-not (Test-Path -LiteralPath $rootPath)) {
            continue
        }
        $files += Get-ChildItem -LiteralPath $rootPath -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Extension -notin @(".pyc", ".pyo") -and
                $_.FullName -notlike "*\__pycache__\*" -and
                $_.FullName -notlike "*\.pytest_cache\*"
            }
    }

    $rootExtensions = @(
        ".cmd", ".example", ".ini", ".json", ".lock", ".md", ".ps1",
        ".py", ".toml", ".txt", ".yaml", ".yml"
    )
    $files += Get-ChildItem -LiteralPath $workspaceRoot -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Extension -in $rootExtensions -or
            $_.Name -in @(".gitignore", ".env.example")
        }
    return @($files | Sort-Object FullName -Unique)
}

function Get-WorkspaceRelativePath {
    param([string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $workspacePrefix = $workspaceRoot + "\"
    if (-not $fullPath.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Transaction path escapes workspace: $fullPath"
    }
    return $fullPath.Substring($workspacePrefix.Length)
}

function New-WorkspaceTransactionSnapshot {
    $pending = Get-ChildItem -LiteralPath $logDir -Directory -Filter "transaction-*" -ErrorAction SilentlyContinue
    if ($pending) {
        $pendingPaths = ($pending | ForEach-Object { $_.FullName }) -join ", "
        throw "A prior watchdog transaction still requires review: $pendingPaths"
    }

    $snapshotRoot = Join-Path $logDir "transaction-$timestamp"
    $filesRoot = Join-Path $snapshotRoot "files"
    $manifestPath = Join-Path $snapshotRoot "manifest.json"
    New-Item -ItemType Directory -Path $filesRoot -Force | Out-Null

    $relativeFiles = @()
    foreach ($file in Get-WorkspaceTransactionFiles) {
        $relativePath = Get-WorkspaceRelativePath -Path $file.FullName
        $backupPath = [System.IO.Path]::GetFullPath((Join-Path $filesRoot $relativePath))
        $filesPrefix = [System.IO.Path]::GetFullPath($filesRoot).TrimEnd("\") + "\"
        if (-not $backupPath.StartsWith($filesPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Transaction backup path escapes snapshot: $backupPath"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
        [System.IO.File]::Copy($file.FullName, $backupPath, $true)
        $relativeFiles += $relativePath
    }

    $threadWriteUtc = $null
    $threadPath = $null
    if ($null -ne $threadSession) {
        $threadPath = $threadSession.FullName
        $threadWriteUtc = $threadSession.LastWriteTimeUtc.ToString("o")
    }
    $manifest = [ordered]@{
        created_utc = (Get-Date).ToUniversalTime().ToString("o")
        workspace = $workspaceRoot
        thread_session_path = $threadPath
        thread_session_last_write_utc = $threadWriteUtc
        files = @($relativeFiles)
    }
    $manifest | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath $manifestPath -Encoding UTF8
    "$(Get-Date -Format o) transaction_snapshot_created: root=$snapshotRoot files=$($relativeFiles.Count)" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    return [pscustomobject]@{
        Root = $snapshotRoot
        FilesRoot = $filesRoot
        Manifest = $manifestPath
    }
}

function Test-GoalThreadActivityAfterSnapshot {
    param([pscustomobject]$Snapshot)

    $manifest = Get-Content -LiteralPath $Snapshot.Manifest -Raw | ConvertFrom-Json
    if (-not $manifest.thread_session_path -or -not $manifest.thread_session_last_write_utc) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $manifest.thread_session_path)) {
        return $false
    }
    $baseline = [datetime]::Parse($manifest.thread_session_last_write_utc).ToUniversalTime()
    $current = (Get-Item -LiteralPath $manifest.thread_session_path).LastWriteTimeUtc
    return $current -gt $baseline
}

function Restore-WorkspaceTransactionSnapshot {
    param([pscustomobject]$Snapshot)

    if (Test-GoalThreadActivityAfterSnapshot -Snapshot $Snapshot) {
        throw "Primary goal thread became active during the watchdog transaction; automatic rollback is unsafe."
    }

    $manifest = Get-Content -LiteralPath $Snapshot.Manifest -Raw | ConvertFrom-Json
    if ([System.IO.Path]::GetFullPath([string]$manifest.workspace) -ne $workspaceRoot) {
        throw "Transaction manifest workspace does not match current workspace."
    }
    $expected = @{}
    foreach ($relativePath in @($manifest.files)) {
        $expected[[string]$relativePath.ToLowerInvariant()] = $true
    }

    foreach ($file in Get-WorkspaceTransactionFiles) {
        $relativePath = Get-WorkspaceRelativePath -Path $file.FullName
        if (-not $expected.ContainsKey($relativePath.ToLowerInvariant())) {
            Remove-Item -LiteralPath $file.FullName -Force
        }
    }

    foreach ($relativePath in @($manifest.files)) {
        $backupPath = [System.IO.Path]::GetFullPath((Join-Path $Snapshot.FilesRoot $relativePath))
        $destinationPath = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot $relativePath))
        $workspacePrefix = $workspaceRoot + "\"
        if (-not $destinationPath.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Transaction restore path escapes workspace: $destinationPath"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destinationPath) -Force | Out-Null
        [System.IO.File]::Copy($backupPath, $destinationPath, $true)
    }
    "$(Get-Date -Format o) transaction_restored: root=$($Snapshot.Root) files=$(@($manifest.files).Count)" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
}

function Remove-WorkspaceTransactionSnapshot {
    param([pscustomobject]$Snapshot)

    $snapshotPath = [System.IO.Path]::GetFullPath($Snapshot.Root)
    $logPrefix = [System.IO.Path]::GetFullPath($logDir).TrimEnd("\") + "\"
    if (
        -not $snapshotPath.StartsWith($logPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $snapshotPath) -notlike "transaction-*"
    ) {
        throw "Refusing to remove unexpected transaction path: $snapshotPath"
    }
    Remove-Item -LiteralPath $snapshotPath -Recurse -Force
}

function Invoke-FreshContinuation {
    param(
        [int]$Attempt = 1
    )

    $attemptLog = if ($Attempt -eq 1) {
        $freshLog
    }
    else {
        Join-Path $logDir "run-$timestamp-fresh-retry$Attempt.jsonl"
    }
    $attemptMessageLog = if ($Attempt -eq 1) {
        $freshMessageLog
    }
    else {
        Join-Path $logDir "run-$timestamp-fresh-retry$Attempt.txt"
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $invokeExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        & codex --ask-for-approval never exec -c "mcp_servers={}" --ephemeral --model $Model --sandbox workspace-write --json --output-last-message $attemptMessageLog -C $Workspace $prompt 2>&1 |
            ForEach-Object { $_.ToString() } |
            Tee-Object -FilePath $attemptLog |
            Out-Host
        $invokeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $invokeExitCode
        EventLog = $attemptLog
        MessageLog = $attemptMessageLog
    }
}

function Invoke-PostContinuationValidation {
    $python = Join-Path $Workspace ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        "$(Get-Date -Format o) post_validation_failed: missing_python=$python" |
            Add-Content -LiteralPath $statusLog -Encoding UTF8
        return [pscustomobject]@{ ExitCode = 1; PytestExitCode = 1; CompileExitCode = 1; Log = $postValidationLog }
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $pytestExitCode = 1
    $compileExitCode = 1
    $pytestBase = Join-Path $env:SystemDrive ("mpt_wd_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        $ErrorActionPreference = "Continue"
        New-Item -ItemType Directory -Path $pytestBase -Force | Out-Null
        & $python -m pytest -q -p no:cacheprovider --basetemp $pytestBase `
            tests\test_reports.py `
            tests\test_model_diagnostics.py `
            tests\test_live_smoke.py `
            tests\test_watchdog_script.py `
            tests\test_gui_controller.py::test_gui_project_wrapper_uses_short_paths_for_long_project_ids `
            tests\test_gui_server_tools.py::test_live_modeling_request_builds_sic_4h_si_face_schottky_contact_scaffold 2>&1 |
            ForEach-Object { $_.ToString() } |
            Tee-Object -FilePath $postValidationLog |
            Out-Host
        $pytestExitCode = $LASTEXITCODE

        & $python -m compileall -q src tests 2>&1 |
            ForEach-Object { $_.ToString() } |
            Tee-Object -FilePath $postValidationLog -Append |
            Out-Host
        $compileExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        $pytestBaseFull = [System.IO.Path]::GetFullPath($pytestBase)
        $allowedPrefix = [System.IO.Path]::GetFullPath((Join-Path $env:SystemDrive "mpt_wd_")).TrimEnd("\")
        if (
            (Test-Path -LiteralPath $pytestBaseFull) -and
            $pytestBaseFull.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            Remove-Item -LiteralPath $pytestBaseFull -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    $exitCode = if ($pytestExitCode -eq 0 -and $compileExitCode -eq 0) { 0 } else { 1 }
    "$(Get-Date -Format o) post_validation_finished: exit_code=$exitCode pytest_exit_code=$pytestExitCode compile_exit_code=$compileExitCode log=$postValidationLog" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    return [pscustomobject]@{
        ExitCode = $exitCode
        PytestExitCode = $pytestExitCode
        CompileExitCode = $compileExitCode
        Log = $postValidationLog
    }
}

function Invoke-TransactionRollback {
    param(
        [pscustomobject]$Snapshot,
        [string]$Reason
    )

    try {
        Restore-WorkspaceTransactionSnapshot -Snapshot $Snapshot
        $rollbackValidation = Invoke-PostContinuationValidation
        if ($rollbackValidation.ExitCode -ne 0) {
            "$(Get-Date -Format o) transaction_rollback_validation_failed: reason=$Reason snapshot=$($Snapshot.Root) validation=$($rollbackValidation.Log)" |
                Add-Content -LiteralPath $statusLog -Encoding UTF8
            return $false
        }
        Remove-WorkspaceTransactionSnapshot -Snapshot $Snapshot
        "$(Get-Date -Format o) transaction_rollback_verified: reason=$Reason validation=$($rollbackValidation.Log)" |
            Add-Content -LiteralPath $statusLog -Encoding UTF8
        return $true
    }
    catch {
        "$(Get-Date -Format o) transaction_rollback_failed: reason=$Reason snapshot=$($Snapshot.Root) error=$($_.Exception.Message)" |
            Add-Content -LiteralPath $statusLog -Encoding UTF8
        return $false
    }
}

function Invoke-WatchdogFallback {
    param(
        [string]$FailureEvidence
    )

    $fallbackPrompt = @"
Continue the active Materials Studio MCP optimization goal in this workspace. This is a watchdog fallback because resuming the original Codex task failed. Inspect current files, logs, tests, and project artifacts before editing. If progress is blocked, diagnose and make a safe concrete improvement toward natural-language semiconductor modeling, same-window Materials Studio hot-loading, multi-view diagnostics, and normality checks. Do not launch Materials Studio unless explicitly requested by the user, preserve the single-window policy, and do not call material_studio_run_script. Leave clear logs under workspace/codex_watchdog.

The watchdog already checked process activity. Do not run Get-Process, Get-CimInstance, tasklist, ps, or other process-enumeration commands.

Resume failure evidence:
$FailureEvidence
"@

    & codex --ask-for-approval never exec -c "mcp_servers={}" --ephemeral --model $Model --sandbox workspace-write --json --output-last-message $fallbackMessageLog -C $Workspace $fallbackPrompt 2>&1 |
        Tee-Object -FilePath $fallbackLog
    $fallbackExitCode = $LASTEXITCODE
    "$(Get-Date -Format o) fallback_finished: exit_code=$fallbackExitCode thread=$ThreadId events=$fallbackLog" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    exit $fallbackExitCode
}

try {
    Push-Location -LiteralPath $Workspace
    if ($DryRun) {
        "$(Get-Date -Format o) dry_run_ready: no validation or competing codex exec process detected" |
            Add-Content -LiteralPath $statusLog -Encoding UTF8
        exit 0
    }

    if ($ValidationOnly) {
        $postValidation = Invoke-PostContinuationValidation
        exit $postValidation.ExitCode
    }

    if (-not $ResumeOriginalThread) {
        $transaction = $null
        try {
            $freshResult = $null
            for ($attempt = 1; $attempt -le 2; $attempt++) {
                $transaction = New-WorkspaceTransactionSnapshot
                $freshResult = Invoke-FreshContinuation -Attempt $attempt
                if ($freshResult.ExitCode -eq 0) {
                    $postValidation = Invoke-PostContinuationValidation
                    if ($postValidation.ExitCode -eq 0) {
                        Remove-WorkspaceTransactionSnapshot -Snapshot $transaction
                        "$(Get-Date -Format o) transaction_committed: attempt=$attempt model=$Model validation=$($postValidation.Log)" |
                            Add-Content -LiteralPath $statusLog -Encoding UTF8
                        "$(Get-Date -Format o) fresh_finished: exit_code=0 attempt=$attempt model=$Model events=$($freshResult.EventLog) validation=$($postValidation.Log)" |
                            Add-Content -LiteralPath $statusLog -Encoding UTF8
                        exit 0
                    }

                    $rollbackOk = Invoke-TransactionRollback -Snapshot $transaction -Reason "post_validation_failed"
                    "$(Get-Date -Format o) fresh_finished: exit_code=1 attempt=$attempt rollback_ok=$rollbackOk model=$Model events=$($freshResult.EventLog) validation=$($postValidation.Log)" |
                        Add-Content -LiteralPath $statusLog -Encoding UTF8
                    exit 1
                }

                $failureEvidence = ""
                if (Test-Path -LiteralPath $freshResult.EventLog) {
                    $failureEvidence = Get-Content -LiteralPath $freshResult.EventLog -Raw -ErrorAction SilentlyContinue
                }
                $permanentFailure = $failureEvidence -match "invalid_request_error|model is not supported|Model metadata for .* not found|unsupported model"
                $transientFailure = -not $permanentFailure -and $failureEvidence -match "failed to refresh available models|timeout waiting for child process to exit|transport channel closed|http/request failed|connection (reset|closed)|timed out"
                $rollbackOk = Invoke-TransactionRollback -Snapshot $transaction -Reason "child_exit_$($freshResult.ExitCode)"
                "$(Get-Date -Format o) fresh_attempt_failed: attempt=$attempt exit_code=$($freshResult.ExitCode) permanent=$permanentFailure transient=$transientFailure events=$($freshResult.EventLog)" |
                    Add-Content -LiteralPath $statusLog -Encoding UTF8
                if (-not $rollbackOk -or -not $transientFailure -or $attempt -ge 2) {
                    break
                }

                "$(Get-Date -Format o) fresh_retry_scheduled: next_attempt=$($attempt + 1) delay_seconds=15" |
                    Add-Content -LiteralPath $statusLog -Encoding UTF8
                Start-Sleep -Seconds 15

                $retryProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
                $retryBusyValidation = $retryProcesses | Where-Object {
                    $_.CommandLine -and
                    ($_.CommandLine -like "*pytest*" -or $_.CommandLine -like "*compileall*") -and
                    (
                        $_.CommandLine -like "*$workspaceRoot*" -or
                        ($_.ExecutablePath -and $_.ExecutablePath -like "$workspaceRoot\*")
                    )
                }
                $retryCompetingCodex = $retryProcesses | Where-Object {
                    $_.CommandLine -and
                    ($_.Name -eq "codex.exe" -or $_.Name -eq "node.exe") -and
                    $_.CommandLine -like "*codex*exec*" -and
                    (
                        (
                            -not [string]::IsNullOrWhiteSpace($ThreadId) -and
                            $_.CommandLine -like "*resume*$ThreadId*"
                        ) -or
                        $_.CommandLine -like "*-C*$Workspace*"
                    )
                }
                if ($retryBusyValidation -or $retryCompetingCodex) {
                    "$(Get-Date -Format o) fresh_retry_skipped: validation_or_competing_codex_started" |
                        Add-Content -LiteralPath $statusLog -Encoding UTF8
                    exit $freshResult.ExitCode
                }
            }

            "$(Get-Date -Format o) fresh_finished: exit_code=$($freshResult.ExitCode) attempts=$attempt model=$Model events=$($freshResult.EventLog)" |
                Add-Content -LiteralPath $statusLog -Encoding UTF8
            exit $freshResult.ExitCode
        }
        catch {
            $rollbackOk = $null
            if ($null -ne $transaction -and (Test-Path -LiteralPath $transaction.Root)) {
                $rollbackOk = Invoke-TransactionRollback -Snapshot $transaction -Reason "fresh_exception"
            }
            "$(Get-Date -Format o) fresh_failed: error=$($_.Exception.Message) events=$freshLog" |
                Add-Content -LiteralPath $statusLog -Encoding UTF8
            exit 1
        }
    }

    & codex exec resume --ignore-user-config --json --output-last-message $messageLog $ThreadId $prompt 2>&1 |
        Tee-Object -FilePath $eventLog
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        "$(Get-Date -Format o) finished: exit_code=0 thread=$ThreadId events=$eventLog" |
            Add-Content -LiteralPath $statusLog -Encoding UTF8
        exit 0
    }

    $resumeErrors = ""
    if (Test-Path -LiteralPath $eventLog) {
        $resumeErrors = Get-Content -LiteralPath $eventLog -Raw -ErrorAction SilentlyContinue
    }

    "$(Get-Date -Format o) resume_failed: exit_code=$exitCode thread=$ThreadId events=$eventLog" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8

    Invoke-WatchdogFallback -FailureEvidence $resumeErrors
}
catch {
    "$(Get-Date -Format o) failed: thread=$ThreadId error=$($_.Exception.Message)" |
        Add-Content -LiteralPath $statusLog -Encoding UTF8
    Invoke-WatchdogFallback -FailureEvidence $_.Exception.Message
}
finally {
    Pop-Location
    $iterationMutex.ReleaseMutex()
    $iterationMutex.Dispose()
}
