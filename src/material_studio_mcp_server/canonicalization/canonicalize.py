"""Deterministic spglib-backed crystal standardization and projection."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import warnings
from collections import Counter
from numbers import Integral, Real
from typing import Callable, Iterable, TypeVar

import numpy as np
import spglib

from material_studio_mcp_server.runtime.contracts import (
    ContractDigest,
    canonical_json_bytes,
    contract_digest,
)

from ._elements import atomic_number, element_symbol
from .cif import parse_cif_structure
from .contracts import (
    CANONICALIZATION_CONTRACT_VERSION,
    CANONICALIZATION_PROFILE,
    STRUCTURE_PROJECTION_PROFILE,
    CanonicalReferenceArtifact,
    CanonicalSite,
    CanonicalStructure,
    CanonicalizationSettings,
    CoordinateFreeStructureProjection,
    Matrix3,
    PeriodicStructure,
    SpeciesCount,
    SymmetryClassification,
)
from .errors import LatticeError, StandardizationError
from .lattice import closest_lattice_image


_SpglibResult = TypeVar("_SpglibResult")
_SPGLIB_27_DEPRECATION = (
    "Set OLD_ERROR_HANDLING to false and catch the errors directly."
)


def _call_spglib(
    operation: str,
    function: Callable[..., _SpglibResult],
    *args: object,
    **kwargs: object,
) -> _SpglibResult:
    """Normalize the spglib 2.7-to-2.x error transition locally."""

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=re.escape(_SPGLIB_27_DEPRECATION),
            category=DeprecationWarning,
        )
        try:
            return function(*args, **kwargs)
        except spglib.SpglibError as exc:
            raise StandardizationError(f"spglib {operation} failed") from exc


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonicalization_settings_sha256(settings: CanonicalizationSettings) -> str:
    if not isinstance(settings, CanonicalizationSettings):
        raise TypeError("settings must be CanonicalizationSettings")
    return canonical_sha256(settings)


def canonical_structure_sha256(structure: CanonicalStructure) -> str:
    if not isinstance(structure, CanonicalStructure):
        raise TypeError("structure must be CanonicalStructure")
    return canonical_sha256(structure)


def canonical_structure_digest(structure: CanonicalStructure) -> ContractDigest:
    if not isinstance(structure, CanonicalStructure):
        raise TypeError("structure must be CanonicalStructure")
    return contract_digest(
        structure,
        contract_name="CanonicalStructure",
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
    )


def _proper_signed_permutations() -> tuple[np.ndarray, ...]:
    matrices: list[np.ndarray] = []
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            matrix = np.zeros((3, 3), dtype=np.int64)
            for row, column in enumerate(permutation):
                matrix[row, column] = signs[row]
            determinant = int(round(float(np.linalg.det(matrix))))
            if determinant == 1:
                matrices.append(matrix)
    matrices.sort(key=lambda item: tuple(int(value) for value in item.flat))
    return tuple(matrices)


_PROPER_SIGNED_PERMUTATIONS = _proper_signed_permutations()
for _orientation_transform in _PROPER_SIGNED_PERMUTATIONS:
    _orientation_transform.setflags(write=False)


def proper_orientation_basis_transforms() -> tuple[
    tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]], ...
]:
    """Return the closed proper signed-permutation basis set."""

    return tuple(
        tuple(
            tuple(int(value) for value in row)
            for row in transform
        )  # type: ignore[misc]
        for transform in _PROPER_SIGNED_PERMUTATIONS
    )


def _wrap_positions(positions: np.ndarray) -> np.ndarray:
    wrapped = positions - np.floor(positions)
    wrapped[wrapped == 0.0] = 0.0
    wrapped[wrapped == 1.0] = 0.0
    return wrapped


def _quantize_scalar(value: float, decimals: int) -> float:
    rounded = round(float(value), decimals)
    return 0.0 if rounded == 0.0 else rounded


def _selection_scalar(value: float, decimals: int, *, periodic: bool) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise StandardizationError("canonical selection key contains a non-finite value")
    if periodic:
        numeric -= math.floor(numeric)
    selected = _quantize_scalar(numeric, decimals)
    if periodic and selected == 1.0:
        selected = 0.0
    return selected


def _proper_cartesian_orientation(lattice: np.ndarray) -> np.ndarray:
    """Remove global rigid orientation with a positive-determinant rotation."""

    gram = lattice @ lattice.T
    try:
        oriented = np.linalg.cholesky(gram)
    except np.linalg.LinAlgError as exc:
        raise StandardizationError("standardized lattice Gram matrix is not positive definite") from exc
    rotation = np.linalg.solve(lattice, oriented)
    determinant = float(np.linalg.det(rotation))
    orthogonality_error = float(np.max(np.abs(rotation @ rotation.T - np.eye(3))))
    if determinant <= 0.0 or not math.isclose(
        determinant,
        1.0,
        rel_tol=1.0e-8,
        abs_tol=1.0e-8,
    ) or orthogonality_error > 1.0e-8:
        raise StandardizationError(
            "canonical Cartesian orientation is not a verified proper rotation"
        )
    return oriented


def _origin_normalized(
    positions: np.ndarray,
    numbers: np.ndarray,
    settings: CanonicalizationSettings,
) -> tuple[np.ndarray, np.ndarray, tuple[tuple[object, ...], ...]]:
    counts = Counter(int(number) for number in numbers)
    anchor_number = min(counts, key=lambda number: (counts[number], number))
    anchor_indices = tuple(
        index for index, number in enumerate(numbers) if int(number) == anchor_number
    )
    best_positions: np.ndarray | None = None
    best_numbers: np.ndarray | None = None
    best_key: tuple[tuple[object, ...], ...] | None = None
    best_tie_key: tuple[tuple[object, ...], ...] | None = None
    for anchor_index in anchor_indices:
        shifted = _wrap_positions(positions - positions[anchor_index])
        selection_positions = tuple(
            tuple(
                _selection_scalar(
                    value,
                    settings.quantization_decimals,
                    periodic=True,
                )
                for value in shifted[index]
            )
            for index in range(len(numbers))
        )
        order = sorted(
            range(len(numbers)),
            key=lambda index: (
                int(numbers[index]),
                *selection_positions[index],
                *(float(value) for value in shifted[index]),
            ),
        )
        ordered_positions = shifted[order]
        ordered_numbers = numbers[order]
        key = tuple(
            (
                int(ordered_numbers[index]),
                *selection_positions[order[index]],
            )
            for index in range(len(order))
        )
        tie_key = tuple(
            (
                int(ordered_numbers[index]),
                *(float(value) for value in ordered_positions[index]),
            )
            for index in range(len(order))
        )
        if (
            best_key is None
            or key < best_key
            or (key == best_key and (best_tie_key is None or tie_key < best_tie_key))
        ):
            best_key = key
            best_tie_key = tie_key
            best_positions = ordered_positions
            best_numbers = ordered_numbers
    if (
        best_positions is None
        or best_numbers is None
        or best_key is None
        or best_tie_key is None
    ):
        raise StandardizationError("origin normalization produced no anchor")
    return best_positions, best_numbers, best_key


def _canonical_basis(
    lattice: np.ndarray,
    positions: np.ndarray,
    numbers: np.ndarray,
    settings: CanonicalizationSettings,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    candidates: list[
        tuple[
            tuple[object, ...],
            tuple[object, ...],
            np.ndarray,
            np.ndarray,
            np.ndarray,
        ]
    ] = []
    for transform in _PROPER_SIGNED_PERMUTATIONS:
        transformed_lattice = transform @ lattice
        transformed_positions = _wrap_positions(positions @ transform.T)
        oriented_lattice = _proper_cartesian_orientation(transformed_lattice)
        normalized_positions, normalized_numbers, position_key = _origin_normalized(
            transformed_positions,
            numbers,
            settings,
        )
        lattice_key = tuple(
            _selection_scalar(
                value,
                settings.quantization_decimals,
                periodic=False,
            )
            for value in oriented_lattice.flat
        )
        key: tuple[object, ...] = (*lattice_key, position_key)
        tie_key: tuple[object, ...] = (
            *(float(value) for value in oriented_lattice.flat),
            tuple(
                (
                    int(normalized_numbers[index]),
                    *(float(value) for value in normalized_positions[index]),
                )
                for index in range(len(normalized_numbers))
            ),
        )
        candidates.append(
            (
                key,
                tie_key,
                oriented_lattice,
                normalized_positions,
                normalized_numbers,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, selected_lattice, selected_positions, selected_numbers = candidates[0]
    if float(np.linalg.det(selected_lattice)) <= 0.0:
        raise StandardizationError("canonical basis is not right-handed")
    return selected_lattice, selected_positions, selected_numbers


def _validate_unique_sites(
    lattice: Matrix3,
    positions: np.ndarray,
    numbers: np.ndarray,
    settings: CanonicalizationSettings,
) -> None:
    candidate_work = 0
    for first in range(len(positions)):
        for second in range(first):
            remaining_work = settings.max_duplicate_site_checks - candidate_work
            if remaining_work < 1:
                raise StandardizationError(
                    "duplicate-site validation exceeds max_duplicate_site_checks "
                    "candidate-work budget"
                )
            delta = tuple(float(value) for value in positions[first] - positions[second])
            try:
                image = closest_lattice_image(
                    delta,  # type: ignore[arg-type]
                    lattice,
                    max_candidates=min(1_000_000, remaining_work),
                )
            except LatticeError as exc:
                raise StandardizationError(
                    "duplicate-site validation exceeds max_duplicate_site_checks "
                    "candidate-work budget"
                ) from exc
            candidate_work += image.candidates_examined
            if candidate_work > settings.max_duplicate_site_checks:
                raise StandardizationError(
                    "duplicate-site validation exceeds max_duplicate_site_checks "
                    "candidate-work budget"
                )
            if image.distance_angstrom <= settings.duplicate_site_tolerance_angstrom:
                first_species = element_symbol(int(numbers[first]))
                second_species = element_symbol(int(numbers[second]))
                if first_species == second_species:
                    raise StandardizationError("duplicate periodic atom sites are unsupported")
                raise StandardizationError(
                    "different species occupy one periodic atom site"
                )


def _quantize(
    lattice: np.ndarray,
    positions: np.ndarray,
    settings: CanonicalizationSettings,
) -> tuple[np.ndarray, np.ndarray]:
    if not np.isfinite(lattice).all() or not np.isfinite(positions).all():
        raise StandardizationError("quantization input contains non-finite values")
    quantized_lattice = np.array(
        [
            [_quantize_scalar(value, settings.quantization_decimals) for value in row]
            for row in lattice
        ],
        dtype=np.float64,
    )
    if not np.isfinite(quantized_lattice).all():
        raise StandardizationError("lattice quantization produced non-finite values")
    lattice_error = float(np.max(np.abs(quantized_lattice - lattice)))
    if not math.isfinite(lattice_error) or lattice_error > settings.max_lattice_quantization_error_angstrom:
        raise StandardizationError("lattice quantization exceeds its configured error bound")
    quantized_determinant = float(np.linalg.det(quantized_lattice))
    if not math.isfinite(quantized_determinant) or quantized_determinant <= 1.0e-12:
        raise StandardizationError("lattice quantization lost right-handed nonsingularity")

    wrapped = _wrap_positions(positions)
    quantized_positions = np.array(
        [
            [_quantize_scalar(value, settings.quantization_decimals) for value in row]
            for row in wrapped
        ],
        dtype=np.float64,
    )
    quantized_positions = _wrap_positions(quantized_positions)
    if not np.isfinite(quantized_positions).all():
        raise StandardizationError("coordinate quantization produced non-finite values")
    periodic_delta = quantized_positions - wrapped
    periodic_delta -= np.rint(periodic_delta)
    coordinate_error = float(np.max(np.abs(periodic_delta)))
    if not math.isfinite(coordinate_error) or coordinate_error > settings.max_fractional_quantization_error:
        raise StandardizationError(
            "fractional-coordinate quantization exceeds its configured error bound"
        )
    return quantized_lattice, quantized_positions


def _exact_spglib_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise StandardizationError(f"spglib returned a non-integral {label}")
    if isinstance(value, Integral):
        integer = int(value)
    else:
        numeric = float(value)
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise StandardizationError(f"spglib returned a non-integral {label}")
        integer = int(numeric)
    if not minimum <= integer <= maximum:
        raise StandardizationError(f"spglib returned an out-of-range {label}")
    return integer


def _validated_spglib_atomic_numbers(
    raw_values: object,
    *,
    input_numbers: np.ndarray,
    mode: str,
) -> np.ndarray:
    try:
        values = np.asarray(raw_values)
    except (TypeError, ValueError) as exc:
        raise StandardizationError("spglib returned malformed standardized type data") from exc
    if values.ndim != 1 or len(values) < 1:
        raise StandardizationError("spglib returned malformed standardized type data")
    validated: list[int] = []
    for raw_value in values.tolist():
        atomic_value = _exact_spglib_integer(
            raw_value,
            label="atomic type",
            minimum=1,
            maximum=118,
        )
        try:
            element_symbol(atomic_value)
        except ValueError as exc:
            raise StandardizationError("spglib returned an unknown atomic type") from exc
        validated.append(atomic_value)

    expected = Counter(int(value) for value in input_numbers)
    observed = Counter(validated)
    input_count = sum(expected.values())
    output_count = sum(observed.values())
    if set(observed) != set(expected) or any(
        observed[number] * input_count != expected[number] * output_count
        for number in expected
    ):
        raise StandardizationError(
            f"spglib {mode} standardization did not preserve composition"
        )
    return np.asarray(validated, dtype=np.intc)


def _validated_equivalence_indices(
    raw_values: object,
    *,
    site_count: int,
) -> tuple[int, ...]:
    try:
        values = np.asarray(raw_values)
    except (TypeError, ValueError) as exc:
        raise StandardizationError("spglib returned malformed equivalence indices") from exc
    if values.ndim != 1 or len(values) != site_count:
        raise StandardizationError("spglib symmetry labels do not cover every site")
    indices = tuple(
        _exact_spglib_integer(
            value,
            label="equivalence index",
            minimum=0,
            maximum=site_count - 1,
        )
        for value in values.tolist()
    )
    if any(indices[indices[index]] != indices[index] for index in range(site_count)):
        raise StandardizationError(
            "spglib returned non-representative equivalence indices"
        )
    return indices


def _spglib_dataset(
    lattice: np.ndarray,
    positions: np.ndarray,
    numbers: np.ndarray,
    settings: CanonicalizationSettings,
) -> tuple[spglib.SpglibDataset, tuple[int, ...]]:
    dataset = _call_spglib(
        "symmetry classification",
        spglib.get_symmetry_dataset,
        (lattice.copy(), positions.copy(), numbers.copy()),
        symprec=settings.symprec_angstrom,
        angle_tolerance=settings.angle_tolerance_degrees,
    )
    if dataset is None:
        raise StandardizationError("spglib did not return a symmetry dataset")
    _exact_spglib_integer(
        dataset.number,
        label="space-group number",
        minimum=1,
        maximum=230,
    )
    _exact_spglib_integer(
        dataset.hall_number,
        label="Hall number",
        minimum=1,
        maximum=530,
    )
    equivalence_indices = _validated_equivalence_indices(
        dataset.equivalent_atoms,
        site_count=len(positions),
    )
    if len(dataset.wyckoffs) != len(positions):
        raise StandardizationError("spglib symmetry labels do not cover every site")
    return dataset, equivalence_indices


def _require_proper_standardization(dataset: spglib.SpglibDataset) -> None:
    transformation = np.asarray(dataset.transformation_matrix, dtype=np.float64)
    rotation = np.asarray(dataset.std_rotation_matrix, dtype=np.float64)
    transformation_determinant = float(np.linalg.det(transformation))
    rotation_determinant = float(np.linalg.det(rotation))
    if (
        not math.isfinite(transformation_determinant)
        or not math.isfinite(rotation_determinant)
        or transformation_determinant <= 0.0
        or rotation_determinant <= 0.0
    ):
        raise StandardizationError(
            "spglib standardization requires an improper orientation transform"
        )


def _matrix_tuple(matrix: np.ndarray) -> Matrix3:
    return tuple(tuple(float(value) for value in row) for row in matrix)  # type: ignore[return-value]


def canonicalize_periodic_crystal(
    structure: PeriodicStructure,
    settings: CanonicalizationSettings | None = None,
) -> CanonicalStructure:
    """Canonicalize one immutable periodic structure through spglib."""

    if not isinstance(structure, PeriodicStructure):
        raise TypeError("structure must be a PeriodicStructure")
    if settings is None:
        settings = CanonicalizationSettings()
    if not isinstance(settings, CanonicalizationSettings):
        raise TypeError("settings must be CanonicalizationSettings")
    if len(structure.sites) > settings.max_sites:
        raise StandardizationError("structure exceeds settings.max_sites")
    if any(site.occupancy != 1.0 for site in structure.sites):
        raise StandardizationError("partial occupancy is unsupported")

    lattice = np.array(structure.lattice, dtype=np.float64, copy=True)
    positions = np.array(
        [site.fractional_coordinates for site in structure.sites],
        dtype=np.float64,
        copy=True,
    )
    positions = _wrap_positions(positions)
    numbers = np.array(
        [atomic_number(site.species) for site in structure.sites],
        dtype=np.intc,
        copy=True,
    )
    input_lattice = _matrix_tuple(lattice)
    _validate_unique_sites(input_lattice, positions, numbers, settings)
    input_dataset, _ = _spglib_dataset(lattice, positions, numbers, settings)
    _require_proper_standardization(input_dataset)

    standardized = _call_spglib(
        "cell standardization",
        spglib.standardize_cell,
        (lattice.copy(), positions.copy(), numbers.copy()),
        to_primitive=settings.mode == "primitive",
        no_idealize=settings.no_idealize,
        symprec=settings.symprec_angstrom,
        angle_tolerance=settings.angle_tolerance_degrees,
    )
    if standardized is None:
        raise StandardizationError("spglib failed to standardize the structure")
    try:
        if len(standardized) != 3:
            raise ValueError
        standardized_lattice = np.array(standardized[0], dtype=np.float64, copy=True)
        standardized_positions = np.array(
            standardized[1],
            dtype=np.float64,
            copy=True,
        )
    except (TypeError, ValueError, IndexError) as exc:
        raise StandardizationError("spglib returned malformed standardized cell data") from exc
    if (
        standardized_lattice.shape != (3, 3)
        or standardized_positions.ndim != 2
        or standardized_positions.shape[1:] != (3,)
        or len(standardized_positions) < 1
        or len(standardized_positions) > settings.max_sites
        or not np.isfinite(standardized_lattice).all()
        or not np.isfinite(standardized_positions).all()
    ):
        raise StandardizationError("spglib returned malformed standardized cell data")
    standardized_numbers = _validated_spglib_atomic_numbers(
        standardized[2],
        input_numbers=numbers,
        mode=settings.mode,
    )
    if len(standardized_positions) != len(standardized_numbers):
        raise StandardizationError("spglib returned malformed standardized cell data")
    standardized_positions = _wrap_positions(standardized_positions)
    standardized_determinant = float(np.linalg.det(standardized_lattice))
    if not math.isfinite(standardized_determinant) or standardized_determinant <= 1.0e-12:
        raise StandardizationError(
            "spglib returned a singular or left-handed standardized lattice"
        )

    canonical_lattice, canonical_positions, canonical_numbers = _canonical_basis(
        standardized_lattice,
        standardized_positions,
        standardized_numbers,
        settings,
    )
    canonical_lattice, canonical_positions = _quantize(
        canonical_lattice,
        canonical_positions,
        settings,
    )
    canonical_order = sorted(
        range(len(canonical_numbers)),
        key=lambda index: (
            int(canonical_numbers[index]),
            *(float(value) for value in canonical_positions[index]),
        ),
    )
    canonical_positions = canonical_positions[canonical_order]
    canonical_numbers = canonical_numbers[canonical_order]
    lattice_tuple = _matrix_tuple(canonical_lattice)
    _validate_unique_sites(lattice_tuple, canonical_positions, canonical_numbers, settings)
    dataset, raw_equivalence_classes = _spglib_dataset(
        canonical_lattice,
        canonical_positions,
        canonical_numbers,
        settings,
    )

    class_by_raw: dict[int, int] = {}
    equivalence_classes: list[int] = []
    for raw_class in raw_equivalence_classes:
        if raw_class not in class_by_raw:
            class_by_raw[raw_class] = len(class_by_raw)
        equivalence_classes.append(class_by_raw[raw_class])

    sites = tuple(
        CanonicalSite(
            species=element_symbol(int(canonical_numbers[index])),
            fractional_coordinates=tuple(
                float(value) for value in canonical_positions[index]
            ),  # type: ignore[arg-type]
            wyckoff_letter=str(dataset.wyckoffs[index]).casefold(),
            equivalence_class=equivalence_classes[index],
        )
        for index in range(len(canonical_positions))
    )
    symmetry = SymmetryClassification(
        international_number=int(dataset.number),
        international_symbol=str(dataset.international).strip(),
        hall_number=int(dataset.hall_number),
        hall_symbol=str(dataset.hall).strip(),
        choice=str(dataset.choice).strip(),
        point_group_symbol=str(dataset.pointgroup).strip(),
    )
    return CanonicalStructure(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        canonical_profile=CANONICALIZATION_PROFILE,
        settings_sha256=canonicalization_settings_sha256(settings),
        mode=settings.mode,
        lattice=lattice_tuple,
        sites=sites,
        symmetry=symmetry,
    )


def canonicalize_cif_bytes(
    raw_bytes: bytes,
    *,
    settings: CanonicalizationSettings | None = None,
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
) -> CanonicalStructure:
    structure = parse_cif_structure(
        raw_bytes,
        expected_sha256=expected_sha256,
        expected_byte_count=expected_byte_count,
    )
    return canonicalize_periodic_crystal(structure, settings=settings)


def _composition(species: Iterable[str]) -> tuple[SpeciesCount, ...]:
    counts = Counter(species)
    return tuple(
        SpeciesCount(species=symbol, count=count)
        for symbol, count in sorted(counts.items())
    )


def project_canonical_structure(
    structure: CanonicalStructure,
) -> CoordinateFreeStructureProjection:
    if not isinstance(structure, CanonicalStructure):
        raise TypeError("structure must be CanonicalStructure")
    return CoordinateFreeStructureProjection(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        projection_profile=STRUCTURE_PROJECTION_PROFILE,
        canonical_structure_sha256=canonical_structure_sha256(structure),
        settings_sha256=structure.settings_sha256,
        mode=structure.mode,
        atom_count=len(structure.sites),
        composition=_composition(site.species for site in structure.sites),
        symmetry=structure.symmetry,
        contains_coordinates=False,
        contains_lattice_vectors=False,
    )


__all__ = [
    "canonical_sha256",
    "canonical_structure_digest",
    "canonical_structure_sha256",
    "canonicalization_settings_sha256",
    "canonicalize_cif_bytes",
    "canonicalize_periodic_crystal",
    "project_canonical_structure",
    "proper_orientation_basis_transforms",
]
