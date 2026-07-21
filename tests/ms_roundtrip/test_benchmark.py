from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from material_studio_mcp_server.benchmark_evaluation import (
    CandidateSubmission,
    EvaluationRoots,
    SubmittedCandidateArtifact,
    TrustedDomainObservation,
    TrustedDomainObservations,
    assert_coordinate_free_payload,
    load_benchmark_case,
)
from material_studio_mcp_server.ms_roundtrip import (
    CandidateBinding,
    MaterialsStudioRoundtripAdapter,
    RoundtripError,
    RoundtripExecutionResult,
    RoundtripRequest,
    evaluate_roundtrip_benchmark,
    roundtrip_receipt_sha256,
)
from material_studio_mcp_server.ms_roundtrip.comparison import (
    _canonicalizer_compatible_cif,
)
from tests.domains.surface.test_blind_benchmark import (
    _analytical_oracle_cif_bytes,
)

from ._helpers import (
    FakeGuiBackend,
    FakeRunner,
    build_candidate,
    copy_roundtrip_output,
    write_model_spec,
)


ROOT = Path(__file__).resolve().parents[2]
CASE_PATH = ROOT / "benchmarks" / "cases" / "sic_3c_ms_roundtrip" / "benchmark_case.json"
SURFACE_VACUUM_METRIC = "surface.vacuum_absolute_error_angstrom"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_case() -> dict[str, object]:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def _prepare_offline_run(tmp_path: Path):
    staging = tmp_path / "staging" / "raw.cif"
    model = build_candidate(staging, project_id="sic_roundtrip_benchmark")
    candidate_root = tmp_path / "candidate" / "sic_3c_ms_roundtrip"
    candidate_root.mkdir(parents=True)
    input_path = candidate_root / "structure.cif"
    input_path.write_bytes(_canonicalizer_compatible_cif(staging.read_bytes()))
    model_spec_path = candidate_root / "model_spec.json"
    write_model_spec(model_spec_path, model)

    runner_path = tmp_path / "fake-install" / "RunMatScript.bat"
    runner_path.parent.mkdir()
    runner_path.write_bytes(b"@echo off\r\nrem offline fake runner\r\n")
    runner = FakeRunner(runner_path)
    gui = FakeGuiBackend(minimized=True)
    output_root = tmp_path / "execution"
    output_root.mkdir()
    input_sha = _sha256(input_path.read_bytes())
    request = RoundtripRequest(
        request_id="roundtrip-benchmark-request",
        run_id="roundtrip-benchmark-run",
        candidate=CandidateBinding(
            structure_path=input_path,
            expected_structure_sha256=input_sha,
        ),
        output_root=output_root,
        execution_mode="execute",
        timeout_seconds=30,
    )
    result = MaterialsStudioRoundtripAdapter(
        runner=runner,
        gui_backend=gui,
        real_environment=False,
    ).run(request)
    assert isinstance(result, RoundtripExecutionResult)
    assert result.status == "PASS"
    assert result.output_path is not None
    output_path = candidate_root / "ms_roundtrip_output.cif"
    copy_roundtrip_output(result.output_path, output_path)

    payloads = {
        "model_spec.json": model_spec_path.read_bytes(),
        "structure.cif": input_path.read_bytes(),
        "ms_roundtrip_output.cif": output_path.read_bytes(),
    }
    hashes = {name: _sha256(payload) for name, payload in payloads.items()}
    submission = CandidateSubmission(
        structure_relative_path="structure.cif",
        structure_sha256=hashes["structure.cif"],
        artifacts=(
            SubmittedCandidateArtifact(
                kind="model_spec",
                relative_path="model_spec.json",
                sha256=hashes["model_spec.json"],
            ),
            SubmittedCandidateArtifact(
                kind="structure",
                relative_path="structure.cif",
                sha256=hashes["structure.cif"],
            ),
            SubmittedCandidateArtifact(
                kind="ms_roundtrip_structure",
                relative_path="ms_roundtrip_output.cif",
                sha256=hashes["ms_roundtrip_output.cif"],
            ),
        ),
    )
    observations = TrustedDomainObservations(
        observations=(
            TrustedDomainObservation(
                metric=SURFACE_VACUUM_METRIC,
                observed=abs(result.receipt.comparison.output_vacuum_angstrom - 15.0),
                evidence_sha256=hashes["ms_roundtrip_output.cif"],
            ),
        )
    )

    reference_root = tmp_path / "reference" / "sic_3c_ms_roundtrip"
    evaluator_root = tmp_path / "evaluation" / "sic_3c_ms_roundtrip"
    reference_root.mkdir(parents=True)
    evaluator_root.mkdir(parents=True)
    oracle = _analytical_oracle_cif_bytes()
    assert _sha256(oracle) == _load_case()["reference"]["structure_artifacts"][0]["sha256"]
    (reference_root / "analytical_oracle.cif").write_bytes(oracle)
    roots = EvaluationRoots(
        reference_root=reference_root,
        candidate_root=candidate_root,
        evaluator_output_root=evaluator_root,
    )
    return result, roots, submission, observations, payloads


def test_offline_benchmark_case_is_schema_valid_and_shared_ms_gate_is_disabled() -> None:
    case = _load_case()
    schema = json.loads(
        (ROOT / "schemas" / "benchmark_case.schema.json").read_text(encoding="utf-8")
    )
    assert not list(Draft202012Validator(schema).iter_errors(case))
    loaded = load_benchmark_case(case)
    assert loaded.candidate.required_artifacts == (
        "model_spec",
        "structure",
        "ms_roundtrip_structure",
    )
    assert loaded.gates.ms_roundtrip_valid.enabled is False
    assert loaded.gates.calculation_evidence_valid.enabled is False
    assert loaded.gates.scientifically_verified.enabled is False


def test_offline_fake_runner_cannot_claim_real_ms_acceptance(tmp_path: Path) -> None:
    result, roots, submission, observations, before = _prepare_offline_run(tmp_path)
    receipt_sha = roundtrip_receipt_sha256(result.receipt)
    acceptance = evaluate_roundtrip_benchmark(
        _load_case(),
        roots=roots,
        submission=submission,
        evaluation_run_id="sic-3c-roundtrip-offline-001",
        trusted_domain_observations=observations,
        receipt=result.receipt,
        receipt_sha256=receipt_sha,
    )
    assert acceptance.shared_evaluator_states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    assert acceptance.states.model_dump() == acceptance.shared_evaluator_states.model_dump()
    assert acceptance.overall_status == "NOT_RUN"
    assert acceptance.real_materials_studio == "NOT_RUN"
    assert acceptance.candidate_immutable is True
    assert_coordinate_free_payload(acceptance.model_dump(mode="json"))
    candidate_root = roots.candidate_root
    after = {name: (candidate_root / name).read_bytes() for name in before}
    assert after == before


def test_offline_benchmark_rejects_unbound_output_observation(tmp_path: Path) -> None:
    result, roots, submission, _observations, _before = _prepare_offline_run(tmp_path)
    wrong = TrustedDomainObservations(
        observations=(
            TrustedDomainObservation(
                metric=SURFACE_VACUUM_METRIC,
                observed=0.0,
                evidence_sha256="f" * 64,
            ),
        )
    )
    with pytest.raises(RoundtripError, match="output digest"):
        evaluate_roundtrip_benchmark(
            _load_case(),
            roots=roots,
            submission=submission,
            evaluation_run_id="sic-3c-roundtrip-offline-binding",
            trusted_domain_observations=wrong,
            receipt=result.receipt,
            receipt_sha256=roundtrip_receipt_sha256(result.receipt),
        )

def test_offline_benchmark_rejects_receipt_digest_mismatch(tmp_path: Path) -> None:
    result, roots, submission, observations, _before = _prepare_offline_run(tmp_path)
    with pytest.raises(RoundtripError, match="receipt digest"):
        evaluate_roundtrip_benchmark(
            _load_case(),
            roots=roots,
            submission=submission,
            evaluation_run_id="sic-3c-roundtrip-offline-receipt",
            trusted_domain_observations=observations,
            receipt=result.receipt,
            receipt_sha256="0" * 64,
        )
