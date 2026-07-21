from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import material_studio_mcp_server.ms_roundtrip.contracts as contracts
from material_studio_mcp_server.benchmark_evaluation import FiveValidityStates
from material_studio_mcp_server.ms_roundtrip import (
    CONTRACT_VERSION,
    IMPLEMENTATION_VERSION,
    CandidateBinding,
    CandidateValidationReceipt,
    MaterialsStudioRoundtripAdapter,
    RoundtripBenchmarkAcceptance,
    RoundtripExecutionResult,
    RoundtripRequest,
    RoundtripThresholds,
    RunArtifactDigest,
    RunnerExecutionReceipt,
)


HASH = "a" * 64


def test_contract_and_implementation_versions_are_frozen() -> None:
    assert CONTRACT_VERSION == "1.0.0"
    assert IMPLEMENTATION_VERSION == "1.0.0"
    assert RoundtripThresholds().model_dump() == {
        "mapping_coverage": 1.0,
        "rms_displacement_angstrom": 0.05,
        "maximum_displacement_angstrom": 0.15,
        "maximum_relative_lattice_error": 0.001,
        "vacuum_absolute_error_angstrom": 0.1,
        "inclusive_lte_boundaries": True,
    }


def test_all_package_contracts_are_strict_frozen_and_extra_forbidden() -> None:
    model_classes = {
        value
        for value in vars(contracts).values()
        if isinstance(value, type)
        and issubclass(value, contracts.RoundtripContractModel)
    }
    assert len(model_classes) >= 15
    for model_class in model_classes:
        assert model_class.model_config["strict"] is True
        assert model_class.model_config["frozen"] is True
        assert model_class.model_config["extra"] == "forbid"
        assert model_class.model_config["allow_inf_nan"] is False


def test_request_rejects_extra_fields_and_is_frozen(tmp_path: Path) -> None:
    candidate = CandidateBinding(
        structure_path=tmp_path / "candidate.cif",
        expected_structure_sha256=HASH,
    )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RoundtripRequest(
            request_id="request-01",
            run_id="run-01",
            candidate=candidate,
            output_root=tmp_path,
            unexpected=True,
        )
    request = RoundtripRequest(
        request_id="request-01",
        run_id="run-01",
        candidate=candidate,
        output_root=tmp_path,
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        request.execution_mode = "execute"


def test_strict_literals_do_not_accept_boolean_or_unknown_execution_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        CandidateBinding(
            revision=True,
            structure_path=tmp_path / "candidate.cif",
            expected_structure_sha256=HASH,
        )
    with pytest.raises(ValidationError):
        RoundtripRequest(
            request_id="request-01",
            run_id="run-01",
            candidate=CandidateBinding(
                structure_path=tmp_path / "candidate.cif",
                expected_structure_sha256=HASH,
            ),
            output_root=tmp_path,
            execution_mode="Execute",
        )


def test_nonfinite_numbers_and_unconfined_relative_paths_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateValidationReceipt(
            plugin_id="sic_3c_001_si_face_surface",
            plugin_contract_version="1.0.0",
            plugin_implementation_version="1.0.0",
            fixed_candidate_match=True,
            canonical_structure_sha256=HASH,
            expected_canonical_structure_sha256=HASH,
            atom_count=80,
            composition=("C:32", "H:16", "Si:32"),
            vacuum_angstrom=float("nan"),
            mapping_coverage=1.0,
            maximum_displacement_angstrom=0.0,
            maximum_relative_lattice_error=0.0,
        )
    for relative in ("../receipt.json", "/receipt.json", "a\\receipt.json"):
        with pytest.raises(ValidationError):
            RunArtifactDigest(
                role="result_receipt",
                relative_path=relative,
                sha256=HASH,
                byte_count=1,
            )


def test_frozen_thresholds_cannot_be_weakened() -> None:
    with pytest.raises(ValidationError):
        RoundtripThresholds(rms_displacement_angstrom=0.051)
    with pytest.raises(ValidationError):
        RoundtripThresholds(vacuum_absolute_error_angstrom=0.11)


def _runner_execution(**updates: object) -> RunnerExecutionReceipt:
    values: dict[str, object] = {
        "success": True,
        "timed_out": False,
        "return_code": 0,
        "duration_seconds": 0.1,
        "command_sha256": HASH,
        "stdout_sha256": HASH,
        "stderr_sha256": HASH,
        "materials_output_sha256": HASH,
        "materials_log_sha256": HASH,
        "artifacts": (
            RunArtifactDigest(
                role="script",
                relative_path="jobs/run/roundtrip_import_export.pl",
                sha256=HASH,
                byte_count=10,
            ),
        ),
        "all_artifacts_confined": True,
    }
    values.update(updates)
    return RunnerExecutionReceipt(**values)


def test_runner_execution_requires_coherent_result_and_artifact_evidence() -> None:
    assert _runner_execution().success is True
    with pytest.raises(ValidationError, match="return zero"):
        _runner_execution(return_code=1)
    with pytest.raises(ValidationError, match="one group"):
        _runner_execution(stderr_sha256=None)
    with pytest.raises(ValidationError, match="exactly one script"):
        _runner_execution(artifacts=())
    duplicate = RunArtifactDigest(
        role="runner_artifact",
        relative_path="jobs/run/roundtrip_import_export.pl",
        sha256=HASH,
        byte_count=10,
    )
    with pytest.raises(ValidationError, match="paths must be unique"):
        _runner_execution(artifacts=(_runner_execution().artifacts[0], duplicate))


def _fake_execution_result(
    request_factory,
    fake_runner,
    fake_gui,
) -> RoundtripExecutionResult:
    result = MaterialsStudioRoundtripAdapter(
        runner=fake_runner,
        gui_backend=fake_gui,
        real_environment=False,
    ).run(request_factory(execution_mode="execute"))
    assert isinstance(result, RoundtripExecutionResult)
    assert result.receipt.comparison is not None
    return result


def test_roundtrip_receipt_rejects_cross_field_evidence_mismatches(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    receipt = _fake_execution_result(request_factory, fake_runner, fake_gui).receipt
    payload = receipt.model_dump(mode="python")

    mismatches = (
        ("real_environment", True, "environment labels"),
        ("output_confined_and_fresh", False, "output freshness"),
        ("started_at", "2999-01-01T00:00:00Z", "completion precedes"),
    )
    for field, value, message in mismatches:
        changed = dict(payload)
        changed[field] = value
        with pytest.raises(ValidationError, match=message):
            contracts.RoundtripReceipt.model_validate(changed, strict=True)

    changed = dict(payload)
    changed["failure_codes"] = ("runner_failed",)
    with pytest.raises(ValidationError, match="runner_failed"):
        contracts.RoundtripReceipt.model_validate(changed, strict=True)

    changed = dict(payload)
    changed["tagged_summary"] = {
        **payload["tagged_summary"],
        "source_path_sha256": "b" * 64,
    }
    with pytest.raises(ValidationError, match="does not bind"):
        contracts.RoundtripReceipt.model_validate(changed, strict=True)


def _offline_acceptance(
    request_factory,
    fake_runner,
    fake_gui,
) -> RoundtripBenchmarkAcceptance:
    receipt = _fake_execution_result(request_factory, fake_runner, fake_gui).receipt
    assert receipt.comparison is not None
    shared = FiveValidityStates(
        structure_valid="PASS",
        semiconductor_domain_valid="PASS",
        ms_roundtrip_valid="NOT_RUN",
        calculation_evidence_valid="NOT_RUN",
        scientifically_verified="NOT_RUN",
    )
    return RoundtripBenchmarkAcceptance(
        evaluation_run_id="offline-acceptance-001",
        shared_evaluator_report_sha256=HASH,
        shared_evaluator_report_unmodified=True,
        shared_evaluator_states=shared,
        states=shared,
        overall_status="NOT_RUN",
        ms_roundtrip_structure_sha256=receipt.output_artifact.sha256,
        roundtrip_receipt_sha256=HASH,
        comparison=receipt.comparison,
        candidate_immutable=True,
        real_materials_studio="NOT_RUN",
    )


def test_benchmark_acceptance_rejects_forged_state_derivations(
    request_factory,
    fake_runner,
    fake_gui,
) -> None:
    acceptance = _offline_acceptance(request_factory, fake_runner, fake_gui)
    payload = acceptance.model_dump(mode="python")

    changed = dict(payload)
    changed["overall_status"] = "PASS"
    with pytest.raises(ValidationError, match="overall status"):
        RoundtripBenchmarkAcceptance.model_validate(changed, strict=True)

    changed = dict(payload)
    changed["states"] = {
        **payload["states"],
        "ms_roundtrip_valid": "PASS",
    }
    with pytest.raises(ValidationError, match="real Materials Studio"):
        RoundtripBenchmarkAcceptance.model_validate(changed, strict=True)

    changed = dict(payload)
    changed["shared_evaluator_states"] = {
        **payload["shared_evaluator_states"],
        "ms_roundtrip_valid": "PASS",
    }
    with pytest.raises(ValidationError, match="PR-7 boundary"):
        RoundtripBenchmarkAcceptance.model_validate(changed, strict=True)
