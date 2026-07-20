from __future__ import annotations

import copy

from material_studio_mcp_server.benchmark_evaluation import (
    SEMANTIC_VALIDATOR_CONTRACT,
    TrustedDomainObservation,
    TrustedDomainObservations,
    evaluate_benchmark_case,
    validate_benchmark_case_semantics,
)


def test_blind_evaluation_never_needs_input_semantic_attestation(
    evaluation_fixture,
) -> None:
    assert "result" not in evaluation_fixture.case
    semantic = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert semantic.validator_contract == SEMANTIC_VALIDATOR_CONTRACT
    assert semantic.input_semantic_attestation_ignored is True
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
        evaluation_run_id="blind-evaluation-001",
        trusted_domain_observations=TrustedDomainObservations(
            observations=(
                TrustedDomainObservation(
                    metric="surface.vacuum_absolute_error_angstrom",
                    observed=0.1,
                    evidence_sha256="e" * 64,
                ),
            )
        ),
    )
    assert outcome.report.overall_status == "PASS"
    assert outcome.report.real_materials_studio == "NOT_RUN"
    assert outcome.report.real_castep == "NOT_RUN"


def test_blind_candidate_is_not_repaired_after_a_failure(evaluation_fixture) -> None:
    before = evaluation_fixture.candidate_path.read_bytes()
    case = copy.deepcopy(evaluation_fixture.case)
    case["candidate"]["root"] = "candidate/forged"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert evaluation_fixture.candidate_path.read_bytes() == before
