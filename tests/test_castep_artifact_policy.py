from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import material_studio_mcp_server.castep_artifact_policy as artifact_policy
from material_studio_mcp_server.castep_artifact_policy import (
    CASTEP_ENERGY_ARTIFACT_POLICY_SCHEMA,
    CastepEnergyArtifactPolicyReceipt,
    validate_castep_energy_artifact_policy,
)


def _artifact(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _energy_contract(
    tmp_path: Path,
    *,
    include_bands: bool = True,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = tmp_path / "native_artifacts"
    root.mkdir()
    castep = root / "input.castep"
    castep.write_text("synthetic completed output\n", encoding="ascii")
    manifest = [_artifact(castep)]
    bands_summary: dict[str, Any] | None = None
    if include_bands:
        bands = root / "input.bands"
        bands.write_text("synthetic fermi-referenced bands\n", encoding="ascii")
        bands_artifact = _artifact(bands)
        manifest.append(bands_artifact)
        bands_summary = {
            "source_path": bands_artifact["path"],
            "source_sha256": bands_artifact["sha256"],
            "source_size_bytes": bands_artifact["size_bytes"],
        }
    manifest.sort(key=lambda item: str(item["path"]).casefold())
    audit = {
        "schema_version": "synthetic_native_audit_v1",
        "status": "complete",
        "task": "Energy",
        "native_artifact_count": len(manifest),
        "native_band_kpoint_path_exported": False,
        "numeric_curve_data_exported": False,
        "numeric_curve_kind": None,
        "derived_artifact_count": 0,
        "derived_artifacts": [],
        "scientific_band_gap_verified": False,
        "scientific_convergence_verified": False,
        "bands_summary": bands_summary,
    }
    receipt = {
        "task": "Energy",
        "backend_run_completed": True,
        "calculation_result_verified": True,
        "native_artifact_count": len(manifest),
        "native_artifacts": manifest,
        "native_output_audit": audit,
        "native_band_kpoint_path_exported": False,
        "numeric_curve_data_exported": False,
        "numeric_curve_kind": None,
        "derived_artifact_count": 0,
        "derived_artifacts": [],
        "scientific_band_gap_claimed": False,
        "scientific_band_gap_verified": False,
        "scientific_convergence_claimed": False,
        "scientific_convergence_verified": False,
    }
    summary = {
        "status": "verified",
        "binding_verified": True,
        "task": "Energy",
        "native_artifact_count": len(manifest),
        "native_output_audit": copy.deepcopy(audit),
        "native_band_kpoint_path_exported": False,
        "numeric_curve_data_exported": False,
        "numeric_curve_kind": None,
        "derived_artifact_count": 0,
        "derived_artifacts": [],
        "scientific_band_gap_verified": False,
        "scientific_convergence_verified": False,
    }
    return root, receipt, summary


def _sync_audit(
    receipt: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    summary["native_output_audit"] = copy.deepcopy(
        receipt["native_output_audit"]
    )


def _validate(
    root: Path,
    receipt: dict[str, Any],
    summary: dict[str, Any],
) -> CastepEnergyArtifactPolicyReceipt:
    spec = SimpleNamespace(
        metadata={"last_castep_electronic_calculation": receipt}
    )
    with patch(
        "material_studio_mcp_server.castep_electronic."
        "verify_castep_electronic_receipt",
        return_value=summary,
    ):
        return validate_castep_energy_artifact_policy(
            spec=spec,
            native_artifact_root=root,
        )


def test_energy_policy_accepts_manifest_bound_standard_native_bands(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)

    result = _validate(root, receipt, summary)
    repeated = _validate(root, receipt, summary)

    assert result == repeated
    assert result.schema_version == CASTEP_ENERGY_ARTIFACT_POLICY_SCHEMA
    assert result.status == "PASS"
    assert result.reason_codes == ()
    assert result.task == "Energy"
    assert result.verified_electronic_receipt is True
    assert result.native_artifact_count == 2
    assert result.native_bands_artifact_count == 1
    assert [item.relative_path for item in result.native_bands_artifacts] == [
        "input.bands"
    ]
    assert result.native_band_kpoint_path_exported is False
    assert result.numeric_curve_data_exported is False
    assert result.numeric_curve_kind is None
    assert result.derived_artifact_count == 0
    assert result.scientific_band_gap_claimed is False
    assert result.scientific_band_gap_verified is False
    assert result.scientific_convergence_claimed is False
    assert result.scientific_convergence_verified is False
    payload = result.model_dump_json()
    assert str(tmp_path) not in payload
    assert "synthetic fermi-referenced bands" not in payload


def test_energy_policy_accepts_no_native_bands_when_semantics_are_clean(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(
        tmp_path,
        include_bands=False,
    )

    result = _validate(root, receipt, summary)

    assert result.status == "PASS"
    assert result.native_bands_artifact_count == 0
    assert result.native_bands_artifacts == ()


def test_energy_policy_rejects_unmanifested_native_bands(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(
        tmp_path,
        include_bands=False,
    )
    (root / "unbound.bands").write_text("unbound\n", encoding="ascii")

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_bands_manifest_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("sha256", "native_artifact_sha256_mismatch"),
        ("size", "native_artifact_size_mismatch"),
        ("duplicate", "native_artifact_duplicate"),
    ],
)
def test_energy_policy_rejects_invalid_manifest_bindings(
    tmp_path: Path,
    mutation: str,
    expected_reason: str,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    bands = next(
        item
        for item in receipt["native_artifacts"]
        if Path(item["path"]).suffix == ".bands"
    )
    if mutation == "sha256":
        bands["sha256"] = "0" * 64
    elif mutation == "size":
        bands["size_bytes"] += 1
    else:
        receipt["native_artifacts"].append(copy.deepcopy(bands))
        receipt["native_artifact_count"] += 1
        summary["native_artifact_count"] += 1
        receipt["native_output_audit"]["native_artifact_count"] += 1
        _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert expected_reason in result.reason_codes


def test_energy_policy_rejects_manifest_path_outside_native_root(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(
        tmp_path,
        include_bands=False,
    )
    outside = tmp_path / "outside.bands"
    outside.write_text("outside\n", encoding="ascii")
    receipt["native_artifacts"].append(_artifact(outside))
    receipt["native_artifact_count"] += 1
    summary["native_artifact_count"] += 1
    receipt["native_output_audit"]["native_artifact_count"] += 1
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_path_outside_root" in result.reason_codes


def test_energy_policy_rejects_relative_manifest_path(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    receipt["native_artifacts"][0]["path"] = "input.castep"

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_manifest_invalid" in result.reason_codes


def test_energy_policy_rejects_multiply_linkable_artifact(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    bands = next(
        Path(item["path"])
        for item in receipt["native_artifacts"]
        if Path(item["path"]).suffix == ".bands"
    )
    try:
        os.link(bands, tmp_path / "second-link.bands")
    except OSError:
        pytest.skip("hard links are unavailable")

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_binding_unstable" in result.reason_codes


def test_energy_policy_rejects_ambiguous_native_bands(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    second = root / "second.bands"
    second.write_text("second\n", encoding="ascii")
    receipt["native_artifacts"].append(_artifact(second))
    receipt["native_artifact_count"] += 1
    summary["native_artifact_count"] += 1
    receipt["native_output_audit"]["native_artifact_count"] += 1
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_bands_artifact_ambiguous" in result.reason_codes


def test_energy_policy_rejects_bands_audit_binding_mismatch(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    receipt["native_output_audit"]["bands_summary"]["source_sha256"] = "0" * 64
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_bands_audit_binding_mismatch" in result.reason_codes


def test_energy_policy_rejects_bandstructure_task(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    receipt["task"] = "BandStructure"
    receipt["native_output_audit"]["task"] = "BandStructure"
    summary["task"] = "BandStructure"
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "energy_task_required" in result.reason_codes
    assert result.task is None


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        (
            "native_band_kpoint_path_exported",
            True,
            "band_kpoint_path_exported",
        ),
        ("numeric_curve_data_exported", True, "numeric_curve_exported"),
        ("numeric_curve_kind", "native_bands", "numeric_curve_kind_present"),
        (
            "scientific_band_gap_verified",
            True,
            "scientific_band_gap_verified",
        ),
        (
            "scientific_convergence_verified",
            True,
            "scientific_convergence_verified",
        ),
    ],
)
def test_energy_policy_rejects_forbidden_receipt_and_audit_semantics(
    tmp_path: Path,
    field: str,
    value: object,
    expected_reason: str,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    receipt[field] = value
    summary[field] = value
    receipt["native_output_audit"][field] = value
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert expected_reason in result.reason_codes
    if field == "numeric_curve_kind":
        assert result.numeric_curve_kind is None


@pytest.mark.parametrize(
    ("field", "expected_reason"),
    [
        ("scientific_band_gap_claimed", "scientific_band_gap_claimed"),
        (
            "scientific_convergence_claimed",
            "scientific_convergence_claimed",
        ),
    ],
)
@pytest.mark.parametrize("source", ["receipt", "summary", "audit"])
def test_energy_policy_rejects_scientific_claims(
    tmp_path: Path,
    field: str,
    expected_reason: str,
    source: str,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    if source == "receipt":
        receipt[field] = True
    elif source == "summary":
        summary[field] = True
    else:
        receipt["native_output_audit"][field] = True
        _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert expected_reason in result.reason_codes


def test_energy_policy_rejects_derived_artifacts(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    derived = [{"artifact_kind": "castep_band_eigenvalues_csv"}]
    receipt["derived_artifacts"] = copy.deepcopy(derived)
    receipt["derived_artifact_count"] = 1
    summary["derived_artifacts"] = copy.deepcopy(derived)
    summary["derived_artifact_count"] = 1
    receipt["native_output_audit"]["derived_artifacts"] = copy.deepcopy(
        derived
    )
    receipt["native_output_audit"]["derived_artifact_count"] = 1
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "derived_artifacts_present" in result.reason_codes


def test_energy_policy_requires_verified_electronic_receipt(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    summary["binding_verified"] = False
    summary["status"] = "binding_mismatch"

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "verified_electronic_receipt_required" in result.reason_codes


def test_energy_policy_rejects_non_canonical_receipt(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    receipt["non_finite"] = float("nan")

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "non_canonical_receipt_or_summary" in result.reason_codes
    assert result.electronic_receipt_sha256 is None


def test_energy_policy_never_echoes_rejected_input_strings(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    untrusted_task = "Energy with fractional coordinates"
    untrusted_kind = "C:\\private\\raw-native-output"
    receipt["task"] = untrusted_task
    receipt["numeric_curve_kind"] = untrusted_kind
    receipt["native_output_audit"]["task"] = untrusted_task
    receipt["native_output_audit"]["numeric_curve_kind"] = untrusted_kind
    summary["task"] = untrusted_task
    summary["numeric_curve_kind"] = untrusted_kind
    _sync_audit(receipt, summary)

    result = _validate(root, receipt, summary)
    payload = result.model_dump_json()

    assert result.status == "FAIL"
    assert result.task is None
    assert result.numeric_curve_kind is None
    assert untrusted_task not in payload
    assert untrusted_kind not in payload


def test_energy_policy_fails_when_artifact_set_changes_during_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    monkeypatch.setattr(
        artifact_policy,
        "_artifact_set_still_stable",
        lambda *_args, **_kwargs: False,
    )

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_binding_unstable" in result.reason_codes


@pytest.mark.parametrize("mutation_scan_number", [1, 2])
def test_energy_policy_rejects_late_bands_in_existing_subdirectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_scan_number: int,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    nested = root / "existing"
    nested.mkdir()
    original_scan = artifact_policy._scan_artifact_tree
    scan_count = 0

    def add_after_first_scan(path: Path) -> object:
        nonlocal scan_count
        snapshot = original_scan(path)
        scan_count += 1
        if scan_count == mutation_scan_number:
            (nested / "late.bands").write_text("late\n", encoding="ascii")
        return snapshot

    monkeypatch.setattr(
        artifact_policy,
        "_scan_artifact_tree",
        add_after_first_scan,
    )

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_binding_unstable" in result.reason_codes


def test_energy_policy_does_not_follow_directory_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    link = root / "linked"
    link.mkdir()
    (link / "unbound.bands").write_text("outside\n", encoding="ascii")
    link_identity = artifact_policy._stat_identity(
        link.stat(follow_symlinks=False)
    )
    original_is_reparse = artifact_policy._is_reparse
    original_scandir = artifact_policy.os.scandir
    scanned_directories: list[Path] = []

    def classify_link(value: os.stat_result) -> bool:
        return (
            artifact_policy._stat_identity(value) == link_identity
            or original_is_reparse(value)
        )

    def record_scandir(path: Path) -> os.ScandirIterator[str]:
        scanned_directories.append(Path(path))
        return original_scandir(path)

    monkeypatch.setattr(artifact_policy, "_is_reparse", classify_link)
    monkeypatch.setattr(artifact_policy.os, "scandir", record_scandir)

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_root_scan_failed" in result.reason_codes
    assert root in scanned_directories
    assert link not in scanned_directories


def test_windows_directory_handle_blocks_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        return
    root, receipt, summary = _energy_contract(tmp_path)
    nested = root / "existing"
    nested.mkdir()
    original_scandir = artifact_policy.os.scandir
    replacement_attempts: list[str] = []

    def attempt_replacement(path: Path) -> os.ScandirIterator[str]:
        target = Path(path)
        if target == nested:
            moved = nested.with_name("replacement-target")
            try:
                nested.rename(moved)
            except PermissionError:
                replacement_attempts.append("blocked")
            else:
                replacement_attempts.append("replaced")
                moved.rename(nested)
        return original_scandir(path)

    monkeypatch.setattr(
        artifact_policy.os,
        "scandir",
        attempt_replacement,
    )

    result = _validate(root, receipt, summary)

    assert result.status == "PASS"
    assert replacement_attempts
    assert set(replacement_attempts) == {"blocked"}


def test_windows_artifact_handles_block_concurrent_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        return
    root, receipt, summary = _energy_contract(tmp_path)
    original_stability_check = artifact_policy._artifact_set_still_stable
    write_attempts: list[str] = []

    def probe_write_sharing(*args: object, **kwargs: object) -> bool:
        artifacts = kwargs["artifacts"]
        assert isinstance(artifacts, list)
        for artifact in artifacts:
            try:
                with artifact.path.open("r+b"):
                    write_attempts.append("allowed")
            except PermissionError:
                write_attempts.append("blocked")
        return original_stability_check(*args, **kwargs)

    monkeypatch.setattr(
        artifact_policy,
        "_artifact_set_still_stable",
        probe_write_sharing,
    )

    result = _validate(root, receipt, summary)

    assert result.status == "PASS"
    assert write_attempts
    assert set(write_attempts) == {"blocked"}


def test_energy_policy_rehashes_open_artifacts_before_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    original_digest = artifact_policy._descriptor_sha256
    seen_descriptors: set[int] = set()

    def disagree_on_second_read(descriptor: int) -> str:
        digest = original_digest(descriptor)
        if descriptor in seen_descriptors:
            return "0" * 64
        seen_descriptors.add(descriptor)
        return digest

    monkeypatch.setattr(
        artifact_policy,
        "_descriptor_sha256",
        disagree_on_second_read,
    )

    result = _validate(root, receipt, summary)

    assert result.status == "FAIL"
    assert "native_artifact_binding_unstable" in result.reason_codes


def test_energy_policy_calls_the_official_receipt_verifier(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    spec = SimpleNamespace(
        metadata={"last_castep_electronic_calculation": receipt}
    )
    with patch(
        "material_studio_mcp_server.castep_electronic."
        "verify_castep_electronic_receipt",
        return_value=summary,
    ) as verifier:
        result = validate_castep_energy_artifact_policy(
            spec=spec,
            native_artifact_root=root,
        )

    assert result.status == "PASS"
    verifier.assert_called_once_with(spec)


def test_energy_policy_binds_receipt_before_and_after_verification(
    tmp_path: Path,
) -> None:
    root, receipt, summary = _energy_contract(tmp_path)
    spec = SimpleNamespace(
        metadata={"last_castep_electronic_calculation": receipt}
    )

    def mutate_receipt(_spec: object) -> dict[str, Any]:
        receipt["calculation_result_verified"] = False
        return summary

    with patch(
        "material_studio_mcp_server.castep_electronic."
        "verify_castep_electronic_receipt",
        side_effect=mutate_receipt,
    ):
        result = validate_castep_energy_artifact_policy(
            spec=spec,
            native_artifact_root=root,
        )

    assert result.status == "FAIL"
    assert "receipt_changed_during_verification" in result.reason_codes
    assert "verified_electronic_receipt_required" in result.reason_codes


def test_policy_receipt_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CastepEnergyArtifactPolicyReceipt.model_validate(
            {
                "status": "FAIL",
                "reason_codes": ["synthetic_failure"],
                "task": None,
                "verified_electronic_receipt": False,
                "electronic_receipt_sha256": None,
                "verified_summary_sha256": None,
                "native_artifact_count": 0,
                "native_bands_artifact_count": 0,
                "derived_artifact_count": None,
                "native_band_kpoint_path_exported": None,
                "numeric_curve_data_exported": None,
                "numeric_curve_kind": None,
                "scientific_band_gap_claimed": None,
                "scientific_band_gap_verified": None,
                "scientific_convergence_claimed": None,
                "scientific_convergence_verified": None,
                "native_artifact_manifest_sha256": None,
                "native_artifacts": [],
                "native_bands_artifacts": [],
                "unexpected": True,
            }
        )
