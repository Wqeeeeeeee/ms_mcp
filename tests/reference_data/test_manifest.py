from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.reference_data import (
    build_reference_manifest,
    canonical_sha256,
    compare_raw_bytes,
    fingerprint_raw_bytes,
    ingest_reference,
    verify_ingestion,
)
from material_studio_mcp_server.reference_data.contracts import ReferenceManifest
from material_studio_mcp_server.reference_data.manifest import (
    manifest_bytes,
    source_record_bytes,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from conftest import SYNTHETIC_RAW_BYTES


def _manifest(source: object) -> ReferenceManifest:
    return build_reference_manifest(
        fingerprint_raw_bytes(SYNTHETIC_RAW_BYTES),
        source,  # type: ignore[arg-type]
    )


def test_manifest_is_deterministic_canonical_json(
    reference_source: object,
) -> None:
    first_manifest = _manifest(reference_source)
    second_manifest = _manifest(reference_source)
    assert second_manifest == first_manifest
    assert manifest_bytes(first_manifest) == canonical_json_bytes(first_manifest)
    assert canonical_sha256(first_manifest) == canonical_sha256(second_manifest)


def test_receipt_is_independent_of_store_root_and_prior_publication_state(
    tmp_path: Path,
    reference_source: object,
) -> None:
    expected_sha256 = hashlib.sha256(SYNTHETIC_RAW_BYTES).hexdigest()
    roots = (tmp_path / "first", tmp_path / "second")
    receipts = [
        ingest_reference(
            reference_store_root=root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
            expected_sha256=expected_sha256,
        )
        for root in roots
    ]
    repeated = ingest_reference(
        reference_store_root=roots[0],
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
        expected_sha256=expected_sha256,
    )
    assert receipts[0] == receipts[1] == repeated
    serialized = canonical_json_bytes(repeated).decode("utf-8")
    assert str(tmp_path) not in serialized
    assert "pid" not in serialized.casefold()
    assert "uuid" not in serialized.casefold()


def test_manifest_binds_source_digest_retrieval_license_format_and_raw_path(
    reference_source: object,
) -> None:
    manifest = _manifest(reference_source)
    assert manifest.source_record_sha256 == hashlib.sha256(
        source_record_bytes(reference_source)  # type: ignore[arg-type]
    ).hexdigest()
    assert manifest.source.retrieval.retrieved_at == "2026-07-20T00:00:00Z"
    assert manifest.source.retrieval.retrieval_purpose
    assert manifest.source.license.spdx_id == "CC0-1.0"
    assert manifest.raw_artifact.structure_format == "cif"
    assert manifest.raw_artifact.media_type == "chemical/x-cif"
    assert manifest.raw_artifact.relative_path.endswith(
        f"/{manifest.raw_artifact.fingerprint.sha256}.bin"
    )
    assert manifest.source.provider_record_id not in manifest.raw_artifact.relative_path


def test_manifest_rejects_tampered_source_digest_and_paths(
    reference_source: object,
) -> None:
    manifest = _manifest(reference_source)
    payload = manifest.model_dump()
    payload["source_record_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        ReferenceManifest(**payload)

    payload = manifest.model_dump()
    payload["raw_artifact"]["relative_path"] = "raw/sha256/00/" + "0" * 64 + ".bin"
    with pytest.raises(ValidationError):
        ReferenceManifest(**payload)


def test_metadata_projection_is_closed_and_contains_no_structure_content(
    tmp_path: Path,
    reference_source: object,
) -> None:
    receipt = ingest_reference(
        reference_store_root=tmp_path / "verified-reference",
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    projection = verify_ingestion(
        reference_store_root=tmp_path / "verified-reference",
        receipt=receipt,
    )
    fields = set(projection.model_dump())
    assert fields == {
        "contract_version",
        "projection_profile",
        "source_id",
        "provider",
        "provider_record_id",
        "provider_revision",
        "record_url",
        "artifact_url",
        "retrieved_at",
        "retrieval_purpose",
        "query",
        "citation",
        "license_name",
        "license_spdx_id",
        "license_url",
        "redistributable",
        "media_type",
        "structure_format",
        "source_record_sha256",
        "source_record_relative_path",
        "raw_artifact_sha256",
        "raw_artifact_byte_count",
        "raw_artifact_relative_path",
        "manifest_sha256",
        "manifest_relative_path",
    }
    serialized = canonical_json_bytes(projection)
    assert SYNTHETIC_RAW_BYTES not in serialized
    lowered_fields = {field.casefold() for field in fields}
    for forbidden in (
        "raw_bytes",
        "atom_sites",
        "fractional_coordinates",
        "cartesian_coordinates",
        "coordinate_excerpt",
        "lattice_vectors",
        "lattice_parameters",
    ):
        assert forbidden not in lowered_fields


def test_exact_raw_bytes_are_deduplicated_without_cif_equivalence_claim() -> None:
    result = compare_raw_bytes(SYNTHETIC_RAW_BYTES, SYNTHETIC_RAW_BYTES)
    assert result.status == "exact_raw_duplicate"
    assert result.duplicate is True
    assert result.byte_count_match is True
    assert result.sha256_match is True
    assert result.cif_parsing_performed is False
    assert result.canonicalization_performed is False
    assert result.structural_equivalence_claimed is False


def test_byte_different_content_is_not_deduplicated_or_canonicalized() -> None:
    result = compare_raw_bytes(b"same text\r\n", b"same text\n")
    assert result.status == "byte_different_unresolved"
    assert result.duplicate is False
    assert result.byte_count_match is False
    assert result.sha256_match is False
    assert result.cif_parsing_performed is False
    assert result.canonicalization_performed is False
    assert result.structural_equivalence_claimed is False
