from __future__ import annotations

import math
from collections import Counter

import pytest

from material_studio_mcp_server.specs import ModelSpec


LATTICE_3C_SIC_ANGSTROM = 4.3596


def _cartesian_positions(model: ModelSpec) -> dict[str, tuple[float, float, float]]:
    crystal = model.model
    lattice = crystal.lattice
    assert lattice.alpha == lattice.beta == lattice.gamma == 90.0
    return {
        atom.id: (
            atom.fractional.x * lattice.a,
            atom.fractional.y * lattice.b,
            atom.fractional.z * lattice.c,
        )
        for atom in crystal.basis_atoms
    }


def _periodic_distance_xy(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
    a: float,
    b: float,
) -> float:
    dx = right[0] - left[0]
    dy = right[1] - left[1]
    dz = right[2] - left[2]
    dx -= round(dx / a) * a
    dy -= round(dy / b) * b
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def test_fixed_surface_composition_cell_and_plane_geometry(
    built_model: ModelSpec,
) -> None:
    crystal = built_model.model
    positions = _cartesian_positions(built_model)
    composition = Counter(atom.element for atom in crystal.basis_atoms)

    assert composition == {"Si": 32, "C": 32, "H": 16}
    assert len(crystal.basis_atoms) == 80
    assert crystal.lattice.a == pytest.approx(2 * LATTICE_3C_SIC_ANGSTROM)
    assert crystal.lattice.b == pytest.approx(2 * LATTICE_3C_SIC_ANGSTROM)

    substrate = [
        (positions[atom.id][2], atom.element)
        for atom in crystal.basis_atoms
        if atom.element in {"Si", "C"}
    ]
    plane_heights = sorted({round(z, 9) for z, _ in substrate})
    assert len(plane_heights) == 8
    assert [
        Counter(element for z, element in substrate if round(z, 9) == height)
        for height in plane_heights
    ] == [
        {"C": 8},
        {"Si": 8},
        {"C": 8},
        {"Si": 8},
        {"C": 8},
        {"Si": 8},
        {"C": 8},
        {"Si": 8},
    ]
    assert [right - left for left, right in zip(plane_heights, plane_heights[1:])] == pytest.approx(
        [LATTICE_3C_SIC_ANGSTROM / 4.0] * 7
    )

    surface = built_model.metadata["surface"]
    assert surface["in_plane_supercell"] == [2, 2]
    assert surface["bilayer_count"] == 4
    assert surface["atomic_plane_count"] == 8
    assert surface["bottom_termination"] == "C"
    assert surface["top_termination"] == "Si"


def test_full_atom_extent_has_exact_centered_vacuum(built_model: ModelSpec) -> None:
    crystal = built_model.model
    positions = _cartesian_positions(built_model)
    heights = [position[2] for position in positions.values()]
    atom_extent = max(heights) - min(heights)
    vacuum = crystal.lattice.c - atom_extent
    center = (min(heights) + max(heights)) / 2.0

    assert vacuum == pytest.approx(15.0, abs=1.0e-10)
    assert center == pytest.approx(crystal.lattice.c / 2.0, abs=1.0e-10)


def test_each_bottom_carbon_has_two_periodic_109_angstrom_hydrogens(
    built_model: ModelSpec,
) -> None:
    crystal = built_model.model
    positions = _cartesian_positions(built_model)
    substrate = [
        atom for atom in crystal.basis_atoms if atom.element in {"Si", "C"}
    ]
    bottom_height = min(positions[atom.id][2] for atom in substrate)
    bottom_carbons = [
        atom
        for atom in substrate
        if atom.element == "C"
        and positions[atom.id][2] == pytest.approx(bottom_height)
    ]
    hydrogens = [atom for atom in crystal.basis_atoms if atom.element == "H"]
    assignments = {carbon.id: [] for carbon in bottom_carbons}

    for hydrogen in hydrogens:
        carbon = min(
            bottom_carbons,
            key=lambda item: _periodic_distance_xy(
                positions[item.id],
                positions[hydrogen.id],
                crystal.lattice.a,
                crystal.lattice.b,
            ),
        )
        distance = _periodic_distance_xy(
            positions[carbon.id],
            positions[hydrogen.id],
            crystal.lattice.a,
            crystal.lattice.b,
        )
        assignments[carbon.id].append(distance)
        assert positions[hydrogen.id][2] < positions[carbon.id][2]

    assert len(bottom_carbons) == 8
    assert all(len(distances) == 2 for distances in assignments.values())
    assert all(
        distance == pytest.approx(1.09, abs=1.0e-10)
        for distances in assignments.values()
        for distance in distances
    )
