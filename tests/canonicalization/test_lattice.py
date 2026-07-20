from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from material_studio_mcp_server.canonicalization import (
    LatticeError,
    closest_lattice_image,
    compute_lattice_metrics,
)


def test_exact_minimum_image_matches_bounded_exhaustive_skew_search() -> None:
    lattice = ((2.0, 0.0, 0.0), (1.8, 0.6, 0.0), (0.3, 0.2, 1.7))
    displacement = (0.49, 0.49, 0.1)
    result = closest_lattice_image(displacement, lattice)
    lattice_array = np.asarray(lattice)
    exhaustive = []
    for translation in itertools.product(range(-3, 4), repeat=3):
        fractional = np.asarray(displacement) + np.asarray(translation)
        exhaustive.append((float(np.linalg.norm(fractional @ lattice_array)), translation))
    expected_distance, expected_translation = min(exhaustive)
    naive = np.asarray(displacement) - np.rint(np.asarray(displacement))
    assert math.isclose(result.distance_angstrom, expected_distance, abs_tol=1.0e-12)
    assert result.lattice_translation == expected_translation
    assert result.distance_angstrom < float(np.linalg.norm(naive @ lattice_array))


def test_minimum_image_is_periodic_deterministic_and_reports_distance_ties() -> None:
    lattice = ((3.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 3.0))
    first = closest_lattice_image((1.5, 0.0, 0.0), lattice)
    second = closest_lattice_image((1.5, 0.0, 0.0), lattice)
    assert first == second
    assert first.distance_degenerate is True
    assert first.lattice_translation == (-2, 0, 0)


def test_minimum_image_selects_strict_numeric_minimum_around_half_cell() -> None:
    lattice = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    below = math.nextafter(0.5, 0.0)
    above = math.nextafter(0.5, 1.0)
    assert closest_lattice_image((below, 0.0, 0.0), lattice).lattice_translation == (
        0,
        0,
        0,
    )
    assert closest_lattice_image((above, 0.0, 0.0), lattice).lattice_translation == (
        -1,
        0,
        0,
    )


def test_minimum_image_fails_when_exact_bound_exceeds_work_limit() -> None:
    lattice = ((1.0, 0.0, 0.0), (0.999999, 0.000001, 0.0), (0.0, 0.0, 1.0))
    with pytest.raises(LatticeError):
        closest_lattice_image((0.5, 0.5, 0.5), lattice, max_candidates=10)


def test_minimum_image_fails_before_integer_bound_overflow() -> None:
    lattice = ((2.0e-20, 0.0, 0.0), (0.0, 1.0e8, 0.0), (0.0, 0.0, 1.0))
    with pytest.raises(LatticeError):
        closest_lattice_image((0.1, 0.49, 0.49), lattice)


def test_minimum_image_uses_conservative_bound_for_high_condition_lattice() -> None:
    lattice = ((1.0e-8, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    result = closest_lattice_image((0.49, 1.0e-9, 0.0), lattice)
    assert result.lattice_translation == (0, 0, 0)
    assert math.isclose(result.distance_angstrom, math.hypot(4.9e-9, 1.0e-9))


def test_minimum_image_preserves_nonzero_sub_picometer_components() -> None:
    lattice = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    displacement = (5.0e-16, -4.0e-16, 3.0e-16)
    result = closest_lattice_image(displacement, lattice)
    assert result.cartesian_displacement_angstrom == displacement
    assert result.distance_angstrom == math.hypot(*displacement)


def test_lattice_metrics_match_analytic_deformation_and_symmetric_strain() -> None:
    reference = np.array(((4.0, 0.0, 0.0), (0.0, 5.0, 0.0), (0.0, 0.0, 6.0)))
    deformation = np.array(((1.02, 0.01, 0.0), (0.0, 0.98, 0.02), (0.0, 0.0, 1.01)))
    candidate = reference @ deformation.T
    metrics = compute_lattice_metrics(
        tuple(tuple(float(value) for value in row) for row in reference),  # type: ignore[arg-type]
        tuple(tuple(float(value) for value in row) for row in candidate),  # type: ignore[arg-type]
    )
    expected_strain = 0.5 * (deformation + deformation.T) - np.eye(3)
    assert np.allclose(np.asarray(metrics.deformation_gradient), deformation, atol=1e-12)
    assert np.allclose(np.asarray(metrics.symmetric_strain), expected_strain, atol=1e-12)
    assert math.isclose(metrics.determinant_ratio, float(np.linalg.det(deformation)))


def test_lattice_metrics_preserve_tiny_deformation_components() -> None:
    reference = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    candidate = ((1.0, 0.0, 0.0), (5.0e-16, 1.0, 0.0), (0.0, 0.0, 1.0))
    metrics = compute_lattice_metrics(reference, candidate)
    assert metrics.deformation_gradient[0][1] == 5.0e-16
    assert metrics.symmetric_strain[0][1] == 2.5e-16
    assert metrics.symmetric_strain[1][0] == 2.5e-16


def test_lattice_geometry_rejects_left_handed_input() -> None:
    with pytest.raises(LatticeError):
        closest_lattice_image(
            (0.0, 0.0, 0.0),
            ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        )
