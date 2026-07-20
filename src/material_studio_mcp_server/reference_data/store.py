"""Offline exact-byte ingestion into a confined content-addressed store."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from pydantic import ValidationError

from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from ._paths import (
    confined_file_path,
    create_confined_file,
    ensure_parent_directory,
    prepare_store_root,
    read_confined_file,
    store_transaction,
)
from .contracts import (
    MAX_RAW_ARTIFACT_BYTES,
    IngestionReceipt,
    ReferenceManifest,
    ReferenceMetadataProjection,
    ReferenceSource,
    manifest_relative_path,
)
from .deduplication import fingerprint_raw_bytes
from .errors import (
    ArtifactCorruptionError,
    DigestMismatchError,
    PartialPublicationError,
    PublicationConflictError,
)
from .manifest import (
    _build_verified_ingestion_receipt,
    _project_verified_reference_metadata,
    build_reference_manifest,
    manifest_bytes,
    source_record_bytes,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_CANONICAL_METADATA_BYTES = 1024 * 1024


def _validate_expected_sha256(expected_sha256: str | None) -> str | None:
    if expected_sha256 is None:
        return None
    if type(expected_sha256) is not str or _SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ValueError("expected_sha256 must be a lowercase 64-character digest")
    return expected_sha256


def _expected_files(
    raw_bytes: bytes,
    source: ReferenceSource,
    manifest: ReferenceManifest,
) -> tuple[tuple[str, bytes], ...]:
    manifest_content = manifest_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()
    return (
        (manifest.raw_artifact.relative_path, raw_bytes),
        (manifest.source_record_relative_path, source_record_bytes(source)),
        (manifest_relative_path(manifest_sha256), manifest_content),
    )


def _preflight_publication_state(
    root: Path,
    expected_files: tuple[tuple[str, bytes], ...],
) -> bool:
    """Return true for an exact complete repeat and false for a fresh publish."""

    states: list[bool] = []
    for relative_path, expected_content in expected_files:
        ensure_parent_directory(root, relative_path)
        path = confined_file_path(root, relative_path)
        present = path.exists()
        states.append(present)
        if present:
            try:
                existing = read_confined_file(
                    root,
                    relative_path,
                    max_bytes=len(expected_content),
                    expected_size=len(expected_content),
                )
            except ArtifactCorruptionError as exc:
                raise PublicationConflictError(
                    "existing content-addressed evidence conflicts with expected bytes"
                ) from exc
            if existing != expected_content:
                raise PublicationConflictError(
                    "existing content-addressed evidence conflicts with expected bytes"
                )
    if all(states):
        return True
    if any(states):
        raise PartialPublicationError(
            "partial immutable publication residue exists; in-place repair is forbidden"
        )
    return False


def _parse_canonical_source(content: bytes) -> ReferenceSource:
    try:
        source = ReferenceSource.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        raise ArtifactCorruptionError("source evidence is not schema-valid") from exc
    if canonical_json_bytes(source) != content:
        raise ArtifactCorruptionError("source evidence is not canonical JSON")
    return source


def _parse_canonical_manifest(content: bytes) -> ReferenceManifest:
    try:
        manifest = ReferenceManifest.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        raise ArtifactCorruptionError("manifest evidence is not schema-valid") from exc
    if canonical_json_bytes(manifest) != content:
        raise ArtifactCorruptionError("manifest evidence is not canonical JSON")
    return manifest


def _verify_manifest_locked(
    root: Path,
    manifest: ReferenceManifest,
    *,
    expected_raw_bytes: bytes | None,
    caller_digest_matched: bool,
    persisted_manifest_content: bytes | None = None,
) -> tuple[IngestionReceipt, ReferenceMetadataProjection]:
    fingerprint = manifest.raw_artifact.fingerprint
    raw_bytes = read_confined_file(
        root,
        manifest.raw_artifact.relative_path,
        max_bytes=MAX_RAW_ARTIFACT_BYTES,
        expected_size=fingerprint.byte_count,
    )
    if expected_raw_bytes is not None and raw_bytes != expected_raw_bytes:
        raise ArtifactCorruptionError("raw artifact does not preserve the supplied exact bytes")
    if hashlib.sha256(raw_bytes).hexdigest() != fingerprint.sha256:
        raise ArtifactCorruptionError("raw artifact SHA-256 does not match the manifest")

    expected_source_content = source_record_bytes(manifest.source)
    source_content = read_confined_file(
        root,
        manifest.source_record_relative_path,
        max_bytes=_MAX_CANONICAL_METADATA_BYTES,
        expected_size=len(expected_source_content),
    )
    if hashlib.sha256(source_content).hexdigest() != manifest.source_record_sha256:
        raise ArtifactCorruptionError("source record SHA-256 does not match the manifest")
    if source_content != expected_source_content:
        raise ArtifactCorruptionError("source evidence does not match the manifest")
    source = _parse_canonical_source(source_content)

    expected_manifest_content = manifest_bytes(manifest)
    manifest_sha256 = hashlib.sha256(expected_manifest_content).hexdigest()
    manifest_path = manifest_relative_path(manifest_sha256)
    manifest_content = persisted_manifest_content
    if manifest_content is None:
        manifest_content = read_confined_file(
            root,
            manifest_path,
            max_bytes=_MAX_CANONICAL_METADATA_BYTES,
            expected_size=len(expected_manifest_content),
        )
    if manifest_content != expected_manifest_content:
        raise ArtifactCorruptionError("persisted manifest does not match expected evidence")
    persisted_manifest = _parse_canonical_manifest(manifest_content)
    if persisted_manifest != manifest or persisted_manifest.source != source:
        raise ArtifactCorruptionError("manifest source does not match source evidence")

    receipt = _build_verified_ingestion_receipt(
        manifest,
        caller_digest_supplied=caller_digest_matched,
    )
    return receipt, _project_verified_reference_metadata(manifest, receipt)


def _verify_receipt_locked(
    root: Path,
    receipt: IngestionReceipt,
) -> ReferenceMetadataProjection:
    manifest_content = read_confined_file(
        root,
        receipt.manifest_relative_path,
        max_bytes=_MAX_CANONICAL_METADATA_BYTES,
    )
    if hashlib.sha256(manifest_content).hexdigest() != receipt.manifest_sha256:
        raise ArtifactCorruptionError("manifest SHA-256 does not match the receipt")
    manifest = _parse_canonical_manifest(manifest_content)
    rebuilt, projection = _verify_manifest_locked(
        root,
        manifest,
        expected_raw_bytes=None,
        caller_digest_matched=(receipt.verification.caller_digest_status == "matched"),
        persisted_manifest_content=manifest_content,
    )
    if rebuilt != receipt:
        raise ArtifactCorruptionError("receipt does not reconcile with stored evidence")
    return projection


def ingest_reference(
    *,
    reference_store_root: str | os.PathLike[str],
    raw_bytes: bytes,
    source: ReferenceSource,
    expected_sha256: str | None = None,
) -> IngestionReceipt:
    """Publish reviewed bytes and metadata without decoding, parsing, or fetching."""

    # Exact-byte identity is computed before any possible raw-content processing.
    fingerprint = fingerprint_raw_bytes(raw_bytes)
    expected_sha256 = _validate_expected_sha256(expected_sha256)
    if expected_sha256 is not None and expected_sha256 != fingerprint.sha256:
        raise DigestMismatchError(
            "caller-provided SHA-256 does not match the exact supplied bytes"
        )
    if not isinstance(source, ReferenceSource):
        raise TypeError("source must be a validated ReferenceSource")

    manifest = build_reference_manifest(fingerprint, source)
    expected_files = _expected_files(raw_bytes, source, manifest)

    root = prepare_store_root(reference_store_root, create=True)
    with store_transaction(root, create_lock=True):
        repeated = _preflight_publication_state(root, expected_files)
        if not repeated:
            for relative_path, content in expected_files:
                create_confined_file(root, relative_path, content)
        receipt, _ = _verify_manifest_locked(
            root,
            manifest,
            expected_raw_bytes=raw_bytes,
            caller_digest_matched=expected_sha256 is not None,
        )
    return receipt


def verify_ingestion(
    *,
    reference_store_root: str | os.PathLike[str],
    receipt: IngestionReceipt,
) -> ReferenceMetadataProjection:
    """Reread and verify one immutable publication without exposing raw bytes."""

    if not isinstance(receipt, IngestionReceipt):
        raise TypeError("receipt must be an IngestionReceipt")
    root = prepare_store_root(reference_store_root, create=False)
    with store_transaction(root, create_lock=False):
        return _verify_receipt_locked(root, receipt)


__all__ = ["ingest_reference", "verify_ingestion"]
