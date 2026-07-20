from __future__ import annotations

import itertools
import math

import pytest

from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkEvaluationError,
    FiveValidityStates,
    VALIDITY_STATE_NAMES,
    aggregate_required_gates,
    evaluate_structure_thresholds,
)


STATUSES = ("PASS", "PASS_WITH_WARNINGS", "FAIL", "NOT_RUN")


def _states(values: tuple[str, ...]) -> FiveValidityStates:
    return FiveValidityStates(**dict(zip(VALIDITY_STATE_NAMES, values, strict=True)))


def _expected(values: tuple[str, ...], indexes: tuple[int, ...]) -> str:
    selected = tuple(values[index] for index in indexes)
    if "FAIL" in selected:
        return "FAIL"
    if "NOT_RUN" in selected:
        return "NOT_RUN"
    if "PASS_WITH_WARNINGS" in selected:
        return "PASS_WITH_WARNINGS"
    return "PASS"


def test_all_five_state_truth_table_combinations_and_required_masks() -> None:
    masks = tuple(
        indexes
        for size in range(1, 6)
        for indexes in itertools.combinations(range(5), size)
    )
    for values in itertools.product(STATUSES, repeat=5):
        states = _states(values)
        for indexes in masks:
            required = tuple(VALIDITY_STATE_NAMES[index] for index in indexes)
            assert aggregate_required_gates(states, required) == _expected(
                values, indexes
            )


def test_hard_failure_cannot_be_compensated() -> None:
    states = _states(("PASS",) * 5)
    assert aggregate_required_gates(
        states,
        ("structure_valid",),
        hard_failure_present=True,
    ) == "FAIL"


def test_failed_warning_downgrades_only_clean_pass() -> None:
    states = _states(("PASS",) * 5)
    assert aggregate_required_gates(
        states,
        ("structure_valid",),
        failed_warning_present=True,
    ) == "PASS_WITH_WARNINGS"


def test_required_state_set_must_be_nonempty_and_unique() -> None:
    states = _states(("PASS",) * 5)
    with pytest.raises(BenchmarkEvaluationError):
        aggregate_required_gates(states, ())
    with pytest.raises(BenchmarkEvaluationError):
        aggregate_required_gates(
            states, ("structure_valid", "structure_valid")
        )


def test_model_construct_cannot_forge_a_state_at_aggregation_boundary() -> None:
    forged = FiveValidityStates.model_construct(
        structure_valid="BOGUS",
        semiconductor_domain_valid="PASS",
        ms_roundtrip_valid="NOT_RUN",
        calculation_evidence_valid="NOT_RUN",
        scientifically_verified="NOT_RUN",
    )
    with pytest.raises(BenchmarkEvaluationError):
        aggregate_required_gates(forged, ("structure_valid",))


def test_frozen_threshold_boundaries_are_inclusive() -> None:
    exact = evaluate_structure_thresholds(
        mapping_coverage=1.0,
        rms_displacement_angstrom=0.05,
        maximum_displacement_angstrom=0.15,
        maximum_relative_lattice_error=0.001,
    )
    assert all(exact.model_dump().values())
    above = evaluate_structure_thresholds(
        mapping_coverage=math.nextafter(1.0, 0.0),
        rms_displacement_angstrom=math.nextafter(0.05, math.inf),
        maximum_displacement_angstrom=math.nextafter(0.15, math.inf),
        maximum_relative_lattice_error=math.nextafter(0.001, math.inf),
    )
    assert not any(above.model_dump().values())


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -math.inf])
def test_thresholds_reject_nonfinite_and_boolean(value: object) -> None:
    with pytest.raises(BenchmarkEvaluationError):
        evaluate_structure_thresholds(
            mapping_coverage=value,
            rms_displacement_angstrom=0.0,
            maximum_displacement_angstrom=0.0,
            maximum_relative_lattice_error=0.0,
        )
