"""Fixed, coordinate-free failures for the offline benchmark evaluator."""

from __future__ import annotations

from enum import Enum


class EvaluationReason(str, Enum):
    CONTRACT_INVALID = "contract_invalid"
    PHYSICAL_ROOTS_REQUIRED = "physical_roots_required"
    ISOLATION_ROOT_INVALID = "isolation_root_invalid"
    ISOLATION_ROOTS_NOT_DISJOINT = "isolation_roots_not_disjoint"
    DECLARED_ROOTS_NOT_DISJOINT = "declared_roots_not_disjoint"
    CANDIDATE_ROOT_DECLARATION_MISMATCH = (
        "candidate_root_declaration_mismatch"
    )
    ARTIFACT_ROOT_BINDING_INVALID = "artifact_root_binding_invalid"
    DUPLICATE_IDENTIFIER = "duplicate_identifier"
    REFERENTIAL_INTEGRITY_INVALID = "referential_integrity_invalid"
    DERIVED_COUNT_MISMATCH = "derived_count_mismatch"
    GATE_BINDING_INVALID = "gate_binding_invalid"
    DISABLED_GATE_INVALID = "disabled_gate_invalid"
    RESULT_BINDING_INVALID = "result_binding_invalid"
    AGGREGATION_INVALID = "aggregation_invalid"
    BACKEND_EVIDENCE_NOT_AUTHORIZED = "backend_evidence_not_authorized"
    SPLIT_ACCESS_NOT_AUTHORIZED = "split_access_not_authorized"
    TIME_ORDER_INVALID = "time_order_invalid"
    NON_FINITE_VALUE = "non_finite_value"
    COORDINATE_DISCLOSURE_RISK = "coordinate_disclosure_risk"
    CANDIDATE_TREE_INVALID = "candidate_tree_invalid"
    CANDIDATE_TREE_CHANGED = "candidate_tree_changed"
    ARTIFACT_IDENTITY_MISMATCH = "artifact_identity_mismatch"
    CANONICALIZATION_DECLARATION_MISMATCH = (
        "canonicalization_declaration_mismatch"
    )
    CANONICALIZATION_FAILED = "canonicalization_failed"
    THRESHOLD_FAILED = "threshold_failed"
    REQUIRED_EVIDENCE_MISSING = "required_evidence_missing"


class BenchmarkEvaluationError(RuntimeError):
    """An intentionally redacted evaluator failure."""

    def __init__(self, reason: EvaluationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


__all__ = ["BenchmarkEvaluationError", "EvaluationReason"]
