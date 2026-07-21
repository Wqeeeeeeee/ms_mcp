"""Pure planning for the frozen 80-atom CASTEP Energy profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from material_studio_mcp_server.castep_relaxation import (
    crystal_structure_sha256,
)
from material_studio_mcp_server.domains.surface import PLUGIN, build, plan
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    ModelKind,
    ModelingIntent,
    ReferenceAccess,
    ReferenceAccessMode,
    RUNTIME_CONTRACT_VERSION,
    SemanticParameter,
)
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.execution import canonical_json_sha256

from .contracts import (
    ACCEPTANCE_PROFILE,
    CastepAcceptancePlan,
    CastepAcceptanceRequest,
    FixedCastepProfile,
    PUBLIC_CASTEP_TOOL,
)


EXPECTED_SIMULATION_PAYLOAD: dict[str, Any] = {
    "band_structure_energy_max_ev": None,
    "band_structure_energy_tolerance_ev": None,
    "band_structure_extra_bands": None,
    "cell_optimization": None,
    "cutoff_energy_ev": 300,
    "dipole_correction": "Self-consistent",
    "displacement_convergence_angstrom": None,
    "dos_energy_max_ev": None,
    "dos_energy_tolerance_ev": None,
    "dos_extra_bands": None,
    "dos_integration_method": None,
    "dos_smearing_width_ev": None,
    "energy_convergence_ev_per_atom": None,
    "force_convergence_ev_per_angstrom": None,
    "functional": "PBE",
    "kpoint_separation": None,
    "kpoints": [2, 2, 1],
    "max_iterations": None,
    "module": "CASTEP",
    "optimization_algorithm": None,
    "output_file": None,
    "properties_kpoint_separation": None,
    "quality": "Medium",
    "task": "Energy",
}


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_external_fresh_workspace(path: Path) -> Path:
    workspace = path.expanduser().resolve(strict=False)
    root = repository_root().resolve()
    if _is_inside(workspace, root):
        raise ValueError("CASTEP acceptance workspace must be outside the repository")
    if workspace.exists():
        raise ValueError("CASTEP acceptance workspace must not already exist")
    if not workspace.parent.is_dir():
        raise ValueError("CASTEP acceptance workspace parent must already exist")
    return workspace


def build_fixed_candidate(project_id: str) -> ModelSpec:
    intent = ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="wo-castep-acceptance-001",
        material="3C-SiC",
        scenario="surface_slab",
        operation="create_si_face_slab",
        model_kind=ModelKind.CRYSTAL,
        requires_current_model=False,
        output_kind=BuildOutputKind.MODEL_SPEC,
        parameters=(SemanticParameter(name="project_id", value=project_id),),
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
    candidate_plan = plan(intent, None)
    if not candidate_plan.build_eligible:
        raise ValueError("fixed surface plugin did not produce an eligible plan")
    candidate = build(candidate_plan)
    if not source_profile_is_exact(candidate):
        raise ValueError("fixed surface plugin output differs from the acceptance profile")
    return candidate


def source_profile_is_exact(spec: ModelSpec) -> bool:
    try:
        validation = PLUGIN.validate(spec)
        metadata = dict(spec.metadata or {})
        surface = dict(metadata.get("surface") or {})
        passivation = dict(surface.get("passivation") or {})
        plugin = dict(metadata.get("domain_plugin") or {})
        composition: dict[str, int] = {}
        for atom in spec.model.basis_atoms:
            composition[atom.element] = composition.get(atom.element, 0) + 1
        return bool(
            spec.revision == 0
            and spec.simulation is None
            and len(spec.model.basis_atoms) == 80
            and composition == {"C": 32, "H": 16, "Si": 32}
            and plugin.get("plugin_id") == "sic_3c_001_si_face_surface"
            and metadata.get("material") == "3C-SiC"
            and surface.get("miller_indices") == [0, 0, 1]
            and surface.get("face") == "Si"
            and surface.get("in_plane_supercell") == [2, 2]
            and surface.get("bilayer_count") == 4
            and surface.get("atomic_plane_count") == 8
            and surface.get("vacuum_angstrom") == 15.0
            and surface.get("top_termination") == "Si"
            and surface.get("bottom_termination") == "C"
            and passivation.get("element") == "H"
            and passivation.get("hydrogens_per_bottom_carbon") == 2
            and validation.preview_eligible is True
        )
    except (AttributeError, TypeError, ValueError):
        return False


def effective_settings_are_exact(value: Any) -> bool:
    return isinstance(value, dict) and value == EXPECTED_SIMULATION_PAYLOAD


def fixed_public_tool_payload(
    *,
    workspace_root: Path,
    project_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "task": "Energy",
        "quality": "Medium",
        "functional": "PBE",
        "cutoff_energy_ev": 300,
        "kpoint_separation": None,
        "kpoints": (2, 2, 1),
        "properties_kpoint_separation": None,
        "band_structure_energy_max_ev": None,
        "band_structure_extra_bands": None,
        "band_structure_energy_tolerance_ev": None,
        "dos_energy_max_ev": None,
        "dos_extra_bands": None,
        "dos_energy_tolerance_ev": None,
        "dos_smearing_width_ev": None,
        "dos_integration_method": None,
        "dipole_correction": "Self-consistent",
        "open_in_gui": False,
        "take_snapshot": False,
        "export_view_audit": False,
        "views": None,
        "working_dir": str(workspace_root),
        "timeout_seconds": timeout_seconds,
        "expected_revision": 0,
        "response_mode": "full",
    }


def _plan_digest_payload(
    *,
    request: CastepAcceptanceRequest,
    candidate: ModelSpec,
    workspace_root: Path,
    tool_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "profile": ACCEPTANCE_PROFILE,
        "request_id": request.request_id,
        "project_id": request.project_id,
        "workspace_root": str(workspace_root),
        "timeout_seconds": request.timeout_seconds,
        "candidate_model_spec_sha256": canonical_json_sha256(
            candidate.model_dump(mode="json")
        ),
        "source_structure_sha256": crystal_structure_sha256(candidate.model),
        "public_tool": PUBLIC_CASTEP_TOOL,
        "public_tool_payload": tool_payload,
    }


def plan_acceptance(request: CastepAcceptanceRequest) -> CastepAcceptancePlan:
    if not isinstance(request, CastepAcceptanceRequest):
        raise TypeError("request must be CastepAcceptanceRequest")
    workspace = validate_external_fresh_workspace(request.workspace_root)
    candidate = build_fixed_candidate(request.project_id)
    tool_payload = fixed_public_tool_payload(
        workspace_root=workspace,
        project_id=request.project_id,
        timeout_seconds=request.timeout_seconds,
    )
    digest = canonical_json_sha256(
        _plan_digest_payload(
            request=request,
            candidate=candidate,
            workspace_root=workspace,
            tool_payload=tool_payload,
        )
    )
    return CastepAcceptancePlan(
        request_id=request.request_id,
        profile=FixedCastepProfile(),
        candidate_model_spec_sha256=canonical_json_sha256(
            candidate.model_dump(mode="json")
        ),
        source_structure_sha256=crystal_structure_sha256(candidate.model),
        public_tool_payload=tool_payload,
        plan_sha256=digest,
    )


__all__ = [
    "EXPECTED_SIMULATION_PAYLOAD",
    "build_fixed_candidate",
    "effective_settings_are_exact",
    "fixed_public_tool_payload",
    "plan_acceptance",
    "repository_root",
    "source_profile_is_exact",
    "validate_external_fresh_workspace",
]
