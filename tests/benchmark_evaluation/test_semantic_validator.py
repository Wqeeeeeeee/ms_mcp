from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import material_studio_mcp_server.benchmark_evaluation.semantic as semantic_module
from material_studio_mcp_server.benchmark_evaluation import (
    EvaluationReason,
    EvaluationRoots,
    FiveValidityStates,
    SEMANTIC_VALIDATOR_CONTRACT,
    StoredCoordinateFreeReportArtifact,
    validate_benchmark_case_semantics,
)

from .conftest import artifact, sha256


def _attestation() -> dict[str, Any]:
    return {
        "validator_contract": SEMANTIC_VALIDATOR_CONTRACT,
        "gate_state_bindings_complete": True,
        "required_gate_truth_table_satisfied": True,
        "criterion_results_complete": True,
        "criterion_bindings_complete": True,
        "hard_failure_results_complete": True,
        "hard_failure_rule_bindings_complete": True,
        "backend_evidence_bindings_complete": True,
        "isolation_roots_disjoint": True,
        "reference_artifacts_bound_to_reference_root": True,
        "candidate_artifacts_bound_to_candidate_root": True,
        "evaluator_artifacts_bound_to_evaluator_root": True,
        "candidate_root_matches_declared_candidate": True,
    }


def _result(evaluation_fixture) -> dict[str, Any]:
    domain_payload = b"domain-evidence"
    states = {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    candidate_sha256 = evaluation_fixture.submission.structure_sha256
    reference_sha256 = evaluation_fixture.case["reference"][
        "structure_artifacts"
    ][0]["sha256"]
    report_payload = StoredCoordinateFreeReportArtifact(
        evaluation_run_id="evaluation-sic-001",
        case_id=evaluation_fixture.case["case_id"],
        candidate_sha256=candidate_sha256,
        reference_sha256=reference_sha256,
        states=FiveValidityStates(**states),
        overall_status="PASS",
    ).model_dump_json().encode("ascii")
    domain_path = evaluation_fixture.evaluator_root / "domain.json"
    report_path = evaluation_fixture.evaluator_root / "report.json"
    domain_path.write_bytes(domain_payload)
    report_path.write_bytes(report_payload)
    return {
        "evaluation_run_id": "evaluation-sic-001",
        "candidate_sha256": candidate_sha256,
        "reference_sha256": reference_sha256,
        "started_at": "2026-07-20T00:00:00Z",
        "completed_at": "2026-07-20T00:01:00Z",
        "semantic_validation": _attestation(),
        "states": states,
        "overall_status": "PASS",
        "criterion_results": [
            {
                "criterion_id": "criterion-structure",
                "validity_state": "structure_valid",
                "severity": "hard_failure",
                "status": "PASS",
                "observed": 1.0,
                "evidence": [
                    artifact(
                        "candidate/sic/structure.cif",
                        evaluation_fixture.candidate_path.read_bytes(),
                    )
                ],
                "notes": [],
            },
            {
                "criterion_id": "criterion-domain-vacuum",
                "validity_state": "semiconductor_domain_valid",
                "severity": "hard_failure",
                "status": "PASS",
                "observed": 0.1,
                "evidence": [artifact("evaluation/sic/domain.json", domain_payload)],
                "notes": [],
            },
        ],
        "hard_failures": [],
        "warnings": [],
        "real_materials_studio": {
            "status": "NOT_RUN",
            "real_environment": False,
            "evidence": [],
        },
        "real_castep": {
            "status": "NOT_RUN",
            "real_environment": False,
            "evidence": [],
        },
        "report_artifacts": [artifact("evaluation/sic/report.json", report_payload)],
    }


def test_valid_template_recomputes_semantics_without_input_attestation(
    evaluation_fixture,
) -> None:
    report = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is True
    assert report.reason_codes == ()
    assert report.input_semantic_attestation_ignored is True
    assert report.validity_state_names == (
        "structure_valid",
        "semiconductor_domain_valid",
        "ms_roundtrip_valid",
        "calculation_evidence_valid",
        "scientifically_verified",
    )


def test_valid_result_reconciles_every_result_record(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is True
    assert report.criterion_results_complete is True
    assert report.required_gate_truth_table_satisfied is True


def test_forged_true_attestation_cannot_mask_candidate_root_mismatch(
    evaluation_fixture,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["candidate"]["root"] = "candidate/other"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.candidate_root_matches_declared_candidate is False
    assert EvaluationReason.CANDIDATE_ROOT_DECLARATION_MISMATCH in report.reason_codes


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_source",
        "duplicate_artifact",
        "duplicate_criterion",
        "duplicate_rule",
        "dangling_source",
    ],
)
def test_duplicate_and_referential_records_fail_closed(
    evaluation_fixture, mutation: str
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    if mutation == "duplicate_source":
        case["reference"]["sources"].append(
            copy.deepcopy(case["reference"]["sources"][0])
        )
    elif mutation == "duplicate_artifact":
        case["reference"]["structure_artifacts"].append(
            copy.deepcopy(case["reference"]["structure_artifacts"][0])
        )
    elif mutation == "duplicate_criterion":
        case["gates"]["semiconductor_domain_valid"]["criteria"].append(
            copy.deepcopy(case["gates"]["structure_valid"]["criteria"][0])
        )
        case["gates"]["semiconductor_domain_valid"]["criteria"][-1][
            "evidence_kind"
        ] = "semiconductor_domain"
    elif mutation == "duplicate_rule":
        case["hard_failure_rules"].append(copy.deepcopy(case["hard_failure_rules"][0]))
    else:
        case["reference"]["structure_artifacts"][0]["source_id"] = "missing-source"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert (
        not report.duplicate_ids_rejected
        or EvaluationReason.REFERENTIAL_INTEGRITY_INVALID in report.reason_codes
    )


def test_disabled_gate_must_be_empty_nonrequired_and_not_run(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    criterion = copy.deepcopy(case["gates"]["structure_valid"]["criteria"][0])
    criterion["criterion_id"] = "disabled-criterion"
    criterion["evidence_kind"] = "ms_roundtrip"
    case["gates"]["ms_roundtrip_valid"]["criteria"] = [criterion]
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.disabled_gates_not_run is False
    assert EvaluationReason.DISABLED_GATE_INVALID in report.reason_codes


def test_later_real_environment_gates_cannot_be_enabled_in_this_phase(
    evaluation_fixture,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    criterion = copy.deepcopy(case["gates"]["structure_valid"]["criteria"][0])
    criterion.update(
        {
            "criterion_id": "roundtrip-criterion",
            "metric": "roundtrip.present",
            "comparison_basis": "presence",
            "operator": "present",
            "expected": True,
            "evidence_kind": "ms_roundtrip",
        }
    )
    case["gates"]["ms_roundtrip_valid"] = {
        "state_name": "ms_roundtrip_valid",
        "enabled": True,
        "required_for_overall_pass": True,
        "criteria": [criterion],
        "not_run_reason": None,
    }
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.gate_state_bindings_complete is False
    assert EvaluationReason.BACKEND_EVIDENCE_NOT_AUTHORIZED in report.reason_codes


def test_task_artifact_attestation_is_not_forwarded_or_trusted(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["task"]["input_artifacts"] = [
        {
            "path": "candidate/sic/structure.cif",
            "sha256": evaluation_fixture.submission.structure_sha256,
        }
    ]
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.criterion_bindings_complete is False
    assert EvaluationReason.COORDINATE_DISCLOSURE_RISK in report.reason_codes


def test_gate_name_and_evidence_kind_are_independently_bound(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["gates"]["structure_valid"]["state_name"] = "semiconductor_domain_valid"
    case["gates"]["structure_valid"]["criteria"][0][
        "evidence_kind"
    ] = "semiconductor_domain"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.gate_state_bindings_complete is False


def test_reported_state_and_overall_status_are_recomputed(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"]["states"]["structure_valid"] = "FAIL"
    case["result"]["overall_status"] = "PASS_WITH_WARNINGS"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.required_gate_truth_table_satisfied is False
    assert EvaluationReason.AGGREGATION_INVALID in report.reason_codes


def test_hard_failure_record_requires_an_observed_trigger(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"]["hard_failures"] = [
        {
            "rule_id": "required-gate-failure",
            "validity_state": "structure_valid",
            "message": "fixed failure",
            "evidence": case["result"]["criterion_results"][0]["evidence"],
        }
    ]
    case["result"]["overall_status"] = "FAIL"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.hard_failure_rule_bindings_complete is False
    assert report.counts_reconciled is False


def test_each_triggered_hard_failure_rule_requires_its_own_record(
    evaluation_fixture,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["hard_failure_rules"].append(
        {
            "rule_id": "second-state-failure",
            "applies_to_states": ["structure_valid"],
            "trigger": "state_failed",
            "description": "A second independently declared hard-failure rule.",
            "forces_overall_status": "FAIL",
        }
    )
    case["result"] = _result(evaluation_fixture)
    case["result"]["criterion_results"][0]["observed"] = 0.0
    case["result"]["criterion_results"][0]["status"] = "FAIL"
    case["result"]["states"]["structure_valid"] = "FAIL"
    case["result"]["overall_status"] = "FAIL"
    case["result"]["hard_failures"] = [
        {
            "rule_id": "required-gate-failure",
            "validity_state": "structure_valid",
            "message": "fixed failure",
            "evidence": case["result"]["criterion_results"][0]["evidence"],
        }
    ]
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.hard_failure_rule_bindings_complete is True
    assert report.hard_failure_results_complete is False
    assert report.counts_reconciled is False


def test_result_duplicate_and_missing_criterion_records_fail_closed(
    evaluation_fixture,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"]["criterion_results"].append(
        copy.deepcopy(case["result"]["criterion_results"][0])
    )
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.duplicate_ids_rejected is False
    assert report.counts_reconciled is False


def test_backend_mock_or_real_claim_cannot_upgrade_later_states(
    evaluation_fixture,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"]["real_materials_studio"] = {
        "status": "PASS",
        "real_environment": True,
        "evidence": case["result"]["report_artifacts"],
    }
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.backend_evidence_bindings_complete is False
    assert EvaluationReason.BACKEND_EVIDENCE_NOT_AUTHORIZED in report.reason_codes


def test_result_time_order_is_recomputed(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"]["completed_at"] = "2026-07-19T00:00:00Z"
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert EvaluationReason.TIME_ORDER_INVALID in report.reason_codes


def test_artifact_ads_and_wrong_root_are_rejected(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["reference"]["structure_artifacts"][0]["path"] = (
        "reference/sic/reference.cif:stream"
    )
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.reference_artifacts_bound_to_reference_root is False


def test_validation_and_hidden_holdout_splits_are_not_authorized(
    evaluation_fixture,
) -> None:
    for split in ("validation", "hidden_holdout"):
        case = copy.deepcopy(evaluation_fixture.case)
        case["split"] = split
        report = validate_benchmark_case_semantics(case, roots=evaluation_fixture.roots)
        assert report.valid is False
        assert EvaluationReason.SPLIT_ACCESS_NOT_AUTHORIZED in report.reason_codes


@pytest.mark.parametrize("split", ["validation", "hidden_holdout"])
def test_unauthorized_split_performs_no_filesystem_access(
    evaluation_fixture,
    monkeypatch: pytest.MonkeyPatch,
    split: str,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["split"] = split

    def forbidden_access(*args, **kwargs):
        raise AssertionError("unauthorized split accessed the filesystem")

    monkeypatch.setattr(semantic_module, "verify_isolation_roots", forbidden_access)
    monkeypatch.setattr(semantic_module, "snapshot_candidate_tree", forbidden_access)
    monkeypatch.setattr(semantic_module, "_read_verified_bytes", forbidden_access)
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.reason_codes == (EvaluationReason.SPLIT_ACCESS_NOT_AUTHORIZED,)


@pytest.mark.parametrize(
    ("criterion_index", "observed"),
    [(0, 0.0), (1, 999.0)],
)
def test_reported_pass_cannot_override_recomputed_observation_failure(
    evaluation_fixture,
    criterion_index: int,
    observed: float,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"]["criterion_results"][criterion_index]["observed"] = observed
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.criterion_bindings_complete is False
    assert EvaluationReason.RESULT_BINDING_INVALID in report.reason_codes


@pytest.mark.parametrize("identity_field", ["candidate_sha256", "reference_sha256"])
def test_result_identity_must_match_submitted_candidate_and_reference(
    evaluation_fixture,
    identity_field: str,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    case["result"][identity_field] = "f" * 64
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.result_identity_bindings_complete is False
    assert EvaluationReason.ARTIFACT_IDENTITY_MISMATCH in report.reason_codes


def test_cif_bytes_cannot_masquerade_as_stored_report_json(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    report_payload = evaluation_fixture.candidate_path.read_bytes()
    (evaluation_fixture.evaluator_root / "report.json").write_bytes(report_payload)
    case["result"]["report_artifacts"] = [
        artifact("evaluation/sic/report.json", report_payload)
    ]
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.evaluator_artifacts_bound_to_evaluator_root is False


def test_duplicate_json_keys_cannot_hide_coordinate_disclosure(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["result"] = _result(evaluation_fixture)
    report_path = evaluation_fixture.evaluator_root / "report.json"
    valid_payload = report_path.read_bytes()
    duplicate_payload = b'{"contains_coordinates":true,' + valid_payload[1:]
    report_path.write_bytes(duplicate_payload)
    case["result"]["report_artifacts"] = [
        artifact("evaluation/sic/report.json", duplicate_payload)
    ]
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.evaluator_artifacts_bound_to_evaluator_root is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "unreviewed-canonicalizer"),
        ("method_version", "999.0.0"),
        ("settings_sha256", "f" * 64),
    ],
)
def test_canonicalization_declaration_drift_is_rejected(
    evaluation_fixture,
    field: str,
    value: str,
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["reference"]["canonicalization"][field] = value
    report = validate_benchmark_case_semantics(
        case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.canonicalization_declaration_matches is False
    assert (
        EvaluationReason.CANONICALIZATION_DECLARATION_MISMATCH
        in report.reason_codes
    )


def test_missing_or_aliased_physical_roots_fail_closed(evaluation_fixture) -> None:
    missing = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=None,
        submission=evaluation_fixture.submission,
    )
    assert missing.valid is False
    assert EvaluationReason.PHYSICAL_ROOTS_REQUIRED in missing.reason_codes
    aliased = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=EvaluationRoots(
            reference_root=evaluation_fixture.roots.reference_root,
            candidate_root=evaluation_fixture.roots.reference_root,
            evaluator_output_root=evaluation_fixture.roots.evaluator_output_root,
        ),
        submission=evaluation_fixture.submission,
    )
    assert aliased.valid is False
    assert EvaluationReason.ISOLATION_ROOTS_NOT_DISJOINT in aliased.reason_codes


def test_reference_bytes_must_match_declared_digest(evaluation_fixture) -> None:
    evaluation_fixture.reference_path.write_bytes(b"identity-mismatch")
    report = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is False
    assert report.reference_artifacts_bound_to_reference_root is False


def test_missing_submission_fails_before_filesystem_access(
    evaluation_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_access(*args, **kwargs):
        raise AssertionError("missing submission accessed the filesystem")

    monkeypatch.setattr(semantic_module, "verify_isolation_roots", forbidden_access)
    monkeypatch.setattr(semantic_module, "snapshot_candidate_tree", forbidden_access)
    monkeypatch.setattr(semantic_module, "_read_verified_bytes", forbidden_access)
    report = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
    )
    assert report.valid is False
    assert report.reason_codes == (EvaluationReason.REQUIRED_EVIDENCE_MISSING,)
    assert report.candidate_artifacts_bound_to_candidate_root is False
