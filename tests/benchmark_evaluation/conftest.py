from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from material_studio_mcp_server.benchmark_evaluation import (
    CandidateSubmission,
    EvaluationRoots,
    SubmittedCandidateArtifact,
)
from material_studio_mcp_server.canonicalization import (
    CANONICALIZATION_PROFILE,
    IMPLEMENTATION_VERSION,
    CanonicalizationSettings,
    canonicalization_settings_sha256,
)


SYNTHETIC_CIF_BYTES = b"""data_synthetic
_cell_length_a 4.0
_cell_length_b 4.0
_cell_length_c 4.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0 0 0 1
C2 C 0 0.5 0.5 1
C3 C 0.5 0 0.5 1
C4 C 0.5 0.5 0 1
Si1 Si 0.25 0.25 0.25 1
Si2 Si 0.25 0.75 0.75 1
Si3 Si 0.75 0.25 0.75 1
Si4 Si 0.75 0.75 0.25 1
"""


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def artifact(path: str, payload: bytes) -> dict[str, str]:
    return {"path": path, "sha256": sha256(payload)}


def gate(
    state_name: str,
    *,
    enabled: bool,
    required: bool,
    criteria: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "state_name": state_name,
        "enabled": enabled,
        "required_for_overall_pass": required,
        "criteria": criteria,
        "not_run_reason": None if enabled else "not authorized in this phase",
    }


def make_case(*, reference_sha256: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    structure_criterion = {
        "criterion_id": "criterion-structure",
        "metric": "structure.mapping_coverage",
        "comparison_basis": "threshold",
        "operator": "eq",
        "expected": 1.0,
        "tolerance": 0.0,
        "unit": "fraction",
        "severity": "hard_failure",
        "evidence_kind": "structure",
        "description": "The periodic atom mapping must be complete.",
    }
    domain_criterion = {
        "criterion_id": "criterion-domain-vacuum",
        "metric": "surface.vacuum_absolute_error_angstrom",
        "comparison_basis": "threshold",
        "operator": "lte",
        "expected": 0.1,
        "tolerance": 0.0,
        "unit": "angstrom",
        "severity": "hard_failure",
        "evidence_kind": "semiconductor_domain",
        "description": "The trusted vacuum error must satisfy the frozen limit.",
    }
    value: dict[str, Any] = {
        "contract_version": "1.0.0",
        "case_id": "sic-surface-contract",
        "split": "development",
        "domain": "surface",
        "scenario": "surface_slab",
        "material": "3C-SiC",
        "task": {
            "task_id": "sic-si-face",
            "prompt": "Build a 3C-SiC(001) Si-face slab.",
            "semantic_requirements": [
                "Use a 2x2 in-plane cell.",
                "Use four bilayers and 15 angstrom vacuum.",
                "Passivate the back surface with hydrogen.",
            ],
            "declared_assumptions": ["Ideal unreconstructed pre-relaxation model."],
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
                    "provider": "Offline Synthetic Provider",
                    "source_url": "https://reference.test/sic",
                    "retrieved_at": "2026-07-20T00:00:00Z",
                    "query_or_record_id": "synthetic-sic",
                    "license": {
                        "name": "CC0",
                        "spdx_id": "CC0-1.0",
                        "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                        "redistributable": True,
                    },
                    "record_sha256": "1" * 64,
                }
            ],
            "structure_artifacts": [
                {
                    "artifact_id": "reference-sic-cif",
                    "source_id": "source-sic",
                    "path": "reference/sic/reference.cif",
                    "format": "cif",
                    "sha256": reference_sha256,
                    "canonical": True,
                    "contains_coordinates": True,
                }
            ],
            "canonicalization": {
                "method": CANONICALIZATION_PROFILE,
                "method_version": IMPLEMENTATION_VERSION,
                "settings_sha256": canonicalization_settings_sha256(
                    CanonicalizationSettings()
                ),
                "preserves_original_artifact": True,
            },
        },
        "candidate": {
            "root": "candidate/sic",
            "required_artifacts": ["model_spec", "structure"],
            "immutable_after_submission": True,
            "public_entry_tool": "material_studio_live_modeling_request",
        },
        "gates": {
            "structure_valid": gate(
                "structure_valid",
                enabled=True,
                required=True,
                criteria=[structure_criterion],
            ),
            "semiconductor_domain_valid": gate(
                "semiconductor_domain_valid",
                enabled=True,
                required=True,
                criteria=[domain_criterion],
            ),
            "ms_roundtrip_valid": gate(
                "ms_roundtrip_valid", enabled=False, required=False, criteria=[]
            ),
            "calculation_evidence_valid": gate(
                "calculation_evidence_valid",
                enabled=False,
                required=False,
                criteria=[],
            ),
            "scientifically_verified": gate(
                "scientifically_verified", enabled=False, required=False, criteria=[]
            ),
        },
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
                "applies_to_states": [
                    "structure_valid",
                    "semiconductor_domain_valid",
                ],
                "trigger": "state_failed",
                "description": "Required gate failures are not compensable.",
                "forces_overall_status": "FAIL",
            }
        ],
    }
    if result is not None:
        value["result"] = result
    return value


@dataclass(frozen=True)
class EvaluationFixture:
    case: dict[str, Any]
    roots: EvaluationRoots
    submission: CandidateSubmission
    reference_path: Path
    candidate_path: Path
    model_spec_path: Path
    evaluator_root: Path


@pytest.fixture
def evaluation_fixture(tmp_path: Path) -> EvaluationFixture:
    reference_root = tmp_path / "reference" / "sic"
    candidate_root = tmp_path / "candidate" / "sic"
    evaluator_root = tmp_path / "evaluation" / "sic"
    reference_root.mkdir(parents=True)
    candidate_root.mkdir(parents=True)
    evaluator_root.mkdir(parents=True)
    reference_path = reference_root / "reference.cif"
    candidate_path = candidate_root / "structure.cif"
    model_spec_path = candidate_root / "model_spec.json"
    reference_path.write_bytes(SYNTHETIC_CIF_BYTES)
    candidate_path.write_bytes(SYNTHETIC_CIF_BYTES)
    model_spec_payload = b'{"contract":"synthetic-model-spec-v1"}'
    model_spec_path.write_bytes(model_spec_payload)
    reference_sha = sha256(SYNTHETIC_CIF_BYTES)
    return EvaluationFixture(
        case=make_case(reference_sha256=reference_sha),
        roots=EvaluationRoots(
            reference_root=reference_root,
            candidate_root=candidate_root,
            evaluator_output_root=evaluator_root,
        ),
        submission=CandidateSubmission(
            structure_relative_path="structure.cif",
            structure_sha256=reference_sha,
            artifacts=(
                SubmittedCandidateArtifact(
                    kind="model_spec",
                    relative_path="model_spec.json",
                    sha256=sha256(model_spec_payload),
                ),
                SubmittedCandidateArtifact(
                    kind="structure",
                    relative_path="structure.cif",
                    sha256=reference_sha,
                ),
            ),
        ),
        reference_path=reference_path,
        candidate_path=candidate_path,
        model_spec_path=model_spec_path,
        evaluator_root=evaluator_root,
    )
