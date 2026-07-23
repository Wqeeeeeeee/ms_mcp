"""Strict contracts for local, revision-bound remote job handoffs.

These models describe evidence exchanged with an external scheduler.  They do
not authorize or implement shell, SSH, scheduler, Materials Studio, or GUI
execution.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .castep import CastepTask, CastepTaskValue
from .common import ExecutionMode, StrictModel


SHA256_PATTERN = r"^[0-9a-f]{64}$"
PROJECT_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
CALCULATION_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"
BUNDLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"
REMOTE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


def _normalized_aware_timestamp(value: str, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class RemoteSchedulerKind(str, Enum):
    """Scheduler families whose identifiers may be recorded as evidence."""

    MATERIALS_STUDIO_JOB_CONTROL = "materials_studio_job_control"
    SLURM = "slurm"
    PBS = "pbs"
    LSF = "lsf"
    OTHER = "other"


class RemoteJobState(str, Enum):
    """Locally recorded scheduler states; no state is queried automatically."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RemoteSubmissionChannel(str, Enum):
    """How the external submission was performed."""

    MATERIALS_STUDIO_JOB_CONTROL = "materials_studio_job_control"
    MANUAL_SCHEDULER_SUBMISSION = "manual_scheduler_submission"
    EXTERNAL_ORCHESTRATOR = "external_orchestrator"


class RemoteSchedulerJobIdentity(StrictModel):
    """Explicit scheduler instance and scheduler-assigned job identity."""

    scheduler_kind: RemoteSchedulerKind
    scheduler_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=REMOTE_IDENTIFIER_PATTERN,
    )
    job_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=REMOTE_IDENTIFIER_PATTERN,
    )


class RemoteCastepBundleRequest(StrictModel):
    """Prepare one immutable CASTEP handoff for an exact current revision."""

    workspace_root: str = Field(min_length=1, max_length=4096)
    project_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=PROJECT_ID_PATTERN,
    )
    expected_revision: int = Field(ge=0)
    calculation_name: str = Field(
        min_length=1,
        max_length=120,
        pattern=CALCULATION_NAME_PATTERN,
    )
    task: CastepTaskValue = CastepTask.GEOMETRY_OPTIMIZATION
    spec_path: str = Field(min_length=1, max_length=4096)
    script_path: str = Field(min_length=1, max_length=4096)
    input_path: str = Field(min_length=1, max_length=4096)
    expected_spec_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_script_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_input_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_preview_manifest_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    requested_cores: int | None = Field(default=None, ge=1, le=1_000_000)
    execution_mode: ExecutionMode = ExecutionMode.PREVIEW
    lock_timeout_seconds: float = Field(default=5.0, ge=0.0, le=60.0)

    @field_validator(
        "expected_spec_sha256",
        "expected_script_sha256",
        "expected_input_sha256",
        "expected_preview_manifest_sha256",
        mode="before",
    )
    @classmethod
    def normalize_sha256(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()

    @model_validator(mode="after")
    def require_preview_manifest_for_execute(self) -> "RemoteCastepBundleRequest":
        if (
            self.execution_mode is ExecutionMode.EXECUTE
            and self.expected_preview_manifest_sha256 is None
        ):
            raise ValueError(
                "execute requires expected_preview_manifest_sha256 from an exact preview"
            )
        return self


class _RemoteBundleBoundRequest(StrictModel):
    """Shared immutable bundle binding for post-preparation evidence."""

    workspace_root: str = Field(min_length=1, max_length=4096)
    project_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=PROJECT_ID_PATTERN,
    )
    bundle_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=BUNDLE_ID_PATTERN,
    )
    expected_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    identity: RemoteSchedulerJobIdentity
    lock_timeout_seconds: float = Field(default=5.0, ge=0.0, le=60.0)

    @field_validator("expected_manifest_sha256", mode="before")
    @classmethod
    def normalize_manifest_sha256(cls, value: object) -> str:
        return str(value).strip().lower()


class RemoteSubmissionRecordRequest(_RemoteBundleBoundRequest):
    """Record an already-performed external submission."""

    submitted_at: str = Field(min_length=1, max_length=80)
    channel: RemoteSubmissionChannel
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("submitted_at")
    @classmethod
    def validate_submitted_at(cls, value: str) -> str:
        return _normalized_aware_timestamp(value, label="submitted_at")


class RemoteStatusRecordRequest(_RemoteBundleBoundRequest):
    """Record one externally observed status without querying the scheduler."""

    observed_at: str = Field(min_length=1, max_length=80)
    state: RemoteJobState
    detail: str | None = Field(default=None, max_length=2000)
    scheduler_message_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=REMOTE_IDENTIFIER_PATTERN,
    )

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: str) -> str:
        return _normalized_aware_timestamp(value, label="observed_at")

    @model_validator(mode="after")
    def normalize_detail(self) -> "RemoteStatusRecordRequest":
        if self.detail is not None:
            cleaned = self.detail.strip()
            self.detail = cleaned or None
        return self


class RemoteStatusQuery(_RemoteBundleBoundRequest):
    """Read one local status projection for an explicit scheduler/job id."""

    lock_timeout_seconds: float = Field(default=0.0, ge=0.0, le=60.0)


__all__ = [
    "RemoteCastepBundleRequest",
    "RemoteJobState",
    "RemoteSchedulerJobIdentity",
    "RemoteSchedulerKind",
    "RemoteStatusQuery",
    "RemoteStatusRecordRequest",
    "RemoteSubmissionChannel",
    "RemoteSubmissionRecordRequest",
]
