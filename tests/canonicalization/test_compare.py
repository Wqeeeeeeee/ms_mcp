from __future__ import annotations

import itertools
import math

import pytest

from material_studio_mcp_server.canonicalization import (
    CANONICALIZATION_CONTRACT_VERSION,
    CANONICALIZATION_PROFILE,
    AtomSite,
    AssignmentWorkLimitError,
    CanonicalSite,
    CanonicalStructure,
    CanonicalizationSettings,
    ComparatorSettings,
    CompositionMismatchError,
    MappingAmbiguityError,
    MinimumImageResult,
    PeriodicStructure,
    SymmetryClassification,
    canonical_structure_sha256,
    canonicalization_settings_sha256,
    canonicalize_periodic_crystal,
    compare_structures,
    map_periodic_atoms_by_species,
    project_structure_comparison,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from .conftest import zincblende_structure


def _replace_site_coordinate(
    structure: PeriodicStructure,
    index: int,
    axis: int,
    delta: float,
) -> PeriodicStructure:
    sites = list(structure.sites)
    site = sites[index]
    coordinates = list(site.fractional_coordinates)
    coordinates[axis] = (coordinates[axis] + delta) % 1.0
    sites[index] = AtomSite(
        species=site.species,
        fractional_coordinates=tuple(coordinates),  # type: ignore[arg-type]
        occupancy=site.occupancy,
        label=site.label,
    )
    return PeriodicStructure(
        lattice=structure.lattice,
        sites=tuple(sites),
    )


def _low_symmetry_structure() -> PeriodicStructure:
    return PeriodicStructure(
        lattice=((4.0, 0.0, 0.0), (0.3, 5.0, 0.0), (0.2, 0.4, 6.0)),
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


def _manual_structure(
    coordinates: tuple[tuple[float, float, float], ...],
    *,
    lattice: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ] = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0)),
) -> CanonicalStructure:
    settings = CanonicalizationSettings()
    return CanonicalStructure(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        canonical_profile=CANONICALIZATION_PROFILE,
        settings_sha256=canonicalization_settings_sha256(settings),
        mode="conventional",
        lattice=lattice,
        sites=tuple(
            CanonicalSite(
                species="C",
                fractional_coordinates=value,
                wyckoff_letter="a",
                equivalence_class=0,
            )
            for value in coordinates
        ),
        symmetry=SymmetryClassification(
            international_number=1,
            international_symbol="P1",
            hall_number=1,
            hall_symbol="P 1",
            choice="",
            point_group_symbol="1",
        ),
    )


def test_comparison_handles_permutation_global_origin_and_symmetry_degeneracy() -> None:
    source = zincblende_structure()
    reference = canonicalize_periodic_crystal(source)
    shift = (0.5, 0.5, 0.0)
    candidate = PeriodicStructure(
        lattice=source.lattice,
        sites=tuple(
            AtomSite(
                species=site.species,
                fractional_coordinates=tuple(
                    (value + offset) % 1.0
                    for value, offset in zip(
                        site.fractional_coordinates,
                        shift,
                        strict=True,
                    )
                ),
                occupancy=site.occupancy,
                label=site.label,
            )
            for site in reversed(source.sites)
        ),
    )
    result = compare_structures(reference, candidate)
    assert result.mapping.coverage == 1.0
    assert result.mapping.mapping_degenerate is True
    assert result.mapping.equivalent_mapping_count >= 2
    assert result.rms_displacement_angstrom == 0.0
    assert result.maximum_displacement_angstrom == 0.0


def test_comparison_metrics_match_known_internal_displacement() -> None:
    source = _low_symmetry_structure()
    reference = canonicalize_periodic_crystal(source)
    candidate = _replace_site_coordinate(source, -1, 0, 0.01)
    result = compare_structures(reference, candidate)
    expected_maximum = 0.04
    assert math.isclose(result.maximum_displacement_angstrom, expected_maximum, abs_tol=1e-12)
    assert math.isclose(
        result.rms_displacement_angstrom,
        expected_maximum / math.sqrt(len(reference.sites)),
        abs_tol=1e-12,
    )


def test_comparison_separates_homogeneous_strain_from_internal_displacement() -> None:
    source = zincblende_structure()
    reference = canonicalize_periodic_crystal(source)
    candidate = PeriodicStructure(
        lattice=tuple(
            tuple(value * 1.01 for value in lattice_row)
            for lattice_row in source.lattice
        ),  # type: ignore[arg-type]
        sites=source.sites,
    )
    result = compare_structures(reference, candidate)
    assert result.rms_displacement_angstrom == 0.0
    assert math.isclose(
        result.lattice_metrics.maximum_relative_lattice_error,
        0.01,
        abs_tol=1e-12,
    )
    for row_index, row in enumerate(result.lattice_metrics.symmetric_strain):
        for column_index, value in enumerate(row):
            expected = 0.01 if row_index == column_index else 0.0
            assert math.isclose(value, expected, abs_tol=1e-12)


def test_materially_different_equal_cost_mapping_fails_closed() -> None:
    import material_studio_mcp_server.canonicalization.compare as implementation

    reference = _manual_structure(((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)))
    candidate = _manual_structure(((0.0, 0.0, 0.0), (0.0, 0.1, 0.0)))
    with pytest.raises(MappingAmbiguityError):
        implementation._map_verified_canonical_atoms_by_species(
            reference,
            candidate,
            ComparatorSettings(),
        )


def test_assignment_budget_covers_minimum_image_matrix_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.compare as implementation

    reference = _manual_structure(((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)))
    candidate = _manual_structure(((0.0, 0.0, 0.0), (0.0, 0.1, 0.0)))

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("Hungarian solver must not run")

    monkeypatch.setattr(implementation, "_all_optimal_assignments", fail_if_called)
    with pytest.raises(AssignmentWorkLimitError):
        implementation._map_verified_canonical_atoms_by_species(
            reference,
            candidate,
            ComparatorSettings(max_assignment_work=1),
        )


def test_remaining_budget_bounds_each_real_skew_minimum_image_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.compare as implementation

    lattice = ((2.0, 0.0, 0.0), (1.8, 0.6, 0.0), (0.3, 0.2, 1.7))
    coordinates = ((0.0, 0.0, 0.0), (0.31, 0.27, 0.19))
    reference = _manual_structure(coordinates, lattice=lattice)
    candidate = _manual_structure(coordinates, lattice=lattice)
    first_image = implementation.closest_lattice_image(
        (0.0, 0.0, 0.0),
        lattice,
    )
    real_closest = implementation.closest_lattice_image
    observed_limits: list[int] = []

    def observed_closest(
        displacement: tuple[float, float, float],
        image_lattice: tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ],
        *,
        max_candidates: int,
    ) -> MinimumImageResult:
        observed_limits.append(max_candidates)
        return real_closest(
            displacement,
            image_lattice,
            max_candidates=max_candidates,
        )

    monkeypatch.setattr(implementation, "closest_lattice_image", observed_closest)
    budget = implementation._WorkBudget(first_image.candidates_examined)
    with pytest.raises(AssignmentWorkLimitError, match="no remaining"):
        implementation._origin_candidate(
            reference,
            candidate,
            (0.0, 0.0, 0.0),
            ComparatorSettings(),
            budget,
        )
    assert observed_limits == [first_image.candidates_examined]
    assert budget.remaining == 0


def test_final_mapping_minimum_and_ties_are_traversal_order_independent() -> None:
    import material_studio_mcp_server.canonicalization.compare as implementation

    zero_image = MinimumImageResult(
        fractional_displacement=(0.0, 0.0, 0.0),
        cartesian_displacement_angstrom=(0.0, 0.0, 0.0),
        distance_angstrom=0.0,
        lattice_translation=(0, 0, 0),
        candidates_examined=1,
        distance_degenerate=False,
    )

    def mapping_candidate(origin: float, total_cost: float) -> object:
        return implementation._MappingCandidate(
            origin_shift=(origin, 0.0, 0.0),
            candidate_by_reference=(0,),
            images_by_reference=(zero_image,),
            total_cost=total_cost,
            metric_signature=(0.0, 0.0),
            semantic_signature=(("C", "a", 0),),
        )

    candidates = (
        mapping_candidate(0.2, 0.0),
        mapping_candidate(0.1, 0.9e-12),
        mapping_candidate(0.3, 1.8e-12),
    )
    expected: tuple[object, ...] | None = None
    for traversal in itertools.permutations(candidates):
        selected = implementation._final_minimum_candidates(
            traversal,
            numeric_tolerance=1.0e-12,
            max_candidates=10,
        )
        if expected is None:
            expected = selected
        elif selected != expected:
            raise AssertionError("final mapping tie set depends on traversal order")
    assert expected is not None
    assert len(expected) == 2
    assert expected[0].total_cost == 0.0
    assert expected[0].origin_shift == (0.2, 0.0, 0.0)


def test_mapping_retains_distinct_fifteen_decimal_origin_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.canonicalization.compare as implementation

    reference = _manual_structure(((0.0, 0.0, 0.0), (0.4, 0.0, 0.0)))
    candidate = _manual_structure(
        ((0.899999999999999, 0.0, 0.0), (0.9, 0.0, 0.0))
    )
    zero_image = MinimumImageResult(
        fractional_displacement=(0.0, 0.0, 0.0),
        cartesian_displacement_angstrom=(0.0, 0.0, 0.0),
        distance_angstrom=0.0,
        lattice_translation=(0, 0, 0),
        candidates_examined=1,
        distance_degenerate=False,
    )
    observed_origins: list[tuple[float, float, float]] = []

    def origin_candidate(
        reference_structure: CanonicalStructure,
        candidate_structure: CanonicalStructure,
        origin_shift: tuple[float, float, float],
        settings: ComparatorSettings,
        budget: object,
    ) -> tuple[object, ...]:
        del reference_structure, candidate_structure, settings, budget
        observed_origins.append(origin_shift)
        return (
            implementation._MappingCandidate(
                origin_shift=origin_shift,
                candidate_by_reference=(0, 1),
                images_by_reference=(zero_image, zero_image),
                total_cost=0.0,
                metric_signature=(0.0, 0.0),
                semantic_signature=(("C", "a", 0), ("C", "a", 0)),
            ),
        )

    monkeypatch.setattr(implementation, "_origin_candidate", origin_candidate)
    mapping = implementation._map_verified_canonical_atoms_by_species(
        reference,
        candidate,
        ComparatorSettings(),
    )
    assert len(observed_origins) == 2
    assert round(observed_origins[0][0], 14) == round(observed_origins[1][0], 14)
    assert observed_origins[0] != observed_origins[1]
    assert mapping.mapping_degenerate is True
    assert mapping.equivalent_mapping_count == 2


def test_exported_mapping_requires_reproducible_canonical_inputs() -> None:
    canonical = canonicalize_periodic_crystal(zincblende_structure())
    assert map_periodic_atoms_by_species(canonical, canonical).coverage == 1.0

    forged = _manual_structure(((0.0, 0.0, 0.0), (0.1, 0.0, 0.0)))
    with pytest.raises(ValueError, match="does not reproduce"):
        map_periodic_atoms_by_species(forged, forged)


def test_composition_mismatch_is_rejected_before_mapping() -> None:
    reference = zincblende_structure()
    sites = list(reference.sites)
    site = sites[-1]
    sites[-1] = AtomSite(
        species="Ge",
        fractional_coordinates=site.fractional_coordinates,
        occupancy=1.0,
        label=None,
    )
    candidate = PeriodicStructure(lattice=reference.lattice, sites=tuple(sites))
    with pytest.raises(CompositionMismatchError):
        compare_structures(reference, candidate)


def test_candidate_is_canonical_json_identical_after_comparison() -> None:
    reference = zincblende_structure()
    candidate = zincblende_structure(lattice_constant=4.04)
    before = canonical_json_bytes(candidate)
    result = compare_structures(reference, candidate)
    assert canonical_json_bytes(candidate) == before
    assert result.candidate_input_unchanged is True
    projection = project_structure_comparison(result)
    assert projection.contains_coordinates is False
    assert projection.contains_atom_mapping is False
    assert "fractional" not in projection.model_dump_json()
    assert "cartesian" not in projection.model_dump_json().casefold()


def test_comparison_rejects_canonical_input_with_different_settings() -> None:
    structure = canonicalize_periodic_crystal(
        zincblende_structure(),
        CanonicalizationSettings(quantization_decimals=13),
    )
    with pytest.raises(ValueError):
        compare_structures(structure, structure, ComparatorSettings())
