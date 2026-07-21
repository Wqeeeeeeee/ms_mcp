from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from material_studio_mcp_server.benchmark_evaluation import (
    CompiledBlindTask,
    assert_coordinate_free_payload,
)
from material_studio_mcp_server.domains.surface import PLUGIN, PLUGIN_MANIFEST
from material_studio_mcp_server.orchestration import CapabilityRegistry, RuntimeRouter
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    ModelKind,
    ModelingIntent,
    ReferenceAccess,
    ReferenceAccessMode,
    RUNTIME_CONTRACT_VERSION,
    RuntimeOutcome,
    SemanticParameter,
    ValidationStatus,
    canonical_json_bytes,
)
from material_studio_mcp_server.translators import write_crystal_cif


EXPECTED_REQUIREMENTS = (
    "Use a 2x2 in-plane repeat.",
    "Use four bilayers and eight alternating atomic planes.",
    "Keep 15.0 angstrom total vacuum over the full atom extent.",
    "Hydrogen passivate only the bottom C termination with two H per bottom C.",
    "Use 1.09 angstrom C-H bonds.",
)
EXPECTED_ASSUMPTIONS = (
    "Use the COD 1010995 revision 278158 lattice source pin.",
    "Ideal unreconstructed unrelaxed preview only.",
)


def _load_request() -> tuple[CompiledBlindTask, dict[str, str]]:
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict) or set(payload) != {
        "compiled_task",
        "candidate_output",
    }:
        raise ValueError("invalid modeler request envelope")
    assert_coordinate_free_payload(payload["compiled_task"])
    task = CompiledBlindTask.model_validate_json(
        json.dumps(payload["compiled_task"], ensure_ascii=True),
        strict=True,
    )
    output = payload["candidate_output"]
    if not isinstance(output, dict) or set(output) != {
        "candidate_root",
        "model_spec_name",
        "project_id",
        "structure_name",
    }:
        raise ValueError("invalid candidate output instructions")
    if not all(type(value) is str and value for value in output.values()):
        raise ValueError("candidate output instructions must be nonempty text")
    return task, output


def _validate_task(task: CompiledBlindTask) -> None:
    if (
        task.case_id != "sic-3c-001-si-face-surface-dev"
        or task.task_id != "sic-3c-001-si-face-fixed-profile"
        or task.domain != "surface"
        or task.scenario != "surface_slab"
        or task.material != "3C-SiC"
        or task.expected_output_kind != "crystal"
        or task.semantic_requirements != EXPECTED_REQUIREMENTS
        or task.declared_assumptions != EXPECTED_ASSUMPTIONS
    ):
        raise ValueError("compiled task is outside the fixed modeler profile")


def _intent(project_id: str) -> ModelingIntent:
    return ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="sic-3c-surface-subprocess",
        material="3C-SiC",
        scenario="surface_slab",
        operation="create_si_face_slab",
        model_kind=ModelKind.CRYSTAL,
        requires_current_model=False,
        output_kind=BuildOutputKind.MODEL_SPEC,
        parameters=(
            SemanticParameter(name="project_id", value=project_id),
            SemanticParameter(name="atom_count", value=80),
            SemanticParameter(name="miller_indices", value="(001)"),
            SemanticParameter(name="surface_face", value="Si"),
            SemanticParameter(name="in_plane_repeat", value=2),
            SemanticParameter(name="bilayer_count", value=4),
            SemanticParameter(name="atomic_plane_count", value=8),
            SemanticParameter(name="bottom_termination", value="C"),
            SemanticParameter(name="top_termination", value="Si"),
            SemanticParameter(name="passivation_element", value="H"),
            SemanticParameter(name="hydrogens_per_bottom_carbon", value=2),
            SemanticParameter(
                name="carbon_hydrogen_bond_angstrom",
                value=1.09,
                unit="angstrom",
            ),
            SemanticParameter(name="vacuum_angstrom", value=15.0, unit="angstrom"),
            SemanticParameter(
                name="vacuum_definition",
                value="total_gap_over_full_atomic_extent",
            ),
            SemanticParameter(name="full_atom_extent_centered", value=True),
            SemanticParameter(name="ideal", value=True),
            SemanticParameter(name="unreconstructed", value=True),
            SemanticParameter(name="relaxed", value=False),
            SemanticParameter(name="simulation_count", value=0),
            SemanticParameter(name="revision", value=0),
        ),
        semantic_requirements=(),
        declared_assumptions=(),
        reference_access=ReferenceAccess(
            mode=ReferenceAccessMode.TASK_ONLY,
            source_ids=("cod-1010995",),
            raw_structure_access=False,
            final_coordinate_access=False,
            hidden_holdout_access=False,
        ),
    )


def _canonicalizer_compatible_cif(payload: bytes) -> bytes:
    lines = payload.decode("utf-8").splitlines()
    atom_loop_index = lines.index("loop_")
    lines[atom_loop_index:atom_loop_index] = [
        "loop_",
        "_space_group_symop_operation_xyz",
        "'x,y,z'",
        "",
    ]
    z_header_index = lines.index("  _atom_site_fract_z")
    lines.insert(z_header_index + 1, "  _atom_site_occupancy")
    element_counts: dict[str, int] = {}
    for index in range(z_header_index + 2, len(lines)):
        if lines[index].startswith("  ") and not lines[index].lstrip().startswith("_"):
            fields = lines[index].split()
            element = fields[1]
            element_counts[element] = element_counts.get(element, 0) + 1
            fields[0] = f"{element}{element_counts[element]:03d}"
            lines[index] = "  " + " ".join((*fields, "1"))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _output_paths(output: dict[str, str]) -> tuple[Path, Path]:
    root = Path(output["candidate_root"])
    if not root.is_absolute():
        raise ValueError("candidate root must be absolute")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("candidate root must be an existing directory")
    if output["model_spec_name"] != "model_spec.json":
        raise ValueError("unexpected model-spec output name")
    if output["structure_name"] != "structure.cif":
        raise ValueError("unexpected structure output name")
    model_spec_path = (root / output["model_spec_name"]).resolve()
    structure_path = (root / output["structure_name"]).resolve()
    if model_spec_path.parent != root or structure_path.parent != root:
        raise ValueError("candidate output escapes candidate root")
    if model_spec_path.exists() or structure_path.exists():
        raise ValueError("candidate output already exists")
    return model_spec_path, structure_path


def main() -> int:
    task, output = _load_request()
    _validate_task(task)
    model_spec_path, structure_path = _output_paths(output)
    registry = CapabilityRegistry(
        ((PLUGIN_MANIFEST, PLUGIN),),
        dependency_resolver=lambda dependency: dependency.required,
    )
    router = RuntimeRouter(registry)
    intent = _intent(output["project_id"])
    routing = router.route(intent)
    if routing.outcome is not RuntimeOutcome.COMPLETED:
        raise RuntimeError("runtime routing did not complete")
    planning = router.plan_selected(routing, intent)
    if planning.outcome is not RuntimeOutcome.COMPLETED or planning.plan is None:
        raise RuntimeError("runtime planning did not complete")
    candidate = PLUGIN.build(planning.plan)
    validation = PLUGIN.validate(candidate)
    if validation.status is not ValidationStatus.PASS_WITH_WARNINGS:
        raise RuntimeError("candidate validation did not pass preview checks")

    model_spec_path.write_bytes(canonical_json_bytes(candidate))
    write_crystal_cif(candidate.model, structure_path)
    structure_path.write_bytes(
        _canonicalizer_compatible_cif(structure_path.read_bytes())
    )
    receipt = {
        "atom_count": len(candidate.model.basis_atoms),
        "model_spec_name": model_spec_path.name,
        "pid": os.getpid(),
        "project_id": candidate.project_id,
        "router_selected_plugin_id": routing.selected_plugin_id,
        "structure_name": structure_path.name,
        "validation_status": validation.status.value,
    }
    sys.stdout.write(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
