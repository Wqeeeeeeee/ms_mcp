"""CIF export for structured crystal specs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from material_studio_mcp_server.specs.crystal import CrystalSpec


def write_crystal_cif(crystal: CrystalSpec, output_path: str | Path, *, space_group: str = "P 1") -> Path:
    """Write a minimal CIF file from a structured crystal spec."""

    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lattice = crystal.lattice
    lines = [
        f"data_{_safe_token(crystal.name, fallback='structure')}",
        f"_symmetry_space_group_name_H-M '{space_group}'",
        f"_cell_length_a    {_format_float(lattice.a)}",
        f"_cell_length_b    {_format_float(lattice.b)}",
        f"_cell_length_c    {_format_float(lattice.c)}",
        f"_cell_angle_alpha {_format_float(lattice.alpha)}",
        f"_cell_angle_beta  {_format_float(lattice.beta)}",
        f"_cell_angle_gamma {_format_float(lattice.gamma)}",
        "",
        "loop_",
        "  _atom_site_label",
        "  _atom_site_type_symbol",
        "  _atom_site_fract_x",
        "  _atom_site_fract_y",
        "  _atom_site_fract_z",
    ]
    for atom in crystal.basis_atoms:
        lines.append(
            "  {label} {element} {x} {y} {z}".format(
                label=_safe_token(atom.id, fallback="Atom"),
                element=atom.element,
                x=_format_float(atom.fractional.x),
                y=_format_float(atom.fractional.y),
                z=_format_float(atom.fractional.z),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def crystal_cif_summary(crystal: CrystalSpec, output_path: str | Path) -> dict[str, Any]:
    """Return stable metadata for a CIF materialization."""

    return {
        "structure_format": "cif",
        "structure_path": str(Path(output_path).expanduser().resolve()),
        "crystal_name": crystal.name,
        "atom_count": len(crystal.basis_atoms),
        "lattice": crystal.lattice.model_dump(mode="json"),
        "elements": _element_counts(crystal),
    }


def _element_counts(crystal: CrystalSpec) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in crystal.basis_atoms:
        counts[atom.element] = counts.get(atom.element, 0) + 1
    return dict(sorted(counts.items()))


def _safe_token(value: str, *, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return safe.strip("_") or fallback


def _format_float(value: float) -> str:
    return f"{float(value):.10g}"
