from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "scripts" / "codex_goal_watchdog.ps1"
STARTER = ROOT / "scripts" / "start_codex_goal_watchdog.ps1"
ENV_EXAMPLE = ROOT / ".env.example"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_watchdog_releases_iteration_mutex_on_every_early_exit() -> None:
    text = WATCHDOG.read_text(encoding="utf-8-sig")

    collision = _section(text, "if (-not $iterationCreatedNew)", "$workspaceRoot")
    assert "$iterationMutex.Dispose()" in collision
    assert collision.index("$iterationMutex.Dispose()") < collision.index("exit 0")

    validation = _section(text, "if ($busyValidation)", "$matchingResume")
    assert "$iterationMutex.ReleaseMutex()" in validation
    assert "$iterationMutex.Dispose()" in validation
    assert validation.index("$iterationMutex.Dispose()") < validation.index("exit 0")

    competing_codex = _section(text, "if ($matchingResume)", "$prompt = @\"")
    assert "$iterationMutex.ReleaseMutex()" in competing_codex
    assert "$iterationMutex.Dispose()" in competing_codex
    assert competing_codex.index("$iterationMutex.Dispose()") < competing_codex.index("exit 0")

    finalizer = text.rsplit("finally {", 1)[1]
    assert "$iterationMutex.ReleaseMutex()" in finalizer
    assert "$iterationMutex.Dispose()" in finalizer


def test_watchdog_starter_keeps_the_twenty_minute_hidden_singleton_contract() -> None:
    text = STARTER.read_text(encoding="utf-8-sig")

    assert "[int]$IntervalMinutes = 20" in text
    assert "[int]$MinimumThreadQuietMinutes = 30" in text
    assert '"Local\\CodexMsMcpGoalWatchdog20Min"' in text
    assert "Start-Sleep -Seconds ($IntervalMinutes * 60)" in text
    assert "-MinimumThreadQuietMinutes $MinimumThreadQuietMinutes" in text
    assert "mode=quiet_gated_transactional_workspace_write" in text
    assert "workspace\\codex_watchdog" in text


def test_watchdog_and_env_example_are_portable_public_templates() -> None:
    watchdog = WATCHDOG.read_text(encoding="utf-8-sig")
    starter = STARTER.read_text(encoding="utf-8-sig")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8-sig")
    combined = "\n".join((watchdog, starter, env_example))

    assert re.search(r"C:\\Users\\[^\\]+\\", combined, flags=re.IGNORECASE) is None
    assert re.search(
        r'\[string\]\$ThreadId\s*=\s*"[0-9a-f-]{36}"',
        combined,
        flags=re.IGNORECASE,
    ) is None
    assert "$env:CODEX_THREAD_ID" in watchdog
    assert "$env:CODEX_THREAD_ID" in starter
    assert '[string]$Workspace = ""' in watchdog
    assert '[string]$Workspace = ""' in starter
    assert 'Join-Path $PSScriptRoot ".."' in watchdog
    assert 'Join-Path $PSScriptRoot ".."' in starter
    assert "-ThreadId $ThreadId" in starter
    assert "MATERIAL_STUDIO_MCP_WORKSPACE=" in env_example


def test_watchdog_skips_workspace_writer_while_primary_goal_thread_is_recent() -> None:
    text = WATCHDOG.read_text(encoding="utf-8-sig")

    assert "[int]$MinimumThreadQuietMinutes = 30" in text
    assert "[switch]$IgnoreThreadActivity" in text
    assert 'Join-Path $env:USERPROFILE ".codex\\sessions"' in text
    assert 'Filter "*$ThreadId*.jsonl"' in text
    assert "skipped: active goal thread" in text
    assert text.index("skipped: active goal thread") < text.index('$prompt = @"')
    gate = _section(text, "if (\n    -not $DryRun", "$matchingResume")
    assert "-not $ValidationOnly" in gate
    assert "-not $IgnoreThreadActivity" in gate
    assert "$iterationMutex.ReleaseMutex()" in gate
    assert "$iterationMutex.Dispose()" in gate


def test_watchdog_wraps_fresh_workspace_writes_in_validated_transaction() -> None:
    text = WATCHDOG.read_text(encoding="utf-8-sig")

    assert "function New-WorkspaceTransactionSnapshot" in text
    assert "function Restore-WorkspaceTransactionSnapshot" in text
    assert "function Remove-WorkspaceTransactionSnapshot" in text
    assert "function Test-GoalThreadActivityAfterSnapshot" in text
    assert "Primary goal thread became active during the watchdog transaction" in text
    assert "$transaction = New-WorkspaceTransactionSnapshot" in text
    assert 'Invoke-TransactionRollback -Snapshot $transaction -Reason "post_validation_failed"' in text
    assert 'Invoke-TransactionRollback -Snapshot $transaction -Reason "child_exit_$($freshResult.ExitCode)"' in text
    assert "transaction_committed" in text
    assert "transaction_rollback_verified" in text


def test_watchdog_parent_validation_catches_source_and_test_corruption() -> None:
    text = WATCHDOG.read_text(encoding="utf-8-sig")

    validation = _section(text, "function Invoke-PostContinuationValidation", "function Invoke-TransactionRollback")
    assert "-p no:cacheprovider --basetemp $pytestBase" in validation
    assert '"mpt_wd_"' in validation
    assert "test_gui_project_wrapper_uses_short_paths_for_long_project_ids" in validation
    assert "test_live_modeling_request_builds_sic_4h_si_face_schottky_contact_scaffold" in validation
    assert "-m compileall -q src tests" in validation


def test_watchdog_does_not_retry_permanent_model_errors_as_network_failures() -> None:
    text = WATCHDOG.read_text(encoding="utf-8-sig")

    assert '$permanentFailure = $failureEvidence -match "invalid_request_error|model is not supported|' in text
    assert "$transientFailure = -not $permanentFailure -and" in text
    assert "permanent=$permanentFailure transient=$transientFailure" in text
