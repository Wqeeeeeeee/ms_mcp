from __future__ import annotations

import warnings

import numpy as np
import pytest
from pydantic import ValidationError

from material_studio_mcp_server.canonicalization import (
    AssignmentWorkLimitError,
    DeterministicAssignment,
    solve_deterministic_assignment,
)


def test_assignment_returns_known_unique_minimum() -> None:
    result = solve_deterministic_assignment(
        ((4.0, 1.0, 3.0), (2.0, 0.0, 5.0), (3.0, 2.0, 2.0))
    )
    assert result.column_by_row == (1, 0, 2)
    assert result.total_cost == 5.0
    assert result.assignment_degenerate is False
    assert result.equivalent_assignment_count == 1


def test_assignment_reports_equal_cost_degeneracy_with_lexical_representative() -> None:
    result = solve_deterministic_assignment(((0.0, 0.0), (0.0, 0.0)))
    assert result.column_by_row == (0, 1)
    assert result.assignment_degenerate is True
    assert result.equivalent_assignment_count == 2


def test_assignment_selects_strict_minimum_before_near_tie_lexical_order() -> None:
    epsilon = 1.0e-13
    result = solve_deterministic_assignment(
        ((epsilon, 0.0), (0.0, 0.0)),
        numeric_tolerance=1.0e-12,
    )
    assert result.column_by_row == (1, 0)
    assert result.total_cost == 0.0
    assert result.assignment_degenerate is True


def test_irrelevant_outlier_does_not_promote_a_materially_worse_lexical_assignment() -> None:
    result = solve_deterministic_assignment(
        (
            (0.1, 0.0, 1.0e12),
            (0.0, 0.0, 1.0e12),
            (1.0e12, 1.0e12, 0.0),
        ),
        numeric_tolerance=1.0e-12,
    )
    assert result.column_by_row == (1, 0, 2)
    assert result.total_cost == 0.0
    assert result.assignment_degenerate is False
    assert result.equivalent_assignment_count == 1


def test_assignment_is_repeatedly_deterministic() -> None:
    matrix = ((1.0, 2.0, 3.0), (2.0, 1.0, 3.0), (3.0, 2.0, 1.0))
    assert solve_deterministic_assignment(matrix) == solve_deterministic_assignment(matrix)


def test_assignment_rejects_malformed_costs_and_work_overflow() -> None:
    with pytest.raises(ValueError):
        solve_deterministic_assignment(((1.0, 2.0),))
    with pytest.raises(ValueError):
        solve_deterministic_assignment(((1.0, -1.0), (2.0, 3.0)))
    with pytest.raises(AssignmentWorkLimitError):
        solve_deterministic_assignment(
            tuple(tuple(0.0 for _ in range(8)) for _ in range(8)),
            max_work=20,
        )
    with pytest.raises(AssignmentWorkLimitError):
        solve_deterministic_assignment(
            ((1.0e308, 1.0e308), (1.0e308, 1.0e308))
        )


def test_assignment_rejects_dimensions_and_work_before_touching_lazy_rows() -> None:
    import material_studio_mcp_server.canonicalization.assignment as implementation

    class UntouchedSquare:
        def __init__(self, size: int) -> None:
            self.size = size
            self.touched = False

        def __len__(self) -> int:
            return self.size

        def __getitem__(self, index: int) -> object:
            self.touched = True
            raise AssertionError(f"row {index} must not be materialized")

    oversized = UntouchedSquare(implementation.MAX_ASSIGNMENT_ROWS + 1)
    with pytest.raises(AssignmentWorkLimitError, match="dimension"):
        solve_deterministic_assignment(oversized, max_work=2_000_000_000)  # type: ignore[arg-type]
    assert oversized.touched is False

    underfunded = UntouchedSquare(100)
    with pytest.raises(AssignmentWorkLimitError, match="minimum"):
        solve_deterministic_assignment(underfunded, max_work=100)  # type: ignore[arg-type]
    assert underfunded.touched is False


def test_optimal_assignment_backtracking_is_iterative_above_recursion_limit() -> None:
    import material_studio_mcp_server.canonicalization.assignment as implementation

    size = 1001
    costs = np.ones((size, size), dtype=np.float64)
    np.fill_diagonal(costs, 0.0)
    result = implementation._all_optimal_assignments(
        costs,
        numeric_tolerance=1.0e-12,
        max_equivalent=2,
        budget=implementation._WorkBudget(5_000_000),
    )
    assert result.mappings == (tuple(range(size)),)
    assert result.total_cost == 0.0


def test_tight_edge_arithmetic_fails_closed_without_numpy_overflow_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.assignment as implementation

    def extreme_hungarian(
        costs: np.ndarray,
        budget: object,
    ) -> tuple[tuple[int, ...], float, np.ndarray, np.ndarray]:
        del costs, budget
        return (
            (0,),
            1.7e308,
            np.asarray((-1.7e308,), dtype=np.float64),
            np.asarray((0.0,), dtype=np.float64),
        )

    monkeypatch.setattr(implementation, "_hungarian", extreme_hungarian)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(AssignmentWorkLimitError, match="non-finite reduced cost"):
            implementation._all_optimal_assignments(
                np.asarray(((1.7e308,),), dtype=np.float64),
                numeric_tolerance=1.0e-12,
                max_equivalent=1,
                budget=implementation._WorkBudget(100),
            )


def test_assignment_contract_rejects_nonpermutation_and_bad_degeneracy() -> None:
    with pytest.raises(ValidationError):
        DeterministicAssignment(
            column_by_row=(0, 0),
            total_cost=0.0,
            assignment_degenerate=False,
            equivalent_assignment_count=1,
        )
    with pytest.raises(ValidationError):
        DeterministicAssignment(
            column_by_row=(0, 1),
            total_cost=0.0,
            assignment_degenerate=True,
            equivalent_assignment_count=1,
        )
