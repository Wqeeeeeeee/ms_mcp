"""Coordinate-free real-run projection and no-clobber persistence."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from material_studio_mcp_server.benchmark_evaluation import (
    assert_coordinate_free_payload,
)
from material_studio_mcp_server.ms_roundtrip.errors import RoundtripError
from material_studio_mcp_server.ms_roundtrip.secure_io import (
    atomic_write_json,
    canonical_json_bytes,
    reject_link_or_reparse_components,
    resolve_existing_directory,
    sha256_bytes,
)
from material_studio_mcp_server.state.execution import canonical_json_sha256

from .contracts import (
    CastepAcceptanceEvidence,
    CastepBenchmarkAcceptance,
    CastepVerificationReport,
    FixedCastepProfile,
)
from .profile import repository_root


_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[^a-z0-9])[a-z]:[\\/]")
_UNC_RE = re.compile(r"(?:^|[^\\])\\\\[^\\]+\\[^\\]+")
_POSIX_LOCAL_RE = re.compile(r"/(?:home|users|tmp|var/tmp|private/tmp)/", re.I)
_COMMAND_RE = re.compile(
    r"(?i)(?:powershell|cmd\.exe|runmatscript\.bat|matstudio\.exe|python\.exe)"
)
_FORBIDDEN_KEY_PARTS = (
    "absolute_path",
    "command_line",
    "raw_output",
    "raw_native",
    "process_id",
    "window_handle",
    "username",
    "hostname",
    "environment_value",
)


def _assert_no_machine_local_values(value: Any, *, key: str = "") -> None:
    folded_key = key.casefold()
    if any(part in folded_key for part in _FORBIDDEN_KEY_PARTS):
        if value not in (False, None, 0, ""):
            raise ValueError(f"machine-local evidence is forbidden at {key}")
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _assert_no_machine_local_values(
                child,
                key=f"{key}.{child_key}" if key else str(child_key),
            )
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_machine_local_values(child, key=f"{key}.{index}")
        return
    if isinstance(value, str) and (
        _WINDOWS_ABSOLUTE_RE.search(value)
        or _UNC_RE.search(value)
        or _POSIX_LOCAL_RE.search(value)
        or _COMMAND_RE.search(value)
    ):
        raise ValueError(f"machine-local string is forbidden at {key}")


def canonical_evidence_sha256(evidence: CastepAcceptanceEvidence) -> str:
    if not isinstance(evidence, CastepAcceptanceEvidence):
        raise TypeError("evidence must be CastepAcceptanceEvidence")
    return canonical_json_sha256(evidence.model_dump(mode="json"))


def project_real_evidence(
    *,
    verification: CastepVerificationReport,
    benchmark_acceptance: CastepBenchmarkAcceptance,
) -> CastepAcceptanceEvidence:
    evidence = CastepAcceptanceEvidence(
        profile=FixedCastepProfile(),
        verification=verification,
        benchmark_acceptance=benchmark_acceptance,
    )
    payload = evidence.model_dump(mode="json")
    assert_coordinate_free_payload(payload)
    _assert_no_machine_local_values(payload)
    return evidence


def validate_evidence_projection(
    payload: Mapping[str, Any],
    *,
    expected_canonical_sha256: str,
) -> CastepAcceptanceEvidence:
    untrusted = dict(payload)
    assert_coordinate_free_payload(untrusted)
    _assert_no_machine_local_values(untrusted)
    encoded = json.dumps(
        untrusted,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence = CastepAcceptanceEvidence.model_validate_json(encoded, strict=True)
    projected = evidence.model_dump(mode="json")
    assert_coordinate_free_payload(projected)
    _assert_no_machine_local_values(projected)
    if canonical_evidence_sha256(evidence) != expected_canonical_sha256:
        raise ValueError("recorded CASTEP evidence canonical SHA-256 mismatch")
    return evidence


def validate_external_evidence_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("path must be Path")
    lexical_output = Path(os.path.abspath(os.fspath(path.expanduser())))
    if os.name == "nt" and ":" in lexical_output.name:
        raise ValueError("real CASTEP evidence path contains an unsafe component")
    try:
        reject_link_or_reparse_components(lexical_output)
        parent = resolve_existing_directory(lexical_output.parent)
    except RoundtripError as exc:
        raise ValueError(
            "real CASTEP evidence path contains an unsafe component"
        ) from exc
    output = parent / lexical_output.name
    root = repository_root().resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("real CASTEP evidence output must be outside the repository")
    if output.exists() or output.is_symlink():
        raise ValueError("real CASTEP evidence output already exists")
    return output


def write_external_evidence(
    path: Path,
    evidence: CastepAcceptanceEvidence,
) -> str:
    output = validate_external_evidence_path(path)
    digest = canonical_evidence_sha256(evidence)
    payload = evidence.model_dump(mode="json")
    _assert_no_machine_local_values(payload)
    try:
        snapshot = atomic_write_json(output, payload)
    except RoundtripError as exc:
        raise ValueError(
            "real CASTEP evidence output already exists or could not be "
            "published atomically"
        ) from exc
    expected_bytes = canonical_json_bytes(payload, trailing_newline=True)
    if (
        snapshot.payload != expected_bytes
        or snapshot.sha256 != sha256_bytes(expected_bytes)
    ):
        raise ValueError("persisted CASTEP evidence bytes or SHA-256 mismatch")
    persisted = json.loads(snapshot.payload)
    validate_evidence_projection(
        persisted,
        expected_canonical_sha256=digest,
    )
    return digest


__all__ = [
    "canonical_evidence_sha256",
    "project_real_evidence",
    "validate_external_evidence_path",
    "validate_evidence_projection",
    "write_external_evidence",
]
