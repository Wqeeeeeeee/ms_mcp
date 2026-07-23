"""Deterministic Python interpreter and dependency contracts."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import struct
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


PYTHON_RUNTIME_CONTRACT_SCHEMA = "material_studio_mcp_python_runtime_v1"
PYTHON_RUNTIME_PROBE_SCHEMA = "material_studio_mcp_python_runtime_probe_v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_SAFE_PROCESS_CWD_LENGTH = 240
_ROOT_DISTRIBUTIONS: Mapping[str, frozenset[str]] = {
    "jinja2": frozenset(),
    "mcp": frozenset({"cli"}),
    "packaging": frozenset(),
    "pydantic": frozenset(),
}
if os.name == "nt":
    _ROOT_DISTRIBUTIONS = {
        **_ROOT_DISTRIBUTIONS,
        "pywinauto": frozenset(),
    }
if sys.version_info < (3, 11):
    _ROOT_DISTRIBUTIONS = {
        **_ROOT_DISTRIBUTIONS,
        "tomli": frozenset(),
    }


def python_runtime_contract() -> dict[str, Any]:
    """Hash the active interpreter and installed runtime dependency closure."""

    errors: list[str] = []
    distribution_names, resolution_errors = _resolve_distribution_closure()
    errors.extend(resolution_errors)
    distributions: dict[str, Any] = {}
    for name in distribution_names:
        snapshot = _distribution_snapshot(name)
        distributions[name] = snapshot
        if snapshot.get("status") != "complete":
            errors.append(f"distribution_incomplete:{name}")

    binaries = _python_binary_snapshots()
    if any(item.get("status") != "complete" for item in binaries):
        errors.append("python_binary_snapshot_incomplete")

    payload: dict[str, Any] = {
        "schema": PYTHON_RUNTIME_CONTRACT_SCHEMA,
        "status": "complete" if not errors else "incomplete",
        "python": {
            "executable": _normalized_path(sys.executable),
            "base_executable": _normalized_path(
                getattr(sys, "_base_executable", sys.executable)
            ),
            "prefix": _normalized_path(sys.prefix),
            "base_prefix": _normalized_path(sys.base_prefix),
            "version": platform.python_version(),
            "version_info": [
                sys.version_info.major,
                sys.version_info.minor,
                sys.version_info.micro,
            ],
            "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag,
            "pointer_bits": struct.calcsize("P") * 8,
            "machine": platform.machine(),
            "platform": sys.platform,
            "is_virtual_environment": sys.prefix != sys.base_prefix,
        },
        "python_binaries": binaries,
        "root_distributions": {
            canonicalize_name(name): sorted(extras)
            for name, extras in sorted(_ROOT_DISTRIBUTIONS.items())
        },
        "distributions": distributions,
        "distribution_count": len(distributions),
        "errors": sorted(set(errors)),
    }
    payload["contract_sha256"] = python_runtime_contract_sha256(payload)
    return payload


def probe_python_runtime_contract(
    python_command: str | Path,
    source_root: str | Path,
    *,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Ask the configured Python executable to describe its own runtime."""

    command = Path(python_command).expanduser().resolve()
    root = Path(source_root).expanduser().resolve()
    source = root / "src"
    base: dict[str, Any] = {
        "schema": PYTHON_RUNTIME_PROBE_SCHEMA,
        "status": "failed",
        "ok": False,
        "python_command": str(command),
        "source_root": str(root),
        "contract": None,
        "contract_sha256": None,
        "error": None,
        "stderr_tail": None,
    }
    if not command.is_file():
        return {**base, "error": "python_command_not_found"}
    if not _filesystem_io_path(source).is_dir():
        return {**base, "error": "python_runtime_probe_source_missing"}

    source_literal = json.dumps(
        str(_filesystem_io_path(source)),
        ensure_ascii=True,
    )
    probe = (
        "import json,sys;"
        "sys.dont_write_bytecode=True;"
        f"sys.path.insert(0,{source_literal});"
        "from material_studio_mcp_server.python_runtime "
        "import python_runtime_contract;"
        "print(json.dumps(python_runtime_contract(),"
        "sort_keys=True,separators=(',',':')))"
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    try:
        completed = subprocess.run(
            [str(command), "-I", "-c", probe],
            cwd=_safe_process_cwd(root),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout_seconds)),
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except Exception as exc:
        return {**base, "error": f"python_runtime_probe_failed:{_bounded_error(exc)}"}

    stderr_tail = (completed.stderr or "").strip()[-2000:] or None
    if completed.returncode != 0:
        return {
            **base,
            "error": f"python_runtime_probe_exit_{completed.returncode}",
            "stderr_tail": stderr_tail,
        }
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return {
            **base,
            "error": "python_runtime_probe_output_invalid",
            "stderr_tail": stderr_tail,
        }
    try:
        contract = json.loads(lines[0])
    except Exception as exc:
        return {
            **base,
            "error": f"python_runtime_probe_json_invalid:{_bounded_error(exc)}",
            "stderr_tail": stderr_tail,
        }
    validation_errors = validate_python_runtime_contract(contract)
    if validation_errors:
        return {
            **base,
            "error": "python_runtime_contract_invalid",
            "contract": contract if isinstance(contract, dict) else None,
            "validation_errors": validation_errors,
            "stderr_tail": stderr_tail,
        }
    if contract.get("status") != "complete":
        return {
            **base,
            "error": "python_runtime_contract_incomplete",
            "contract": contract,
            "contract_sha256": contract.get("contract_sha256"),
            "stderr_tail": stderr_tail,
        }
    return {
        **base,
        "status": "complete",
        "ok": True,
        "contract": contract,
        "contract_sha256": contract.get("contract_sha256"),
        "stderr_tail": stderr_tail,
    }


def validate_python_runtime_contract(contract: Any) -> list[str]:
    """Validate a stored contract without probing the current interpreter."""

    if not isinstance(contract, dict):
        return ["python_runtime_contract_not_object"]
    errors: list[str] = []
    if contract.get("schema") != PYTHON_RUNTIME_CONTRACT_SCHEMA:
        errors.append("python_runtime_contract_schema_mismatch")
    if contract.get("status") not in {"complete", "incomplete"}:
        errors.append("python_runtime_contract_status_invalid")
    expected_hash = contract.get("contract_sha256")
    if not isinstance(expected_hash, str) or not _SHA256_PATTERN.fullmatch(
        expected_hash
    ):
        errors.append("python_runtime_contract_sha256_invalid")
    elif python_runtime_contract_sha256(contract) != expected_hash:
        errors.append("python_runtime_contract_sha256_mismatch")
    python = contract.get("python")
    if not isinstance(python, dict) or not python.get("executable"):
        errors.append("python_runtime_identity_missing")
    distributions = contract.get("distributions")
    if not isinstance(distributions, dict) or not distributions:
        errors.append("python_runtime_distributions_missing")
    if contract.get("distribution_count") != (
        len(distributions) if isinstance(distributions, dict) else None
    ):
        errors.append("python_runtime_distribution_count_mismatch")
    return errors


def python_runtime_contract_sha256(contract: Mapping[str, Any]) -> str:
    payload = dict(contract)
    payload.pop("contract_sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def python_runtime_contract_summary(contract: Any) -> dict[str, Any]:
    if not isinstance(contract, dict):
        return {
            "status": "missing",
            "contract_sha256": None,
            "python_executable": None,
            "python_version": None,
            "distribution_count": None,
            "distribution_versions": {},
        }
    python = contract.get("python") if isinstance(contract.get("python"), dict) else {}
    distributions = (
        contract.get("distributions")
        if isinstance(contract.get("distributions"), dict)
        else {}
    )
    return {
        "status": contract.get("status"),
        "contract_sha256": contract.get("contract_sha256"),
        "python_executable": python.get("executable"),
        "base_executable": python.get("base_executable"),
        "python_version": python.get("version"),
        "implementation": python.get("implementation"),
        "pointer_bits": python.get("pointer_bits"),
        "distribution_count": contract.get("distribution_count"),
        "distribution_versions": {
            name: item.get("version")
            for name, item in sorted(distributions.items())
            if isinstance(item, dict)
        },
        "errors": contract.get("errors") or [],
    }


def _resolve_distribution_closure() -> tuple[list[str], list[str]]:
    environment = default_environment()
    enabled_extras: dict[str, set[str]] = {
        canonicalize_name(name): set(extras)
        for name, extras in _ROOT_DISTRIBUTIONS.items()
    }
    pending = sorted(enabled_extras)
    processed: dict[str, frozenset[str]] = {}
    errors: list[str] = []
    while pending:
        name = pending.pop(0)
        extras = frozenset(enabled_extras.get(name) or ())
        if processed.get(name) == extras:
            continue
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            errors.append(f"distribution_missing:{name}")
            processed[name] = extras
            continue
        processed[name] = extras
        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement:
                errors.append(f"distribution_requirement_invalid:{name}")
                continue
            contexts = extras or frozenset({""})
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({**environment, "extra": extra})
                for extra in contexts
            ):
                continue
            child = canonicalize_name(requirement.name)
            previous = set(enabled_extras.get(child) or ())
            updated = previous | {
                canonicalize_name(extra) for extra in requirement.extras
            }
            if child not in enabled_extras or updated != previous:
                enabled_extras[child] = updated
                pending.append(child)
    return sorted(enabled_extras), sorted(set(errors))


def _distribution_snapshot(name: str) -> dict[str, Any]:
    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return {
            "status": "missing",
            "version": None,
            "sha256": None,
            "file_count": 0,
            "total_bytes": 0,
            "errors": ["distribution_not_installed"],
        }
    files = distribution.files
    if files is None:
        return {
            "status": "incomplete",
            "version": distribution.version,
            "sha256": None,
            "file_count": 0,
            "total_bytes": 0,
            "errors": ["distribution_file_manifest_missing"],
        }

    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    errors: list[str] = []
    normalized_files = {
        str(item).replace("\\", "/"): item
        for item in files
        if "__pycache__" not in Path(str(item)).parts
        and Path(str(item)).suffix.casefold() not in {".pyc", ".pyo"}
    }
    for relative, item in sorted(normalized_files.items()):
        path = _filesystem_io_path(distribution.locate_file(item))
        if path.is_symlink():
            errors.append(f"distribution_file_is_link:{relative}")
            continue
        if not path.is_file():
            errors.append(f"distribution_file_missing:{relative}")
            continue
        try:
            content = path.read_bytes()
        except OSError:
            errors.append(f"distribution_file_unreadable:{relative}")
            continue
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        file_count += 1
        total_bytes += len(content)
    complete = not errors and file_count == len(normalized_files)
    return {
        "status": "complete" if complete else "incomplete",
        "version": distribution.version,
        "sha256": digest.hexdigest() if complete else None,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "errors": errors[:50],
    }


def _python_binary_snapshots() -> list[dict[str, Any]]:
    candidates: dict[str, Path] = {
        "executable": Path(sys.executable),
        "base_executable": Path(getattr(sys, "_base_executable", sys.executable)),
    }
    base = Path(sys.base_prefix)
    patterns = (
        ("runtime_library", "python*.dll"),
        ("runtime_library", "libpython*.so*"),
        ("runtime_library", "libpython*.dylib"),
    )
    for role, pattern in patterns:
        for directory in (base, base / "DLLs", base / "lib"):
            if not directory.is_dir():
                continue
            for path in directory.glob(pattern):
                candidates[f"{role}:{path.name}"] = path

    snapshots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role, path in sorted(candidates.items()):
        normalized = _normalized_path(path)
        identity = normalized.casefold() if os.name == "nt" else normalized
        if identity in seen:
            continue
        seen.add(identity)
        io_path = _filesystem_io_path(path)
        if not io_path.is_file():
            snapshots.append(
                {
                    "role": role,
                    "path": normalized,
                    "status": "missing",
                    "sha256": None,
                    "size": None,
                }
            )
            continue
        try:
            content = io_path.read_bytes()
        except OSError:
            snapshots.append(
                {
                    "role": role,
                    "path": normalized,
                    "status": "unreadable",
                    "sha256": None,
                    "size": None,
                }
            )
            continue
        snapshots.append(
            {
                "role": role,
                "path": normalized,
                "status": "complete",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    return snapshots


def _filesystem_io_path(path: str | Path) -> Path:
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


def _safe_process_cwd(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if os.name != "nt":
        return root
    root = Path(_portable_path_text(root))
    for candidate in (root, *root.parents):
        if (
            len(str(candidate)) < _WINDOWS_SAFE_PROCESS_CWD_LENGTH
            and _filesystem_io_path(candidate).is_dir()
        ):
            return candidate
    return Path(root.anchor)


def _portable_path_text(path: str | Path) -> str:
    text = str(path)
    if os.name != "nt":
        return text
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def _normalized_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _bounded_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:500]


__all__: Sequence[str] = (
    "PYTHON_RUNTIME_CONTRACT_SCHEMA",
    "PYTHON_RUNTIME_PROBE_SCHEMA",
    "probe_python_runtime_contract",
    "python_runtime_contract",
    "python_runtime_contract_sha256",
    "python_runtime_contract_summary",
    "validate_python_runtime_contract",
)
