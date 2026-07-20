from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from material_studio_mcp_server.reference_data import (
    canonical_sha256,
    ingest_reference,
    verify_ingestion,
)
from material_studio_mcp_server.reference_data.contracts import (
    ReferenceManifest,
    ReferenceSource,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
STORE_ROOT = ROOT / "benchmarks" / "references" / "development" / "sic_3c_bulk"

RAW_SHA256 = "7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb"
SOURCE_SHA256 = "31c04bc038b7d4ce3bfced24c189e1c2e3939ef23c4c7eae8d384cb80402ed6b"
MANIFEST_SHA256 = "97bf9304eeffad3bdbe1d58d272719dc1a33ee18660e9f06961ad30b917882b1"
RECEIPT_SHA256 = "43b75cb8d19616ddb6c2bc549cba6fcc19e1663fd56813d58c4063f2ca96f7dc"

RAW_RELATIVE_PATH = f"raw/sha256/7b/{RAW_SHA256}.bin"
SOURCE_RELATIVE_PATH = f"sources/sha256/31/{SOURCE_SHA256}.json"
MANIFEST_RELATIVE_PATH = f"manifests/sha256/97/{MANIFEST_SHA256}.json"


def _path(relative_path: str) -> Path:
    return STORE_ROOT.joinpath(*relative_path.split("/"))


def _manifest() -> ReferenceManifest:
    content = _path(MANIFEST_RELATIVE_PATH).read_bytes()
    assert hashlib.sha256(content).hexdigest() == MANIFEST_SHA256
    manifest = ReferenceManifest.model_validate_json(content)
    assert canonical_json_bytes(manifest) == content
    return manifest


def test_cod_1010995_raw_snapshot_has_exact_pinned_byte_identity() -> None:
    raw_bytes = _path(RAW_RELATIVE_PATH).read_bytes()
    assert len(raw_bytes) == 3387
    assert hashlib.sha256(raw_bytes).hexdigest() == RAW_SHA256


def test_cod_1010995_source_evidence_is_canonical_complete_and_cc0() -> None:
    content = _path(SOURCE_RELATIVE_PATH).read_bytes()
    assert hashlib.sha256(content).hexdigest() == SOURCE_SHA256
    source = ReferenceSource.model_validate_json(content)
    assert canonical_json_bytes(source) == content
    assert source.source_id == "cod-1010995"
    assert source.provider == "Crystallography Open Database"
    assert source.provider_record_id == "1010995"
    assert source.provider_revision == "278158"
    assert source.record_url == "https://www.crystallography.net/cod/1010995.html"
    assert (
        source.artifact_url
        == "https://www.crystallography.net/cod/1010995.cif@278158"
    )
    assert source.retrieval.retrieved_at == "2026-07-20T08:44:26.560631Z"
    assert source.retrieval.retrieval_purpose
    assert source.retrieval.query is None
    assert source.citation
    assert source.media_type == "chemical/x-cif"
    assert source.structure_format == "cif"
    assert source.license.name == "CC0 1.0 Universal"
    assert source.license.spdx_id == "CC0-1.0"
    assert source.license.url == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert source.license.redistributable is True


def test_cod_1010995_manifest_and_receipt_reconcile_exactly(tmp_path: Path) -> None:
    manifest = _manifest()
    assert manifest.source_record_sha256 == SOURCE_SHA256
    assert manifest.source_record_relative_path == SOURCE_RELATIVE_PATH
    assert manifest.raw_artifact.fingerprint.sha256 == RAW_SHA256
    assert manifest.raw_artifact.fingerprint.byte_count == 3387
    assert manifest.raw_artifact.relative_path == RAW_RELATIVE_PATH
    assert manifest.deduplication.basis == "exact_raw_byte_length_and_sha256"
    assert manifest.deduplication.cif_parsing_performed is False
    assert manifest.deduplication.canonicalization_performed is False
    assert manifest.deduplication.structural_equivalence_claimed is False

    receipt = ingest_reference(
        reference_store_root=tmp_path / "verified-copy",
        raw_bytes=_path(RAW_RELATIVE_PATH).read_bytes(),
        source=manifest.source,
        expected_sha256=RAW_SHA256,
    )
    assert receipt.manifest_sha256 == MANIFEST_SHA256
    assert receipt.manifest_relative_path == MANIFEST_RELATIVE_PATH
    assert canonical_sha256(receipt) == RECEIPT_SHA256
    projection = verify_ingestion(
        reference_store_root=tmp_path / "verified-copy",
        receipt=receipt,
    )
    assert projection.source_id == "cod-1010995"
    assert projection.raw_artifact_sha256 == RAW_SHA256


def test_real_snapshot_projection_discloses_no_raw_or_coordinate_content(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    receipt = ingest_reference(
        reference_store_root=tmp_path / "verified-copy",
        raw_bytes=_path(RAW_RELATIVE_PATH).read_bytes(),
        source=manifest.source,
        expected_sha256=RAW_SHA256,
    )
    projection = verify_ingestion(
        reference_store_root=tmp_path / "verified-copy",
        receipt=receipt,
    )
    projection_bytes = canonical_json_bytes(projection)
    raw_bytes = _path(RAW_RELATIVE_PATH).read_bytes()
    assert raw_bytes not in projection_bytes
    fields = set(projection.model_dump())
    assert not fields.intersection(
        {
            "raw_bytes",
            "atom_sites",
            "fractional_coordinates",
            "cartesian_coordinates",
            "coordinate_excerpt",
            "lattice_vectors",
            "lattice_parameters",
        }
    )


def test_exact_raw_snapshot_is_binary_under_git_clean_filters() -> None:
    attribute = subprocess.run(
        ["git", "check-attr", "text", "diff", "merge", "--", str(_path(RAW_RELATIVE_PATH))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = tuple(line.strip() for line in attribute.stdout.splitlines())
    assert any(line.endswith("text: unset") for line in lines)
    assert any(line.endswith("diff: unset") for line in lines)
    assert any(line.endswith("merge: unset") for line in lines)

    filtered = subprocess.run(
        ["git", "hash-object", "--path", RAW_RELATIVE_PATH, RAW_RELATIVE_PATH],
        cwd=STORE_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    unfiltered = subprocess.run(
        ["git", "hash-object", "--no-filters", RAW_RELATIVE_PATH],
        cwd=STORE_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert filtered == unfiltered


def test_fixture_is_development_evidence_not_validation_or_hidden_holdout() -> None:
    relative = STORE_ROOT.relative_to(ROOT).as_posix()
    assert relative == "benchmarks/references/development/sic_3c_bulk"
    assert "validation" not in relative.split("/")
    assert "hidden_holdout" not in relative.split("/")
