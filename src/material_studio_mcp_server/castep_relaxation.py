"""Trusted CASTEP geometry-optimization result promotion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .specs.castep import CastepCellOptimization, CastepEnergySpec, CastepTask
from .specs.crystal import BasisAtomSpec, CrystalOperation, CrystalSpec, LatticeSpec
from .specs.project import ModelSpec


CASTEP_RELAXATION_RECEIPT_SCHEMA = "material_studio_castep_relaxation_receipt_v1"


def crystal_structure_sha256(crystal: CrystalSpec) -> str:
    """Hash lattice, atom identity, and fractional positions canonically."""

    payload = {
        "lattice": {
            key: round(float(getattr(crystal.lattice, key)), 12)
            for key in ("a", "b", "c", "alpha", "beta", "gamma")
        },
        "atoms": [
            {
                "id": atom.id,
                "element": atom.element,
                "fractional": [
                    round(float(atom.fractional.x), 12),
                    round(float(atom.fractional.y), 12),
                    round(float(atom.fractional.z), 12),
                ],
            }
            for atom in sorted(crystal.basis_atoms, key=lambda item: item.id)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_relaxed_revision_spec(
    base_spec: ModelSpec,
    *,
    simulation: CastepEnergySpec,
    parsed_cif: dict[str, Any],
    result_payload: dict[str, Any],
    output_structure: str | Path,
    output_report: str | Path,
    script_sha256: str,
    target_revision: int | None = None,
) -> tuple[ModelSpec, dict[str, Any]]:
    """Promote a converged, identity-preserving CASTEP result to a new spec."""

    if not isinstance(base_spec.model, CrystalSpec):
        raise ValueError("CASTEP relaxed-result promotion requires a crystal ModelSpec")
    if simulation.task is not CastepTask.GEOMETRY_OPTIMIZATION:
        raise ValueError("CASTEP relaxed-result promotion requires GeometryOptimization")
    if not parsed_cif.get("ok"):
        raise ValueError(
            "Optimized CIF did not pass parsing: "
            + "; ".join(str(item) for item in parsed_cif.get("errors", []) or [])
        )
    if result_payload.get("converged") is not True:
        raise ValueError(
            "CASTEP geometry optimization did not converge; result cannot become a new revision"
        )

    source_atoms = {atom.id: atom for atom in base_spec.model.basis_atoms}
    parsed_atoms = {
        str(atom.get("id")): atom for atom in parsed_cif.get("atoms", [])
    }
    missing_ids = sorted(set(source_atoms) - set(parsed_atoms))
    extra_ids = sorted(set(parsed_atoms) - set(source_atoms))
    if missing_ids or extra_ids:
        raise ValueError(
            "Optimized CIF atom identities differ from the source revision: "
            f"missing={missing_ids}, extra={extra_ids}"
        )
    element_mismatches = [
        atom_id
        for atom_id in sorted(source_atoms)
        if source_atoms[atom_id].element != parsed_atoms[atom_id].get("element")
    ]
    if element_mismatches:
        raise ValueError(
            "Optimized CIF changed atom elements for IDs: "
            + ", ".join(element_mismatches)
        )

    lattice = LatticeSpec.model_validate(parsed_cif.get("lattice"))
    cell_mode = simulation.cell_optimization or CastepCellOptimization.NONE
    lattice_deltas = {
        key: abs(
            float(getattr(lattice, key))
            - float(getattr(base_spec.model.lattice, key))
        )
        for key in ("a", "b", "c", "alpha", "beta", "gamma")
    }
    max_lattice_delta = max(lattice_deltas.values(), default=0.0)
    if cell_mode is CastepCellOptimization.NONE and max_lattice_delta > 1.0e-6:
        raise ValueError(
            "CASTEP reported CellOptimization=None but changed the lattice "
            f"(max delta {max_lattice_delta:g})"
        )

    relaxed_atoms = [
        BasisAtomSpec.model_validate(
            {
                "id": atom_id,
                "element": source_atoms[atom_id].element,
                "fractional": [
                    _wrapped_fractional(value)
                    for value in _fractional_values(
                        parsed_atoms[atom_id]["fractional"]
                    )
                ],
            }
        )
        for atom_id in sorted(source_atoms)
    ]
    operation = CrystalOperation(
        type="castep_geometry_optimization",
        parameters={
            "source_revision": base_spec.revision,
            "converged": True,
            "cell_optimization": cell_mode.value,
            "materials_studio_api_contract": "Materials Studio 20.1",
        },
    )
    relaxed_crystal = CrystalSpec(
        name=base_spec.model.name,
        lattice=lattice,
        basis_atoms=relaxed_atoms,
        operations=[*base_spec.model.operations, operation],
    )
    source_hash = crystal_structure_sha256(base_spec.model)
    output_hash = crystal_structure_sha256(relaxed_crystal)
    source_atom_id_hash = _atom_id_sha256(source_atoms)
    output_atom_id_hash = _atom_id_sha256(parsed_atoms)
    structure_path = Path(output_structure).expanduser().resolve()
    report_path = Path(output_report).expanduser().resolve()
    receipt = {
        "schema_version": CASTEP_RELAXATION_RECEIPT_SCHEMA,
        "backend": "Materials Studio 20.1 CASTEP GeometryOptimization",
        "source_project_id": base_spec.project_id,
        "source_revision": base_spec.revision,
        "target_revision": target_revision,
        "task": simulation.task.value,
        "converged": True,
        "geometry_relaxation_verified": True,
        "cell_optimization": cell_mode.value,
        "optimization_algorithm": (
            simulation.optimization_algorithm.value
            if simulation.optimization_algorithm is not None
            else None
        ),
        "source_structure_sha256": source_hash,
        "output_structure_sha256": output_hash,
        "source_atom_id_sha256": source_atom_id_hash,
        "output_atom_id_sha256": output_atom_id_hash,
        "atom_identity_preserved": source_atom_id_hash == output_atom_id_hash,
        "atom_elements_preserved": True,
        "lattice_changed": max_lattice_delta > 1.0e-6,
        "max_lattice_delta": max_lattice_delta,
        "lattice_deltas": lattice_deltas,
        "total_energy_kcal_per_mol": result_payload.get(
            "total_energy_kcal_per_mol"
        ),
        "enthalpy_kcal_per_mol": result_payload.get("enthalpy_kcal_per_mol"),
        "output_structure": str(structure_path),
        "output_structure_file_sha256": _file_sha256(structure_path),
        "output_report": str(report_path),
        "output_report_sha256": _file_sha256(report_path),
        "script_sha256": script_sha256,
    }
    metadata = dict(base_spec.metadata or {})
    history = [
        dict(item)
        for item in metadata.get("castep_geometry_optimization_history", []) or []
        if isinstance(item, dict)
    ]
    history.append(receipt)
    metadata.update(
        {
            "castep_geometry_optimization_history": history,
            "last_castep_geometry_optimization": receipt,
            "pre_relaxation_scaffold": False,
            "unrelaxed_interface": False,
            "requires_geometry_relaxation": False,
            "geometry_relaxed": True,
            "calculation_ready": False,
            "calculation_readiness_requires_reaudit": True,
        }
    )
    outputs = {
        key: value
        for key, value in dict(base_spec.outputs or {}).items()
        if key != "output_file"
    }
    notes = [
        note
        for note in base_spec.acceptance.notes
        if "requires geometry relaxation" not in note.lower()
        and "pre-relaxation" not in note.lower()
    ]
    notes.append(
        "CASTEP GeometryOptimization converged; the promoted structure remains subject "
        "to a fresh structural, semiconductor, and GUI-view audit."
    )
    relaxed_spec = base_spec.model_copy(
        update={
            "model": relaxed_crystal,
            "simulation": simulation,
            "outputs": outputs,
            "metadata": metadata,
            "acceptance": base_spec.acceptance.model_copy(
                update={"require_convergence": True, "notes": notes}
            ),
        },
        deep=True,
    )
    return ModelSpec.model_validate(relaxed_spec.model_dump(mode="json")), receipt


def _wrapped_fractional(value: Any) -> float:
    numeric = float(value)
    wrapped = numeric % 1.0
    if abs(wrapped - 1.0) <= 1.0e-12 or abs(wrapped) <= 1.0e-12:
        return 0.0
    return wrapped


def _fractional_values(value: Any) -> tuple[Any, Any, Any]:
    if isinstance(value, dict):
        try:
            return value["x"], value["y"], value["z"]
        except KeyError as exc:
            raise ValueError("Parsed CIF fractional mapping requires x, y, and z") from exc
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return value[0], value[1], value[2]
    raise ValueError("Parsed CIF fractional coordinates require three values")


def _atom_id_sha256(atoms: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(atoms), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
