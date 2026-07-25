"""Structured model diagnostics and view audit helpers."""

from __future__ import annotations

import json
import csv
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from .castep_electronic import (
    assess_castep_electronic_result,
    verify_castep_electronic_receipt,
)
from .castep_relaxation import (
    CASTEP_RELAXATION_RECEIPT_SCHEMA,
    crystal_structure_sha256,
)
from .diagnostic_contract import (
    DIAGNOSTIC_EXPORT_CONTRACT_VERSION,
    VIEW_BUNDLE_SCHEMA_VERSION,
)
from .semiconductor_contracts import (
    DIAMOND_NV_CHARGE_SPIN_BINDING_REQUIRED_STATUS,
    DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS,
    DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS,
    DIAMOND_NV_REVIEWED_BACKEND_STATUSES,
    diamond_nv_castep_binding_receipt,
)
from .semiconductor_site_selection import (
    PERIODIC_MAXIMIN_STRATEGY,
    analyze_periodic_site_pair_distribution,
    analyze_periodic_site_short_range_order,
    audit_periodic_maximin_selection,
)
from .specs.castep import (
    CASTEP_CHARGE_SPIN_API_CONTRACT,
    CASTEP_DIPOLE_CORRECTION_API_CONTRACT,
    CASTEP_DIPOLE_CORRECTION_API_PROPERTY,
    CASTEP_DIPOLE_MINIMUM_VACUUM_ANGSTROM,
    CastepDipoleCorrection,
    CastepEnergySpec,
    CastepTask,
    normalize_castep_task,
)
from .specs.crystal import BasisAtomSpec, CrystalSpec, LatticeSpec
from .specs.molecule import MoleculeSpec
from .specs.project import ImportedStructureSpec, ModelSpec


DEFAULT_VIEWS = ("front", "back", "right", "left", "top", "bottom", "isometric")
SEMICONDUCTOR_VIEW_DEFAULT_POLICY_VERSION = 1
SEMICONDUCTOR_DEFAULT_CARTESIAN_VIEWS = ("front", "top", "isometric")
SEMICONDUCTOR_BULK_DEFAULT_VIEW_PROFILES: dict[str, tuple[str, ...]] = {
    "cubic": (
        "crystal_plane_100",
        "crystal_plane_110",
        "crystal_plane_111",
    ),
    "hexagonal": (
        "crystal_plane_0001",
        "crystal_plane_10m10",
        "crystal_plane_11m20",
    ),
    "tetragonal": (
        "crystal_plane_001",
        "crystal_plane_100",
        "crystal_plane_110",
    ),
    "orthorhombic": (
        "crystal_plane_100",
        "crystal_plane_010",
        "crystal_plane_001",
    ),
    "monoclinic": (
        "crystal_plane_100",
        "crystal_plane_010",
        "crystal_plane_001",
    ),
    "rhombohedral": (
        "crystal_plane_001",
        "crystal_plane_100",
        "crystal_plane_110",
    ),
    "triclinic": (
        "crystal_plane_100",
        "crystal_plane_010",
        "crystal_plane_001",
    ),
}
SEMICONDUCTOR_LATTICE_FAMILY_MARKERS: tuple[
    tuple[str, tuple[str, ...]], ...
] = (
    (
        "hexagonal",
        (
            "hexagonal",
            "wurtzite",
            "4h-sic",
            "4h sic",
            "6h-sic",
            "6h sic",
            "2d tmd",
            "layered tmd",
            "hbn",
        ),
    ),
    (
        "cubic",
        (
            "diamond cubic",
            "zinc blende",
            "zincblende",
            "3c-sic",
            "3c sic",
            "cubic perovskite",
        ),
    ),
    (
        "orthorhombic",
        ("orthorhombic", "phosphorene", "black phosphorus"),
    ),
    (
        "monoclinic",
        ("monoclinic", "beta-ga2o3", "beta gallium oxide"),
    ),
    ("tetragonal", ("tetragonal",)),
    ("rhombohedral", ("rhombohedral", "trigonal")),
)
CRYSTAL_DIRECTION_VIEW_INDICES: dict[str, tuple[int, ...]] = {
    "crystal_100": (1, 0, 0),
    "crystal_010": (0, 1, 0),
    "crystal_001": (0, 0, 1),
    "crystal_110": (1, 1, 0),
    "crystal_111": (1, 1, 1),
    "crystal_0001": (0, 0, 0, 1),
}
CRYSTAL_PLANE_VIEW_INDICES: dict[str, tuple[int, ...]] = {
    "crystal_plane_100": (1, 0, 0),
    "crystal_plane_010": (0, 1, 0),
    "crystal_plane_001": (0, 0, 1),
    "crystal_plane_110": (1, 1, 0),
    "crystal_plane_111": (1, 1, 1),
    "crystal_plane_0001": (0, 0, 0, 1),
    "crystal_plane_10m10": (1, 0, -1, 0),
    "crystal_plane_11m20": (1, 1, -2, 0),
}
DIRECTION_VIEW_ONTO_MILLER_MAX_ABS_INDEX = 12
DIRECTION_VIEW_ONTO_MILLER_COLLINEARITY_SINE_TOLERANCE = 1.0e-9
ORIENTED_FRAME_VIEW_SPECS: dict[str, tuple[str, str]] = {
    "surface_normal": ("surface", "normal"),
    "surface_in_plane_1": ("surface", "in_plane_1"),
    "surface_in_plane_2": ("surface", "in_plane_2"),
    "interface_normal": ("interface", "normal"),
    "interface_in_plane_1": ("interface", "in_plane_1"),
    "interface_in_plane_2": ("interface", "in_plane_2"),
}
MAX_PROJECTED_ATOMS = 500
MAX_HEALTH_DETAIL_ROWS = 1000
VIEW_REFERENCE_SCHEMA_VERSION = "ms_mcp_view_reference_v1"
MAX_VIEW_REFERENCE_PANELS = 32
VIEW_REFERENCE_PANEL_WIDTH = 420
VIEW_REFERENCE_PANEL_HEIGHT = 360
VIEW_REFERENCE_COLUMNS = 3
VIEW_REFERENCE_HEADER_HEIGHT = 72
VIEW_REFERENCE_ELEMENT_COLORS = {
    "H": "#f5f5f5",
    "B": "#ffb5b5",
    "C": "#6f7680",
    "N": "#4f6bed",
    "O": "#e34b4b",
    "F": "#68c96b",
    "Mg": "#78c850",
    "Al": "#b7bdc8",
    "Si": "#e0b589",
    "P": "#e59b45",
    "S": "#e5d84b",
    "Cl": "#55b95c",
    "Ti": "#9aa4af",
    "Zn": "#8aa7c7",
    "Ga": "#a97ac8",
    "Ge": "#7f8f9f",
    "As": "#a56cc1",
    "Se": "#d48b45",
    "Mo": "#4c9a9a",
    "Cd": "#c77f96",
    "In": "#a66f6f",
    "Te": "#a36a45",
    "Hf": "#5da39a",
    "W": "#466f8a",
}
OXIDE_INTERFACE_SHORT_CONTACT_THRESHOLD_FRACTION = 0.55
OXIDE_INTERFACE_SPACING_TOLERANCE_ANGSTROM = 0.05
ANGSTROM3_TO_CM3 = 1.0e-24
VIEW_DEFINITIONS: dict[str, dict[str, tuple[float, float, float]]] = {
    "front": {"direction": (0.0, 0.0, 1.0), "up": (0.0, 1.0, 0.0)},
    "back": {"direction": (0.0, 0.0, -1.0), "up": (0.0, 1.0, 0.0)},
    "right": {"direction": (1.0, 0.0, 0.0), "up": (0.0, 0.0, 1.0)},
    "left": {"direction": (-1.0, 0.0, 0.0), "up": (0.0, 0.0, 1.0)},
    "top": {"direction": (0.0, 1.0, 0.0), "up": (0.0, 0.0, 1.0)},
    "bottom": {"direction": (0.0, -1.0, 0.0), "up": (0.0, 0.0, 1.0)},
    "isometric": {"direction": (1.0, 1.0, 1.0), "up": (-1.0, -1.0, 2.0)},
}
COMMON_MAX_BOND_ORDER = {
    "H": 1.0,
    "F": 1.0,
    "Cl": 1.0,
    "Br": 1.0,
    "I": 1.0,
    "C": 4.0,
    "N": 4.0,
    "O": 3.0,
}
COVALENT_RADII_ANGSTROM = {
    "H": 0.31,
    "B": 0.84,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "Mg": 1.41,
    "Al": 1.21,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
    "Cu": 1.32,
    "Si": 1.11,
    "Zn": 1.22,
    "Ga": 1.22,
    "Ge": 1.20,
    "Se": 1.20,
    "Cd": 1.44,
    "In": 1.42,
    "Sn": 1.39,
    "Pb": 1.46,
    "As": 1.19,
    "Sb": 1.39,
    "Te": 1.38,
    "Br": 1.20,
    "Mo": 0.92,
    "Nb": 0.92,
    "Ta": 0.92,
    "Ti": 1.60,
    "W": 0.92,
    "Re": 0.92,
    "Hf": 1.75,
    "I": 1.39,
}
BOND_ORDER_BY_TYPE = {
    "Single": 1.0,
    "Aromatic": 1.5,
    "Partial double": 1.5,
    "Double": 2.0,
    "Triple": 3.0,
}
GROUP_IV_SEMICONDUCTORS = {"C", "Si", "Ge", "Sn"}
III_V_CATIONS = {"B", "Al", "Ga", "In"}
III_V_ANIONS = {"N", "P", "As", "Sb"}
II_VI_CATIONS = {"Zn", "Cd"}
II_VI_ANIONS = {"O", "S", "Se", "Te"}
TMD_METALS = {"Mo", "W"}
TMD_CHALCOGENS = {"S", "Se", "Te"}
TMD_HETEROBILAYER_ELEMENTS = {
    "MoS2": ("Mo", "S"),
    "WS2": ("W", "S"),
    "MoSe2": ("Mo", "Se"),
    "WSe2": ("W", "Se"),
}
HALIDE_PEROVSKITE_B_CATIONS = {"Pb", "Sn"}
HALIDE_PEROVSKITE_HALIDES = {"F", "Cl", "Br", "I"}
SURFACE_PASSIVANTS = {"H"}
CASTEP_RECOMMENDED_MIN_CUTOFF_EV = 300
CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION = 0.08
CASTEP_PROPERTY_TASK_INTENTS = {
    "projecteddensityofstates": "projected_density_of_states",
    "projecteddos": "projected_density_of_states",
    "pdos": "projected_density_of_states",
    "band": "band_structure",
    "bands": "band_structure",
    "bandstructure": "band_structure",
    "dos": "density_of_states",
    "densityofstates": "density_of_states",
    "optical": "optical_properties",
    "optics": "optical_properties",
    "phonon": "phonon",
    "phonons": "phonon",
    "elastic": "elastic_constants",
}
SEMICONDUCTOR_BAND_PATH_LIBRARY = {
    "cubic_perovskite": {
        "bravais_lattice": "cubic_perovskite",
        "structure_markers": ("perovskite", "halide perovskite", "mapbi3", "cspbi3"),
        "path": ["Gamma", "X", "M", "Gamma", "R", "X"],
        "points": {
            "Gamma": (0.0, 0.0, 0.0),
            "X": (0.0, 0.5, 0.0),
            "M": (0.5, 0.5, 0.0),
            "R": (0.5, 0.5, 0.5),
        },
        "notes": ["Conservative simple-cubic path for ideal ABX3 halide perovskite starts."],
    },
    "fcc": {
        "bravais_lattice": "fcc",
        "structure_markers": ("diamond", "zinc blende", "zincblende"),
        "path": ["Gamma", "X", "W", "K", "Gamma", "L", "U", "W", "L", "K"],
        "points": {
            "Gamma": (0.0, 0.0, 0.0),
            "X": (0.0, 0.5, 0.5),
            "W": (0.25, 0.5, 0.75),
            "K": (0.375, 0.375, 0.75),
            "L": (0.5, 0.5, 0.5),
            "U": (0.625, 0.25, 0.625),
        },
        "notes": ["Conventional fcc path for diamond-cubic and zinc-blende semiconductor starts."],
    },
    "orthorhombic_2d": {
        "bravais_lattice": "orthorhombic_2d",
        "structure_markers": ("phosphorene", "black phosphorus", "puckered phosphorus"),
        "path": ["Gamma", "X", "S", "Y", "Gamma"],
        "points": {
            "Gamma": (0.0, 0.0, 0.0),
            "X": (0.5, 0.0, 0.0),
            "S": (0.5, 0.5, 0.0),
            "Y": (0.0, 0.5, 0.0),
        },
        "notes": ["Conservative in-plane orthorhombic 2D path for puckered phosphorene starts."],
    },
    "hexagonal": {
        "bravais_lattice": "hexagonal",
        "structure_markers": ("wurtzite", "hexagonal", "2d tmd", "layered tmd", "monolayer"),
        "path": ["Gamma", "M", "K", "Gamma", "A", "L", "H", "A", "L", "M", "K", "H"],
        "points": {
            "Gamma": (0.0, 0.0, 0.0),
            "M": (0.5, 0.0, 0.0),
            "K": (1.0 / 3.0, 1.0 / 3.0, 0.0),
            "A": (0.0, 0.0, 0.5),
            "L": (0.5, 0.0, 0.5),
            "H": (1.0 / 3.0, 1.0 / 3.0, 0.5),
        },
        "notes": ["Conventional hexagonal path for wurtzite semiconductor starts."],
    },
}
SEMICONDUCTOR_MIN_DILUTE_CELL_ATOMS = 64
SEMICONDUCTOR_MAX_DILUTE_DEFECT_FRACTION = 0.03
SEMICONDUCTOR_REFERENCE_ELECTRONIC_PROPERTIES = {
    "Si": {"electron_affinity_ev": 4.05, "band_gap_ev": 1.12},
    "Ge": {"electron_affinity_ev": 4.0, "band_gap_ev": 0.66},
    "GaN": {"electron_affinity_ev": 4.1, "band_gap_ev": 3.4},
    "AlN": {"electron_affinity_ev": 2.3, "band_gap_ev": 6.2},
    "InN": {"electron_affinity_ev": 5.1, "band_gap_ev": 0.4},
    "GaAs": {"electron_affinity_ev": 4.07, "band_gap_ev": 1.42},
    "AlAs": {"electron_affinity_ev": 3.5, "band_gap_ev": 2.16},
    "GaP": {"electron_affinity_ev": 3.8, "band_gap_ev": 2.26},
    "AlP": {"electron_affinity_ev": 3.5, "band_gap_ev": 2.45},
    "InP": {"electron_affinity_ev": 4.38, "band_gap_ev": 1.34},
    "InAs": {"electron_affinity_ev": 4.9, "band_gap_ev": 0.36},
    "GaSb": {"electron_affinity_ev": 4.06, "band_gap_ev": 0.73},
    "AlSb": {"electron_affinity_ev": 3.6, "band_gap_ev": 1.62},
    "InSb": {"electron_affinity_ev": 4.59, "band_gap_ev": 0.17},
    "CdS": {"electron_affinity_ev": 4.5, "band_gap_ev": 2.42},
    "CdSe": {"electron_affinity_ev": 4.9, "band_gap_ev": 1.74},
    "CdTe": {"electron_affinity_ev": 4.3, "band_gap_ev": 1.5},
    "ZnS": {"electron_affinity_ev": 3.9, "band_gap_ev": 3.7},
    "ZnSe": {"electron_affinity_ev": 4.09, "band_gap_ev": 2.7},
    "ZnTe": {"electron_affinity_ev": 3.5, "band_gap_ev": 2.26},
}
III_NITRIDE_POLARIZATION_REFERENCES = {
    "GaN": {
        "a_lattice_angstrom": 3.189,
        "spontaneous_polarization_c_per_m2": -0.029,
        "e31_c_per_m2": -0.49,
        "e33_c_per_m2": 0.73,
        "c13_gpa": 106.0,
        "c33_gpa": 398.0,
    },
    "AlN": {
        "a_lattice_angstrom": 3.112,
        "spontaneous_polarization_c_per_m2": -0.081,
        "e31_c_per_m2": -0.60,
        "e33_c_per_m2": 1.46,
        "c13_gpa": 108.0,
        "c33_gpa": 373.0,
    },
    "InN": {
        "a_lattice_angstrom": 3.545,
        "spontaneous_polarization_c_per_m2": -0.032,
        "e31_c_per_m2": -0.57,
        "e33_c_per_m2": 0.97,
        "c13_gpa": 92.0,
        "c33_gpa": 224.0,
    },
}
ELEMENTARY_CHARGE_C = 1.602176634e-19
NOMINAL_VALENCE_ELECTRONS = {
    "H": 1,
    "B": 3,
    "C": 4,
    "N": 5,
    "O": 6,
    "F": 7,
    "Mg": 2,
    "Al": 3,
    "Si": 4,
    "P": 5,
    "S": 6,
    "Cl": 7,
    "Zn": 2,
    "Ga": 3,
    "Ge": 4,
    "Br": 7,
    "Se": 6,
    "Cd": 2,
    "As": 5,
    "In": 3,
    "Sn": 4,
    "Sb": 5,
    "Te": 6,
    "Mo": 6,
    "Nb": 5,
    "Ta": 5,
    "Ti": 4,
    "W": 6,
    "Re": 7,
    "Hf": 4,
    "I": 7,
    "Pb": 4,
}


def _view_axis_name(value: Any) -> str | None:
    axis = str(value or "").strip().lower()
    return {"a": "a", "b": "b", "c": "c", "x": "a", "y": "b", "z": "c"}.get(
        axis
    )


def _is_semiconductor_view_domain(spec: ModelSpec) -> bool:
    metadata = spec.metadata or {}
    domain = str(metadata.get("domain") or "").strip().lower()
    family = str(metadata.get("structure_family") or "").strip().lower()
    return bool(
        "semiconductor" in domain
        or "semiconductor" in family
        or metadata.get("wide_bandgap_semiconductor") is True
        or metadata.get("oxide_semiconductor") is True
        or metadata.get("semiconductor_oxide_interface") is True
    )


def _lattice_values_close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1.0e-4, abs_tol=1.0e-3)


def _lattice_angle_close(value: float, target: float) -> bool:
    return math.isclose(value, target, rel_tol=0.0, abs_tol=5.0e-2)


def _semiconductor_lattice_family(spec: ModelSpec) -> str | None:
    if not isinstance(spec.model, CrystalSpec):
        return None

    metadata = spec.metadata or {}
    family_text = " ".join(
        str(value or "")
        for value in (
            metadata.get("structure_family"),
            metadata.get("prototype"),
            metadata.get("polytype"),
            spec.model.name,
        )
    ).lower()
    for family, markers in SEMICONDUCTOR_LATTICE_FAMILY_MARKERS:
        if any(marker in family_text for marker in markers):
            return family

    lattice = spec.model.lattice
    a_eq_b = _lattice_values_close(lattice.a, lattice.b)
    b_eq_c = _lattice_values_close(lattice.b, lattice.c)
    alpha_90 = _lattice_angle_close(lattice.alpha, 90.0)
    beta_90 = _lattice_angle_close(lattice.beta, 90.0)
    gamma_90 = _lattice_angle_close(lattice.gamma, 90.0)
    gamma_hexagonal = _lattice_angle_close(
        lattice.gamma, 120.0
    ) or _lattice_angle_close(lattice.gamma, 60.0)

    if a_eq_b and alpha_90 and beta_90 and gamma_hexagonal:
        return "hexagonal"
    if a_eq_b and b_eq_c and alpha_90 and beta_90 and gamma_90:
        return "cubic"
    if a_eq_b and b_eq_c and _lattice_values_close(
        lattice.alpha, lattice.beta
    ) and _lattice_values_close(lattice.beta, lattice.gamma):
        return "rhombohedral"
    if a_eq_b and alpha_90 and beta_90 and gamma_90:
        return "tetragonal"
    right_angle_count = sum((alpha_90, beta_90, gamma_90))
    if right_angle_count == 3:
        return "orthorhombic"
    if right_angle_count == 2:
        return "monoclinic"
    return "triclinic"


def resolve_view_selection(
    spec: ModelSpec,
    views: list[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Resolve explicit or domain-aware default views with an audit receipt."""

    metadata = spec.metadata or {}
    is_crystal = isinstance(spec.model, CrystalSpec)
    semiconductor_domain = is_crystal and _is_semiconductor_view_domain(spec)
    lattice_family = _semiconductor_lattice_family(spec) if semiconductor_domain else None
    default_view_names = list(DEFAULT_VIEWS)
    default_profile = "generic_default"
    orientation_kind: str | None = None
    orientation_axis: str | None = None
    domain_views: list[str] = []
    reason_codes: list[str] = []

    if semiconductor_domain:
        interface_axis_raw = metadata.get("interface_axis")
        surface_axis_raw = metadata.get("surface_axis")
        interface_axis = _view_axis_name(interface_axis_raw)
        surface_axis = _view_axis_name(surface_axis_raw)
        if interface_axis_raw is not None and interface_axis is None:
            reason_codes.append("invalid_interface_axis_ignored")
        if surface_axis_raw is not None and surface_axis is None:
            reason_codes.append("invalid_surface_axis_ignored")

        if interface_axis is not None:
            orientation_kind = "interface"
            orientation_axis = interface_axis
            default_profile = "semiconductor_interface_frame"
            domain_views = [
                "interface_normal",
                "interface_in_plane_1",
                "interface_in_plane_2",
            ]
            reason_codes.append("valid_interface_axis_has_priority")
        elif surface_axis is not None:
            orientation_kind = "surface"
            orientation_axis = surface_axis
            default_profile = "semiconductor_surface_frame"
            domain_views = [
                "surface_normal",
                "surface_in_plane_1",
                "surface_in_plane_2",
            ]
            reason_codes.append("valid_surface_axis_selected")
        else:
            resolved_family = lattice_family or "triclinic"
            default_profile = f"semiconductor_bulk_{resolved_family}"
            domain_views = list(
                SEMICONDUCTOR_BULK_DEFAULT_VIEW_PROFILES.get(
                    resolved_family,
                    SEMICONDUCTOR_BULK_DEFAULT_VIEW_PROFILES["triclinic"],
                )
            )
            reason_codes.append("bulk_lattice_family_selected")

        default_view_names = list(
            dict.fromkeys([*SEMICONDUCTOR_DEFAULT_CARTESIAN_VIEWS, *domain_views])
        )

    default_receipt = {
        "policy_version": SEMICONDUCTOR_VIEW_DEFAULT_POLICY_VERSION,
        "source": (
            "semiconductor_domain_default"
            if semiconductor_domain
            else "generic_default"
        ),
        "policy_applied": True,
        "explicit_views_provided": False,
        "model_type": spec.model_type.value,
        "domain": metadata.get("domain"),
        "semiconductor_domain": semiconductor_domain,
        "selection_profile": default_profile,
        "lattice_family": lattice_family,
        "orientation_kind": orientation_kind,
        "orientation_axis": orientation_axis,
        "cartesian_context_views": (
            list(SEMICONDUCTOR_DEFAULT_CARTESIAN_VIEWS)
            if semiconductor_domain
            else list(DEFAULT_VIEWS)
        ),
        "domain_diagnostic_views": domain_views,
        "view_names": default_view_names,
        "view_count": len(default_view_names),
        "reason_codes": reason_codes
        or [
            "non_crystal_generic_default"
            if not is_crystal
            else "non_semiconductor_generic_default"
        ],
        "explicit_views_override_domain_defaults": True,
    }
    if views:
        explicit_view_names = list(views)
        return explicit_view_names, {
            **default_receipt,
            "source": "explicit_request",
            "policy_applied": False,
            "explicit_views_provided": True,
            "selection_profile": "explicit_request",
            "suggested_default_profile": default_profile,
            "suggested_default_view_names": default_view_names,
            "view_names": explicit_view_names,
            "view_count": len(explicit_view_names),
            "reason_codes": ["explicit_views_preserved"],
        }
    return default_view_names, default_receipt


def model_view_audit(spec: ModelSpec, views: list[str] | None = None) -> dict[str, Any]:
    """Return JSON-serializable model health and view audit data."""

    requested_views, view_selection = resolve_view_selection(spec, views)
    points, atom_rows, warnings, model_summary = _extract_points(spec)
    geometry = _geometry_summary(points)
    view_rows = []
    for view_name in requested_views:
        definition, unsupported_warning = _view_definition_for_spec(spec, view_name)
        view_rows.append(
            _view_projection(
                view_name,
                atom_rows,
                geometry,
                definition=definition,
                unsupported_warning=unsupported_warning,
            )
        )
    return {
        "project_id": spec.project_id,
        "revision": spec.revision,
        "model_type": spec.model_type.value,
        "spec_fingerprint": _fingerprint(spec.model_dump(mode="json", exclude_none=True)),
        "metadata": spec.metadata,
        "model": model_summary,
        "atoms": [_audit_atom(atom) for atom in atom_rows],
        "geometry": geometry,
        "views": view_rows,
        "view_selection": view_selection,
        "health": _health_checks(spec, points, atom_rows, warnings),
        "simulation": spec.simulation.model_dump(mode="json") if spec.simulation else None,
        "outputs": spec.outputs,
    }


def write_view_audit_report(
    output_dir: str | Path,
    spec: ModelSpec,
    audit: dict[str, Any],
    *,
    gui_status: dict[str, Any] | None = None,
    gui_artifacts: list[dict[str, Any]] | None = None,
    modeling_health: dict[str, Any] | None = None,
) -> Path:
    """Write a view audit JSON report and return its path."""

    path = Path(output_dir) / "view_audit.json"
    payload = {
        **audit,
        "gui_status": gui_status,
        "gui_artifacts": gui_artifacts or [],
    }
    if modeling_health is not None:
        payload["modeling_health"] = modeling_health
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_view_audit_bundle(
    output_dir: str | Path,
    spec: ModelSpec,
    audit: dict[str, Any],
    *,
    gui_status: dict[str, Any] | None = None,
    gui_artifacts: list[dict[str, Any]] | None = None,
    modeling_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a JSON/CSV diagnostic bundle for external model checks."""

    output_path = Path(output_dir)
    report_path = write_view_audit_report(
        output_path,
        spec,
        audit,
        gui_status=gui_status,
        gui_artifacts=gui_artifacts,
        modeling_health=modeling_health,
    )
    bundle_dir = output_path / "view_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    health = audit.get("health") or {}
    files: dict[str, str] = {"view_audit_json": str(report_path)}
    row_counts: dict[str, int] = {}

    atoms = [_atom_csv_row(atom) for atom in audit.get("atoms", []) or []]
    files["atoms_csv"] = str(bundle_dir / "atoms.csv")
    row_counts["atoms"] = _write_csv(bundle_dir / "atoms.csv", ["atom_id", "element", "x_angstrom", "y_angstrom", "z_angstrom", "fractional_a", "fractional_b", "fractional_c"], atoms)

    structure_artifact = audit.get("structure_artifact_validation")
    if isinstance(structure_artifact, dict) and structure_artifact.get("applicable"):
        artifact_json_path = bundle_dir / "structure_artifact_validation.json"
        artifact_json_path.write_text(
            json.dumps(structure_artifact, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        files["structure_artifact_validation_json"] = str(artifact_json_path)
        files["structure_artifact_validation_csv"] = str(
            bundle_dir / "structure_artifact_validation.csv"
        )
        artifact_row = {
            "status": structure_artifact.get("status"),
            "ok": structure_artifact.get("ok"),
            "required": structure_artifact.get("required"),
            "structure_path": structure_artifact.get("structure_path"),
            "exists": structure_artifact.get("exists"),
            "sha256": structure_artifact.get("sha256"),
            "file_size_bytes": structure_artifact.get("file_size_bytes"),
            "expected_atom_count": structure_artifact.get("expected_atom_count"),
            "actual_atom_count": structure_artifact.get("actual_atom_count"),
            "atom_count_matches": structure_artifact.get("atom_count_matches"),
            "element_counts_match": structure_artifact.get("element_counts_match"),
            "atom_ids_match": structure_artifact.get("atom_ids_match"),
            "atom_elements_match": structure_artifact.get("atom_elements_match"),
            "fractional_coordinates_match": structure_artifact.get("fractional_coordinates_match"),
            "lattice_matches": structure_artifact.get("lattice_matches"),
            "max_fractional_delta": structure_artifact.get("max_fractional_delta"),
            "max_lattice_delta": structure_artifact.get("max_lattice_delta"),
            "missing_atom_ids": _json_csv_value(structure_artifact.get("missing_atom_ids")),
            "extra_atom_ids": _json_csv_value(structure_artifact.get("extra_atom_ids")),
            "element_mismatches": _json_csv_value(structure_artifact.get("element_mismatches")),
            "fractional_coordinate_mismatches": _json_csv_value(
                structure_artifact.get("fractional_coordinate_mismatches")
            ),
            "lattice_mismatches": _json_csv_value(structure_artifact.get("lattice_mismatches")),
            "errors": _json_csv_value(structure_artifact.get("errors")),
            "warnings": _json_csv_value(structure_artifact.get("warnings")),
        }
        row_counts["structure_artifact_validation"] = _write_csv(
            bundle_dir / "structure_artifact_validation.csv",
            list(artifact_row),
            [artifact_row],
        )

    bonds = list(health.get("bond_lengths_angstrom") or [])
    files["bonds_csv"] = str(bundle_dir / "bonds.csv")
    row_counts["bonds"] = _write_csv(bundle_dir / "bonds.csv", ["atom1", "atom2", "type", "length_angstrom", "bond_order"], bonds)

    bond_angles = list(health.get("bond_angles_deg") or [])
    files["bond_angles_csv"] = str(bundle_dir / "bond_angles.csv")
    row_counts["bond_angles"] = _write_csv(bundle_dir / "bond_angles.csv", ["atom1", "center_atom", "atom3", "angle_deg"], bond_angles)

    dihedrals = list(health.get("dihedral_angles_deg") or [])
    files["dihedrals_csv"] = str(bundle_dir / "dihedrals.csv")
    row_counts["dihedrals"] = _write_csv(bundle_dir / "dihedrals.csv", ["atom1", "atom2", "atom3", "atom4", "angle_deg"], dihedrals)

    connectivity = [_connectivity_csv_row(item) for item in health.get("atom_connectivity", []) or []]
    files["connectivity_csv"] = str(bundle_dir / "connectivity.csv")
    row_counts["connectivity"] = _write_csv(bundle_dir / "connectivity.csv", ["atom_id", "element", "degree", "bond_order_sum", "bonded_atoms"], connectivity)

    close_contacts = list(health.get("nonbonded_close_contacts") or [])
    files["close_contacts_csv"] = str(bundle_dir / "close_contacts.csv")
    row_counts["close_contacts"] = _write_csv(bundle_dir / "close_contacts.csv", ["atom1", "atom2", "distance_angstrom", "threshold_angstrom"], close_contacts)

    if "crystal_nearest_neighbors" in health:
        crystal_nearest = [_crystal_nearest_csv_row(item) for item in health.get("crystal_nearest_neighbors") or []]
        files["crystal_nearest_neighbors_csv"] = str(bundle_dir / "crystal_nearest_neighbors.csv")
        row_counts["crystal_nearest_neighbors"] = _write_csv(
            bundle_dir / "crystal_nearest_neighbors.csv",
            [
                "atom_id",
                "element",
                "nearest_atom_id",
                "nearest_element",
                "distance_angstrom",
                "image_offset_a",
                "image_offset_b",
                "image_offset_c",
            ],
            crystal_nearest,
        )

    if "crystal_coordination" in health:
        crystal_coordination = [_crystal_coordination_csv_row(item) for item in health.get("crystal_coordination") or []]
        files["crystal_coordination_csv"] = str(bundle_dir / "crystal_coordination.csv")
        row_counts["crystal_coordination"] = _write_csv(
            bundle_dir / "crystal_coordination.csv",
            [
                "atom_id",
                "element",
                "neighbor_count",
                "unique_neighbor_count",
                "nearest_distance_angstrom",
                "mean_neighbor_distance_angstrom",
                "min_neighbor_distance_angstrom",
                "max_neighbor_distance_angstrom",
                "neighbor_ids",
                "unique_neighbor_ids",
                "cutoff_rule",
            ],
            crystal_coordination,
        )

    semiconductor = health.get("semiconductor_health") or {}
    lattice_summary = semiconductor.get("lattice_summary") or {}
    if lattice_summary:
        lattice_rows = _semiconductor_lattice_csv_rows(lattice_summary)
        files["semiconductor_lattice_csv"] = str(bundle_dir / "semiconductor_lattice.csv")
        row_counts["semiconductor_lattice"] = _write_csv(
            bundle_dir / "semiconductor_lattice.csv",
            [
                "available",
                "a_angstrom",
                "b_angstrom",
                "c_angstrom",
                "alpha_deg",
                "beta_deg",
                "gamma_deg",
                "cell_volume_angstrom3",
                "atom_count",
                "non_passivant_atom_count",
                "passivant_atom_count",
                "atom_density_per_angstrom3",
                "non_passivant_atom_density_per_angstrom3",
                "volume_per_atom_angstrom3",
                "volume_per_non_passivant_atom_angstrom3",
                "is_slab",
                "surface_axis",
                "surface_axis_length_angstrom",
                "declared_vacuum_angstrom",
                "declared_vacuum_fraction",
                "atom_extent_vacuum_angstrom",
                "atom_extent_vacuum_fraction",
                "vacuum_ok",
                "slab_vacuum_status",
                "slab_vacuum_next_action",
                "centered_in_cell",
                "vacuum_asymmetry_abs_angstrom",
                "metadata_cell_mismatch",
            ],
            lattice_rows,
        )

    neighbor_distance_summary = semiconductor.get("neighbor_distance_summary") or {}
    if neighbor_distance_summary:
        neighbor_rows = _semiconductor_neighbor_distance_csv_rows(neighbor_distance_summary)
        files["semiconductor_neighbor_pairs_csv"] = str(bundle_dir / "semiconductor_neighbor_pairs.csv")
        row_counts["semiconductor_neighbor_pairs"] = _write_csv(
            bundle_dir / "semiconductor_neighbor_pairs.csv",
            [
                "pair_type",
                "pair_role",
                "count",
                "min_distance_angstrom",
                "mean_distance_angstrom",
                "max_distance_angstrom",
                "distance_spread_angstrom",
                "mean_neighbor_threshold_angstrom",
                "max_distance_to_threshold_fraction",
            ],
            neighbor_rows,
        )

    local_environment_summary = semiconductor.get("local_environment_summary") or {}
    if local_environment_summary:
        local_environment_rows = _semiconductor_local_environment_csv_rows(local_environment_summary)
        files["semiconductor_local_environment_csv"] = str(bundle_dir / "semiconductor_local_environment.csv")
        row_counts["semiconductor_local_environment"] = _write_csv(
            bundle_dir / "semiconductor_local_environment.csv",
            [
                "atom_id",
                "element",
                "neighbor_count",
                "expected_coordination",
                "coordination_outlier",
                "nearest_distance_angstrom",
                "mean_neighbor_distance_angstrom",
                "min_neighbor_distance_angstrom",
                "max_neighbor_distance_angstrom",
                "angle_count",
                "min_angle_deg",
                "mean_angle_deg",
                "max_angle_deg",
                "mean_tetrahedral_angle_deviation_deg",
                "max_tetrahedral_angle_deviation_deg",
                "neighbor_ids",
                "neighbor_elements",
            ],
            local_environment_rows,
        )

    composition_summary = semiconductor.get("composition_summary") or {}
    if composition_summary:
        composition_rows = _semiconductor_composition_csv_rows(composition_summary)
        files["semiconductor_composition_csv"] = str(bundle_dir / "semiconductor_composition.csv")
        row_counts["semiconductor_composition"] = _write_csv(
            bundle_dir / "semiconductor_composition.csv",
            [
                "element",
                "count",
                "atomic_fraction",
                "atomic_percent",
                "non_passivant_fraction",
                "non_passivant_percent",
                "role",
            ],
            composition_rows,
        )

    charge_balance_summary = semiconductor.get("charge_balance_summary") or {}
    if charge_balance_summary:
        charge_rows = _semiconductor_charge_balance_csv_rows(charge_balance_summary)
        files["semiconductor_charge_balance_csv"] = str(bundle_dir / "semiconductor_charge_balance.csv")
        row_counts["semiconductor_charge_balance"] = _write_csv(
            bundle_dir / "semiconductor_charge_balance.csv",
            [
                "element",
                "count",
                "role",
                "nominal_valence_electrons",
                "total_valence_electrons",
                "valence_fraction",
                "dopant_delta_electrons",
                "defect_charge_state_label",
                "defect_charge_state_explicit",
                "defect_charge_state_unresolved",
                "requested_net_charge_e",
                "reference_spin_multiplicity",
                "nominal_composition_electron_count_parity",
                "charge_adjusted_valence_electron_count",
                "charge_adjusted_electron_count_parity",
                "backend_charge_binding_status",
                "backend_spin_binding_status",
                "charge_spin_backend_binding_ready",
                "expected_castep_total_charge",
                "observed_castep_total_charge",
                "expected_castep_spin_treatment",
                "observed_castep_spin_treatment",
                "expected_castep_use_formal_spin",
                "observed_castep_use_formal_spin",
                "expected_castep_initial_spin",
                "observed_castep_initial_spin",
                "expected_castep_optimize_total_spin",
                "observed_castep_optimize_total_spin",
                "castep_charge_spin_all_fields_match",
                "spin_charge_review_required",
            ],
            charge_rows,
        )

    calculation_preflight_summary = semiconductor.get("calculation_preflight_summary") or {}
    if calculation_preflight_summary:
        calculation_rows = _semiconductor_calculation_preflight_csv_rows(calculation_preflight_summary)
        files["semiconductor_calculation_preflight_csv"] = str(bundle_dir / "semiconductor_calculation_preflight.csv")
        row_counts["semiconductor_calculation_preflight"] = _write_csv(
            bundle_dir / "semiconductor_calculation_preflight.csv",
            [
                "configured",
                "module",
                "task",
                "functional",
                "quality",
                "status",
                "ready_for_energy_preflight",
                "cutoff_energy_ev",
                "cutoff_status",
                "kpoint_mode",
                "kpoint_separation",
                "kpoints",
                "dipole_correction_mode",
                "dipole_correction_configured",
                "dipole_correction_enabled",
                "dipole_correction_api_contract",
                "dipole_correction_api_property",
                "total_charge",
                "spin_treatment",
                "use_formal_spin",
                "initial_spin",
                "optimize_total_spin",
                "charge_spin_settings_configured",
                "charge_spin_api_contract",
                "slab_axis",
                "slab_kpoint_axis_value",
                "output_file",
                "task_family",
                "task_intent",
                "ready_for_requested_task_preflight",
                "changes_structure",
                "requires_prior_relaxed_structure",
                "settings_review_required",
                "execution_risk",
                "next_action",
                "warning_count",
                "warnings",
            ],
            calculation_rows,
        )

    reciprocal_lattice_summary = semiconductor.get("reciprocal_lattice_summary") or {}
    if reciprocal_lattice_summary:
        reciprocal_rows = _semiconductor_reciprocal_lattice_csv_rows(reciprocal_lattice_summary)
        files["semiconductor_reciprocal_lattice_csv"] = str(bundle_dir / "semiconductor_reciprocal_lattice.csv")
        row_counts["semiconductor_reciprocal_lattice"] = _write_csv(
            bundle_dir / "semiconductor_reciprocal_lattice.csv",
            [
                "axis",
                "real_length_angstrom",
                "reciprocal_length_1_per_angstrom",
                "configured_kpoint",
                "estimated_kpoint_from_separation",
                "recommended_kpoint",
                "actual_separation_1_per_angstrom",
                "recommended_separation_1_per_angstrom",
                "surface_normal_axis",
                "surface_normal_warning",
                "recommendation_reason_codes",
            ],
            reciprocal_rows,
        )

    band_path_summary = semiconductor.get("band_path_summary") or {}
    if band_path_summary:
        band_path_rows = _semiconductor_band_path_csv_rows(band_path_summary)
        files["semiconductor_band_path_csv"] = str(bundle_dir / "semiconductor_band_path.csv")
        row_counts["semiconductor_band_path"] = _write_csv(
            bundle_dir / "semiconductor_band_path.csv",
            [
                "available",
                "task_relevant",
                "structure_family",
                "bravais_lattice",
                "path_label",
                "point_index",
                "point_label",
                "kx_fractional",
                "ky_fractional",
                "kz_fractional",
                "next_point_label",
                "segment_label",
                "requires_materials_studio_review",
                "warning_count",
                "warnings",
            ],
            band_path_rows,
        )

    band_alignment = semiconductor.get("band_alignment_summary") or {}
    if band_alignment:
        band_alignment_rows = _semiconductor_band_alignment_csv_rows(band_alignment)
        files["semiconductor_band_alignment_csv"] = str(bundle_dir / "semiconductor_band_alignment.csv")
        row_counts["semiconductor_band_alignment"] = _write_csv(
            bundle_dir / "semiconductor_band_alignment.csv",
            [
                "interface",
                "model",
                "reference",
                "reference_material",
                "material",
                "role",
                "reference_electron_affinity_ev",
                "reference_band_gap_ev",
                "material_electron_affinity_ev",
                "material_band_gap_ev",
                "conduction_band_offset_vs_reference_ev",
                "valence_band_offset_vs_reference_ev",
                "electron_barrier_height_ev",
                "hole_barrier_height_ev",
                "band_gap_difference_vs_reference_ev",
                "confines_electrons",
                "confines_holes",
                "alignment_type",
                "quality",
                "warning_count",
                "warnings",
            ],
            band_alignment_rows,
        )

    polarization_2deg = semiconductor.get("polarization_2deg_summary") or {}
    if polarization_2deg:
        polarization_2deg_rows = _semiconductor_polarization_2deg_csv_rows(polarization_2deg)
        files["semiconductor_polarization_2deg_csv"] = str(bundle_dir / "semiconductor_polarization_2deg.csv")
        row_counts["semiconductor_polarization_2deg"] = _write_csv(
            bundle_dir / "semiconductor_polarization_2deg.csv",
            [
                "interface",
                "model",
                "reference",
                "well_material",
                "barrier_material",
                "barrier_al_fraction",
                "barrier_in_fraction",
                "in_plane_lattice_angstrom",
                "barrier_reference_lattice_angstrom",
                "barrier_in_plane_strain_percent",
                "well_total_polarization_c_per_m2",
                "barrier_spontaneous_polarization_c_per_m2",
                "barrier_piezoelectric_polarization_c_per_m2",
                "barrier_total_polarization_c_per_m2",
                "polarization_discontinuity_c_per_m2",
                "sheet_charge_density_c_per_m2",
                "sheet_carrier_density_cm2_abs",
                "electron_barrier_height_ev",
                "two_deg_candidate",
                "quality",
                "warning_count",
                "warnings",
            ],
            polarization_2deg_rows,
        )

    p_gan_gate_cap = semiconductor.get("p_gan_gate_cap_summary") or {}
    if p_gan_gate_cap:
        p_gan_gate_cap_rows = _semiconductor_p_gan_gate_cap_csv_rows(p_gan_gate_cap)
        files["semiconductor_p_gan_gate_cap_csv"] = str(bundle_dir / "semiconductor_p_gan_gate_cap.csv")
        row_counts["semiconductor_p_gan_gate_cap"] = _write_csv(
            bundle_dir / "semiconductor_p_gan_gate_cap.csv",
            [
                "material",
                "role",
                "quality",
                "cap_layer_index",
                "global_layer_index",
                "fractional_center",
                "axis_coordinate_angstrom",
                "atom_count",
                "dopant_layer",
                "dopant_atom_id",
                "requested_thickness_angstrom",
                "actual_thickness_angstrom",
                "thickness_error_angstrom",
                "layer_count",
                "matched_layer_count",
                "dopant_site_found",
                "polarization_2deg_quality",
                "polarization_2deg_barrier_materials",
                "warning_count",
                "warnings",
                "atom_ids",
            ],
            p_gan_gate_cap_rows,
        )

    sublattice_summary = semiconductor.get("sublattice_balance_summary") or {}
    if sublattice_summary:
        sublattice_rows = _semiconductor_sublattice_balance_csv_rows(sublattice_summary)
        files["semiconductor_sublattice_balance_csv"] = str(bundle_dir / "semiconductor_sublattice_balance.csv")
        row_counts["semiconductor_sublattice_balance"] = _write_csv(
            bundle_dir / "semiconductor_sublattice_balance.csv",
            [
                "category",
                "elements",
                "count",
                "fraction_of_non_passivant",
                "balance_kind",
                "balance_delta_count",
                "balanced",
                "warning",
            ],
            sublattice_rows,
        )

    dopant_summary = semiconductor.get("dopant_summary") or {}
    if dopant_summary:
        dopant_rows = _semiconductor_dopant_csv_rows(dopant_summary)
        files["semiconductor_dopants_csv"] = str(bundle_dir / "semiconductor_dopants.csv")
        row_counts["semiconductor_dopants"] = _write_csv(
            bundle_dir / "semiconductor_dopants.csv",
            [
                "host_elements",
                "dopant_element",
                "count",
                "atom_ids",
                "concentration_fraction",
                "concentration_percent",
                "role_hint",
                "coordination_min",
                "coordination_max",
                "coordination_mean",
                "coordination_count",
                "coordination_outlier_count",
                "neighbor_element_counts",
            ],
            dopant_rows,
        )

    dopant_concentration_summary = semiconductor.get("dopant_concentration_summary") or {}
    if dopant_concentration_summary:
        dopant_concentration_rows = _semiconductor_dopant_concentration_csv_rows(
            dopant_concentration_summary
        )
        files["semiconductor_dopant_concentration_csv"] = str(
            bundle_dir / "semiconductor_dopant_concentration.csv"
        )
        row_counts["semiconductor_dopant_concentration"] = _write_csv(
            bundle_dir / "semiconductor_dopant_concentration.csv",
            [
                "row_type",
                "dopant_element",
                "count",
                "atom_ids",
                "concentration_fraction",
                "concentration_percent",
                "density_cm3",
                "density_log10_cm3",
                "carrier_density_cm3_signed",
                "carrier_density_cm3_abs",
                "carrier_type_hint",
                "role_hint",
                "warning_level",
                "high_concentration_warning",
                "degenerate_doping_review_required",
                "cell_volume_angstrom3",
                "cell_volume_cm3",
                "assumption",
                "next_action",
            ],
            dopant_concentration_rows,
        )

    dopant_site_summary = semiconductor.get("dopant_site_summary") or {}
    if dopant_site_summary:
        dopant_site_rows = _semiconductor_dopant_site_csv_rows(dopant_site_summary)
        files["semiconductor_dopant_sites_csv"] = str(bundle_dir / "semiconductor_dopant_sites.csv")
        row_counts["semiconductor_dopant_sites"] = _write_csv(
            bundle_dir / "semiconductor_dopant_sites.csv",
            [
                "index",
                "site_id",
                "site_element",
                "dopant_element",
                "site_family",
                "site_valence_electrons",
                "dopant_valence_electrons",
                "nominal_delta_electrons",
                "role_hint",
                "carrier_type_hint",
                "fractional_a",
                "fractional_b",
                "fractional_c",
                "auto_selected_site",
                "source",
                "actual_element",
                "record_status",
                "consistency_error",
            ],
            dopant_site_rows,
        )

    carrier_intent_summary = semiconductor.get("carrier_intent_summary") or {}
    if carrier_intent_summary:
        carrier_intent_rows = _semiconductor_carrier_intent_csv_rows(carrier_intent_summary)
        files["semiconductor_carrier_intents_csv"] = str(bundle_dir / "semiconductor_carrier_intents.csv")
        row_counts["semiconductor_carrier_intents"] = _write_csv(
            bundle_dir / "semiconductor_carrier_intents.csv",
            [
                "index",
                "requested_carrier_type",
                "requested_carrier_mechanism",
                "requested_dopant_element",
                "requested_defect_type",
                "requested_site_element",
                "requested_site_id",
                "requested_fraction",
                "requested_percent",
                "actual_carrier_type",
                "actual_carrier_type_hint",
                "actual_dopant_present",
                "actual_dopant_fraction",
                "actual_dopant_percent",
                "actual_defect_present",
                "actual_defect_count",
                "matches",
                "source",
                "warning",
            ],
            carrier_intent_rows,
        )

    junction_summary = semiconductor.get("junction_summary") or {}
    if junction_summary:
        junction_rows = _semiconductor_junction_csv_rows(junction_summary)
        files["semiconductor_junctions_csv"] = str(bundle_dir / "semiconductor_junctions.csv")
        row_counts["semiconductor_junctions"] = _write_csv(
            bundle_dir / "semiconductor_junctions.csv",
            [
                "index",
                "junction_type",
                "host_element",
                "axis",
                "p_carrier_type",
                "p_dopant_element",
                "p_site_ids",
                "p_fractional_range",
                "n_carrier_type",
                "n_dopant_element",
                "n_site_ids",
                "n_fractional_range",
                "source",
            ],
            junction_rows,
        )

    dopant_fraction_summary = semiconductor.get("dopant_fraction_summary") or {}
    if dopant_fraction_summary:
        dopant_fraction_rows = _semiconductor_dopant_fraction_csv_rows(dopant_fraction_summary)
        files["semiconductor_dopant_fraction_csv"] = str(bundle_dir / "semiconductor_dopant_fraction.csv")
        row_counts["semiconductor_dopant_fraction"] = _write_csv(
            bundle_dir / "semiconductor_dopant_fraction.csv",
            [
                "index",
                "host_element",
                "dopant_element",
                "requested_fraction",
                "requested_percent",
                "actual_fraction",
                "actual_percent",
                "candidate_site_count",
                "substituted_site_count",
                "selected_atom_ids",
                "selection_strategy",
                "scientific_scope",
                "site_selection_integrity_ok",
                "site_selection_replay_verified",
                "site_selection_geometry_unchanged",
                "selected_pair_minimum_angstrom",
                "selected_pair_mean_angstrom",
                "selected_pair_maximum_angstrom",
                "candidate_nearest_pair_distance_angstrom",
                "selected_pairs_at_candidate_nearest_distance",
                "minimum_distance_improvement_over_atom_id_order_angstrom",
                "site_selection_warning_count",
                "site_selection_error_count",
                "site_selection_warnings",
                "site_selection_errors",
                "rounding_error_fraction",
                "rounding_warning",
                "source",
            ],
            dopant_fraction_rows,
        )

    alloy_summary = semiconductor.get("alloy_summary") or {}
    if alloy_summary:
        alloy_rows = _semiconductor_alloy_csv_rows(alloy_summary)
        files["semiconductor_alloy_csv"] = str(bundle_dir / "semiconductor_alloy.csv")
        row_counts["semiconductor_alloy"] = _write_csv(
            bundle_dir / "semiconductor_alloy.csv",
            [
                "index",
                "host_element",
                "alloy_element",
                "requested_fraction",
                "requested_percent",
                "actual_fraction",
                "actual_percent",
                "candidate_site_count",
                "substituted_site_count",
                "selected_atom_ids",
                "selection_strategy",
                "scientific_scope",
                "site_selection_integrity_ok",
                "site_selection_replay_verified",
                "site_selection_geometry_unchanged",
                "selected_pair_minimum_angstrom",
                "selected_pair_mean_angstrom",
                "selected_pair_maximum_angstrom",
                "candidate_nearest_pair_distance_angstrom",
                "selected_pairs_at_candidate_nearest_distance",
                "minimum_distance_improvement_over_atom_id_order_angstrom",
                "site_selection_warning_count",
                "site_selection_error_count",
                "site_selection_warnings",
                "site_selection_errors",
                "rounding_error_fraction",
                "rounding_warning",
                "source",
            ],
            alloy_rows,
        )

    site_pair_distribution_rows = _semiconductor_site_pair_distribution_csv_rows(
        dopant_fraction_summary,
        alloy_summary,
    )
    if site_pair_distribution_rows:
        files["semiconductor_site_pair_distribution_csv"] = str(
            bundle_dir / "semiconductor_site_pair_distribution.csv"
        )
        row_counts["semiconductor_site_pair_distribution"] = _write_csv(
            bundle_dir / "semiconductor_site_pair_distribution.csv",
            [
                "entry_kind",
                "entry_index",
                "host_element",
                "replacement_element",
                "selection_strategy",
                "scientific_scope",
                "geometry_basis",
                "source_receipt_sha256",
                "candidate_geometry_sha256",
                "analysis_sha256",
                "analysis_integrity_ok",
                "current_geometry_applicable",
                "candidate_site_count",
                "selected_site_count",
                "selected_fraction",
                "fixed_composition_expected_pair_probability",
                "pair_conservation_verified",
                "shell_count",
                "reported_shell_count",
                "shells_truncated",
                "shell_index",
                "distance_min_angstrom",
                "distance_mean_angstrom",
                "distance_max_angstrom",
                "candidate_pair_count",
                "coordination_number_per_candidate",
                "candidate_degree_min",
                "candidate_degree_mean",
                "candidate_degree_max",
                "candidate_degree_uniform",
                "selected_pair_count",
                "selected_pair_fraction",
                "unselected_pair_count",
                "mixed_selected_unselected_pair_count",
                "occupancy_pair_partition_verified",
                "baseline_pair_count",
                "baseline_pair_fraction",
                "baseline_unselected_pair_count",
                "baseline_mixed_selected_unselected_pair_count",
                "baseline_occupancy_pair_partition_verified",
                "fixed_composition_expected_pair_count",
                "fixed_composition_expected_pair_fraction",
                "fixed_composition_expected_unselected_pair_count",
                "fixed_composition_expected_mixed_pair_count",
                "fixed_composition_expected_mixed_pair_fraction",
                "selected_minus_expected_pair_count",
                "baseline_minus_expected_pair_count",
                "selected_pair_avoidance_fraction",
                "selected_pair_expectation_class",
                "baseline_pair_expectation_class",
                "selected_pair_examples",
                "baseline_pair_examples",
                "nearest_shell_pair_count_reduction_vs_atom_id_order",
                "nearest_shell_pair_excess_review_required",
                "nearest_shell_pair_avoidance_observed",
                "selection_reduces_nearest_shell_pairs_vs_atom_id_order",
                "selected_pair_fraction_rmse_from_fixed_composition_expectation",
                "baseline_pair_fraction_rmse_from_fixed_composition_expectation",
                "error_count",
                "warning_count",
                "errors",
                "warnings",
            ],
            site_pair_distribution_rows,
        )

    site_short_range_order_rows = _semiconductor_site_short_range_order_csv_rows(
        dopant_fraction_summary,
        alloy_summary,
    )
    if site_short_range_order_rows:
        files["semiconductor_site_short_range_order_csv"] = str(
            bundle_dir / "semiconductor_site_short_range_order.csv"
        )
        row_counts["semiconductor_site_short_range_order"] = _write_csv(
            bundle_dir / "semiconductor_site_short_range_order.csv",
            [
                "entry_kind",
                "entry_index",
                "host_element",
                "replacement_element",
                "selection_strategy",
                "scientific_scope",
                "pair_graph_scope",
                "source_pair_distribution_analysis_sha256",
                "analysis_sha256",
                "analysis_integrity_ok",
                "current_geometry_applicable",
                "standard_periodic_shell_multiplicity_verified",
                "crystallographic_symmetry_orbits_verified",
                "candidate_site_count",
                "selected_site_count",
                "unselected_site_count",
                "selected_fraction",
                "unselected_fraction",
                "binary_occupancy_available",
                "degree_uniform_all_reported_shells",
                "classical_bulk_shell_interpretation_ready",
                "shell_count",
                "reported_shell_count",
                "shells_truncated",
                "shell_index",
                "distance_min_angstrom",
                "distance_mean_angstrom",
                "distance_max_angstrom",
                "candidate_pair_count",
                "candidate_degree_min",
                "candidate_degree_mean",
                "candidate_degree_max",
                "candidate_degree_uniform",
                "selected_selected_pair_count",
                "unselected_unselected_pair_count",
                "mixed_selected_unselected_pair_count",
                "baseline_selected_selected_pair_count",
                "baseline_unselected_unselected_pair_count",
                "baseline_mixed_selected_unselected_pair_count",
                "occupancy_pair_partition_verified",
                "classical_random_mixed_pair_expectation",
                "fixed_composition_random_mixed_pair_expectation",
                "warren_cowley_pair_count_alpha_classical",
                "baseline_warren_cowley_pair_count_alpha_classical",
                "finite_composition_corrected_pair_alpha",
                "baseline_finite_composition_corrected_pair_alpha",
                "unlike_pair_expectation_class",
                "baseline_unlike_pair_expectation_class",
                "mixed_pair_count_change_vs_atom_id_order",
                "unlike_pair_enrichment_ratio",
                "baseline_unlike_pair_enrichment_ratio",
                "nearest_shell_unlike_pair_expectation_class",
                "nearest_shell_ordering_like_unlike_pair_enrichment",
                "nearest_shell_clustering_like_unlike_pair_depletion_review_required",
                "finite_composition_corrected_pair_alpha_rmse",
                "baseline_finite_composition_corrected_pair_alpha_rmse",
                "error_count",
                "warning_count",
                "errors",
                "warnings",
            ],
            site_short_range_order_rows,
        )

    layer_profile = semiconductor.get("layer_profile_summary") or {}
    if layer_profile:
        layer_rows = _semiconductor_layer_profile_csv_rows(layer_profile)
        files["semiconductor_layer_profile_csv"] = str(bundle_dir / "semiconductor_layer_profile.csv")
        row_counts["semiconductor_layer_profile"] = _write_csv(
            bundle_dir / "semiconductor_layer_profile.csv",
            [
                "layer_index",
                "axis",
                "fractional_center",
                "fractional_min",
                "fractional_max",
                "axis_coordinate_angstrom",
                "span_fractional",
                "span_angstrom",
                "spacing_to_previous_angstrom",
                "spacing_to_next_angstrom",
                "atom_count",
                "non_passivant_atom_count",
                "passivant_atom_count",
                "element_counts",
                "atom_ids",
            ],
            layer_rows,
        )

    layer_translation = semiconductor.get("layer_translation_summary") or {}
    if layer_translation:
        translation_rows = _semiconductor_layer_translation_csv_rows(layer_translation)
        files["semiconductor_layer_translation_csv"] = str(bundle_dir / "semiconductor_layer_translation.csv")
        row_counts["semiconductor_layer_translation"] = _write_csv(
            bundle_dir / "semiconductor_layer_translation.csv",
            [
                "index",
                "is_latest",
                "target_selector",
                "layer_index",
                "layer_count",
                "profile_axis",
                "profile_fractional_center",
                "translation_axis",
                "distance_angstrom",
                "delta_fractional",
                "atom_count",
                "atom_ids",
                "periodic_wrap",
                "wrapped_atom_count",
                "wrapped_atom_ids",
                "in_plane_translation",
                "target_binding_matches_current_layer",
                "metadata_consistent",
                "source",
            ],
            translation_rows,
        )

    layer_rotation = semiconductor.get("layer_rotation_summary") or {}
    if layer_rotation:
        rotation_rows = _semiconductor_layer_rotation_csv_rows(layer_rotation)
        files["semiconductor_layer_rotation_csv"] = str(bundle_dir / "semiconductor_layer_rotation.csv")
        row_counts["semiconductor_layer_rotation"] = _write_csv(
            bundle_dir / "semiconductor_layer_rotation.csv",
            [
                "index",
                "is_latest",
                "target_selector",
                "layer_index",
                "layer_count",
                "profile_axis",
                "profile_fractional_center",
                "rotation_axis",
                "rotation_axis_source",
                "angle_degrees",
                "pivot_fractional",
                "atom_count",
                "atom_ids",
                "periodic_wrap",
                "wrapped_atom_count",
                "wrapped_atom_ids",
                "axis_orthogonality_max_abs_cosine",
                "target_binding_matches_current_layer",
                "coordinate_binding_matches_current",
                "commensurability_verified",
                "requires_commensurate_supercell",
                "requires_geometry_relaxation",
                "visual_review_only",
                "calculation_ready",
                "metadata_consistent",
                "source",
            ],
            rotation_rows,
        )

    castep_relaxation = semiconductor.get("castep_geometry_optimization_summary") or {}
    if castep_relaxation:
        files["semiconductor_castep_geometry_optimization_csv"] = str(
            bundle_dir / "semiconductor_castep_geometry_optimization.csv"
        )
        row_counts["semiconductor_castep_geometry_optimization"] = _write_csv(
            bundle_dir / "semiconductor_castep_geometry_optimization.csv",
            [
                "source_project_id",
                "source_revision",
                "target_revision",
                "task",
                "backend",
                "cell_optimization",
                "optimization_algorithm",
                "converged",
                "total_energy_kcal_per_mol",
                "enthalpy_kcal_per_mol",
                "source_structure_sha256",
                "output_structure_sha256",
                "current_structure_sha256",
                "schema_verified",
                "history_binding_verified",
                "project_binding_verified",
                "revision_binding_verified",
                "task_verified",
                "backend_verified",
                "convergence_verified",
                "atom_identity_verified",
                "source_binding_verified",
                "output_binding_verified",
                "script_binding_verified",
                "operation_binding_verified",
                "simulation_binding_verified",
                "fixed_cell_verified",
                "transition_verified",
                "fixed_cell_transition_verified",
                "quality",
                "blocking_reasons",
                "warning_count",
            ],
            [_semiconductor_castep_geometry_optimization_csv_row(castep_relaxation)],
        )

    castep_electronic = semiconductor.get("castep_electronic_result_summary") or {}
    if castep_electronic:
        files["semiconductor_castep_electronic_result_csv"] = str(
            bundle_dir / "semiconductor_castep_electronic_result.csv"
        )
        electronic_row = _semiconductor_castep_electronic_result_csv_row(
            castep_electronic,
            semiconductor.get("castep_electronic_result_assessment"),
        )
        row_counts["semiconductor_castep_electronic_result"] = _write_csv(
            bundle_dir / "semiconductor_castep_electronic_result.csv",
            list(electronic_row),
            [electronic_row],
        )
        band_edge_rows = _semiconductor_castep_band_edge_csv_rows(
            castep_electronic,
            semiconductor.get("castep_electronic_result_assessment"),
        )
        if band_edge_rows:
            files["semiconductor_castep_band_edges_csv"] = str(
                bundle_dir / "semiconductor_castep_band_edges.csv"
            )
            row_counts["semiconductor_castep_band_edges"] = _write_csv(
                bundle_dir / "semiconductor_castep_band_edges.csv",
                list(band_edge_rows[0]),
                band_edge_rows,
            )

    castep_convergence = semiconductor.get("castep_convergence_audit") or {}
    if castep_convergence:
        convergence_rows = _semiconductor_castep_convergence_csv_rows(
            castep_convergence
        )
        files["semiconductor_castep_convergence_series_csv"] = str(
            bundle_dir / "semiconductor_castep_convergence_series.csv"
        )
        row_counts["semiconductor_castep_convergence_series"] = _write_csv(
            bundle_dir / "semiconductor_castep_convergence_series.csv",
            list(convergence_rows[0]),
            convergence_rows,
        )

    commensurate_twist = semiconductor.get("commensurate_twist_summary") or {}
    if commensurate_twist:
        twist_rows = _semiconductor_commensurate_twist_csv_rows(commensurate_twist)
        files["semiconductor_commensurate_twist_csv"] = str(
            bundle_dir / "semiconductor_commensurate_twist.csv"
        )
        row_counts["semiconductor_commensurate_twist"] = _write_csv(
            bundle_dir / "semiconductor_commensurate_twist.csv",
            [
                "index",
                "is_latest",
                "commensurate_m",
                "commensurate_n",
                "supercell_index",
                "bottom_supercell_matrix",
                "top_supercell_matrix",
                "twist_orientation",
                "twist_angle_degrees",
                "requested_twist_angle_degrees",
                "twist_angle_error_degrees",
                "common_lattice_a_angstrom",
                "common_lattice_b_angstrom",
                "common_lattice_gamma_degrees",
                "interlayer_distance_angstrom",
                "monolayer_thickness_angstrom",
                "interlayer_chalcogen_gap_angstrom",
                "total_slab_thickness_angstrom",
                "vacuum_angstrom",
                "atom_count",
                "atoms_per_layer",
                "bottom_layer_atom_id_sha256",
                "top_layer_atom_id_sha256",
                "structure_sha256",
                "indices_valid",
                "supercell_index_verified",
                "matrix_pattern_verified",
                "matrix_determinant_verified",
                "angle_verified",
                "lattice_verified",
                "layer_counts_verified",
                "layer_atom_ids_verified",
                "interlayer_distance_verified",
                "interlayer_gap_verified",
                "geometry_measurement_binding_verified",
                "construction_structure_binding_matches_current",
                "structure_binding_matches_current",
                "structure_binding_scope",
                "castep_relaxation_transition_verified",
                "current_structure_sha256",
                "metadata_consistent",
                "commensurability_verified",
                "requires_geometry_relaxation",
                "geometry_relaxed",
                "calculation_ready",
                "quality",
                "warning_count",
                "source",
            ],
            twist_rows,
        )

    commensurate_heterobilayer = semiconductor.get("commensurate_heterobilayer_summary") or {}
    if commensurate_heterobilayer:
        heterobilayer_rows = _semiconductor_commensurate_heterobilayer_csv_rows(
            commensurate_heterobilayer
        )
        files["semiconductor_commensurate_heterobilayer_csv"] = str(
            bundle_dir / "semiconductor_commensurate_heterobilayer.csv"
        )
        row_counts["semiconductor_commensurate_heterobilayer"] = _write_csv(
            bundle_dir / "semiconductor_commensurate_heterobilayer.csv",
            [
                "index",
                "is_latest",
                "bottom_material",
                "top_material",
                "commensurate_m",
                "commensurate_n",
                "supercell_index",
                "bottom_supercell_matrix",
                "top_supercell_matrix",
                "twist_orientation",
                "twist_angle_degrees",
                "requested_twist_angle_degrees",
                "twist_angle_error_degrees",
                "bottom_primitive_lattice_a_angstrom",
                "top_primitive_lattice_a_angstrom",
                "unstrained_lattice_mismatch_percent",
                "strain_policy",
                "common_primitive_lattice_a_angstrom",
                "bottom_biaxial_strain_percent",
                "top_biaxial_strain_percent",
                "max_abs_biaxial_strain_percent",
                "max_strain_percent",
                "common_lattice_a_angstrom",
                "common_lattice_b_angstrom",
                "interlayer_distance_angstrom",
                "bottom_monolayer_thickness_angstrom",
                "top_monolayer_thickness_angstrom",
                "interlayer_chalcogen_gap_angstrom",
                "vacuum_angstrom",
                "atom_count",
                "bottom_layer_element_counts",
                "top_layer_element_counts",
                "layer_materials_verified",
                "strain_partition_verified",
                "strain_within_limit",
                "matrix_determinant_verified",
                "angle_verified",
                "lattice_verified",
                "interlayer_distance_verified",
                "interlayer_gap_verified",
                "structure_sha256",
                "current_structure_sha256",
                "construction_structure_binding_matches_current",
                "structure_binding_matches_current",
                "structure_binding_scope",
                "castep_relaxation_transition_verified",
                "metadata_consistent",
                "commensurability_verified",
                "requires_geometry_relaxation",
                "geometry_relaxed",
                "calculation_ready",
                "quality",
                "warning_count",
                "source",
            ],
            heterobilayer_rows,
        )

    two_dimensional_electrostatics = (
        semiconductor.get("two_dimensional_electrostatic_summary") or {}
    )
    if two_dimensional_electrostatics:
        files["semiconductor_2d_electrostatics_csv"] = str(
            bundle_dir / "semiconductor_2d_electrostatics.csv"
        )
        row_counts["semiconductor_2d_electrostatics"] = _write_csv(
            bundle_dir / "semiconductor_2d_electrostatics.csv",
            [
                "status",
                "quality",
                "bottom_material",
                "top_material",
                "surface_axis",
                "surface_orientation",
                "bottom_surface_formula",
                "top_surface_formula",
                "bottom_surface_element_counts",
                "top_surface_element_counts",
                "bottom_layer_element_counts",
                "top_layer_element_counts",
                "surface_asymmetry_expected",
                "surface_asymmetry_expected_reason",
                "expected_compositional_asymmetry_verified",
                "outer_surface_asymmetry_observed",
                "outer_surface_formulas_distinct",
                "periodic_out_of_plane_boundary",
                "cell_axis_length_angstrom",
                "declared_vacuum_angstrom",
                "bottom_vacuum_angstrom",
                "top_vacuum_angstrom",
                "vacuum_asymmetry_abs_angstrom",
                "vacuum_geometry_verified",
                "structure_binding_verified",
                "expected_structure_sha256",
                "current_structure_sha256",
                "model_geometry_verified",
                "model_geometry_normality_blocker",
                "charge_density_available",
                "dipole_moment_calculated",
                "dipole_correction_api_verified",
                "dipole_correction_api_contract",
                "dipole_correction_api_property",
                "dipole_correction_direction_property_exposed",
                "dipole_correction_direction_status",
                "dipole_correction_setting_source",
                "dipole_correction_setting_configured",
                "dipole_correction_mode",
                "dipole_correction_enabled",
                "dipole_correction_task",
                "dipole_correction_task_compatible",
                "dipole_correction_minimum_vacuum_angstrom",
                "dipole_correction_vacuum_requirement_met",
                "dipole_correction_symmetry_behavior",
                "dipole_correction_setting_verified",
                "dipole_correction_review_method",
                "geometry_relaxation_required",
                "geometry_relaxation_verified",
                "geometry_relaxation_source_revision",
                "geometry_relaxation_target_revision",
                "geometry_relaxation_output_structure_sha256",
                "calculation_review_required",
                "quantitative_electrostatic_calculation_ready",
                "calculation_blocking_reasons",
                "next_action",
                "warning_count",
            ],
            [_semiconductor_2d_electrostatic_csv_row(two_dimensional_electrostatics)],
        )

    interface_profile = semiconductor.get("interface_profile_summary") or {}
    if interface_profile:
        interface_rows = _semiconductor_interface_profile_csv_rows(interface_profile)
        files["semiconductor_interface_profile_csv"] = str(bundle_dir / "semiconductor_interface_profile.csv")
        row_counts["semiconductor_interface_profile"] = _write_csv(
            bundle_dir / "semiconductor_interface_profile.csv",
            [
                "layer_index",
                "axis",
                "fractional_center",
                "axis_coordinate_angstrom",
                "layer_role",
                "material_marker",
                "segment_index",
                "boundary_before_layer",
                "boundary_after_layer",
                "mixed_layer",
                "non_passivant_elements",
                "element_signature",
                "atom_count",
                "atom_ids",
            ],
            interface_rows,
        )

    interface_scaffold = semiconductor.get("interface_scaffold_summary") or {}
    if interface_scaffold:
        scaffold_rows = _semiconductor_interface_scaffold_csv_rows(interface_scaffold)
        files["semiconductor_interface_scaffold_csv"] = str(bundle_dir / "semiconductor_interface_scaffold.csv")
        row_counts["semiconductor_interface_scaffold"] = _write_csv(
            bundle_dir / "semiconductor_interface_scaffold.csv",
            [
                "available",
                "status",
                "interface",
                "substrate_material",
                "film_material",
                "interface_orientation",
                "axis",
                "common_in_plane_lattice_angstrom",
                "film_in_plane_strain_percent",
                "interface_gap_angstrom",
                "substrate_thickness_angstrom",
                "film_thickness_angstrom",
                "slab_thickness_angstrom",
                "vacuum_angstrom",
                "bottom_vacuum_angstrom",
                "top_vacuum_angstrom",
                "slab_vacuum_status",
                "slab_centered_in_cell",
                "slab_vacuum_ok",
                "layer_count",
                "min_interlayer_spacing_angstrom",
                "layer_spacing_warning",
                "requires_geometry_relaxation",
                "visual_hotload_ready",
                "calculation_ready",
                "warning_count",
                "warnings",
                "next_action",
            ],
            scaffold_rows,
        )

    interface_quality = semiconductor.get("interface_quality_summary") or {}
    if interface_quality:
        interface_quality_rows = _semiconductor_interface_quality_csv_rows(interface_quality)
        files["semiconductor_interface_quality_csv"] = str(bundle_dir / "semiconductor_interface_quality.csv")
        row_counts["semiconductor_interface_quality"] = _write_csv(
            bundle_dir / "semiconductor_interface_quality.csv",
            [
                "segment_index",
                "period_index",
                "segment_in_period",
                "axis",
                "material",
                "role",
                "expected_material",
                "matches_expected_material",
                "first_layer_index",
                "last_layer_index",
                "layer_count",
                "mixed_layer_count",
                "material_sequence",
                "expected_material_sequence",
                "period_count",
                "material_segment_count",
                "linear_interface_transition_count",
                "periodic_interface_transition_count",
                "expected_segment_count_from_periods",
                "segment_count_matches_periods",
                "period_sequence_complete",
                "transition_sequence_complete",
                "declared_materials_present",
                "missing_declared_materials",
                "quality",
                "warning_count",
                "warnings",
            ],
            interface_quality_rows,
        )

    oxide_interface_geometry = semiconductor.get("oxide_interface_geometry_summary") or {}
    if oxide_interface_geometry:
        oxide_interface_geometry_rows = _semiconductor_oxide_interface_geometry_csv_rows(
            oxide_interface_geometry
        )
        files["semiconductor_oxide_interface_geometry_csv"] = str(
            bundle_dir / "semiconductor_oxide_interface_geometry.csv"
        )
        row_counts["semiconductor_oxide_interface_geometry"] = _write_csv(
            bundle_dir / "semiconductor_oxide_interface_geometry.csv",
            [
                "row_kind",
                "interface",
                "axis",
                "semiconductor_material",
                "oxide_material",
                "status",
                "quality",
                "atom_binding_complete",
                "semiconductor_boundary_layer_index",
                "oxide_boundary_layer_index",
                "semiconductor_boundary_atom_count",
                "oxide_boundary_atom_count",
                "oxide_atom_count",
                "interface_spacing_definition",
                "interface_spacing_tolerance_angstrom",
                "interface_spacing_count",
                "interface_spacing_declared_count",
                "interface_spacing_binding_review_count",
                "interface_spacing_mismatch_count",
                "interface_spacing_all_declared",
                "interface_spacing_declared_values_match",
                "target_interface",
                "spacing_binding_status",
                "interface_spacing_status",
                "expected_materials",
                "lower_material",
                "upper_material",
                "lower_layer_index",
                "upper_layer_index",
                "lower_axis_coordinate_angstrom",
                "upper_axis_coordinate_angstrom",
                "actual_gap_angstrom",
                "declared_gap_angstrom",
                "declared_gap_source",
                "declared_gap_status",
                "actual_minus_declared_angstrom",
                "matches_declared_gap",
                "transition_match_count",
                "patch_operation",
                "boundary_candidate_pair_count",
                "boundary_neighbor_pair_count",
                "boundary_connected_within_neighbor_cutoff",
                "boundary_pair_distance_min_angstrom",
                "boundary_pair_distance_mean_angstrom",
                "boundary_pair_distance_max_angstrom",
                "boundary_neighbor_distance_min_angstrom",
                "boundary_neighbor_distance_mean_angstrom",
                "boundary_neighbor_distance_max_angstrom",
                "pair_scope",
                "atom1_id",
                "element1",
                "atom2_id",
                "element2",
                "semiconductor_atom_id",
                "semiconductor_element",
                "oxide_atom_id",
                "oxide_element",
                "pair_type",
                "distance_angstrom",
                "neighbor_threshold_angstrom",
                "distance_to_threshold_fraction",
                "within_neighbor_cutoff",
                "short_contact_review",
                "image_offset_a",
                "image_offset_b",
                "image_offset_c",
                "oxide_atom_layer_index",
                "global_neighbor_count",
                "oxide_internal_neighbor_count",
                "oxide_internal_unique_neighbor_count",
                "oxide_internal_neighbor_ids",
                "oxide_internal_neighbor_elements",
                "semiconductor_boundary_neighbor_count",
                "semiconductor_boundary_neighbor_ids",
                "oxygen_cation_neighbor_count",
                "cation_oxygen_neighbor_count",
                "nearest_relevant_neighbor_distance_angstrom",
                "isolated_from_oxide_and_semiconductor_boundary",
                "oxide_internal_neighbor_pair_count",
                "oxide_atom_neighbor_coverage_count",
                "oxide_oxygen_atom_count",
                "oxide_oxygen_with_cation_neighbor_count",
                "oxide_cation_atom_count",
                "oxide_cations_with_oxygen_neighbor_count",
                "short_contact_review_threshold_fraction",
                "short_contact_count",
                "isolated_oxide_atom_count",
                "geometry_preflight_ready",
                "calculation_geometry_ready",
                "normality_reason_codes",
                "next_action",
                "warning_count",
                "warnings",
            ],
            oxide_interface_geometry_rows,
        )

    oxide_interface_health = semiconductor.get("oxide_interface_health_summary") or {}
    if oxide_interface_health:
        oxide_interface_rows = _semiconductor_oxide_interface_health_csv_rows(
            oxide_interface_health
        )
        files["semiconductor_oxide_interface_health_csv"] = str(
            bundle_dir / "semiconductor_oxide_interface_health.csv"
        )
        row_counts["semiconductor_oxide_interface_health"] = _write_csv(
            bundle_dir / "semiconductor_oxide_interface_health.csv",
            [
                "row_kind",
                "interface",
                "axis",
                "semiconductor_material",
                "oxide_material",
                "metal_gate_present",
                "material_sequence",
                "sequence_matches_expected",
                "status",
                "quality",
                "layer_profile_complete",
                "oxide_layer_count",
                "layer_index",
                "fractional_center",
                "axis_coordinate_angstrom",
                "material_group",
                "atom_count",
                "element_counts",
                "atom_ids",
                "oxide_cation_elements",
                "cation_count",
                "oxygen_count",
                "oxygen_to_cation_ratio",
                "expected_oxygen_per_cation_ratio",
                "expected_oxygen_count",
                "oxygen_delta_count",
                "oxygen_deficit_count",
                "oxygen_excess_count",
                "stoichiometry_status",
                "oxygen_deficit_binding_status",
                "oxygen_deficit_explained_by_recorded_vacancies",
                "recorded_oxygen_vacancy_count",
                "recorded_oxygen_vacancy_site_ids",
                "all_recorded_oxygen_vacancy_locations_verified",
                "vacancy_site_id",
                "vacancy_fractional_a",
                "vacancy_fractional_b",
                "vacancy_fractional_c",
                "vacancy_axis_coordinate_angstrom",
                "vacancy_region",
                "vacancy_nearest_layer_index",
                "vacancy_nearest_layer_material",
                "vacancy_nearest_layer_delta_angstrom",
                "vacancy_distance_to_boundary_angstrom",
                "vacancy_interface_proximal",
                "vacancy_position_verified",
                "vacancy_auto_selected_site",
                "semiconductor_oxide_boundary_angstrom",
                "geometry_preflight_status",
                "geometry_preflight_quality",
                "geometry_preflight_ready",
                "geometry_visualization_ready",
                "geometry_boundary_neighbor_pair_count",
                "geometry_interface_spacing_count",
                "geometry_interface_spacing_mismatch_count",
                "geometry_interface_spacing_declared_values_match",
                "geometry_short_contact_count",
                "geometry_isolated_oxide_atom_count",
                "pre_relaxation_scaffold",
                "requires_geometry_relaxation",
                "geometry_relaxed",
                "geometry_relaxation_verified",
                "visual_preflight_ready",
                "calculation_ready",
                "normality_reason_codes",
                "calculation_blocking_reasons",
                "next_action",
                "warning_count",
                "warnings",
            ],
            oxide_interface_rows,
        )

    gate_stack = semiconductor.get("gate_stack_summary") or {}
    if gate_stack:
        gate_stack_rows = _semiconductor_gate_stack_csv_rows(gate_stack)
        files["semiconductor_gate_stack_csv"] = str(bundle_dir / "semiconductor_gate_stack.csv")
        row_counts["semiconductor_gate_stack"] = _write_csv(
            bundle_dir / "semiconductor_gate_stack.csv",
            [
                "segment_index",
                "axis",
                "interface",
                "material",
                "role",
                "expected_stack_sequence",
                "material_sequence",
                "sequence_matches_expected",
                "quality",
                "gate_material",
                "gate_oxide_material",
                "semiconductor_channel_material",
                "first_layer_index",
                "last_layer_index",
                "layer_count",
                "mixed_layer_count",
                "fractional_center_start",
                "fractional_center_end",
                "axis_center_start_angstrom",
                "axis_center_end_angstrom",
                "center_span_angstrom",
                "declared_oxide_thickness_angstrom",
                "declared_gate_thickness_angstrom",
                "declared_channel_thickness_angstrom",
                "declared_vacuum_angstrom",
                "atom_count",
                "element_counts",
                "atom_ids",
                "warnings",
            ],
            gate_stack_rows,
        )

    contact = semiconductor.get("metal_semiconductor_contact_summary") or {}
    if contact:
        contact_rows = _semiconductor_contact_csv_rows(contact)
        files["semiconductor_contact_csv"] = str(bundle_dir / "semiconductor_contact.csv")
        row_counts["semiconductor_contact"] = _write_csv(
            bundle_dir / "semiconductor_contact.csv",
            [
                "segment_index",
                "axis",
                "interface",
                "contact_type",
                "material",
                "role",
                "expected_contact_sequence",
                "material_sequence",
                "sequence_matches_expected",
                "quality",
                "metal_material",
                "semiconductor_material",
                "first_layer_index",
                "last_layer_index",
                "layer_count",
                "fractional_center_start",
                "fractional_center_end",
                "axis_center_start_angstrom",
                "axis_center_end_angstrom",
                "center_span_angstrom",
                "declared_contact_gap_angstrom",
                "actual_contact_gap_angstrom",
                "contact_gap_delta_angstrom",
                "contact_geometry_status",
                "contact_geometry_next_action",
                "declared_metal_thickness_angstrom",
                "actual_metal_thickness_angstrom",
                "metal_thickness_delta_angstrom",
                "declared_semiconductor_thickness_angstrom",
                "barrier_model",
                "metal_work_function_ev",
                "semiconductor_electron_affinity_ev",
                "semiconductor_band_gap_ev",
                "ideal_n_type_barrier_ev",
                "ideal_p_type_barrier_ev",
                "barrier_warning_count",
                "barrier_warnings",
                "atom_count",
                "element_counts",
                "atom_ids",
                "warnings",
            ],
            contact_rows,
        )

    quantum_well = semiconductor.get("quantum_well_summary") or {}
    if quantum_well:
        quantum_well_rows = _semiconductor_quantum_well_csv_rows(quantum_well)
        files["semiconductor_quantum_well_csv"] = str(bundle_dir / "semiconductor_quantum_well.csv")
        row_counts["semiconductor_quantum_well"] = _write_csv(
            bundle_dir / "semiconductor_quantum_well.csv",
            [
                "segment_index",
                "period_index",
                "segment_in_period",
                "axis",
                "material",
                "material_marker",
                "role",
                "requested_well_layer_count",
                "requested_barrier_layer_count",
                "requested_well_thickness_angstrom",
                "requested_barrier_thickness_angstrom",
                "well_thickness_error_angstrom",
                "barrier_thickness_error_angstrom",
                "first_layer_index",
                "last_layer_index",
                "layer_count",
                "marker_layer_count",
                "atom_count",
                "non_passivant_atom_count",
                "mixed_layer_count",
                "axis_start_angstrom",
                "axis_end_angstrom",
                "thickness_angstrom",
                "fractional_start",
                "fractional_end",
                "wraps_periodic_boundary",
                "element_signatures",
                "element_counts",
                "cation_counts",
                "anion_counts",
                "cation_fractions",
            ],
            quantum_well_rows,
        )

    defect_summary = semiconductor.get("defect_summary") or {}
    if defect_summary:
        defect_rows = _semiconductor_defect_csv_rows(defect_summary)
        files["semiconductor_defects_csv"] = str(bundle_dir / "semiconductor_defects.csv")
        row_counts["semiconductor_defects"] = _write_csv(
            bundle_dir / "semiconductor_defects.csv",
            [
                "defect_type",
                "site_id",
                "site_element",
                "site_family",
                "original_element",
                "new_element",
                "fractional_a",
                "fractional_b",
                "fractional_c",
                "concentration_fraction",
                "concentration_percent",
                "expected_neighbor_count",
                "nearest_neighbor_count",
                "nearest_neighbor_ids",
                "nearest_neighbor_elements",
                "interstitial_neighbor_count",
                "antisite_neighbor_count",
                "coordination_outlier",
                "same_sublattice_neighbor_count",
                "same_sublattice_neighbor_ids",
                "undercoordinated_neighbor_count",
                "undercoordinated_neighbor_ids",
                "missing_neighbor_bond_estimate",
                "role_hint",
                "carrier_type_hint",
                "auto_selected_site",
                "complex_id",
                "complex_type",
                "pair_site_id",
                "pair_distance_angstrom",
                "nearest_neighbor_verified",
                "source",
            ],
            defect_rows,
        )
        complex_rows = _semiconductor_defect_complex_csv_rows(defect_summary)
        if complex_rows:
            files["semiconductor_defect_complexes_csv"] = str(
                bundle_dir / "semiconductor_defect_complexes.csv"
            )
            row_counts["semiconductor_defect_complexes"] = _write_csv(
                bundle_dir / "semiconductor_defect_complexes.csv",
                [
                    "complex_id",
                    "complex_type",
                    "member_site_ids",
                    "member_site_elements",
                    "member_count",
                    "member_vacancy_record_count",
                    "member_dopant_record_count",
                    "substitution_site_id",
                    "substitution_host_element",
                    "substitution_element",
                    "vacancy_site_id",
                    "vacancy_site_element",
                    "pair_distance_angstrom_recorded",
                    "pair_distance_angstrom_recomputed",
                    "distance_delta_angstrom",
                    "nearest_neighbor_threshold_angstrom",
                    "nearest_neighbor_metadata_claim",
                    "nearest_neighbor_recomputed",
                    "nearest_neighbor_verified",
                    "periodic_minimum_image",
                    "image_offset",
                    "selection",
                    "selection_rule",
                    "charge_state_label",
                    "charge_state_explicit",
                    "requested_net_charge_e",
                    "reference_spin_multiplicity",
                    "reference_spin_state",
                    "backend_charge_binding_status",
                    "backend_spin_binding_status",
                    "charge_spin_backend_binding_ready",
                    "expected_castep_total_charge",
                    "observed_castep_total_charge",
                    "expected_castep_spin_treatment",
                    "observed_castep_spin_treatment",
                    "expected_castep_use_formal_spin",
                    "observed_castep_use_formal_spin",
                    "expected_castep_initial_spin",
                    "observed_castep_initial_spin",
                    "expected_castep_optimize_total_spin",
                    "observed_castep_optimize_total_spin",
                    "castep_charge_spin_all_fields_match",
                    "calculation_execution_ready",
                    "structure_hotload_allowed",
                    "state_result_computed",
                    "metadata_consistent",
                    "integrity_errors",
                    "source",
                ],
                complex_rows,
            )

    finite_size = semiconductor.get("finite_size_summary") or {}
    if finite_size:
        finite_rows = _semiconductor_finite_size_csv_rows(finite_size)
        files["semiconductor_finite_size_csv"] = str(bundle_dir / "semiconductor_finite_size.csv")
        row_counts["semiconductor_finite_size"] = _write_csv(
            bundle_dir / "semiconductor_finite_size.csv",
            [
                "non_passivant_atom_count",
                "min_lattice_length_angstrom",
                "max_isolated_fraction",
                "max_isolated_kind",
                "max_isolated_label",
                "dilute_cell_atom_threshold",
                "dilute_fraction_threshold",
                "small_cell_warning",
                "high_concentration_warning",
                "finite_size_warning",
                "warnings",
            ],
            finite_rows,
        )

    heterostructure = semiconductor.get("heterostructure_summary") or {}
    if heterostructure:
        heterostructure_rows = _semiconductor_heterostructure_csv_rows(heterostructure)
        files["semiconductor_heterostructure_csv"] = str(bundle_dir / "semiconductor_heterostructure.csv")
        row_counts["semiconductor_heterostructure"] = _write_csv(
            bundle_dir / "semiconductor_heterostructure.csv",
            [
                "interface",
                "interface_orientation",
                "interface_axis",
                "substrate",
                "coherent_strain_model",
                "in_plane_lattice_angstrom",
                "material",
                "reference_lattice_angstrom",
                "in_plane_strain_percent",
                "lattice_mismatch_to_substrate_percent",
                "is_substrate",
                "max_abs_in_plane_strain_percent",
                "max_abs_lattice_mismatch_to_substrate_percent",
                "strain_warning",
            ],
            heterostructure_rows,
        )

    substrate_epitaxy_preflight = semiconductor.get("substrate_epitaxy_preflight_summary") or {}
    if substrate_epitaxy_preflight:
        substrate_epitaxy_rows = _semiconductor_substrate_epitaxy_preflight_csv_rows(
            substrate_epitaxy_preflight
        )
        files["semiconductor_substrate_epitaxy_preflight_csv"] = str(
            bundle_dir / "semiconductor_substrate_epitaxy_preflight.csv"
        )
        row_counts["semiconductor_substrate_epitaxy_preflight"] = _write_csv(
            bundle_dir / "semiconductor_substrate_epitaxy_preflight.csv",
            [
                "available",
                "substrate_material",
                "substrate_orientation",
                "requested_target_material",
                "selected_target_material",
                "selected_target_found",
                "target_material",
                "is_requested_target",
                "film_orientation",
                "relationship",
                "in_plane_rotation_deg",
                "film_reference_lattice_angstrom",
                "direct_substrate_spacing_angstrom",
                "direct_mismatch_percent",
                "direct_mismatch_warning",
                "domain_film_repeats",
                "domain_substrate_repeats",
                "domain_film_period_angstrom",
                "domain_substrate_period_angstrom",
                "domain_mismatch_percent",
                "domain_mismatch_warning",
                "domain_matching_ready",
                "buffer_layer_hint",
                "target_next_action",
                "max_abs_direct_mismatch_percent",
                "max_abs_domain_mismatch_percent",
                "warning_count",
                "warnings",
                "next_action",
            ],
            substrate_epitaxy_rows,
        )

    strain_summary = semiconductor.get("strain_summary") or {}
    if strain_summary:
        strain_rows = _semiconductor_strain_csv_rows(strain_summary)
        files["semiconductor_strain_csv"] = str(bundle_dir / "semiconductor_strain.csv")
        row_counts["semiconductor_strain"] = _write_csv(
            bundle_dir / "semiconductor_strain.csv",
            [
                "index",
                "mode",
                "axes",
                "percent",
                "scale_factor",
                "reference_a",
                "reference_b",
                "reference_c",
                "strained_a",
                "strained_b",
                "strained_c",
                "source",
                "max_abs_strain_percent",
                "strain_warning",
            ],
            strain_rows,
        )

    surface_model = semiconductor.get("surface_model_summary") or {}
    if surface_model:
        surface_model_rows = _semiconductor_surface_model_csv_rows(surface_model)
        files["semiconductor_surface_model_csv"] = str(bundle_dir / "semiconductor_surface_model.csv")
        row_counts["semiconductor_surface_model"] = _write_csv(
            bundle_dir / "semiconductor_surface_model.csv",
            [
                "available",
                "status",
                "ready_for_calculation_preflight",
                "next_action",
                "slab_vacuum_status",
                "surface_preparation_status",
                "surface_polarity_status",
                "surface_orientation_status",
                "surface_orientation_basis",
                "surface_plane_label",
                "surface_plane_indices",
                "surface_axis",
                "surface_axis_cartesian",
                "mapped_surface_normal_cell_axis",
                "mapping_axis_matches_surface_axis",
                "alignment_applicable",
                "plane_normal_cartesian",
                "plane_spacing_angstrom",
                "axis_plane_alignment_angle_degrees",
                "axis_plane_alignment_ok",
                "orientation_validation_level",
                "orientation_next_action",
                "blocking_reasons",
                "review_reasons",
            ],
            surface_model_rows,
        )

    surface_termination = semiconductor.get("surface_termination_summary") or {}
    if surface_termination:
        surface_rows = _semiconductor_surface_termination_csv_rows(surface_termination)
        files["semiconductor_surface_termination_csv"] = str(bundle_dir / "semiconductor_surface_termination.csv")
        row_counts["semiconductor_surface_termination"] = _write_csv(
            bundle_dir / "semiconductor_surface_termination.csv",
            [
                "surface",
                "surface_orientation",
                "surface_axis",
                "termination",
                "atom_id",
                "element",
                "neighbor_count",
                "expected_coordination",
                "dangling_bond_estimate",
                "passivant_neighbor_count",
                "neighbor_ids",
                "neighbor_elements",
                "surface_dangling_bond_estimate",
                "surface_passivant_bond_count",
                "surface_passivation_coverage_fraction",
                "total_dangling_bond_estimate",
                "total_passivant_bond_count",
                "total_passivation_coverage_fraction",
                "fully_passivated",
                "surface_preparation_status",
                "surface_preparation_next_action",
            ],
            surface_rows,
        )

    surface_polarity = semiconductor.get("surface_polarity_summary") or {}
    if surface_polarity:
        polarity_rows = _semiconductor_surface_polarity_csv_rows(surface_polarity)
        files["semiconductor_surface_polarity_csv"] = str(bundle_dir / "semiconductor_surface_polarity.csv")
        row_counts["semiconductor_surface_polarity"] = _write_csv(
            bundle_dir / "semiconductor_surface_polarity.csv",
            [
                "surface_orientation",
                "surface_axis",
                "termination",
                "bottom_formula",
                "top_formula",
                "bottom_atom_count",
                "top_atom_count",
                "bottom_dangling_bond_estimate",
                "top_dangling_bond_estimate",
                "bottom_passivant_bond_count",
                "top_passivant_bond_count",
                "same_element_counts",
                "passivation_symmetric",
                "polar_surface_hint",
                "surface_asymmetry_observed",
                "surface_asymmetry_expected",
                "surface_asymmetry_expected_reason",
                "surface_asymmetry_warning",
                "surface_polarity_status",
                "surface_polarity_next_action",
                "warnings",
            ],
            polarity_rows,
        )

    view_summaries = [_view_summary_csv_row(view) for view in audit.get("views", []) or []]
    files["view_summary_csv"] = str(bundle_dir / "view_summary.csv")
    row_counts["view_summary"] = _write_csv(
        bundle_dir / "view_summary.csv",
        [
            "view",
            "supported",
            "coordinate_system",
            "crystal_direction_indices",
            "crystal_direction_label",
            "crystal_direction_cartesian",
            "crystal_direction_view_onto_plane_mapping",
            "crystal_plane_indices",
            "crystal_plane_label",
            "crystal_plane_normal_cartesian",
            "crystal_plane_reciprocal_vector_per_angstrom",
            "crystal_plane_reciprocal_convention",
            "crystal_plane_spacing_angstrom",
            "oriented_frame_kind",
            "oriented_frame_role",
            "oriented_frame_axis",
            "oriented_frame_source_metadata_field",
            "oriented_frame_reference_cell_axis",
            "oriented_frame_axis_cartesian",
            "oriented_frame_direction_cartesian",
            "oriented_frame_in_plane_1_cartesian",
            "oriented_frame_in_plane_2_cartesian",
            "camera_direction",
            "camera_up",
            "camera_right",
            "camera_position",
            "look_at_direction",
            "target",
            "camera_distance_angstrom",
            "orthographic_width_angstrom",
            "orthographic_height_angstrom",
            "near_clip_angstrom",
            "far_clip_angstrom",
            "bbox_x_min",
            "bbox_x_max",
            "bbox_y_min",
            "bbox_y_max",
            "bbox_depth_min",
            "bbox_depth_max",
            "span_x_angstrom",
            "span_y_angstrom",
            "span_depth_angstrom",
            "atom_projection_count",
            "overlap_candidate_count",
            "health_ok",
            "warnings",
        ],
        view_summaries,
    )

    projections = []
    overlaps = []
    for view in audit.get("views", []) or []:
        view_name = view.get("name")
        for projection in view.get("atom_projections", []) or []:
            projections.append({"view": view_name, **projection})
        for overlap in view.get("overlap_candidates", []) or []:
            overlaps.append({"view": view_name, **overlap})
    files["view_projections_csv"] = str(bundle_dir / "view_projections.csv")
    row_counts["view_projections"] = _write_csv(bundle_dir / "view_projections.csv", ["view", "atom_id", "element", "x", "y", "depth"], projections)
    files["view_overlaps_csv"] = str(bundle_dir / "view_overlaps.csv")
    row_counts["view_overlaps"] = _write_csv(bundle_dir / "view_overlaps.csv", ["view", "atom1", "atom2", "distance_2d_angstrom", "depth_delta_angstrom"], overlaps)
    view_quality = _view_quality_csv_rows(audit)
    files["view_quality_csv"] = str(bundle_dir / "view_quality.csv")
    row_counts["view_quality"] = _write_csv(
        bundle_dir / "view_quality.csv",
        [
            "view",
            "supported",
            "recommended_rank",
            "clean_for_visual_review",
            "nonblocking_visual_note",
            "calculation_risk",
            "recommendation",
            "atom_projection_count",
            "projection_count_matches_model",
            "overlap_candidate_count",
            "warning_count",
            "nearly_degenerate",
            "atom_projections_truncated",
            "span_area_angstrom2",
            "span_x_angstrom",
            "span_y_angstrom",
            "span_depth_angstrom",
            "camera_direction",
            "camera_up",
            "camera_position",
            "warnings",
        ],
        view_quality,
    )

    view_reference = _write_view_reference_artifacts(bundle_dir, spec, audit)
    files.update(view_reference["files"])
    row_counts["view_reference_views"] = view_reference["view_count"]

    health_summary_path = bundle_dir / "health_summary.json"
    health_summary_path.write_text(
        json.dumps(
            {
                "project_id": spec.project_id,
                "revision": spec.revision,
                "modeling_health": modeling_health,
                "audit_health": health,
                "geometry": audit.get("geometry"),
                "model": audit.get("model"),
                "structure_artifact_validation": audit.get("structure_artifact_validation"),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    files["health_summary_json"] = str(health_summary_path)

    health_summary_rows = [_modeling_health_summary_csv_row(spec, audit, modeling_health)]
    files["modeling_health_summary_csv"] = str(bundle_dir / "modeling_health_summary.csv")
    row_counts["modeling_health_summary"] = _write_csv(
        bundle_dir / "modeling_health_summary.csv",
        [
            "project_id",
            "revision",
            "model_type",
            "spec_fingerprint",
            "verdict",
            "ok",
            "execution_mode",
            "error_count",
            "warning_count",
            "verdict_warning_count",
            "next_action",
            "model_health_ok",
            "audit_health_ok",
            "atom_count",
            "bond_count",
            "element_count",
            "elements",
            "view_count",
            "view_warning_count",
            "close_contact_count",
            "planned_structure_exists",
            "structure_artifact_validation_status",
            "structure_artifact_validation_ok",
            "structure_artifact_validation_required",
            "structure_artifact_sha256",
            "structure_artifact_atom_count_matches",
            "structure_artifact_element_counts_match",
            "structure_artifact_atom_ids_match",
            "structure_artifact_atom_elements_match",
            "structure_artifact_fractional_coordinates_match",
            "structure_artifact_lattice_matches",
            "runner_success",
            "gui_opened",
            "gui_open_identity_verification",
            "gui_open_identity_uses_project_wrapper",
            "gui_open_identity_project_wrapper_matches_structure",
            "gui_window_identity_verification",
            "gui_selected_window_identity_verification",
            "gui_foreground_window_identity_verification",
            "snapshot_readable",
            "snapshot_likely_nonblank",
            "snapshot_unique_sampled_colors",
            "semiconductor_health_available",
            "semiconductor_rule",
            "semiconductor_formula",
            "semiconductor_reduced_formula",
            "semiconductor_total_atom_count",
            "semiconductor_unexpected_neighbor_pair_count",
            "semiconductor_alloy_same_sublattice_neighbor_pair_count",
            "semiconductor_same_sublattice_cutoff_artifact_pair_count",
            "semiconductor_coordination_excluded_neighbor_pair_count",
            "semiconductor_coordination_excluded_pair_types",
            "semiconductor_coordination_outlier_count",
            "semiconductor_interface_quality",
            "semiconductor_interface_period_sequence_complete",
            "semiconductor_oxide_interface_geometry_status",
            "semiconductor_oxide_interface_geometry_quality",
            "semiconductor_oxide_interface_geometry_atom_binding_complete",
            "semiconductor_oxide_interface_boundary_neighbor_pair_count",
            "semiconductor_oxide_interface_boundary_connected",
            "semiconductor_oxide_interface_spacing_count",
            "semiconductor_oxide_interface_spacing_mismatch_count",
            "semiconductor_oxide_interface_spacing_declared_values_match",
            "semiconductor_oxide_interface_short_contact_count",
            "semiconductor_oxide_interface_isolated_oxide_atom_count",
            "semiconductor_oxide_interface_geometry_preflight_ready",
            "semiconductor_oxide_interface_calculation_geometry_ready",
            "semiconductor_oxide_interface_status",
            "semiconductor_oxide_interface_stoichiometry_status",
            "semiconductor_oxide_interface_oxygen_deficit_count",
            "semiconductor_oxide_interface_recorded_oxygen_vacancy_count",
            "semiconductor_oxide_interface_calculation_ready",
            "semiconductor_gate_stack_quality",
            "semiconductor_gate_stack_sequence_matches_expected",
            "semiconductor_quantum_well_period_count",
            "semiconductor_quantum_well_barrier_materials",
            "semiconductor_surface_dangling_bond_estimate",
            "semiconductor_surface_fully_passivated",
            "semiconductor_finite_size_warning",
            "semiconductor_defect_complex_count",
            "semiconductor_divacancy_count",
            "semiconductor_defect_complex_integrity_ok",
            "semiconductor_total_dopant_density_cm3",
            "semiconductor_net_nominal_carrier_density_cm3_abs",
            "semiconductor_dopant_concentration_warning_level",
            "semiconductor_degenerate_doping_review_required",
            "semiconductor_calculation_status",
            "semiconductor_castep_electronic_assessment_status",
            "semiconductor_castep_electronic_assessment_trust_status",
            "semiconductor_castep_electronic_artifact_evidence_verified",
            "semiconductor_castep_electronic_calculation_result_review_required",
            "semiconductor_castep_electronic_structure_normality_blocked",
            "semiconductor_castep_electronic_result_review_reasons",
            "semiconductor_castep_convergence_status",
            "semiconductor_castep_convergence_verified_point_count",
            "semiconductor_castep_convergence_rejected_point_count",
            "semiconductor_castep_convergence_series_count",
            "semiconductor_castep_convergence_artifact_evidence_verified",
            "semiconductor_castep_parameter_sensitivity_evidence_verified",
            "semiconductor_castep_parameter_sensitivity_within_tolerance",
            "semiconductor_castep_scientific_convergence_verified",
            "semiconductor_castep_convergence_structure_normality_blocked",
            "semiconductor_castep_convergence_review_reasons",
            "semiconductor_2d_electrostatic_status",
            "semiconductor_2d_electrostatic_quality",
            "semiconductor_2d_expected_asymmetry_verified",
            "semiconductor_2d_vacuum_geometry_verified",
            "semiconductor_2d_structure_binding_verified",
            "semiconductor_2d_model_geometry_verified",
            "semiconductor_2d_model_geometry_normality_blocker",
            "semiconductor_2d_charge_density_available",
            "semiconductor_2d_dipole_moment_calculated",
            "semiconductor_2d_dipole_correction_api_verified",
            "semiconductor_2d_dipole_correction_api_contract",
            "semiconductor_2d_dipole_correction_api_property",
            "semiconductor_2d_dipole_correction_mode",
            "semiconductor_2d_dipole_correction_enabled",
            "semiconductor_2d_dipole_correction_task_compatible",
            "semiconductor_2d_dipole_correction_vacuum_requirement_met",
            "semiconductor_2d_dipole_correction_setting_verified",
            "semiconductor_2d_geometry_relaxation_required",
            "semiconductor_2d_calculation_review_required",
            "semiconductor_2d_quantitative_electrostatic_calculation_ready",
            "errors",
            "warnings",
        ],
        health_summary_rows,
    )

    manifest = {
        "schema_version": VIEW_BUNDLE_SCHEMA_VERSION,
        "contract_version": DIAGNOSTIC_EXPORT_CONTRACT_VERSION,
        "project_id": spec.project_id,
        "revision": spec.revision,
        "model_type": spec.model_type.value,
        "spec_fingerprint": audit.get("spec_fingerprint"),
        "bundle_dir": str(bundle_dir),
        "report_path": str(report_path),
        "files": files,
        "row_counts": row_counts,
        "modeling_health": modeling_health,
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}


def _write_view_reference_artifacts(
    bundle_dir: Path,
    spec: ModelSpec,
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Write a deterministic, review-only visual atlas for the selected views.

    The atlas is derived from the same projection rows as the CSV export. It is
    intentionally separate from GUI evidence: a spec projection cannot prove
    what Materials Studio currently renders.
    """

    requested_views = list(audit.get("views", []) or [])
    rendered_views = requested_views[:MAX_VIEW_REFERENCE_PANELS]
    atlas_bytes, view_entries, render_profile = _render_view_reference_atlas(
        spec,
        audit,
        rendered_views,
    )
    atlas_path = bundle_dir / "view_reference_atlas.svg"
    atlas_path.write_bytes(atlas_bytes)
    atlas_sha256 = hashlib.sha256(atlas_bytes).hexdigest()

    index_rows = []
    for entry in view_entries:
        index_rows.append(
            {
                "view": entry.get("view"),
                "supported": entry.get("supported"),
                "panel_index": entry.get("panel_index"),
                "atom_projection_count": entry.get("atom_projection_count"),
                "rendered_atom_count": entry.get("rendered_atom_count"),
                "projection_complete": entry.get("projection_complete"),
                "projection_truncated": entry.get("projection_truncated"),
                "camera_direction": _join_vector(entry.get("camera_direction")),
                "camera_up": _join_vector(entry.get("camera_up")),
                "span_x_angstrom": (entry.get("projection_span_angstrom") or {}).get("x"),
                "span_y_angstrom": (entry.get("projection_span_angstrom") or {}).get("y"),
                "span_depth_angstrom": (entry.get("projection_span_angstrom") or {}).get("depth"),
                "overlap_candidate_count": entry.get("overlap_candidate_count"),
                "health_ok": entry.get("health_ok"),
                "warning": entry.get("warning"),
                "counts_as_visual_confirmation": False,
                "atlas_sha256": atlas_sha256,
            }
        )
    index_path = bundle_dir / "view_reference_index.csv"
    _write_csv(
        index_path,
        [
            "view",
            "supported",
            "panel_index",
            "atom_projection_count",
            "rendered_atom_count",
            "projection_complete",
            "projection_truncated",
            "camera_direction",
            "camera_up",
            "span_x_angstrom",
            "span_y_angstrom",
            "span_depth_angstrom",
            "overlap_candidate_count",
            "health_ok",
            "warning",
            "counts_as_visual_confirmation",
            "atlas_sha256",
        ],
        index_rows,
    )

    supported_entries = [entry for entry in view_entries if entry.get("supported")]
    reference_limitations: list[str] = []
    if any(entry.get("projection_truncated") for entry in supported_entries):
        reference_limitations.append(
            "one_or_more_supported_views_use_truncated_atom_projection_data"
        )
    if len(rendered_views) < len(requested_views):
        reference_limitations.append("view_reference_panel_limit_exceeded")
    manifest = {
        "schema_version": VIEW_REFERENCE_SCHEMA_VERSION,
        "artifact_role": "deterministic_spec_projection_reference",
        "project_id": spec.project_id,
        "revision": spec.revision,
        "model_type": spec.model_type.value,
        "spec_fingerprint": audit.get("spec_fingerprint"),
        "atlas_path": str(atlas_path),
        "atlas_sha256": atlas_sha256,
        "atlas_size_bytes": len(atlas_bytes),
        "view_count": len(requested_views),
        "rendered_view_count": len(rendered_views),
        "supported_view_count": sum(1 for entry in view_entries if entry.get("supported")),
        "unsupported_view_count": sum(1 for entry in view_entries if not entry.get("supported")),
        "atlas_truncated": len(rendered_views) < len(requested_views),
        "all_requested_views_rendered": len(rendered_views) == len(requested_views),
        "all_supported_projections_complete": bool(supported_entries)
        and all(entry.get("projection_complete") is True for entry in supported_entries),
        "reference_limitations": reference_limitations,
        "gui_evidence_policy": {
            "counts_as_gui_screenshot": False,
            "counts_as_visual_confirmation": False,
            "requires_fresh_materials_studio_screenshot_for_acceptance": True,
        },
        "render_profile": render_profile,
        "views": [
            {
                **entry,
                "atlas_path": str(atlas_path),
                "atlas_sha256": atlas_sha256,
            }
            for entry in view_entries
        ],
    }
    manifest_path = bundle_dir / "view_reference_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "files": {
            "view_reference_atlas_svg": str(atlas_path),
            "view_reference_manifest_json": str(manifest_path),
            "view_reference_index_csv": str(index_path),
        },
        "view_count": len(requested_views),
        "manifest": manifest,
    }


def _render_view_reference_atlas(
    spec: ModelSpec,
    audit: dict[str, Any],
    views: list[dict[str, Any]],
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    """Render selected projection rows into a deterministic SVG contact sheet."""

    panel_count = max(len(views), 1)
    columns = min(VIEW_REFERENCE_COLUMNS, panel_count)
    rows = int(math.ceil(panel_count / columns))
    width = VIEW_REFERENCE_PANEL_WIDTH * columns
    height = VIEW_REFERENCE_HEADER_HEIGHT + VIEW_REFERENCE_PANEL_HEIGHT * rows

    root = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "version": "1.1",
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
            "role": "img",
            "data-artifact-role": "deterministic_spec_projection_reference",
            "data-project-id": str(spec.project_id),
            "data-revision": str(spec.revision),
            "data-spec-fingerprint": str(audit.get("spec_fingerprint") or ""),
        },
    )
    ET.SubElement(root, "title").text = (
        f"Materials Studio spec projection reference, {spec.project_id}, revision {spec.revision}"
    )
    ET.SubElement(root, "desc").text = (
        "Deterministic atom projections derived from the stored model specification; "
        "not GUI evidence and not a Materials Studio screenshot."
    )
    ET.SubElement(root, "style").text = (
        ".panel{fill:#151515;stroke:#68717a;stroke-width:1}"
        ".plot{fill:#050505;stroke:#87919a;stroke-width:1}"
        ".axis{stroke:#46515b;stroke-width:1;stroke-dasharray:3 3}"
        ".label{fill:#f0f3f5;font-family:Arial,sans-serif;font-size:14px}"
        ".small{fill:#b9c1c8;font-family:Arial,sans-serif;font-size:10px}"
        ".warning{fill:#f1bb5b;font-family:Arial,sans-serif;font-size:10px}"
    )
    ET.SubElement(root, "rect", {"x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#0b0b0b"})
    ET.SubElement(
        root,
        "text",
        {"x": "18", "y": "26", "class": "label"},
    ).text = "Spec projection reference atlas"
    ET.SubElement(
        root,
        "text",
        {"x": "18", "y": "48", "class": "small"},
    ).text = (
        f"{spec.project_id} | revision {spec.revision} | "
        "deterministic reference only; not GUI evidence"
    )

    entries: list[dict[str, Any]] = []
    for panel_index, view in enumerate(views):
        panel_column = panel_index % columns
        panel_row = panel_index // columns
        panel_x = panel_column * VIEW_REFERENCE_PANEL_WIDTH + 10
        panel_y = VIEW_REFERENCE_HEADER_HEIGHT + panel_row * VIEW_REFERENCE_PANEL_HEIGHT + 8
        entry = _render_view_reference_panel(
            root,
            view,
            panel_index=panel_index,
            panel_x=panel_x,
            panel_y=panel_y,
        )
        entries.append(entry)

    if not views:
        ET.SubElement(
            root,
            "text",
            {
                "x": str(width // 2),
                "y": str(VIEW_REFERENCE_HEADER_HEIGHT + 80),
                "text-anchor": "middle",
                "class": "warning",
            },
        ).text = "No selected view projections"

    return (
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        entries,
        {
            "canvas_width_px": width,
            "canvas_height_px": height,
            "panel_width_px": VIEW_REFERENCE_PANEL_WIDTH,
            "panel_height_px": VIEW_REFERENCE_PANEL_HEIGHT,
            "columns": columns,
            "rows": rows,
            "max_panels": MAX_VIEW_REFERENCE_PANELS,
            "projection_source": "view_audit.atom_projections",
            "projection_units": "angstrom_relative_to_target",
        },
    )


def _render_view_reference_panel(
    root: ET.Element,
    view: dict[str, Any],
    *,
    panel_index: int,
    panel_x: int,
    panel_y: int,
) -> dict[str, Any]:
    """Render one view panel and return its review metadata."""

    view_name = str(view.get("name") or f"view_{panel_index + 1}")
    supported = bool(view.get("supported"))
    projections = [
        item for item in (view.get("atom_projections") or []) if isinstance(item, dict)
    ]
    projections = sorted(
        projections,
        key=lambda item: (
            _float_or_zero(item.get("depth")),
            str(item.get("atom_id") or ""),
        ),
    )
    projection_count = _safe_nonnegative_int(view.get("atom_projection_count"), len(projections))
    projection_truncated = bool(view.get("atom_projections_truncated")) or (
        projection_count > len(projections)
    )
    projection_complete = bool(supported and not projection_truncated and projection_count == len(projections))
    health = view.get("health") if isinstance(view.get("health"), dict) else {}
    warnings = [str(item) for item in health.get("warnings", []) or [] if item]
    warning = str(view.get("warning") or (warnings[0] if warnings else "")) or None
    overlap_candidate_count = len(view.get("overlap_candidates") or [])

    panel_width = VIEW_REFERENCE_PANEL_WIDTH - 20
    panel_height = VIEW_REFERENCE_PANEL_HEIGHT - 16
    ET.SubElement(
        root,
        "rect",
        {
            "x": str(panel_x),
            "y": str(panel_y),
            "width": str(panel_width),
            "height": str(panel_height),
            "rx": "3",
            "class": "panel",
            "data-view-name": view_name,
            "data-view-index": str(panel_index),
        },
    )
    ET.SubElement(
        root,
        "text",
        {
            "x": str(panel_x + 12),
            "y": str(panel_y + 22),
            "class": "label",
            "data-view-label": view_name,
        },
    ).text = _view_reference_short_label(view_name, 43)

    camera_direction = view.get("camera_direction")
    camera_text = "cam=" + _join_vector(camera_direction) if camera_direction else "cam=unknown"
    ET.SubElement(
        root,
        "text",
        {"x": str(panel_x + 12), "y": str(panel_y + 39), "class": "small"},
    ).text = camera_text

    plot_x = panel_x + 18
    plot_y = panel_y + 52
    plot_width = panel_width - 36
    plot_height = 236
    ET.SubElement(
        root,
        "rect",
        {
            "x": str(plot_x),
            "y": str(plot_y),
            "width": str(plot_width),
            "height": str(plot_height),
            "class": "plot",
            "data-view-name": view_name,
        },
    )

    transform = _view_reference_transform(projections, plot_x, plot_y, plot_width, plot_height)
    if supported and projections:
        center_x = transform["center_x_px"]
        center_y = transform["center_y_px"]
        ET.SubElement(
            root,
            "line",
            {
                "x1": _svg_number(plot_x),
                "y1": _svg_number(center_y),
                "x2": _svg_number(plot_x + plot_width),
                "y2": _svg_number(center_y),
                "class": "axis",
            },
        )
        ET.SubElement(
            root,
            "line",
            {
                "x1": _svg_number(center_x),
                "y1": _svg_number(plot_y),
                "x2": _svg_number(center_x),
                "y2": _svg_number(plot_y + plot_height),
                "class": "axis",
            },
        )
        radius = max(2.0, min(6.0, 28.0 / math.sqrt(max(len(projections), 1))))
        show_labels = len(projections) <= 48 and overlap_candidate_count == 0
        for projection in projections:
            element = str(projection.get("element") or "?")
            atom_id = str(projection.get("atom_id") or "")
            point_x, point_y = _view_reference_project_point(projection, transform)
            ET.SubElement(
                root,
                "circle",
                {
                    "cx": _svg_number(point_x),
                    "cy": _svg_number(point_y),
                    "r": _svg_number(radius),
                    "fill": VIEW_REFERENCE_ELEMENT_COLORS.get(element, "#c0c7ce"),
                    "stroke": "#ffffff",
                    "stroke-width": "0.65",
                    "data-atom-id": atom_id,
                    "data-element": element,
                    "data-depth-angstrom": _svg_number(projection.get("depth")),
                },
            )
            if show_labels:
                ET.SubElement(
                    root,
                    "text",
                    {
                        "x": _svg_number(point_x + radius + 1),
                        "y": _svg_number(point_y - radius - 1),
                        "class": "small",
                        "data-atom-label": atom_id,
                    },
                ).text = _view_reference_short_label(atom_id, 16)
    elif supported:
        ET.SubElement(
            root,
            "text",
            {
                "x": str(plot_x + plot_width // 2),
                "y": str(plot_y + plot_height // 2),
                "text-anchor": "middle",
                "class": "warning",
            },
        ).text = "No atom projection data"
    else:
        ET.SubElement(
            root,
            "text",
            {
                "x": str(plot_x + plot_width // 2),
                "y": str(plot_y + plot_height // 2 - 8),
                "text-anchor": "middle",
                "class": "warning",
            },
        ).text = "Unsupported view"
        if warning:
            ET.SubElement(
                root,
                "text",
                {
                    "x": str(plot_x + plot_width // 2),
                    "y": str(plot_y + plot_height // 2 + 12),
                    "text-anchor": "middle",
                    "class": "small",
                },
            ).text = _view_reference_short_label(warning, 51)

    element_counts = Counter(str(item.get("element") or "?") for item in projections)
    legend_x = panel_x + 14
    legend_y = panel_y + panel_height - 28
    for index, element in enumerate(sorted(element_counts)):
        if index >= 7:
            break
        offset = index * 48
        ET.SubElement(
            root,
            "circle",
            {
                "cx": str(legend_x + offset),
                "cy": str(legend_y),
                "r": "4",
                "fill": VIEW_REFERENCE_ELEMENT_COLORS.get(element, "#c0c7ce"),
                "stroke": "#ffffff",
                "stroke-width": "0.5",
                "aria-label": element,
            },
        )
        ET.SubElement(
            root,
            "text",
            {"x": str(legend_x + offset + 8), "y": str(legend_y + 3), "class": "small"},
        ).text = f"{element} {element_counts[element]}"

    status_text = "projection complete; overlap review" if overlap_candidate_count else (
        "projection complete" if projection_complete else (
        "projection truncated" if projection_truncated else "review required"
        )
    )
    ET.SubElement(
        root,
        "text",
        {
            "x": str(panel_x + panel_width - 12),
            "y": str(panel_y + panel_height - 10),
            "text-anchor": "end",
            "class": "warning"
            if (not projection_complete or overlap_candidate_count)
            else "small",
        },
    ).text = status_text

    projection_span = view.get("projection_span_angstrom") or {}
    return {
        "view": view_name,
        "supported": supported,
        "panel_index": panel_index,
        "panel_box_px": {
            "x": panel_x,
            "y": panel_y,
            "width": panel_width,
            "height": panel_height,
        },
        "atom_projection_count": projection_count,
        "rendered_atom_count": len(projections),
        "projection_complete": projection_complete,
        "projection_truncated": projection_truncated,
        "projection_bbox_angstrom": view.get("projection_bbox_angstrom"),
        "projection_span_angstrom": projection_span,
        "camera_direction": view.get("camera_direction"),
        "camera_up": view.get("camera_up"),
        "camera_right": view.get("camera_right"),
        "target": view.get("target"),
        "coordinate_system": view.get("coordinate_system"),
        "crystal_direction_label": view.get("crystal_direction_label"),
        "crystal_plane_label": view.get("crystal_plane_label"),
        "overlap_candidate_count": overlap_candidate_count,
        "labels_suppressed_for_overlap": bool(overlap_candidate_count),
        "health_ok": health.get("ok"),
        "warning": warning,
        "warnings": warnings,
        "element_counts": dict(sorted(element_counts.items())),
        "plot_transform": transform,
        "counts_as_visual_confirmation": False,
    }


def _view_reference_transform(
    projections: list[dict[str, Any]],
    plot_x: int,
    plot_y: int,
    plot_width: int,
    plot_height: int,
) -> dict[str, Any]:
    xs = [_float_or_zero(item.get("x")) for item in projections]
    ys = [_float_or_zero(item.get("y")) for item in projections]
    min_x = min(xs) if xs else -0.5
    max_x = max(xs) if xs else 0.5
    min_y = min(ys) if ys else -0.5
    max_y = max(ys) if ys else 0.5
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = min((plot_width - 24) / span_x, (plot_height - 24) / span_y)
    center_x = plot_x + plot_width / 2.0
    center_y = plot_y + plot_height / 2.0
    return {
        "min_x_angstrom": _round(min_x),
        "max_x_angstrom": _round(max_x),
        "min_y_angstrom": _round(min_y),
        "max_y_angstrom": _round(max_y),
        "scale_px_per_angstrom": _round(scale),
        "center_x_px": _round(center_x),
        "center_y_px": _round(center_y),
        "plot_x_px": plot_x,
        "plot_y_px": plot_y,
        "plot_width_px": plot_width,
        "plot_height_px": plot_height,
        "y_axis_inverted": True,
    }


def _view_reference_project_point(
    projection: dict[str, Any],
    transform: dict[str, Any],
) -> tuple[float, float]:
    scale = float(transform.get("scale_px_per_angstrom") or 1.0)
    x = float(transform.get("center_x_px") or 0.0) + (
        _float_or_zero(projection.get("x"))
        - (float(transform.get("min_x_angstrom") or 0.0) + float(transform.get("max_x_angstrom") or 0.0))
        / 2.0
    ) * scale
    y = float(transform.get("center_y_px") or 0.0) - (
        _float_or_zero(projection.get("y"))
        - (float(transform.get("min_y_angstrom") or 0.0) + float(transform.get("max_y_angstrom") or 0.0))
        / 2.0
    ) * scale
    return x, y


def _view_reference_short_label(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 1)] + "..."


def _svg_number(value: Any) -> str:
    number = _float_or_zero(value)
    return f"{number:.4f}".rstrip("0").rstrip(".") or "0"


def _float_or_zero(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _safe_nonnegative_int(value: Any, fallback: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(number, 0)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> int:
    """Write rows with stable headers and return the row count."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def _modeling_health_summary_csv_row(
    spec: ModelSpec,
    audit: dict[str, Any],
    modeling_health: dict[str, Any] | None,
) -> dict[str, Any]:
    health = audit.get("health") or {}
    model = audit.get("model") or {}
    checks = (modeling_health or {}).get("checks") or {}
    errors = (modeling_health or {}).get("errors") or []
    warnings = (modeling_health or {}).get("warnings") or []
    semiconductor = health.get("semiconductor_health") or {}
    composition = semiconductor.get("composition_summary") or {}
    surface = semiconductor.get("surface_termination_summary") or {}
    defects = semiconductor.get("defect_summary") or {}
    calculation = semiconductor.get("calculation_preflight_summary") or {}
    castep_electronic_assessment = (
        semiconductor.get("castep_electronic_result_assessment") or {}
    )
    castep_convergence = semiconductor.get("castep_convergence_audit") or {}
    return {
        "project_id": spec.project_id,
        "revision": spec.revision,
        "model_type": spec.model_type.value,
        "spec_fingerprint": audit.get("spec_fingerprint"),
        "verdict": (modeling_health or {}).get("verdict"),
        "ok": (modeling_health or {}).get("ok"),
        "execution_mode": (modeling_health or {}).get("execution_mode"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "verdict_warning_count": checks.get("verdict_warning_count"),
        "next_action": (modeling_health or {}).get("next_action"),
        "model_health_ok": checks.get("model_health_ok"),
        "audit_health_ok": health.get("ok"),
        "atom_count": model.get("atom_count"),
        "bond_count": model.get("bond_count"),
        "element_count": len(model.get("elements") or {}),
        "elements": _json_csv_value(model.get("elements")),
        "view_count": checks.get("view_count", len(audit.get("views") or [])),
        "view_warning_count": checks.get("view_warning_count"),
        "close_contact_count": len(health.get("nonbonded_close_contacts") or []),
        "planned_structure_exists": checks.get("planned_structure_exists"),
        "structure_artifact_validation_status": checks.get("structure_artifact_validation_status"),
        "structure_artifact_validation_ok": checks.get("structure_artifact_validation_ok"),
        "structure_artifact_validation_required": checks.get("structure_artifact_validation_required"),
        "structure_artifact_sha256": checks.get("structure_artifact_sha256"),
        "structure_artifact_atom_count_matches": checks.get("structure_artifact_atom_count_matches"),
        "structure_artifact_element_counts_match": checks.get("structure_artifact_element_counts_match"),
        "structure_artifact_atom_ids_match": checks.get("structure_artifact_atom_ids_match"),
        "structure_artifact_atom_elements_match": checks.get("structure_artifact_atom_elements_match"),
        "structure_artifact_fractional_coordinates_match": checks.get(
            "structure_artifact_fractional_coordinates_match"
        ),
        "structure_artifact_lattice_matches": checks.get("structure_artifact_lattice_matches"),
        "runner_success": checks.get("runner_success"),
        "gui_opened": checks.get("gui_opened"),
        "gui_open_identity_verification": checks.get("gui_open_identity_verification"),
        "gui_open_identity_uses_project_wrapper": checks.get("gui_open_identity_uses_project_wrapper"),
        "gui_open_identity_project_wrapper_matches_structure": checks.get("gui_open_identity_project_wrapper_matches_structure"),
        "gui_window_identity_verification": checks.get("gui_window_identity_verification"),
        "gui_selected_window_identity_verification": checks.get("gui_selected_window_identity_verification"),
        "gui_foreground_window_identity_verification": checks.get("gui_foreground_window_identity_verification"),
        "snapshot_readable": checks.get("snapshot_readable"),
        "snapshot_likely_nonblank": checks.get("snapshot_likely_nonblank"),
        "snapshot_unique_sampled_colors": checks.get("snapshot_unique_sampled_colors"),
        "semiconductor_health_available": checks.get("semiconductor_health_available", bool(semiconductor)),
        "semiconductor_rule": checks.get("semiconductor_rule", semiconductor.get("rule")),
        "semiconductor_formula": checks.get("semiconductor_formula", composition.get("formula")),
        "semiconductor_reduced_formula": checks.get("semiconductor_reduced_formula", composition.get("reduced_formula")),
        "semiconductor_total_atom_count": checks.get("semiconductor_total_atom_count", composition.get("total_atom_count")),
        "semiconductor_unexpected_neighbor_pair_count": checks.get(
            "semiconductor_unexpected_neighbor_pair_count",
            semiconductor.get("unexpected_neighbor_pair_count"),
        ),
        "semiconductor_alloy_same_sublattice_neighbor_pair_count": checks.get(
            "semiconductor_alloy_same_sublattice_neighbor_pair_count",
            semiconductor.get("alloy_same_sublattice_neighbor_pair_count"),
        ),
        "semiconductor_same_sublattice_cutoff_artifact_pair_count": checks.get(
            "semiconductor_same_sublattice_cutoff_artifact_pair_count",
            semiconductor.get("same_sublattice_cutoff_artifact_pair_count"),
        ),
        "semiconductor_coordination_excluded_neighbor_pair_count": checks.get(
            "semiconductor_coordination_excluded_neighbor_pair_count",
            semiconductor.get("coordination_excluded_neighbor_pair_count"),
        ),
        "semiconductor_coordination_excluded_pair_types": _json_csv_value(
            checks.get(
                "semiconductor_coordination_excluded_pair_types",
                semiconductor.get("coordination_excluded_pair_types"),
            )
        ),
        "semiconductor_coordination_outlier_count": checks.get(
            "semiconductor_coordination_outlier_count",
            semiconductor.get("coordination_outlier_count"),
        ),
        "semiconductor_interface_quality": checks.get("semiconductor_interface_quality"),
        "semiconductor_interface_period_sequence_complete": checks.get("semiconductor_interface_period_sequence_complete"),
        "semiconductor_oxide_interface_geometry_status": checks.get(
            "semiconductor_oxide_interface_geometry_status"
        ),
        "semiconductor_oxide_interface_geometry_quality": checks.get(
            "semiconductor_oxide_interface_geometry_quality"
        ),
        "semiconductor_oxide_interface_geometry_atom_binding_complete": checks.get(
            "semiconductor_oxide_interface_geometry_atom_binding_complete"
        ),
        "semiconductor_oxide_interface_boundary_neighbor_pair_count": checks.get(
            "semiconductor_oxide_interface_boundary_neighbor_pair_count"
        ),
        "semiconductor_oxide_interface_boundary_connected": checks.get(
            "semiconductor_oxide_interface_boundary_connected"
        ),
        "semiconductor_oxide_interface_spacing_count": checks.get(
            "semiconductor_oxide_interface_spacing_count"
        ),
        "semiconductor_oxide_interface_spacing_mismatch_count": checks.get(
            "semiconductor_oxide_interface_spacing_mismatch_count"
        ),
        "semiconductor_oxide_interface_spacing_declared_values_match": checks.get(
            "semiconductor_oxide_interface_spacing_declared_values_match"
        ),
        "semiconductor_oxide_interface_short_contact_count": checks.get(
            "semiconductor_oxide_interface_short_contact_count"
        ),
        "semiconductor_oxide_interface_isolated_oxide_atom_count": checks.get(
            "semiconductor_oxide_interface_isolated_oxide_atom_count"
        ),
        "semiconductor_oxide_interface_geometry_preflight_ready": checks.get(
            "semiconductor_oxide_interface_geometry_preflight_ready"
        ),
        "semiconductor_oxide_interface_calculation_geometry_ready": checks.get(
            "semiconductor_oxide_interface_calculation_geometry_ready"
        ),
        "semiconductor_oxide_interface_status": checks.get("semiconductor_oxide_interface_status"),
        "semiconductor_oxide_interface_stoichiometry_status": checks.get(
            "semiconductor_oxide_interface_stoichiometry_status"
        ),
        "semiconductor_oxide_interface_oxygen_deficit_count": checks.get(
            "semiconductor_oxide_interface_oxygen_deficit_count"
        ),
        "semiconductor_oxide_interface_recorded_oxygen_vacancy_count": checks.get(
            "semiconductor_oxide_interface_recorded_oxygen_vacancy_count"
        ),
        "semiconductor_oxide_interface_calculation_ready": checks.get(
            "semiconductor_oxide_interface_calculation_ready"
        ),
        "semiconductor_gate_stack_quality": checks.get("semiconductor_gate_stack_quality"),
        "semiconductor_gate_stack_sequence_matches_expected": checks.get("semiconductor_gate_stack_sequence_matches_expected"),
        "semiconductor_quantum_well_period_count": checks.get("semiconductor_quantum_well_period_count"),
        "semiconductor_quantum_well_barrier_materials": _json_csv_value(checks.get("semiconductor_quantum_well_barrier_materials")),
        "semiconductor_surface_dangling_bond_estimate": checks.get(
            "semiconductor_surface_dangling_bond_estimate",
            surface.get("dangling_bond_estimate"),
        ),
        "semiconductor_surface_fully_passivated": checks.get("semiconductor_surface_fully_passivated", surface.get("fully_passivated")),
        "semiconductor_finite_size_warning": checks.get("semiconductor_finite_size_warning"),
        "semiconductor_defect_complex_count": checks.get(
            "semiconductor_defect_complex_count",
            defects.get("complex_count"),
        ),
        "semiconductor_divacancy_count": checks.get(
            "semiconductor_divacancy_count",
            defects.get("divacancy_count"),
        ),
        "semiconductor_defect_complex_integrity_ok": checks.get(
            "semiconductor_defect_complex_integrity_ok",
            defects.get("defect_complex_integrity_ok"),
        ),
        "semiconductor_total_dopant_density_cm3": checks.get("semiconductor_total_dopant_density_cm3"),
        "semiconductor_net_nominal_carrier_density_cm3_abs": checks.get(
            "semiconductor_net_nominal_carrier_density_cm3_abs"
        ),
        "semiconductor_dopant_concentration_warning_level": checks.get(
            "semiconductor_dopant_concentration_warning_level"
        ),
        "semiconductor_degenerate_doping_review_required": checks.get(
            "semiconductor_degenerate_doping_review_required"
        ),
        "semiconductor_calculation_status": checks.get("semiconductor_calculation_status", calculation.get("status")),
        "semiconductor_castep_electronic_assessment_status": checks.get(
            "semiconductor_castep_electronic_assessment_status",
            castep_electronic_assessment.get("status"),
        ),
        "semiconductor_castep_electronic_assessment_trust_status": checks.get(
            "semiconductor_castep_electronic_assessment_trust_status",
            castep_electronic_assessment.get("trust_status"),
        ),
        "semiconductor_castep_electronic_artifact_evidence_verified": checks.get(
            "semiconductor_castep_electronic_artifact_evidence_verified",
            castep_electronic_assessment.get("artifact_evidence_verified"),
        ),
        "semiconductor_castep_electronic_calculation_result_review_required": checks.get(
            "semiconductor_castep_electronic_calculation_result_review_required",
            castep_electronic_assessment.get("calculation_result_review_required"),
        ),
        "semiconductor_castep_electronic_structure_normality_blocked": checks.get(
            "semiconductor_castep_electronic_structure_normality_blocked",
            castep_electronic_assessment.get("structure_normality_blocked"),
        ),
        "semiconductor_castep_electronic_result_review_reasons": _json_csv_value(
            checks.get(
                "semiconductor_castep_electronic_result_review_reasons",
                castep_electronic_assessment.get("result_review_reasons") or [],
            )
        ),
        "semiconductor_castep_convergence_status": checks.get(
            "semiconductor_castep_convergence_status",
            castep_convergence.get("status"),
        ),
        "semiconductor_castep_convergence_verified_point_count": checks.get(
            "semiconductor_castep_convergence_verified_point_count",
            castep_convergence.get("verified_point_count"),
        ),
        "semiconductor_castep_convergence_rejected_point_count": checks.get(
            "semiconductor_castep_convergence_rejected_point_count",
            castep_convergence.get("rejected_point_count"),
        ),
        "semiconductor_castep_convergence_series_count": checks.get(
            "semiconductor_castep_convergence_series_count",
            castep_convergence.get("comparable_series_count"),
        ),
        "semiconductor_castep_convergence_artifact_evidence_verified": checks.get(
            "semiconductor_castep_convergence_artifact_evidence_verified",
            castep_convergence.get("artifact_evidence_verified"),
        ),
        "semiconductor_castep_parameter_sensitivity_evidence_verified": checks.get(
            "semiconductor_castep_parameter_sensitivity_evidence_verified",
            castep_convergence.get("parameter_sensitivity_evidence_verified"),
        ),
        "semiconductor_castep_parameter_sensitivity_within_tolerance": checks.get(
            "semiconductor_castep_parameter_sensitivity_within_tolerance",
            castep_convergence.get("parameter_sensitivity_within_tolerance"),
        ),
        "semiconductor_castep_scientific_convergence_verified": checks.get(
            "semiconductor_castep_scientific_convergence_verified",
            castep_convergence.get("scientific_convergence_verified"),
        ),
        "semiconductor_castep_convergence_structure_normality_blocked": checks.get(
            "semiconductor_castep_convergence_structure_normality_blocked",
            castep_convergence.get("structure_normality_blocked"),
        ),
        "semiconductor_castep_convergence_review_reasons": _json_csv_value(
            checks.get(
                "semiconductor_castep_convergence_review_reasons",
                castep_convergence.get("result_review_reasons") or [],
            )
        ),
        "semiconductor_2d_electrostatic_status": checks.get("semiconductor_2d_electrostatic_status"),
        "semiconductor_2d_electrostatic_quality": checks.get("semiconductor_2d_electrostatic_quality"),
        "semiconductor_2d_expected_asymmetry_verified": checks.get(
            "semiconductor_2d_expected_asymmetry_verified"
        ),
        "semiconductor_2d_vacuum_geometry_verified": checks.get(
            "semiconductor_2d_vacuum_geometry_verified"
        ),
        "semiconductor_2d_structure_binding_verified": checks.get(
            "semiconductor_2d_structure_binding_verified"
        ),
        "semiconductor_2d_model_geometry_verified": checks.get(
            "semiconductor_2d_model_geometry_verified"
        ),
        "semiconductor_2d_model_geometry_normality_blocker": checks.get(
            "semiconductor_2d_model_geometry_normality_blocker"
        ),
        "semiconductor_2d_charge_density_available": checks.get(
            "semiconductor_2d_charge_density_available"
        ),
        "semiconductor_2d_dipole_moment_calculated": checks.get(
            "semiconductor_2d_dipole_moment_calculated"
        ),
        "semiconductor_2d_dipole_correction_api_verified": checks.get(
            "semiconductor_2d_dipole_correction_api_verified"
        ),
        "semiconductor_2d_dipole_correction_api_contract": checks.get(
            "semiconductor_2d_dipole_correction_api_contract"
        ),
        "semiconductor_2d_dipole_correction_api_property": checks.get(
            "semiconductor_2d_dipole_correction_api_property"
        ),
        "semiconductor_2d_dipole_correction_mode": checks.get(
            "semiconductor_2d_dipole_correction_mode"
        ),
        "semiconductor_2d_dipole_correction_enabled": checks.get(
            "semiconductor_2d_dipole_correction_enabled"
        ),
        "semiconductor_2d_dipole_correction_task_compatible": checks.get(
            "semiconductor_2d_dipole_correction_task_compatible"
        ),
        "semiconductor_2d_dipole_correction_vacuum_requirement_met": checks.get(
            "semiconductor_2d_dipole_correction_vacuum_requirement_met"
        ),
        "semiconductor_2d_dipole_correction_setting_verified": checks.get(
            "semiconductor_2d_dipole_correction_setting_verified"
        ),
        "semiconductor_2d_geometry_relaxation_required": checks.get(
            "semiconductor_2d_geometry_relaxation_required"
        ),
        "semiconductor_2d_calculation_review_required": checks.get(
            "semiconductor_2d_calculation_review_required"
        ),
        "semiconductor_2d_quantitative_electrostatic_calculation_ready": checks.get(
            "semiconductor_2d_quantitative_electrostatic_calculation_ready"
        ),
        "errors": _json_csv_value(errors),
        "warnings": _json_csv_value(warnings),
    }


def _json_csv_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _atom_csv_row(atom: dict[str, Any]) -> dict[str, Any]:
    xyz = atom.get("xyz_angstrom") or [None, None, None]
    fractional = atom.get("fractional") or [None, None, None]
    return {
        "atom_id": atom.get("id"),
        "element": atom.get("element"),
        "x_angstrom": _list_item(xyz, 0),
        "y_angstrom": _list_item(xyz, 1),
        "z_angstrom": _list_item(xyz, 2),
        "fractional_a": _list_item(fractional, 0),
        "fractional_b": _list_item(fractional, 1),
        "fractional_c": _list_item(fractional, 2),
    }


def _connectivity_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "atom_id": item.get("atom_id"),
        "element": item.get("element"),
        "degree": item.get("degree"),
        "bond_order_sum": item.get("bond_order_sum"),
        "bonded_atoms": ";".join(str(value) for value in item.get("bonded_atoms", []) or []),
    }


def _crystal_nearest_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    offset = item.get("image_offset_to_nearest") or [None, None, None]
    return {
        "atom_id": item.get("atom_id"),
        "element": item.get("element"),
        "nearest_atom_id": item.get("nearest_atom_id"),
        "nearest_element": item.get("nearest_element"),
        "distance_angstrom": item.get("distance_angstrom"),
        "image_offset_a": _list_item(offset, 0),
        "image_offset_b": _list_item(offset, 1),
        "image_offset_c": _list_item(offset, 2),
    }


def _crystal_coordination_csv_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "atom_id": item.get("atom_id"),
        "element": item.get("element"),
        "neighbor_count": item.get("neighbor_count"),
        "unique_neighbor_count": item.get("unique_neighbor_count"),
        "nearest_distance_angstrom": item.get("nearest_distance_angstrom"),
        "mean_neighbor_distance_angstrom": item.get("mean_neighbor_distance_angstrom"),
        "min_neighbor_distance_angstrom": item.get("min_neighbor_distance_angstrom"),
        "max_neighbor_distance_angstrom": item.get("max_neighbor_distance_angstrom"),
        "neighbor_ids": ";".join(str(value) for value in item.get("neighbor_ids", []) or []),
        "unique_neighbor_ids": ";".join(str(value) for value in item.get("unique_neighbor_ids", []) or []),
        "cutoff_rule": item.get("cutoff_rule"),
    }


def _semiconductor_composition_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for element in summary.get("elements", []) or []:
        rows.append(
            {
                "element": element.get("element"),
                "count": element.get("count"),
                "atomic_fraction": element.get("atomic_fraction"),
                "atomic_percent": element.get("atomic_percent"),
                "non_passivant_fraction": element.get("non_passivant_fraction"),
                "non_passivant_percent": element.get("non_passivant_percent"),
                "role": element.get("role"),
            }
        )
    return rows


def _semiconductor_charge_balance_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    expected_settings = summary.get("expected_castep_charge_spin_settings") or {}
    observed_settings = summary.get("observed_castep_charge_spin_settings") or {}
    field_matches = summary.get("castep_charge_spin_field_matches") or {}
    for element in summary.get("elements", []) or []:
        rows.append(
            {
                "element": element.get("element"),
                "count": element.get("count"),
                "role": element.get("role"),
                "nominal_valence_electrons": element.get("nominal_valence_electrons"),
                "total_valence_electrons": element.get("total_valence_electrons"),
                "valence_fraction": element.get("valence_fraction"),
                "dopant_delta_electrons": element.get("dopant_delta_electrons"),
                "defect_charge_state_label": summary.get("defect_charge_state_label"),
                "defect_charge_state_explicit": summary.get("defect_charge_state_explicit"),
                "defect_charge_state_unresolved": summary.get("defect_charge_state_unresolved"),
                "requested_net_charge_e": summary.get("requested_net_charge_e"),
                "reference_spin_multiplicity": summary.get("reference_spin_multiplicity"),
                "nominal_composition_electron_count_parity": summary.get(
                    "nominal_composition_electron_count_parity"
                ),
                "charge_adjusted_valence_electron_count": summary.get(
                    "charge_adjusted_valence_electron_count"
                ),
                "charge_adjusted_electron_count_parity": summary.get(
                    "charge_adjusted_electron_count_parity"
                ),
                "backend_charge_binding_status": summary.get("backend_charge_binding_status"),
                "backend_spin_binding_status": summary.get("backend_spin_binding_status"),
                "charge_spin_backend_binding_ready": summary.get(
                    "charge_spin_backend_binding_ready"
                ),
                "expected_castep_total_charge": expected_settings.get("total_charge"),
                "observed_castep_total_charge": observed_settings.get("total_charge"),
                "expected_castep_spin_treatment": expected_settings.get("spin_treatment"),
                "observed_castep_spin_treatment": observed_settings.get("spin_treatment"),
                "expected_castep_use_formal_spin": expected_settings.get("use_formal_spin"),
                "observed_castep_use_formal_spin": observed_settings.get("use_formal_spin"),
                "expected_castep_initial_spin": expected_settings.get("initial_spin"),
                "observed_castep_initial_spin": observed_settings.get("initial_spin"),
                "expected_castep_optimize_total_spin": expected_settings.get(
                    "optimize_total_spin"
                ),
                "observed_castep_optimize_total_spin": observed_settings.get(
                    "optimize_total_spin"
                ),
                "castep_charge_spin_all_fields_match": bool(
                    field_matches and all(field_matches.values())
                ),
                "spin_charge_review_required": summary.get("spin_charge_review_required"),
            }
        )
    return rows


def _semiconductor_calculation_preflight_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "configured": summary.get("configured"),
            "module": summary.get("module"),
            "task": summary.get("task"),
            "functional": summary.get("functional"),
            "quality": summary.get("quality"),
            "status": summary.get("status"),
            "ready_for_energy_preflight": summary.get("ready_for_energy_preflight"),
            "cutoff_energy_ev": summary.get("cutoff_energy_ev"),
            "cutoff_status": summary.get("cutoff_status"),
            "kpoint_mode": summary.get("kpoint_mode"),
            "kpoint_separation": summary.get("kpoint_separation"),
            "kpoints": _join_vector(summary.get("kpoints")),
            "dipole_correction_mode": summary.get("dipole_correction_mode"),
            "dipole_correction_configured": summary.get("dipole_correction_configured"),
            "dipole_correction_enabled": summary.get("dipole_correction_enabled"),
            "dipole_correction_api_contract": summary.get("dipole_correction_api_contract"),
            "dipole_correction_api_property": summary.get("dipole_correction_api_property"),
            "total_charge": summary.get("total_charge"),
            "spin_treatment": summary.get("spin_treatment"),
            "use_formal_spin": summary.get("use_formal_spin"),
            "initial_spin": summary.get("initial_spin"),
            "optimize_total_spin": summary.get("optimize_total_spin"),
            "charge_spin_settings_configured": summary.get(
                "charge_spin_settings_configured"
            ),
            "charge_spin_api_contract": summary.get("charge_spin_api_contract"),
            "slab_axis": summary.get("slab_axis"),
            "slab_kpoint_axis_value": summary.get("slab_kpoint_axis_value"),
            "output_file": summary.get("output_file"),
            "task_family": summary.get("task_family"),
            "task_intent": summary.get("task_intent"),
            "ready_for_requested_task_preflight": summary.get("ready_for_requested_task_preflight"),
            "changes_structure": summary.get("changes_structure"),
            "requires_prior_relaxed_structure": summary.get("requires_prior_relaxed_structure"),
            "settings_review_required": summary.get("settings_review_required"),
            "execution_risk": summary.get("execution_risk"),
            "next_action": summary.get("next_action"),
            "warning_count": summary.get("warning_count"),
            "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
        }
    ]


def _semiconductor_reciprocal_lattice_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "axis": row.get("axis"),
            "real_length_angstrom": row.get("real_length_angstrom"),
            "reciprocal_length_1_per_angstrom": row.get("reciprocal_length_1_per_angstrom"),
            "configured_kpoint": row.get("configured_kpoint"),
            "estimated_kpoint_from_separation": row.get("estimated_kpoint_from_separation"),
            "recommended_kpoint": row.get("recommended_kpoint"),
            "actual_separation_1_per_angstrom": row.get("actual_separation_1_per_angstrom"),
            "recommended_separation_1_per_angstrom": row.get(
                "recommended_separation_1_per_angstrom"
            ),
            "surface_normal_axis": row.get("surface_normal_axis"),
            "surface_normal_warning": row.get("surface_normal_warning"),
            "recommendation_reason_codes": ";".join(
                str(value) for value in summary.get("recommendation_reason_codes", []) or []
            ),
        }
        for row in summary.get("axes", []) or []
    ]


def _semiconductor_band_path_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    path = summary.get("path") or []
    if not path:
        return [
            {
                "available": summary.get("available"),
                "task_relevant": summary.get("task_relevant"),
                "structure_family": summary.get("structure_family"),
                "bravais_lattice": summary.get("bravais_lattice"),
                "path_label": summary.get("path_label"),
                "point_index": None,
                "point_label": None,
                "kx_fractional": None,
                "ky_fractional": None,
                "kz_fractional": None,
                "next_point_label": None,
                "segment_label": None,
                "requires_materials_studio_review": summary.get("requires_materials_studio_review"),
                "warning_count": summary.get("warning_count"),
                "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
            }
        ]
    return [
        {
            "available": summary.get("available"),
            "task_relevant": summary.get("task_relevant"),
            "structure_family": summary.get("structure_family"),
            "bravais_lattice": summary.get("bravais_lattice"),
            "path_label": summary.get("path_label"),
            "point_index": row.get("index"),
            "point_label": row.get("label"),
            "kx_fractional": (row.get("fractional") or [None, None, None])[0],
            "ky_fractional": (row.get("fractional") or [None, None, None])[1],
            "kz_fractional": (row.get("fractional") or [None, None, None])[2],
            "next_point_label": row.get("next_label"),
            "segment_label": row.get("segment_label"),
            "requires_materials_studio_review": summary.get("requires_materials_studio_review"),
            "warning_count": summary.get("warning_count"),
            "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
        }
        for row in path
    ]


def _semiconductor_band_alignment_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    warnings = ";".join(str(value) for value in summary.get("warnings", []) or [])
    for row in summary.get("offsets", []) or []:
        rows.append(
            {
                "interface": summary.get("interface"),
                "model": summary.get("model"),
                "reference": summary.get("reference"),
                "reference_material": summary.get("reference_material"),
                "material": row.get("material"),
                "role": row.get("role"),
                "reference_electron_affinity_ev": row.get("reference_electron_affinity_ev"),
                "reference_band_gap_ev": row.get("reference_band_gap_ev"),
                "material_electron_affinity_ev": row.get("material_electron_affinity_ev"),
                "material_band_gap_ev": row.get("material_band_gap_ev"),
                "conduction_band_offset_vs_reference_ev": row.get("conduction_band_offset_vs_reference_ev"),
                "valence_band_offset_vs_reference_ev": row.get("valence_band_offset_vs_reference_ev"),
                "electron_barrier_height_ev": row.get("electron_barrier_height_ev"),
                "hole_barrier_height_ev": row.get("hole_barrier_height_ev"),
                "band_gap_difference_vs_reference_ev": row.get("band_gap_difference_vs_reference_ev"),
                "confines_electrons": row.get("confines_electrons"),
                "confines_holes": row.get("confines_holes"),
                "alignment_type": row.get("alignment_type"),
                "quality": summary.get("quality"),
                "warning_count": summary.get("warning_count", 0),
                "warnings": warnings,
            }
        )
    if not rows:
        rows.append(
            {
                "interface": summary.get("interface"),
                "model": summary.get("model"),
                "reference": summary.get("reference"),
                "reference_material": summary.get("reference_material"),
                "quality": summary.get("quality"),
                "warning_count": summary.get("warning_count", 0),
                "warnings": warnings,
            }
        )
    return rows


def _semiconductor_polarization_2deg_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    warnings = ";".join(str(value) for value in summary.get("warnings", []) or [])
    for row in summary.get("barriers", []) or []:
        rows.append(
            {
                "interface": summary.get("interface"),
                "model": summary.get("model"),
                "reference": summary.get("reference"),
                "well_material": summary.get("well_material"),
                "barrier_material": row.get("barrier_material"),
                "barrier_al_fraction": row.get("barrier_al_fraction"),
                "barrier_in_fraction": row.get("barrier_in_fraction"),
                "in_plane_lattice_angstrom": row.get("in_plane_lattice_angstrom"),
                "barrier_reference_lattice_angstrom": row.get("barrier_reference_lattice_angstrom"),
                "barrier_in_plane_strain_percent": row.get("barrier_in_plane_strain_percent"),
                "well_total_polarization_c_per_m2": row.get("well_total_polarization_c_per_m2"),
                "barrier_spontaneous_polarization_c_per_m2": row.get("barrier_spontaneous_polarization_c_per_m2"),
                "barrier_piezoelectric_polarization_c_per_m2": row.get("barrier_piezoelectric_polarization_c_per_m2"),
                "barrier_total_polarization_c_per_m2": row.get("barrier_total_polarization_c_per_m2"),
                "polarization_discontinuity_c_per_m2": row.get("polarization_discontinuity_c_per_m2"),
                "sheet_charge_density_c_per_m2": row.get("sheet_charge_density_c_per_m2"),
                "sheet_carrier_density_cm2_abs": row.get("sheet_carrier_density_cm2_abs"),
                "electron_barrier_height_ev": row.get("electron_barrier_height_ev"),
                "two_deg_candidate": row.get("two_deg_candidate"),
                "quality": summary.get("quality"),
                "warning_count": summary.get("warning_count", 0),
                "warnings": warnings,
            }
        )
    return rows


def _semiconductor_p_gan_gate_cap_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    warnings = ";".join(str(value) for value in summary.get("warnings", []) or [])
    for layer in summary.get("layers", []) or []:
        rows.append(
            {
                "material": summary.get("material"),
                "role": summary.get("role"),
                "quality": summary.get("quality"),
                "cap_layer_index": layer.get("cap_layer_index"),
                "global_layer_index": layer.get("global_layer_index"),
                "fractional_center": layer.get("fractional_center"),
                "axis_coordinate_angstrom": layer.get("axis_coordinate_angstrom"),
                "atom_count": layer.get("atom_count"),
                "dopant_layer": layer.get("dopant_layer"),
                "dopant_atom_id": summary.get("dopant_atom_id"),
                "requested_thickness_angstrom": summary.get("requested_thickness_angstrom"),
                "actual_thickness_angstrom": summary.get("actual_thickness_angstrom"),
                "thickness_error_angstrom": summary.get("thickness_error_angstrom"),
                "layer_count": summary.get("layer_count"),
                "matched_layer_count": summary.get("matched_layer_count"),
                "dopant_site_found": summary.get("dopant_site_found"),
                "polarization_2deg_quality": summary.get("polarization_2deg_quality"),
                "polarization_2deg_barrier_materials": _join_vector(summary.get("polarization_2deg_barrier_materials")),
                "warning_count": summary.get("warning_count", 0),
                "warnings": warnings,
                "atom_ids": _join_vector(layer.get("atom_ids")),
            }
        )
    if not rows:
        rows.append(
            {
                "material": summary.get("material"),
                "role": summary.get("role"),
                "quality": summary.get("quality"),
                "cap_layer_index": None,
                "global_layer_index": None,
                "fractional_center": None,
                "axis_coordinate_angstrom": None,
                "atom_count": None,
                "dopant_layer": None,
                "dopant_atom_id": summary.get("dopant_atom_id"),
                "requested_thickness_angstrom": summary.get("requested_thickness_angstrom"),
                "actual_thickness_angstrom": summary.get("actual_thickness_angstrom"),
                "thickness_error_angstrom": summary.get("thickness_error_angstrom"),
                "layer_count": summary.get("layer_count"),
                "matched_layer_count": summary.get("matched_layer_count"),
                "dopant_site_found": summary.get("dopant_site_found"),
                "polarization_2deg_quality": summary.get("polarization_2deg_quality"),
                "polarization_2deg_barrier_materials": _join_vector(summary.get("polarization_2deg_barrier_materials")),
                "warning_count": summary.get("warning_count", 0),
                "warnings": warnings,
                "atom_ids": None,
            }
        )
    return rows


def _semiconductor_lattice_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "available": summary.get("available"),
            "a_angstrom": summary.get("a_angstrom"),
            "b_angstrom": summary.get("b_angstrom"),
            "c_angstrom": summary.get("c_angstrom"),
            "alpha_deg": summary.get("alpha_deg"),
            "beta_deg": summary.get("beta_deg"),
            "gamma_deg": summary.get("gamma_deg"),
            "cell_volume_angstrom3": summary.get("cell_volume_angstrom3"),
            "atom_count": summary.get("atom_count"),
            "non_passivant_atom_count": summary.get("non_passivant_atom_count"),
            "passivant_atom_count": summary.get("passivant_atom_count"),
            "atom_density_per_angstrom3": summary.get("atom_density_per_angstrom3"),
            "non_passivant_atom_density_per_angstrom3": summary.get("non_passivant_atom_density_per_angstrom3"),
            "volume_per_atom_angstrom3": summary.get("volume_per_atom_angstrom3"),
            "volume_per_non_passivant_atom_angstrom3": summary.get("volume_per_non_passivant_atom_angstrom3"),
            "is_slab": summary.get("is_slab"),
            "surface_axis": summary.get("surface_axis"),
            "surface_axis_length_angstrom": summary.get("surface_axis_length_angstrom"),
            "declared_vacuum_angstrom": summary.get("declared_vacuum_angstrom"),
            "declared_vacuum_fraction": summary.get("declared_vacuum_fraction"),
            "atom_extent_vacuum_angstrom": summary.get("atom_extent_vacuum_angstrom"),
            "atom_extent_vacuum_fraction": summary.get("atom_extent_vacuum_fraction"),
            "vacuum_ok": summary.get("vacuum_ok"),
            "slab_vacuum_status": summary.get("slab_vacuum_status"),
            "slab_vacuum_next_action": summary.get("slab_vacuum_next_action"),
            "centered_in_cell": summary.get("centered_in_cell"),
            "vacuum_asymmetry_abs_angstrom": summary.get("vacuum_asymmetry_abs_angstrom"),
            "metadata_cell_mismatch": summary.get("metadata_cell_mismatch"),
        }
    ]


def _semiconductor_neighbor_distance_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for pair in summary.get("pair_types", []) or []:
        distance_stats = pair.get("distance_stats_angstrom") or {}
        threshold_stats = pair.get("neighbor_threshold_stats_angstrom") or {}
        rows.append(
            {
                "pair_type": pair.get("pair_type"),
                "pair_role": pair.get("pair_role"),
                "count": distance_stats.get("count"),
                "min_distance_angstrom": distance_stats.get("min"),
                "mean_distance_angstrom": distance_stats.get("mean"),
                "max_distance_angstrom": distance_stats.get("max"),
                "distance_spread_angstrom": pair.get("distance_spread_angstrom"),
                "mean_neighbor_threshold_angstrom": threshold_stats.get("mean"),
                "max_distance_to_threshold_fraction": pair.get("max_distance_to_threshold_fraction"),
            }
        )
    return rows


def _semiconductor_local_environment_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for environment in summary.get("local_environments", []) or []:
        angle_stats = environment.get("angle_stats_deg") or {}
        rows.append(
            {
                "atom_id": environment.get("atom_id"),
                "element": environment.get("element"),
                "neighbor_count": environment.get("neighbor_count"),
                "expected_coordination": environment.get("expected_coordination"),
                "coordination_outlier": environment.get("coordination_outlier"),
                "nearest_distance_angstrom": environment.get("nearest_distance_angstrom"),
                "mean_neighbor_distance_angstrom": environment.get("mean_neighbor_distance_angstrom"),
                "min_neighbor_distance_angstrom": environment.get("min_neighbor_distance_angstrom"),
                "max_neighbor_distance_angstrom": environment.get("max_neighbor_distance_angstrom"),
                "angle_count": angle_stats.get("count"),
                "min_angle_deg": angle_stats.get("min"),
                "mean_angle_deg": angle_stats.get("mean"),
                "max_angle_deg": angle_stats.get("max"),
                "mean_tetrahedral_angle_deviation_deg": environment.get("mean_tetrahedral_angle_deviation_deg"),
                "max_tetrahedral_angle_deviation_deg": environment.get("max_tetrahedral_angle_deviation_deg"),
                "neighbor_ids": _join_vector(environment.get("neighbor_ids")),
                "neighbor_elements": _join_vector(environment.get("neighbor_elements")),
            }
        )
    return rows


def _semiconductor_sublattice_balance_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for category in summary.get("categories", []) or []:
        rows.append(
            {
                "category": category.get("category"),
                "elements": _join_vector(category.get("elements")),
                "count": category.get("count"),
                "fraction_of_non_passivant": category.get("fraction_of_non_passivant"),
                "balance_kind": summary.get("balance_kind"),
                "balance_delta_count": summary.get("balance_delta_count"),
                "balanced": summary.get("balanced"),
                "warning": summary.get("warning"),
            }
        )
    return rows


def _semiconductor_dopant_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    host_elements = _join_vector(summary.get("host_elements"))
    for dopant in summary.get("dopants", []) or []:
        coordination = dopant.get("coordination_stats") or {}
        rows.append(
            {
                "host_elements": host_elements,
                "dopant_element": dopant.get("element"),
                "count": dopant.get("count"),
                "atom_ids": _join_vector(dopant.get("atom_ids")),
                "concentration_fraction": dopant.get("concentration_fraction"),
                "concentration_percent": dopant.get("concentration_percent"),
                "role_hint": dopant.get("role_hint"),
                "coordination_min": coordination.get("min"),
                "coordination_max": coordination.get("max"),
                "coordination_mean": coordination.get("mean"),
                "coordination_count": coordination.get("count"),
                "coordination_outlier_count": dopant.get("coordination_outlier_count"),
                "neighbor_element_counts": json.dumps(dopant.get("neighbor_element_counts") or {}, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def _semiconductor_dopant_concentration_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {
            "row_type": "summary",
            "dopant_element": None,
            "count": summary.get("total_dopant_count"),
            "atom_ids": None,
            "concentration_fraction": summary.get("total_dopant_fraction"),
            "concentration_percent": summary.get("total_dopant_percent"),
            "density_cm3": summary.get("total_dopant_density_cm3"),
            "density_log10_cm3": summary.get("total_dopant_density_log10_cm3"),
            "carrier_density_cm3_signed": summary.get("net_nominal_carrier_density_cm3_signed"),
            "carrier_density_cm3_abs": summary.get("net_nominal_carrier_density_cm3_abs"),
            "carrier_type_hint": summary.get("carrier_type_hint"),
            "role_hint": None,
            "warning_level": summary.get("concentration_warning_level"),
            "high_concentration_warning": summary.get("high_concentration_warning"),
            "degenerate_doping_review_required": summary.get("degenerate_doping_review_required"),
            "cell_volume_angstrom3": summary.get("cell_volume_angstrom3"),
            "cell_volume_cm3": summary.get("cell_volume_cm3"),
            "assumption": summary.get("assumption"),
            "next_action": summary.get("next_action"),
        }
    ]
    for dopant in summary.get("dopants", []) or []:
        rows.append(
            {
                "row_type": "dopant",
                "dopant_element": dopant.get("element"),
                "count": dopant.get("count"),
                "atom_ids": _join_vector(dopant.get("atom_ids")),
                "concentration_fraction": dopant.get("concentration_fraction"),
                "concentration_percent": dopant.get("concentration_percent"),
                "density_cm3": dopant.get("density_cm3"),
                "density_log10_cm3": dopant.get("density_log10_cm3"),
                "carrier_density_cm3_signed": dopant.get("carrier_density_cm3_signed"),
                "carrier_density_cm3_abs": dopant.get("carrier_density_cm3_abs"),
                "carrier_type_hint": dopant.get("carrier_type_hint"),
                "role_hint": dopant.get("role_hint"),
                "warning_level": dopant.get("concentration_warning_level"),
                "high_concentration_warning": dopant.get("high_concentration_warning"),
                "degenerate_doping_review_required": dopant.get("degenerate_doping_review_required"),
                "cell_volume_angstrom3": summary.get("cell_volume_angstrom3"),
                "cell_volume_cm3": summary.get("cell_volume_cm3"),
                "assumption": summary.get("assumption"),
                "next_action": dopant.get("next_action") or summary.get("next_action"),
            }
        )
    return rows


def _semiconductor_dopant_site_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    entries = [
        *[dict(item) for item in summary.get("entries", []) or [] if isinstance(item, dict)],
        *[dict(item) for item in summary.get("stale_entries", []) or [] if isinstance(item, dict)],
    ]
    for index, entry in enumerate(entries, start=1):
        fractional = entry.get("fractional") or [None, None, None]
        rows.append(
            {
                "index": index,
                "site_id": entry.get("site_id"),
                "site_element": entry.get("site_element"),
                "dopant_element": entry.get("dopant_element"),
                "actual_element": entry.get("actual_element"),
                "record_status": entry.get("record_status"),
                "consistency_error": entry.get("consistency_error"),
                "site_family": entry.get("site_family"),
                "site_valence_electrons": entry.get("site_valence_electrons"),
                "dopant_valence_electrons": entry.get("dopant_valence_electrons"),
                "nominal_delta_electrons": entry.get("nominal_delta_electrons"),
                "role_hint": entry.get("role_hint"),
                "carrier_type_hint": entry.get("carrier_type_hint"),
                "fractional_a": _list_item(fractional, 0),
                "fractional_b": _list_item(fractional, 1),
                "fractional_c": _list_item(fractional, 2),
                "auto_selected_site": entry.get("auto_selected_site"),
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_carrier_intent_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, entry in enumerate(summary.get("entries", []) or [], start=1):
        rows.append(
            {
                "index": index,
                "requested_carrier_type": entry.get("requested_carrier_type"),
                "requested_carrier_mechanism": entry.get("requested_carrier_mechanism"),
                "requested_dopant_element": entry.get("requested_dopant_element"),
                "requested_defect_type": entry.get("requested_defect_type"),
                "requested_site_element": entry.get("requested_site_element"),
                "requested_site_id": entry.get("requested_site_id"),
                "requested_fraction": entry.get("requested_fraction"),
                "requested_percent": entry.get("requested_percent"),
                "actual_carrier_type": entry.get("actual_carrier_type"),
                "actual_carrier_type_hint": entry.get("actual_carrier_type_hint"),
                "actual_dopant_present": entry.get("actual_dopant_present"),
                "actual_dopant_fraction": entry.get("actual_dopant_fraction"),
                "actual_dopant_percent": entry.get("actual_dopant_percent"),
                "actual_defect_present": entry.get("actual_defect_present"),
                "actual_defect_count": entry.get("actual_defect_count"),
                "matches": entry.get("matches"),
                "source": entry.get("source"),
                "warning": entry.get("warning"),
            }
        )
    return rows


def _semiconductor_junction_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, entry in enumerate(summary.get("entries", []) or [], start=1):
        p_region = entry.get("p_region") if isinstance(entry.get("p_region"), dict) else {}
        n_region = entry.get("n_region") if isinstance(entry.get("n_region"), dict) else {}
        rows.append(
            {
                "index": index,
                "junction_type": entry.get("junction_type"),
                "host_element": entry.get("host_element"),
                "axis": entry.get("axis"),
                "p_carrier_type": p_region.get("carrier_type"),
                "p_dopant_element": p_region.get("dopant_element"),
                "p_site_ids": _join_vector(p_region.get("site_ids")),
                "p_fractional_range": _join_vector(p_region.get("fractional_range")),
                "n_carrier_type": n_region.get("carrier_type"),
                "n_dopant_element": n_region.get("dopant_element"),
                "n_site_ids": _join_vector(n_region.get("site_ids")),
                "n_fractional_range": _join_vector(n_region.get("fractional_range")),
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_dopant_fraction_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, entry in enumerate(summary.get("entries", []) or [], start=1):
        rows.append(
            {
                "index": index,
                "host_element": entry.get("host_element"),
                "dopant_element": entry.get("dopant_element"),
                "requested_fraction": entry.get("requested_fraction"),
                "requested_percent": entry.get("requested_percent"),
                "actual_fraction": entry.get("actual_fraction"),
                "actual_percent": entry.get("actual_percent"),
                "candidate_site_count": entry.get("candidate_site_count"),
                "substituted_site_count": entry.get("substituted_site_count"),
                "selected_atom_ids": _join_vector(entry.get("selected_atom_ids")),
                "selection_strategy": entry.get("selection_strategy") or "atom_id_order",
                "scientific_scope": entry.get("scientific_scope"),
                "site_selection_integrity_ok": entry.get("site_selection_integrity_ok"),
                "site_selection_replay_verified": entry.get("site_selection_replay_verified"),
                "site_selection_geometry_unchanged": entry.get("site_selection_geometry_unchanged"),
                "selected_pair_minimum_angstrom": entry.get("selected_pair_minimum_angstrom"),
                "selected_pair_mean_angstrom": entry.get("selected_pair_mean_angstrom"),
                "selected_pair_maximum_angstrom": entry.get("selected_pair_maximum_angstrom"),
                "candidate_nearest_pair_distance_angstrom": entry.get(
                    "candidate_nearest_pair_distance_angstrom"
                ),
                "selected_pairs_at_candidate_nearest_distance": entry.get(
                    "selected_pairs_at_candidate_nearest_distance"
                ),
                "minimum_distance_improvement_over_atom_id_order_angstrom": entry.get(
                    "minimum_distance_improvement_over_atom_id_order_angstrom"
                ),
                "site_selection_warning_count": entry.get("site_selection_warning_count"),
                "site_selection_error_count": entry.get("site_selection_error_count"),
                "site_selection_warnings": _join_vector(entry.get("site_selection_warnings")),
                "site_selection_errors": _join_vector(entry.get("site_selection_errors")),
                "rounding_error_fraction": entry.get("rounding_error_fraction"),
                "rounding_warning": entry.get("rounding_warning"),
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_alloy_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, entry in enumerate(summary.get("entries", []) or [], start=1):
        rows.append(
            {
                "index": index,
                "host_element": entry.get("host_element"),
                "alloy_element": entry.get("alloy_element"),
                "requested_fraction": entry.get("requested_fraction"),
                "requested_percent": entry.get("requested_percent"),
                "actual_fraction": entry.get("actual_fraction"),
                "actual_percent": entry.get("actual_percent"),
                "candidate_site_count": entry.get("candidate_site_count"),
                "substituted_site_count": entry.get("substituted_site_count"),
                "selected_atom_ids": _join_vector(entry.get("selected_atom_ids")),
                "selection_strategy": entry.get("selection_strategy") or "atom_id_order",
                "scientific_scope": entry.get("scientific_scope"),
                "site_selection_integrity_ok": entry.get("site_selection_integrity_ok"),
                "site_selection_replay_verified": entry.get("site_selection_replay_verified"),
                "site_selection_geometry_unchanged": entry.get("site_selection_geometry_unchanged"),
                "selected_pair_minimum_angstrom": entry.get("selected_pair_minimum_angstrom"),
                "selected_pair_mean_angstrom": entry.get("selected_pair_mean_angstrom"),
                "selected_pair_maximum_angstrom": entry.get("selected_pair_maximum_angstrom"),
                "candidate_nearest_pair_distance_angstrom": entry.get(
                    "candidate_nearest_pair_distance_angstrom"
                ),
                "selected_pairs_at_candidate_nearest_distance": entry.get(
                    "selected_pairs_at_candidate_nearest_distance"
                ),
                "minimum_distance_improvement_over_atom_id_order_angstrom": entry.get(
                    "minimum_distance_improvement_over_atom_id_order_angstrom"
                ),
                "site_selection_warning_count": entry.get("site_selection_warning_count"),
                "site_selection_error_count": entry.get("site_selection_error_count"),
                "site_selection_warnings": _join_vector(entry.get("site_selection_warnings")),
                "site_selection_errors": _join_vector(entry.get("site_selection_errors")),
                "rounding_error_fraction": entry.get("rounding_error_fraction"),
                "rounding_warning": entry.get("rounding_warning"),
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_site_pair_distribution_csv_rows(
    dopant_fraction_summary: dict[str, Any],
    alloy_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry_kind, summary, replacement_field in (
        ("dopant_fraction", dopant_fraction_summary, "dopant_element"),
        ("alloy_fraction", alloy_summary, "alloy_element"),
    ):
        for entry_index, entry in enumerate(summary.get("entries", []) or [], start=1):
            distribution = entry.get("site_pair_distribution")
            if not isinstance(distribution, dict):
                continue
            shells = distribution.get("shells") or [{}]
            for shell in shells:
                rows.append(
                    {
                        "entry_kind": entry_kind,
                        "entry_index": entry_index,
                        "host_element": entry.get("host_element"),
                        "replacement_element": entry.get(replacement_field),
                        "selection_strategy": entry.get("selection_strategy"),
                        "scientific_scope": distribution.get("scientific_scope"),
                        "geometry_basis": distribution.get("geometry_basis"),
                        "source_receipt_sha256": distribution.get("source_receipt_sha256"),
                        "candidate_geometry_sha256": distribution.get("candidate_geometry_sha256"),
                        "analysis_sha256": distribution.get("analysis_sha256"),
                        "analysis_integrity_ok": distribution.get("integrity_ok"),
                        "current_geometry_applicable": entry.get(
                            "site_pair_distribution_current_geometry_applicable"
                        ),
                        "candidate_site_count": distribution.get("candidate_site_count"),
                        "selected_site_count": distribution.get("selected_site_count"),
                        "selected_fraction": distribution.get("selected_fraction"),
                        "fixed_composition_expected_pair_probability": distribution.get(
                            "fixed_composition_expected_pair_probability"
                        ),
                        "pair_conservation_verified": distribution.get("pair_conservation_verified"),
                        "shell_count": distribution.get("shell_count"),
                        "reported_shell_count": distribution.get("reported_shell_count"),
                        "shells_truncated": distribution.get("shells_truncated"),
                        "shell_index": shell.get("shell_index"),
                        "distance_min_angstrom": shell.get("distance_min_angstrom"),
                        "distance_mean_angstrom": shell.get("distance_mean_angstrom"),
                        "distance_max_angstrom": shell.get("distance_max_angstrom"),
                        "candidate_pair_count": shell.get("candidate_pair_count"),
                        "coordination_number_per_candidate": shell.get(
                            "coordination_number_per_candidate"
                        ),
                        "candidate_degree_min": shell.get("candidate_degree_min"),
                        "candidate_degree_mean": shell.get("candidate_degree_mean"),
                        "candidate_degree_max": shell.get("candidate_degree_max"),
                        "candidate_degree_uniform": shell.get("candidate_degree_uniform"),
                        "selected_pair_count": shell.get("selected_pair_count"),
                        "selected_pair_fraction": shell.get("selected_pair_fraction"),
                        "unselected_pair_count": shell.get("unselected_pair_count"),
                        "mixed_selected_unselected_pair_count": shell.get(
                            "mixed_selected_unselected_pair_count"
                        ),
                        "occupancy_pair_partition_verified": shell.get(
                            "occupancy_pair_partition_verified"
                        ),
                        "baseline_pair_count": shell.get("baseline_pair_count"),
                        "baseline_pair_fraction": shell.get("baseline_pair_fraction"),
                        "baseline_unselected_pair_count": shell.get(
                            "baseline_unselected_pair_count"
                        ),
                        "baseline_mixed_selected_unselected_pair_count": shell.get(
                            "baseline_mixed_selected_unselected_pair_count"
                        ),
                        "baseline_occupancy_pair_partition_verified": shell.get(
                            "baseline_occupancy_pair_partition_verified"
                        ),
                        "fixed_composition_expected_pair_count": shell.get(
                            "fixed_composition_expected_pair_count"
                        ),
                        "fixed_composition_expected_pair_fraction": shell.get(
                            "fixed_composition_expected_pair_fraction"
                        ),
                        "fixed_composition_expected_unselected_pair_count": shell.get(
                            "fixed_composition_expected_unselected_pair_count"
                        ),
                        "fixed_composition_expected_mixed_pair_count": shell.get(
                            "fixed_composition_expected_mixed_pair_count"
                        ),
                        "fixed_composition_expected_mixed_pair_fraction": shell.get(
                            "fixed_composition_expected_mixed_pair_fraction"
                        ),
                        "selected_minus_expected_pair_count": shell.get(
                            "selected_minus_expected_pair_count"
                        ),
                        "baseline_minus_expected_pair_count": shell.get(
                            "baseline_minus_expected_pair_count"
                        ),
                        "selected_pair_avoidance_fraction": shell.get(
                            "selected_pair_avoidance_fraction"
                        ),
                        "selected_pair_expectation_class": shell.get(
                            "selected_pair_expectation_class"
                        ),
                        "baseline_pair_expectation_class": shell.get(
                            "baseline_pair_expectation_class"
                        ),
                        "selected_pair_examples": _join_vector(shell.get("selected_pair_examples")),
                        "baseline_pair_examples": _join_vector(shell.get("baseline_pair_examples")),
                        "nearest_shell_pair_count_reduction_vs_atom_id_order": distribution.get(
                            "nearest_shell_pair_count_reduction_vs_atom_id_order"
                        ),
                        "nearest_shell_pair_excess_review_required": distribution.get(
                            "nearest_shell_pair_excess_review_required"
                        ),
                        "nearest_shell_pair_avoidance_observed": distribution.get(
                            "nearest_shell_pair_avoidance_observed"
                        ),
                        "selection_reduces_nearest_shell_pairs_vs_atom_id_order": distribution.get(
                            "selection_reduces_nearest_shell_pairs_vs_atom_id_order"
                        ),
                        "selected_pair_fraction_rmse_from_fixed_composition_expectation": distribution.get(
                            "selected_pair_fraction_rmse_from_fixed_composition_expectation"
                        ),
                        "baseline_pair_fraction_rmse_from_fixed_composition_expectation": distribution.get(
                            "baseline_pair_fraction_rmse_from_fixed_composition_expectation"
                        ),
                        "error_count": distribution.get("error_count"),
                        "warning_count": distribution.get("warning_count"),
                        "errors": _join_vector(distribution.get("errors")),
                        "warnings": _join_vector(distribution.get("warnings")),
                    }
                )
    return rows


def _semiconductor_site_short_range_order_csv_rows(
    dopant_fraction_summary: dict[str, Any],
    alloy_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry_kind, summary, replacement_field in (
        ("dopant_fraction", dopant_fraction_summary, "dopant_element"),
        ("alloy_fraction", alloy_summary, "alloy_element"),
    ):
        for entry_index, entry in enumerate(summary.get("entries", []) or [], start=1):
            analysis = entry.get("site_short_range_order")
            if not isinstance(analysis, dict):
                continue
            shells = analysis.get("shells") or [{}]
            for shell in shells:
                rows.append(
                    {
                        "entry_kind": entry_kind,
                        "entry_index": entry_index,
                        "host_element": entry.get("host_element"),
                        "replacement_element": entry.get(replacement_field),
                        "selection_strategy": entry.get("selection_strategy"),
                        "scientific_scope": analysis.get("scientific_scope"),
                        "pair_graph_scope": analysis.get("pair_graph_scope"),
                        "source_pair_distribution_analysis_sha256": analysis.get(
                            "source_pair_distribution_analysis_sha256"
                        ),
                        "analysis_sha256": analysis.get("analysis_sha256"),
                        "analysis_integrity_ok": analysis.get("integrity_ok"),
                        "current_geometry_applicable": entry.get(
                            "site_short_range_order_current_geometry_applicable"
                        ),
                        "standard_periodic_shell_multiplicity_verified": analysis.get(
                            "standard_periodic_shell_multiplicity_verified"
                        ),
                        "crystallographic_symmetry_orbits_verified": analysis.get(
                            "crystallographic_symmetry_orbits_verified"
                        ),
                        "candidate_site_count": analysis.get("candidate_site_count"),
                        "selected_site_count": analysis.get("selected_site_count"),
                        "unselected_site_count": analysis.get("unselected_site_count"),
                        "selected_fraction": analysis.get("selected_fraction"),
                        "unselected_fraction": analysis.get("unselected_fraction"),
                        "binary_occupancy_available": analysis.get("binary_occupancy_available"),
                        "degree_uniform_all_reported_shells": analysis.get(
                            "degree_uniform_all_reported_shells"
                        ),
                        "classical_bulk_shell_interpretation_ready": analysis.get(
                            "classical_bulk_shell_interpretation_ready"
                        ),
                        "shell_count": analysis.get("shell_count"),
                        "reported_shell_count": analysis.get("reported_shell_count"),
                        "shells_truncated": analysis.get("shells_truncated"),
                        "shell_index": shell.get("shell_index"),
                        "distance_min_angstrom": shell.get("distance_min_angstrom"),
                        "distance_mean_angstrom": shell.get("distance_mean_angstrom"),
                        "distance_max_angstrom": shell.get("distance_max_angstrom"),
                        "candidate_pair_count": shell.get("candidate_pair_count"),
                        "candidate_degree_min": shell.get("candidate_degree_min"),
                        "candidate_degree_mean": shell.get("candidate_degree_mean"),
                        "candidate_degree_max": shell.get("candidate_degree_max"),
                        "candidate_degree_uniform": shell.get("candidate_degree_uniform"),
                        "selected_selected_pair_count": shell.get(
                            "selected_selected_pair_count"
                        ),
                        "unselected_unselected_pair_count": shell.get(
                            "unselected_unselected_pair_count"
                        ),
                        "mixed_selected_unselected_pair_count": shell.get(
                            "mixed_selected_unselected_pair_count"
                        ),
                        "baseline_selected_selected_pair_count": shell.get(
                            "baseline_selected_selected_pair_count"
                        ),
                        "baseline_unselected_unselected_pair_count": shell.get(
                            "baseline_unselected_unselected_pair_count"
                        ),
                        "baseline_mixed_selected_unselected_pair_count": shell.get(
                            "baseline_mixed_selected_unselected_pair_count"
                        ),
                        "occupancy_pair_partition_verified": shell.get(
                            "occupancy_pair_partition_verified"
                        ),
                        "classical_random_mixed_pair_expectation": shell.get(
                            "classical_random_mixed_pair_expectation"
                        ),
                        "fixed_composition_random_mixed_pair_expectation": shell.get(
                            "fixed_composition_random_mixed_pair_expectation"
                        ),
                        "warren_cowley_pair_count_alpha_classical": shell.get(
                            "warren_cowley_pair_count_alpha_classical"
                        ),
                        "baseline_warren_cowley_pair_count_alpha_classical": shell.get(
                            "baseline_warren_cowley_pair_count_alpha_classical"
                        ),
                        "finite_composition_corrected_pair_alpha": shell.get(
                            "finite_composition_corrected_pair_alpha"
                        ),
                        "baseline_finite_composition_corrected_pair_alpha": shell.get(
                            "baseline_finite_composition_corrected_pair_alpha"
                        ),
                        "unlike_pair_expectation_class": shell.get(
                            "unlike_pair_expectation_class"
                        ),
                        "baseline_unlike_pair_expectation_class": shell.get(
                            "baseline_unlike_pair_expectation_class"
                        ),
                        "mixed_pair_count_change_vs_atom_id_order": shell.get(
                            "mixed_pair_count_change_vs_atom_id_order"
                        ),
                        "unlike_pair_enrichment_ratio": shell.get(
                            "unlike_pair_enrichment_ratio"
                        ),
                        "baseline_unlike_pair_enrichment_ratio": shell.get(
                            "baseline_unlike_pair_enrichment_ratio"
                        ),
                        "nearest_shell_unlike_pair_expectation_class": analysis.get(
                            "nearest_shell_unlike_pair_expectation_class"
                        ),
                        "nearest_shell_ordering_like_unlike_pair_enrichment": analysis.get(
                            "nearest_shell_ordering_like_unlike_pair_enrichment"
                        ),
                        "nearest_shell_clustering_like_unlike_pair_depletion_review_required": analysis.get(
                            "nearest_shell_clustering_like_unlike_pair_depletion_review_required"
                        ),
                        "finite_composition_corrected_pair_alpha_rmse": analysis.get(
                            "finite_composition_corrected_pair_alpha_rmse"
                        ),
                        "baseline_finite_composition_corrected_pair_alpha_rmse": analysis.get(
                            "baseline_finite_composition_corrected_pair_alpha_rmse"
                        ),
                        "error_count": analysis.get("error_count"),
                        "warning_count": analysis.get("warning_count"),
                        "errors": _join_vector(analysis.get("errors")),
                        "warnings": _join_vector(analysis.get("warnings")),
                    }
                )
    return rows


def _semiconductor_layer_profile_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for layer in summary.get("layers", []) or []:
        rows.append(
            {
                "layer_index": layer.get("layer_index"),
                "axis": summary.get("axis"),
                "fractional_center": layer.get("fractional_center"),
                "fractional_min": layer.get("fractional_min"),
                "fractional_max": layer.get("fractional_max"),
                "axis_coordinate_angstrom": layer.get("axis_coordinate_angstrom"),
                "span_fractional": layer.get("span_fractional"),
                "span_angstrom": layer.get("span_angstrom"),
                "spacing_to_previous_angstrom": layer.get("spacing_to_previous_angstrom"),
                "spacing_to_next_angstrom": layer.get("spacing_to_next_angstrom"),
                "atom_count": layer.get("atom_count"),
                "non_passivant_atom_count": layer.get("non_passivant_atom_count"),
                "passivant_atom_count": layer.get("passivant_atom_count"),
                "element_counts": json.dumps(layer.get("element_counts") or {}, ensure_ascii=False, sort_keys=True),
                "atom_ids": _join_vector(layer.get("atom_ids")),
            }
        )
    return rows


def _semiconductor_layer_translation_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    entries = summary.get("entries", []) or []
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    rows = []
    for index, entry in enumerate(entries, start=1):
        is_latest = index == len(entries) and entry == latest
        rows.append(
            {
                "index": index,
                "is_latest": is_latest,
                "target_selector": entry.get("target_selector"),
                "layer_index": entry.get("layer_index"),
                "layer_count": entry.get("layer_count"),
                "profile_axis": entry.get("profile_axis"),
                "profile_fractional_center": entry.get("profile_fractional_center"),
                "translation_axis": entry.get("translation_axis"),
                "distance_angstrom": entry.get("distance_angstrom"),
                "delta_fractional": entry.get("delta_fractional"),
                "atom_count": entry.get("atom_count"),
                "atom_ids": _join_vector(entry.get("atom_ids")),
                "periodic_wrap": entry.get("periodic_wrap"),
                "wrapped_atom_count": entry.get("wrapped_atom_count"),
                "wrapped_atom_ids": _join_vector(entry.get("wrapped_atom_ids")),
                "in_plane_translation": entry.get("in_plane_translation"),
                "target_binding_matches_current_layer": (
                    summary.get("target_binding_matches_current_layer") if is_latest else None
                ),
                "metadata_consistent": summary.get("metadata_consistent") if is_latest else None,
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_layer_rotation_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    entries = summary.get("entries", []) or []
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    rows = []
    for index, entry in enumerate(entries, start=1):
        is_latest = index == len(entries) and entry == latest
        rows.append(
            {
                "index": index,
                "is_latest": is_latest,
                "target_selector": entry.get("target_selector"),
                "layer_index": entry.get("layer_index"),
                "layer_count": entry.get("layer_count"),
                "profile_axis": entry.get("profile_axis"),
                "profile_fractional_center": entry.get("profile_fractional_center"),
                "rotation_axis": entry.get("rotation_axis"),
                "rotation_axis_source": entry.get("rotation_axis_source"),
                "angle_degrees": entry.get("angle_degrees"),
                "pivot_fractional": _join_vector(entry.get("pivot_fractional")),
                "atom_count": entry.get("atom_count"),
                "atom_ids": _join_vector(entry.get("atom_ids")),
                "periodic_wrap": entry.get("periodic_wrap"),
                "wrapped_atom_count": entry.get("wrapped_atom_count"),
                "wrapped_atom_ids": _join_vector(entry.get("wrapped_atom_ids")),
                "axis_orthogonality_max_abs_cosine": entry.get("axis_orthogonality_max_abs_cosine"),
                "target_binding_matches_current_layer": (
                    summary.get("target_binding_matches_current_layer") if is_latest else None
                ),
                "coordinate_binding_matches_current": (
                    summary.get("coordinate_binding_matches_current") if is_latest else None
                ),
                "commensurability_verified": entry.get("commensurability_verified"),
                "requires_commensurate_supercell": entry.get("requires_commensurate_supercell"),
                "requires_geometry_relaxation": entry.get("requires_geometry_relaxation"),
                "visual_review_only": entry.get("visual_review_only"),
                "calculation_ready": entry.get("calculation_ready"),
                "metadata_consistent": summary.get("metadata_consistent") if is_latest else None,
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_castep_geometry_optimization_csv_row(
    summary: dict[str, Any],
) -> dict[str, Any]:
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    row = {
        key: latest.get(key)
        for key in (
            "source_project_id",
            "source_revision",
            "target_revision",
            "task",
            "backend",
            "cell_optimization",
            "optimization_algorithm",
            "converged",
            "total_energy_kcal_per_mol",
            "enthalpy_kcal_per_mol",
            "source_structure_sha256",
            "output_structure_sha256",
        )
    }
    for key in (
        "current_structure_sha256",
        "schema_verified",
        "history_binding_verified",
        "project_binding_verified",
        "revision_binding_verified",
        "task_verified",
        "backend_verified",
        "convergence_verified",
        "atom_identity_verified",
        "source_binding_verified",
        "output_binding_verified",
        "script_binding_verified",
        "operation_binding_verified",
        "simulation_binding_verified",
        "fixed_cell_verified",
        "transition_verified",
        "fixed_cell_transition_verified",
        "quality",
        "warning_count",
    ):
        row[key] = summary.get(key)
    row["blocking_reasons"] = json.dumps(
        summary.get("blocking_reasons") or [],
        separators=(",", ":"),
    )
    return row


def _semiconductor_castep_electronic_result_csv_row(
    summary: dict[str, Any],
    assessment_value: Any = None,
) -> dict[str, Any]:
    checks = summary.get("checks") if isinstance(summary.get("checks"), dict) else {}
    native = (
        summary.get("native_output_audit")
        if isinstance(summary.get("native_output_audit"), dict)
        else {}
    )
    scf = (
        native.get("castep_output_audit")
        if isinstance(native.get("castep_output_audit"), dict)
        else {}
    )
    bands = (
        native.get("bands_summary")
        if isinstance(native.get("bands_summary"), dict)
        else {}
    )
    band_edges = (
        native.get("sampled_band_edges")
        if isinstance(native.get("sampled_band_edges"), dict)
        else {}
    )
    gap_crosscheck = (
        band_edges.get("reported_band_gap_crosscheck")
        if isinstance(band_edges.get("reported_band_gap_crosscheck"), dict)
        else {}
    )
    assessment = assessment_value if isinstance(assessment_value, dict) else {}
    return {
        "available": summary.get("available"),
        "status": summary.get("status"),
        "binding_verified": summary.get("binding_verified"),
        "task": summary.get("task"),
        "source_revision": summary.get("source_revision"),
        "target_revision": summary.get("target_revision"),
        "backend_run_completed": summary.get("backend_run_completed"),
        "scientific_convergence_verified": summary.get(
            "scientific_convergence_verified"
        ),
        "scientific_band_gap_verified": summary.get(
            "scientific_band_gap_verified"
        ),
        "numeric_curve_data_exported": summary.get(
            "numeric_curve_data_exported"
        ),
        "numeric_curve_kind": summary.get("numeric_curve_kind"),
        "native_band_kpoint_path_exported": summary.get(
            "native_band_kpoint_path_exported"
        ),
        "pdos_projection_weights_exported": summary.get(
            "pdos_projection_weights_exported"
        ),
        "band_path_binding_verified": summary.get(
            "band_path_binding_verified"
        ),
        "required_result_document": summary.get("required_result_document"),
        "result_document_name": summary.get("result_document_name"),
        "total_energy_kcal_per_mol": summary.get(
            "total_energy_kcal_per_mol"
        ),
        "free_energy_kcal_per_mol": summary.get("free_energy_kcal_per_mol"),
        "band_gap_ev": summary.get("band_gap_ev"),
        "fermi_level_ev": summary.get("fermi_level_ev"),
        "work_function_ev": summary.get("work_function_ev"),
        "work_function_top_ev": summary.get("work_function_top_ev"),
        "work_function_bottom_ev": summary.get("work_function_bottom_ev"),
        "output_structure": summary.get("output_structure"),
        "output_report": summary.get("output_report"),
        "result_metadata": summary.get("result_metadata"),
        "native_artifact_count": summary.get("native_artifact_count"),
        "native_output_audit_status": native.get("status"),
        "native_output_audit_path": summary.get("native_output_audit_path"),
        "derived_artifact_count": summary.get("derived_artifact_count"),
        "derived_artifact_paths": json.dumps(
            [
                item.get("path")
                for item in summary.get("derived_artifacts", []) or []
                if isinstance(item, dict)
            ],
            separators=(",", ":"),
        ),
        "scf_audit_status": scf.get("status"),
        "scf_run_completed": scf.get("run_completed"),
        "scf_max_cycles": scf.get("max_scf_cycles"),
        "scf_last_iteration": scf.get("last_scf_iteration"),
        "scf_maximum_cycles_reached": scf.get("maximum_scf_cycles_reached"),
        "scf_final_energy_ev": scf.get("final_energy_ev"),
        "scf_final_free_energy_ev": scf.get("final_free_energy_ev"),
        "scf_total_time_seconds": scf.get("total_time_seconds"),
        "scf_warning_count": scf.get("warning_count"),
        "scf_fatal_marker_count": scf.get("fatal_marker_count"),
        "native_band_kpoint_count": bands.get("number_of_kpoints"),
        "native_band_spin_component_count": bands.get(
            "number_of_spin_components"
        ),
        "native_band_eigenvalue_count": bands.get("eigenvalue_count"),
        "native_band_kpoint_weight_sum": bands.get("kpoint_weight_sum"),
        "sampled_band_edge_status": band_edges.get("status"),
        "sampled_band_gap_ev": band_edges.get("sampled_gap_ev"),
        "sampled_band_gap_spin_component": band_edges.get(
            "gap_spin_component"
        ),
        "sampled_fermi_crossing_observed": band_edges.get(
            "fermi_crossing_observed"
        ),
        "sampled_crossing_band_count": band_edges.get("crossing_band_count"),
        "sampled_minimum_same_kpoint_separation_ev": band_edges.get(
            "minimum_same_kpoint_fermi_separation_ev"
        ),
        "sampled_minimum_abs_energy_minus_fermi_ev": band_edges.get(
            "minimum_abs_energy_minus_fermi_ev"
        ),
        "reported_band_gap_crosscheck_status": gap_crosscheck.get("status"),
        "reported_band_gap_difference_ev": gap_crosscheck.get(
            "absolute_difference_ev"
        ),
        "reported_band_gap_comparison_tolerance_ev": gap_crosscheck.get(
            "comparison_tolerance_ev"
        ),
        "assessment_status": assessment.get("status"),
        "assessment_trust_status": assessment.get("trust_status"),
        "assessment_artifact_evidence_verified": assessment.get(
            "artifact_evidence_verified"
        ),
        "assessment_scientific_result_verified": False,
        "assessment_structure_normality_blocked": assessment.get(
            "structure_normality_blocked"
        ),
        "assessment_calculation_result_review_required": assessment.get(
            "calculation_result_review_required"
        ),
        "assessment_result_review_reasons": json.dumps(
            assessment.get("result_review_reasons") or [],
            separators=(",", ":"),
        ),
        **_castep_band_edge_state_csv_values("sampled_vbm", band_edges.get("vbm")),
        **_castep_band_edge_state_csv_values("sampled_cbm", band_edges.get("cbm")),
        "checks": json.dumps(checks, sort_keys=True, separators=(",", ":")),
        "warning_count": len(summary.get("warnings") or []),
        "warnings": json.dumps(
            summary.get("warnings") or [],
            separators=(",", ":"),
        ),
    }


def _castep_band_edge_state_csv_values(
    prefix: str,
    value: Any,
) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    fractional = state.get("kpoint_fractional")
    if not isinstance(fractional, list) or len(fractional) != 3:
        fractional = [None, None, None]
    return {
        f"{prefix}_spin_component": state.get("spin_component"),
        f"{prefix}_kpoint_index": state.get("kpoint_index"),
        f"{prefix}_kx_fractional": fractional[0],
        f"{prefix}_ky_fractional": fractional[1],
        f"{prefix}_kz_fractional": fractional[2],
        f"{prefix}_band_index": state.get("band_index"),
        f"{prefix}_eigenvalue_ev": state.get("eigenvalue_ev"),
        f"{prefix}_energy_minus_fermi_ev": state.get(
            "energy_minus_fermi_ev"
        ),
    }


def _semiconductor_castep_band_edge_csv_rows(
    summary: dict[str, Any],
    assessment_value: Any,
) -> list[dict[str, Any]]:
    if summary.get("binding_verified") is not True:
        return []
    native = (
        summary.get("native_output_audit")
        if isinstance(summary.get("native_output_audit"), dict)
        else {}
    )
    bands = (
        native.get("bands_summary")
        if isinstance(native.get("bands_summary"), dict)
        else {}
    )
    edges = (
        native.get("sampled_band_edges")
        if isinstance(native.get("sampled_band_edges"), dict)
        else {}
    )
    if not edges:
        return []
    assessment = assessment_value if isinstance(assessment_value, dict) else {}
    aggregate_crosscheck = (
        edges.get("reported_band_gap_crosscheck")
        if isinstance(edges.get("reported_band_gap_crosscheck"), dict)
        else {}
    )
    common = {
        "receipt_binding_verified": True,
        "task": summary.get("task"),
        "source_revision": summary.get("source_revision"),
        "target_revision": summary.get("target_revision"),
        "native_output_audit_schema": native.get("schema_version"),
        "native_output_audit_status": native.get("status"),
        "native_bands_source_path": bands.get("source_path"),
        "native_bands_source_sha256": bands.get("source_sha256"),
        "native_band_kpoint_count": bands.get("number_of_kpoints"),
        "native_band_spin_component_count": bands.get(
            "number_of_spin_components"
        ),
        "scientific_convergence_verified": False,
        "scientific_band_gap_verified": False,
        "assessment_status": assessment.get("status"),
        "assessment_trust_status": assessment.get("trust_status"),
        "assessment_result_review_reasons": json.dumps(
            assessment.get("result_review_reasons") or [],
            separators=(",", ":"),
        ),
    }

    def row_for(
        row_type: str,
        edge: dict[str, Any],
        *,
        crossing_band: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        crosscheck = (
            edge.get("reported_band_gap_crosscheck")
            if isinstance(edge.get("reported_band_gap_crosscheck"), dict)
            else aggregate_crosscheck
        )
        crossing = crossing_band or {}
        return {
            **common,
            "row_type": row_type,
            "status": edge.get("status"),
            "spin_component": edge.get("spin_component"),
            "gap_spin_component": edge.get("gap_spin_component"),
            "fermi_energy_hartree": edge.get("fermi_energy_hartree"),
            "fermi_energy_ev": edge.get("fermi_energy_ev"),
            "sampled_gap_ev": edge.get("sampled_gap_ev"),
            "fermi_crossing_observed": edge.get(
                "fermi_crossing_observed"
            ),
            "crossing_band_count": edge.get("crossing_band_count"),
            "crossing_bands_truncated": edge.get("crossing_bands_truncated"),
            "minimum_same_kpoint_fermi_separation_ev": edge.get(
                "minimum_same_kpoint_fermi_separation_ev"
            ),
            "minimum_abs_energy_minus_fermi_ev": edge.get(
                "minimum_abs_energy_minus_fermi_ev"
            ),
            "crossing_band_index": crossing.get("band_index"),
            "crossing_minimum_energy_minus_fermi_ev": crossing.get(
                "minimum_energy_minus_fermi_ev"
            ),
            "crossing_maximum_energy_minus_fermi_ev": crossing.get(
                "maximum_energy_minus_fermi_ev"
            ),
            "crossing_near_fermi_state_count": crossing.get(
                "near_fermi_state_count"
            ),
            "reported_band_gap_crosscheck_status": crosscheck.get("status"),
            "reported_band_gap_ev": crosscheck.get("reported_band_gap_ev"),
            "reported_band_gap_difference_ev": crosscheck.get(
                "absolute_difference_ev"
            ),
            "reported_band_gap_comparison_tolerance_ev": crosscheck.get(
                "comparison_tolerance_ev"
            ),
            **_castep_band_edge_state_csv_values("vbm", edge.get("vbm")),
            **_castep_band_edge_state_csv_values("cbm", edge.get("cbm")),
            "warnings": json.dumps(
                edge.get("warnings") or [],
                separators=(",", ":"),
            ),
        }

    rows = [row_for("aggregate", edges)]
    for channel in edges.get("spin_channels") or []:
        if not isinstance(channel, dict):
            continue
        rows.append(row_for("spin_channel", channel))
        for crossing in channel.get("crossing_bands") or []:
            if isinstance(crossing, dict):
                rows.append(
                    row_for(
                        "crossing_band",
                        channel,
                        crossing_band=crossing,
                    )
                )
    return rows


def _semiconductor_castep_convergence_csv_rows(
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flatten a convergence audit into inspectable summary, point, and delta rows."""

    columns = (
        "row_type",
        "audit_status",
        "project_id",
        "current_revision",
        "current_structure_sha256",
        "history_entry_count",
        "verified_point_count",
        "rejected_point_count",
        "artifact_evidence_verified",
        "parameter_sensitivity_evidence_verified",
        "parameter_sensitivity_within_tolerance",
        "scientific_convergence_verified",
        "structure_normality_blocked",
        "energy_tolerance_ev_per_atom",
        "band_gap_tolerance_ev",
        "minimum_sequence_point_count",
        "comparable_series_count",
        "stable_series_count",
        "above_tolerance_series_count",
        "pairwise_only_series_count",
        "active_series_id",
        "active_axis",
        "series_id",
        "series_status",
        "axis",
        "axis_mode",
        "refinement_direction",
        "series_point_count",
        "sequence_evidence_sufficient",
        "latest_pair_within_tolerance",
        "series_ids",
        "target_revision",
        "source_revision",
        "task",
        "functional",
        "quality",
        "cutoff_energy_ev",
        "kpoint_mode",
        "kpoint_grid",
        "kpoint_grid_product",
        "kpoint_separation",
        "properties_kpoint_separation",
        "total_energy_kcal_per_mol",
        "total_energy_ev_per_cell",
        "total_energy_ev_per_atom",
        "band_gap_ev",
        "fermi_level_ev",
        "native_output_audit_status",
        "native_scf_status",
        "native_scf_last_iteration",
        "native_scf_maximum_cycles_reached",
        "receipt_sha256",
        "simulation_sha256",
        "output_report_sha256",
        "coarse_revision",
        "fine_revision",
        "coarse_axis_value",
        "fine_axis_value",
        "refinement_verified",
        "total_energy_delta_ev_per_atom",
        "energy_within_tolerance",
        "band_gap_delta_ev",
        "band_gap_within_tolerance",
        "available_metric_count",
        "all_available_metrics_within_tolerance",
        "binding_error_history_index",
        "binding_error_target_revision",
        "binding_error_reason",
        "binding_error_detail",
        "result_review_reasons",
        "recommended_action_id",
        "recommended_tool",
        "recommended_action",
        "recommended_preview_payload",
        "execute_requires_explicit_confirmation",
    )
    common = {
        "audit_status": audit.get("status"),
        "project_id": audit.get("project_id"),
        "current_revision": audit.get("current_revision"),
        "current_structure_sha256": audit.get("current_structure_sha256"),
        "history_entry_count": audit.get("history_entry_count"),
        "verified_point_count": audit.get("verified_point_count"),
        "rejected_point_count": audit.get("rejected_point_count"),
        "artifact_evidence_verified": audit.get("artifact_evidence_verified"),
        "parameter_sensitivity_evidence_verified": audit.get(
            "parameter_sensitivity_evidence_verified"
        ),
        "parameter_sensitivity_within_tolerance": audit.get(
            "parameter_sensitivity_within_tolerance"
        ),
        "scientific_convergence_verified": False,
        "structure_normality_blocked": audit.get("structure_normality_blocked"),
        "energy_tolerance_ev_per_atom": audit.get(
            "energy_tolerance_ev_per_atom"
        ),
        "band_gap_tolerance_ev": audit.get("band_gap_tolerance_ev"),
        "minimum_sequence_point_count": audit.get(
            "minimum_sequence_point_count"
        ),
        "comparable_series_count": audit.get("comparable_series_count"),
        "stable_series_count": audit.get("stable_series_count"),
        "above_tolerance_series_count": audit.get(
            "above_tolerance_series_count"
        ),
        "pairwise_only_series_count": audit.get("pairwise_only_series_count"),
        "active_series_id": audit.get("active_series_id"),
        "active_axis": audit.get("active_axis"),
        "result_review_reasons": json.dumps(
            audit.get("result_review_reasons") or [], separators=(",", ":")
        ),
        "recommended_action_id": audit.get("recommended_action_id"),
        "recommended_tool": audit.get("recommended_tool"),
        "recommended_action": audit.get("recommended_action"),
        "recommended_preview_payload": json.dumps(
            audit.get("recommended_preview_payload"),
            sort_keys=True,
            separators=(",", ":"),
        ),
        "execute_requires_explicit_confirmation": audit.get(
            "execute_requires_explicit_confirmation"
        ),
    }

    def row(row_type: str, **values: Any) -> dict[str, Any]:
        result = {column: None for column in columns}
        result.update(common)
        result["row_type"] = row_type
        result.update(values)
        return result

    series_values = [
        item for item in audit.get("series", []) or [] if isinstance(item, dict)
    ]
    memberships: dict[int, list[str]] = defaultdict(list)
    for item in series_values:
        for revision in item.get("target_revisions", []) or []:
            if isinstance(revision, int):
                memberships[revision].append(str(item.get("series_id")))
    rows = [row("audit_summary")]
    for point in audit.get("points", []) or []:
        if not isinstance(point, dict):
            continue
        revision = point.get("target_revision")
        rows.append(
            row(
                "verified_point",
                series_ids=json.dumps(
                    memberships.get(revision, []), separators=(",", ":")
                ),
                target_revision=revision,
                source_revision=point.get("source_revision"),
                task=point.get("task"),
                functional=point.get("functional"),
                quality=point.get("quality"),
                cutoff_energy_ev=point.get("cutoff_energy_ev"),
                kpoint_mode=point.get("kpoint_mode"),
                kpoint_grid=json.dumps(
                    point.get("kpoint_grid"), separators=(",", ":")
                ),
                kpoint_grid_product=point.get("kpoint_grid_product"),
                kpoint_separation=point.get("kpoint_separation"),
                properties_kpoint_separation=point.get(
                    "properties_kpoint_separation"
                ),
                total_energy_kcal_per_mol=point.get(
                    "total_energy_kcal_per_mol"
                ),
                total_energy_ev_per_cell=point.get("total_energy_ev_per_cell"),
                total_energy_ev_per_atom=point.get("total_energy_ev_per_atom"),
                band_gap_ev=point.get("band_gap_ev"),
                fermi_level_ev=point.get("fermi_level_ev"),
                native_output_audit_status=point.get("native_output_audit_status"),
                native_scf_status=point.get("native_scf_status"),
                native_scf_last_iteration=point.get("native_scf_last_iteration"),
                native_scf_maximum_cycles_reached=point.get(
                    "native_scf_maximum_cycles_reached"
                ),
                receipt_sha256=point.get("receipt_sha256"),
                simulation_sha256=point.get("simulation_sha256"),
                output_report_sha256=point.get("output_report_sha256"),
            )
        )
    for item in series_values:
        for delta in item.get("deltas", []) or []:
            if not isinstance(delta, dict):
                continue
            rows.append(
                row(
                    "series_delta",
                    series_id=item.get("series_id"),
                    series_status=item.get("status"),
                    axis=item.get("axis"),
                    axis_mode=item.get("axis_mode"),
                    refinement_direction=item.get("refinement_direction"),
                    series_point_count=item.get("point_count"),
                    sequence_evidence_sufficient=item.get(
                        "sequence_evidence_sufficient"
                    ),
                    latest_pair_within_tolerance=item.get(
                        "latest_pair_within_tolerance"
                    ),
                    coarse_revision=delta.get("coarse_revision"),
                    fine_revision=delta.get("fine_revision"),
                    coarse_axis_value=json.dumps(
                        delta.get("coarse_axis_value"), separators=(",", ":")
                    ),
                    fine_axis_value=json.dumps(
                        delta.get("fine_axis_value"), separators=(",", ":")
                    ),
                    refinement_verified=delta.get("refinement_verified"),
                    total_energy_delta_ev_per_atom=delta.get(
                        "total_energy_delta_ev_per_atom"
                    ),
                    energy_within_tolerance=delta.get(
                        "energy_within_tolerance"
                    ),
                    band_gap_delta_ev=delta.get("band_gap_delta_ev"),
                    band_gap_within_tolerance=delta.get(
                        "band_gap_within_tolerance"
                    ),
                    available_metric_count=delta.get("available_metric_count"),
                    all_available_metrics_within_tolerance=delta.get(
                        "all_available_metrics_within_tolerance"
                    ),
                )
            )
    for error in audit.get("binding_errors", []) or []:
        if not isinstance(error, dict):
            continue
        rows.append(
            row(
                "binding_error",
                binding_error_history_index=error.get("history_index"),
                binding_error_target_revision=error.get("target_revision"),
                binding_error_reason=error.get("reason"),
                binding_error_detail=error.get("detail"),
            )
        )
    return rows


def _semiconductor_commensurate_twist_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    entries = summary.get("entries", []) or []
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    rows = []
    for index, entry in enumerate(entries, start=1):
        is_latest = index == len(entries) and entry == latest
        rows.append(
            {
                "index": index,
                "is_latest": is_latest,
                "commensurate_m": entry.get("commensurate_m"),
                "commensurate_n": entry.get("commensurate_n"),
                "supercell_index": entry.get("supercell_index"),
                "bottom_supercell_matrix": json.dumps(
                    entry.get("bottom_supercell_matrix"),
                    separators=(",", ":"),
                ),
                "top_supercell_matrix": json.dumps(
                    entry.get("top_supercell_matrix"),
                    separators=(",", ":"),
                ),
                "twist_orientation": entry.get("twist_orientation"),
                "twist_angle_degrees": entry.get("twist_angle_degrees"),
                "requested_twist_angle_degrees": entry.get("requested_twist_angle_degrees"),
                "twist_angle_error_degrees": entry.get("twist_angle_error_degrees"),
                "common_lattice_a_angstrom": entry.get("common_lattice_a_angstrom"),
                "common_lattice_b_angstrom": entry.get("common_lattice_b_angstrom"),
                "common_lattice_gamma_degrees": entry.get("common_lattice_gamma_degrees"),
                "interlayer_distance_angstrom": entry.get("interlayer_distance_angstrom"),
                "monolayer_thickness_angstrom": entry.get("monolayer_thickness_angstrom"),
                "interlayer_chalcogen_gap_angstrom": entry.get("interlayer_chalcogen_gap_angstrom"),
                "total_slab_thickness_angstrom": entry.get("total_slab_thickness_angstrom"),
                "vacuum_angstrom": entry.get("vacuum_angstrom"),
                "atom_count": entry.get("atom_count"),
                "atoms_per_layer": entry.get("atoms_per_layer"),
                "bottom_layer_atom_id_sha256": entry.get("bottom_layer_atom_id_sha256"),
                "top_layer_atom_id_sha256": entry.get("top_layer_atom_id_sha256"),
                "structure_sha256": entry.get("structure_sha256"),
                "indices_valid": summary.get("indices_valid") if is_latest else None,
                "supercell_index_verified": (
                    summary.get("supercell_index_verified") if is_latest else None
                ),
                "matrix_pattern_verified": (
                    summary.get("matrix_pattern_verified") if is_latest else None
                ),
                "matrix_determinant_verified": (
                    summary.get("matrix_determinant_verified") if is_latest else None
                ),
                "angle_verified": summary.get("angle_verified") if is_latest else None,
                "lattice_verified": summary.get("lattice_verified") if is_latest else None,
                "layer_counts_verified": (
                    summary.get("layer_counts_verified") if is_latest else None
                ),
                "layer_atom_ids_verified": (
                    summary.get("layer_atom_ids_verified") if is_latest else None
                ),
                "interlayer_distance_verified": (
                    summary.get("interlayer_distance_verified") if is_latest else None
                ),
                "interlayer_gap_verified": (
                    summary.get("interlayer_gap_verified") if is_latest else None
                ),
                "geometry_measurement_binding_verified": (
                    summary.get("geometry_measurement_binding_verified")
                    if is_latest
                    else None
                ),
                "construction_structure_binding_matches_current": (
                    summary.get("construction_structure_binding_matches_current")
                    if is_latest
                    else None
                ),
                "structure_binding_matches_current": (
                    summary.get("structure_binding_matches_current") if is_latest else None
                ),
                "structure_binding_scope": (
                    summary.get("structure_binding_scope") if is_latest else None
                ),
                "castep_relaxation_transition_verified": (
                    summary.get("castep_relaxation_transition_verified")
                    if is_latest
                    else None
                ),
                "current_structure_sha256": (
                    summary.get("current_structure_sha256") if is_latest else None
                ),
                "metadata_consistent": summary.get("metadata_consistent") if is_latest else None,
                "commensurability_verified": (
                    summary.get("commensurability_verified") if is_latest else None
                ),
                "requires_geometry_relaxation": (
                    summary.get("requires_geometry_relaxation") if is_latest else None
                ),
                "geometry_relaxed": summary.get("geometry_relaxed") if is_latest else None,
                "calculation_ready": summary.get("calculation_ready") if is_latest else None,
                "quality": summary.get("quality") if is_latest else None,
                "warning_count": summary.get("warning_count") if is_latest else None,
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_commensurate_heterobilayer_csv_rows(
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    entries = summary.get("entries", []) or []
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    rows = []
    for index, entry in enumerate(entries, start=1):
        is_latest = index == len(entries) and entry == latest
        rows.append(
            {
                "index": index,
                "is_latest": is_latest,
                "bottom_material": entry.get("bottom_material"),
                "top_material": entry.get("top_material"),
                "commensurate_m": entry.get("commensurate_m"),
                "commensurate_n": entry.get("commensurate_n"),
                "supercell_index": entry.get("supercell_index"),
                "bottom_supercell_matrix": json.dumps(
                    entry.get("bottom_supercell_matrix"), separators=(",", ":")
                ),
                "top_supercell_matrix": json.dumps(
                    entry.get("top_supercell_matrix"), separators=(",", ":")
                ),
                "twist_orientation": entry.get("twist_orientation"),
                "twist_angle_degrees": entry.get("twist_angle_degrees"),
                "requested_twist_angle_degrees": entry.get("requested_twist_angle_degrees"),
                "twist_angle_error_degrees": entry.get("twist_angle_error_degrees"),
                "bottom_primitive_lattice_a_angstrom": entry.get(
                    "bottom_primitive_lattice_a_angstrom"
                ),
                "top_primitive_lattice_a_angstrom": entry.get(
                    "top_primitive_lattice_a_angstrom"
                ),
                "unstrained_lattice_mismatch_percent": entry.get(
                    "unstrained_lattice_mismatch_percent"
                ),
                "strain_policy": entry.get("strain_policy"),
                "common_primitive_lattice_a_angstrom": entry.get(
                    "common_primitive_lattice_a_angstrom"
                ),
                "bottom_biaxial_strain_percent": entry.get("bottom_biaxial_strain_percent"),
                "top_biaxial_strain_percent": entry.get("top_biaxial_strain_percent"),
                "max_abs_biaxial_strain_percent": entry.get(
                    "max_abs_biaxial_strain_percent"
                ),
                "max_strain_percent": entry.get("max_strain_percent"),
                "common_lattice_a_angstrom": entry.get("common_lattice_a_angstrom"),
                "common_lattice_b_angstrom": entry.get("common_lattice_b_angstrom"),
                "interlayer_distance_angstrom": entry.get("interlayer_distance_angstrom"),
                "bottom_monolayer_thickness_angstrom": entry.get(
                    "bottom_monolayer_thickness_angstrom"
                ),
                "top_monolayer_thickness_angstrom": entry.get(
                    "top_monolayer_thickness_angstrom"
                ),
                "interlayer_chalcogen_gap_angstrom": entry.get(
                    "interlayer_chalcogen_gap_angstrom"
                ),
                "vacuum_angstrom": entry.get("vacuum_angstrom"),
                "atom_count": entry.get("atom_count"),
                "bottom_layer_element_counts": json.dumps(
                    summary.get("bottom_layer_element_counts") if is_latest else None,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "top_layer_element_counts": json.dumps(
                    summary.get("top_layer_element_counts") if is_latest else None,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "layer_materials_verified": (
                    summary.get("layer_materials_verified") if is_latest else None
                ),
                "strain_partition_verified": (
                    summary.get("strain_partition_verified") if is_latest else None
                ),
                "strain_within_limit": summary.get("strain_within_limit") if is_latest else None,
                "matrix_determinant_verified": (
                    summary.get("matrix_determinant_verified") if is_latest else None
                ),
                "angle_verified": summary.get("angle_verified") if is_latest else None,
                "lattice_verified": summary.get("lattice_verified") if is_latest else None,
                "interlayer_distance_verified": (
                    summary.get("interlayer_distance_verified") if is_latest else None
                ),
                "interlayer_gap_verified": (
                    summary.get("interlayer_gap_verified") if is_latest else None
                ),
                "structure_sha256": entry.get("structure_sha256"),
                "current_structure_sha256": (
                    summary.get("current_structure_sha256") if is_latest else None
                ),
                "construction_structure_binding_matches_current": (
                    summary.get("construction_structure_binding_matches_current")
                    if is_latest
                    else None
                ),
                "structure_binding_matches_current": (
                    summary.get("structure_binding_matches_current") if is_latest else None
                ),
                "structure_binding_scope": (
                    summary.get("structure_binding_scope") if is_latest else None
                ),
                "castep_relaxation_transition_verified": (
                    summary.get("castep_relaxation_transition_verified")
                    if is_latest
                    else None
                ),
                "metadata_consistent": summary.get("metadata_consistent") if is_latest else None,
                "commensurability_verified": (
                    summary.get("commensurability_verified") if is_latest else None
                ),
                "requires_geometry_relaxation": (
                    summary.get("requires_geometry_relaxation") if is_latest else None
                ),
                "geometry_relaxed": summary.get("geometry_relaxed") if is_latest else None,
                "calculation_ready": summary.get("calculation_ready") if is_latest else None,
                "quality": summary.get("quality") if is_latest else None,
                "warning_count": summary.get("warning_count") if is_latest else None,
                "source": entry.get("source"),
            }
        )
    return rows


def _semiconductor_2d_electrostatic_csv_row(summary: dict[str, Any]) -> dict[str, Any]:
    row = dict(summary)
    for key in (
        "bottom_surface_element_counts",
        "top_surface_element_counts",
        "bottom_layer_element_counts",
        "top_layer_element_counts",
        "calculation_blocking_reasons",
    ):
        row[key] = json.dumps(
            summary.get(key) or ({} if key.endswith("element_counts") else []),
            sort_keys=True,
            separators=(",", ":"),
        )
    return row


def _semiconductor_interface_profile_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for layer in summary.get("layers", []) or []:
        rows.append(
            {
                "layer_index": layer.get("layer_index"),
                "axis": summary.get("axis"),
                "fractional_center": layer.get("fractional_center"),
                "axis_coordinate_angstrom": layer.get("axis_coordinate_angstrom"),
                "layer_role": layer.get("layer_role"),
                "material_marker": layer.get("material_marker"),
                "segment_index": layer.get("segment_index"),
                "boundary_before_layer": layer.get("boundary_before_layer"),
                "boundary_after_layer": layer.get("boundary_after_layer"),
                "mixed_layer": layer.get("mixed_layer"),
                "non_passivant_elements": _join_vector(layer.get("non_passivant_elements")),
                "element_signature": layer.get("element_signature"),
                "atom_count": layer.get("atom_count"),
                "atom_ids": _join_vector(layer.get("atom_ids")),
            }
        )
    return rows


def _semiconductor_interface_scaffold_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "available": summary.get("available"),
            "status": summary.get("status"),
            "interface": summary.get("interface"),
            "substrate_material": summary.get("substrate_material"),
            "film_material": summary.get("film_material"),
            "interface_orientation": summary.get("interface_orientation"),
            "axis": summary.get("axis"),
            "common_in_plane_lattice_angstrom": summary.get("common_in_plane_lattice_angstrom"),
            "film_in_plane_strain_percent": summary.get("film_in_plane_strain_percent"),
            "interface_gap_angstrom": summary.get("interface_gap_angstrom"),
            "substrate_thickness_angstrom": summary.get("substrate_thickness_angstrom"),
            "film_thickness_angstrom": summary.get("film_thickness_angstrom"),
            "slab_thickness_angstrom": summary.get("slab_thickness_angstrom"),
            "vacuum_angstrom": summary.get("vacuum_angstrom"),
            "bottom_vacuum_angstrom": summary.get("bottom_vacuum_angstrom"),
            "top_vacuum_angstrom": summary.get("top_vacuum_angstrom"),
            "slab_vacuum_status": summary.get("slab_vacuum_status"),
            "slab_centered_in_cell": summary.get("slab_centered_in_cell"),
            "slab_vacuum_ok": summary.get("slab_vacuum_ok"),
            "layer_count": summary.get("layer_count"),
            "min_interlayer_spacing_angstrom": summary.get("min_interlayer_spacing_angstrom"),
            "layer_spacing_warning": summary.get("layer_spacing_warning"),
            "requires_geometry_relaxation": summary.get("requires_geometry_relaxation"),
            "visual_hotload_ready": summary.get("visual_hotload_ready"),
            "calculation_ready": summary.get("calculation_ready"),
            "warning_count": summary.get("warning_count"),
            "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
            "next_action": summary.get("next_action"),
        }
    ]


def _semiconductor_interface_quality_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    material_sequence = _join_vector(summary.get("material_sequence"))
    expected_material_sequence = _join_vector(summary.get("expected_material_sequence"))
    missing_materials = _join_vector(summary.get("missing_declared_materials"))
    warnings = ";".join(str(value) for value in summary.get("warnings", []) or [])
    for segment in summary.get("segments", []) or []:
        rows.append(
            {
                "segment_index": segment.get("segment_index"),
                "period_index": segment.get("period_index"),
                "segment_in_period": segment.get("segment_in_period"),
                "axis": summary.get("axis"),
                "material": segment.get("material"),
                "role": segment.get("role"),
                "expected_material": segment.get("expected_material"),
                "matches_expected_material": segment.get("matches_expected_material"),
                "first_layer_index": segment.get("first_layer_index"),
                "last_layer_index": segment.get("last_layer_index"),
                "layer_count": segment.get("layer_count"),
                "mixed_layer_count": segment.get("mixed_layer_count"),
                "material_sequence": material_sequence,
                "expected_material_sequence": expected_material_sequence,
                "period_count": summary.get("period_count"),
                "material_segment_count": summary.get("material_segment_count"),
                "linear_interface_transition_count": summary.get("linear_interface_transition_count"),
                "periodic_interface_transition_count": summary.get("periodic_interface_transition_count"),
                "expected_segment_count_from_periods": summary.get("expected_segment_count_from_periods"),
                "segment_count_matches_periods": summary.get("segment_count_matches_periods"),
                "period_sequence_complete": summary.get("period_sequence_complete"),
                "transition_sequence_complete": summary.get("transition_sequence_complete"),
                "declared_materials_present": summary.get("declared_materials_present"),
                "missing_declared_materials": missing_materials,
                "quality": summary.get("quality"),
                "warning_count": summary.get("warning_count"),
                "warnings": warnings,
            }
        )
    if rows:
        return rows
    return [
        {
            "segment_index": None,
            "period_index": None,
            "segment_in_period": None,
            "axis": summary.get("axis"),
            "material": None,
            "role": None,
            "expected_material": None,
            "matches_expected_material": None,
            "first_layer_index": None,
            "last_layer_index": None,
            "layer_count": None,
            "mixed_layer_count": None,
            "material_sequence": material_sequence,
            "expected_material_sequence": expected_material_sequence,
            "period_count": summary.get("period_count"),
            "material_segment_count": summary.get("material_segment_count"),
            "linear_interface_transition_count": summary.get("linear_interface_transition_count"),
            "periodic_interface_transition_count": summary.get("periodic_interface_transition_count"),
            "expected_segment_count_from_periods": summary.get("expected_segment_count_from_periods"),
            "segment_count_matches_periods": summary.get("segment_count_matches_periods"),
            "period_sequence_complete": summary.get("period_sequence_complete"),
            "transition_sequence_complete": summary.get("transition_sequence_complete"),
            "declared_materials_present": summary.get("declared_materials_present"),
            "missing_declared_materials": missing_materials,
            "quality": summary.get("quality"),
            "warning_count": summary.get("warning_count"),
            "warnings": warnings,
        }
    ]


def _semiconductor_oxide_interface_geometry_csv_rows(
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    common = {
        "interface": summary.get("interface"),
        "axis": summary.get("axis"),
        "semiconductor_material": summary.get("semiconductor_material"),
        "oxide_material": summary.get("oxide_material"),
        "status": summary.get("status"),
        "quality": summary.get("quality"),
        "atom_binding_complete": summary.get("atom_binding_complete"),
        "interface_spacing_definition": summary.get("interface_spacing_definition"),
        "interface_spacing_tolerance_angstrom": summary.get(
            "interface_spacing_tolerance_angstrom"
        ),
        "interface_spacing_count": summary.get("interface_spacing_count"),
        "interface_spacing_declared_count": summary.get(
            "interface_spacing_declared_count"
        ),
        "interface_spacing_binding_review_count": summary.get(
            "interface_spacing_binding_review_count"
        ),
        "interface_spacing_mismatch_count": summary.get(
            "interface_spacing_mismatch_count"
        ),
        "interface_spacing_all_declared": summary.get(
            "interface_spacing_all_declared"
        ),
        "interface_spacing_declared_values_match": summary.get(
            "interface_spacing_declared_values_match"
        ),
        "boundary_candidate_pair_count": summary.get("boundary_candidate_pair_count"),
        "boundary_neighbor_pair_count": summary.get("boundary_neighbor_pair_count"),
        "boundary_connected_within_neighbor_cutoff": summary.get(
            "boundary_connected_within_neighbor_cutoff"
        ),
        "oxide_internal_neighbor_pair_count": summary.get(
            "oxide_internal_neighbor_pair_count"
        ),
        "short_contact_review_threshold_fraction": summary.get(
            "short_contact_review_threshold_fraction"
        ),
        "short_contact_count": summary.get("short_contact_count"),
        "isolated_oxide_atom_count": summary.get("isolated_oxide_atom_count"),
        "geometry_preflight_ready": summary.get("geometry_preflight_ready"),
        "calculation_geometry_ready": summary.get("calculation_geometry_ready"),
        "normality_reason_codes": _join_vector(summary.get("normality_reason_codes")),
        "next_action": summary.get("next_action"),
        "warning_count": summary.get("warning_count"),
        "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
    }
    pair_stats = summary.get("boundary_pair_distance_stats_angstrom") or {}
    neighbor_stats = summary.get("boundary_neighbor_distance_stats_angstrom") or {}
    rows: list[dict[str, Any]] = [
        {
            **common,
            "row_kind": "summary",
            "semiconductor_boundary_layer_index": summary.get(
                "semiconductor_boundary_layer_index"
            ),
            "oxide_boundary_layer_index": summary.get("oxide_boundary_layer_index"),
            "semiconductor_boundary_atom_count": summary.get(
                "semiconductor_boundary_atom_count"
            ),
            "oxide_boundary_atom_count": summary.get("oxide_boundary_atom_count"),
            "oxide_atom_count": summary.get("oxide_atom_count"),
            "oxide_atom_neighbor_coverage_count": summary.get(
                "oxide_atom_neighbor_coverage_count"
            ),
            "oxide_oxygen_atom_count": summary.get("oxide_oxygen_atom_count"),
            "oxide_oxygen_with_cation_neighbor_count": summary.get(
                "oxide_oxygen_with_cation_neighbor_count"
            ),
            "oxide_cation_atom_count": summary.get("oxide_cation_atom_count"),
            "oxide_cations_with_oxygen_neighbor_count": summary.get(
                "oxide_cations_with_oxygen_neighbor_count"
            ),
            "boundary_pair_distance_min_angstrom": pair_stats.get("min"),
            "boundary_pair_distance_mean_angstrom": pair_stats.get("mean"),
            "boundary_pair_distance_max_angstrom": pair_stats.get("max"),
            "boundary_neighbor_distance_min_angstrom": neighbor_stats.get("min"),
            "boundary_neighbor_distance_mean_angstrom": neighbor_stats.get("mean"),
            "boundary_neighbor_distance_max_angstrom": neighbor_stats.get("max"),
        }
    ]
    for spacing in summary.get("interface_spacings", []) or []:
        rows.append(
            {
                **common,
                "row_kind": "interface_spacing",
                "target_interface": spacing.get("target_interface"),
                "spacing_binding_status": spacing.get("binding_status"),
                "interface_spacing_status": spacing.get("status"),
                "expected_materials": _join_vector(
                    spacing.get("expected_materials")
                ),
                "lower_material": spacing.get("lower_material"),
                "upper_material": spacing.get("upper_material"),
                "lower_layer_index": spacing.get("lower_layer_index"),
                "upper_layer_index": spacing.get("upper_layer_index"),
                "lower_axis_coordinate_angstrom": spacing.get(
                    "lower_axis_coordinate_angstrom"
                ),
                "upper_axis_coordinate_angstrom": spacing.get(
                    "upper_axis_coordinate_angstrom"
                ),
                "actual_gap_angstrom": spacing.get("actual_gap_angstrom"),
                "declared_gap_angstrom": spacing.get("declared_gap_angstrom"),
                "declared_gap_source": spacing.get("declared_gap_source"),
                "declared_gap_status": spacing.get("declared_gap_status"),
                "actual_minus_declared_angstrom": spacing.get(
                    "actual_minus_declared_angstrom"
                ),
                "matches_declared_gap": spacing.get("matches_declared_gap"),
                "transition_match_count": spacing.get("transition_match_count"),
                "patch_operation": (
                    json.dumps(
                        spacing.get("patch_operation"),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if spacing.get("patch_operation")
                    else None
                ),
            }
        )
    for pair in summary.get("boundary_pairs", []) or []:
        offset = pair.get("image_offset_oxide_atom") or [None, None, None]
        rows.append(
            {
                **common,
                "row_kind": "boundary_pair",
                "pair_scope": pair.get("pair_scope"),
                "atom1_id": pair.get("semiconductor_atom_id"),
                "element1": pair.get("semiconductor_element"),
                "atom2_id": pair.get("oxide_atom_id"),
                "element2": pair.get("oxide_element"),
                "semiconductor_atom_id": pair.get("semiconductor_atom_id"),
                "semiconductor_element": pair.get("semiconductor_element"),
                "oxide_atom_id": pair.get("oxide_atom_id"),
                "oxide_element": pair.get("oxide_element"),
                "pair_type": pair.get("pair_type"),
                "distance_angstrom": pair.get("distance_angstrom"),
                "neighbor_threshold_angstrom": pair.get("neighbor_threshold_angstrom"),
                "distance_to_threshold_fraction": pair.get(
                    "distance_to_threshold_fraction"
                ),
                "within_neighbor_cutoff": pair.get("within_neighbor_cutoff"),
                "short_contact_review": pair.get("short_contact_review"),
                "image_offset_a": offset[0] if len(offset) > 0 else None,
                "image_offset_b": offset[1] if len(offset) > 1 else None,
                "image_offset_c": offset[2] if len(offset) > 2 else None,
            }
        )
    for atom in summary.get("oxide_atoms", []) or []:
        rows.append(
            {
                **common,
                "row_kind": "oxide_atom",
                "oxide_atom_id": atom.get("atom_id"),
                "oxide_element": atom.get("element"),
                "oxide_atom_layer_index": atom.get("layer_index"),
                "global_neighbor_count": atom.get("global_neighbor_count"),
                "oxide_internal_neighbor_count": atom.get(
                    "oxide_internal_neighbor_count"
                ),
                "oxide_internal_unique_neighbor_count": atom.get(
                    "oxide_internal_unique_neighbor_count"
                ),
                "oxide_internal_neighbor_ids": _join_vector(
                    atom.get("oxide_internal_neighbor_ids")
                ),
                "oxide_internal_neighbor_elements": _join_vector(
                    atom.get("oxide_internal_neighbor_elements")
                ),
                "semiconductor_boundary_neighbor_count": atom.get(
                    "semiconductor_boundary_neighbor_count"
                ),
                "semiconductor_boundary_neighbor_ids": _join_vector(
                    atom.get("semiconductor_boundary_neighbor_ids")
                ),
                "oxygen_cation_neighbor_count": atom.get(
                    "oxygen_cation_neighbor_count"
                ),
                "cation_oxygen_neighbor_count": atom.get(
                    "cation_oxygen_neighbor_count"
                ),
                "nearest_relevant_neighbor_distance_angstrom": atom.get(
                    "nearest_relevant_neighbor_distance_angstrom"
                ),
                "isolated_from_oxide_and_semiconductor_boundary": atom.get(
                    "isolated_from_oxide_and_semiconductor_boundary"
                ),
            }
        )
    for contact in summary.get("short_contacts", []) or []:
        if contact.get("pair_scope") != "oxide_internal":
            continue
        offset = contact.get("image_offset_atom2") or [None, None, None]
        rows.append(
            {
                **common,
                "row_kind": "oxide_internal_short_contact",
                "pair_scope": contact.get("pair_scope"),
                "atom1_id": contact.get("atom1"),
                "element1": contact.get("element1"),
                "atom2_id": contact.get("atom2"),
                "element2": contact.get("element2"),
                "pair_type": contact.get("pair_type"),
                "distance_angstrom": contact.get("distance_angstrom"),
                "neighbor_threshold_angstrom": contact.get(
                    "neighbor_threshold_angstrom"
                ),
                "distance_to_threshold_fraction": contact.get(
                    "distance_to_threshold_fraction"
                ),
                "within_neighbor_cutoff": contact.get("within_neighbor_cutoff"),
                "short_contact_review": contact.get("short_contact_review"),
                "image_offset_a": offset[0] if len(offset) > 0 else None,
                "image_offset_b": offset[1] if len(offset) > 1 else None,
                "image_offset_c": offset[2] if len(offset) > 2 else None,
            }
        )
    return rows


def _semiconductor_oxide_interface_health_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    boundary = summary.get("semiconductor_oxide_boundary") or {}
    common = {
        "interface": summary.get("interface"),
        "axis": summary.get("axis"),
        "semiconductor_material": summary.get("semiconductor_material"),
        "oxide_material": summary.get("oxide_material"),
        "metal_gate_present": summary.get("metal_gate_present"),
        "material_sequence": _join_vector(summary.get("material_sequence")),
        "sequence_matches_expected": summary.get("sequence_matches_expected"),
        "status": summary.get("status"),
        "quality": summary.get("quality"),
        "layer_profile_complete": summary.get("layer_profile_complete"),
        "oxide_layer_count": summary.get("oxide_layer_count"),
        "oxide_cation_elements": _join_vector(summary.get("oxide_cation_elements")),
        "expected_oxygen_per_cation_ratio": summary.get("expected_oxygen_per_cation_ratio"),
        "oxygen_deficit_binding_status": summary.get("oxygen_deficit_binding_status"),
        "oxygen_deficit_explained_by_recorded_vacancies": summary.get(
            "oxygen_deficit_explained_by_recorded_vacancies"
        ),
        "recorded_oxygen_vacancy_count": summary.get("recorded_oxygen_vacancy_count"),
        "recorded_oxygen_vacancy_site_ids": _join_vector(
            summary.get("recorded_oxygen_vacancy_site_ids")
        ),
        "all_recorded_oxygen_vacancy_locations_verified": summary.get(
            "all_recorded_oxygen_vacancy_locations_verified"
        ),
        "semiconductor_oxide_boundary_angstrom": boundary.get("axis_coordinate_angstrom"),
        "geometry_preflight_status": summary.get("geometry_preflight_status"),
        "geometry_preflight_quality": summary.get("geometry_preflight_quality"),
        "geometry_preflight_ready": summary.get("geometry_preflight_ready"),
        "geometry_visualization_ready": summary.get("geometry_visualization_ready"),
        "geometry_boundary_neighbor_pair_count": summary.get(
            "geometry_boundary_neighbor_pair_count"
        ),
        "geometry_interface_spacing_count": summary.get(
            "geometry_interface_spacing_count"
        ),
        "geometry_interface_spacing_mismatch_count": summary.get(
            "geometry_interface_spacing_mismatch_count"
        ),
        "geometry_interface_spacing_declared_values_match": summary.get(
            "geometry_interface_spacing_declared_values_match"
        ),
        "geometry_short_contact_count": summary.get("geometry_short_contact_count"),
        "geometry_isolated_oxide_atom_count": summary.get(
            "geometry_isolated_oxide_atom_count"
        ),
        "pre_relaxation_scaffold": summary.get("pre_relaxation_scaffold"),
        "requires_geometry_relaxation": summary.get("requires_geometry_relaxation"),
        "geometry_relaxed": summary.get("geometry_relaxed"),
        "geometry_relaxation_verified": summary.get("geometry_relaxation_verified"),
        "visual_preflight_ready": summary.get("visual_preflight_ready"),
        "calculation_ready": summary.get("calculation_ready"),
        "normality_reason_codes": _join_vector(summary.get("normality_reason_codes")),
        "calculation_blocking_reasons": _join_vector(summary.get("calculation_blocking_reasons")),
        "next_action": summary.get("next_action"),
        "warning_count": summary.get("warning_count"),
        "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
    }
    rows = [
        {
            **common,
            "row_kind": "summary",
            "layer_index": None,
            "fractional_center": None,
            "axis_coordinate_angstrom": None,
            "material_group": None,
            "atom_count": summary.get("oxide_atom_count"),
            "element_counts": json.dumps(
                summary.get("oxide_element_counts") or {},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "atom_ids": None,
            "cation_count": summary.get("oxide_cation_count"),
            "oxygen_count": summary.get("oxygen_count"),
            "oxygen_to_cation_ratio": summary.get("oxygen_to_cation_ratio"),
            "expected_oxygen_count": summary.get("expected_oxygen_count"),
            "oxygen_delta_count": summary.get("oxygen_delta_count"),
            "oxygen_deficit_count": summary.get("oxygen_deficit_count"),
            "oxygen_excess_count": summary.get("oxygen_excess_count"),
            "stoichiometry_status": summary.get("stoichiometry_status"),
        }
    ]
    for layer in summary.get("oxide_layers", []) or []:
        rows.append(
            {
                **common,
                "row_kind": "oxide_layer",
                "layer_index": layer.get("layer_index"),
                "fractional_center": layer.get("fractional_center"),
                "axis_coordinate_angstrom": layer.get("axis_coordinate_angstrom"),
                "material_group": layer.get("material_group"),
                "atom_count": layer.get("atom_count"),
                "element_counts": json.dumps(
                    layer.get("element_counts") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "atom_ids": _join_vector(layer.get("atom_ids")),
                "cation_count": layer.get("cation_count"),
                "oxygen_count": layer.get("oxygen_count"),
                "oxygen_to_cation_ratio": layer.get("oxygen_to_cation_ratio"),
                "expected_oxygen_count": layer.get("expected_oxygen_count"),
                "oxygen_delta_count": layer.get("oxygen_delta_count"),
                "oxygen_deficit_count": layer.get("oxygen_deficit_count"),
                "oxygen_excess_count": layer.get("oxygen_excess_count"),
                "stoichiometry_status": layer.get("stoichiometry_status"),
            }
        )
    for vacancy in summary.get("oxygen_vacancy_locations", []) or []:
        fractional = vacancy.get("fractional") or [None, None, None]
        rows.append(
            {
                **common,
                "row_kind": "oxygen_vacancy",
                "vacancy_site_id": vacancy.get("site_id"),
                "vacancy_fractional_a": fractional[0] if len(fractional) > 0 else None,
                "vacancy_fractional_b": fractional[1] if len(fractional) > 1 else None,
                "vacancy_fractional_c": fractional[2] if len(fractional) > 2 else None,
                "vacancy_axis_coordinate_angstrom": vacancy.get("axis_coordinate_angstrom"),
                "vacancy_region": vacancy.get("region"),
                "vacancy_nearest_layer_index": vacancy.get("nearest_layer_index"),
                "vacancy_nearest_layer_material": vacancy.get("nearest_layer_material"),
                "vacancy_nearest_layer_delta_angstrom": vacancy.get("nearest_layer_delta_angstrom"),
                "vacancy_distance_to_boundary_angstrom": vacancy.get(
                    "distance_to_semiconductor_oxide_boundary_angstrom"
                ),
                "vacancy_interface_proximal": vacancy.get("interface_proximal"),
                "vacancy_position_verified": vacancy.get("position_verified"),
                "vacancy_auto_selected_site": vacancy.get("auto_selected_site"),
            }
        )
    return rows


def _semiconductor_gate_stack_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    expected_sequence = _join_vector(summary.get("expected_stack_sequence"))
    material_sequence = _join_vector(summary.get("material_sequence"))
    warnings = ";".join(str(value) for value in summary.get("warnings", []) or [])
    for segment in summary.get("segments", []) or []:
        rows.append(
            {
                "segment_index": segment.get("segment_index"),
                "axis": summary.get("axis"),
                "interface": summary.get("interface"),
                "material": segment.get("material"),
                "role": segment.get("role"),
                "expected_stack_sequence": expected_sequence,
                "material_sequence": material_sequence,
                "sequence_matches_expected": summary.get("sequence_matches_expected"),
                "quality": summary.get("quality"),
                "gate_material": summary.get("gate_material"),
                "gate_oxide_material": summary.get("gate_oxide_material"),
                "semiconductor_channel_material": summary.get("semiconductor_channel_material"),
                "first_layer_index": segment.get("first_layer_index"),
                "last_layer_index": segment.get("last_layer_index"),
                "layer_count": segment.get("layer_count"),
                "mixed_layer_count": segment.get("mixed_layer_count"),
                "fractional_center_start": segment.get("fractional_center_start"),
                "fractional_center_end": segment.get("fractional_center_end"),
                "axis_center_start_angstrom": segment.get("axis_center_start_angstrom"),
                "axis_center_end_angstrom": segment.get("axis_center_end_angstrom"),
                "center_span_angstrom": segment.get("center_span_angstrom"),
                "declared_oxide_thickness_angstrom": summary.get("declared_oxide_thickness_angstrom"),
                "declared_gate_thickness_angstrom": summary.get("declared_gate_thickness_angstrom"),
                "declared_channel_thickness_angstrom": summary.get("declared_channel_thickness_angstrom"),
                "declared_vacuum_angstrom": summary.get("declared_vacuum_angstrom"),
                "atom_count": segment.get("atom_count"),
                "element_counts": json.dumps(segment.get("element_counts") or {}, ensure_ascii=False, sort_keys=True),
                "atom_ids": _join_vector(segment.get("atom_ids")),
                "warnings": warnings,
            }
        )
    return rows


def _semiconductor_contact_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    expected_sequence = _join_vector(summary.get("expected_contact_sequence"))
    material_sequence = _join_vector(summary.get("material_sequence"))
    warnings = ";".join(str(value) for value in summary.get("warnings", []) or [])
    barrier = summary.get("barrier_preflight") or {}
    barrier_warnings = ";".join(str(value) for value in barrier.get("warnings", []) or [])
    for segment in summary.get("segments", []) or []:
        rows.append(
            {
                "segment_index": segment.get("segment_index"),
                "axis": summary.get("axis"),
                "interface": summary.get("interface"),
                "contact_type": summary.get("contact_type"),
                "material": segment.get("material"),
                "role": segment.get("role"),
                "expected_contact_sequence": expected_sequence,
                "material_sequence": material_sequence,
                "sequence_matches_expected": summary.get("sequence_matches_expected"),
                "quality": summary.get("quality"),
                "metal_material": summary.get("metal_material"),
                "semiconductor_material": summary.get("semiconductor_material"),
                "first_layer_index": segment.get("first_layer_index"),
                "last_layer_index": segment.get("last_layer_index"),
                "layer_count": segment.get("layer_count"),
                "fractional_center_start": segment.get("fractional_center_start"),
                "fractional_center_end": segment.get("fractional_center_end"),
                "axis_center_start_angstrom": segment.get("axis_center_start_angstrom"),
                "axis_center_end_angstrom": segment.get("axis_center_end_angstrom"),
                "center_span_angstrom": segment.get("center_span_angstrom"),
                "declared_contact_gap_angstrom": summary.get("declared_contact_gap_angstrom"),
                "actual_contact_gap_angstrom": summary.get("actual_contact_gap_angstrom"),
                "contact_gap_delta_angstrom": summary.get("contact_gap_delta_angstrom"),
                "contact_geometry_status": summary.get("contact_geometry_status"),
                "contact_geometry_next_action": summary.get("contact_geometry_next_action"),
                "declared_metal_thickness_angstrom": summary.get("declared_metal_thickness_angstrom"),
                "actual_metal_thickness_angstrom": summary.get("actual_metal_thickness_angstrom"),
                "metal_thickness_delta_angstrom": summary.get("metal_thickness_delta_angstrom"),
                "declared_semiconductor_thickness_angstrom": summary.get("declared_semiconductor_thickness_angstrom"),
                "barrier_model": barrier.get("model"),
                "metal_work_function_ev": barrier.get("metal_work_function_ev"),
                "semiconductor_electron_affinity_ev": barrier.get("semiconductor_electron_affinity_ev"),
                "semiconductor_band_gap_ev": barrier.get("semiconductor_band_gap_ev"),
                "ideal_n_type_barrier_ev": barrier.get("ideal_n_type_barrier_ev"),
                "ideal_p_type_barrier_ev": barrier.get("ideal_p_type_barrier_ev"),
                "barrier_warning_count": barrier.get("warning_count"),
                "barrier_warnings": barrier_warnings,
                "atom_count": segment.get("atom_count"),
                "element_counts": json.dumps(segment.get("element_counts") or {}, ensure_ascii=False, sort_keys=True),
                "atom_ids": _join_vector(segment.get("atom_ids")),
                "warnings": warnings,
            }
        )
    return rows


def _semiconductor_quantum_well_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for segment in summary.get("segments", []) or []:
        rows.append(
            {
                "segment_index": segment.get("segment_index"),
                "period_index": segment.get("period_index"),
                "segment_in_period": segment.get("segment_in_period"),
                "axis": summary.get("axis"),
                "material": segment.get("material"),
                "material_marker": segment.get("material_marker"),
                "role": segment.get("role"),
                "requested_well_layer_count": summary.get("requested_well_layer_count"),
                "requested_barrier_layer_count": summary.get("requested_barrier_layer_count"),
                "requested_well_thickness_angstrom": summary.get("requested_well_thickness_angstrom"),
                "requested_barrier_thickness_angstrom": summary.get("requested_barrier_thickness_angstrom"),
                "well_thickness_error_angstrom": summary.get("well_thickness_error_angstrom"),
                "barrier_thickness_error_angstrom": summary.get("barrier_thickness_error_angstrom"),
                "first_layer_index": segment.get("first_layer_index"),
                "last_layer_index": segment.get("last_layer_index"),
                "layer_count": segment.get("layer_count"),
                "marker_layer_count": segment.get("marker_layer_count"),
                "atom_count": segment.get("atom_count"),
                "non_passivant_atom_count": segment.get("non_passivant_atom_count"),
                "mixed_layer_count": segment.get("mixed_layer_count"),
                "axis_start_angstrom": segment.get("axis_start_angstrom"),
                "axis_end_angstrom": segment.get("axis_end_angstrom"),
                "thickness_angstrom": segment.get("thickness_angstrom"),
                "fractional_start": segment.get("fractional_start"),
                "fractional_end": segment.get("fractional_end"),
                "wraps_periodic_boundary": segment.get("wraps_periodic_boundary"),
                "element_signatures": _join_vector(segment.get("element_signatures")),
                "element_counts": json.dumps(segment.get("element_counts") or {}, ensure_ascii=False, sort_keys=True),
                "cation_counts": json.dumps(segment.get("cation_counts") or {}, ensure_ascii=False, sort_keys=True),
                "anion_counts": json.dumps(segment.get("anion_counts") or {}, ensure_ascii=False, sort_keys=True),
                "cation_fractions": json.dumps(segment.get("cation_fractions") or {}, ensure_ascii=False, sort_keys=True),
            }
        )
    return rows


def _semiconductor_defect_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for defect in summary.get("defects", []) or []:
        fractional = defect.get("fractional") or [None, None, None]
        rows.append(
            {
                "defect_type": defect.get("type"),
                "site_id": defect.get("site_id"),
                "site_element": defect.get("site_element"),
                "site_family": defect.get("site_family"),
                "original_element": defect.get("original_element"),
                "new_element": defect.get("new_element"),
                "fractional_a": _list_item(fractional, 0),
                "fractional_b": _list_item(fractional, 1),
                "fractional_c": _list_item(fractional, 2),
                "concentration_fraction": defect.get("concentration_fraction"),
                "concentration_percent": defect.get("concentration_percent"),
                "expected_neighbor_count": defect.get("expected_neighbor_count"),
                "nearest_neighbor_count": defect.get("nearest_neighbor_count"),
                "nearest_neighbor_ids": _join_vector(defect.get("nearest_neighbor_ids")),
                "nearest_neighbor_elements": _join_vector(defect.get("nearest_neighbor_elements")),
                "interstitial_neighbor_count": defect.get("interstitial_neighbor_count"),
                "antisite_neighbor_count": defect.get("antisite_neighbor_count"),
                "coordination_outlier": defect.get("coordination_outlier"),
                "same_sublattice_neighbor_count": defect.get("same_sublattice_neighbor_count"),
                "same_sublattice_neighbor_ids": _join_vector(defect.get("same_sublattice_neighbor_ids")),
                "undercoordinated_neighbor_count": defect.get("undercoordinated_neighbor_count"),
                "undercoordinated_neighbor_ids": _join_vector(defect.get("undercoordinated_neighbor_ids")),
                "missing_neighbor_bond_estimate": defect.get("missing_neighbor_bond_estimate"),
                "role_hint": defect.get("role_hint"),
                "carrier_type_hint": defect.get("carrier_type_hint"),
                "auto_selected_site": defect.get("auto_selected_site"),
                "complex_id": defect.get("complex_id"),
                "complex_type": defect.get("complex_type"),
                "pair_site_id": defect.get("pair_site_id"),
                "pair_distance_angstrom": defect.get("pair_distance_angstrom"),
                "nearest_neighbor_verified": defect.get("nearest_neighbor_verified"),
                "source": defect.get("source"),
            }
        )
    return rows


def _semiconductor_defect_complex_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for complex_row in summary.get("complexes", []) or []:
        expected_settings = (
            complex_row.get("expected_castep_charge_spin_settings") or {}
        )
        observed_settings = (
            complex_row.get("observed_castep_charge_spin_settings") or {}
        )
        field_matches = complex_row.get("castep_charge_spin_field_matches") or {}
        rows.append(
            {
                "complex_id": complex_row.get("complex_id"),
                "complex_type": complex_row.get("type"),
                "member_site_ids": _join_vector(complex_row.get("member_site_ids")),
                "member_site_elements": _join_vector(complex_row.get("member_site_elements")),
                "member_count": complex_row.get("member_count"),
                "member_vacancy_record_count": complex_row.get("member_vacancy_record_count"),
                "member_dopant_record_count": complex_row.get("member_dopant_record_count"),
                "substitution_site_id": complex_row.get("substitution_site_id"),
                "substitution_host_element": complex_row.get("substitution_host_element"),
                "substitution_element": complex_row.get("substitution_element"),
                "vacancy_site_id": complex_row.get("vacancy_site_id"),
                "vacancy_site_element": complex_row.get("vacancy_site_element"),
                "pair_distance_angstrom_recorded": complex_row.get("pair_distance_angstrom_recorded"),
                "pair_distance_angstrom_recomputed": complex_row.get("pair_distance_angstrom_recomputed"),
                "distance_delta_angstrom": complex_row.get("distance_delta_angstrom"),
                "nearest_neighbor_threshold_angstrom": complex_row.get("nearest_neighbor_threshold_angstrom"),
                "nearest_neighbor_metadata_claim": complex_row.get("nearest_neighbor_metadata_claim"),
                "nearest_neighbor_recomputed": complex_row.get("nearest_neighbor_recomputed"),
                "nearest_neighbor_verified": complex_row.get("nearest_neighbor_verified"),
                "periodic_minimum_image": complex_row.get("periodic_minimum_image"),
                "image_offset": _join_vector(complex_row.get("image_offset")),
                "selection": complex_row.get("selection"),
                "selection_rule": complex_row.get("selection_rule"),
                "charge_state_label": complex_row.get("charge_state_label"),
                "charge_state_explicit": complex_row.get("charge_state_explicit"),
                "requested_net_charge_e": complex_row.get("requested_net_charge_e"),
                "reference_spin_multiplicity": complex_row.get("reference_spin_multiplicity"),
                "reference_spin_state": complex_row.get("reference_spin_state"),
                "backend_charge_binding_status": complex_row.get("backend_charge_binding_status"),
                "backend_spin_binding_status": complex_row.get("backend_spin_binding_status"),
                "charge_spin_backend_binding_ready": complex_row.get("charge_spin_backend_binding_ready"),
                "expected_castep_total_charge": expected_settings.get("total_charge"),
                "observed_castep_total_charge": observed_settings.get("total_charge"),
                "expected_castep_spin_treatment": expected_settings.get("spin_treatment"),
                "observed_castep_spin_treatment": observed_settings.get("spin_treatment"),
                "expected_castep_use_formal_spin": expected_settings.get("use_formal_spin"),
                "observed_castep_use_formal_spin": observed_settings.get("use_formal_spin"),
                "expected_castep_initial_spin": expected_settings.get("initial_spin"),
                "observed_castep_initial_spin": observed_settings.get("initial_spin"),
                "expected_castep_optimize_total_spin": expected_settings.get(
                    "optimize_total_spin"
                ),
                "observed_castep_optimize_total_spin": observed_settings.get(
                    "optimize_total_spin"
                ),
                "castep_charge_spin_all_fields_match": bool(
                    field_matches and all(field_matches.values())
                ),
                "calculation_execution_ready": complex_row.get("calculation_execution_ready"),
                "structure_hotload_allowed": complex_row.get("structure_hotload_allowed"),
                "state_result_computed": complex_row.get("state_result_computed"),
                "metadata_consistent": complex_row.get("metadata_consistent"),
                "integrity_errors": _join_vector(complex_row.get("integrity_errors")),
                "source": complex_row.get("source"),
            }
        )
    return rows


def _semiconductor_finite_size_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    max_item = summary.get("max_isolated_item") or {}
    return [
        {
            "non_passivant_atom_count": summary.get("non_passivant_atom_count"),
            "min_lattice_length_angstrom": summary.get("min_lattice_length_angstrom"),
            "max_isolated_fraction": summary.get("max_isolated_fraction"),
            "max_isolated_kind": max_item.get("kind"),
            "max_isolated_label": max_item.get("label"),
            "dilute_cell_atom_threshold": summary.get("dilute_cell_atom_threshold"),
            "dilute_fraction_threshold": summary.get("dilute_fraction_threshold"),
            "small_cell_warning": summary.get("small_cell_warning"),
            "high_concentration_warning": summary.get("high_concentration_warning"),
            "finite_size_warning": summary.get("finite_size_warning"),
            "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
        }
    ]


def _semiconductor_heterostructure_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for material in summary.get("materials_detail", []) or []:
        rows.append(
            {
                "interface": summary.get("interface"),
                "interface_orientation": summary.get("interface_orientation"),
                "interface_axis": summary.get("interface_axis"),
                "substrate": summary.get("substrate"),
                "coherent_strain_model": summary.get("coherent_strain_model"),
                "in_plane_lattice_angstrom": summary.get("in_plane_lattice_angstrom"),
                "material": material.get("material"),
                "reference_lattice_angstrom": material.get("reference_lattice_angstrom"),
                "in_plane_strain_percent": material.get("in_plane_strain_percent"),
                "lattice_mismatch_to_substrate_percent": material.get("lattice_mismatch_to_substrate_percent"),
                "is_substrate": material.get("is_substrate"),
                "max_abs_in_plane_strain_percent": summary.get("max_abs_in_plane_strain_percent"),
                "max_abs_lattice_mismatch_to_substrate_percent": summary.get("max_abs_lattice_mismatch_to_substrate_percent"),
                "strain_warning": summary.get("strain_warning"),
            }
        )
    return rows


def _semiconductor_substrate_epitaxy_preflight_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    targets = list(summary.get("targets") or [])
    if not targets:
        targets = [{}]
    for target in targets:
        rows.append(
            {
                "available": summary.get("available"),
                "substrate_material": summary.get("substrate_material"),
                "substrate_orientation": summary.get("substrate_orientation"),
                "requested_target_material": summary.get("requested_target_material"),
                "selected_target_material": summary.get("selected_target_material"),
                "selected_target_found": summary.get("selected_target_found"),
                "target_material": target.get("material"),
                "is_requested_target": target.get("is_requested_target"),
                "film_orientation": target.get("film_orientation"),
                "relationship": target.get("relationship"),
                "in_plane_rotation_deg": target.get("in_plane_rotation_deg"),
                "film_reference_lattice_angstrom": target.get("film_reference_lattice_angstrom"),
                "direct_substrate_spacing_angstrom": target.get("direct_substrate_spacing_angstrom"),
                "direct_mismatch_percent": target.get("direct_mismatch_percent"),
                "direct_mismatch_warning": target.get("direct_mismatch_warning"),
                "domain_film_repeats": target.get("domain_film_repeats"),
                "domain_substrate_repeats": target.get("domain_substrate_repeats"),
                "domain_film_period_angstrom": target.get("domain_film_period_angstrom"),
                "domain_substrate_period_angstrom": target.get("domain_substrate_period_angstrom"),
                "domain_mismatch_percent": target.get("domain_mismatch_percent"),
                "domain_mismatch_warning": target.get("domain_mismatch_warning"),
                "domain_matching_ready": target.get("domain_matching_ready"),
                "buffer_layer_hint": target.get("buffer_layer_hint"),
                "target_next_action": target.get("next_action"),
                "max_abs_direct_mismatch_percent": summary.get("max_abs_direct_mismatch_percent"),
                "max_abs_domain_mismatch_percent": summary.get("max_abs_domain_mismatch_percent"),
                "warning_count": summary.get("warning_count"),
                "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
                "next_action": summary.get("next_action"),
            }
        )
    return rows


def _semiconductor_strain_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, entry in enumerate(summary.get("entries", []) or [], start=1):
        reference = entry.get("reference_lattice") or {}
        strained = entry.get("lattice") or {}
        rows.append(
            {
                "index": index,
                "mode": entry.get("mode"),
                "axes": _join_vector(entry.get("axes")),
                "percent": entry.get("percent"),
                "scale_factor": entry.get("scale_factor"),
                "reference_a": reference.get("a"),
                "reference_b": reference.get("b"),
                "reference_c": reference.get("c"),
                "strained_a": strained.get("a"),
                "strained_b": strained.get("b"),
                "strained_c": strained.get("c"),
                "source": entry.get("source"),
                "max_abs_strain_percent": summary.get("max_abs_strain_percent"),
                "strain_warning": summary.get("strain_warning"),
            }
        )
    return rows


def _semiconductor_surface_model_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    orientation = summary.get("surface_orientation_summary") or {}
    return [
        {
            "available": summary.get("available"),
            "status": summary.get("status"),
            "ready_for_calculation_preflight": summary.get("ready_for_calculation_preflight"),
            "next_action": summary.get("next_action"),
            "slab_vacuum_status": summary.get("slab_vacuum_status"),
            "surface_preparation_status": summary.get("surface_preparation_status"),
            "surface_polarity_status": summary.get("surface_polarity_status"),
            "surface_orientation_status": orientation.get("status"),
            "surface_orientation_basis": orientation.get("orientation_basis"),
            "surface_plane_label": orientation.get("surface_plane_label"),
            "surface_plane_indices": _join_vector(orientation.get("surface_plane_indices")),
            "surface_axis": orientation.get("surface_axis"),
            "surface_axis_cartesian": _join_vector(orientation.get("surface_axis_cartesian")),
            "mapped_surface_normal_cell_axis": orientation.get("mapped_surface_normal_cell_axis"),
            "mapping_axis_matches_surface_axis": orientation.get("mapping_axis_matches_surface_axis"),
            "alignment_applicable": orientation.get("alignment_applicable"),
            "plane_normal_cartesian": _join_vector(orientation.get("plane_normal_cartesian")),
            "plane_spacing_angstrom": orientation.get("plane_spacing_angstrom"),
            "axis_plane_alignment_angle_degrees": orientation.get("axis_plane_alignment_angle_degrees"),
            "axis_plane_alignment_ok": orientation.get("axis_plane_alignment_ok"),
            "orientation_validation_level": orientation.get("validation_level"),
            "orientation_next_action": orientation.get("next_action"),
            "blocking_reasons": ";".join(str(value) for value in summary.get("blocking_reasons", []) or []),
            "review_reasons": ";".join(str(value) for value in summary.get("review_reasons", []) or []),
        }
    ]


def _semiconductor_surface_termination_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for surface, surface_summary in (summary.get("surfaces") or {}).items():
        for atom in surface_summary.get("atoms", []) or []:
            rows.append(
                {
                    "surface": surface,
                    "surface_orientation": summary.get("surface_orientation"),
                    "surface_axis": summary.get("surface_axis"),
                    "termination": summary.get("termination"),
                    "atom_id": atom.get("atom_id"),
                    "element": atom.get("element"),
                    "neighbor_count": atom.get("neighbor_count"),
                    "expected_coordination": atom.get("expected_coordination"),
                    "dangling_bond_estimate": atom.get("dangling_bond_estimate"),
                    "passivant_neighbor_count": atom.get("passivant_neighbor_count"),
                    "neighbor_ids": _join_vector(atom.get("neighbor_ids")),
                    "neighbor_elements": _join_vector(atom.get("neighbor_elements")),
                    "surface_dangling_bond_estimate": surface_summary.get("dangling_bond_estimate"),
                    "surface_passivant_bond_count": surface_summary.get("passivant_bond_count"),
                    "surface_passivation_coverage_fraction": surface_summary.get("passivation_coverage_fraction"),
                    "total_dangling_bond_estimate": summary.get("dangling_bond_estimate"),
                    "total_passivant_bond_count": summary.get("passivant_bond_count"),
                    "total_passivation_coverage_fraction": summary.get("passivation_coverage_fraction"),
                    "fully_passivated": summary.get("fully_passivated"),
                    "surface_preparation_status": summary.get("surface_preparation_status"),
                    "surface_preparation_next_action": summary.get("surface_preparation_next_action"),
                }
            )
    return rows


def _semiconductor_surface_polarity_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    bottom = summary.get("bottom") or {}
    top = summary.get("top") or {}
    return [
        {
            "surface_orientation": summary.get("surface_orientation"),
            "surface_axis": summary.get("surface_axis"),
            "termination": summary.get("termination"),
            "bottom_formula": bottom.get("formula"),
            "top_formula": top.get("formula"),
            "bottom_atom_count": bottom.get("atom_count"),
            "top_atom_count": top.get("atom_count"),
            "bottom_dangling_bond_estimate": bottom.get("dangling_bond_estimate"),
            "top_dangling_bond_estimate": top.get("dangling_bond_estimate"),
            "bottom_passivant_bond_count": bottom.get("passivant_bond_count"),
            "top_passivant_bond_count": top.get("passivant_bond_count"),
            "same_element_counts": summary.get("same_element_counts"),
            "passivation_symmetric": summary.get("passivation_symmetric"),
            "polar_surface_hint": summary.get("polar_surface_hint"),
            "surface_asymmetry_observed": summary.get("surface_asymmetry_observed"),
            "surface_asymmetry_expected": summary.get("surface_asymmetry_expected"),
            "surface_asymmetry_expected_reason": summary.get("surface_asymmetry_expected_reason"),
            "surface_asymmetry_warning": summary.get("surface_asymmetry_warning"),
            "surface_polarity_status": summary.get("surface_polarity_status"),
            "surface_polarity_next_action": summary.get("surface_polarity_next_action"),
            "warnings": ";".join(str(value) for value in summary.get("warnings", []) or []),
        }
    ]


def _view_summary_csv_row(view: dict[str, Any]) -> dict[str, Any]:
    bbox = view.get("projection_bbox_angstrom") or {}
    span = view.get("projection_span_angstrom") or {}
    health = view.get("health") or {}
    return {
        "view": view.get("name"),
        "supported": view.get("supported"),
        "coordinate_system": view.get("coordinate_system"),
        "crystal_direction_indices": _join_vector(view.get("crystal_direction_indices")),
        "crystal_direction_label": view.get("crystal_direction_label"),
        "crystal_direction_cartesian": _join_vector(view.get("crystal_direction_cartesian")),
        "crystal_direction_view_onto_plane_mapping": json.dumps(
            view.get("crystal_direction_view_onto_plane_mapping"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if view.get("crystal_direction_view_onto_plane_mapping") is not None
        else None,
        "crystal_plane_indices": _join_vector(view.get("crystal_plane_indices")),
        "crystal_plane_label": view.get("crystal_plane_label"),
        "crystal_plane_normal_cartesian": _join_vector(view.get("crystal_plane_normal_cartesian")),
        "crystal_plane_reciprocal_vector_per_angstrom": _join_vector(
            view.get("crystal_plane_reciprocal_vector_per_angstrom")
        ),
        "crystal_plane_reciprocal_convention": view.get("crystal_plane_reciprocal_convention"),
        "crystal_plane_spacing_angstrom": view.get("crystal_plane_spacing_angstrom"),
        "oriented_frame_kind": view.get("oriented_frame_kind"),
        "oriented_frame_role": view.get("oriented_frame_role"),
        "oriented_frame_axis": view.get("oriented_frame_axis"),
        "oriented_frame_source_metadata_field": view.get("oriented_frame_source_metadata_field"),
        "oriented_frame_reference_cell_axis": view.get("oriented_frame_reference_cell_axis"),
        "oriented_frame_axis_cartesian": _join_vector(view.get("oriented_frame_axis_cartesian")),
        "oriented_frame_direction_cartesian": _join_vector(view.get("oriented_frame_direction_cartesian")),
        "oriented_frame_in_plane_1_cartesian": _join_vector(view.get("oriented_frame_in_plane_1_cartesian")),
        "oriented_frame_in_plane_2_cartesian": _join_vector(view.get("oriented_frame_in_plane_2_cartesian")),
        "camera_direction": _join_vector(view.get("camera_direction")),
        "camera_up": _join_vector(view.get("camera_up")),
        "camera_right": _join_vector(view.get("camera_right")),
        "camera_position": _join_vector(view.get("camera_position")),
        "look_at_direction": _join_vector(view.get("look_at_direction")),
        "target": _join_vector(view.get("target")),
        "camera_distance_angstrom": view.get("camera_distance_angstrom"),
        "orthographic_width_angstrom": (view.get("framing") or {}).get("orthographic_width_angstrom"),
        "orthographic_height_angstrom": (view.get("framing") or {}).get("orthographic_height_angstrom"),
        "near_clip_angstrom": (view.get("framing") or {}).get("near_clip_angstrom"),
        "far_clip_angstrom": (view.get("framing") or {}).get("far_clip_angstrom"),
        "bbox_x_min": _list_item(bbox.get("x"), 0),
        "bbox_x_max": _list_item(bbox.get("x"), 1),
        "bbox_y_min": _list_item(bbox.get("y"), 0),
        "bbox_y_max": _list_item(bbox.get("y"), 1),
        "bbox_depth_min": _list_item(bbox.get("depth"), 0),
        "bbox_depth_max": _list_item(bbox.get("depth"), 1),
        "span_x_angstrom": span.get("x"),
        "span_y_angstrom": span.get("y"),
        "span_depth_angstrom": span.get("depth"),
        "atom_projection_count": view.get("atom_projection_count"),
        "overlap_candidate_count": len(view.get("overlap_candidates") or []),
        "health_ok": health.get("ok"),
        "warnings": ";".join(str(value) for value in health.get("warnings", []) or []),
    }


def _view_quality_csv_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    views = [view for view in audit.get("views", []) or [] if isinstance(view, dict)]
    expected_atoms = (audit.get("model") or {}).get("atom_count")
    rows = [_view_quality_base_row(view, expected_atoms) for view in views]
    clean_views = [row for row in rows if row["clean_for_visual_review"]]
    global_projection_safe = bool(clean_views) and _audit_has_clean_semiconductor_health(audit)
    if global_projection_safe:
        global_projection_safe = all(
            row["clean_for_visual_review"] or row["only_projection_overlap_warnings"]
            for row in rows
            if row["warning_count"] or row["overlap_candidate_count"]
        )

    ranked_clean = sorted(
        [row for row in rows if row["clean_for_visual_review"]],
        key=lambda row: (-float(row["span_area_angstrom2"] or 0.0), str(row["view"] or "")),
    )
    ranks = {str(row["view"]): index + 1 for index, row in enumerate(ranked_clean)}

    result: list[dict[str, Any]] = []
    for row in rows:
        clean = bool(row["clean_for_visual_review"])
        nonblocking_visual_note = bool(global_projection_safe and row["only_projection_overlap_warnings"])
        calculation_risk = not clean and not nonblocking_visual_note
        result.append(
            {
                **row,
                "recommended_rank": ranks.get(str(row["view"])),
                "nonblocking_visual_note": nonblocking_visual_note,
                "calculation_risk": calculation_risk,
                "recommendation": _view_quality_recommendation(row, clean, nonblocking_visual_note),
            }
        )
    return result


def _view_quality_base_row(view: dict[str, Any], expected_atoms: Any) -> dict[str, Any]:
    health = view.get("health") or {}
    warnings = [str(value) for value in health.get("warnings", []) or []]
    span = view.get("projection_span_angstrom") or {}
    span_x = _optional_float(span.get("x"))
    span_y = _optional_float(span.get("y"))
    atom_projection_count = view.get("atom_projection_count")
    projection_count_matches = None
    if expected_atoms is not None and atom_projection_count is not None:
        try:
            projection_count_matches = int(atom_projection_count) == int(expected_atoms)
        except (TypeError, ValueError):
            projection_count_matches = False
    overlap_count = len(view.get("overlap_candidates") or [])
    nearly_degenerate = any("degenerate" in warning.lower() for warning in warnings)
    truncated = bool(view.get("atom_projections_truncated"))
    only_projection_overlap_warnings = bool(
        warnings
        and overlap_count > 0
        and all("nearly overlapping in this 2d projection" in warning.lower() for warning in warnings)
    )
    clean = bool(
        view.get("supported")
        and overlap_count == 0
        and not warnings
        and not nearly_degenerate
        and not truncated
        and projection_count_matches is not False
    )
    return {
        "view": view.get("name"),
        "supported": view.get("supported"),
        "recommended_rank": None,
        "clean_for_visual_review": clean,
        "nonblocking_visual_note": False,
        "calculation_risk": not clean,
        "recommendation": None,
        "atom_projection_count": atom_projection_count,
        "projection_count_matches_model": projection_count_matches,
        "overlap_candidate_count": overlap_count,
        "warning_count": len(warnings),
        "nearly_degenerate": nearly_degenerate,
        "atom_projections_truncated": truncated,
        "only_projection_overlap_warnings": only_projection_overlap_warnings,
        "span_area_angstrom2": _round((span_x or 0.0) * (span_y or 0.0)),
        "span_x_angstrom": span.get("x"),
        "span_y_angstrom": span.get("y"),
        "span_depth_angstrom": span.get("depth"),
        "camera_direction": _join_vector(view.get("camera_direction")),
        "camera_up": _join_vector(view.get("camera_up")),
        "camera_position": _join_vector(view.get("camera_position")),
        "warnings": ";".join(warnings),
    }


def _view_quality_recommendation(row: dict[str, Any], clean: bool, nonblocking_visual_note: bool) -> str:
    if clean:
        return "use_for_visual_review"
    if nonblocking_visual_note:
        return "projection_overlap_visual_note_use_clean_view_for_review"
    if row.get("supported") is False:
        return "unsupported_view"
    if row.get("projection_count_matches_model") is False:
        return "re_export_view_audit_projection_count_mismatch"
    if row.get("nearly_degenerate"):
        return "rotate_or_use_non_degenerate_view"
    if row.get("atom_projections_truncated"):
        return "increase_projection_export_limit_or_use_summary"
    if int(row.get("overlap_candidate_count") or 0) > 0:
        return "inspect_depth_ordering_or_rotate_model"
    if int(row.get("warning_count") or 0) > 0:
        return "inspect_view_warnings"
    return "review_view_before_use"


def _audit_has_clean_semiconductor_health(audit: dict[str, Any]) -> bool:
    health = audit.get("health") if isinstance(audit.get("health"), dict) else {}
    semiconductor = health.get("semiconductor_health") if isinstance(health.get("semiconductor_health"), dict) else {}
    if not semiconductor:
        return False
    if health.get("ok") is False or semiconductor.get("ok") is False:
        return False
    if int(semiconductor.get("unexpected_neighbor_pair_count") or 0) > 0:
        return False
    if int(semiconductor.get("coordination_outlier_count") or 0) > 0:
        return False
    if int(semiconductor.get("same_sublattice_cutoff_artifact_pair_count") or 0) > 0:
        return False
    local_environment = semiconductor.get("local_environment_summary") or {}
    return int(local_environment.get("coordination_outlier_count") or 0) == 0


def _join_vector(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return ";".join(str(item) for item in value)
    return str(value)


def _list_item(value: Any, index: int) -> Any:
    if isinstance(value, (list, tuple)) and len(value) > index:
        return value[index]
    return None


def _extract_points(spec: ModelSpec) -> tuple[list[tuple[float, float, float]], list[dict[str, Any]], list[str], dict[str, Any]]:
    warnings: list[str] = []
    if isinstance(spec.model, MoleculeSpec):
        atoms = [
            {
                "id": atom.id,
                "element": atom.element,
                "xyz": atom.xyz_angstrom.as_tuple(),
            }
            for atom in spec.model.atoms
        ]
        return (
            [atom["xyz"] for atom in atoms],
            atoms,
            warnings,
            {
                "name": spec.model.name,
                "atom_count": len(spec.model.atoms),
                "bond_count": len(spec.model.bonds),
                "elements": dict(sorted(Counter(atom.element for atom in spec.model.atoms).items())),
                "total_charge": spec.model.total_charge,
                "spin_multiplicity": spec.model.spin_multiplicity,
            },
        )
    if isinstance(spec.model, CrystalSpec):
        vectors = _lattice_vectors(spec.model.lattice)
        atoms = []
        for atom in spec.model.basis_atoms:
            xyz = _fractional_to_cartesian(atom.fractional.as_tuple(), vectors)
            atoms.append({"id": atom.id, "element": atom.element, "fractional": atom.fractional.as_tuple(), "xyz": xyz})
        return (
            [atom["xyz"] for atom in atoms],
            atoms,
            warnings,
            {
                "name": spec.model.name,
                "atom_count": len(spec.model.basis_atoms),
                "bond_count": None,
                "elements": dict(sorted(Counter(atom.element for atom in spec.model.basis_atoms).items())),
                "lattice": spec.model.lattice.model_dump(mode="json"),
                "cell_volume_angstrom3": _lattice_volume(spec.model.lattice),
                "operations": [operation.model_dump(mode="json") for operation in spec.model.operations],
            },
        )
    if isinstance(spec.model, ImportedStructureSpec):
        warnings.append("Imported structure geometry is not available until Materials Studio summary output is parsed.")
        return (
            [],
            [],
            warnings,
            {
                "name": spec.model.name,
                "atom_count": None,
                "bond_count": None,
                "elements": {},
                "source_file": spec.model.source_file.path,
                "format": spec.model.format,
            },
        )
    warnings.append("Unsupported model payload for diagnostics.")
    return [], [], warnings, {"atom_count": None, "bond_count": None, "elements": {}}


def _geometry_summary(points: list[tuple[float, float, float]]) -> dict[str, Any]:
    if not points:
        return {
            "bbox": None,
            "span_angstrom": None,
            "center": None,
            "radius_angstrom": None,
            "min_pair_distance_angstrom": None,
        }
    mins = tuple(min(point[index] for point in points) for index in range(3))
    maxs = tuple(max(point[index] for point in points) for index in range(3))
    center = tuple((mins[index] + maxs[index]) / 2.0 for index in range(3))
    span = tuple(maxs[index] - mins[index] for index in range(3))
    radius = max(_distance(point, center) for point in points)
    min_pair = None
    for index, point in enumerate(points):
        for other in points[index + 1 :]:
            distance = _distance(point, other)
            min_pair = distance if min_pair is None else min(min_pair, distance)
    return {
        "bbox": {"min": _round_tuple(mins), "max": _round_tuple(maxs)},
        "span_angstrom": _round_tuple(span),
        "center": _round_tuple(center),
        "radius_angstrom": _round(radius),
        "min_pair_distance_angstrom": _round(min_pair) if min_pair is not None else None,
    }


def _health_checks(
    spec: ModelSpec,
    points: list[tuple[float, float, float]],
    atom_rows: list[dict[str, Any]],
    initial_warnings: list[str],
) -> dict[str, Any]:
    warnings = list(initial_warnings)
    errors: list[str] = []
    if not points:
        warnings.append("No coordinate set was available for geometric health checks.")
    else:
        min_pair = _geometry_summary(points)["min_pair_distance_angstrom"]
        if min_pair is not None and min_pair < 0.35:
            errors.append(f"Atoms are unrealistically close: minimum pair distance {min_pair} A.")
        elif min_pair is not None and min_pair < 0.6:
            warnings.append(f"Atoms are very close: minimum pair distance {min_pair} A.")

    if isinstance(spec.model, MoleculeSpec):
        atom_map = {atom["id"]: atom["xyz"] for atom in atom_rows}
        element_map = {atom["id"]: atom["element"] for atom in atom_rows}
        connectivity = {
            atom["id"]: {
                "atom_id": atom["id"],
                "element": atom["element"],
                "degree": 0,
                "bond_order_sum": 0.0,
                "bonded_atoms": [],
            }
            for atom in atom_rows
        }
        bond_lengths = []
        bond_length_rows: list[dict[str, Any]] = []
        bonded_pairs: set[frozenset[str]] = set()
        for bond in spec.model.bonds:
            if bond.atom1 in atom_map and bond.atom2 in atom_map:
                length = _distance(atom_map[bond.atom1], atom_map[bond.atom2])
                order = _bond_order(bond.type)
                bond_lengths.append(length)
                bonded_pairs.add(frozenset((bond.atom1, bond.atom2)))
                bond_length_rows.append(
                    {
                        "atom1": bond.atom1,
                        "atom2": bond.atom2,
                        "type": bond.type,
                        "length_angstrom": _round(length),
                        "bond_order": order,
                    }
                )
                _add_connectivity(connectivity, bond.atom1, bond.atom2, order)
                _add_connectivity(connectivity, bond.atom2, bond.atom1, order)
                if length < 0.35:
                    errors.append(f"Bond {bond.atom1}-{bond.atom2} is unrealistically short: {_round(length)} A.")
                elif length > 3.0 and bond.type in {"Single", "Double", "Triple", "Aromatic", "Partial double"}:
                    warnings.append(f"Bond {bond.atom1}-{bond.atom2} is unusually long: {_round(length)} A.")
        bond_stats = _stats(bond_lengths)
        for item in connectivity.values():
            item["bond_order_sum"] = _round(item["bond_order_sum"])
            max_order = COMMON_MAX_BOND_ORDER.get(item["element"])
            if max_order is not None and item["bond_order_sum"] > max_order + 0.1:
                errors.append(
                    f"Atom {item['atom_id']} ({item['element']}) is over-coordinated: "
                    f"bond order sum {item['bond_order_sum']} exceeds expected maximum {max_order}."
                )
            if spec.model.bonds and item["degree"] == 0:
                warnings.append(f"Atom {item['atom_id']} ({item['element']}) is isolated from the molecular graph.")
        nonbonded_close_contacts = _nonbonded_close_contacts(atom_rows, element_map, bonded_pairs)
        if nonbonded_close_contacts:
            warnings.append("Some non-bonded atoms are unusually close; inspect missing bonds or geometry clashes.")
        bond_angle_rows = _bond_angle_rows(atom_map, connectivity)
        bond_angle_stats = _stats([row["angle_deg"] for row in bond_angle_rows])
        dihedral_rows = _dihedral_angle_rows(atom_map, connectivity)
        dihedral_stats = _stats([abs(row["angle_deg"]) for row in dihedral_rows])
    else:
        bond_stats = None
        bond_length_rows = []
        bond_angle_rows = []
        bond_angle_stats = None
        dihedral_rows = []
        dihedral_stats = None
        connectivity = {}
        nonbonded_close_contacts = []

    crystal_health: dict[str, Any] = {}
    if isinstance(spec.model, CrystalSpec):
        crystal_health = _crystal_health_checks(spec, atom_rows)
        warnings.extend(crystal_health.pop("_warnings", []))
        errors.extend(crystal_health.pop("_errors", []))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "bond_length_stats_angstrom": bond_stats,
        "bond_lengths_angstrom": bond_length_rows[:MAX_HEALTH_DETAIL_ROWS],
        "bond_angle_stats_deg": bond_angle_stats,
        "bond_angles_deg": bond_angle_rows[:MAX_HEALTH_DETAIL_ROWS],
        "dihedral_angle_stats_abs_deg": dihedral_stats,
        "dihedral_angles_deg": dihedral_rows[:MAX_HEALTH_DETAIL_ROWS],
        "atom_connectivity": list(connectivity.values())[:MAX_HEALTH_DETAIL_ROWS],
        "nonbonded_close_contacts": nonbonded_close_contacts[:MAX_HEALTH_DETAIL_ROWS],
        **crystal_health,
    }


def _crystal_health_checks(spec: ModelSpec, atom_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return periodic minimum-image checks for crystal specs."""

    if not isinstance(spec.model, CrystalSpec):
        return {}

    pair_rows = _crystal_pair_distance_rows(spec.model, atom_rows)
    distances = [float(row["distance_angstrom"]) for row in pair_rows]
    errors: list[str] = []
    warnings: list[str] = []
    if distances:
        min_distance = min(distances)
        if min_distance < 0.35:
            errors.append(f"Crystal atoms are unrealistically close under periodic images: minimum pair distance {_round(min_distance)} A.")
        elif min_distance < 0.6:
            warnings.append(f"Crystal atoms are very close under periodic images: minimum pair distance {_round(min_distance)} A.")

    nearest_rows = _crystal_nearest_neighbor_rows(atom_rows, pair_rows)
    neighbor_pair_rows = _crystal_periodic_neighbor_pair_rows(spec.model, atom_rows)
    coordination_rows = _crystal_coordination_rows(atom_rows, neighbor_pair_rows)
    slab_vacuum = _slab_vacuum_summary(spec, atom_rows)
    if slab_vacuum:
        declared_vacuum = slab_vacuum.get("declared_vacuum_angstrom")
        inferred_vacuum = slab_vacuum.get("atom_extent_vacuum_angstrom")
        if declared_vacuum is not None and declared_vacuum < 8.0:
            warnings.append(f"Declared slab vacuum is low for visual/DFT setup: {_round(float(declared_vacuum))} A.")
        elif declared_vacuum is None and inferred_vacuum is not None and inferred_vacuum < 8.0:
            warnings.append(f"Atom-center slab vacuum appears low: {_round(float(inferred_vacuum))} A.")
        if slab_vacuum.get("metadata_cell_mismatch"):
            warnings.append("Slab metadata thickness plus vacuum does not match the lattice axis length.")

    coordination_counts = [float(row["neighbor_count"]) for row in coordination_rows]
    semiconductor_health = _semiconductor_health_summary(spec, atom_rows, neighbor_pair_rows, coordination_rows, slab_vacuum)
    if semiconductor_health and not semiconductor_health.get("ok", True):
        errors.extend(str(item) for item in semiconductor_health.get("errors", []) or [])
    return {
        "_errors": errors,
        "_warnings": warnings,
        "crystal_distance_mode": "periodic_minimum_image_3x3",
        "crystal_pair_distance_stats_angstrom": _stats_with_count(distances),
        "crystal_nearest_neighbor_stats_angstrom": _stats_with_count([float(row["distance_angstrom"]) for row in nearest_rows]),
        "crystal_nearest_neighbors": nearest_rows[:MAX_HEALTH_DETAIL_ROWS],
        "crystal_coordination_stats": _stats_with_count(coordination_counts),
        "crystal_coordination": coordination_rows[:MAX_HEALTH_DETAIL_ROWS],
        "slab_vacuum": slab_vacuum,
        "semiconductor_health": semiconductor_health,
    }


def _crystal_pair_distance_rows(model: CrystalSpec, atom_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vectors = _lattice_vectors(model.lattice)
    rows: list[dict[str, Any]] = []
    for index, atom in enumerate(atom_rows):
        atom_fractional = atom.get("fractional")
        if atom_fractional is None:
            continue
        for other in atom_rows[index + 1 :]:
            other_fractional = other.get("fractional")
            if other_fractional is None:
                continue
            distance, offset = _minimum_image_distance(
                tuple(float(value) for value in atom_fractional),
                tuple(float(value) for value in other_fractional),
                vectors,
            )
            rows.append(
                {
                    "atom1": atom.get("id"),
                    "element1": atom.get("element"),
                    "atom2": other.get("id"),
                    "element2": other.get("element"),
                    "distance_angstrom": _round(distance),
                    "image_offset_atom2": list(offset),
                }
            )
    rows.sort(key=lambda row: (float(row["distance_angstrom"]), str(row["atom1"]), str(row["atom2"])))
    return rows


def _minimum_image_distance(
    fractional1: tuple[float, float, float],
    fractional2: tuple[float, float, float],
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, tuple[int, int, int]]:
    best_distance: float | None = None
    best_offset = (0, 0, 0)
    for da in (-1, 0, 1):
        for db in (-1, 0, 1):
            for dc in (-1, 0, 1):
                diff = (
                    fractional2[0] + da - fractional1[0],
                    fractional2[1] + db - fractional1[1],
                    fractional2[2] + dc - fractional1[2],
                )
                distance = _distance(_fractional_to_cartesian(diff, vectors), (0.0, 0.0, 0.0))
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_offset = (da, db, dc)
    return float(best_distance or 0.0), best_offset


def _crystal_nearest_neighbor_rows(atom_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    element_map = {str(atom.get("id")): atom.get("element") for atom in atom_rows}
    nearest: dict[str, dict[str, Any]] = {}
    for row in pair_rows:
        atom1 = str(row.get("atom1"))
        atom2 = str(row.get("atom2"))
        distance = float(row["distance_angstrom"])
        offset = tuple(int(value) for value in row.get("image_offset_atom2") or [0, 0, 0])
        candidates = [
            (atom1, atom2, offset),
            (atom2, atom1, tuple(-value for value in offset)),
        ]
        for atom_id, nearest_id, image_offset in candidates:
            current = nearest.get(atom_id)
            if current is None or distance < float(current["distance_angstrom"]):
                nearest[atom_id] = {
                    "atom_id": atom_id,
                    "element": element_map.get(atom_id),
                    "nearest_atom_id": nearest_id,
                    "nearest_element": element_map.get(nearest_id),
                    "distance_angstrom": _round(distance),
                    "image_offset_to_nearest": list(image_offset),
                }
    return [nearest[atom_id] for atom_id in sorted(nearest)]


def _crystal_periodic_neighbor_pair_rows(model: CrystalSpec, atom_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vectors = _lattice_vectors(model.lattice)
    rows: list[dict[str, Any]] = []
    for index, atom in enumerate(atom_rows):
        atom_fractional = atom.get("fractional")
        if atom_fractional is None:
            continue
        fractional1 = tuple(float(value) for value in atom_fractional)
        for other in atom_rows[index + 1 :]:
            other_fractional = other.get("fractional")
            if other_fractional is None:
                continue
            element1 = str(atom.get("element") or "")
            element2 = str(other.get("element") or "")
            threshold = _crystal_neighbor_threshold(element1, element2)
            fractional2 = tuple(float(value) for value in other_fractional)
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        offset = (da, db, dc)
                        diff = (
                            fractional2[0] + da - fractional1[0],
                            fractional2[1] + db - fractional1[1],
                            fractional2[2] + dc - fractional1[2],
                        )
                        distance = _distance(_fractional_to_cartesian(diff, vectors), (0.0, 0.0, 0.0))
                        if distance <= threshold:
                            rows.append(
                                {
                                    "atom1": atom.get("id"),
                                    "element1": atom.get("element"),
                                    "atom2": other.get("id"),
                                    "element2": other.get("element"),
                                    "distance_angstrom": _round(distance),
                                    "image_offset_atom2": list(offset),
                                    "pair_type": _element_pair_label(element1, element2),
                                    "neighbor_threshold_angstrom": _round(threshold),
                                }
                            )
    rows.sort(
        key=lambda row: (
            float(row["distance_angstrom"]),
            str(row["atom1"]),
            str(row["atom2"]),
            tuple(int(value) for value in row.get("image_offset_atom2") or [0, 0, 0]),
        )
    )
    return rows


def _crystal_coordination_rows(atom_rows: list[dict[str, Any]], neighbor_pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    element_map = {str(atom.get("id")): str(atom.get("element")) for atom in atom_rows}
    neighbor_distances: dict[str, list[tuple[str, float]]] = {str(atom.get("id")): [] for atom in atom_rows}
    neighbor_offsets: dict[str, list[tuple[str, tuple[int, int, int]]]] = {str(atom.get("id")): [] for atom in atom_rows}
    for row in neighbor_pair_rows:
        atom1 = str(row.get("atom1"))
        atom2 = str(row.get("atom2"))
        distance = float(row["distance_angstrom"])
        offset = tuple(int(value) for value in row.get("image_offset_atom2") or [0, 0, 0])
        neighbor_distances[atom1].append((atom2, distance))
        neighbor_distances[atom2].append((atom1, distance))
        neighbor_offsets[atom1].append((atom2, offset))
        neighbor_offsets[atom2].append((atom1, tuple(-value for value in offset)))
    rows: list[dict[str, Any]] = []
    for atom in atom_rows:
        atom_id = str(atom.get("id"))
        distances = [distance for _, distance in neighbor_distances.get(atom_id, [])]
        neighbor_ids = [neighbor for neighbor, _ in sorted(neighbor_distances.get(atom_id, []))]
        unique_neighbor_ids = sorted({neighbor for neighbor, _ in neighbor_distances.get(atom_id, [])})
        rows.append(
            {
                "atom_id": atom_id,
                "element": atom.get("element"),
                "neighbor_count": len(distances),
                "unique_neighbor_count": len(unique_neighbor_ids),
                "nearest_distance_angstrom": _round(min(distances)) if distances else None,
                "mean_neighbor_distance_angstrom": _round(sum(distances) / len(distances)) if distances else None,
                "min_neighbor_distance_angstrom": _round(min(distances)) if distances else None,
                "max_neighbor_distance_angstrom": _round(max(distances)) if distances else None,
                "neighbor_ids": neighbor_ids,
                "unique_neighbor_ids": unique_neighbor_ids,
                "neighbor_elements": [element_map.get(neighbor) for neighbor in neighbor_ids],
                "neighbor_image_offsets": [
                    {"neighbor_id": neighbor, "image_offset": list(offset)}
                    for neighbor, offset in sorted(
                        neighbor_offsets.get(atom_id, []),
                        key=lambda item: (item[0], item[1]),
                    )
                ],
                "cutoff_rule": "distance <= 1.25 * covalent_radius_sum",
            }
        )
    return rows


def _semiconductor_health_summary(
    spec: ModelSpec,
    atom_rows: list[dict[str, Any]],
    neighbor_pair_rows: list[dict[str, Any]],
    coordination_rows: list[dict[str, Any]],
    slab_vacuum: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata = spec.metadata or {}
    if metadata.get("domain") != "semiconductor":
        return None

    elements = sorted({str(row.get("element")) for row in coordination_rows if row.get("element")})
    non_passivant_elements = [element for element in elements if element not in SURFACE_PASSIVANTS]
    all_element_counts = Counter(
        str(row.get("element"))
        for row in coordination_rows
        if row.get("element")
    )
    element_counts = Counter(
        str(row.get("element"))
        for row in coordination_rows
        if row.get("element") and str(row.get("element")) not in SURFACE_PASSIVANTS
    )
    family = str(metadata.get("structure_family") or "").lower()
    material = str(metadata.get("material") or "").lower()
    surface_context = _surface_slab_diagnostics_applicable(metadata)
    operations = list(getattr(spec.model, "operations", []) or [])
    rule = "generic_semiconductor"
    expected_coordination = None
    expected_coordination_by_element: dict[str, int] = {}
    host_elements, dopant_elements = _semiconductor_host_and_dopants(metadata, non_passivant_elements, element_counts)
    has_alloy_metadata = bool(metadata.get("applied_alloy"))
    tmd_metals = sorted(set(non_passivant_elements) & TMD_METALS)
    tmd_chalcogens = sorted(set(non_passivant_elements) & TMD_CHALCOGENS)
    host_tmd_metals = sorted(set(host_elements) & TMD_METALS)
    host_tmd_chalcogens = sorted(set(host_elements) & TMD_CHALCOGENS)
    oxide_cations = sorted(set(non_passivant_elements) - {"O"})
    oxide_material = bool(
        metadata.get("oxide_semiconductor") or metadata.get("oxide_material") or metadata.get("insulating_substrate")
    ) and "O" in non_passivant_elements and bool(
        oxide_cations
    )
    hbn_layered = (
        {"B", "N"} <= set(non_passivant_elements)
        and (
            "hbn" in family
            or "h-bn" in material
            or str(metadata.get("layered_insulator") or "").lower() == "true"
        )
        and ("monolayer" in family or metadata.get("vacuum_angstrom") is not None)
    )
    phosphorene_layered = (
        set(non_passivant_elements) == {"P"}
        and (
            "phosphorene" in family
            or "phosphorene" in material
            or "black phosphorus" in family
            or "black phosphorus" in material
            or bool(metadata.get("puckered_layered_semiconductor"))
        )
        and ("monolayer" in family or metadata.get("vacuum_angstrom") is not None)
    )
    dopant_site_elements = _dopant_site_context_elements(metadata)
    halide_framework_elements = set(non_passivant_elements) | _metadata_material_elements(metadata) | dopant_site_elements
    halide_perovskite = _is_halide_perovskite_context(metadata, sorted(halide_framework_elements))
    if halide_perovskite and has_alloy_metadata and host_elements and dopant_elements:
        rule = "doped_alloyed_halide_perovskite_framework"
        expected_coordination_by_element = {
            **{element: 6 for element in halide_framework_elements & HALIDE_PEROVSKITE_B_CATIONS},
            **{element: 2 for element in halide_framework_elements & HALIDE_PEROVSKITE_HALIDES},
        }
    elif halide_perovskite and has_alloy_metadata:
        rule = "alloyed_halide_perovskite_framework"
        expected_coordination_by_element = {
            **{element: 6 for element in halide_framework_elements & HALIDE_PEROVSKITE_B_CATIONS},
            **{element: 2 for element in halide_framework_elements & HALIDE_PEROVSKITE_HALIDES},
        }
    elif halide_perovskite and host_elements and dopant_elements:
        rule = "doped_halide_perovskite_framework"
        expected_coordination_by_element = {
            **{element: 6 for element in halide_framework_elements & HALIDE_PEROVSKITE_B_CATIONS},
            **{element: 2 for element in halide_framework_elements & HALIDE_PEROVSKITE_HALIDES},
        }
    elif halide_perovskite:
        rule = "halide_perovskite_framework"
        expected_coordination_by_element = {
            **{element: 6 for element in halide_framework_elements & HALIDE_PEROVSKITE_B_CATIONS},
            **{element: 2 for element in halide_framework_elements & HALIDE_PEROVSKITE_HALIDES},
        }
    elif hbn_layered and host_elements and dopant_elements:
        rule = "doped_iii_v_layered_trigonal_planar"
        expected_coordination_by_element = {"B": 3, "N": 3}
    elif phosphorene_layered and host_elements and dopant_elements:
        rule = "doped_phosphorene_layered_puckered"
        expected_coordination_by_element = {"P": 3}
    elif host_elements and dopant_elements and set(host_elements) <= GROUP_IV_SEMICONDUCTORS:
        rule = "doped_group_iv_tetrahedral"
        expected_coordination = 4
    elif host_elements and dopant_elements and (
        set(host_elements) & III_V_CATIONS and set(host_elements) & III_V_ANIONS
    ):
        rule = "doped_iii_v_tetrahedral"
        expected_coordination = 4
    elif host_elements and dopant_elements and (
        set(host_elements) & II_VI_CATIONS and set(host_elements) & II_VI_ANIONS
    ):
        rule = "doped_ii_vi_tetrahedral"
        expected_coordination = 4
    elif host_elements and dopant_elements and host_tmd_metals and host_tmd_chalcogens:
        rule = "doped_tmd_layered_trigonal_prismatic"
        expected_coordination_by_element = {
            **{element: 6 for element in host_tmd_metals},
            **{element: 3 for element in host_tmd_chalcogens},
        }
    elif non_passivant_elements and set(non_passivant_elements) <= GROUP_IV_SEMICONDUCTORS:
        rule = "group_iv_tetrahedral"
        expected_coordination = 4
    elif hbn_layered:
        rule = "iii_v_layered_trigonal_planar"
        expected_coordination_by_element = {"B": 3, "N": 3}
    elif phosphorene_layered:
        rule = "phosphorene_layered_puckered"
        expected_coordination_by_element = {"P": 3}
    elif set(non_passivant_elements) & III_V_CATIONS and set(non_passivant_elements) & III_V_ANIONS:
        rule = "iii_v_tetrahedral"
        expected_coordination = 4
    elif set(non_passivant_elements) & II_VI_CATIONS and set(non_passivant_elements) & II_VI_ANIONS:
        rule = "ii_vi_tetrahedral"
        expected_coordination = 4
    elif tmd_metals and tmd_chalcogens:
        rule = "tmd_layered_trigonal_prismatic"
        expected_coordination_by_element = {
            **{element: 6 for element in tmd_metals},
            **{element: 3 for element in tmd_chalcogens},
        }
    elif oxide_material:
        rule = (
            "oxide_semiconductor_mixed_coordination"
            if bool(metadata.get("oxide_semiconductor"))
            else "oxide_material_mixed_coordination"
        )
    elif "diamond" in family or "zinc" in family or "wurtzite" in family:
        expected_coordination = 4
    expected_coordination_by_element = _expected_coordination_map_with_site_dopants(
        metadata,
        expected_coordination_by_element,
    )
    tmd_site_roles = _tmd_site_roles_by_element(metadata, expected_coordination_by_element)
    halide_perovskite_site_roles = _halide_perovskite_site_roles_by_element(
        metadata,
        expected_coordination_by_element,
    )

    pair_counts = Counter(str(row.get("pair_type")) for row in neighbor_pair_rows)
    unexpected_pairs: list[dict[str, Any]] = []
    unchecked_pairs: list[dict[str, Any]] = []
    passivant_pairs: list[dict[str, Any]] = []
    alloy_same_sublattice_pairs: list[dict[str, Any]] = []
    same_sublattice_cutoff_artifact_pairs: list[dict[str, Any]] = []
    coordination_excluded_pair_ids: set[int] = set()
    for row in neighbor_pair_rows:
        element1 = str(row.get("element1") or "")
        element2 = str(row.get("element2") or "")
        pair = str(row.get("pair_type"))
        if element1 in SURFACE_PASSIVANTS or element2 in SURFACE_PASSIVANTS:
            passivant_pairs.append(_semiconductor_pair_detail(row))
            if "halide_perovskite" in rule:
                coordination_excluded_pair_ids.add(id(row))
            continue
        if rule == "group_iv_tetrahedral":
            if element1 not in GROUP_IV_SEMICONDUCTORS or element2 not in GROUP_IV_SEMICONDUCTORS:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif rule == "doped_group_iv_tetrahedral":
            if element1 in host_elements and element2 in host_elements:
                continue
            if element1 in dopant_elements and element2 in host_elements:
                continue
            if element2 in dopant_elements and element1 in host_elements:
                continue
            unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif rule == "iii_v_tetrahedral":
            if _is_iii_v_cation_anion_pair(element1, element2):
                continue
            if has_alloy_metadata and _is_iii_v_same_sublattice_pair(element1, element2):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                alloy_same_sublattice_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
                continue
            if (
                not has_alloy_metadata
                and not operations
                and _is_iii_v_same_sublattice_pair(element1, element2)
                and _is_long_same_sublattice_cutoff_artifact(
                    row,
                    neighbor_pair_rows,
                    expected_pair=_is_iii_v_cation_anion_pair,
                )
            ):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                same_sublattice_cutoff_artifact_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
                continue
            if (
                (element1 in III_V_CATIONS or element1 in III_V_ANIONS)
                and (element2 in III_V_CATIONS or element2 in III_V_ANIONS)
            ):
                unexpected_pairs.append(_semiconductor_pair_detail(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif "iii_v_layered" in rule:
            if _is_iii_v_cation_anion_pair(element1, element2):
                continue
            if _is_iii_v_same_sublattice_pair(element1, element2):
                unexpected_pairs.append(_semiconductor_pair_detail(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif "phosphorene_layered" in rule:
            if element1 == "P" and element2 == "P":
                continue
            unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif "halide_perovskite" in rule:
            if _is_halide_perovskite_b_halide_pair(element1, element2, halide_perovskite_site_roles):
                continue
            detail = _semiconductor_pair_detail(row)
            if _is_halide_perovskite_framework_same_sublattice_pair(element1, element2, halide_perovskite_site_roles):
                unexpected_pairs.append(detail)
            else:
                unchecked_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
        elif rule == "ii_vi_tetrahedral":
            if _is_ii_vi_cation_anion_pair(element1, element2):
                continue
            if has_alloy_metadata and _is_ii_vi_same_sublattice_pair(element1, element2):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                alloy_same_sublattice_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
                continue
            if (
                not has_alloy_metadata
                and not operations
                and _is_ii_vi_same_sublattice_pair(element1, element2)
                and _is_long_same_sublattice_cutoff_artifact(
                    row,
                    neighbor_pair_rows,
                    expected_pair=_is_ii_vi_cation_anion_pair,
                )
            ):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                same_sublattice_cutoff_artifact_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
                continue
            if (
                (element1 in II_VI_CATIONS or element1 in II_VI_ANIONS)
                and (element2 in II_VI_CATIONS or element2 in II_VI_ANIONS)
            ):
                unexpected_pairs.append(_semiconductor_pair_detail(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif rule == "doped_ii_vi_tetrahedral":
            if _is_ii_vi_cation_anion_pair(element1, element2):
                continue
            if has_alloy_metadata and _is_ii_vi_same_sublattice_pair(element1, element2):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                alloy_same_sublattice_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
                continue
            if element1 in dopant_elements or element2 in dopant_elements:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
            elif (
                (element1 in II_VI_CATIONS or element1 in II_VI_ANIONS)
                and (element2 in II_VI_CATIONS or element2 in II_VI_ANIONS)
            ):
                unexpected_pairs.append(_semiconductor_pair_detail(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif rule == "doped_iii_v_tetrahedral":
            if _is_iii_v_cation_anion_pair(element1, element2):
                continue
            if has_alloy_metadata and _is_iii_v_same_sublattice_pair(element1, element2):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                alloy_same_sublattice_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
                continue
            if element1 in dopant_elements or element2 in dopant_elements:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
            elif (
                (element1 in III_V_CATIONS or element1 in III_V_ANIONS)
                and (element2 in III_V_CATIONS or element2 in III_V_ANIONS)
            ):
                unexpected_pairs.append(_semiconductor_pair_detail(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif "tmd_layered" in rule:
            if _is_tmd_metal_chalcogen_pair(element1, element2, tmd_site_roles):
                continue
            if _is_tmd_same_sublattice_pair(element1, element2, tmd_site_roles):
                unexpected_pairs.append(_semiconductor_pair_detail(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))
        elif rule in {"oxide_semiconductor_mixed_coordination", "oxide_material_mixed_coordination"}:
            if _is_oxide_cation_oxygen_pair(element1, element2, oxide_cations):
                continue
            if _is_oxide_same_sublattice_pair(element1, element2, oxide_cations):
                detail = _semiconductor_pair_detail(row)
                unchecked_pairs.append(detail)
                same_sublattice_cutoff_artifact_pairs.append(detail)
                coordination_excluded_pair_ids.add(id(row))
            else:
                unchecked_pairs.append(_semiconductor_pair_detail(row))

    coordination_neighbor_pair_rows = [
        row for row in neighbor_pair_rows if id(row) not in coordination_excluded_pair_ids
    ]
    if len(coordination_neighbor_pair_rows) != len(neighbor_pair_rows):
        coordination_rows = _crystal_coordination_rows(atom_rows, coordination_neighbor_pair_rows)

    coordination_by_element: dict[str, dict[str, Any]] = {}
    for element in elements:
        counts = [float(row.get("neighbor_count") or 0) for row in coordination_rows if row.get("element") == element]
        coordination_by_element[element] = _stats_with_count(counts)

    coordination_outliers = []
    for row in coordination_rows:
        element = str(row.get("element") or "")
        atom_expected_coordination = _expected_coordination_for_element(
            element,
            expected_coordination,
            expected_coordination_by_element,
        )
        if atom_expected_coordination is None:
            continue
        count = int(row.get("neighbor_count") or 0)
        if count != atom_expected_coordination:
            coordination_outliers.append(
                {
                    "atom_id": row.get("atom_id"),
                    "element": element,
                    "neighbor_count": count,
                    "expected_coordination": atom_expected_coordination,
                    "neighbor_ids": row.get("neighbor_ids") or [],
                    "surface_or_defect_context": bool(surface_context or operations),
                }
            )

    defect_summary = _defect_summary(
        spec,
        metadata,
        atom_rows,
        coordination_rows,
        element_counts,
        expected_coordination=expected_coordination,
        expected_coordination_by_element=expected_coordination_by_element,
    )
    errors = []
    warnings = []
    if defect_summary and not defect_summary.get("defect_complex_integrity_ok", True):
        errors.extend(
            str(item)
            for item in defect_summary.get("defect_complex_integrity_errors", []) or []
        )
    if unexpected_pairs:
        unexpected_counts = Counter(str(row.get("pair_type")) for row in unexpected_pairs)
        message = (
            "Unexpected semiconductor nearest-neighbor pair types: "
            + ", ".join(f"{pair}={count}" for pair, count in sorted(unexpected_counts.items()))
            + "."
        )
        if defect_summary and int(defect_summary.get("antisite_count") or 0) > 0:
            warnings.append(message + " Intentional antisite defect metadata is present; inspect defect_summary.")
        else:
            errors.append(message)
    if alloy_same_sublattice_pairs:
        warnings.append(
            "Semiconductor alloy has same-sublattice neighbor pairs under the preflight distance cutoff; "
            "inspect neighbor_distance_summary before quantitative calculations."
        )
    if same_sublattice_cutoff_artifact_pairs:
        warnings.append(
            "Semiconductor same-sublattice neighbor candidates were treated as long-distance cutoff artifacts; "
            "inspect neighbor_distance_summary before quantitative calculations."
        )
    dopant_summary = _dopant_summary(
        host_elements=host_elements,
        dopant_elements=dopant_elements,
        element_counts=element_counts,
        coordination_rows=coordination_rows,
        expected_coordination=expected_coordination,
        expected_coordination_by_element=expected_coordination_by_element,
    )
    composition_summary = _composition_summary(
        metadata,
        all_element_counts,
        host_elements=host_elements,
        dopant_elements=dopant_elements,
    )
    dopant_site_summary = _dopant_site_summary(metadata, atom_rows)
    if dopant_site_summary:
        warnings.extend(str(item) for item in dopant_site_summary.get("warnings", []) or [])
        errors.extend(str(item) for item in dopant_site_summary.get("errors", []) or [])
    charge_balance_summary = _charge_balance_summary(
        metadata,
        all_element_counts,
        host_elements=host_elements,
        dopant_elements=dopant_elements,
        dopant_site_summary=dopant_site_summary,
        simulation=spec.simulation,
    )
    carrier_intent_summary = _carrier_intent_summary(
        metadata,
        dopant_summary=dopant_summary,
        charge_balance_summary=charge_balance_summary,
        dopant_site_summary=dopant_site_summary,
        defect_summary=defect_summary,
    )
    if carrier_intent_summary:
        warnings.extend(str(item) for item in carrier_intent_summary.get("warnings", []) or [])
    junction_summary = _junction_summary(metadata)
    lattice_summary = _semiconductor_lattice_summary(
        spec,
        all_element_counts,
        slab_vacuum,
    )
    dopant_concentration_summary = _dopant_concentration_summary(
        lattice_summary=lattice_summary,
        dopant_summary=dopant_summary,
        charge_balance_summary=charge_balance_summary,
    )
    if dopant_concentration_summary and dopant_concentration_summary.get("high_concentration_warning"):
        warnings.append(
            "Semiconductor dopant concentration is high for a periodic supercell; inspect dopant_concentration_summary."
        )
    finite_size_summary = _finite_size_summary(
        spec,
        lattice_summary,
        dopant_summary,
        defect_summary,
    )
    calculation_preflight_summary = _calculation_preflight_summary(spec, lattice_summary)
    reciprocal_lattice_summary = _reciprocal_lattice_summary(spec, lattice_summary, calculation_preflight_summary)
    band_path_summary = _semiconductor_band_path_summary(spec, metadata, calculation_preflight_summary)
    neighbor_distance_summary = _semiconductor_neighbor_distance_summary(
        neighbor_pair_rows,
        unexpected_pairs=unexpected_pairs,
        unchecked_pairs=unchecked_pairs,
        passivant_pairs=passivant_pairs,
    )
    local_environment_summary = _semiconductor_local_environment_summary(
        spec,
        atom_rows,
        coordination_neighbor_pair_rows,
        coordination_rows,
        expected_coordination=expected_coordination,
        expected_coordination_by_element=expected_coordination_by_element,
    )
    sublattice_balance_summary = _sublattice_balance_summary(
        element_counts,
        host_elements=host_elements,
        dopant_elements=dopant_elements,
        rule=rule,
    )
    if sublattice_balance_summary and sublattice_balance_summary.get("warning"):
        warnings.append("Semiconductor sublattice balance is off; inspect sublattice_balance_summary.")
    dopant_fraction_summary = _dopant_fraction_summary(spec, metadata)
    alloy_summary = _alloy_summary(spec, metadata)
    for label, fraction_summary in (
        ("dopant fraction", dopant_fraction_summary),
        ("alloy fraction", alloy_summary),
    ):
        if not fraction_summary or not fraction_summary.get("periodic_maximin_count"):
            continue
        if fraction_summary.get("site_selection_integrity_ok") is False:
            errors.extend(str(item) for item in fraction_summary.get("site_selection_errors", []) or [])
        if fraction_summary.get("site_pair_distribution_integrity_ok") is False:
            errors.extend(
                str(item)
                for item in fraction_summary.get("site_pair_distribution_errors", []) or []
            )
        if fraction_summary.get("site_short_range_order_integrity_ok") is False:
            errors.extend(
                str(item)
                for item in fraction_summary.get("site_short_range_order_errors", []) or []
            )
        warnings.append(
            f"Semiconductor {label} uses deterministic periodic maximin site separation; this is not an SQS."
        )
        if fraction_summary.get("site_selection_replay_verified") is False:
            warnings.append(
                f"Semiconductor {label} periodic maximin selection cannot be replayed on the current geometry."
            )
        if fraction_summary.get("adjacent_pair_review_required"):
            warnings.append(
                f"Semiconductor {label} contains candidate-nearest substituted-site pairs; inspect local environments."
            )
        if fraction_summary.get(
            "site_pair_distribution_nearest_shell_pair_excess_review_required"
        ):
            warnings.append(
                f"Semiconductor {label} has a nearest-shell selected-pair excess relative to the fixed-composition expectation; inspect site_pair_distribution."
            )
        if fraction_summary.get(
            "site_short_range_order_nearest_shell_clustering_like_review_required"
        ):
            warnings.append(
                f"Semiconductor {label} has clustering-like unlike-pair depletion in the nearest finite-cell distance shell; inspect site_short_range_order."
            )
    heterostructure_summary = _heterostructure_summary(metadata)
    substrate_epitaxy_preflight_summary = _substrate_epitaxy_preflight_summary(metadata, lattice_summary)
    strain_summary = _applied_strain_summary(metadata)
    layer_profile_summary = _layer_profile_summary(spec, metadata, atom_rows)
    layer_translation_summary = _crystal_layer_translation_summary(spec, metadata, layer_profile_summary)
    if layer_translation_summary and not layer_translation_summary.get("metadata_consistent"):
        warnings.append(
            "Crystal layer-translation metadata no longer matches the current layer profile; "
            "inspect layer_translation_summary."
        )
    layer_rotation_summary = _crystal_layer_rotation_summary(spec, metadata, layer_profile_summary)
    if layer_rotation_summary:
        if not layer_rotation_summary.get("metadata_consistent"):
            warnings.append(
                "Crystal layer-rotation receipt no longer matches the current layer coordinates or profile; "
                "inspect layer_rotation_summary."
            )
        if layer_rotation_summary.get("calculation_ready") is False:
            warnings.append(
                "Crystal layer rotation is a non-commensurate visual-review scaffold; build a commensurate "
                "supercell and relax it before calculation."
            )
    castep_geometry_optimization_summary = _castep_geometry_optimization_summary(
        spec,
        metadata,
    )
    if (
        castep_geometry_optimization_summary
        and not castep_geometry_optimization_summary.get("transition_verified")
    ):
        warnings.append(
            "CASTEP geometry-optimization metadata is not bound to the current immutable "
            "revision; inspect castep_geometry_optimization_summary."
        )
    castep_electronic_result_summary = verify_castep_electronic_receipt(spec)
    castep_electronic_result_assessment = assess_castep_electronic_result(
        spec,
        receipt_summary=castep_electronic_result_summary,
    )
    if castep_electronic_result_summary:
        if not castep_electronic_result_summary.get("binding_verified"):
            warnings.append(
                "CASTEP electronic-result metadata is not bound to the current "
                "immutable revision; inspect castep_electronic_result_summary."
            )
        else:
            warnings.append(
                "CASTEP electronic backend completion does not independently verify SCF convergence in MS 20.1."
            )
            native_audit = castep_electronic_result_summary.get(
                "native_output_audit"
            )
            if isinstance(native_audit, dict) and native_audit.get("status") == (
                "review_required"
            ):
                warnings.append(
                    "CASTEP native-output audit requires review; inspect its errors and SCF markers."
                )
            if not castep_electronic_result_summary.get(
                "numeric_curve_data_exported"
            ):
                warnings.append(
                    "The requested CASTEP numeric property curve was not exported."
                )
            sampled_band_edges = castep_electronic_result_summary.get(
                "sampled_band_edges"
            )
            if isinstance(sampled_band_edges, dict):
                if sampled_band_edges.get("fermi_crossing_observed") is True:
                    warnings.append(
                        "Native sampled CASTEP bands show a Fermi-level crossing; "
                        "review metallic or semimetallic behavior."
                    )
                gap_crosscheck = sampled_band_edges.get(
                    "reported_band_gap_crosscheck"
                )
                if isinstance(gap_crosscheck, dict) and gap_crosscheck.get(
                    "status"
                ) == "review_difference":
                    warnings.append(
                        "Native sampled CASTEP band edges differ from the reported "
                        "BandGap beyond the recorded comparison tolerance."
                    )
    commensurate_twist_summary = _commensurate_tmd_twist_summary(spec, metadata)
    if commensurate_twist_summary:
        if not commensurate_twist_summary.get("metadata_consistent"):
            warnings.append(
                "Commensurate TMD twist receipt no longer matches the current lattice or atom coordinates; "
                "inspect commensurate_twist_summary."
            )
        elif commensurate_twist_summary.get("requires_geometry_relaxation"):
            warnings.append(
                "Commensurate TMD twisted bilayer is an exact periodic pre-relaxation structure; "
                "complete geometry relaxation before production calculations."
            )
    commensurate_heterobilayer_summary = _commensurate_tmd_heterobilayer_summary(spec, metadata)
    if commensurate_heterobilayer_summary:
        if not commensurate_heterobilayer_summary.get("metadata_consistent"):
            warnings.append(
                "Commensurate TMD heterobilayer receipt no longer matches the current materials, "
                "strain partition, lattice, or atom coordinates; inspect "
                "commensurate_heterobilayer_summary."
            )
        elif commensurate_heterobilayer_summary.get("requires_geometry_relaxation"):
            warnings.append(
                "Commensurate TMD heterobilayer is periodic only after the recorded biaxial strain "
                "partition and remains pre-relaxation; review strain and relax before production calculations."
            )
    interface_scaffold_summary = _interface_scaffold_summary(metadata, lattice_summary, layer_profile_summary)
    superlattice_period_summary = _superlattice_period_summary(metadata, layer_profile_summary)
    interface_profile_summary = _interface_profile_summary(metadata, layer_profile_summary, heterostructure_summary)
    quantum_well_summary = _quantum_well_summary(
        metadata,
        layer_profile_summary,
        interface_profile_summary,
        heterostructure_summary,
        superlattice_period_summary,
    )
    band_alignment_summary = _band_alignment_summary(
        metadata,
        heterostructure_summary,
        quantum_well_summary,
    )
    polarization_2deg_summary = _polarization_2deg_summary(
        metadata,
        heterostructure_summary,
        quantum_well_summary,
        band_alignment_summary,
    )
    p_gan_gate_cap_summary = _p_gan_gate_cap_summary(
        metadata,
        layer_profile_summary,
        dopant_site_summary,
        polarization_2deg_summary,
    )
    if p_gan_gate_cap_summary and p_gan_gate_cap_summary.get("quality") != "complete":
        warnings.append("p-GaN gate/cap metadata needs review; inspect p_gan_gate_cap_summary.")
    interface_quality_summary = _interface_quality_summary(
        metadata,
        interface_profile_summary,
        quantum_well_summary,
        heterostructure_summary,
        superlattice_period_summary,
    )
    oxide_interface_geometry_summary = _semiconductor_oxide_interface_geometry_summary(
        spec,
        metadata,
        atom_rows,
        neighbor_pair_rows,
        coordination_rows,
        layer_profile_summary,
        interface_profile_summary,
    )
    oxide_interface_health_summary = _semiconductor_oxide_interface_health_summary(
        metadata,
        layer_profile_summary,
        interface_profile_summary,
        interface_quality_summary,
        defect_summary,
        oxide_interface_geometry_summary,
    )
    if oxide_interface_health_summary:
        warnings.extend(
            str(item)
            for item in oxide_interface_health_summary.get("warnings", []) or []
        )
    gate_stack_summary = _gate_stack_summary(
        metadata,
        layer_profile_summary,
        interface_profile_summary,
        interface_quality_summary,
    )
    metal_semiconductor_contact_summary = _metal_semiconductor_contact_summary(
        metadata,
        layer_profile_summary,
        interface_profile_summary,
        interface_quality_summary,
    )
    surface_termination_summary = _surface_termination_summary(
        metadata,
        atom_rows,
        coordination_rows,
        expected_coordination=expected_coordination,
        expected_coordination_by_element=expected_coordination_by_element,
    )
    surface_polarity_summary = _surface_polarity_summary(surface_termination_summary, metadata)
    two_dimensional_electrostatic_summary = _two_dimensional_electrostatic_summary(
        spec,
        metadata,
        slab_vacuum,
        surface_polarity_summary,
        commensurate_heterobilayer_summary,
    )
    if two_dimensional_electrostatic_summary:
        if two_dimensional_electrostatic_summary.get("model_geometry_normality_blocker"):
            warnings.append(
                "Two-dimensional heterobilayer electrostatic preflight could not verify the current "
                "structure, expected surface asymmetry, or vacuum geometry."
            )
        elif two_dimensional_electrostatic_summary.get("calculation_review_required"):
            warnings.append(
                "Two-dimensional heterobilayer geometry is consistent with expected compositional "
                "asymmetry; review out-of-plane dipole correction before quantitative calculations."
            )
    surface_orientation_summary = _surface_orientation_summary(spec, slab_vacuum)
    if surface_orientation_summary and surface_orientation_summary.get("blocking"):
        errors.append(
            "Surface orientation metadata or lattice-axis mapping is inconsistent: "
            f"{surface_orientation_summary.get('status')}."
        )
    surface_model_summary = _surface_model_summary(
        slab_vacuum,
        surface_termination_summary,
        surface_polarity_summary,
        surface_orientation_summary,
        two_dimensional_electrostatic_summary,
    )

    return {
        "available": True,
        "ok": not errors,
        "rule": rule,
        "structure_family": metadata.get("structure_family"),
        "interface": metadata.get("interface"),
        "surface_context": surface_context,
        "operation_count": len(operations),
        "elements": elements,
        "host_elements": host_elements,
        "dopant_elements": dopant_elements,
        "lattice_summary": lattice_summary,
        "calculation_preflight_summary": calculation_preflight_summary,
        "reciprocal_lattice_summary": reciprocal_lattice_summary,
        "band_path_summary": band_path_summary,
        "neighbor_distance_summary": neighbor_distance_summary,
        "local_environment_summary": local_environment_summary,
        "composition_summary": composition_summary,
        "charge_balance_summary": charge_balance_summary,
        "sublattice_balance_summary": sublattice_balance_summary,
        "dopant_summary": dopant_summary,
        "dopant_concentration_summary": dopant_concentration_summary,
        "dopant_site_summary": dopant_site_summary,
        "carrier_intent_summary": carrier_intent_summary,
        "junction_summary": junction_summary,
        "dopant_fraction_summary": dopant_fraction_summary,
        "alloy_summary": alloy_summary,
        "defect_summary": defect_summary,
        "finite_size_summary": finite_size_summary,
        "heterostructure_summary": heterostructure_summary,
        "substrate_epitaxy_preflight_summary": substrate_epitaxy_preflight_summary,
        "strain_summary": strain_summary,
        "layer_profile_summary": layer_profile_summary,
        "layer_translation_summary": layer_translation_summary,
        "layer_rotation_summary": layer_rotation_summary,
        "castep_geometry_optimization_summary": castep_geometry_optimization_summary,
        "castep_electronic_result_summary": castep_electronic_result_summary,
        "castep_electronic_result_assessment": (
            castep_electronic_result_assessment
        ),
        "commensurate_twist_summary": commensurate_twist_summary,
        "commensurate_heterobilayer_summary": commensurate_heterobilayer_summary,
        "interface_scaffold_summary": interface_scaffold_summary,
        "interface_profile_summary": interface_profile_summary,
        "superlattice_period_summary": superlattice_period_summary,
        "quantum_well_summary": quantum_well_summary,
        "band_alignment_summary": band_alignment_summary,
        "polarization_2deg_summary": polarization_2deg_summary,
        "p_gan_gate_cap_summary": p_gan_gate_cap_summary,
        "interface_quality_summary": interface_quality_summary,
        "oxide_interface_geometry_summary": oxide_interface_geometry_summary,
        "oxide_interface_health_summary": oxide_interface_health_summary,
        "gate_stack_summary": gate_stack_summary,
        "metal_semiconductor_contact_summary": metal_semiconductor_contact_summary,
        "surface_model_summary": surface_model_summary,
        "surface_orientation_summary": surface_orientation_summary,
        "surface_termination_summary": surface_termination_summary,
        "surface_polarity_summary": surface_polarity_summary,
        "two_dimensional_electrostatic_summary": two_dimensional_electrostatic_summary,
        "expected_coordination": expected_coordination,
        "expected_coordination_by_element": dict(sorted(expected_coordination_by_element.items())),
        "neighbor_pair_counts": dict(sorted(pair_counts.items())),
        "unexpected_neighbor_pair_count": len(unexpected_pairs),
        "unexpected_neighbor_pairs": unexpected_pairs[:MAX_HEALTH_DETAIL_ROWS],
        "unchecked_neighbor_pair_count": len(unchecked_pairs),
        "unchecked_neighbor_pairs": unchecked_pairs[:MAX_HEALTH_DETAIL_ROWS],
        "passivant_neighbor_pair_count": len(passivant_pairs),
        "alloy_same_sublattice_neighbor_pair_count": len(alloy_same_sublattice_pairs),
        "alloy_same_sublattice_neighbor_pairs": alloy_same_sublattice_pairs[:MAX_HEALTH_DETAIL_ROWS],
        "same_sublattice_cutoff_artifact_pair_count": len(same_sublattice_cutoff_artifact_pairs),
        "same_sublattice_cutoff_artifact_pairs": same_sublattice_cutoff_artifact_pairs[:MAX_HEALTH_DETAIL_ROWS],
        "coordination_excluded_neighbor_pair_count": len(coordination_excluded_pair_ids),
        "coordination_excluded_pair_types": sorted(
            {
                str(row.get("pair_type"))
                for row in neighbor_pair_rows
                if id(row) in coordination_excluded_pair_ids and row.get("pair_type")
            }
        ),
        "coordination_by_element": coordination_by_element,
        "coordination_outlier_count": len(coordination_outliers),
        "coordination_outliers": coordination_outliers[:MAX_HEALTH_DETAIL_ROWS],
        "errors": errors,
        "warnings": warnings,
    }


def _expected_coordination_for_element(
    element: str,
    expected_coordination: int | None,
    expected_coordination_by_element: dict[str, int] | None,
) -> int | None:
    if element in SURFACE_PASSIVANTS:
        return None
    if expected_coordination_by_element and element in expected_coordination_by_element:
        return int(expected_coordination_by_element[element])
    return expected_coordination


def _expected_coordination_map_with_site_dopants(
    metadata: dict[str, Any],
    expected_coordination_by_element: dict[str, int],
) -> dict[str, int]:
    if not expected_coordination_by_element:
        return {}
    result = dict(expected_coordination_by_element)
    raw_entries = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_entries:
        raw_entries.append(dict(latest))
    for raw in raw_entries:
        site_element = _element_or_none(raw.get("site_element"))
        dopant_element = _element_or_none(raw.get("dopant_element") or raw.get("new_element") or raw.get("element"))
        if site_element in expected_coordination_by_element and dopant_element:
            result.setdefault(dopant_element, expected_coordination_by_element[site_element])
    return result


def _dopant_site_context_elements(metadata: dict[str, Any]) -> set[str]:
    elements: set[str] = set()
    raw_entries = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_entries:
        raw_entries.append(dict(latest))
    for raw in raw_entries:
        for value in (
            raw.get("site_element"),
            raw.get("dopant_element"),
            raw.get("new_element"),
            raw.get("element"),
        ):
            element = _element_or_none(value)
            if element:
                elements.add(element)
    return elements


def _tmd_site_roles_by_element(
    metadata: dict[str, Any],
    expected_coordination_by_element: dict[str, int],
) -> dict[str, str]:
    roles: dict[str, str] = {}
    for element in expected_coordination_by_element:
        if element in TMD_METALS:
            roles[element] = "metal"
        elif element in TMD_CHALCOGENS:
            roles[element] = "chalcogen"
    raw_entries = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_entries:
        raw_entries.append(dict(latest))
    for raw in raw_entries:
        site_element = _element_or_none(raw.get("site_element"))
        dopant_element = _element_or_none(raw.get("dopant_element") or raw.get("new_element") or raw.get("element"))
        if not dopant_element:
            continue
        if site_element in TMD_METALS:
            roles[dopant_element] = "metal"
        elif site_element in TMD_CHALCOGENS:
            roles[dopant_element] = "chalcogen"
    return roles


def _halide_perovskite_site_roles_by_element(
    metadata: dict[str, Any],
    expected_coordination_by_element: dict[str, int],
) -> dict[str, str]:
    roles: dict[str, str] = {}
    for element in expected_coordination_by_element:
        if element in HALIDE_PEROVSKITE_B_CATIONS:
            roles[element] = "b_cation"
        elif element in HALIDE_PEROVSKITE_HALIDES:
            roles[element] = "halide"
    raw_entries = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_entries:
        raw_entries.append(dict(latest))
    for raw in raw_entries:
        site_element = _element_or_none(raw.get("site_element"))
        dopant_element = _element_or_none(raw.get("dopant_element") or raw.get("new_element") or raw.get("element"))
        if not dopant_element:
            continue
        if site_element in HALIDE_PEROVSKITE_B_CATIONS:
            roles[dopant_element] = "b_cation"
        elif site_element in HALIDE_PEROVSKITE_HALIDES:
            roles[dopant_element] = "halide"
    return roles


def _surface_termination_summary(
    metadata: dict[str, Any],
    atom_rows: list[dict[str, Any]],
    coordination_rows: list[dict[str, Any]],
    *,
    expected_coordination: int | None,
    expected_coordination_by_element: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    if not _surface_slab_diagnostics_applicable(metadata):
        return None
    axis_name = str(metadata.get("surface_axis") or "c").lower()
    axis_key = {"x": "a", "y": "b", "z": "c"}.get(axis_name, axis_name)
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_key)
    if axis_index is None:
        return {
            "available": False,
            "surface_axis": axis_name,
            "warning": "Unsupported surface_axis for surface termination diagnostics.",
            "surface_preparation_status": "unsupported_axis",
            "surface_preparation_next_action": "fix_surface_axis_metadata_before_surface_diagnostics",
        }
    atoms_with_fractional = [
        atom
        for atom in atom_rows
        if atom.get("fractional") is not None and atom.get("element") not in SURFACE_PASSIVANTS
    ]
    if not atoms_with_fractional:
        return None
    coord_map = {str(row.get("atom_id")): row for row in coordination_rows}
    values = [float(atom["fractional"][axis_index]) for atom in atoms_with_fractional]
    fractional_min = min(values)
    fractional_max = max(values)
    tolerance = 1e-5
    surface_rows = {
        "bottom": [
            atom
            for atom in atoms_with_fractional
            if abs(float(atom["fractional"][axis_index]) - fractional_min) <= tolerance
        ],
        "top": [
            atom
            for atom in atoms_with_fractional
            if abs(float(atom["fractional"][axis_index]) - fractional_max) <= tolerance
        ],
    }
    surfaces = {}
    total_dangling = 0
    total_passivant_bonds = 0
    total_undercoordinated = 0
    for surface, atoms in surface_rows.items():
        atom_summaries = []
        dangling_count = 0
        passivant_bonds = 0
        undercoordinated_count = 0
        for atom in sorted(atoms, key=lambda item: str(item.get("id"))):
            atom_id = str(atom.get("id"))
            coord = coord_map.get(atom_id, {})
            neighbor_count = int(coord.get("neighbor_count") or 0)
            passivant_neighbor_count = sum(1 for element in coord.get("neighbor_elements") or [] if element in SURFACE_PASSIVANTS)
            atom_expected_coordination = _expected_coordination_for_element(
                str(atom.get("element") or ""),
                expected_coordination,
                expected_coordination_by_element,
            )
            missing = max((atom_expected_coordination or neighbor_count) - neighbor_count, 0)
            if missing:
                undercoordinated_count += 1
            dangling_count += missing
            passivant_bonds += passivant_neighbor_count
            atom_summaries.append(
                {
                    "atom_id": atom_id,
                    "element": atom.get("element"),
                    "neighbor_count": neighbor_count,
                    "expected_coordination": atom_expected_coordination,
                    "dangling_bond_estimate": missing,
                    "passivant_neighbor_count": passivant_neighbor_count,
                    "neighbor_ids": coord.get("neighbor_ids") or [],
                    "neighbor_elements": coord.get("neighbor_elements") or [],
                }
            )
        total_sites = dangling_count + passivant_bonds
        surfaces[surface] = {
            "atom_count": len(atoms),
            "surface_atom_ids": [str(atom.get("id")) for atom in sorted(atoms, key=lambda item: str(item.get("id")))],
            "element_counts": dict(sorted(Counter(str(atom.get("element")) for atom in atoms).items())),
            "undercoordinated_atom_count": undercoordinated_count,
            "dangling_bond_estimate": dangling_count,
            "passivant_bond_count": passivant_bonds,
            "passivation_coverage_fraction": _round(passivant_bonds / total_sites) if total_sites else None,
            "atoms": atom_summaries,
        }
        total_dangling += dangling_count
        total_passivant_bonds += passivant_bonds
        total_undercoordinated += undercoordinated_count
    total_sites = total_dangling + total_passivant_bonds
    passivation = metadata.get("passivation") if isinstance(metadata.get("passivation"), dict) else {}
    passivation_coverage = _round(total_passivant_bonds / total_sites) if total_sites else None
    fully_passivated = bool(total_passivant_bonds and total_dangling == 0)
    preparation_status, preparation_next_action = _surface_preparation_status(
        dangling_bond_estimate=total_dangling,
        passivant_bond_count=total_passivant_bonds,
        fully_passivated=fully_passivated,
    )
    return {
        "available": True,
        "surface_orientation": metadata.get("surface_orientation"),
        "surface_axis": axis_key,
        "termination": metadata.get("termination"),
        "passivation": passivation or None,
        "expected_coordination": expected_coordination,
        "expected_coordination_by_element": dict(sorted((expected_coordination_by_element or {}).items())),
        "surface_atom_count": sum(surface["atom_count"] for surface in surfaces.values()),
        "undercoordinated_surface_atom_count": total_undercoordinated,
        "dangling_bond_estimate": total_dangling,
        "passivant_bond_count": total_passivant_bonds,
        "passivation_coverage_fraction": passivation_coverage,
        "fully_passivated": fully_passivated,
        "surface_preparation_status": preparation_status,
        "surface_preparation_next_action": preparation_next_action,
        "surfaces": surfaces,
    }


def _surface_preparation_status(
    *,
    dangling_bond_estimate: int,
    passivant_bond_count: int,
    fully_passivated: bool,
) -> tuple[str, str]:
    if dangling_bond_estimate > 0 and passivant_bond_count > 0:
        return (
            "partially_passivated_with_dangling_bonds",
            "passivate_remaining_surface_dangling_bonds_before_calculation_or_claiming_normality",
        )
    if dangling_bond_estimate > 0:
        return (
            "dangling_bonds",
            "passivate_surface_dangling_bonds_before_calculation_or_claiming_normality",
        )
    if fully_passivated:
        return "fully_passivated", "surface_passivation_ready"
    return "no_dangling_bonds_detected", "surface_coordination_ready_review_polarity_before_calculation"


def _surface_model_summary(
    slab_vacuum: dict[str, Any] | None,
    surface_termination: dict[str, Any] | None,
    surface_polarity: dict[str, Any] | None,
    surface_orientation: dict[str, Any] | None = None,
    two_dimensional_electrostatics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not slab_vacuum and not surface_termination and not surface_polarity and not surface_orientation:
        return None

    vacuum = slab_vacuum or {}
    termination = surface_termination or {}
    polarity = surface_polarity or {}
    orientation = surface_orientation or {}
    electrostatics = two_dimensional_electrostatics or {}
    vacuum_status = vacuum.get("slab_vacuum_status")
    preparation_status = termination.get("surface_preparation_status")
    polarity_status = polarity.get("surface_polarity_status")
    orientation_status = orientation.get("status")

    blocking_reasons: list[str] = []
    review_reasons: list[str] = []
    if vacuum_status not in {None, "ready"}:
        blocking_reasons.append(f"slab_vacuum:{vacuum_status}")
    if orientation.get("blocking"):
        blocking_reasons.append(f"surface_orientation:{orientation_status}")
    if preparation_status in {
        "unsupported_axis",
        "dangling_bonds",
        "partially_passivated_with_dangling_bonds",
    }:
        blocking_reasons.append(f"surface_preparation:{preparation_status}")
    elif preparation_status not in {
        None,
        "fully_passivated",
        "no_dangling_bonds_detected",
    }:
        review_reasons.append(f"surface_preparation:{preparation_status}")
    if electrostatics.get("model_geometry_normality_blocker"):
        blocking_reasons.append("two_dimensional_electrostatics:model_geometry_unverified")
    elif electrostatics.get("calculation_review_required"):
        review_reasons.append("two_dimensional_electrostatics:dipole_correction_review_required")
    elif polarity.get("expected_2d_heterobilayer_asymmetry") is True:
        pass
    elif polarity_status == "asymmetric_or_polar":
        review_reasons.append("surface_polarity:asymmetric_or_polar")
    elif polarity_status not in {None, "symmetric_nonpolar"}:
        review_reasons.append(f"surface_polarity:{polarity_status}")

    if blocking_reasons:
        status = "blocked"
        if orientation.get("blocking"):
            next_action = orientation.get("next_action") or "fix_surface_orientation_metadata_or_lattice"
        elif vacuum_status not in {None, "ready"}:
            next_action = vacuum.get("slab_vacuum_next_action") or "fix_slab_vacuum_before_calculation"
        else:
            next_action = termination.get("surface_preparation_next_action") or "fix_surface_preparation_before_calculation"
    elif review_reasons:
        status = (
            "calculation_review"
            if electrostatics.get("model_geometry_verified") is True
            and electrostatics.get("calculation_review_required") is True
            else "review"
        )
        if status == "calculation_review":
            next_action = electrostatics.get("next_action") or "review_2d_dipole_correction_before_calculation"
        elif polarity_status and polarity_status != "symmetric_nonpolar":
            next_action = polarity.get("surface_polarity_next_action") or "review_surface_polarity_before_calculation"
        else:
            next_action = termination.get("surface_preparation_next_action") or "review_surface_preparation_before_calculation"
    else:
        status = "ready"
        next_action = "surface_model_ready_for_calculation_preflight"

    return {
        "available": True,
        "status": status,
        "ready_for_calculation_preflight": status == "ready",
        "model_geometry_ready": status in {"ready", "calculation_review"},
        "calculation_review_only": status == "calculation_review",
        "next_action": next_action,
        "slab_vacuum_status": vacuum_status,
        "surface_preparation_status": preparation_status,
        "surface_polarity_status": polarity_status,
        "surface_orientation_status": orientation_status,
        "surface_orientation_summary": orientation or None,
        "two_dimensional_electrostatic_status": electrostatics.get("status"),
        "two_dimensional_electrostatic_summary": electrostatics or None,
        "blocking_reasons": blocking_reasons,
        "review_reasons": review_reasons,
    }


def _parse_surface_plane_indices(surface_orientation: str) -> tuple[tuple[int, ...] | None, str | None]:
    match = re.search(r"\(\s*([-0-9,\s]+?)\s*\)", surface_orientation)
    if match is None:
        return None, "Surface orientation does not contain Miller or Miller-Bravais indices."
    content = match.group(1)
    compact = re.sub(r"[\s,]", "", content)
    known = {
        "".join(str(value) for value in indices): indices
        for indices in CRYSTAL_PLANE_VIEW_INDICES.values()
    }
    if compact in known:
        return known[compact], None
    if re.fullmatch(r"\d{3,4}", compact):
        return tuple(int(value) for value in compact), None
    compact_signed_values = tuple(int(value) for value in re.findall(r"-?\d", compact))
    if (
        len(compact_signed_values) in {3, 4}
        and "".join(str(value) for value in compact_signed_values) == compact
    ):
        return compact_signed_values, None
    if "," in content or re.search(r"\s", content.strip()):
        values = tuple(int(value) for value in re.findall(r"-?\d+", content))
        if len(values) in {3, 4}:
            return values, None
    return None, f"Unsupported or ambiguous surface orientation indices: ({content.strip()})."


def _surface_orientation_summary(
    spec: ModelSpec,
    slab_vacuum: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec) or not slab_vacuum:
        return None
    metadata = spec.metadata or {}
    surface_axis = str(metadata.get("surface_axis") or slab_vacuum.get("surface_axis") or "c").lower()
    axis_index = {"a": 0, "b": 1, "c": 2}.get(surface_axis)
    declared_orientation = str(metadata.get("surface_orientation") or "").strip()
    raw_basis = str(metadata.get("surface_orientation_basis") or "").strip().lower()
    if raw_basis in {"current_cell", "current_crystal_cell"}:
        orientation_basis = "current_cell"
    elif raw_basis in {"parent_bulk", "source_bulk", "parent_bulk_mapped_to_cell_axis", ""}:
        orientation_basis = "parent_bulk_mapped_to_cell_axis" if declared_orientation else None
    else:
        orientation_basis = raw_basis
    mapped_axis = str(metadata.get("surface_normal_cell_axis") or surface_axis).lower()
    vectors = _lattice_vectors(spec.model.lattice)
    axis_vector = _normalize(vectors[axis_index]) if axis_index is not None else None
    base = {
        "available": True,
        "declared_surface_orientation": declared_orientation or None,
        "orientation_basis": orientation_basis,
        "orientation_basis_source": "explicit_metadata" if raw_basis else "default_surface_metadata_convention",
        "surface_axis": surface_axis,
        "surface_axis_cartesian": _round_tuple(axis_vector) if axis_vector is not None else None,
        "surface_axis_length_angstrom": (
            _round(math.sqrt(_dot(vectors[axis_index], vectors[axis_index])))
            if axis_index is not None
            else None
        ),
        "mapped_surface_normal_cell_axis": mapped_axis,
        "mapping_axis_matches_surface_axis": mapped_axis == surface_axis,
        "alignment_tolerance_degrees": 1.0,
        "reciprocal_convention": "dual_basis_without_2pi",
    }
    if axis_index is None:
        return {
            **base,
            "status": "unsupported_surface_axis",
            "ok": False,
            "blocking": True,
            "validation_level": "metadata_axis_validation",
            "alignment_applicable": False,
            "axis_plane_alignment_ok": None,
            "next_action": "set_surface_axis_to_a_b_or_c_before_claiming_normality",
            "warning": "Unsupported surface_axis for orientation diagnostics.",
        }
    if mapped_axis != surface_axis:
        return {
            **base,
            "status": "surface_axis_mapping_mismatch",
            "ok": False,
            "blocking": True,
            "validation_level": "metadata_axis_mapping",
            "alignment_applicable": False,
            "axis_plane_alignment_ok": None,
            "next_action": "make_surface_normal_cell_axis_match_surface_axis",
            "warning": "surface_normal_cell_axis does not match the slab vacuum surface_axis.",
        }
    if not declared_orientation:
        return {
            **base,
            "status": "surface_axis_only",
            "ok": True,
            "blocking": False,
            "validation_level": "surface_axis_geometry",
            "alignment_applicable": False,
            "axis_plane_alignment_ok": None,
            "next_action": "surface_axis_geometry_available_no_parent_plane_declared",
            "warning": None,
        }
    plane_indices, parse_warning = _parse_surface_plane_indices(declared_orientation)
    if plane_indices is None:
        return {
            **base,
            "status": "invalid_surface_orientation",
            "ok": False,
            "blocking": True,
            "validation_level": "surface_orientation_parse",
            "alignment_applicable": False,
            "axis_plane_alignment_ok": None,
            "next_action": "fix_surface_orientation_miller_indices",
            "warning": parse_warning,
        }
    plane_label = "(" + "".join(str(value) for value in plane_indices) + ")"
    indexed = {
        **base,
        "surface_plane_indices": list(plane_indices),
        "surface_plane_label": plane_label,
    }
    if orientation_basis == "parent_bulk_mapped_to_cell_axis":
        return {
            **indexed,
            "status": "parent_plane_mapped_to_surface_axis",
            "ok": True,
            "blocking": False,
            "validation_level": "parent_plane_to_cell_axis_metadata_mapping",
            "alignment_applicable": False,
            "axis_plane_alignment_ok": None,
            "next_action": "surface_orientation_mapping_and_vacuum_axis_consistent",
            "warning": None,
        }
    if orientation_basis != "current_cell":
        return {
            **indexed,
            "status": "unsupported_orientation_basis",
            "ok": False,
            "blocking": True,
            "validation_level": "orientation_basis_validation",
            "alignment_applicable": False,
            "axis_plane_alignment_ok": None,
            "next_action": "use_parent_bulk_mapped_to_cell_axis_or_current_cell_orientation_basis",
            "warning": f"Unsupported surface_orientation_basis: {orientation_basis}.",
        }
    plane_geometry, plane_warning = _reciprocal_plane_geometry(vectors, plane_indices)
    if plane_geometry is None:
        return {
            **indexed,
            "status": "invalid_current_cell_plane",
            "ok": False,
            "blocking": True,
            "validation_level": "current_cell_reciprocal_lattice",
            "alignment_applicable": True,
            "axis_plane_alignment_ok": False,
            "next_action": "fix_current_cell_surface_plane_indices_or_lattice",
            "warning": plane_warning,
        }
    plane_normal = plane_geometry["normal_cartesian"]
    cosine = min(max(abs(_dot(axis_vector, plane_normal)), 0.0), 1.0)
    angle = math.degrees(math.acos(cosine))
    alignment_ok = angle <= 1.0
    return {
        **indexed,
        "status": "current_cell_plane_aligned" if alignment_ok else "current_cell_plane_axis_mismatch",
        "ok": alignment_ok,
        "blocking": not alignment_ok,
        "validation_level": "current_cell_reciprocal_lattice_numeric",
        "alignment_applicable": True,
        "plane_normal_cartesian": _round_tuple(plane_normal),
        "plane_spacing_angstrom": _round(float(plane_geometry["spacing_angstrom"])),
        "axis_plane_alignment_cosine_abs": _round(cosine),
        "axis_plane_alignment_angle_degrees": _round(angle),
        "axis_plane_alignment_ok": alignment_ok,
        "next_action": (
            "surface_plane_normal_and_vacuum_axis_aligned"
            if alignment_ok
            else "reorient_cell_or_fix_surface_orientation_and_surface_axis_before_claiming_normality"
        ),
        "warning": None if alignment_ok else "Current-cell surface plane normal is not aligned with surface_axis.",
    }


def _surface_polarity_summary(
    surface_termination: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not surface_termination or not surface_termination.get("available"):
        return None
    surfaces = surface_termination.get("surfaces") or {}
    bottom = surfaces.get("bottom") or {}
    top = surfaces.get("top") or {}
    if not bottom or not top:
        return None

    bottom_counts = Counter({str(key): int(value) for key, value in (bottom.get("element_counts") or {}).items()})
    top_counts = Counter({str(key): int(value) for key, value in (top.get("element_counts") or {}).items()})
    bottom_formula = _format_formula(bottom_counts) if bottom_counts else None
    top_formula = _format_formula(top_counts) if top_counts else None
    same_element_counts = dict(sorted(bottom_counts.items())) == dict(sorted(top_counts.items()))
    bottom_passivant = int(bottom.get("passivant_bond_count") or 0)
    top_passivant = int(top.get("passivant_bond_count") or 0)
    passivation_symmetric = bottom_passivant == top_passivant
    polar_surface_hint = not same_element_counts
    metadata = metadata or {}
    surface_asymmetry_observed = bool(polar_surface_hint or not passivation_symmetric)
    expected_reason = metadata.get("surface_asymmetry_expected_reason")
    surface_asymmetry_expected = bool(
        metadata.get("surface_asymmetry_expected")
        or (
            metadata.get("metal_semiconductor_interface")
            and metadata.get("pre_relaxation_scaffold")
        )
    )
    surface_asymmetry_warning = bool(surface_asymmetry_observed and not surface_asymmetry_expected)
    warnings: list[str] = []
    if polar_surface_hint and not surface_asymmetry_expected:
        warnings.append("Top and bottom slab surfaces have different non-passivant element counts; inspect possible polar/asymmetric termination.")
    if not passivation_symmetric and not surface_asymmetry_expected:
        warnings.append("Top and bottom slab surfaces have different passivant-bond counts; inspect asymmetric passivation.")
    expected_2d_heterobilayer = bool(
        surface_asymmetry_expected
        and metadata.get("two_dimensional_electrostatic_preflight_required")
        and expected_reason == "distinct_tmd_layers_in_vdw_heterobilayer"
    )
    if expected_2d_heterobilayer:
        polarity_status = (
            "asymmetric_expected_2d_heterobilayer"
            if surface_asymmetry_observed
            else "outer_surfaces_symmetric_2d_heterobilayer_composition_expected"
        )
        polarity_next_action = "review_2d_out_of_plane_dipole_correction_before_quantitative_calculation"
    elif surface_asymmetry_observed and surface_asymmetry_expected:
        polarity_status = "asymmetric_expected"
        polarity_next_action = (
            "review_unrelaxed_metal_semiconductor_interface_before_calculation"
            if metadata.get("metal_semiconductor_interface")
            else "review_expected_surface_asymmetry_before_calculation"
        )
    elif surface_asymmetry_warning:
        polarity_status = "asymmetric_or_polar"
        polarity_next_action = "review_surface_polarity_or_apply_symmetric_passivation_before_calculation"
    else:
        polarity_status = "symmetric_nonpolar"
        polarity_next_action = "surface_polarity_ready"

    return {
        "available": True,
        "model": "surface_element_and_passivation_symmetry_heuristic",
        "surface_orientation": surface_termination.get("surface_orientation"),
        "surface_axis": surface_termination.get("surface_axis"),
        "termination": surface_termination.get("termination"),
        "bottom": {
            "formula": bottom_formula,
            "element_counts": dict(sorted(bottom_counts.items())),
            "atom_count": bottom.get("atom_count"),
            "surface_atom_ids": bottom.get("surface_atom_ids") or [],
            "dangling_bond_estimate": bottom.get("dangling_bond_estimate"),
            "passivant_bond_count": bottom_passivant,
        },
        "top": {
            "formula": top_formula,
            "element_counts": dict(sorted(top_counts.items())),
            "atom_count": top.get("atom_count"),
            "surface_atom_ids": top.get("surface_atom_ids") or [],
            "dangling_bond_estimate": top.get("dangling_bond_estimate"),
            "passivant_bond_count": top_passivant,
        },
        "same_element_counts": same_element_counts,
        "passivation_symmetric": passivation_symmetric,
        "polar_surface_hint": polar_surface_hint,
        "surface_asymmetry_observed": surface_asymmetry_observed,
        "surface_asymmetry_expected": surface_asymmetry_expected,
        "surface_asymmetry_expected_reason": expected_reason,
        "expected_2d_heterobilayer_asymmetry": expected_2d_heterobilayer,
        "two_dimensional_electrostatic_review_required": expected_2d_heterobilayer,
        "surface_asymmetry_warning": surface_asymmetry_warning,
        "surface_polarity_status": polarity_status,
        "surface_polarity_next_action": polarity_next_action,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _two_dimensional_electrostatic_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    slab_vacuum: dict[str, Any] | None,
    surface_polarity: dict[str, Any] | None,
    commensurate_heterobilayer: dict[str, Any] | None,
) -> dict[str, Any] | None:
    required = bool(
        metadata.get("two_dimensional_electrostatic_preflight_required")
        or metadata.get("commensurate_tmd_heterobilayer")
    )
    if not required:
        return None

    vacuum = slab_vacuum or {}
    polarity = surface_polarity or {}
    heterobilayer = commensurate_heterobilayer or {}
    latest = heterobilayer.get("latest") if isinstance(heterobilayer.get("latest"), dict) else {}
    bottom = polarity.get("bottom") if isinstance(polarity.get("bottom"), dict) else {}
    top = polarity.get("top") if isinstance(polarity.get("top"), dict) else {}
    bottom_material = heterobilayer.get("bottom_material") or latest.get("bottom_material")
    top_material = heterobilayer.get("top_material") or latest.get("top_material")
    bottom_layer_element_counts = heterobilayer.get("bottom_layer_element_counts") or {}
    top_layer_element_counts = heterobilayer.get("top_layer_element_counts") or {}
    simulation = spec.simulation if isinstance(spec.simulation, CastepEnergySpec) else None
    dipole_mode = simulation.dipole_correction if simulation is not None else None
    dipole_setting_configured = dipole_mode is not None
    dipole_enabled = dipole_mode in {
        CastepDipoleCorrection.SELF_CONSISTENT,
        CastepDipoleCorrection.NON_SELF_CONSISTENT,
    }
    dipole_task = normalize_castep_task(simulation.task) if simulation is not None else None
    dipole_task_compatible = bool(
        dipole_enabled
        and (
            dipole_mode is CastepDipoleCorrection.SELF_CONSISTENT
            or dipole_task is CastepTask.ENERGY
        )
    )
    dipole_vacuum_requirement_met = bool(vacuum.get("vacuum_ok") is True)

    crystal_model = isinstance(spec.model, CrystalSpec)
    structure_binding_verified = bool(
        crystal_model
        and heterobilayer.get("metadata_consistent") is True
        and heterobilayer.get("structure_binding_matches_current") is True
        and heterobilayer.get("layer_materials_verified") is True
    )
    expected_reason = metadata.get("surface_asymmetry_expected_reason")
    expected_compositional_asymmetry = bool(
        polarity.get("surface_asymmetry_expected") is True
        and polarity.get("expected_2d_heterobilayer_asymmetry") is True
        and expected_reason == "distinct_tmd_layers_in_vdw_heterobilayer"
        and heterobilayer.get("materials_distinct") is True
        and heterobilayer.get("layer_materials_verified") is True
        and bottom_material
        and top_material
        and bottom_material != top_material
        and bottom_layer_element_counts
        and top_layer_element_counts
        and bottom_layer_element_counts != top_layer_element_counts
    )
    outer_surface_formulas_distinct = bool(
        bottom.get("formula")
        and top.get("formula")
        and bottom.get("formula") != top.get("formula")
    )
    vacuum_geometry_verified = bool(
        vacuum.get("slab_vacuum_status") == "ready"
        and vacuum.get("vacuum_ok") is True
        and vacuum.get("centered_in_cell") is True
        and vacuum.get("metadata_cell_mismatch") is False
    )
    model_geometry_verified = bool(
        structure_binding_verified
        and expected_compositional_asymmetry
        and vacuum_geometry_verified
    )
    dipole_setting_verified = bool(
        model_geometry_verified
        and dipole_enabled
        and dipole_task_compatible
        and dipole_vacuum_requirement_met
    )
    geometry_relaxation_required = bool(
        heterobilayer.get("requires_geometry_relaxation")
        and heterobilayer.get("geometry_relaxed") is not True
    )
    castep_relaxation_transition = (
        heterobilayer.get("castep_relaxation_transition") or {}
    )
    geometry_relaxation_verified = bool(
        heterobilayer.get("castep_relaxation_transition_verified") is True
        and heterobilayer.get("geometry_relaxed") is True
    )
    quantitative_calculation_ready = bool(
        model_geometry_verified
        and dipole_setting_verified
        and not geometry_relaxation_required
    )

    warnings: list[str] = []
    if not crystal_model:
        warnings.append("Two-dimensional electrostatic preflight requires a crystal model.")
    if not structure_binding_verified:
        warnings.append(
            "Current structure or layer composition is not bound to the commensurate TMD heterobilayer receipt."
        )
    if not expected_compositional_asymmetry:
        warnings.append(
            "Current bottom/top layer materials or element counts do not verify the declared "
            "two-dimensional heterobilayer compositional asymmetry."
        )
    if not vacuum_geometry_verified:
        warnings.append("Slab vacuum spacing, centering, or metadata binding is not ready for electrostatic review.")
    if simulation is None:
        warnings.append(
            "No CASTEP simulation is configured, so the verified Materials Studio 20.1 "
            "DipoleCorrection setting is not present in the current ModelSpec."
        )
    elif not dipole_enabled:
        warnings.append(
            "CASTEP DipoleCorrection is not enabled for the current two-dimensional heterobilayer."
        )
    elif not dipole_task_compatible:
        warnings.append(
            "The selected CASTEP dipole-correction mode is not compatible with the configured task."
        )
    elif not dipole_vacuum_requirement_met:
        warnings.append(
            "CASTEP dipole correction requires at least 8 angstrom of slab vacuum."
        )
    warnings.append(
        "No charge density is available in ModelSpec diagnostics, so the out-of-plane dipole moment "
        "has not been calculated; this preflight verifies the calculation input setting only."
    )

    calculation_blocking_reasons = []
    if not model_geometry_verified:
        calculation_blocking_reasons.append("two_dimensional_electrostatic_model_geometry_unverified")
    if not dipole_setting_verified:
        calculation_blocking_reasons.append("two_dimensional_dipole_correction_review_required")
    if geometry_relaxation_required:
        calculation_blocking_reasons.append(
            "commensurate_tmd_heterobilayer_requires_geometry_relaxation"
        )
    if not model_geometry_verified:
        status = "model_review_required"
        quality = "review_required"
        next_action = "fix_2d_model_geometry_before_quantitative_calculation"
    elif not dipole_setting_verified:
        status = "model_geometry_verified_calculation_review"
        quality = "preflight_complete"
        next_action = "configure_verified_castep_dipole_correction_before_quantitative_calculation"
    elif geometry_relaxation_required:
        status = "dipole_correction_verified_geometry_relaxation_required"
        quality = "dipole_correction_verified"
        next_action = "relax_commensurate_tmd_heterobilayer_before_quantitative_calculation"
    else:
        status = "quantitative_electrostatic_input_ready"
        quality = "calculation_input_ready"
        next_action = "preview_or_explicitly_execute_quantitative_electrostatic_calculation"
    return {
        "available": True,
        "model": "metadata_surface_and_vacuum_preflight_without_charge_density",
        "status": status,
        "quality": quality,
        "bottom_material": bottom_material,
        "top_material": top_material,
        "surface_axis": polarity.get("surface_axis") or vacuum.get("surface_axis"),
        "surface_orientation": polarity.get("surface_orientation") or vacuum.get("surface_orientation"),
        "bottom_surface_formula": bottom.get("formula"),
        "top_surface_formula": top.get("formula"),
        "bottom_surface_element_counts": bottom.get("element_counts") or {},
        "top_surface_element_counts": top.get("element_counts") or {},
        "bottom_layer_element_counts": bottom_layer_element_counts,
        "top_layer_element_counts": top_layer_element_counts,
        "surface_asymmetry_expected": polarity.get("surface_asymmetry_expected") is True,
        "surface_asymmetry_expected_reason": expected_reason,
        "expected_compositional_asymmetry_verified": expected_compositional_asymmetry,
        "outer_surface_asymmetry_observed": polarity.get("surface_asymmetry_observed") is True,
        "outer_surface_formulas_distinct": outer_surface_formulas_distinct,
        "periodic_out_of_plane_boundary": crystal_model,
        "cell_axis_length_angstrom": vacuum.get("cell_axis_length_angstrom"),
        "declared_vacuum_angstrom": vacuum.get("declared_vacuum_angstrom"),
        "bottom_vacuum_angstrom": vacuum.get("bottom_vacuum_angstrom"),
        "top_vacuum_angstrom": vacuum.get("top_vacuum_angstrom"),
        "vacuum_asymmetry_abs_angstrom": vacuum.get("vacuum_asymmetry_abs_angstrom"),
        "vacuum_geometry_verified": vacuum_geometry_verified,
        "structure_binding_verified": structure_binding_verified,
        "expected_structure_sha256": heterobilayer.get("expected_structure_sha256"),
        "current_structure_sha256": heterobilayer.get("current_structure_sha256"),
        "model_geometry_verified": model_geometry_verified,
        "model_geometry_normality_blocker": not model_geometry_verified,
        "charge_density_available": False,
        "dipole_moment_calculated": False,
        "dipole_correction_api_verified": True,
        "dipole_correction_api_contract": CASTEP_DIPOLE_CORRECTION_API_CONTRACT,
        "dipole_correction_api_property": CASTEP_DIPOLE_CORRECTION_API_PROPERTY,
        "dipole_correction_direction_property_exposed": False,
        "dipole_correction_direction_status": "not_exposed_by_verified_materialscript_contract",
        "dipole_correction_setting_source": "model_spec.simulation.dipole_correction",
        "dipole_correction_setting_configured": dipole_setting_configured,
        "dipole_correction_mode": dipole_mode.value if dipole_mode is not None else None,
        "dipole_correction_enabled": dipole_enabled,
        "dipole_correction_task": dipole_task.value if dipole_task is not None else None,
        "dipole_correction_task_compatible": dipole_task_compatible,
        "dipole_correction_minimum_vacuum_angstrom": CASTEP_DIPOLE_MINIMUM_VACUUM_ANGSTROM,
        "dipole_correction_vacuum_requirement_met": dipole_vacuum_requirement_met,
        "dipole_correction_symmetry_behavior": "materials_studio_converts_molecule_or_slab_to_p1",
        "dipole_correction_setting_verified": dipole_setting_verified,
        "dipole_correction_review_method": "structured_materialscript_setting_verified_against_ms20_1_help",
        "geometry_relaxation_required": geometry_relaxation_required,
        "geometry_relaxation_verified": geometry_relaxation_verified,
        "geometry_relaxation_source_revision": castep_relaxation_transition.get(
            "source_revision"
        ),
        "geometry_relaxation_target_revision": castep_relaxation_transition.get(
            "target_revision"
        ),
        "geometry_relaxation_output_structure_sha256": (
            castep_relaxation_transition.get("output_structure_sha256")
        ),
        "calculation_review_required": not dipole_setting_verified,
        "quantitative_electrostatic_calculation_ready": quantitative_calculation_ready,
        "calculation_blocking_reasons": calculation_blocking_reasons,
        "next_action": next_action,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _has_heterostructure_context(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    return bool(
        metadata.get("interface")
        or metadata.get("heterostructure")
        or metadata.get("superlattice")
        or metadata.get("quantum_well")
        or metadata.get("gate_stack")
        or metadata.get("mos_capacitor")
        or any(
            term in family
            for term in (
                "heterostructure",
                "superlattice",
                "quantum well",
                "mqw",
                "interface",
                "gate stack",
                "mos capacitor",
                "schottky contact",
            )
        )
    )


def _heterostructure_summary(metadata: dict[str, Any]) -> dict[str, Any] | None:
    interface = metadata.get("interface")
    materials = metadata.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    materials = [str(material) for material in materials if str(material)]
    if not _has_heterostructure_context(metadata) or (not interface and len(materials) < 2):
        return None

    in_plane = _optional_float(metadata.get("in_plane_lattice_angstrom"))
    substrate = str(metadata.get("substrate") or "") or None
    substrate_reference = _material_reference_lattice(metadata, substrate) if substrate else None
    material_rows = []
    strain_values: list[float] = []
    mismatch_values: list[float] = []
    for material in materials:
        reference = _material_reference_lattice(metadata, material)
        strain = None
        mismatch_to_substrate = None
        if reference is not None and in_plane is not None:
            strain = 100.0 * (in_plane - reference) / reference
            strain_values.append(strain)
        if reference is not None and substrate_reference is not None:
            mismatch_to_substrate = 100.0 * (reference - substrate_reference) / substrate_reference
            mismatch_values.append(mismatch_to_substrate)
        material_rows.append(
            {
                "material": material,
                "reference_lattice_angstrom": _round(reference) if reference is not None else None,
                "in_plane_strain_percent": _round(strain) if strain is not None else None,
                "lattice_mismatch_to_substrate_percent": _round(mismatch_to_substrate)
                if mismatch_to_substrate is not None
                else None,
                "is_substrate": material == substrate,
            }
        )

    return {
        "available": True,
        "interface": interface,
        "interface_orientation": metadata.get("interface_orientation"),
        "interface_axis": metadata.get("interface_axis"),
        "materials": materials,
        "substrate": substrate,
        "coherent_strain_model": metadata.get("coherent_strain_model"),
        "in_plane_lattice_angstrom": _round(in_plane) if in_plane is not None else None,
        "materials_detail": material_rows,
        "max_abs_in_plane_strain_percent": _round(max((abs(value) for value in strain_values), default=0.0))
        if strain_values
        else None,
        "max_abs_lattice_mismatch_to_substrate_percent": _round(
            max((abs(value) for value in mismatch_values), default=0.0)
        )
        if mismatch_values
        else None,
        "strain_warning": bool(strain_values and max(abs(value) for value in strain_values) > 5.0),
    }


def _substrate_epitaxy_preflight_summary(
    metadata: dict[str, Any],
    lattice_summary: dict[str, Any],
) -> dict[str, Any] | None:
    raw_targets = metadata.get("epitaxy_targets") or metadata.get("substrate_epitaxy_targets") or []
    if not isinstance(raw_targets, list) or not raw_targets:
        return None

    reference_lattice = metadata.get("reference_lattice_angstrom")
    substrate_reference_a = None
    if isinstance(reference_lattice, dict):
        substrate_reference_a = _optional_float(reference_lattice.get("a"))
    if substrate_reference_a is None:
        substrate_reference_a = _optional_float(lattice_summary.get("a_angstrom"))

    substrate_material = str(metadata.get("material") or metadata.get("substrate_material") or "").strip() or None
    substrate_orientation = (
        str(metadata.get("substrate_orientation") or metadata.get("surface_orientation") or "").strip()
        or None
    )
    requested_target_material = str(metadata.get("nl_epitaxy_target") or metadata.get("epitaxy_target") or "").strip() or None
    direct_values: list[float] = []
    domain_values: list[float] = []
    target_rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, raw_target in enumerate(raw_targets, start=1):
        if not isinstance(raw_target, dict):
            warnings.append(f"epitaxy_targets[{index}] is not an object and was skipped.")
            continue

        material = str(raw_target.get("material") or "").strip()
        film_reference = _optional_float(raw_target.get("film_reference_lattice_angstrom"))
        if film_reference is None:
            film_reference = _material_reference_lattice(metadata, material)
        direct_substrate_spacing = _optional_float(raw_target.get("direct_substrate_spacing_angstrom"))
        if direct_substrate_spacing is None:
            direct_substrate_spacing = _optional_float(raw_target.get("substrate_reference_lattice_angstrom"))
        if direct_substrate_spacing is None:
            direct_substrate_spacing = substrate_reference_a

        direct_mismatch = None
        if film_reference is not None and direct_substrate_spacing not in {None, 0.0}:
            direct_mismatch = 100.0 * (film_reference - float(direct_substrate_spacing)) / float(direct_substrate_spacing)
            direct_values.append(direct_mismatch)

        domain = raw_target.get("domain_match") if isinstance(raw_target.get("domain_match"), dict) else {}
        film_repeats = _optional_int(domain.get("film_repeats"))
        substrate_repeats = _optional_int(domain.get("substrate_repeats"))
        film_period = _optional_float(domain.get("film_period_angstrom"))
        substrate_period = _optional_float(domain.get("substrate_period_angstrom"))
        if film_period is None and film_repeats is not None and film_reference is not None:
            film_period = film_repeats * film_reference
        if substrate_period is None and substrate_repeats is not None and substrate_reference_a is not None:
            substrate_period = substrate_repeats * substrate_reference_a
        domain_mismatch = _optional_float(domain.get("mismatch_percent"))
        if domain_mismatch is None and film_period is not None and substrate_period not in {None, 0.0}:
            domain_mismatch = 100.0 * (film_period - float(substrate_period)) / float(substrate_period)
        if domain_mismatch is not None:
            domain_values.append(domain_mismatch)

        direct_warning = bool(direct_mismatch is not None and abs(direct_mismatch) > 5.0)
        domain_warning = bool(domain_mismatch is not None and abs(domain_mismatch) > 2.0)
        domain_matching_ready = bool(domain_mismatch is not None and not domain_warning)
        if not material:
            warnings.append(f"epitaxy_targets[{index}] is missing material.")
        if film_reference is None:
            warnings.append(f"epitaxy target {material or index} is missing film reference lattice.")
        if direct_warning:
            warnings.append(
                f"{material or 'target'} direct in-plane mismatch exceeds 5%; inspect buffer-layer or domain-match assumptions."
            )
        if domain_warning:
            warnings.append(f"{material or 'target'} domain-match mismatch exceeds 2%; refine supercell matching.")

        is_requested_target = bool(
            requested_target_material
            and material
            and material.lower() == requested_target_material.lower()
        )
        target_rows.append(
            {
                "material": material or None,
                "is_requested_target": is_requested_target,
                "film_orientation": raw_target.get("film_orientation"),
                "substrate_orientation": raw_target.get("substrate_orientation") or substrate_orientation,
                "relationship": raw_target.get("relationship"),
                "in_plane_rotation_deg": _optional_float(raw_target.get("in_plane_rotation_deg")),
                "film_reference_lattice_angstrom": _round(film_reference) if film_reference is not None else None,
                "direct_substrate_spacing_angstrom": _round(direct_substrate_spacing)
                if direct_substrate_spacing is not None
                else None,
                "direct_mismatch_percent": _round(direct_mismatch) if direct_mismatch is not None else None,
                "direct_mismatch_warning": direct_warning,
                "domain_film_repeats": film_repeats,
                "domain_substrate_repeats": substrate_repeats,
                "domain_film_period_angstrom": _round(film_period) if film_period is not None else None,
                "domain_substrate_period_angstrom": _round(substrate_period) if substrate_period is not None else None,
                "domain_mismatch_percent": _round(domain_mismatch) if domain_mismatch is not None else None,
                "domain_mismatch_warning": domain_warning,
                "domain_matching_ready": domain_matching_ready,
                "domain_notes": domain.get("notes"),
                "buffer_layer_hint": raw_target.get("buffer_layer_hint"),
                "next_action": (
                    "review_buffer_layer_and_domain_matching_before_interface_build"
                    if direct_warning
                    else "ready_for_atomic_interface_spec_review"
                ),
            }
        )

    if not target_rows:
        return {
            "available": False,
            "substrate_material": substrate_material,
            "substrate_orientation": substrate_orientation,
            "requested_target_material": requested_target_material,
            "target_count": 0,
            "targets": [],
            "warning_count": len(warnings) or 1,
            "warnings": warnings or ["No usable epitaxy targets were declared."],
            "next_action": "add_epitaxy_targets_metadata_before_interface_preflight",
        }

    selected_target = _selected_substrate_epitaxy_target(target_rows, requested_target_material)
    selected_target_found = selected_target is not None
    return {
        "available": True,
        "substrate_material": substrate_material,
        "substrate_orientation": substrate_orientation,
        "requested_target_material": requested_target_material,
        "selected_target_material": selected_target.get("material") if selected_target else None,
        "selected_target_found": selected_target_found,
        "selected_target": selected_target,
        "interface_spec_payload_hint": _substrate_epitaxy_interface_payload_hint(
            substrate_material,
            substrate_orientation,
            selected_target,
        ),
        "substrate_reference_a_angstrom": _round(substrate_reference_a) if substrate_reference_a is not None else None,
        "target_count": len(target_rows),
        "targets": target_rows,
        "max_abs_direct_mismatch_percent": _round(max((abs(value) for value in direct_values), default=0.0))
        if direct_values
        else None,
        "max_abs_domain_mismatch_percent": _round(max((abs(value) for value in domain_values), default=0.0))
        if domain_values
        else None,
        "warning_count": len(warnings),
        "warnings": warnings[:MAX_HEALTH_DETAIL_ROWS],
        "next_action": "choose_epitaxy_target_then_build_reviewed_interface_spec",
    }


def _selected_substrate_epitaxy_target(
    target_rows: list[dict[str, Any]],
    requested_target_material: str | None,
) -> dict[str, Any] | None:
    if not requested_target_material:
        return None
    requested = requested_target_material.lower()
    for row in target_rows:
        material = str(row.get("material") or "").lower()
        if material == requested:
            return dict(row)
    return None


def _substrate_epitaxy_interface_payload_hint(
    substrate_material: str | None,
    substrate_orientation: str | None,
    selected_target: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not selected_target:
        return None
    return {
        "workflow": "reviewed_interface_model_spec",
        "substrate_material": substrate_material,
        "substrate_orientation": substrate_orientation,
        "film_material": selected_target.get("material"),
        "film_orientation": selected_target.get("film_orientation"),
        "epitaxial_relationship": selected_target.get("relationship"),
        "in_plane_rotation_deg": selected_target.get("in_plane_rotation_deg"),
        "domain_match": {
            "film_repeats": selected_target.get("domain_film_repeats"),
            "substrate_repeats": selected_target.get("domain_substrate_repeats"),
            "film_period_angstrom": selected_target.get("domain_film_period_angstrom"),
            "substrate_period_angstrom": selected_target.get("domain_substrate_period_angstrom"),
            "mismatch_percent": selected_target.get("domain_mismatch_percent"),
        },
        "preflight_next_action": selected_target.get("next_action"),
    }


def _interface_scaffold_summary(
    metadata: dict[str, Any],
    lattice_summary: dict[str, Any],
    layer_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not metadata.get("interface_scaffold"):
        return None

    warnings: list[str] = []
    interface_gap = _optional_float(metadata.get("interface_gap_angstrom"))
    film_strain = _optional_float(metadata.get("film_in_plane_strain_percent"))
    if interface_gap is not None and interface_gap < 2.5:
        warnings.append("Interface gap is small for an unrelaxed scaffold; inspect close contacts before hot-loading or relaxing.")
    if film_strain is not None and abs(film_strain) > 3.0:
        warnings.append("Film in-plane strain exceeds 3%; review the domain match before relaxation.")
    if metadata.get("requires_geometry_relaxation") or metadata.get("unrelaxed_interface"):
        warnings.append("Interface scaffold is unrelaxed and requires geometry relaxation before quantitative calculations.")

    slab_vacuum_status = lattice_summary.get("slab_vacuum_status")
    slab_vacuum_ok = lattice_summary.get("vacuum_ok")
    slab_centered = lattice_summary.get("centered_in_cell")
    if slab_vacuum_status not in {None, "ready"}:
        warnings.append("Slab vacuum diagnostics are not ready; inspect lattice_summary before visual or DFT use.")

    layer_count = (layer_profile or {}).get("layer_count")
    spacing_warning = bool((layer_profile or {}).get("spacing_warning"))
    if spacing_warning:
        warnings.append("Layer-profile spacing warning is present; inspect layer_profile_summary before relaxation.")

    visual_hotload_ready = bool(slab_vacuum_ok is not False and slab_centered is not False)
    calculation_ready = False
    if not visual_hotload_ready:
        status = "visual_review_blocked"
        next_action = "fix_slab_vacuum_or_centering_before_hotload"
    elif warnings:
        status = "visualization_ready_relaxation_required"
        next_action = "hotload_for_visual_review_then_run_relaxation_only_after_confirmation"
    else:
        status = "visualization_ready"
        next_action = "hotload_for_visual_review"

    return {
        "available": True,
        "model": "pre_relaxation_interface_scaffold",
        "status": status,
        "interface": metadata.get("interface"),
        "substrate_material": metadata.get("substrate_material") or metadata.get("substrate"),
        "film_material": metadata.get("film_material") or metadata.get("nl_epitaxy_target"),
        "interface_orientation": metadata.get("interface_orientation"),
        "axis": metadata.get("interface_axis") or metadata.get("surface_axis") or "c",
        "substrate_supercell": metadata.get("substrate_supercell"),
        "film_supercell": metadata.get("film_supercell"),
        "domain_match": metadata.get("domain_match") or {},
        "common_in_plane_lattice_angstrom": _optional_float(metadata.get("common_in_plane_lattice_angstrom")),
        "strained_film_in_plane_lattice_angstrom": _optional_float(
            metadata.get("strained_film_in_plane_lattice_angstrom")
        ),
        "film_in_plane_strain_percent": film_strain,
        "interface_gap_angstrom": interface_gap,
        "substrate_thickness_angstrom": _optional_float(metadata.get("substrate_thickness_angstrom")),
        "film_thickness_angstrom": _optional_float(metadata.get("film_thickness_angstrom")),
        "slab_thickness_angstrom": _optional_float(metadata.get("slab_thickness_angstrom")),
        "vacuum_angstrom": _optional_float(metadata.get("vacuum_angstrom")),
        "bottom_vacuum_angstrom": _optional_float(metadata.get("bottom_vacuum_angstrom")),
        "top_vacuum_angstrom": _optional_float(metadata.get("top_vacuum_angstrom")),
        "slab_vacuum_status": slab_vacuum_status,
        "slab_centered_in_cell": slab_centered,
        "slab_vacuum_ok": slab_vacuum_ok,
        "layer_count": layer_count,
        "min_interlayer_spacing_angstrom": (layer_profile or {}).get("min_interlayer_spacing_angstrom"),
        "layer_spacing_warning": spacing_warning,
        "requires_geometry_relaxation": bool(
            metadata.get("requires_geometry_relaxation") or metadata.get("unrelaxed_interface")
        ),
        "visual_hotload_ready": visual_hotload_ready,
        "calculation_ready": calculation_ready,
        "warning_count": len(warnings),
        "warnings": warnings[:MAX_HEALTH_DETAIL_ROWS],
        "next_action": next_action,
    }


def _layer_profile_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    atom_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    atoms_with_fractional = [atom for atom in atom_rows if atom.get("fractional") is not None]
    if not atoms_with_fractional:
        return None
    axis_key = _profile_axis_key(metadata)
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_key)
    if axis_index is None:
        return {
            "available": False,
            "axis": axis_key,
            "warning": "Unsupported axis for semiconductor layer-profile diagnostics.",
        }
    axis_length = float(getattr(spec.model.lattice, axis_key))
    tolerance = _optional_float(metadata.get("layer_profile_tolerance_fractional")) or 1e-4
    sorted_atoms = sorted(
        atoms_with_fractional,
        key=lambda atom: (float(atom["fractional"][axis_index]), str(atom.get("id"))),
    )
    clusters: list[list[dict[str, Any]]] = []
    for atom in sorted_atoms:
        value = float(atom["fractional"][axis_index])
        if not clusters:
            clusters.append([atom])
            continue
        previous_values = [float(item["fractional"][axis_index]) for item in clusters[-1]]
        previous_center = sum(previous_values) / len(previous_values)
        if abs(value - previous_center) <= tolerance:
            clusters[-1].append(atom)
        else:
            clusters.append([atom])

    layers = []
    centers = []
    for index, cluster in enumerate(clusters, start=1):
        values = [float(atom["fractional"][axis_index]) for atom in cluster]
        center = sum(values) / len(values)
        centers.append(center)
        element_counts = Counter(str(atom.get("element")) for atom in cluster if atom.get("element"))
        passivant_count = sum(count for element, count in element_counts.items() if element in SURFACE_PASSIVANTS)
        layers.append(
            {
                "layer_index": index,
                "fractional_center": _round(center),
                "fractional_min": _round(min(values)),
                "fractional_max": _round(max(values)),
                "axis_coordinate_angstrom": _round(center * axis_length),
                "span_fractional": _round(max(values) - min(values)),
                "span_angstrom": _round((max(values) - min(values)) * axis_length),
                "atom_count": len(cluster),
                "non_passivant_atom_count": len(cluster) - passivant_count,
                "passivant_atom_count": passivant_count,
                "element_counts": dict(sorted(element_counts.items())),
                "atom_ids": [str(atom.get("id")) for atom in sorted(cluster, key=lambda item: str(item.get("id")))],
            }
        )

    spacings = [
        (centers[index] - centers[index - 1]) * axis_length
        for index in range(1, len(centers))
    ]
    for index, layer in enumerate(layers):
        if index > 0:
            layer["spacing_to_previous_angstrom"] = _round(spacings[index - 1])
        else:
            layer["spacing_to_previous_angstrom"] = None
        if index < len(spacings):
            layer["spacing_to_next_angstrom"] = _round(spacings[index])
        else:
            layer["spacing_to_next_angstrom"] = None

    element_counts_total = Counter(str(atom.get("element")) for atom in atoms_with_fractional if atom.get("element"))
    passivant_atom_count = sum(count for element, count in element_counts_total.items() if element in SURFACE_PASSIVANTS)
    spacing_stats = _stats_with_count([float(value) for value in spacings])
    min_spacing = min(spacings) if spacings else None
    axis_source = _profile_axis_source(metadata)
    spacing_warning_applicable = axis_source != "default_c_axis" or bool(
        metadata.get("interface")
        or metadata.get("surface_context")
        or metadata.get("surface_model")
        or metadata.get("heterostructure")
        or metadata.get("quantum_well")
        or metadata.get("gate_stack")
        or metadata.get("metal_semiconductor_contact")
        or metadata.get("interface_scaffold")
    )
    spacing_warning = bool(
        spacing_warning_applicable
        and min_spacing is not None
        and min_spacing < 0.5
    )
    return {
        "available": True,
        "axis": axis_key,
        "axis_source": axis_source,
        "axis_length_angstrom": _round(axis_length),
        "tolerance_fractional": _round(tolerance),
        "layer_count": len(layers),
        "atom_count": len(atoms_with_fractional),
        "non_passivant_atom_count": len(atoms_with_fractional) - passivant_atom_count,
        "passivant_atom_count": passivant_atom_count,
        "element_counts": dict(sorted(element_counts_total.items())),
        "interlayer_spacing_stats_angstrom": spacing_stats,
        "min_interlayer_spacing_angstrom": _round(min_spacing) if min_spacing is not None else None,
        "spacing_warning_applicable": spacing_warning_applicable,
        "spacing_warning": spacing_warning,
        "spacing_warning_reason": (
            "min_interlayer_spacing_below_threshold"
            if spacing_warning
            else "bulk_default_axis_projection_not_layered_model"
            if min_spacing is not None and min_spacing < 0.5 and not spacing_warning_applicable
            else None
        ),
        "layers": layers[:MAX_HEALTH_DETAIL_ROWS],
    }


def _crystal_layer_translation_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("crystal_layer_translations", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_crystal_layer_translation")
    if isinstance(latest, dict) and latest not in entries:
        entries.append(dict(latest))
    if not entries:
        return None

    latest = entries[-1]
    warnings: list[str] = []
    profile_available = bool(layer_profile and layer_profile.get("available"))
    recorded_profile_axis = str(latest.get("profile_axis") or "")
    current_profile_axis = str((layer_profile or {}).get("axis") or "")
    profile_axis_matches = profile_available and recorded_profile_axis == current_profile_axis
    if not profile_available:
        warnings.append("Current layer profile is unavailable; translation target binding cannot be verified.")
    elif not profile_axis_matches:
        warnings.append("Recorded translation profile axis differs from the current layer-profile axis.")

    layer_index = _optional_int(latest.get("layer_index"))
    tolerance = _optional_float((layer_profile or {}).get("tolerance_fractional")) or 1e-4
    current_layers = _crystal_layer_atom_ids_by_axis(spec, current_profile_axis, tolerance)
    current_layer_atom_ids = (
        current_layers[layer_index - 1]
        if layer_index is not None and 1 <= layer_index <= len(current_layers)
        else None
    )
    recorded_atom_ids = sorted(str(value) for value in latest.get("atom_ids", []) or [])
    current_atom_ids = sorted(current_layer_atom_ids or [])
    target_layer_found = current_layer_atom_ids is not None
    target_binding_matches = target_layer_found and recorded_atom_ids == current_atom_ids
    if not target_layer_found:
        warnings.append("Recorded target layer is not present in the current layer profile.")
    elif not target_binding_matches:
        warnings.append("Recorded translation atom IDs differ from the current target layer atom IDs.")

    translation_axis = str(latest.get("translation_axis") or "")
    translation_axis_is_in_plane = translation_axis in {"a", "b", "c"} and translation_axis != recorded_profile_axis
    if not translation_axis_is_in_plane:
        warnings.append("Recorded translation axis is not an in-plane lattice axis for the target layer profile.")
    distance = _optional_float(latest.get("distance_angstrom"))
    distance_valid = distance is not None and abs(distance) > 1e-12
    if not distance_valid:
        warnings.append("Recorded layer translation distance is missing or zero.")

    metadata_consistent = bool(
        profile_axis_matches
        and target_binding_matches
        and translation_axis_is_in_plane
        and distance_valid
    )
    return {
        "available": True,
        "quality": "complete" if metadata_consistent else "review_required",
        "entry_count": len(entries),
        "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
        "latest": latest,
        "profile_available": profile_available,
        "profile_axis_matches": profile_axis_matches,
        "target_layer_found": target_layer_found,
        "target_binding_matches_current_layer": target_binding_matches,
        "current_layer_atom_ids": current_atom_ids,
        "translation_axis_is_in_plane": translation_axis_is_in_plane,
        "distance_valid": distance_valid,
        "metadata_consistent": metadata_consistent,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _crystal_layer_rotation_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("crystal_layer_rotations", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_crystal_layer_rotation")
    if isinstance(latest, dict) and latest not in entries:
        entries.append(dict(latest))
    if not entries:
        return None

    latest = entries[-1]
    warnings: list[str] = []
    profile_available = bool(layer_profile and layer_profile.get("available"))
    recorded_profile_axis = str(latest.get("profile_axis") or "")
    current_profile_axis = str((layer_profile or {}).get("axis") or "")
    profile_axis_matches = profile_available and recorded_profile_axis == current_profile_axis
    if not profile_available:
        warnings.append("Current layer profile is unavailable; rotation target binding cannot be verified.")
    elif not profile_axis_matches:
        warnings.append("Recorded rotation profile axis differs from the current layer-profile axis.")

    layer_index = _optional_int(latest.get("layer_index"))
    tolerance = _optional_float((layer_profile or {}).get("tolerance_fractional")) or 1e-4
    current_layers = _crystal_layer_atom_ids_by_axis(spec, current_profile_axis, tolerance)
    current_layer_atom_ids = (
        current_layers[layer_index - 1]
        if layer_index is not None and 1 <= layer_index <= len(current_layers)
        else None
    )
    recorded_atom_ids = sorted(str(value) for value in latest.get("atom_ids", []) or [])
    current_atom_ids = sorted(current_layer_atom_ids or [])
    target_layer_found = current_layer_atom_ids is not None
    target_binding_matches = target_layer_found and recorded_atom_ids == current_atom_ids
    if not target_layer_found:
        warnings.append("Recorded rotation target layer is not present in the current layer profile.")
    elif not target_binding_matches:
        warnings.append("Recorded rotation atom IDs differ from the current target layer atom IDs.")

    rotation_axis = str(latest.get("rotation_axis") or "")
    rotation_axis_matches_profile = rotation_axis in {"a", "b", "c"} and rotation_axis == recorded_profile_axis
    if not rotation_axis_matches_profile:
        warnings.append("Recorded rotation axis is not the layer-profile axis.")
    current_orthogonality = (
        _diagnostic_lattice_axis_orthogonality_max_abs_cosine(spec.model.lattice, rotation_axis)
        if isinstance(spec.model, CrystalSpec) and rotation_axis in {"a", "b", "c"}
        else None
    )
    rotation_axis_orthogonal = current_orthogonality is not None and current_orthogonality <= 1e-6
    if not rotation_axis_orthogonal:
        warnings.append("Current rotation axis is not orthogonal to both in-plane lattice vectors.")

    angle = _optional_float(latest.get("angle_degrees"))
    angle_valid = angle is not None and 1e-12 < abs(angle) < 360.0 - 1e-12
    if not angle_valid:
        warnings.append("Recorded layer-rotation angle is missing or produces an identity rotation.")
    pivot = latest.get("pivot_fractional")
    pivot_valid = bool(
        isinstance(pivot, (list, tuple))
        and len(pivot) == 3
        and all(
            value is not None and -1e-12 <= value <= 1.0 + 1e-12
            for value in (_optional_float(item) for item in pivot)
        )
    )
    if not pivot_valid:
        warnings.append("Recorded layer-rotation pivot is missing or outside the fractional unit cell.")

    expected_coordinate_sha256 = str(latest.get("post_rotation_atom_coordinate_sha256") or "")
    current_coordinate_sha256 = _crystal_atom_coordinate_sha256(spec, recorded_atom_ids)
    coordinate_binding_matches = bool(
        target_binding_matches
        and len(expected_coordinate_sha256) == 64
        and current_coordinate_sha256 == expected_coordinate_sha256
    )
    if not coordinate_binding_matches:
        warnings.append("Current target-layer coordinates differ from the recorded post-rotation coordinates.")

    metadata_consistent = bool(
        profile_axis_matches
        and target_binding_matches
        and coordinate_binding_matches
        and rotation_axis_matches_profile
        and rotation_axis_orthogonal
        and angle_valid
        and pivot_valid
    )
    commensurability_verified = latest.get("commensurability_verified") is True
    requires_commensurate_supercell = latest.get("requires_commensurate_supercell") is not False
    requires_geometry_relaxation = latest.get("requires_geometry_relaxation") is not False
    calculation_ready = bool(
        metadata_consistent
        and commensurability_verified
        and not requires_commensurate_supercell
        and not requires_geometry_relaxation
        and latest.get("calculation_ready") is True
    )
    if not commensurability_verified:
        warnings.append("Twist commensurability has not been verified for the current periodic cell.")
    if requires_geometry_relaxation:
        warnings.append("The rotated layer scaffold requires geometry relaxation before calculation.")

    return {
        "available": True,
        "quality": (
            "calculation_preflight_ready"
            if calculation_ready
            else "visual_review_only"
            if metadata_consistent
            else "review_required"
        ),
        "entry_count": len(entries),
        "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
        "latest": latest,
        "profile_available": profile_available,
        "profile_axis_matches": profile_axis_matches,
        "target_layer_found": target_layer_found,
        "target_binding_matches_current_layer": target_binding_matches,
        "current_layer_atom_ids": current_atom_ids,
        "rotation_axis_matches_profile": rotation_axis_matches_profile,
        "rotation_axis_orthogonal_to_in_plane_vectors": rotation_axis_orthogonal,
        "axis_orthogonality_max_abs_cosine": (
            _round(current_orthogonality) if current_orthogonality is not None else None
        ),
        "angle_valid": angle_valid,
        "pivot_valid": pivot_valid,
        "expected_post_rotation_atom_coordinate_sha256": expected_coordinate_sha256 or None,
        "current_atom_coordinate_sha256": current_coordinate_sha256,
        "coordinate_binding_matches_current": coordinate_binding_matches,
        "metadata_consistent": metadata_consistent,
        "scaffold_only": latest.get("scaffold_only") is not False,
        "visual_review_only": latest.get("visual_review_only") is not False,
        "visual_hotload_ready": latest.get("visual_hotload_ready") is True,
        "commensurability_verified": commensurability_verified,
        "requires_commensurate_supercell": requires_commensurate_supercell,
        "requires_geometry_relaxation": requires_geometry_relaxation,
        "calculation_ready": calculation_ready,
        "calculation_blocking_reasons": [
            reason
            for condition, reason in (
                (not metadata_consistent, "layer_rotation_metadata_inconsistent"),
                (not commensurability_verified, "layer_rotation_commensurability_unverified"),
                (requires_commensurate_supercell, "layer_rotation_requires_commensurate_supercell"),
                (requires_geometry_relaxation, "layer_rotation_requires_geometry_relaxation"),
            )
            if condition
        ],
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _castep_geometry_optimization_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    *,
    expected_source_structure_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Verify the immutable structure transition recorded after CASTEP relaxation."""

    history = [
        dict(item)
        for item in metadata.get("castep_geometry_optimization_history", []) or []
        if isinstance(item, dict)
    ]
    latest_value = metadata.get("last_castep_geometry_optimization")
    latest = dict(latest_value) if isinstance(latest_value, dict) else None
    if latest is None and history:
        latest = dict(history[-1])
    if latest is None:
        return None

    entries = list(history)
    if latest not in entries:
        entries.append(dict(latest))
    current_structure_sha256 = (
        crystal_structure_sha256(spec.model)
        if isinstance(spec.model, CrystalSpec)
        else None
    )
    source_structure_sha256 = str(latest.get("source_structure_sha256") or "")
    output_structure_sha256 = str(latest.get("output_structure_sha256") or "")
    source_atom_id_sha256 = str(latest.get("source_atom_id_sha256") or "")
    output_atom_id_sha256 = str(latest.get("output_atom_id_sha256") or "")
    expected_source = str(expected_source_structure_sha256 or "")
    source_revision = _optional_int(latest.get("source_revision"))
    target_revision = _optional_int(latest.get("target_revision"))

    schema_verified = latest.get("schema_version") == CASTEP_RELAXATION_RECEIPT_SCHEMA
    history_binding_verified = bool(history and latest == history[-1])
    project_binding_verified = latest.get("source_project_id") == spec.project_id
    revision_binding_verified = bool(
        source_revision is not None
        and target_revision is not None
        and source_revision < target_revision
        and target_revision == spec.revision
    )
    task_verified = latest.get("task") == CastepTask.GEOMETRY_OPTIMIZATION.value
    backend_verified = (
        latest.get("backend")
        == "Materials Studio 20.1 CASTEP GeometryOptimization"
    )
    convergence_verified = latest.get("converged") is True
    receipt_verification_flag = latest.get("geometry_relaxation_verified") is True
    atom_identity_verified = bool(
        latest.get("atom_identity_preserved") is True
        and latest.get("atom_elements_preserved") is True
        and _is_sha256(source_atom_id_sha256)
        and source_atom_id_sha256 == output_atom_id_sha256
    )
    source_structure_hash_valid = _is_sha256(source_structure_sha256)
    output_structure_hash_valid = _is_sha256(output_structure_sha256)
    source_binding_verified = bool(
        source_structure_hash_valid
        and (not expected_source or source_structure_sha256 == expected_source)
    )
    output_binding_verified = bool(
        output_structure_hash_valid
        and current_structure_sha256 is not None
        and output_structure_sha256 == current_structure_sha256
    )
    script_binding_verified = _is_sha256(str(latest.get("script_sha256") or ""))
    cell_optimization = str(latest.get("cell_optimization") or "")
    fixed_cell_verified = bool(
        cell_optimization == "None"
        and latest.get("lattice_changed") is False
        and (_optional_float(latest.get("max_lattice_delta")) or 0.0) <= 1.0e-6
    )
    matching_operation = None
    if isinstance(spec.model, CrystalSpec):
        for operation in reversed(spec.model.operations):
            if operation.type != "castep_geometry_optimization":
                continue
            parameters = operation.parameters or {}
            if (
                _optional_int(parameters.get("source_revision")) == source_revision
                and parameters.get("converged") is True
                and str(parameters.get("cell_optimization") or "") == cell_optimization
                and parameters.get("materials_studio_api_contract")
                == "Materials Studio 20.1"
            ):
                matching_operation = operation
                break
    operation_binding_verified = matching_operation is not None
    simulation_binding_verified = bool(
        isinstance(spec.simulation, CastepEnergySpec)
        and spec.simulation.task is CastepTask.GEOMETRY_OPTIMIZATION
        and (
            spec.simulation.cell_optimization.value
            if spec.simulation.cell_optimization is not None
            else "None"
        )
        == cell_optimization
    )

    checks = (
        (schema_verified, "castep_relaxation_receipt_schema_mismatch"),
        (history_binding_verified, "castep_relaxation_history_binding_mismatch"),
        (project_binding_verified, "castep_relaxation_project_binding_mismatch"),
        (revision_binding_verified, "castep_relaxation_revision_binding_mismatch"),
        (task_verified, "castep_relaxation_task_mismatch"),
        (backend_verified, "castep_relaxation_backend_mismatch"),
        (convergence_verified, "castep_relaxation_not_converged"),
        (receipt_verification_flag, "castep_relaxation_verification_flag_missing"),
        (atom_identity_verified, "castep_relaxation_atom_identity_mismatch"),
        (source_binding_verified, "castep_relaxation_source_structure_mismatch"),
        (output_binding_verified, "castep_relaxation_output_structure_mismatch"),
        (script_binding_verified, "castep_relaxation_script_binding_invalid"),
        (operation_binding_verified, "castep_relaxation_operation_binding_mismatch"),
        (simulation_binding_verified, "castep_relaxation_simulation_binding_mismatch"),
    )
    blocking_reasons = [reason for condition, reason in checks if not condition]
    transition_verified = not blocking_reasons
    fixed_cell_transition_verified = transition_verified and fixed_cell_verified
    warnings = [
        "CASTEP geometry-optimization receipt failed immutable transition verification: "
        + ", ".join(blocking_reasons)
    ] if blocking_reasons else []
    if transition_verified and not fixed_cell_verified:
        warnings.append(
            "CASTEP relaxation is bound to the current revision, but it did not preserve a fixed cell."
        )

    return {
        "available": True,
        "quality": (
            "fixed_cell_relaxation_verified"
            if fixed_cell_transition_verified
            else "relaxation_verified"
            if transition_verified
            else "review_required"
        ),
        "entry_count": len(entries),
        "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
        "latest": latest,
        "schema_verified": schema_verified,
        "history_binding_verified": history_binding_verified,
        "project_binding_verified": project_binding_verified,
        "revision_binding_verified": revision_binding_verified,
        "task_verified": task_verified,
        "backend_verified": backend_verified,
        "convergence_verified": convergence_verified,
        "receipt_verification_flag": receipt_verification_flag,
        "atom_identity_verified": atom_identity_verified,
        "source_structure_hash_valid": source_structure_hash_valid,
        "source_structure_sha256": source_structure_sha256 or None,
        "expected_source_structure_sha256": expected_source or None,
        "source_binding_verified": source_binding_verified,
        "output_structure_hash_valid": output_structure_hash_valid,
        "output_structure_sha256": output_structure_sha256 or None,
        "current_structure_sha256": current_structure_sha256,
        "output_binding_verified": output_binding_verified,
        "script_binding_verified": script_binding_verified,
        "operation_binding_verified": operation_binding_verified,
        "simulation_binding_verified": simulation_binding_verified,
        "cell_optimization": cell_optimization or None,
        "fixed_cell_verified": fixed_cell_verified,
        "source_revision": source_revision,
        "target_revision": target_revision,
        "transition_verified": transition_verified,
        "fixed_cell_transition_verified": fixed_cell_transition_verified,
        "blocking_reasons": blocking_reasons,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _commensurate_tmd_twist_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("commensurate_twist_history", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_commensurate_twist")
    if isinstance(latest, dict) and latest not in entries:
        entries.append(dict(latest))
    if not entries:
        return None

    latest = entries[-1]
    warnings: list[str] = []
    if not isinstance(spec.model, CrystalSpec):
        return {
            "available": True,
            "quality": "review_required",
            "entry_count": len(entries),
            "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
            "latest": latest,
            "metadata_consistent": False,
            "commensurability_verified": False,
            "calculation_ready": False,
            "calculation_blocking_reasons": ["commensurate_twist_requires_crystal_model"],
            "warning_count": 1,
            "warnings": ["Commensurate TMD twist metadata requires a crystal model."],
        }

    m = _optional_int(latest.get("commensurate_m"))
    n = _optional_int(latest.get("commensurate_n"))
    indices_valid = bool(
        m is not None
        and n is not None
        and m > n >= 0
        and math.gcd(m, n) == 1
    )
    if not indices_valid:
        warnings.append("Recorded commensurate twist indices are invalid or non-coprime.")
    supercell_index = m * m + m * n + n * n if indices_valid and m is not None and n is not None else None
    recorded_index = _optional_int(latest.get("supercell_index"))
    supercell_index_verified = supercell_index is not None and recorded_index == supercell_index
    if not supercell_index_verified:
        warnings.append("Recorded commensurate supercell index does not equal m^2+mn+n^2.")

    bottom_matrix = _two_by_two_integer_matrix(latest.get("bottom_supercell_matrix"))
    top_matrix = _two_by_two_integer_matrix(latest.get("top_supercell_matrix"))
    orientation = str(latest.get("twist_orientation") or "")
    if indices_valid and m is not None and n is not None:
        matrix_a = ((m + n, n), (-n, m))
        matrix_b = ((m + n, m), (-m, n))
        expected_bottom, expected_top = (
            (matrix_b, matrix_a)
            if orientation == "counterclockwise"
            else (matrix_a, matrix_b)
            if orientation == "clockwise"
            else (None, None)
        )
    else:
        expected_bottom, expected_top = None, None
    matrix_pattern_verified = bool(
        expected_bottom is not None
        and bottom_matrix == expected_bottom
        and top_matrix == expected_top
    )
    matrix_determinant_verified = bool(
        supercell_index is not None
        and bottom_matrix is not None
        and top_matrix is not None
        and _two_by_two_determinant(bottom_matrix) == supercell_index
        and _two_by_two_determinant(top_matrix) == supercell_index
        and matrix_pattern_verified
    )
    if not matrix_determinant_verified:
        warnings.append("Recorded bottom/top integer matrices do not verify the commensurate cell.")

    expected_angle = (
        _diagnostic_commensurate_twist_angle_degrees(m, n)
        if indices_valid and m is not None and n is not None
        else None
    )
    recorded_angle = _optional_float(latest.get("twist_angle_degrees"))
    signed_expected_angle = (
        expected_angle
        if orientation == "counterclockwise"
        else -expected_angle
        if orientation == "clockwise" and expected_angle is not None
        else None
    )
    angle_verified = bool(
        recorded_angle is not None
        and signed_expected_angle is not None
        and abs(recorded_angle - signed_expected_angle) <= 1e-6
    )
    if not angle_verified:
        warnings.append("Recorded twist angle does not match the exact integer commensurability formula.")

    lattice = spec.model.lattice
    expected_common_a = _optional_float(latest.get("common_lattice_a_angstrom"))
    expected_common_b = _optional_float(latest.get("common_lattice_b_angstrom"))
    expected_gamma = _optional_float(latest.get("common_lattice_gamma_degrees"))
    lattice_verified = bool(
        expected_common_a is not None
        and expected_common_b is not None
        and expected_gamma is not None
        and abs(float(lattice.a) - expected_common_a) <= 1e-6
        and abs(float(lattice.b) - expected_common_b) <= 1e-6
        and abs(float(lattice.gamma) - expected_gamma) <= 1e-8
        and abs(float(lattice.alpha) - 90.0) <= 1e-8
        and abs(float(lattice.beta) - 90.0) <= 1e-8
    )
    if not lattice_verified:
        warnings.append("Current lattice no longer matches the recorded commensurate common cell.")

    atoms = list(spec.model.basis_atoms)
    bottom_atoms = [atom for atom in atoms if "_L1_" in atom.id]
    top_atoms = [atom for atom in atoms if "_L2_" in atom.id]
    expected_atoms_per_layer = _optional_int(latest.get("atoms_per_layer"))
    expected_atom_count = _optional_int(latest.get("atom_count"))
    layer_counts_verified = bool(
        expected_atoms_per_layer is not None
        and expected_atom_count is not None
        and len(bottom_atoms) == expected_atoms_per_layer
        and len(top_atoms) == expected_atoms_per_layer
        and len(atoms) == expected_atom_count == 2 * expected_atoms_per_layer
    )
    bottom_id_hash = _diagnostic_atom_id_list_sha256([atom.id for atom in bottom_atoms])
    top_id_hash = _diagnostic_atom_id_list_sha256([atom.id for atom in top_atoms])
    layer_atom_ids_verified = bool(
        layer_counts_verified
        and bottom_id_hash == latest.get("bottom_layer_atom_id_sha256")
        and top_id_hash == latest.get("top_layer_atom_id_sha256")
    )
    if not layer_atom_ids_verified:
        warnings.append("Current layer atom IDs or counts no longer match the commensurate twist receipt.")

    bottom_metals = [atom for atom in bottom_atoms if atom.element in TMD_METALS]
    top_metals = [atom for atom in top_atoms if atom.element in TMD_METALS]
    current_interlayer_distance = (
        (
            sum(float(atom.fractional.z) for atom in top_metals) / len(top_metals)
            - sum(float(atom.fractional.z) for atom in bottom_metals) / len(bottom_metals)
        )
        * float(lattice.c)
        if bottom_metals and top_metals
        else None
    )
    expected_interlayer_distance = _optional_float(latest.get("interlayer_distance_angstrom"))
    interlayer_distance_verified = bool(
        current_interlayer_distance is not None
        and expected_interlayer_distance is not None
        and abs(current_interlayer_distance - expected_interlayer_distance) <= 1e-6
    )

    bottom_chalcogens = [atom for atom in bottom_atoms if atom.element in TMD_CHALCOGENS]
    top_chalcogens = [atom for atom in top_atoms if atom.element in TMD_CHALCOGENS]
    current_interlayer_gap = (
        (
            min(float(atom.fractional.z) for atom in top_chalcogens)
            - max(float(atom.fractional.z) for atom in bottom_chalcogens)
        )
        * float(lattice.c)
        if bottom_chalcogens and top_chalcogens
        else None
    )
    expected_interlayer_gap = _optional_float(latest.get("interlayer_chalcogen_gap_angstrom"))
    interlayer_gap_verified = bool(
        current_interlayer_gap is not None
        and expected_interlayer_gap is not None
        and abs(current_interlayer_gap - expected_interlayer_gap) <= 1e-6
    )

    expected_structure_sha256 = str(latest.get("structure_sha256") or "")
    current_structure_sha256 = _diagnostic_crystal_structure_sha256(spec.model)
    construction_structure_binding_matches = bool(
        len(expected_structure_sha256) == 64
        and current_structure_sha256 == expected_structure_sha256
    )
    castep_relaxation_transition = _castep_geometry_optimization_summary(
        spec,
        metadata,
        expected_source_structure_sha256=expected_structure_sha256,
    )
    fixed_cell_relaxation_verified = bool(
        castep_relaxation_transition
        and castep_relaxation_transition.get("fixed_cell_transition_verified") is True
    )
    structure_binding_matches = bool(
        construction_structure_binding_matches or fixed_cell_relaxation_verified
    )
    geometry_measurement_binding_verified = bool(
        (
            interlayer_distance_verified
            and interlayer_gap_verified
            and construction_structure_binding_matches
        )
        or fixed_cell_relaxation_verified
    )
    if not interlayer_distance_verified and not fixed_cell_relaxation_verified:
        warnings.append("Current TMD metal-plane separation differs from the recorded interlayer distance.")
    if not interlayer_gap_verified and not fixed_cell_relaxation_verified:
        warnings.append("Current opposing-chalcogen gap differs from the commensurate twist receipt.")
    if not structure_binding_matches:
        warnings.append(
            "Current crystal SHA-256 matches neither the generated commensurate structure nor "
            "a verified fixed-cell CASTEP relaxation output."
        )
    if castep_relaxation_transition and not fixed_cell_relaxation_verified:
        warnings.extend(str(item) for item in castep_relaxation_transition.get("warnings", []) or [])

    metadata_consistent = bool(
        indices_valid
        and supercell_index_verified
        and matrix_determinant_verified
        and angle_verified
        and lattice_verified
        and layer_atom_ids_verified
        and geometry_measurement_binding_verified
        and structure_binding_matches
    )
    commensurability_verified = bool(
        metadata_consistent and latest.get("commensurability_verified") is True
    )
    geometry_relaxed = fixed_cell_relaxation_verified
    requires_geometry_relaxation = not geometry_relaxed
    calculation_ready = bool(
        commensurability_verified
        and geometry_relaxed
        and not requires_geometry_relaxation
    )
    if requires_geometry_relaxation or not geometry_relaxed:
        warnings.append("Commensurate twisted bilayer remains a pre-relaxation structure.")

    return {
        "available": True,
        "quality": (
            "calculation_preflight_ready"
            if calculation_ready
            else "commensurate_pre_relaxation"
            if metadata_consistent and commensurability_verified
            else "review_required"
        ),
        "entry_count": len(entries),
        "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
        "latest": latest,
        "indices_valid": indices_valid,
        "supercell_index_verified": supercell_index_verified,
        "matrix_pattern_verified": matrix_pattern_verified,
        "matrix_determinant_verified": matrix_determinant_verified,
        "expected_twist_angle_degrees": (
            _round(signed_expected_angle) if signed_expected_angle is not None else None
        ),
        "angle_verified": angle_verified,
        "lattice_verified": lattice_verified,
        "layer_counts_verified": layer_counts_verified,
        "layer_atom_ids_verified": layer_atom_ids_verified,
        "current_bottom_layer_atom_id_sha256": bottom_id_hash,
        "current_top_layer_atom_id_sha256": top_id_hash,
        "current_interlayer_distance_angstrom": (
            _round(current_interlayer_distance) if current_interlayer_distance is not None else None
        ),
        "interlayer_distance_verified": interlayer_distance_verified,
        "current_interlayer_chalcogen_gap_angstrom": (
            _round(current_interlayer_gap) if current_interlayer_gap is not None else None
        ),
        "interlayer_gap_verified": interlayer_gap_verified,
        "geometry_measurement_binding_verified": geometry_measurement_binding_verified,
        "expected_structure_sha256": expected_structure_sha256 or None,
        "current_structure_sha256": current_structure_sha256,
        "construction_structure_binding_matches_current": (
            construction_structure_binding_matches
        ),
        "structure_binding_matches_current": structure_binding_matches,
        "structure_binding_scope": (
            "verified_fixed_cell_castep_relaxation_output"
            if fixed_cell_relaxation_verified
            else "commensurate_construction_receipt"
            if construction_structure_binding_matches
            else "unverified"
        ),
        "castep_relaxation_transition": castep_relaxation_transition,
        "castep_relaxation_transition_verified": fixed_cell_relaxation_verified,
        "metadata_consistent": metadata_consistent,
        "commensurability_verified": commensurability_verified,
        "pre_relaxation_scaffold": not geometry_relaxed,
        "visual_review_only": not calculation_ready,
        "visual_hotload_ready": latest.get("visual_hotload_ready") is True,
        "requires_geometry_relaxation": requires_geometry_relaxation,
        "geometry_relaxed": geometry_relaxed,
        "calculation_ready": calculation_ready,
        "calculation_blocking_reasons": [
            reason
            for condition, reason in (
                (not metadata_consistent, "commensurate_twist_metadata_inconsistent"),
                (not commensurability_verified, "commensurate_twist_not_verified"),
                (
                    requires_geometry_relaxation or not geometry_relaxed,
                    "commensurate_twisted_bilayer_requires_geometry_relaxation",
                ),
            )
            if condition
        ],
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _commensurate_tmd_heterobilayer_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("commensurate_heterobilayer_history", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_commensurate_heterobilayer")
    if isinstance(latest, dict) and latest not in entries:
        entries.append(dict(latest))
    if not entries:
        return None

    latest = entries[-1]
    common_metadata = {
        **metadata,
        "commensurate_twist_history": entries,
        "last_commensurate_twist": latest,
    }
    common = _commensurate_tmd_twist_summary(spec, common_metadata) or {}
    warnings = [
        str(item)
        for item in common.get("warnings", []) or []
        if "Commensurate twisted bilayer remains" not in str(item)
    ]
    if not isinstance(spec.model, CrystalSpec):
        warnings.append("Commensurate TMD heterobilayer metadata requires a crystal model.")
        return {
            **common,
            "available": True,
            "quality": "review_required",
            "entry_count": len(entries),
            "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
            "latest": latest,
            "metadata_consistent": False,
            "commensurability_verified": False,
            "calculation_ready": False,
            "calculation_blocking_reasons": ["commensurate_tmd_heterobilayer_requires_crystal_model"],
            "warning_count": len(warnings),
            "warnings": warnings,
        }

    bottom_material = _canonical_diagnostic_tmd_material(latest.get("bottom_material"))
    top_material = _canonical_diagnostic_tmd_material(latest.get("top_material"))
    materials_distinct = bool(
        bottom_material is not None
        and top_material is not None
        and bottom_material != top_material
    )
    if not materials_distinct:
        warnings.append("Recorded TMD heterobilayer materials are missing, unsupported, or identical.")

    atoms = list(spec.model.basis_atoms)
    bottom_atoms = [atom for atom in atoms if "_L1_" in atom.id]
    top_atoms = [atom for atom in atoms if "_L2_" in atom.id]
    bottom_counts = _element_count_map(bottom_atoms)
    top_counts = _element_count_map(top_atoms)
    supercell_index = _optional_int(latest.get("supercell_index"))
    bottom_elements_verified = _tmd_layer_composition_verified(
        bottom_counts,
        bottom_material,
        supercell_index,
    )
    top_elements_verified = _tmd_layer_composition_verified(
        top_counts,
        top_material,
        supercell_index,
    )
    layer_materials_verified = bool(
        materials_distinct and bottom_elements_verified and top_elements_verified
    )
    if not layer_materials_verified:
        warnings.append("Current layer element counts do not match the recorded TMD material pair.")

    bottom_reference_a = _optional_float(latest.get("bottom_primitive_lattice_a_angstrom"))
    top_reference_a = _optional_float(latest.get("top_primitive_lattice_a_angstrom"))
    common_primitive_a = _optional_float(latest.get("common_primitive_lattice_a_angstrom"))
    strain_policy = str(latest.get("strain_policy") or "")
    strain_policy_valid = strain_policy in {"balanced", "bottom_fixed", "top_fixed"}
    expected_common_primitive_a = None
    if bottom_reference_a and top_reference_a and strain_policy_valid:
        if strain_policy == "balanced":
            expected_common_primitive_a = (
                2.0 * bottom_reference_a * top_reference_a
                / (bottom_reference_a + top_reference_a)
            )
        elif strain_policy == "bottom_fixed":
            expected_common_primitive_a = bottom_reference_a
        else:
            expected_common_primitive_a = top_reference_a
    common_primitive_verified = bool(
        common_primitive_a is not None
        and expected_common_primitive_a is not None
        and abs(common_primitive_a - expected_common_primitive_a) <= 1e-6
    )

    expected_bottom_strain = (
        100.0 * (common_primitive_a / bottom_reference_a - 1.0)
        if common_primitive_a is not None and bottom_reference_a
        else None
    )
    expected_top_strain = (
        100.0 * (common_primitive_a / top_reference_a - 1.0)
        if common_primitive_a is not None and top_reference_a
        else None
    )
    recorded_bottom_strain = _optional_float(latest.get("bottom_biaxial_strain_percent"))
    recorded_top_strain = _optional_float(latest.get("top_biaxial_strain_percent"))
    bottom_strain_verified = bool(
        expected_bottom_strain is not None
        and recorded_bottom_strain is not None
        and abs(expected_bottom_strain - recorded_bottom_strain) <= 1e-5
    )
    top_strain_verified = bool(
        expected_top_strain is not None
        and recorded_top_strain is not None
        and abs(expected_top_strain - recorded_top_strain) <= 1e-5
    )
    expected_max_abs_strain = max(
        abs(expected_bottom_strain or 0.0),
        abs(expected_top_strain or 0.0),
    )
    recorded_max_abs_strain = _optional_float(latest.get("max_abs_biaxial_strain_percent"))
    strain_limit = _optional_float(latest.get("max_strain_percent"))
    max_strain_verified = bool(
        recorded_max_abs_strain is not None
        and abs(recorded_max_abs_strain - expected_max_abs_strain) <= 1e-5
    )
    strain_within_limit = bool(
        strain_limit is not None
        and recorded_max_abs_strain is not None
        and recorded_max_abs_strain <= strain_limit + 1e-9
        and latest.get("strain_within_limit") is True
    )
    strain_partition_verified = bool(
        strain_policy_valid
        and common_primitive_verified
        and bottom_strain_verified
        and top_strain_verified
        and max_strain_verified
        and strain_within_limit
    )
    if not strain_partition_verified:
        warnings.append("Recorded TMD heterobilayer strain partition or limit does not verify.")

    common_metadata_consistent = common.get("metadata_consistent") is True
    metadata_consistent = bool(
        common_metadata_consistent
        and layer_materials_verified
        and strain_partition_verified
    )
    commensurability_verified = bool(
        metadata_consistent
        and latest.get("commensurability_verified") is True
        and latest.get("commensurability_model")
        == "exact_integer_coincidence_after_explicit_biaxial_strain"
    )
    requires_geometry_relaxation = common.get("requires_geometry_relaxation") is not False
    geometry_relaxed = common.get("geometry_relaxed") is True
    calculation_ready = bool(
        commensurability_verified
        and geometry_relaxed
        and not requires_geometry_relaxation
        and common.get("calculation_ready") is True
    )
    if requires_geometry_relaxation or not geometry_relaxed:
        warnings.append(
            "Commensurate TMD heterobilayer remains a strained pre-relaxation structure."
        )

    blocking_reasons = [
        reason
        for condition, reason in (
            (not materials_distinct, "commensurate_tmd_heterobilayer_material_pair_invalid"),
            (not layer_materials_verified, "commensurate_tmd_heterobilayer_layer_composition_mismatch"),
            (not strain_partition_verified, "commensurate_tmd_heterobilayer_strain_unverified"),
            (not common_metadata_consistent, "commensurate_tmd_heterobilayer_structure_binding_mismatch"),
            (not commensurability_verified, "commensurate_tmd_heterobilayer_commensurability_unverified"),
            (
                requires_geometry_relaxation or not geometry_relaxed,
                "commensurate_tmd_heterobilayer_requires_geometry_relaxation",
            ),
        )
        if condition
    ]
    return {
        **common,
        "available": True,
        "quality": (
            "calculation_preflight_ready"
            if calculation_ready
            else "commensurate_strained_pre_relaxation"
            if metadata_consistent and commensurability_verified
            else "review_required"
        ),
        "entry_count": len(entries),
        "entries": entries[-MAX_HEALTH_DETAIL_ROWS:],
        "latest": latest,
        "bottom_material": bottom_material,
        "top_material": top_material,
        "materials_distinct": materials_distinct,
        "bottom_layer_element_counts": bottom_counts,
        "top_layer_element_counts": top_counts,
        "bottom_layer_composition_verified": bottom_elements_verified,
        "top_layer_composition_verified": top_elements_verified,
        "layer_materials_verified": layer_materials_verified,
        "strain_policy": strain_policy or None,
        "strain_policy_valid": strain_policy_valid,
        "expected_common_primitive_lattice_a_angstrom": (
            _round(expected_common_primitive_a)
            if expected_common_primitive_a is not None
            else None
        ),
        "common_primitive_lattice_verified": common_primitive_verified,
        "expected_bottom_biaxial_strain_percent": (
            _round(expected_bottom_strain) if expected_bottom_strain is not None else None
        ),
        "expected_top_biaxial_strain_percent": (
            _round(expected_top_strain) if expected_top_strain is not None else None
        ),
        "bottom_biaxial_strain_verified": bottom_strain_verified,
        "top_biaxial_strain_verified": top_strain_verified,
        "max_abs_biaxial_strain_percent": (
            _round(recorded_max_abs_strain) if recorded_max_abs_strain is not None else None
        ),
        "max_strain_percent": _round(strain_limit) if strain_limit is not None else None,
        "max_strain_verified": max_strain_verified,
        "strain_within_limit": strain_within_limit,
        "strain_partition_verified": strain_partition_verified,
        "metadata_consistent": metadata_consistent,
        "commensurability_verified": commensurability_verified,
        "commensurability_model": latest.get("commensurability_model"),
        "requires_geometry_relaxation": requires_geometry_relaxation,
        "geometry_relaxed": geometry_relaxed,
        "calculation_ready": calculation_ready,
        "calculation_blocking_reasons": blocking_reasons,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _canonical_diagnostic_tmd_material(value: Any) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    for material in TMD_HETEROBILAYER_ELEMENTS:
        if normalized == re.sub(r"[^a-z0-9]+", "", material.lower()):
            return material
    return None


def _element_count_map(atoms: list[BasisAtomSpec]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in atoms:
        counts[atom.element] = counts.get(atom.element, 0) + 1
    return dict(sorted(counts.items()))


def _tmd_layer_composition_verified(
    counts: dict[str, int],
    material: str | None,
    supercell_index: int | None,
) -> bool:
    if material is None or supercell_index is None or supercell_index <= 0:
        return False
    elements = TMD_HETEROBILAYER_ELEMENTS.get(material)
    if elements is None:
        return False
    metal, chalcogen = elements
    return counts == {metal: supercell_index, chalcogen: 2 * supercell_index}


def _two_by_two_integer_matrix(
    value: Any,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    rows: list[tuple[int, int]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return None
        converted: list[int] = []
        for item in row:
            try:
                integer = int(item)
                numeric = float(item)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(numeric) or abs(numeric - integer) > 1e-12:
                return None
            converted.append(integer)
        rows.append((converted[0], converted[1]))
    return rows[0], rows[1]


def _two_by_two_determinant(matrix: tuple[tuple[int, int], tuple[int, int]]) -> int:
    return matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]


def _diagnostic_commensurate_twist_angle_degrees(m: int, n: int) -> float:
    index = m * m + m * n + n * n
    cosine = (m * m + 4 * m * n + n * n) / (2.0 * index)
    return math.degrees(math.acos(min(1.0, max(-1.0, cosine))))


def _diagnostic_atom_id_list_sha256(atom_ids: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(atom_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _diagnostic_crystal_structure_sha256(crystal: CrystalSpec) -> str:
    return crystal_structure_sha256(crystal)


def _is_sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _crystal_atom_coordinate_sha256(spec: ModelSpec, atom_ids: Sequence[str]) -> str | None:
    if not isinstance(spec.model, CrystalSpec) or not atom_ids:
        return None
    atoms_by_id = {atom.id: atom for atom in spec.model.basis_atoms}
    if any(atom_id not in atoms_by_id for atom_id in atom_ids):
        return None
    payload = [
        {
            "id": atom_id,
            "fractional": [
                round(float(atoms_by_id[atom_id].fractional.x), 12),
                round(float(atoms_by_id[atom_id].fractional.y), 12),
                round(float(atoms_by_id[atom_id].fractional.z), 12),
            ],
        }
        for atom_id in sorted(atom_ids)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _diagnostic_lattice_axis_orthogonality_max_abs_cosine(
    lattice: LatticeSpec,
    axis: str,
) -> float | None:
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    if axis_index is None:
        return None
    vectors = _lattice_vectors(lattice)
    axis_vector = vectors[axis_index]
    axis_norm = math.sqrt(_dot(axis_vector, axis_vector))
    if axis_norm <= 1e-12:
        return None
    cosines = []
    for index, vector in enumerate(vectors):
        if index == axis_index:
            continue
        vector_norm = math.sqrt(_dot(vector, vector))
        if vector_norm <= 1e-12:
            return None
        cosines.append(abs(_dot(axis_vector, vector)) / (axis_norm * vector_norm))
    return max(cosines, default=0.0)


def _crystal_layer_atom_ids_by_axis(
    spec: ModelSpec,
    axis: str,
    tolerance: float,
) -> list[list[str]]:
    if not isinstance(spec.model, CrystalSpec):
        return []
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    if axis_index is None:
        return []
    atoms = sorted(
        spec.model.basis_atoms,
        key=lambda atom: (
            (float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z))[axis_index],
            atom.id,
        ),
    )
    layers: list[list[BasisAtomSpec]] = []
    for atom in atoms:
        value = (float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z))[axis_index]
        if not layers:
            layers.append([atom])
            continue
        center = sum(
            (float(item.fractional.x), float(item.fractional.y), float(item.fractional.z))[axis_index]
            for item in layers[-1]
        ) / len(layers[-1])
        if abs(value - center) <= tolerance:
            layers[-1].append(atom)
        else:
            layers.append([atom])
    return [sorted(atom.id for atom in layer) for layer in layers]


def _profile_axis_key(metadata: dict[str, Any]) -> str:
    raw_axis = metadata.get("interface_axis") or metadata.get("surface_axis") or "c"
    axis = str(raw_axis).strip().lower()
    return {"x": "a", "y": "b", "z": "c"}.get(axis, axis)


def _profile_axis_source(metadata: dict[str, Any]) -> str:
    if metadata.get("interface_axis") is not None:
        return "interface_axis"
    if metadata.get("surface_axis") is not None:
        return "surface_axis"
    return "default_c_axis"


def _superlattice_period_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("applied_superlattice_period", []) or []
        if isinstance(item, dict)
    ]
    if not entries:
        return None
    latest = entries[-1]
    estimated_total = _optional_int(latest.get("estimated_total_period_count")) or _optional_int(metadata.get("superlattice_period_count"))
    layer_count = _optional_int((layer_profile or {}).get("layer_count"))
    layers_per_period = None
    if estimated_total and layer_count:
        layers_per_period = layer_count / estimated_total
    return {
        "available": True,
        "entry_count": len(entries),
        "entries": entries[:MAX_HEALTH_DETAIL_ROWS],
        "latest": latest,
        "estimated_total_period_count": estimated_total,
        "latest_requested_period_count": _optional_int(latest.get("requested_period_count")),
        "axis": latest.get("axis"),
        "supercell_matrix": latest.get("supercell_matrix"),
        "layer_count": layer_count,
        "estimated_layers_per_period": _round(layers_per_period) if layers_per_period is not None else None,
        "layer_profile_axis": (layer_profile or {}).get("axis"),
    }


def _interface_profile_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
    heterostructure: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not layer_profile or not layer_profile.get("layers"):
        return None
    materials = metadata.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    materials = [str(material) for material in materials if str(material)]
    if not heterostructure and not _has_heterostructure_context(metadata):
        return None

    material_marker_map = _material_marker_to_material_map(materials, metadata)
    layers = []
    marker_layers: list[dict[str, Any]] = []
    mixed_layer_count = 0
    shared_anion_layer_count = 0
    for layer in layer_profile.get("layers", []) or []:
        element_counts = dict(layer.get("element_counts") or {})
        non_passivant_elements = sorted(
            element
            for element, count in element_counts.items()
            if count and element not in SURFACE_PASSIVANTS
        )
        cations = [element for element in non_passivant_elements if element in III_V_CATIONS]
        anions = [element for element in non_passivant_elements if element in III_V_ANIONS]
        group_iv = [element for element in non_passivant_elements if element in GROUP_IV_SEMICONDUCTORS]
        mixed_layer = len(non_passivant_elements) > 1
        material_marker = None
        if not non_passivant_elements:
            layer_role = "passivant_only"
        elif cations and len(cations) == len(non_passivant_elements):
            layer_role = "iii_v_cation_layer"
            material_marker = _join_vector(cations)
        elif anions and len(anions) == len(non_passivant_elements):
            layer_role = "iii_v_anion_layer"
            shared_anion_layer_count += 1
        elif group_iv and len(group_iv) == len(non_passivant_elements):
            layer_role = "group_iv_layer"
            material_marker = _join_vector(group_iv)
        else:
            layer_role = "mixed_non_passivant"
            material_marker = _join_vector(non_passivant_elements)
        if mixed_layer:
            mixed_layer_count += 1
        row = {
            "layer_index": layer.get("layer_index"),
            "fractional_center": layer.get("fractional_center"),
            "axis_coordinate_angstrom": layer.get("axis_coordinate_angstrom"),
            "atom_count": layer.get("atom_count"),
            "element_counts": element_counts,
            "element_signature": _element_signature(element_counts),
            "non_passivant_elements": non_passivant_elements,
            "layer_role": layer_role,
            "material_marker": material_marker,
            "material_group": material_marker_map.get(str(material_marker), material_marker) if material_marker else None,
            "mixed_layer": mixed_layer,
            "segment_index": None,
            "boundary_before_layer": False,
            "boundary_after_layer": False,
            "atom_ids": layer.get("atom_ids") or [],
        }
        layers.append(row)
        if material_marker:
            marker_layers.append(row)

    segments = []
    transitions = []
    current_segment: dict[str, Any] | None = None
    previous_marker_layer: dict[str, Any] | None = None
    for marker_layer in marker_layers:
        marker = str(marker_layer.get("material_marker"))
        material_group = str(marker_layer.get("material_group") or marker)
        if current_segment is None or current_segment.get("material_group") != material_group:
            if current_segment is not None:
                current_segment["last_layer_index"] = previous_marker_layer.get("layer_index") if previous_marker_layer else current_segment["first_layer_index"]
                current_segment["marker_layer_count"] = len(current_segment["marker_layer_indices"])
                transitions.append(
                    {
                        "from_segment_index": current_segment["segment_index"],
                        "to_segment_index": len(segments) + 1,
                        "from_material_marker": current_segment.get("material_marker"),
                        "to_material_marker": marker,
                        "from_material_group": current_segment.get("material_group"),
                        "to_material_group": material_group,
                        "from_layer_index": current_segment["last_layer_index"],
                        "to_layer_index": marker_layer.get("layer_index"),
                    }
                )
                if previous_marker_layer is not None:
                    previous_marker_layer["boundary_after_layer"] = True
                marker_layer["boundary_before_layer"] = True
            current_segment = {
                "segment_index": len(segments) + 1,
                "material_marker": marker,
                "material_group": material_group,
                "material_markers": [],
                "first_layer_index": marker_layer.get("layer_index"),
                "last_layer_index": marker_layer.get("layer_index"),
                "marker_layer_indices": [],
            }
            segments.append(current_segment)
        if marker not in current_segment["material_markers"]:
            current_segment["material_markers"].append(marker)
        current_segment["marker_layer_indices"].append(marker_layer.get("layer_index"))
        marker_layer["segment_index"] = current_segment["segment_index"]
        previous_marker_layer = marker_layer
    if current_segment is not None:
        current_segment["last_layer_index"] = previous_marker_layer.get("layer_index") if previous_marker_layer else current_segment["first_layer_index"]
        current_segment["marker_layer_count"] = len(current_segment["marker_layer_indices"])

    layer_role_counts = Counter(str(layer.get("layer_role")) for layer in layers)
    interface_transition_count = len(transitions)
    return {
        "available": True,
        "interface": metadata.get("interface"),
        "materials": materials,
        "axis": layer_profile.get("axis"),
        "axis_source": layer_profile.get("axis_source"),
        "layer_count": len(layers),
        "material_segment_count": len(segments),
        "interface_transition_count": interface_transition_count,
        "mixed_layer_count": mixed_layer_count,
        "shared_anion_layer_count": shared_anion_layer_count,
        "abrupt_interface": bool(interface_transition_count > 0 and mixed_layer_count == 0),
        "layer_role_counts": dict(sorted(layer_role_counts.items())),
        "segments": segments[:MAX_HEALTH_DETAIL_ROWS],
        "transitions": transitions[:MAX_HEALTH_DETAIL_ROWS],
        "layers": layers[:MAX_HEALTH_DETAIL_ROWS],
    }


def _quantum_well_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
    interface_profile: dict[str, Any] | None,
    heterostructure: dict[str, Any] | None,
    superlattice_period: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if _is_single_oxide_interface_metadata(metadata) or _is_metal_semiconductor_contact_metadata(metadata):
        return None
    if not layer_profile or not interface_profile:
        return None
    raw_segments = [
        dict(segment)
        for segment in interface_profile.get("segments", []) or []
        if isinstance(segment, dict)
    ]
    raw_layers = [
        dict(layer)
        for layer in layer_profile.get("layers", []) or []
        if isinstance(layer, dict) and _optional_int(layer.get("layer_index")) is not None
    ]
    if not raw_segments or not raw_layers:
        return None

    raw_materials = interface_profile.get("materials") or (heterostructure or {}).get("materials") or []
    if isinstance(raw_materials, str):
        materials = [raw_materials]
    else:
        materials = list(raw_materials)
    materials = [str(material) for material in materials if str(material)]
    if len(materials) < 2 and not interface_profile.get("interface"):
        return None

    axis_length = _optional_float(layer_profile.get("axis_length_angstrom"))
    if axis_length is None or axis_length <= 0:
        return {
            "available": False,
            "warning": "Missing layer-profile axis length for quantum-well thickness diagnostics.",
        }

    layer_indices = sorted(int(layer["layer_index"]) for layer in raw_layers)
    layer_by_index = {int(layer["layer_index"]): layer for layer in raw_layers}
    interface_layer_by_index = {
        int(layer["layer_index"]): layer
        for layer in interface_profile.get("layers", []) or []
        if isinstance(layer, dict) and _optional_int(layer.get("layer_index")) is not None
    }
    sorted_segments = sorted(raw_segments, key=lambda item: int(item.get("segment_index") or 0))
    marker_to_material = _material_marker_to_material_map(materials, metadata)
    substrate = str((heterostructure or {}).get("substrate") or metadata.get("substrate") or "") or None
    well_material = substrate or (materials[0] if materials else None)
    material_period_length = len(materials) if materials else max(len(sorted_segments), 1)
    estimated_period_count = _optional_int((superlattice_period or {}).get("estimated_total_period_count"))
    inferred_period_count = len(sorted_segments) // material_period_length if material_period_length else None
    if inferred_period_count == 0:
        inferred_period_count = None
    period_count = estimated_period_count or inferred_period_count or 1

    segment_rows = []
    warnings: list[str] = []
    for index, segment in enumerate(sorted_segments):
        first_layer_index = _optional_int(segment.get("first_layer_index"))
        if first_layer_index is None:
            continue
        next_first_layer_index = (
            _optional_int(sorted_segments[index + 1].get("first_layer_index"))
            if index + 1 < len(sorted_segments)
            else None
        )
        if next_first_layer_index is not None and next_first_layer_index > first_layer_index:
            last_layer_index = next_first_layer_index - 1
        else:
            last_layer_index = layer_indices[-1]
        first_layer_index = max(first_layer_index, layer_indices[0])
        last_layer_index = min(last_layer_index, layer_indices[-1])
        segment_layers = [
            layer_by_index[layer_index]
            for layer_index in layer_indices
            if first_layer_index <= layer_index <= last_layer_index
        ]
        if not segment_layers:
            continue

        marker = str(segment.get("material_marker") or "")
        material = marker_to_material.get(marker, marker or None)
        role = _quantum_well_segment_role(material, well_material)
        segment_index = int(segment.get("segment_index") or (index + 1))
        segment_in_period = ((segment_index - 1) % material_period_length) + 1 if material_period_length else None
        period_index = ((segment_index - 1) // material_period_length) + 1 if material_period_length else None
        boundaries = _layer_segment_boundaries(segment_layers, raw_layers, axis_length)
        mixed_layer_count = sum(
            1
            for layer in segment_layers
            if bool((interface_layer_by_index.get(int(layer["layer_index"])) or {}).get("mixed_layer"))
        )
        element_signatures = [
            _element_signature(dict(layer.get("element_counts") or {}))
            for layer in segment_layers
            if layer.get("element_counts")
        ]
        element_counts = _sum_layer_element_counts(segment_layers)
        cation_counts = {
            element: count
            for element, count in element_counts.items()
            if element in III_V_CATIONS and count > 0
        }
        anion_counts = {
            element: count
            for element, count in element_counts.items()
            if element in III_V_ANIONS and count > 0
        }
        cation_total = sum(cation_counts.values())
        cation_fractions = {
            element: _round(count / cation_total)
            for element, count in sorted(cation_counts.items())
        } if cation_total else {}
        segment_rows.append(
            {
                "segment_index": segment_index,
                "period_index": period_index,
                "segment_in_period": segment_in_period,
                "material": material,
                "material_marker": marker or None,
                "role": role,
                "first_layer_index": first_layer_index,
                "last_layer_index": last_layer_index,
                "layer_count": len(segment_layers),
                "marker_layer_count": segment.get("marker_layer_count"),
                "marker_layer_indices": segment.get("marker_layer_indices") or [],
                "atom_count": sum(int(layer.get("atom_count") or 0) for layer in segment_layers),
                "non_passivant_atom_count": sum(int(layer.get("non_passivant_atom_count") or 0) for layer in segment_layers),
                "mixed_layer_count": mixed_layer_count,
                "element_counts": dict(sorted(element_counts.items())),
                "cation_counts": dict(sorted(cation_counts.items())),
                "anion_counts": dict(sorted(anion_counts.items())),
                "cation_fractions": cation_fractions,
                "element_signatures": element_signatures,
                **boundaries,
            }
        )

    if not segment_rows:
        return None

    if int(interface_profile.get("mixed_layer_count") or 0) > 0:
        warnings.append("Interface profile contains mixed layers; quantum-well segment thicknesses need visual review.")
    notes: list[str] = []
    if int(interface_profile.get("shared_anion_layer_count") or 0) > 0:
        notes.append("Shared-anion III-V layers are assigned to the preceding cation segment for thickness estimates.")
    if materials and len(segment_rows) % len(materials) != 0:
        warnings.append("Material segment count is not an integer multiple of declared materials; period grouping is approximate.")
    if estimated_period_count and inferred_period_count and estimated_period_count != inferred_period_count:
        warnings.append("Requested superlattice period count differs from the inferred material-segment cycle count.")

    period_thicknesses = []
    for period_index in sorted({row.get("period_index") for row in segment_rows if row.get("period_index") is not None}):
        values = [
            float(row["thickness_angstrom"])
            for row in segment_rows
            if row.get("period_index") == period_index and row.get("thickness_angstrom") is not None
        ]
        if values:
            period_thicknesses.append(sum(values))

    well_segments = [row for row in segment_rows if row.get("role") == "well"]
    barrier_segments = [row for row in segment_rows if row.get("role") == "barrier"]
    barrier_materials = sorted(
        {
            str(row.get("material"))
            for row in barrier_segments
            if row.get("material")
        }
    )
    layer_request = metadata.get("quantum_well_layer_request") if isinstance(metadata.get("quantum_well_layer_request"), dict) else {}
    thickness_stats = _stats_with_count([float(row["thickness_angstrom"]) for row in segment_rows if row.get("thickness_angstrom") is not None])
    well_thickness_stats = _stats_with_count([float(row["thickness_angstrom"]) for row in well_segments if row.get("thickness_angstrom") is not None])
    barrier_thickness_stats = _stats_with_count([float(row["thickness_angstrom"]) for row in barrier_segments if row.get("thickness_angstrom") is not None])
    requested_well_thickness = _optional_float(layer_request.get("requested_well_thickness_angstrom"))
    requested_barrier_thickness = _optional_float(layer_request.get("requested_barrier_thickness_angstrom"))
    actual_well_thickness = (well_thickness_stats or {}).get("mean") if requested_well_thickness is not None else _optional_float(layer_request.get("actual_well_thickness_angstrom"))
    actual_barrier_thickness = (barrier_thickness_stats or {}).get("mean") if requested_barrier_thickness is not None else _optional_float(layer_request.get("actual_barrier_thickness_angstrom"))
    return {
        "available": True,
        "model": "periodic_layer_boundary_estimate",
        "interface": interface_profile.get("interface"),
        "axis": layer_profile.get("axis"),
        "axis_source": layer_profile.get("axis_source"),
        "axis_length_angstrom": _round(axis_length),
        "materials": materials,
        "substrate": substrate,
        "well_material": well_material,
        "barrier_materials": barrier_materials,
        "requested_well_material": layer_request.get("well_material"),
        "requested_barrier_material": layer_request.get("barrier_material"),
        "requested_well_layer_count": _optional_int(layer_request.get("well_layer_count")),
        "requested_barrier_layer_count": _optional_int(layer_request.get("barrier_layer_count")),
        "requested_well_thickness_angstrom": requested_well_thickness,
        "requested_barrier_thickness_angstrom": requested_barrier_thickness,
        "actual_well_thickness_angstrom": actual_well_thickness,
        "actual_barrier_thickness_angstrom": actual_barrier_thickness,
        "well_thickness_error_angstrom": _round(float(actual_well_thickness) - requested_well_thickness) if actual_well_thickness is not None and requested_well_thickness is not None else _optional_float(layer_request.get("well_thickness_error_angstrom")),
        "barrier_thickness_error_angstrom": _round(float(actual_barrier_thickness) - requested_barrier_thickness) if actual_barrier_thickness is not None and requested_barrier_thickness is not None else _optional_float(layer_request.get("barrier_thickness_error_angstrom")),
        "layer_request_source": layer_request.get("source"),
        "period_count": period_count,
        "requested_period_count": _optional_int((superlattice_period or {}).get("latest_requested_period_count")),
        "inferred_period_count": inferred_period_count,
        "material_segment_count": len(segment_rows),
        "well_segment_count": len(well_segments),
        "barrier_segment_count": len(barrier_segments),
        "well_cation_fractions_by_material": _segment_cation_fractions_by_material(well_segments),
        "barrier_cation_fractions_by_material": _segment_cation_fractions_by_material(barrier_segments),
        "layer_count": layer_profile.get("layer_count"),
        "interface_transition_count": interface_profile.get("interface_transition_count"),
        "mixed_layer_count": interface_profile.get("mixed_layer_count"),
        "shared_anion_layer_count": interface_profile.get("shared_anion_layer_count"),
        "thickness_stats_angstrom": thickness_stats,
        "well_thickness_stats_angstrom": well_thickness_stats,
        "barrier_thickness_stats_angstrom": barrier_thickness_stats,
        "period_thickness_stats_angstrom": _stats_with_count(period_thicknesses),
        "warning_count": len(warnings),
        "warnings": warnings,
        "note_count": len(notes),
        "notes": notes,
        "segments": segment_rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _band_alignment_summary(
    metadata: dict[str, Any],
    heterostructure: dict[str, Any] | None,
    quantum_well: dict[str, Any] | None,
) -> dict[str, Any] | None:
    materials = list((heterostructure or {}).get("materials") or metadata.get("materials") or [])
    materials = [str(material) for material in materials if str(material)]
    if len(materials) < 2:
        return None
    if not _has_material_electronic_metadata(metadata, materials):
        return None

    well_material = str((quantum_well or {}).get("well_material") or (heterostructure or {}).get("substrate") or materials[0])
    if well_material not in materials:
        materials = [well_material, *materials]
    barrier_materials = [
        str(material)
        for material in (quantum_well or {}).get("barrier_materials", []) or []
        if str(material)
    ]
    reference_properties = _material_electronic_properties(metadata, well_material)
    warnings: list[str] = []
    if reference_properties["electron_affinity_ev"] is None:
        warnings.append(f"Missing electron_affinity_ev for band-alignment reference material {well_material}.")
    if reference_properties["band_gap_ev"] is None:
        warnings.append(f"Missing band_gap_ev for band-alignment reference material {well_material}.")

    offsets: list[dict[str, Any]] = []
    complete_offsets = 0
    type_i_count = 0
    review_count = 0
    missing_property_count = 0
    for material in materials:
        if material == well_material:
            continue
        material_properties = _material_electronic_properties(metadata, material)
        role = "barrier" if material in barrier_materials else "material"
        missing = []
        for label, value in (
            ("electron_affinity_ev", material_properties["electron_affinity_ev"]),
            ("band_gap_ev", material_properties["band_gap_ev"]),
        ):
            if value is None:
                missing.append(label)
        if missing:
            missing_property_count += len(missing)
            warnings.append(f"Missing {', '.join(missing)} for band-alignment material {material}.")

        conduction_offset = None
        valence_offset = None
        electron_barrier = None
        hole_barrier = None
        band_gap_difference = None
        confines_electrons = None
        confines_holes = None
        alignment_type = "insufficient_metadata"
        if (
            reference_properties["electron_affinity_ev"] is not None
            and reference_properties["band_gap_ev"] is not None
            and material_properties["electron_affinity_ev"] is not None
            and material_properties["band_gap_ev"] is not None
        ):
            complete_offsets += 1
            reference_affinity = float(reference_properties["electron_affinity_ev"])
            material_affinity = float(material_properties["electron_affinity_ev"])
            reference_gap = float(reference_properties["band_gap_ev"])
            material_gap = float(material_properties["band_gap_ev"])
            conduction_offset = _round(reference_affinity - material_affinity)
            valence_offset = _round(reference_affinity + reference_gap - material_affinity - material_gap)
            band_gap_difference = _round(material_gap - reference_gap)
            if role == "barrier":
                electron_barrier = conduction_offset
                hole_barrier = _round(-float(valence_offset))
                confines_electrons = electron_barrier is not None and electron_barrier > 0
                confines_holes = hole_barrier is not None and hole_barrier > 0
                if confines_electrons and confines_holes:
                    alignment_type = "type_i_quantum_well_preflight"
                    type_i_count += 1
                else:
                    alignment_type = "type_ii_or_inverted_barrier_review"
                    review_count += 1
                    warnings.append(
                        f"Band-alignment metadata suggests {material} may not confine both carriers relative to {well_material}."
                    )
            else:
                alignment_type = "offset_vs_reference_preflight"

        offsets.append(
            {
                "material": material,
                "role": role,
                "reference_electron_affinity_ev": reference_properties["electron_affinity_ev"],
                "reference_band_gap_ev": reference_properties["band_gap_ev"],
                "material_electron_affinity_ev": material_properties["electron_affinity_ev"],
                "material_band_gap_ev": material_properties["band_gap_ev"],
                "conduction_band_offset_vs_reference_ev": conduction_offset,
                "valence_band_offset_vs_reference_ev": valence_offset,
                "electron_barrier_height_ev": electron_barrier,
                "hole_barrier_height_ev": hole_barrier,
                "band_gap_difference_vs_reference_ev": band_gap_difference,
                "confines_electrons": confines_electrons,
                "confines_holes": confines_holes,
                "alignment_type": alignment_type,
            }
        )

    if missing_property_count:
        quality = "incomplete"
    elif warnings:
        quality = "review"
    else:
        quality = "complete"
    if barrier_materials and not offsets:
        warnings.append("No non-reference material was available for band-alignment preflight.")
        quality = "incomplete"

    return {
        "available": True,
        "model": str(metadata.get("band_alignment_model") or "electron_affinity_metadata_reference"),
        "reference": metadata.get("band_alignment_reference"),
        "interface": (heterostructure or {}).get("interface") or metadata.get("interface"),
        "reference_material": well_material,
        "barrier_materials": barrier_materials,
        "material_count": len(materials),
        "offset_count": len(offsets),
        "complete_offset_count": complete_offsets,
        "type_i_barrier_count": type_i_count,
        "review_offset_count": review_count,
        "missing_property_count": missing_property_count,
        "quality": quality,
        "warning_count": len(warnings),
        "warnings": warnings,
        "notes": [
            "Metadata-only electron-affinity preflight; not a DFT band-alignment result.",
            "Use explicit electronic-structure calculations before drawing quantitative device conclusions.",
        ],
        "offsets": offsets[:MAX_HEALTH_DETAIL_ROWS],
    }


def _polarization_2deg_summary(
    metadata: dict[str, Any],
    heterostructure: dict[str, Any] | None,
    quantum_well: dict[str, Any] | None,
    band_alignment: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not heterostructure:
        return None
    family = str(metadata.get("structure_family") or "").lower()
    orientation = str(metadata.get("interface_orientation") or (heterostructure or {}).get("interface_orientation") or "")
    if "wurtzite" not in family and "0001" not in orientation:
        return None

    well_material = str((quantum_well or {}).get("well_material") or (heterostructure or {}).get("substrate") or "")
    well_polarization = _nitride_polarization_properties(metadata, well_material)
    if not well_polarization:
        return None
    barrier_materials = [
        str(material)
        for material in (quantum_well or {}).get("barrier_materials", []) or []
        if str(material)
    ]
    explicit_barrier_materials = metadata.get("polarization_2deg_barrier_materials")
    if isinstance(explicit_barrier_materials, str):
        barrier_materials = [explicit_barrier_materials]
    elif isinstance(explicit_barrier_materials, list):
        selected = [str(material) for material in explicit_barrier_materials if str(material)]
        if selected:
            barrier_materials = selected
    if not barrier_materials:
        barrier_materials = [
            str(material)
            for material in (heterostructure or {}).get("materials", []) or []
            if str(material) and str(material) != well_material
        ]

    in_plane = _optional_float((heterostructure or {}).get("in_plane_lattice_angstrom")) or _optional_float(
        metadata.get("in_plane_lattice_angstrom")
    )
    well_reference_lattice = (
        _material_reference_lattice(metadata, well_material)
        or _optional_float(well_polarization.get("a_lattice_angstrom"))
    )
    well_strain = _in_plane_strain(in_plane, well_reference_lattice)
    well_piezo = _piezoelectric_polarization(well_polarization, well_strain)
    well_spontaneous = _optional_float(well_polarization.get("spontaneous_polarization_c_per_m2"))
    well_total = _round(float(well_spontaneous or 0.0) + float(well_piezo or 0.0))

    offset_by_material = {
        str(row.get("material")): row
        for row in (band_alignment or {}).get("offsets", []) or []
        if isinstance(row, dict) and row.get("material")
    }
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for material in barrier_materials:
        barrier_polarization = _nitride_polarization_properties(metadata, material)
        if not barrier_polarization:
            warnings.append(f"Missing III-nitride polarization reference for {material}.")
            continue
        barrier_reference_lattice = (
            _material_reference_lattice(metadata, material)
            or _optional_float(barrier_polarization.get("a_lattice_angstrom"))
        )
        barrier_strain = _in_plane_strain(in_plane, barrier_reference_lattice)
        barrier_piezo = _piezoelectric_polarization(barrier_polarization, barrier_strain)
        barrier_spontaneous = _optional_float(barrier_polarization.get("spontaneous_polarization_c_per_m2"))
        barrier_total = _round(float(barrier_spontaneous or 0.0) + float(barrier_piezo or 0.0))
        discontinuity = _round(float(barrier_total) - float(well_total))
        sheet_density = _round(float(discontinuity) / ELEMENTARY_CHARGE_C / 1.0e4)
        sheet_density_abs = _round(abs(float(sheet_density)))
        offset = offset_by_material.get(material) or {}
        electron_barrier = _optional_float(offset.get("electron_barrier_height_ev"))
        al_fraction = _material_element_fraction(material, "Al")
        in_fraction = _material_element_fraction(material, "In")
        two_deg_candidate = bool(
            (al_fraction or 0.0) > 0.0
            and electron_barrier is not None
            and electron_barrier > 0.0
            and sheet_density_abs >= 1.0e12
        )
        if not two_deg_candidate:
            warnings.append(
                f"Polarization/2DEG preflight does not identify {material} as an electron-confining Al-containing barrier."
            )
        rows.append(
            {
                "barrier_material": material,
                "barrier_al_fraction": _round(al_fraction) if al_fraction is not None else None,
                "barrier_in_fraction": _round(in_fraction) if in_fraction is not None else None,
                "in_plane_lattice_angstrom": _round(in_plane) if in_plane is not None else None,
                "barrier_reference_lattice_angstrom": _round(barrier_reference_lattice)
                if barrier_reference_lattice is not None
                else None,
                "barrier_in_plane_strain_percent": _round(float(barrier_strain) * 100.0)
                if barrier_strain is not None
                else None,
                "well_spontaneous_polarization_c_per_m2": _round(well_spontaneous) if well_spontaneous is not None else None,
                "well_piezoelectric_polarization_c_per_m2": _round(well_piezo) if well_piezo is not None else None,
                "well_total_polarization_c_per_m2": well_total,
                "barrier_spontaneous_polarization_c_per_m2": _round(barrier_spontaneous)
                if barrier_spontaneous is not None
                else None,
                "barrier_piezoelectric_polarization_c_per_m2": _round(barrier_piezo) if barrier_piezo is not None else None,
                "barrier_total_polarization_c_per_m2": barrier_total,
                "polarization_discontinuity_c_per_m2": discontinuity,
                "sheet_charge_density_c_per_m2": discontinuity,
                "sheet_carrier_density_cm2_abs": sheet_density_abs,
                "electron_barrier_height_ev": electron_barrier,
                "two_deg_candidate": two_deg_candidate,
            }
        )

    if not rows:
        return None
    candidate_count = sum(1 for row in rows if row.get("two_deg_candidate"))
    quality = "complete" if candidate_count and not warnings else "review"
    return {
        "available": True,
        "model": "iii_nitride_polarization_2deg_metadata_preflight",
        "reference": "endpoint_linear_interpolation_for_preflight_only",
        "interface": (heterostructure or {}).get("interface") or metadata.get("interface"),
        "interface_orientation": orientation or None,
        "axis": metadata.get("interface_axis") or (heterostructure or {}).get("interface_axis"),
        "well_material": well_material,
        "barrier_materials": barrier_materials,
        "candidate_count": candidate_count,
        "max_abs_sheet_carrier_density_cm2": _round(
            max((float(row.get("sheet_carrier_density_cm2_abs") or 0.0) for row in rows), default=0.0)
        ),
        "quality": quality,
        "warning_count": len(warnings),
        "warnings": warnings,
        "notes": [
            "Metadata-only III-nitride polarization/2DEG preflight; not a self-consistent electrostatic or DFT result.",
            "Use explicit electronic-structure and device calculations before drawing quantitative HEMT conclusions.",
        ],
        "barriers": rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _p_gan_gate_cap_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
    dopant_site_summary: dict[str, Any] | None,
    polarization_2deg: dict[str, Any] | None,
) -> dict[str, Any] | None:
    raw = metadata.get("p_gan_gate_cap")
    if not isinstance(raw, dict):
        return None

    raw_layers = [
        dict(item)
        for item in raw.get("layers", []) or []
        if isinstance(item, dict)
    ]
    layer_by_atom: dict[str, dict[str, Any]] = {}
    if isinstance(layer_profile, dict):
        for layer in layer_profile.get("layers", []) or []:
            if not isinstance(layer, dict):
                continue
            for atom_id in layer.get("atom_ids", []) or []:
                layer_by_atom[str(atom_id)] = layer

    dopant_atom_id = str(raw.get("dopant_atom_id") or "")
    dopant_entries = (
        dopant_site_summary.get("entries", [])
        if isinstance(dopant_site_summary, dict)
        else []
    )
    dopant_site_found = any(
        isinstance(entry, dict) and str(entry.get("atom_id") or entry.get("site_id") or "") == dopant_atom_id
        for entry in dopant_entries
    )
    polarization_barriers = (
        polarization_2deg.get("barrier_materials", [])
        if isinstance(polarization_2deg, dict)
        else metadata.get("polarization_2deg_barrier_materials", [])
    )
    if isinstance(polarization_barriers, str):
        polarization_barriers = [polarization_barriers]
    polarization_barriers = [str(material) for material in polarization_barriers or [] if str(material)]
    polarization_quality = polarization_2deg.get("quality") if isinstance(polarization_2deg, dict) else None

    layers: list[dict[str, Any]] = []
    warnings: list[str] = []
    matched_layer_count = 0
    split_layer_count = 0
    for fallback_index, raw_layer in enumerate(raw_layers, start=1):
        atom_ids = [str(atom_id) for atom_id in raw_layer.get("atom_ids", []) or [] if str(atom_id)]
        matched_layers = {
            _optional_int(layer_by_atom[atom_id].get("layer_index")): layer_by_atom[atom_id]
            for atom_id in atom_ids
            if atom_id in layer_by_atom and _optional_int(layer_by_atom[atom_id].get("layer_index")) is not None
        }
        global_layer = None
        if matched_layers:
            matched_layer_count += 1
            if len(matched_layers) > 1:
                split_layer_count += 1
            global_layer = matched_layers[sorted(matched_layers)[0]]
        layer_row = {
            "cap_layer_index": _optional_int(raw_layer.get("layer_index")) or fallback_index,
            "template_layer_index": _optional_int(raw_layer.get("template_layer_index")),
            "global_layer_index": global_layer.get("layer_index") if global_layer else None,
            "fractional_center": global_layer.get("fractional_center") if global_layer else raw_layer.get("fractional_center"),
            "axis_coordinate_angstrom": global_layer.get("axis_coordinate_angstrom") if global_layer else None,
            "atom_count": len(atom_ids),
            "atom_ids": atom_ids,
            "dopant_layer": bool(dopant_atom_id and dopant_atom_id in atom_ids),
            "matched_current_structure": bool(global_layer),
        }
        layers.append(layer_row)

    requested_thickness = _optional_float(raw.get("requested_thickness_angstrom"))
    actual_thickness = _optional_float(raw.get("actual_thickness_angstrom"))
    thickness_error = _optional_float(raw.get("thickness_error_angstrom"))
    layer_spacing = _optional_float(raw.get("layer_spacing_angstrom"))
    layer_count = _optional_int(raw.get("layer_count")) or len(raw_layers)
    if matched_layer_count != len(raw_layers):
        warnings.append("Not all declared p-GaN cap layers were found in the current layer profile.")
    if split_layer_count:
        warnings.append("At least one declared p-GaN cap layer maps to multiple current layer-profile rows.")
    if dopant_atom_id and not dopant_site_found:
        warnings.append("p-GaN cap Mg dopant marker is missing from dopant_site_summary.")
    if requested_thickness is not None and actual_thickness is not None and layer_spacing is not None:
        motif_indices = {
            _optional_int(layer.get("template_layer_index"))
            for layer in raw_layers
            if _optional_int(layer.get("template_layer_index")) is not None
        }
        motif_length = max(motif_indices) if motif_indices else 1
        motif_tolerance = max(float(layer_spacing) * max(int(motif_length), 1), 1e-9)
        if abs(float(actual_thickness) - float(requested_thickness)) > motif_tolerance:
            warnings.append("p-GaN cap actual thickness differs from requested thickness by more than one periodic motif.")
    if not polarization_barriers:
        warnings.append("No explicit 2DEG barrier materials are recorded for the p-GaN HEMT preflight.")
    if polarization_quality not in {None, "complete"}:
        warnings.append("2DEG polarization preflight is not complete for the p-GaN HEMT stack.")

    return {
        "available": True,
        "model": "metadata_backed_p_gan_gate_cap_preflight",
        "material": raw.get("material") or "p-GaN",
        "role": raw.get("role") or "gate_cap",
        "axis": raw.get("axis") or "c",
        "source": raw.get("source"),
        "quality": "complete" if not warnings else "review",
        "requested_thickness_angstrom": requested_thickness,
        "actual_thickness_angstrom": actual_thickness,
        "thickness_error_angstrom": thickness_error,
        "layer_count": layer_count,
        "declared_layer_record_count": len(raw_layers),
        "matched_layer_count": matched_layer_count,
        "layer_spacing_angstrom": layer_spacing,
        "dopant_element": raw.get("dopant_element"),
        "dopant_site_element": raw.get("dopant_site_element"),
        "dopant_atom_id": dopant_atom_id or None,
        "dopant_fraction_of_cap_cations": _optional_float(raw.get("dopant_fraction_of_cap_cations")),
        "dopant_site_found": dopant_site_found,
        "polarization_2deg_quality": polarization_quality,
        "polarization_2deg_barrier_materials": polarization_barriers,
        "warning_count": len(warnings),
        "warnings": warnings,
        "notes": raw.get("notes") or [],
        "layers": layers[:MAX_HEALTH_DETAIL_ROWS],
    }


def _interface_quality_summary(
    metadata: dict[str, Any],
    interface_profile: dict[str, Any] | None,
    quantum_well: dict[str, Any] | None,
    heterostructure: dict[str, Any] | None,
    superlattice_period: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not interface_profile or not interface_profile.get("segments"):
        return None

    raw_materials = interface_profile.get("materials") or (heterostructure or {}).get("materials") or []
    if isinstance(raw_materials, str):
        materials = [raw_materials]
    else:
        materials = list(raw_materials)
    materials = [str(material) for material in materials if str(material)]

    segment_rows: list[dict[str, Any]] = []
    material_count = len(materials)
    well_material = (quantum_well or {}).get("well_material") or (heterostructure or {}).get("substrate") or (materials[0] if materials else None)
    contact_metal = str(metadata.get("metal_contact_material") or "")
    contact_semiconductor = str(metadata.get("semiconductor_channel_material") or "")

    def segment_role(material: str | None) -> str:
        if metadata.get("metal_semiconductor_interface"):
            if material and material == contact_metal:
                return "metal"
            if material and material == contact_semiconductor:
                return "semiconductor"
        return _quantum_well_segment_role(material, str(well_material) if well_material else None)

    if isinstance(quantum_well, dict) and quantum_well.get("segments"):
        source_segments = [
            dict(segment)
            for segment in quantum_well.get("segments", []) or []
            if isinstance(segment, dict)
        ]
        for fallback_index, segment in enumerate(source_segments, start=1):
            segment_index = _optional_int(segment.get("segment_index")) or fallback_index
            segment_in_period = _optional_int(segment.get("segment_in_period"))
            if segment_in_period is None and material_count:
                segment_in_period = ((segment_index - 1) % material_count) + 1
            expected_material = materials[segment_in_period - 1] if material_count and segment_in_period else None
            material = str(segment.get("material") or segment.get("material_marker") or "")
            row = {
                "segment_index": segment_index,
                "period_index": _optional_int(segment.get("period_index")) or (((segment_index - 1) // material_count) + 1 if material_count else None),
                "segment_in_period": segment_in_period,
                "material": material or None,
                "material_marker": segment.get("material_marker"),
                "role": segment.get("role") or segment_role(material or None),
                "expected_material": expected_material,
                "matches_expected_material": (material == expected_material) if material and expected_material else None,
                "first_layer_index": segment.get("first_layer_index"),
                "last_layer_index": segment.get("last_layer_index"),
                "layer_count": segment.get("layer_count"),
                "mixed_layer_count": segment.get("mixed_layer_count", 0),
            }
            segment_rows.append(row)
    else:
        marker_to_material = _material_marker_to_material_map(materials, metadata)
        for fallback_index, segment in enumerate(interface_profile.get("segments", []) or [], start=1):
            if not isinstance(segment, dict):
                continue
            segment_index = _optional_int(segment.get("segment_index")) or fallback_index
            segment_in_period = ((segment_index - 1) % material_count) + 1 if material_count else None
            expected_material = materials[segment_in_period - 1] if material_count and segment_in_period else None
            marker = str(segment.get("material_marker") or "")
            material = marker_to_material.get(marker, marker)
            row = {
                "segment_index": segment_index,
                "period_index": ((segment_index - 1) // material_count) + 1 if material_count else None,
                "segment_in_period": segment_in_period,
                "material": material or None,
                "material_marker": marker or None,
                "role": segment_role(material or None),
                "expected_material": expected_material,
                "matches_expected_material": (material == expected_material) if material and expected_material else None,
                "first_layer_index": segment.get("first_layer_index"),
                "last_layer_index": segment.get("last_layer_index"),
                "layer_count": segment.get("marker_layer_count"),
                "mixed_layer_count": 0,
            }
            segment_rows.append(row)

    if not segment_rows:
        return None

    segment_rows = sorted(segment_rows, key=lambda item: int(item.get("segment_index") or 0))
    if _is_single_oxide_interface_metadata(metadata):
        return _metal_oxide_interface_quality_summary(
            metadata,
            interface_profile,
            segment_rows,
            materials,
        )

    material_sequence = [str(row.get("material")) for row in segment_rows if row.get("material")]
    expected_material_sequence = [
        materials[(index % material_count)]
        for index in range(len(segment_rows))
    ] if material_count else []
    unique_material_sequence = list(dict.fromkeys(material_sequence))
    missing_declared_materials = [
        material
        for material in materials
        if material not in set(material_sequence)
    ]
    declared_materials_present = None if not materials else not missing_declared_materials
    mismatch_rows = [
        row
        for row in segment_rows
        if row.get("matches_expected_material") is False
    ]

    period_count = (
        _optional_int((quantum_well or {}).get("period_count"))
        or _optional_int((superlattice_period or {}).get("estimated_total_period_count"))
    )
    if period_count is None and material_count:
        inferred = len(segment_rows) / material_count
        if inferred.is_integer():
            period_count = int(inferred)
    expected_segment_count = period_count * material_count if period_count is not None and material_count else None
    segment_count_matches_periods = (
        len(segment_rows) == expected_segment_count
        if expected_segment_count is not None
        else None
    )

    linear_transition_count = _optional_int(interface_profile.get("interface_transition_count"))
    expected_linear_transition_count = max(len(segment_rows) - 1, 0)
    transition_sequence_complete = (
        linear_transition_count == expected_linear_transition_count
        if linear_transition_count is not None
        else None
    )
    periodic_transition_count = len(segment_rows) if len(segment_rows) > 1 else 0
    period_sequence_complete = (
        not mismatch_rows and segment_count_matches_periods is not False
        if material_count
        else None
    )
    mixed_layer_count = int(interface_profile.get("mixed_layer_count") or 0)
    mixed_layers_expected = bool(
        metadata.get("mixed_layers_expected")
        or metadata.get("mixed_oxide_layers_expected")
    )

    warnings: list[str] = []
    if missing_declared_materials:
        warnings.append("Declared heterostructure materials are missing from the inferred interface sequence.")
    if segment_count_matches_periods is False:
        warnings.append("Interface material-segment count does not match the requested period count and material sequence.")
    if transition_sequence_complete is False:
        warnings.append("Interface transition count does not match the inferred material segment sequence.")
    if mismatch_rows:
        warnings.append("Interface material sequence does not match the declared material order.")
    if mixed_layer_count > 0 and not mixed_layers_expected:
        warnings.append("Interface contains mixed cation/alloy layers; inspect interface_profile_summary before quantitative interface calculations.")

    if any(item for item in (missing_declared_materials, mismatch_rows)) or segment_count_matches_periods is False or transition_sequence_complete is False:
        quality = "incomplete"
    elif mixed_layer_count > 0 and not mixed_layers_expected:
        quality = "complete_with_mixed_layers"
    else:
        quality = "complete"

    return {
        "available": True,
        "model": "semiconductor_interface_sequence_preflight",
        "interface": interface_profile.get("interface"),
        "axis": interface_profile.get("axis"),
        "axis_source": interface_profile.get("axis_source"),
        "materials": materials,
        "declared_material_count": material_count,
        "material_sequence": material_sequence,
        "expected_material_sequence": expected_material_sequence,
        "unique_material_sequence": unique_material_sequence,
        "missing_declared_materials": missing_declared_materials,
        "declared_materials_present": declared_materials_present,
        "period_count": period_count,
        "material_segment_count": len(segment_rows),
        "expected_segment_count_from_periods": expected_segment_count,
        "segment_count_matches_periods": segment_count_matches_periods,
        "period_sequence_complete": period_sequence_complete,
        "period_sequence_mismatch_count": len(mismatch_rows),
        "linear_interface_transition_count": linear_transition_count,
        "expected_linear_interface_transition_count": expected_linear_transition_count,
        "periodic_interface_transition_count": periodic_transition_count,
        "transition_sequence_complete": transition_sequence_complete,
        "mixed_layer_count": mixed_layer_count,
        "mixed_layers_expected": mixed_layers_expected,
        "mixed_layers_expected_reason": metadata.get("mixed_layers_expected_reason"),
        "shared_anion_layer_count": interface_profile.get("shared_anion_layer_count"),
        "abrupt_interface": interface_profile.get("abrupt_interface"),
        "quality": quality,
        "warning_count": len(warnings),
        "warnings": warnings,
        "segments": segment_rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _gate_stack_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
    interface_profile: dict[str, Any] | None,
    interface_quality: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _is_gate_stack_metadata(metadata):
        return None
    if not layer_profile or not interface_profile:
        return {
            "available": False,
            "model": "mos_gate_stack_preflight",
            "quality": "incomplete",
            "warnings": ["Missing layer or interface profile for gate-stack diagnostics."],
            "warning_count": 1,
        }

    raw_stack_sequence = metadata.get("stack_sequence") or metadata.get("materials") or []
    if isinstance(raw_stack_sequence, str):
        expected_stack_sequence = [raw_stack_sequence]
    else:
        expected_stack_sequence = [str(item) for item in raw_stack_sequence if str(item)]
    channel_material = str(metadata.get("semiconductor_channel_material") or metadata.get("substrate") or "")
    oxide_material = str(metadata.get("gate_oxide_material") or "")
    gate_material = str(metadata.get("gate_material") or "")
    for material in (channel_material, oxide_material, gate_material):
        if material and material not in expected_stack_sequence:
            expected_stack_sequence.append(material)

    materials = [
        str(material)
        for material in (interface_profile.get("materials") or expected_stack_sequence)
        if str(material)
    ]
    marker_to_material = _material_marker_to_material_map(materials or expected_stack_sequence, metadata)
    layer_by_index = {
        int(layer["layer_index"]): dict(layer)
        for layer in layer_profile.get("layers", []) or []
        if isinstance(layer, dict) and _optional_int(layer.get("layer_index")) is not None
    }
    interface_layer_by_index = {
        int(layer["layer_index"]): dict(layer)
        for layer in interface_profile.get("layers", []) or []
        if isinstance(layer, dict) and _optional_int(layer.get("layer_index")) is not None
    }
    segment_rows: list[dict[str, Any]] = []
    for fallback_index, segment in enumerate(interface_profile.get("segments", []) or [], start=1):
        if not isinstance(segment, dict):
            continue
        marker = str(segment.get("material_marker") or "")
        material = marker_to_material.get(marker, marker)
        first_layer_index = _optional_int(segment.get("first_layer_index"))
        last_layer_index = _optional_int(segment.get("last_layer_index"))
        marker_layer_indices = [
            int(index)
            for index in segment.get("marker_layer_indices", []) or []
            if _optional_int(index) is not None
        ]
        if not marker_layer_indices and first_layer_index is not None and last_layer_index is not None:
            marker_layer_indices = [
                index
                for index in sorted(layer_by_index)
                if first_layer_index <= index <= last_layer_index
            ]
        segment_layers = [
            layer_by_index[index]
            for index in marker_layer_indices
            if index in layer_by_index
        ]
        if not material or not segment_layers:
            continue
        centers = [
            float(layer["axis_coordinate_angstrom"])
            for layer in segment_layers
            if layer.get("axis_coordinate_angstrom") is not None
        ]
        fractional_centers = [
            float(layer["fractional_center"])
            for layer in segment_layers
            if layer.get("fractional_center") is not None
        ]
        element_counts = _sum_layer_element_counts(segment_layers)
        interface_layers = [interface_layer_by_index.get(int(layer["layer_index"]), {}) for layer in segment_layers]
        mixed_layer_count = sum(1 for layer in interface_layers if bool(layer.get("mixed_layer")))
        atom_ids: list[str] = []
        for layer in segment_layers:
            atom_ids.extend(str(atom_id) for atom_id in layer.get("atom_ids", []) or [])
        segment_rows.append(
            {
                "segment_index": _optional_int(segment.get("segment_index")) or fallback_index,
                "material": material,
                "material_marker": marker or None,
                "role": _gate_stack_material_role(material, metadata),
                "first_layer_index": min(marker_layer_indices),
                "last_layer_index": max(marker_layer_indices),
                "layer_count": len(segment_layers),
                "mixed_layer_count": mixed_layer_count,
                "fractional_center_start": _round(min(fractional_centers)) if fractional_centers else None,
                "fractional_center_end": _round(max(fractional_centers)) if fractional_centers else None,
                "axis_center_start_angstrom": _round(min(centers)) if centers else None,
                "axis_center_end_angstrom": _round(max(centers)) if centers else None,
                "center_span_angstrom": _round(max(centers) - min(centers)) if len(centers) >= 2 else 0.0 if centers else None,
                "atom_count": sum(int(layer.get("atom_count") or 0) for layer in segment_layers),
                "element_counts": dict(sorted(element_counts.items())),
                "atom_ids": sorted(atom_ids),
            }
        )

    material_sequence = [
        str(material)
        for material in (interface_quality or {}).get("material_sequence", [])
        if str(material)
    ] or [str(segment.get("material")) for segment in segment_rows if segment.get("material")]
    missing_expected = [
        material
        for material in expected_stack_sequence
        if material not in set(material_sequence)
    ]
    sequence_matches_expected = (
        material_sequence == expected_stack_sequence
        if expected_stack_sequence and material_sequence
        else None
    )
    role_counts = Counter(str(segment.get("role")) for segment in segment_rows if segment.get("role"))
    warnings: list[str] = []
    if missing_expected:
        warnings.append("Declared MOS gate-stack materials are missing from the inferred layer sequence.")
    if sequence_matches_expected is False:
        warnings.append("Inferred MOS gate-stack sequence does not match the declared stack_sequence.")
    if gate_material and gate_material not in set(material_sequence):
        warnings.append("Declared metal gate material is missing from the inferred stack.")
    if oxide_material and oxide_material not in set(material_sequence):
        warnings.append("Declared gate oxide material is missing from the inferred stack.")
    if channel_material and channel_material not in set(material_sequence):
        warnings.append("Declared semiconductor channel material is missing from the inferred stack.")
    mixed_layer_count = int((interface_quality or {}).get("mixed_layer_count") or interface_profile.get("mixed_layer_count") or 0)
    mixed_layers_expected = bool((interface_quality or {}).get("mixed_layers_expected") or metadata.get("mixed_oxide_layers_expected"))
    if mixed_layer_count > 0 and not mixed_layers_expected:
        warnings.append("MOS gate-stack profile contains mixed layers that are not declared as expected.")

    if missing_expected or sequence_matches_expected is False:
        quality = "incomplete"
    elif warnings:
        quality = "complete_with_warnings"
    else:
        quality = "complete"

    segment_by_role = {str(segment.get("role")): segment for segment in segment_rows if segment.get("role")}
    return {
        "available": True,
        "model": "mos_gate_stack_preflight",
        "interface": interface_profile.get("interface") or metadata.get("interface"),
        "axis": layer_profile.get("axis") or interface_profile.get("axis"),
        "axis_source": layer_profile.get("axis_source") or interface_profile.get("axis_source"),
        "axis_length_angstrom": layer_profile.get("axis_length_angstrom"),
        "expected_stack_sequence": expected_stack_sequence,
        "material_sequence": material_sequence,
        "sequence_matches_expected": sequence_matches_expected,
        "missing_expected_materials": missing_expected,
        "gate_material": gate_material or None,
        "gate_oxide_material": oxide_material or None,
        "semiconductor_channel_material": channel_material or None,
        "metal_gate_present": bool(gate_material and gate_material in set(material_sequence)),
        "gate_oxide_present": bool(oxide_material and oxide_material in set(material_sequence)),
        "semiconductor_channel_present": bool(channel_material and channel_material in set(material_sequence)),
        "material_segment_count": len(segment_rows),
        "expected_segment_count": len(expected_stack_sequence) if expected_stack_sequence else None,
        "role_counts": dict(sorted(role_counts.items())),
        "mixed_layer_count": mixed_layer_count,
        "mixed_layers_expected": mixed_layers_expected,
        "interface_quality": (interface_quality or {}).get("quality"),
        "declared_oxide_thickness_angstrom": _optional_float(metadata.get("oxide_thickness_angstrom")),
        "declared_gate_thickness_angstrom": _optional_float(
            metadata.get("aluminum_gate_thickness_angstrom")
            or metadata.get("gate_thickness_angstrom")
            or metadata.get("metal_gate_thickness_angstrom")
        ),
        "declared_channel_thickness_angstrom": _optional_float(
            metadata.get("silicon_slab_thickness_angstrom")
            or metadata.get("channel_thickness_angstrom")
            or metadata.get("semiconductor_channel_thickness_angstrom")
        ),
        "declared_vacuum_angstrom": _optional_float(metadata.get("vacuum_angstrom")),
        "channel_center_span_angstrom": (segment_by_role.get("channel") or {}).get("center_span_angstrom"),
        "oxide_center_span_angstrom": (segment_by_role.get("oxide") or {}).get("center_span_angstrom"),
        "gate_center_span_angstrom": (segment_by_role.get("gate") or {}).get("center_span_angstrom"),
        "quality": quality,
        "warning_count": len(warnings),
        "warnings": warnings,
        "segments": segment_rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _metal_semiconductor_contact_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
    interface_profile: dict[str, Any] | None,
    interface_quality: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _is_metal_semiconductor_contact_metadata(metadata):
        return None
    if not layer_profile or not interface_profile:
        return {
            "available": False,
            "model": "metal_semiconductor_contact_preflight",
            "quality": "incomplete",
            "warnings": ["Missing layer or interface profile for metal/semiconductor contact diagnostics."],
            "warning_count": 1,
        }

    raw_sequence = metadata.get("stack_sequence") or metadata.get("materials") or []
    if isinstance(raw_sequence, str):
        expected_sequence = [raw_sequence]
    else:
        expected_sequence = [str(item) for item in raw_sequence if str(item)]
    semiconductor_material = str(metadata.get("semiconductor_channel_material") or metadata.get("substrate") or "")
    metal_material = str(metadata.get("metal_contact_material") or metadata.get("electrode_material") or "")
    for material in (semiconductor_material, metal_material):
        if material and material not in expected_sequence:
            expected_sequence.append(material)

    materials = [
        str(material)
        for material in (interface_profile.get("materials") or expected_sequence)
        if str(material)
    ]
    marker_to_material = _material_marker_to_material_map(materials or expected_sequence, metadata)
    layer_by_index = {
        int(layer["layer_index"]): dict(layer)
        for layer in layer_profile.get("layers", []) or []
        if isinstance(layer, dict) and _optional_int(layer.get("layer_index")) is not None
    }
    segment_rows: list[dict[str, Any]] = []
    for fallback_index, segment in enumerate(interface_profile.get("segments", []) or [], start=1):
        if not isinstance(segment, dict):
            continue
        marker = str(segment.get("material_marker") or "")
        material = marker_to_material.get(marker, marker)
        first_layer_index = _optional_int(segment.get("first_layer_index"))
        last_layer_index = _optional_int(segment.get("last_layer_index"))
        marker_layer_indices = [
            int(index)
            for index in segment.get("marker_layer_indices", []) or []
            if _optional_int(index) is not None
        ]
        if not marker_layer_indices and first_layer_index is not None and last_layer_index is not None:
            marker_layer_indices = [
                index
                for index in sorted(layer_by_index)
                if first_layer_index <= index <= last_layer_index
            ]
        segment_layers = [
            layer_by_index[index]
            for index in marker_layer_indices
            if index in layer_by_index
        ]
        if not material or not segment_layers:
            continue
        centers = [
            float(layer["axis_coordinate_angstrom"])
            for layer in segment_layers
            if layer.get("axis_coordinate_angstrom") is not None
        ]
        fractional_centers = [
            float(layer["fractional_center"])
            for layer in segment_layers
            if layer.get("fractional_center") is not None
        ]
        atom_ids: list[str] = []
        for layer in segment_layers:
            atom_ids.extend(str(atom_id) for atom_id in layer.get("atom_ids", []) or [])
        segment_rows.append(
            {
                "segment_index": _optional_int(segment.get("segment_index")) or fallback_index,
                "material": material,
                "material_marker": marker or None,
                "role": _metal_semiconductor_contact_role(material, metadata),
                "first_layer_index": min(marker_layer_indices),
                "last_layer_index": max(marker_layer_indices),
                "layer_count": len(segment_layers),
                "fractional_center_start": _round(min(fractional_centers)) if fractional_centers else None,
                "fractional_center_end": _round(max(fractional_centers)) if fractional_centers else None,
                "axis_center_start_angstrom": _round(min(centers)) if centers else None,
                "axis_center_end_angstrom": _round(max(centers)) if centers else None,
                "center_span_angstrom": _round(max(centers) - min(centers)) if len(centers) >= 2 else 0.0 if centers else None,
                "atom_count": sum(int(layer.get("atom_count") or 0) for layer in segment_layers),
                "element_counts": dict(sorted(_sum_layer_element_counts(segment_layers).items())),
                "atom_ids": sorted(atom_ids),
            }
        )

    material_sequence = [
        str(material)
        for material in (interface_quality or {}).get("material_sequence", [])
        if str(material)
    ] or [str(segment.get("material")) for segment in segment_rows if segment.get("material")]
    sequence_matches_expected = (
        material_sequence == expected_sequence
        if expected_sequence and material_sequence
        else None
    )
    role_counts = Counter(str(segment.get("role")) for segment in segment_rows if segment.get("role"))
    missing_expected = [
        material
        for material in expected_sequence
        if material not in set(material_sequence)
    ]
    warnings: list[str] = []
    if missing_expected:
        warnings.append("Declared metal/semiconductor contact materials are missing from the inferred layer sequence.")
    if sequence_matches_expected is False:
        warnings.append("Inferred metal/semiconductor contact sequence does not match the declared stack_sequence.")
    if not role_counts.get("metal"):
        warnings.append("No metal contact segment was inferred.")
    if not role_counts.get("semiconductor"):
        warnings.append("No semiconductor segment was inferred.")
    geometry = _metal_semiconductor_contact_geometry(
        segment_rows,
        declared_gap=_optional_float(metadata.get("interface_gap_angstrom")),
        declared_metal_thickness=_optional_float(
            metadata.get("metal_contact_thickness_angstrom")
            or metadata.get("electrode_thickness_angstrom")
        ),
    )
    warnings.extend(str(item) for item in geometry.get("warnings", []) or [])
    barrier_preflight = _schottky_barrier_preflight(metadata)
    barrier_warnings = [
        str(item)
        for item in (barrier_preflight or {}).get("warnings", []) or []
        if str(item)
    ]
    warnings.extend(barrier_warnings)
    if missing_expected or sequence_matches_expected is False or not role_counts.get("metal") or not role_counts.get("semiconductor"):
        quality = "incomplete"
    elif warnings:
        quality = "complete_with_warnings"
    else:
        quality = "complete"

    return {
        "available": True,
        "model": "metal_semiconductor_contact_preflight",
        "contact_type": str(metadata.get("contact_type") or ("schottky" if metadata.get("schottky_contact") else "metal_semiconductor")),
        "interface": interface_profile.get("interface") or metadata.get("interface"),
        "axis": layer_profile.get("axis") or interface_profile.get("axis"),
        "axis_source": layer_profile.get("axis_source") or interface_profile.get("axis_source"),
        "axis_length_angstrom": layer_profile.get("axis_length_angstrom"),
        "expected_contact_sequence": expected_sequence,
        "material_sequence": material_sequence,
        "sequence_matches_expected": sequence_matches_expected,
        "missing_expected_materials": missing_expected,
        "metal_material": metal_material or None,
        "semiconductor_material": semiconductor_material or None,
        "metal_present": bool(role_counts.get("metal")),
        "semiconductor_present": bool(role_counts.get("semiconductor")),
        "material_segment_count": len(segment_rows),
        "role_counts": dict(sorted(role_counts.items())),
        "interface_quality": (interface_quality or {}).get("quality"),
        "abrupt_interface": (interface_quality or {}).get("abrupt_interface") or interface_profile.get("abrupt_interface"),
        "declared_contact_gap_angstrom": _optional_float(metadata.get("interface_gap_angstrom")),
        "actual_contact_gap_angstrom": geometry.get("actual_contact_gap_angstrom"),
        "contact_gap_delta_angstrom": geometry.get("contact_gap_delta_angstrom"),
        "contact_geometry_status": geometry.get("contact_geometry_status"),
        "contact_geometry_next_action": geometry.get("contact_geometry_next_action"),
        "declared_metal_thickness_angstrom": _optional_float(
            metadata.get("metal_contact_thickness_angstrom")
            or metadata.get("electrode_thickness_angstrom")
        ),
        "actual_metal_thickness_angstrom": geometry.get("actual_metal_thickness_angstrom"),
        "metal_thickness_delta_angstrom": geometry.get("metal_thickness_delta_angstrom"),
        "declared_semiconductor_thickness_angstrom": _optional_float(
            metadata.get("silicon_slab_thickness_angstrom")
            or metadata.get("channel_thickness_angstrom")
            or metadata.get("semiconductor_channel_thickness_angstrom")
        ),
        "barrier_preflight": barrier_preflight,
        "quality": quality,
        "warning_count": len(warnings),
        "warnings": warnings,
        "segments": segment_rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _metal_semiconductor_contact_geometry(
    segment_rows: list[dict[str, Any]],
    *,
    declared_gap: float | None,
    declared_metal_thickness: float | None,
) -> dict[str, Any]:
    metal_segments = [segment for segment in segment_rows if segment.get("role") == "metal"]
    semiconductor_segments = [segment for segment in segment_rows if segment.get("role") == "semiconductor"]
    metal_segment = metal_segments[0] if metal_segments else None
    semiconductor_segment = semiconductor_segments[-1] if semiconductor_segments else None
    actual_gap = None
    if metal_segment and semiconductor_segment:
        metal_start = _optional_float(metal_segment.get("axis_center_start_angstrom"))
        semiconductor_end = _optional_float(semiconductor_segment.get("axis_center_end_angstrom"))
        if metal_start is not None and semiconductor_end is not None:
            actual_gap = _round(metal_start - semiconductor_end)
    actual_metal_thickness = None
    if metal_segment:
        actual_metal_thickness = _optional_float(metal_segment.get("center_span_angstrom"))

    gap_delta = _round(actual_gap - declared_gap) if actual_gap is not None and declared_gap is not None else None
    metal_delta = (
        _round(actual_metal_thickness - declared_metal_thickness)
        if actual_metal_thickness is not None and declared_metal_thickness is not None
        else None
    )
    warnings: list[str] = []
    tolerance = 0.05
    if gap_delta is not None and abs(gap_delta) > tolerance:
        warnings.append("Declared contact gap differs from inferred geometry; inspect actual_contact_gap_angstrom.")
    if metal_delta is not None and abs(metal_delta) > tolerance:
        warnings.append("Declared metal contact thickness differs from inferred geometry; inspect actual_metal_thickness_angstrom.")
    declared_checks = [
        (declared_gap, actual_gap, gap_delta),
        (declared_metal_thickness, actual_metal_thickness, metal_delta),
    ]
    expected_check_count = sum(1 for declared, _actual, _delta in declared_checks if declared is not None)
    unresolved_check_count = sum(
        1 for declared, actual, _delta in declared_checks if declared is not None and actual is None
    )
    mismatch_count = sum(
        1 for _declared, _actual, delta in declared_checks if delta is not None and abs(delta) > tolerance
    )
    if mismatch_count:
        geometry_status = "mismatch"
        geometry_next_action = "apply_contact_gap_or_thickness_geometry_patch_before_claiming_normality"
    elif expected_check_count and not unresolved_check_count:
        geometry_status = "matched"
        geometry_next_action = "geometry_matches_declared_contact_metadata"
    else:
        geometry_status = "unknown"
        geometry_next_action = "inspect_contact_geometry_inputs_before_claiming_normality"
    return {
        "actual_contact_gap_angstrom": actual_gap,
        "contact_gap_delta_angstrom": gap_delta,
        "contact_geometry_status": geometry_status,
        "contact_geometry_next_action": geometry_next_action,
        "actual_metal_thickness_angstrom": actual_metal_thickness,
        "metal_thickness_delta_angstrom": metal_delta,
        "warnings": warnings,
    }


def _schottky_barrier_preflight(metadata: dict[str, Any]) -> dict[str, Any] | None:
    contact_type = str(metadata.get("contact_type") or "").lower()
    if not (metadata.get("schottky_contact") or contact_type == "schottky"):
        return None

    metal_work_function = _optional_float(metadata.get("metal_work_function_ev"))
    electron_affinity = _optional_float(metadata.get("semiconductor_electron_affinity_ev"))
    band_gap = _optional_float(metadata.get("semiconductor_band_gap_ev"))
    warnings: list[str] = []
    if metal_work_function is None:
        warnings.append("Missing metal_work_function_ev for Schottky barrier preflight.")
    if electron_affinity is None:
        warnings.append("Missing semiconductor_electron_affinity_ev for Schottky barrier preflight.")
    if band_gap is None:
        warnings.append("Missing semiconductor_band_gap_ev for Schottky barrier preflight.")

    n_type_barrier = None
    p_type_barrier = None
    if metal_work_function is not None and electron_affinity is not None:
        n_type_barrier = _round(metal_work_function - electron_affinity)
        if n_type_barrier < 0:
            warnings.append("Ideal n-type Schottky-Mott barrier is negative; inspect contact metadata.")
    if n_type_barrier is not None and band_gap is not None:
        p_type_barrier = _round(band_gap - n_type_barrier)
        if p_type_barrier < 0:
            warnings.append("Ideal p-type Schottky-Mott barrier is negative; inspect contact metadata.")

    return {
        "available": True,
        "model": str(metadata.get("schottky_barrier_model") or "ideal_schottky_mott_metadata_reference"),
        "reference": metadata.get("schottky_barrier_reference"),
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": electron_affinity,
        "semiconductor_band_gap_ev": band_gap,
        "ideal_n_type_barrier_ev": n_type_barrier,
        "ideal_p_type_barrier_ev": p_type_barrier,
        "warning_count": len(warnings),
        "warnings": warnings,
        "notes": [
            "Metadata-only Schottky-Mott preflight; not a DFT band-alignment result.",
            "Use explicit electronic-structure calculations before drawing quantitative device conclusions.",
        ],
    }


def _gate_stack_material_role(material: str, metadata: dict[str, Any]) -> str:
    if material == str(metadata.get("gate_material") or ""):
        return "gate"
    if material == str(metadata.get("gate_oxide_material") or ""):
        return "oxide"
    if material == str(metadata.get("semiconductor_channel_material") or metadata.get("substrate") or ""):
        return "channel"
    lower = material.lower()
    if lower in {"sio2", "hfo2", "al2o3"} or "oxide" in lower:
        return "oxide"
    if material in {"Al", "Cu", "TiN", "W", "Mo"}:
        return "gate"
    return "channel"


def _is_metal_semiconductor_contact_metadata(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    contact_type = str(metadata.get("contact_type") or "").lower()
    return bool(
        metadata.get("metal_semiconductor_interface")
        or metadata.get("schottky_contact")
        or contact_type in {"schottky", "ohmic", "metal_semiconductor"}
        or "schottky" in family
        or "metal semiconductor" in family
        or "metal-semiconductor" in family
    )


def _metal_semiconductor_contact_role(material: str, metadata: dict[str, Any]) -> str:
    if material == str(metadata.get("metal_contact_material") or metadata.get("electrode_material") or ""):
        return "metal"
    if material == str(metadata.get("semiconductor_channel_material") or metadata.get("substrate") or ""):
        return "semiconductor"
    if material in {"Al", "Cu", "TiN", "W", "Mo", "Ni", "Au", "Pt", "Ag"}:
        return "metal"
    return "semiconductor"


def _material_marker_to_material_map(materials: list[str], metadata: dict[str, Any] | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    element_to_materials: dict[str, list[str]] = {}
    for material in materials:
        mapping[material] = material
        for element in _material_label_elements(material):
            element_to_materials.setdefault(element, []).append(material)
    for element, element_materials in element_to_materials.items():
        unique_materials = sorted(set(element_materials))
        if len(unique_materials) == 1:
            mapping[element] = unique_materials[0]
    marker_map = (metadata or {}).get("material_marker_map")
    if isinstance(marker_map, dict):
        for marker, material in marker_map.items():
            if marker is not None and material is not None:
                mapping[str(marker)] = str(material)
    return mapping


def _is_metal_oxide_interface_metadata(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    return bool(metadata.get("metal_oxide_interface") or "metal oxide interface" in family)


def _is_single_oxide_interface_metadata(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    return bool(
        _is_metal_oxide_interface_metadata(metadata)
        or metadata.get("oxide_interface")
        or metadata.get("semiconductor_oxide_interface")
        or "oxide interface" in family
    )


def _is_gate_stack_metadata(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    return bool(
        metadata.get("metal_gate_stack")
        or metadata.get("gate_stack")
        or "gate stack" in family
        or "mos capacitor" in family
    )


def _surface_slab_diagnostics_applicable(metadata: dict[str, Any]) -> bool:
    if not any(
        key in metadata
        for key in (
            "surface_orientation",
            "surface_axis",
            "vacuum_angstrom",
            "slab_thickness_angstrom",
            "termination",
            "passivation",
            "surface_model",
        )
    ):
        return False

    family = str(metadata.get("structure_family") or "").lower()
    explicit_surface_model = bool(
        metadata.get("surface_model") is not None
        or metadata.get("slab_thickness_angstrom") is not None
        or metadata.get("termination") is not None
        or metadata.get("passivation") is not None
        or "slab" in family
        or "monolayer" in family
        or "surface" in family
    )
    interface_or_gate_stack = bool(
        _is_single_oxide_interface_metadata(metadata)
        or _is_metal_semiconductor_contact_metadata(metadata)
        or _is_gate_stack_metadata(metadata)
    )
    if interface_or_gate_stack and not explicit_surface_model:
        return False
    return True


def _metal_oxide_interface_quality_summary(
    metadata: dict[str, Any],
    interface_profile: dict[str, Any],
    segment_rows: list[dict[str, Any]],
    materials: list[str],
) -> dict[str, Any]:
    compressed_segments: list[dict[str, Any]] = []
    for row in segment_rows:
        material = str(row.get("material") or "")
        if not material:
            continue
        if compressed_segments and compressed_segments[-1].get("material") == material:
            current = compressed_segments[-1]
            current["last_layer_index"] = row.get("last_layer_index")
            current["layer_count"] = int(current.get("layer_count") or 0) + int(row.get("layer_count") or 0)
            current["source_segment_indices"].append(row.get("segment_index"))
            continue
        compressed_segments.append(
            {
                "segment_index": len(compressed_segments) + 1,
                "material": material,
                "first_layer_index": row.get("first_layer_index"),
                "last_layer_index": row.get("last_layer_index"),
                "layer_count": row.get("layer_count"),
                "source_segment_indices": [row.get("segment_index")],
                "mixed_layer_count": row.get("mixed_layer_count", 0),
            }
        )

    material_sequence = [str(row.get("material")) for row in compressed_segments if row.get("material")]
    expected_material_sequence = materials
    missing_declared_materials = [
        material
        for material in materials
        if material not in set(material_sequence)
    ]
    declared_materials_present = None if not materials else not missing_declared_materials
    sequence_matches = material_sequence == expected_material_sequence if expected_material_sequence else None
    linear_transition_count = max(len(compressed_segments) - 1, 0)
    raw_transition_count = _optional_int(interface_profile.get("interface_transition_count"))
    mixed_layer_count = int(interface_profile.get("mixed_layer_count") or 0)
    mixed_layers_expected = bool(metadata.get("mixed_oxide_layers_expected") or metadata.get("semiconductor_oxide_interface"))
    warnings: list[str] = []
    if missing_declared_materials:
        warnings.append("Declared oxide-interface materials are missing from the inferred layer sequence.")
    if sequence_matches is False:
        warnings.append("Inferred oxide-interface material sequence does not match the declared material order.")
    if mixed_layer_count > 0 and not mixed_layers_expected:
        warnings.append("Oxide-interface profile contains mixed layers; inspect interface_profile_summary.")
    quality = "complete"
    if missing_declared_materials or sequence_matches is False:
        quality = "incomplete"
    elif mixed_layer_count > 0 and not mixed_layers_expected:
        quality = "complete_with_mixed_layers"
    model_name = (
        "metal_oxide_interface_sequence_preflight"
        if _is_metal_oxide_interface_metadata(metadata)
        else "semiconductor_oxide_interface_sequence_preflight"
    )
    return {
        "available": True,
        "model": model_name,
        "interface": interface_profile.get("interface") or metadata.get("interface"),
        "axis": interface_profile.get("axis"),
        "axis_source": interface_profile.get("axis_source"),
        "materials": materials,
        "declared_material_count": len(materials),
        "material_sequence": material_sequence,
        "expected_material_sequence": expected_material_sequence,
        "unique_material_sequence": list(dict.fromkeys(material_sequence)),
        "missing_declared_materials": missing_declared_materials,
        "declared_materials_present": declared_materials_present,
        "period_count": None,
        "material_segment_count": len(compressed_segments),
        "raw_material_segment_count": len(segment_rows),
        "expected_segment_count_from_periods": None,
        "segment_count_matches_periods": None,
        "period_sequence_complete": sequence_matches,
        "period_sequence_mismatch_count": 0 if sequence_matches is not False else 1,
        "linear_interface_transition_count": linear_transition_count,
        "raw_linear_interface_transition_count": raw_transition_count,
        "expected_linear_interface_transition_count": linear_transition_count,
        "periodic_interface_transition_count": None,
        "transition_sequence_complete": True,
        "mixed_layer_count": mixed_layer_count,
        "mixed_layers_expected": mixed_layers_expected,
        "shared_anion_layer_count": interface_profile.get("shared_anion_layer_count"),
        "abrupt_interface": bool(linear_transition_count > 0 and mixed_layer_count == 0),
        "quality": quality,
        "warning_count": len(warnings),
        "warnings": warnings,
        "segments": compressed_segments[:MAX_HEALTH_DETAIL_ROWS],
    }


def _semiconductor_oxide_interface_applicable(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    return bool(
        not _is_metal_oxide_interface_metadata(metadata)
        and (
            metadata.get("semiconductor_oxide_interface")
            or "semiconductor oxide interface" in family
            or "mos capacitor" in family
        )
    )


def _semiconductor_oxide_material(metadata: dict[str, Any]) -> str | None:
    explicit = metadata.get("oxide_material") or metadata.get("gate_oxide_material")
    if explicit:
        return str(explicit)
    raw_materials = metadata.get("materials") or []
    materials = [raw_materials] if isinstance(raw_materials, str) else list(raw_materials)
    for material in materials:
        formula = _material_formula_amounts(str(material))
        if formula and formula.get("O", 0.0) > 0 and any(
            element != "O" and amount > 0
            for element, amount in formula.items()
        ):
            return str(material)
    return None


def _semiconductor_oxide_interface_boundary(
    interface_profile: dict[str, Any],
    *,
    semiconductor_material: str | None,
    oxide_material: str | None,
) -> dict[str, Any] | None:
    if not oxide_material:
        return None
    layers_by_index = {
        int(layer["layer_index"]): layer
        for layer in interface_profile.get("layers", []) or []
        if isinstance(layer, dict) and _optional_int(layer.get("layer_index")) is not None
    }
    for transition in interface_profile.get("transitions", []) or []:
        if not isinstance(transition, dict):
            continue
        from_material = str(transition.get("from_material_group") or "")
        to_material = str(transition.get("to_material_group") or "")
        materials = {from_material, to_material}
        if oxide_material not in materials:
            continue
        if semiconductor_material and semiconductor_material not in materials:
            continue
        from_index = _optional_int(transition.get("from_layer_index"))
        to_index = _optional_int(transition.get("to_layer_index"))
        from_layer = layers_by_index.get(from_index, {})
        to_layer = layers_by_index.get(to_index, {})
        from_coordinate = _optional_float(from_layer.get("axis_coordinate_angstrom"))
        to_coordinate = _optional_float(to_layer.get("axis_coordinate_angstrom"))
        boundary_coordinate = (
            _round((from_coordinate + to_coordinate) / 2.0)
            if from_coordinate is not None and to_coordinate is not None
            else None
        )
        oxide_layer_index = to_index if to_material == oxide_material else from_index
        semiconductor_layer_index = from_index if to_material == oxide_material else to_index
        return {
            "from_material": from_material or None,
            "to_material": to_material or None,
            "from_layer_index": from_index,
            "to_layer_index": to_index,
            "semiconductor_layer_index": semiconductor_layer_index,
            "oxide_layer_index": oxide_layer_index,
            "axis_coordinate_angstrom": boundary_coordinate,
        }
    return None


def _semiconductor_oxide_interface_spacing_rows(
    metadata: dict[str, Any],
    interface_profile: dict[str, Any],
    *,
    semiconductor_material: str | None,
    oxide_material: str | None,
) -> list[dict[str, Any]]:
    if not semiconductor_material or not oxide_material:
        return []

    tolerance = _optional_float(
        metadata.get("interface_spacing_tolerance_angstrom")
    )
    if tolerance is None or not math.isfinite(tolerance) or tolerance <= 0.0:
        tolerance = OXIDE_INTERFACE_SPACING_TOLERANCE_ANGSTROM
    layers_by_index = {
        int(layer["layer_index"]): layer
        for layer in interface_profile.get("layers", []) or []
        if isinstance(layer, dict)
        and _optional_int(layer.get("layer_index")) is not None
    }
    gate_material = str(metadata.get("gate_material") or "") or None
    if gate_material is None and (
        metadata.get("metal_gate_stack") or metadata.get("gate_stack")
    ):
        sequence = metadata.get("stack_sequence") or metadata.get("materials") or []
        if isinstance(sequence, (list, tuple)) and len(sequence) >= 3:
            candidate = sequence[-1]
            if candidate is not None and str(candidate).strip():
                gate_material = str(candidate).strip()
    targets: list[tuple[str, str, str, str | None, str | None]] = [
        (
            "semiconductor_oxide",
            semiconductor_material,
            oxide_material,
            "semiconductor_oxide_interface_gap_angstrom",
            "interface_gap_angstrom",
        )
    ]
    if gate_material:
        targets.append(
            (
                "oxide_gate",
                oxide_material,
                gate_material,
                "oxide_gate_interface_gap_angstrom",
                None,
            )
        )

    rows: list[dict[str, Any]] = []
    transitions = [
        transition
        for transition in interface_profile.get("transitions", []) or []
        if isinstance(transition, dict)
    ]
    for target_interface, first_material, second_material, primary_key, fallback_key in targets:
        expected_materials = {first_material, second_material}
        matches = [
            transition
            for transition in transitions
            if {
                str(transition.get("from_material_group") or ""),
                str(transition.get("to_material_group") or ""),
            }
            == expected_materials
        ]
        declared_source = None
        declared_gap = None
        if primary_key and primary_key in metadata:
            declared_source = primary_key
            declared_gap = _optional_float(metadata.get(primary_key))
        elif fallback_key and fallback_key in metadata:
            declared_source = fallback_key
            declared_gap = _optional_float(metadata.get(fallback_key))
        declared_gap_status = "not_declared"
        if declared_source is not None:
            if (
                declared_gap is None
                or not math.isfinite(declared_gap)
                or declared_gap <= 0.0
                or declared_gap > 100.0
            ):
                declared_gap = None
                declared_gap_status = "invalid"
            else:
                declared_gap_status = "valid"

        row: dict[str, Any] = {
            "target_interface": target_interface,
            "spacing_definition": "adjacent_boundary_layer_center_distance",
            "expected_materials": [first_material, second_material],
            "declared_gap_angstrom": (
                _round(declared_gap) if declared_gap is not None else None
            ),
            "declared_gap_source": declared_source,
            "declared_gap_status": declared_gap_status,
            "tolerance_angstrom": _round(tolerance),
            "transition_match_count": len(matches),
            "patch_operation": None,
        }
        if len(matches) != 1:
            row.update(
                {
                    "binding_status": (
                        "missing" if not matches else "ambiguous"
                    ),
                    "status": "binding_review",
                    "matches_declared_gap": None,
                    "actual_gap_angstrom": None,
                    "actual_minus_declared_angstrom": None,
                }
            )
            rows.append(row)
            continue

        transition = matches[0]
        from_index = _optional_int(transition.get("from_layer_index"))
        to_index = _optional_int(transition.get("to_layer_index"))
        from_layer = layers_by_index.get(from_index, {})
        to_layer = layers_by_index.get(to_index, {})
        from_coordinate = _optional_float(from_layer.get("axis_coordinate_angstrom"))
        to_coordinate = _optional_float(to_layer.get("axis_coordinate_angstrom"))
        if from_coordinate is None or to_coordinate is None:
            row.update(
                {
                    "binding_status": "coordinate_missing",
                    "status": "binding_review",
                    "matches_declared_gap": None,
                    "actual_gap_angstrom": None,
                    "actual_minus_declared_angstrom": None,
                }
            )
            rows.append(row)
            continue

        if from_coordinate <= to_coordinate:
            lower_layer, upper_layer = from_layer, to_layer
            lower_coordinate, upper_coordinate = from_coordinate, to_coordinate
        else:
            lower_layer, upper_layer = to_layer, from_layer
            lower_coordinate, upper_coordinate = to_coordinate, from_coordinate
        actual_gap = upper_coordinate - lower_coordinate
        bound_fields = {
            "lower_material": lower_layer.get("material_group"),
            "upper_material": upper_layer.get("material_group"),
            "lower_layer_index": lower_layer.get("layer_index"),
            "upper_layer_index": upper_layer.get("layer_index"),
            "lower_axis_coordinate_angstrom": _round(lower_coordinate),
            "upper_axis_coordinate_angstrom": _round(upper_coordinate),
            "actual_gap_angstrom": _round(actual_gap),
        }
        if declared_gap_status == "invalid":
            row.update(
                {
                    **bound_fields,
                    "binding_status": "declared_value_invalid",
                    "status": "binding_review",
                    "matches_declared_gap": None,
                    "actual_minus_declared_angstrom": None,
                }
            )
            rows.append(row)
            continue

        delta = actual_gap - declared_gap if declared_gap is not None else None
        matches_declared = (
            abs(delta) <= tolerance if delta is not None else None
        )
        if declared_gap is None:
            status = "not_declared"
        elif matches_declared:
            status = "matched"
        else:
            status = "mismatch"
        row.update(
            {
                "binding_status": "bound",
                "status": status,
                **bound_fields,
                "actual_minus_declared_angstrom": (
                    _round(delta) if delta is not None else None
                ),
                "matches_declared_gap": matches_declared,
                "patch_operation": (
                    {
                        "type": "set_gate_stack_interface_gap",
                        "target_interface": target_interface,
                        "thickness_angstrom": _round(declared_gap),
                    }
                    if status == "mismatch"
                    else None
                ),
            }
        )
        rows.append(row)
    return rows


def _oxide_stoichiometry_status(
    oxygen_count: int,
    cation_count: int,
    expected_oxygen_per_cation: float | None,
) -> tuple[str, float | None, float | None, float | None, float | None]:
    if expected_oxygen_per_cation is None or cation_count <= 0:
        return "not_evaluated", None, None, None, None
    expected_oxygen = cation_count * expected_oxygen_per_cation
    delta = oxygen_count - expected_oxygen
    tolerance = 1e-6
    status = "matched" if abs(delta) <= tolerance else "oxygen_deficient" if delta < 0 else "oxygen_excess"
    return (
        status,
        _round(expected_oxygen),
        _round(delta),
        _round(max(-delta, 0.0)),
        _round(max(delta, 0.0)),
    )


def _sum_layer_element_counts(layers: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for layer in layers:
        for element, count in (layer.get("element_counts") or {}).items():
            try:
                counts[str(element)] += int(count)
            except (TypeError, ValueError):
                continue
    return dict(sorted((element, count) for element, count in counts.items() if count > 0))


def _semiconductor_oxide_interface_geometry_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    atom_rows: list[dict[str, Any]],
    neighbor_pair_rows: list[dict[str, Any]],
    coordination_rows: list[dict[str, Any]],
    layer_profile: dict[str, Any] | None,
    interface_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _semiconductor_oxide_interface_applicable(metadata):
        return None

    oxide_material = _semiconductor_oxide_material(metadata)
    semiconductor_material = str(
        metadata.get("semiconductor_channel_material") or metadata.get("substrate") or ""
    ) or None
    profile = interface_profile or {}
    layers = [
        dict(layer)
        for layer in profile.get("layers", []) or []
        if isinstance(layer, dict)
    ]
    layer_by_index = {
        int(layer["layer_index"]): layer
        for layer in layers
        if _optional_int(layer.get("layer_index")) is not None
    }
    oxide_layers = [
        layer
        for layer in layers
        if str(layer.get("material_group") or "") == str(oxide_material or "")
    ]
    if not oxide_layers:
        oxide_layers = [
            layer
            for layer in layers
            if int((layer.get("element_counts") or {}).get("O") or 0) > 0
            and str(layer.get("material_group") or "") != str(semiconductor_material or "")
        ]

    boundary = _semiconductor_oxide_interface_boundary(
        profile,
        semiconductor_material=semiconductor_material,
        oxide_material=oxide_material,
    )
    interface_spacings = _semiconductor_oxide_interface_spacing_rows(
        metadata,
        profile,
        semiconductor_material=semiconductor_material,
        oxide_material=oxide_material,
    )
    spacing_binding_reviews = [
        row for row in interface_spacings if row.get("status") == "binding_review"
    ]
    spacing_mismatches = [
        row for row in interface_spacings if row.get("status") == "mismatch"
    ]
    semiconductor_boundary_layer_index = _optional_int(
        (boundary or {}).get("semiconductor_layer_index")
    )
    oxide_boundary_layer_index = _optional_int(
        (boundary or {}).get("oxide_layer_index")
    )
    semiconductor_layer = layer_by_index.get(semiconductor_boundary_layer_index)
    oxide_boundary_layer = layer_by_index.get(oxide_boundary_layer_index)
    semiconductor_boundary_atom_ids = sorted(
        str(atom_id)
        for atom_id in (semiconductor_layer or {}).get("atom_ids", []) or []
        if atom_id
    )
    oxide_boundary_atom_ids = sorted(
        str(atom_id)
        for atom_id in (oxide_boundary_layer or {}).get("atom_ids", []) or []
        if atom_id
    )
    oxide_atom_ids = sorted(
        {
            str(atom_id)
            for layer in oxide_layers
            for atom_id in layer.get("atom_ids", []) or []
            if atom_id
        }
    )
    atom_by_id = {
        str(row.get("id")): row
        for row in atom_rows
        if row.get("id")
    }
    coordination_by_id = {
        str(row.get("atom_id")): row
        for row in coordination_rows
        if row.get("atom_id")
    }
    missing_atom_ids = sorted(
        atom_id
        for atom_id in set(
            semiconductor_boundary_atom_ids
            + oxide_boundary_atom_ids
            + oxide_atom_ids
        )
        if atom_id not in atom_by_id
    )
    atom_binding_complete = bool(
        boundary
        and semiconductor_boundary_atom_ids
        and oxide_boundary_atom_ids
        and oxide_atom_ids
        and not missing_atom_ids
    )

    formula = _material_formula_amounts(oxide_material)
    oxide_cation_elements = sorted(
        element
        for element, amount in (formula or {}).items()
        if element != "O" and amount > 0
    )
    if not oxide_cation_elements:
        oxide_cation_elements = sorted(
            {
                str(atom_by_id[atom_id].get("element"))
                for atom_id in oxide_atom_ids
                if atom_id in atom_by_id
                and str(atom_by_id[atom_id].get("element") or "") not in {"", "O"}
            }
        )

    boundary_pairs: list[dict[str, Any]] = []
    if atom_binding_complete and isinstance(spec.model, CrystalSpec):
        vectors = _lattice_vectors(spec.model.lattice)
        for semiconductor_atom_id in semiconductor_boundary_atom_ids:
            semiconductor_atom = atom_by_id[semiconductor_atom_id]
            semiconductor_fractional = _coerce_fractional(
                semiconductor_atom.get("fractional")
            )
            if semiconductor_fractional is None:
                continue
            for oxide_atom_id in oxide_boundary_atom_ids:
                oxide_atom = atom_by_id[oxide_atom_id]
                oxide_fractional = _coerce_fractional(oxide_atom.get("fractional"))
                if oxide_fractional is None:
                    continue
                distance, offset = _minimum_image_distance(
                    semiconductor_fractional,
                    oxide_fractional,
                    vectors,
                )
                semiconductor_element = str(semiconductor_atom.get("element") or "")
                oxide_element = str(oxide_atom.get("element") or "")
                threshold = _crystal_neighbor_threshold(
                    semiconductor_element,
                    oxide_element,
                )
                threshold_fraction = distance / threshold if threshold > 0 else None
                boundary_pairs.append(
                    {
                        "pair_scope": "semiconductor_oxide_boundary",
                        "semiconductor_atom_id": semiconductor_atom_id,
                        "semiconductor_element": semiconductor_element,
                        "oxide_atom_id": oxide_atom_id,
                        "oxide_element": oxide_element,
                        "pair_type": _element_pair_label(
                            semiconductor_element,
                            oxide_element,
                        ),
                        "distance_angstrom": _round(distance),
                        "neighbor_threshold_angstrom": _round(threshold),
                        "distance_to_threshold_fraction": (
                            _round(threshold_fraction)
                            if threshold_fraction is not None
                            else None
                        ),
                        "within_neighbor_cutoff": bool(distance <= threshold),
                        "short_contact_review": bool(
                            threshold_fraction is not None
                            and threshold_fraction
                            < OXIDE_INTERFACE_SHORT_CONTACT_THRESHOLD_FRACTION
                        ),
                        "image_offset_oxide_atom": list(offset),
                    }
                )
    boundary_pairs.sort(
        key=lambda row: (
            float(row.get("distance_angstrom") or 0.0),
            str(row.get("semiconductor_atom_id") or ""),
            str(row.get("oxide_atom_id") or ""),
        )
    )
    boundary_neighbor_pairs = [
        row for row in boundary_pairs if row.get("within_neighbor_cutoff") is True
    ]

    oxide_atom_id_set = set(oxide_atom_ids)
    oxide_internal_pairs = [
        dict(row)
        for row in neighbor_pair_rows
        if str(row.get("atom1") or "") in oxide_atom_id_set
        and str(row.get("atom2") or "") in oxide_atom_id_set
    ]
    internal_neighbors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in oxide_internal_pairs:
        atom1 = str(row.get("atom1") or "")
        atom2 = str(row.get("atom2") or "")
        internal_neighbors[atom1].append({**row, "neighbor_id": atom2})
        internal_neighbors[atom2].append({**row, "neighbor_id": atom1})
    boundary_neighbors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in boundary_neighbor_pairs:
        boundary_neighbors[str(row.get("semiconductor_atom_id") or "")].append(
            {**row, "neighbor_id": row.get("oxide_atom_id")}
        )
        boundary_neighbors[str(row.get("oxide_atom_id") or "")].append(
            {**row, "neighbor_id": row.get("semiconductor_atom_id")}
        )

    oxide_layer_index_by_atom = {
        str(atom_id): layer.get("layer_index")
        for layer in oxide_layers
        for atom_id in layer.get("atom_ids", []) or []
    }
    oxide_atom_rows: list[dict[str, Any]] = []
    for atom_id in oxide_atom_ids:
        atom = atom_by_id.get(atom_id, {})
        element = str(atom.get("element") or "") or None
        internal = internal_neighbors.get(atom_id, [])
        cross_boundary = boundary_neighbors.get(atom_id, [])
        internal_neighbor_ids = [
            str(row.get("neighbor_id"))
            for row in internal
            if row.get("neighbor_id")
        ]
        internal_neighbor_elements = [
            str((atom_by_id.get(neighbor_id) or {}).get("element") or "")
            for neighbor_id in internal_neighbor_ids
        ]
        relevant_distances = [
            float(row.get("distance_angstrom"))
            for row in [*internal, *cross_boundary]
            if _optional_float(row.get("distance_angstrom")) is not None
        ]
        global_coordination = coordination_by_id.get(atom_id) or {}
        oxide_atom_rows.append(
            {
                "atom_id": atom_id,
                "element": element,
                "layer_index": oxide_layer_index_by_atom.get(atom_id),
                "global_neighbor_count": global_coordination.get("neighbor_count"),
                "oxide_internal_neighbor_count": len(internal),
                "oxide_internal_unique_neighbor_count": len(set(internal_neighbor_ids)),
                "oxide_internal_neighbor_ids": sorted(set(internal_neighbor_ids)),
                "oxide_internal_neighbor_elements": sorted(
                    value for value in internal_neighbor_elements if value
                ),
                "semiconductor_boundary_neighbor_count": len(cross_boundary),
                "semiconductor_boundary_neighbor_ids": sorted(
                    {
                        str(row.get("neighbor_id"))
                        for row in cross_boundary
                        if row.get("neighbor_id")
                    }
                ),
                "oxygen_cation_neighbor_count": (
                    sum(
                        1
                        for value in internal_neighbor_elements
                        if value in oxide_cation_elements
                    )
                    if element == "O"
                    else None
                ),
                "cation_oxygen_neighbor_count": (
                    sum(1 for value in internal_neighbor_elements if value == "O")
                    if element in oxide_cation_elements
                    else None
                ),
                "nearest_relevant_neighbor_distance_angstrom": (
                    _round(min(relevant_distances)) if relevant_distances else None
                ),
                "isolated_from_oxide_and_semiconductor_boundary": not bool(
                    internal or cross_boundary
                ),
            }
        )

    short_contacts: list[dict[str, Any]] = [
        dict(row) for row in boundary_pairs if row.get("short_contact_review") is True
    ]
    for row in oxide_internal_pairs:
        distance = _optional_float(row.get("distance_angstrom"))
        threshold = _optional_float(row.get("neighbor_threshold_angstrom"))
        threshold_fraction = (
            distance / threshold
            if distance is not None and threshold is not None and threshold > 0
            else None
        )
        if (
            threshold_fraction is None
            or threshold_fraction >= OXIDE_INTERFACE_SHORT_CONTACT_THRESHOLD_FRACTION
        ):
            continue
        short_contacts.append(
            {
                "pair_scope": "oxide_internal",
                "atom1": row.get("atom1"),
                "element1": row.get("element1"),
                "atom2": row.get("atom2"),
                "element2": row.get("element2"),
                "pair_type": row.get("pair_type"),
                "distance_angstrom": distance,
                "neighbor_threshold_angstrom": threshold,
                "distance_to_threshold_fraction": _round(threshold_fraction),
                "within_neighbor_cutoff": True,
                "short_contact_review": True,
                "image_offset_atom2": row.get("image_offset_atom2") or [0, 0, 0],
            }
        )

    isolated_oxide_atom_ids = [
        str(row.get("atom_id"))
        for row in oxide_atom_rows
        if row.get("isolated_from_oxide_and_semiconductor_boundary") is True
    ]
    oxygen_rows = [row for row in oxide_atom_rows if row.get("element") == "O"]
    cation_rows = [
        row for row in oxide_atom_rows if row.get("element") in oxide_cation_elements
    ]
    oxygen_with_cation_neighbor_count = sum(
        1 for row in oxygen_rows if int(row.get("oxygen_cation_neighbor_count") or 0) > 0
    )
    cations_with_oxygen_neighbor_count = sum(
        1 for row in cation_rows if int(row.get("cation_oxygen_neighbor_count") or 0) > 0
    )
    normality_reason_codes: list[str] = []
    if not atom_binding_complete:
        normality_reason_codes.append("oxide_interface_geometry_binding_review")
    elif not boundary_pairs:
        normality_reason_codes.append("oxide_interface_boundary_pair_evidence_missing")
    if spacing_binding_reviews:
        normality_reason_codes.append("oxide_interface_spacing_binding_review")
    if spacing_mismatches:
        normality_reason_codes.append("oxide_interface_declared_spacing_mismatch")
    if boundary_pairs and not boundary_neighbor_pairs:
        normality_reason_codes.append("oxide_interface_boundary_disconnected")
    if short_contacts:
        normality_reason_codes.append("oxide_interface_short_contact_review")
    if isolated_oxide_atom_ids:
        normality_reason_codes.append("oxide_interface_isolated_oxide_atoms")

    if "oxide_interface_geometry_binding_review" in normality_reason_codes:
        status = "geometry_binding_review"
        next_action = "repair_interface_layer_and_atom_binding_before_geometry_review"
    elif "oxide_interface_boundary_pair_evidence_missing" in normality_reason_codes:
        status = "boundary_pair_evidence_missing"
        next_action = "regenerate_boundary_pair_evidence_before_geometry_review"
    elif "oxide_interface_spacing_binding_review" in normality_reason_codes:
        status = "interface_spacing_binding_review"
        next_action = "repair_gate_stack_interface_spacing_binding_before_geometry_review"
    elif "oxide_interface_declared_spacing_mismatch" in normality_reason_codes:
        status = "declared_interface_spacing_mismatch"
        next_action = "align_declared_and_measured_gate_stack_interface_spacing"
    elif "oxide_interface_boundary_disconnected" in normality_reason_codes:
        status = "boundary_disconnected_review"
        next_action = "review_semiconductor_oxide_boundary_gap_before_relaxation"
    elif "oxide_interface_short_contact_review" in normality_reason_codes:
        status = "short_contact_review"
        next_action = "review_short_oxide_interface_contacts_before_relaxation"
    elif "oxide_interface_isolated_oxide_atoms" in normality_reason_codes:
        status = "oxide_connectivity_review"
        next_action = "review_isolated_oxide_atoms_before_relaxation"
    elif metadata.get("requires_geometry_relaxation") or metadata.get("unrelaxed_interface"):
        status = "connected_pre_relaxation_scaffold"
        next_action = "review_connected_interface_geometry_then_plan_relaxation"
    else:
        status = "connected_geometry_preflight"
        next_action = "continue_with_reviewed_interface_geometry_workflow"

    warnings: list[str] = []
    if not atom_binding_complete:
        warnings.append(
            "Semiconductor/oxide boundary layers or atom IDs could not be bound to the current structure."
        )
    if atom_binding_complete and not boundary_pairs:
        warnings.append("No semiconductor/oxide boundary pair distances could be computed.")
    elif boundary_pairs and not boundary_neighbor_pairs:
        warnings.append(
            "No semiconductor/oxide boundary atom pair falls within the covalent-radius neighbor cutoff."
        )
    if spacing_binding_reviews:
        warnings.append(
            "At least one gate-stack interface spacing could not be bound to one adjacent layer transition or has an invalid declared value."
        )
    if spacing_mismatches:
        warnings.append(
            "At least one declared gate-stack interface gap differs from the measured adjacent layer-center spacing."
        )
    if short_contacts:
        warnings.append(
            "At least one oxide or semiconductor/oxide pair is below the conservative normalized short-contact review threshold."
        )
    if isolated_oxide_atom_ids:
        warnings.append(
            "At least one oxide atom has no oxide-internal or semiconductor-boundary neighbor within the current cutoff."
        )

    geometry_preflight_ready = not normality_reason_codes
    geometry_relaxed = metadata.get("geometry_relaxed") is True and not bool(
        metadata.get("requires_geometry_relaxation") or metadata.get("unrelaxed_interface")
    )
    boundary_pair_distances = [
        float(row["distance_angstrom"])
        for row in boundary_pairs
        if row.get("distance_angstrom") is not None
    ]
    boundary_neighbor_distances = [
        float(row["distance_angstrom"])
        for row in boundary_neighbor_pairs
        if row.get("distance_angstrom") is not None
    ]
    threshold_fractions = [
        float(row["distance_to_threshold_fraction"])
        for row in boundary_pairs
        if row.get("distance_to_threshold_fraction") is not None
    ]
    short_contact_scopes = Counter(
        str(row.get("pair_scope") or "unknown") for row in short_contacts
    )
    return {
        "available": True,
        "schema_version": 1,
        "model": "semiconductor_oxide_interface_geometry_preflight",
        "status": status,
        "quality": "complete" if geometry_preflight_ready else "review_required",
        "interface": profile.get("interface") or metadata.get("interface"),
        "axis": profile.get("axis") or (layer_profile or {}).get("axis"),
        "semiconductor_material": semiconductor_material,
        "oxide_material": oxide_material,
        "semiconductor_oxide_boundary": boundary,
        "semiconductor_boundary_layer_index": (boundary or {}).get(
            "semiconductor_layer_index"
        ),
        "oxide_boundary_layer_index": (boundary or {}).get("oxide_layer_index"),
        "semiconductor_boundary_atom_count": len(semiconductor_boundary_atom_ids),
        "semiconductor_boundary_atom_ids": semiconductor_boundary_atom_ids,
        "oxide_boundary_atom_count": len(oxide_boundary_atom_ids),
        "oxide_boundary_atom_ids": oxide_boundary_atom_ids,
        "oxide_atom_count": len(oxide_atom_ids),
        "oxide_atom_ids": oxide_atom_ids,
        "oxide_cation_elements": oxide_cation_elements,
        "interface_spacing_definition": "adjacent_boundary_layer_center_distance",
        "interface_spacing_tolerance_angstrom": (
            interface_spacings[0].get("tolerance_angstrom")
            if interface_spacings
            else OXIDE_INTERFACE_SPACING_TOLERANCE_ANGSTROM
        ),
        "interface_spacing_count": len(interface_spacings),
        "interface_spacing_declared_count": sum(
            1 for row in interface_spacings if row.get("declared_gap_angstrom") is not None
        ),
        "interface_spacing_binding_review_count": len(spacing_binding_reviews),
        "interface_spacing_mismatch_count": len(spacing_mismatches),
        "interface_spacing_all_declared": bool(
            interface_spacings
            and all(
                row.get("declared_gap_angstrom") is not None
                for row in interface_spacings
            )
        ),
        "interface_spacing_declared_values_match": bool(
            any(
                row.get("declared_gap_angstrom") is not None
                for row in interface_spacings
            )
            and not spacing_binding_reviews
            and not spacing_mismatches
        ),
        "interface_spacings": interface_spacings,
        "atom_binding_complete": atom_binding_complete,
        "missing_bound_atom_ids": missing_atom_ids,
        "boundary_candidate_pair_count": len(boundary_pairs),
        "boundary_neighbor_pair_count": len(boundary_neighbor_pairs),
        "boundary_connected_within_neighbor_cutoff": bool(boundary_neighbor_pairs),
        "boundary_pair_type_counts": dict(
            sorted(Counter(str(row.get("pair_type")) for row in boundary_pairs).items())
        ),
        "boundary_neighbor_pair_type_counts": dict(
            sorted(
                Counter(
                    str(row.get("pair_type")) for row in boundary_neighbor_pairs
                ).items()
            )
        ),
        "boundary_pair_distance_stats_angstrom": _stats_with_count(
            boundary_pair_distances
        ),
        "boundary_neighbor_distance_stats_angstrom": _stats_with_count(
            boundary_neighbor_distances
        ),
        "boundary_distance_to_threshold_fraction_stats": _stats_with_count(
            threshold_fractions
        ),
        "neighbor_cutoff_rule": "distance <= 1.25 * covalent_radius_sum",
        "short_contact_review_threshold_fraction": (
            OXIDE_INTERFACE_SHORT_CONTACT_THRESHOLD_FRACTION
        ),
        "short_contact_count": len(short_contacts),
        "short_contact_scope_counts": dict(sorted(short_contact_scopes.items())),
        "oxide_internal_neighbor_pair_count": len(oxide_internal_pairs),
        "oxide_internal_pair_type_counts": dict(
            sorted(
                Counter(
                    str(row.get("pair_type")) for row in oxide_internal_pairs
                ).items()
            )
        ),
        "oxide_atom_neighbor_coverage_count": (
            len(oxide_atom_rows) - len(isolated_oxide_atom_ids)
        ),
        "isolated_oxide_atom_count": len(isolated_oxide_atom_ids),
        "isolated_oxide_atom_ids": isolated_oxide_atom_ids,
        "oxide_oxygen_atom_count": len(oxygen_rows),
        "oxide_oxygen_with_cation_neighbor_count": oxygen_with_cation_neighbor_count,
        "oxide_cation_atom_count": len(cation_rows),
        "oxide_cations_with_oxygen_neighbor_count": cations_with_oxygen_neighbor_count,
        "pre_relaxation_scaffold": bool(
            metadata.get("requires_geometry_relaxation")
            or metadata.get("unrelaxed_interface")
            or metadata.get("pre_relaxation_scaffold")
        ),
        "geometry_relaxed": metadata.get("geometry_relaxed"),
        "geometry_relaxation_verified": geometry_relaxed,
        "visualization_ready": bool(atom_binding_complete and boundary_pairs),
        "geometry_preflight_ready": geometry_preflight_ready,
        "calculation_geometry_ready": bool(
            geometry_preflight_ready and geometry_relaxed
        ),
        "normality_reason_codes": normality_reason_codes,
        "calculation_blocking_reasons": [
            f"semiconductor:{reason}" for reason in normality_reason_codes
        ],
        "next_action": next_action,
        "warning_count": len(warnings),
        "warnings": warnings,
        "notes": [
            "Boundary distances use periodic minimum-image geometry and the current covalent-radius neighbor cutoff.",
            "Coordination and short-contact fields are conservative structural preflight evidence, not proof of oxide phase, bonding order, relaxation, or electronic quality.",
        ],
        "boundary_pairs": boundary_pairs[:MAX_HEALTH_DETAIL_ROWS],
        "short_contacts": short_contacts[:MAX_HEALTH_DETAIL_ROWS],
        "oxide_atoms": oxide_atom_rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _semiconductor_oxide_interface_health_summary(
    metadata: dict[str, Any],
    layer_profile: dict[str, Any] | None,
    interface_profile: dict[str, Any] | None,
    interface_quality: dict[str, Any] | None,
    defect_summary: dict[str, Any] | None,
    geometry_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _semiconductor_oxide_interface_applicable(metadata):
        return None

    oxide_material = _semiconductor_oxide_material(metadata)
    semiconductor_material = str(
        metadata.get("semiconductor_channel_material") or metadata.get("substrate") or ""
    ) or None
    profile = interface_profile or {}
    layer_summary = layer_profile or {}
    all_layers = [
        dict(layer)
        for layer in profile.get("layers", []) or []
        if isinstance(layer, dict)
    ]
    oxide_layers = [
        layer
        for layer in all_layers
        if str(layer.get("material_group") or "") == str(oxide_material or "")
    ]
    if not oxide_layers:
        oxide_layers = [
            layer
            for layer in all_layers
            if int((layer.get("element_counts") or {}).get("O") or 0) > 0
            and str(layer.get("material_group") or "") != str(semiconductor_material or "")
        ]

    formula = _material_formula_amounts(oxide_material)
    cation_elements = sorted(
        element
        for element, amount in (formula or {}).items()
        if element != "O" and amount > 0
    )
    oxide_element_counts = _sum_layer_element_counts(oxide_layers)
    if not cation_elements:
        cation_elements = sorted(
            element
            for element, count in oxide_element_counts.items()
            if element != "O" and count > 0
        )
    formula_cation_amount = sum(
        amount
        for element, amount in (formula or {}).items()
        if element != "O" and amount > 0
    )
    expected_oxygen_per_cation = (
        float((formula or {}).get("O", 0.0)) / formula_cation_amount
        if formula_cation_amount > 0 and float((formula or {}).get("O", 0.0)) > 0
        else None
    )
    oxide_cation_count = sum(int(oxide_element_counts.get(element) or 0) for element in cation_elements)
    oxygen_count = int(oxide_element_counts.get("O") or 0)
    profile_complete = bool(
        all_layers
        and int(profile.get("layer_count") or len(all_layers)) == len(all_layers)
        and int(layer_summary.get("layer_count") or len(all_layers)) == len(all_layers)
    )
    stoichiometry_status, expected_oxygen_count, oxygen_delta, oxygen_deficit, oxygen_excess = (
        _oxide_stoichiometry_status(
            oxygen_count,
            oxide_cation_count,
            expected_oxygen_per_cation if profile_complete and oxide_layers else None,
        )
    )
    oxygen_to_cation_ratio = (
        _round(oxygen_count / oxide_cation_count)
        if oxide_cation_count > 0
        else None
    )

    oxide_layer_rows: list[dict[str, Any]] = []
    for layer in oxide_layers:
        counts = {
            str(element): int(count)
            for element, count in (layer.get("element_counts") or {}).items()
        }
        layer_cation_count = sum(int(counts.get(element) or 0) for element in cation_elements)
        layer_oxygen_count = int(counts.get("O") or 0)
        layer_status, layer_expected, layer_delta, layer_deficit, layer_excess = (
            _oxide_stoichiometry_status(
                layer_oxygen_count,
                layer_cation_count,
                expected_oxygen_per_cation if profile_complete else None,
            )
        )
        oxide_layer_rows.append(
            {
                "layer_index": layer.get("layer_index"),
                "fractional_center": layer.get("fractional_center"),
                "axis_coordinate_angstrom": layer.get("axis_coordinate_angstrom"),
                "material_group": layer.get("material_group"),
                "atom_count": layer.get("atom_count"),
                "element_counts": dict(sorted(counts.items())),
                "cation_count": layer_cation_count,
                "oxygen_count": layer_oxygen_count,
                "oxygen_to_cation_ratio": (
                    _round(layer_oxygen_count / layer_cation_count)
                    if layer_cation_count > 0
                    else None
                ),
                "expected_oxygen_count": layer_expected,
                "oxygen_delta_count": layer_delta,
                "oxygen_deficit_count": layer_deficit,
                "oxygen_excess_count": layer_excess,
                "stoichiometry_status": layer_status,
                "atom_ids": layer.get("atom_ids") or [],
            }
        )

    boundary = _semiconductor_oxide_interface_boundary(
        profile,
        semiconductor_material=semiconductor_material,
        oxide_material=oxide_material,
    )
    axis = str(profile.get("axis") or layer_summary.get("axis") or metadata.get("interface_axis") or "")
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    axis_length = _optional_float(layer_summary.get("axis_length_angstrom"))
    fractional_tolerance = _optional_float(layer_summary.get("tolerance_fractional")) or 1e-4
    coordinate_tolerance = max((axis_length or 0.0) * fractional_tolerance * 2.0, 0.05)
    interface_proximity_threshold = max(
        _optional_float(
            metadata.get("semiconductor_oxide_interface_gap_angstrom")
            or metadata.get("interface_gap_angstrom")
        )
        or 0.0,
        2.0,
    )
    oxygen_vacancies = [
        dict(defect)
        for defect in (defect_summary or {}).get("defects", []) or []
        if isinstance(defect, dict)
        and str(defect.get("type") or "").lower() == "vacancy"
        and str(defect.get("site_element") or "") == "O"
    ]
    vacancy_locations: list[dict[str, Any]] = []
    for vacancy in oxygen_vacancies:
        fractional = _coerce_fractional(vacancy.get("fractional"))
        axis_fractional = fractional[axis_index] if fractional is not None and axis_index is not None else None
        axis_coordinate = (
            _round(axis_fractional * axis_length)
            if axis_fractional is not None and axis_length is not None
            else None
        )
        nearest_layer = None
        nearest_delta = None
        if axis_fractional is not None:
            candidates = [
                (abs(axis_fractional - float(layer.get("fractional_center"))), layer)
                for layer in all_layers
                if _optional_float(layer.get("fractional_center")) is not None
            ]
            if candidates:
                fractional_delta, nearest_layer = min(
                    candidates,
                    key=lambda item: (item[0], int(item[1].get("layer_index") or 0)),
                )
                nearest_delta = (
                    _round(fractional_delta * axis_length)
                    if axis_length is not None
                    else None
                )
        nearest_material = str((nearest_layer or {}).get("material_group") or "") or None
        exact_layer_match = nearest_delta is not None and nearest_delta <= coordinate_tolerance
        boundary_coordinate = _optional_float((boundary or {}).get("axis_coordinate_angstrom"))
        boundary_distance = (
            _round(abs(axis_coordinate - boundary_coordinate))
            if axis_coordinate is not None and boundary_coordinate is not None
            else None
        )
        if exact_layer_match and nearest_material == oxide_material:
            region = "oxide"
        elif exact_layer_match and nearest_material == semiconductor_material:
            region = "semiconductor"
        elif exact_layer_match and nearest_material:
            region = "other_stack_material"
        elif boundary_distance is not None and boundary_distance <= interface_proximity_threshold:
            region = "interface_boundary"
        else:
            region = "unknown"
        vacancy_locations.append(
            {
                "site_id": vacancy.get("site_id"),
                "site_element": vacancy.get("site_element"),
                "fractional": list(fractional) if fractional is not None else None,
                "axis_fractional": _round(axis_fractional) if axis_fractional is not None else None,
                "axis_coordinate_angstrom": axis_coordinate,
                "region": region,
                "nearest_layer_index": (nearest_layer or {}).get("layer_index"),
                "nearest_layer_material": nearest_material,
                "nearest_layer_delta_angstrom": nearest_delta,
                "distance_to_semiconductor_oxide_boundary_angstrom": boundary_distance,
                "interface_proximal": (
                    boundary_distance <= interface_proximity_threshold
                    if boundary_distance is not None
                    else None
                ),
                "position_verified": bool(exact_layer_match and region != "unknown"),
                "auto_selected_site": bool(vacancy.get("auto_selected_site")),
                "source": vacancy.get("source"),
            }
        )

    recorded_vacancy_count = len(oxygen_vacancies)
    all_vacancy_locations_verified = all(
        bool(location.get("position_verified"))
        for location in vacancy_locations
    )
    if stoichiometry_status == "not_evaluated":
        deficit_binding_status = "not_evaluated"
        deficit_explained = None
    elif oxygen_deficit and oxygen_deficit > 0:
        deficit_explained = abs(float(oxygen_deficit) - recorded_vacancy_count) <= 1e-6
        deficit_binding_status = (
            "matched_recorded_oxygen_vacancies"
            if deficit_explained
            else "unexplained_oxygen_deficit"
            if recorded_vacancy_count == 0
            else "oxygen_deficit_vacancy_count_mismatch"
        )
    elif recorded_vacancy_count:
        deficit_explained = False
        deficit_binding_status = "recorded_oxygen_vacancy_without_matching_deficit"
    else:
        deficit_explained = True
        deficit_binding_status = "none_detected"

    sequence_matches = (interface_quality or {}).get("period_sequence_complete")
    declared_materials_present = (interface_quality or {}).get("declared_materials_present")
    sequence_ok = sequence_matches is not False and declared_materials_present is not False
    unrelaxed_scaffold = bool(
        metadata.get("interface_scaffold")
        or metadata.get("pre_relaxation_scaffold")
        or metadata.get("unrelaxed_interface")
        or metadata.get("requires_geometry_relaxation")
    )
    geometry_relaxed = metadata.get("geometry_relaxed")
    geometry_relaxation_verified = geometry_relaxed is True and not unrelaxed_scaffold
    geometry = geometry_summary or {}
    geometry_reason_codes = [
        str(reason)
        for reason in geometry.get("normality_reason_codes", []) or []
        if reason
    ]
    unexplained_stoichiometry = bool(
        stoichiometry_status in {"oxygen_excess", "not_evaluated"}
        or deficit_binding_status
        in {
            "unexplained_oxygen_deficit",
            "oxygen_deficit_vacancy_count_mismatch",
            "recorded_oxygen_vacancy_without_matching_deficit",
        }
    )
    normality_reason_codes: list[str] = list(geometry_reason_codes)
    if unexplained_stoichiometry or not sequence_ok:
        normality_reason_codes.append("oxide_interface_stoichiometry_review")
    if recorded_vacancy_count and not all_vacancy_locations_verified:
        normality_reason_codes.append("oxide_interface_defect_location_unverified")
    if recorded_vacancy_count:
        normality_reason_codes.append("oxide_interface_recorded_oxygen_vacancy")
    if unrelaxed_scaffold:
        normality_reason_codes.append("oxide_interface_unrelaxed_scaffold")
    elif not geometry_relaxation_verified:
        normality_reason_codes.append("oxide_interface_geometry_relaxation_unverified")

    if geometry.get("quality") == "review_required":
        status = "geometry_review"
        quality = "review_required"
        next_action = geometry.get("next_action") or "review_semiconductor_oxide_interface_geometry"
    elif unexplained_stoichiometry or not sequence_ok:
        status = "stoichiometry_review"
        quality = "review_required"
        next_action = "reconcile_oxide_layer_stoichiometry_and_defect_metadata_before_relaxation"
    elif recorded_vacancy_count and not all_vacancy_locations_verified:
        status = "oxygen_vacancy_location_unverified"
        quality = "review_required"
        next_action = "verify_oxygen_vacancy_layer_and_interface_distance_before_relaxation"
    elif recorded_vacancy_count:
        status = "recorded_oxygen_vacancy_review"
        quality = "complete_with_recorded_defect"
        next_action = "review_oxygen_vacancy_site_and_supercell_before_relaxation"
    elif unrelaxed_scaffold:
        status = "pre_relaxation_review"
        quality = "complete"
        next_action = "review_or_relax_semiconductor_oxide_interface_before_quantitative_use"
    elif not geometry_relaxation_verified:
        status = "geometry_relaxation_unverified"
        quality = "complete"
        next_action = "verify_geometry_relaxation_before_quantitative_interface_use"
    else:
        status = "ready_for_available_preflight"
        quality = "complete"
        next_action = "continue_with_reviewed_semiconductor_oxide_interface_workflow"

    warnings: list[str] = []
    warnings.extend(str(item) for item in geometry.get("warnings", []) or [])
    if not profile_complete:
        warnings.append("Layer-profile rows are incomplete; oxide stoichiometry was not accepted as complete.")
    if not oxide_layers:
        warnings.append("No oxide layers were identified from the current interface material markers.")
    if expected_oxygen_per_cation is None:
        warnings.append("Oxide formula could not be parsed for an oxygen-to-cation stoichiometry check.")
    if not sequence_ok:
        warnings.append("Semiconductor/oxide material sequence is incomplete or differs from metadata.")
    if deficit_binding_status in {
        "unexplained_oxygen_deficit",
        "oxygen_deficit_vacancy_count_mismatch",
        "recorded_oxygen_vacancy_without_matching_deficit",
    }:
        warnings.append("Oxide oxygen deficit does not match the recorded oxygen-vacancy metadata.")
    if stoichiometry_status == "oxygen_excess":
        warnings.append("Oxide layer profile contains oxygen in excess of the parsed material formula.")
    if recorded_vacancy_count:
        warnings.append(
            "Recorded oxygen-vacancy metadata requires site, finite-size, charge-state, and relaxation review."
        )
    if recorded_vacancy_count and not all_vacancy_locations_verified:
        warnings.append("At least one recorded oxygen vacancy could not be bound to a current stack layer.")
    if unrelaxed_scaffold:
        warnings.append(
            "Semiconductor/oxide interface is a pre-relaxation scaffold, not a verified relaxed or amorphous interface."
        )
    elif not geometry_relaxation_verified:
        warnings.append("Geometry-relaxation evidence is not recorded for this semiconductor/oxide interface.")

    calculation_blocking_reasons = [
        f"semiconductor:{reason}"
        for reason in normality_reason_codes
    ]
    visual_preflight_ready = bool(
        profile_complete
        and oxide_layers
        and sequence_ok
        and not unexplained_stoichiometry
        and all_vacancy_locations_verified
        and geometry.get("geometry_preflight_ready") is not False
    )
    calculation_ready = bool(
        visual_preflight_ready
        and geometry_relaxation_verified
        and geometry.get("calculation_geometry_ready") is not False
        and not recorded_vacancy_count
        and stoichiometry_status == "matched"
    )
    region_counts = Counter(str(location.get("region") or "unknown") for location in vacancy_locations)
    return {
        "available": True,
        "schema_version": 2,
        "model": "semiconductor_oxide_interface_health_preflight",
        "status": status,
        "quality": quality,
        "interface": profile.get("interface") or metadata.get("interface"),
        "axis": axis or None,
        "axis_source": profile.get("axis_source") or layer_summary.get("axis_source"),
        "axis_length_angstrom": axis_length,
        "semiconductor_material": semiconductor_material,
        "oxide_material": oxide_material,
        "metal_gate_present": bool(metadata.get("metal_gate_stack") or metadata.get("gate_material")),
        "material_sequence": (interface_quality or {}).get("material_sequence") or [],
        "expected_material_sequence": (interface_quality or {}).get("expected_material_sequence") or [],
        "sequence_matches_expected": sequence_matches,
        "declared_materials_present": declared_materials_present,
        "layer_profile_complete": profile_complete,
        "oxide_layer_count": len(oxide_layer_rows),
        "oxide_layer_indices": [row.get("layer_index") for row in oxide_layer_rows],
        "oxide_atom_count": sum(oxide_element_counts.values()),
        "oxide_element_counts": dict(sorted(oxide_element_counts.items())),
        "oxide_cation_elements": cation_elements,
        "oxide_cation_count": oxide_cation_count,
        "oxygen_count": oxygen_count,
        "oxygen_to_cation_ratio": oxygen_to_cation_ratio,
        "expected_oxygen_per_cation_ratio": (
            _round(expected_oxygen_per_cation)
            if expected_oxygen_per_cation is not None
            else None
        ),
        "expected_oxygen_count": expected_oxygen_count,
        "oxygen_delta_count": oxygen_delta,
        "oxygen_deficit_count": oxygen_deficit,
        "oxygen_excess_count": oxygen_excess,
        "stoichiometry_status": stoichiometry_status,
        "oxygen_deficit_binding_status": deficit_binding_status,
        "oxygen_deficit_explained_by_recorded_vacancies": deficit_explained,
        "recorded_oxygen_vacancy_count": recorded_vacancy_count,
        "recorded_oxygen_vacancy_site_ids": [
            str(vacancy.get("site_id"))
            for vacancy in oxygen_vacancies
            if vacancy.get("site_id")
        ],
        "all_recorded_oxygen_vacancy_locations_verified": all_vacancy_locations_verified,
        "oxygen_vacancy_region_counts": dict(sorted(region_counts.items())),
        "oxygen_vacancy_interface_proximal_count": sum(
            1 for location in vacancy_locations if location.get("interface_proximal") is True
        ),
        "interface_proximity_threshold_angstrom": _round(interface_proximity_threshold),
        "semiconductor_oxide_boundary": boundary,
        "geometry_preflight_status": geometry.get("status"),
        "geometry_preflight_quality": geometry.get("quality"),
        "geometry_preflight_ready": geometry.get("geometry_preflight_ready"),
        "geometry_visualization_ready": geometry.get("visualization_ready"),
        "geometry_boundary_neighbor_pair_count": geometry.get(
            "boundary_neighbor_pair_count"
        ),
        "geometry_interface_spacing_count": geometry.get("interface_spacing_count"),
        "geometry_interface_spacing_mismatch_count": geometry.get(
            "interface_spacing_mismatch_count"
        ),
        "geometry_interface_spacing_declared_values_match": geometry.get(
            "interface_spacing_declared_values_match"
        ),
        "geometry_short_contact_count": geometry.get("short_contact_count"),
        "geometry_isolated_oxide_atom_count": geometry.get(
            "isolated_oxide_atom_count"
        ),
        "pre_relaxation_scaffold": unrelaxed_scaffold,
        "requires_geometry_relaxation": bool(metadata.get("requires_geometry_relaxation") or unrelaxed_scaffold),
        "geometry_relaxed": geometry_relaxed,
        "geometry_relaxation_verified": geometry_relaxation_verified,
        "visual_preflight_ready": visual_preflight_ready,
        "calculation_ready": calculation_ready,
        "normality_reason_codes": normality_reason_codes,
        "calculation_blocking_reasons": calculation_blocking_reasons,
        "next_action": next_action,
        "warning_count": len(warnings),
        "warnings": warnings,
        "notes": [
            "Stoichiometry is derived from deterministic layer markers and the parsed oxide formula.",
            "This preflight does not prove amorphous topology, relaxed interface chemistry, defect charge state, or calculation readiness.",
        ],
        "oxide_layers": oxide_layer_rows[:MAX_HEALTH_DETAIL_ROWS],
        "oxygen_vacancy_locations": vacancy_locations[:MAX_HEALTH_DETAIL_ROWS],
    }


def _segment_cation_fractions_by_material(segments: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_material: dict[str, Counter[str]] = {}
    for segment in segments:
        material = str(segment.get("material") or segment.get("material_marker") or "")
        if not material:
            continue
        counts = by_material.setdefault(material, Counter())
        for element, count in (segment.get("cation_counts") or {}).items():
            try:
                counts[str(element)] += int(count)
            except (TypeError, ValueError):
                continue
    result: dict[str, dict[str, float]] = {}
    for material, counts in sorted(by_material.items()):
        total = sum(counts.values())
        if total <= 0:
            continue
        result[material] = {
            element: _round(count / total)
            for element, count in sorted(counts.items())
        }
    return result


def _material_label_elements(material: str) -> set[str]:
    return {
        symbol
        for symbol in re.findall(r"[A-Z][a-z]?", str(material))
        if symbol in COVALENT_RADII_ANGSTROM
    }


def _quantum_well_segment_role(material: str | None, well_material: str | None) -> str:
    if material and well_material and material == well_material:
        return "well"
    return "barrier"


def _layer_segment_boundaries(
    segment_layers: list[dict[str, Any]],
    all_layers: list[dict[str, Any]],
    axis_length: float,
) -> dict[str, Any]:
    sorted_layers = sorted(all_layers, key=lambda item: int(item.get("layer_index") or 0))
    layer_indices = [int(layer["layer_index"]) for layer in sorted_layers]
    position_by_index = {layer_index: index for index, layer_index in enumerate(layer_indices)}
    first_index = int(segment_layers[0]["layer_index"])
    last_index = int(segment_layers[-1]["layer_index"])
    first_position = position_by_index[first_index]
    last_position = position_by_index[last_index]
    first_center = float(segment_layers[0].get("axis_coordinate_angstrom") or 0.0)
    last_center = float(segment_layers[-1].get("axis_coordinate_angstrom") or 0.0)

    if first_position == 0:
        previous_center = float(sorted_layers[-1].get("axis_coordinate_angstrom") or 0.0) - axis_length
    else:
        previous_center = float(sorted_layers[first_position - 1].get("axis_coordinate_angstrom") or 0.0)
    if last_position == len(sorted_layers) - 1:
        next_center = float(sorted_layers[0].get("axis_coordinate_angstrom") or 0.0) + axis_length
    else:
        next_center = float(sorted_layers[last_position + 1].get("axis_coordinate_angstrom") or 0.0)

    start = (previous_center + first_center) / 2.0
    end = (last_center + next_center) / 2.0
    if end <= start:
        end += axis_length
    thickness = end - start
    normalized_start = start % axis_length
    normalized_end = end % axis_length
    wraps = bool(start < 0 or end > axis_length or normalized_end < normalized_start)
    return {
        "axis_start_angstrom": _round(start),
        "axis_end_angstrom": _round(end),
        "axis_start_normalized_angstrom": _round(normalized_start),
        "axis_end_normalized_angstrom": _round(normalized_end),
        "thickness_angstrom": _round(thickness),
        "fractional_start": _round(start / axis_length),
        "fractional_end": _round(end / axis_length),
        "fractional_start_normalized": _round(normalized_start / axis_length),
        "fractional_end_normalized": _round(normalized_end / axis_length),
        "wraps_periodic_boundary": wraps,
    }


def _element_signature(element_counts: dict[str, Any]) -> str:
    return ";".join(
        f"{element}:{element_counts[element]}"
        for element in sorted(element_counts)
    )


def _applied_strain_summary(metadata: dict[str, Any]) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("applied_strain", []) or []
        if isinstance(item, dict)
    ]
    if not entries:
        return None
    percents = [
        abs(float(item.get("percent")))
        for item in entries
        if item.get("percent") is not None
    ]
    max_abs = max(percents, default=0.0)
    return {
        "available": True,
        "entry_count": len(entries),
        "entries": entries[:MAX_HEALTH_DETAIL_ROWS],
        "latest": entries[-1],
        "max_abs_strain_percent": _round(max_abs),
        "strain_warning": max_abs > 5.0,
    }


def _material_reference_lattice(metadata: dict[str, Any], material: str | None) -> float | None:
    if not material:
        return None
    key = re.sub(r"[^a-z0-9]+", "", material.lower()) + "_reference_lattice_angstrom"
    value = metadata.get(key)
    if value is None:
        return None
    return _optional_float(value)


def _has_material_electronic_metadata(metadata: dict[str, Any], materials: list[str]) -> bool:
    available_material_count = 0
    for material in materials:
        if (
            _material_electronic_property(metadata, material, "electron_affinity_ev") is not None
            or _material_electronic_property(metadata, material, "band_gap_ev") is not None
        ):
            available_material_count += 1
    return available_material_count >= 2


def _material_electronic_properties(metadata: dict[str, Any], material: str | None) -> dict[str, float | None]:
    return {
        "electron_affinity_ev": _material_electronic_property(metadata, material, "electron_affinity_ev"),
        "band_gap_ev": _material_electronic_property(metadata, material, "band_gap_ev"),
    }


def _material_electronic_property(metadata: dict[str, Any], material: str | None, property_name: str) -> float | None:
    direct = _lookup_material_electronic_property(metadata, material, property_name)
    if direct is not None:
        return direct
    return _interpolated_material_electronic_property(metadata, material, property_name)


def _lookup_material_electronic_property(metadata: dict[str, Any], material: str | None, property_name: str) -> float | None:
    if not material:
        return None
    grouped = metadata.get("material_electronic_properties")
    if isinstance(grouped, dict):
        candidates = [
            material,
            material.lower(),
            re.sub(r"[^a-z0-9]+", "", material.lower()),
        ]
        for candidate in candidates:
            entry = grouped.get(candidate)
            if isinstance(entry, dict):
                value = entry.get(property_name)
                if value is not None:
                    return _optional_float(value)
    key = re.sub(r"[^a-z0-9]+", "", material.lower()) + "_" + property_name
    value = _optional_float(metadata.get(key))
    if value is not None:
        return value
    return _reference_material_electronic_property(material, property_name)


def _reference_material_electronic_property(material: str | None, property_name: str) -> float | None:
    if not material:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", material.lower())
    for key, values in SEMICONDUCTOR_REFERENCE_ELECTRONIC_PROPERTIES.items():
        if normalized == re.sub(r"[^a-z0-9]+", "", key.lower()):
            return _optional_float(values.get(property_name))
    return None


def _interpolated_material_electronic_property(metadata: dict[str, Any], material: str | None, property_name: str) -> float | None:
    counts = _material_formula_amounts(material)
    if not counts:
        return None
    endmembers = _alloy_endmember_weights(counts)
    if not endmembers:
        return None
    weighted = 0.0
    for endmember, weight in endmembers:
        value = _lookup_material_electronic_property(metadata, endmember, property_name)
        if value is None:
            return None
        weighted += float(value) * weight
    return _round(weighted)


def _material_formula_amounts(material: str | None) -> dict[str, float] | None:
    if not material:
        return None
    text = str(material).strip()
    if not text:
        return None
    position = 0
    counts: dict[str, float] = {}
    for match in re.finditer(r"([A-Z][a-z]?)(\d*(?:\.\d+)?)", text):
        if match.start() != position:
            return None
        position = match.end()
        element = match.group(1)
        amount_text = match.group(2)
        amount = float(amount_text) if amount_text else 1.0
        counts[element] = counts.get(element, 0.0) + amount
    if position != len(text) or len(counts) < 2:
        return None
    return counts


def _alloy_endmember_weights(counts: dict[str, float]) -> list[tuple[str, float]]:
    elements = set(counts)
    if elements <= GROUP_IV_SEMICONDUCTORS and len(elements) >= 2:
        return _normalize_endmember_weights([(element, counts[element]) for element in sorted(elements)])

    iii_v_cations = sorted(element for element in elements if element in III_V_CATIONS)
    iii_v_anions = sorted(element for element in elements if element in III_V_ANIONS)
    iii_v = _compound_alloy_endmembers(counts, iii_v_cations, iii_v_anions)
    if iii_v:
        return iii_v

    ii_vi_cations = sorted(element for element in elements if element in II_VI_CATIONS)
    ii_vi_anions = sorted(element for element in elements if element in II_VI_ANIONS)
    return _compound_alloy_endmembers(counts, ii_vi_cations, ii_vi_anions)


def _compound_alloy_endmembers(
    counts: dict[str, float],
    cations: list[str],
    anions: list[str],
) -> list[tuple[str, float]]:
    if len(cations) >= 2 and len(anions) == 1:
        anion = anions[0]
        return _normalize_endmember_weights([(f"{cation}{anion}", counts[cation]) for cation in cations])
    if len(cations) == 1 and len(anions) >= 2:
        cation = cations[0]
        return _normalize_endmember_weights([(f"{cation}{anion}", counts[anion]) for anion in anions])
    return []


def _normalize_endmember_weights(entries: list[tuple[str, float]]) -> list[tuple[str, float]]:
    total = sum(float(weight) for _, weight in entries)
    if total <= 0:
        return []
    return [(name, float(weight) / total) for name, weight in entries]


def _nitride_polarization_properties(metadata: dict[str, Any], material: str | None) -> dict[str, float] | None:
    if not material:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", str(material).lower())
    for key, values in III_NITRIDE_POLARIZATION_REFERENCES.items():
        if normalized == re.sub(r"[^a-z0-9]+", "", key.lower()):
            return {name: float(value) for name, value in values.items()}

    endmembers = _alloy_endmember_weights(_material_formula_amounts(material) or {})
    if not endmembers or not all(endmember in III_NITRIDE_POLARIZATION_REFERENCES for endmember, _ in endmembers):
        return None
    properties: dict[str, float] = {}
    keys = set().union(*(III_NITRIDE_POLARIZATION_REFERENCES[endmember].keys() for endmember, _ in endmembers))
    for key in keys:
        value = 0.0
        for endmember, weight in endmembers:
            value += float(III_NITRIDE_POLARIZATION_REFERENCES[endmember][key]) * float(weight)
        properties[key] = _round(value)
    return properties


def _in_plane_strain(in_plane_lattice: float | None, reference_lattice: float | None) -> float | None:
    if in_plane_lattice is None or reference_lattice in {None, 0}:
        return None
    return (float(in_plane_lattice) - float(reference_lattice)) / float(reference_lattice)


def _piezoelectric_polarization(properties: dict[str, Any], strain: float | None) -> float | None:
    if strain is None:
        return None
    e31 = _optional_float(properties.get("e31_c_per_m2"))
    e33 = _optional_float(properties.get("e33_c_per_m2"))
    c13 = _optional_float(properties.get("c13_gpa"))
    c33 = _optional_float(properties.get("c33_gpa"))
    if None in {e31, e33, c13, c33} or c33 == 0:
        return None
    return _round(2.0 * float(strain) * (float(e31) - float(e33) * float(c13) / float(c33)))


def _material_element_fraction(material: str | None, element: str) -> float | None:
    counts = _material_formula_amounts(material)
    if not counts or element not in counts:
        return 0.0 if counts else None
    comparable = [
        value
        for key, value in counts.items()
        if key == element or (element in III_V_CATIONS and key in III_V_CATIONS) or (element in II_VI_CATIONS and key in II_VI_CATIONS)
    ]
    total = sum(float(value) for value in comparable)
    if total <= 0:
        return None
    return float(counts[element]) / total


def _semiconductor_host_and_dopants(
    metadata: dict[str, Any],
    non_passivant_elements: list[str],
    element_counts: Counter[str],
) -> tuple[list[str], list[str]]:
    if not non_passivant_elements:
        return [], []

    component_elements = _semiconductor_non_dopant_component_elements(metadata)
    semiconductor_elements = [
        element for element in non_passivant_elements if element not in component_elements
    ]
    if not semiconductor_elements:
        return [], []

    alloy_elements = _metadata_alloy_elements(metadata)
    if alloy_elements:
        material_elements = _metadata_material_elements(metadata)
        host_set = {
            element
            for element in semiconductor_elements
            if element in alloy_elements or element in material_elements
        }
        if alloy_elements & III_V_CATIONS and set(semiconductor_elements) & III_V_ANIONS:
            host_set.update(element for element in semiconductor_elements if element in III_V_ANIONS)
        if alloy_elements & III_V_ANIONS and set(semiconductor_elements) & III_V_CATIONS:
            host_set.update(element for element in semiconductor_elements if element in III_V_CATIONS)
        host = [element for element in semiconductor_elements if element in host_set]
        dopants = [element for element in semiconductor_elements if element not in host_set]
        return sorted(host), sorted(dopants)

    material_elements = _metadata_material_elements(metadata)
    if material_elements:
        host = [element for element in semiconductor_elements if element in material_elements]
        dopants = [element for element in semiconductor_elements if element not in material_elements]
        return sorted(host), sorted(dopants)

    family = str(metadata.get("structure_family") or "").lower()
    group_iv_elements = [element for element in semiconductor_elements if element in GROUP_IV_SEMICONDUCTORS]
    if "diamond" in family and group_iv_elements:
        host_count = max(element_counts[element] for element in group_iv_elements)
        host = [element for element in group_iv_elements if element_counts[element] == host_count]
        dopants = [element for element in semiconductor_elements if element not in host]
        return sorted(host), sorted(dopants)

    if set(semiconductor_elements) & III_V_CATIONS and set(semiconductor_elements) & III_V_ANIONS:
        host = [
            element
            for element in semiconductor_elements
            if element in III_V_CATIONS or element in III_V_ANIONS
        ]
        return sorted(host), [element for element in semiconductor_elements if element not in host]

    return sorted(semiconductor_elements), []


def _semiconductor_non_dopant_component_elements(metadata: dict[str, Any]) -> set[str]:
    """Return explicit device-stack components that must not be classified as dopants."""

    if not (
        metadata.get("metal_semiconductor_interface")
        or metadata.get("schottky_contact")
        or str(metadata.get("contact_type") or "").lower() in {"schottky", "ohmic"}
    ):
        return set()
    values = [
        metadata.get("metal_contact_material"),
        metadata.get("electrode_material"),
    ]
    return {
        str(value).strip()
        for value in values
        if isinstance(value, str) and re.fullmatch(r"[A-Z][a-z]?", value.strip())
    }


def _metadata_material_elements(metadata: dict[str, Any]) -> set[str]:
    materials = metadata.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    elif not isinstance(materials, list):
        materials = []
    material = metadata.get("material")
    if isinstance(material, str):
        materials = [*materials, material]
    elements: set[str] = set()
    for material in materials:
        for symbol in re.findall(r"[A-Z][a-z]?", str(material)):
            if symbol in COVALENT_RADII_ANGSTROM or symbol in NOMINAL_VALENCE_ELECTRONS:
                elements.add(symbol)
    return elements


def _metadata_alloy_elements(metadata: dict[str, Any]) -> set[str]:
    elements: set[str] = set()
    for entry in metadata.get("applied_alloy", []) or []:
        if not isinstance(entry, dict):
            continue
        for key in ("host_element", "alloy_element"):
            value = entry.get(key)
            if isinstance(value, str) and (value in COVALENT_RADII_ANGSTROM or value in NOMINAL_VALENCE_ELECTRONS):
                elements.add(value)
    return elements


def _composition_summary(
    metadata: dict[str, Any],
    all_element_counts: Counter[str],
    *,
    host_elements: list[str],
    dopant_elements: list[str],
) -> dict[str, Any] | None:
    counts = Counter({element: count for element, count in all_element_counts.items() if count > 0})
    if not counts:
        return None
    total = sum(counts.values())
    passivant_count = sum(count for element, count in counts.items() if element in SURFACE_PASSIVANTS)
    non_passivant_total = total - passivant_count
    alloy_elements = _metadata_alloy_elements(metadata)
    element_rows = []
    for element in sorted(counts):
        count = counts[element]
        non_passivant_fraction = None
        non_passivant_percent = None
        if element not in SURFACE_PASSIVANTS and non_passivant_total:
            non_passivant_fraction = _round(count / non_passivant_total)
            non_passivant_percent = _round(100.0 * count / non_passivant_total)
        element_rows.append(
            {
                "element": element,
                "count": count,
                "atomic_fraction": _round(count / total) if total else None,
                "atomic_percent": _round(100.0 * count / total) if total else None,
                "non_passivant_fraction": non_passivant_fraction,
                "non_passivant_percent": non_passivant_percent,
                "role": _composition_role(element, host_elements, dopant_elements, alloy_elements),
            }
        )
    return {
        "available": True,
        "formula": _format_formula(counts, order=_semiconductor_formula_order(counts)),
        "reduced_formula": _format_formula(_reduced_counts(counts), order=_semiconductor_formula_order(counts)),
        "element_count": len(counts),
        "total_atom_count": total,
        "non_passivant_atom_count": non_passivant_total,
        "passivant_atom_count": passivant_count,
        "element_counts": dict(sorted(counts.items())),
        "host_elements": host_elements,
        "dopant_elements": dopant_elements,
        "elements": element_rows,
    }


def _charge_balance_summary(
    metadata: dict[str, Any],
    all_element_counts: Counter[str],
    *,
    host_elements: list[str],
    dopant_elements: list[str],
    dopant_site_summary: dict[str, Any] | None = None,
    simulation: Any | None = None,
) -> dict[str, Any] | None:
    counts = Counter({element: count for element, count in all_element_counts.items() if count > 0})
    if not counts:
        return None
    alloy_elements = _metadata_alloy_elements(metadata)
    known_elements = {element: count for element, count in counts.items() if element in NOMINAL_VALENCE_ELECTRONS}
    unknown_elements = sorted(element for element in counts if element not in NOMINAL_VALENCE_ELECTRONS)
    total_valence = sum(NOMINAL_VALENCE_ELECTRONS[element] * count for element, count in known_elements.items())
    non_passivant_valence = sum(
        NOMINAL_VALENCE_ELECTRONS[element] * count
        for element, count in known_elements.items()
        if element not in SURFACE_PASSIVANTS
    )
    passivant_valence = total_valence - non_passivant_valence
    non_passivant_count = sum(count for element, count in counts.items() if element not in SURFACE_PASSIVANTS)
    host_reference_valence = _host_reference_valence(host_elements)
    total_dopant_delta = 0
    element_rows = []
    for element in sorted(counts):
        count = counts[element]
        valence = NOMINAL_VALENCE_ELECTRONS.get(element)
        total = valence * count if valence is not None else None
        dopant_delta = None
        role = _composition_role(element, host_elements, dopant_elements, alloy_elements)
        if role == "dopant" and valence is not None and host_reference_valence is not None:
            dopant_delta = _round((valence - host_reference_valence) * count)
            total_dopant_delta += int(round(dopant_delta))
        element_rows.append(
            {
                "element": element,
                "count": count,
                "role": role,
                "nominal_valence_electrons": valence,
                "total_valence_electrons": total,
                "valence_fraction": _round(total / total_valence) if total is not None and total_valence else None,
                "dopant_delta_electrons": dopant_delta,
            }
        )
    average_host_dopant_delta = total_dopant_delta
    average_host_carrier_hint = "neutral_or_intrinsic"
    if total_dopant_delta > 0:
        average_host_carrier_hint = "donor_like_n_type"
    elif total_dopant_delta < 0:
        average_host_carrier_hint = "acceptor_like_p_type"
    carrier_hint = average_host_carrier_hint
    carrier_hint_source = "average_host_reference"
    site_adjusted_delta = None
    if dopant_site_summary:
        raw_site_adjusted_delta = dopant_site_summary.get("site_adjusted_nominal_delta_electrons")
        if raw_site_adjusted_delta is not None:
            site_adjusted_delta = int(round(float(raw_site_adjusted_delta)))
            total_dopant_delta = site_adjusted_delta
            carrier_hint = dopant_site_summary.get("carrier_type_hint") or average_host_carrier_hint
            carrier_hint_source = "dopant_site_summary"
    defect_charge_spin_request = (
        dict(metadata.get("defect_charge_spin_request"))
        if isinstance(metadata.get("defect_charge_spin_request"), dict)
        else {}
    )
    if defect_charge_spin_request:
        carrier_hint = "defect_charge_state_dependent"
        carrier_hint_source = "defect_charge_spin_request"
    charge_state_label = (
        str(defect_charge_spin_request.get("charge_state_label") or "")
        or None
    )
    charge_state_explicit = (
        defect_charge_spin_request.get("charge_state_explicit") is True
    )
    requested_net_charge = _optional_int(
        defect_charge_spin_request.get("requested_net_charge_e")
    )
    reference_spin_multiplicity = _optional_int(
        defect_charge_spin_request.get("reference_spin_multiplicity")
    )
    charge_adjusted_electron_count = (
        total_valence - requested_net_charge
        if charge_state_explicit and requested_net_charge is not None
        else total_valence
    )
    nominal_composition_odd = bool(total_valence % 2)
    odd_electron_warning = bool(charge_adjusted_electron_count % 2)
    charge_spin_binding = (
        diamond_nv_castep_binding_receipt(charge_state_label, simulation)
        if defect_charge_spin_request
        else None
    )
    backend_charge_status = (
        str(defect_charge_spin_request.get("backend_charge_binding_status") or "")
        if defect_charge_spin_request
        else ""
    )
    backend_spin_status = (
        str(defect_charge_spin_request.get("backend_spin_binding_status") or "")
        if defect_charge_spin_request
        else ""
    )
    metadata_declares_bound = bool(
        backend_charge_status == DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS
        and backend_spin_status == DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS
        and defect_charge_spin_request.get("calculation_execution_ready") is True
        and defect_charge_spin_request.get("state_result_computed") is False
    )
    charge_spin_backend_binding_ready = (
        None
        if not defect_charge_spin_request
        else bool(
            charge_state_explicit
            and metadata_declares_bound
            and (charge_spin_binding or {}).get("exact_match") is True
        )
    )
    charge_state_unresolved = bool(
        defect_charge_spin_request and not charge_state_explicit
    )
    spin_charge_review_required = bool(
        (odd_electron_warning and charge_spin_backend_binding_ready is not True)
        or charge_state_unresolved
        or charge_spin_backend_binding_ready is False
    )
    if charge_spin_backend_binding_ready is False:
        recommended_spin_treatment = (
            "bind_reviewed_castep_net_charge_and_spin_settings_before_execution"
        )
        calculation_readiness_impact = (
            "defect_charge_spin_backend_binding_required"
        )
        next_action = (
            "review_defect_charge_state_and_bind_castep_charge_spin_backend"
        )
    elif odd_electron_warning:
        recommended_spin_treatment = (
            "review_spin_polarized_calculation_or_explicit_charge_state"
        )
        calculation_readiness_impact = (
            "review_before_spin_sensitive_or_closed_shell_calculation"
        )
        next_action = (
            "review_spin_polarization_or_charge_state_before_castep_execution"
        )
    else:
        recommended_spin_treatment = (
            "closed_shell_or_non_spin_polarized_default_reasonable"
        )
        calculation_readiness_impact = (
            "no_odd_electron_charge_spin_warning"
        )
        next_action = (
            "charge_balance_preflight_passed_for_nominal_electron_parity"
        )
    return {
        "available": True,
        "model": (
            "defect_charge_state_aware_nominal_valence_electron_heuristic"
            if carrier_hint_source == "defect_charge_spin_request"
            else "site_adjusted_nominal_valence_electron_heuristic"
            if carrier_hint_source == "dopant_site_summary"
            else "nominal_valence_electron_heuristic"
        ),
        "total_atom_count": sum(counts.values()),
        "known_valence_atom_count": sum(known_elements.values()),
        "unknown_valence_elements": unknown_elements,
        "total_valence_electron_count": total_valence,
        "nominal_composition_electron_count_parity": (
            "odd" if nominal_composition_odd else "even"
        ),
        "nominal_composition_odd_electron_warning": nominal_composition_odd,
        "defect_charge_state_label": charge_state_label,
        "defect_charge_state_explicit": charge_state_explicit,
        "defect_charge_state_unresolved": charge_state_unresolved,
        "requested_net_charge_e": requested_net_charge,
        "reference_spin_multiplicity": reference_spin_multiplicity,
        "charge_adjusted_valence_electron_count": (
            charge_adjusted_electron_count
        ),
        "charge_adjusted_electron_count_parity": (
            "odd" if charge_adjusted_electron_count % 2 else "even"
        ),
        "backend_charge_binding_status": backend_charge_status or None,
        "backend_spin_binding_status": backend_spin_status or None,
        "charge_spin_backend_binding_ready": (
            charge_spin_backend_binding_ready
        ),
        "charge_spin_binding_contract": charge_spin_binding,
        "expected_castep_charge_spin_settings": (
            (charge_spin_binding or {}).get("expected_settings")
        ),
        "observed_castep_charge_spin_settings": (
            (charge_spin_binding or {}).get("observed_settings")
        ),
        "castep_charge_spin_field_matches": (
            (charge_spin_binding or {}).get("field_matches")
        ),
        "non_passivant_valence_electron_count": non_passivant_valence,
        "passivant_valence_electron_count": passivant_valence,
        "valence_electrons_per_non_passivant_atom": _round(non_passivant_valence / non_passivant_count)
        if non_passivant_count
        else None,
        "electron_count_parity": "even" if total_valence % 2 == 0 else "odd",
        "odd_electron_warning": odd_electron_warning,
        "spin_charge_review_required": spin_charge_review_required,
        "spin_polarization_review_required": spin_charge_review_required,
        "minimum_unpaired_electron_count_hint": (
            reference_spin_multiplicity - 1
            if reference_spin_multiplicity is not None
            else 1
            if odd_electron_warning
            else 0
        ),
        "recommended_spin_treatment": recommended_spin_treatment,
        "calculation_readiness_impact": calculation_readiness_impact,
        "next_action": next_action,
        "host_reference_valence_electrons": _round(host_reference_valence) if host_reference_valence is not None else None,
        "average_host_reference_valence_electrons": _round(host_reference_valence)
        if host_reference_valence is not None
        else None,
        "average_host_nominal_dopant_delta_electrons": average_host_dopant_delta,
        "average_host_carrier_type_hint": average_host_carrier_hint,
        "site_adjusted_dopant_delta_electrons": site_adjusted_delta,
        "carrier_type_hint_source": carrier_hint_source,
        "nominal_dopant_delta_electrons": total_dopant_delta,
        "carrier_type_hint": carrier_hint,
        "elements": element_rows,
    }


def _dopant_site_summary(
    metadata: dict[str, Any],
    atom_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    raw_entries = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_entries:
        raw_entries.append(dict(latest))
    if not raw_entries:
        return None

    tmd_context = _is_tmd_metadata_context(metadata)
    oxide_context = _is_oxide_dopant_site_context(metadata, raw_entries)
    oxide_cations = _oxide_dopant_site_cations(metadata, raw_entries) if oxide_context else set()
    halide_context_elements = _metadata_material_elements(metadata) | {
        element
        for raw in raw_entries
        for element in (
            _element_or_none(raw.get("site_element")),
            _element_or_none(raw.get("dopant_element") or raw.get("new_element") or raw.get("element")),
        )
        if element
    }
    halide_perovskite_context = _is_halide_perovskite_context(metadata, sorted(halide_context_elements))
    entries = []
    stale_entries = []
    role_counts: Counter[str] = Counter()
    carrier_counts: Counter[str] = Counter()
    site_family_counts: Counter[str] = Counter()
    site_adjusted_delta = 0
    site_adjusted_delta_available = True
    atoms_by_id = (
        {
            str(atom.get("id")): str(atom.get("element"))
            for atom in atom_rows
            if isinstance(atom, dict) and atom.get("id") is not None
        }
        if atom_rows is not None
        else None
    )
    for raw in raw_entries:
        atom_id = str(raw.get("atom_id") or raw.get("site_id") or "").strip()
        site_element = _element_or_none(raw.get("site_element"))
        dopant_element = _element_or_none(raw.get("dopant_element") or raw.get("new_element") or raw.get("element"))
        actual_element = atoms_by_id.get(atom_id) if atoms_by_id is not None and atom_id else None
        record_status = "valid"
        consistency_error = None
        if atoms_by_id is not None:
            if not atom_id:
                record_status = "missing_atom_id"
                consistency_error = "Dopant-site metadata is missing atom_id/site_id."
            elif atom_id not in atoms_by_id:
                record_status = "atom_not_found"
                consistency_error = f"Dopant-site metadata references missing atom {atom_id}."
            elif dopant_element is None:
                record_status = "invalid_dopant_element"
                consistency_error = f"Dopant-site metadata for {atom_id} has no valid dopant element."
            elif actual_element != dopant_element:
                record_status = "actual_element_mismatch"
                consistency_error = (
                    f"Dopant-site metadata references {atom_id} as {dopant_element}, "
                    f"but the current structure contains {actual_element}."
                )
        if record_status != "valid":
            stale_entries.append(
                {
                    "site_id": raw.get("site_id") or raw.get("atom_id"),
                    "atom_id": raw.get("atom_id") or raw.get("site_id"),
                    "site_element": site_element,
                    "dopant_element": dopant_element,
                    "actual_element": actual_element,
                    "record_status": record_status,
                    "consistency_error": consistency_error,
                    "fractional": _coerce_fractional(raw.get("fractional")),
                    "auto_selected_site": bool(raw.get("auto_selected_site")),
                    "source": raw.get("source"),
                }
            )
            continue
        site_family = _semiconductor_site_family(
            site_element,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        role_hint = _dopant_site_role_hint(
            site_element,
            dopant_element,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        carrier_hint = _carrier_hint_from_dopant_site_role(role_hint)
        role_counts[role_hint] += 1
        carrier_counts[carrier_hint] += 1
        site_family_counts[site_family] += 1
        site_valence = NOMINAL_VALENCE_ELECTRONS.get(site_element or "")
        dopant_valence = NOMINAL_VALENCE_ELECTRONS.get(dopant_element or "")
        nominal_delta = (dopant_valence - site_valence) if dopant_valence is not None and site_valence is not None else None
        if nominal_delta is None:
            site_adjusted_delta_available = False
        else:
            site_adjusted_delta += nominal_delta
        entries.append(
            {
                "site_id": raw.get("site_id") or raw.get("atom_id"),
                "atom_id": raw.get("atom_id") or raw.get("site_id"),
                "site_element": site_element,
                "dopant_element": dopant_element,
                "actual_element": actual_element or dopant_element,
                "record_status": "valid",
                "consistency_error": None,
                "site_family": site_family,
                "site_valence_electrons": site_valence,
                "dopant_valence_electrons": dopant_valence,
                "nominal_delta_electrons": nominal_delta,
                "role_hint": role_hint,
                "carrier_type_hint": carrier_hint,
                "fractional": _coerce_fractional(raw.get("fractional")),
                "auto_selected_site": bool(raw.get("auto_selected_site")),
                "source": raw.get("source"),
            }
        )

    carrier_type_hint = _summarize_site_carrier_hint(carrier_counts) if entries else None
    warnings = []
    if carrier_type_hint == "mixed_or_compensated":
        warnings.append("Semiconductor dopant sites include mixed donor-like and acceptor-like roles; inspect dopant_site_summary.")
    elif carrier_type_hint == "unknown_site_dependent":
        warnings.append("Semiconductor dopant site role is unknown for at least one substitution; inspect dopant_site_summary.")
    if stale_entries:
        warnings.append(
            "Semiconductor dopant-site metadata is stale; reconcile dopant metadata with the current structure and re-audit."
        )
    errors = [
        str(entry.get("consistency_error"))
        for entry in stale_entries
        if entry.get("consistency_error")
    ]
    next_action = (
        "reconcile_dopant_metadata_with_current_structure_then_reaudit"
        if stale_entries
        else None
    )
    recommended_tool = (
        "material_studio_project_reconcile_dopant_metadata"
        if stale_entries
        else None
    )

    context = {
        "tmd_context": tmd_context,
        "oxide_context": oxide_context,
        "oxide_cations": sorted(oxide_cations),
    }
    if halide_perovskite_context:
        context.update(
            {
                "halide_perovskite_context": True,
                "halide_perovskite_b_cations": sorted(halide_context_elements & HALIDE_PEROVSKITE_B_CATIONS),
                "halide_perovskite_halides": sorted(halide_context_elements & HALIDE_PEROVSKITE_HALIDES),
            }
        )

    return {
        "available": True,
        "raw_site_count": len(raw_entries),
        "site_count": len(entries),
        "valid_site_count": len(entries),
        "stale_site_count": len(stale_entries),
        "metadata_consistent": not stale_entries,
        "carrier_type_hint": carrier_type_hint,
        "role_counts": dict(sorted(role_counts.items())),
        "carrier_type_counts": dict(sorted(carrier_counts.items())),
        "site_family_counts": dict(sorted(site_family_counts.items())),
        "context": context,
        "donor_like_count": carrier_counts.get("donor_like_n_type", 0),
        "acceptor_like_count": carrier_counts.get("acceptor_like_p_type", 0),
        "isovalent_count": carrier_counts.get("neutral_or_intrinsic", 0),
        "unknown_count": carrier_counts.get("unknown_site_dependent", 0),
        "site_adjusted_nominal_delta_electrons": (
            site_adjusted_delta if entries and site_adjusted_delta_available else None
        ),
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
        "next_action": next_action,
        "recommended_tool": recommended_tool,
        "latest": entries[-1] if entries else None,
        "entries": entries[:MAX_HEALTH_DETAIL_ROWS],
        "stale_entries": stale_entries[:MAX_HEALTH_DETAIL_ROWS],
    }


def _element_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text in COVALENT_RADII_ANGSTROM or text in NOMINAL_VALENCE_ELECTRONS else None


def _is_tmd_metadata_context(metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("structure_family") or "").lower()
    if "tmd" in family:
        return True
    material_elements = _metadata_material_elements(metadata)
    return bool(material_elements & TMD_METALS and material_elements & TMD_CHALCOGENS)


def _is_oxide_dopant_site_context(metadata: dict[str, Any], raw_entries: list[dict[str, Any]]) -> bool:
    if not (metadata.get("oxide_semiconductor") or metadata.get("oxide_material")):
        return False
    material_elements = _metadata_material_elements(metadata)
    entry_elements = {
        element
        for raw in raw_entries
        for element in (
            _element_or_none(raw.get("site_element")),
            _element_or_none(raw.get("dopant_element") or raw.get("new_element") or raw.get("element")),
        )
        if element
    }
    return "O" in (material_elements | entry_elements)


def _oxide_dopant_site_cations(metadata: dict[str, Any], raw_entries: list[dict[str, Any]]) -> set[str]:
    material_elements = _metadata_material_elements(metadata)
    site_elements = {
        element
        for raw in raw_entries
        for element in (_element_or_none(raw.get("site_element")),)
        if element
    }
    return {element for element in (material_elements | site_elements) if element != "O"}


def _semiconductor_site_family(
    element: str | None,
    *,
    tmd_context: bool = False,
    oxide_context: bool = False,
    oxide_cations: set[str] | None = None,
    halide_perovskite_context: bool = False,
) -> str:
    oxide_cations = oxide_cations or set()
    if oxide_context and element in oxide_cations:
        return "oxide_cation"
    if oxide_context and element == "O":
        return "oxide_anion"
    if halide_perovskite_context and element in HALIDE_PEROVSKITE_B_CATIONS:
        return "halide_perovskite_b_cation"
    if halide_perovskite_context and element in HALIDE_PEROVSKITE_HALIDES:
        return "halide_perovskite_halide"
    if halide_perovskite_context and element in {"C", "N"}:
        return "halide_perovskite_organic_cation"
    if tmd_context and element in TMD_METALS:
        return "tmd_metal"
    if tmd_context and element in TMD_CHALCOGENS:
        return "tmd_chalcogen"
    if element in GROUP_IV_SEMICONDUCTORS:
        return "group_iv"
    if element in III_V_CATIONS:
        return "iii_v_cation"
    if element in III_V_ANIONS:
        return "iii_v_anion"
    if element in II_VI_CATIONS:
        return "ii_vi_cation"
    if element in II_VI_ANIONS:
        return "ii_vi_anion"
    if element in SURFACE_PASSIVANTS:
        return "passivant"
    return "unknown"


def _dopant_site_role_hint(
    site_element: str | None,
    dopant_element: str | None,
    *,
    tmd_context: bool = False,
    oxide_context: bool = False,
    oxide_cations: set[str] | None = None,
    halide_perovskite_context: bool = False,
) -> str:
    if not site_element or not dopant_element:
        return "unknown_site_dependent"
    if site_element == dopant_element:
        return "identity_substitution"
    site_valence = NOMINAL_VALENCE_ELECTRONS.get(site_element)
    dopant_valence = NOMINAL_VALENCE_ELECTRONS.get(dopant_element)
    oxide_cations = oxide_cations or set()
    if oxide_context and site_element in oxide_cations:
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_oxide_cation_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_oxide_cation_site"
            return "isovalent_oxide_cation_substitution"
    if oxide_context and site_element == "O":
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_oxide_anion_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_oxide_anion_site"
            return "isovalent_oxide_anion_substitution"
    if halide_perovskite_context and site_element in HALIDE_PEROVSKITE_B_CATIONS:
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_halide_perovskite_b_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_halide_perovskite_b_site"
            return "isovalent_halide_perovskite_b_site_substitution"
    if halide_perovskite_context and site_element in HALIDE_PEROVSKITE_HALIDES:
        if dopant_element in HALIDE_PEROVSKITE_HALIDES and dopant_valence == site_valence:
            return "isovalent_halide_perovskite_halide_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_halide_perovskite_halide_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_halide_perovskite_halide_site"
            return "isovalent_halide_perovskite_halide_substitution"
    if halide_perovskite_context and site_element in {"C", "N"}:
        return "halide_perovskite_organic_cation_substitution_review_required"
    if tmd_context and site_element in TMD_METALS:
        if dopant_element in TMD_METALS and dopant_valence == site_valence:
            return "isovalent_tmd_metal_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_tmd_metal_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_tmd_metal_site"
            return "isovalent_tmd_metal_substitution"
    if tmd_context and site_element in TMD_CHALCOGENS:
        if dopant_element in TMD_CHALCOGENS and dopant_valence == site_valence:
            return "isovalent_tmd_chalcogen_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_tmd_chalcogen_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_tmd_chalcogen_site"
            return "isovalent_tmd_chalcogen_substitution"
    if site_element in GROUP_IV_SEMICONDUCTORS:
        if dopant_element in III_V_ANIONS:
            return "donor_like_n_type_on_group_iv_site"
        if dopant_element in III_V_CATIONS:
            return "acceptor_like_p_type_on_group_iv_site"
        if dopant_element in GROUP_IV_SEMICONDUCTORS:
            return "isovalent_group_iv_substitution"
    if site_element in III_V_CATIONS:
        if dopant_element in III_V_CATIONS:
            return "isovalent_iii_v_cation_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_iii_v_cation_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_iii_v_cation_site"
            return "isovalent_iii_v_cation_substitution"
    if site_element in III_V_ANIONS:
        if dopant_element in III_V_ANIONS:
            return "isovalent_iii_v_anion_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_iii_v_anion_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_iii_v_anion_site"
            return "isovalent_iii_v_anion_substitution"
    if site_element in II_VI_CATIONS:
        if dopant_element in II_VI_CATIONS:
            return "isovalent_ii_vi_cation_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_ii_vi_cation_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_ii_vi_cation_site"
            return "isovalent_ii_vi_cation_substitution"
    if site_element in II_VI_ANIONS:
        if dopant_element in II_VI_ANIONS:
            return "isovalent_ii_vi_anion_substitution"
        if site_valence is not None and dopant_valence is not None:
            if dopant_valence > site_valence:
                return "donor_like_n_type_on_ii_vi_anion_site"
            if dopant_valence < site_valence:
                return "acceptor_like_p_type_on_ii_vi_anion_site"
            return "isovalent_ii_vi_anion_substitution"
    if site_valence is not None and dopant_valence is not None:
        if dopant_valence > site_valence:
            return "donor_like_n_type_by_nominal_valence"
        if dopant_valence < site_valence:
            return "acceptor_like_p_type_by_nominal_valence"
        return "isovalent_substitution_by_nominal_valence"
    return "unknown_site_dependent"


def _carrier_hint_from_dopant_site_role(role_hint: str) -> str:
    if role_hint.startswith("donor_like"):
        return "donor_like_n_type"
    if role_hint.startswith("acceptor_like"):
        return "acceptor_like_p_type"
    if role_hint.startswith("isovalent") or role_hint == "identity_substitution":
        return "neutral_or_intrinsic"
    return "unknown_site_dependent"


def _summarize_site_carrier_hint(carrier_counts: Counter[str]) -> str:
    donor = carrier_counts.get("donor_like_n_type", 0)
    acceptor = carrier_counts.get("acceptor_like_p_type", 0)
    unknown = carrier_counts.get("unknown_site_dependent", 0)
    neutral = carrier_counts.get("neutral_or_intrinsic", 0)
    if donor and not acceptor and not unknown:
        return "donor_like_n_type"
    if acceptor and not donor and not unknown:
        return "acceptor_like_p_type"
    if donor or acceptor:
        return "mixed_or_compensated"
    if neutral and not unknown:
        return "neutral_or_intrinsic"
    return "unknown_site_dependent"


def _carrier_intent_summary(
    metadata: dict[str, Any],
    *,
    dopant_summary: dict[str, Any] | None,
    charge_balance_summary: dict[str, Any] | None,
    dopant_site_summary: dict[str, Any] | None,
    defect_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("semiconductor_carrier_intents", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_carrier_intent")
    if isinstance(latest, dict) and latest not in entries:
        entries.append(dict(latest))
    if not entries:
        return None

    charge_balance_summary = charge_balance_summary or {}
    dopant_summary = dopant_summary or {}
    dopant_site_summary = dopant_site_summary or {}
    defect_summary = defect_summary or {}
    default_actual_carrier_hint = (
        dopant_site_summary.get("carrier_type_hint")
        or defect_summary.get("carrier_type_hint")
        or charge_balance_summary.get("carrier_type_hint")
    )
    dopant_rows = {
        str(item.get("element")): item
        for item in dopant_summary.get("dopants", []) or []
        if isinstance(item, dict) and item.get("element")
    }
    actual_dopants = sorted(dopant_rows)
    defect_rows = [
        dict(item)
        for item in defect_summary.get("defects", []) or []
        if isinstance(item, dict)
    ]

    normalized_entries = []
    warnings: list[str] = []
    for entry in entries:
        requested_type = _normalize_carrier_type(entry.get("carrier_type"))
        requested_dopant = entry.get("dopant_element")
        requested_dopant = str(requested_dopant) if requested_dopant else None
        requested_mechanism = str(entry.get("carrier_mechanism") or "").strip() or (
            "defect" if entry.get("defect_type") else ("dopant" if requested_dopant else None)
        )
        requested_defect_type = str(entry.get("defect_type") or "").strip() or None
        requested_site_element = entry.get("site_element")
        requested_site_element = str(requested_site_element) if requested_site_element else None
        if requested_mechanism == "defect":
            actual_carrier_hint = defect_summary.get("carrier_type_hint") or default_actual_carrier_hint
        else:
            actual_carrier_hint = default_actual_carrier_hint
        actual_carrier_type = _carrier_hint_to_requested_type(actual_carrier_hint)
        dopant = dopant_rows.get(requested_dopant or "")
        defect_matches = [
            defect
            for defect in defect_rows
            if (requested_defect_type is None or str(defect.get("type") or "") == requested_defect_type)
            and (requested_site_element is None or str(defect.get("site_element") or "") == requested_site_element)
        ]
        defect_present = bool(defect_matches) if requested_mechanism == "defect" else None
        dopant_present = bool(dopant) if requested_dopant else None
        carrier_matches = requested_type is not None and requested_type == actual_carrier_type
        target_matches = defect_present if requested_mechanism == "defect" else (True if requested_dopant is None else dopant_present)
        matches = bool(carrier_matches and target_matches)
        warning = None
        if not carrier_matches:
            warning = (
                f"Requested semiconductor carrier type {requested_type or 'unknown'} does not match "
                f"actual carrier hint {actual_carrier_hint or 'unknown'}."
            )
        elif requested_mechanism == "defect" and not defect_present:
            warning = "Requested defect carrier mechanism is not present in the current semiconductor defects."
        elif requested_dopant and not dopant_present:
            warning = f"Requested dopant {requested_dopant} is not present in the current semiconductor composition."
        if warning:
            warnings.append(warning)
        normalized_entries.append(
            {
                "requested_carrier_type": requested_type,
                "requested_carrier_mechanism": requested_mechanism,
                "requested_dopant_element": requested_dopant,
                "requested_defect_type": requested_defect_type,
                "requested_site_element": requested_site_element,
                "requested_site_id": entry.get("site_id"),
                "requested_mapping_rule": entry.get("mapping_rule"),
                "requested_fraction": entry.get("requested_fraction"),
                "requested_percent": entry.get("requested_percent"),
                "actual_carrier_type": actual_carrier_type,
                "actual_carrier_type_hint": actual_carrier_hint,
                "actual_dopant_present": dopant_present,
                "actual_dopant_fraction": dopant.get("concentration_fraction") if dopant else None,
                "actual_dopant_percent": dopant.get("concentration_percent") if dopant else None,
                "actual_defect_present": defect_present,
                "actual_defect_count": len(defect_matches) if requested_mechanism == "defect" else None,
                "matches": matches,
                "source": entry.get("source"),
                "warning": warning,
            }
        )

    latest_entry = normalized_entries[-1]
    return {
        "available": True,
        "entry_count": len(normalized_entries),
        "requested_carrier_type": latest_entry.get("requested_carrier_type"),
        "requested_carrier_mechanism": latest_entry.get("requested_carrier_mechanism"),
        "requested_dopant_element": latest_entry.get("requested_dopant_element"),
        "requested_defect_type": latest_entry.get("requested_defect_type"),
        "requested_site_element": latest_entry.get("requested_site_element"),
        "requested_site_id": latest_entry.get("requested_site_id"),
        "requested_mapping_rule": latest_entry.get("requested_mapping_rule"),
        "actual_carrier_type": latest_entry.get("actual_carrier_type"),
        "actual_carrier_type_hint": latest_entry.get("actual_carrier_type_hint"),
        "actual_dopant_elements": actual_dopants,
        "actual_defect_count": defect_summary.get("defect_count"),
        "latest_matches": bool(latest_entry.get("matches")),
        "all_entries_match": all(bool(item.get("matches")) for item in normalized_entries),
        "warning_count": len(warnings),
        "warnings": warnings[:MAX_HEALTH_DETAIL_ROWS],
        "latest": latest_entry,
        "entries": normalized_entries[:MAX_HEALTH_DETAIL_ROWS],
    }


def _junction_summary(metadata: dict[str, Any]) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("semiconductor_junctions", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_junction")
    if isinstance(latest, dict) and latest not in entries:
        entries.append(dict(latest))
    if not entries:
        return None

    normalized = []
    warnings = []
    junction_type_counts: Counter[str] = Counter()
    for raw in entries:
        p_region = dict(raw.get("p_region") or {}) if isinstance(raw.get("p_region"), dict) else {}
        n_region = dict(raw.get("n_region") or {}) if isinstance(raw.get("n_region"), dict) else {}
        junction_type = str(raw.get("junction_type") or raw.get("type") or "junction")
        junction_type_counts[junction_type] += 1
        p_sites = [str(item) for item in p_region.get("site_ids", []) or [] if str(item)]
        n_sites = [str(item) for item in n_region.get("site_ids", []) or [] if str(item)]
        if junction_type == "pn_junction" and (not p_sites or not n_sites):
            warnings.append("PN junction metadata is missing p-side or n-side dopant sites.")
        normalized.append(
            {
                "junction_type": junction_type,
                "host_element": _element_or_none(raw.get("host_element")),
                "axis": str(raw.get("axis") or "a"),
                "p_region": {
                    "carrier_type": _normalize_carrier_type(p_region.get("carrier_type")) or p_region.get("carrier_type"),
                    "dopant_element": _element_or_none(p_region.get("dopant_element")),
                    "site_ids": p_sites,
                    "site_count": len(p_sites),
                    "fractional_range": _coerce_fractional_range(p_region.get("fractional_range")),
                },
                "n_region": {
                    "carrier_type": _normalize_carrier_type(n_region.get("carrier_type")) or n_region.get("carrier_type"),
                    "dopant_element": _element_or_none(n_region.get("dopant_element")),
                    "site_ids": n_sites,
                    "site_count": len(n_sites),
                    "fractional_range": _coerce_fractional_range(n_region.get("fractional_range")),
                },
                "source": raw.get("source"),
            }
        )

    latest_entry = normalized[-1]
    return {
        "available": True,
        "junction_count": len(normalized),
        "pn_junction_count": junction_type_counts.get("pn_junction", 0),
        "junction_type_counts": dict(sorted(junction_type_counts.items())),
        "host_element": latest_entry.get("host_element"),
        "axis": latest_entry.get("axis"),
        "p_dopant_element": (latest_entry.get("p_region") or {}).get("dopant_element"),
        "n_dopant_element": (latest_entry.get("n_region") or {}).get("dopant_element"),
        "warning_count": len(warnings),
        "warnings": warnings[:MAX_HEALTH_DETAIL_ROWS],
        "latest": latest_entry,
        "entries": normalized[:MAX_HEALTH_DETAIL_ROWS],
    }


def _coerce_fractional_range(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return [_round(float(value[0])), _round(float(value[1]))]
    except (TypeError, ValueError):
        return None


def _normalize_carrier_type(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"n", "n_type", "ntype", "donor", "donor_like", "donor_like_n_type"}:
        return "n_type"
    if text in {"p", "p_type", "ptype", "acceptor", "acceptor_like", "acceptor_like_p_type"}:
        return "p_type"
    if text in {"intrinsic", "neutral", "neutral_or_intrinsic"}:
        return "intrinsic"
    return None


def _carrier_hint_to_requested_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if "donor" in text or "n_type" in text:
        return "n_type"
    if "acceptor" in text or "p_type" in text:
        return "p_type"
    if "intrinsic" in text or "neutral" in text:
        return "intrinsic"
    return None


def _host_reference_valence(host_elements: list[str]) -> float | None:
    values = [
        NOMINAL_VALENCE_ELECTRONS[element]
        for element in host_elements
        if element in NOMINAL_VALENCE_ELECTRONS and element not in SURFACE_PASSIVANTS
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _castep_task_classification(task: Any) -> dict[str, Any]:
    try:
        canonical_task = normalize_castep_task(task) if task is not None else None
    except ValueError:
        canonical_task = None
    text = (
        canonical_task.value
        if canonical_task is not None
        else (str(task).strip() if task is not None else "")
    )
    normalized = re.sub(r"[^a-z0-9]+", "", text.lower())
    if not normalized:
        return {
            "task_family": "not_set",
            "task_intent": None,
            "changes_structure": False,
            "requires_prior_relaxed_structure": False,
            "settings_review_required": True,
            "execution_risk": "unknown",
        }
    if normalized in {"energy", "singlepoint", "singlepointenergy"}:
        return {
            "task_family": "energy",
            "task_intent": "static_energy",
            "changes_structure": False,
            "requires_prior_relaxed_structure": False,
            "settings_review_required": False,
            "execution_risk": "moderate",
        }
    if normalized in {
        "geometryoptimization",
        "geometryoptimisation",
        "geomopt",
        "optimize",
        "optimise",
        "relax",
        "relaxation",
    }:
        return {
            "task_family": "relaxation",
            "task_intent": "geometry_optimization",
            "changes_structure": True,
            "requires_prior_relaxed_structure": False,
            "settings_review_required": True,
            "execution_risk": "high",
        }
    for token, intent in CASTEP_PROPERTY_TASK_INTENTS.items():
        if token in normalized:
            return {
                "task_family": "property",
                "task_intent": intent,
                "changes_structure": False,
                "requires_prior_relaxed_structure": True,
                "settings_review_required": True,
                "execution_risk": "high",
            }
    return {
        "task_family": "unknown",
        "task_intent": normalized,
        "changes_structure": False,
        "requires_prior_relaxed_structure": True,
        "settings_review_required": True,
        "execution_risk": "unknown",
    }


def _calculation_next_action(classification: dict[str, Any], warnings: list[str]) -> str:
    if warnings:
        if classification.get("task_family") == "property":
            return "review_property_task_settings_and_prior_relaxation"
        if classification.get("task_family") == "relaxation":
            return "review_relaxation_settings_before_execution"
        return "review_preflight_warnings"
    if classification.get("task_family") == "energy":
        return "ready_for_static_energy_preview_or_explicit_execute"
    return "review_task_before_execution"


def _calculation_preflight_summary(spec: ModelSpec, lattice_summary: dict[str, Any] | None) -> dict[str, Any] | None:
    simulation = spec.simulation
    if simulation is None:
        return {
            "available": True,
            "configured": False,
            "module": None,
            "task": None,
            "task_family": "not_configured",
            "task_intent": None,
            "functional": None,
            "quality": None,
            "status": "not_configured",
            "ready_for_energy_preflight": False,
            "ready_for_requested_task_preflight": False,
            "changes_structure": False,
            "requires_prior_relaxed_structure": False,
            "settings_review_required": True,
            "execution_risk": "unknown",
            "cutoff_energy_ev": None,
            "cutoff_status": "not_set",
            "kpoint_mode": "not_set",
            "kpoint_separation": None,
            "kpoints": None,
            "dipole_correction_mode": None,
            "dipole_correction_configured": False,
            "dipole_correction_enabled": False,
            "dipole_correction_api_contract": CASTEP_DIPOLE_CORRECTION_API_CONTRACT,
            "dipole_correction_api_property": CASTEP_DIPOLE_CORRECTION_API_PROPERTY,
            "total_charge": None,
            "spin_treatment": None,
            "use_formal_spin": None,
            "initial_spin": None,
            "optimize_total_spin": None,
            "charge_spin_settings_configured": False,
            "charge_spin_api_contract": CASTEP_CHARGE_SPIN_API_CONTRACT,
            "slab_axis": (lattice_summary or {}).get("surface_axis"),
            "slab_kpoint_axis_value": None,
            "output_file": None,
            "recommendations": {
                "minimum_cutoff_energy_ev": CASTEP_RECOMMENDED_MIN_CUTOFF_EV,
                "maximum_kpoint_separation": CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION,
                "property_tasks_need_prior_relaxation": True,
            },
            "next_action": "configure_simulation_settings",
            "warning_count": 1,
            "warnings": ["No simulation settings are configured for this semiconductor model."],
        }

    module = _simulation_module_name(getattr(simulation, "module", None))
    task = getattr(simulation, "task", None)
    functional = getattr(simulation, "functional", None)
    quality = getattr(simulation, "quality", None)
    output_file = getattr(simulation, "output_file", None)
    warnings: list[str] = []
    cutoff_energy = _optional_int(getattr(simulation, "cutoff_energy_ev", None))
    kpoint_separation = _optional_float(getattr(simulation, "kpoint_separation", None))
    kpoints = _simulation_kpoints(getattr(simulation, "kpoints", None))
    dipole_correction = getattr(simulation, "dipole_correction", None)
    dipole_correction_mode = (
        str(getattr(dipole_correction, "value", dipole_correction))
        if dipole_correction is not None
        else None
    )
    dipole_correction_enabled = dipole_correction_mode in {
        CastepDipoleCorrection.SELF_CONSISTENT.value,
        CastepDipoleCorrection.NON_SELF_CONSISTENT.value,
    }
    total_charge = _optional_int(getattr(simulation, "total_charge", None))
    spin_treatment_value = getattr(simulation, "spin_treatment", None)
    spin_treatment = (
        str(getattr(spin_treatment_value, "value", spin_treatment_value))
        if spin_treatment_value is not None
        else None
    )
    use_formal_spin = getattr(simulation, "use_formal_spin", None)
    initial_spin = _optional_int(getattr(simulation, "initial_spin", None))
    optimize_total_spin = getattr(simulation, "optimize_total_spin", None)
    charge_spin_settings_configured = any(
        value is not None
        for value in (
            total_charge,
            spin_treatment,
            use_formal_spin,
            initial_spin,
            optimize_total_spin,
        )
    )
    lattice_summary = lattice_summary or {}
    is_slab = bool(lattice_summary.get("is_slab"))
    slab_axis = lattice_summary.get("surface_axis")
    slab_axis_value = None
    classification = _castep_task_classification(task) if module == "CASTEP" else {
        "task_family": "non_castep",
        "task_intent": None,
        "changes_structure": False,
        "requires_prior_relaxed_structure": False,
        "settings_review_required": True,
        "execution_risk": "unknown",
    }

    cutoff_status = "not_applicable"
    kpoint_mode = "not_applicable"
    if module == "CASTEP":
        task_family = classification.get("task_family")
        if task_family == "not_set":
            warnings.append("CASTEP task is not set.")
        elif task_family == "relaxation":
            warnings.append(
                "CASTEP geometry optimization can change atomic positions or cell parameters; "
                "review relaxation settings before execution."
            )
        elif task_family == "property":
            warnings.append(
                f"CASTEP {task} property task should usually use a relaxed structure and reviewed property settings "
                "before execution."
            )
        elif task_family == "unknown":
            warnings.append(
                f"CASTEP task {task!r} is not recognized by MCP semiconductor preflight; "
                "review Materials Studio Copy Script/settings before execution."
            )

        if cutoff_energy is None:
            cutoff_status = "not_set"
            warnings.append("CASTEP cutoff_energy_ev is not set.")
        elif cutoff_energy < CASTEP_RECOMMENDED_MIN_CUTOFF_EV:
            cutoff_status = "low"
            warnings.append(
                f"CASTEP cutoff_energy_ev={cutoff_energy} is below the conservative semiconductor preflight threshold "
                f"of {CASTEP_RECOMMENDED_MIN_CUTOFF_EV} eV."
            )
        else:
            cutoff_status = "ok"

        if kpoints is not None:
            kpoint_mode = "explicit_grid"
            if is_slab:
                axis_index = _axis_index(slab_axis or "c")
                if axis_index is not None and axis_index < len(kpoints):
                    slab_axis_value = kpoints[axis_index]
                    if slab_axis_value != 1:
                        warnings.append(
                            "CASTEP slab k-point grid should usually use 1 point along the surface-normal axis; "
                            f"got {slab_axis or 'c'}={slab_axis_value}."
                        )
        elif kpoint_separation is not None:
            kpoint_mode = "separation"
            if kpoint_separation > CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION:
                warnings.append(
                    f"CASTEP kpoint_separation={_round(kpoint_separation)} is coarser than the conservative semiconductor "
                    f"preflight threshold of {CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION}."
                )
        else:
            kpoint_mode = "not_set"
            warnings.append("CASTEP k-point settings are not set.")
    else:
        if kpoints is not None:
            kpoint_mode = "explicit_grid"
        elif kpoint_separation is not None:
            kpoint_mode = "separation"

    status = "ok" if not warnings else "warnings"
    if module != "CASTEP":
        status = "non_castep"

    return {
        "available": True,
        "configured": True,
        "module": module,
        "task": task,
        "task_family": classification.get("task_family"),
        "task_intent": classification.get("task_intent"),
        "functional": str(functional) if functional is not None else None,
        "quality": str(quality) if quality is not None else None,
        "status": status,
        "ready_for_energy_preflight": bool(module == "CASTEP" and classification.get("task_family") == "energy" and not warnings),
        "ready_for_requested_task_preflight": bool(module == "CASTEP" and not warnings),
        "changes_structure": bool(classification.get("changes_structure")),
        "requires_prior_relaxed_structure": bool(classification.get("requires_prior_relaxed_structure")),
        "settings_review_required": bool(classification.get("settings_review_required")),
        "execution_risk": classification.get("execution_risk"),
        "cutoff_energy_ev": cutoff_energy,
        "cutoff_status": cutoff_status,
        "kpoint_mode": kpoint_mode,
        "kpoint_separation": _round(kpoint_separation) if kpoint_separation is not None else None,
        "kpoints": list(kpoints) if kpoints is not None else None,
        "dipole_correction_mode": dipole_correction_mode,
        "dipole_correction_configured": dipole_correction_mode is not None,
        "dipole_correction_enabled": dipole_correction_enabled,
        "dipole_correction_api_contract": CASTEP_DIPOLE_CORRECTION_API_CONTRACT,
        "dipole_correction_api_property": CASTEP_DIPOLE_CORRECTION_API_PROPERTY,
        "total_charge": total_charge,
        "spin_treatment": spin_treatment,
        "use_formal_spin": use_formal_spin,
        "initial_spin": initial_spin,
        "optimize_total_spin": optimize_total_spin,
        "charge_spin_settings_configured": charge_spin_settings_configured,
        "charge_spin_api_contract": CASTEP_CHARGE_SPIN_API_CONTRACT,
        "slab_axis": slab_axis,
        "slab_kpoint_axis_value": slab_axis_value,
        "output_file": output_file,
        "recommendations": {
            "minimum_cutoff_energy_ev": CASTEP_RECOMMENDED_MIN_CUTOFF_EV,
            "maximum_kpoint_separation": CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION,
            "slab_surface_normal_kpoint_count": 1,
            "property_tasks_need_prior_relaxation": True,
        },
        "next_action": _calculation_next_action(classification, warnings),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _semiconductor_band_path_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    calculation_preflight_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None

    family_text = " ".join(
        str(value or "")
        for value in (
            metadata.get("structure_family"),
            metadata.get("interface"),
            getattr(spec.model, "name", None),
        )
    ).lower()
    calculation_preflight_summary = calculation_preflight_summary or {}
    task_intent = calculation_preflight_summary.get("task_intent")
    task_relevant = task_intent == "band_structure"
    entry = _band_path_library_entry(family_text)
    warnings: list[str] = []
    notes: list[str] = [
        "Band path coordinates are fractional reciprocal coordinates for preflight review.",
        "Review the generated Materials Studio/CASTEP BandStructure settings before execution.",
    ]

    if entry is None:
        warnings.append(
            "No conservative high-symmetry band path is registered for this semiconductor structure family."
        )
        return {
            "available": False,
            "model": "semiconductor_band_path_preflight",
            "structure_family": metadata.get("structure_family"),
            "bravais_lattice": None,
            "task_intent": task_intent,
            "task_relevant": task_relevant,
            "path_label": None,
            "point_count": 0,
            "unique_point_count": 0,
            "segment_count": 0,
            "requires_materials_studio_review": True,
            "high_symmetry_points": [],
            "path": [],
            "notes": notes,
            "warning_count": len(warnings),
            "warnings": warnings,
        }

    path_labels = list(entry["path"])
    points = dict(entry["points"])
    path_rows = []
    for index, label in enumerate(path_labels, start=1):
        next_label = path_labels[index] if index < len(path_labels) else None
        path_rows.append(
            {
                "index": index,
                "label": label,
                "fractional": [_round(value) for value in points[label]],
                "next_label": next_label,
                "segment_label": f"{label}-{next_label}" if next_label else None,
            }
        )
    unique_labels = []
    for label in path_labels:
        if label not in unique_labels:
            unique_labels.append(label)

    if not task_relevant:
        notes.append("Current CASTEP task is not BandStructure; this path is an optional setup aid.")

    return {
        "available": True,
        "model": "semiconductor_band_path_preflight",
        "structure_family": metadata.get("structure_family"),
        "bravais_lattice": entry["bravais_lattice"],
        "task_intent": task_intent,
        "task_relevant": task_relevant,
        "path_label": "-".join(path_labels),
        "point_count": len(path_rows),
        "unique_point_count": len(unique_labels),
        "segment_count": max(len(path_rows) - 1, 0),
        "requires_materials_studio_review": True,
        "high_symmetry_points": [
            {"label": label, "fractional": [_round(value) for value in points[label]]}
            for label in unique_labels
        ],
        "path": path_rows,
        "notes": [*notes, *list(entry.get("notes", []))],
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _band_path_library_entry(family_text: str) -> dict[str, Any] | None:
    for entry in SEMICONDUCTOR_BAND_PATH_LIBRARY.values():
        if any(marker in family_text for marker in entry["structure_markers"]):
            return entry
    return None


def _reciprocal_lattice_summary(
    spec: ModelSpec,
    lattice_summary: dict[str, Any] | None,
    calculation_preflight_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None

    lattice = spec.model.lattice
    real_vectors = _lattice_vectors(lattice)
    reciprocal_vectors = _reciprocal_lattice_vectors(real_vectors)
    if reciprocal_vectors is None:
        return {
            "available": False,
            "status": "invalid_lattice",
            "warning_count": 1,
            "warnings": ["Could not compute reciprocal lattice vectors from the crystal cell."],
        }

    real_lengths = [lattice.a, lattice.b, lattice.c]
    reciprocal_lengths = [_vector_length(vector) for vector in reciprocal_vectors]
    simulation = spec.simulation
    kpoint_separation = _optional_float(getattr(simulation, "kpoint_separation", None)) if simulation else None
    kpoints = _simulation_kpoints(getattr(simulation, "kpoints", None)) if simulation else None
    lattice_summary = lattice_summary or {}
    calculation_preflight_summary = calculation_preflight_summary or {}
    slab_axis = lattice_summary.get("surface_axis")
    slab_axis_index = _axis_index(slab_axis) if slab_axis else None
    warnings: list[str] = []

    estimated_grid: list[int] | None = None
    if kpoint_separation is not None:
        estimated_grid = [max(1, int(math.ceil(length / kpoint_separation))) for length in reciprocal_lengths]
        if kpoint_separation > CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION:
            warnings.append(
                f"kpoint_separation={_round(kpoint_separation)} is coarser than the conservative semiconductor reciprocal-space threshold "
                f"of {CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION}."
            )
        if bool(lattice_summary.get("is_slab")):
            warnings.append(
                "Slab model uses kpoint_separation; verify whether an explicit grid with 1 point along the surface-normal axis is preferable."
            )

    actual_separations: list[float | None] = [None, None, None]
    if kpoints is not None:
        actual_separations = [
            _round(reciprocal_lengths[index] / kpoints[index]) if kpoints[index] else None
            for index in range(3)
        ]
        coarse_axes = [
            axis
            for index, (axis, separation) in enumerate(
                zip(("a", "b", "c"), actual_separations)
            )
            if index != slab_axis_index
            and separation is not None
            and separation > CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION
        ]
        if coarse_axes:
            warnings.append(
                "Explicit k-point grid is coarser than the conservative semiconductor preflight threshold along "
                + ",".join(coarse_axes)
                + "."
            )
        if slab_axis_index is not None and kpoints[slab_axis_index] != 1:
            warnings.append(
                f"Slab surface-normal k-point count should usually be 1; got {slab_axis}={kpoints[slab_axis_index]}."
            )
    elif estimated_grid is not None:
        actual_separations = [
            _round(reciprocal_lengths[index] / estimated_grid[index]) if estimated_grid[index] else None
            for index in range(3)
        ]
    elif calculation_preflight_summary.get("module") == "CASTEP":
        warnings.append("No CASTEP k-point grid or separation is configured for reciprocal-space preflight.")

    recommended_grid, recommendation_reason_codes = _recommended_explicit_kpoint_grid(
        reciprocal_lengths,
        configured_kpoints=kpoints,
        kpoint_separation=kpoint_separation,
        slab_axis_index=slab_axis_index,
        is_castep=calculation_preflight_summary.get("module") == "CASTEP",
    )
    recommended_separations: list[float] | None = (
        [
            _round(reciprocal_lengths[index] / recommended_grid[index])
            for index in range(3)
        ]
        if recommended_grid is not None
        else None
    )

    rows = []
    for index, axis in enumerate(("a", "b", "c")):
        surface_normal = slab_axis_index == index
        rows.append(
            {
                "axis": axis,
                "real_length_angstrom": _round(real_lengths[index]),
                "reciprocal_vector_1_per_angstrom": _round_tuple(reciprocal_vectors[index]),
                "reciprocal_length_1_per_angstrom": _round(reciprocal_lengths[index]),
                "configured_kpoint": kpoints[index] if kpoints is not None else None,
                "estimated_kpoint_from_separation": estimated_grid[index] if estimated_grid is not None else None,
                "recommended_kpoint": recommended_grid[index] if recommended_grid is not None else None,
                "actual_separation_1_per_angstrom": actual_separations[index],
                "recommended_separation_1_per_angstrom": (
                    recommended_separations[index]
                    if recommended_separations is not None
                    else None
                ),
                "surface_normal_axis": surface_normal,
                "surface_normal_warning": bool(surface_normal and kpoints is not None and kpoints[index] != 1),
            }
        )

    return {
        "available": True,
        "model": "reciprocal_lattice_kpoint_preflight",
        "status": "warnings" if warnings else "ok",
        "real_lengths_angstrom": [_round(value) for value in real_lengths],
        "reciprocal_lengths_1_per_angstrom": [_round(value) for value in reciprocal_lengths],
        "kpoint_mode": calculation_preflight_summary.get("kpoint_mode"),
        "kpoint_separation": _round(kpoint_separation) if kpoint_separation is not None else None,
        "explicit_kpoints": list(kpoints) if kpoints is not None else None,
        "estimated_kpoints_from_separation": estimated_grid,
        "actual_separations_1_per_angstrom": actual_separations,
        "recommended_kpoint_mode": "explicit_grid" if recommended_grid is not None else None,
        "recommended_kpoints": recommended_grid,
        "recommended_separations_1_per_angstrom": recommended_separations,
        "recommendation_reason_codes": recommendation_reason_codes,
        "recommended_max_kpoint_separation": CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION,
        "slab_axis": slab_axis,
        "axes": rows,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _recommended_explicit_kpoint_grid(
    reciprocal_lengths: list[float],
    *,
    configured_kpoints: tuple[int, int, int] | None,
    kpoint_separation: float | None,
    slab_axis_index: int | None,
    is_castep: bool,
) -> tuple[list[int] | None, list[str]]:
    """Return a conservative explicit grid that can resolve k-point warnings."""

    if not is_castep:
        return None, []

    threshold_grid = [
        max(1, int(math.ceil(length / CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION)))
        for length in reciprocal_lengths
    ]
    reasons: list[str] = []
    if configured_kpoints is not None:
        recommended = [
            max(configured_kpoints[index], threshold_grid[index])
            for index in range(3)
        ]
        if any(
            recommended[index] > configured_kpoints[index]
            for index in range(3)
            if index != slab_axis_index
        ):
            reasons.append("increase_coarse_explicit_grid_axes")
    elif kpoint_separation is not None:
        target_separation = min(
            kpoint_separation,
            CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION,
        )
        recommended = [
            max(1, int(math.ceil(length / target_separation)))
            for length in reciprocal_lengths
        ]
        if kpoint_separation > CASTEP_RECOMMENDED_MAX_KPOINT_SEPARATION:
            reasons.append("replace_coarse_kpoint_separation_with_explicit_grid")
        if slab_axis_index is not None:
            reasons.append("replace_slab_kpoint_separation_with_explicit_grid")
    else:
        recommended = threshold_grid
        reasons.append("configure_missing_explicit_grid")

    if slab_axis_index is not None and recommended[slab_axis_index] != 1:
        recommended[slab_axis_index] = 1
        reasons.append("set_slab_surface_normal_kpoint_to_one")

    current_grid = list(configured_kpoints) if configured_kpoints is not None else None
    if current_grid == recommended or not reasons:
        return None, []
    return recommended, list(dict.fromkeys(reasons))


def _reciprocal_lattice_vectors(
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None:
    a_vec, b_vec, c_vec = vectors
    volume = _dot(a_vec, _cross(b_vec, c_vec))
    if abs(volume) <= 1e-12:
        return None
    factor = 2.0 * math.pi / volume
    return (
        _scale_tuple(_cross(b_vec, c_vec), factor),
        _scale_tuple(_cross(c_vec, a_vec), factor),
        _scale_tuple(_cross(a_vec, b_vec), factor),
    )


def _vector_length(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _simulation_module_name(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _simulation_kpoints(value: Any) -> tuple[int, int, int] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return (int(value[0]), int(value[1]), int(value[2]))
        except (TypeError, ValueError):
            return None
    return None


def _axis_index(axis: Any) -> int | None:
    return {"a": 0, "x": 0, "b": 1, "y": 1, "c": 2, "z": 2}.get(str(axis or "").lower())


def _semiconductor_neighbor_distance_summary(
    neighbor_pair_rows: list[dict[str, Any]],
    *,
    unexpected_pairs: list[dict[str, Any]],
    unchecked_pairs: list[dict[str, Any]],
    passivant_pairs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not neighbor_pair_rows:
        return None

    distances_by_pair: dict[str, list[float]] = {}
    thresholds_by_pair: dict[str, list[float]] = {}
    for row in neighbor_pair_rows:
        pair_type = str(row.get("pair_type") or "")
        distance = _optional_float(row.get("distance_angstrom"))
        if not pair_type or distance is None:
            continue
        distances_by_pair.setdefault(pair_type, []).append(distance)
        threshold = _optional_float(row.get("neighbor_threshold_angstrom"))
        if threshold is not None:
            thresholds_by_pair.setdefault(pair_type, []).append(threshold)

    if not distances_by_pair:
        return None

    unexpected_types = {str(row.get("pair_type")) for row in unexpected_pairs if row.get("pair_type")}
    unchecked_types = {str(row.get("pair_type")) for row in unchecked_pairs if row.get("pair_type")}
    passivant_types = {str(row.get("pair_type")) for row in passivant_pairs if row.get("pair_type")}
    all_distances = [distance for distances in distances_by_pair.values() for distance in distances]
    pair_type_rows = []
    for pair_type in sorted(distances_by_pair):
        distances = distances_by_pair[pair_type]
        distance_stats = _stats_with_count(distances) or {}
        threshold_stats = _stats_with_count(thresholds_by_pair.get(pair_type, [])) or None
        max_distance = max(distances)
        mean_threshold = threshold_stats.get("mean") if threshold_stats else None
        if pair_type in passivant_types:
            pair_role = "passivant"
        elif pair_type in unexpected_types:
            pair_role = "unexpected"
        elif pair_type in unchecked_types:
            pair_role = "unchecked"
        else:
            pair_role = "expected"
        pair_type_rows.append(
            {
                "pair_type": pair_type,
                "pair_role": pair_role,
                "distance_stats_angstrom": distance_stats,
                "neighbor_threshold_stats_angstrom": threshold_stats,
                "distance_spread_angstrom": _round(max(distances) - min(distances)),
                "max_distance_to_threshold_fraction": _round(max_distance / float(mean_threshold))
                if mean_threshold
                else None,
            }
        )

    return {
        "available": True,
        "neighbor_pair_count": len(all_distances),
        "pair_type_count": len(pair_type_rows),
        "distance_stats_angstrom": _stats_with_count(all_distances),
        "unexpected_pair_types": sorted(unexpected_types),
        "unchecked_pair_types": sorted(unchecked_types),
        "passivant_pair_types": sorted(passivant_types),
        "pair_types": pair_type_rows,
    }


def _semiconductor_local_environment_summary(
    spec: ModelSpec,
    atom_rows: list[dict[str, Any]],
    neighbor_pair_rows: list[dict[str, Any]],
    coordination_rows: list[dict[str, Any]],
    *,
    expected_coordination: int | None,
    expected_coordination_by_element: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec) or not atom_rows:
        return None

    vectors = _lattice_vectors(spec.model.lattice)
    atom_map = {str(atom.get("id")): atom for atom in atom_rows}
    coordination_map = {str(row.get("atom_id")): row for row in coordination_rows}
    neighbors: dict[str, list[dict[str, Any]]] = {str(atom.get("id")): [] for atom in atom_rows}
    for row in neighbor_pair_rows:
        atom1_id = str(row.get("atom1"))
        atom2_id = str(row.get("atom2"))
        atom1 = atom_map.get(atom1_id)
        atom2 = atom_map.get(atom2_id)
        if atom1 is None or atom2 is None:
            continue
        fractional1 = atom1.get("fractional")
        fractional2 = atom2.get("fractional")
        if fractional1 is None or fractional2 is None:
            continue
        offset = tuple(int(value) for value in row.get("image_offset_atom2") or [0, 0, 0])
        vector_1_to_2 = _fractional_to_cartesian(
            (
                float(fractional2[0]) + offset[0] - float(fractional1[0]),
                float(fractional2[1]) + offset[1] - float(fractional1[1]),
                float(fractional2[2]) + offset[2] - float(fractional1[2]),
            ),
            vectors,
        )
        distance = _optional_float(row.get("distance_angstrom"))
        neighbors.setdefault(atom1_id, []).append(
            {
                "neighbor_id": atom2_id,
                "neighbor_element": atom2.get("element"),
                "distance_angstrom": distance,
                "vector": vector_1_to_2,
            }
        )
        neighbors.setdefault(atom2_id, []).append(
            {
                "neighbor_id": atom1_id,
                "neighbor_element": atom1.get("element"),
                "distance_angstrom": distance,
                "vector": _scale_tuple(vector_1_to_2, -1.0),
            }
        )

    local_rows = []
    all_angles: list[float] = []
    all_tetrahedral_deviations: list[float] = []
    coordination_outlier_count = 0
    ideal_tetrahedral_angle = 109.471221
    for atom in sorted(atom_rows, key=lambda item: str(item.get("id"))):
        atom_id = str(atom.get("id"))
        atom_neighbors = sorted(
            neighbors.get(atom_id, []),
            key=lambda item: (
                float(item.get("distance_angstrom") or 0.0),
                str(item.get("neighbor_id")),
            ),
        )
        distances = [
            float(item["distance_angstrom"])
            for item in atom_neighbors
            if item.get("distance_angstrom") is not None
        ]
        angles = _neighbor_vector_angles([tuple(item["vector"]) for item in atom_neighbors])
        all_angles.extend(angles)
        element = str(atom.get("element") or "")
        atom_expected_coordination = _expected_coordination_for_element(
            element,
            expected_coordination,
            expected_coordination_by_element,
        )
        coordination_outlier = bool(
            atom_expected_coordination is not None
            and len(atom_neighbors) != atom_expected_coordination
        )
        if coordination_outlier:
            coordination_outlier_count += 1
        tetrahedral_deviations = [abs(angle - ideal_tetrahedral_angle) for angle in angles]
        all_tetrahedral_deviations.extend(tetrahedral_deviations)
        coordination = coordination_map.get(atom_id) or {}
        local_rows.append(
            {
                "atom_id": atom_id,
                "element": atom.get("element"),
                "neighbor_count": len(atom_neighbors),
                "expected_coordination": atom_expected_coordination,
                "coordination_outlier": coordination_outlier,
                "nearest_distance_angstrom": coordination.get("nearest_distance_angstrom"),
                "mean_neighbor_distance_angstrom": coordination.get("mean_neighbor_distance_angstrom"),
                "min_neighbor_distance_angstrom": coordination.get("min_neighbor_distance_angstrom"),
                "max_neighbor_distance_angstrom": coordination.get("max_neighbor_distance_angstrom"),
                "angle_stats_deg": _stats_with_count(angles),
                "mean_tetrahedral_angle_deviation_deg": _round(sum(tetrahedral_deviations) / len(tetrahedral_deviations))
                if tetrahedral_deviations
                else None,
                "max_tetrahedral_angle_deviation_deg": _round(max(tetrahedral_deviations))
                if tetrahedral_deviations
                else None,
                "neighbor_ids": [str(item.get("neighbor_id")) for item in atom_neighbors],
                "neighbor_elements": [str(item.get("neighbor_element")) for item in atom_neighbors],
            }
        )

    return {
        "available": True,
        "atom_count": len(local_rows),
        "expected_coordination": expected_coordination,
        "expected_coordination_by_element": dict(sorted((expected_coordination_by_element or {}).items())),
        "coordination_outlier_count": coordination_outlier_count,
        "angle_stats_deg": _stats_with_count(all_angles),
        "tetrahedral_angle_reference_deg": ideal_tetrahedral_angle,
        "tetrahedral_angle_deviation_stats_deg": _stats_with_count(all_tetrahedral_deviations),
        "local_environments": local_rows[:MAX_HEALTH_DETAIL_ROWS],
    }


def _neighbor_vector_angles(vectors: list[tuple[float, float, float]]) -> list[float]:
    angles = []
    origin = (0.0, 0.0, 0.0)
    for index, left in enumerate(vectors):
        for right in vectors[index + 1 :]:
            angles.append(_round(_angle_degrees(left, origin, right)))
    return angles


def _semiconductor_lattice_summary(
    spec: ModelSpec,
    all_element_counts: Counter[str],
    slab_vacuum: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    lattice = spec.model.lattice
    volume = _lattice_volume(lattice)
    atom_count = sum(int(count) for count in all_element_counts.values())
    passivant_atom_count = sum(
        int(count)
        for element, count in all_element_counts.items()
        if element in SURFACE_PASSIVANTS
    )
    non_passivant_atom_count = atom_count - passivant_atom_count
    summary: dict[str, Any] = {
        "available": True,
        "a_angstrom": _round(lattice.a),
        "b_angstrom": _round(lattice.b),
        "c_angstrom": _round(lattice.c),
        "alpha_deg": _round(lattice.alpha),
        "beta_deg": _round(lattice.beta),
        "gamma_deg": _round(lattice.gamma),
        "cell_volume_angstrom3": volume,
        "atom_count": atom_count,
        "non_passivant_atom_count": non_passivant_atom_count,
        "passivant_atom_count": passivant_atom_count,
        "atom_density_per_angstrom3": _round(atom_count / volume) if atom_count and volume else None,
        "non_passivant_atom_density_per_angstrom3": _round(non_passivant_atom_count / volume)
        if non_passivant_atom_count and volume
        else None,
        "volume_per_atom_angstrom3": _round(volume / atom_count) if atom_count and volume else None,
        "volume_per_non_passivant_atom_angstrom3": _round(volume / non_passivant_atom_count)
        if non_passivant_atom_count and volume
        else None,
        "is_slab": bool(slab_vacuum),
    }

    if slab_vacuum:
        axis_length = _optional_float(slab_vacuum.get("cell_axis_length_angstrom"))
        declared_vacuum = _optional_float(slab_vacuum.get("declared_vacuum_angstrom"))
        atom_extent_vacuum = _optional_float(slab_vacuum.get("atom_extent_vacuum_angstrom"))
        summary.update(
            {
                "surface_axis": slab_vacuum.get("surface_axis"),
                "surface_axis_length_angstrom": _round(axis_length) if axis_length is not None else None,
                "declared_vacuum_angstrom": _round(declared_vacuum) if declared_vacuum is not None else None,
                "declared_vacuum_fraction": _round(declared_vacuum / axis_length)
                if declared_vacuum is not None and axis_length
                else None,
                "atom_extent_vacuum_angstrom": _round(atom_extent_vacuum) if atom_extent_vacuum is not None else None,
                "atom_extent_vacuum_fraction": _round(atom_extent_vacuum / axis_length)
                if atom_extent_vacuum is not None and axis_length
                else None,
                "bottom_vacuum_angstrom": slab_vacuum.get("bottom_vacuum_angstrom"),
                "top_vacuum_angstrom": slab_vacuum.get("top_vacuum_angstrom"),
                "vacuum_asymmetry_angstrom": slab_vacuum.get("vacuum_asymmetry_angstrom"),
                "vacuum_asymmetry_abs_angstrom": slab_vacuum.get("vacuum_asymmetry_abs_angstrom"),
                "slab_center_fractional": slab_vacuum.get("slab_center_fractional"),
                "slab_center_offset_angstrom": slab_vacuum.get("slab_center_offset_angstrom"),
                "centered_in_cell": slab_vacuum.get("centered_in_cell"),
                "vacuum_ok": slab_vacuum.get("vacuum_ok"),
                "slab_vacuum_status": slab_vacuum.get("slab_vacuum_status"),
                "slab_vacuum_next_action": slab_vacuum.get("slab_vacuum_next_action"),
                "metadata_cell_mismatch": slab_vacuum.get("metadata_cell_mismatch"),
            }
        )
    return summary


def _sublattice_balance_summary(
    element_counts: Counter[str],
    *,
    host_elements: list[str],
    dopant_elements: list[str],
    rule: str,
) -> dict[str, Any] | None:
    counts = Counter({element: count for element, count in element_counts.items() if count > 0})
    if not counts:
        return None
    total = sum(counts.values())
    cation_elements = sorted(element for element in counts if element in III_V_CATIONS)
    anion_elements = sorted(element for element in counts if element in III_V_ANIONS)
    tmd_rule = "tmd" in rule
    ii_vi_cation_elements = [] if tmd_rule else sorted(element for element in counts if element in II_VI_CATIONS)
    ii_vi_anion_elements = [] if tmd_rule else sorted(element for element in counts if element in II_VI_ANIONS)
    tmd_metal_elements = sorted(element for element in counts if element in TMD_METALS) if tmd_rule else []
    tmd_chalcogen_elements = sorted(element for element in counts if element in TMD_CHALCOGENS) if tmd_rule else []
    group_iv_elements = sorted(element for element in counts if element in GROUP_IV_SEMICONDUCTORS)
    dopant_set = set(dopant_elements)

    categories = []

    def add_category(category: str, elements: list[str]) -> int:
        count = sum(counts[element] for element in elements)
        if not elements and count == 0:
            return 0
        categories.append(
            {
                "category": category,
                "elements": elements,
                "count": count,
                "fraction_of_non_passivant": _round(count / total) if total else None,
            }
        )
        return count

    cation_count = add_category("iii_v_cation_like_elements", cation_elements)
    anion_count = add_category("iii_v_anion_like_elements", anion_elements)
    ii_vi_cation_count = add_category("ii_vi_cation_like_elements", ii_vi_cation_elements)
    ii_vi_anion_count = add_category("ii_vi_anion_like_elements", ii_vi_anion_elements)
    tmd_metal_count = add_category("tmd_metal_like_elements", tmd_metal_elements)
    tmd_chalcogen_count = add_category("tmd_chalcogen_like_elements", tmd_chalcogen_elements)
    group_iv_count = add_category("group_iv_like_elements", group_iv_elements)
    dopant_count = add_category("dopants", sorted(element for element in dopant_set if element in counts))
    other_elements = sorted(
        element
        for element in counts
        if element not in set(cation_elements)
        and element not in set(anion_elements)
        and element not in set(ii_vi_cation_elements)
        and element not in set(ii_vi_anion_elements)
        and element not in set(tmd_metal_elements)
        and element not in set(tmd_chalcogen_elements)
        and element not in set(group_iv_elements)
        and element not in dopant_set
    )
    other_count = add_category("other_non_passivant", other_elements)

    balance_kind = "generic"
    balance_delta = None
    balanced = None
    warning = False
    if tmd_metal_count and tmd_chalcogen_count and tmd_rule:
        balance_kind = "tmd_metal_chalcogen_ratio"
        balance_delta = tmd_chalcogen_count - (2 * tmd_metal_count)
        balanced = balance_delta == 0
        warning = not balanced
    elif cation_count and anion_count and ("iii_v" in rule or not group_iv_count):
        balance_kind = "iii_v_cation_anion_count"
        balance_delta = cation_count - anion_count
        balanced = balance_delta == 0
        warning = not balanced
    elif ii_vi_cation_count and ii_vi_anion_count and ("ii_vi" in rule or not group_iv_count):
        balance_kind = "ii_vi_cation_anion_count"
        balance_delta = ii_vi_cation_count - ii_vi_anion_count
        balanced = balance_delta == 0
        warning = not balanced
    elif group_iv_count and not cation_count and not anion_count:
        balance_kind = "group_iv_single_sublattice"
        balance_delta = 0
        balanced = True
    elif group_iv_count and dopant_count:
        balance_kind = "group_iv_with_dopants"
        balance_delta = 0
        balanced = True

    return {
        "available": True,
        "balance_kind": balance_kind,
        "balanced": balanced,
        "warning": warning,
        "balance_delta_count": balance_delta,
        "non_passivant_atom_count": total,
        "host_elements": host_elements,
        "dopant_elements": dopant_elements,
        "iii_v_cation_count": cation_count,
        "iii_v_anion_count": anion_count,
        "ii_vi_cation_count": ii_vi_cation_count,
        "ii_vi_anion_count": ii_vi_anion_count,
        "tmd_metal_count": tmd_metal_count,
        "tmd_chalcogen_count": tmd_chalcogen_count,
        "group_iv_count": group_iv_count,
        "dopant_count": dopant_count,
        "other_non_passivant_count": other_count,
        "categories": categories,
    }


def _composition_role(
    element: str,
    host_elements: list[str],
    dopant_elements: list[str],
    alloy_elements: set[str],
) -> str:
    if element in SURFACE_PASSIVANTS:
        return "passivant"
    if element in dopant_elements:
        return "dopant"
    if element in alloy_elements:
        return "host_or_alloy"
    if element in host_elements:
        return "host"
    return "other"


def _format_formula(counts: Counter[str] | dict[str, int], *, order: list[str] | None = None) -> str:
    parts = []
    for element in order or sorted(counts):
        count = int(counts[element])
        if count <= 0:
            continue
        parts.append(element if count == 1 else f"{element}{count}")
    return "".join(parts)


def _semiconductor_formula_order(counts: Counter[str] | dict[str, int]) -> list[str]:
    elements = {element for element, count in counts.items() if int(count) > 0}
    cations = sorted(element for element in elements if element in III_V_CATIONS)
    anions = sorted(element for element in elements if element in III_V_ANIONS)
    if cations and anions:
        ordered = [*cations, *anions]
        ordered.extend(sorted(elements - set(ordered)))
        return ordered
    ii_vi_cations = sorted(element for element in elements if element in II_VI_CATIONS)
    ii_vi_anions = sorted(element for element in elements if element in II_VI_ANIONS)
    if ii_vi_cations and ii_vi_anions:
        ordered = [*ii_vi_cations, *ii_vi_anions]
        ordered.extend(sorted(elements - set(ordered)))
        return ordered
    tmd_metals = sorted(element for element in elements if element in TMD_METALS)
    tmd_chalcogens = sorted(element for element in elements if element in TMD_CHALCOGENS)
    if tmd_metals and tmd_chalcogens:
        ordered = [*tmd_metals, *tmd_chalcogens]
        ordered.extend(sorted(elements - set(ordered)))
        return ordered
    return sorted(elements)


def _reduced_counts(counts: Counter[str]) -> dict[str, int]:
    values = [int(count) for count in counts.values() if int(count) > 0]
    if not values:
        return {}
    divisor = values[0]
    for value in values[1:]:
        divisor = math.gcd(divisor, value)
    divisor = max(divisor, 1)
    return {element: int(count / divisor) for element, count in counts.items() if count > 0}


def _alloy_summary(spec: ModelSpec, metadata: dict[str, Any]) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("applied_alloy", []) or []
        if isinstance(item, dict)
    ]
    if not entries:
        return None
    normalized_entries = []
    max_rounding = 0.0
    for entry in entries:
        rounding = abs(float(entry.get("rounding_error_fraction") or 0.0))
        max_rounding = max(max_rounding, rounding)
        normalized_entries.append(
            _fraction_site_selection_entry(
                spec,
                {
                    **entry,
                    "rounding_warning": rounding > 0.05,
                },
                expected_element_field="alloy_element",
            )
        )
    return _fraction_site_selection_summary(
        {
            "available": True,
            "entry_count": len(entries),
            "entries": normalized_entries[:MAX_HEALTH_DETAIL_ROWS],
            "latest": normalized_entries[-1],
            "max_abs_rounding_error_fraction": _round(max_rounding),
            "rounding_warning": max_rounding > 0.05,
        },
        normalized_entries,
    )


def _dopant_fraction_summary(spec: ModelSpec, metadata: dict[str, Any]) -> dict[str, Any] | None:
    entries = [
        dict(item)
        for item in metadata.get("applied_dopant_fraction", []) or []
        if isinstance(item, dict)
    ]
    if not entries:
        return None
    normalized_entries = []
    max_rounding = 0.0
    for entry in entries:
        rounding = abs(float(entry.get("rounding_error_fraction") or 0.0))
        max_rounding = max(max_rounding, rounding)
        normalized_entries.append(
            _fraction_site_selection_entry(
                spec,
                {
                    **entry,
                    "rounding_warning": rounding > 0.05,
                },
                expected_element_field="dopant_element",
            )
        )
    return _fraction_site_selection_summary(
        {
            "available": True,
            "entry_count": len(entries),
            "entries": normalized_entries[:MAX_HEALTH_DETAIL_ROWS],
            "latest": normalized_entries[-1],
            "max_abs_rounding_error_fraction": _round(max_rounding),
            "rounding_warning": max_rounding > 0.05,
        },
        normalized_entries,
    )


def _fraction_site_selection_entry(
    spec: ModelSpec,
    entry: dict[str, Any],
    *,
    expected_element_field: str,
) -> dict[str, Any]:
    receipt = entry.get("site_selection")
    strategy = entry.get("selection_strategy")
    if not isinstance(receipt, dict) or strategy != PERIODIC_MAXIMIN_STRATEGY:
        return {
            **entry,
            "selection_strategy": strategy or "atom_id_order",
        }
    if not isinstance(spec.model, CrystalSpec):
        audit = {
            "integrity_ok": False,
            "replay_verified": False,
            "geometry_unchanged": False,
            "adjacent_pair_review_required": False,
            "selected_pairs_at_candidate_nearest_distance": 0,
            "current_selected_pair_distance_stats_angstrom": {},
            "error_count": 1,
            "warning_count": 0,
            "errors": ["Periodic maximin site selection requires a crystal model."],
            "warnings": [],
        }
    else:
        audit = audit_periodic_maximin_selection(
            spec.model,
            receipt,
            expected_selected_element=str(entry.get(expected_element_field) or "") or None,
        )
    pair_stats = audit.get("current_selected_pair_distance_stats_angstrom") or {}
    candidate_stats = receipt.get("candidate_pair_distance_stats_angstrom") or {}
    pair_distribution = analyze_periodic_site_pair_distribution(receipt)
    short_range_order = analyze_periodic_site_short_range_order(receipt)
    return {
        **entry,
        "selection_strategy": PERIODIC_MAXIMIN_STRATEGY,
        "scientific_scope": receipt.get("scientific_scope"),
        "site_selection_audit": audit,
        "site_selection_integrity_ok": audit.get("integrity_ok"),
        "site_selection_replay_verified": audit.get("replay_verified"),
        "site_selection_geometry_unchanged": audit.get("geometry_unchanged"),
        "site_pair_distribution": pair_distribution,
        "site_pair_distribution_integrity_ok": pair_distribution.get("integrity_ok"),
        "site_pair_distribution_current_geometry_applicable": bool(
            pair_distribution.get("integrity_ok") and audit.get("geometry_unchanged")
        ),
        "site_pair_distribution_shell_count": pair_distribution.get("shell_count"),
        "site_pair_distribution_nearest_shell_selected_pair_count": pair_distribution.get(
            "nearest_shell_selected_pair_count"
        ),
        "site_pair_distribution_nearest_shell_baseline_pair_count": pair_distribution.get(
            "nearest_shell_baseline_pair_count"
        ),
        "site_pair_distribution_nearest_shell_pair_count_reduction": pair_distribution.get(
            "nearest_shell_pair_count_reduction_vs_atom_id_order"
        ),
        "site_pair_distribution_nearest_shell_expectation_class": pair_distribution.get(
            "nearest_shell_pair_expectation_class"
        ),
        "site_pair_distribution_nearest_shell_pair_excess_review_required": pair_distribution.get(
            "nearest_shell_pair_excess_review_required"
        ),
        "site_pair_distribution_nearest_shell_pair_avoidance_observed": pair_distribution.get(
            "nearest_shell_pair_avoidance_observed"
        ),
        "site_pair_distribution_analysis_sha256": pair_distribution.get("analysis_sha256"),
        "site_pair_distribution_error_count": pair_distribution.get("error_count"),
        "site_pair_distribution_warning_count": pair_distribution.get("warning_count"),
        "site_pair_distribution_errors": pair_distribution.get("errors") or [],
        "site_pair_distribution_warnings": pair_distribution.get("warnings") or [],
        "site_short_range_order": short_range_order,
        "site_short_range_order_integrity_ok": short_range_order.get("integrity_ok"),
        "site_short_range_order_current_geometry_applicable": bool(
            short_range_order.get("integrity_ok") and audit.get("geometry_unchanged")
        ),
        "site_short_range_order_shell_count": short_range_order.get("shell_count"),
        "site_short_range_order_nearest_shell_expectation_class": short_range_order.get(
            "nearest_shell_unlike_pair_expectation_class"
        ),
        "site_short_range_order_nearest_shell_corrected_alpha": short_range_order.get(
            "nearest_shell_finite_composition_corrected_pair_alpha"
        ),
        "site_short_range_order_nearest_shell_baseline_corrected_alpha": short_range_order.get(
            "nearest_shell_baseline_finite_composition_corrected_pair_alpha"
        ),
        "site_short_range_order_nearest_shell_ordering_like_observed": short_range_order.get(
            "nearest_shell_ordering_like_unlike_pair_enrichment"
        ),
        "site_short_range_order_nearest_shell_clustering_like_review_required": short_range_order.get(
            "nearest_shell_clustering_like_unlike_pair_depletion_review_required"
        ),
        "site_short_range_order_analysis_sha256": short_range_order.get("analysis_sha256"),
        "site_short_range_order_error_count": short_range_order.get("error_count"),
        "site_short_range_order_warning_count": short_range_order.get("warning_count"),
        "site_short_range_order_errors": short_range_order.get("errors") or [],
        "site_short_range_order_warnings": short_range_order.get("warnings") or [],
        "selected_pair_minimum_angstrom": pair_stats.get("minimum_angstrom"),
        "selected_pair_mean_angstrom": pair_stats.get("mean_angstrom"),
        "selected_pair_maximum_angstrom": pair_stats.get("maximum_angstrom"),
        "candidate_nearest_pair_distance_angstrom": candidate_stats.get("minimum_angstrom"),
        "selected_pairs_at_candidate_nearest_distance": audit.get(
            "selected_pairs_at_candidate_nearest_distance"
        ),
        "minimum_distance_improvement_over_atom_id_order_angstrom": audit.get(
            "minimum_distance_improvement_over_atom_id_order_angstrom"
        ),
        "site_selection_warning_count": audit.get("warning_count"),
        "site_selection_error_count": audit.get("error_count"),
        "site_selection_warnings": audit.get("warnings") or [],
        "site_selection_errors": audit.get("errors") or [],
    }


def _fraction_site_selection_summary(
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    distributed = [
        entry
        for entry in entries
        if entry.get("selection_strategy") == PERIODIC_MAXIMIN_STRATEGY
    ]
    if not distributed:
        return {
            **summary,
            "periodic_maximin_count": 0,
            "site_selection_integrity_ok": None,
            "site_selection_replay_verified": None,
            "site_selection_review_required": False,
            "adjacent_pair_review_required": False,
            "site_pair_distribution_count": 0,
            "site_pair_distribution_integrity_ok": None,
            "site_pair_distribution_current_geometry_applicable": None,
            "site_pair_distribution_nearest_shell_pair_excess_review_required": False,
            "site_pair_distribution_nearest_shell_pair_avoidance_observed": False,
            "site_pair_distribution_errors": [],
            "site_pair_distribution_warnings": [],
            "site_short_range_order_count": 0,
            "site_short_range_order_integrity_ok": None,
            "site_short_range_order_current_geometry_applicable": None,
            "site_short_range_order_nearest_shell_ordering_like_observed": False,
            "site_short_range_order_nearest_shell_clustering_like_review_required": False,
            "site_short_range_order_errors": [],
            "site_short_range_order_warnings": [],
            "site_selection_errors": [],
            "site_selection_warnings": [],
        }
    errors = list(
        dict.fromkeys(
            str(item)
            for entry in distributed
            for item in entry.get("site_selection_errors", []) or []
        )
    )
    warnings = list(
        dict.fromkeys(
            str(item)
            for entry in distributed
            for item in entry.get("site_selection_warnings", []) or []
        )
    )
    integrity_ok = all(entry.get("site_selection_integrity_ok") is True for entry in distributed)
    replay_verified = all(entry.get("site_selection_replay_verified") is True for entry in distributed)
    adjacent_review = any(
        bool((entry.get("site_selection_audit") or {}).get("adjacent_pair_review_required"))
        for entry in distributed
    )
    pair_distributions = [
        entry.get("site_pair_distribution")
        for entry in distributed
        if isinstance(entry.get("site_pair_distribution"), dict)
    ]
    pair_distribution_errors = list(
        dict.fromkeys(
            str(item)
            for entry in distributed
            for item in entry.get("site_pair_distribution_errors", []) or []
        )
    )
    pair_distribution_warnings = list(
        dict.fromkeys(
            str(item)
            for entry in distributed
            for item in entry.get("site_pair_distribution_warnings", []) or []
        )
    )
    pair_distribution_integrity_ok = bool(pair_distributions) and all(
        distribution.get("integrity_ok") is True for distribution in pair_distributions
    )
    pair_distribution_current_geometry_applicable = bool(pair_distributions) and all(
        entry.get("site_pair_distribution_current_geometry_applicable") is True
        for entry in distributed
    )
    pair_excess_review = any(
        bool(distribution.get("nearest_shell_pair_excess_review_required"))
        for distribution in pair_distributions
    )
    pair_avoidance_observed = any(
        bool(distribution.get("nearest_shell_pair_avoidance_observed"))
        for distribution in pair_distributions
    )
    short_range_orders = [
        entry.get("site_short_range_order")
        for entry in distributed
        if isinstance(entry.get("site_short_range_order"), dict)
    ]
    short_range_order_errors = list(
        dict.fromkeys(
            str(item)
            for entry in distributed
            for item in entry.get("site_short_range_order_errors", []) or []
        )
    )
    short_range_order_warnings = list(
        dict.fromkeys(
            str(item)
            for entry in distributed
            for item in entry.get("site_short_range_order_warnings", []) or []
        )
    )
    short_range_order_integrity_ok = bool(short_range_orders) and all(
        analysis.get("integrity_ok") is True for analysis in short_range_orders
    )
    short_range_order_current_geometry_applicable = bool(short_range_orders) and all(
        entry.get("site_short_range_order_current_geometry_applicable") is True
        for entry in distributed
    )
    short_range_order_ordering_like_observed = any(
        bool(analysis.get("nearest_shell_ordering_like_unlike_pair_enrichment"))
        for analysis in short_range_orders
    )
    short_range_order_clustering_like_review = any(
        bool(analysis.get("nearest_shell_clustering_like_unlike_pair_depletion_review_required"))
        for analysis in short_range_orders
    )
    return {
        **summary,
        "periodic_maximin_count": len(distributed),
        "site_selection_integrity_ok": integrity_ok,
        "site_selection_replay_verified": replay_verified,
        "site_selection_review_required": True,
        "adjacent_pair_review_required": adjacent_review,
        "site_selection_error_count": len(errors),
        "site_selection_warning_count": len(warnings),
        "site_selection_errors": errors,
        "site_selection_warnings": warnings,
        "site_pair_distribution_count": len(pair_distributions),
        "site_pair_distribution_integrity_ok": pair_distribution_integrity_ok,
        "site_pair_distribution_current_geometry_applicable": (
            pair_distribution_current_geometry_applicable
        ),
        "site_pair_distribution_nearest_shell_pair_excess_review_required": pair_excess_review,
        "site_pair_distribution_nearest_shell_pair_avoidance_observed": pair_avoidance_observed,
        "site_pair_distribution_error_count": len(pair_distribution_errors),
        "site_pair_distribution_warning_count": len(pair_distribution_warnings),
        "site_pair_distribution_errors": pair_distribution_errors,
        "site_pair_distribution_warnings": pair_distribution_warnings,
        "site_short_range_order_count": len(short_range_orders),
        "site_short_range_order_integrity_ok": short_range_order_integrity_ok,
        "site_short_range_order_current_geometry_applicable": (
            short_range_order_current_geometry_applicable
        ),
        "site_short_range_order_nearest_shell_ordering_like_observed": (
            short_range_order_ordering_like_observed
        ),
        "site_short_range_order_nearest_shell_clustering_like_review_required": (
            short_range_order_clustering_like_review
        ),
        "site_short_range_order_error_count": len(short_range_order_errors),
        "site_short_range_order_warning_count": len(short_range_order_warnings),
        "site_short_range_order_errors": short_range_order_errors,
        "site_short_range_order_warnings": short_range_order_warnings,
    }


def _dopant_concentration_summary(
    *,
    lattice_summary: dict[str, Any] | None,
    dopant_summary: dict[str, Any] | None,
    charge_balance_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not dopant_summary:
        return None
    lattice_summary = lattice_summary or {}
    volume_angstrom3 = _optional_float(lattice_summary.get("cell_volume_angstrom3"))
    if volume_angstrom3 is None or volume_angstrom3 <= 0:
        return None
    volume_cm3 = volume_angstrom3 * ANGSTROM3_TO_CM3
    if volume_cm3 <= 0:
        return None

    charge_balance_summary = charge_balance_summary or {}
    dopants = []
    for dopant in dopant_summary.get("dopants", []) or []:
        count = _optional_float(dopant.get("count")) or 0.0
        density = count / volume_cm3 if count > 0 else 0.0
        role_hint = str(dopant.get("role_hint") or "")
        carrier_multiplier = _dopant_role_carrier_multiplier(role_hint)
        carrier_density_signed = (
            _round_significant(density * carrier_multiplier)
            if carrier_multiplier is not None and density is not None
            else None
        )
        warning_level = _dopant_concentration_warning_level(density)
        dopants.append(
            {
                "element": dopant.get("element"),
                "count": dopant.get("count"),
                "atom_ids": dopant.get("atom_ids") or [],
                "concentration_fraction": dopant.get("concentration_fraction"),
                "concentration_percent": dopant.get("concentration_percent"),
                "density_cm3": _round_significant(density),
                "density_log10_cm3": _round(math.log10(density)) if density > 0 else None,
                "carrier_density_cm3_signed": carrier_density_signed,
                "carrier_density_cm3_abs": abs(carrier_density_signed)
                if carrier_density_signed is not None
                else None,
                "carrier_type_hint": _carrier_hint_from_signed_density(carrier_density_signed),
                "role_hint": dopant.get("role_hint"),
                "concentration_warning_level": warning_level,
                "high_concentration_warning": warning_level in {"high", "very_high"},
                "degenerate_doping_review_required": warning_level == "very_high",
                "next_action": _dopant_concentration_next_action(warning_level),
            }
        )

    total_count = _optional_float(dopant_summary.get("total_dopant_count")) or 0.0
    total_density = total_count / volume_cm3 if total_count > 0 else 0.0
    net_delta = _optional_float(charge_balance_summary.get("nominal_dopant_delta_electrons"))
    net_density_signed = _round_significant(net_delta / volume_cm3) if net_delta is not None else None
    warning_level = _dopant_concentration_warning_level(total_density)
    return {
        "available": True,
        "model": "periodic_supercell_equivalent_dopant_concentration",
        "assumption": "periodic_supercell_one_cell_volume; not an experimental activation estimate",
        "cell_volume_angstrom3": _round(volume_angstrom3),
        "cell_volume_cm3": _round_significant(volume_cm3),
        "total_dopant_count": int(total_count) if float(total_count).is_integer() else _round(total_count),
        "total_dopant_fraction": dopant_summary.get("total_dopant_fraction"),
        "total_dopant_percent": _round(100.0 * total_count / float(dopant_summary.get("total_non_passivant_atom_count")))
        if total_count and dopant_summary.get("total_non_passivant_atom_count")
        else None,
        "total_dopant_density_cm3": _round_significant(total_density),
        "total_dopant_density_log10_cm3": _round(math.log10(total_density)) if total_density > 0 else None,
        "net_nominal_carrier_delta_electrons_per_cell": charge_balance_summary.get("nominal_dopant_delta_electrons"),
        "net_nominal_carrier_density_cm3_signed": net_density_signed,
        "net_nominal_carrier_density_cm3_abs": abs(net_density_signed) if net_density_signed is not None else None,
        "carrier_type_hint": charge_balance_summary.get("carrier_type_hint"),
        "concentration_warning_level": warning_level,
        "high_concentration_warning": warning_level in {"high", "very_high"},
        "degenerate_doping_review_required": warning_level == "very_high",
        "next_action": _dopant_concentration_next_action(warning_level),
        "dopants": dopants,
    }


def _dopant_role_carrier_multiplier(role_hint: str) -> float | None:
    if "donor_like" in role_hint:
        return 1.0
    if "acceptor_like" in role_hint:
        return -1.0
    if "isovalent" in role_hint:
        return 0.0
    return None


def _carrier_hint_from_signed_density(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0:
        return "donor_like_n_type"
    if value < 0:
        return "acceptor_like_p_type"
    return "neutral_or_intrinsic"


def _dopant_concentration_warning_level(density_cm3: float | None) -> str:
    if density_cm3 is None or density_cm3 <= 0:
        return "none"
    if density_cm3 >= 1.0e21:
        return "very_high"
    if density_cm3 >= 1.0e20:
        return "high"
    if density_cm3 >= 1.0e19:
        return "elevated"
    return "typical_or_low"


def _dopant_concentration_next_action(warning_level: str) -> str:
    if warning_level == "very_high":
        return "increase_supercell_or_reduce_dopant_count_before_quantitative_semiconductor_claims"
    if warning_level == "high":
        return "review_supercell_dopant_concentration_before_quantitative_semiconductor_claims"
    if warning_level == "elevated":
        return "confirm_intended_heavy_doping_regime_before_calculation"
    return "dopant_concentration_preflight_passed"


def _dopant_summary(
    *,
    host_elements: list[str],
    dopant_elements: list[str],
    element_counts: Counter[str],
    coordination_rows: list[dict[str, Any]],
    expected_coordination: int | None,
    expected_coordination_by_element: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    if not host_elements or not dopant_elements:
        return None
    total_non_passivant = sum(element_counts.values())
    dopants = []
    for dopant in dopant_elements:
        rows = [row for row in coordination_rows if row.get("element") == dopant]
        counts = [float(row.get("neighbor_count") or 0) for row in rows]
        dopant_expected_coordination = _expected_coordination_for_element(
            dopant,
            expected_coordination,
            expected_coordination_by_element,
        )
        outliers = []
        if dopant_expected_coordination is not None:
            outliers = [
                {
                    "atom_id": row.get("atom_id"),
                    "neighbor_count": row.get("neighbor_count"),
                    "expected_coordination": dopant_expected_coordination,
                    "neighbor_ids": row.get("neighbor_ids") or [],
                    "neighbor_elements": row.get("neighbor_elements") or [],
                }
                for row in rows
                if int(row.get("neighbor_count") or 0) != dopant_expected_coordination
            ]
        dopants.append(
            {
                "element": dopant,
                "count": element_counts[dopant],
                "atom_ids": [str(row.get("atom_id")) for row in rows],
                "concentration_fraction": _round(element_counts[dopant] / total_non_passivant)
                if total_non_passivant
                else None,
                "concentration_percent": _round(100.0 * element_counts[dopant] / total_non_passivant)
                if total_non_passivant
                else None,
                "role_hint": _dopant_role_hint(host_elements, dopant),
                "coordination_stats": _stats_with_count(counts),
                "coordination_outlier_count": len(outliers),
                "coordination_outliers": outliers[:MAX_HEALTH_DETAIL_ROWS],
                "neighbor_element_counts": dict(
                    sorted(
                        Counter(
                            str(element)
                            for row in rows
                            for element in (row.get("neighbor_elements") or [])
                            if element
                        ).items()
                    )
                ),
            }
        )
    return {
        "available": True,
        "host_elements": host_elements,
        "dopant_elements": dopant_elements,
        "total_non_passivant_atom_count": total_non_passivant,
        "total_dopant_count": sum(element_counts[element] for element in dopant_elements),
        "total_dopant_fraction": _round(
            sum(element_counts[element] for element in dopant_elements) / total_non_passivant
        )
        if total_non_passivant
        else None,
        "dopants": dopants,
    }


def _dopant_role_hint(host_elements: list[str], dopant: str) -> str:
    if set(host_elements) <= GROUP_IV_SEMICONDUCTORS:
        if dopant in III_V_ANIONS:
            return "donor_like_n_type_for_group_iv_host"
        if dopant in III_V_CATIONS:
            return "acceptor_like_p_type_for_group_iv_host"
        if dopant in GROUP_IV_SEMICONDUCTORS:
            return "isovalent_group_iv_alloy"
    return "unknown_or_site_dependent"


def _is_oxide_defect_context(
    metadata: dict[str, Any],
    element_counts: Counter[str],
    site_elements: set[str],
) -> bool:
    material_elements = _metadata_material_elements(metadata)
    elements = material_elements | set(element_counts) | {element for element in site_elements if element}
    if "O" not in elements:
        return False
    if metadata.get("oxide_semiconductor") or metadata.get("oxide_material"):
        return True
    material = str(metadata.get("material") or metadata.get("formula") or "").lower()
    family = str(metadata.get("structure_family") or "").lower()
    domain = str(metadata.get("domain") or "").lower()
    return bool(
        domain == "semiconductor"
        and (
            "oxide" in family
            or "oxide" in material
            or any(element in elements for element in {"Zn", "Ga", "In", "Sn", "Ti", "Hf"})
        )
    )


def _oxide_defect_site_cations(
    metadata: dict[str, Any],
    element_counts: Counter[str],
    site_elements: set[str],
) -> set[str]:
    material_elements = _metadata_material_elements(metadata)
    elements = material_elements | set(element_counts) | {element for element in site_elements if element}
    return {element for element in elements if element not in {"O", "H"}}


def _defect_role_hint(
    defect_type: str,
    site_element: str | None,
    new_element: str | None = None,
    *,
    tmd_context: bool = False,
    oxide_context: bool = False,
    oxide_cations: set[str] | None = None,
    halide_perovskite_context: bool = False,
) -> str:
    if not site_element:
        return "unknown_defect_role"
    defect_type = defect_type.lower()
    oxide_cations = oxide_cations or set()
    if defect_type == "vacancy":
        if halide_perovskite_context and site_element in HALIDE_PEROVSKITE_HALIDES:
            return "donor_like_n_type_halide_perovskite_halide_vacancy"
        if halide_perovskite_context and site_element in HALIDE_PEROVSKITE_B_CATIONS:
            return "acceptor_like_p_type_halide_perovskite_b_site_vacancy"
        if halide_perovskite_context and site_element in {"C", "N"}:
            return "organic_cation_vacancy_review_required"
        if oxide_context and site_element == "O":
            return "donor_like_n_type_oxygen_vacancy"
        if oxide_context and site_element in oxide_cations:
            return "acceptor_like_p_type_oxide_cation_vacancy"
        if tmd_context and site_element in TMD_CHALCOGENS:
            return "donor_like_n_type_tmd_chalcogen_vacancy"
        if tmd_context and site_element in TMD_METALS:
            return "acceptor_like_p_type_tmd_metal_vacancy"
        if site_element in III_V_ANIONS or site_element in II_VI_ANIONS:
            return "donor_like_n_type_anion_vacancy"
        if site_element in III_V_CATIONS or site_element in II_VI_CATIONS:
            return "acceptor_like_p_type_cation_vacancy"
        if site_element in GROUP_IV_SEMICONDUCTORS:
            return "dangling_bond_group_iv_vacancy"
        return "unknown_vacancy_role"
    if defect_type == "interstitial":
        if halide_perovskite_context and site_element in HALIDE_PEROVSKITE_HALIDES:
            return "halide_interstitial_review_required"
        if halide_perovskite_context and site_element in HALIDE_PEROVSKITE_B_CATIONS:
            return "halide_perovskite_b_cation_interstitial_review_required"
        if halide_perovskite_context and site_element in {"C", "N"}:
            return "organic_cation_interstitial_review_required"
        if oxide_context and site_element == "O":
            return "oxygen_interstitial_review_required"
        if oxide_context and site_element in oxide_cations:
            return "oxide_cation_interstitial_review_required"
        return "interstitial_review_required"
    if defect_type == "antisite":
        return _antisite_role_hint(site_element, new_element or "")
    return "unknown_defect_role"


def _carrier_hint_from_defect_role(role_hint: str) -> str:
    if role_hint.startswith("donor_like"):
        return "donor_like_n_type"
    if role_hint.startswith("acceptor_like"):
        return "acceptor_like_p_type"
    if role_hint.startswith("isovalent"):
        return "neutral_or_intrinsic"
    return "unknown_site_dependent"


def _defect_summary(
    spec: ModelSpec,
    metadata: dict[str, Any],
    atom_rows: list[dict[str, Any]],
    coordination_rows: list[dict[str, Any]],
    element_counts: Counter[str],
    *,
    expected_coordination: int | None,
    expected_coordination_by_element: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    defects = [dict(item) for item in metadata.get("defects", []) or [] if isinstance(item, dict)]
    vacancy_inputs = [item for item in defects if str(item.get("type") or "").lower() == "vacancy"]
    interstitial_inputs = [item for item in defects if str(item.get("type") or "").lower() == "interstitial"]
    antisite_inputs = [item for item in defects if str(item.get("type") or "").lower() == "antisite"]
    complex_inputs = [
        dict(item)
        for item in metadata.get("defect_complexes", []) or []
        if isinstance(item, dict)
    ]
    dopant_site_inputs = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    if not vacancy_inputs and not interstitial_inputs and not antisite_inputs and not complex_inputs:
        return None

    vectors = _lattice_vectors(spec.model.lattice) if isinstance(spec.model, CrystalSpec) else None
    coord_map = {str(row.get("atom_id")): row for row in coordination_rows}
    total_sites = sum(element_counts.values()) - len(interstitial_inputs) + len(vacancy_inputs)
    tmd_context = _is_tmd_metadata_context(metadata)
    defect_site_elements = {
        str(item.get("site_element") or item.get("element") or item.get("original_element") or "")
        for item in defects
        if isinstance(item, dict)
    }
    oxide_context = _is_oxide_defect_context(metadata, element_counts, defect_site_elements)
    oxide_cations = _oxide_defect_site_cations(metadata, element_counts, defect_site_elements) if oxide_context else set()
    halide_context_elements = set(element_counts) | {element for element in defect_site_elements if element}
    halide_perovskite_context = _is_halide_perovskite_context(metadata, sorted(halide_context_elements))
    role_counts: Counter[str] = Counter()
    carrier_counts: Counter[str] = Counter()
    site_family_counts: Counter[str] = Counter()
    vacancy_rows = []
    for vacancy in vacancy_inputs:
        site_id = str(vacancy.get("site_id") or vacancy.get("atom_id") or "")
        site_element = str(vacancy.get("site_element") or vacancy.get("element") or "")
        fractional = _coerce_fractional(vacancy.get("fractional"))
        site_family = _semiconductor_site_family(
            site_element or None,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        role_hint = _defect_role_hint(
            "vacancy",
            site_element or None,
            None,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        carrier_hint = _carrier_hint_from_defect_role(role_hint)
        role_counts[role_hint] += 1
        carrier_counts[carrier_hint] += 1
        site_family_counts[site_family] += 1
        site_expected_coordination = _expected_coordination_for_element(
            site_element,
            expected_coordination,
            expected_coordination_by_element,
        )
        neighbors = _defect_neighbor_rows(
            fractional=fractional,
            element=site_element,
            atom_rows=atom_rows,
            coord_map=coord_map,
            vectors=vectors,
            expected_coordination=site_expected_coordination,
            expected_coordination_by_element=expected_coordination_by_element,
        )
        undercoordinated = [item for item in neighbors if int(item.get("missing_coordination") or 0) > 0]
        vacancy_rows.append(
            {
                "type": "vacancy",
                "site_id": site_id or None,
                "site_element": site_element or None,
                "site_family": site_family,
                "fractional": list(fractional) if fractional is not None else None,
                "source": vacancy.get("source"),
                "expected_neighbor_count": site_expected_coordination,
                "nearest_neighbor_count": len(neighbors),
                "nearest_neighbor_ids": [str(item.get("atom_id")) for item in neighbors],
                "nearest_neighbor_elements": [str(item.get("element")) for item in neighbors],
                "nearest_neighbors": neighbors[:MAX_HEALTH_DETAIL_ROWS],
                "undercoordinated_neighbor_count": len(undercoordinated),
                "undercoordinated_neighbor_ids": [str(item.get("atom_id")) for item in undercoordinated],
                "missing_neighbor_bond_estimate": sum(int(item.get("missing_coordination") or 0) for item in undercoordinated),
                "role_hint": role_hint,
                "carrier_type_hint": carrier_hint,
                "auto_selected_site": bool(vacancy.get("auto_selected_site")),
                "complex_id": vacancy.get("complex_id"),
                "complex_type": vacancy.get("complex_type"),
                "pair_site_id": vacancy.get("pair_site_id"),
                "pair_distance_angstrom": _optional_float(vacancy.get("pair_distance_angstrom")),
                "nearest_neighbor_verified": bool(vacancy.get("nearest_neighbor_verified")),
                "concentration_fraction": _round(1.0 / total_sites) if total_sites else None,
                "concentration_percent": _round(100.0 / total_sites) if total_sites else None,
            }
        )

    interstitial_rows = []
    for interstitial in interstitial_inputs:
        atom_id = str(interstitial.get("atom_id") or interstitial.get("site_id") or "")
        element = str(interstitial.get("element") or interstitial.get("site_element") or "")
        atom_row = next((atom for atom in atom_rows if str(atom.get("id")) == atom_id), None)
        fractional = _coerce_fractional(interstitial.get("fractional")) or _coerce_fractional((atom_row or {}).get("fractional"))
        coord = coord_map.get(atom_id, {})
        neighbor_count = int(coord.get("neighbor_count") or 0)
        site_family = _semiconductor_site_family(
            element or None,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        role_hint = _defect_role_hint(
            "interstitial",
            element or None,
            None,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        carrier_hint = _carrier_hint_from_defect_role(role_hint)
        role_counts[role_hint] += 1
        carrier_counts[carrier_hint] += 1
        site_family_counts[site_family] += 1
        atom_expected_coordination = _expected_coordination_for_element(
            element,
            expected_coordination,
            expected_coordination_by_element,
        )
        missing = max((atom_expected_coordination or neighbor_count) - neighbor_count, 0)
        coordination_outlier = atom_expected_coordination is not None and neighbor_count != atom_expected_coordination
        neighbors = _defect_neighbor_rows(
            fractional=fractional,
            element=element,
            atom_rows=atom_rows,
            coord_map=coord_map,
            vectors=vectors,
            expected_coordination=atom_expected_coordination,
            expected_coordination_by_element=expected_coordination_by_element,
            exclude_atom_id=atom_id or None,
        )
        undercoordinated = [item for item in neighbors if int(item.get("missing_coordination") or 0) > 0]
        interstitial_rows.append(
            {
                "type": "interstitial",
                "site_id": atom_id or None,
                "site_element": element or None,
                "atom_id": atom_id or None,
                "element": element or None,
                "site_family": site_family,
                "fractional": list(fractional) if fractional is not None else None,
                "source": interstitial.get("source"),
                "expected_neighbor_count": atom_expected_coordination,
                "nearest_neighbor_count": len(neighbors),
                "nearest_neighbor_ids": [str(item.get("atom_id")) for item in neighbors],
                "nearest_neighbor_elements": [str(item.get("element")) for item in neighbors],
                "nearest_neighbors": neighbors[:MAX_HEALTH_DETAIL_ROWS],
                "interstitial_neighbor_count": neighbor_count,
                "coordination_outlier": coordination_outlier,
                "undercoordinated_neighbor_count": len(undercoordinated),
                "undercoordinated_neighbor_ids": [str(item.get("atom_id")) for item in undercoordinated],
                "missing_neighbor_bond_estimate": missing + sum(int(item.get("missing_coordination") or 0) for item in undercoordinated),
                "role_hint": role_hint,
                "carrier_type_hint": carrier_hint,
                "auto_selected_site": bool(interstitial.get("auto_selected_site")),
                "concentration_fraction": _round(1.0 / total_sites) if total_sites else None,
                "concentration_percent": _round(100.0 / total_sites) if total_sites else None,
            }
        )

    antisite_rows = []
    for antisite in antisite_inputs:
        atom_id = str(antisite.get("atom_id") or antisite.get("site_id") or "")
        original_element = str(antisite.get("original_element") or antisite.get("site_element") or "")
        new_element = str(antisite.get("new_element") or antisite.get("element") or "")
        atom_row = next((atom for atom in atom_rows if str(atom.get("id")) == atom_id), None)
        fractional = _coerce_fractional(antisite.get("fractional")) or _coerce_fractional((atom_row or {}).get("fractional"))
        coord = coord_map.get(atom_id, {})
        neighbor_count = int(coord.get("neighbor_count") or 0)
        site_family = _semiconductor_site_family(
            original_element or None,
            tmd_context=tmd_context,
            oxide_context=oxide_context,
            oxide_cations=oxide_cations,
            halide_perovskite_context=halide_perovskite_context,
        )
        role_hint = _antisite_role_hint(original_element, new_element)
        carrier_hint = _carrier_hint_from_defect_role(role_hint)
        role_counts[role_hint] += 1
        carrier_counts[carrier_hint] += 1
        site_family_counts[site_family] += 1
        atom_expected_coordination = _expected_coordination_for_element(
            new_element,
            expected_coordination,
            expected_coordination_by_element,
        )
        missing = max((atom_expected_coordination or neighbor_count) - neighbor_count, 0)
        coordination_outlier = atom_expected_coordination is not None and neighbor_count != atom_expected_coordination
        neighbors = _defect_neighbor_rows(
            fractional=fractional,
            element=new_element,
            atom_rows=atom_rows,
            coord_map=coord_map,
            vectors=vectors,
            expected_coordination=atom_expected_coordination,
            expected_coordination_by_element=expected_coordination_by_element,
            exclude_atom_id=atom_id or None,
        )
        same_sublattice_neighbors = [
            item for item in neighbors if str(item.get("element") or "") == new_element
        ]
        antisite_rows.append(
            {
                "type": "antisite",
                "site_id": atom_id or None,
                "site_element": original_element or None,
                "atom_id": atom_id or None,
                "element": new_element or None,
                "original_element": original_element or None,
                "new_element": new_element or None,
                "site_family": site_family,
                "fractional": list(fractional) if fractional is not None else None,
                "source": antisite.get("source"),
                "expected_neighbor_count": atom_expected_coordination,
                "nearest_neighbor_count": len(neighbors),
                "nearest_neighbor_ids": [str(item.get("atom_id")) for item in neighbors],
                "nearest_neighbor_elements": [str(item.get("element")) for item in neighbors],
                "nearest_neighbors": neighbors[:MAX_HEALTH_DETAIL_ROWS],
                "antisite_neighbor_count": neighbor_count,
                "coordination_outlier": coordination_outlier,
                "same_sublattice_neighbor_count": len(same_sublattice_neighbors),
                "same_sublattice_neighbor_ids": [str(item.get("atom_id")) for item in same_sublattice_neighbors],
                "undercoordinated_neighbor_count": 1 if missing else 0,
                "undercoordinated_neighbor_ids": [atom_id] if missing else [],
                "missing_neighbor_bond_estimate": missing,
                "role_hint": role_hint,
                "carrier_type_hint": carrier_hint,
                "auto_selected_site": bool(antisite.get("auto_selected_site")),
                "concentration_fraction": _round(1.0 / total_sites) if total_sites else None,
                "concentration_percent": _round(100.0 / total_sites) if total_sites else None,
            }
        )

    defect_rows = vacancy_rows + interstitial_rows + antisite_rows
    complex_rows = _defect_complex_rows(
        spec,
        complex_inputs,
        vacancy_inputs,
        dopant_site_inputs,
    )
    complex_integrity_errors = [
        str(error)
        for complex_row in complex_rows
        for error in complex_row.get("integrity_errors", []) or []
    ]
    context = {
        "tmd_context": tmd_context,
        "oxide_context": oxide_context,
        "oxide_cations": sorted(oxide_cations),
    }
    if halide_perovskite_context:
        context.update(
            {
                "halide_perovskite_context": True,
                "halide_perovskite_b_cations": sorted(halide_context_elements & HALIDE_PEROVSKITE_B_CATIONS),
                "halide_perovskite_halides": sorted(halide_context_elements & HALIDE_PEROVSKITE_HALIDES),
            }
        )

    return {
        "available": True,
        "defect_count": len(defects),
        "vacancy_count": len(vacancy_rows),
        "interstitial_count": len(interstitial_rows),
        "antisite_count": len(antisite_rows),
        "complex_count": len(complex_rows),
        "divacancy_count": sum(1 for item in complex_rows if str(item.get("type") or "").lower() == "divacancy"),
        "nitrogen_vacancy_count": sum(
            1
            for item in complex_rows
            if str(item.get("type") or "").lower() == "nitrogen_vacancy"
        ),
        "defect_charge_state_unresolved_count": sum(
            1
            for item in complex_rows
            if item.get("charge_state_explicit") is False
        ),
        "defect_charge_spin_backend_unbound_count": sum(
            1
            for item in complex_rows
            if item.get("charge_spin_backend_binding_ready") is False
        ),
        "defect_complex_integrity_ok": not complex_integrity_errors,
        "defect_complex_integrity_errors": complex_integrity_errors,
        "total_lattice_site_count_estimate": total_sites,
        "total_defect_fraction": _round(len(defect_rows) / total_sites) if total_sites else None,
        "total_defect_percent": _round(100.0 * len(defect_rows) / total_sites) if total_sites else None,
        "total_vacancy_fraction": _round(len(vacancy_rows) / total_sites) if total_sites else None,
        "total_vacancy_percent": _round(100.0 * len(vacancy_rows) / total_sites) if total_sites else None,
        "total_interstitial_fraction": _round(len(interstitial_rows) / total_sites) if total_sites else None,
        "total_interstitial_percent": _round(100.0 * len(interstitial_rows) / total_sites) if total_sites else None,
        "total_antisite_fraction": _round(len(antisite_rows) / total_sites) if total_sites else None,
        "total_antisite_percent": _round(100.0 * len(antisite_rows) / total_sites) if total_sites else None,
        "carrier_type_hint": _summarize_site_carrier_hint(carrier_counts),
        "role_counts": dict(sorted(role_counts.items())),
        "carrier_type_counts": dict(sorted(carrier_counts.items())),
        "site_family_counts": dict(sorted(site_family_counts.items())),
        "donor_like_count": carrier_counts.get("donor_like_n_type", 0),
        "acceptor_like_count": carrier_counts.get("acceptor_like_p_type", 0),
        "neutral_or_intrinsic_count": carrier_counts.get("neutral_or_intrinsic", 0),
        "unknown_count": carrier_counts.get("unknown_site_dependent", 0),
        "context": context,
        "defects": defect_rows,
        "complexes": complex_rows,
    }


def _defect_complex_rows(
    spec: ModelSpec,
    complex_inputs: list[dict[str, Any]],
    vacancy_inputs: list[dict[str, Any]],
    dopant_site_inputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    vectors = _lattice_vectors(spec.model.lattice) if isinstance(spec.model, CrystalSpec) else None
    complex_id_counts = Counter(
        str(item.get("complex_id") or item.get("id") or "")
        for item in complex_inputs
    )
    rows: list[dict[str, Any]] = []
    for index, complex_input in enumerate(complex_inputs, start=1):
        complex_id = str(complex_input.get("complex_id") or complex_input.get("id") or "")
        complex_type = str(complex_input.get("type") or "").lower()
        member_site_ids = [str(item) for item in complex_input.get("member_site_ids", []) or []]
        member_site_elements = [str(item) for item in complex_input.get("member_site_elements", []) or []]
        raw_fractionals = list(complex_input.get("member_fractionals", []) or [])
        member_fractionals = []
        for item in raw_fractionals:
            fractional = _coerce_fractional(item)
            member_fractionals.append(
                fractional
                if fractional is not None and all(math.isfinite(value) for value in fractional)
                else None
            )
        errors: list[str] = []
        label = complex_id or f"defect_complex[{index}]"

        if complex_type == "nitrogen_vacancy":
            rows.append(
                _nitrogen_vacancy_complex_row(
                    spec,
                    complex_input,
                    vacancy_inputs,
                    dopant_site_inputs,
                    complex_id_count=complex_id_counts[complex_id],
                    fallback_label=label,
                )
            )
            continue

        if not complex_id:
            errors.append(f"{label} is missing complex_id.")
        elif complex_id_counts[complex_id] != 1:
            errors.append(f"Defect complex id {complex_id} is duplicated in metadata.")
        if complex_type != "divacancy":
            errors.append(f"{label} has unsupported complex type {complex_type or 'missing'}.")
        if len(member_site_ids) != 2 or len(set(member_site_ids)) != 2:
            errors.append(f"{label} must reference exactly two distinct vacancy site ids.")
        if len(member_site_elements) != 2:
            errors.append(f"{label} must record exactly two member site elements.")
        if len(member_fractionals) != 2 or any(item is None for item in member_fractionals):
            errors.append(f"{label} must record two valid fractional-coordinate triples.")

        member_vacancies = [
            item
            for item in vacancy_inputs
            if str(item.get("complex_id") or "") == complex_id
        ]
        if len(member_vacancies) != 2:
            errors.append(
                f"{label} must bind exactly two vacancy records; found {len(member_vacancies)}."
            )
        for member_index, member_site_id in enumerate(member_site_ids[:2]):
            matching = [
                item
                for item in member_vacancies
                if str(item.get("site_id") or item.get("atom_id") or "") == member_site_id
            ]
            if len(matching) != 1:
                errors.append(
                    f"{label} member {member_site_id} does not bind exactly one vacancy record."
                )
                continue
            vacancy = matching[0]
            if member_index < len(member_site_elements):
                vacancy_element = str(vacancy.get("site_element") or vacancy.get("element") or "")
                if vacancy_element != member_site_elements[member_index]:
                    errors.append(
                        f"{label} member {member_site_id} element differs from its vacancy record."
                    )
            vacancy_fractional = _coerce_fractional(vacancy.get("fractional"))
            expected_fractional = member_fractionals[member_index] if member_index < len(member_fractionals) else None
            if vacancy_fractional is None or expected_fractional is None or any(
                abs(vacancy_fractional[axis] - expected_fractional[axis]) > 1e-6
                for axis in range(3)
            ):
                errors.append(
                    f"{label} member {member_site_id} fractional coordinates differ from its vacancy record."
                )
            expected_pair_id = member_site_ids[1 - member_index] if len(member_site_ids) == 2 else None
            if expected_pair_id and str(vacancy.get("pair_site_id") or "") != expected_pair_id:
                errors.append(
                    f"{label} member {member_site_id} has an inconsistent pair_site_id."
                )

        recorded_distance = _optional_float(complex_input.get("pair_distance_angstrom"))
        if recorded_distance is not None and not math.isfinite(recorded_distance):
            errors.append(f"{label} pair_distance_angstrom must be finite.")
            recorded_distance = None
        recomputed_distance = None
        image_offset = None
        threshold = None
        nearest_neighbor_recomputed = False
        if (
            vectors is not None
            and len(member_fractionals) == 2
            and member_fractionals[0] is not None
            and member_fractionals[1] is not None
            and len(member_site_elements) == 2
        ):
            recomputed_distance, image_offset = _minimum_image_distance(
                member_fractionals[0],
                member_fractionals[1],
                vectors,
            )
            threshold = _crystal_neighbor_threshold(member_site_elements[0], member_site_elements[1])
            nearest_neighbor_recomputed = recomputed_distance <= threshold + 1e-6
        if recorded_distance is None:
            errors.append(f"{label} is missing pair_distance_angstrom.")
        if recomputed_distance is None:
            errors.append(f"{label} pair distance could not be recomputed from current lattice metadata.")
        distance_delta = (
            abs(recorded_distance - recomputed_distance)
            if recorded_distance is not None and recomputed_distance is not None
            else None
        )
        if distance_delta is not None and distance_delta > 1e-5:
            errors.append(f"{label} recorded pair distance does not match the current lattice.")
        threshold_claim = _optional_float(complex_input.get("nearest_neighbor_threshold_angstrom"))
        if threshold_claim is not None and not math.isfinite(threshold_claim):
            errors.append(f"{label} nearest-neighbor threshold must be finite.")
            threshold_claim = None
        if threshold is not None and (
            threshold_claim is None or abs(threshold_claim - threshold) > 1e-5
        ):
            errors.append(f"{label} nearest-neighbor threshold metadata is inconsistent.")
        if not nearest_neighbor_recomputed:
            errors.append(f"{label} members are not a nearest-neighbor pair under the current distance rule.")
        nearest_neighbor_metadata_claim = complex_input.get("nearest_neighbor_verified") is True
        if not nearest_neighbor_metadata_claim:
            errors.append(f"{label} does not carry a positive nearest-neighbor verification claim.")
        periodic_minimum_image = complex_input.get("periodic_minimum_image") is True
        if not periodic_minimum_image:
            errors.append(f"{label} is not bound to periodic minimum-image distance semantics.")

        metadata_consistent = not errors
        rows.append(
            {
                "complex_id": complex_id or None,
                "type": complex_type or None,
                "member_site_ids": member_site_ids,
                "member_site_elements": member_site_elements,
                "member_fractionals": [list(item) if item is not None else None for item in member_fractionals],
                "member_count": len(member_site_ids),
                "member_vacancy_record_count": len(member_vacancies),
                "pair_distance_angstrom_recorded": _round(recorded_distance) if recorded_distance is not None else None,
                "pair_distance_angstrom_recomputed": _round(recomputed_distance) if recomputed_distance is not None else None,
                "distance_delta_angstrom": _round(distance_delta) if distance_delta is not None else None,
                "nearest_neighbor_threshold_angstrom": _round(threshold) if threshold is not None else None,
                "nearest_neighbor_metadata_claim": nearest_neighbor_metadata_claim,
                "nearest_neighbor_recomputed": nearest_neighbor_recomputed,
                "nearest_neighbor_verified": bool(metadata_consistent and nearest_neighbor_recomputed),
                "periodic_minimum_image": periodic_minimum_image,
                "image_offset": list(image_offset) if image_offset is not None else None,
                "selection": complex_input.get("selection"),
                "selection_rule": complex_input.get("selection_rule"),
                "metadata_consistent": metadata_consistent,
                "integrity_errors": errors,
                "source": complex_input.get("source"),
            }
        )
    return rows


def _nitrogen_vacancy_complex_row(
    spec: ModelSpec,
    complex_input: dict[str, Any],
    vacancy_inputs: list[dict[str, Any]],
    dopant_site_inputs: list[dict[str, Any]],
    *,
    complex_id_count: int,
    fallback_label: str,
) -> dict[str, Any]:
    """Recompute and verify one substitutional-N plus C-vacancy complex."""

    complex_id = str(complex_input.get("complex_id") or complex_input.get("id") or "")
    label = complex_id or fallback_label
    member_site_ids = [
        str(item) for item in complex_input.get("member_site_ids", []) or []
    ]
    member_site_elements = [
        str(item)
        for item in complex_input.get("member_site_elements", []) or []
    ]
    member_fractionals = [
        _coerce_fractional(item)
        for item in complex_input.get("member_fractionals", []) or []
    ]
    substitution_site_id = str(complex_input.get("substitution_site_id") or "")
    substitution_host_element = str(
        complex_input.get("substitution_host_element") or ""
    )
    substitution_element = str(complex_input.get("substitution_element") or "")
    vacancy_site_id = str(complex_input.get("vacancy_site_id") or "")
    vacancy_site_element = str(complex_input.get("vacancy_site_element") or "")
    errors: list[str] = []

    if not complex_id:
        errors.append(f"{label} is missing complex_id.")
    elif complex_id_count != 1:
        errors.append(f"Defect complex id {complex_id} is duplicated in metadata.")
    if len(member_site_ids) != 2 or len(set(member_site_ids)) != 2:
        errors.append(
            f"{label} must reference one substitution site and one distinct vacancy site."
        )
    if len(member_site_elements) != 2:
        errors.append(f"{label} must record two member site elements.")
    if len(member_fractionals) != 2 or any(
        item is None for item in member_fractionals
    ):
        errors.append(
            f"{label} must record two valid fractional-coordinate triples."
        )
    if (
        member_site_ids[:2]
        != [substitution_site_id, vacancy_site_id]
    ):
        errors.append(
            f"{label} member order must be substitution site then vacancy site."
        )
    if member_site_elements[:2] != [
        substitution_element,
        vacancy_site_element,
    ]:
        errors.append(
            f"{label} member elements do not match the substitution/vacancy fields."
        )
    if substitution_host_element != "C" or substitution_element != "N":
        errors.append(
            f"{label} must bind a substitutional N on an original C site."
        )
    if vacancy_site_element != "C":
        errors.append(f"{label} vacancy member must be an original C site.")

    atoms_by_id = (
        {atom.id: atom for atom in spec.model.basis_atoms}
        if isinstance(spec.model, CrystalSpec)
        else {}
    )
    substitution_atom = atoms_by_id.get(substitution_site_id)
    if substitution_atom is None:
        errors.append(
            f"{label} substitution site {substitution_site_id or 'missing'} "
            "is absent from the current structure."
        )
    elif substitution_atom.element != "N":
        errors.append(
            f"{label} substitution site {substitution_site_id} is not N "
            "in the current structure."
        )
    if vacancy_site_id and vacancy_site_id in atoms_by_id:
        errors.append(
            f"{label} vacancy site {vacancy_site_id} still exists in the "
            "current structure."
        )

    member_dopants = [
        item
        for item in dopant_site_inputs
        if str(item.get("complex_id") or "") == complex_id
    ]
    member_vacancies = [
        item
        for item in vacancy_inputs
        if str(item.get("complex_id") or "") == complex_id
    ]
    if len(member_dopants) != 1:
        errors.append(
            f"{label} must bind exactly one substitutional dopant record; "
            f"found {len(member_dopants)}."
        )
    if len(member_vacancies) != 1:
        errors.append(
            f"{label} must bind exactly one vacancy record; "
            f"found {len(member_vacancies)}."
        )

    substitution_fractional = (
        member_fractionals[0] if len(member_fractionals) > 0 else None
    )
    vacancy_fractional = (
        member_fractionals[1] if len(member_fractionals) > 1 else None
    )
    if len(member_dopants) == 1:
        dopant = member_dopants[0]
        if str(dopant.get("atom_id") or dopant.get("site_id") or "") != (
            substitution_site_id
        ):
            errors.append(
                f"{label} substitutional dopant record has a different site id."
            )
        if str(dopant.get("site_element") or "") != substitution_host_element:
            errors.append(
                f"{label} substitutional dopant record has a different host element."
            )
        if str(
            dopant.get("dopant_element")
            or dopant.get("new_element")
            or ""
        ) != substitution_element:
            errors.append(
                f"{label} substitutional dopant record has a different dopant element."
            )
        dopant_fractional = _coerce_fractional(dopant.get("fractional"))
        if (
            dopant_fractional is None
            or substitution_fractional is None
            or any(
                abs(dopant_fractional[axis] - substitution_fractional[axis])
                > 1e-6
                for axis in range(3)
            )
        ):
            errors.append(
                f"{label} substitutional dopant fractional coordinates differ "
                "from the complex record."
            )
        if str(dopant.get("pair_site_id") or "") != vacancy_site_id:
            errors.append(
                f"{label} substitutional dopant has an inconsistent pair_site_id."
            )
    if len(member_vacancies) == 1:
        vacancy = member_vacancies[0]
        if str(vacancy.get("site_id") or vacancy.get("atom_id") or "") != (
            vacancy_site_id
        ):
            errors.append(f"{label} vacancy record has a different site id.")
        if str(vacancy.get("site_element") or vacancy.get("element") or "") != (
            vacancy_site_element
        ):
            errors.append(f"{label} vacancy record has a different site element.")
        recorded_vacancy_fractional = _coerce_fractional(
            vacancy.get("fractional")
        )
        if (
            recorded_vacancy_fractional is None
            or vacancy_fractional is None
            or any(
                abs(recorded_vacancy_fractional[axis] - vacancy_fractional[axis])
                > 1e-6
                for axis in range(3)
            )
        ):
            errors.append(
                f"{label} vacancy fractional coordinates differ from the "
                "complex record."
            )
        if str(vacancy.get("pair_site_id") or "") != substitution_site_id:
            errors.append(f"{label} vacancy has an inconsistent pair_site_id.")

    recomputed_distance = None
    image_offset = None
    threshold = None
    nearest_neighbor_recomputed = False
    if (
        isinstance(spec.model, CrystalSpec)
        and substitution_fractional is not None
        and vacancy_fractional is not None
    ):
        recomputed_distance, image_offset = _minimum_image_distance(
            substitution_fractional,
            vacancy_fractional,
            _lattice_vectors(spec.model.lattice),
        )
        threshold = _crystal_neighbor_threshold(
            substitution_element,
            vacancy_site_element,
        )
        nearest_neighbor_recomputed = (
            recomputed_distance <= threshold + 1e-6
        )

    recorded_distance = _optional_float(
        complex_input.get("pair_distance_angstrom")
    )
    if recorded_distance is None or not math.isfinite(recorded_distance):
        errors.append(f"{label} pair_distance_angstrom must be finite.")
        recorded_distance = None
    if recomputed_distance is None:
        errors.append(
            f"{label} pair distance could not be recomputed from current lattice metadata."
        )
    distance_delta = (
        abs(recorded_distance - recomputed_distance)
        if recorded_distance is not None and recomputed_distance is not None
        else None
    )
    if distance_delta is not None and distance_delta > 1e-5:
        errors.append(
            f"{label} recorded pair distance does not match the current lattice."
        )
    threshold_claim = _optional_float(
        complex_input.get("nearest_neighbor_threshold_angstrom")
    )
    if (
        threshold is not None
        and (
            threshold_claim is None
            or not math.isfinite(threshold_claim)
            or abs(threshold_claim - threshold) > 1e-5
        )
    ):
        errors.append(
            f"{label} nearest-neighbor threshold metadata is inconsistent."
        )
    if not nearest_neighbor_recomputed:
        errors.append(
            f"{label} members are not a nearest-neighbor pair under the "
            "current distance rule."
        )
    nearest_neighbor_metadata_claim = (
        complex_input.get("nearest_neighbor_verified") is True
    )
    if not nearest_neighbor_metadata_claim:
        errors.append(
            f"{label} does not carry a positive nearest-neighbor verification claim."
        )
    periodic_minimum_image = (
        complex_input.get("periodic_minimum_image") is True
    )
    if not periodic_minimum_image:
        errors.append(
            f"{label} is not bound to periodic minimum-image distance semantics."
        )

    charge_state_label = str(
        complex_input.get("charge_state_label") or ""
    )
    expected_states: dict[
        str, tuple[bool, int | None, int | None, str | None]
    ] = {
        "unspecified": (False, None, None, None),
        "NV0": (True, 0, 2, "doublet"),
        "NV-": (True, -1, 3, "triplet"),
    }
    state_contract = expected_states.get(charge_state_label)
    if state_contract is None:
        errors.append(
            f"{label} has unsupported charge_state_label "
            f"{charge_state_label or 'missing'}."
        )
        state_contract = (False, None, None, None)
    (
        expected_explicit,
        expected_charge,
        expected_multiplicity,
        expected_spin_state,
    ) = state_contract
    charge_state_explicit = (
        complex_input.get("charge_state_explicit") is True
    )
    if charge_state_explicit != expected_explicit:
        errors.append(f"{label} charge-state explicitness is inconsistent.")
    raw_charge = complex_input.get("requested_net_charge_e")
    requested_net_charge = _optional_int(raw_charge)
    if (
        (expected_charge is None and raw_charge is not None)
        or (
            expected_charge is not None
            and requested_net_charge != expected_charge
        )
    ):
        errors.append(f"{label} requested net charge is inconsistent.")
    raw_multiplicity = complex_input.get("reference_spin_multiplicity")
    reference_spin_multiplicity = _optional_int(raw_multiplicity)
    if (
        (expected_multiplicity is None and raw_multiplicity is not None)
        or (
            expected_multiplicity is not None
            and reference_spin_multiplicity != expected_multiplicity
        )
    ):
        errors.append(f"{label} reference spin multiplicity is inconsistent.")
    reference_spin_state = complex_input.get("reference_spin_state")
    if reference_spin_state != expected_spin_state:
        errors.append(f"{label} reference spin state is inconsistent.")

    metadata = dict(spec.metadata or {})
    charge_spin_request = (
        dict(metadata.get("defect_charge_spin_request"))
        if isinstance(metadata.get("defect_charge_spin_request"), dict)
        else {}
    )
    charge_spin_contract_fields = (
        "complex_id",
        "charge_state_label",
        "charge_state_explicit",
        "requested_net_charge_e",
        "reference_spin_multiplicity",
        "reference_spin_state",
        "backend",
        "backend_charge_binding_status",
        "backend_spin_binding_status",
        "calculation_execution_ready",
        "structure_hotload_allowed",
        "state_result_computed",
    )
    if not charge_spin_request:
        errors.append(f"{label} is missing the top-level defect charge/spin request.")
    else:
        for field in charge_spin_contract_fields:
            if field not in charge_spin_request:
                errors.append(
                    f"{label} top-level defect charge/spin request is missing {field}."
                )
            elif charge_spin_request.get(field) != complex_input.get(field):
                errors.append(
                    f"{label} top-level defect charge/spin request differs at {field}."
                )

    backend_charge_status = str(
        complex_input.get("backend_charge_binding_status") or ""
    )
    backend_spin_status = str(
        complex_input.get("backend_spin_binding_status") or ""
    )
    backend_statuses_match = bool(
        backend_charge_status
        and backend_charge_status == backend_spin_status
    )
    if not backend_statuses_match:
        errors.append(
            f"{label} charge and spin backend binding statuses differ."
        )
    if (
        backend_charge_status not in DIAMOND_NV_REVIEWED_BACKEND_STATUSES
        or backend_spin_status not in DIAMOND_NV_REVIEWED_BACKEND_STATUSES
    ):
        errors.append(
            f"{label} backend binding status is not a reviewed structured value."
        )
    structured_binding = diamond_nv_castep_binding_receipt(
        charge_state_label,
        spec.simulation,
    )
    metadata_declares_bound = bool(
        backend_charge_status == DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS
        and backend_spin_status == DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS
    )
    charge_spin_backend_binding_ready = bool(
        metadata_declares_bound
        and charge_state_explicit
        and structured_binding.get("exact_match") is True
    )
    expected_execution_ready = charge_spin_backend_binding_ready
    if (
        complex_input.get("calculation_execution_ready")
        is not expected_execution_ready
    ):
        errors.append(
            f"{label} calculation readiness does not match its structured "
            "CASTEP charge/spin binding."
        )
    if metadata_declares_bound and structured_binding.get("exact_match") is not True:
        errors.append(
            f"{label} declares a structured CASTEP binding but the simulation "
            "settings do not match the reviewed charge state."
        )
    if (
        backend_charge_status
        in {
            DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS,
            DIAMOND_NV_CHARGE_SPIN_BINDING_REQUIRED_STATUS,
        }
        and complex_input.get("calculation_execution_ready") is not False
    ):
        errors.append(f"{label} unbound legacy metadata must remain fail-closed.")
    if complex_input.get("state_result_computed") is not False:
        errors.append(
            f"{label} must not claim a computed charge or spin state."
        )
    if complex_input.get("structure_hotload_allowed") is not True:
        errors.append(
            f"{label} must preserve the reviewed structure-hotload allowance."
        )
    if complex_input.get("backend") != "Materials Studio 20.1 CASTEP":
        errors.append(f"{label} is not bound to the reviewed CASTEP backend label.")

    metadata_consistent = not errors
    return {
        "complex_id": complex_id or None,
        "type": "nitrogen_vacancy",
        "member_site_ids": member_site_ids,
        "member_site_elements": member_site_elements,
        "member_fractionals": [
            list(item) if item is not None else None
            for item in member_fractionals
        ],
        "member_count": len(member_site_ids),
        "member_vacancy_record_count": len(member_vacancies),
        "member_dopant_record_count": len(member_dopants),
        "substitution_site_id": substitution_site_id or None,
        "substitution_host_element": substitution_host_element or None,
        "substitution_element": substitution_element or None,
        "vacancy_site_id": vacancy_site_id or None,
        "vacancy_site_element": vacancy_site_element or None,
        "pair_distance_angstrom_recorded": (
            _round(recorded_distance)
            if recorded_distance is not None
            else None
        ),
        "pair_distance_angstrom_recomputed": (
            _round(recomputed_distance)
            if recomputed_distance is not None
            else None
        ),
        "distance_delta_angstrom": (
            _round(distance_delta) if distance_delta is not None else None
        ),
        "nearest_neighbor_threshold_angstrom": (
            _round(threshold) if threshold is not None else None
        ),
        "nearest_neighbor_metadata_claim": nearest_neighbor_metadata_claim,
        "nearest_neighbor_recomputed": nearest_neighbor_recomputed,
        "nearest_neighbor_verified": bool(
            metadata_consistent and nearest_neighbor_recomputed
        ),
        "periodic_minimum_image": periodic_minimum_image,
        "image_offset": list(image_offset) if image_offset is not None else None,
        "selection": complex_input.get("selection"),
        "selection_rule": complex_input.get("selection_rule"),
        "charge_state_label": charge_state_label or None,
        "charge_state_explicit": charge_state_explicit,
        "requested_net_charge_e": requested_net_charge,
        "reference_spin_multiplicity": reference_spin_multiplicity,
        "reference_spin_state": reference_spin_state,
        "backend_charge_binding_status": backend_charge_status or None,
        "backend_spin_binding_status": backend_spin_status or None,
        "charge_spin_backend_binding_ready": charge_spin_backend_binding_ready,
        "structured_castep_binding": structured_binding,
        "expected_castep_charge_spin_settings": structured_binding.get(
            "expected_settings"
        ),
        "observed_castep_charge_spin_settings": structured_binding.get(
            "observed_settings"
        ),
        "castep_charge_spin_field_matches": structured_binding.get(
            "field_matches"
        ),
        "calculation_execution_ready": (
            complex_input.get("calculation_execution_ready") is True
            and charge_spin_backend_binding_ready
        ),
        "structure_hotload_allowed": (
            complex_input.get("structure_hotload_allowed") is True
        ),
        "state_result_computed": False,
        "metadata_consistent": metadata_consistent,
        "integrity_errors": errors,
        "source": complex_input.get("source"),
    }


def _finite_size_summary(
    spec: ModelSpec,
    lattice_summary: dict[str, Any] | None,
    dopant_summary: dict[str, Any] | None,
    defect_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    items: list[dict[str, Any]] = []
    for dopant in (dopant_summary or {}).get("dopants", []) or []:
        fraction = _optional_float(dopant.get("concentration_fraction"))
        if fraction is None:
            continue
        items.append(
            {
                "kind": "dopant",
                "label": dopant.get("element"),
                "count": dopant.get("count"),
                "fraction": _round(fraction),
                "percent": _round(100.0 * fraction),
            }
        )
    for kind, field in (
        ("vacancy", "total_vacancy_fraction"),
        ("interstitial", "total_interstitial_fraction"),
        ("antisite", "total_antisite_fraction"),
    ):
        fraction = _optional_float((defect_summary or {}).get(field))
        if fraction is None or fraction <= 0:
            continue
        items.append(
            {
                "kind": kind,
                "label": kind,
                "count": (defect_summary or {}).get(f"{kind}_count"),
                "fraction": _round(fraction),
                "percent": _round(100.0 * fraction),
            }
        )
    if not items:
        return None

    lattice_summary = lattice_summary or {}
    non_passivant_atom_count = _optional_int(lattice_summary.get("non_passivant_atom_count"))
    lengths = [spec.model.lattice.a, spec.model.lattice.b, spec.model.lattice.c]
    max_item = max(items, key=lambda item: float(item.get("fraction") or 0.0))
    max_fraction = _optional_float(max_item.get("fraction")) or 0.0
    small_cell_warning = bool(
        non_passivant_atom_count is not None
        and non_passivant_atom_count < SEMICONDUCTOR_MIN_DILUTE_CELL_ATOMS
    )
    high_concentration_warning = max_fraction > SEMICONDUCTOR_MAX_DILUTE_DEFECT_FRACTION
    warnings: list[str] = []
    if small_cell_warning:
        warnings.append(
            "Semiconductor isolated dopant/defect model uses a small cell; consider a larger supercell before quantitative DFT."
        )
    if high_concentration_warning:
        warnings.append(
            "Semiconductor isolated dopant/defect concentration is high for dilute-defect interpretation; inspect finite_size_summary."
        )
    return {
        "available": True,
        "model": "isolated_dopant_defect_finite_size_heuristic",
        "non_passivant_atom_count": non_passivant_atom_count,
        "min_lattice_length_angstrom": _round(min(lengths)),
        "lattice_lengths_angstrom": [_round(value) for value in lengths],
        "isolated_item_count": len(items),
        "items": items[:MAX_HEALTH_DETAIL_ROWS],
        "max_isolated_item": max_item,
        "max_isolated_fraction": _round(max_fraction),
        "dilute_cell_atom_threshold": SEMICONDUCTOR_MIN_DILUTE_CELL_ATOMS,
        "dilute_fraction_threshold": SEMICONDUCTOR_MAX_DILUTE_DEFECT_FRACTION,
        "small_cell_warning": small_cell_warning,
        "high_concentration_warning": high_concentration_warning,
        "finite_size_warning": bool(small_cell_warning or high_concentration_warning),
        "warnings": warnings,
    }


def _antisite_role_hint(original_element: str, new_element: str) -> str:
    if original_element in III_V_CATIONS and new_element in III_V_ANIONS:
        return "anion_on_cation_site"
    if original_element in III_V_ANIONS and new_element in III_V_CATIONS:
        return "cation_on_anion_site"
    if original_element in GROUP_IV_SEMICONDUCTORS and new_element in GROUP_IV_SEMICONDUCTORS:
        return "group_iv_site_substitution"
    return "site_substitution"


def _defect_neighbor_rows(
    *,
    fractional: tuple[float, float, float] | None,
    element: str,
    atom_rows: list[dict[str, Any]],
    coord_map: dict[str, dict[str, Any]],
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None,
    expected_coordination: int | None,
    expected_coordination_by_element: dict[str, int] | None = None,
    exclude_atom_id: str | None = None,
) -> list[dict[str, Any]]:
    neighbors = []
    if vectors is None or fractional is None:
        return neighbors
    for atom in atom_rows:
        atom_id = str(atom.get("id") or "")
        if exclude_atom_id and atom_id == exclude_atom_id:
            continue
        atom_fractional = _coerce_fractional(atom.get("fractional"))
        atom_element = str(atom.get("element") or "")
        if atom_fractional is None or atom_element in SURFACE_PASSIVANTS:
            continue
        distance, offset = _minimum_image_distance(fractional, atom_fractional, vectors)
        threshold = _crystal_neighbor_threshold(element, atom_element)
        if distance <= threshold:
            coord = coord_map.get(atom_id, {})
            neighbor_count = int(coord.get("neighbor_count") or 0)
            atom_expected_coordination = _expected_coordination_for_element(
                atom_element,
                expected_coordination,
                expected_coordination_by_element,
            )
            missing = max((atom_expected_coordination or neighbor_count) - neighbor_count, 0)
            neighbors.append(
                {
                    "atom_id": atom.get("id"),
                    "element": atom_element,
                    "distance_angstrom": _round(distance),
                    "image_offset": list(offset),
                    "neighbor_count": neighbor_count,
                    "expected_coordination": atom_expected_coordination,
                    "missing_coordination": missing,
                }
            )
    neighbors.sort(key=lambda item: (float(item["distance_angstrom"]), str(item["atom_id"])))
    return neighbors


def _coerce_fractional(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    if len(items) != 3:
        return None
    try:
        return float(items[0]), float(items[1]), float(items[2])
    except (TypeError, ValueError):
        return None


def _is_iii_v_cation_anion_pair(element1: str, element2: str) -> bool:
    return (element1 in III_V_CATIONS and element2 in III_V_ANIONS) or (
        element2 in III_V_CATIONS and element1 in III_V_ANIONS
    )


def _is_iii_v_same_sublattice_pair(element1: str, element2: str) -> bool:
    return (element1 in III_V_CATIONS and element2 in III_V_CATIONS) or (
        element1 in III_V_ANIONS and element2 in III_V_ANIONS
    )


def _is_ii_vi_cation_anion_pair(element1: str, element2: str) -> bool:
    return (element1 in II_VI_CATIONS and element2 in II_VI_ANIONS) or (
        element2 in II_VI_CATIONS and element1 in II_VI_ANIONS
    )


def _is_ii_vi_same_sublattice_pair(element1: str, element2: str) -> bool:
    return (element1 in II_VI_CATIONS and element2 in II_VI_CATIONS) or (
        element1 in II_VI_ANIONS and element2 in II_VI_ANIONS
    )


def _is_oxide_cation_oxygen_pair(element1: str, element2: str, oxide_cations: Sequence[str]) -> bool:
    cations = set(oxide_cations)
    return (element1 in cations and element2 == "O") or (element2 in cations and element1 == "O")


def _is_oxide_same_sublattice_pair(element1: str, element2: str, oxide_cations: Sequence[str]) -> bool:
    cations = set(oxide_cations)
    return (element1 in cations and element2 in cations) or (element1 == "O" and element2 == "O")


def _is_halide_perovskite_context(metadata: dict[str, Any], non_passivant_elements: list[str]) -> bool:
    elements = set(non_passivant_elements)
    if not (elements & HALIDE_PEROVSKITE_B_CATIONS and elements & HALIDE_PEROVSKITE_HALIDES):
        return False
    if metadata.get("halide_perovskite") or metadata.get("perovskite_abx3"):
        return True
    materials = metadata.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    elif not isinstance(materials, list):
        materials = []
    markers = " ".join(
        str(item or "").lower()
        for item in [
            metadata.get("structure_family"),
            metadata.get("material"),
            metadata.get("formula"),
            *materials,
        ]
    )
    return "perovskite" in markers or "mapbi3" in markers or "cspbi3" in markers or "pbi3" in markers


def _halide_perovskite_site_role(element: str, site_roles: dict[str, str] | None = None) -> str | None:
    if site_roles and element in site_roles:
        return site_roles[element]
    if element in HALIDE_PEROVSKITE_B_CATIONS:
        return "b_cation"
    if element in HALIDE_PEROVSKITE_HALIDES:
        return "halide"
    return None


def _is_halide_perovskite_b_halide_pair(
    element1: str,
    element2: str,
    site_roles: dict[str, str] | None = None,
) -> bool:
    return {
        _halide_perovskite_site_role(element1, site_roles),
        _halide_perovskite_site_role(element2, site_roles),
    } == {"b_cation", "halide"}


def _is_halide_perovskite_framework_same_sublattice_pair(
    element1: str,
    element2: str,
    site_roles: dict[str, str] | None = None,
) -> bool:
    role1 = _halide_perovskite_site_role(element1, site_roles)
    role2 = _halide_perovskite_site_role(element2, site_roles)
    return role1 is not None and role1 == role2


def _is_long_same_sublattice_cutoff_artifact(
    row: dict[str, Any],
    neighbor_pair_rows: list[dict[str, Any]],
    *,
    expected_pair: Any,
) -> bool:
    """Identify same-sublattice candidates that sit well beyond the heteropolar first shell."""

    try:
        distance = float(row.get("distance_angstrom"))
    except (TypeError, ValueError):
        return False
    expected_distances: list[float] = []
    for candidate in neighbor_pair_rows:
        element1 = str(candidate.get("element1") or "")
        element2 = str(candidate.get("element2") or "")
        if not expected_pair(element1, element2):
            continue
        try:
            expected_distances.append(float(candidate.get("distance_angstrom")))
        except (TypeError, ValueError):
            continue
    if not expected_distances:
        return False
    return distance > max(expected_distances) * 1.35


def _is_tmd_metal_chalcogen_pair(element1: str, element2: str, site_roles: dict[str, str] | None = None) -> bool:
    if site_roles:
        return {site_roles.get(element1), site_roles.get(element2)} == {"metal", "chalcogen"}
    return (element1 in TMD_METALS and element2 in TMD_CHALCOGENS) or (
        element2 in TMD_METALS and element1 in TMD_CHALCOGENS
    )


def _is_tmd_same_sublattice_pair(element1: str, element2: str, site_roles: dict[str, str] | None = None) -> bool:
    if site_roles:
        role1 = site_roles.get(element1)
        role2 = site_roles.get(element2)
        return bool(role1 and role1 == role2)
    return (element1 in TMD_METALS and element2 in TMD_METALS) or (
        element1 in TMD_CHALCOGENS and element2 in TMD_CHALCOGENS
    )


def _semiconductor_pair_detail(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "atom1": row.get("atom1"),
        "element1": row.get("element1"),
        "atom2": row.get("atom2"),
        "element2": row.get("element2"),
        "pair_type": row.get("pair_type"),
        "distance_angstrom": row.get("distance_angstrom"),
    }


def _crystal_neighbor_threshold(element1: str | None, element2: str | None) -> float:
    radius1 = COVALENT_RADII_ANGSTROM.get(element1 or "", 0.9)
    radius2 = COVALENT_RADII_ANGSTROM.get(element2 or "", 0.9)
    return max(0.8, 1.25 * (radius1 + radius2))


def _element_pair_label(element1: str, element2: str) -> str:
    if element1 in HALIDE_PEROVSKITE_B_CATIONS and element2 in HALIDE_PEROVSKITE_HALIDES:
        return f"{element1}-{element2}"
    if element2 in HALIDE_PEROVSKITE_B_CATIONS and element1 in HALIDE_PEROVSKITE_HALIDES:
        return f"{element2}-{element1}"
    if element1 in III_V_CATIONS and element2 in III_V_ANIONS:
        return f"{element1}-{element2}"
    if element2 in III_V_CATIONS and element1 in III_V_ANIONS:
        return f"{element2}-{element1}"
    if element1 in II_VI_CATIONS and element2 in II_VI_ANIONS:
        return f"{element1}-{element2}"
    if element2 in II_VI_CATIONS and element1 in II_VI_ANIONS:
        return f"{element2}-{element1}"
    if element1 in TMD_METALS and element2 in TMD_CHALCOGENS:
        return f"{element1}-{element2}"
    if element2 in TMD_METALS and element1 in TMD_CHALCOGENS:
        return f"{element2}-{element1}"
    if element1 in SURFACE_PASSIVANTS and element2 not in SURFACE_PASSIVANTS:
        return f"{element2}-{element1}"
    if element2 in SURFACE_PASSIVANTS and element1 not in SURFACE_PASSIVANTS:
        return f"{element1}-{element2}"
    return "-".join(sorted((element1, element2)))


def _slab_vacuum_summary(spec: ModelSpec, atom_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    metadata = spec.metadata or {}
    if not _surface_slab_diagnostics_applicable(metadata):
        return None
    axis_name = str(metadata.get("surface_axis") or "c").lower()
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_name)
    axis_length = {
        "a": spec.model.lattice.a,
        "b": spec.model.lattice.b,
        "c": spec.model.lattice.c,
    }.get(axis_name)
    if axis_index is None or axis_length is None:
        return {
            "surface_axis": axis_name,
            "surface_orientation": metadata.get("surface_orientation"),
            "warning": "Unsupported surface_axis for slab vacuum diagnostics.",
            "slab_vacuum_status": "unsupported_axis",
            "slab_vacuum_next_action": "fix_surface_axis_metadata_before_slab_diagnostics",
        }
    fractional_values = [float(atom["fractional"][axis_index]) for atom in atom_rows if atom.get("fractional") is not None]
    if not fractional_values:
        return None
    fractional_min = min(fractional_values)
    fractional_max = max(fractional_values)
    atom_extent = (fractional_max - fractional_min) * axis_length
    atom_extent_vacuum = max(axis_length - atom_extent, 0.0)
    bottom_vacuum = max(fractional_min * axis_length, 0.0)
    top_vacuum = max((1.0 - fractional_max) * axis_length, 0.0)
    slab_center_fractional = (fractional_min + fractional_max) / 2.0
    slab_center_offset = (slab_center_fractional - 0.5) * axis_length
    vacuum_asymmetry = top_vacuum - bottom_vacuum
    declared_vacuum = _optional_float(metadata.get("vacuum_angstrom"))
    declared_thickness = _optional_float(metadata.get("slab_thickness_angstrom"))
    metadata_cell_mismatch = False
    if declared_vacuum is not None and declared_thickness is not None:
        metadata_cell_mismatch = abs((declared_vacuum + declared_thickness) - axis_length) > 0.5
    vacuum_basis = declared_vacuum if declared_vacuum is not None else atom_extent_vacuum
    centered_in_cell = abs(slab_center_offset) <= 0.25
    vacuum_ok = vacuum_basis >= 8.0
    vacuum_status, vacuum_next_action = _slab_vacuum_status(
        vacuum_ok=vacuum_ok,
        centered_in_cell=centered_in_cell,
        metadata_cell_mismatch=metadata_cell_mismatch,
    )
    return {
        "surface_orientation": metadata.get("surface_orientation"),
        "surface_axis": axis_name,
        "termination": metadata.get("termination"),
        "cell_axis_length_angstrom": _round(axis_length),
        "declared_slab_thickness_angstrom": _round(declared_thickness) if declared_thickness is not None else None,
        "declared_vacuum_angstrom": _round(declared_vacuum) if declared_vacuum is not None else None,
        "atom_fractional_min": _round(fractional_min),
        "atom_fractional_max": _round(fractional_max),
        "atom_extent_angstrom": _round(atom_extent),
        "atom_extent_vacuum_angstrom": _round(atom_extent_vacuum),
        "bottom_vacuum_angstrom": _round(bottom_vacuum),
        "top_vacuum_angstrom": _round(top_vacuum),
        "vacuum_asymmetry_angstrom": _round(vacuum_asymmetry),
        "vacuum_asymmetry_abs_angstrom": _round(abs(vacuum_asymmetry)),
        "slab_center_fractional": _round(slab_center_fractional),
        "slab_center_offset_angstrom": _round(slab_center_offset),
        "centered_in_cell": centered_in_cell,
        "vacuum_ok": vacuum_ok,
        "slab_vacuum_status": vacuum_status,
        "slab_vacuum_next_action": vacuum_next_action,
        "metadata_cell_mismatch": metadata_cell_mismatch,
    }


def _slab_vacuum_status(
    *,
    vacuum_ok: bool,
    centered_in_cell: bool,
    metadata_cell_mismatch: bool,
) -> tuple[str, str]:
    if metadata_cell_mismatch:
        return (
            "metadata_mismatch",
            "fix_slab_vacuum_or_lattice_metadata_before_claiming_normality",
        )
    if not vacuum_ok:
        return (
            "insufficient_vacuum",
            "increase_slab_vacuum_before_calculation_or_claiming_normality",
        )
    if not centered_in_cell:
        return (
            "off_center",
            "center_slab_or_review_asymmetric_vacuum_before_claiming_normality",
        )
    return "ready", "slab_vacuum_spacing_and_centering_ok"


def _reciprocal_plane_geometry(
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    plane_indices: tuple[int, ...],
) -> tuple[dict[str, Any] | None, str | None]:
    if len(plane_indices) == 4:
        h, k, i, l = plane_indices
        if h + k + i != 0:
            return None, f"Miller-Bravais plane indices {plane_indices!r} must satisfy h + k + i = 0."
        reciprocal_coefficients = (h, k, l)
    elif len(plane_indices) == 3:
        reciprocal_coefficients = plane_indices
    else:
        return None, "Crystal plane indices must contain three Miller or four Miller-Bravais values."

    cell_volume = _dot(vectors[0], _cross(vectors[1], vectors[2]))
    if abs(cell_volume) <= 1e-20:
        return None, "Crystal lattice is singular; a reciprocal-space plane normal cannot be constructed."
    reciprocal_vectors = (
        _scale_tuple(_cross(vectors[1], vectors[2]), 1.0 / cell_volume),
        _scale_tuple(_cross(vectors[2], vectors[0]), 1.0 / cell_volume),
        _scale_tuple(_cross(vectors[0], vectors[1]), 1.0 / cell_volume),
    )
    reciprocal_vector = tuple(
        sum(
            float(reciprocal_coefficients[axis]) * reciprocal_vectors[axis][component]
            for axis in range(3)
        )
        for component in range(3)
    )
    reciprocal_length = math.sqrt(max(_dot(reciprocal_vector, reciprocal_vector), 0.0))
    if reciprocal_length <= 1e-20:
        return None, f"Crystallographic plane {plane_indices!r} produced a zero-length normal."
    return (
        {
            "normal_cartesian": _scale_tuple(reciprocal_vector, 1.0 / reciprocal_length),
            "reciprocal_vector_per_angstrom": reciprocal_vector,
            "reciprocal_convention": "dual_basis_without_2pi",
            "spacing_angstrom": 1.0 / reciprocal_length,
        },
        None,
    )


def _direction_view_onto_plane_mapping(
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    direction: tuple[float, float, float],
) -> dict[str, Any]:
    """Find an exact low-index Miller normal collinear with a direct direction."""

    normalized_direction = _normalize(direction)
    if _dot(normalized_direction, normalized_direction) <= 1e-20:
        return {
            "status": "invalid_zero_length_direction",
            "automation_eligible": False,
            "search_max_abs_index": DIRECTION_VIEW_ONTO_MILLER_MAX_ABS_INDEX,
            "collinearity_sine_tolerance": DIRECTION_VIEW_ONTO_MILLER_COLLINEARITY_SINE_TOLERANCE,
        }

    best: tuple[
        tuple[float, int, int, tuple[int, int, int]],
        tuple[int, int, int],
        tuple[float, float, float],
        float,
    ] | None = None
    seen: set[tuple[int, int, int]] = set()
    limit = DIRECTION_VIEW_ONTO_MILLER_MAX_ABS_INDEX
    for h in range(-limit, limit + 1):
        for k in range(-limit, limit + 1):
            for l in range(-limit, limit + 1):
                if h == 0 and k == 0 and l == 0:
                    continue
                divisor = math.gcd(math.gcd(abs(h), abs(k)), abs(l))
                primitive = (h // divisor, k // divisor, l // divisor)
                first_nonzero = next(value for value in primitive if value != 0)
                if first_nonzero < 0:
                    primitive = tuple(-value for value in primitive)
                if primitive in seen:
                    continue
                seen.add(primitive)
                geometry, _ = _reciprocal_plane_geometry(vectors, primitive)
                if geometry is None:
                    continue
                normal = tuple(float(value) for value in geometry["normal_cartesian"])
                dot_product = _dot(normalized_direction, normal)
                oriented_indices = primitive
                oriented_normal = normal
                if dot_product < 0.0:
                    oriented_indices = tuple(-value for value in primitive)
                    oriented_normal = _scale_tuple(normal, -1.0)
                    dot_product = -dot_product
                cross_product = _cross(normalized_direction, oriented_normal)
                sine_error = math.sqrt(max(_dot(cross_product, cross_product), 0.0))
                score = (
                    sine_error,
                    max(abs(value) for value in primitive),
                    sum(abs(value) for value in primitive),
                    primitive,
                )
                candidate = (score, oriented_indices, oriented_normal, dot_product)
                if best is None or score < best[0]:
                    best = candidate

    if best is None:
        return {
            "status": "no_integer_plane_candidate",
            "automation_eligible": False,
            "search_max_abs_index": limit,
            "collinearity_sine_tolerance": DIRECTION_VIEW_ONTO_MILLER_COLLINEARITY_SINE_TOLERANCE,
        }

    score, indices, normal, dot_product = best
    sine_error = score[0]
    angular_error_degrees = math.degrees(math.asin(min(1.0, sine_error)))
    exact = sine_error <= DIRECTION_VIEW_ONTO_MILLER_COLLINEARITY_SINE_TOLERANCE
    return {
        "status": (
            "exact_integer_plane_collinear"
            if exact
            else "no_exact_integer_plane_within_search_bound"
        ),
        "automation_eligible": exact,
        "miller_plane_indices": list(indices) if exact else None,
        "miller_plane_label": (
            "(" + "".join(str(value) for value in indices) + ")"
            if exact
            else None
        ),
        "plane_normal_cartesian": _round_tuple(normal) if exact else None,
        "direction_plane_dot_product": _round(dot_product),
        "angular_error_degrees": _round(angular_error_degrees),
        "closest_candidate_indices": list(indices),
        "search_max_abs_index": limit,
        "collinearity_sine_tolerance": DIRECTION_VIEW_ONTO_MILLER_COLLINEARITY_SINE_TOLERANCE,
        "relation": "direct_lattice_direction_collinear_with_reciprocal_plane_normal",
    }


def _oriented_frame_view_definition(
    spec: ModelSpec,
    view_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    frame_spec = ORIENTED_FRAME_VIEW_SPECS.get(view_name)
    if frame_spec is None:
        return None, "Unknown oriented-frame view name."
    if not isinstance(spec.model, CrystalSpec):
        return None, "Surface and interface frame views require a crystal model with lattice vectors."

    frame_kind, frame_role = frame_spec
    metadata_field = "surface_axis" if frame_kind == "surface" else "interface_axis"
    axis_name = str((spec.metadata or {}).get(metadata_field) or "").strip().lower()
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_name)
    if axis_index is None:
        return None, f"{view_name} requires metadata.{metadata_field} set to a, b, or c."

    vectors = _lattice_vectors(spec.model.lattice)
    normal = _normalize(vectors[axis_index])
    if _dot(normal, normal) <= 1e-20:
        return None, f"{metadata_field}={axis_name} produced a zero-length frame normal."
    remaining_axes = [index for index in range(3) if index != axis_index]
    in_plane_1 = None
    reference_axis_1 = None
    for candidate_index in remaining_axes:
        candidate = vectors[candidate_index]
        projected = _subtract(candidate, _scale_tuple(normal, _dot(candidate, normal)))
        projected = _normalize(projected)
        if _dot(projected, projected) <= 1e-20:
            continue
        in_plane_1 = projected
        reference_axis_1 = candidate_index
        break
    if in_plane_1 is None or reference_axis_1 is None:
        return None, f"Could not construct an in-plane direction perpendicular to {metadata_field}={axis_name}."
    remaining_reference_axes = [index for index in remaining_axes if index != reference_axis_1]
    in_plane_2 = _normalize(_cross(normal, in_plane_1))
    if _dot(in_plane_2, in_plane_2) <= 1e-20:
        return None, f"Could not construct the second in-plane direction for {metadata_field}={axis_name}."
    reference_axis_2 = remaining_reference_axes[0] if remaining_reference_axes else reference_axis_1
    if _dot(in_plane_2, vectors[reference_axis_2]) < 0.0:
        in_plane_2 = _scale_tuple(in_plane_2, -1.0)

    axis_labels = ("a", "b", "c")
    if frame_role == "normal":
        direction = normal
        up = in_plane_2
        reference_axis = axis_name
    elif frame_role == "in_plane_1":
        direction = in_plane_1
        up = normal
        reference_axis = axis_labels[reference_axis_1]
    else:
        direction = in_plane_2
        up = normal
        reference_axis = axis_labels[reference_axis_2]
    coordinate_system = f"{frame_kind}_cell_frame"
    return (
        {
            "direction": direction,
            "up": up,
            "coordinate_system": coordinate_system,
            "oriented_frame_kind": frame_kind,
            "oriented_frame_role": frame_role,
            "oriented_frame_axis": axis_name,
            "oriented_frame_source_metadata_field": metadata_field,
            "oriented_frame_reference_cell_axis": reference_axis,
            "oriented_frame_axis_cartesian": normal,
            "oriented_frame_direction_cartesian": direction,
            "oriented_frame_in_plane_1_cartesian": in_plane_1,
            "oriented_frame_in_plane_2_cartesian": in_plane_2,
        },
        None,
    )


def _view_definition_for_spec(
    spec: ModelSpec,
    view_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    base_definition = VIEW_DEFINITIONS.get(view_name)
    if base_definition is not None:
        return {**base_definition, "coordinate_system": "cartesian"}, None

    if view_name in ORIENTED_FRAME_VIEW_SPECS:
        return _oriented_frame_view_definition(spec, view_name)

    direction_indices = CRYSTAL_DIRECTION_VIEW_INDICES.get(view_name)
    plane_indices = CRYSTAL_PLANE_VIEW_INDICES.get(view_name)
    if direction_indices is None and plane_indices is None:
        return None, "Unknown view name."
    if not isinstance(spec.model, CrystalSpec):
        return None, "Crystallographic direction and plane views require a crystal model with lattice vectors."

    vectors = _lattice_vectors(spec.model.lattice)
    metadata: dict[str, Any]
    if direction_indices is not None:
        if len(direction_indices) == 3:
            direction = tuple(
                sum(float(direction_indices[axis]) * vectors[axis][component] for axis in range(3))
                for component in range(3)
            )
        else:
            u, v, t, w = direction_indices
            a3 = tuple(-(vectors[0][component] + vectors[1][component]) for component in range(3))
            direction = tuple(
                float(u) * vectors[0][component]
                + float(v) * vectors[1][component]
                + float(t) * a3[component]
                + float(w) * vectors[2][component]
                for component in range(3)
            )
        direction = _normalize(direction)
        label = "[" + "".join(str(value) for value in direction_indices) + "]"
        metadata = {
            "coordinate_system": "crystal_lattice_direction",
            "crystal_direction_indices": direction_indices,
            "crystal_direction_label": label,
            "crystal_direction_cartesian": direction,
            "crystal_direction_view_onto_plane_mapping": _direction_view_onto_plane_mapping(
                vectors,
                direction,
            ),
        }
    else:
        assert plane_indices is not None
        plane_geometry, plane_warning = _reciprocal_plane_geometry(vectors, plane_indices)
        if plane_geometry is None:
            return None, plane_warning
        direction = plane_geometry["normal_cartesian"]
        label = "(" + "".join(str(value) for value in plane_indices) + ")"
        metadata = {
            "coordinate_system": "crystal_reciprocal_plane_normal",
            "crystal_plane_indices": plane_indices,
            "crystal_plane_label": label,
            "crystal_plane_normal_cartesian": direction,
            "crystal_plane_reciprocal_vector_per_angstrom": plane_geometry[
                "reciprocal_vector_per_angstrom"
            ],
            "crystal_plane_reciprocal_convention": plane_geometry["reciprocal_convention"],
            "crystal_plane_spacing_angstrom": plane_geometry["spacing_angstrom"],
        }

    if _dot(direction, direction) <= 1e-20:
        return None, f"Crystallographic view {view_name!r} produced a zero-length camera vector."

    up = None
    for candidate in (vectors[2], vectors[1], vectors[0], (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)):
        right = _normalize(_cross(candidate, direction))
        if _dot(right, right) <= 1e-20:
            continue
        up = _normalize(_cross(direction, right))
        break
    if up is None:
        return None, f"Could not construct a stable camera-up vector for crystallographic view {view_name!r}."

    return (
        {
            "direction": direction,
            "up": up,
            **metadata,
        },
        None,
    )


def _view_projection(
    view_name: str,
    atom_rows: list[dict[str, Any]],
    geometry: dict[str, Any],
    *,
    definition: dict[str, Any] | None = None,
    unsupported_warning: str | None = None,
) -> dict[str, Any]:
    definition = definition or VIEW_DEFINITIONS.get(view_name)
    if definition is None:
        return {
            "name": view_name,
            "supported": False,
            "warning": unsupported_warning or "Unknown view name.",
        }
    direction = _normalize(definition["direction"])
    up = _normalize(definition["up"])
    right = _normalize(_cross(up, direction))
    center = geometry.get("center")
    if center is None:
        center_tuple = (0.0, 0.0, 0.0)
    else:
        center_tuple = tuple(float(value) for value in center)
    projections: list[dict[str, Any]] = []
    for atom in atom_rows:
        point = tuple(float(value) for value in atom["xyz"])
        relative = _subtract(point, center_tuple)
        projections.append(
            {
                "atom_id": atom.get("id"),
                "element": atom.get("element"),
                "x": _round(_dot(relative, right)),
                "y": _round(_dot(relative, up)),
                "depth": _round(_dot(relative, direction)),
            }
        )
    if projections:
        x_values = [item["x"] for item in projections]
        y_values = [item["y"] for item in projections]
        depth_values = [item["depth"] for item in projections]
        x_span = max(x_values) - min(x_values)
        y_span = max(y_values) - min(y_values)
        depth_span = max(depth_values) - min(depth_values)
        bbox = {
            "x": [_round(min(x_values)), _round(max(x_values))],
            "y": [_round(min(y_values)), _round(max(y_values))],
            "depth": [_round(min(depth_values)), _round(max(depth_values))],
        }
    else:
        x_span = y_span = depth_span = 0.0
        bbox = None
    warnings: list[str] = []
    if projections and (x_span < 1e-6 or y_span < 1e-6):
        warnings.append("Projected structure is nearly degenerate in this view.")
    overlap_pairs = _projection_overlap_pairs(projections)
    if overlap_pairs:
        warnings.append("Some atoms are nearly overlapping in this 2D projection; inspect depth ordering or rotate the model.")
    truncated = len(projections) > MAX_PROJECTED_ATOMS
    framing = _view_framing(
        center=center_tuple,
        direction=direction,
        x_span=x_span,
        y_span=y_span,
        depth_span=depth_span,
        radius=float(geometry.get("radius_angstrom") or 0.0),
    )
    return {
        "name": view_name,
        "supported": True,
        "coordinate_system": definition.get("coordinate_system", "cartesian"),
        "crystal_direction_indices": (
            list(definition["crystal_direction_indices"])
            if definition.get("crystal_direction_indices") is not None
            else None
        ),
        "crystal_direction_label": definition.get("crystal_direction_label"),
        "crystal_direction_cartesian": (
            _round_tuple(definition["crystal_direction_cartesian"])
            if definition.get("crystal_direction_cartesian") is not None
            else None
        ),
        "crystal_direction_view_onto_plane_mapping": definition.get(
            "crystal_direction_view_onto_plane_mapping"
        ),
        "crystal_plane_indices": (
            list(definition["crystal_plane_indices"])
            if definition.get("crystal_plane_indices") is not None
            else None
        ),
        "crystal_plane_label": definition.get("crystal_plane_label"),
        "crystal_plane_normal_cartesian": (
            _round_tuple(definition["crystal_plane_normal_cartesian"])
            if definition.get("crystal_plane_normal_cartesian") is not None
            else None
        ),
        "crystal_plane_reciprocal_vector_per_angstrom": (
            _round_tuple(definition["crystal_plane_reciprocal_vector_per_angstrom"])
            if definition.get("crystal_plane_reciprocal_vector_per_angstrom") is not None
            else None
        ),
        "crystal_plane_reciprocal_convention": definition.get("crystal_plane_reciprocal_convention"),
        "crystal_plane_spacing_angstrom": (
            _round(float(definition["crystal_plane_spacing_angstrom"]))
            if definition.get("crystal_plane_spacing_angstrom") is not None
            else None
        ),
        "oriented_frame_kind": definition.get("oriented_frame_kind"),
        "oriented_frame_role": definition.get("oriented_frame_role"),
        "oriented_frame_axis": definition.get("oriented_frame_axis"),
        "oriented_frame_source_metadata_field": definition.get("oriented_frame_source_metadata_field"),
        "oriented_frame_reference_cell_axis": definition.get("oriented_frame_reference_cell_axis"),
        "oriented_frame_axis_cartesian": (
            _round_tuple(definition["oriented_frame_axis_cartesian"])
            if definition.get("oriented_frame_axis_cartesian") is not None
            else None
        ),
        "oriented_frame_direction_cartesian": (
            _round_tuple(definition["oriented_frame_direction_cartesian"])
            if definition.get("oriented_frame_direction_cartesian") is not None
            else None
        ),
        "oriented_frame_in_plane_1_cartesian": (
            _round_tuple(definition["oriented_frame_in_plane_1_cartesian"])
            if definition.get("oriented_frame_in_plane_1_cartesian") is not None
            else None
        ),
        "oriented_frame_in_plane_2_cartesian": (
            _round_tuple(definition["oriented_frame_in_plane_2_cartesian"])
            if definition.get("oriented_frame_in_plane_2_cartesian") is not None
            else None
        ),
        "camera_direction": _round_tuple(direction),
        "camera_up": _round_tuple(up),
        "camera_right": _round_tuple(right),
        "look_at_direction": _round_tuple(_scale_tuple(direction, -1.0)),
        "camera_position": framing["camera_position"],
        "camera_distance_angstrom": framing["camera_distance_angstrom"],
        "target": _round_tuple(center_tuple),
        "framing": framing,
        "projection_bbox_angstrom": bbox,
        "projection_span_angstrom": {
            "x": _round(x_span),
            "y": _round(y_span),
            "depth": _round(depth_span),
        },
        "atom_projection_count": len(projections),
        "atom_projections": projections[:MAX_PROJECTED_ATOMS],
        "atom_projections_truncated": truncated,
        "overlap_candidates": overlap_pairs,
        "health": {"ok": not warnings, "warnings": warnings},
    }


def _lattice_vectors(lattice: LatticeSpec) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    alpha = math.radians(lattice.alpha)
    beta = math.radians(lattice.beta)
    gamma = math.radians(lattice.gamma)
    a_vec = (lattice.a, 0.0, 0.0)
    b_vec = (lattice.b * math.cos(gamma), lattice.b * math.sin(gamma), 0.0)
    cx = lattice.c * math.cos(beta)
    cy = lattice.c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / max(math.sin(gamma), 1e-12)
    cz2 = max(lattice.c * lattice.c - cx * cx - cy * cy, 0.0)
    c_vec = (cx, cy, math.sqrt(cz2))
    return a_vec, b_vec, c_vec


def _lattice_volume(lattice: LatticeSpec) -> float:
    alpha = math.radians(lattice.alpha)
    beta = math.radians(lattice.beta)
    gamma = math.radians(lattice.gamma)
    factor = math.sqrt(
        max(
            1
            + 2 * math.cos(alpha) * math.cos(beta) * math.cos(gamma)
            - math.cos(alpha) ** 2
            - math.cos(beta) ** 2
            - math.cos(gamma) ** 2,
            0.0,
        )
    )
    return _round(lattice.a * lattice.b * lattice.c * factor)


def _fractional_to_cartesian(
    fractional: tuple[float, float, float],
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float, float]:
    return tuple(
        fractional[0] * vectors[0][index] + fractional[1] * vectors[1][index] + fractional[2] * vectors[2][index]
        for index in range(3)
    )


def _audit_atom(atom: dict[str, Any]) -> dict[str, Any]:
    """Return rounded atom coordinates for audit reports."""

    payload = {
        "id": atom.get("id"),
        "element": atom.get("element"),
        "xyz_angstrom": _round_tuple(tuple(float(value) for value in atom["xyz"])),
    }
    if "fractional" in atom:
        payload["fractional"] = _round_tuple(tuple(float(value) for value in atom["fractional"]))
    return payload


def _projection_overlap_pairs(projections: list[dict[str, Any]], *, threshold: float = 0.12, limit: int = 12) -> list[dict[str, Any]]:
    """Return likely 2D overlap candidates for a projected view."""

    pairs: list[dict[str, Any]] = []
    for index, atom in enumerate(projections):
        for other in projections[index + 1 :]:
            dx = float(atom["x"]) - float(other["x"])
            dy = float(atom["y"]) - float(other["y"])
            distance_2d = math.sqrt(dx * dx + dy * dy)
            if distance_2d <= threshold:
                pairs.append(
                    {
                        "atom1": atom.get("atom_id"),
                        "atom2": other.get("atom_id"),
                        "distance_2d_angstrom": _round(distance_2d),
                        "depth_delta_angstrom": _round(abs(float(atom["depth"]) - float(other["depth"]))),
                    }
                )
                if len(pairs) >= limit:
                    return pairs
    return pairs


def _view_framing(
    *,
    center: tuple[float, float, float],
    direction: tuple[float, float, float],
    x_span: float,
    y_span: float,
    depth_span: float,
    radius: float,
    padding_fraction: float = 0.15,
) -> dict[str, Any]:
    """Return deterministic camera/framing parameters for reproducing a view."""

    padded_x = max(x_span * (1.0 + 2.0 * padding_fraction), 1.0)
    padded_y = max(y_span * (1.0 + 2.0 * padding_fraction), 1.0)
    padded_depth = max(depth_span * (1.0 + 2.0 * padding_fraction), 1.0)
    distance = max(radius * 3.0, padded_depth * 2.0, 10.0)
    position = tuple(center[index] + direction[index] * distance for index in range(3))
    near_clip = max(0.01, distance - padded_depth)
    far_clip = distance + padded_depth
    return {
        "camera_position": _round_tuple(position),
        "camera_distance_angstrom": _round(distance),
        "orthographic_width_angstrom": _round(padded_x),
        "orthographic_height_angstrom": _round(padded_y),
        "depth_range_angstrom": _round(padded_depth),
        "near_clip_angstrom": _round(near_clip),
        "far_clip_angstrom": _round(far_clip),
        "padding_fraction": padding_fraction,
        "projection_units": "angstrom_relative_to_target",
    }


def _bond_order(bond_type: str) -> float:
    return BOND_ORDER_BY_TYPE.get(bond_type, 1.0)


def _add_connectivity(connectivity: dict[str, dict[str, Any]], atom_id: str, other_atom_id: str, bond_order: float) -> None:
    item = connectivity[atom_id]
    item["degree"] += 1
    item["bond_order_sum"] += bond_order
    item["bonded_atoms"].append(other_atom_id)


def _bond_angle_rows(
    atom_map: dict[str, tuple[float, float, float]],
    connectivity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return all bonded A-B-C angles around each center atom."""

    rows: list[dict[str, Any]] = []
    for center_id in sorted(connectivity):
        neighbors = sorted(
            neighbor
            for neighbor in connectivity[center_id].get("bonded_atoms", []) or []
            if neighbor in atom_map and center_id in atom_map
        )
        for left_index, atom1 in enumerate(neighbors):
            for atom3 in neighbors[left_index + 1 :]:
                rows.append(
                    {
                        "atom1": atom1,
                        "center_atom": center_id,
                        "atom3": atom3,
                        "angle_deg": _round(_angle_degrees(atom_map[atom1], atom_map[center_id], atom_map[atom3])),
                    }
                )
                if len(rows) >= MAX_HEALTH_DETAIL_ROWS:
                    return rows
    return rows


def _dihedral_angle_rows(
    atom_map: dict[str, tuple[float, float, float]],
    connectivity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return all bonded A-B-C-D dihedrals around each central bond."""

    rows: list[dict[str, Any]] = []
    seen_central_bonds: set[frozenset[str]] = set()
    for atom2 in sorted(connectivity):
        for atom3 in sorted(connectivity[atom2].get("bonded_atoms", []) or []):
            central = frozenset((atom2, atom3))
            if atom2 == atom3 or central in seen_central_bonds:
                continue
            seen_central_bonds.add(central)
            if atom2 not in atom_map or atom3 not in atom_map:
                continue
            left_neighbors = sorted(
                atom1
                for atom1 in connectivity.get(atom2, {}).get("bonded_atoms", []) or []
                if atom1 != atom3 and atom1 in atom_map
            )
            right_neighbors = sorted(
                atom4
                for atom4 in connectivity.get(atom3, {}).get("bonded_atoms", []) or []
                if atom4 != atom2 and atom4 in atom_map
            )
            for atom1 in left_neighbors:
                for atom4 in right_neighbors:
                    if atom1 == atom4:
                        continue
                    rows.append(
                        {
                            "atom1": atom1,
                            "atom2": atom2,
                            "atom3": atom3,
                            "atom4": atom4,
                            "angle_deg": _round(
                                _dihedral_degrees(
                                    atom_map[atom1],
                                    atom_map[atom2],
                                    atom_map[atom3],
                                    atom_map[atom4],
                                )
                            ),
                        }
                    )
                    if len(rows) >= MAX_HEALTH_DETAIL_ROWS:
                        return rows
    return rows


def _nonbonded_close_contacts(
    atom_rows: list[dict[str, Any]],
    element_map: dict[str, str],
    bonded_pairs: set[frozenset[str]],
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    for index, atom in enumerate(atom_rows):
        atom_id = atom["id"]
        atom_xyz = atom["xyz"]
        for other in atom_rows[index + 1 :]:
            other_id = other["id"]
            if frozenset((atom_id, other_id)) in bonded_pairs:
                continue
            threshold = _nonbonded_close_threshold(element_map.get(atom_id), element_map.get(other_id))
            distance = _distance(atom_xyz, other["xyz"])
            if distance < threshold:
                contacts.append(
                    {
                        "atom1": atom_id,
                        "atom2": other_id,
                        "distance_angstrom": _round(distance),
                        "threshold_angstrom": _round(threshold),
                    }
                )
                if len(contacts) >= limit:
                    return contacts
    return contacts


def _nonbonded_close_threshold(element1: str | None, element2: str | None) -> float:
    radius1 = COVALENT_RADII_ANGSTROM.get(element1 or "", 0.75)
    radius2 = COVALENT_RADII_ANGSTROM.get(element2 or "", 0.75)
    return max(0.55, 0.65 * (radius1 + radius2))


def _fingerprint(payload: dict[str, Any]) -> str:
    """Return a stable short fingerprint for a model spec."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": _round(min(values)),
        "max": _round(max(values)),
        "mean": _round(sum(values) / len(values)),
    }


def _stats_with_count(values: list[float]) -> dict[str, float | int] | None:
    stats = _stats(values)
    if stats is None:
        return None
    return {**stats, "count": len(values)}


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def _angle_degrees(
    atom1: tuple[float, float, float],
    center: tuple[float, float, float],
    atom3: tuple[float, float, float],
) -> float:
    left = _subtract(atom1, center)
    right = _subtract(atom3, center)
    left_len = math.sqrt(_dot(left, left))
    right_len = math.sqrt(_dot(right, right))
    if left_len <= 1e-12 or right_len <= 1e-12:
        return 0.0
    cosine = max(-1.0, min(1.0, _dot(left, right) / (left_len * right_len)))
    return math.degrees(math.acos(cosine))


def _dihedral_degrees(
    atom1: tuple[float, float, float],
    atom2: tuple[float, float, float],
    atom3: tuple[float, float, float],
    atom4: tuple[float, float, float],
) -> float:
    b0 = _subtract(atom1, atom2)
    b1 = _subtract(atom3, atom2)
    b2 = _subtract(atom4, atom3)
    b1_unit = _normalize(b1)
    v = _subtract(b0, _scale_tuple(b1_unit, _dot(b0, b1_unit)))
    w = _subtract(b2, _scale_tuple(b1_unit, _dot(b2, b1_unit)))
    if _dot(v, v) <= 1e-20 or _dot(w, w) <= 1e-20:
        return 0.0
    x = _dot(v, w)
    y = _dot(_cross(b1_unit, v), w)
    return math.degrees(math.atan2(y, x))


def _scale_tuple(value: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return tuple(item * factor for item in value)


def _subtract(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(a[index] - b[index] for index in range(3))


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum(a[index] * b[index] for index in range(3))


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(item * item for item in value))
    if length <= 1e-12:
        return (0.0, 0.0, 0.0)
    return tuple(item / length for item in value)


def _round_tuple(value: tuple[float, float, float]) -> list[float]:
    return [_round(item) for item in value]


def _round(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0.0 else rounded


def _round_significant(value: float, digits: int = 6) -> float:
    return float(f"{float(value):.{digits}g}")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
