"""模型规格的小型 JSON 可序列化差异助手。

此模块提供了比较模型规格并生成差异的功能。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from material_studio_mcp_server.specs.crystal import CrystalSpec
from material_studio_mcp_server.specs.molecule import MoleculeSpec
from material_studio_mcp_server.specs.project import ModelSpec


def diff_specs(old: ModelSpec, new: ModelSpec) -> list[str]:
    """返回适合 API 响应的紧凑语义差异。

    参数:
        old: 旧模型规格
        new: 新模型规格

    返回:
        差异列表
    """
    changes: list[str] = []
    if old.model_type != new.model_type:
        changes.append(f"model_type {old.model_type.value}->{new.model_type.value}")

    if isinstance(old.model, MoleculeSpec) and isinstance(new.model, MoleculeSpec):
        changes.extend(_diff_molecule(old.model, new.model))
    elif isinstance(old.model, CrystalSpec) and isinstance(new.model, CrystalSpec):
        changes.extend(_diff_crystal(old.model, new.model))
    elif old.model.model_dump(mode="json") != new.model.model_dump(mode="json"):
        changes.append("model changed")

    old_sim = old.simulation.model_dump(mode="json") if old.simulation else None
    new_sim = new.simulation.model_dump(mode="json") if new.simulation else None
    if old_sim != new_sim:
        changes.append("simulation changed")
    if old.outputs != new.outputs:
        changes.append("outputs changed")
    return changes


def summarize_spec_delta(old: ModelSpec, new: ModelSpec, *, diff: list[str] | None = None) -> dict[str, Any]:
    """Return a compact structured delta for live-modeling client review."""

    old_counts = _element_counts(old)
    new_counts = _element_counts(new)
    summary: dict[str, Any] = {
        "available": True,
        "project_id": new.project_id,
        "base_revision": old.revision,
        "new_revision": new.revision,
        "model_type_before": old.model_type.value,
        "model_type_after": new.model_type.value,
        "model_type_changed": old.model_type != new.model_type,
        "diff": diff if diff is not None else diff_specs(old, new),
        "atom_count_before": _atom_count(old),
        "atom_count_after": _atom_count(new),
        "atom_count_delta": _atom_count(new) - _atom_count(old),
        "element_counts_before": old_counts,
        "element_counts_after": new_counts,
        "element_count_delta": _counter_delta(old_counts, new_counts),
        "metadata_changed_keys": _changed_mapping_keys(old.metadata or {}, new.metadata or {}),
        "output_changed_keys": _changed_mapping_keys(old.outputs or {}, new.outputs or {}),
        "simulation": _simulation_delta(old, new),
    }
    if isinstance(old.model, MoleculeSpec) and isinstance(new.model, MoleculeSpec):
        summary["molecule"] = _molecule_delta(old.model, new.model)
    elif isinstance(old.model, CrystalSpec) and isinstance(new.model, CrystalSpec):
        summary["crystal"] = _crystal_delta(old.model, new.model)
    return summary


def _diff_molecule(old: MoleculeSpec, new: MoleculeSpec) -> list[str]:
    """比较分子规格。"""
    changes: list[str] = []
    old_atoms = {atom.id: atom for atom in old.atoms}
    new_atoms = {atom.id: atom for atom in new.atoms}
    for atom_id in sorted(new_atoms.keys() - old_atoms.keys()):
        changes.append(f"add_atom {atom_id}")
    for atom_id in sorted(old_atoms.keys() - new_atoms.keys()):
        changes.append(f"delete_atom {atom_id}")
    for atom_id in sorted(old_atoms.keys() & new_atoms.keys()):
        old_atom = old_atoms[atom_id]
        new_atom = new_atoms[atom_id]
        if old_atom.element != new_atom.element:
            changes.append(f"substitute_atom {atom_id}->{new_atom.element}")
        elif old_atom.xyz_angstrom != new_atom.xyz_angstrom:
            changes.append(f"set_atom_position {atom_id}")

    old_bonds = {_bond_key(bond.model_dump(mode="json")) for bond in old.bonds}
    new_bonds = {_bond_key(bond.model_dump(mode="json")) for bond in new.bonds}
    for atom1, atom2, bond_type in sorted(new_bonds - old_bonds):
        changes.append(f"add_bond {atom1}-{atom2} {bond_type}")
    for atom1, atom2, bond_type in sorted(old_bonds - new_bonds):
        changes.append(f"delete_bond {atom1}-{atom2} {bond_type}")
    if old.total_charge != new.total_charge:
        changes.append(f"set_total_charge {new.total_charge}")
    if old.spin_multiplicity != new.spin_multiplicity:
        changes.append(f"set_spin_multiplicity {new.spin_multiplicity}")
    return changes


def _diff_crystal(old: CrystalSpec, new: CrystalSpec) -> list[str]:
    """比较晶体规格。"""
    changes: list[str] = []
    if old.lattice != new.lattice:
        changes.append("set_lattice")
    old_atoms = {atom.id: atom for atom in old.basis_atoms}
    new_atoms = {atom.id: atom for atom in new.basis_atoms}
    for atom_id in sorted(new_atoms.keys() - old_atoms.keys()):
        changes.append(f"add_atom {atom_id}")
    for atom_id in sorted(old_atoms.keys() - new_atoms.keys()):
        changes.append(f"delete_atom {atom_id}")
    for atom_id in sorted(old_atoms.keys() & new_atoms.keys()):
        if old_atoms[atom_id] != new_atoms[atom_id]:
            changes.append(f"update_atom {atom_id}")
    return changes


def _molecule_delta(old: MoleculeSpec, new: MoleculeSpec) -> dict[str, Any]:
    old_atoms = {atom.id: atom for atom in old.atoms}
    new_atoms = {atom.id: atom for atom in new.atoms}
    substituted_atoms = []
    moved_atoms = []
    for atom_id in sorted(old_atoms.keys() & new_atoms.keys()):
        old_atom = old_atoms[atom_id]
        new_atom = new_atoms[atom_id]
        if old_atom.element != new_atom.element:
            substituted_atoms.append({"atom_id": atom_id, "from": old_atom.element, "to": new_atom.element})
        if old_atom.xyz_angstrom != new_atom.xyz_angstrom:
            moved_atoms.append(
                {
                    "atom_id": atom_id,
                    "from_xyz_angstrom": _vector_json(old_atom.xyz_angstrom),
                    "to_xyz_angstrom": _vector_json(new_atom.xyz_angstrom),
                }
            )

    old_bonds = {_bond_key(bond.model_dump(mode="json")) for bond in old.bonds}
    new_bonds = {_bond_key(bond.model_dump(mode="json")) for bond in new.bonds}
    old_pairs = {(atom1, atom2): bond_type for atom1, atom2, bond_type in old_bonds}
    new_pairs = {(atom1, atom2): bond_type for atom1, atom2, bond_type in new_bonds}
    changed_bond_types = []
    for pair in sorted(old_pairs.keys() & new_pairs.keys()):
        if old_pairs[pair] != new_pairs[pair]:
            changed_bond_types.append(
                {"atom1": pair[0], "atom2": pair[1], "from": old_pairs[pair], "to": new_pairs[pair]}
            )

    return {
        "name_before": old.name,
        "name_after": new.name,
        "added_atoms": _atom_payloads(new_atoms, sorted(new_atoms.keys() - old_atoms.keys()), coordinate_kind="xyz_angstrom"),
        "deleted_atoms": _atom_payloads(old_atoms, sorted(old_atoms.keys() - new_atoms.keys()), coordinate_kind="xyz_angstrom"),
        "substituted_atoms": substituted_atoms,
        "moved_atoms": moved_atoms,
        "bond_count_before": len(old.bonds),
        "bond_count_after": len(new.bonds),
        "bond_count_delta": len(new.bonds) - len(old.bonds),
        "added_bonds": [_bond_payload(atom1, atom2, bond_type) for atom1, atom2, bond_type in sorted(new_bonds - old_bonds)],
        "deleted_bonds": [_bond_payload(atom1, atom2, bond_type) for atom1, atom2, bond_type in sorted(old_bonds - new_bonds)],
        "changed_bond_types": changed_bond_types,
        "total_charge_before": old.total_charge,
        "total_charge_after": new.total_charge,
        "spin_multiplicity_before": old.spin_multiplicity,
        "spin_multiplicity_after": new.spin_multiplicity,
    }


def _crystal_delta(old: CrystalSpec, new: CrystalSpec) -> dict[str, Any]:
    old_atoms = {atom.id: atom for atom in old.basis_atoms}
    new_atoms = {atom.id: atom for atom in new.basis_atoms}
    old_vectors = _lattice_vectors(old.lattice)
    new_vectors = _lattice_vectors(new.lattice)
    substituted_atoms = []
    moved_atoms = []
    cartesian_moved_atoms = []
    cartesian_preserved_atoms = []
    max_cartesian_displacement = 0.0
    for atom_id in sorted(old_atoms.keys() & new_atoms.keys()):
        old_atom = old_atoms[atom_id]
        new_atom = new_atoms[atom_id]
        if old_atom.element != new_atom.element:
            substituted_atoms.append({"atom_id": atom_id, "from": old_atom.element, "to": new_atom.element})
        old_cartesian = _fractional_to_cartesian(_fractional_tuple(old_atom.fractional), old_vectors)
        new_cartesian = _fractional_to_cartesian(_fractional_tuple(new_atom.fractional), new_vectors)
        cartesian_displacement = _distance(old_cartesian, new_cartesian)
        max_cartesian_displacement = max(max_cartesian_displacement, cartesian_displacement)
        if old_atom.fractional != new_atom.fractional:
            row = {
                "atom_id": atom_id,
                "from_fractional": _vector_json(old_atom.fractional),
                "to_fractional": _vector_json(new_atom.fractional),
                "from_cartesian_angstrom": _round_tuple(old_cartesian),
                "to_cartesian_angstrom": _round_tuple(new_cartesian),
                "cartesian_displacement_angstrom": round(cartesian_displacement, 10),
            }
            moved_atoms.append(row)
            if cartesian_displacement <= 1e-8:
                cartesian_preserved_atoms.append(row)
            else:
                cartesian_moved_atoms.append(row)
        elif cartesian_displacement > 1e-8:
            cartesian_moved_atoms.append(
                {
                    "atom_id": atom_id,
                    "from_fractional": _vector_json(old_atom.fractional),
                    "to_fractional": _vector_json(new_atom.fractional),
                    "from_cartesian_angstrom": _round_tuple(old_cartesian),
                    "to_cartesian_angstrom": _round_tuple(new_cartesian),
                    "cartesian_displacement_angstrom": round(cartesian_displacement, 10),
                }
            )

    old_lattice = old.lattice.model_dump(mode="json")
    new_lattice = new.lattice.model_dump(mode="json")
    return {
        "name_before": old.name,
        "name_after": new.name,
        "lattice_changed": old.lattice != new.lattice,
        "lattice_before": old_lattice,
        "lattice_after": new_lattice,
        "lattice_delta": _numeric_mapping_delta(old_lattice, new_lattice),
        "added_atoms": _atom_payloads(new_atoms, sorted(new_atoms.keys() - old_atoms.keys()), coordinate_kind="fractional"),
        "deleted_atoms": _atom_payloads(old_atoms, sorted(old_atoms.keys() - new_atoms.keys()), coordinate_kind="fractional"),
        "substituted_atoms": substituted_atoms,
        "moved_atoms": moved_atoms,
        "fractional_coordinate_update_count": len(moved_atoms),
        "cartesian_moved_atoms": cartesian_moved_atoms,
        "cartesian_moved_atom_count": len(cartesian_moved_atoms),
        "cartesian_preserved_atoms": cartesian_preserved_atoms[:20],
        "cartesian_preserved_atom_count": len(cartesian_preserved_atoms),
        "max_cartesian_displacement_angstrom": round(max_cartesian_displacement, 10),
        "fractional_rescale_preserved_cartesian": bool(moved_atoms and not cartesian_moved_atoms),
        "operation_count_before": len(old.operations or []),
        "operation_count_after": len(new.operations or []),
    }


def _bond_key(bond: dict[str, Any]) -> tuple[str, str, str]:
    """生成键的唯一标识。"""
    atom1, atom2 = sorted([str(bond["atom1"]), str(bond["atom2"])])
    return atom1, atom2, str(bond["type"])


def _atom_count(spec: ModelSpec) -> int:
    if isinstance(spec.model, MoleculeSpec):
        return len(spec.model.atoms)
    if isinstance(spec.model, CrystalSpec):
        return len(spec.model.basis_atoms)
    return 0


def _element_counts(spec: ModelSpec) -> dict[str, int]:
    if isinstance(spec.model, MoleculeSpec):
        counts = Counter(atom.element for atom in spec.model.atoms)
    elif isinstance(spec.model, CrystalSpec):
        counts = Counter(atom.element for atom in spec.model.basis_atoms)
    else:
        counts = Counter()
    return dict(sorted(counts.items()))


def _counter_delta(old: dict[str, int], new: dict[str, int]) -> dict[str, int]:
    keys = sorted(set(old) | set(new))
    return {
        key: int(new.get(key, 0)) - int(old.get(key, 0))
        for key in keys
        if int(new.get(key, 0)) != int(old.get(key, 0))
    }


def _simulation_delta(old: ModelSpec, new: ModelSpec) -> dict[str, Any]:
    old_sim = old.simulation.model_dump(mode="json") if old.simulation else None
    new_sim = new.simulation.model_dump(mode="json") if new.simulation else None
    if isinstance(old_sim, dict) and isinstance(new_sim, dict):
        changed_fields = _changed_mapping_keys(old_sim, new_sim)
    elif old_sim != new_sim:
        changed_fields = ["simulation"]
    else:
        changed_fields = []
    return {
        "changed": old_sim != new_sim,
        "before": old_sim,
        "after": new_sim,
        "changed_fields": changed_fields,
    }


def _changed_mapping_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    return sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))


def _numeric_mapping_delta(old: dict[str, Any], new: dict[str, Any]) -> dict[str, float]:
    delta: dict[str, float] = {}
    for key in sorted(set(old) | set(new)):
        old_value = old.get(key)
        new_value = new.get(key)
        if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)) and old_value != new_value:
            delta[key] = round(float(new_value) - float(old_value), 10)
    return delta


def _atom_payloads(atoms: dict[str, Any], atom_ids: list[str], *, coordinate_kind: str) -> list[dict[str, Any]]:
    rows = []
    for atom_id in atom_ids:
        atom = atoms[atom_id]
        row = {"atom_id": atom_id, "element": atom.element}
        coordinate = getattr(atom, coordinate_kind, None)
        if coordinate is not None:
            row[coordinate_kind] = _vector_json(coordinate)
        rows.append(row)
    return rows


def _bond_payload(atom1: str, atom2: str, bond_type: str) -> dict[str, str]:
    return {"atom1": atom1, "atom2": atom2, "type": bond_type}


def _vector_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _fractional_tuple(value: Any) -> tuple[float, float, float]:
    return float(value.x), float(value.y), float(value.z)


def _lattice_vectors(lattice: Any) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    alpha = math.radians(float(lattice.alpha))
    beta = math.radians(float(lattice.beta))
    gamma = math.radians(float(lattice.gamma))
    a_vec = (float(lattice.a), 0.0, 0.0)
    b_vec = (float(lattice.b) * math.cos(gamma), float(lattice.b) * math.sin(gamma), 0.0)
    cx = float(lattice.c) * math.cos(beta)
    cy = float(lattice.c) * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / max(math.sin(gamma), 1e-12)
    cz2 = max(float(lattice.c) * float(lattice.c) - cx * cx - cy * cy, 0.0)
    c_vec = (cx, cy, math.sqrt(cz2))
    return a_vec, b_vec, c_vec


def _fractional_to_cartesian(
    fractional: tuple[float, float, float],
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float, float]:
    return tuple(
        fractional[0] * vectors[0][index] + fractional[1] * vectors[1][index] + fractional[2] * vectors[2][index]
        for index in range(3)
    )


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return math.sqrt(sum((first[index] - second[index]) ** 2 for index in range(3)))


def _round_tuple(values: tuple[float, float, float]) -> list[float]:
    return [round(float(value), 10) for value in values]
