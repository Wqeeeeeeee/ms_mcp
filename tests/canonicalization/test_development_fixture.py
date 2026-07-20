from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Callable, TypeVar

import pytest

from material_studio_mcp_server.canonicalization import (
    CanonicalReferenceArtifact,
    build_canonical_reference_artifact,
    canonical_reference_artifact_bytes,
    canonical_reference_artifact_relative_path,
    canonical_reference_artifact_sha256,
    verify_canonical_reference_artifact,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
STORE_ROOT = ROOT / "benchmarks" / "references" / "development" / "sic_3c_bulk"

RAW_SHA256 = "7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb"
SOURCE_SHA256 = "31c04bc038b7d4ce3bfced24c189e1c2e3939ef23c4c7eae8d384cb80402ed6b"
MANIFEST_SHA256 = "97bf9304eeffad3bdbe1d58d272719dc1a33ee18660e9f06961ad30b917882b1"
ARTIFACT_SHA256 = "bba4b03dd57d55816c21cdc32cd687362af6d32ad3da6823749c59abf019a781"
STRUCTURE_SHA256 = "fdb07c9079a70220a1319e3ca95171d23b975a55ea53b32ed219007b41e6b759"
SETTINGS_SHA256 = "6c1aca62ddd2ce2862e670c259c0accfbc0789130fa973d65e75c307fc3161b8"

RAW_PATH = STORE_ROOT / "raw" / "sha256" / "7b" / f"{RAW_SHA256}.bin"
SOURCE_PATH = STORE_ROOT / "sources" / "sha256" / "31" / f"{SOURCE_SHA256}.json"
MANIFEST_PATH = (
    STORE_ROOT / "manifests" / "sha256" / "97" / f"{MANIFEST_SHA256}.json"
)
ARTIFACT_PATH = (
    STORE_ROOT / "canonical" / "sha256" / "bb" / f"{ARTIFACT_SHA256}.json"
)

_REDACTED_EVIDENCE_FAILURE = (
    "development reference evidence mismatch; coordinate-bearing values redacted"
)
_T = TypeVar("_T")


def _evidence() -> tuple[bytes, bytes, bytes, bytes]:
    return (
        RAW_PATH.read_bytes(),
        SOURCE_PATH.read_bytes(),
        MANIFEST_PATH.read_bytes(),
        ARTIFACT_PATH.read_bytes(),
    )


def _assert_redacted_bytes_match(actual: bytes, expected: bytes) -> None:
    actual_digest = hashlib.sha256(actual).digest()
    expected_digest = hashlib.sha256(expected).digest()
    digests_match = hmac.compare_digest(actual_digest, expected_digest)
    if len(actual) != len(expected) or not digests_match:
        raise AssertionError(_REDACTED_EVIDENCE_FAILURE) from None


def _parse_artifact_redacted(content: bytes) -> CanonicalReferenceArtifact:
    try:
        return CanonicalReferenceArtifact.model_validate_json(content)
    except Exception:
        raise AssertionError(_REDACTED_EVIDENCE_FAILURE) from None


def _call_with_redacted_failure(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except Exception:
        raise AssertionError(_REDACTED_EVIDENCE_FAILURE) from None


def test_coordinate_bearing_comparison_failure_is_redacted() -> None:
    sensitive_marker = b"synthetic-sensitive-coordinate-marker"
    with pytest.raises(AssertionError) as captured:
        _assert_redacted_bytes_match(sensitive_marker, b"different")
    message = str(captured.value)
    assert message == _REDACTED_EVIDENCE_FAILURE
    assert sensitive_marker.decode("ascii") not in message
    with pytest.raises(AssertionError) as call_failure:
        _call_with_redacted_failure(
            lambda: (_ for _ in ()).throw(ValueError(sensitive_marker.decode("ascii")))
        )
    assert str(call_failure.value) == _REDACTED_EVIDENCE_FAILURE
    assert sensitive_marker.decode("ascii") not in str(call_failure.value)


def test_pinned_development_canonical_artifact_has_exact_content_identity() -> None:
    raw, source, manifest, artifact = _evidence()
    assert len(raw) == 3387
    assert hashlib.sha256(raw).hexdigest() == RAW_SHA256
    assert hashlib.sha256(source).hexdigest() == SOURCE_SHA256
    assert hashlib.sha256(manifest).hexdigest() == MANIFEST_SHA256
    assert hashlib.sha256(artifact).hexdigest() == ARTIFACT_SHA256
    parsed = _parse_artifact_redacted(artifact)
    serialized = _call_with_redacted_failure(
        lambda: canonical_reference_artifact_bytes(parsed)
    )
    _assert_redacted_bytes_match(serialized, artifact)
    assert parsed.canonical_structure_sha256 == STRUCTURE_SHA256
    assert parsed.settings_sha256 == SETTINGS_SHA256
    assert parsed.source.raw_artifact_sha256 == RAW_SHA256
    assert parsed.source.source_record_sha256 == SOURCE_SHA256
    assert parsed.source.manifest_sha256 == MANIFEST_SHA256


def test_development_artifact_rebuild_is_byte_reproducible() -> None:
    raw, source, manifest, artifact_bytes = _evidence()
    rebuilt = _call_with_redacted_failure(
        lambda: build_canonical_reference_artifact(
            raw_bytes=raw,
            source_record_bytes=source,
            manifest_bytes=manifest,
        )
    )
    _assert_redacted_bytes_match(
        canonical_reference_artifact_bytes(rebuilt),
        artifact_bytes,
    )
    assert canonical_reference_artifact_sha256(rebuilt) == ARTIFACT_SHA256
    assert (
        canonical_reference_artifact_relative_path(rebuilt)
        == f"canonical/sha256/bb/{ARTIFACT_SHA256}.json"
    )


def test_development_artifact_verifier_reconciles_source_without_mutation() -> None:
    raw, source, manifest, artifact = _evidence()
    before = tuple(hashlib.sha256(item).hexdigest() for item in (raw, source, manifest))
    verified = _call_with_redacted_failure(
        lambda: verify_canonical_reference_artifact(
            artifact_bytes=artifact,
            raw_bytes=raw,
            source_record_bytes=source,
            manifest_bytes=manifest,
            expected_artifact_sha256=ARTIFACT_SHA256,
        )
    )
    after = tuple(hashlib.sha256(item).hexdigest() for item in (raw, source, manifest))
    assert after == before
    assert verified.original_artifact_preserved is True
    assert verified.candidate_template is False
    assert verified.hidden_holdout is False


def test_development_summary_is_coordinate_free_and_symmetry_bound() -> None:
    artifact = _parse_artifact_redacted(ARTIFACT_PATH.read_bytes())
    summary = artifact.coordinate_free_summary
    fields = set(summary.model_dump())
    assert not fields.intersection(
        {
            "fractional_coordinates",
            "cartesian_coordinates",
            "lattice",
            "lattice_vectors",
            "atom_mapping",
            "coordinate_excerpt",
        }
    )
    assert summary.contains_coordinates is False
    assert summary.contains_lattice_vectors is False
    assert summary.atom_count == len(artifact.canonical_structure.sites)
    assert summary.symmetry == artifact.canonical_structure.symmetry
    if canonical_json_bytes(summary) in _evidence()[0]:
        raise AssertionError("coordinate-free summary leaked into raw reference evidence")


def test_development_artifact_is_confined_to_authorized_split_and_path() -> None:
    relative = ARTIFACT_PATH.relative_to(ROOT).as_posix()
    assert relative.startswith(
        "benchmarks/references/development/sic_3c_bulk/canonical/sha256/"
    )
    assert "validation" not in relative.split("/")
    assert "hidden_holdout" not in relative.split("/")
