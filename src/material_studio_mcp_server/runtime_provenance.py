"""Runtime source provenance for long-lived MCP server processes."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


RUNTIME_PROVENANCE_SCHEMA = "material_studio_mcp_runtime_provenance_v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _package_version() -> str | None:
    try:
        return version("materials-studio-mcp")
    except PackageNotFoundError:
        return None


def source_tree_snapshot(package_root: str | Path) -> dict[str, Any]:
    """Hash Python sources under a package root with deterministic framing."""

    root = Path(package_root).resolve()
    if not root.is_dir():
        return {
            "status": "unavailable",
            "sha256": None,
            "file_count": 0,
            "total_bytes": 0,
            "unreadable_files": [],
            "error": f"Package source root does not exist: {root}",
        }
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    unreadable_files: list[str] = []
    try:
        source_files = sorted(
            (
                path
                for path in root.rglob("*.py")
                if "__pycache__" not in path.parts and path.is_file()
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    except OSError as exc:
        return {
            "status": "unavailable",
            "sha256": None,
            "file_count": 0,
            "total_bytes": 0,
            "unreadable_files": [],
            "error": str(exc),
        }

    for path in source_files:
        relative_path = path.relative_to(root).as_posix()
        try:
            content = path.read_bytes()
        except OSError:
            unreadable_files.append(relative_path)
            continue
        relative_bytes = relative_path.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        file_count += 1
        total_bytes += len(content)

    status = "complete" if not unreadable_files else "incomplete"
    return {
        "status": status,
        "sha256": digest.hexdigest() if file_count or not unreadable_files else None,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "unreadable_files": unreadable_files,
    }


@dataclass(frozen=True)
class RuntimeProvenanceTracker:
    """Capture the source loaded by one MCP process and compare it with disk."""

    package_root: Path
    loaded_at: datetime
    process_id: int
    initial_snapshot: dict[str, Any]
    instance_id: str

    @classmethod
    def capture(
        cls,
        package_root: str | Path,
        *,
        loaded_at: datetime | None = None,
        process_id: int | None = None,
    ) -> "RuntimeProvenanceTracker":
        root = Path(package_root).resolve()
        captured_at = loaded_at or _utc_now()
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        pid = os.getpid() if process_id is None else process_id
        snapshot = source_tree_snapshot(root)
        instance_material = "\0".join(
            (
                str(pid),
                captured_at.isoformat(),
                str(snapshot.get("sha256") or "unavailable"),
                str(root),
            )
        ).encode("utf-8")
        instance_id = hashlib.sha256(instance_material).hexdigest()[:20]
        return cls(
            package_root=root,
            loaded_at=captured_at,
            process_id=pid,
            initial_snapshot=snapshot,
            instance_id=instance_id,
        )

    def status(
        self,
        *,
        current_snapshot: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        current = current_snapshot or source_tree_snapshot(self.package_root)
        observed = observed_at or _utc_now()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        initial_complete = self.initial_snapshot.get("status") == "complete"
        current_complete = current.get("status") == "complete"
        source_current = bool(
            initial_complete
            and current_complete
            and self.initial_snapshot.get("sha256") == current.get("sha256")
        )
        if source_current:
            status = "current"
        elif not initial_complete or not current_complete:
            status = "source_snapshot_unavailable"
        else:
            status = "source_changed_since_start"
        return {
            "schema": RUNTIME_PROVENANCE_SCHEMA,
            "status": status,
            "source_current": source_current,
            "source_changed_since_start": status == "source_changed_since_start",
            "restart_required": not source_current,
            "runtime_instance_id": self.instance_id,
            "process_id": self.process_id,
            "loaded_at_utc": self.loaded_at.astimezone(timezone.utc).isoformat(),
            "observed_at_utc": observed.astimezone(timezone.utc).isoformat(),
            "package_name": "materials-studio-mcp",
            "package_version": _package_version(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "package_root": str(self.package_root),
            "source_snapshot_at_start": dict(self.initial_snapshot),
            "source_snapshot_current": dict(current),
            "restart_action": (
                "restart_mcp_server_then_retry_preflight"
                if not source_current
                else None
            ),
        }


_TRACKER = RuntimeProvenanceTracker.capture(Path(__file__).resolve().parent)


def runtime_provenance_status() -> dict[str, Any]:
    """Return the current process/source binding receipt."""

    return _TRACKER.status()


__all__ = [
    "RUNTIME_PROVENANCE_SCHEMA",
    "RuntimeProvenanceTracker",
    "runtime_provenance_status",
    "source_tree_snapshot",
]
