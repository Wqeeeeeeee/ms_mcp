"""Trusted DMol3 molecular geometry-optimization result promotion."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .parsers.dmol3_log import (
    DMol3GeometryResultPayload,
    validate_dmol3_output_evidence,
)
from .specs.dmol3 import DMol3GeometryOptimizationSpec, DMol3YesNo
from .specs.molecule import AtomSpec, MoleculeSpec
from .specs.project import ModelSpec


DMOL3_RELAXATION_RECEIPT_SCHEMA = (
    "material_studio_dmol3_relaxation_receipt_v1"
)


def molecule_structure_sha256(molecule: MoleculeSpec) -> str:
    """Hash molecular identity, topology, and Cartesian geometry canonically."""

    payload = {
        "name": molecule.name,
        "atoms": [
            {
                "id": atom.id,
                "element": atom.element,
                "xyz_angstrom": [
                    round(float(atom.xyz_angstrom.x), 12),
                    round(float(atom.xyz_angstrom.y), 12),
                    round(float(atom.xyz_angstrom.z), 12),
                ],
                "charge": (
                    round(float(atom.charge), 12)
                    if atom.charge is not None
                    else None
                ),
            }
            for atom in sorted(molecule.atoms, key=lambda item: item.id)
        ],
        "bonds": sorted(
            [
                {
                "atom1": min(bond.atom1, bond.atom2),
                "atom2": max(bond.atom1, bond.atom2),
                "type": bond.type,
                }
                for bond in molecule.bonds
            ],
            key=lambda item: (item["atom1"], item["atom2"], item["type"]),
        ),
        "total_charge": molecule.total_charge,
        "spin_multiplicity": molecule.spin_multiplicity,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def build_dmol3_relaxed_revision_spec(
    base_spec: ModelSpec,
    *,
    simulation: DMol3GeometryOptimizationSpec,
    result_payload: dict[str, Any],
    input_structure: str | Path,
    output_structure: str | Path,
    output_report: str | Path,
    script_sha256: str,
    target_revision: int | None = None,
) -> tuple[ModelSpec, dict[str, Any]]:
    """Promote a converged, identity-preserving DMol3 result."""

    if not isinstance(base_spec.model, MoleculeSpec):
        raise ValueError(
            "DMol3 relaxed-result promotion currently requires a molecule ModelSpec"
        )
    if simulation.task != "GeometryOptimization":
        raise ValueError(
            "DMol3 relaxed-result promotion requires GeometryOptimization"
        )
    if (
        base_spec.model.total_charge is not None
        and simulation.charge != base_spec.model.total_charge
    ):
        raise ValueError(
            "DMol3 simulation charge does not match molecule total_charge"
        )
    if target_revision is not None and target_revision <= base_spec.revision:
        raise ValueError("DMol3 target revision must advance the source revision")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", script_sha256):
        raise ValueError("DMol3 script_sha256 must contain 64 hexadecimal digits")

    parsed = DMol3GeometryResultPayload.model_validate(result_payload)
    if parsed.project_id != base_spec.project_id:
        raise ValueError("DMol3 result project_id does not match the source project")
    if parsed.base_revision != base_spec.revision:
        raise ValueError("DMol3 result base_revision does not match the source revision")
    if parsed.converged is not True:
        raise ValueError(
            "DMol3 geometry optimization did not converge; result cannot become a new revision"
        )
    charts_expected = (
        simulation.create_energy_evolution_chart is DMol3YesNo.YES
    )
    if parsed.energy_evolution_charts_requested is not charts_expected:
        raise ValueError(
            "DMol3 result chart contract does not match the executed simulation spec"
        )

    paths = {
        "input_structure": Path(input_structure).expanduser().resolve(),
        "output_structure": Path(output_structure).expanduser().resolve(),
        "output_report": Path(output_report).expanduser().resolve(),
    }
    payload_paths = {
        "input_structure": Path(parsed.input_structure).expanduser().resolve(),
        "output_structure": Path(parsed.output_structure).expanduser().resolve(),
        "output_report": Path(parsed.output_report).expanduser().resolve(),
    }
    for key, path in paths.items():
        if payload_paths[key] != path:
            raise ValueError(
                f"DMol3 result {key} is not bound to the promoted artifact"
            )
        if not path.is_file():
            raise ValueError(f"DMol3 required artifact was not found: {path}")

    output_evidence = validate_dmol3_output_evidence(
        parsed,
        source_molecule=base_spec.model,
        output_structure=paths["output_structure"],
        output_report=paths["output_report"],
    )
    if not output_evidence.get("verified"):
        raise ValueError(
            "DMol3 exported structure/report evidence is not consistent with "
            "the tagged optimized geometry: "
            + "; ".join(str(item) for item in output_evidence.get("errors") or [])
        )

    source_atoms = {atom.id: atom for atom in base_spec.model.atoms}
    optimized_atoms = {atom.id: atom for atom in parsed.optimized_atoms}
    missing_ids = sorted(set(source_atoms) - set(optimized_atoms))
    extra_ids = sorted(set(optimized_atoms) - set(source_atoms))
    if missing_ids or extra_ids:
        raise ValueError(
            "DMol3 optimized atom identities differ from the source revision: "
            f"missing={missing_ids}, extra={extra_ids}"
        )
    element_mismatches = [
        atom_id
        for atom_id in sorted(source_atoms)
        if source_atoms[atom_id].element != optimized_atoms[atom_id].element
    ]
    if element_mismatches:
        raise ValueError(
            "DMol3 optimized structure changed atom elements for IDs: "
            + ", ".join(element_mismatches)
        )

    promoted_atoms = [
        AtomSpec(
            id=source_atom.id,
            element=source_atom.element,
            xyz_angstrom=optimized_atoms[source_atom.id].xyz_angstrom,
            charge=source_atom.charge,
        )
        for source_atom in base_spec.model.atoms
    ]
    promoted_molecule = base_spec.model.model_copy(
        update={"atoms": promoted_atoms},
        deep=True,
    )
    source_hash = molecule_structure_sha256(base_spec.model)
    output_hash = molecule_structure_sha256(promoted_molecule)
    source_atom_id_hash = _atom_id_sha256(source_atoms)
    output_atom_id_hash = _atom_id_sha256(optimized_atoms)
    displacements = {
        atom_id: _cartesian_distance(
            source_atoms[atom_id].xyz_angstrom.as_tuple(),
            optimized_atoms[atom_id].xyz_angstrom.as_tuple(),
        )
        for atom_id in source_atoms
    }
    max_displacement = max(displacements.values(), default=0.0)

    receipt = {
        "schema_version": DMOL3_RELAXATION_RECEIPT_SCHEMA,
        "backend": "Materials Studio 20.1 DMol3 GeometryOptimization",
        "source_project_id": base_spec.project_id,
        "source_revision": base_spec.revision,
        "target_revision": target_revision,
        "task": simulation.task,
        "converged": True,
        "geometry_relaxation_verified": True,
        "output_evidence": output_evidence,
        "quality": simulation.quality.value,
        "theory_level": simulation.theory_level.value,
        "geometry_optimization_quality": (
            simulation.geometry_optimization_quality or simulation.quality
        ).value,
        "charge": simulation.charge,
        "use_symmetry": simulation.use_symmetry.value,
        "source_structure_sha256": source_hash,
        "output_structure_sha256": output_hash,
        "source_atom_id_sha256": source_atom_id_hash,
        "output_atom_id_sha256": output_atom_id_hash,
        "atom_identity_preserved": source_atom_id_hash == output_atom_id_hash,
        "atom_elements_preserved": True,
        "max_cartesian_displacement_angstrom": max_displacement,
        "total_energy_kcal_per_mol": parsed.total_energy_kcal_per_mol,
        "input_structure": str(paths["input_structure"]),
        "input_structure_sha256": _file_sha256(paths["input_structure"]),
        "output_structure": str(paths["output_structure"]),
        "output_structure_file_sha256": _file_sha256(
            paths["output_structure"]
        ),
        "output_report": str(paths["output_report"]),
        "output_report_sha256": _file_sha256(paths["output_report"]),
        "script_sha256": script_sha256.lower(),
    }
    metadata = dict(base_spec.metadata or {})
    history = [
        dict(item)
        for item in metadata.get("dmol3_geometry_optimization_history", []) or []
        if isinstance(item, dict)
    ]
    history.append(receipt)
    metadata.update(
        {
            "dmol3_geometry_optimization_history": history,
            "last_dmol3_geometry_optimization": receipt,
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
        "DMol3 GeometryOptimization converged; the promoted molecular structure "
        "still requires fresh structural and GUI-view audits."
    )
    relaxed_spec = base_spec.model_copy(
        update={
            "model": promoted_molecule,
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


def _atom_id_sha256(atoms: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(atoms), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _cartesian_distance(
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> float:
    return math.sqrt(
        sum((float(new) - float(old)) ** 2 for old, new in zip(source, target))
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
