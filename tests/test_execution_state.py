from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_studio_mcp_server.gui import (
    _workspace_advisory_lock_status,
    _workspace_advisory_write_lock,
)
from material_studio_mcp_server.state.execution import (
    ExecutionAttemptHistoryError,
    begin_execution_attempt,
    canonical_json_sha256,
    execution_attempt_paths,
    finish_execution_attempt,
    inspect_execution_runtime,
    publish_terminal_execution_attempt,
)


def _begin_attempt(tmp_path: Path) -> tuple[Path, dict]:
    output_dir = tmp_path / "project" / "outputs" / "r000"
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = tmp_path / "project" / "revisions" / "r000_model_spec.json"
    script_path = tmp_path / "project" / "scripts" / "r000_build.pl"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    spec_payload = {"project_id": "execution_state", "revision": 0}
    spec_path.write_text(json.dumps(spec_payload), encoding="utf-8")
    script_path.write_text("use MaterialsScript qw(:all);", encoding="utf-8")
    receipt = begin_execution_attempt(
        output_dir,
        project_id="execution_state",
        revision=0,
        backend="materials_script",
        lock_path=output_dir / "revision_execution.lock",
        spec_path=spec_path,
        spec_payload=spec_payload,
        script_path=script_path,
        script=script_path.read_text(encoding="utf-8"),
        planned_structure_path=str(output_dir / "structure.xsd"),
        current_revision_at_start=0,
    )
    return output_dir, receipt


def _constant_lock_probe(active: bool):
    return lambda: {
        "status": "active" if active else "inactive",
        "path": "revision_execution.lock",
        "exists": True,
        "active": active,
        "observed_at": "2026-07-15T00:00:00+00:00",
        "error": None,
    }


def test_workspace_lock_status_is_read_only_and_reports_activity(tmp_path: Path) -> None:
    lock_path = tmp_path / "revision_execution.lock"

    missing = _workspace_advisory_lock_status(
        lock_path,
        workspace_root=tmp_path,
    )
    assert missing["status"] == "missing"
    assert missing["active"] is False
    assert lock_path.exists() is False

    with _workspace_advisory_write_lock(
        lock_path,
        workspace_root=tmp_path,
        timeout_seconds=1.0,
        poll_seconds=0.01,
    ):
        active = _workspace_advisory_lock_status(
            lock_path,
            workspace_root=tmp_path,
        )
        assert active["status"] == "active"
        assert active["active"] is True

    inactive = _workspace_advisory_lock_status(
        lock_path,
        workspace_root=tmp_path,
    )
    assert inactive["status"] == "inactive"
    assert inactive["active"] is False


def test_execution_attempt_lifecycle_is_hash_chained_and_observable(
    tmp_path: Path,
) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    running = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(True),
    )
    assert running["status"] == "running"
    assert running["active"] is True
    assert running["latest_attempt"]["attempt_id"] == started["attempt"]["attempt_id"]
    assert running["journal"]["event_count"] == 1

    result_path = output_dir / "result_metadata.json"
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=result_path,
    )
    terminal = publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    result_metadata = {
        "success": True,
        "execution_attempt": completed.model_dump(mode="json"),
    }
    result_path.write_text(json.dumps(result_metadata), encoding="utf-8")

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=result_metadata,
        lock_probe=_constant_lock_probe(False),
    )
    assert observed["status"] == "completed"
    assert observed["active"] is False
    assert observed["latest_attempt"]["result_success"] is True
    assert observed["journal"]["event_count"] == 2
    assert observed["journal"]["attempt_count"] == 1
    assert observed["journal"]["incomplete_attempt_count"] == 0
    assert [event["event_type"] for event in observed["journal"]["recent_events"]] == [
        "started",
        "completed",
    ]
    assert observed["consistency"]["ok"] is True
    assert terminal["state"]["latest_attempt"]["status"] == "completed"

    events = [
        json.loads(line)
        for line in Path(terminal["events_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["previous_event_sha256"] is None
    assert events[1]["previous_event_sha256"] == events[0]["event_record_sha256"]

    mismatched = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=result_metadata,
        lock_probe=_constant_lock_probe(False),
        expected_spec_payload={"project_id": "execution_state", "revision": 99},
    )
    assert mismatched["status"] == "identity_mismatch"
    assert "execution_attempt_spec_sha256_mismatch" in mismatched["consistency"][
        "issue_codes"
    ]


def test_result_attempt_planned_structure_drift_from_journal_is_history_invalid(
    tmp_path: Path,
) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    result_path = output_dir / "result_metadata.json"
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=result_path,
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    tampered_attempt = completed.model_copy(
        update={
            "planned_structure_path": str(output_dir / "alternate_structure.xsd")
        }
    )
    result_metadata = {
        "success": True,
        "execution_attempt": tampered_attempt.model_dump(mode="json"),
    }

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=result_metadata,
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "history_invalid"
    assert observed["attempt_record_source"] == "journal"
    assert observed["consistency"]["ok"] is False
    assert "execution_attempt_result_journal_record_mismatch" in observed[
        "consistency"
    ]["issue_codes"]


def test_running_attempt_without_active_lock_is_interrupted(tmp_path: Path) -> None:
    output_dir, started = _begin_attempt(tmp_path)

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "interrupted"
    assert observed["latest_attempt"]["attempt_id"] == started["attempt"]["attempt_id"]
    assert observed["continuation"]["automatic_retry_allowed"] is False
    assert observed["continuation"]["explicit_execute_confirmation_required"] is True
    assert observed["continuation"]["execution_may_still_be_running"] is True
    assert observed["continuation"]["recommended_payload"] == {
        "project_id": "execution_state",
        "include_gui_status": False,
    }
    assert (
        observed["continuation"]["explicit_retry_tool"]
        == "material_studio_gui_apply_current_revision"
    )


def test_new_explicit_attempt_recovers_prior_running_attempt(tmp_path: Path) -> None:
    output_dir, first = _begin_attempt(tmp_path)

    _, second = _begin_attempt(tmp_path)

    recovered = second["recovered_interrupted_attempts"]
    assert second["attempt"]["sequence"] == 2
    assert len(recovered) == 1
    assert recovered[0]["attempt_id"] == first["attempt"]["attempt_id"]
    assert recovered[0]["sequence"] == 1
    assert recovered[0]["status"] == "interrupted"
    assert recovered[0]["finished_at"] is not None
    assert recovered[0]["error_type"] == "ExecutionAttemptInterrupted"
    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(True),
    )
    assert observed["status"] == "running"
    assert observed["journal"]["event_count"] == 3
    assert observed["journal"]["attempt_count"] == 2
    assert [event["event_type"] for event in observed["journal"]["recent_events"]] == [
        "started",
        "interrupted",
        "started",
    ]
    assert observed["journal"]["incomplete_attempt_ids"] == [
        second["attempt"]["attempt_id"]
    ]


def test_terminal_event_cannot_change_started_attempt_identity(tmp_path: Path) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=output_dir / "result_metadata.json",
    ).model_copy(update={"backend": "tampered_backend"})
    paths = execution_attempt_paths(output_dir)

    with pytest.raises(ExecutionAttemptHistoryError, match="immutable fields.*backend"):
        publish_terminal_execution_attempt(
            output_dir,
            completed.model_dump(mode="json"),
        )

    assert len(paths["events"].read_text(encoding="utf-8").splitlines()) == 1


def test_rehashed_tampered_terminal_event_is_rejected_on_read(tmp_path: Path) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=output_dir / "result_metadata.json",
    )
    terminal = publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    events_path = Path(terminal["events_path"])
    events = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    events[1]["attempt"]["backend"] = "tampered_backend"
    events[1]["event_record_sha256"] = canonical_json_sha256(
        {
            key: value
            for key, value in events[1].items()
            if key != "event_record_sha256"
        }
    )
    events_path.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(False),
    )
    assert observed["status"] == "history_invalid"
    assert "immutable fields changed" in observed["journal"]["read_error"]
    assert "backend" in observed["journal"]["read_error"]


def test_rehashed_overlapping_running_attempt_is_rejected(tmp_path: Path) -> None:
    output_dir, _ = _begin_attempt(tmp_path)
    events_path = execution_attempt_paths(output_dir)["events"]
    first = json.loads(events_path.read_text(encoding="utf-8").strip())
    second = {
        **first,
        "event_id": "1" * 32,
        "previous_event_sha256": first["event_record_sha256"],
        "attempt": {
            **first["attempt"],
            "attempt_id": "2" * 32,
            "sequence": 2,
        },
    }
    second["event_record_sha256"] = canonical_json_sha256(
        {
            key: value
            for key, value in second.items()
            if key != "event_record_sha256"
        }
    )
    events_path.write_text(
        json.dumps(first, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + json.dumps(second, ensure_ascii=False, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "history_invalid"
    assert "another attempt is still running" in observed["journal"]["read_error"]


def test_unsuccessful_backend_result_has_failed_runtime_status(tmp_path: Path) -> None:
    output_dir, started = _begin_attempt(tmp_path)

    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=False,
        result_metadata_path=output_dir / "result_metadata.json",
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    result_metadata = {
        "success": False,
        "execution_attempt": completed.model_dump(mode="json"),
    }

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=result_metadata,
        lock_probe=_constant_lock_probe(False),
    )

    assert completed.status == "completed"
    assert completed.result_success is False
    assert completed.error_type is None
    assert observed["status"] == "failed"
    assert observed["consistency"]["ok"] is True


def test_completed_attempt_without_canonical_result_is_not_trusted(
    tmp_path: Path,
) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=output_dir / "result_metadata.json",
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "result_missing"
    assert "execution_attempt_result_metadata_missing" in observed["consistency"][
        "issue_codes"
    ]
    assert observed["continuation"]["automatic_retry_allowed"] is False


def test_managed_result_without_attempt_journal_is_history_invalid(
    tmp_path: Path,
) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=output_dir / "result_metadata.json",
    )
    paths = execution_attempt_paths(output_dir)
    paths["events"].unlink()
    paths["state"].unlink()

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata={
            "success": True,
            "execution_attempt": completed.model_dump(mode="json"),
        },
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "history_invalid"
    assert "result_execution_attempt_journal_missing" in observed["consistency"][
        "issue_codes"
    ]
    assert observed["continuation"]["automatic_retry_allowed"] is False


def test_missing_saved_script_is_an_identity_mismatch(tmp_path: Path) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    result_path = output_dir / "result_metadata.json"
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=result_path,
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    result_metadata = {
        "success": True,
        "execution_attempt": completed.model_dump(mode="json"),
    }
    result_path.write_text(json.dumps(result_metadata), encoding="utf-8")
    script_path = Path(started["attempt"]["script_path"])
    expected_script = script_path.read_text(encoding="utf-8")
    script_path.unlink()

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=result_metadata,
        lock_probe=_constant_lock_probe(False),
        expected_script=expected_script,
        expected_script_path=script_path,
    )

    assert observed["status"] == "identity_mismatch"
    assert "execution_attempt_script_artifact_missing" in observed["consistency"][
        "issue_codes"
    ]


def test_modified_saved_script_is_an_identity_mismatch(tmp_path: Path) -> None:
    output_dir, started = _begin_attempt(tmp_path)
    result_path = output_dir / "result_metadata.json"
    completed = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=result_path,
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    result_metadata = {
        "success": True,
        "execution_attempt": completed.model_dump(mode="json"),
    }
    result_path.write_text(json.dumps(result_metadata), encoding="utf-8")
    script_path = Path(started["attempt"]["script_path"])
    expected_script = script_path.read_text(encoding="utf-8")
    script_path.write_text(expected_script + "\n# changed", encoding="utf-8")

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=result_metadata,
        lock_probe=_constant_lock_probe(False),
        expected_script=expected_script,
        expected_script_path=script_path,
    )

    assert observed["status"] == "identity_mismatch"
    assert (
        "execution_attempt_script_artifact_sha256_mismatch"
        in observed["consistency"]["issue_codes"]
    )


def test_changing_lock_observation_reports_transitioning(tmp_path: Path) -> None:
    output_dir, _ = _begin_attempt(tmp_path)
    observations = iter((True, False))

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=lambda: {
            "status": "probe",
            "path": "revision_execution.lock",
            "exists": True,
            "active": next(observations),
            "observed_at": "2026-07-15T00:00:00+00:00",
            "error": None,
        },
    )

    assert observed["status"] == "transitioning"
    assert observed["active"] is None
    assert observed["lock_observation_stable"] is False
    assert observed["continuation"]["automatic_retry_allowed"] is False


def test_invalid_attempt_journal_blocks_extension(tmp_path: Path) -> None:
    output_dir = tmp_path / "project" / "outputs" / "r000"
    output_dir.mkdir(parents=True)
    paths = execution_attempt_paths(output_dir)
    paths["events"].write_text('{"partial":true}', encoding="utf-8")

    with pytest.raises(ExecutionAttemptHistoryError, match="newline-terminated"):
        begin_execution_attempt(
            output_dir,
            project_id="execution_state",
            revision=0,
            backend="materials_script",
            lock_path=output_dir / "revision_execution.lock",
            spec_path=tmp_path / "spec.json",
            spec_payload={"project_id": "execution_state", "revision": 0},
            script_path=None,
            script="use MaterialsScript qw(:all);",
            planned_structure_path=None,
            current_revision_at_start=0,
        )
    assert paths["state"].exists() is False
    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata=None,
        lock_probe=_constant_lock_probe(False),
    )
    assert observed["status"] == "history_invalid"
    assert observed["consistency"]["issue_codes"] == [
        "execution_attempt_journal_invalid"
    ]


def test_legacy_result_metadata_remains_observable(tmp_path: Path) -> None:
    output_dir = tmp_path / "project" / "outputs" / "r000"
    output_dir.mkdir(parents=True)

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata={"success": True, "return_code": 0},
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "legacy_completed"
    assert observed["latest_attempt"] is None
    assert observed["consistency"]["ok"] is True


def test_repeated_attempts_keep_independent_sequences(tmp_path: Path) -> None:
    output_dir, first = _begin_attempt(tmp_path)
    completed = finish_execution_attempt(
        first["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=output_dir / "result_metadata.json",
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    _, second = _begin_attempt(tmp_path)

    assert first["attempt"]["sequence"] == 1
    assert second["attempt"]["sequence"] == 2
    assert second["recovered_interrupted_attempts"] == []
    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata={
            "success": True,
            "execution_attempt": completed.model_dump(mode="json"),
        },
        lock_probe=_constant_lock_probe(True),
    )
    assert observed["status"] == "running"
    assert observed["latest_attempt"]["attempt_id"] == second["attempt"]["attempt_id"]
    assert observed["journal"]["event_count"] == 3
    assert observed["journal"]["attempt_count"] == 2
    assert observed["journal"]["incomplete_attempt_ids"] == [
        second["attempt"]["attempt_id"]
    ]
    assert "execution_attempt_result_stale" in observed["consistency"]["issue_codes"]


def test_repeated_attempt_cannot_change_revision_binding(tmp_path: Path) -> None:
    output_dir, first = _begin_attempt(tmp_path)
    completed = finish_execution_attempt(
        first["attempt"],
        current_revision_after_execution=0,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=output_dir / "result_metadata.json",
    )
    publish_terminal_execution_attempt(
        output_dir,
        completed.model_dump(mode="json"),
    )
    spec_path = Path(first["attempt"]["spec_path"])
    script_path = Path(first["attempt"]["script_path"])

    with pytest.raises(
        ExecutionAttemptHistoryError,
        match="revision binding changed.*spec_sha256",
    ):
        begin_execution_attempt(
            output_dir,
            project_id="execution_state",
            revision=0,
            backend="materials_script",
            lock_path=output_dir / "revision_execution.lock",
            spec_path=spec_path,
            spec_payload={"project_id": "execution_state", "revision": 999},
            script_path=script_path,
            script=script_path.read_text(encoding="utf-8"),
            planned_structure_path=str(output_dir / "structure.xsd"),
            current_revision_at_start=0,
        )

    assert len(
        execution_attempt_paths(output_dir)["events"]
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 2


def test_invalid_managed_result_attempt_is_history_invalid(tmp_path: Path) -> None:
    output_dir = tmp_path / "project" / "outputs" / "r000"
    output_dir.mkdir(parents=True)

    observed = inspect_execution_runtime(
        output_dir,
        project_id="execution_state",
        revision=0,
        result_metadata={
            "success": True,
            "execution_attempt": {"attempt_id": "invalid"},
        },
        lock_probe=_constant_lock_probe(False),
    )

    assert observed["status"] == "history_invalid"
    assert "result_execution_attempt_invalid" in observed["consistency"][
        "issue_codes"
    ]
