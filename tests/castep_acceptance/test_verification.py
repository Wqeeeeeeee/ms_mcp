from __future__ import annotations

import copy
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.castep_acceptance import (
    project_real_evidence,
    validate_evidence_projection,
    write_external_evidence,
)
from material_studio_mcp_server.castep_acceptance.contracts import (
    CastepAcceptanceEvidence,
    FixedCastepProfile,
)
from material_studio_mcp_server.castep_acceptance.evidence import (
    canonical_evidence_sha256,
)
from material_studio_mcp_server.state.execution import canonical_json_sha256

from ._helpers import (
    run_fake_acceptance,
    synthetic_benchmark_acceptance,
    synthetic_verification,
)


def test_existing_receipt_native_scf_and_attempt_verifiers_all_bind(
    monkeypatch,
    tmp_path,
) -> None:
    result, _runner, _gui = run_fake_acceptance(monkeypatch, tmp_path)
    report = result.verification
    assert report.revision_execution_lock_verified is True
    assert report.execution_attempt_event_types == ("started", "completed")
    assert report.execution_attempt_binding_verified is True
    assert report.electronic_receipt_binding_verified is True
    assert report.native_castep_file_count == 1
    assert not tuple(result.workspace_root.rglob("*.bands"))
    assert report.native_scf_status == "completed_below_max_cycles"
    assert report.native_scf_audit_valid is True
    assert report.total_energy_finite is True
    assert report.total_energy_kcal_per_mol == -101.25
    assert report.scientific_convergence_verified is False
    assert report.scientifically_verified is False


def test_coordinate_free_evidence_rejects_tampering_and_machine_local_values() -> None:
    evidence = project_real_evidence(
        verification=synthetic_verification(real=True),
        benchmark_acceptance=synthetic_benchmark_acceptance(),
    )
    digest = canonical_evidence_sha256(evidence)
    payload = evidence.model_dump(mode="json")
    assert "process_inventory_sha256_before_after" in payload["verification"]["gui"]
    assert "window_inventory_sha256_before_after" in payload["verification"]["gui"]
    assert validate_evidence_projection(
        payload,
        expected_canonical_sha256=digest,
    ) == evidence

    tampered = copy.deepcopy(payload)
    tampered["verification"]["total_energy_kcal_per_mol"] = -99.0
    with pytest.raises(ValueError, match="cross-bound calculation PASS"):
        validate_evidence_projection(
            tampered,
            expected_canonical_sha256=digest,
        )

    local_path = copy.deepcopy(payload)
    local_path["verification"]["native_scf_status"] = (
        r"C:\\Users\\local\\native.castep"
    )
    local_path["benchmark_acceptance"]["calculation_evidence_sha256"] = (
        canonical_json_sha256(local_path["verification"])
    )
    with pytest.raises(ValueError, match="machine-local string"):
        validate_evidence_projection(
            local_path,
            expected_canonical_sha256=canonical_json_sha256(local_path),
        )


def test_evidence_contract_cross_binds_verification_and_benchmark() -> None:
    verification = synthetic_verification(real=True)
    benchmark = synthetic_benchmark_acceptance()
    with pytest.raises(ValidationError, match="cross-bound calculation PASS"):
        CastepAcceptanceEvidence(
            profile=FixedCastepProfile(),
            verification=verification,
            benchmark_acceptance=benchmark.model_copy(
                update={"calculation_evidence_sha256": "0" * 64}
            ),
        )

    failed_verification = verification.model_copy(
        update={"status": "FAIL", "failure_codes": ("coordinated_tamper",)}
    )
    failed_states = benchmark.states.model_copy(
        update={"calculation_evidence_valid": "FAIL"}
    )
    coordinated_benchmark = benchmark.model_copy(
        update={
            "states": failed_states,
            "overall_status": "FAIL",
            "real_castep": "FAIL",
            "calculation_evidence_sha256": canonical_json_sha256(
                failed_verification.model_dump(mode="json")
            ),
        }
    )
    with pytest.raises(ValidationError, match="cross-bound calculation PASS"):
        CastepAcceptanceEvidence(
            profile=FixedCastepProfile(),
            verification=failed_verification,
            benchmark_acceptance=coordinated_benchmark,
        )

    forged_verification = verification.model_copy(update={"runner_success": False})
    forged_benchmark = benchmark.model_copy(
        update={
            "calculation_evidence_sha256": canonical_json_sha256(
                forged_verification.model_dump(mode="json")
            )
        }
    )
    with pytest.raises(ValidationError, match="PASS requires every real acceptance check"):
        CastepAcceptanceEvidence(
            profile=FixedCastepProfile(),
            verification=forged_verification,
            benchmark_acceptance=forged_benchmark,
        )


def test_external_evidence_write_is_atomic_bound_and_no_clobber(tmp_path) -> None:
    evidence = project_real_evidence(
        verification=synthetic_verification(real=True),
        benchmark_acceptance=synthetic_benchmark_acceptance(),
    )
    output = tmp_path / "coordinate-free-castep-evidence.json"
    digest = write_external_evidence(output, evidence)
    persisted = json.loads(output.read_text(encoding="ascii"))
    assert digest == canonical_evidence_sha256(evidence)
    assert validate_evidence_projection(
        persisted,
        expected_canonical_sha256=digest,
    ) == evidence
    with pytest.raises(ValueError, match="already exists"):
        write_external_evidence(output, evidence)


def test_concurrent_external_evidence_publication_has_one_winner(tmp_path) -> None:
    evidence = project_real_evidence(
        verification=synthetic_verification(real=True),
        benchmark_acceptance=synthetic_benchmark_acceptance(),
    )
    output = tmp_path / "concurrent-castep-evidence.json"

    def publish():
        try:
            return write_external_evidence(output, evidence)
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _index: publish(), range(2)))
    assert sum(isinstance(item, str) for item in outcomes) == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1
    assert output.is_file()


def test_external_evidence_rejects_symlink_or_reparse_parent(tmp_path) -> None:
    evidence = project_real_evidence(
        verification=synthetic_verification(real=True),
        benchmark_acceptance=synthetic_benchmark_acceptance(),
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks are unavailable: {exc}")
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(linked_parent), str(real_parent)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory links and junctions are unavailable")

    with pytest.raises(ValueError, match="unsafe component"):
        write_external_evidence(linked_parent / "evidence.json", evidence)
    assert not (real_parent / "evidence.json").exists()
