"""Species-constrained periodic comparison with global-origin handling."""

from __future__ import annotations

import itertools
import math
from collections import Counter
from dataclasses import dataclass

import numpy as np

from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from ._elements import atomic_number
from .assignment import _WorkBudget, _all_optimal_assignments
from .canonicalize import (
    canonical_sha256,
    canonical_structure_sha256,
    canonicalization_settings_sha256,
    canonicalize_periodic_crystal,
)
from .contracts import (
    CANONICALIZATION_CONTRACT_VERSION,
    COMPARISON_PROFILE,
    COMPARISON_PROJECTION_PROFILE,
    AtomDisplacement,
    AtomMapping,
    AtomSite,
    CanonicalStructure,
    ComparatorSettings,
    CoordinateFreeComparisonProjection,
    MinimumImageResult,
    PeriodicStructure,
    SpeciesCount,
    StructureComparison,
)
from .errors import (
    AssignmentWorkLimitError,
    CompositionMismatchError,
    LatticeError,
    MappingAmbiguityError,
)
from .lattice import closest_lattice_image, compute_lattice_metrics


@dataclass(frozen=True)
class _MappingCandidate:
    origin_shift: tuple[float, float, float]
    candidate_by_reference: tuple[int, ...]
    images_by_reference: tuple[MinimumImageResult, ...]
    total_cost: float
    metric_signature: tuple[object, ...]
    semantic_signature: tuple[object, ...]

    @property
    def lexical_key(self) -> tuple[object, ...]:
        return (_origin_lexical_key(self.origin_shift), self.candidate_by_reference)


def _origin_lexical_key(
    origin_shift: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if not all(math.isfinite(value) for value in origin_shift):
        raise MappingAmbiguityError("origin shift contains a non-finite component")
    rounded = tuple(round(value, 14) for value in origin_shift)
    return rounded, origin_shift  # type: ignore[return-value]


def _composition_from_species(species: tuple[str, ...]) -> tuple[SpeciesCount, ...]:
    counts = Counter(species)
    return tuple(
        SpeciesCount(species=symbol, count=count)
        for symbol, count in sorted(counts.items())
    )


def _input_species(
    structure: PeriodicStructure | CanonicalStructure,
) -> tuple[str, ...]:
    return tuple(site.species for site in structure.sites)


def _require_matching_composition(
    reference: PeriodicStructure | CanonicalStructure,
    candidate: PeriodicStructure | CanonicalStructure,
) -> None:
    reference_species = _input_species(reference)
    candidate_species = _input_species(candidate)
    if len(reference_species) != len(candidate_species):
        raise CompositionMismatchError("reference and candidate atom counts differ")
    if Counter(reference_species) != Counter(candidate_species):
        raise CompositionMismatchError("reference and candidate compositions differ")


def _canonical_input(
    structure: PeriodicStructure | CanonicalStructure,
    settings: ComparatorSettings,
) -> CanonicalStructure:
    if isinstance(structure, PeriodicStructure):
        return canonicalize_periodic_crystal(structure, settings.canonicalization)
    if isinstance(structure, CanonicalStructure):
        expected = canonicalization_settings_sha256(settings.canonicalization)
        if structure.settings_sha256 != expected or structure.mode != settings.canonicalization.mode:
            raise ValueError("canonical input does not match comparator canonicalization settings")
        materialized = PeriodicStructure(
            lattice=structure.lattice,
            sites=tuple(
                AtomSite(
                    species=site.species,
                    fractional_coordinates=site.fractional_coordinates,
                    occupancy=1.0,
                    label=None,
                )
                for site in structure.sites
            ),
        )
        if canonicalize_periodic_crystal(materialized, settings.canonicalization) != structure:
            raise ValueError("canonical input does not reproduce under its declared settings")
        return structure
    raise TypeError("structure must be PeriodicStructure or CanonicalStructure")


def _wrapped_shift(values: np.ndarray) -> tuple[float, float, float]:
    wrapped = values - np.floor(values)
    wrapped[wrapped == 0.0] = 0.0
    wrapped[wrapped == 1.0] = 0.0
    return tuple(float(value) for value in wrapped)  # type: ignore[return-value]


def _metric_signature(
    reference: CanonicalStructure,
    images: tuple[MinimumImageResult, ...],
) -> tuple[object, ...]:
    distances = tuple(image.distance_angstrom for image in images)
    species_distributions = tuple(
        (
            species,
            tuple(
                sorted(
                    distances[index]
                    for index, site in enumerate(reference.sites)
                    if site.species == species
                )
            ),
        )
        for species in sorted({site.species for site in reference.sites})
    )
    rms = math.hypot(*distances) / math.sqrt(len(distances))
    if not math.isfinite(rms):
        raise MappingAmbiguityError("displacement metric is not finite")
    return (rms, max(distances), species_distributions)


def _semantic_signature(
    reference: CanonicalStructure,
    candidate: CanonicalStructure,
    candidate_by_reference: tuple[int, ...],
) -> tuple[object, ...]:
    return tuple(
        (
            reference.sites[index].species,
            reference.sites[index].wyckoff_letter,
            reference.sites[index].equivalence_class,
            candidate.sites[candidate_index].species,
            candidate.sites[candidate_index].wyckoff_letter,
            candidate.sites[candidate_index].equivalence_class,
        )
        for index, candidate_index in enumerate(candidate_by_reference)
    )


def _numeric_signatures_equal(
    first: object,
    second: object,
    tolerance: float,
) -> bool:
    if isinstance(first, float) and isinstance(second, float):
        return math.isclose(first, second, rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(first, tuple) and isinstance(second, tuple):
        return len(first) == len(second) and all(
            _numeric_signatures_equal(left, right, tolerance)
            for left, right in zip(first, second, strict=True)
        )
    return first == second


def _final_minimum_candidates(
    candidates: tuple[_MappingCandidate, ...],
    *,
    numeric_tolerance: float,
    max_candidates: int,
) -> tuple[_MappingCandidate, ...]:
    if not candidates:
        raise MappingAmbiguityError("no complete species-constrained mapping was found")
    if len(candidates) > max_candidates:
        raise MappingAmbiguityError(
            "mapping candidate enumeration exceeds max_equivalent_mappings"
        )
    if any(not math.isfinite(candidate.total_cost) for candidate in candidates):
        raise MappingAmbiguityError("mapping candidate cost is not finite")

    strict_minimum = min(candidate.total_cost for candidate in candidates)
    finalists = tuple(
        candidate
        for candidate in candidates
        if math.isclose(
            candidate.total_cost,
            strict_minimum,
            rel_tol=numeric_tolerance,
            abs_tol=numeric_tolerance,
        )
    )
    deduplicated: dict[tuple[object, ...], _MappingCandidate] = {}
    for candidate in finalists:
        previous = deduplicated.get(candidate.lexical_key)
        if previous is None or candidate.total_cost < previous.total_cost:
            deduplicated[candidate.lexical_key] = candidate
        elif candidate.total_cost == previous.total_cost and candidate != previous:
            raise MappingAmbiguityError(
                "duplicate mapping identity has inconsistent evidence"
            )
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda candidate: (candidate.total_cost, candidate.lexical_key),
        )
    )


def _origin_candidate(
    reference: CanonicalStructure,
    candidate: CanonicalStructure,
    origin_shift: tuple[float, float, float],
    settings: ComparatorSettings,
    budget: _WorkBudget,
) -> tuple[_MappingCandidate, ...]:
    reference_positions = np.array(
        [site.fractional_coordinates for site in reference.sites], dtype=np.float64
    )
    candidate_positions = np.array(
        [site.fractional_coordinates for site in candidate.sites], dtype=np.float64
    )
    shift = np.asarray(origin_shift, dtype=np.float64)
    species_names = sorted({site.species for site in reference.sites})

    per_species: list[
        tuple[
            tuple[int, ...],
            tuple[int, ...],
            tuple[tuple[int, ...], ...],
            tuple[tuple[MinimumImageResult, ...], ...],
        ]
    ] = []
    for species in species_names:
        reference_indices = tuple(
            index for index, site in enumerate(reference.sites) if site.species == species
        )
        candidate_indices = tuple(
            index for index, site in enumerate(candidate.sites) if site.species == species
        )
        image_rows: list[tuple[MinimumImageResult, ...]] = []
        cost_rows: list[tuple[float, ...]] = []
        for reference_index in reference_indices:
            image_row: list[MinimumImageResult] = []
            cost_row: list[float] = []
            for candidate_index in candidate_indices:
                remaining = budget.remaining
                if remaining < 1:
                    raise AssignmentWorkLimitError(
                        "assignment has no remaining minimum-image work budget"
                    )
                fractional = (
                    candidate_positions[candidate_index]
                    + shift
                    - reference_positions[reference_index]
                )
                image_limit = min(
                    settings.max_minimum_image_candidates,
                    remaining,
                )
                try:
                    image = closest_lattice_image(
                        tuple(float(value) for value in fractional),  # type: ignore[arg-type]
                        reference.lattice,
                        max_candidates=image_limit,
                    )
                except LatticeError as exc:
                    if image_limit == remaining:
                        raise AssignmentWorkLimitError(
                            "minimum-image search exceeds the remaining assignment work budget"
                        ) from exc
                    raise
                budget.use(image.candidates_examined)
                image_row.append(image)
                cost = image.distance_angstrom * image.distance_angstrom
                if not math.isfinite(cost):
                    raise MappingAmbiguityError("assignment cost is not finite")
                cost_row.append(cost)
            image_rows.append(tuple(image_row))
            cost_rows.append(tuple(cost_row))
        optimal = _all_optimal_assignments(
            np.asarray(cost_rows, dtype=np.float64),
            numeric_tolerance=settings.assignment_numeric_tolerance,
            max_equivalent=settings.max_equivalent_mappings,
            budget=budget,
        )
        per_species.append(
            (
                reference_indices,
                candidate_indices,
                optimal.mappings,
                tuple(image_rows),
            )
        )

    combinations = 1
    for _, _, mappings, _ in per_species:
        combinations *= len(mappings)
        if combinations > settings.max_equivalent_mappings:
            raise MappingAmbiguityError(
                "combined equal-cost assignments exceed max_equivalent_mappings"
            )

    results: list[_MappingCandidate] = []
    mapping_options = tuple(item[2] for item in per_species)
    for choices in itertools.product(*mapping_options):
        budget.use()
        candidate_by_reference = [-1] * len(reference.sites)
        images_by_reference: list[MinimumImageResult | None] = [None] * len(
            reference.sites
        )
        total_cost = 0.0
        for species_index, local_mapping in enumerate(choices):
            reference_indices, candidate_indices, _, image_rows = per_species[
                species_index
            ]
            for local_reference, local_candidate in enumerate(local_mapping):
                reference_index = reference_indices[local_reference]
                candidate_index = candidate_indices[local_candidate]
                image = image_rows[local_reference][local_candidate]
                candidate_by_reference[reference_index] = candidate_index
                images_by_reference[reference_index] = image
                total_cost += image.distance_angstrom * image.distance_angstrom
                if not math.isfinite(total_cost):
                    raise MappingAmbiguityError("combined assignment cost is not finite")
        if any(index < 0 for index in candidate_by_reference) or any(
            image is None for image in images_by_reference
        ):
            raise MappingAmbiguityError("assignment did not cover every atom")
        mapping_tuple = tuple(candidate_by_reference)
        image_tuple = tuple(images_by_reference)  # type: ignore[arg-type]
        results.append(
            _MappingCandidate(
                origin_shift=origin_shift,
                candidate_by_reference=mapping_tuple,
                images_by_reference=image_tuple,
                total_cost=total_cost,
                metric_signature=_metric_signature(reference, image_tuple),
                semantic_signature=_semantic_signature(
                    reference,
                    candidate,
                    mapping_tuple,
                ),
            )
        )
    return tuple(results)


def _map_verified_canonical_atoms_by_species(
    reference: CanonicalStructure,
    candidate: CanonicalStructure,
    settings: ComparatorSettings,
) -> AtomMapping:
    """Map inputs whose canonical identity has already been reproduced."""

    _require_matching_composition(reference, candidate)
    if len(reference.sites) > settings.max_assignment_sites:
        raise MappingAmbiguityError("atom count exceeds max_assignment_sites")

    species_counts = Counter(site.species for site in reference.sites)
    anchor_species = min(
        species_counts,
        key=lambda species: (species_counts[species], atomic_number(species)),
    )
    reference_anchor = next(
        index for index, site in enumerate(reference.sites) if site.species == anchor_species
    )
    reference_anchor_position = np.asarray(
        reference.sites[reference_anchor].fractional_coordinates,
        dtype=np.float64,
    )
    origin_shifts: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        tuple[float, float, float],
    ] = {}
    for site in candidate.sites:
        if site.species != anchor_species:
            continue
        shift = _wrapped_shift(
            reference_anchor_position
            - np.asarray(site.fractional_coordinates, dtype=np.float64)
        )
        key = _origin_lexical_key(shift)
        origin_shifts.setdefault(key, shift)

    budget = _WorkBudget(settings.max_assignment_work)
    candidates: list[_MappingCandidate] = []
    for key in sorted(origin_shifts):
        for result in _origin_candidate(
            reference,
            candidate,
            origin_shifts[key],
            settings,
            budget,
        ):
            candidates.append(result)
            if len(candidates) > settings.max_equivalent_mappings:
                raise MappingAmbiguityError(
                    "mapping candidate enumeration exceeds max_equivalent_mappings"
                )
    tied = _final_minimum_candidates(
        tuple(candidates),
        numeric_tolerance=settings.assignment_numeric_tolerance,
        max_candidates=settings.max_equivalent_mappings,
    )
    representative = tied[0]
    for alternative in tied[1:]:
        if alternative.semantic_signature != representative.semantic_signature:
            raise MappingAmbiguityError(
                "equal-cost mappings have materially different semantic identity"
            )
        if not _numeric_signatures_equal(
            alternative.metric_signature,
            representative.metric_signature,
            settings.degenerate_metric_tolerance,
        ):
            raise MappingAmbiguityError(
                "equal-cost mappings have materially different displacement metrics"
            )

    displacements = tuple(
        AtomDisplacement(
            reference_index=reference_index,
            candidate_index=representative.candidate_by_reference[reference_index],
            species=reference.sites[reference_index].species,
            fractional_displacement=representative.images_by_reference[
                reference_index
            ].fractional_displacement,
            cartesian_displacement_angstrom=representative.images_by_reference[
                reference_index
            ].cartesian_displacement_angstrom,
            distance_angstrom=representative.images_by_reference[
                reference_index
            ].distance_angstrom,
        )
        for reference_index in range(len(reference.sites))
    )
    return AtomMapping(
        global_origin_shift_fractional=representative.origin_shift,
        reference_atom_count=len(reference.sites),
        candidate_atom_count=len(candidate.sites),
        displacements=displacements,
        coverage=1.0,
        mapping_degenerate=len(tied) > 1,
        equivalent_mapping_count=len(tied),
        semantic_identity_preserved=True,
    )


def map_periodic_atoms_by_species(
    reference: CanonicalStructure,
    candidate: CanonicalStructure,
    settings: ComparatorSettings | None = None,
) -> AtomMapping:
    """Verify canonical inputs, then map over explicit global origins."""

    if not isinstance(reference, CanonicalStructure) or not isinstance(
        candidate, CanonicalStructure
    ):
        raise TypeError("mapping inputs must be CanonicalStructure instances")
    if settings is None:
        settings = ComparatorSettings()
    if not isinstance(settings, ComparatorSettings):
        raise TypeError("settings must be ComparatorSettings")
    verified_reference = _canonical_input(reference, settings)
    verified_candidate = _canonical_input(candidate, settings)
    return _map_verified_canonical_atoms_by_species(
        verified_reference,
        verified_candidate,
        settings,
    )


def compare_periodic_structures(
    reference: PeriodicStructure | CanonicalStructure,
    candidate: PeriodicStructure | CanonicalStructure,
    settings: ComparatorSettings | None = None,
) -> StructureComparison:
    """Canonicalize, map, and measure without mutating either input."""

    if not isinstance(reference, (PeriodicStructure, CanonicalStructure)):
        raise TypeError("reference must be a periodic or canonical structure")
    if not isinstance(candidate, (PeriodicStructure, CanonicalStructure)):
        raise TypeError("candidate must be a periodic or canonical structure")
    if settings is None:
        settings = ComparatorSettings()
    if not isinstance(settings, ComparatorSettings):
        raise TypeError("settings must be ComparatorSettings")
    _require_matching_composition(reference, candidate)
    candidate_before = canonical_json_bytes(candidate)

    canonical_reference = _canonical_input(reference, settings)
    canonical_candidate = _canonical_input(candidate, settings)
    _require_matching_composition(canonical_reference, canonical_candidate)
    mapping = _map_verified_canonical_atoms_by_species(
        canonical_reference,
        canonical_candidate,
        settings,
    )
    distances = tuple(item.distance_angstrom for item in mapping.displacements)
    composition = _composition_from_species(
        tuple(site.species for site in canonical_reference.sites)
    )
    candidate_unchanged = candidate_before == canonical_json_bytes(candidate)
    if not candidate_unchanged:
        raise RuntimeError("candidate input changed during comparison")
    return StructureComparison(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        comparison_profile=COMPARISON_PROFILE,
        settings_sha256=canonical_sha256(settings),
        reference_structure_sha256=canonical_structure_sha256(canonical_reference),
        candidate_structure_sha256=canonical_structure_sha256(canonical_candidate),
        atom_count=len(canonical_reference.sites),
        composition=composition,
        mapping=mapping,
        lattice_metrics=compute_lattice_metrics(
            canonical_reference.lattice,
            canonical_candidate.lattice,
        ),
        rms_displacement_angstrom=math.hypot(*distances) / math.sqrt(len(distances)),
        maximum_displacement_angstrom=max(distances),
        candidate_input_unchanged=True,
        scientifically_verified="not_assessed",
    )


compare_structures = compare_periodic_structures


def project_structure_comparison(
    comparison: StructureComparison,
) -> CoordinateFreeComparisonProjection:
    if not isinstance(comparison, StructureComparison):
        raise TypeError("comparison must be StructureComparison")
    return CoordinateFreeComparisonProjection(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        projection_profile=COMPARISON_PROJECTION_PROFILE,
        settings_sha256=comparison.settings_sha256,
        reference_structure_sha256=comparison.reference_structure_sha256,
        candidate_structure_sha256=comparison.candidate_structure_sha256,
        atom_count=comparison.atom_count,
        composition=comparison.composition,
        mapping_coverage=comparison.mapping.coverage,
        mapping_degenerate=comparison.mapping.mapping_degenerate,
        rms_displacement_angstrom=comparison.rms_displacement_angstrom,
        maximum_displacement_angstrom=comparison.maximum_displacement_angstrom,
        maximum_relative_lattice_error=(
            comparison.lattice_metrics.maximum_relative_lattice_error
        ),
        contains_coordinates=False,
        contains_atom_mapping=False,
        scientifically_verified="not_assessed",
    )


__all__ = [
    "compare_periodic_structures",
    "compare_structures",
    "map_periodic_atoms_by_species",
    "project_structure_comparison",
]
