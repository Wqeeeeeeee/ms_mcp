from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

import material_studio_mcp_server.benchmark_evaluation.evaluator as evaluator_module
from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkEvaluationError,
    CandidateSubmission,
    EvaluationReason,
    SubmittedCandidateArtifact,
    TrustedDomainObservation,
    TrustedDomainObservations,
    evaluate_benchmark_case,
    project_coordinate_free_contract,
)


def _domain(value: float) -> TrustedDomainObservations:
    return TrustedDomainObservations(
        observations=(
            TrustedDomainObservation(
                metric="surface.vacuum_absolute_error_angstrom",
                observed=value,
                evidence_sha256="d" * 64,
            ),
        )
    )


def _submission_with_structure_bytes(
    evaluation_fixture,
    payload: bytes,
) -> CandidateSubmission:
    digest = hashlib.sha256(payload).hexdigest()
    artifacts = tuple(
        item.model_copy(update={"sha256": digest})
        if item.kind == "structure"
        else item
        for item in evaluation_fixture.submission.artifacts
    )
    return evaluation_fixture.submission.model_copy(
        update={"structure_sha256": digest, "artifacts": artifacts}
    )


def test_exact_candidate_passes_structure_and_trusted_domain_gates(
    evaluation_fixture,
) -> None:
    before = evaluation_fixture.candidate_path.read_bytes()
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
        evaluation_run_id="evaluation-exact-001",
        trusted_domain_observations=_domain(0.1),
    )
    assert outcome.report.states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    assert outcome.report.overall_status == "PASS"
    assert outcome.report.candidate_immutable is True
    assert outcome.report.candidate_tree_before == outcome.report.candidate_tree_after
    assert evaluation_fixture.candidate_path.read_bytes() == before
    assert not tuple(evaluation_fixture.evaluator_root.iterdir())


def test_missing_domain_evidence_retains_not_run_and_never_claims_science(
    evaluation_fixture,
) -> None:
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
        evaluation_run_id="evaluation-no-domain",
    )
    assert outcome.report.states.semiconductor_domain_valid == "NOT_RUN"
    assert outcome.report.states.scientifically_verified == "NOT_RUN"
    assert outcome.report.overall_status == "NOT_RUN"
    assert EvaluationReason.REQUIRED_EVIDENCE_MISSING in (
        outcome.report.hard_failure_reason_codes
    )


def test_domain_value_above_inclusive_limit_is_hard_failure(evaluation_fixture) -> None:
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
        evaluation_run_id="evaluation-domain-fail",
        trusted_domain_observations=_domain(0.10000000000000002),
    )
    assert outcome.report.states.semiconductor_domain_valid == "FAIL"
    assert outcome.report.overall_status == "FAIL"
    assert outcome.report.hard_failure_reason_codes == (
        EvaluationReason.THRESHOLD_FAILED,
    )


def test_lattice_error_above_frozen_limit_fails_structure(evaluation_fixture) -> None:
    changed = evaluation_fixture.candidate_path.read_bytes().replace(b"4.0", b"4.01")
    evaluation_fixture.candidate_path.write_bytes(changed)
    submission = _submission_with_structure_bytes(evaluation_fixture, changed)
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=submission,
        evaluation_run_id="evaluation-lattice-fail",
        trusted_domain_observations=_domain(0.0),
    )
    assert outcome.report.states.structure_valid == "FAIL"
    assert outcome.report.overall_status == "FAIL"
    assert outcome.report.structure_threshold_results.lattice_relative_error_pass is False


def test_candidate_mutation_during_canonicalization_is_detected(
    evaluation_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = evaluator_module.canonicalize_cif_bytes
    calls = 0

    def mutating_canonicalize(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            evaluation_fixture.candidate_path.write_bytes(
                evaluation_fixture.candidate_path.read_bytes() + b"\n# mutation"
            )
        return result

    monkeypatch.setattr(
        evaluator_module, "canonicalize_cif_bytes", mutating_canonicalize
    )
    with pytest.raises(BenchmarkEvaluationError) as captured:
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=evaluation_fixture.submission,
            evaluation_run_id="evaluation-mutation",
            trusted_domain_observations=_domain(0.0),
        )
    assert captured.value.reason is EvaluationReason.CANDIDATE_TREE_CHANGED


def test_canonicalization_exception_is_redacted(
    evaluation_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unsafe_exception(*args, **kwargs):
        raise RuntimeError("REFERENCE_CANARY coordinate payload")

    monkeypatch.setattr(evaluator_module, "canonicalize_cif_bytes", unsafe_exception)
    with pytest.raises(BenchmarkEvaluationError) as captured:
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=evaluation_fixture.submission,
            evaluation_run_id="evaluation-redaction",
        )
    assert captured.value.reason is EvaluationReason.CANONICALIZATION_FAILED
    assert "CANARY" not in str(captured.value)


def test_submission_digest_must_match_snapshotted_file(evaluation_fixture) -> None:
    artifacts = tuple(
        item.model_copy(update={"sha256": "f" * 64})
        if item.kind == "structure"
        else item
        for item in evaluation_fixture.submission.artifacts
    )
    forged = evaluation_fixture.submission.model_copy(
        update={"structure_sha256": "f" * 64, "artifacts": artifacts}
    )
    with pytest.raises(BenchmarkEvaluationError) as captured:
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=forged,
            evaluation_run_id="evaluation-forged-digest",
        )
    assert captured.value.reason is EvaluationReason.ARTIFACT_IDENTITY_MISMATCH


@pytest.mark.parametrize("candidate_kind", ["malformed", "composition_mismatch"])
def test_structurally_invalid_candidate_returns_a_fail_report(
    evaluation_fixture,
    candidate_kind: str,
) -> None:
    if candidate_kind == "malformed":
        changed = b"not-a-cif\n"
    else:
        changed = evaluation_fixture.candidate_path.read_bytes()
        for label in (b"Si1", b"Si2", b"Si3", b"Si4"):
            changed = changed.replace(label + b" Si", label + b" C")
    evaluation_fixture.candidate_path.write_bytes(changed)
    submission = _submission_with_structure_bytes(evaluation_fixture, changed)
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=submission,
        evaluation_run_id=f"evaluation-{candidate_kind}",
        trusted_domain_observations=_domain(0.0),
    )
    assert outcome.report.states.structure_valid == "FAIL"
    assert outcome.report.overall_status == "FAIL"
    assert outcome.report.comparison_projection is None
    assert outcome.report.structure_threshold_results is None


def test_missing_required_candidate_artifact_is_rejected(evaluation_fixture) -> None:
    structure = next(
        item
        for item in evaluation_fixture.submission.artifacts
        if item.kind == "structure"
    )
    submission = evaluation_fixture.submission.model_copy(
        update={"artifacts": (structure,)}
    )
    with pytest.raises(BenchmarkEvaluationError):
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=submission,
            evaluation_run_id="evaluation-missing-artifact",
        )


def test_extra_candidate_artifact_is_rejected(evaluation_fixture) -> None:
    payload = b'{"revision":"r001"}'
    path = evaluation_fixture.roots.candidate_root / "revision.json"
    path.write_bytes(payload)
    extra = SubmittedCandidateArtifact(
        kind="revision_metadata",
        relative_path="revision.json",
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    submission = evaluation_fixture.submission.model_copy(
        update={"artifacts": (*evaluation_fixture.submission.artifacts, extra)}
    )
    with pytest.raises(BenchmarkEvaluationError):
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=submission,
            evaluation_run_id="evaluation-extra-artifact",
        )


def test_nonstructure_candidate_artifact_digest_is_bound(evaluation_fixture) -> None:
    artifacts = tuple(
        item.model_copy(update={"sha256": "f" * 64})
        if item.kind == "model_spec"
        else item
        for item in evaluation_fixture.submission.artifacts
    )
    submission = evaluation_fixture.submission.model_copy(
        update={"artifacts": artifacts}
    )
    with pytest.raises(BenchmarkEvaluationError) as captured:
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=submission,
            evaluation_run_id="evaluation-model-spec-digest",
        )
    assert captured.value.reason is EvaluationReason.ARTIFACT_IDENTITY_MISMATCH


def test_invalid_run_identifier_is_rejected_before_artifact_evaluation(
    evaluation_fixture,
) -> None:
    with pytest.raises(BenchmarkEvaluationError) as captured:
        evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=evaluation_fixture.submission,
            evaluation_run_id="x",
        )
    assert captured.value.reason is EvaluationReason.CONTRACT_INVALID


def test_outcome_projection_is_coordinate_free(evaluation_fixture) -> None:
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
        evaluation_run_id="evaluation-projection",
        trusted_domain_observations=_domain(0.0),
    )
    task_payload = project_coordinate_free_contract(outcome.compiled_task)
    report_payload = project_coordinate_free_contract(outcome.report)
    serialized = repr((task_payload, report_payload)).casefold()
    for forbidden in (
        "fractional_coordinates",
        "cartesian_coordinates",
        "'atom_mapping':",
        "'displacement_vectors':",
        "'raw_bytes':",
    ):
        assert forbidden not in serialized
    assert task_payload["contains_atom_mapping"] is False
    assert report_payload["contains_displacement_vectors"] is False
