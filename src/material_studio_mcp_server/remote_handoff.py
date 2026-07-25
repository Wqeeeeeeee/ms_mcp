"""Immutable local handoffs for externally submitted CASTEP jobs.

This module deliberately stops at the local evidence boundary.  It copies
already-reviewed revision artifacts into an immutable bundle and records
caller-observed scheduler evidence.  It never invokes a shell, SSH client,
scheduler command, Materials Studio runner, or GUI action.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, TypeVar

from material_studio_mcp_server.specs.castep import CastepEnergySpec
from material_studio_mcp_server.specs.common import ExecutionMode
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.specs.remote_job import (
    RemoteCastepBundleRequest,
    RemoteJobState,
    RemoteStatusQuery,
    RemoteStatusRecordRequest,
    RemoteSubmissionRecordRequest,
)
from material_studio_mcp_server.translators.project_to_perl import (
    render_model_to_perl,
)


REMOTE_HANDOFF_MANIFEST_SCHEMA = "material_studio_remote_castep_handoff_v1"
REMOTE_HANDOFF_EVENT_SCHEMA = "material_studio_remote_job_event_v1"
REMOTE_HANDOFF_DIRECTORY = "remote_handoffs"
REMOTE_HANDOFF_EVENT_TAIL_LIMIT = 100
MAX_SPEC_BYTES = 10 * 1024 * 1024
MAX_SCRIPT_BYTES = 10 * 1024 * 1024
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_EVENT_JOURNAL_BYTES = 16 * 1024 * 1024
MAX_EVENT_COUNT = 10_000
MAX_EVENT_LINE_BYTES = 256 * 1024
LOCK_POLL_SECONDS = 0.05
OS_BINARY_FLAG = getattr(os, "O_BINARY", 0)


class RemoteHandoffError(RuntimeError):
    """Base error for a local remote-job handoff."""


class RemoteHandoffBusyError(RemoteHandoffError):
    """Raised when another writer owns the per-job advisory lock."""


class RemoteHandoffHistoryError(RemoteHandoffError):
    """Raised when the append-only event chain is malformed or tampered."""


class RemoteHandoffBindingError(RemoteHandoffError):
    """Raised when a request does not bind the exact current revision."""


RequestModel = TypeVar(
    "RequestModel",
    RemoteCastepBundleRequest,
    RemoteSubmissionRecordRequest,
    RemoteStatusRecordRequest,
    RemoteStatusQuery,
)


@dataclass(frozen=True)
class _BundlePlan:
    request: RemoteCastepBundleRequest
    workspace_root: Path
    project_dir: Path
    bundle_dir: Path
    manifest_path: Path
    events_path: Path
    lock_path: Path
    bundle_id: str
    manifest: dict[str, Any]
    manifest_bytes: bytes
    manifest_sha256: str
    artifact_bytes: dict[str, bytes]


def _coerce_request(
    model_type: type[RequestModel],
    value: RequestModel | Mapping[str, Any],
) -> RequestModel:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _manifest_json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _require_descendant(path: Path, root: Path, *, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RemoteHandoffBindingError(
            f"{label} escapes the required root: {path}"
        ) from exc


def _is_link_like(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(details.st_mode):
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_no_link_components(
    path: Path,
    *,
    root: Path,
    label: str,
) -> None:
    absolute_root = Path(os.path.abspath(os.fspath(root)))
    absolute_path = Path(os.path.abspath(os.fspath(path)))
    _require_descendant(absolute_path, absolute_root, label=label)
    relative = absolute_path.relative_to(absolute_root)
    candidates = [absolute_root]
    candidate = absolute_root
    for part in relative.parts:
        candidate = candidate / part
        candidates.append(candidate)
    for candidate in candidates:
        if os.path.lexists(candidate) and _is_link_like(candidate):
            raise RemoteHandoffBindingError(
                f"{label} contains a symbolic link, junction, or reparse point: "
                f"{candidate}"
            )


def _resolve_existing_file(
    value: str | Path,
    *,
    root: Path,
    label: str,
    max_bytes: int,
) -> tuple[Path, bytes]:
    unresolved = Path(value).expanduser()
    _assert_no_link_components(unresolved, root=root, label=label)
    try:
        resolved = unresolved.resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(f"{label} was not found: {unresolved}") from exc
    _require_descendant(resolved, root, label=label)
    if not resolved.is_file():
        raise RemoteHandoffBindingError(f"{label} is not a regular file: {resolved}")
    content = _bounded_read_regular_file(
        resolved,
        label=label,
        max_bytes=max_bytes,
    )
    return resolved, content


def _read_json_object(
    path: Path,
    *,
    label: str,
    max_bytes: int = MAX_SPEC_BYTES,
) -> tuple[dict[str, Any], bytes]:
    content = _bounded_read_regular_file(
        path,
        label=label,
        max_bytes=max_bytes,
    )
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteHandoffBindingError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RemoteHandoffBindingError(f"{label} must contain a JSON object")
    return value, content


def _current_pointer_binding(
    *,
    project_dir: Path,
    request: RemoteCastepBundleRequest,
) -> tuple[Path, Path, bytes]:
    current_path = project_dir / "current.json"
    current, current_bytes = _read_json_object(
        current_path,
        label="current revision pointer",
    )
    if current.get("project_id") != request.project_id:
        raise RemoteHandoffBindingError(
            "current revision pointer project_id does not match the request"
        )
    if current.get("revision") != request.expected_revision:
        raise RemoteHandoffBindingError(
            f"expected current revision {request.expected_revision}, "
            f"found {current.get('revision')!r}"
        )
    current_spec_path = current.get("spec_path")
    current_script_path = current.get("calculation_preview_script_path")
    if not isinstance(current_spec_path, str) or not current_spec_path:
        raise RemoteHandoffBindingError(
            "current revision pointer has no immutable spec_path"
        )
    if not isinstance(current_script_path, str) or not current_script_path:
        raise RemoteHandoffBindingError(
            "current revision has no reviewed CASTEP calculation preview script"
        )
    _assert_no_link_components(
        Path(current_spec_path).expanduser(),
        root=project_dir,
        label="current revision spec",
    )
    _assert_no_link_components(
        Path(current_script_path).expanduser(),
        root=project_dir,
        label="current CASTEP script",
    )
    try:
        resolved_spec_path = Path(current_spec_path).expanduser().resolve(strict=True)
        resolved_script_path = Path(current_script_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            "current revision pointer references a missing spec or CASTEP script"
        ) from exc
    return resolved_spec_path, resolved_script_path, current_bytes


def _artifact_manifest_entry(
    *,
    role: str,
    source: Path,
    project_dir: Path,
    bundled_relative_path: str,
    content: bytes,
) -> dict[str, Any]:
    return {
        "role": role,
        "source_project_relative_path": source.relative_to(project_dir).as_posix(),
        "bundled_relative_path": bundled_relative_path,
        "sha256": _sha256_bytes(content),
        "bytes": len(content),
    }


def _build_bundle_plan(
    request_value: RemoteCastepBundleRequest | Mapping[str, Any],
) -> _BundlePlan:
    request = _coerce_request(RemoteCastepBundleRequest, request_value)
    try:
        workspace_root = Path(request.workspace_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            f"workspace_root was not found: {request.workspace_root}"
        ) from exc
    if not workspace_root.is_dir():
        raise RemoteHandoffBindingError("workspace_root must be a directory")

    project_candidate = workspace_root / request.project_id
    _assert_no_link_components(
        project_candidate,
        root=workspace_root,
        label="project directory",
    )
    try:
        project_dir = project_candidate.resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            f"project was not found: {request.project_id}"
        ) from exc
    _require_descendant(project_dir, workspace_root, label="project directory")
    if not project_dir.is_dir():
        raise RemoteHandoffBindingError("project path must be a directory")

    pointer_spec_path, pointer_script_path, current_bytes = _current_pointer_binding(
        project_dir=project_dir,
        request=request,
    )
    spec_path, spec_bytes = _resolve_existing_file(
        request.spec_path,
        root=project_dir,
        label="revision spec",
        max_bytes=MAX_SPEC_BYTES,
    )
    script_path, script_bytes = _resolve_existing_file(
        request.script_path,
        root=project_dir,
        label="CASTEP script",
        max_bytes=MAX_SCRIPT_BYTES,
    )
    input_path, input_bytes = _resolve_existing_file(
        request.input_path,
        root=project_dir,
        label="CASTEP input structure",
        max_bytes=MAX_INPUT_BYTES,
    )

    if not _same_path(spec_path, pointer_spec_path):
        raise RemoteHandoffBindingError(
            "spec_path does not match the exact current revision pointer"
        )
    if not _same_path(script_path, pointer_script_path):
        raise RemoteHandoffBindingError(
            "script_path does not match the exact current CASTEP preview script"
        )
    expected_output_root = (
        project_dir / "outputs" / f"r{request.expected_revision:03d}"
    )
    try:
        expected_output_root = expected_output_root.resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            "current revision output directory was not found"
        ) from exc
    _require_descendant(
        input_path,
        expected_output_root,
        label="CASTEP input structure",
    )
    if input_path.suffix.lower() not in {".cif", ".xsd"}:
        raise RemoteHandoffBindingError(
            "CASTEP input structure must be a .cif or .xsd artifact"
        )

    observed_hashes = {
        "spec": _sha256_bytes(spec_bytes),
        "script": _sha256_bytes(script_bytes),
        "input": _sha256_bytes(input_bytes),
    }
    expected_hashes = {
        "spec": request.expected_spec_sha256,
        "script": request.expected_script_sha256,
        "input": request.expected_input_sha256,
    }
    for role in ("spec", "script", "input"):
        if observed_hashes[role] != expected_hashes[role]:
            raise RemoteHandoffBindingError(
                f"{role} SHA-256 does not match the expected revision binding"
            )

    try:
        spec = ModelSpec.model_validate_json(spec_bytes)
    except Exception as exc:
        raise RemoteHandoffBindingError(
            "revision spec is not a valid ModelSpec"
        ) from exc
    if spec.project_id != request.project_id or spec.revision != request.expected_revision:
        raise RemoteHandoffBindingError(
            "revision spec project/revision does not match the request"
        )
    if not isinstance(spec.simulation, CastepEnergySpec):
        raise RemoteHandoffBindingError(
            "revision spec is not bound to a structured CASTEP calculation"
        )
    if spec.simulation.task is not request.task:
        raise RemoteHandoffBindingError(
            "requested CASTEP task does not match the immutable revision spec"
        )
    expected_script = render_model_to_perl(
        spec,
        expected_output_root,
    ).calculation_preview_script
    if expected_script is None:
        raise RemoteHandoffBindingError(
            "revision spec has no deterministic CASTEP companion script"
        )
    if script_bytes != expected_script.encode("utf-8"):
        raise RemoteHandoffBindingError(
            "saved CASTEP script differs from deterministic translator output"
        )

    current_payload, current_bytes_after_validation = _read_json_object(
        project_dir / "current.json",
        label="current revision pointer",
    )
    if current_bytes_after_validation != current_bytes:
        raise RemoteHandoffBindingError(
            "current revision pointer changed while the handoff was being validated"
        )
    try:
        embedded_spec = ModelSpec.model_validate(current_payload.get("spec"))
    except Exception as exc:
        raise RemoteHandoffBindingError(
            "current revision pointer has no valid embedded ModelSpec"
        ) from exc
    if embedded_spec.model_dump(mode="json") != spec.model_dump(mode="json"):
        raise RemoteHandoffBindingError(
            "current revision pointer embedded spec differs from immutable spec"
        )

    identity_seed = {
        "project_id": request.project_id,
        "revision": request.expected_revision,
        "calculation_name": request.calculation_name,
        "task": request.task.value,
        "hashes": expected_hashes,
    }
    identity_digest = _sha256_bytes(_canonical_json_bytes(identity_seed))
    # Keep the generated identifier safely inside both the 200-character
    # contract and practical Windows path limits.  The digest binds the full,
    # untruncated project/calculation identity.
    bundle_id = (
        f"{request.project_id[:48]}-r{request.expected_revision:03d}-"
        f"{request.calculation_name[:64]}-{identity_digest[:16]}"
    )
    bundle_dir = project_dir / REMOTE_HANDOFF_DIRECTORY / bundle_id
    manifest_path = bundle_dir / "manifest.json"
    events_path = bundle_dir / "events.jsonl"
    lock_path = bundle_dir / "remote_job.lock"
    _assert_no_link_components(
        bundle_dir,
        root=project_dir,
        label="remote handoff bundle",
    )

    input_bundle_name = f"structure{input_path.suffix.lower()}"
    artifact_bytes = {
        "artifacts/model_spec.json": spec_bytes,
        "artifacts/castep_script.pl": script_bytes,
        f"artifacts/{input_bundle_name}": input_bytes,
    }
    artifacts = [
        _artifact_manifest_entry(
            role="model_spec",
            source=spec_path,
            project_dir=project_dir,
            bundled_relative_path="artifacts/model_spec.json",
            content=spec_bytes,
        ),
        _artifact_manifest_entry(
            role="castep_script",
            source=script_path,
            project_dir=project_dir,
            bundled_relative_path="artifacts/castep_script.pl",
            content=script_bytes,
        ),
        _artifact_manifest_entry(
            role="input_structure",
            source=input_path,
            project_dir=project_dir,
            bundled_relative_path=f"artifacts/{input_bundle_name}",
            content=input_bytes,
        ),
    ]
    manifest = {
        "schema": REMOTE_HANDOFF_MANIFEST_SCHEMA,
        "bundle_id": bundle_id,
        "project_id": request.project_id,
        "revision": request.expected_revision,
        "calculation": {
            "module": "CASTEP",
            "task": request.task.value,
            "calculation_name": request.calculation_name,
            "requested_cores": request.requested_cores,
        },
        "revision_binding": {
            "expected_revision": request.expected_revision,
            "current_pointer_sha256": _sha256_bytes(current_bytes),
            "spec_sha256": expected_hashes["spec"],
            "script_sha256": expected_hashes["script"],
            "input_sha256": expected_hashes["input"],
            "deterministic_script_verified": True,
        },
        "artifacts": artifacts,
        "submission_contract": {
            "transport_implemented": False,
            "scheduler_identity_required": True,
            "job_id_required": True,
            "submission_is_external": True,
            "status_is_local_evidence_only": True,
        },
    }
    manifest_bytes = _manifest_json_bytes(manifest)
    return _BundlePlan(
        request=request,
        workspace_root=workspace_root,
        project_dir=project_dir,
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        events_path=events_path,
        lock_path=lock_path,
        bundle_id=bundle_id,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        artifact_bytes=artifact_bytes,
    )


def _lock_file_descriptor_nonblocking(file_descriptor: int) -> None:
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file_descriptor(file_descriptor: int) -> None:
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file_descriptor, fcntl.LOCK_UN)


def _verify_open_regular_file(
    path: Path,
    file_descriptor: int,
    *,
    label: str,
) -> os.stat_result:
    try:
        path_details = path.lstat()
    except FileNotFoundError as exc:
        raise RemoteHandoffBindingError(f"{label} disappeared while being opened") from exc
    if _is_link_like(path) or not stat.S_ISREG(path_details.st_mode):
        raise RemoteHandoffBindingError(
            f"{label} must be a regular file, not a link or reparse point"
        )
    descriptor_details = os.fstat(file_descriptor)
    if not stat.S_ISREG(descriptor_details.st_mode):
        raise RemoteHandoffBindingError(f"{label} descriptor is not a regular file")
    try:
        same_file = os.path.samestat(path_details, descriptor_details)
    except (AttributeError, OSError):
        same_file = (
            path_details.st_dev == descriptor_details.st_dev
            and path_details.st_ino == descriptor_details.st_ino
        )
    if not same_file:
        raise RemoteHandoffBindingError(
            f"{label} changed identity while it was being opened"
        )
    return descriptor_details


def _file_change_signature(details: os.stat_result) -> tuple[int, int, int]:
    return (
        int(details.st_size),
        int(getattr(details, "st_mtime_ns", int(details.st_mtime * 1_000_000_000))),
        int(getattr(details, "st_ctime_ns", int(details.st_ctime * 1_000_000_000))),
    )


def _bounded_read_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    """Read one stable regular-file identity without trusting a prior stat."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise RemoteHandoffBindingError(f"{label} was not found: {path}") from exc
    if _is_link_like(path) or not stat.S_ISREG(before.st_mode):
        raise RemoteHandoffBindingError(
            f"{label} must be a regular file, not a link or reparse point"
        )
    if before.st_size > max_bytes:
        raise RemoteHandoffBindingError(
            f"{label} exceeds the {max_bytes}-byte read limit"
        )

    flags = os.O_RDONLY | OS_BINARY_FLAG | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            f"{label} could not be opened safely: {path}"
        ) from exc
    try:
        opened = _verify_open_regular_file(
            path,
            file_descriptor,
            label=label,
        )
        if opened.st_size > max_bytes:
            raise RemoteHandoffBindingError(
                f"{label} exceeds the {max_bytes}-byte read limit"
            )
        if _file_change_signature(opened) != _file_change_signature(before):
            raise RemoteHandoffBindingError(
                f"{label} changed during its safe-open check"
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            read_size = min(64 * 1024, max_bytes + 1 - total)
            chunk = os.read(file_descriptor, read_size)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise RemoteHandoffBindingError(
                    f"{label} exceeds the {max_bytes}-byte read limit"
                )

        after = _verify_open_regular_file(
            path,
            file_descriptor,
            label=label,
        )
        content = b"".join(chunks)
        if (
            _file_change_signature(after) != _file_change_signature(opened)
            or len(content) != opened.st_size
        ):
            raise RemoteHandoffBindingError(
                f"{label} changed while it was being read"
            )
        return content
    except OSError as exc:
        raise RemoteHandoffBindingError(
            f"{label} could not be read safely: {path}"
        ) from exc
    finally:
        os.close(file_descriptor)


@contextmanager
def _remote_job_lock(
    path: Path,
    *,
    timeout_seconds: float,
    create: bool,
    allowed_root: Path | None,
) -> Iterator[dict[str, Any]]:
    root = allowed_root if allowed_root is not None else path.parent
    _assert_no_link_components(path, root=root, label="remote job lock")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_link_components(path, root=root, label="remote job lock")
    elif not os.path.lexists(path):
        raise RemoteHandoffBindingError(
            f"remote job lock was not found: {path}"
        )
    flags = os.O_RDWR | OS_BINARY_FLAG | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    try:
        file_descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            f"remote job lock could not be opened safely: {path}"
        ) from exc
    acquired = False
    lock_initialized = False
    started = time.monotonic()
    try:
        descriptor_details = _verify_open_regular_file(
            path,
            file_descriptor,
            label="remote job lock",
        )
        _assert_no_link_components(path, root=root, label="remote job lock")
        if descriptor_details.st_size == 0 and create:
            os.write(file_descriptor, b"\0")
            os.fsync(file_descriptor)
            lock_initialized = True
        elif descriptor_details.st_size == 0:
            raise RemoteHandoffBindingError(
                "existing remote job lock is uninitialized"
            )
        deadline = started + max(float(timeout_seconds), 0.0)
        while True:
            try:
                _lock_file_descriptor_nonblocking(file_descriptor)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise RemoteHandoffBusyError(
                        f"remote job lock could not be acquired: {path}"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise RemoteHandoffBusyError(
                        f"remote job handoff is busy: {path}"
                    ) from exc
                time.sleep(LOCK_POLL_SECONDS)
                continue
            acquired = True
            break
        yield {
            "path": str(path),
            "domain": "remote_job_handoff",
            "access": "write" if create else "read",
            "waited_seconds": round(time.monotonic() - started, 6),
            "timeout_seconds": float(timeout_seconds),
            "lock_file_initialized": lock_initialized,
            "filesystem_write_performed": lock_initialized,
        }
    finally:
        if acquired:
            try:
                _unlock_file_descriptor(file_descriptor)
            except OSError:
                pass
        os.close(file_descriptor)


@contextmanager
def _remote_job_write_lock(
    path: Path,
    *,
    timeout_seconds: float,
    allowed_root: Path | None = None,
) -> Iterator[dict[str, Any]]:
    with _remote_job_lock(
        path,
        timeout_seconds=timeout_seconds,
        create=True,
        allowed_root=allowed_root,
    ) as transaction:
        yield transaction


@contextmanager
def _remote_job_existing_lock(
    path: Path,
    *,
    timeout_seconds: float,
    allowed_root: Path | None = None,
) -> Iterator[dict[str, Any]]:
    with _remote_job_lock(
        path,
        timeout_seconds=timeout_seconds,
        create=False,
        allowed_root=allowed_root,
    ) as transaction:
        yield transaction


def _write_all(file_descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(file_descriptor, content[offset:])
        if written <= 0:
            raise OSError("immutable handoff write made no progress")
        offset += written


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    file_descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _verify_immutable_content(path: Path, content: bytes) -> None:
    existing = _bounded_read_regular_file(
        path,
        label="immutable handoff artifact",
        max_bytes=len(content),
    )
    if existing != content:
        raise RemoteHandoffBindingError(
            f"immutable handoff artifact already exists with different content: {path}"
        )


def _publish_immutable_file(
    path: Path,
    content: bytes,
    *,
    allowed_root: Path | None = None,
) -> str:
    root = allowed_root if allowed_root is not None else path.parent
    _assert_no_link_components(
        path,
        root=root,
        label="immutable handoff artifact",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_components(
        path,
        root=root,
        label="immutable handoff artifact",
    )
    if os.path.lexists(path):
        _verify_immutable_content(path, content)
        return "verified_existing"

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    descriptor_open = True
    try:
        _verify_open_regular_file(
            temporary_path,
            file_descriptor,
            label="temporary immutable handoff artifact",
        )
        _write_all(file_descriptor, content)
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        descriptor_open = False
        _assert_no_link_components(
            temporary_path,
            root=root,
            label="temporary immutable handoff artifact",
        )
        _assert_no_link_components(
            path,
            root=root,
            label="immutable handoff artifact",
        )
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            _verify_immutable_content(path, content)
            return "verified_existing"
        _fsync_directory(path.parent)
        _assert_no_link_components(
            path,
            root=root,
            label="immutable handoff artifact",
        )
        _verify_immutable_content(path, content)
        return "published"
    finally:
        if descriptor_open:
            os.close(file_descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _event_sha256(event_without_digest: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(event_without_digest))


def _parse_event_journal(
    content: bytes,
    *,
    bundle_id: str,
    manifest_sha256: str,
) -> list[dict[str, Any]]:
    if not content:
        raise RemoteHandoffHistoryError("remote job event journal is empty")
    if len(content) > MAX_EVENT_JOURNAL_BYTES:
        raise RemoteHandoffHistoryError(
            "remote job event journal exceeds the configured byte limit"
        )
    if not content.endswith(b"\n"):
        raise RemoteHandoffHistoryError(
            "remote job event journal is not newline-terminated"
        )
    events: list[dict[str, Any]] = []
    previous_digest: str | None = None
    submitted_identity: dict[str, Any] | None = None
    for index, raw_line in enumerate(io.BytesIO(content), start=1):
        if index > MAX_EVENT_COUNT:
            raise RemoteHandoffHistoryError(
                "remote job event journal exceeds the configured event-count limit"
            )
        if len(raw_line) > MAX_EVENT_LINE_BYTES:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} exceeds the configured line-size limit"
            )
        line_bytes = raw_line[:-1]
        if line_bytes.endswith(b"\r"):
            line_bytes = line_bytes[:-1]
        try:
            line = line_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} is not UTF-8"
            ) from exc
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} is not valid JSON"
            ) from exc
        if not isinstance(event, dict):
            raise RemoteHandoffHistoryError(
                f"remote job event {index} is not an object"
            )
        if event.get("schema") != REMOTE_HANDOFF_EVENT_SCHEMA:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} has an unsupported schema"
            )
        if event.get("sequence") != index:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} has a non-contiguous sequence"
            )
        if event.get("bundle_id") != bundle_id:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} has the wrong bundle_id"
            )
        if event.get("manifest_sha256") != manifest_sha256:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} has the wrong manifest SHA-256"
            )
        if event.get("previous_event_sha256") != previous_digest:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} breaks the hash chain"
            )
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise RemoteHandoffHistoryError(
                f"remote job event {index} has no object payload"
            )
        if index == 1 and event_type != "prepared":
            raise RemoteHandoffHistoryError(
                "remote job event journal must begin with prepared"
            )
        if index > 1 and event_type == "prepared":
            raise RemoteHandoffHistoryError(
                "remote job event journal contains a repeated prepared event"
            )
        if event_type == "submitted":
            identity = payload.get("identity")
            if submitted_identity is not None or not isinstance(identity, dict):
                raise RemoteHandoffHistoryError(
                    "remote job event journal has an invalid submission event"
                )
            submitted_identity = identity
        elif event_type == "status":
            if (
                submitted_identity is None
                or payload.get("identity") != submitted_identity
            ):
                raise RemoteHandoffHistoryError(
                    "remote status event is not bound to the submission identity"
                )
        elif event_type != "prepared":
            raise RemoteHandoffHistoryError(
                f"remote job event {index} has an unsupported event_type"
            )
        declared_digest = event.get("event_sha256")
        unsigned = dict(event)
        unsigned.pop("event_sha256", None)
        observed_digest = _event_sha256(unsigned)
        if declared_digest != observed_digest:
            raise RemoteHandoffHistoryError(
                f"remote job event {index} SHA-256 mismatch"
            )
        previous_digest = observed_digest
        events.append(event)
    return events


def _read_event_journal(
    path: Path,
    *,
    bundle_id: str,
    manifest_sha256: str,
    allow_missing: bool = False,
) -> list[dict[str, Any]]:
    if not os.path.lexists(path):
        if allow_missing:
            return []
        raise RemoteHandoffHistoryError(
            f"remote job event journal was not found: {path}"
        )
    try:
        content = _bounded_read_regular_file(
            path,
            label="remote job event journal",
            max_bytes=MAX_EVENT_JOURNAL_BYTES,
        )
    except RemoteHandoffBindingError as exc:
        raise RemoteHandoffHistoryError(
            f"remote job event journal could not be read safely: {exc}"
        ) from exc
    return _parse_event_journal(
        content,
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
    )


def _append_event(
    *,
    path: Path,
    bundle_id: str,
    manifest_sha256: str,
    events: list[dict[str, Any]],
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if _is_link_like(path):
        raise RemoteHandoffHistoryError(
            "remote job event journal must not be a link or reparse point"
        )
    if len(events) >= MAX_EVENT_COUNT:
        raise RemoteHandoffHistoryError(
            "remote job event journal has reached the configured event-count limit"
        )
    unsigned = {
        "schema": REMOTE_HANDOFF_EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "event_type": event_type,
        "recorded_at": _utc_now(),
        "bundle_id": bundle_id,
        "manifest_sha256": manifest_sha256,
        "previous_event_sha256": (
            events[-1]["event_sha256"] if events else None
        ),
        "payload": payload,
    }
    event = dict(unsigned)
    event["event_sha256"] = _event_sha256(unsigned)
    line = _canonical_json_bytes(event) + b"\n"
    if len(line) > MAX_EVENT_LINE_BYTES:
        raise RemoteHandoffHistoryError(
            "remote job event exceeds the configured line-size limit"
        )
    file_descriptor = os.open(
        path,
        os.O_CREAT
        | os.O_APPEND
        | os.O_WRONLY
        | OS_BINARY_FLAG
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        details = _verify_open_regular_file(
            path,
            file_descriptor,
            label="remote job event journal",
        )
        if details.st_size + len(line) > MAX_EVENT_JOURNAL_BYTES:
            raise RemoteHandoffHistoryError(
                "remote job event journal has reached the configured byte limit"
            )
        _write_all(file_descriptor, line)
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)
    return event


def _verify_manifest_artifacts(
    *,
    bundle_dir: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 3:
        raise RemoteHandoffBindingError(
            "remote handoff manifest must declare exactly three artifacts"
        )
    verified: list[dict[str, Any]] = []
    seen: set[Path] = set()
    seen_roles: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise RemoteHandoffBindingError(
                "remote handoff artifact entry is not an object"
            )
        relative = item.get("bundled_relative_path")
        if not isinstance(relative, str) or not relative:
            raise RemoteHandoffBindingError(
                "remote handoff artifact has no bundled path"
            )
        role = item.get("role")
        if role not in {"model_spec", "castep_script", "input_structure"}:
            raise RemoteHandoffBindingError(
                "remote handoff artifact has an unsupported role"
            )
        if role in seen_roles:
            raise RemoteHandoffBindingError(
                "remote handoff manifest contains duplicate artifact roles"
            )
        seen_roles.add(role)
        expected_relative = {
            "model_spec": "artifacts/model_spec.json",
            "castep_script": "artifacts/castep_script.pl",
        }.get(role)
        if expected_relative is not None and relative != expected_relative:
            raise RemoteHandoffBindingError(
                f"remote handoff {role} path does not match its contract"
            )
        if role == "input_structure" and relative not in {
            "artifacts/structure.cif",
            "artifacts/structure.xsd",
        }:
            raise RemoteHandoffBindingError(
                "remote handoff input structure path does not match its contract"
            )
        declared_size = item.get("bytes")
        if (
            not isinstance(declared_size, int)
            or isinstance(declared_size, bool)
            or declared_size < 0
        ):
            raise RemoteHandoffBindingError(
                "remote handoff artifact has an invalid byte count"
            )
        role_max_bytes = {
            "model_spec": MAX_SPEC_BYTES,
            "castep_script": MAX_SCRIPT_BYTES,
            "input_structure": MAX_INPUT_BYTES,
        }[role]
        if declared_size > role_max_bytes:
            raise RemoteHandoffBindingError(
                f"remote handoff {role} exceeds its configured byte limit"
            )
        declared_digest = item.get("sha256")
        if (
            not isinstance(declared_digest, str)
            or len(declared_digest) != 64
            or any(character not in "0123456789abcdef" for character in declared_digest)
        ):
            raise RemoteHandoffBindingError(
                "remote handoff artifact has an invalid SHA-256"
            )
        unresolved = bundle_dir / relative
        _assert_no_link_components(
            unresolved,
            root=bundle_dir,
            label=f"remote handoff artifact {relative}",
        )
        try:
            path = unresolved.resolve(strict=True)
        except OSError as exc:
            raise RemoteHandoffBindingError(
                f"remote handoff artifact is missing: {relative}"
            ) from exc
        _require_descendant(path, bundle_dir, label="bundled artifact")
        if path in seen:
            raise RemoteHandoffBindingError(
                "remote handoff manifest contains duplicate artifact paths"
            )
        seen.add(path)
        content = _bounded_read_regular_file(
            path,
            label=f"remote handoff artifact {relative}",
            max_bytes=role_max_bytes,
        )
        if len(content) != declared_size:
            raise RemoteHandoffBindingError(
                f"remote handoff artifact size mismatch: {relative}"
            )
        digest = _sha256_bytes(content)
        if digest != declared_digest:
            raise RemoteHandoffBindingError(
                f"remote handoff artifact SHA-256 mismatch: {relative}"
            )
        verified.append(
            {
                "role": role,
                "path": str(path),
                "sha256": digest,
                "bytes": len(content),
            }
        )
    return verified


def _bundle_location(
    *,
    workspace_root_value: str,
    project_id: str,
    bundle_id: str,
) -> tuple[Path, Path, Path, Path]:
    try:
        workspace_root = Path(workspace_root_value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            "workspace directory was not found"
        ) from exc
    project_candidate = workspace_root / project_id
    _assert_no_link_components(
        project_candidate,
        root=workspace_root,
        label="project directory",
    )
    try:
        project_dir = project_candidate.resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            "project directory was not found"
        ) from exc
    _require_descendant(project_dir, workspace_root, label="project directory")
    handoff_root = project_dir / REMOTE_HANDOFF_DIRECTORY
    bundle_dir = handoff_root / bundle_id
    _assert_no_link_components(
        bundle_dir,
        root=project_dir,
        label="remote handoff bundle",
    )
    try:
        resolved_handoff_root = handoff_root.resolve(strict=True)
        resolved_bundle_dir = bundle_dir.resolve(strict=True)
    except OSError as exc:
        raise RemoteHandoffBindingError(
            f"remote handoff bundle was not found: {bundle_id}"
        ) from exc
    _require_descendant(
        resolved_bundle_dir,
        resolved_handoff_root,
        label="remote handoff bundle",
    )
    return (
        resolved_bundle_dir,
        resolved_bundle_dir / "manifest.json",
        resolved_bundle_dir / "events.jsonl",
        resolved_bundle_dir / "remote_job.lock",
    )


def _load_bundle(
    *,
    workspace_root_value: str,
    project_id: str,
    bundle_id: str,
    expected_manifest_sha256: str,
) -> tuple[Path, dict[str, Any], str, list[dict[str, Any]], list[dict[str, Any]], Path]:
    bundle_dir, manifest_path, events_path, lock_path = _bundle_location(
        workspace_root_value=workspace_root_value,
        project_id=project_id,
        bundle_id=bundle_id,
    )
    manifest, manifest_bytes = _read_json_object(
        manifest_path,
        label="remote handoff manifest",
    )
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    if manifest_sha256 != expected_manifest_sha256:
        raise RemoteHandoffBindingError(
            "remote handoff manifest SHA-256 does not match the request"
        )
    if manifest.get("schema") != REMOTE_HANDOFF_MANIFEST_SCHEMA:
        raise RemoteHandoffBindingError(
            "remote handoff manifest schema is unsupported"
        )
    if manifest.get("bundle_id") != bundle_id:
        raise RemoteHandoffBindingError(
            "remote handoff manifest bundle_id does not match the request"
        )
    if manifest.get("project_id") != project_id:
        raise RemoteHandoffBindingError(
            "remote handoff manifest project_id does not match the request"
        )
    verified_artifacts = _verify_manifest_artifacts(
        bundle_dir=bundle_dir,
        manifest=manifest,
    )
    events = _read_event_journal(
        events_path,
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
    )
    return (
        bundle_dir,
        manifest,
        manifest_sha256,
        verified_artifacts,
        events,
        lock_path,
    )


def _prepared_event_payload(plan: _BundlePlan) -> dict[str, Any]:
    return {
        "project_id": plan.request.project_id,
        "revision": plan.request.expected_revision,
        "calculation_name": plan.request.calculation_name,
        "task": plan.request.task.value,
        "revision_binding": dict(plan.manifest["revision_binding"]),
    }


def prepare_remote_castep_bundle(
    request_value: RemoteCastepBundleRequest | Mapping[str, Any],
) -> dict[str, Any]:
    """Preview or publish an immutable local CASTEP handoff bundle."""

    plan = _build_bundle_plan(request_value)
    common = {
        "ok": True,
        "schema": REMOTE_HANDOFF_MANIFEST_SCHEMA,
        "execution_mode": plan.request.execution_mode.value,
        "project_id": plan.request.project_id,
        "revision": plan.request.expected_revision,
        "bundle_id": plan.bundle_id,
        "bundle_dir": str(plan.bundle_dir),
        "manifest_path": str(plan.manifest_path),
        "events_path": str(plan.events_path),
        "lock_path": str(plan.lock_path),
        "manifest_sha256": plan.manifest_sha256,
        "manifest": plan.manifest,
        "shell_execution_performed": False,
        "ssh_execution_performed": False,
        "scheduler_execution_performed": False,
        "materials_studio_execution_performed": False,
        "gui_input_performed": False,
    }
    if plan.request.execution_mode is ExecutionMode.PREVIEW:
        return {
            **common,
            "status": "preview",
            "write_performed": False,
            "publication": [],
            "execute_binding": {
                "expected_preview_manifest_sha256": plan.manifest_sha256,
            },
        }
    if (
        plan.request.expected_preview_manifest_sha256
        != plan.manifest_sha256
    ):
        raise RemoteHandoffBindingError(
            "remote handoff execute manifest SHA-256 does not match the exact preview"
        )

    with _remote_job_write_lock(
        plan.lock_path,
        timeout_seconds=plan.request.lock_timeout_seconds,
        allowed_root=plan.project_dir,
    ) as transaction:
        locked_plan = _build_bundle_plan(plan.request)
        if (
            locked_plan.bundle_id != plan.bundle_id
            or locked_plan.manifest_sha256 != plan.manifest_sha256
        ):
            raise RemoteHandoffBindingError(
                "remote handoff plan changed while waiting for its lock"
            )
        publication: list[dict[str, str]] = []
        for relative, content in locked_plan.artifact_bytes.items():
            path = locked_plan.bundle_dir / relative
            publication.append(
                {
                    "path": str(path),
                    "status": _publish_immutable_file(
                        path,
                        content,
                        allowed_root=locked_plan.project_dir,
                    ),
                }
            )
        publication.append(
            {
                "path": str(locked_plan.manifest_path),
                "status": _publish_immutable_file(
                    locked_plan.manifest_path,
                    locked_plan.manifest_bytes,
                    allowed_root=locked_plan.project_dir,
                ),
            }
        )
        events = _read_event_journal(
            locked_plan.events_path,
            bundle_id=locked_plan.bundle_id,
            manifest_sha256=locked_plan.manifest_sha256,
            allow_missing=True,
        )
        prepared_payload = _prepared_event_payload(locked_plan)
        if events:
            prepared_events = [
                event for event in events if event.get("event_type") == "prepared"
            ]
            if len(prepared_events) != 1 or prepared_events[0].get("payload") != prepared_payload:
                raise RemoteHandoffHistoryError(
                    "remote handoff prepared evidence conflicts with the manifest"
                )
            prepared_event = prepared_events[0]
            event_status = "verified_existing"
        else:
            prepared_event = _append_event(
                path=locked_plan.events_path,
                bundle_id=locked_plan.bundle_id,
                manifest_sha256=locked_plan.manifest_sha256,
                events=events,
                event_type="prepared",
                payload=prepared_payload,
            )
            event_status = "appended"
        verified_artifacts = _verify_manifest_artifacts(
            bundle_dir=locked_plan.bundle_dir,
            manifest=locked_plan.manifest,
        )
        final_events = _read_event_journal(
            locked_plan.events_path,
            bundle_id=locked_plan.bundle_id,
            manifest_sha256=locked_plan.manifest_sha256,
        )

    return {
        **common,
        "status": "prepared",
        "write_performed": (
            event_status == "appended"
            or any(item["status"] == "published" for item in publication)
        ),
        "publication": publication,
        "prepared_event_status": event_status,
        "prepared_event": prepared_event,
        "event_count": len(final_events),
        "artifact_integrity_status": "verified",
        "verified_artifacts": verified_artifacts,
        "write_transaction": transaction,
    }


def _identity_payload(
    request: (
        RemoteSubmissionRecordRequest
        | RemoteStatusRecordRequest
        | RemoteStatusQuery
    ),
) -> dict[str, Any]:
    return request.identity.model_dump(mode="json")


def record_remote_submission(
    request_value: RemoteSubmissionRecordRequest | Mapping[str, Any],
) -> dict[str, Any]:
    """Append evidence for an already-performed external submission."""

    request = _coerce_request(RemoteSubmissionRecordRequest, request_value)
    _, _, _, lock_path = _bundle_location(
        workspace_root_value=request.workspace_root,
        project_id=request.project_id,
        bundle_id=request.bundle_id,
    )
    with _remote_job_write_lock(
        lock_path,
        timeout_seconds=request.lock_timeout_seconds,
        allowed_root=Path(request.workspace_root).expanduser().resolve(strict=True),
    ) as transaction:
        (
            bundle_dir,
            manifest,
            manifest_sha256,
            verified_artifacts,
            events,
            _,
        ) = _load_bundle(
            workspace_root_value=request.workspace_root,
            project_id=request.project_id,
            bundle_id=request.bundle_id,
            expected_manifest_sha256=request.expected_manifest_sha256,
        )
        payload = {
            "identity": _identity_payload(request),
            "submitted_at": request.submitted_at,
            "channel": request.channel.value,
            "initial_state": RemoteJobState.QUEUED.value,
            "note": request.note,
        }
        prior = [
            event for event in events if event.get("event_type") == "submitted"
        ]
        if prior:
            if len(prior) == 1 and prior[0].get("payload") == payload:
                event = prior[0]
                event_status = "verified_existing"
            else:
                raise RemoteHandoffBindingError(
                    "remote handoff already has a different submission identity or receipt"
                )
        else:
            event = _append_event(
                path=bundle_dir / "events.jsonl",
                bundle_id=request.bundle_id,
                manifest_sha256=manifest_sha256,
                events=events,
                event_type="submitted",
                payload=payload,
            )
            event_status = "appended"
        final_events = _read_event_journal(
            bundle_dir / "events.jsonl",
            bundle_id=request.bundle_id,
            manifest_sha256=manifest_sha256,
        )
    return {
        "ok": True,
        "status": "submitted",
        "event_status": event_status,
        "write_performed": event_status == "appended",
        "bundle_id": request.bundle_id,
        "manifest_sha256": manifest_sha256,
        "revision": manifest.get("revision"),
        "identity": _identity_payload(request),
        "event": event,
        "event_count": len(final_events),
        "artifact_integrity_status": "verified",
        "verified_artifacts": verified_artifacts,
        "write_transaction": transaction,
        "submission_performed_by_this_module": False,
        "shell_execution_performed": False,
        "ssh_execution_performed": False,
        "scheduler_execution_performed": False,
    }


_TERMINAL_STATES = {
    RemoteJobState.SUCCEEDED,
    RemoteJobState.FAILED,
    RemoteJobState.CANCELLED,
}


def _submission_event(
    events: list[dict[str, Any]],
    identity: dict[str, Any],
) -> dict[str, Any]:
    submitted = [
        event for event in events if event.get("event_type") == "submitted"
    ]
    if len(submitted) != 1:
        raise RemoteHandoffBindingError(
            "remote handoff must have exactly one submission before status evidence"
        )
    if submitted[0].get("payload", {}).get("identity") != identity:
        raise RemoteHandoffBindingError(
            "scheduler/job identity does not match the recorded submission"
        )
    return submitted[0]


def _current_recorded_state(events: list[dict[str, Any]]) -> RemoteJobState:
    status_events = [
        event for event in events if event.get("event_type") == "status"
    ]
    if not status_events:
        return RemoteJobState.QUEUED
    try:
        return RemoteJobState(status_events[-1]["payload"]["state"])
    except (KeyError, ValueError, TypeError) as exc:
        raise RemoteHandoffHistoryError(
            "remote status event has an unsupported state"
        ) from exc


def _validate_status_transition(
    current: RemoteJobState,
    requested: RemoteJobState,
) -> None:
    if current in _TERMINAL_STATES and requested is not current:
        raise RemoteHandoffBindingError(
            f"terminal remote state {current.value} cannot transition to {requested.value}"
        )
    allowed = {
        RemoteJobState.QUEUED: {
            RemoteJobState.QUEUED,
            RemoteJobState.RUNNING,
            RemoteJobState.SUCCEEDED,
            RemoteJobState.FAILED,
            RemoteJobState.CANCELLED,
            RemoteJobState.UNKNOWN,
        },
        RemoteJobState.RUNNING: {
            RemoteJobState.RUNNING,
            RemoteJobState.SUCCEEDED,
            RemoteJobState.FAILED,
            RemoteJobState.CANCELLED,
            RemoteJobState.UNKNOWN,
        },
        RemoteJobState.UNKNOWN: set(RemoteJobState),
        RemoteJobState.SUCCEEDED: {RemoteJobState.SUCCEEDED},
        RemoteJobState.FAILED: {RemoteJobState.FAILED},
        RemoteJobState.CANCELLED: {RemoteJobState.CANCELLED},
    }
    if requested not in allowed[current]:
        raise RemoteHandoffBindingError(
            f"remote state {current.value} cannot transition to {requested.value}"
        )


def record_remote_status(
    request_value: RemoteStatusRecordRequest | Mapping[str, Any],
) -> dict[str, Any]:
    """Append one externally observed status; perform no remote query."""

    request = _coerce_request(RemoteStatusRecordRequest, request_value)
    _, _, _, lock_path = _bundle_location(
        workspace_root_value=request.workspace_root,
        project_id=request.project_id,
        bundle_id=request.bundle_id,
    )
    with _remote_job_write_lock(
        lock_path,
        timeout_seconds=request.lock_timeout_seconds,
        allowed_root=Path(request.workspace_root).expanduser().resolve(strict=True),
    ) as transaction:
        (
            bundle_dir,
            manifest,
            manifest_sha256,
            verified_artifacts,
            events,
            _,
        ) = _load_bundle(
            workspace_root_value=request.workspace_root,
            project_id=request.project_id,
            bundle_id=request.bundle_id,
            expected_manifest_sha256=request.expected_manifest_sha256,
        )
        identity = _identity_payload(request)
        submitted = _submission_event(events, identity)
        submitted_at = submitted["payload"]["submitted_at"]
        if request.observed_at < submitted_at:
            raise RemoteHandoffBindingError(
                "status observed_at precedes submitted_at"
            )
        prior_status_events = [
            event for event in events if event.get("event_type") == "status"
        ]
        if (
            prior_status_events
            and request.observed_at
            < prior_status_events[-1]["payload"]["observed_at"]
        ):
            raise RemoteHandoffBindingError(
                "status observed_at precedes the latest recorded observation"
            )
        payload = {
            "identity": identity,
            "observed_at": request.observed_at,
            "state": request.state.value,
            "detail": request.detail,
            "scheduler_message_id": request.scheduler_message_id,
        }
        exact = [
            event
            for event in prior_status_events
            if event.get("payload") == payload
        ]
        if exact:
            event = exact[-1]
            event_status = "verified_existing"
        else:
            _validate_status_transition(
                _current_recorded_state(events),
                request.state,
            )
            event = _append_event(
                path=bundle_dir / "events.jsonl",
                bundle_id=request.bundle_id,
                manifest_sha256=manifest_sha256,
                events=events,
                event_type="status",
                payload=payload,
            )
            event_status = "appended"
        final_events = _read_event_journal(
            bundle_dir / "events.jsonl",
            bundle_id=request.bundle_id,
            manifest_sha256=manifest_sha256,
        )
        current_state = _current_recorded_state(final_events)
    return {
        "ok": True,
        "status": "status_recorded",
        "event_status": event_status,
        "write_performed": event_status == "appended",
        "bundle_id": request.bundle_id,
        "manifest_sha256": manifest_sha256,
        "revision": manifest.get("revision"),
        "identity": identity,
        "current_state": current_state.value,
        "event": event,
        "event_count": len(final_events),
        "artifact_integrity_status": "verified",
        "verified_artifacts": verified_artifacts,
        "write_transaction": transaction,
        "remote_query_performed": False,
        "shell_execution_performed": False,
        "ssh_execution_performed": False,
        "scheduler_execution_performed": False,
    }


def read_remote_job_status(
    request_value: RemoteStatusQuery | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a read-only local projection for an exact scheduler/job id."""

    request = _coerce_request(RemoteStatusQuery, request_value)
    _, _, _, lock_path = _bundle_location(
        workspace_root_value=request.workspace_root,
        project_id=request.project_id,
        bundle_id=request.bundle_id,
    )
    with _remote_job_existing_lock(
        lock_path,
        timeout_seconds=request.lock_timeout_seconds,
        allowed_root=Path(request.workspace_root).expanduser().resolve(strict=True),
    ) as transaction:
        (
            bundle_dir,
            manifest,
            manifest_sha256,
            verified_artifacts,
            events,
            loaded_lock_path,
        ) = _load_bundle(
            workspace_root_value=request.workspace_root,
            project_id=request.project_id,
            bundle_id=request.bundle_id,
            expected_manifest_sha256=request.expected_manifest_sha256,
        )
        if not _same_path(loaded_lock_path, lock_path):
            raise RemoteHandoffBindingError(
                "remote job lock changed identity while status was waiting"
            )
        identity = _identity_payload(request)
        submitted = _submission_event(events, identity)
        current_state = _current_recorded_state(events)
        status_events = [
            event for event in events if event.get("event_type") == "status"
        ]
        tail = events[-REMOTE_HANDOFF_EVENT_TAIL_LIMIT:]
    return {
        "ok": True,
        "status": current_state.value,
        "source": "local_append_only_event_journal",
        "bundle_id": request.bundle_id,
        "bundle_dir": str(bundle_dir),
        "manifest_sha256": manifest_sha256,
        "project_id": manifest.get("project_id"),
        "revision": manifest.get("revision"),
        "calculation": manifest.get("calculation"),
        "identity": identity,
        "submission": submitted,
        "latest_status_event": status_events[-1] if status_events else None,
        "latest_event": events[-1],
        "event_count": len(events),
        "event_tail": tail,
        "event_tail_truncated": len(events) > len(tail),
        "journal_consistency_status": "consistent",
        "artifact_integrity_status": "verified",
        "verified_artifacts": verified_artifacts,
        "lock_path": str(lock_path),
        "lock_exists": lock_path.is_file(),
        "read_transaction": transaction,
        "write_performed": False,
        "filesystem_write_performed": False,
        "remote_query_performed": False,
        "shell_execution_performed": False,
        "ssh_execution_performed": False,
        "scheduler_execution_performed": False,
        "materials_studio_execution_performed": False,
        "gui_input_performed": False,
    }


__all__ = [
    "REMOTE_HANDOFF_EVENT_SCHEMA",
    "REMOTE_HANDOFF_MANIFEST_SCHEMA",
    "RemoteHandoffBindingError",
    "RemoteHandoffBusyError",
    "RemoteHandoffError",
    "RemoteHandoffHistoryError",
    "prepare_remote_castep_bundle",
    "read_remote_job_status",
    "record_remote_status",
    "record_remote_submission",
]
