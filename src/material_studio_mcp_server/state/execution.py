"""Durable execution-attempt state for structured model revisions."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .store import atomic_write_text


EXECUTION_ATTEMPT_SCHEMA_VERSION = "material_studio_execution_attempt_v1"
EXECUTION_ATTEMPT_EVENT_SCHEMA_VERSION = "material_studio_execution_attempt_event_v1"
EXECUTION_ATTEMPT_STATE_SCHEMA_VERSION = "material_studio_execution_attempt_state_v1"
EXECUTION_RUNTIME_SCHEMA_VERSION = "material_studio_execution_runtime_v1"
EXECUTION_ATTEMPT_STATE_FILENAME = "execution_attempt_state.json"
EXECUTION_ATTEMPT_EVENTS_FILENAME = "execution_attempts.jsonl"
EXECUTION_ATTEMPT_MAX_EVENTS = 10_000
EXECUTION_ATTEMPT_MAX_JOURNAL_BYTES = 16 * 1024 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[0-9a-f]{32}$"
_ATTEMPT_IMMUTABLE_FIELDS = (
    "schema_version",
    "attempt_id",
    "sequence",
    "project_id",
    "revision",
    "backend",
    "process_id",
    "started_at",
    "lock_path",
    "spec_path",
    "spec_sha256",
    "script_path",
    "script_sha256",
    "planned_structure_path",
    "current_revision_at_start",
)
_REVISION_BINDING_FIELDS = (
    "project_id",
    "revision",
    "backend",
    "lock_path",
    "spec_path",
    "spec_sha256",
    "script_path",
    "script_sha256",
    "planned_structure_path",
    "current_revision_at_start",
)


class ExecutionAttemptHistoryError(RuntimeError):
    """Raised when an execution attempt journal cannot be safely extended."""


class ExecutionAttemptRecord(BaseModel):
    """One immutable identity with a mutable terminal execution outcome."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[EXECUTION_ATTEMPT_SCHEMA_VERSION] = (
        EXECUTION_ATTEMPT_SCHEMA_VERSION
    )
    attempt_id: str = Field(pattern=_ID_PATTERN)
    sequence: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=0)
    backend: str = Field(min_length=1, max_length=120)
    status: Literal["running", "completed", "failed", "interrupted"]
    process_id: int = Field(ge=1)
    started_at: str = Field(min_length=1, max_length=80)
    finished_at: str | None = Field(default=None, max_length=80)
    lock_path: str = Field(min_length=1, max_length=4096)
    spec_path: str = Field(min_length=1, max_length=4096)
    spec_sha256: str = Field(pattern=_SHA256_PATTERN)
    script_path: str | None = Field(default=None, max_length=4096)
    script_sha256: str = Field(pattern=_SHA256_PATTERN)
    planned_structure_path: str | None = Field(default=None, max_length=4096)
    current_revision_at_start: int = Field(ge=0)
    current_revision_after_execution: int | None = Field(default=None, ge=0)
    current_revision_still_current: bool | None = None
    result_success: bool | None = None
    result_metadata_path: str | None = Field(default=None, max_length=4096)
    error_type: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_lifecycle_fields(self) -> "ExecutionAttemptRecord":
        terminal_fields = (
            self.finished_at,
            self.current_revision_after_execution,
            self.current_revision_still_current,
            self.result_success,
            self.result_metadata_path,
            self.error_type,
            self.error,
        )
        if self.status == "running" and any(
            value is not None for value in terminal_fields
        ):
            raise ValueError("running execution attempt contains terminal fields")
        if self.status != "running" and self.finished_at is None:
            raise ValueError("terminal execution attempt requires finished_at")
        if self.status == "completed" and (
            self.error_type is not None or self.error is not None
        ):
            raise ValueError("completed execution attempt cannot contain an error")
        if self.status in {"failed", "interrupted"} and (
            self.error_type is None or self.error is None
        ):
            raise ValueError(
                f"{self.status} execution attempt requires bounded error details"
            )
        return self


class ExecutionAttemptEvent(BaseModel):
    """One hash-linked lifecycle event in the per-revision journal."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[EXECUTION_ATTEMPT_EVENT_SCHEMA_VERSION] = (
        EXECUTION_ATTEMPT_EVENT_SCHEMA_VERSION
    )
    event_id: str = Field(pattern=_ID_PATTERN)
    event_type: Literal["started", "completed", "failed", "interrupted"]
    recorded_at: str = Field(min_length=1, max_length=80)
    previous_event_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    event_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    attempt: ExecutionAttemptRecord


class ExecutionAttemptState(BaseModel):
    """Latest attempt cache bound to the append-only event journal head."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[EXECUTION_ATTEMPT_STATE_SCHEMA_VERSION] = (
        EXECUTION_ATTEMPT_STATE_SCHEMA_VERSION
    )
    project_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=0)
    updated_at: str = Field(min_length=1, max_length=80)
    event_count: int = Field(ge=1)
    latest_event_id: str = Field(pattern=_ID_PATTERN)
    latest_event_sha256: str = Field(pattern=_SHA256_PATTERN)
    latest_attempt: ExecutionAttemptRecord


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for persisted attempt records."""

    return datetime.now(timezone.utc).isoformat()


def canonical_json_sha256(payload: Any) -> str:
    """Hash JSON-compatible data with deterministic encoding."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def text_sha256(value: str) -> str:
    """Hash one generated script exactly as executed."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def execution_attempt_paths(output_dir: Path) -> dict[str, Path]:
    """Return the stable state and journal paths for one revision output."""

    resolved = output_dir.expanduser().resolve()
    return {
        "state": resolved / EXECUTION_ATTEMPT_STATE_FILENAME,
        "events": resolved / EXECUTION_ATTEMPT_EVENTS_FILENAME,
    }


def _event_record_sha256(payload: dict[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key != "event_record_sha256"
    }
    return canonical_json_sha256(canonical)


def _event_type_matches_attempt(event: ExecutionAttemptEvent) -> bool:
    return event.event_type == (
        "started" if event.attempt.status == "running" else event.attempt.status
    )


def _validate_appended_event(
    events: list[ExecutionAttemptEvent],
    event: ExecutionAttemptEvent,
) -> None:
    """Reject a lifecycle event before it can make the journal invalid."""

    if any(existing.event_id == event.event_id for existing in events):
        raise ExecutionAttemptHistoryError(
            f"Execution attempt journal repeats event_id {event.event_id}"
        )
    expected_previous = events[-1].event_record_sha256 if events else None
    if event.previous_event_sha256 != expected_previous:
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal append does not extend the current digest head"
        )
    if events and (
        event.attempt.project_id,
        event.attempt.revision,
    ) != (
        events[0].attempt.project_id,
        events[0].attempt.revision,
    ):
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal append changes project/revision identity"
        )

    attempt_id = event.attempt.attempt_id
    sequence = event.attempt.sequence
    starts = [existing for existing in events if existing.event_type == "started"]
    if event.event_type == "started":
        latest_status_by_attempt: dict[str, str] = {}
        for existing in events:
            latest_status_by_attempt[existing.attempt.attempt_id] = (
                existing.attempt.status
            )
        if any(status == "running" for status in latest_status_by_attempt.values()):
            raise ExecutionAttemptHistoryError(
                "Execution attempt starts while another attempt is still running"
            )
        if any(existing.attempt.attempt_id == attempt_id for existing in events):
            raise ExecutionAttemptHistoryError(
                f"Execution attempt {attempt_id} starts more than once"
            )
        if any(existing.attempt.sequence == sequence for existing in starts):
            raise ExecutionAttemptHistoryError(
                f"Execution attempt sequence {sequence} is reused"
            )
        expected_sequence = max(
            (existing.attempt.sequence for existing in starts),
            default=0,
        ) + 1
        if sequence != expected_sequence:
            raise ExecutionAttemptHistoryError(
                "Execution attempt sequence is not contiguous: "
                f"expected {expected_sequence}, found {sequence}"
            )
        if starts:
            revision_binding_mismatches = [
                field_name
                for field_name in _REVISION_BINDING_FIELDS
                if getattr(starts[0].attempt, field_name)
                != getattr(event.attempt, field_name)
            ]
            if revision_binding_mismatches:
                raise ExecutionAttemptHistoryError(
                    "Execution attempt revision binding changed: "
                    f"{', '.join(revision_binding_mismatches)}"
                )
        return

    started = next(
        (
            existing.attempt
            for existing in starts
            if existing.attempt.attempt_id == attempt_id
        ),
        None,
    )
    if started is None or started.sequence != sequence:
        raise ExecutionAttemptHistoryError(
            "Execution attempt terminal event has no matching start"
        )
    immutable_mismatches = [
        field_name
        for field_name in _ATTEMPT_IMMUTABLE_FIELDS
        if getattr(started, field_name) != getattr(event.attempt, field_name)
    ]
    if immutable_mismatches:
        raise ExecutionAttemptHistoryError(
            "Execution attempt immutable fields changed: "
            f"{', '.join(immutable_mismatches)}"
        )
    prior_terminal_types = [
        existing.event_type
        for existing in events
        if existing.attempt.attempt_id == attempt_id
        and existing.event_type != "started"
    ]
    if prior_terminal_types and not (
        prior_terminal_types == ["completed"] and event.event_type == "failed"
    ):
        raise ExecutionAttemptHistoryError(
            "Execution attempt has an invalid repeated terminal event"
        )


def _load_events_strict(path: Path) -> tuple[str, list[ExecutionAttemptEvent]]:
    if not path.exists():
        return "", []
    size_bytes = path.stat().st_size
    if size_bytes > EXECUTION_ATTEMPT_MAX_JOURNAL_BYTES:
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal exceeds the bounded size limit"
        )
    content = path.read_text(encoding="utf-8")
    if content and not content.endswith("\n"):
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal is not newline-terminated"
        )
    lines = content.splitlines()
    if len(lines) > EXECUTION_ATTEMPT_MAX_EVENTS:
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal exceeds the bounded event limit"
        )
    events: list[ExecutionAttemptEvent] = []
    previous_digest: str | None = None
    seen_event_ids: set[str] = set()
    attempts_by_id: dict[str, ExecutionAttemptRecord] = {}
    sequence_to_attempt_id: dict[int, str] = {}
    terminal_status_by_attempt_id: dict[str, str] = {}
    running_attempt_ids: set[str] = set()
    journal_identity: tuple[str, int] | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ExecutionAttemptHistoryError(
                f"Execution attempt journal contains a blank line at {line_number}"
            )
        try:
            raw = json.loads(line)
            event = ExecutionAttemptEvent.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ExecutionAttemptHistoryError(
                f"Execution attempt journal line {line_number} is invalid: {exc}"
            ) from exc
        if event.event_id in seen_event_ids:
            raise ExecutionAttemptHistoryError(
                f"Execution attempt journal repeats event_id {event.event_id}"
            )
        if event.previous_event_sha256 != previous_digest:
            raise ExecutionAttemptHistoryError(
                f"Execution attempt journal chain diverges at line {line_number}"
            )
        if event.event_record_sha256 != _event_record_sha256(raw):
            raise ExecutionAttemptHistoryError(
                f"Execution attempt journal digest mismatch at line {line_number}"
            )
        if not _event_type_matches_attempt(event):
            raise ExecutionAttemptHistoryError(
                f"Execution attempt event/status mismatch at line {line_number}"
            )
        event_identity = (event.attempt.project_id, event.attempt.revision)
        if journal_identity is None:
            journal_identity = event_identity
        elif event_identity != journal_identity:
            raise ExecutionAttemptHistoryError(
                f"Execution attempt identity changes at line {line_number}"
            )
        attempt_id = event.attempt.attempt_id
        sequence = event.attempt.sequence
        if event.event_type == "started":
            if running_attempt_ids:
                raise ExecutionAttemptHistoryError(
                    "Execution attempt starts while another attempt is still running "
                    f"at line {line_number}"
                )
            if attempt_id in attempts_by_id:
                raise ExecutionAttemptHistoryError(
                    f"Execution attempt {attempt_id} starts more than once"
                )
            if sequence in sequence_to_attempt_id:
                raise ExecutionAttemptHistoryError(
                    f"Execution attempt sequence {sequence} is reused"
                )
            expected_sequence = max(sequence_to_attempt_id, default=0) + 1
            if sequence != expected_sequence:
                raise ExecutionAttemptHistoryError(
                    "Execution attempt sequence is not contiguous at line "
                    f"{line_number}: expected {expected_sequence}, found {sequence}"
                )
            if attempts_by_id:
                reference_attempt = next(iter(attempts_by_id.values()))
                revision_binding_mismatches = [
                    field_name
                    for field_name in _REVISION_BINDING_FIELDS
                    if getattr(reference_attempt, field_name)
                    != getattr(event.attempt, field_name)
                ]
                if revision_binding_mismatches:
                    raise ExecutionAttemptHistoryError(
                        "Execution attempt revision binding changed at line "
                        f"{line_number}: {', '.join(revision_binding_mismatches)}"
                    )
            attempts_by_id[attempt_id] = event.attempt
            sequence_to_attempt_id[sequence] = attempt_id
            running_attempt_ids.add(attempt_id)
        else:
            started = attempts_by_id.get(attempt_id)
            if started is None or started.sequence != sequence:
                raise ExecutionAttemptHistoryError(
                    "Execution attempt terminal event has no matching start at line "
                    f"{line_number}"
                )
            immutable_mismatches = [
                field_name
                for field_name in _ATTEMPT_IMMUTABLE_FIELDS
                if getattr(started, field_name) != getattr(event.attempt, field_name)
            ]
            if immutable_mismatches:
                raise ExecutionAttemptHistoryError(
                    "Execution attempt immutable fields changed at line "
                    f"{line_number}: {', '.join(immutable_mismatches)}"
                )
            previous_terminal = terminal_status_by_attempt_id.get(attempt_id)
            if previous_terminal is not None and not (
                previous_terminal == "completed" and event.event_type == "failed"
            ):
                raise ExecutionAttemptHistoryError(
                    "Execution attempt has an invalid repeated terminal event at line "
                    f"{line_number}"
                )
            terminal_status_by_attempt_id[attempt_id] = event.event_type
            if previous_terminal is None:
                running_attempt_ids.discard(attempt_id)
        seen_event_ids.add(event.event_id)
        previous_digest = event.event_record_sha256
        events.append(event)
    return content, events


def _append_attempt_event(
    output_dir: Path,
    attempt: ExecutionAttemptRecord,
    *,
    event_type: Literal["started", "completed", "failed", "interrupted"],
) -> dict[str, Any]:
    paths = execution_attempt_paths(output_dir)
    existing_content, events = _load_events_strict(paths["events"])
    if len(events) >= EXECUTION_ATTEMPT_MAX_EVENTS:
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal cannot accept another bounded event"
        )
    event_payload: dict[str, Any] = {
        "schema_version": EXECUTION_ATTEMPT_EVENT_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "recorded_at": utc_now_iso(),
        "previous_event_sha256": (
            events[-1].event_record_sha256 if events else None
        ),
        "attempt": attempt.model_dump(mode="json"),
    }
    event_payload["event_record_sha256"] = _event_record_sha256(event_payload)
    event = ExecutionAttemptEvent.model_validate(event_payload)
    if not _event_type_matches_attempt(event):
        raise ValueError("Execution attempt event does not match attempt status")
    _validate_appended_event(events, event)
    serialized_event = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    next_content = existing_content + serialized_event + "\n"
    if len(next_content.encode("utf-8")) > EXECUTION_ATTEMPT_MAX_JOURNAL_BYTES:
        raise ExecutionAttemptHistoryError(
            "Execution attempt journal cannot accept another bounded record"
        )
    atomic_write_text(paths["events"], next_content)
    state = ExecutionAttemptState(
        project_id=attempt.project_id,
        revision=attempt.revision,
        updated_at=event.recorded_at,
        event_count=len(events) + 1,
        latest_event_id=event.event_id,
        latest_event_sha256=event.event_record_sha256,
        latest_attempt=attempt,
    )
    state_error: str | None = None
    try:
        atomic_write_text(
            paths["state"],
            json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
    except OSError as exc:
        state_error = str(exc)[:2000]
    return {
        "attempt": attempt.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
        "state": state.model_dump(mode="json"),
        "state_path": str(paths["state"]),
        "events_path": str(paths["events"]),
        "state_publish_error": state_error,
    }


def begin_execution_attempt(
    output_dir: Path,
    *,
    project_id: str,
    revision: int,
    backend: str,
    lock_path: Path,
    spec_path: Path,
    spec_payload: dict[str, Any],
    script_path: Path | None,
    script: str,
    planned_structure_path: str | None,
    current_revision_at_start: int,
) -> dict[str, Any]:
    """Persist a running attempt before invoking an execution backend."""

    _, events = _load_events_strict(execution_attempt_paths(output_dir)["events"])
    latest_by_attempt: dict[str, ExecutionAttemptRecord] = {}
    for event in events:
        latest_by_attempt[event.attempt.attempt_id] = event.attempt
    recovered_interrupted_attempts: list[dict[str, Any]] = []
    for incomplete in sorted(
        (
            attempt
            for attempt in latest_by_attempt.values()
            if attempt.status == "running"
        ),
        key=lambda item: item.sequence,
    ):
        interrupted = ExecutionAttemptRecord.model_validate(
            {
                **incomplete.model_dump(mode="json"),
                "status": "interrupted",
                "finished_at": utc_now_iso(),
                "error_type": "ExecutionAttemptInterrupted",
                "error": (
                    "A later explicit execution acquired the revision lock before "
                    "this attempt recorded a terminal event."
                ),
            }
        )
        _append_attempt_event(
            output_dir,
            interrupted,
            event_type="interrupted",
        )
        recovered_interrupted_attempts.append(
            interrupted.model_dump(mode="json")
        )
    _, events = _load_events_strict(execution_attempt_paths(output_dir)["events"])
    sequence = max((event.attempt.sequence for event in events), default=0) + 1
    attempt = ExecutionAttemptRecord(
        attempt_id=uuid.uuid4().hex,
        sequence=sequence,
        project_id=project_id,
        revision=revision,
        backend=backend,
        status="running",
        process_id=os.getpid(),
        started_at=utc_now_iso(),
        lock_path=str(lock_path.resolve()),
        spec_path=str(spec_path.resolve()),
        spec_sha256=canonical_json_sha256(spec_payload),
        script_path=str(script_path.resolve()) if script_path is not None else None,
        script_sha256=text_sha256(script),
        planned_structure_path=planned_structure_path,
        current_revision_at_start=current_revision_at_start,
    )
    receipt = _append_attempt_event(output_dir, attempt, event_type="started")
    receipt["recovered_interrupted_attempts"] = recovered_interrupted_attempts
    return receipt


def finish_execution_attempt(
    attempt_payload: dict[str, Any],
    *,
    current_revision_after_execution: int | None,
    current_revision_still_current: bool | None,
    result_success: bool | None,
    result_metadata_path: Path | None,
    error: Exception | None = None,
) -> ExecutionAttemptRecord:
    """Build a terminal record for a previously started attempt."""

    running = ExecutionAttemptRecord.model_validate(attempt_payload)
    if running.status != "running":
        raise ValueError("Only a running execution attempt can be finalized")
    status: Literal["completed", "failed"] = "failed" if error else "completed"
    terminal = ExecutionAttemptRecord.model_validate(
        {
            **running.model_dump(mode="json"),
            "status": status,
            "finished_at": utc_now_iso(),
            "current_revision_after_execution": current_revision_after_execution,
            "current_revision_still_current": current_revision_still_current,
            "result_success": result_success,
            "result_metadata_path": (
                str(result_metadata_path.resolve())
                if result_metadata_path is not None
                else None
            ),
            "error_type": error.__class__.__name__ if error is not None else None,
            "error": (str(error).strip() or error.__class__.__name__)[:2000]
            if error is not None
            else None,
        }
    )
    return terminal


def publish_terminal_execution_attempt(
    output_dir: Path,
    attempt_payload: dict[str, Any],
) -> dict[str, Any]:
    """Append and cache one validated terminal attempt record."""

    attempt = ExecutionAttemptRecord.model_validate(attempt_payload)
    if attempt.status not in {"completed", "failed", "interrupted"}:
        raise ValueError("Execution attempt must be terminal before publication")
    return _append_attempt_event(
        output_dir,
        attempt,
        event_type=attempt.status,
    )


def _read_state(path: Path) -> tuple[ExecutionAttemptState | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ExecutionAttemptState.model_validate(payload), None
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        return None, str(exc)[:2000]


def _read_journal(
    path: Path,
) -> tuple[list[ExecutionAttemptEvent], dict[str, Any]]:
    try:
        _, events = _load_events_strict(path)
        latest_status_by_attempt: dict[str, str] = {}
        for event in events:
            latest_status_by_attempt[event.attempt.attempt_id] = event.attempt.status
        incomplete_attempt_ids = sorted(
            attempt_id
            for attempt_id, status in latest_status_by_attempt.items()
            if status == "running"
        )
        recent_events = [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "recorded_at": event.recorded_at,
                "event_record_sha256": event.event_record_sha256,
                "attempt_id": event.attempt.attempt_id,
                "sequence": event.attempt.sequence,
                "status": event.attempt.status,
                "backend": event.attempt.backend,
                "process_id": event.attempt.process_id,
                "result_success": event.attempt.result_success,
                "error_type": event.attempt.error_type,
            }
            for event in events[-10:]
        ]
        return events, {
            "status": "loaded" if path.exists() else "missing",
            "path": str(path),
            "exists": path.exists(),
            "event_count": len(events),
            "attempt_count": len(
                {event.attempt.attempt_id for event in events if event.event_type == "started"}
            ),
            "incomplete_attempt_count": len(incomplete_attempt_ids),
            "incomplete_attempt_ids": incomplete_attempt_ids,
            "latest_event_id": events[-1].event_id if events else None,
            "latest_event_sha256": (
                events[-1].event_record_sha256 if events else None
            ),
            "recent_events": recent_events,
            "read_error": None,
        }
    except (ExecutionAttemptHistoryError, OSError, UnicodeError) as exc:
        return [], {
            "status": "invalid",
            "path": str(path),
            "exists": path.exists(),
            "event_count": 0,
            "attempt_count": 0,
            "incomplete_attempt_count": 0,
            "incomplete_attempt_ids": [],
            "latest_event_id": None,
            "latest_event_sha256": None,
            "recent_events": [],
            "read_error": str(exc)[:2000],
        }


def inspect_execution_runtime(
    output_dir: Path,
    *,
    project_id: str,
    revision: int,
    result_metadata: dict[str, Any] | None,
    lock_probe: Callable[[], dict[str, Any]],
    expected_spec_payload: dict[str, Any] | None = None,
    expected_script: str | None = None,
    expected_script_path: Path | None = None,
    expected_lock_path: Path | None = None,
    expected_result_metadata_path: Path | None = None,
) -> dict[str, Any]:
    """Reconcile durable attempts with two read-only kernel-lock observations."""

    paths = execution_attempt_paths(output_dir)
    first_probe = lock_probe()
    events, journal = _read_journal(paths["events"])
    state, state_error = _read_state(paths["state"])
    second_probe = lock_probe()
    first_active = first_probe.get("active")
    second_active = second_probe.get("active")
    lock_observation_stable = (
        first_active is not None
        and second_active is not None
        and first_active == second_active
    )
    active = first_active if lock_observation_stable else None

    script_artifact: dict[str, Any] = {
        "path": None,
        "exists": None,
        "sha256": None,
        "expected_sha256": (
            text_sha256(expected_script) if expected_script is not None else None
        ),
        "matches_expected_script": None,
        "read_error": None,
    }
    if expected_script_path is not None:
        try:
            resolved_script_path = expected_script_path.expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            script_artifact["path"] = str(expected_script_path)
            script_artifact["exists"] = False
            script_artifact["read_error"] = str(exc)[:2000]
        else:
            script_artifact["path"] = str(resolved_script_path)
            try:
                script_artifact["exists"] = resolved_script_path.is_file()
                if script_artifact["exists"]:
                    script_artifact["sha256"] = text_sha256(
                        resolved_script_path.read_text(encoding="utf-8")
                    )
                    if expected_script is not None:
                        script_artifact["matches_expected_script"] = (
                            script_artifact["sha256"]
                            == script_artifact["expected_sha256"]
                        )
            except (OSError, UnicodeError) as exc:
                script_artifact["read_error"] = str(exc)[:2000]

    journal_attempt = events[-1].attempt if events else None
    state_attempt = state.latest_attempt if state is not None else None
    raw_result_attempt = (
        result_metadata.get("execution_attempt")
        if isinstance(result_metadata, dict)
        else None
    )
    result_attempt: ExecutionAttemptRecord | None = None
    result_attempt_error: str | None = None
    if raw_result_attempt is not None:
        try:
            result_attempt = ExecutionAttemptRecord.model_validate(raw_result_attempt)
        except ValidationError as exc:
            result_attempt_error = str(exc)[:2000]

    latest_attempt = journal_attempt or state_attempt
    latest_source = (
        "journal"
        if journal_attempt is not None
        else "state"
        if state_attempt is not None
        else None
    )
    if (
        latest_attempt is not None
        and result_attempt is not None
        and latest_attempt.attempt_id == result_attempt.attempt_id
        and latest_attempt.status == "running"
        and result_attempt.status in {"completed", "failed"}
    ):
        latest_attempt = result_attempt
        latest_source = "result_metadata"
    elif latest_attempt is None and result_attempt is not None:
        latest_attempt = result_attempt
        latest_source = "result_metadata"

    issue_codes: list[str] = []
    if journal.get("status") == "invalid":
        issue_codes.append("execution_attempt_journal_invalid")
    if state_error:
        issue_codes.append("execution_attempt_state_invalid")
    if result_attempt_error:
        issue_codes.append("result_execution_attempt_invalid")
    if state is not None and (
        state.project_id != project_id or state.revision != revision
    ):
        issue_codes.append("execution_attempt_state_identity_mismatch")
    if state is not None and events:
        if state.event_count != len(events):
            issue_codes.append("execution_attempt_state_event_count_mismatch")
        if state.latest_event_id != events[-1].event_id:
            issue_codes.append("execution_attempt_state_event_id_mismatch")
        if state.latest_event_sha256 != events[-1].event_record_sha256:
            issue_codes.append("execution_attempt_state_event_digest_mismatch")
        if state.latest_attempt != events[-1].attempt:
            issue_codes.append("execution_attempt_state_record_mismatch")
    if state is None and events:
        issue_codes.append("execution_attempt_state_missing")
    if state is not None and not events:
        issue_codes.append("execution_attempt_journal_missing")
    if (
        result_attempt is not None
        and journal_attempt is not None
        and result_attempt.attempt_id == journal_attempt.attempt_id
        and result_attempt.status != journal_attempt.status
    ):
        issue_codes.append("execution_attempt_result_journal_status_mismatch")
    if result_attempt is not None and not events:
        issue_codes.append("result_execution_attempt_journal_missing")
    if (
        result_attempt is not None
        and journal_attempt is not None
        and result_attempt.attempt_id != journal_attempt.attempt_id
    ):
        issue_codes.append("execution_attempt_result_stale")
    latest_expected_result_missing = bool(
        active is False
        and latest_attempt is not None
        and latest_attempt.status in {"completed", "failed"}
        and latest_attempt.result_metadata_path is not None
        and (
            result_attempt is None
            or result_attempt.attempt_id != latest_attempt.attempt_id
        )
    )
    if latest_expected_result_missing:
        issue_codes.append("execution_attempt_result_metadata_missing")
    if (
        latest_attempt is not None
        and latest_attempt.status in {"completed", "failed"}
        and result_attempt is not None
        and result_attempt.attempt_id == latest_attempt.attempt_id
        and isinstance(result_metadata, dict)
        and bool(result_metadata.get("success")) != latest_attempt.result_success
    ):
        issue_codes.append("execution_attempt_result_success_mismatch")
    if latest_attempt is not None:
        if (
            latest_attempt.project_id != project_id
            or latest_attempt.revision != revision
        ):
            issue_codes.append("execution_attempt_revision_identity_mismatch")
        if (
            expected_spec_payload is not None
            and latest_attempt.spec_sha256
            != canonical_json_sha256(expected_spec_payload)
        ):
            issue_codes.append("execution_attempt_spec_sha256_mismatch")
        if (
            expected_script is not None
            and latest_attempt.script_sha256 != text_sha256(expected_script)
        ):
            issue_codes.append("execution_attempt_script_sha256_mismatch")
        if expected_script_path is not None:
            if latest_attempt.script_path != script_artifact["path"]:
                issue_codes.append("execution_attempt_script_path_mismatch")
            if script_artifact["read_error"] is not None:
                issue_codes.append("execution_attempt_script_artifact_unreadable")
            elif not script_artifact["exists"]:
                issue_codes.append("execution_attempt_script_artifact_missing")
            elif script_artifact["sha256"] != latest_attempt.script_sha256:
                issue_codes.append(
                    "execution_attempt_script_artifact_sha256_mismatch"
                )
        if expected_lock_path is not None:
            resolved_lock_path = expected_lock_path.expanduser().resolve()
            if latest_attempt.lock_path != str(resolved_lock_path):
                issue_codes.append("execution_attempt_lock_path_mismatch")
            if not resolved_lock_path.is_file():
                issue_codes.append("execution_attempt_lock_artifact_missing")
        if (
            expected_result_metadata_path is not None
            and latest_attempt.status in {"completed", "failed"}
            and latest_attempt.result_metadata_path is not None
            and latest_attempt.result_metadata_path
            != str(expected_result_metadata_path.expanduser().resolve())
        ):
            issue_codes.append("execution_attempt_result_metadata_path_mismatch")

    identity_issue_codes = {
        "execution_attempt_state_identity_mismatch",
        "execution_attempt_revision_identity_mismatch",
        "execution_attempt_spec_sha256_mismatch",
        "execution_attempt_script_sha256_mismatch",
        "execution_attempt_script_path_mismatch",
        "execution_attempt_script_artifact_missing",
        "execution_attempt_script_artifact_unreadable",
        "execution_attempt_script_artifact_sha256_mismatch",
        "execution_attempt_lock_path_mismatch",
        "execution_attempt_lock_artifact_missing",
        "execution_attempt_result_metadata_path_mismatch",
        "execution_attempt_result_success_mismatch",
    }
    identity_mismatch = bool(identity_issue_codes.intersection(issue_codes))
    history_issue_codes = {
        "execution_attempt_journal_invalid",
        "execution_attempt_journal_missing",
        "result_execution_attempt_journal_missing",
        "execution_attempt_result_journal_status_mismatch",
        "result_execution_attempt_invalid",
    }
    history_invalid = bool(history_issue_codes.intersection(issue_codes)) or bool(
        state_error and not events
    )

    if active is None:
        runtime_status = "transitioning"
    elif active:
        runtime_status = (
            "running_identity_mismatch"
            if identity_mismatch
            else "running"
            if latest_attempt is not None and latest_attempt.status == "running"
            else "running_unrecorded"
        )
    elif history_invalid:
        runtime_status = "history_invalid"
    elif identity_mismatch:
        runtime_status = "identity_mismatch"
    elif latest_expected_result_missing:
        runtime_status = "result_missing"
    elif latest_attempt is not None:
        if latest_attempt.status == "running":
            runtime_status = "interrupted"
        elif (
            latest_attempt.status == "completed"
            and latest_attempt.result_success is False
        ):
            runtime_status = "failed"
        else:
            runtime_status = latest_attempt.status
    elif isinstance(result_metadata, dict):
        runtime_status = "legacy_completed"
    else:
        runtime_status = "not_started"

    if runtime_status in {"running", "running_unrecorded", "transitioning"}:
        recommended_action = "wait_and_poll_execution_status"
    elif runtime_status == "interrupted":
        recommended_action = "inspect_runner_logs_before_explicit_retry"
    elif runtime_status == "failed":
        recommended_action = "review_execution_error_before_explicit_retry"
    elif runtime_status == "history_invalid":
        recommended_action = "preserve_and_repair_execution_history_before_retry"
    elif runtime_status in {"identity_mismatch", "running_identity_mismatch"}:
        recommended_action = "preserve_and_reconcile_execution_identity_before_retry"
    elif runtime_status == "result_missing":
        recommended_action = "preserve_outputs_and_reconcile_result_metadata"
    elif runtime_status in {"completed", "legacy_completed"}:
        recommended_action = "continue_with_result_review_or_gui_sync"
    else:
        recommended_action = "execute_only_after_explicit_confirmation"

    return {
        "schema_version": EXECUTION_RUNTIME_SCHEMA_VERSION,
        "status": runtime_status,
        "project_id": project_id,
        "revision": revision,
        "active": active,
        "lock_observation_stable": lock_observation_stable,
        "lock_probe_before": first_probe,
        "lock_probe_after": second_probe,
        "state_path": str(paths["state"]),
        "events_path": str(paths["events"]),
        "state_exists": paths["state"].exists(),
        "events_exist": paths["events"].exists(),
        "state_read_error": state_error,
        "result_attempt_read_error": result_attempt_error,
        "script_artifact": script_artifact,
        "attempt_record_source": latest_source,
        "latest_attempt": (
            latest_attempt.model_dump(mode="json")
            if latest_attempt is not None
            else None
        ),
        "journal": journal,
        "consistency": {
            "ok": not issue_codes,
            "issue_codes": issue_codes,
        },
        "continuation": {
            "automatic_retry_allowed": False,
            "explicit_execute_confirmation_required": runtime_status
            in {"not_started", "interrupted", "failed"},
            "execution_may_still_be_running": runtime_status
            in {
                "running",
                "running_unrecorded",
                "running_identity_mismatch",
                "transitioning",
                "interrupted",
            },
            "recommended_tool": "material_studio_live_project_status",
            "recommended_payload": {
                "project_id": project_id,
                "include_gui_status": False,
            },
            "recommended_action": recommended_action,
            "explicit_retry_tool": (
                "material_studio_gui_apply_current_revision"
                if runtime_status in {"interrupted", "failed"}
                else None
            ),
            "explicit_retry_payload": (
                {
                    "project_id": project_id,
                    "execution_mode": "execute",
                    "open_in_gui": False,
                }
                if runtime_status in {"interrupted", "failed"}
                else None
            ),
        },
    }


__all__ = [
    "EXECUTION_ATTEMPT_EVENTS_FILENAME",
    "EXECUTION_ATTEMPT_SCHEMA_VERSION",
    "EXECUTION_ATTEMPT_STATE_FILENAME",
    "EXECUTION_RUNTIME_SCHEMA_VERSION",
    "ExecutionAttemptHistoryError",
    "ExecutionAttemptRecord",
    "begin_execution_attempt",
    "canonical_json_sha256",
    "execution_attempt_paths",
    "finish_execution_attempt",
    "inspect_execution_runtime",
    "publish_terminal_execution_attempt",
    "text_sha256",
]
