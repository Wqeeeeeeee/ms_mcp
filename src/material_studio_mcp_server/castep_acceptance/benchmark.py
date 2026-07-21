"""Five-state benchmark derivation over the immutable shared evaluator."""

from __future__ import annotations

from typing import Mapping

from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkCase,
    CandidateSubmission,
    EvaluationRoots,
    FiveValidityStates,
    TrustedDomainObservations,
    aggregate_required_gates,
    assert_coordinate_free_payload,
    evaluate_benchmark_case,
    load_benchmark_case,
)
from material_studio_mcp_server.state.execution import canonical_json_sha256

from .contracts import CastepBenchmarkAcceptance, CastepVerificationReport


SURFACE_VACUUM_METRIC = "surface.vacuum_absolute_error_angstrom"


def _artifact_digest(submission: CandidateSubmission, kind: str) -> str:
    matches = tuple(item for item in submission.artifacts if item.kind == kind)
    if len(matches) != 1:
        raise ValueError(f"candidate submission requires exactly one {kind} artifact")
    return matches[0].sha256


def evaluate_castep_acceptance_benchmark(
    case_value: Mapping[str, object] | BenchmarkCase,
    *,
    roots: EvaluationRoots,
    submission: CandidateSubmission,
    evaluation_run_id: str,
    trusted_domain_observations: TrustedDomainObservations,
    verification: CastepVerificationReport,
) -> CastepBenchmarkAcceptance:
    """Derive calculation validity without changing shared evaluator behavior."""

    if not isinstance(verification, CastepVerificationReport):
        raise TypeError("verification must be CastepVerificationReport")
    if not isinstance(submission, CandidateSubmission):
        raise TypeError("submission must be CandidateSubmission")
    case = load_benchmark_case(case_value)
    for state_name in (
        "ms_roundtrip_valid",
        "calculation_evidence_valid",
        "scientifically_verified",
    ):
        gate = getattr(case.gates, state_name)
        if (
            gate.enabled
            or gate.required_for_overall_pass
            or gate.criteria
            or gate.not_run_reason is None
        ):
            raise ValueError(
                f"shared benchmark gate {state_name} must remain disabled"
            )

    structure_digest = _artifact_digest(submission, "structure")
    vacuum_observations = tuple(
        observation
        for observation in trusted_domain_observations.observations
        if observation.metric == SURFACE_VACUUM_METRIC
    )
    if (
        len(vacuum_observations) != 1
        or vacuum_observations[0].evidence_sha256 != structure_digest
    ):
        raise ValueError(
            "trusted surface vacuum evidence must bind the submitted structure"
        )

    calculation_digest = canonical_json_sha256(
        verification.model_dump(mode="json")
    )
    if _artifact_digest(submission, "calculation_result") != calculation_digest:
        raise ValueError("calculation-result artifact is not bound to verification")
    _artifact_digest(submission, "revision_metadata")

    shared = evaluate_benchmark_case(
        case,
        roots=roots,
        submission=submission,
        evaluation_run_id=evaluation_run_id,
        trusted_domain_observations=trusted_domain_observations,
    )
    shared_before = shared.report.model_dump(mode="json")
    if (
        shared.report.states.ms_roundtrip_valid != "NOT_RUN"
        or shared.report.states.calculation_evidence_valid != "NOT_RUN"
        or shared.report.states.scientifically_verified != "NOT_RUN"
        or not shared.report.candidate_immutable
    ):
        raise ValueError("shared evaluator crossed the private calculation boundary")

    if verification.real_environment:
        calculation_status = "PASS" if verification.status == "PASS" else "FAIL"
        real_castep = calculation_status
    else:
        calculation_status = "NOT_RUN"
        real_castep = "NOT_RUN"
    states = FiveValidityStates(
        structure_valid=shared.report.states.structure_valid,
        semiconductor_domain_valid=shared.report.states.semiconductor_domain_valid,
        ms_roundtrip_valid="NOT_RUN",
        calculation_evidence_valid=calculation_status,
        scientifically_verified="NOT_RUN",
    )
    overall = aggregate_required_gates(
        states,
        (
            "structure_valid",
            "semiconductor_domain_valid",
            "calculation_evidence_valid",
        ),
        hard_failure_present="FAIL" in states.as_dict().values(),
    )
    shared_after = shared.report.model_dump(mode="json")
    if shared_before != shared_after:
        raise ValueError("shared evaluator report changed during private derivation")
    acceptance = CastepBenchmarkAcceptance(
        evaluation_run_id=evaluation_run_id,
        shared_evaluator_report_sha256=canonical_json_sha256(shared_before),
        shared_evaluator_states=shared.report.states,
        states=states,
        overall_status=overall,
        calculation_evidence_sha256=calculation_digest,
        real_castep=real_castep,
    )
    assert_coordinate_free_payload(acceptance.model_dump(mode="json"))
    return acceptance


__all__ = [
    "SURFACE_VACUUM_METRIC",
    "evaluate_castep_acceptance_benchmark",
]
