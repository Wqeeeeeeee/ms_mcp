"""Strict immutable contracts for blind benchmark evaluation."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from material_studio_mcp_server.canonicalization import (
    CoordinateFreeComparisonProjection,
    CoordinateFreeStructureProjection,
)

from .errors import BenchmarkEvaluationError, EvaluationReason


SEMANTIC_VALIDATOR_CONTRACT = "benchmark_evaluation_semantic_validator_v1"
COMPILED_TASK_CONTRACT = "benchmark_coordinate_free_task_v1"
EVALUATION_REPORT_CONTRACT = "benchmark_coordinate_free_report_v1"
STORED_REPORT_CONTRACT = "benchmark_evaluation_stored_report_v1"

Status = Literal["PASS", "PASS_WITH_WARNINGS", "FAIL", "NOT_RUN"]
ValidityStateName = Literal[
    "structure_valid",
    "semiconductor_domain_valid",
    "ms_roundtrip_valid",
    "calculation_evidence_valid",
    "scientifically_verified",
]
Severity = Literal["hard_failure", "warning", "evidence_only"]
EvidenceKind = Literal[
    "structure",
    "semiconductor_domain",
    "ms_roundtrip",
    "calculation",
    "scientific",
]
CandidateArtifactKind = Literal[
    "model_spec",
    "semantic_patch",
    "structure",
    "revision_metadata",
    "ms_roundtrip_structure",
    "calculation_result",
]
Identifier = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MetricName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_.]*$", max_length=128),
]

VALIDITY_STATE_NAMES: tuple[ValidityStateName, ...] = (
    "structure_valid",
    "semiconductor_domain_valid",
    "ms_roundtrip_valid",
    "calculation_evidence_valid",
    "scientifically_verified",
)


class FrozenContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{field_name} must be finite")
    return converted


def _validate_criterion_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _finite_number(value, "criterion value")
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (list, tuple)) and value:
        return tuple(_validate_criterion_value(item) for item in value)
    raise ValueError("criterion value is invalid")


class ArtifactBinding(FrozenContractModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: Sha256


class BlindTask(FrozenContractModel):
    task_id: Identifier
    prompt: str = Field(min_length=1, max_length=8192)
    semantic_requirements: tuple[str, ...] = Field(min_length=1)
    declared_assumptions: tuple[str, ...]
    input_artifacts: tuple[ArtifactBinding, ...]
    expected_output_kind: Literal["molecule", "crystal", "imported_structure"]
    includes_final_reference_coordinates: Literal[False]


class IsolationDeclaration(FrozenContractModel):
    reference_root: str = Field(min_length=1, max_length=512)
    candidate_root: str = Field(min_length=1, max_length=512)
    evaluator_output_root: str = Field(min_length=1, max_length=512)
    reference_visibility: Literal["development_auditor", "evaluator_only"]
    modeler_input_scope: Literal["compiled_task_only"]
    modeler_reference_access: Literal["denied"]
    evaluator_reference_access: Literal["read_only"]
    evaluator_candidate_access: Literal["read_only"]
    reference_coordinates_in_task: Literal[False]
    candidate_write_after_evaluation_start: Literal[False]
    process_isolation_required: Literal[True]


class LicenseRecord(FrozenContractModel):
    name: str = Field(min_length=1, max_length=256)
    spdx_id: str | None = Field(default=None, min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    redistributable: StrictBool


class SourceRecord(FrozenContractModel):
    source_id: Identifier
    provider: str = Field(min_length=1, max_length=256)
    source_url: str = Field(min_length=1, max_length=2048)
    retrieved_at: str = Field(min_length=1, max_length=64)
    query_or_record_id: str = Field(min_length=1, max_length=256)
    citation: str | None = Field(default=None, min_length=1, max_length=4096)
    license: LicenseRecord
    record_sha256: Sha256


class StructureArtifact(FrozenContractModel):
    artifact_id: Identifier
    source_id: Identifier
    path: str = Field(min_length=1, max_length=512)
    format: Literal["cif", "poscar", "cell", "xsd", "json"]
    sha256: Sha256
    canonical: StrictBool
    contains_coordinates: Literal[True]


class CanonicalizationDeclaration(FrozenContractModel):
    method: str = Field(min_length=1, max_length=256)
    method_version: str = Field(min_length=1, max_length=64)
    settings_sha256: Sha256
    preserves_original_artifact: Literal[True]


class ReferenceDeclaration(FrozenContractModel):
    sources: tuple[SourceRecord, ...] = Field(min_length=1)
    structure_artifacts: tuple[StructureArtifact, ...] = Field(min_length=1)
    canonicalization: CanonicalizationDeclaration


class CandidateDeclaration(FrozenContractModel):
    root: str = Field(min_length=1, max_length=512)
    required_artifacts: tuple[CandidateArtifactKind, ...] = Field(min_length=1)
    immutable_after_submission: Literal[True]
    public_entry_tool: Literal["material_studio_live_modeling_request"]

    @field_validator("required_artifacts")
    @classmethod
    def validate_unique_artifact_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("candidate artifact kinds must be unique")
        return value


class GateCriterion(FrozenContractModel):
    criterion_id: Identifier
    metric: MetricName
    comparison_basis: Literal[
        "exact", "reference", "threshold", "presence", "method_compatible"
    ]
    operator: Literal[
        "eq", "ne", "lt", "lte", "gt", "gte", "contains", "set_eq", "present"
    ]
    expected: Any
    tolerance: float | None = Field(default=None, ge=0.0)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    severity: Severity
    evidence_kind: EvidenceKind
    description: str = Field(min_length=1, max_length=2048)

    @field_validator("expected", mode="before")
    @classmethod
    def validate_expected(cls, value: Any) -> Any:
        return _validate_criterion_value(value)

    @field_validator("tolerance", mode="before")
    @classmethod
    def validate_tolerance(cls, value: Any) -> Any:
        if value is None:
            return None
        return _finite_number(value, "tolerance")


class GateDefinition(FrozenContractModel):
    state_name: ValidityStateName
    enabled: StrictBool
    required_for_overall_pass: StrictBool
    criteria: tuple[GateCriterion, ...]
    not_run_reason: str | None = Field(default=None, min_length=1, max_length=1024)


class Gates(FrozenContractModel):
    structure_valid: GateDefinition
    semiconductor_domain_valid: GateDefinition
    ms_roundtrip_valid: GateDefinition
    calculation_evidence_valid: GateDefinition
    scientifically_verified: GateDefinition


class CalculationComparison(FrozenContractModel):
    required_equal_settings: tuple[
        Literal[
            "castep_version",
            "functional",
            "pseudopotential",
            "cutoff_energy",
            "k_points",
            "spin",
            "dft_u",
            "dispersion_correction",
            "fixed_atoms",
            "dipole_correction",
            "convergence_thresholds",
        ],
        ...,
    ]
    mismatch_policy: Literal["cross_method_reference_only"]
    cross_method_results_strictly_scored: Literal[False]

    @field_validator("required_equal_settings")
    @classmethod
    def validate_unique_settings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("calculation settings must be unique")
        return value


class AggregationDeclaration(FrozenContractModel):
    weighted_score_allowed: Literal[False]
    hard_failures_compensable: Literal[False]
    all_required_gates_must_pass: Literal[True]


class HardFailureRule(FrozenContractModel):
    rule_id: Identifier
    applies_to_states: tuple[ValidityStateName, ...] = Field(min_length=1)
    trigger: Literal[
        "criterion_failed",
        "state_failed",
        "required_evidence_missing",
        "method_mismatch",
        "reference_leak",
        "artifact_identity_mismatch",
    ]
    description: str = Field(min_length=1, max_length=2048)
    forces_overall_status: Literal["FAIL"]

    @field_validator("applies_to_states")
    @classmethod
    def validate_unique_states(
        cls, value: tuple[ValidityStateName, ...]
    ) -> tuple[ValidityStateName, ...]:
        if len(value) != len(set(value)):
            raise ValueError("hard failure state bindings must be unique")
        return value


class FiveValidityStates(FrozenContractModel):
    structure_valid: Status
    semiconductor_domain_valid: Status
    ms_roundtrip_valid: Status
    calculation_evidence_valid: Status
    scientifically_verified: Status

    def as_dict(self) -> dict[ValidityStateName, Status]:
        return {
            name: getattr(self, name)
            for name in VALIDITY_STATE_NAMES
        }


class CriterionResult(FrozenContractModel):
    criterion_id: Identifier
    validity_state: ValidityStateName
    severity: Severity
    status: Status
    observed: Any | None = None
    evidence: tuple[ArtifactBinding, ...]
    notes: tuple[str, ...]

    @field_validator("observed", mode="before")
    @classmethod
    def validate_observed(cls, value: Any) -> Any:
        if value is None:
            return None
        return _validate_criterion_value(value)


class HardFailureResult(FrozenContractModel):
    rule_id: Identifier
    validity_state: ValidityStateName
    message: str = Field(min_length=1, max_length=1024)
    evidence: tuple[ArtifactBinding, ...] = Field(min_length=1)


class BackendRunStatus(FrozenContractModel):
    status: Status
    real_environment: StrictBool
    evidence: tuple[ArtifactBinding, ...]


class InputSemanticAttestation(FrozenContractModel):
    validator_contract: Literal["benchmark_evaluation_semantic_validator_v1"]
    gate_state_bindings_complete: Literal[True]
    required_gate_truth_table_satisfied: Literal[True]
    criterion_results_complete: Literal[True]
    criterion_bindings_complete: Literal[True]
    hard_failure_results_complete: Literal[True]
    hard_failure_rule_bindings_complete: Literal[True]
    backend_evidence_bindings_complete: Literal[True]
    isolation_roots_disjoint: Literal[True]
    reference_artifacts_bound_to_reference_root: Literal[True]
    candidate_artifacts_bound_to_candidate_root: Literal[True]
    evaluator_artifacts_bound_to_evaluator_root: Literal[True]
    candidate_root_matches_declared_candidate: Literal[True]


class InputEvaluationResult(FrozenContractModel):
    evaluation_run_id: Identifier
    candidate_sha256: Sha256
    reference_sha256: Sha256
    started_at: str = Field(min_length=1, max_length=64)
    completed_at: str = Field(min_length=1, max_length=64)
    semantic_validation: InputSemanticAttestation
    states: FiveValidityStates
    overall_status: Status
    criterion_results: tuple[CriterionResult, ...] = Field(min_length=1)
    hard_failures: tuple[HardFailureResult, ...]
    warnings: tuple[str, ...]
    real_materials_studio: BackendRunStatus
    real_castep: BackendRunStatus
    report_artifacts: tuple[ArtifactBinding, ...] = Field(min_length=1)


class BenchmarkCase(FrozenContractModel):
    contract_version: Annotated[
        str, StringConstraints(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    ]
    case_id: Identifier
    split: Literal["development", "validation", "hidden_holdout"]
    domain: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    scenario: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    material: str = Field(min_length=1, max_length=128)
    task: BlindTask
    isolation: IsolationDeclaration
    reference: ReferenceDeclaration
    candidate: CandidateDeclaration
    gates: Gates
    calculation_comparison: CalculationComparison
    aggregation: AggregationDeclaration
    hard_failure_rules: tuple[HardFailureRule, ...] = Field(min_length=1)
    result: InputEvaluationResult | None = None


class EvaluationRoots(FrozenContractModel):
    reference_root: Path
    candidate_root: Path
    evaluator_output_root: Path


class SemanticValidationReport(FrozenContractModel):
    validator_contract: Literal["benchmark_evaluation_semantic_validator_v1"] = (
        SEMANTIC_VALIDATOR_CONTRACT
    )
    valid: StrictBool
    reason_codes: tuple[EvaluationReason, ...]
    input_semantic_attestation_ignored: Literal[True] = True
    gate_state_bindings_complete: StrictBool
    required_gate_truth_table_satisfied: StrictBool
    criterion_results_complete: StrictBool
    criterion_bindings_complete: StrictBool
    hard_failure_results_complete: StrictBool
    hard_failure_rule_bindings_complete: StrictBool
    backend_evidence_bindings_complete: StrictBool
    isolation_roots_disjoint: StrictBool
    reference_artifacts_bound_to_reference_root: StrictBool
    candidate_artifacts_bound_to_candidate_root: StrictBool
    evaluator_artifacts_bound_to_evaluator_root: StrictBool
    candidate_root_matches_declared_candidate: StrictBool
    duplicate_ids_rejected: StrictBool
    counts_reconciled: StrictBool
    disabled_gates_not_run: StrictBool
    candidate_tree_immutable: StrictBool
    result_identity_bindings_complete: StrictBool
    canonicalization_declaration_matches: StrictBool
    validity_state_names: tuple[ValidityStateName, ...] = VALIDITY_STATE_NAMES
    required_gate_status_precedence: tuple[Status, ...] = (
        "FAIL",
        "NOT_RUN",
        "PASS_WITH_WARNINGS",
        "PASS",
    )
    hard_failures_compensable: Literal[False] = False


class CandidateTreeSummary(FrozenContractModel):
    digest_sha256: Sha256
    file_count: int = Field(ge=1)
    directory_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)
    contains_paths: Literal[False] = False
    contains_raw_bytes: Literal[False] = False


class SubmittedCandidateArtifact(FrozenContractModel):
    kind: CandidateArtifactKind
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: Sha256


class CandidateSubmission(FrozenContractModel):
    structure_relative_path: str = Field(min_length=1, max_length=512)
    structure_sha256: Sha256
    structure_format: Literal["cif"] = "cif"
    artifacts: tuple[SubmittedCandidateArtifact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_artifact_bindings(self) -> "CandidateSubmission":
        kinds = tuple(item.kind for item in self.artifacts)
        paths = tuple(item.relative_path for item in self.artifacts)
        if len(kinds) != len(set(kinds)) or len(paths) != len(set(paths)):
            raise ValueError("submitted candidate artifacts must be unique")
        structures = tuple(item for item in self.artifacts if item.kind == "structure")
        if len(structures) != 1:
            raise ValueError("one submitted structure artifact is required")
        structure = structures[0]
        if (
            structure.relative_path != self.structure_relative_path
            or structure.sha256 != self.structure_sha256
        ):
            raise ValueError("submitted structure binding does not match")
        return self


class PrecisionThresholds(FrozenContractModel):
    vacuum_absolute_error_angstrom: Literal[0.1] = 0.1
    rms_displacement_angstrom: Literal[0.05] = 0.05
    maximum_displacement_angstrom: Literal[0.15] = 0.15
    maximum_relative_lattice_error: Literal[0.001] = 0.001
    inclusive_lte_boundaries: Literal[True] = True


FIRST_PHASE_THRESHOLDS = PrecisionThresholds()


class TrustedDomainObservation(FrozenContractModel):
    metric: MetricName
    observed: Any
    evidence_sha256: Sha256

    @field_validator("observed", mode="before")
    @classmethod
    def validate_observed(cls, value: Any) -> Any:
        return _validate_criterion_value(value)


class TrustedDomainObservations(FrozenContractModel):
    observations: tuple[TrustedDomainObservation, ...]

    @model_validator(mode="after")
    def validate_unique_metrics(self) -> "TrustedDomainObservations":
        metrics = tuple(item.metric for item in self.observations)
        if len(metrics) != len(set(metrics)):
            raise ValueError("domain observation metrics must be unique")
        return self


class CompiledBlindTask(FrozenContractModel):
    contract_version: Literal["benchmark_coordinate_free_task_v1"] = (
        COMPILED_TASK_CONTRACT
    )
    case_id: Identifier
    task_id: Identifier
    domain: str
    scenario: str
    material: str
    prompt: str
    semantic_requirements: tuple[str, ...]
    declared_assumptions: tuple[str, ...]
    expected_output_kind: Literal["molecule", "crystal", "imported_structure"]
    reference_projection: CoordinateFreeStructureProjection
    contains_final_reference_coordinates: Literal[False] = False
    contains_lattice_vectors: Literal[False] = False
    contains_atom_mapping: Literal[False] = False
    contains_displacement_vectors: Literal[False] = False
    contains_raw_artifact_bytes: Literal[False] = False


class StructureThresholdResults(FrozenContractModel):
    mapping_coverage_pass: StrictBool
    rms_displacement_pass: StrictBool
    maximum_displacement_pass: StrictBool
    lattice_relative_error_pass: StrictBool


class CoordinateFreeEvaluationReport(FrozenContractModel):
    contract_version: Literal["benchmark_coordinate_free_report_v1"] = (
        EVALUATION_REPORT_CONTRACT
    )
    evaluation_run_id: Identifier
    case_id: Identifier
    semantic_validation: SemanticValidationReport
    states: FiveValidityStates
    overall_status: Status
    structure_projection: CoordinateFreeStructureProjection
    comparison_projection: CoordinateFreeComparisonProjection | None
    structure_threshold_results: StructureThresholdResults | None
    trusted_domain_metrics_evaluated: tuple[MetricName, ...]
    hard_failure_reason_codes: tuple[EvaluationReason, ...]
    warning_reason_codes: tuple[EvaluationReason, ...]
    thresholds: PrecisionThresholds = FIRST_PHASE_THRESHOLDS
    candidate_tree_before: CandidateTreeSummary
    candidate_tree_after: CandidateTreeSummary
    candidate_immutable: Literal[True]
    real_materials_studio: Literal["NOT_RUN"] = "NOT_RUN"
    real_castep: Literal["NOT_RUN"] = "NOT_RUN"
    contains_coordinates: Literal[False] = False
    contains_lattice_vectors: Literal[False] = False
    contains_atom_mapping: Literal[False] = False
    contains_displacement_vectors: Literal[False] = False
    contains_raw_artifact_bytes: Literal[False] = False

    @model_validator(mode="after")
    def validate_structure_evidence(self) -> "CoordinateFreeEvaluationReport":
        if self.states.structure_valid in {"PASS", "PASS_WITH_WARNINGS"} and (
            self.comparison_projection is None
            or self.structure_threshold_results is None
        ):
            raise ValueError("passing structure state requires comparison evidence")
        if (self.comparison_projection is None) != (
            self.structure_threshold_results is None
        ):
            raise ValueError("structure evidence must be present or absent together")
        return self


class StoredCoordinateFreeReportArtifact(FrozenContractModel):
    contract_version: Literal["benchmark_evaluation_stored_report_v1"] = (
        STORED_REPORT_CONTRACT
    )
    evaluation_run_id: Identifier
    case_id: Identifier
    candidate_sha256: Sha256
    reference_sha256: Sha256
    states: FiveValidityStates
    overall_status: Status
    contains_coordinates: Literal[False] = False
    contains_lattice_vectors: Literal[False] = False
    contains_atom_mapping: Literal[False] = False
    contains_displacement_vectors: Literal[False] = False
    contains_raw_artifact_bytes: Literal[False] = False


class BenchmarkEvaluationOutcome(FrozenContractModel):
    compiled_task: CompiledBlindTask
    report: CoordinateFreeEvaluationReport


def load_benchmark_case(value: Mapping[str, Any] | BenchmarkCase) -> BenchmarkCase:
    """Copy and revalidate input without rendering untrusted values on failure."""

    try:
        raw: Any
        if isinstance(value, BaseModel):
            raw = value.model_dump(mode="json", warnings=False)
        elif isinstance(value, Mapping):
            raw = dict(value)
        else:
            raise TypeError
        payload = json.dumps(
            raw,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        return BenchmarkCase.model_validate_json(payload, strict=True)
    except (TypeError, ValueError, ValidationError, OverflowError) as exc:
        raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID) from None


def load_candidate_submission(value: CandidateSubmission) -> CandidateSubmission:
    try:
        if not isinstance(value, CandidateSubmission):
            raise TypeError
        payload = json.dumps(
            value.model_dump(mode="json", warnings=False),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        return CandidateSubmission.model_validate_json(payload, strict=True)
    except (TypeError, ValueError, ValidationError, OverflowError):
        raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID) from None


def parse_contract_datetime(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise BenchmarkEvaluationError(EvaluationReason.TIME_ORDER_INVALID)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise BenchmarkEvaluationError(EvaluationReason.TIME_ORDER_INVALID) from None
    if parsed.tzinfo is None:
        raise BenchmarkEvaluationError(EvaluationReason.TIME_ORDER_INVALID)
    return parsed


__all__ = [
    "AggregationDeclaration",
    "ArtifactBinding",
    "BenchmarkCase",
    "BenchmarkEvaluationOutcome",
    "BackendRunStatus",
    "COMPILED_TASK_CONTRACT",
    "CandidateSubmission",
    "CandidateArtifactKind",
    "CandidateTreeSummary",
    "CompiledBlindTask",
    "CoordinateFreeEvaluationReport",
    "EVALUATION_REPORT_CONTRACT",
    "EvaluationRoots",
    "FIRST_PHASE_THRESHOLDS",
    "FiveValidityStates",
    "FrozenContractModel",
    "GateCriterion",
    "GateDefinition",
    "Gates",
    "HardFailureRule",
    "InputEvaluationResult",
    "MetricName",
    "PrecisionThresholds",
    "SEMANTIC_VALIDATOR_CONTRACT",
    "SemanticValidationReport",
    "Severity",
    "Status",
    "StructureThresholdResults",
    "STORED_REPORT_CONTRACT",
    "StoredCoordinateFreeReportArtifact",
    "SubmittedCandidateArtifact",
    "TrustedDomainObservation",
    "TrustedDomainObservations",
    "VALIDITY_STATE_NAMES",
    "ValidityStateName",
    "load_benchmark_case",
    "load_candidate_submission",
    "parse_contract_datetime",
]
