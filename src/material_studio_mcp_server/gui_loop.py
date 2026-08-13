"""Signed, fixed-operation queue for a Materials Studio GUI-side loop.

The queue is intentionally narrower than a general MaterialsScript runner.  A
job can only request that one workspace-confined structure artifact is imported
into the already-bound Materials Studio GUI session.  No script body, command,
or expression is accepted from the caller.

The queue root is stable for the lifetime of a ``pid``/``window_handle``/
``project_id`` binding.  Revisions advance through a compare-and-swap contract:
each job records the signed current revision as ``expected_revision`` and the
requested ``target_revision``.  The generated GUI loop updates the signed state
only after the import succeeds.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import errno
import os
import re
import secrets
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from material_studio_mcp_server.state.store import atomic_write_text


PROTOCOL = "materials-studio-gui-loop-v1"
ENVELOPE_VERSION = 1
ALLOWED_OPERATION = "import_structure"
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DOCUMENT_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,180}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_ENVELOPE_BYTES = 1_048_576
ALLOWED_STRUCTURE_EXTENSIONS = {".arc", ".car", ".cif", ".mol", ".pdb", ".xsd", ".xtd"}


def _lock_descriptor_nonblocking(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _producer_lock(path: Path, *, timeout_seconds: float, poll_seconds: float):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    started = time.monotonic()
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        deadline = started + max(float(timeout_seconds), 0.0)
        while True:
            try:
                _lock_descriptor_nonblocking(descriptor)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise GuiLoopError(
                        "Another producer owns the exact GUI-loop queue",
                        {
                            "status": "producer_busy",
                            "producer_lock_path": str(path),
                        },
                    ) from exc
                time.sleep(max(float(poll_seconds), 0.005))
        yield
    finally:
        if acquired:
            try:
                _unlock_descriptor(descriptor)
            except OSError:
                pass
        os.close(descriptor)


class GuiLoopError(RuntimeError):
    """A GUI-loop operation failed with a machine-readable receipt."""

    def __init__(self, message: str, receipt: Mapping[str, Any] | None = None) -> None:
        self.receipt = dict(receipt or {})
        super().__init__(message)


class GuiLoopManager:
    """Prepare and communicate with one fixed-operation GUI-side loop."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        secret: bytes | str | None = None,
        heartbeat_max_age_seconds: float = 15.0,
        poll_seconds: float = 0.05,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.gui_loop_root = (self.workspace_root / "gui_loop").resolve()
        self._manager_key_path = self.gui_loop_root / "manager.key"
        if isinstance(secret, str):
            secret_bytes = secret.encode("utf-8")
        elif secret is not None:
            secret_bytes = bytes(secret)
        else:
            secret_bytes = None
        if secret_bytes is not None and len(secret_bytes) < 32:
            raise ValueError("GUI-loop HMAC secret must contain at least 32 bytes")
        self._secret = secret_bytes
        self.heartbeat_max_age_seconds = max(float(heartbeat_max_age_seconds), 0.1)
        self.poll_seconds = max(float(poll_seconds), 0.005)

    def prepare(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        """Create an exact-session queue and its fixed MaterialsScript loop.

        ``base_revision`` (or the legacy spelling ``revision``) seeds a new
        session only.  Re-running prepare never rewinds an existing signed
        ``current_state``.
        """

        normalized = self._normalize_binding(binding, require_base_revision=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.gui_loop_root.mkdir(parents=True, exist_ok=True)
        self._secret_for_write()
        paths = self._paths(normalized)
        for name in ("staging", "pending", "running", "done", "failed", "control"):
            paths[name].mkdir(parents=True, exist_ok=True)

        self._ensure_key_file(paths["key"])
        session_binding = self._session_binding(normalized)
        config_payload = {
            "kind": "config",
            "protocol": PROTOCOL,
            "envelope_version": ENVELOPE_VERSION,
            "binding": session_binding,
            "base_revision": normalized["base_revision"],
            "initial_document_name": normalized["initial_document_name"],
            "workspace_root": str(self.workspace_root),
            "queue_root": str(paths["root"]),
        }
        configured_base_revision = normalized["base_revision"]
        if paths["config"].exists():
            existing_config = self._read_signed_envelope(paths["config"], "config")
            if existing_config.get("binding") != session_binding:
                raise GuiLoopError(
                    "Existing GUI-loop configuration has a different session binding",
                    {"status": "binding_conflict", "binding": session_binding},
                )
            configured_base_revision = self._revision(
                existing_config.get("base_revision"), "base_revision"
            )
            if (
                existing_config.get("initial_document_name")
                != normalized["initial_document_name"]
            ):
                raise GuiLoopError(
                    "Existing GUI-loop configuration has a different initial document",
                    {"status": "binding_conflict", "binding": session_binding},
                )
        else:
            self._write_signed_envelope(paths["config"], config_payload)

        if paths["state"].exists():
            current_state = self._read_signed_envelope(paths["state"], "current_state")
            self._require_payload_binding(current_state, normalized)
        else:
            current_state = {
                "kind": "current_state",
                "protocol": PROTOCOL,
                "binding": session_binding,
                "current_revision": normalized["base_revision"],
                "current_document_name": normalized["initial_document_name"],
                "last_job_id": None,
                "structure_sha256": "",
                "updated_at_epoch": time.time(),
            }
            self._write_signed_envelope(paths["state"], current_state)

        atomic_write_text(paths["script"], self._render_loop_script(normalized, paths))
        return {
            "ok": True,
            "status": "prepared",
            "protocol": PROTOCOL,
            "binding": {
                **session_binding,
                "base_revision": configured_base_revision,
            },
            "queue_root": str(paths["root"]),
            "loop_script_path": str(paths["script"]),
            "current_revision": int(current_state["current_revision"]),
            "current_document_name": current_state.get("current_document_name"),
            "operation_allowlist": [ALLOWED_OPERATION],
            "arbitrary_script_supported": False,
            "requires_gui_context_start": True,
            "secret_exposed": False,
        }

    def status(
        self,
        binding: Mapping[str, Any],
        *,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Return signed loop health, CAS state, and optional job status."""

        normalized = self._normalize_binding(binding, require_base_revision=False)
        paths = self._paths(normalized)
        receipt: dict[str, Any] = {
            "ok": True,
            "protocol": PROTOCOL,
            "binding": self._session_binding(normalized),
            "queue_root": str(paths["root"]),
            "prepared": paths["script"].is_file() and paths["config"].is_file(),
            "loop_lock_present": paths["lock"].is_file(),
            "stop_requested": paths["stop"].is_file(),
            "heartbeat_present": paths["heartbeat"].is_file(),
            "heartbeat_signature_valid": False,
            "heartbeat_fresh": False,
            "current_state_signature_valid": False,
        }
        if paths["lock"].is_file():
            try:
                if paths["lock"].stat().st_size > 256:
                    raise ValueError("loop lock marker is too large")
                receipt["loop_lock_id"] = paths["lock"].read_text(
                    encoding="ascii"
                ).strip()
            except (OSError, UnicodeError, ValueError) as exc:
                receipt["ok"] = False
                receipt["loop_lock_error"] = self._bounded_error(exc)

        current_state: dict[str, Any] | None = None
        if paths["state"].is_file():
            try:
                current_state = self._read_signed_envelope(paths["state"], "current_state")
                self._require_payload_binding(current_state, normalized)
                receipt["current_state_signature_valid"] = True
                receipt["current_revision"] = int(current_state["current_revision"])
                receipt["current_document_name"] = current_state.get("current_document_name")
                receipt["last_job_id"] = current_state.get("last_job_id")
            except (GuiLoopError, KeyError, TypeError, ValueError) as exc:
                receipt["ok"] = False
                receipt["current_state_error"] = self._bounded_error(exc)

        heartbeat: dict[str, Any] | None = None
        if paths["heartbeat"].is_file():
            try:
                heartbeat = self._read_signed_envelope(paths["heartbeat"], "heartbeat")
                self._require_payload_binding(heartbeat, normalized)
                heartbeat_epoch = float(heartbeat["heartbeat_at_epoch"])
                age = time.time() - heartbeat_epoch
                receipt["heartbeat_signature_valid"] = True
                receipt["heartbeat_age_seconds"] = round(age, 6)
                receipt["heartbeat_fresh"] = -5.0 <= age <= self.heartbeat_max_age_seconds
                receipt["loop_id"] = heartbeat.get("loop_id")
                receipt["heartbeat_current_revision"] = heartbeat.get("current_revision")
                receipt["loop_status"] = heartbeat.get("status")
                receipt["heartbeat_active_document_name"] = heartbeat.get(
                    "active_document_name"
                )
            except (GuiLoopError, KeyError, TypeError, ValueError) as exc:
                receipt["ok"] = False
                receipt["heartbeat_error"] = self._bounded_error(exc)

        receipt["loop_lock_matches_heartbeat"] = bool(
            receipt.get("loop_lock_id")
            and receipt.get("loop_lock_id") == receipt.get("loop_id")
        )
        receipt["heartbeat_revision_matches_state"] = bool(
            receipt.get("heartbeat_current_revision") is not None
            and receipt.get("current_revision") is not None
            and int(receipt["heartbeat_current_revision"])
            == int(receipt["current_revision"])
        )
        receipt["heartbeat_document_matches_state"] = bool(
            receipt.get("heartbeat_active_document_name")
            and receipt.get("current_document_name")
            and receipt.get("heartbeat_active_document_name")
            == receipt.get("current_document_name")
        )
        receipt["loop_ready"] = bool(
            receipt["prepared"]
            and receipt["loop_lock_present"]
            and receipt["loop_lock_matches_heartbeat"]
            and receipt["heartbeat_signature_valid"]
            and receipt["heartbeat_fresh"]
            and receipt["heartbeat_revision_matches_state"]
            and receipt["heartbeat_document_matches_state"]
            and receipt["current_state_signature_valid"]
            and not receipt["stop_requested"]
        )
        if receipt["loop_ready"]:
            receipt["status"] = "running"
        elif receipt["heartbeat_present"] and receipt["heartbeat_signature_valid"]:
            receipt["status"] = "stale" if not receipt["heartbeat_fresh"] else "not_ready"
        elif receipt["prepared"]:
            receipt["status"] = "prepared"
        else:
            receipt["status"] = "not_prepared"

        receipt["queue"] = {
            name: self._job_ids(paths[name])
            for name in ("staging", "pending", "running", "done", "failed")
        }
        if job_id is not None:
            receipt["job"] = self._job_status(paths, job_id)
        return receipt

    def enqueue_and_wait(
        self,
        structure_path: str | Path,
        binding: Mapping[str, Any],
        target_revision: int,
        timeout_seconds: float = 30.0,
        *,
        document_name: str | None = None,
    ) -> dict[str, Any]:
        """Serialize one producer through its signed terminal receipt."""

        normalized = self._normalize_binding(binding, require_base_revision=False)
        paths = self._paths(normalized)
        if not paths["root"].is_dir():
            raise GuiLoopError(
                "GUI loop has not been prepared",
                {"status": "not_prepared", "binding": self._session_binding(normalized)},
            )
        with _producer_lock(
            paths["producer_lock"],
            timeout_seconds=max(float(timeout_seconds), 0.0),
            poll_seconds=self.poll_seconds,
        ):
            return self._enqueue_and_wait_locked(
                structure_path,
                normalized,
                target_revision,
                timeout_seconds,
                document_name=document_name,
            )

    def _enqueue_and_wait_locked(
        self,
        structure_path: str | Path,
        binding: Mapping[str, Any],
        target_revision: int,
        timeout_seconds: float = 30.0,
        *,
        document_name: str | None = None,
    ) -> dict[str, Any]:
        """Atomically enqueue one fixed import and wait for its signed terminal result."""

        normalized = self._normalize_binding(binding, require_base_revision=False)
        target_revision = self._revision(target_revision, "target_revision")
        preflight = self.status(normalized)
        if not preflight.get("loop_ready"):
            raise GuiLoopError(
                "The bound Materials Studio GUI loop is not ready",
                {"status": "loop_not_ready", "preflight": preflight},
            )
        active = preflight["queue"]["pending"] + preflight["queue"]["running"]
        if active:
            raise GuiLoopError(
                "The bound GUI loop already has an active job",
                {"status": "queue_busy", "active_job_ids": active},
            )
        expected_revision = int(preflight["current_revision"])
        if "revision" in binding and int(binding["revision"]) != expected_revision:
            raise GuiLoopError(
                "Caller revision does not match the signed GUI-loop current revision",
                {
                    "status": "revision_conflict",
                    "expected_revision": int(binding["revision"]),
                    "current_revision": expected_revision,
                },
            )

        structure = self._workspace_file(structure_path)
        if not structure.is_file():
            raise GuiLoopError(
                "Structure artifact does not exist",
                {"status": "structure_missing", "structure_path": str(structure)},
            )
        if structure.suffix.lower() not in ALLOWED_STRUCTURE_EXTENSIONS:
            raise GuiLoopError(
                "Structure artifact extension is not allowed for GUI hot-load",
                {
                    "status": "structure_extension_not_allowed",
                    "structure_path": str(structure),
                    "allowed_extensions": sorted(ALLOWED_STRUCTURE_EXTENSIONS),
                },
            )
        if document_name is None:
            # Materials Studio's Document.Name omits the imported file suffix.
            document_name = structure.stem
        if not DOCUMENT_NAME_RE.fullmatch(document_name) or any(
            token in document_name for token in ("/", "\\")
        ):
            raise ValueError("document_name must be a bounded file name without path separators")

        job_id = uuid.uuid4().hex
        paths = self._paths(normalized)
        created_at_epoch = time.time()
        payload = {
            "kind": "job",
            "protocol": PROTOCOL,
            "operation": ALLOWED_OPERATION,
            "job_id": job_id,
            "binding": self._session_binding(normalized),
            "expected_revision": expected_revision,
            "target_revision": target_revision,
            "structure_path": str(structure),
            "structure_sha256": self._sha256_file(structure),
            "document_name": document_name,
            "created_at_epoch": created_at_epoch,
            "expires_at_epoch": created_at_epoch
            + max(float(timeout_seconds), 0.1),
        }
        staging_path = paths["staging"] / f"{job_id}.json"
        pending_path = paths["pending"] / staging_path.name
        self._write_signed_envelope(staging_path, payload)
        os.replace(staging_path, pending_path)

        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        while True:
            job = self._job_status(paths, job_id)
            if job["status"] == "done":
                result = job.get("result")
                if result is None:
                    if time.monotonic() < deadline:
                        time.sleep(self.poll_seconds)
                        continue
                    result = {}
                if result.get("status") != "done":
                    raise GuiLoopError(
                        "GUI loop published an invalid completion receipt",
                        {
                            "status": "invalid_terminal_receipt",
                            "job_id": job_id,
                            "expected_revision": expected_revision,
                            "target_revision": target_revision,
                            "structure_sha256": payload["structure_sha256"],
                            "job": job,
                            "side_effect_may_have_occurred": True,
                            "automatic_dialog_fallback_allowed": False,
                        },
                    )
                imported_document_name = result.get("current_document_name")
                try:
                    committed_state = self._read_signed_envelope(
                        paths["state"], "current_state"
                    )
                    self._require_payload_binding(committed_state, normalized)
                except (GuiLoopError, OSError) as exc:
                    raise GuiLoopError(
                        "GUI loop completion has no verifiable committed state",
                        {
                            "status": "terminal_state_verification_failed",
                            "job_id": job_id,
                            "expected_revision": expected_revision,
                            "target_revision": target_revision,
                            "structure_sha256": payload["structure_sha256"],
                            "job": job,
                            "error": self._bounded_error(exc),
                            "side_effect_may_have_occurred": True,
                            "automatic_dialog_fallback_allowed": False,
                        },
                    ) from exc
                terminal_structure_sha256 = result.get("structure_sha256")
                terminal_matches = bool(
                    result.get("job_id") == job_id
                    and result.get("current_revision") == target_revision
                    and isinstance(imported_document_name, str)
                    and imported_document_name.strip()
                    and imported_document_name == document_name
                    and terminal_structure_sha256
                    in {None, payload["structure_sha256"]}
                    and committed_state.get("current_revision") == target_revision
                    and committed_state.get("current_document_name") == document_name
                    and committed_state.get("last_job_id") == job_id
                    and committed_state.get("structure_sha256")
                    == payload["structure_sha256"]
                )
                if not terminal_matches:
                    raise GuiLoopError(
                        "GUI loop completion receipt does not match the queued job",
                        {
                            "status": "terminal_receipt_binding_mismatch",
                            "job_id": job_id,
                            "expected_revision": expected_revision,
                            "target_revision": target_revision,
                            "job": job,
                            "side_effect_may_have_occurred": True,
                            "automatic_dialog_fallback_allowed": False,
                        },
                    )
                post_commit = self.status(normalized, job_id=job_id)
                if not (
                    post_commit.get("loop_ready") is True
                    and post_commit.get("current_revision") == target_revision
                    and post_commit.get("current_document_name") == document_name
                ):
                    if time.monotonic() < deadline:
                        time.sleep(self.poll_seconds)
                        continue
                    raise GuiLoopError(
                        "GUI loop committed the import but did not publish a matching heartbeat",
                        {
                            "status": "post_commit_heartbeat_timeout",
                            "job_id": job_id,
                            "expected_revision": expected_revision,
                            "target_revision": target_revision,
                            "structure_sha256": payload["structure_sha256"],
                            "job": job,
                            "post_commit_status": post_commit,
                            "side_effect_may_have_occurred": True,
                            "automatic_dialog_fallback_allowed": False,
                        },
                    )
                return {
                    "ok": True,
                    "status": "done",
                    "job_id": job_id,
                    "operation": ALLOWED_OPERATION,
                    "expected_revision": expected_revision,
                    "target_revision": target_revision,
                    "structure_path": str(structure),
                    "structure_sha256": payload["structure_sha256"],
                    "imported_document_name": imported_document_name,
                    "terminal_structure_sha256_source": (
                        "job_result" if terminal_structure_sha256 else "current_state"
                    ),
                    "result": result,
                    "post_commit_status": post_commit,
                }
            if job["status"] == "failed":
                if job.get("result") is None and time.monotonic() < deadline:
                    time.sleep(self.poll_seconds)
                    continue
                raise GuiLoopError(
                    "Materials Studio GUI loop import failed",
                    {
                        "status": "failed",
                        "job_id": job_id,
                        "expected_revision": expected_revision,
                        "target_revision": target_revision,
                        "structure_sha256": payload["structure_sha256"],
                        "job": job,
                        "side_effect_may_have_occurred": True,
                        "automatic_dialog_fallback_allowed": False,
                    },
                )
            if time.monotonic() >= deadline:
                raise GuiLoopError(
                    "Timed out waiting for the Materials Studio GUI loop",
                    {
                        "status": "timeout",
                        "job_id": job_id,
                        "expected_revision": expected_revision,
                        "target_revision": target_revision,
                        "structure_sha256": payload["structure_sha256"],
                        "last_job_status": job,
                        "side_effect_may_have_occurred": job["status"]
                        in {"pending", "running", "done"},
                        "automatic_dialog_fallback_allowed": False,
                    },
                )
            time.sleep(self.poll_seconds)

    def request_stop(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        """Publish a signed stop request for the exact bound loop."""

        normalized = self._normalize_binding(binding, require_base_revision=False)
        paths = self._paths(normalized)
        if not paths["root"].is_dir():
            raise GuiLoopError(
                "GUI loop has not been prepared",
                {"status": "not_prepared", "binding": self._session_binding(normalized)},
            )
        if paths["stop"].is_file():
            existing = self._read_signed_envelope(paths["stop"], "stop")
            self._require_payload_binding(existing, normalized)
            return {
                "ok": True,
                "status": "stop_requested",
                "binding": self._session_binding(normalized),
                "request_id": existing["request_id"],
                "idempotent": True,
                "secret_exposed": False,
            }
        payload = {
            "kind": "stop",
            "protocol": PROTOCOL,
            "binding": self._session_binding(normalized),
            "requested_at_epoch": time.time(),
            "request_id": uuid.uuid4().hex,
        }
        self._write_signed_envelope(paths["stop"], payload)
        return {
            "ok": True,
            "status": "stop_requested",
            "binding": self._session_binding(normalized),
            "request_id": payload["request_id"],
            "secret_exposed": False,
        }

    def _normalize_binding(
        self,
        binding: Mapping[str, Any],
        *,
        require_base_revision: bool,
    ) -> dict[str, Any]:
        try:
            pid = int(binding["pid"])
            window_handle = int(binding["window_handle"])
            project_id = str(binding["project_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("binding requires integer pid/window_handle and project_id") from exc
        if pid <= 0 or window_handle <= 0:
            raise ValueError("binding pid and window_handle must be positive")
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("binding project_id may contain only letters, digits, _ and -")
        normalized: dict[str, Any] = {
            "pid": pid,
            "window_handle": window_handle,
            "project_id": project_id,
        }
        revision_value = binding.get("base_revision", binding.get("revision"))
        if revision_value is not None:
            normalized["base_revision"] = self._revision(revision_value, "base_revision")
        elif require_base_revision:
            raise ValueError("prepare binding requires base_revision or revision")
        if "revision" in binding:
            normalized["revision"] = self._revision(binding["revision"], "revision")
        initial_document_name = binding.get("initial_document_name")
        if initial_document_name is not None:
            normalized["initial_document_name"] = self._document_name(
                initial_document_name,
                "initial_document_name",
            )
        elif require_base_revision:
            raise ValueError("prepare binding requires initial_document_name")
        return normalized

    @staticmethod
    def _document_name(value: Any, field: str) -> str:
        name = str(value).strip()
        if (
            not DOCUMENT_NAME_RE.fullmatch(name)
            or any(token in name for token in ("/", "\\"))
        ):
            raise ValueError(
                f"{field} must be a bounded document name without path separators"
            )
        return name

    @staticmethod
    def _revision(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a non-negative integer")
        revision = int(value)
        if revision < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return revision

    @staticmethod
    def _session_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "pid": int(binding["pid"]),
            "window_handle": int(binding["window_handle"]),
            "project_id": str(binding["project_id"]),
        }

    def _paths(self, binding: Mapping[str, Any]) -> dict[str, Path]:
        root = (
            self.gui_loop_root
            / str(binding["project_id"])
            / f"pid_{int(binding['pid'])}_hwnd_{int(binding['window_handle'])}"
        ).resolve()
        self._require_inside(self.gui_loop_root, root)
        return {
            "root": root,
            "staging": root / "staging",
            "pending": root / "pending",
            "running": root / "running",
            "done": root / "done",
            "failed": root / "failed",
            "control": root / "control",
            "config": root / "loop_config.json",
            "state": root / "current_state.json",
            "heartbeat": root / "heartbeat.json",
            "stop": root / "control" / "stop.json",
            "stop_ack": root / "control" / "stop_ack.json",
            "lock": root / "loop.lock",
            "producer_lock": root / "producer.lock",
            "key": root / "loop.key",
            "script": root / "materials_studio_gui_loop.pl",
        }

    def _workspace_file(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.workspace_root / path
        resolved = path.resolve()
        self._require_inside(self.workspace_root, resolved)
        return resolved

    @staticmethod
    def _require_inside(root: Path, path: Path) -> None:
        if path != root and root not in path.parents:
            raise ValueError(f"Path escapes allowed root: {path}")

    def _ensure_key_file(self, path: Path) -> None:
        expected = self._secret_for_write().hex()
        if path.exists():
            observed = path.read_text(encoding="ascii").strip()
            if not hmac.compare_digest(observed, expected):
                raise GuiLoopError(
                    "Existing GUI-loop session uses a different HMAC key",
                    {"status": "key_conflict", "queue_root": str(path.parent)},
                )
            return
        atomic_write_text(path, expected + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _load_manager_secret(path: Path, *, create: bool) -> bytes:
        """Load the durable key, creating it only for a mutating operation."""

        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                observed = path.read_text(encoding="ascii").strip()
            except FileNotFoundError:
                if not create:
                    raise GuiLoopError(
                        "Persistent GUI-loop manager key is missing",
                        {"status": "manager_key_missing"},
                    )
                candidate = secrets.token_bytes(32)
                try:
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    continue
                try:
                    os.write(descriptor, candidate.hex().encode("ascii") + b"\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return candidate
            try:
                secret = bytes.fromhex(observed)
            except ValueError as exc:
                raise GuiLoopError(
                    "Persistent GUI-loop manager key is malformed",
                    {"status": "manager_key_invalid"},
                ) from exc
            if len(secret) < 32:
                raise GuiLoopError(
                    "Persistent GUI-loop manager key is too short",
                    {"status": "manager_key_invalid"},
                )
            return secret

    def _secret_for_write(self) -> bytes:
        if self._secret is None:
            self._secret = self._load_manager_secret(
                self._manager_key_path,
                create=True,
            )
        return self._secret

    def _secret_for_read(self) -> bytes:
        if self._secret is None:
            self._secret = self._load_manager_secret(
                self._manager_key_path,
                create=False,
            )
        return self._secret

    def _write_signed_envelope(self, path: Path, payload: Mapping[str, Any]) -> None:
        payload_json = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        signature = hmac.new(
            self._secret_for_write(), payload_json.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        envelope = {
            "envelope_version": ENVELOPE_VERSION,
            "hmac_sha256": signature,
            "payload_json": payload_json,
            "protocol": PROTOCOL,
        }
        atomic_write_text(
            path,
            json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n",
        )

    def _read_signed_envelope(self, path: Path, expected_kind: str) -> dict[str, Any]:
        if path.stat().st_size > MAX_ENVELOPE_BYTES:
            raise GuiLoopError("Signed envelope exceeds the maximum size")
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload_json = envelope["payload_json"]
            signature = envelope["hmac_sha256"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise GuiLoopError(f"Invalid signed envelope: {path}") from exc
        if envelope.get("protocol") != PROTOCOL or envelope.get("envelope_version") != ENVELOPE_VERSION:
            raise GuiLoopError("Signed envelope protocol/version mismatch")
        if not isinstance(payload_json, str) or not isinstance(signature, str):
            raise GuiLoopError("Signed envelope payload/signature has the wrong type")
        expected = hmac.new(
            self._secret_for_read(), payload_json.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature.lower(), expected):
            raise GuiLoopError("Signed envelope HMAC verification failed")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise GuiLoopError("Signed envelope payload is not valid JSON") from exc
        if payload.get("protocol") != PROTOCOL or payload.get("kind") != expected_kind:
            raise GuiLoopError("Signed payload kind/protocol mismatch")
        return payload

    def _require_payload_binding(
        self, payload: Mapping[str, Any], binding: Mapping[str, Any]
    ) -> None:
        if payload.get("binding") != self._session_binding(binding):
            raise GuiLoopError("Signed payload does not match the requested GUI binding")

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _bounded_error(exc: Exception) -> str:
        return (str(exc).strip() or exc.__class__.__name__)[:1000]

    @staticmethod
    def _job_ids(path: Path) -> list[str]:
        if not path.is_dir():
            return []
        return sorted(
            item.stem
            for item in path.glob("*.json")
            if JOB_ID_RE.fullmatch(item.stem)
        )

    def _job_status(self, paths: Mapping[str, Path], job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValueError("job_id must be a 32-character lowercase hexadecimal UUID")
        for state in ("failed", "done", "running", "pending", "staging"):
            job_path = paths[state] / f"{job_id}.json"
            result_path = paths[state] / f"{job_id}.result.json"
            if job_path.exists() or result_path.exists():
                receipt: dict[str, Any] = {"job_id": job_id, "status": state}
                if result_path.exists():
                    try:
                        result = self._read_signed_envelope(result_path, "job_result")
                        self._require_payload_binding(result, self._binding_from_paths(paths))
                        receipt["result"] = result
                        receipt["result_signature_valid"] = True
                    except GuiLoopError as exc:
                        receipt["result_signature_valid"] = False
                        receipt["result_error"] = self._bounded_error(exc)
                return receipt
        return {"job_id": job_id, "status": "unknown"}

    @staticmethod
    def _binding_from_paths(paths: Mapping[str, Path]) -> dict[str, Any]:
        # The caller only uses this helper after _paths() built trusted names.
        session = paths["root"].name
        match = re.fullmatch(r"pid_(\d+)_hwnd_(\d+)", session)
        if match is None:
            raise GuiLoopError("Invalid internal GUI-loop session path")
        return {
            "pid": int(match.group(1)),
            "window_handle": int(match.group(2)),
            "project_id": paths["root"].parent.name,
        }

    @staticmethod
    def _perl_string(value: Path | str) -> str:
        text = str(value).replace("\\", "/")
        text = text.replace("\\", "\\\\").replace('"', '\\"')
        text = text.replace("$", "\\$").replace("@", "\\@")
        return f'"{text}"'

    def _render_loop_script(
        self, binding: Mapping[str, Any], paths: Mapping[str, Path]
    ) -> str:
        values = {
            "queue_root": self._perl_string(paths["root"]),
            "workspace_root": self._perl_string(self.workspace_root),
            "key_path": self._perl_string(paths["key"]),
            "expected_pid": int(binding["pid"]),
            "expected_hwnd": int(binding["window_handle"]),
            "project_id": self._perl_string(str(binding["project_id"])),
            "protocol": self._perl_string(PROTOCOL),
        }
        return _PERL_LOOP_TEMPLATE.format(**values)


_PERL_LOOP_TEMPLATE = r'''#!perl
use strict;
use warnings;
use MaterialsScript qw(:all);
use Digest::SHA qw(hmac_sha256_hex sha256_hex);
use Fcntl qw(O_CREAT O_EXCL O_WRONLY);
use JSON::PP;
use Time::HiRes qw(time sleep);

# Generated fixed-operation MS-MCP GUI loop.  This program never evaluates or
# executes code supplied by a queue job.  The sole operation is
# Documents->Import on a signed, workspace-confined structure artifact.
my $PROTOCOL = {protocol};
my $QUEUE_ROOT = {queue_root};
my $WORKSPACE_ROOT = {workspace_root};
my $KEY_PATH = {key_path};
my $EXPECTED_PID = {expected_pid};
my $EXPECTED_HWND = {expected_hwnd};
my $PROJECT_ID = {project_id};
my $LOOP_ID = sprintf("%d-%d-%d", $$, time() * 1000, int(rand(1000000)));
my $POLL_SECONDS = 0.20;
my $HEARTBEAT_SECONDS = 2.0;

open(my $key_fh, "<", $KEY_PATH) or die "Cannot read GUI-loop key";
local $/;
my $KEY_HEX = <$key_fh>;
close($key_fh);
$KEY_HEX =~ s/\s+//g;
die "Invalid GUI-loop key" unless $KEY_HEX =~ /^[0-9a-f]{{64,}}$/i;
my $SECRET = pack("H*", $KEY_HEX);

my $STAGING = "$QUEUE_ROOT/staging";
my $PENDING = "$QUEUE_ROOT/pending";
my $RUNNING = "$QUEUE_ROOT/running";
my $DONE = "$QUEUE_ROOT/done";
my $FAILED = "$QUEUE_ROOT/failed";
my $CONTROL = "$QUEUE_ROOT/control";
my $STATE_PATH = "$QUEUE_ROOT/current_state.json";
my $HEARTBEAT_PATH = "$QUEUE_ROOT/heartbeat.json";
my $STOP_PATH = "$CONTROL/stop.json";
my $STOP_ACK_PATH = "$CONTROL/stop_ack.json";
my $LOCK_PATH = "$QUEUE_ROOT/loop.lock";

sysopen(my $lock_fh, $LOCK_PATH, O_WRONLY | O_CREAT | O_EXCL)
    or die "Another GUI loop owns this exact PID/HWND/project queue";
syswrite($lock_fh, "$LOOP_ID\n") or die "Cannot initialize GUI-loop owner lock";
my $owns_lock = 1;

sub session_binding {{
    return {{
        pid => 0 + $EXPECTED_PID,
        window_handle => 0 + $EXPECTED_HWND,
        project_id => $PROJECT_ID,
    }};
}}

sub slurp_bounded {{
    my ($path) = @_;
    open(my $fh, "<", $path) or die "Cannot read $path";
    binmode($fh);
    my $size = -s $fh;
    die "Envelope too large" if defined($size) && $size > 1048576;
    local $/;
    my $text = <$fh>;
    close($fh);
    return $text;
}}

sub read_signed {{
    my ($path, $kind) = @_;
    my $outer = JSON::PP->new->decode(slurp_bounded($path));
    die "Envelope protocol mismatch" unless ($outer->{{protocol}} || "") eq $PROTOCOL;
    die "Envelope version mismatch" unless ($outer->{{envelope_version}} || 0) == 1;
    my $payload_json = $outer->{{payload_json}};
    my $observed = lc($outer->{{hmac_sha256}} || "");
    my $expected = hmac_sha256_hex($payload_json, $SECRET);
    die "Envelope HMAC mismatch" unless $observed eq $expected;
    my $payload = JSON::PP->new->decode($payload_json);
    die "Payload protocol mismatch" unless ($payload->{{protocol}} || "") eq $PROTOCOL;
    die "Payload kind mismatch" unless ($payload->{{kind}} || "") eq $kind;
    return $payload;
}}

sub require_binding {{
    my ($payload) = @_;
    my $binding = $payload->{{binding}} || {{}};
    die "Job PID binding mismatch" unless ($binding->{{pid}} || 0) == $EXPECTED_PID;
    die "Job HWND binding mismatch" unless ($binding->{{window_handle}} || 0) == $EXPECTED_HWND;
    die "Job project binding mismatch" unless ($binding->{{project_id}} || "") eq $PROJECT_ID;
}}

sub write_signed_atomic {{
    my ($path, $payload) = @_;
    my $json = JSON::PP->new->canonical(1)->ascii(1);
    my $payload_json = $json->encode($payload);
    my $outer = {{
        envelope_version => 1,
        hmac_sha256 => hmac_sha256_hex($payload_json, $SECRET),
        payload_json => $payload_json,
        protocol => $PROTOCOL,
    }};
    my $temporary = "$path.$LOOP_ID.tmp";
    open(my $fh, ">", $temporary) or die "Cannot write $temporary";
    binmode($fh);
    print $fh $json->encode($outer), "\n";
    close($fh) or die "Cannot close $temporary";
    rename($temporary, $path) or die "Cannot publish $path";
}}

sub workspace_path_ok {{
    my ($path) = @_;
    my $candidate = lc($path || "");
    my $root = lc($WORKSPACE_ROOT);
    $candidate =~ s!\\!/!g;
    $root =~ s!\\!/!g;
    $root =~ s!/$!!;
    return 0 if $candidate =~ m!(?:^|/)\.\.(?:/|$)!;
    return index($candidate, "$root/") == 0;
}}

sub file_sha256 {{
    my ($path) = @_;
    open(my $fh, "<", $path) or die "Cannot read structure artifact";
    binmode($fh);
    my $sha = Digest::SHA->new(256);
    $sha->addfile($fh);
    close($fh);
    return $sha->hexdigest;
}}

sub current_state {{
    my $state = read_signed($STATE_PATH, "current_state");
    require_binding($state);
    return $state;
}}

sub active_document_name {{
    my $active = Documents->ActiveDocument;
    die "GUI loop requires an active Materials Studio project document"
        unless $active;
    my $name = "";
    eval {{ $name = $active->Name; }};
    die "GUI loop cannot observe the active Materials Studio document name"
        unless length($name);
    return $name;
}}

sub require_active_document_binding {{
    my ($state) = @_;
    my $expected = $state->{{current_document_name}} || "";
    die "Signed GUI-loop state has no current document binding"
        unless length($expected);
    my $observed = active_document_name();
    die "Active Materials Studio document binding mismatch"
        unless $observed eq $expected;
    return $observed;
}}

sub heartbeat {{
    my ($status) = @_;
    my $state = current_state();
    my $active_name = require_active_document_binding($state);
    write_signed_atomic($HEARTBEAT_PATH, {{
        kind => "heartbeat",
        protocol => $PROTOCOL,
        binding => session_binding(),
        loop_id => $LOOP_ID,
        status => $status,
        current_revision => 0 + $state->{{current_revision}},
        active_document_name => $active_name,
        heartbeat_at_epoch => 0 + time(),
    }});
}}

sub terminal_result {{
    my ($directory, $job_id, $status, $detail, $state) = @_;
    write_signed_atomic("$directory/$job_id.result.json", {{
        kind => "job_result",
        protocol => $PROTOCOL,
        binding => session_binding(),
        job_id => $job_id,
        status => $status,
        detail => $detail,
        current_revision => 0 + $state->{{current_revision}},
        current_document_name => $state->{{current_document_name}},
        structure_sha256 => lc($state->{{structure_sha256}} || ""),
        completed_at_epoch => 0 + time(),
    }});
}}

sub process_job {{
    my ($name) = @_;
    my $job_id = $name;
    $job_id =~ s/\.json$//;
    die "Invalid job file name" unless $job_id =~ /^[0-9a-f]{{32}}$/;
    my $pending_path = "$PENDING/$name";
    my $running_path = "$RUNNING/$name";
    return unless rename($pending_path, $running_path);
    my $state;
    my $success = eval {{
        my $job = read_signed($running_path, "job");
        require_binding($job);
        die "Only import_structure is permitted"
            unless ($job->{{operation}} || "") eq "import_structure";
        die "Job id mismatch" unless ($job->{{job_id}} || "") eq $job_id;
        $state = current_state();
        require_active_document_binding($state);
        die "Duplicate job replay" if ($state->{{last_job_id}} || "") eq $job_id;
        die "Job expired before GUI import"
            if time() > (0 + ($job->{{expires_at_epoch}} || 0));
        my $expected_revision = 0 + $job->{{expected_revision}};
        my $target_revision = 0 + $job->{{target_revision}};
        die "Revision compare-and-swap failed"
            unless (0 + $state->{{current_revision}}) == $expected_revision;
        die "Invalid target revision" if $target_revision < 0;
        my $structure_path = $job->{{structure_path}} || "";
        die "Structure path is outside the bound workspace"
            unless workspace_path_ok($structure_path);
        die "Structure artifact is missing" unless -f $structure_path;
        die "Structure extension is not allowed"
            unless $structure_path =~ /\.(?:arc|car|cif|mol|pdb|xsd|xtd)$/i;
        die "Structure SHA-256 mismatch"
            unless file_sha256($structure_path) eq lc($job->{{structure_sha256}} || "");

        my $previous_name = $state->{{current_document_name}} || "";
        my $doc = Documents->Import($structure_path);
        die "Materials Studio did not return an imported document" unless $doc;
        my $imported_name = "";
        eval {{ $imported_name = $doc->Name; }};
        die "Imported document has no observable name" unless length($imported_name);
        my $active_name = "";
        eval {{
            my $active = Documents->ActiveDocument;
            $active_name = $active->Name if $active;
        }};
        if ($active_name ne $imported_name) {{
            eval {{ Documents->ActiveDocument = $doc; }};
            $active_name = "";
            eval {{
                my $active = Documents->ActiveDocument;
                $active_name = $active->Name if $active;
            }};
        }}
        die "Imported document did not become the active GUI document"
            unless $active_name eq $imported_name;

        # Never delete or close a previous document automatically. It may have
        # unsaved user changes; cleanup requires a separate reviewed action.

        $state = {{
            kind => "current_state",
            protocol => $PROTOCOL,
            binding => session_binding(),
            current_revision => $target_revision,
            current_document_name => $imported_name,
            last_job_id => $job_id,
            structure_sha256 => lc($job->{{structure_sha256}}),
            updated_at_epoch => 0 + time(),
        }};
        write_signed_atomic($STATE_PATH, $state);
        1;
    }};
    if ($success) {{
        # Publish the committed revision before the producer can observe the
        # terminal receipt. This keeps immediate rN -> rN+1 jobs on the loop.
        heartbeat("running");
        my $done_path = "$DONE/$name";
        rename($running_path, $done_path) or die "Cannot publish completed job";
        terminal_result($DONE, $job_id, "done", "import_structure completed", $state);
    }} else {{
        my $error = $@ || "Unknown fixed import failure";
        $error =~ s/[\r\n]+/ /g;
        $error = substr($error, 0, 1000);
        my $failed_path = "$FAILED/$name";
        rename($running_path, $failed_path) or die "Cannot publish failed job";
        my $safe_state = eval {{ current_state() }} || {{
            current_revision => -1,
            current_document_name => undef,
        }};
        terminal_result($FAILED, $job_id, "failed", $error, $safe_state);
    }}
}}

my $last_heartbeat = 0;
eval {{
    while (1) {{
        if (-f $STOP_PATH) {{
            my $stop = read_signed($STOP_PATH, "stop");
            require_binding($stop);
            heartbeat("stopping");
            write_signed_atomic($STOP_ACK_PATH, {{
                kind => "stop_ack",
                protocol => $PROTOCOL,
                binding => session_binding(),
                loop_id => $LOOP_ID,
                request_id => $stop->{{request_id}},
                stopped_at_epoch => 0 + time(),
            }});
            last;
        }}
        my $now = time();
        if ($now - $last_heartbeat >= $HEARTBEAT_SECONDS) {{
            heartbeat("running");
            $last_heartbeat = $now;
        }}
        opendir(my $dh, $PENDING) or die "Cannot open pending queue";
        my @jobs = sort grep {{ /^[0-9a-f]{{32}}\.json$/ && -f "$PENDING/$_" }} readdir($dh);
        closedir($dh);
        foreach my $job (@jobs) {{ process_job($job); }}
        sleep($POLL_SECONDS);
    }}
}};
my $loop_error = $@;
if ($owns_lock) {{
    close($lock_fh);
    unlink($LOCK_PATH);
    $owns_lock = 0;
}}
die $loop_error if $loop_error;
'''


__all__ = ["GuiLoopError", "GuiLoopManager"]
