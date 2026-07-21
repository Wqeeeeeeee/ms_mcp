"""Independent coordinate-derived validation for the fixed surface model."""

from __future__ import annotations

import itertools
import math
from collections import Counter
from typing import Iterable

from material_studio_mcp_server.runtime import (
    DomainFact,
    DomainValidationReport,
    RUNTIME_CONTRACT_VERSION,
    RuntimeIssue,
    RuntimeIssueKind,
    ValidationStatus,
    model_spec_digest,
)
from material_studio_mcp_server.specs import (
    BasisAtomSpec,
    CrystalSpec,
    ModelSpec,
    ModelType,
)

from .constants import (
    ATOMIC_PLANE_COUNT,
    BOTTOM_CARBON_COUNT,
    BULK_LATTICE_ANGSTROM,
    CARBON_COUNT,
    CARBON_HYDROGEN_BOND_ANGSTROM,
    CONTRACT_VERSION,
    HYDROGEN_COUNT,
    HYDROGENS_PER_BOTTOM_CARBON,
    IMPLEMENTATION_VERSION,
    IN_PLANE_REPEAT,
    MATERIAL,
    PLUGIN_ID,
    SILICON_COUNT,
    SOURCE_ID,
    TOTAL_ATOM_COUNT,
    VACUUM_ANGSTROM,
)


_LENGTH_TOLERANCE = 1.0e-6
_PLANE_TOLERANCE = 1.0e-5
_BOND_TOLERANCE = 1.0e-5
_DUPLICATE_DISTANCE = 1.0e-6
_SEVERE_SHORT_CONTACT_DISTANCE = 0.70

_Vector = tuple[float, float, float]
_AtomPoint = tuple[BasisAtomSpec, _Vector, float]


def _issue(code: str, message: str, field_path: str) -> RuntimeIssue:
    return RuntimeIssue(
        kind=RuntimeIssueKind.INVALID_INPUT,
        code=code,
        message=message,
        field_path=field_path,
    )


def _dot(left: _Vector, right: _Vector) -> float:
    return sum(a * b for a, b in zip(left, right))


def _cross(left: _Vector, right: _Vector) -> _Vector:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _norm(vector: _Vector) -> float:
    return math.sqrt(_dot(vector, vector))


def _scale(vector: _Vector, factor: float) -> _Vector:
    return (
        vector[0] * factor,
        vector[1] * factor,
        vector[2] * factor,
    )


def _add(*vectors: _Vector) -> _Vector:
    return (
        sum(vector[0] for vector in vectors),
        sum(vector[1] for vector in vectors),
        sum(vector[2] for vector in vectors),
    )


def _lattice_vectors(crystal: CrystalSpec) -> tuple[_Vector, _Vector, _Vector]:
    lattice = crystal.lattice
    lengths = (lattice.a, lattice.b, lattice.c)
    angles = (lattice.alpha, lattice.beta, lattice.gamma)
    if any(
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value <= 0.0
        for value in lengths
    ):
        raise ValueError("lattice lengths must be finite and positive")
    if any(
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value <= 0.0
        or value >= 180.0
        for value in angles
    ):
        raise ValueError("lattice angles must be finite and between 0 and 180 degrees")
    alpha = math.radians(lattice.alpha)
    beta = math.radians(lattice.beta)
    gamma = math.radians(lattice.gamma)
    sin_gamma = math.sin(gamma)
    if abs(sin_gamma) < 1.0e-12:
        raise ValueError("degenerate gamma angle")
    a_vector = (lattice.a, 0.0, 0.0)
    b_vector = (
        lattice.b * math.cos(gamma),
        lattice.b * sin_gamma,
        0.0,
    )
    c_x = lattice.c * math.cos(beta)
    c_y = lattice.c * (
        math.cos(alpha) - math.cos(beta) * math.cos(gamma)
    ) / sin_gamma
    c_z_squared = lattice.c * lattice.c - c_x * c_x - c_y * c_y
    if not math.isfinite(c_z_squared) or c_z_squared <= 0.0:
        raise ValueError("degenerate c lattice vector")
    return a_vector, b_vector, (c_x, c_y, math.sqrt(c_z_squared))


def _cartesian(
    fractional: tuple[float, float, float],
    vectors: tuple[_Vector, _Vector, _Vector],
) -> _Vector:
    return _add(
        *(
            _scale(vector, coefficient)
            for vector, coefficient in zip(vectors, fractional)
        )
    )


def _minimum_image_vector(
    left: BasisAtomSpec,
    right: BasisAtomSpec,
    vectors: tuple[_Vector, _Vector, _Vector],
) -> _Vector:
    delta = tuple(
        right_value - left_value
        for left_value, right_value in zip(
            left.fractional.as_tuple(), right.fractional.as_tuple()
        )
    )
    candidates = (
        _cartesian(
            (
                delta[0] + shift[0],
                delta[1] + shift[1],
                delta[2] + shift[2],
            ),
            vectors,
        )
        for shift in itertools.product((-1, 0, 1), repeat=3)
    )
    return min(candidates, key=lambda vector: (_dot(vector, vector), vector))


def _group_planes(points: Iterable[_AtomPoint]) -> list[list[_AtomPoint]]:
    planes: list[list[_AtomPoint]] = []
    for point in sorted(points, key=lambda item: (item[2], item[0].id)):
        if not planes or abs(point[2] - planes[-1][0][2]) > _PLANE_TOLERANCE:
            planes.append([point])
        else:
            planes[-1].append(point)
    return planes


def _registry_sites(
    local_sites: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    sites = []
    for repeat_x in range(IN_PLANE_REPEAT):
        for repeat_y in range(IN_PLANE_REPEAT):
            for local_x, local_y in local_sites:
                sites.append(
                    (
                        (repeat_x + local_x) / IN_PLANE_REPEAT,
                        (repeat_y + local_y) / IN_PLANE_REPEAT,
                    )
                )
    return tuple(sorted(sites))


_EXPECTED_PLANE_REGISTRIES = (
    _registry_sites(((0.25, 0.25), (0.75, 0.75))),
    _registry_sites(((0.0, 0.5), (0.5, 0.0))),
    _registry_sites(((0.25, 0.75), (0.75, 0.25))),
    _registry_sites(((0.0, 0.0), (0.5, 0.5))),
    _registry_sites(((0.25, 0.25), (0.75, 0.75))),
    _registry_sites(((0.0, 0.5), (0.5, 0.0))),
    _registry_sites(((0.25, 0.75), (0.75, 0.25))),
    _registry_sites(((0.0, 0.0), (0.5, 0.5))),
)


def _xy_registry_matches(
    plane: list[_AtomPoint],
    expected: tuple[tuple[float, float], ...],
) -> bool:
    observed = sorted(
        (
            point[0].fractional.x % 1.0,
            point[0].fractional.y % 1.0,
        )
        for point in plane
    )
    return len(observed) == len(expected) and all(
        abs(actual_x - expected_x) <= _LENGTH_TOLERANCE
        and abs(actual_y - expected_y) <= _LENGTH_TOLERANCE
        for (actual_x, actual_y), (expected_x, expected_y) in zip(observed, expected)
    )


def _strict_equal(observed: object, expected: object) -> bool:
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            key in observed and _strict_equal(observed[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _strict_equal(actual, wanted)
            for actual, wanted in zip(observed, expected)
        )
    return observed == expected


def _metadata_mismatches(metadata: dict[str, object]) -> tuple[str, ...]:
    expected = {
        "domain_plugin": {
            "plugin_id": PLUGIN_ID,
            "contract_version": CONTRACT_VERSION,
            "implementation_version": IMPLEMENTATION_VERSION,
        },
        "domain": "surface",
        "material": MATERIAL,
        "polytype": "3C",
        "structure_family": "zinc blende",
        "source_provenance": {
            "source_id": SOURCE_ID,
            "provider": "Crystallography Open Database",
            "provider_revision": "278158",
            "license_spdx_id": "CC0-1.0",
            "reference_lattice_angstrom": BULK_LATTICE_ANGSTROM,
            "coordinate_access": "denied",
        },
        "surface": {
            "miller_indices": [0, 0, 1],
            "orientation": "3C-SiC(001)",
            "face": "Si",
            "in_plane_supercell": [2, 2],
            "bilayer_count": 4,
            "atomic_plane_count": 8,
            "top_termination": "Si",
            "bottom_termination": "C",
            "surface_axis": "c",
            "vacuum_angstrom": 15.0,
            "vacuum_definition": "total_gap_over_full_atomic_extent",
            "passivation": {
                "surface": "bottom",
                "element": "H",
                "hydrogens_per_bottom_carbon": 2,
                "carbon_hydrogen_bond_angstrom": 1.09,
            },
            "ideal": True,
            "unreconstructed": True,
            "relaxed": False,
        },
        "composition": {"Si": 32, "C": 32, "H": 16, "total": 80},
        "simulations": [],
        "workflow": {
            "preview_only": True,
            "calculation_plan": False,
            "materials_studio_roundtrip": False,
            "scientifically_verified": False,
        },
    }
    mismatches = []
    if set(metadata) != set(expected):
        mismatches.append("metadata")
    for key, value in expected.items():
        if key not in metadata or not _strict_equal(metadata[key], value):
            mismatches.append(f"metadata.{key}")
    return tuple(mismatches)


def _pairwise_contact_metrics(
    atoms: list[BasisAtomSpec],
    vectors: tuple[_Vector, _Vector, _Vector],
) -> tuple[float, int, int]:
    minimum = math.inf
    duplicates = 0
    severe = 0
    for left_index, left in enumerate(atoms):
        for right in atoms[left_index + 1 :]:
            distance = _norm(_minimum_image_vector(left, right, vectors))
            minimum = min(minimum, distance)
            if distance < _DUPLICATE_DISTANCE:
                duplicates += 1
            elif distance < _SEVERE_SHORT_CONTACT_DISTANCE:
                severe += 1
    return (minimum if math.isfinite(minimum) else 0.0), duplicates, severe


def _back_bonds_match(
    vectors_for_carbon: list[_Vector],
    unit_a: _Vector,
    unit_b: _Vector,
    normal: _Vector,
) -> bool:
    if len(vectors_for_carbon) != HYDROGENS_PER_BOTTOM_CARBON:
        return False
    lengths = tuple(_norm(vector) for vector in vectors_for_carbon)
    if any(length <= _DUPLICATE_DISTANCE for length in lengths):
        return False
    expected_component = CARBON_HYDROGEN_BOND_ANGSTROM / math.sqrt(3.0)
    components_match = all(
        abs(abs(_dot(vector, unit_a)) - expected_component) <= _BOND_TOLERANCE
        and abs(abs(_dot(vector, unit_b)) - expected_component) <= _BOND_TOLERANCE
        and abs(_dot(vector, normal) + expected_component) <= _BOND_TOLERANCE
        for vector in vectors_for_carbon
    )
    azimuths = {
        (
            1 if _dot(vector, unit_a) > 0.0 else -1,
            1 if _dot(vector, unit_b) > 0.0 else -1,
        )
        for vector in vectors_for_carbon
    }
    return components_match and azimuths == {(-1, -1), (1, 1)}


def validate_fixed_model(model: ModelSpec) -> DomainValidationReport:
    """Validate geometry and identity without trusting builder atom IDs or metadata."""

    if not isinstance(model, ModelSpec):
        raise TypeError("validate requires a ModelSpec")

    issues: list[RuntimeIssue] = []
    facts: list[DomainFact] = []
    if model.revision != 0:
        issues.append(
            _issue(
                "revision_mismatch",
                "Surface preview revision must be zero.",
                "revision",
            )
        )
    if model.simulation is not None:
        issues.append(
            _issue(
                "simulation_not_empty",
                "The preview-only surface must not contain a simulation.",
                "simulation",
            )
        )
    if model.outputs:
        issues.append(
            _issue(
                "outputs_not_empty",
                "The preview-only surface must not request outputs.",
                "outputs",
            )
        )

    crystal = model.model if isinstance(model.model, CrystalSpec) else None
    if model.model_type is not ModelType.CRYSTAL or crystal is None:
        issues.append(
            _issue(
                "invalid_model_kind",
                "The fixed surface must be represented as a crystal.",
                "model_type",
            )
        )
    else:
        if crystal.operations:
            issues.append(
                _issue(
                    "crystal_operations_not_empty",
                    "The built crystal must be fully materialized without operations.",
                    "model.operations",
                )
            )
        atoms = crystal.basis_atoms
        composition = Counter(atom.element for atom in atoms)
        expected_composition = {
            "Si": SILICON_COUNT,
            "C": CARBON_COUNT,
            "H": HYDROGEN_COUNT,
        }
        facts.extend(
            (
                DomainFact(code="atom_count", value=len(atoms)),
                DomainFact(code="silicon_count", value=composition.get("Si", 0)),
                DomainFact(code="carbon_count", value=composition.get("C", 0)),
                DomainFact(code="hydrogen_count", value=composition.get("H", 0)),
            )
        )
        if len(atoms) != TOTAL_ATOM_COUNT or composition != expected_composition:
            issues.append(
                _issue(
                    "composition_mismatch",
                    "Composition must be exactly Si32 C32 H16 (80 atoms).",
                    "model.basis_atoms",
                )
            )

        try:
            vectors = _lattice_vectors(crystal)
        except ValueError as exc:
            issues.append(_issue("invalid_lattice", str(exc), "model.lattice"))
        else:
            a_vector, b_vector, c_vector = vectors
            normal_raw = _cross(a_vector, b_vector)
            normal_length = _norm(normal_raw)
            normal = _scale(normal_raw, 1.0 / normal_length)
            cell_height = _dot(c_vector, normal)
            expected_in_plane = IN_PLANE_REPEAT * BULK_LATTICE_ANGSTROM
            expected_cell_c = (
                VACUUM_ANGSTROM
                + CARBON_HYDROGEN_BOND_ANGSTROM / math.sqrt(3.0)
                + (ATOMIC_PLANE_COUNT - 1) * BULK_LATTICE_ANGSTROM / 4.0
            )
            lattice_matches = (
                abs(crystal.lattice.a - expected_in_plane) <= _LENGTH_TOLERANCE
                and abs(crystal.lattice.b - expected_in_plane) <= _LENGTH_TOLERANCE
                and abs(crystal.lattice.c - expected_cell_c) <= _LENGTH_TOLERANCE
                and all(
                    abs(angle - 90.0) <= _LENGTH_TOLERANCE
                    for angle in (
                        crystal.lattice.alpha,
                        crystal.lattice.beta,
                        crystal.lattice.gamma,
                    )
                )
            )
            if not lattice_matches:
                issues.append(
                    _issue(
                        "lattice_mismatch",
                        "Lattice must be the fixed orthogonal 2x2 conventional "
                        "slab cell.",
                        "model.lattice",
                    )
                )

            points: list[_AtomPoint] = []
            for atom in atoms:
                cartesian = _cartesian(atom.fractional.as_tuple(), vectors)
                points.append((atom, cartesian, _dot(cartesian, normal)))
            substrate_points = [
                point for point in points if point[0].element in {"Si", "C"}
            ]
            planes = _group_planes(substrate_points)
            facts.append(DomainFact(code="substrate_plane_count", value=len(planes)))
            if len(planes) != ATOMIC_PLANE_COUNT:
                issues.append(
                    _issue(
                        "plane_count_mismatch",
                        "The substrate must contain exactly eight atomic planes.",
                        "model.basis_atoms",
                    )
                )

            expected_order = ("C", "Si", "C", "Si", "C", "Si", "C", "Si")
            observed_order = tuple(
                next(iter({point[0].element for point in plane}))
                if len({point[0].element for point in plane}) == 1
                else "mixed"
                for plane in planes
            )
            if observed_order != expected_order:
                issues.append(
                    _issue(
                        "plane_order_mismatch",
                        "Plane order must alternate C/Si from bottom C to top Si.",
                        "model.basis_atoms",
                    )
                )
            if any(len(plane) != 8 for plane in planes):
                issues.append(
                    _issue(
                        "plane_population_mismatch",
                        "Every substrate plane must contain exactly eight atoms.",
                        "model.basis_atoms",
                    )
                )
            if len(planes) == ATOMIC_PLANE_COUNT and any(
                not _xy_registry_matches(plane, expected)
                for plane, expected in zip(planes, _EXPECTED_PLANE_REGISTRIES)
            ):
                issues.append(
                    _issue(
                        "in_plane_registry_mismatch",
                        "Plane coordinates do not form the fixed 2x2 "
                        "conventional registry.",
                        "model.basis_atoms",
                    )
                )
            if len(planes) == ATOMIC_PLANE_COUNT:
                plane_heights = [
                    sum(point[2] for point in plane) / len(plane) for plane in planes
                ]
                expected_spacing = BULK_LATTICE_ANGSTROM / 4.0
                if any(
                    abs(right - left - expected_spacing) > _LENGTH_TOLERANCE
                    for left, right in zip(plane_heights, plane_heights[1:])
                ):
                    issues.append(
                        _issue(
                            "plane_spacing_mismatch",
                            "Adjacent substrate planes must have the fixed "
                            "3C-SiC spacing.",
                            "model.basis_atoms",
                        )
                    )

            bottom_carbons = (
                [point[0] for point in planes[0] if point[0].element == "C"]
                if planes
                else []
            )
            facts.append(
                DomainFact(code="bottom_carbon_count", value=len(bottom_carbons))
            )
            if len(bottom_carbons) != BOTTOM_CARBON_COUNT:
                issues.append(
                    _issue(
                        "bottom_carbon_count_mismatch",
                        "The bottom plane must contain exactly eight carbon atoms.",
                        "model.basis_atoms",
                    )
                )

            hydrogens = [atom for atom in atoms if atom.element == "H"]
            assignments: dict[str, list[_Vector]] = {
                carbon.id: [] for carbon in bottom_carbons
            }
            unmatched_hydrogens = 0
            bad_bond_distances = 0
            for hydrogen in hydrogens:
                if not bottom_carbons:
                    unmatched_hydrogens += 1
                    continue
                carbon, vector = min(
                    (
                        (carbon, _minimum_image_vector(carbon, hydrogen, vectors))
                        for carbon in bottom_carbons
                    ),
                    key=lambda item: (_dot(item[1], item[1]), item[0].id),
                )
                distance = _norm(vector)
                if abs(distance - CARBON_HYDROGEN_BOND_ANGSTROM) > _BOND_TOLERANCE:
                    bad_bond_distances += 1
                assignments[carbon.id].append(vector)

            facts.append(
                DomainFact(
                    code="periodic_carbon_hydrogen_bond_count",
                    value=sum(
                        len(vectors_for_carbon)
                        for vectors_for_carbon in assignments.values()
                    ),
                )
            )
            if unmatched_hydrogens or any(
                len(vectors_for_carbon) != HYDROGENS_PER_BOTTOM_CARBON
                for vectors_for_carbon in assignments.values()
            ):
                issues.append(
                    _issue(
                        "hydrogen_coordination_mismatch",
                        "Each bottom carbon must own exactly two periodic nearest "
                        "hydrogens.",
                        "model.basis_atoms",
                    )
                )
            if bad_bond_distances:
                issues.append(
                    _issue(
                        "carbon_hydrogen_distance_mismatch",
                        "Every periodic bottom C-H distance must be exactly "
                        "1.09 angstrom.",
                        "model.basis_atoms",
                    )
                )

            unit_a = _scale(a_vector, 1.0 / _norm(a_vector))
            unit_b = _scale(b_vector, 1.0 / _norm(b_vector))
            back_bonds_ok = bool(assignments) and all(
                _back_bonds_match(
                    vectors_for_carbon,
                    unit_a,
                    unit_b,
                    normal,
                )
                for vectors_for_carbon in assignments.values()
            )
            if not back_bonds_ok:
                issues.append(
                    _issue(
                        "back_bond_geometry_mismatch",
                        "Hydrogens must follow the two missing tetrahedral back bonds.",
                        "model.basis_atoms",
                    )
                )

            if points:
                min_height = min(point[2] for point in points)
                max_height = max(point[2] for point in points)
                atom_extent = max_height - min_height
                vacuum = cell_height - atom_extent
                center_offset = (min_height + max_height) / 2.0 - cell_height / 2.0
                facts.extend(
                    (
                        DomainFact(
                            code="vacuum_angstrom",
                            value=vacuum,
                            unit="angstrom",
                        ),
                        DomainFact(
                            code="full_atom_extent_center_offset_angstrom",
                            value=center_offset,
                            unit="angstrom",
                        ),
                    )
                )
                if abs(vacuum - VACUUM_ANGSTROM) > _LENGTH_TOLERANCE:
                    issues.append(
                        _issue(
                            "vacuum_mismatch",
                            "Total vacuum over the full atom extent must be "
                            "15.0 angstrom.",
                            "model.lattice.c",
                        )
                    )
                if abs(center_offset) > _LENGTH_TOLERANCE:
                    issues.append(
                        _issue(
                            "slab_not_centered",
                            "The full atom extent must be centered in the "
                            "periodic cell.",
                            "model.basis_atoms",
                        )
                    )

            minimum_contact, duplicate_count, severe_count = _pairwise_contact_metrics(
                atoms, vectors
            )
            facts.append(
                DomainFact(
                    code="minimum_periodic_contact_angstrom",
                    value=minimum_contact,
                    unit="angstrom",
                )
            )
            if duplicate_count:
                issues.append(
                    _issue(
                        "duplicate_atoms",
                        "The model contains periodic duplicate atom positions.",
                        "model.basis_atoms",
                    )
                )
            if severe_count:
                issues.append(
                    _issue(
                        "severe_short_contacts",
                        "The model contains nonduplicate periodic contacts below "
                        "0.70 angstrom.",
                        "model.basis_atoms",
                    )
                )

    metadata_mismatches = _metadata_mismatches(model.metadata)
    if metadata_mismatches:
        issues.append(
            _issue(
                "metadata_identity_mismatch",
                "Required fixed-profile metadata differs at: "
                + ", ".join(metadata_mismatches),
                "metadata",
            )
        )

    issues.append(
        RuntimeIssue(
            kind=RuntimeIssueKind.PREVIEW_WARNING,
            code="ideal_unrelaxed_preview",
            message=(
                "This is an ideal, unreconstructed, unrelaxed preview model with "
                "no calculation or Materials Studio round-trip evidence."
            ),
            field_path="metadata.workflow",
        )
    )
    has_blocking = any(issue.is_blocking for issue in issues)
    return DomainValidationReport(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id=PLUGIN_ID,
        plugin_contract_version=CONTRACT_VERSION,
        plugin_implementation_version=IMPLEMENTATION_VERSION,
        model_spec_digest=model_spec_digest(model),
        status=(
            ValidationStatus.FAIL
            if has_blocking
            else ValidationStatus.PASS_WITH_WARNINGS
        ),
        facts=tuple(facts),
        issues=tuple(issues),
        preview_eligible=not has_blocking,
    )


__all__ = ["validate_fixed_model"]
