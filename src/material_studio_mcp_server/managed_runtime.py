"""Managed immutable runtime manifests and content verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, MutableMapping, Sequence

from .python_runtime import (
    python_runtime_contract,
    python_runtime_contract_summary,
    validate_python_runtime_contract,
)


MANAGED_RUNTIME_SCHEMA = "material_studio_mcp_managed_runtime_v2"
MANAGED_RUNTIME_MANIFEST = ".materials_studio_mcp_runtime.json"
RUNTIME_MANIFEST_ARGUMENT = "--runtime-manifest-sha256"
RUNTIME_MANIFEST_ENV = "MATERIAL_STUDIO_MCP_EXPECTED_RUNTIME_MANIFEST_SHA256"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_SAFE_PROCESS_CWD_LENGTH = 240


def filesystem_io_path(path: str | Path) -> Path:
    """Return an absolute path suitable for long-path I/O on Windows."""

    candidate = Path(path)
    if os.name != "nt":
        return candidate
    value = str(candidate)
    if value.startswith("\\\\?\\"):
        return candidate
    absolute = str(candidate.resolve())
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def process_launch_path(path: str | Path) -> Path:
    """Use an extended Windows path only when CreateProcess may need one."""

    resolved = Path(path).expanduser().resolve()
    value = str(resolved)
    if os.name == "nt" and (
        value.startswith("\\\\?\\")
        or len(value) >= _WINDOWS_SAFE_PROCESS_CWD_LENGTH
    ):
        return filesystem_io_path(resolved)
    return resolved


def managed_runtime_launch_cwd(runtime_root: str | Path) -> Path:
    """Choose the nearest existing ancestor safe for Windows process startup."""

    root = Path(runtime_root).expanduser().resolve()
    if os.name != "nt":
        return root
    root = Path(_portable_path_text(root))
    for candidate in (root, *root.parents):
        value = str(candidate)
        if (
            not value.startswith("\\\\?\\")
            and len(value) < _WINDOWS_SAFE_PROCESS_CWD_LENGTH
            and filesystem_io_path(candidate).is_dir()
        ):
            return candidate
    return Path(root.anchor)


def default_managed_runtime_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return (Path(base) / "materials_studio_mcp" / "runtimes").resolve()
    return (
        Path.home() / ".local" / "share" / "materials_studio_mcp" / "runtimes"
    ).resolve()


def runtime_content_snapshot(runtime_root: str | Path) -> dict[str, Any]:
    """Hash all immutable runtime files except the self-describing manifest."""

    root = Path(runtime_root).expanduser().resolve()
    io_root = filesystem_io_path(root)
    result: dict[str, Any] = {
        "status": "unavailable",
        "sha256": None,
        "file_count": 0,
        "total_bytes": 0,
        "unreadable_files": [],
        "unexpected_links": [],
    }
    if not io_root.is_dir():
        result["error"] = f"runtime_root_not_found: {root}"
        return result

    files: list[tuple[str, Path]] = []
    unexpected_links: list[str] = []
    try:
        for path in io_root.rglob("*"):
            relative = path.relative_to(io_root)
            if relative.as_posix() == MANAGED_RUNTIME_MANIFEST:
                continue
            io_path = filesystem_io_path(path)
            if io_path.is_symlink():
                unexpected_links.append(relative.as_posix())
                continue
            if io_path.is_file():
                files.append((relative.as_posix(), io_path))
    except OSError as exc:
        result["error"] = f"runtime_walk_failed: {exc}"
        return result

    digest = hashlib.sha256()
    total_bytes = 0
    unreadable: list[str] = []
    for relative, path in sorted(files, key=lambda item: item[0]):
        try:
            content = path.read_bytes()
        except OSError:
            unreadable.append(relative)
            continue
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        total_bytes += len(content)

    complete = not unreadable and not unexpected_links
    result.update(
        {
            "status": "complete" if complete else "incomplete",
            "sha256": digest.hexdigest() if complete else None,
            "file_count": len(files) - len(unreadable),
            "total_bytes": total_bytes,
            "unreadable_files": unreadable,
            "unexpected_links": sorted(unexpected_links),
            "error": None,
        }
    )
    return result


def managed_runtime_status(
    runtime_root: str | Path,
    *,
    expected_manifest_sha256: str | None = None,
    verify_python_runtime: bool = False,
) -> dict[str, Any]:
    """Verify a managed runtime against its manifest and optional host binding."""

    root = Path(runtime_root).expanduser().resolve()
    manifest_path = root / MANAGED_RUNTIME_MANIFEST
    manifest_exists = filesystem_io_path(manifest_path).is_file()
    observed_manifest_hash = _file_sha256(manifest_path)
    expected_hash = _normalize_sha256(expected_manifest_sha256)
    result: dict[str, Any] = {
        "schema": MANAGED_RUNTIME_SCHEMA,
        "status": "not_managed",
        "managed": False,
        "runtime_root": str(root),
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_exists,
        "manifest_sha256": observed_manifest_hash,
        "expected_manifest_sha256": expected_hash,
        "manifest_binding_matches": (
            observed_manifest_hash == expected_hash if expected_hash else None
        ),
        "integrity_verified": None,
        "content_snapshot": None,
        "source_commit": None,
        "source_tree": None,
        "source_branch": None,
        "source_remote": None,
        "declared_python_runtime": None,
        "observed_python_runtime": None,
        "python_runtime_verified": None,
        "errors": [],
    }
    if expected_manifest_sha256 is not None and expected_hash is None:
        result.update(
            {
                "status": "invalid_expected_manifest_sha256",
                "integrity_verified": False,
                "errors": ["expected_manifest_sha256_invalid"],
            }
        )
        return result
    if not manifest_exists:
        if expected_hash:
            result.update(
                {
                    "status": "expected_manifest_missing",
                    "integrity_verified": False,
                    "errors": ["managed_runtime_manifest_missing"],
                }
            )
        return result

    result["managed"] = True
    try:
        payload = json.loads(
            filesystem_io_path(manifest_path).read_text(encoding="utf-8")
        )
    except Exception as exc:
        result.update(
            {
                "status": "manifest_parse_failed",
                "integrity_verified": False,
                "errors": [f"manifest_parse_failed: {_bounded_error(exc)}"],
            }
        )
        return result
    if not isinstance(payload, dict):
        result.update(
            {
                "status": "manifest_invalid",
                "integrity_verified": False,
                "errors": ["manifest_root_not_object"],
            }
        )
        return result

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    expected_content = (
        payload.get("content_snapshot")
        if isinstance(payload.get("content_snapshot"), dict)
        else {}
    )
    declared_python_runtime = payload.get("python_runtime_contract")
    result.update(
        {
            "manifest_schema": payload.get("schema"),
            "source_commit": source.get("commit"),
            "source_tree": source.get("tree"),
            "source_branch": source.get("branch"),
            "source_remote": source.get("remote"),
            "archive_sha256": payload.get("archive_sha256"),
            "declared_runtime_root": payload.get("runtime_root"),
            "declared_content_snapshot": expected_content,
            "declared_python_runtime": python_runtime_contract_summary(
                declared_python_runtime
            ),
        }
    )
    errors: list[str] = []
    if payload.get("schema") != MANAGED_RUNTIME_SCHEMA:
        errors.append("manifest_schema_mismatch")
    if not _same_path(payload.get("runtime_root"), root):
        errors.append("manifest_runtime_root_mismatch")
    if not _is_git_object_id(source.get("commit")):
        errors.append("manifest_source_commit_invalid")
    if not _is_git_object_id(source.get("tree")):
        errors.append("manifest_source_tree_invalid")
    if expected_hash and observed_manifest_hash != expected_hash:
        errors.append("manifest_sha256_binding_mismatch")
    runtime_contract_errors = validate_python_runtime_contract(
        declared_python_runtime
    )
    errors.extend(runtime_contract_errors)
    if (
        isinstance(declared_python_runtime, dict)
        and declared_python_runtime.get("status") != "complete"
    ):
        errors.append("python_runtime_contract_incomplete")

    if verify_python_runtime and not runtime_contract_errors:
        observed_python_runtime = python_runtime_contract()
        observed_summary = python_runtime_contract_summary(
            observed_python_runtime
        )
        declared_hash = (
            declared_python_runtime.get("contract_sha256")
            if isinstance(declared_python_runtime, dict)
            else None
        )
        observed_hash = observed_python_runtime.get("contract_sha256")
        python_runtime_verified = bool(
            observed_python_runtime.get("status") == "complete"
            and observed_hash == declared_hash
        )
        result.update(
            {
                "observed_python_runtime": observed_summary,
                "python_runtime_verified": python_runtime_verified,
            }
        )
        if not python_runtime_verified:
            errors.append("python_runtime_contract_mismatch")

    required_paths = (
        root / "run_server.py",
        root / "register_codex.py",
        root / "pyproject.toml",
        root / "src" / "material_studio_mcp_server" / "python_runtime.py",
        root / "src" / "material_studio_mcp_server" / "server.py",
    )
    missing_required = [
        path.relative_to(root).as_posix()
        for path in required_paths
        if not filesystem_io_path(path).is_file()
    ]
    if missing_required:
        errors.append("managed_runtime_required_files_missing")

    current_content = runtime_content_snapshot(root)
    result["content_snapshot"] = current_content
    expected_content_hash = expected_content.get("sha256")
    if current_content.get("status") != "complete":
        errors.append("managed_runtime_content_unreadable")
    elif current_content.get("sha256") != expected_content_hash:
        errors.append("managed_runtime_content_sha256_mismatch")
    if current_content.get("file_count") != expected_content.get("file_count"):
        errors.append("managed_runtime_file_count_mismatch")
    if current_content.get("total_bytes") != expected_content.get("total_bytes"):
        errors.append("managed_runtime_total_bytes_mismatch")

    integrity_verified = not errors
    result.update(
        {
            "status": "verified" if integrity_verified else "integrity_failed",
            "integrity_verified": integrity_verified,
            "errors": errors,
            "missing_required_files": missing_required,
        }
    )
    return result


def managed_runtime_server_args(runtime_root: str | Path) -> list[str]:
    """Return the exact server argv for one verified managed runtime."""

    root = Path(runtime_root).expanduser().resolve()
    status = managed_runtime_status(root)
    args = [str(process_launch_path(root / "run_server.py"))]
    if status.get("managed") and status.get("integrity_verified") is True:
        args.extend(
            (
                RUNTIME_MANIFEST_ARGUMENT,
                str(status["manifest_sha256"]),
            )
        )
    return args


def consume_runtime_manifest_argument(
    argv: list[str],
    environ: MutableMapping[str, str],
) -> str | None:
    """Consume one launcher-only manifest binding before importing the server."""

    indexes = [
        index for index, value in enumerate(argv) if value == RUNTIME_MANIFEST_ARGUMENT
    ]
    if not indexes:
        return None
    if len(indexes) != 1:
        raise RuntimeError("runtime manifest argument must appear exactly once")
    index = indexes[0]
    if index + 1 >= len(argv):
        raise RuntimeError("runtime manifest argument requires a SHA-256 value")
    value = _normalize_sha256(argv[index + 1])
    if value is None:
        raise RuntimeError("runtime manifest SHA-256 must be 64 lowercase hex characters")
    existing = _normalize_sha256(environ.get(RUNTIME_MANIFEST_ENV))
    if environ.get(RUNTIME_MANIFEST_ENV) is not None and existing != value:
        raise RuntimeError("runtime manifest argument conflicts with the environment")
    environ[RUNTIME_MANIFEST_ENV] = value
    del argv[index : index + 2]
    return value


def require_managed_runtime_launcher_binding(
    runtime_root: str | Path,
    manifest_sha256: str | None,
) -> None:
    """Reject an unbound or modified managed runtime before server import."""

    root = Path(runtime_root).expanduser().resolve()
    manifest_exists = filesystem_io_path(root / MANAGED_RUNTIME_MANIFEST).is_file()
    if manifest_exists and manifest_sha256 is None:
        raise RuntimeError(
            "managed runtime server launch requires --runtime-manifest-sha256"
        )
    if manifest_sha256 is None:
        return
    status = managed_runtime_status(
        root,
        expected_manifest_sha256=manifest_sha256,
        verify_python_runtime=True,
    )
    if status.get("integrity_verified") is not True:
        errors = ", ".join(str(item) for item in status.get("errors") or [])
        detail = errors or str(status.get("status") or "verification_failed")
        raise RuntimeError(f"managed runtime integrity verification failed: {detail}")


def manifest_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if _SHA256_PATTERN.fullmatch(normalized) else None


def _is_git_object_id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40,64}", value))


def _same_path(left: Any, right: str | Path) -> bool:
    if not isinstance(left, str) or not left:
        return False
    try:
        left_path = _portable_path_text(Path(left).expanduser().resolve())
        right_path = _portable_path_text(Path(right).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    if os.name == "nt":
        return left_path.casefold() == right_path.casefold()
    return left_path == right_path


def _portable_path_text(path: str | Path) -> str:
    text = str(path)
    if os.name != "nt":
        return text
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def _file_sha256(path: Path) -> str | None:
    io_path = filesystem_io_path(path)
    if not io_path.is_file():
        return None
    return sha256_bytes(io_path.read_bytes())


def _bounded_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:500]


__all__: Sequence[str] = (
    "MANAGED_RUNTIME_MANIFEST",
    "MANAGED_RUNTIME_SCHEMA",
    "RUNTIME_MANIFEST_ARGUMENT",
    "RUNTIME_MANIFEST_ENV",
    "consume_runtime_manifest_argument",
    "default_managed_runtime_root",
    "filesystem_io_path",
    "managed_runtime_launch_cwd",
    "managed_runtime_server_args",
    "managed_runtime_status",
    "manifest_bytes",
    "process_launch_path",
    "require_managed_runtime_launcher_binding",
    "runtime_content_snapshot",
    "sha256_bytes",
)
