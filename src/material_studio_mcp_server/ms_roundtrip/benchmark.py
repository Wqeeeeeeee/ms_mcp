"""PR-7 benchmark derivation layered over the immutable shared evaluator."""

from __future__ import annotations

from typing import Mapping

from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkCase,
    CandidateSubmission,
    CandidateTreeGuard,
    EvaluationRoots,
    FiveValidityStates,
    TrustedDomainObservations,
    aggregate_required_gates,
    assert_coordinate_free_payload,
    evaluate_benchmark_case,
    load_benchmark_case,
    read_candidate_artifact,
    verify_isolation_roots,
)

from .comparison import compare_roundtrip_cif_bytes
from .contracts import RoundtripBenchmarkAcceptance, RoundtripReceipt
from .errors import RoundtripError, RoundtripErrorCode
from .secure_io import canonical_json_bytes, sha256_bytes


def roundtrip_receipt_sha256(receipt: RoundtripReceipt) -> str:
    if not isinstance(receipt, RoundtripReceipt):
        raise TypeError("receipt must be RoundtripReceipt")
    return sha256_bytes(
        canonical_json_bytes(receipt.model_dump(mode="json"), trailing_newline=True)
    )


def _artifact_by_kind(submission: CandidateSubmission, kind: str):
    selected = tuple(item for item in submission.artifacts if item.kind == kind)
    if len(selected) != 1:
        raise RoundtripError(
            RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
            "The candidate submission has an invalid artifact-kind binding.",
        )
    return selected[0]


def evaluate_roundtrip_benchmark(
    case_value: Mapping[str, object] | BenchmarkCase,
    *,
    roots: EvaluationRoots,
    submission: CandidateSubmission,
    evaluation_run_id: str,
    trusted_domain_observations: TrustedDomainObservations,
    receipt: RoundtripReceipt,
    receipt_sha256: str,
) -> RoundtripBenchmarkAcceptance:
    """Derive PR-7 acceptance without changing the shared evaluator report."""

    if not isinstance(submission, CandidateSubmission):
        raise TypeError("submission must be CandidateSubmission")
    if not isinstance(receipt, RoundtripReceipt):
        raise TypeError("receipt must be RoundtripReceipt")
    case = load_benchmark_case(case_value)
    ms_gate = case.gates.ms_roundtrip_valid
    if (
        ms_gate.enabled
        or ms_gate.required_for_overall_pass
        or ms_gate.criteria
        or ms_gate.not_run_reason is None
    ):
        raise RoundtripError(
            RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
            "The shared benchmark case must keep the MS gate disabled.",
        )
    if roundtrip_receipt_sha256(receipt) != receipt_sha256:
        raise RoundtripError(
            RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
            "The submitted round-trip receipt digest is invalid.",
        )

    canonical_roots = verify_isolation_roots(roots)
    input_artifact = _artifact_by_kind(submission, "structure")
    output_artifact = _artifact_by_kind(submission, "ms_roundtrip_structure")
    if not trusted_domain_observations.observations or any(
        observation.evidence_sha256 != output_artifact.sha256
        for observation in trusted_domain_observations.observations
    ):
        raise RoundtripError(
            RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
            "Trusted domain observations must bind the round-trip output digest.",
        )
    guard = CandidateTreeGuard(canonical_roots.candidate_root)
    with guard:
        input_payload = read_candidate_artifact(
            canonical_roots.candidate_root,
            input_artifact.relative_path,
            input_artifact.sha256,
            guard.before,
        )
        output_payload = read_candidate_artifact(
            canonical_roots.candidate_root,
            output_artifact.relative_path,
            output_artifact.sha256,
            guard.before,
        )
        comparison = compare_roundtrip_cif_bytes(
            input_payload,
            output_payload,
            expected_input_sha256=input_artifact.sha256,
            expected_output_sha256=output_artifact.sha256,
        )
        if (
            receipt.input_artifact.sha256 != input_artifact.sha256
            or receipt.output_artifact is None
            or receipt.output_artifact.sha256 != output_artifact.sha256
            or receipt.comparison != comparison
        ):
            raise RoundtripError(
                RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
                "Round-trip receipt and frozen candidate artifacts disagree.",
            )
        guard.checkpoint()
        shared_outcome = evaluate_benchmark_case(
            case,
            roots=canonical_roots,
            submission=submission,
            evaluation_run_id=evaluation_run_id,
            trusted_domain_observations=trusted_domain_observations,
        )
        shared_before = canonical_json_bytes(
            shared_outcome.report.model_dump(mode="json")
        )
        if (
            shared_outcome.report.states.ms_roundtrip_valid != "NOT_RUN"
            or shared_outcome.report.states.calculation_evidence_valid != "NOT_RUN"
            or shared_outcome.report.states.scientifically_verified != "NOT_RUN"
            or not shared_outcome.report.candidate_immutable
        ):
            raise RoundtripError(
                RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
                "The shared evaluator outcome violates the PR-7 boundary.",
            )
        if receipt.real_environment:
            ms_status = (
                "PASS"
                if (
                    receipt.status == "PASS"
                    and receipt.real_materials_studio_status == "PASS"
                    and comparison.passed
                )
                else "FAIL"
            )
        else:
            ms_status = "NOT_RUN"
        states = FiveValidityStates(
            structure_valid=shared_outcome.report.states.structure_valid,
            semiconductor_domain_valid=(
                shared_outcome.report.states.semiconductor_domain_valid
            ),
            ms_roundtrip_valid=ms_status,
            calculation_evidence_valid="NOT_RUN",
            scientifically_verified="NOT_RUN",
        )
        overall = aggregate_required_gates(
            states,
            (
                "structure_valid",
                "semiconductor_domain_valid",
                "ms_roundtrip_valid",
            ),
            hard_failure_present=("FAIL" in states.as_dict().values()),
        )
        guard.checkpoint()
        shared_after = canonical_json_bytes(
            shared_outcome.report.model_dump(mode="json")
        )
        if shared_before != shared_after:
            raise RoundtripError(
                RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
                "The shared evaluator report changed during derivation.",
            )

    if guard.after is None or guard.before.summary != guard.after.summary:
        raise RoundtripError(
            RoundtripErrorCode.BENCHMARK_BINDING_INVALID,
            "The candidate tree changed during round-trip evaluation.",
        )
    acceptance = RoundtripBenchmarkAcceptance(
        evaluation_run_id=evaluation_run_id,
        shared_evaluator_report_sha256=sha256_bytes(shared_before),
        shared_evaluator_report_unmodified=True,
        shared_evaluator_states=shared_outcome.report.states,
        states=states,
        overall_status=overall,
        ms_roundtrip_structure_sha256=output_artifact.sha256,
        roundtrip_receipt_sha256=receipt_sha256,
        comparison=comparison,
        candidate_immutable=True,
        real_materials_studio=receipt.real_materials_studio_status,
    )
    assert_coordinate_free_payload(acceptance.model_dump(mode="json"))
    return acceptance


__all__ = ["evaluate_roundtrip_benchmark", "roundtrip_receipt_sha256"]
