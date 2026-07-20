"""Deterministic bounded minimum-cost assignment without SciPy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Sequence

import numpy as np

from .contracts import DeterministicAssignment
from .errors import AssignmentWorkLimitError


MAX_ASSIGNMENT_ROWS = 4096
MAX_ASSIGNMENT_ENTRIES = MAX_ASSIGNMENT_ROWS * MAX_ASSIGNMENT_ROWS


@dataclass
class _WorkBudget:
    limit: int
    consumed: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.consumed

    def use(self, amount: int = 1) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("assignment work amount must be a nonnegative integer")
        if amount > self.remaining:
            raise AssignmentWorkLimitError(
                "assignment exceeds the configured deterministic work limit"
            )
        self.consumed += amount


@dataclass(frozen=True)
class _OptimalAssignments:
    total_cost: float
    mappings: tuple[tuple[int, ...], ...]


def _cost_array(
    cost_matrix: Sequence[Sequence[float]],
    budget: _WorkBudget,
) -> np.ndarray:
    if isinstance(cost_matrix, (str, bytes)):
        raise TypeError("cost_matrix must be a square numeric sequence")
    try:
        size = len(cost_matrix)
    except (TypeError, OverflowError) as exc:
        raise TypeError("cost_matrix must be a sized square numeric sequence") from exc
    if size < 1:
        raise ValueError("cost_matrix must be non-empty and square")
    entry_count = size * size
    if size > MAX_ASSIGNMENT_ROWS or entry_count > MAX_ASSIGNMENT_ENTRIES:
        raise AssignmentWorkLimitError("cost_matrix exceeds the assignment dimension bound")

    # Inspection, one-pass Hungarian work, tight-edge inspection, and one
    # complete iterative path are unavoidable even for a unique assignment.
    minimum_work = 3 * entry_count + 4 * size + 1
    if minimum_work > budget.limit - budget.consumed:
        raise AssignmentWorkLimitError(
            "cost_matrix cannot fit the minimum deterministic assignment work budget"
        )

    rows: list[tuple[float, ...]] = []
    for row_index in range(size):
        try:
            row = cost_matrix[row_index]
            row_size = len(row)
        except (IndexError, TypeError, OverflowError) as exc:
            raise TypeError("cost_matrix rows must be sized numeric sequences") from exc
        if isinstance(row, (str, bytes)) or row_size != size:
            raise ValueError("cost_matrix must be non-empty and square")
        converted: list[float] = []
        for column_index in range(size):
            budget.use()
            try:
                raw_value = row[column_index]
            except (IndexError, TypeError) as exc:
                raise ValueError("cost_matrix row length is inconsistent") from exc
            if isinstance(raw_value, bool) or not isinstance(raw_value, Real):
                raise TypeError("cost_matrix values must be real numbers")
            try:
                value = float(raw_value)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("cost_matrix values must be finite") from exc
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    "cost_matrix values must be finite and nonnegative"
                )
            converted.append(value)
        rows.append(tuple(converted))

    values = np.array(rows, dtype=np.float64, copy=True)
    if values.shape != (size, size):
        raise ValueError("cost_matrix must be non-empty and square")
    return values


def _hungarian(
    costs: np.ndarray,
    budget: _WorkBudget,
) -> tuple[tuple[int, ...], float, np.ndarray, np.ndarray]:
    size = costs.shape[0]
    u = np.zeros(size + 1, dtype=np.float64)
    v = np.zeros(size + 1, dtype=np.float64)
    p = np.zeros(size + 1, dtype=np.int64)
    way = np.zeros(size + 1, dtype=np.int64)

    for row in range(1, size + 1):
        p[0] = row
        column0 = 0
        minimum = np.full(size + 1, np.inf, dtype=np.float64)
        used = np.zeros(size + 1, dtype=np.bool_)
        while True:
            budget.use()
            used[column0] = True
            row0 = int(p[column0])
            delta = math.inf
            column1 = 0
            for column in range(1, size + 1):
                if used[column]:
                    continue
                budget.use()
                current = (
                    float(costs[row0 - 1, column - 1])
                    - float(u[row0])
                    - float(v[column])
                )
                if not math.isfinite(current):
                    raise AssignmentWorkLimitError(
                        "assignment arithmetic produced a non-finite reduced cost"
                    )
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta or (
                    minimum[column] == delta and column < column1
                ):
                    delta = float(minimum[column])
                    column1 = column
            if not math.isfinite(delta):
                raise ValueError("cost matrix has no complete finite assignment")
            for column in range(size + 1):
                if used[column]:
                    updated_u = float(u[p[column]]) + delta
                    updated_v = float(v[column]) - delta
                    if not math.isfinite(updated_u) or not math.isfinite(updated_v):
                        raise AssignmentWorkLimitError(
                            "assignment arithmetic produced a non-finite potential"
                        )
                    u[p[column]] = updated_u
                    v[column] = updated_v
                else:
                    updated_minimum = float(minimum[column]) - delta
                    if not math.isfinite(updated_minimum):
                        raise AssignmentWorkLimitError(
                            "assignment arithmetic produced a non-finite slack"
                        )
                    minimum[column] = updated_minimum
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = int(way[column0])
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break

    mapping = [-1] * size
    for column in range(1, size + 1):
        mapping[int(p[column]) - 1] = column - 1
    result = tuple(mapping)
    try:
        total = math.fsum(float(costs[row, column]) for row, column in enumerate(result))
    except OverflowError as exc:
        raise AssignmentWorkLimitError("assignment total cost overflowed") from exc
    if not math.isfinite(total):
        raise AssignmentWorkLimitError("assignment total cost is not finite")
    return result, total, u[1:].copy(), v[1:].copy()


def _all_optimal_assignments(
    costs: np.ndarray,
    *,
    numeric_tolerance: float,
    max_equivalent: int,
    budget: _WorkBudget,
) -> _OptimalAssignments:
    primary, optimum, row_potential, column_potential = _hungarian(costs, budget)
    tight_columns: list[tuple[int, ...]] = []
    for row in range(costs.shape[0]):
        columns_list: list[int] = []
        for column in range(costs.shape[1]):
            budget.use()
            reduced = float(costs[row, column]) - float(row_potential[row])
            if not math.isfinite(reduced):
                raise AssignmentWorkLimitError(
                    "assignment arithmetic produced a non-finite reduced cost"
                )
            reduced -= float(column_potential[column])
            if not math.isfinite(reduced):
                raise AssignmentWorkLimitError(
                    "assignment arithmetic produced a non-finite reduced cost"
                )
            dual_scale = max(
                1.0,
                abs(float(costs[row, column])),
                abs(float(row_potential[row])),
                abs(float(column_potential[column])),
            )
            dual_tolerance = numeric_tolerance * dual_scale
            if not math.isfinite(dual_tolerance):
                raise AssignmentWorkLimitError(
                    "assignment arithmetic produced a non-finite dual tolerance"
                )
            if abs(reduced) <= dual_tolerance:
                columns_list.append(column)
        if primary[row] not in columns_list:
            columns_list.append(primary[row])
        columns = tuple(sorted(columns_list))
        tight_columns.append(columns)

    candidates: list[tuple[tuple[int, ...], float]] = []
    selected = [-1] * costs.shape[0]
    used: set[int] = set()
    next_option = [0] * costs.shape[0]
    row = 0
    while row >= 0:
        budget.use()
        if row == costs.shape[0]:
            mapping = tuple(selected)
            try:
                total = math.fsum(
                    float(costs[index, column])
                    for index, column in enumerate(mapping)
                )
            except OverflowError as exc:
                raise AssignmentWorkLimitError("assignment total cost overflowed") from exc
            if not math.isfinite(total):
                raise AssignmentWorkLimitError("assignment total cost is not finite")
            candidates.append((mapping, total))
            if len(candidates) > max_equivalent:
                raise AssignmentWorkLimitError(
                    "assignment candidate enumeration exceeds max_equivalent_mappings"
                )
            row -= 1
            if row >= 0:
                used.remove(selected[row])
                selected[row] = -1
            continue

        advanced = False
        while next_option[row] < len(tight_columns[row]):
            budget.use()
            column = tight_columns[row][next_option[row]]
            next_option[row] += 1
            if column in used:
                continue
            used.add(column)
            selected[row] = column
            row += 1
            if row < costs.shape[0]:
                next_option[row] = 0
            advanced = True
            break
        if advanced:
            continue

        next_option[row] = 0
        row -= 1
        if row >= 0:
            used.remove(selected[row])
            selected[row] = -1

    if not candidates:
        candidates = [(primary, optimum)]
    strict_minimum = min(total for _, total in candidates)
    if not math.isfinite(strict_minimum):
        raise AssignmentWorkLimitError("assignment strict minimum is not finite")
    minimum_by_mapping: dict[tuple[int, ...], float] = {}
    for mapping, total in candidates:
        if not math.isclose(
            total,
            strict_minimum,
            rel_tol=numeric_tolerance,
            abs_tol=numeric_tolerance,
        ):
            continue
        previous = minimum_by_mapping.get(mapping)
        if previous is None or total < previous:
            minimum_by_mapping[mapping] = total
    unique = tuple(
        mapping
        for mapping, _ in sorted(
            minimum_by_mapping.items(),
            key=lambda item: (item[1], item[0]),
        )
    )
    if not unique:
        raise AssignmentWorkLimitError("assignment produced no finite minimum")
    if len(unique) > max_equivalent:
        raise AssignmentWorkLimitError("assignment exceeds max_equivalent_mappings")
    return _OptimalAssignments(total_cost=strict_minimum, mappings=unique)


def solve_deterministic_assignment(
    cost_matrix: Sequence[Sequence[float]],
    *,
    numeric_tolerance: float = 1.0e-12,
    max_work: int = 50_000_000,
    max_equivalent: int = 10_000,
) -> DeterministicAssignment:
    """Return the lexical representative and explicit optimal degeneracy."""

    if not isinstance(numeric_tolerance, float):
        raise TypeError("numeric_tolerance must be a strict float")
    if not math.isfinite(numeric_tolerance) or not 0.0 < numeric_tolerance <= 1.0e-6:
        raise ValueError("numeric_tolerance is outside the supported bound")
    for value, label in ((max_work, "max_work"), (max_equivalent, "max_equivalent")):
        if type(value) is not int or isinstance(value, bool):
            raise TypeError(f"{label} must be a strict integer")
        if value < 1:
            raise ValueError(f"{label} must be positive")
    budget = _WorkBudget(max_work)
    costs = _cost_array(cost_matrix, budget)
    optimal = _all_optimal_assignments(
        costs,
        numeric_tolerance=numeric_tolerance,
        max_equivalent=max_equivalent,
        budget=budget,
    )
    representative = optimal.mappings[0]
    try:
        representative_cost = math.fsum(
            float(costs[row, column])
            for row, column in enumerate(representative)
        )
    except OverflowError as exc:
        raise AssignmentWorkLimitError("representative assignment cost overflowed") from exc
    if not math.isfinite(representative_cost):
        raise AssignmentWorkLimitError("representative assignment cost is not finite")
    return DeterministicAssignment(
        column_by_row=representative,
        total_cost=representative_cost,
        assignment_degenerate=len(optimal.mappings) > 1,
        equivalent_assignment_count=len(optimal.mappings),
    )


__all__ = ["solve_deterministic_assignment"]
