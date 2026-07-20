"""Frozen precision thresholds and five-state aggregation."""

from __future__ import annotations

import math
import json
from typing import Any, Mapping

from .contracts import (
    FIRST_PHASE_THRESHOLDS,
    VALIDITY_STATE_NAMES,
    FiveValidityStates,
    GateCriterion,
    Status,
    StructureThresholdResults,
    ValidityStateName,
)
from .errors import BenchmarkEvaluationError, EvaluationReason


_STATUS_PRECEDENCE: tuple[Status, ...] = (
    "FAIL",
    "NOT_RUN",
    "PASS_WITH_WARNINGS",
    "PASS",
)


def _finite_scalar(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkEvaluationError(EvaluationReason.NON_FINITE_VALUE)
    converted = float(value)
    if not math.isfinite(converted):
        raise BenchmarkEvaluationError(EvaluationReason.NON_FINITE_VALUE)
    return converted


def evaluate_structure_thresholds(
    *,
    mapping_coverage: float,
    rms_displacement_angstrom: float,
    maximum_displacement_angstrom: float,
    maximum_relative_lattice_error: float,
) -> StructureThresholdResults:
    coverage = _finite_scalar(mapping_coverage)
    rms = _finite_scalar(rms_displacement_angstrom)
    maximum = _finite_scalar(maximum_displacement_angstrom)
    lattice = _finite_scalar(maximum_relative_lattice_error)
    return StructureThresholdResults(
        mapping_coverage_pass=coverage == 1.0,
        rms_displacement_pass=(
            rms <= FIRST_PHASE_THRESHOLDS.rms_displacement_angstrom
        ),
        maximum_displacement_pass=(
            maximum <= FIRST_PHASE_THRESHOLDS.maximum_displacement_angstrom
        ),
        lattice_relative_error_pass=(
            lattice <= FIRST_PHASE_THRESHOLDS.maximum_relative_lattice_error
        ),
    )


def structure_threshold_status(results: StructureThresholdResults) -> Status:
    values = (
        results.mapping_coverage_pass,
        results.rms_displacement_pass,
        results.maximum_displacement_pass,
        results.lattice_relative_error_pass,
    )
    return "PASS" if all(values) else "FAIL"


def evaluate_criterion(criterion: GateCriterion, observed: Any) -> bool:
    operator = criterion.operator
    expected = criterion.expected
    tolerance = criterion.tolerance or 0.0
    if operator == "present":
        return observed is not None
    if operator in {"lt", "lte", "gt", "gte"}:
        actual_number = _finite_scalar(observed)
        expected_number = _finite_scalar(expected)
        if operator == "lt":
            return actual_number < expected_number
        if operator == "lte":
            return actual_number <= expected_number
        if operator == "gt":
            return actual_number > expected_number
        return actual_number >= expected_number
    if operator in {"eq", "ne"}:
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            actual_number = _finite_scalar(observed)
            expected_number = _finite_scalar(expected)
            equal = math.isclose(
                actual_number,
                expected_number,
                rel_tol=0.0,
                abs_tol=float(tolerance),
            )
        else:
            equal = observed == expected
        return equal if operator == "eq" else not equal
    if operator == "contains":
        if isinstance(observed, str) and isinstance(expected, str):
            return expected in observed
        if isinstance(observed, (tuple, list)):
            return expected in observed
        return False
    if operator == "set_eq":
        if not isinstance(observed, (tuple, list)) or not isinstance(
            expected, (tuple, list)
        ):
            return False
        try:
            return set(observed) == set(expected)
        except TypeError:
            return False
    raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID)


def aggregate_required_gates(
    states: FiveValidityStates,
    required_states: tuple[ValidityStateName, ...],
    *,
    hard_failure_present: bool = False,
    failed_warning_present: bool = False,
) -> Status:
    """Apply FAIL > NOT_RUN > PASS_WITH_WARNINGS > PASS exactly."""

    try:
        if not isinstance(states, FiveValidityStates):
            raise TypeError
        states = FiveValidityStates.model_validate_json(
            json.dumps(
                states.model_dump(mode="json", warnings=False),
                allow_nan=False,
                separators=(",", ":"),
            ),
            strict=True,
        )
    except (TypeError, ValueError):
        raise BenchmarkEvaluationError(EvaluationReason.AGGREGATION_INVALID) from None
    if not required_states or len(required_states) != len(set(required_states)):
        raise BenchmarkEvaluationError(EvaluationReason.AGGREGATION_INVALID)
    if any(name not in VALIDITY_STATE_NAMES for name in required_states):
        raise BenchmarkEvaluationError(EvaluationReason.AGGREGATION_INVALID)
    if type(hard_failure_present) is not bool or type(failed_warning_present) is not bool:
        raise BenchmarkEvaluationError(EvaluationReason.AGGREGATION_INVALID)
    if hard_failure_present:
        return "FAIL"
    state_map = states.as_dict()
    selected = tuple(state_map[name] for name in required_states)
    if "FAIL" in selected:
        return "FAIL"
    if "NOT_RUN" in selected:
        return "NOT_RUN"
    if "PASS_WITH_WARNINGS" in selected or failed_warning_present:
        return "PASS_WITH_WARNINGS"
    return "PASS"


def derive_gate_status(
    criteria: tuple[GateCriterion, ...],
    result_statuses: Mapping[str, Status],
) -> Status:
    """Derive one enabled gate without trusting a reported state."""

    if not criteria:
        raise BenchmarkEvaluationError(EvaluationReason.GATE_BINDING_INVALID)
    normalized: list[Status] = []
    for criterion in criteria:
        if criterion.criterion_id not in result_statuses:
            raise BenchmarkEvaluationError(EvaluationReason.DERIVED_COUNT_MISMATCH)
        status = result_statuses[criterion.criterion_id]
        if criterion.severity == "warning" and status == "FAIL":
            normalized.append("PASS_WITH_WARNINGS")
        elif criterion.severity == "evidence_only" and status == "FAIL":
            normalized.append("PASS_WITH_WARNINGS")
        else:
            normalized.append(status)
    for status in _STATUS_PRECEDENCE:
        if status in normalized:
            return status
    raise BenchmarkEvaluationError(EvaluationReason.AGGREGATION_INVALID)


__all__ = [
    "aggregate_required_gates",
    "derive_gate_status",
    "evaluate_criterion",
    "evaluate_structure_thresholds",
    "structure_threshold_status",
]
