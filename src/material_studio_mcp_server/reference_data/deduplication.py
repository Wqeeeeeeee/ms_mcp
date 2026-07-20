"""Conservative exact-byte duplicate classification."""

from __future__ import annotations

import hashlib

from .contracts import (
    MAX_RAW_ARTIFACT_BYTES,
    REFERENCE_CONTRACT_VERSION,
    RawArtifactFingerprint,
    RawDeduplicationResult,
)
from .errors import RawArtifactPolicyError


def fingerprint_raw_bytes(raw_bytes: bytes) -> RawArtifactFingerprint:
    """Hash an exact byte sequence without decoding or parsing it."""

    if type(raw_bytes) is not bytes:
        raise TypeError("raw_bytes must be an exact bytes instance")
    byte_count = len(raw_bytes)
    if byte_count == 0:
        raise RawArtifactPolicyError("zero-length raw artifacts are forbidden")
    if byte_count > MAX_RAW_ARTIFACT_BYTES:
        raise RawArtifactPolicyError(
            f"raw artifact exceeds the fixed {MAX_RAW_ARTIFACT_BYTES}-byte limit"
        )
    return RawArtifactFingerprint(
        algorithm="sha256",
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        byte_count=byte_count,
    )


def compare_raw_fingerprints(
    left: RawArtifactFingerprint,
    right: RawArtifactFingerprint,
) -> RawDeduplicationResult:
    """Declare a duplicate only when exact length and SHA-256 both match."""

    if not isinstance(left, RawArtifactFingerprint) or not isinstance(
        right, RawArtifactFingerprint
    ):
        raise TypeError("deduplication requires RawArtifactFingerprint inputs")
    byte_count_match = left.byte_count == right.byte_count
    sha256_match = left.sha256 == right.sha256
    duplicate = byte_count_match and sha256_match
    return RawDeduplicationResult(
        contract_version=REFERENCE_CONTRACT_VERSION,
        status=("exact_raw_duplicate" if duplicate else "byte_different_unresolved"),
        duplicate=duplicate,
        byte_count_match=byte_count_match,
        sha256_match=sha256_match,
        basis="exact_raw_byte_length_and_sha256",
        cif_parsing_performed=False,
        canonicalization_performed=False,
        structural_equivalence_claimed=False,
    )


def compare_raw_bytes(left: bytes, right: bytes) -> RawDeduplicationResult:
    """Fingerprint and compare two exact byte sequences without parsing them."""

    return compare_raw_fingerprints(
        fingerprint_raw_bytes(left),
        fingerprint_raw_bytes(right),
    )


__all__ = [
    "compare_raw_bytes",
    "compare_raw_fingerprints",
    "fingerprint_raw_bytes",
]
