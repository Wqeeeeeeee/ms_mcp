"""Bounded exact periodic geometry for right-handed row-vector lattices."""

from __future__ import annotations

import itertools
import math

import numpy as np

from .contracts import LatticeMetrics, Matrix3, MinimumImageResult, Vector3
from .errors import LatticeError


_MAX_EXACT_INTEGER = 2**53


def _matrix(value: Matrix3, *, label: str) -> np.ndarray:
    matrix = np.array(value, dtype=np.float64, copy=True)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise LatticeError(f"{label} must be a finite 3 by 3 matrix")
    determinant = float(np.linalg.det(matrix))
    if not math.isfinite(determinant) or determinant <= 1.0e-12:
        raise LatticeError(
            f"{label} must be nonsingular and right-handed with positive determinant"
        )
    return matrix


def _vector(value: Vector3, *, label: str) -> np.ndarray:
    vector = np.array(value, dtype=np.float64, copy=True)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise LatticeError(f"{label} must be a finite three-vector")
    return vector


def _clean_float(value: float) -> float:
    result = float(value)
    return 0.0 if result == 0.0 else result


def _tuple3(vector: np.ndarray) -> Vector3:
    return tuple(_clean_float(component) for component in vector)  # type: ignore[return-value]


def _matrix3(matrix: np.ndarray) -> Matrix3:
    return tuple(_tuple3(row) for row in matrix)  # type: ignore[return-value]


def closest_lattice_image(
    fractional_displacement: Vector3,
    lattice: Matrix3,
    *,
    max_candidates: int = 1_000_000,
) -> MinimumImageResult:
    """Solve the closest lattice-vector problem exactly within a proven bound."""

    if type(max_candidates) is not int or isinstance(max_candidates, bool):
        raise TypeError("max_candidates must be a strict integer")
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")

    delta = _vector(fractional_displacement, label="fractional displacement")
    lattice_array = _matrix(lattice, label="lattice")
    singular_values = np.linalg.svd(lattice_array, compute_uv=False)
    sigma_min = float(singular_values[-1])
    sigma_max = float(singular_values[0])
    if not math.isfinite(sigma_min) or sigma_min <= np.finfo(np.float64).tiny:
        raise LatticeError("lattice singular-value bound is not usable")
    singular_value_error = float(
        64.0 * np.finfo(np.float64).eps * sigma_max
    )
    sigma_lower_bound = sigma_min - singular_value_error
    if not math.isfinite(sigma_lower_bound) or sigma_lower_bound <= 0.0:
        raise LatticeError(
            "lattice has no reliable positive lower singular-value bound"
        )

    initial_values: list[int] = []
    for component in delta:
        endpoint = -math.floor(float(component) + 0.5)
        if abs(endpoint) > _MAX_EXACT_INTEGER:
            raise LatticeError("minimum-image translation exceeds exact integer bounds")
        initial_values.append(endpoint)
    initial_translation = tuple(initial_values)
    initial_residual = delta + np.asarray(initial_translation, dtype=np.float64)
    initial_cartesian = initial_residual @ lattice_array
    best_squared = float(initial_cartesian @ initial_cartesian)
    if not math.isfinite(best_squared):
        raise LatticeError("initial minimum-image distance is not finite")

    numerical_radius_padding = 64.0 * np.finfo(np.float64).eps * (
        1.0 + math.sqrt(best_squared)
    )
    fractional_radius = (
        math.sqrt(best_squared) + numerical_radius_padding
    ) / sigma_lower_bound
    if not math.isfinite(fractional_radius):
        raise LatticeError("minimum-image singular-value radius is not finite")
    lower_values: list[int] = []
    upper_values: list[int] = []
    for axis, component in enumerate(delta):
        lower_float = -float(component) - fractional_radius
        upper_float = -float(component) + fractional_radius
        if not math.isfinite(lower_float) or not math.isfinite(upper_float):
            raise LatticeError("minimum-image search endpoint is not finite")
        if (
            lower_float < -_MAX_EXACT_INTEGER
            or upper_float > _MAX_EXACT_INTEGER
        ):
            raise LatticeError("minimum-image search endpoint exceeds exact integer bounds")
        lower_values.append(min(math.ceil(lower_float), initial_translation[axis]))
        upper_values.append(max(math.floor(upper_float), initial_translation[axis]))
    lower = tuple(lower_values)
    upper = tuple(upper_values)

    widths = tuple(upper[index] - lower[index] + 1 for index in range(3))
    if any(width < 1 for width in widths):
        raise LatticeError("minimum-image search produced an invalid integer interval")
    candidate_count = widths[0] * widths[1] * widths[2]
    if candidate_count > max_candidates:
        raise LatticeError(
            "closest-lattice-image search exceeds max_candidates under the "
            "singular-value bound"
        )

    best_translation = initial_translation
    best_fractional = initial_residual
    best_cartesian = initial_cartesian
    distance_degenerate = False
    comparison_floor = float(128.0 * np.finfo(np.float64).eps)

    ranges = (
        range(lower[0], upper[0] + 1),
        range(lower[1], upper[1] + 1),
        range(lower[2], upper[2] + 1),
    )
    for translation in itertools.product(*ranges):
        residual = delta + np.asarray(translation, dtype=np.float64)
        cartesian = residual @ lattice_array
        squared = float(cartesian @ cartesian)
        tolerance = comparison_floor * max(1.0, abs(best_squared), abs(squared))
        if not math.isfinite(squared):
            raise LatticeError("minimum-image squared distance is not finite")
        if squared < best_squared:
            indistinguishable = bool(best_squared - squared <= tolerance)
            best_squared = squared
            best_translation = translation
            best_fractional = residual
            best_cartesian = cartesian
            distance_degenerate = indistinguishable
        elif squared == best_squared:
            if translation != best_translation:
                distance_degenerate = True
            if translation < best_translation:
                best_translation = translation
                best_fractional = residual
                best_cartesian = cartesian
        elif squared - best_squared <= tolerance:
            distance_degenerate = True
    distance = math.hypot(*(float(component) for component in best_cartesian))
    if not math.isfinite(distance):
        raise LatticeError("minimum-image distance is not finite")
    return MinimumImageResult(
        fractional_displacement=_tuple3(best_fractional),
        cartesian_displacement_angstrom=_tuple3(best_cartesian),
        distance_angstrom=_clean_float(distance),
        lattice_translation=best_translation,
        candidates_examined=candidate_count,
        distance_degenerate=bool(distance_degenerate),
    )


def lattice_lengths_and_angles(lattice: Matrix3) -> tuple[Vector3, Vector3]:
    """Return row-vector lengths and alpha, beta, gamma angles."""

    matrix = _matrix(lattice, label="lattice")
    lengths = np.linalg.norm(matrix, axis=1)

    def angle(first: int, second: int) -> float:
        cosine = float(
            np.dot(matrix[first], matrix[second])
            / (lengths[first] * lengths[second])
        )
        return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

    angles = np.array((angle(1, 2), angle(0, 2), angle(0, 1)))
    return _tuple3(lengths), _tuple3(angles)


def compute_lattice_metrics(
    reference_lattice: Matrix3,
    candidate_lattice: Matrix3,
) -> LatticeMetrics:
    """Separate homogeneous lattice deformation from internal displacements."""

    reference = _matrix(reference_lattice, label="reference lattice")
    candidate = _matrix(candidate_lattice, label="candidate lattice")
    reference_lengths, reference_angles = lattice_lengths_and_angles(reference_lattice)
    candidate_lengths, candidate_angles = lattice_lengths_and_angles(candidate_lattice)
    reference_length_array = np.asarray(reference_lengths)
    candidate_length_array = np.asarray(candidate_lengths)
    relative_errors = candidate_length_array / reference_length_array - 1.0
    angle_differences = np.asarray(candidate_angles) - np.asarray(reference_angles)

    deformation_gradient = np.linalg.solve(reference, candidate).T
    symmetric_strain = 0.5 * (
        deformation_gradient + deformation_gradient.T
    ) - np.eye(3)
    determinant_ratio = float(np.linalg.det(candidate) / np.linalg.det(reference))
    if (
        determinant_ratio <= 0.0
        or not math.isfinite(determinant_ratio)
        or not np.isfinite(deformation_gradient).all()
        or not np.isfinite(symmetric_strain).all()
        or not np.isfinite(relative_errors).all()
        or not np.isfinite(angle_differences).all()
    ):
        raise LatticeError("lattice deformation is not a finite proper transform")

    return LatticeMetrics(
        reference_lengths_angstrom=reference_lengths,
        candidate_lengths_angstrom=candidate_lengths,
        reference_angles_degrees=reference_angles,
        candidate_angles_degrees=candidate_angles,
        relative_length_errors=_tuple3(relative_errors),
        angle_differences_degrees=_tuple3(angle_differences),
        maximum_relative_lattice_error=float(np.max(np.abs(relative_errors))),
        deformation_gradient=_matrix3(deformation_gradient),
        symmetric_strain=_matrix3(symmetric_strain),
        determinant_ratio=determinant_ratio,
    )


__all__ = [
    "closest_lattice_image",
    "compute_lattice_metrics",
    "lattice_lengths_and_angles",
]
