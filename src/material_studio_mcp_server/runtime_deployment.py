"""Preview-first deployment of reviewed commits to immutable local runtimes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from .codex_config import default_active_config_path, resolve_python_command
from .codex_registration import plan_codex_registration
from .managed_runtime import (
    MANAGED_RUNTIME_MANIFEST,
    MANAGED_RUNTIME_SCHEMA,
    default_managed_runtime_root,
    managed_runtime_server_args,
    managed_runtime_status,
    manifest_bytes,
    runtime_content_snapshot,
    sha256_bytes,
)
from .protocol_smoke import run_protocol_acceptance


RUNTIME_DEPLOYMENT_PLAN_SCHEMA = "material_studio_mcp_runtime_deployment_plan_v1"
_REQUIRED_ARCHIVE_PATHS = {
    "pyproject.toml",
    "register_codex.py",
    "run_server.py",
    "src/material_studio_mcp_server/server.py",
}


@dataclass(frozen=True)
class _PreparedDeployment:
    receipt: dict[str, Any]
    archive_bytes: bytes | None
    manifest_content: bytes | None


class _DeploymentError(RuntimeError):
    pass


def plan_runtime_deployment(
    *,
    source_root: str | Path | None = None,
    runtime_root: str | Path | None = None,
    python_command: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a read-only deployment plan for the current pushed commit."""

    return _prepare_runtime_deployment(
        source_root=source_root,
        runtime_root=runtime_root,
        python_command=python_command,
        config_path=config_path,
    ).receipt


def apply_runtime_deployment(
    *,
    expected_plan_id: str | None,
    source_root: str | Path | None = None,
    runtime_root: str | Path | None = None,
    python_command: str | Path | None = None,
    config_path: str | Path | None = None,
    validate_protocol: bool = True,
) -> dict[str, Any]:
    """Publish one reviewed runtime plan and return a config registration handoff."""

    prepared = _prepare_runtime_deployment(
        source_root=source_root,
        runtime_root=runtime_root,
        python_command=python_command,
        config_path=config_path,
    )
    plan = prepared.receipt
    target = Path(plan["target_runtime_path"])
    base = {
        "schema": RUNTIME_DEPLOYMENT_PLAN_SCHEMA,
        "operation": "apply_runtime_deployment",
        "runtime_deployment_plan_id": plan.get("runtime_deployment_plan_id"),
        "expected_plan_id": expected_plan_id,
        "source_root": plan.get("source_root"),
        "source_commit": plan.get("source_commit"),
        "target_runtime_path": str(target),
        "runtime_written": False,
        "active_config_modified": False,
        "codex_restart_performed": False,
        "materials_studio_process_touched": False,
    }
    if not expected_plan_id:
        return {
            **base,
            "ok": False,
            "status": "explicit_plan_confirmation_required",
            "apply_ready": bool(plan.get("apply_ready")),
        }
    if expected_plan_id != plan.get("runtime_deployment_plan_id"):
        return {
            **base,
            "ok": False,
            "status": "runtime_deployment_plan_mismatch",
            "apply_ready": False,
            "next_actions": ["Generate and review a fresh runtime deployment plan."],
        }
    if not plan.get("apply_ready"):
        return {
            **base,
            "ok": False,
            "status": "runtime_deployment_not_applicable",
            "blocking_reasons": plan.get("blocking_reasons") or [],
        }

    deployed_now = False
    staging: Path | None = None
    try:
        if plan.get("deployment_required"):
            if prepared.archive_bytes is None or prepared.manifest_content is None:
                raise _DeploymentError("prepared deployment content is unavailable")
            parent = target.parent
            parent.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{target.name}.staging-",
                    dir=parent,
                )
            ).resolve()
            _extract_archive_safely(prepared.archive_bytes, staging)
            extracted = runtime_content_snapshot(staging)
            if extracted != plan.get("content_snapshot"):
                raise _DeploymentError("staging content does not match the reviewed plan")
            manifest_path = staging / MANAGED_RUNTIME_MANIFEST
            _write_new_file(manifest_path, prepared.manifest_content)
            if sha256_bytes(manifest_path.read_bytes()) != plan.get("manifest_sha256"):
                raise _DeploymentError("staging manifest hash verification failed")
            if target.exists():
                raise _DeploymentError("runtime destination appeared during publication")
            staging.rename(target)
            staging = None
            deployed_now = True
        integrity = managed_runtime_status(
            target,
            expected_manifest_sha256=plan.get("manifest_sha256"),
        )
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "status": "runtime_deployment_failed",
            "error": _bounded_error(exc),
            "runtime_written": deployed_now,
            "runtime_integrity": (
                managed_runtime_status(target)
                if target.exists()
                else None
            ),
        }
    finally:
        if staging is not None:
            _remove_staging_directory(staging, target.parent)

    if integrity.get("integrity_verified") is not True:
        return {
            **base,
            "ok": False,
            "status": "runtime_integrity_postcheck_failed",
            "runtime_written": deployed_now,
            "runtime_integrity": integrity,
        }

    if validate_protocol:
        protocol = _validate_deployed_runtime(
            target,
            python_command=plan["python_command"],
        )
    else:
        protocol = {
            "ok": True,
            "status": "skipped_by_internal_caller",
            "list_only": True,
        }
    integrity_after_protocol = managed_runtime_status(
        target,
        expected_manifest_sha256=plan.get("manifest_sha256"),
    )
    runtime_ready = bool(
        protocol.get("ok")
        and integrity_after_protocol.get("integrity_verified") is True
    )
    registration_plan: dict[str, Any] | None = None
    registration_handoff: dict[str, Any] | None = None
    if runtime_ready:
        registration_plan = plan_codex_registration(
            config_path=plan["config_path"],
            repo_root=target,
            python_command=plan["python_command"],
            include_snippet=False,
        )
        registration_handoff = _registration_handoff(
            target=target,
            python_command=Path(plan["python_command"]),
            config_path=Path(plan["config_path"]),
            manifest_sha256=str(plan["manifest_sha256"]),
            registration_plan=registration_plan,
        )

    ok = bool(
        runtime_ready
        and registration_plan is not None
        and registration_plan.get("status")
        in {"registration_ready", "already_registered"}
    )
    return {
        **base,
        "ok": ok,
        "status": (
            "runtime_deployed_registration_ready"
            if ok and deployed_now
            else (
                "runtime_reused_registration_ready"
                if ok
                else "runtime_deployed_registration_blocked"
            )
        ),
        "runtime_written": deployed_now,
        "runtime_reused": not deployed_now,
        "runtime_integrity": integrity_after_protocol,
        "protocol_validation": protocol,
        "registration_plan": registration_plan,
        "registration_handoff": registration_handoff,
        "restart_required_after_registration": True,
        "next_actions": (
            [
                "Review the returned registration plan.",
                "Apply only its exact registration_plan_id.",
                "Restart Codex, then call material_studio_live_session_preflight.",
            ]
            if ok
            else [
                "Do not update the active Codex configuration.",
                "Resolve the runtime protocol or registration blocker first.",
            ]
        ),
    }


def _prepare_runtime_deployment(
    *,
    source_root: str | Path | None,
    runtime_root: str | Path | None,
    python_command: str | Path | None,
    config_path: str | Path | None,
) -> _PreparedDeployment:
    source_probe = _inspect_source_checkout(source_root or Path.cwd())
    repository = Path(
        source_probe.get("repository_root") or source_root or Path.cwd()
    ).expanduser().resolve()
    runtimes = (
        Path(runtime_root).expanduser().resolve()
        if runtime_root is not None
        else default_managed_runtime_root()
    )
    command = resolve_python_command(repository, python_command)
    active_config = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else default_active_config_path()
    )
    commit = str(source_probe.get("head_commit") or "unresolved")
    target = (runtimes / commit).resolve()
    receipt: dict[str, Any] = {
        "schema": RUNTIME_DEPLOYMENT_PLAN_SCHEMA,
        "operation": "plan_runtime_deployment",
        "ok": True,
        "status": "runtime_deployment_blocked",
        "read_only": True,
        "source_root": str(repository),
        "source_commit": source_probe.get("head_commit"),
        "source_tree": source_probe.get("tree"),
        "source_branch": source_probe.get("branch"),
        "source_upstream": source_probe.get("upstream_ref"),
        "source_remote": source_probe.get("remote_url"),
        "source_commit_time": source_probe.get("commit_time"),
        "source_tracked_clean": source_probe.get("tracked_clean"),
        "source_pushed_to_upstream": source_probe.get("pushed_to_upstream"),
        "runtime_root": str(runtimes),
        "target_runtime_path": str(target),
        "python_command": str(command),
        "python_exists": command.is_file(),
        "config_path": str(active_config),
        "deployment_required": False,
        "apply_ready": False,
        "explicit_apply_required": True,
        "active_config_modified": False,
        "codex_restart_performed": False,
        "materials_studio_process_touched": False,
        "blocking_reasons": list(source_probe.get("blocking_reasons") or []),
    }
    if not command.is_file():
        receipt["blocking_reasons"].append("python_command_not_found")
    if _paths_overlap(repository, runtimes):
        receipt["blocking_reasons"].append("runtime_root_overlaps_source_checkout")
    if receipt["blocking_reasons"]:
        receipt.update({"ok": False, "status": "source_not_deployable"})
        return _PreparedDeployment(receipt, None, None)

    try:
        archive = _git_archive(repository, str(source_probe["head_commit"]))
        archive_snapshot = _archive_content_snapshot(archive)
    except Exception as exc:
        receipt.update(
            {
                "ok": False,
                "status": "runtime_archive_failed",
                "blocking_reasons": [_bounded_error(exc)],
            }
        )
        return _PreparedDeployment(receipt, None, None)
    missing = sorted(_REQUIRED_ARCHIVE_PATHS - set(archive_snapshot["paths"]))
    if missing:
        receipt.update(
            {
                "ok": False,
                "status": "runtime_archive_incomplete",
                "blocking_reasons": ["required_archive_paths_missing"],
                "missing_required_paths": missing,
            }
        )
        return _PreparedDeployment(receipt, None, None)

    content_snapshot = {
        "status": "complete",
        "sha256": archive_snapshot["sha256"],
        "file_count": archive_snapshot["file_count"],
        "total_bytes": archive_snapshot["total_bytes"],
        "unreadable_files": [],
        "unexpected_links": [],
        "error": None,
    }
    manifest = {
        "schema": MANAGED_RUNTIME_SCHEMA,
        "runtime_root": str(target),
        "source": {
            "commit": source_probe.get("head_commit"),
            "tree": source_probe.get("tree"),
            "commit_time": source_probe.get("commit_time"),
        },
        "archive_sha256": sha256_bytes(archive),
        "content_snapshot": content_snapshot,
        "entrypoints": {
            "mcp_server": "run_server.py",
            "codex_registration": "register_codex.py",
        },
        "immutability": {
            "path_is_commit_addressed": True,
            "existing_runtime_never_overwritten": True,
            "old_runtimes_never_deleted": True,
        },
    }
    encoded_manifest = manifest_bytes(manifest)
    manifest_hash = sha256_bytes(encoded_manifest)
    receipt.update(
        {
            "archive_sha256": manifest["archive_sha256"],
            "archive_bytes": len(archive),
            "content_snapshot": content_snapshot,
            "manifest_schema": MANAGED_RUNTIME_SCHEMA,
            "manifest_path": str(target / MANAGED_RUNTIME_MANIFEST),
            "manifest_sha256": manifest_hash,
            "server_args_after_deployment": [
                str(target / "run_server.py"),
                "--runtime-manifest-sha256",
                manifest_hash,
            ],
        }
    )

    if target.exists():
        existing = managed_runtime_status(
            target,
            expected_manifest_sha256=manifest_hash,
        )
        receipt["existing_runtime"] = existing
        if existing.get("integrity_verified") is not True:
            receipt.update(
                {
                    "ok": False,
                    "status": "runtime_destination_conflict",
                    "blocking_reasons": [
                        "existing_commit_runtime_failed_integrity_verification"
                    ],
                }
            )
            return _PreparedDeployment(receipt, None, None)
        status = "runtime_already_deployed"
        deployment_required = False
    else:
        status = "runtime_deployment_ready"
        deployment_required = True

    receipt.update(
        {
            "status": status,
            "deployment_required": deployment_required,
            "apply_ready": True,
            "blocking_reasons": [],
        }
    )
    receipt["runtime_deployment_plan_id"] = _deployment_plan_id(receipt)
    receipt["apply_contract"] = {
        "operation": "apply_runtime_deployment",
        "expected_plan_id": receipt["runtime_deployment_plan_id"],
        "source_root": str(repository),
        "runtime_root": str(runtimes),
        "python_command": str(command),
        "config_path": str(active_config),
    }
    receipt["next_actions"] = [
        "Review the exact source commit, upstream, runtime path, and manifest SHA.",
        "Apply only with this runtime_deployment_plan_id.",
        "Review the separate Codex registration plan returned after protocol validation.",
    ]
    return _PreparedDeployment(receipt, archive, encoded_manifest)


def _inspect_source_checkout(source: str | Path) -> dict[str, Any]:
    requested = Path(source).expanduser().resolve()
    result: dict[str, Any] = {
        "requested_path": str(requested),
        "repository_root": None,
        "head_commit": None,
        "tree": None,
        "branch": None,
        "commit_time": None,
        "upstream_ref": None,
        "upstream_commit": None,
        "remote_url": None,
        "tracked_clean": False,
        "pushed_to_upstream": False,
        "blocking_reasons": [],
    }
    try:
        root = Path(_git_text(requested, "rev-parse", "--show-toplevel")).resolve()
        head = _git_text(root, "rev-parse", "HEAD")
        tree = _git_text(root, "rev-parse", "HEAD^{tree}")
        branch = _git_text(root, "branch", "--show-current")
        commit_time = _git_text(root, "show", "-s", "--format=%cI", "HEAD")
        tracked_status = _git_text(
            root,
            "status",
            "--porcelain",
            "--untracked-files=no",
        )
        try:
            upstream_ref = _git_text(
                root,
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            )
            upstream_commit = _git_text(root, "rev-parse", "@{upstream}")
        except _DeploymentError:
            upstream_ref = None
            upstream_commit = None
        remote_name = upstream_ref.split("/", 1)[0] if upstream_ref else None
        remote_url = (
            _git_text(root, "remote", "get-url", remote_name)
            if remote_name
            else None
        )
    except Exception as exc:
        result["blocking_reasons"] = [f"git_source_probe_failed: {_bounded_error(exc)}"]
        return result

    result.update(
        {
            "repository_root": str(root),
            "head_commit": head,
            "tree": tree,
            "branch": branch or None,
            "commit_time": commit_time,
            "upstream_ref": upstream_ref,
            "upstream_commit": upstream_commit,
            "remote_url": remote_url,
            "tracked_clean": not tracked_status,
            "pushed_to_upstream": bool(upstream_commit and upstream_commit == head),
        }
    )
    if tracked_status:
        result["blocking_reasons"].append("tracked_source_changes_present")
    if not upstream_ref:
        result["blocking_reasons"].append("source_upstream_not_configured")
    elif upstream_commit != head:
        result["blocking_reasons"].append("source_head_not_equal_to_upstream")
    if not branch:
        result["blocking_reasons"].append("detached_source_head")
    return result


def _git_archive(repository: Path, commit: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), "archive", "--format=tar", commit],
        check=False,
        capture_output=True,
        timeout=60.0,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise _DeploymentError(error or "git archive failed")
    if not completed.stdout:
        raise _DeploymentError("git archive returned no content")
    return completed.stdout


def _git_text(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10.0,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "git command failed").strip()
        raise _DeploymentError(message)
    return completed.stdout.strip()


def _archive_content_snapshot(archive: bytes) -> dict[str, Any]:
    files: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle:
            relative = _validated_archive_path(member)
            if member.isdir():
                continue
            if relative in seen:
                raise _DeploymentError(f"duplicate archive path: {relative}")
            seen.add(relative)
            stream = bundle.extractfile(member)
            if stream is None:
                raise _DeploymentError(f"archive file is unreadable: {relative}")
            files.append((relative, stream.read()))

    digest = hashlib.sha256()
    total_bytes = 0
    paths: list[str] = []
    for relative, content in sorted(files):
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        paths.append(relative)
        total_bytes += len(content)
    return {
        "sha256": digest.hexdigest(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "paths": sorted(paths),
    }


def _extract_archive_safely(archive: bytes, destination: Path) -> None:
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle:
            relative = _validated_archive_path(member)
            target = _archive_destination(destination, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if relative in seen:
                raise _DeploymentError(f"duplicate archive path: {relative}")
            seen.add(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise _DeploymentError(f"archive path already exists: {relative}")
            stream = bundle.extractfile(member)
            if stream is None:
                raise _DeploymentError(f"archive file is unreadable: {relative}")
            with target.open("xb") as handle:
                shutil.copyfileobj(stream, handle)
                handle.flush()
                os.fsync(handle.fileno())


def _validated_archive_path(member: tarfile.TarInfo) -> str:
    if "\\" in member.name or "\x00" in member.name:
        raise _DeploymentError(f"unsafe archive path: {member.name}")
    path = PurePosixPath(member.name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        raise _DeploymentError(f"unsafe archive path: {member.name}")
    if path.as_posix() == MANAGED_RUNTIME_MANIFEST:
        raise _DeploymentError("archive contains the reserved runtime manifest")
    if not (member.isdir() or member.isfile()):
        raise _DeploymentError(f"unsupported archive member type: {member.name}")
    return path.as_posix()


def _archive_destination(destination: Path, relative: str) -> Path:
    target = destination.joinpath(*PurePosixPath(relative).parts)
    resolved_destination = destination.resolve()
    resolved_target = target.resolve()
    try:
        common = Path(os.path.commonpath((resolved_destination, resolved_target)))
    except ValueError as exc:
        raise _DeploymentError(f"unsafe archive destination: {relative}") from exc
    if common != resolved_destination:
        raise _DeploymentError(f"unsafe archive destination: {relative}")
    return target


def _validate_deployed_runtime(
    target: Path,
    *,
    python_command: str | Path,
) -> dict[str, Any]:
    args = managed_runtime_server_args(target)
    with tempfile.TemporaryDirectory(prefix="ms_mcp_managed_runtime_protocol_") as temp:
        summary = asyncio.run(
            run_protocol_acceptance(
                command=str(python_command),
                args=args,
                cwd=target,
                workspace=Path(temp) / "workspace",
                timeout_seconds=60.0,
                list_only=True,
            )
        )
    discovery = summary.get("discovery") or {}
    return {
        "ok": bool(summary.get("ok")),
        "status": "passed" if summary.get("ok") else "failed",
        "transport": summary.get("transport"),
        "list_only": summary.get("list_only"),
        "tool_count": summary.get("tool_count"),
        "required_tool_count": discovery.get("required_tool_count"),
        "missing_tools": discovery.get("missing_tools") or [],
        "annotation_errors": discovery.get("annotation_errors") or [],
        "schema_errors": discovery.get("schema_errors") or [],
        "errors": summary.get("errors") or [],
        "server_stderr_tail": summary.get("server_stderr_tail"),
        "materials_studio_process_touched": False,
    }


def _registration_handoff(
    *,
    target: Path,
    python_command: Path,
    config_path: Path,
    manifest_sha256: str,
    registration_plan: dict[str, Any],
) -> dict[str, Any]:
    plan_id = registration_plan.get("registration_plan_id")
    command: list[str] | None = None
    if plan_id and registration_plan.get("status") == "registration_ready":
        command = [
            str(python_command),
            str(target / "register_codex.py"),
            "--runtime-manifest-sha256",
            manifest_sha256,
            "--config",
            str(config_path),
            "--cwd",
            str(target),
            "--python",
            str(python_command),
            "--apply",
            "--expected-plan-id",
            str(plan_id),
        ]
    return {
        "status": registration_plan.get("status"),
        "config_path": str(config_path),
        "runtime_path": str(target),
        "runtime_manifest_sha256": manifest_sha256,
        "registration_plan_id": plan_id,
        "explicit_registration_confirmation_required": bool(command),
        "apply_command": command,
        "restart_required_after_apply": True,
        "materials_studio_process_touched": False,
    }


def _deployment_plan_id(receipt: dict[str, Any]) -> str:
    guard = {
        "schema": RUNTIME_DEPLOYMENT_PLAN_SCHEMA,
        "source_root": _normalized_path(receipt["source_root"]),
        "source_commit": receipt.get("source_commit"),
        "source_tree": receipt.get("source_tree"),
        "source_upstream": receipt.get("source_upstream"),
        "runtime_root": _normalized_path(receipt["runtime_root"]),
        "target_runtime_path": _normalized_path(receipt["target_runtime_path"]),
        "python_command": _normalized_path(receipt["python_command"]),
        "config_path": _normalized_path(receipt["config_path"]),
        "archive_sha256": receipt.get("archive_sha256"),
        "content_sha256": (receipt.get("content_snapshot") or {}).get("sha256"),
        "manifest_sha256": receipt.get("manifest_sha256"),
        "deployment_required": receipt.get("deployment_required"),
    }
    encoded = json.dumps(guard, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256_bytes(encoded.encode("ascii"))


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        common = Path(os.path.commonpath((left, right))).resolve()
    except (OSError, ValueError):
        return True
    return common == left or common == right


def _normalized_path(value: str | Path) -> str:
    normalized = str(Path(value).expanduser().resolve())
    return normalized.casefold() if os.name == "nt" else normalized


def _write_new_file(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _remove_staging_directory(staging: Path, parent: Path) -> None:
    resolved = staging.resolve()
    expected_parent = parent.resolve()
    if resolved.parent != expected_parent or ".staging-" not in resolved.name:
        raise _DeploymentError("refusing to remove an unrecognized staging directory")
    shutil.rmtree(resolved)


def _bounded_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:1000]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or explicitly deploy one pushed Materials Studio MCP commit "
            "to an immutable local runtime."
        )
    )
    parser.add_argument("--source", default=str(Path.cwd()))
    parser.add_argument("--runtime-root")
    parser.add_argument("--python", dest="python_command")
    parser.add_argument("--config")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _build_parser().parse_args(argv)
    if options.apply:
        result = apply_runtime_deployment(
            source_root=options.source,
            runtime_root=options.runtime_root,
            python_command=options.python_command,
            config_path=options.config,
            expected_plan_id=options.expected_plan_id,
        )
    else:
        result = plan_runtime_deployment(
            source_root=options.source,
            runtime_root=options.runtime_root,
            python_command=options.python_command,
            config_path=options.config,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
