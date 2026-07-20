from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from jsonschema.validators import validator_for

from schemas.validate_work_order import canonical_sha256, validate_pair


SCHEMAS = Path("schemas")
SHA40 = "a" * 40
SHA64 = "b" * 64


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_load_schema(name), format_checker=FormatChecker())


def _assert_valid(validator: Draft202012Validator, payload: dict[str, Any]) -> None:
    validator.validate(payload)


def _assert_invalid(validator: Draft202012Validator, payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validator.validate(payload)


def _work_order() -> dict[str, Any]:
    required_tests = [
        {
            "test_id": "runtime-unit",
            "category": "unit",
            "command": "python -m pytest -q tests/test_runtime_contracts.py",
            "required": True,
            "environment": "ci",
        },
        {
            "test_id": "runtime-contracts",
            "category": "contract",
            "command": "python -m pytest -q tests/test_runtime_receipts.py",
            "required": True,
            "environment": "ci",
        },
        {
            "test_id": "protocol-preview",
            "category": "protocol_preview",
            "command": "python -m pytest -q tests/test_mcp_protocol_smoke.py",
            "required": True,
            "environment": "ci",
        },
        {
            "test_id": "benchmark-blind",
            "category": "benchmark_blind",
            "command": "not-run-contract-only",
            "required": False,
            "environment": "ci",
        },
        {
            "test_id": "benchmark-regression",
            "category": "benchmark_regression",
            "command": "not-run-contract-only",
            "required": False,
            "environment": "ci",
        },
        {
            "test_id": "reference-leak",
            "category": "no_reference_leak",
            "command": "python -m pytest -q tests/test_runtime_contracts.py",
            "required": True,
            "environment": "ci",
        },
    ]
    return {
        "document_type": "work_order",
        "contract_version": "1.0.0",
        "goal_id": "SEM-PRECISION-MULTI-AGENT-V1",
        "work_order_id": "WO-RUNTIME-001",
        "role": "runtime_orchestration_agent",
        "base_sha": SHA40,
        "expected_branch": "agent/runtime-contract-models-v1",
        "dependencies": [],
        "scope": {
            "scenarios": ["surface_slab"],
            "materials": ["3C-SiC"],
            "operations": ["define_runtime_contracts"],
        },
        "allowed_paths": [
            "src/material_studio_mcp_server/runtime/**",
            "tests/test_runtime_contracts.py",
        ],
        "forbidden_paths": [
            "src/material_studio_mcp_server/server.py",
            "src/material_studio_mcp_server/state/**",
        ],
        "reference_access": {
            "policy": "none",
            "allowed_sources": [],
            "raw_reference_structure_access": False,
            "hidden_holdout_access": False,
            "candidate_write_access": False,
            "coordinate_disclosure_to_modeler": False,
            "constraints": [],
        },
        "required_tests": required_tests,
        "acceptance": {
            "criteria": [
                {
                    "criterion_id": "strict-contracts",
                    "validity_state": "structure_valid",
                    "metric": "contracts.schema_valid",
                    "operator": "eq",
                    "expected": True,
                    "severity": "hard_failure",
                    "evidence_required": True,
                    "real_environment_required": False,
                    "required_real_environment": None,
                }
            ],
            "weighted_score_allowed": False,
            "hard_failures_compensable": False,
        },
        "result_receipt_required": True,
        "notes": ["Contracts only; no router or backend behavior."],
    }


def _benchmark_summary() -> dict[str, Any]:
    return {
        "suite_id": "runtime-contracts",
        "run_id": None,
        "status": "NOT_RUN",
        "case_count": 0,
        "passed": 0,
        "passed_with_warnings": 0,
        "failed": 0,
        "not_run": 0,
        "counts_reconciled": True,
        "report_paths": [],
        "notes": ["No benchmark behavior in this contract-only change."],
    }


def _agent_result_receipt() -> dict[str, Any]:
    work_order = _work_order()
    tests = []
    for requirement in work_order["required_tests"]:
        status = "PASS" if requirement["required"] else "NOT_RUN"
        tests.append(
            {
                **requirement,
                "status": status,
                "evidence_paths": (
                    [f"artifacts/{requirement['test_id']}.txt"] if status == "PASS" else []
                ),
                "summary": (
                    "Required check passed."
                    if status == "PASS"
                    else "Not required for this contract-only change."
                ),
            }
        )
    return {
        "document_type": "agent_result_receipt",
        "contract_version": "1.0.0",
        "goal_id": "SEM-PRECISION-MULTI-AGENT-V1",
        "work_order_id": "WO-RUNTIME-001",
        "role": "runtime_orchestration_agent",
        "branch": "agent/runtime-contract-models-v1",
        "base_sha": SHA40,
        "head_sha": "c" * 40,
        "work_order_binding": {
            "validator_contract": "work_order_result_reconciliation_v1",
            "work_order_sha256": canonical_sha256(work_order),
            "goal_id_matches": True,
            "contract_version_matches": True,
            "work_order_id_matches": True,
            "role_matches": True,
            "base_sha_matches": True,
            "branch_matches": True,
            "dependencies_reconciled": True,
            "dependencies": [],
            "required_test_ids_complete": True,
            "acceptance_criterion_ids_complete": True,
            "changed_paths_within_allowed_paths": True,
            "forbidden_paths_untouched": True,
            "path_scopes_non_overlapping": True,
            "reference_access_matches": True,
        },
        "changed_paths": ["src/material_studio_mcp_server/runtime/contracts.py"],
        "new_capabilities": ["Versioned runtime contract models."],
        "unsupported_capabilities": ["Runtime routing and registry behavior."],
        "reference_sources": [],
        "tests": tests,
        "benchmark_before": _benchmark_summary(),
        "benchmark_after": _benchmark_summary(),
        "acceptance_results": [
            {
                "criterion_id": "strict-contracts",
                "severity": "hard_failure",
                "status": "PASS",
                "observed": True,
                "evidence_paths": ["artifacts/runtime-contracts.txt"],
                "notes": [],
            }
        ],
        "reference_isolation": {
            "policy": "none",
            "complied": True,
            "hidden_reference_read": False,
            "reference_coordinates_disclosed_to_modeler": False,
            "candidate_modified_by_evaluator": False,
            "evidence_paths": [],
            "notes": [],
        },
        "real_materials_studio": {
            "status": "NOT_RUN",
            "environment": "not_run",
            "evidence_paths": [],
            "notes": ["Not required for contract models."],
        },
        "real_castep": {
            "status": "NOT_RUN",
            "environment": "not_run",
            "evidence_paths": [],
            "notes": ["Not required for contract models."],
        },
        "scientific_boundaries": ["No geometry or scientific result is produced."],
        "known_gaps": ["Router behavior is deferred."],
        "contract_changes_requested": [],
        "overall_status": "PASS",
    }


def _source_pin() -> dict[str, Any]:
    return {
        "source_id": "cod-1010995",
        "provider": "Crystallography Open Database",
        "artifact_url": "https://www.crystallography.net/cod/1010995.cif@278158",
        "provider_revision": "278158",
        "expected_sha256": "7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb",
        "expected_byte_count": 3387,
        "media_type": "chemical/x-cif",
        "license_spdx_id": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "redistributable": True,
    }


def _reference_work_order_v11() -> dict[str, Any]:
    work_order = _work_order()
    work_order.update(
        {
            "contract_version": "1.1.0",
            "work_order_id": "WO-REFERENCE-001",
            "role": "reference_data_agent",
            "expected_branch": "agent/reference-ingestion-v1",
            "allowed_paths": [
                "src/material_studio_mcp_server/reference_data/**",
                "tests/reference_data/**",
            ],
            "forbidden_paths": [
                "src/material_studio_mcp_server/server.py",
                "src/material_studio_mcp_server/state/**",
            ],
            "source_pins": [_source_pin()],
        }
    )
    work_order["reference_access"].update(
        {
            "policy": "reference_builder",
            "allowed_sources": [_source_pin()["artifact_url"]],
            "raw_reference_structure_access": True,
        }
    )
    work_order["acceptance"]["criteria"] = [
        {
            "criterion_id": "source-pin-bound",
            "acceptance_domain": "artifact_provenance",
            "validity_state": None,
            "metric": "reference.source_pin_bound",
            "operator": "eq",
            "expected": True,
            "severity": "hard_failure",
            "evidence_required": True,
            "real_environment_required": False,
            "required_real_environment": None,
        }
    ]
    return work_order


def _reference_receipt_v11() -> dict[str, Any]:
    work_order = _reference_work_order_v11()
    receipt = _agent_result_receipt()
    receipt.update(
        {
            "contract_version": "1.1.0",
            "work_order_id": "WO-REFERENCE-001",
            "role": "reference_data_agent",
            "branch": "agent/reference-ingestion-v1",
            "changed_paths": [
                "src/material_studio_mcp_server/reference_data/contracts.py"
            ],
            "new_capabilities": ["Pinned immutable reference ingestion."],
            "unsupported_capabilities": ["Reference canonicalization."],
            "reference_sources": [_source_pin()["artifact_url"]],
            "acceptance_results": [
                {
                    "criterion_id": "source-pin-bound",
                    "severity": "hard_failure",
                    "status": "PASS",
                    "observed": True,
                    "evidence_paths": ["artifacts/source-pin.json"],
                    "notes": [],
                }
            ],
        }
    )
    receipt["reference_isolation"].update(
        {
            "policy": "reference_builder",
            "hidden_reference_read": False,
        }
    )
    receipt["work_order_binding"].update(
        {
            "validator_contract": "work_order_result_reconciliation_v2",
            "work_order_sha256": canonical_sha256(work_order),
            "source_pins": work_order["source_pins"],
            "source_pins_reconciled": True,
            "acceptance_observations_match": True,
        }
    )
    return receipt


def _unpinned_work_order_v11(policy: str) -> dict[str, Any]:
    work_order = _work_order()
    work_order["contract_version"] = "1.1.0"
    work_order["reference_access"].update(
        {
            "policy": policy,
            "allowed_sources": ["https://example.invalid/authorized-metadata.json"],
        }
    )
    work_order["acceptance"]["criteria"][0].update(
        {
            "acceptance_domain": "contract_infrastructure",
            "validity_state": None,
        }
    )
    return work_order


def _unpinned_receipt_v11(policy: str) -> dict[str, Any]:
    work_order = _unpinned_work_order_v11(policy)
    receipt = _agent_result_receipt()
    receipt["contract_version"] = "1.1.0"
    receipt["reference_sources"] = work_order["reference_access"]["allowed_sources"]
    receipt["reference_isolation"]["policy"] = policy
    receipt["work_order_binding"].update(
        {
            "validator_contract": "work_order_result_reconciliation_v2",
            "work_order_sha256": canonical_sha256(work_order),
            "acceptance_observations_match": True,
            "source_pins_reconciled": True,
            "source_pins": [],
        }
    )
    return receipt


def _stage(callable_name: str, inputs: list[str], outputs: list[str]) -> dict[str, Any]:
    return {
        "callable": callable_name,
        "input_contracts": inputs,
        "output_contracts": outputs,
        "deterministic": True,
        "filesystem_side_effects": False,
        "process_side_effects": False,
        "network_access": False,
        "gui_access": False,
    }


def _plugin_manifest() -> dict[str, Any]:
    return {
        "plugin_id": "sic_surface",
        "contract_version": "1.0.0",
        "implementation_version": "1.0.0",
        "description": "Deterministic 3C-SiC surface modeling plugin.",
        "capabilities": {
            "materials": ["3C-SiC"],
            "scenarios": ["surface_slab"],
            "operations": ["create_si_face_slab"],
        },
        "limits": {
            "min_atoms": 1,
            "max_atoms": 10000,
            "supported_periodicity_dimensions": [2, 3],
            "supported_model_kinds": ["crystal"],
            "requires_current_model": False,
            "supports_create": True,
            "supports_patch": False,
            "supports_calculation_plan": False,
            "unsupported_capabilities": ["reconstruction_search"],
        },
        "routing": {
            "priority": 100,
            "ambiguity_policy": "fail_closed",
            "forced_selection_requires_capability_match": True,
        },
        "reference_policy": {
            "allowed_access_modes": ["none", "metadata_only", "task_only"],
            "hidden_holdout_access": False,
            "final_reference_coordinate_access": False,
        },
        "runtime_behavior": {
            "deterministic": True,
            "preview_first": True,
            "mutates_input_model": False,
            "owns_revision_state": False,
            "executes_backend_directly": False,
            "registers_public_mcp_tools": False,
            "owns_gui_session": False,
            "network_access_during_match_plan_build_validate": False,
        },
        "contracts": {
            "match": _stage("sic_surface.match", ["ModelingIntent"], ["MatchResult"]),
            "plan": _stage(
                "sic_surface.plan",
                ["ModelingIntent", "ModelState"],
                ["ModelingPlan"],
            ),
            "build": _stage(
                "sic_surface.build",
                ["ModelingPlan"],
                ["ModelSpec", "SemanticPatch"],
            ),
            "validate": _stage(
                "sic_surface.validate",
                ["ModelSpec"],
                ["DomainValidationReport"],
            ),
        },
        "dependencies": [],
    }


def _artifact(path: str) -> dict[str, str]:
    return {"path": path, "sha256": SHA64}


def _criterion(state: str, suffix: str) -> dict[str, Any]:
    return {
        "criterion_id": f"criterion-{suffix}",
        "metric": f"{suffix}.valid",
        "comparison_basis": "exact",
        "operator": "eq",
        "expected": True,
        "severity": "hard_failure",
        "evidence_kind": (
            "structure" if state == "structure_valid" else "semiconductor_domain"
        ),
        "description": f"Verify {suffix} validity.",
    }


def _gate(state: str, suffix: str, *, enabled: bool, required: bool) -> dict[str, Any]:
    return {
        "state_name": state,
        "enabled": enabled,
        "required_for_overall_pass": required,
        "criteria": [_criterion(state, suffix)] if enabled else [],
        "not_run_reason": None if enabled else "Not part of this architecture test.",
    }


def _benchmark_case() -> dict[str, Any]:
    states = {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    gates = {
        "structure_valid": _gate("structure_valid", "structure", enabled=True, required=True),
        "semiconductor_domain_valid": _gate(
            "semiconductor_domain_valid", "domain", enabled=True, required=True
        ),
        "ms_roundtrip_valid": _gate(
            "ms_roundtrip_valid", "roundtrip", enabled=False, required=False
        ),
        "calculation_evidence_valid": _gate(
            "calculation_evidence_valid", "calculation", enabled=False, required=False
        ),
        "scientifically_verified": _gate(
            "scientifically_verified", "scientific", enabled=False, required=False
        ),
    }
    criterion_results = [
        {
            "criterion_id": "criterion-structure",
            "validity_state": "structure_valid",
            "severity": "hard_failure",
            "status": "PASS",
            "observed": True,
            "evidence": [_artifact("candidate/structure.json")],
            "notes": [],
        },
        {
            "criterion_id": "criterion-domain",
            "validity_state": "semiconductor_domain_valid",
            "severity": "hard_failure",
            "status": "PASS",
            "observed": True,
            "evidence": [_artifact("candidate/domain.json")],
            "notes": [],
        },
    ]
    return {
        "contract_version": "1.0.0",
        "case_id": "sic-surface-contract",
        "split": "development",
        "domain": "surface",
        "scenario": "surface_slab",
        "material": "3C-SiC",
        "task": {
            "task_id": "sic-si-face",
            "prompt": "Build a 3C-SiC(001) Si-face slab.",
            "semantic_requirements": ["Si-face termination"],
            "declared_assumptions": [],
            "input_artifacts": [],
            "expected_output_kind": "crystal",
            "includes_final_reference_coordinates": False,
        },
        "isolation": {
            "reference_root": "reference/sic",
            "candidate_root": "candidate/sic",
            "evaluator_output_root": "evaluation/sic",
            "reference_visibility": "development_auditor",
            "modeler_input_scope": "compiled_task_only",
            "modeler_reference_access": "denied",
            "evaluator_reference_access": "read_only",
            "evaluator_candidate_access": "read_only",
            "reference_coordinates_in_task": False,
            "candidate_write_after_evaluation_start": False,
            "process_isolation_required": True,
        },
        "reference": {
            "sources": [
                {
                    "source_id": "source-sic",
                    "provider": "example",
                    "source_url": "https://example.invalid/sic",
                    "retrieved_at": "2026-07-20T00:00:00Z",
                    "query_or_record_id": "sic-001",
                    "license": {
                        "name": "CC0",
                        "spdx_id": "CC0-1.0",
                        "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                        "redistributable": True,
                    },
                    "record_sha256": SHA64,
                }
            ],
            "structure_artifacts": [
                {
                    "artifact_id": "reference-sic-cif",
                    "source_id": "source-sic",
                    "path": "reference/sic/reference.cif",
                    "format": "cif",
                    "sha256": SHA64,
                    "canonical": True,
                    "contains_coordinates": True,
                }
            ],
            "canonicalization": {
                "method": "identity-test",
                "method_version": "1",
                "settings_sha256": SHA64,
                "preserves_original_artifact": True,
            },
        },
        "candidate": {
            "root": "candidate/sic",
            "required_artifacts": ["model_spec", "structure"],
            "immutable_after_submission": True,
            "public_entry_tool": "material_studio_live_modeling_request",
        },
        "gates": gates,
        "calculation_comparison": {
            "required_equal_settings": [],
            "mismatch_policy": "cross_method_reference_only",
            "cross_method_results_strictly_scored": False,
        },
        "aggregation": {
            "weighted_score_allowed": False,
            "hard_failures_compensable": False,
            "all_required_gates_must_pass": True,
        },
        "hard_failure_rules": [
            {
                "rule_id": "required-gate-failure",
                "applies_to_states": ["structure_valid", "semiconductor_domain_valid"],
                "trigger": "state_failed",
                "description": "A required gate failure is not compensable.",
                "forces_overall_status": "FAIL",
            }
        ],
        "result": {
            "evaluation_run_id": "evaluation-sic-001",
            "candidate_sha256": SHA64,
            "reference_sha256": SHA64,
            "started_at": "2026-07-20T00:00:00Z",
            "completed_at": "2026-07-20T00:01:00Z",
            "semantic_validation": {
                "validator_contract": "benchmark_evaluation_semantic_validator_v1",
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
            },
            "states": states,
            "overall_status": "PASS",
            "criterion_results": criterion_results,
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
            "report_artifacts": [_artifact("evaluation/sic/report.json")],
        },
    }


def test_architecture_schemas_are_draft_2020_12_meta_valid() -> None:
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        validator_for(schema).check_schema(schema)


def test_runtime_work_order_and_receipt_are_valid() -> None:
    validator = _validator("work_order.schema.json")
    _assert_valid(validator, _work_order())
    _assert_valid(validator, _agent_result_receipt())


def test_v10_compatibility_keeps_legacy_observed_and_validity_rules() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _work_order()
    receipt = _agent_result_receipt()
    del receipt["acceptance_results"][0]["observed"]

    _assert_valid(validator, work_order)
    _assert_valid(validator, receipt)
    assert validate_pair(work_order, receipt)["ok"] is True

    invalid_work_order = copy.deepcopy(work_order)
    invalid_work_order["acceptance"]["criteria"][0]["validity_state"] = None
    _assert_invalid(validator, invalid_work_order)

    invalid_work_order = copy.deepcopy(work_order)
    invalid_work_order["acceptance"]["criteria"][0]["acceptance_domain"] = "artifact_provenance"
    _assert_invalid(validator, invalid_work_order)

    invalid_work_order = copy.deepcopy(work_order)
    invalid_work_order["source_pins"] = [_source_pin()]
    _assert_invalid(validator, invalid_work_order)

    for field, value in (
        ("acceptance_observations_match", True),
        ("source_pins_reconciled", True),
        ("source_pins", [_source_pin()]),
    ):
        invalid_receipt = copy.deepcopy(receipt)
        invalid_receipt["work_order_binding"][field] = value
        _assert_invalid(validator, invalid_receipt)

    invalid_receipt = _agent_result_receipt()
    invalid_receipt["acceptance_results"][0]["observed"] = None
    _assert_invalid(validator, invalid_receipt)

    for empty_observation in ("", []):
        invalid_receipt = _agent_result_receipt()
        invalid_receipt["acceptance_results"][0]["observed"] = empty_observation
        _assert_invalid(validator, invalid_receipt)

    legacy_operator_value = copy.deepcopy(work_order)
    legacy_operator_value["acceptance"]["criteria"][0].update(
        {"operator": "lt", "expected": "legacy-compatible-value"}
    )
    _assert_valid(validator, legacy_operator_value)


def test_unknown_contract_versions_fail_closed_without_v1_downgrade() -> None:
    validator = _validator("work_order.schema.json")
    unknown = _work_order()
    unknown["contract_version"] = "1.2.0"
    _assert_invalid(validator, unknown)

    receipt = _agent_result_receipt()
    receipt["contract_version"] = "1.2.0"
    _assert_invalid(validator, receipt)


def test_v11_reference_work_order_pins_source_and_reconciles_observations() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()

    _assert_valid(validator, work_order)
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)

    assert report["ok"] is True
    assert report["validator_contract"] == "work_order_result_reconciliation_v2"
    assert report["checks"]["source_pins_reconciled"] is True
    assert report["checks"]["acceptance_observations_match"] is True


@pytest.mark.parametrize("policy", ["metadata_only", "task_only"])
def test_v11_unpinned_source_policies_use_authorized_subset_rules(policy: str) -> None:
    validator = _validator("work_order.schema.json")
    work_order = _unpinned_work_order_v11(policy)
    receipt = _unpinned_receipt_v11(policy)

    _assert_valid(validator, work_order)
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is True
    assert report["checks"]["reference_access_matches"] is True
    assert report["checks"]["source_pins_reconciled"] is True

    receipt["reference_sources"].append("https://example.invalid/unauthorized.json")
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["reference_access_matches"] is False
    assert report["checks"]["source_pins_reconciled"] is True


def test_v11_reference_work_order_requires_machine_readable_source_pins() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()
    del work_order["source_pins"]
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    work_order["source_pins"][0]["expected_byte_count"] = 0
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    work_order["source_pins"][0]["expected_sha256"] = "not-a-sha256"
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    work_order["source_pins"][0]["artifact_url"] = (
        "https://www.crystallography.net/cod/1010995.cif"
    )
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    work_order["source_pins"][0]["provider"] = "Different Provider"
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    work_order["source_pins"][0]["provider_revision"] = "278159"
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    receipt["work_order_binding"]["source_pins"] = work_order["source_pins"]
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False
    assert any("provider revision" in error for error in report["errors"])


def test_v11_acceptance_domains_do_not_mislabel_infrastructure_as_model_validity() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()
    del work_order["acceptance"]["criteria"][0]["acceptance_domain"]
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    work_order["acceptance"]["criteria"][0]["validity_state"] = "structure_valid"
    _assert_invalid(validator, work_order)

    work_order = _reference_work_order_v11()
    criterion = work_order["acceptance"]["criteria"][0]
    criterion["acceptance_domain"] = "model_validity"
    criterion["validity_state"] = None
    _assert_invalid(validator, work_order)


def test_executed_acceptance_result_requires_observed_value() -> None:
    validator = _validator("work_order.schema.json")
    receipt = _reference_receipt_v11()
    del receipt["acceptance_results"][0]["observed"]
    _assert_invalid(validator, receipt)

    receipt["acceptance_results"][0].update(
        {
            "status": "NOT_RUN",
            "evidence_paths": [],
        }
    )
    receipt["overall_status"] = "FAIL"
    _assert_valid(validator, receipt)


def test_v11_reconciliation_rejects_self_asserted_acceptance_and_source_pins() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()

    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["observed"] = False
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["acceptance_observations_match"] is False

    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["status"] = "FAIL"
    del receipt["acceptance_results"][0]["observed"]
    receipt["overall_status"] = "FAIL"
    _assert_invalid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["checks"]["acceptance_observations_match"] is False

    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["source_pins"][0]["expected_sha256"] = "e" * 64
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False

    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()
    receipt["reference_sources"].append("https://example.invalid/extra.cif")
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False

    work_order = _reference_work_order_v11()
    work_order["reference_access"]["allowed_sources"] = [
        "https://example.invalid/unpinned.cif"
    ]
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False

    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()
    receipt["reference_sources"] = []
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False

    duplicate = copy.deepcopy(_source_pin())
    duplicate["artifact_url"] = "https://www.crystallography.net/cod/1010996.cif@278158"
    work_order = _reference_work_order_v11()
    work_order["source_pins"].append(duplicate)
    work_order["reference_access"]["allowed_sources"].append(duplicate["artifact_url"])
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    receipt["work_order_binding"]["source_pins"] = work_order["source_pins"]
    receipt["reference_sources"] = [
        item["artifact_url"] for item in work_order["source_pins"]
    ]
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("duplicate source pin IDs" in error for error in report["errors"])

    duplicate_url = copy.deepcopy(_source_pin())
    duplicate_url["source_id"] = "cod-1010995-alias"
    work_order = _reference_work_order_v11()
    work_order["source_pins"].append(duplicate_url)
    work_order["reference_access"]["allowed_sources"].append(duplicate_url["artifact_url"])
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    receipt["work_order_binding"]["source_pins"] = work_order["source_pins"]
    receipt["reference_sources"] = [item["artifact_url"] for item in work_order["source_pins"]]
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("duplicate source pin URLs" in error for error in report["errors"])


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("provider", "Different Provider"),
        ("artifact_url", "https://www.crystallography.net/cod/1010995.cif@278157"),
        ("provider_revision", "278157"),
        ("expected_sha256", "e" * 64),
        ("expected_byte_count", 3388),
        ("media_type", "text/plain"),
        ("license_spdx_id", "MIT"),
        ("license_url", "https://opensource.org/license/mit"),
        ("redistributable", False),
    ],
)
def test_v11_receipt_source_pin_field_mismatch_fails_reconciliation(
    field: str,
    replacement: Any,
) -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["source_pins"][0][field] = replacement

    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False


@pytest.mark.parametrize(
    ("operator", "expected", "observed", "tolerance"),
    [
        ("eq", True, True, None),
        ("eq", 10**400, 10**400, None),
        ("eq", [1], [1.0], None),
        ("eq", 1.0, 1.05, 0.1),
        ("ne", False, True, None),
        ("ne", [1], [True], None),
        ("lt", 2, 1, None),
        ("lte", 1, 1, None),
        ("gt", 1, 2, None),
        ("gte", 2, 2, None),
        ("contains", "SiC", "3C-SiC", None),
        ("contains", ["Si", "C"], ["C", "Si", "H"], None),
        ("contains", 1, [1.0], None),
        ("set_eq", ["Si", "C"], ["C", "Si", "Si"], None),
        ("set_eq", [1], [1.0], None),
        ("present", True, "bound", None),
        ("present", True, False, None),
        ("present", True, 0, None),
        ("present", True, "", None),
    ],
)
def test_v11_acceptance_operator_truth_table(
    operator: str,
    expected: Any,
    observed: Any,
    tolerance: float | None,
) -> None:
    work_order = _reference_work_order_v11()
    criterion = work_order["acceptance"]["criteria"][0]
    criterion.update({"operator": operator, "expected": expected})
    if tolerance is not None:
        criterion["tolerance"] = tolerance

    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["observed"] = observed
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)

    report = validate_pair(work_order, receipt)
    assert report["ok"] is True
    assert report["checks"]["acceptance_observations_match"] is True


def test_v11_acceptance_observation_keeps_boolean_and_integer_distinct() -> None:
    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["observed"] = 1

    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["acceptance_observations_match"] is False


def test_v11_acceptance_numeric_equality_accepts_int_and_float() -> None:
    work_order = _reference_work_order_v11()
    work_order["acceptance"]["criteria"][0].update(
        {"expected": 1, "operator": "eq"}
    )
    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["observed"] = 1.0
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)

    report = validate_pair(work_order, receipt)
    assert report["checks"]["acceptance_observations_match"] is True
    assert report["ok"] is True


@pytest.mark.parametrize(
    ("operator", "expected", "observed"),
    [
        ("eq", True, False),
        ("eq", 1, True),
        ("eq", [1], [True]),
        ("ne", False, False),
        ("ne", [1], [1.0]),
        ("lt", 2, 3),
        ("lte", 1, 2),
        ("gt", 1, 0),
        ("gte", 2, 1),
        ("contains", "SiC", "GaN"),
        ("contains", ["Si", "C"], ["Si"]),
        ("contains", True, [1]),
        ("contains", 1, [True]),
        ("set_eq", ["Si", "C"], ["Si"]),
        ("set_eq", [True], [1]),
        ("set_eq", [1], [True]),
        ("present", True, None),
    ],
)
def test_v11_acceptance_operator_negative_truth_table(
    operator: str,
    expected: Any,
    observed: Any,
) -> None:
    work_order = _reference_work_order_v11()
    work_order["acceptance"]["criteria"][0].update(
        {"operator": operator, "expected": expected}
    )
    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["observed"] = observed
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)

    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["acceptance_observations_match"] is False


def test_v11_acceptance_status_must_agree_with_observed_value() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["status"] = "FAIL"
    receipt["overall_status"] = "FAIL"
    _assert_valid(validator, receipt)

    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["acceptance_observations_match"] is False

    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0].update(
        {"status": "NOT_RUN", "evidence_paths": []}
    )
    receipt["overall_status"] = "FAIL"
    _assert_invalid(validator, receipt)

    del receipt["acceptance_results"][0]["observed"]
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("was not run" in error for error in report["errors"])


@pytest.mark.parametrize(
    ("operator", "expected", "tolerance"),
    [
        ("lt", "SiC", None),
        ("set_eq", True, None),
        ("present", False, None),
        ("present", True, 0.1),
        ("eq", True, 0.1),
        ("contains", "SiC", 0.1),
    ],
)
def test_v11_acceptance_schema_rejects_incompatible_operator_values(
    operator: str,
    expected: Any,
    tolerance: float | None,
) -> None:
    validator = _validator("work_order.schema.json")
    work_order = _reference_work_order_v11()
    criterion = work_order["acceptance"]["criteria"][0]
    criterion.update({"operator": operator, "expected": expected})
    if tolerance is not None:
        criterion["tolerance"] = tolerance
    _assert_invalid(validator, work_order)


@pytest.mark.parametrize(
    "field",
    [
        "goal_id_matches",
        "contract_version_matches",
        "work_order_id_matches",
        "role_matches",
        "base_sha_matches",
        "branch_matches",
        "dependencies_reconciled",
        "required_test_ids_complete",
        "acceptance_criterion_ids_complete",
        "changed_paths_within_allowed_paths",
        "forbidden_paths_untouched",
        "path_scopes_non_overlapping",
        "reference_access_matches",
        "acceptance_observations_match",
        "source_pins_reconciled",
    ],
)
def test_v11_false_binding_checks_require_failed_receipt_status(field: str) -> None:
    validator = _validator("work_order.schema.json")
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"][field] = False
    _assert_invalid(validator, receipt)

    receipt["overall_status"] = "FAIL"
    _assert_valid(validator, receipt)
    assert validate_pair(_reference_work_order_v11(), receipt)["ok"] is False


@pytest.mark.parametrize("replacement", [0, 1, "true"])
def test_v11_binding_checks_reject_non_boolean_values(replacement: Any) -> None:
    validator = _validator("work_order.schema.json")
    receipt = _reference_receipt_v11()
    receipt["work_order_binding"]["source_pins_reconciled"] = replacement
    _assert_invalid(validator, receipt)


@pytest.mark.parametrize(
    "field", ["acceptance_observations_match", "source_pins_reconciled", "source_pins"]
)
def test_v11_binding_requires_all_v2_fields(field: str) -> None:
    validator = _validator("work_order.schema.json")
    receipt = _reference_receipt_v11()
    del receipt["work_order_binding"][field]
    _assert_invalid(validator, receipt)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("location", ["expected", "observed", "tolerance"])
def test_v11_non_finite_numbers_fail_closed(location: str, value: float) -> None:
    work_order = _reference_work_order_v11()
    receipt = _reference_receipt_v11()
    if location == "expected":
        work_order["acceptance"]["criteria"][0]["expected"] = value
    elif location == "observed":
        receipt["acceptance_results"][0]["observed"] = value
    else:
        work_order["acceptance"]["criteria"][0].update(
            {"expected": 1.0, "tolerance": value}
        )

    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("non-finite JSON number" in error for error in report["errors"])


def test_json_exponent_overflow_and_canonical_hash_reject_non_finite_numbers() -> None:
    overflow = json.loads("1e999")
    assert overflow == float("inf")
    with pytest.raises(ValueError):
        canonical_sha256({"value": overflow})

    work_order = _reference_work_order_v11()
    work_order["acceptance"]["criteria"][0]["expected"] = overflow
    report = validate_pair(work_order, _reference_receipt_v11())
    assert report["ok"] is False
    assert report["work_order_sha256"] is None


def test_schema_invalid_observations_and_source_pins_return_structured_rejection() -> None:
    work_order = _reference_work_order_v11()
    work_order["acceptance"]["criteria"][0].update(
        {"operator": "set_eq", "expected": ["Si"]}
    )
    receipt = _reference_receipt_v11()
    receipt["acceptance_results"][0]["observed"] = [{}]
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["acceptance_observations_match"] is False
    assert any(error.startswith("receipt:schema:") for error in report["errors"])

    malformed_work_order = _reference_work_order_v11()
    malformed_work_order["source_pins"] = [[]]
    report = validate_pair(malformed_work_order, _reference_receipt_v11())
    assert report["ok"] is False
    assert report["checks"]["source_pins_reconciled"] is False
    assert any(error.startswith("work_order:schema:") for error in report["errors"])

    report = validate_pair([], {})  # type: ignore[arg-type]
    assert report["ok"] is False
    assert report["validator_contract"] is None
    assert any("reconciliation failed closed" in error for error in report["errors"])


def test_modeling_role_cannot_receive_hidden_reference_policy() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _work_order()
    work_order["reference_access"].update(
        {
            "policy": "reference_builder",
            "raw_reference_structure_access": True,
            "hidden_holdout_access": True,
        }
    )
    _assert_invalid(validator, work_order)


def test_work_order_rejects_main_branch_and_real_test_environment_mismatch() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _work_order()
    work_order["expected_branch"] = "main"
    _assert_invalid(validator, work_order)

    work_order = _work_order()
    work_order["required_tests"][0]["category"] = "real_ms_20_1"
    work_order["required_tests"][0]["environment"] = "local_mock"
    _assert_invalid(validator, work_order)

    work_order = _work_order()
    work_order["required_tests"] = [
        item for item in work_order["required_tests"] if item["category"] != "protocol_preview"
    ]
    _assert_invalid(validator, work_order)


def test_required_not_run_test_and_binding_mismatch_cannot_pass() -> None:
    validator = _validator("work_order.schema.json")
    receipt = _agent_result_receipt()
    receipt["tests"][0]["status"] = "NOT_RUN"
    receipt["tests"][0]["evidence_paths"] = []
    _assert_invalid(validator, receipt)
    receipt["overall_status"] = "FAIL"
    _assert_valid(validator, receipt)

    receipt = _agent_result_receipt()
    receipt["work_order_binding"]["required_test_ids_complete"] = False
    _assert_invalid(validator, receipt)


def test_real_environment_labels_and_isolation_violations_fail_closed() -> None:
    validator = _validator("work_order.schema.json")
    receipt = _agent_result_receipt()
    receipt["real_materials_studio"] = {
        "status": "PASS",
        "environment": "real_castep",
        "evidence_paths": ["artifacts/castep.txt"],
        "notes": [],
    }
    _assert_invalid(validator, receipt)

    receipt = _agent_result_receipt()
    receipt["reference_isolation"].update(
        {
            "complied": False,
            "reference_coordinates_disclosed_to_modeler": True,
        }
    )
    _assert_invalid(validator, receipt)
    receipt["overall_status"] = "FAIL"
    _assert_valid(validator, receipt)

    receipt = _agent_result_receipt()
    receipt["reference_isolation"].update(
        {
            "policy": "evaluation_only",
            "hidden_reference_read": True,
        }
    )
    _assert_invalid(validator, receipt)


def test_executed_results_require_evidence() -> None:
    validator = _validator("work_order.schema.json")
    receipt = _agent_result_receipt()
    receipt["tests"][0]["evidence_paths"] = []
    _assert_invalid(validator, receipt)

    receipt = _agent_result_receipt()
    receipt["acceptance_results"][0]["evidence_paths"] = []
    _assert_invalid(validator, receipt)


def test_work_order_semantic_reconciliation_catches_relabeling_and_duplicates() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _work_order()
    receipt = _agent_result_receipt()
    assert validate_pair(work_order, receipt)["ok"] is True

    receipt["tests"][0].update(
        {
            "required": False,
            "status": "NOT_RUN",
            "evidence_paths": [],
        }
    )
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["required_test_ids_complete"] is False

    receipt = _agent_result_receipt()
    receipt["acceptance_results"][0]["severity"] = "warning"
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["acceptance_criterion_ids_complete"] is False

    duplicate = copy.deepcopy(work_order["required_tests"][0])
    duplicate["command"] = "python -m pytest -q tests/test_runtime_receipts.py"
    work_order["required_tests"].append(duplicate)
    receipt = _agent_result_receipt()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    _assert_valid(validator, work_order)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("duplicate test IDs" in error for error in report["errors"])

    receipt = _agent_result_receipt()
    receipt["benchmark_after"].update(
        {
            "run_id": "run-001",
            "status": "PASS",
            "case_count": 1,
            "failed": 1,
            "report_paths": ["artifacts/benchmark-after.json"],
        }
    )
    _assert_valid(validator, receipt)
    report = validate_pair(_work_order(), receipt)
    assert report["ok"] is False
    assert any("benchmark_after status" in error for error in report["errors"])


def test_work_order_reconciliation_binds_sources_versions_paths_and_real_runs() -> None:
    validator = _validator("work_order.schema.json")
    work_order = _work_order()

    receipt = _agent_result_receipt()
    receipt["reference_sources"] = ["hidden/reference.cif"]
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["reference_access_matches"] is False

    receipt = _agent_result_receipt()
    receipt["contract_version"] = "2.0.0"
    _assert_invalid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["contract_version_matches"] is False

    work_order = _work_order()
    work_order["acceptance"]["criteria"][0].update(
        {
            "real_environment_required": True,
            "required_real_environment": "castep",
        }
    )
    receipt = _agent_result_receipt()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    _assert_valid(validator, work_order)
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("requires real castep evidence" in error for error in report["errors"])

    work_order = _work_order()
    real_ms_requirement = {
        "test_id": "real-ms-smoke",
        "category": "real_ms_20_1",
        "command": "python -m pytest -q tests/real/test_ms_smoke.py",
        "required": True,
        "environment": "real_ms",
    }
    work_order["required_tests"].append(real_ms_requirement)
    receipt = _agent_result_receipt()
    receipt["tests"].append(
        {
            **real_ms_requirement,
            "status": "PASS",
            "evidence_paths": ["artifacts/real-ms-smoke.txt"],
            "summary": "Reported as passed.",
        }
    )
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    _assert_valid(validator, work_order)
    _assert_valid(validator, receipt)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert any("real MS run" in error for error in report["errors"])

    work_order = _work_order()
    work_order["forbidden_paths"].append(work_order["allowed_paths"][0])
    receipt = _agent_result_receipt()
    receipt["work_order_binding"]["work_order_sha256"] = canonical_sha256(work_order)
    _assert_valid(validator, work_order)
    report = validate_pair(work_order, receipt)
    assert report["ok"] is False
    assert report["checks"]["path_scopes_non_overlapping"] is False


@pytest.mark.parametrize(
    ("stage", "field", "replacement"),
    [
        ("match", "input_contracts", ["ModelState"]),
        ("match", "output_contracts", ["ModelingPlan"]),
        ("plan", "input_contracts", ["ModelingIntent"]),
        ("plan", "output_contracts", ["MatchResult"]),
        ("build", "input_contracts", ["ModelState"]),
        ("build", "output_contracts", ["ModelSpec"]),
        ("validate", "input_contracts", ["ModelingPlan"]),
        ("validate", "output_contracts", ["MatchResult"]),
    ],
)
def test_plugin_manifest_requires_exact_stage_contracts(
    stage: str,
    field: str,
    replacement: list[str],
) -> None:
    validator = _validator("domain_plugin.schema.json")
    manifest = _plugin_manifest()
    _assert_valid(validator, manifest)

    invalid = copy.deepcopy(manifest)
    invalid["contracts"][stage][field] = replacement
    _assert_invalid(validator, invalid)


def test_plugin_manifest_requires_a_build_output_mode() -> None:
    validator = _validator("domain_plugin.schema.json")
    manifest = _plugin_manifest()
    manifest["limits"].update(
        {
            "supports_create": False,
            "supports_patch": False,
            "supports_calculation_plan": False,
        }
    )
    _assert_invalid(validator, manifest)


def test_benchmark_truth_table_and_semantic_receipt_fail_closed() -> None:
    validator = _validator("benchmark_case.schema.json")
    case = _benchmark_case()
    _assert_valid(validator, case)

    invalid = copy.deepcopy(case)
    invalid["result"]["states"]["structure_valid"] = "NOT_RUN"
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    invalid["result"]["criterion_results"][0]["status"] = "FAIL"
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    del invalid["result"]["semantic_validation"]
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    invalid["result"]["states"]["ms_roundtrip_valid"] = "PASS"
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    invalid["result"]["overall_status"] = "NOT_RUN"
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    invalid["result"]["criterion_results"][0].update(
        {
            "severity": "warning",
            "status": "FAIL",
        }
    )
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    invalid["result"]["criterion_results"][0]["evidence"] = []
    _assert_invalid(validator, invalid)

    invalid = copy.deepcopy(case)
    del invalid["result"]["semantic_validation"]["isolation_roots_disjoint"]
    _assert_invalid(validator, invalid)


def test_disabled_gate_and_backend_mock_cannot_claim_pass() -> None:
    validator = _validator("benchmark_case.schema.json")
    case = _benchmark_case()
    case["gates"]["ms_roundtrip_valid"]["required_for_overall_pass"] = True
    _assert_invalid(validator, case)

    case = _benchmark_case()
    case["result"]["real_materials_studio"]["status"] = "PASS"
    _assert_invalid(validator, case)

    case = _benchmark_case()
    case["gates"]["ms_roundtrip_valid"] = _gate(
        "ms_roundtrip_valid", "roundtrip", enabled=True, required=True
    )
    case["result"]["states"]["ms_roundtrip_valid"] = "PASS"
    case["result"]["criterion_results"].append(
        {
            "criterion_id": "criterion-roundtrip",
            "validity_state": "ms_roundtrip_valid",
            "severity": "hard_failure",
            "status": "PASS",
            "observed": True,
            "evidence": [_artifact("candidate/ms-roundtrip.json")],
            "notes": [],
        }
    )
    _assert_invalid(validator, case)
