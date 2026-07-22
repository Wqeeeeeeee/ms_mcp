"""Pure planning for the frozen 80-atom CASTEP Energy profile."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from material_studio_mcp_server.castep_relaxation import (
    crystal_structure_sha256,
)
from material_studio_mcp_server.domains.surface import PLUGIN, build, plan
from material_studio_mcp_server.ms_roundtrip.errors import RoundtripError
from material_studio_mcp_server.ms_roundtrip.secure_io import (
    reject_link_or_reparse_components,
    resolve_existing_directory,
)
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
    FIXED_PROJECT_ID,
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

WORKSPACE_GUARD_NAME = ".castep_acceptance_workspace.lock"

# Windows CreateProcess accepts a shorter current-directory path than the
# general extended-path APIs. Keep the fixed acceptance layout below that
# boundary before the public runner is called.
WINDOWS_JOB_CWD_LIMIT = 248
WINDOWS_JOB_PATH_LIMIT = 260
_FIXED_RUN_DIRECTORY_PARTS = (
    "outputs",
    "r000",
    "castep_electronic",
    "energy",
    "run_0001",
    ".material-studio-mcp",
    "jobs",
)


@dataclass
class WorkspaceReservation:
    path: Path
    identity: tuple[int, int]
    guard_path: Path
    handle: int
    handle_kind: str
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        _close_workspace_handle(self.handle, self.handle_kind)
        self.closed = True

    def __enter__(self) -> "WorkspaceReservation":
        if self.closed:
            raise ValueError("workspace reservation is already closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _close_workspace_handle(handle: int, handle_kind: str) -> None:
    if handle_kind == "windows":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(wintypes.HANDLE(handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.close(handle)


def _open_workspace_guard(path: Path) -> tuple[int, str, Path]:
    guard_path = path / WORKSPACE_GUARD_NAME
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        generic_read = 0x80000000
        generic_write = 0x40000000
        create_new = 1
        file_attribute_hidden = 0x00000002
        file_flag_open_reparse_point = 0x00200000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(
            str(guard_path),
            generic_read | generic_write,
            0,
            None,
            create_new,
            file_attribute_hidden | file_flag_open_reparse_point,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle), "windows", guard_path

    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.open(guard_path, flags, 0o600), "posix", guard_path


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def windows_job_path_lengths(workspace: Path) -> dict[str, int]:
    """Return worst-case Windows lengths for the frozen runner artifacts."""

    if not isinstance(workspace, Path):
        raise TypeError("workspace must be Path")
    run_root = workspace / FIXED_PROJECT_ID
    for part in _FIXED_RUN_DIRECTORY_PARTS:
        run_root /= part
    job_name = (
        f"{FIXED_PROJECT_ID}_r000_castep_energy-"
        + ("0" * 15)
        + "-"
        + ("0" * 8)
    )
    job_dir = run_root / job_name
    return {
        "cwd": len(os.fspath(job_dir)),
        "script": len(os.fspath(job_dir / "run_castep_electronic.pl")),
        "log": len(os.fspath(job_dir / "run_castep_electronicMatStudioLog.htm")),
    }


def windows_job_cwd_length(workspace: Path) -> int:
    """Return the worst-case job cwd length for the frozen CASTEP profile."""

    return windows_job_path_lengths(workspace)["cwd"]


def validate_windows_job_cwd(workspace: Path) -> None:
    """Reject a real-run workspace that cannot be a Windows process cwd."""

    if not isinstance(workspace, Path):
        raise TypeError("workspace must be Path")
    if os.name != "nt":
        return
    lengths = windows_job_path_lengths(workspace)
    if lengths["cwd"] >= WINDOWS_JOB_CWD_LIMIT:
        raise ValueError(
            "CASTEP acceptance workspace is too long for the Materials Studio "
            f"Windows job cwd (estimated {lengths['cwd']}; limit "
            f"{WINDOWS_JOB_CWD_LIMIT}); choose a shorter external path"
        )
    longest_kind, longest_length = max(lengths.items(), key=lambda item: item[1])
    if longest_length >= WINDOWS_JOB_PATH_LIMIT:
        raise ValueError(
            "CASTEP acceptance workspace is too long for the Materials Studio "
            f"Windows {longest_kind} path (estimated {longest_length}; limit "
            f"{WINDOWS_JOB_PATH_LIMIT}); "
            "choose a shorter external path"
        )


def validate_external_fresh_workspace(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("path must be Path")
    workspace = Path(os.path.abspath(os.fspath(path.expanduser())))
    if os.name == "nt" and ":" in workspace.name:
        raise ValueError("CASTEP acceptance workspace contains an unsafe component")
    try:
        reject_link_or_reparse_components(workspace)
        parent = resolve_existing_directory(workspace.parent)
    except RoundtripError as exc:
        raise ValueError(
            "CASTEP acceptance workspace contains an unsafe component"
        ) from exc
    workspace = parent / workspace.name
    root = repository_root().resolve()
    if _is_inside(workspace, root):
        raise ValueError("CASTEP acceptance workspace must be outside the repository")
    if workspace.exists() or workspace.is_symlink():
        raise ValueError("CASTEP acceptance workspace must not already exist")
    return workspace


def reserve_external_fresh_workspace(path: Path) -> WorkspaceReservation:
    workspace = validate_external_fresh_workspace(path)
    try:
        workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as exc:
        raise ValueError(
            "CASTEP acceptance workspace could not be reserved atomically"
        ) from exc
    handle: int | None = None
    handle_kind: str | None = None
    guard_path: Path | None = None
    try:
        handle, handle_kind, guard_path = _open_workspace_guard(workspace)
        resolved = resolve_existing_directory(workspace)
        value = resolved.stat(follow_symlinks=False)
    except (OSError, RoundtripError) as exc:
        if handle is not None and handle_kind is not None:
            try:
                _close_workspace_handle(handle, handle_kind)
            except OSError:
                pass
        raise ValueError(
            "CASTEP acceptance workspace identity could not be verified"
        ) from exc
    if os.path.normcase(str(resolved)) != os.path.normcase(str(workspace)):
        if handle is not None and handle_kind is not None:
            _close_workspace_handle(handle, handle_kind)
        raise ValueError("CASTEP acceptance workspace identity changed")
    if handle is None or handle_kind is None or guard_path is None:
        if handle is not None and handle_kind is not None:
            _close_workspace_handle(handle, handle_kind)
        raise ValueError("CASTEP acceptance workspace lock was not acquired")
    return WorkspaceReservation(
        path=resolved,
        identity=(int(value.st_dev), int(value.st_ino)),
        guard_path=guard_path,
        handle=handle,
        handle_kind=handle_kind,
    )


def assert_workspace_reservation(reservation: WorkspaceReservation) -> Path:
    if not isinstance(reservation, WorkspaceReservation):
        raise TypeError("reservation must be WorkspaceReservation")
    if reservation.closed:
        raise ValueError("CASTEP acceptance workspace reservation is closed")
    if not reservation.guard_path.is_file() or reservation.guard_path.is_symlink():
        raise ValueError("CASTEP acceptance workspace guard is unavailable")
    try:
        resolved = resolve_existing_directory(reservation.path)
        value = resolved.stat(follow_symlinks=False)
    except (OSError, RoundtripError) as exc:
        raise ValueError(
            "CASTEP acceptance workspace identity could not be reverified"
        ) from exc
    identity = (int(value.st_dev), int(value.st_ino))
    if (
        os.path.normcase(str(resolved)) != os.path.normcase(str(reservation.path))
        or identity != reservation.identity
    ):
        raise ValueError("CASTEP acceptance workspace identity changed")
    return resolved


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
    "WORKSPACE_GUARD_NAME",
    "WorkspaceReservation",
    "assert_workspace_reservation",
    "build_fixed_candidate",
    "effective_settings_are_exact",
    "fixed_public_tool_payload",
    "plan_acceptance",
    "repository_root",
    "reserve_external_fresh_workspace",
    "source_profile_is_exact",
    "validate_external_fresh_workspace",
    "WINDOWS_JOB_CWD_LIMIT",
    "WINDOWS_JOB_PATH_LIMIT",
    "windows_job_cwd_length",
    "windows_job_path_lengths",
    "validate_windows_job_cwd",
]
