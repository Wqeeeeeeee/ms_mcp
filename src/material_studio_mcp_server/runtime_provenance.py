"""Runtime source provenance for long-lived MCP server processes."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .managed_runtime import (
    RUNTIME_MANIFEST_ENV,
    managed_runtime_status,
)


RUNTIME_PROVENANCE_SCHEMA = "material_studio_mcp_runtime_provenance_v1"
RUNTIME_DEPLOYMENT_SCHEMA = "material_studio_mcp_runtime_deployment_binding_v1"


def _portable_path_text(path: str | Path) -> str:
    """Remove Windows I/O-only prefixes from paths used in identity receipts."""

    text = str(path)
    if os.name != "nt":
        return text
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def _same_path(first: str | Path, second: str | Path) -> bool:
    first_text = os.path.normcase(os.path.normpath(_portable_path_text(first)))
    second_text = os.path.normcase(os.path.normpath(_portable_path_text(second)))
    return first_text == second_text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _package_version() -> str | None:
    try:
        return version("materials-studio-mcp")
    except PackageNotFoundError:
        return None


def _find_repository_root(package_root: Path) -> Path | None:
    """Find the source checkout that owns a package, without scanning outside it."""

    root = package_root.resolve()
    for candidate in (root, *root.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "run_server.py"
        ).is_file():
            return candidate
    return None


def _git_metadata(repository_root: Path | None) -> dict[str, Any]:
    """Return bounded local Git identity; failure never affects MCP startup."""

    if repository_root is None:
        return {
            "status": "repository_root_unavailable",
            "head_commit": None,
            "branch": None,
            "worktree_dirty": None,
            "dirty_scope": "tracked_files_only",
            "untracked_files_included": False,
            "error": None,
        }

    command = [
        "git",
        "-C",
        str(repository_root),
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=no",
        "--no-ahead-behind",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "git_probe_failed",
            "head_commit": None,
            "branch": None,
            "worktree_dirty": None,
            "dirty_scope": "tracked_files_only",
            "untracked_files_included": False,
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "git status failed").strip()
        return {
            "status": "git_probe_failed",
            "head_commit": None,
            "branch": None,
            "worktree_dirty": None,
            "dirty_scope": "tracked_files_only",
            "untracked_files_included": False,
            "error": message[:500],
        }

    head_commit: str | None = None
    branch: str | None = None
    worktree_dirty = False
    for line in completed.stdout.splitlines():
        if line.startswith("# branch.oid "):
            candidate = line.removeprefix("# branch.oid ").strip()
            head_commit = candidate if candidate not in {"(initial)"} else None
        elif line.startswith("# branch.head "):
            candidate = line.removeprefix("# branch.head ").strip()
            branch = candidate if candidate not in {"(detached)"} else None
        elif line and not line.startswith("# "):
            worktree_dirty = True

    return {
        "status": "available",
        "head_commit": head_commit,
        "branch": branch,
        "worktree_dirty": worktree_dirty,
        "dirty_scope": "tracked_files_only",
        "untracked_files_included": False,
        "error": None,
    }


def runtime_deployment_status(
    package_root: str | Path | None = None,
    *,
    entrypoint: str | Path | None = None,
    process_cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Describe which local checkout and entrypoint this process is using.

    This is diagnostic-only. It does not read or modify Codex configuration and
    it never launches Materials Studio.
    """

    package = Path(package_root or Path(__file__).resolve().parent).resolve()
    repository_root = _find_repository_root(package)
    expected_manifest_sha256 = os.environ.get(RUNTIME_MANIFEST_ENV)
    managed_runtime = (
        managed_runtime_status(
            repository_root,
            expected_manifest_sha256=expected_manifest_sha256,
            verify_python_runtime=expected_manifest_sha256 is not None,
        )
        if repository_root is not None
        else None
    )
    expected_entrypoint = (
        (repository_root / "run_server.py") if repository_root else None
    )
    observed_entrypoint: Path | None = None
    default_entrypoint = sys.argv[0] if sys.argv else ""
    raw_entrypoint = str(
        entrypoint if entrypoint is not None else default_entrypoint
    )
    if raw_entrypoint and not raw_entrypoint.startswith("-"):
        try:
            observed_entrypoint = Path(raw_entrypoint).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            observed_entrypoint = None

    observed_cwd = Path(process_cwd or os.getcwd()).expanduser().resolve()
    package_is_in_checkout = bool(
        repository_root
        and _same_path(
            package,
            repository_root / "src" / "material_studio_mcp_server",
        )
    )
    if managed_runtime and (
        managed_runtime.get("managed") is True
        or expected_manifest_sha256 is not None
    ):
        deployment_status = (
            "managed_immutable_runtime"
            if managed_runtime.get("integrity_verified") is True
            else "managed_runtime_integrity_failed"
        )
    elif repository_root is None:
        deployment_status = "installed_or_unmanaged_package"
    elif package_is_in_checkout:
        deployment_status = "source_checkout"
    else:
        deployment_status = "nonstandard_source_layout"

    if observed_entrypoint is None:
        entrypoint_binding = "unobserved"
    elif expected_entrypoint and _same_path(observed_entrypoint, expected_entrypoint):
        entrypoint_binding = "matched_source_run_server"
    elif repository_root is None:
        entrypoint_binding = "installed_or_external_entrypoint"
    else:
        entrypoint_binding = "different_entrypoint"

    try:
        cwd_matches_repository = bool(
            repository_root
            and _same_path(observed_cwd, repository_root)
        )
    except (OSError, RuntimeError):
        cwd_matches_repository = False

    if managed_runtime and managed_runtime.get("managed") is True:
        git = {
            "status": "managed_runtime_manifest",
            "head_commit": managed_runtime.get("source_commit"),
            "branch": managed_runtime.get("source_branch"),
            "remote": managed_runtime.get("source_remote"),
            "worktree_dirty": False,
            "dirty_scope": "immutable_runtime_manifest",
            "untracked_files_included": False,
            "error": None,
        }
    else:
        git = _git_metadata(repository_root)

    return {
        "schema": RUNTIME_DEPLOYMENT_SCHEMA,
        "status": deployment_status,
        "package_root": _portable_path_text(package),
        "repository_root": (
            _portable_path_text(repository_root) if repository_root else None
        ),
        "source_layout": "checkout_src_package" if package_is_in_checkout else None,
        "entrypoint": (
            _portable_path_text(observed_entrypoint) if observed_entrypoint else None
        ),
        "expected_source_entrypoint": (
            _portable_path_text(expected_entrypoint) if expected_entrypoint else None
        ),
        "entrypoint_binding": entrypoint_binding,
        "process_cwd": _portable_path_text(observed_cwd),
        "cwd_matches_repository": cwd_matches_repository,
        "python_executable": str(Path(sys.executable).resolve()),
        "git": git,
        "managed_runtime": managed_runtime,
        "diagnostic_only": True,
        "materials_studio_process_started": False,
    }


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
    deployment_binding: dict[str, Any]
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
        deployment_binding = runtime_deployment_status(root)
        git = deployment_binding.get("git")
        git_head = git.get("head_commit") if isinstance(git, dict) else None
        managed_runtime = deployment_binding.get("managed_runtime")
        managed_manifest_hash = (
            managed_runtime.get("manifest_sha256")
            if isinstance(managed_runtime, dict)
            else None
        )
        instance_material = "\0".join(
            (
                str(pid),
                captured_at.isoformat(),
                str(snapshot.get("sha256") or "unavailable"),
                str(root),
                str(git_head or "unavailable"),
                str(managed_manifest_hash or "unmanaged"),
            )
        ).encode("utf-8")
        instance_id = hashlib.sha256(instance_material).hexdigest()[:20]
        return cls(
            package_root=root,
            loaded_at=captured_at,
            process_id=pid,
            initial_snapshot=snapshot,
            deployment_binding=deployment_binding,
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
        initial_managed = self.deployment_binding.get("managed_runtime")
        managed_required = bool(
            os.environ.get(RUNTIME_MANIFEST_ENV)
            or (
                isinstance(initial_managed, dict)
                and initial_managed.get("managed") is True
            )
        )
        repository_root = self.deployment_binding.get("repository_root")
        current_managed = (
            managed_runtime_status(
                repository_root,
                expected_manifest_sha256=os.environ.get(RUNTIME_MANIFEST_ENV),
                verify_python_runtime=True,
            )
            if managed_required and repository_root
            else None
        )
        managed_current = bool(
            not managed_required
            or (
                isinstance(current_managed, dict)
                and current_managed.get("integrity_verified") is True
            )
        )
        source_current = bool(
            initial_complete
            and current_complete
            and self.initial_snapshot.get("sha256") == current.get("sha256")
            and managed_current
        )
        if source_current:
            status = "current"
        elif managed_required and not managed_current:
            status = "managed_runtime_integrity_failed"
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
            "deployment_binding": dict(self.deployment_binding),
            "source_snapshot_at_start": dict(self.initial_snapshot),
            "source_snapshot_current": dict(current),
            "managed_runtime": current_managed,
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
    "RUNTIME_DEPLOYMENT_SCHEMA",
    "RUNTIME_PROVENANCE_SCHEMA",
    "RuntimeProvenanceTracker",
    "runtime_deployment_status",
    "runtime_provenance_status",
    "source_tree_snapshot",
]
