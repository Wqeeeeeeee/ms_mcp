"""Deterministic manifest, receipt, and safe projection construction."""

from __future__ import annotations

import hashlib

from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from .contracts import (
    REFERENCE_CONTRACT_VERSION,
    REFERENCE_MANIFEST_PROFILE,
    REFERENCE_METADATA_PROFILE,
    REFERENCE_RECEIPT_PROFILE,
    IngestionReceipt,
    IngestionVerification,
    RawArtifactFingerprint,
    RawArtifactRecord,
    RawDeduplicationBoundary,
    ReferenceManifest,
    ReferenceMetadataProjection,
    ReferenceSource,
    manifest_relative_path,
    raw_artifact_relative_path,
    source_record_relative_path,
)


def canonical_sha256(value: object) -> str:
    """Hash one value under the frozen runtime canonical JSON profile."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def source_record_bytes(source: ReferenceSource) -> bytes:
    if not isinstance(source, ReferenceSource):
        raise TypeError("source must be a ReferenceSource")
    return canonical_json_bytes(source)


def manifest_bytes(manifest: ReferenceManifest) -> bytes:
    if not isinstance(manifest, ReferenceManifest):
        raise TypeError("manifest must be a ReferenceManifest")
    return canonical_json_bytes(manifest)


def build_reference_manifest(
    fingerprint: RawArtifactFingerprint,
    source: ReferenceSource,
) -> ReferenceManifest:
    """Build one canonical raw-only manifest without filesystem access."""

    if not isinstance(fingerprint, RawArtifactFingerprint):
        raise TypeError("fingerprint must be a RawArtifactFingerprint")
    if not isinstance(source, ReferenceSource):
        raise TypeError("source must be a ReferenceSource")
    source_sha256 = hashlib.sha256(source_record_bytes(source)).hexdigest()
    return ReferenceManifest(
        contract_version=REFERENCE_CONTRACT_VERSION,
        manifest_profile=REFERENCE_MANIFEST_PROFILE,
        source=source,
        source_record_sha256=source_sha256,
        source_record_relative_path=source_record_relative_path(source_sha256),
        raw_artifact=RawArtifactRecord(
            fingerprint=fingerprint,
            media_type=source.media_type,
            structure_format=source.structure_format,
            relative_path=raw_artifact_relative_path(fingerprint.sha256),
        ),
        deduplication=RawDeduplicationBoundary(
            basis="exact_raw_byte_length_and_sha256",
            cif_parsing_performed=False,
            canonicalization_performed=False,
            structural_equivalence_claimed=False,
        ),
    )


def _build_verified_ingestion_receipt(
    manifest: ReferenceManifest,
    *,
    caller_digest_supplied: bool,
) -> IngestionReceipt:
    """Build a receipt only after the store has verified persisted evidence."""

    if not isinstance(manifest, ReferenceManifest):
        raise TypeError("manifest must be a ReferenceManifest")
    if type(caller_digest_supplied) is not bool:
        raise TypeError("caller_digest_supplied must be a strict boolean")
    manifest_sha256 = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
    fingerprint = manifest.raw_artifact.fingerprint
    return IngestionReceipt(
        contract_version=REFERENCE_CONTRACT_VERSION,
        receipt_profile=REFERENCE_RECEIPT_PROFILE,
        source_id=manifest.source.source_id,
        source_record_sha256=manifest.source_record_sha256,
        source_record_relative_path=manifest.source_record_relative_path,
        raw_artifact_sha256=fingerprint.sha256,
        raw_artifact_byte_count=fingerprint.byte_count,
        raw_artifact_relative_path=manifest.raw_artifact.relative_path,
        manifest_sha256=manifest_sha256,
        manifest_relative_path=manifest_relative_path(manifest_sha256),
        verification=IngestionVerification(
            exact_bytes_hashed_before_processing=True,
            raw_bytes_reread_and_matched=True,
            caller_digest_status=("matched" if caller_digest_supplied else "not_supplied"),
            source_license_query_complete=True,
            retrieval_context_secret_free=True,
            content_addressed_paths=True,
            create_only_publication=True,
            root_confinement_verified=True,
            raw_bytes_disclosed=False,
            atom_sites_disclosed=False,
            coordinates_disclosed=False,
            lattice_values_derived=False,
            cif_parsing_performed=False,
            canonicalization_performed=False,
            structural_equivalence_claimed=False,
        ),
    )


def _project_verified_reference_metadata(
    manifest: ReferenceManifest,
    receipt: IngestionReceipt,
) -> ReferenceMetadataProjection:
    """Return the allowlisted coordinate-free projection of a verified manifest."""

    expected_receipt = _build_verified_ingestion_receipt(
        manifest,
        caller_digest_supplied=(receipt.verification.caller_digest_status == "matched"),
    )
    if expected_receipt != receipt:
        raise ValueError("receipt does not bind the supplied manifest")
    source = manifest.source
    return ReferenceMetadataProjection(
        contract_version=REFERENCE_CONTRACT_VERSION,
        projection_profile=REFERENCE_METADATA_PROFILE,
        source_id=source.source_id,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
        provider_revision=source.provider_revision,
        record_url=source.record_url,
        artifact_url=source.artifact_url,
        retrieved_at=source.retrieval.retrieved_at,
        retrieval_purpose=source.retrieval.retrieval_purpose,
        query=source.retrieval.query,
        citation=source.citation,
        license_name=source.license.name,
        license_spdx_id=source.license.spdx_id,
        license_url=source.license.url,
        redistributable=source.license.redistributable,
        media_type=source.media_type,
        structure_format=source.structure_format,
        source_record_sha256=manifest.source_record_sha256,
        source_record_relative_path=manifest.source_record_relative_path,
        raw_artifact_sha256=receipt.raw_artifact_sha256,
        raw_artifact_byte_count=receipt.raw_artifact_byte_count,
        raw_artifact_relative_path=receipt.raw_artifact_relative_path,
        manifest_sha256=receipt.manifest_sha256,
        manifest_relative_path=receipt.manifest_relative_path,
    )


__all__ = [
    "build_reference_manifest",
    "canonical_sha256",
    "manifest_bytes",
    "source_record_bytes",
]
