from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from pydantic import ValidationError

from material_studio_mcp_server.canonicalization import (
    AtomSite,
    CanonicalizationSettings,
    PeriodicStructure,
    StandardizationError,
    canonical_structure_digest,
    canonical_structure_sha256,
    canonicalize_periodic_crystal,
    closest_lattice_image,
    proper_orientation_basis_transforms,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from .conftest import zincblende_structure


def _proper_axis_variant(structure: PeriodicStructure) -> PeriodicStructure:
    transform = np.array(((0, 1, 0), (0, 0, 1), (1, 0, 0)))
    lattice = transform @ np.asarray(structure.lattice)
    shift = np.asarray((0.17, 0.23, 0.31))
    sites = []
    for site in reversed(structure.sites):
        coordinates = (
            np.asarray(site.fractional_coordinates) @ transform.T + shift
        ) % 1.0
        sites.append(
            AtomSite(
                species=site.species,
                fractional_coordinates=tuple(float(value) for value in coordinates),
                occupancy=1.0,
                label=None,
            )
        )
    return PeriodicStructure(
        lattice=tuple(tuple(float(value) for value in row) for row in lattice),  # type: ignore[arg-type]
        sites=tuple(sites),
    )


def test_spglib_standardizes_conventional_and_primitive_cells() -> None:
    structure = zincblende_structure()
    conventional = canonicalize_periodic_crystal(structure)
    primitive = canonicalize_periodic_crystal(
        structure,
        CanonicalizationSettings(mode="primitive"),
    )
    assert conventional.mode == "conventional"
    assert primitive.mode == "primitive"
    assert len(conventional.sites) == 8
    assert len(primitive.sites) == 2
    assert conventional.symmetry.international_number == 216
    assert primitive.symmetry.international_number == 216
    assert all(site.wyckoff_letter for site in conventional.sites)


def test_canonicalization_is_permutation_origin_and_proper_axis_invariant() -> None:
    original = canonicalize_periodic_crystal(zincblende_structure())
    variant = canonicalize_periodic_crystal(_proper_axis_variant(zincblende_structure()))
    assert variant == original
    assert canonical_structure_sha256(variant) == canonical_structure_sha256(original)


def test_canonicalization_rejects_improper_orientation_and_enumerates_only_proper_transforms() -> None:
    structure = zincblende_structure()
    with pytest.raises(ValidationError):
        PeriodicStructure(
            lattice=(
                structure.lattice[1],
                structure.lattice[0],
                structure.lattice[2],
            ),
            sites=structure.sites,
        )
    transforms = proper_orientation_basis_transforms()
    reflection = ((-1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert len(transforms) == 24
    assert reflection not in transforms
    assert all(round(float(np.linalg.det(np.asarray(item)))) == 1 for item in transforms)


def test_canonicalization_is_repeatable_and_uses_runtime_digest_profile() -> None:
    first = canonicalize_periodic_crystal(zincblende_structure())
    second = canonicalize_periodic_crystal(zincblende_structure())
    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    digest = canonical_structure_digest(first)
    assert digest.contract_name == "CanonicalStructure"
    assert digest.contract_version == "1.0.0"
    assert len(digest.sha256) == 64
    assert "-0.0" not in canonical_json_bytes(first).decode("utf-8")


def test_no_idealize_preserves_bounded_internal_perturbation() -> None:
    structure = zincblende_structure()
    sites = list(structure.sites)
    target = sites[-1]
    coordinates = list(target.fractional_coordinates)
    coordinates[0] += 1.0e-6
    sites[-1] = AtomSite(
        species=target.species,
        fractional_coordinates=tuple(coordinates),  # type: ignore[arg-type]
        occupancy=1.0,
        label=None,
    )
    perturbed = canonicalize_periodic_crystal(
        PeriodicStructure(lattice=structure.lattice, sites=tuple(sites))
    )
    baseline = canonicalize_periodic_crystal(structure)
    assert canonical_structure_sha256(perturbed) != canonical_structure_sha256(baseline)


def test_canonicalization_rejects_partial_occupancy_and_duplicate_sites() -> None:
    structure = zincblende_structure()
    partial_sites = list(structure.sites)
    first = partial_sites[0]
    partial_sites[0] = AtomSite(
        species=first.species,
        fractional_coordinates=first.fractional_coordinates,
        occupancy=0.5,
        label=None,
    )
    with pytest.raises(StandardizationError):
        canonicalize_periodic_crystal(
            PeriodicStructure(lattice=structure.lattice, sites=tuple(partial_sites))
        )

    near_full_sites = list(structure.sites)
    near_full_sites[0] = AtomSite(
        species=first.species,
        fractional_coordinates=first.fractional_coordinates,
        occupancy=0.9999999999999,
        label=None,
    )
    with pytest.raises(StandardizationError):
        canonicalize_periodic_crystal(
            PeriodicStructure(lattice=structure.lattice, sites=tuple(near_full_sites))
        )

    with pytest.raises(StandardizationError):
        canonicalize_periodic_crystal(
            PeriodicStructure(
                lattice=structure.lattice,
                sites=structure.sites + (structure.sites[0],),
            )
        )


def test_canonicalization_counts_exact_duplicate_image_candidate_work() -> None:
    structure = PeriodicStructure(
        lattice=((2.0, 0.0, 0.0), (1.8, 0.6, 0.0), (0.3, 0.2, 1.7)),
        sites=(
            AtomSite(
                species="C",
                fractional_coordinates=(0.12, 0.23, 0.34),
                occupancy=1.0,
                label=None,
            ),
            AtomSite(
                species="Si",
                fractional_coordinates=(0.41, 0.27, 0.63),
                occupancy=1.0,
                label=None,
            ),
            AtomSite(
                species="Si",
                fractional_coordinates=(0.76, 0.58, 0.19),
                occupancy=1.0,
                label=None,
            ),
        ),
    )
    displacement = tuple(
        candidate - reference
        for candidate, reference in zip(
            structure.sites[1].fractional_coordinates,
            structure.sites[0].fractional_coordinates,
            strict=True,
        )
    )
    first_image = closest_lattice_image(displacement, structure.lattice)
    with pytest.raises(StandardizationError, match="candidate-work"):
        canonicalize_periodic_crystal(
            structure,
            CanonicalizationSettings(
                max_duplicate_site_checks=first_image.candidates_examined
            ),
        )


def test_quantization_settings_are_hash_bound() -> None:
    structure = zincblende_structure()
    first = canonicalize_periodic_crystal(structure)
    second = canonicalize_periodic_crystal(
        structure,
        CanonicalizationSettings(quantization_decimals=13),
    )
    assert first.settings_sha256 != second.settings_sha256


def test_spglib_exception_is_normalized_without_global_error_mode_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import spglib
    import material_studio_mcp_server.canonicalization.canonicalize as implementation

    def fail(*args: object, **kwargs: object) -> object:
        raise spglib.SpglibError("synthetic failure")

    monkeypatch.setattr(implementation.spglib, "standardize_cell", fail)
    with pytest.raises(StandardizationError, match="spglib cell standardization failed"):
        canonicalize_periodic_crystal(zincblende_structure())


def test_malformed_nonfinite_spglib_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.canonicalize as implementation

    structure = zincblende_structure()
    positions = np.asarray([site.fractional_coordinates for site in structure.sites])
    numbers = np.asarray([6 if site.species == "C" else 14 for site in structure.sites])

    def malformed(*args: object, **kwargs: object) -> object:
        return np.full((3, 3), np.nan), positions, numbers

    monkeypatch.setattr(implementation.spglib, "standardize_cell", malformed)
    with pytest.raises(StandardizationError):
        canonicalize_periodic_crystal(structure)


def test_origin_tie_breaking_is_permutation_independent_beyond_rounded_key() -> None:
    import material_studio_mcp_server.canonicalization.canonicalize as implementation

    tied_value = 0.2
    tied_next = np.nextafter(tied_value, np.inf)
    positions = np.asarray(
        ((0.0, 0.0, 0.0), (tied_value, 0.3, 0.4), (tied_next, 0.3, 0.4))
    )
    numbers = np.asarray((6, 14, 14))
    settings = CanonicalizationSettings(quantization_decimals=12)
    assert implementation._selection_scalar(
        tied_value,
        settings.quantization_decimals,
        periodic=True,
    ) == implementation._selection_scalar(
        tied_next,
        settings.quantization_decimals,
        periodic=True,
    )
    first = implementation._origin_normalized(positions, numbers, settings)
    permutation = np.asarray((0, 2, 1))
    second = implementation._origin_normalized(
        positions[permutation],
        numbers[permutation],
        settings,
    )
    assert np.array_equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])
    assert first[2] == second[2]


def test_fixed_seed_boundary_canonicalization_is_proper_axis_origin_and_permutation_invariant() -> None:
    rng = np.random.default_rng(20260720)
    lattice = np.empty((3, 3), dtype=np.float64)
    positions = np.empty((5, 3), dtype=np.float64)
    for _ in range(42):
        diagonal = rng.uniform(3.0, 7.0, size=3)
        lattice = np.asarray(
            (
                (diagonal[0], 0.0, 0.0),
                (rng.uniform(-0.45, 0.45), diagonal[1], 0.0),
                (
                    rng.uniform(-0.45, 0.45),
                    rng.uniform(-0.45, 0.45),
                    diagonal[2],
                ),
            ),
            dtype=np.float64,
        )
        positions = rng.random((5, 3))

    species = ("C", "C", "Si", "Si", "Si")

    def structure_from_arrays(
        candidate_lattice: np.ndarray,
        candidate_positions: np.ndarray,
        order: np.ndarray,
    ) -> PeriodicStructure:
        return PeriodicStructure(
            lattice=tuple(
                tuple(float(value) for value in row) for row in candidate_lattice
            ),  # type: ignore[arg-type]
            sites=tuple(
                AtomSite(
                    species=species[int(index)],
                    fractional_coordinates=tuple(
                        float(value) for value in candidate_positions[int(index)]
                    ),  # type: ignore[arg-type]
                    occupancy=1.0,
                    label=None,
                )
                for index in order
            ),
        )

    baseline = structure_from_arrays(lattice, positions, np.arange(5))
    expected_sha256 = canonical_structure_sha256(
        canonicalize_periodic_crystal(baseline)
    )
    for raw_transform in proper_orientation_basis_transforms():
        transform = np.asarray(raw_transform, dtype=np.float64)
        transformed_lattice = transform @ lattice
        transformed_positions = positions @ transform.T
        boundary_anchor = transformed_positions[0]
        shifts = (
            rng.random(3),
            -boundary_anchor,
            np.nextafter(-boundary_anchor, np.full(3, np.inf)),
        )
        for shift in shifts:
            shifted = (transformed_positions + shift) % 1.0
            variant = structure_from_arrays(
                transformed_lattice,
                shifted,
                rng.permutation(5),
            )
            actual_sha256 = canonical_structure_sha256(
                canonicalize_periodic_crystal(variant)
            )
            if actual_sha256 != expected_sha256:
                raise AssertionError("fixed-seed canonical invariance regression failed")


def test_spglib_standardized_types_are_validated_before_conversion_and_preserve_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.canonicalize as implementation

    structure = zincblende_structure()
    valid_numbers = np.asarray(
        [6 if site.species == "C" else 14 for site in structure.sites]
    )
    non_integral = valid_numbers.astype(np.float64)
    non_integral[0] += 0.5
    unknown = valid_numbers.copy()
    unknown[0] = 119
    wrong_composition = valid_numbers.copy()
    wrong_composition[0] = 14
    malformed_values = (
        valid_numbers.reshape((-1, 1)),
        non_integral,
        unknown,
        wrong_composition,
    )

    for malformed_numbers in malformed_values:
        def malformed_standardization(
            cell: tuple[np.ndarray, np.ndarray, np.ndarray],
            *args: object,
            _numbers: np.ndarray = malformed_numbers,
            **kwargs: object,
        ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            del args, kwargs
            return cell[0], cell[1], _numbers

        monkeypatch.setattr(
            implementation.spglib,
            "standardize_cell",
            malformed_standardization,
        )
        with pytest.raises(StandardizationError):
            canonicalize_periodic_crystal(structure)


def test_spglib_equivalence_indices_are_integral_and_in_range_before_remapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.canonicalize as implementation

    real_get_dataset = implementation.spglib.get_symmetry_dataset
    site_count = len(zincblende_structure().sites)
    cyclic = np.arange(site_count, dtype=np.int64)
    cyclic[:2] = (1, 0)
    non_representative = np.arange(site_count, dtype=np.int64)
    non_representative[:3] = (0, 0, 1)
    malformed_values = (
        np.full(site_count, 0.5, dtype=np.float64),
        np.full(site_count, site_count, dtype=np.int64),
        cyclic,
        non_representative,
    )
    for malformed_indices in malformed_values:
        def malformed_dataset(
            *args: object,
            _indices: np.ndarray = malformed_indices,
            **kwargs: object,
        ) -> object:
            dataset = real_get_dataset(*args, **kwargs)
            if dataset is None:
                raise AssertionError("real spglib dataset unexpectedly missing")
            return replace(dataset, equivalent_atoms=_indices)

        monkeypatch.setattr(
            implementation.spglib,
            "get_symmetry_dataset",
            malformed_dataset,
        )
        with pytest.raises(StandardizationError):
            canonicalize_periodic_crystal(zincblende_structure())
