"""Analytical construction of the one supported ideal surface profile."""

from __future__ import annotations

import math

from material_studio_mcp_server.specs import (
    AcceptanceCriteria,
    BasisAtomSpec,
    CrystalSpec,
    FractionalVector3,
    LatticeSpec,
    ModelSpec,
    ModelType,
)

from .constants import (
    ATOMIC_PLANE_COUNT,
    BILAYER_COUNT,
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


_PLANE_REGISTRIES = (
    ("C", ((0.25, 0.25), (0.75, 0.75))),
    ("Si", ((0.0, 0.5), (0.5, 0.0))),
    ("C", ((0.25, 0.75), (0.75, 0.25))),
    ("Si", ((0.0, 0.0), (0.5, 0.5))),
    ("C", ((0.25, 0.25), (0.75, 0.75))),
    ("Si", ((0.0, 0.5), (0.5, 0.0))),
    ("C", ((0.25, 0.75), (0.75, 0.25))),
    ("Si", ((0.0, 0.0), (0.5, 0.5))),
)


def fixed_cell_dimensions() -> tuple[float, float, float]:
    """Return the exact orthogonal cell lengths for the fixed profile."""

    in_plane = IN_PLANE_REPEAT * BULK_LATTICE_ANGSTROM
    back_bond_z = CARBON_HYDROGEN_BOND_ANGSTROM / math.sqrt(3.0)
    semiconductor_extent = (ATOMIC_PLANE_COUNT - 1) * BULK_LATTICE_ANGSTROM / 4.0
    c_length = VACUUM_ANGSTROM + back_bond_z + semiconductor_extent
    return in_plane, in_plane, c_length


def _registry_sites(
    registry: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    sites = []
    for repeat_x in range(IN_PLANE_REPEAT):
        for repeat_y in range(IN_PLANE_REPEAT):
            for local_x, local_y in registry:
                sites.append(
                    (
                        (repeat_x + local_x) / IN_PLANE_REPEAT,
                        (repeat_y + local_y) / IN_PLANE_REPEAT,
                    )
                )
    return tuple(sorted(sites))


def build_fixed_model(project_id: str) -> ModelSpec:
    """Build a fresh revision-zero ModelSpec without external side effects."""

    cell_a, cell_b, cell_c = fixed_cell_dimensions()
    back_bond_component = CARBON_HYDROGEN_BOND_ANGSTROM / math.sqrt(3.0)
    bottom_hydrogen_z = VACUUM_ANGSTROM / 2.0
    bottom_carbon_z = bottom_hydrogen_z + back_bond_component
    plane_spacing = BULK_LATTICE_ANGSTROM / 4.0

    atoms: list[BasisAtomSpec] = []
    bottom_carbons: list[BasisAtomSpec] = []
    for plane_index, (element, registry) in enumerate(_PLANE_REGISTRIES, start=1):
        cartesian_z = bottom_carbon_z + (plane_index - 1) * plane_spacing
        for site_index, (fractional_x, fractional_y) in enumerate(
            _registry_sites(registry), start=1
        ):
            atom = BasisAtomSpec(
                id=f"{element}_p{plane_index:02d}_{site_index:02d}",
                element=element,
                fractional=FractionalVector3(
                    x=fractional_x,
                    y=fractional_y,
                    z=cartesian_z / cell_c,
                ),
            )
            atoms.append(atom)
            if plane_index == 1:
                bottom_carbons.append(atom)

    for carbon_index, carbon in enumerate(bottom_carbons, start=1):
        carbon_x = carbon.fractional.x * cell_a
        carbon_y = carbon.fractional.y * cell_b
        for bond_index, sign in enumerate((-1.0, 1.0), start=1):
            hydrogen_x = (carbon_x + sign * back_bond_component) % cell_a
            hydrogen_y = (carbon_y + sign * back_bond_component) % cell_b
            atoms.append(
                BasisAtomSpec(
                    id=f"H_c{carbon_index:02d}_{bond_index}",
                    element="H",
                    fractional=FractionalVector3(
                        x=hydrogen_x / cell_a,
                        y=hydrogen_y / cell_b,
                        z=bottom_hydrogen_z / cell_c,
                    ),
                )
            )

    crystal = CrystalSpec(
        name="sic_3c_001_si_face_2x2_4bilayer_h_backed",
        lattice=LatticeSpec(
            a=cell_a,
            b=cell_b,
            c=cell_c,
            alpha=90.0,
            beta=90.0,
            gamma=90.0,
        ),
        basis_atoms=atoms,
        operations=[],
    )
    return ModelSpec(
        project_id=project_id,
        revision=0,
        model_type=ModelType.CRYSTAL,
        model=crystal,
        simulation=None,
        outputs={},
        acceptance=AcceptanceCriteria(
            max_warnings=3,
            require_convergence=False,
            notes=[
                "Preview-only ideal unreconstructed and unrelaxed surface model.",
                "No Materials Studio round trip or calculation evidence is claimed.",
            ],
        ),
        metadata={
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
                "bilayer_count": BILAYER_COUNT,
                "atomic_plane_count": ATOMIC_PLANE_COUNT,
                "top_termination": "Si",
                "bottom_termination": "C",
                "surface_axis": "c",
                "vacuum_angstrom": VACUUM_ANGSTROM,
                "vacuum_definition": "total_gap_over_full_atomic_extent",
                "passivation": {
                    "surface": "bottom",
                    "element": "H",
                    "hydrogens_per_bottom_carbon": HYDROGENS_PER_BOTTOM_CARBON,
                    "carbon_hydrogen_bond_angstrom": (
                        CARBON_HYDROGEN_BOND_ANGSTROM
                    ),
                },
                "ideal": True,
                "unreconstructed": True,
                "relaxed": False,
            },
            "composition": {
                "Si": SILICON_COUNT,
                "C": CARBON_COUNT,
                "H": HYDROGEN_COUNT,
                "total": TOTAL_ATOM_COUNT,
            },
            "simulations": [],
            "workflow": {
                "preview_only": True,
                "calculation_plan": False,
                "materials_studio_roundtrip": False,
                "scientifically_verified": False,
            },
        },
    )


__all__ = ["build_fixed_model", "fixed_cell_dimensions"]
