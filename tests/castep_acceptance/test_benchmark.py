from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import material_studio_mcp_server.castep_acceptance.benchmark as benchmark_module
from material_studio_mcp_server.benchmark_evaluation import (
    CandidateSubmission,
    EvaluationRoots,
    SubmittedCandidateArtifact,
    TrustedDomainObservation,
    TrustedDomainObservations,
    assert_coordinate_free_payload,
    load_benchmark_case,
)
from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceExecutionResult,
    evaluate_castep_acceptance_benchmark,
)
from material_studio_mcp_server.castep_acceptance.benchmark import (
    SURFACE_VACUUM_METRIC,
)
from material_studio_mcp_server.castep_acceptance.contracts import (
    CastepVerificationReport,
)
from material_studio_mcp_server.castep_acceptance.profile import (
    build_fixed_candidate,
)
from material_studio_mcp_server.canonicalization import parse_cif_structure
from material_studio_mcp_server.ms_roundtrip.comparison import (
    _canonicalizer_compatible_cif,
)
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.translators import write_crystal_cif
from tests.domains.surface.test_blind_benchmark import (
    _analytical_oracle_cif_bytes,
)

from ._helpers import synthetic_verification, write_canonical_json


ROOT = Path(__file__).resolve().parents[2]
CASE_PATH = (
    ROOT / "benchmarks" / "cases" / "sic_3c_castep_energy" / "benchmark_case.json"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_case() -> dict[str, object]:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def _vacuum_error_from_candidate(payload: bytes) -> float:
    structure = parse_cif_structure(
        payload,
        expected_sha256=_sha256(payload),
        expected_byte_count=len(payload),
    )
    c_length = math.sqrt(sum(value * value for value in structure.lattice[2]))
    fractional_z = tuple(site.fractional_coordinates[2] for site in structure.sites)
    measured_vacuum = c_length * (1.0 - max(fractional_z) + min(fractional_z))
    return abs(measured_vacuum - 15.0)


@dataclass(frozen=True)
class _StagedBenchmark:
    case: dict[str, object]
    roots: EvaluationRoots
    submission: CandidateSubmission
    observations: TrustedDomainObservations
    candidate_payloads: dict[str, bytes]


def _stage_benchmark(
    root: Path,
    *,
    source_spec: ModelSpec,
    verification: CastepVerificationReport,
    structure_payload: bytes | None = None,
) -> _StagedBenchmark:
    case = _load_case()
    candidate_root = root / "candidate" / "sic_3c_castep_energy"
    candidate_root.mkdir(parents=True)
    structure_path = candidate_root / "structure.cif"
    if structure_payload is None:
        staging_root = root / "staging"
        staging_root.mkdir()
        raw_structure_path = staging_root / "raw_structure.cif"
        write_crystal_cif(source_spec.model, raw_structure_path)
        structure_payload = raw_structure_path.read_bytes()
    structure_path.write_bytes(_canonicalizer_compatible_cif(structure_payload))

    model_spec_path = candidate_root / "model_spec.json"
    write_canonical_json(model_spec_path, source_spec.model_dump(mode="json"))
    revision_metadata_path = candidate_root / "revision_metadata.json"
    write_canonical_json(
        revision_metadata_path,
        {
            "project_id": source_spec.project_id,
            "source_revision": 0,
            "result_revision": verification.result_revision,
            "source_structure_sha256": verification.source_structure_sha256,
            "structure_unchanged": verification.structure_unchanged,
        },
    )
    calculation_path = candidate_root / "calculation_result.json"
    write_canonical_json(
        calculation_path,
        verification.model_dump(mode="json"),
    )

    paths = {
        "model_spec": model_spec_path,
        "structure": structure_path,
        "revision_metadata": revision_metadata_path,
        "calculation_result": calculation_path,
    }
    payloads = {path.name: path.read_bytes() for path in paths.values()}
    hashes = {kind: _sha256(path.read_bytes()) for kind, path in paths.items()}
    submission = CandidateSubmission(
        structure_relative_path=structure_path.name,
        structure_sha256=hashes["structure"],
        artifacts=tuple(
            SubmittedCandidateArtifact(
                kind=kind,
                relative_path=path.name,
                sha256=hashes[kind],
            )
            for kind, path in paths.items()
        ),
    )
    observations = TrustedDomainObservations(
        observations=(
            TrustedDomainObservation(
                metric=SURFACE_VACUUM_METRIC,
                observed=_vacuum_error_from_candidate(payloads[structure_path.name]),
                evidence_sha256=hashes["structure"],
            ),
        )
    )

    # Coordinate-bearing oracle bytes enter only after candidate artifacts freeze.
    reference_root = root / "reference" / "sic_3c_castep_energy"
    evaluator_root = root / "evaluation" / "sic_3c_castep_energy"
    reference_root.mkdir(parents=True)
    evaluator_root.mkdir(parents=True)
    oracle = _analytical_oracle_cif_bytes()
    expected_oracle_sha256 = case["reference"]["structure_artifacts"][0]["sha256"]
    assert _sha256(oracle) == expected_oracle_sha256
    (reference_root / "analytical_oracle.cif").write_bytes(oracle)
    return _StagedBenchmark(
        case=case,
        roots=EvaluationRoots(
            reference_root=reference_root,
            candidate_root=candidate_root,
            evaluator_output_root=evaluator_root,
        ),
        submission=submission,
        observations=observations,
        candidate_payloads=payloads,
    )


def _evaluate_completed_castep(
    root: Path,
    *,
    result: CastepAcceptanceExecutionResult,
    evaluation_run_id: str,
):
    """Freeze a completed calculation, then enter the isolated evaluator."""

    assert result.verification.runner_success is True
    validation = result.public_execute["input_structure_validation"]
    assert validation["ok"] is True
    source_path = Path(validation["structure_path"])
    source_payload = source_path.read_bytes()
    assert _sha256(source_payload) == validation["sha256"]
    staged = _stage_benchmark(
        root,
        source_spec=result.source_spec,
        verification=result.verification,
        structure_payload=source_payload,
    )
    acceptance = evaluate_castep_acceptance_benchmark(
        staged.case,
        roots=staged.roots,
        submission=staged.submission,
        evaluation_run_id=evaluation_run_id,
        trusted_domain_observations=staged.observations,
        verification=result.verification,
    )
    assert_coordinate_free_payload(acceptance.model_dump(mode="json"))
    return acceptance


def test_offline_benchmark_case_is_schema_valid_and_private_gates_are_disabled() -> None:
    case = _load_case()
    schema = json.loads(
        (ROOT / "schemas" / "benchmark_case.schema.json").read_text(encoding="utf-8")
    )
    assert not list(Draft202012Validator(schema).iter_errors(case))
    loaded = load_benchmark_case(case)
    assert loaded.candidate.required_artifacts == (
        "model_spec",
        "structure",
        "revision_metadata",
        "calculation_result",
    )
    assert loaded.gates.ms_roundtrip_valid.enabled is False
    assert loaded.gates.calculation_evidence_valid.enabled is False
    assert loaded.gates.scientifically_verified.enabled is False


def test_offline_fake_calculation_cannot_claim_real_acceptance(tmp_path: Path) -> None:
    verification = synthetic_verification(real=False)
    staged = _stage_benchmark(
        tmp_path,
        source_spec=build_fixed_candidate("sic_3c_castep_energy_acceptance"),
        verification=verification,
    )
    acceptance = evaluate_castep_acceptance_benchmark(
        staged.case,
        roots=staged.roots,
        submission=staged.submission,
        evaluation_run_id="sic-3c-castep-energy-offline-fake",
        trusted_domain_observations=staged.observations,
        verification=verification,
    )
    assert acceptance.shared_evaluator_states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    assert acceptance.states == acceptance.shared_evaluator_states
    assert acceptance.overall_status == "NOT_RUN"
    assert acceptance.real_castep == "NOT_RUN"
    assert acceptance.scientific_status == "NOT_RUN"
    assert_coordinate_free_payload(acceptance.model_dump(mode="json"))
    assert {
        name: (staged.roots.candidate_root / name).read_bytes()
        for name in staged.candidate_payloads
    } == staged.candidate_payloads


def test_offline_synthetic_real_projection_derives_only_calculation_state(
    tmp_path: Path,
) -> None:
    verification = synthetic_verification(real=True)
    staged = _stage_benchmark(
        tmp_path,
        source_spec=build_fixed_candidate("sic_3c_castep_energy_acceptance"),
        verification=verification,
    )
    acceptance = evaluate_castep_acceptance_benchmark(
        staged.case,
        roots=staged.roots,
        submission=staged.submission,
        evaluation_run_id="sic-3c-castep-energy-offline-contract",
        trusted_domain_observations=staged.observations,
        verification=verification,
    )
    assert acceptance.states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "PASS",
        "scientifically_verified": "NOT_RUN",
    }
    assert acceptance.overall_status == "PASS"
    assert acceptance.real_castep == "PASS"


def test_offline_benchmark_rejects_unbound_surface_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verification = synthetic_verification(real=False)
    staged = _stage_benchmark(
        tmp_path,
        source_spec=build_fixed_candidate("sic_3c_castep_energy_acceptance"),
        verification=verification,
    )
    unbound = TrustedDomainObservations(
        observations=(
            staged.observations.observations[0].model_copy(
                update={"evidence_sha256": "0" * 64}
            ),
        )
    )

    def forbidden_evaluator(*args, **kwargs):
        raise AssertionError("unbound evidence must stop before evaluator entry")

    monkeypatch.setattr(
        benchmark_module,
        "evaluate_benchmark_case",
        forbidden_evaluator,
    )
    with pytest.raises(ValueError, match="bind the submitted structure"):
        evaluate_castep_acceptance_benchmark(
            staged.case,
            roots=staged.roots,
            submission=staged.submission,
            evaluation_run_id="sic-3c-castep-energy-offline-unbound",
            trusted_domain_observations=unbound,
            verification=verification,
        )


def test_offline_benchmark_rejects_unbound_calculation_artifact(
    tmp_path: Path,
) -> None:
    verification = synthetic_verification(real=False)
    staged = _stage_benchmark(
        tmp_path,
        source_spec=build_fixed_candidate("sic_3c_castep_energy_acceptance"),
        verification=verification,
    )
    artifacts = tuple(
        artifact.model_copy(update={"sha256": "0" * 64})
        if artifact.kind == "calculation_result"
        else artifact
        for artifact in staged.submission.artifacts
    )
    tampered_submission = staged.submission.model_copy(update={"artifacts": artifacts})
    with pytest.raises(ValueError, match="not bound to verification"):
        evaluate_castep_acceptance_benchmark(
            staged.case,
            roots=staged.roots,
            submission=tampered_submission,
            evaluation_run_id="sic-3c-castep-energy-offline-calculation-binding",
            trusted_domain_observations=staged.observations,
            verification=verification,
        )
