"""Private preview-first adapter around the existing public CASTEP tool."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from material_studio_mcp_server.config import COMMON_INSTALL_ROOTS
from material_studio_mcp_server.ms_roundtrip.gui_inventory import (
    GuiObservation,
    ReadOnlyGuiBackend,
    capture_gui_inventory,
    default_gui_backend,
)
from material_studio_mcp_server.ms_roundtrip.errors import (
    RoundtripError,
    RoundtripErrorCode,
)
from material_studio_mcp_server.ms_roundtrip.secure_io import (
    FileSnapshot,
    MAX_RUNNER_ARTIFACT_BYTES,
    reject_link_or_reparse_components,
    snapshot_unchanged,
    stable_read_file,
)
from material_studio_mcp_server.runner import MaterialStudioRunner
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.store import ProjectStore

from .contracts import (
    CastepAcceptancePlan,
    CastepAcceptanceRequest,
    CastepVerificationReport,
    PUBLIC_CASTEP_TOOL,
)
from .profile import (
    WorkspaceReservation,
    assert_workspace_reservation,
    build_fixed_candidate,
    effective_settings_are_exact,
    plan_acceptance,
    reserve_external_fresh_workspace,
    WORKSPACE_GUARD_NAME,
    validate_external_fresh_workspace,
    validate_windows_job_cwd,
)
from .verification import verify_castep_acceptance_execution


CastepTool = Callable[..., dict[str, Any]]
CastepToolResolver = Callable[[], CastepTool]
GuiBackendResolver = Callable[[], ReadOnlyGuiBackend]


class CastepAcceptanceError(RuntimeError):
    """Fail-closed harness error; existing workspace evidence is preserved."""


@dataclass(frozen=True)
class CastepAcceptanceExecutionResult:
    plan: CastepAcceptancePlan
    workspace_root: Path
    source_spec: ModelSpec
    public_preview: dict[str, Any]
    public_execute: dict[str, Any]
    verification: CastepVerificationReport


def _default_tool_resolver() -> CastepTool:
    from material_studio_mcp_server import server

    return server.material_studio_castep_run_current


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshots: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.name == WORKSPACE_GUARD_NAME:
            continue
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            snapshots[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshots


def _public_tool_identity_valid(tool: CastepTool) -> bool:
    from material_studio_mcp_server import server

    return callable(tool) and tool is getattr(server, PUBLIC_CASTEP_TOOL, None)


def _trusted_runner_path(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError:
        return False
    parts = tuple(part.casefold() for part in resolved.parts)
    if len(parts) < 6 or parts[-1] != "runmatscript.bat":
        return False
    if parts[-4:-1] != ("etc", "scripting", "bin"):
        return False
    if parts[-5] not in {"materials studio 20.1", "materials studio 20.1 x64 server"}:
        return False
    install_root = Path(*resolved.parts[:-5])
    trusted_roots = {
        os.path.normcase(str(Path(root).expanduser().resolve(strict=False)))
        for root in COMMON_INSTALL_ROOTS
    }
    return os.path.normcase(str(install_root)) in trusted_roots


def _real_runner_snapshot(tool: CastepTool) -> FileSnapshot | None:
    from material_studio_mcp_server import server

    if tool is not getattr(server, PUBLIC_CASTEP_TOOL, None):
        return None
    runner = getattr(server, "runner", None)
    if not isinstance(runner, MaterialStudioRunner):
        return None
    config = runner.config
    path = getattr(config, "runner", None)
    if not isinstance(path, Path) or not _trusted_runner_path(path):
        return None
    if tuple(getattr(config, "extra_runner_args", ())) or os.environ.get(
        "MATERIAL_STUDIO_COMMAND_TEMPLATE"
    ):
        return None
    try:
        reject_link_or_reparse_components(path)
        return stable_read_file(
            path,
            max_bytes=MAX_RUNNER_ARTIFACT_BYTES,
            require_single_link=False,
            code=RoundtripErrorCode.RUNNER_IDENTITY_INVALID,
        )
    except (OSError, RoundtripError):
        return None


def _real_runner_unchanged(
    tool: CastepTool,
    before: FileSnapshot | None,
) -> bool:
    after = _real_runner_snapshot(tool)
    return before is not None and after is not None and snapshot_unchanged(before, after)


class CastepAcceptanceHarness:
    """No-tool-registration adapter with lazy backend and GUI resolution."""

    def __init__(
        self,
        *,
        tool_resolver: CastepToolResolver | None = None,
        gui_backend_resolver: GuiBackendResolver | None = None,
        real_environment: bool = True,
    ) -> None:
        if type(real_environment) is not bool:
            raise TypeError("real_environment must be bool")
        self._tool_resolver = tool_resolver or _default_tool_resolver
        self._gui_backend_resolver = gui_backend_resolver or default_gui_backend
        self._real_environment = real_environment

    def run(
        self,
        request: CastepAcceptanceRequest,
    ) -> CastepAcceptancePlan | CastepAcceptanceExecutionResult:
        plan = plan_acceptance(request)
        if request.execution_mode == "preview":
            return plan
        if request.expected_plan_sha256 != plan.plan_sha256:
            raise CastepAcceptanceError(
                "execute request does not match the reviewed preview plan"
            )
        return self._execute(request, plan)

    def _execute(
        self,
        request: CastepAcceptanceRequest,
        plan: CastepAcceptancePlan,
    ) -> CastepAcceptanceExecutionResult:
        gui_backend = self._gui_backend_resolver()
        gui_before = capture_gui_inventory(gui_backend)
        if not gui_before.receipt.usable_single_window:
            raise CastepAcceptanceError(
                "real CASTEP execution requires one existing visible Materials Studio window"
            )

        tool = self._tool_resolver()
        public_tool_reused = _public_tool_identity_valid(tool)
        if not public_tool_reused:
            raise CastepAcceptanceError(
                "acceptance execution requires material_studio_castep_run_current"
            )
        runner_before = _real_runner_snapshot(tool) if self._real_environment else None
        runner_identity_valid = runner_before is not None
        if self._real_environment and not runner_identity_valid:
            raise CastepAcceptanceError(
                "real CASTEP runner identity failed before workspace creation"
            )
        if self._real_environment:
            try:
                workspace = validate_external_fresh_workspace(request.workspace_root)
                validate_windows_job_cwd(workspace)
            except (OSError, TypeError, ValueError) as exc:
                raise CastepAcceptanceError(
                    "real CASTEP workspace failed the Windows job path preflight"
                ) from exc

        try:
            reservation = reserve_external_fresh_workspace(request.workspace_root)
        except (OSError, TypeError, ValueError) as exc:
            raise CastepAcceptanceError(
                "real CASTEP workspace could not be reserved safely"
            ) from exc
        with reservation:
            return self._execute_reserved(
                plan=plan,
                gui_backend=gui_backend,
                gui_before=gui_before,
                tool=tool,
                public_tool_reused=public_tool_reused,
                runner_before=runner_before,
                runner_identity_valid=runner_identity_valid,
                reservation=reservation,
            )

    def _execute_reserved(
        self,
        *,
        plan: CastepAcceptancePlan,
        gui_backend: ReadOnlyGuiBackend,
        gui_before: GuiObservation,
        tool: CastepTool,
        public_tool_reused: bool,
        runner_before: FileSnapshot | None,
        runner_identity_valid: bool,
        reservation: WorkspaceReservation,
    ) -> CastepAcceptanceExecutionResult:
        workspace = reservation.path

        source_spec = build_fixed_candidate(plan.project_id)
        store = ProjectStore(workspace)
        store.create_project(
            source_spec,
            user_text="WO-CASTEP-ACCEPTANCE-001 fixed surface candidate",
            diff=["create frozen 80-atom 3C-SiC(001) Si-face slab"],
        )
        source_spec = store.load_current(plan.project_id)

        try:
            assert_workspace_reservation(reservation)
        except (OSError, TypeError, ValueError) as exc:
            raise CastepAcceptanceError(
                "real CASTEP workspace identity changed before preview"
            ) from exc
        baseline = _snapshot_tree(workspace)
        preview_payload = dict(plan.public_tool_payload)
        public_preview = tool(execution_mode="preview", **preview_payload)
        preview_after = _snapshot_tree(workspace)
        run_directory = Path(str(public_preview.get("run_directory") or ""))
        preview_side_effect_free = bool(
            baseline == preview_after
            and public_preview.get("ok") is True
            and public_preview.get("status") == "ready_for_explicit_execute"
            and public_preview.get("execution_started") is False
            and public_preview.get("revision_created") is False
            and effective_settings_are_exact(public_preview.get("simulation"))
            and not run_directory.exists()
        )
        if not preview_side_effect_free:
            raise CastepAcceptanceError(
                "public CASTEP preview changed state or failed the frozen profile"
            )
        if self._real_environment and not _real_runner_unchanged(tool, runner_before):
            raise CastepAcceptanceError(
                "real CASTEP runner changed during preview"
            )
        try:
            assert_workspace_reservation(reservation)
        except (OSError, TypeError, ValueError) as exc:
            raise CastepAcceptanceError(
                "real CASTEP workspace identity changed before execution"
            ) from exc

        execute_invocation_count = 0
        public_execute: dict[str, Any]
        execution_error: Exception | None = None
        gui_after_error: Exception | None = None
        try:
            execute_invocation_count += 1
            public_execute = tool(execution_mode="execute", **preview_payload)
        except Exception as exc:
            execution_error = exc
        finally:
            try:
                gui_after = capture_gui_inventory(gui_backend)
            except Exception as exc:
                gui_after = gui_before
                gui_after_error = exc
            if self._real_environment:
                runner_identity_valid = runner_identity_valid and _real_runner_unchanged(
                    tool,
                    runner_before,
                )
        if execution_error is not None:
            raise CastepAcceptanceError(
                f"public CASTEP execution raised {execution_error.__class__.__name__}; "
                "workspace evidence was preserved"
            ) from execution_error
        if gui_after_error is not None:
            raise CastepAcceptanceError(
                "read-only GUI inventory failed after CASTEP execution; "
                "workspace evidence was preserved"
            ) from gui_after_error
        try:
            assert_workspace_reservation(reservation)
        except (OSError, TypeError, ValueError) as exc:
            raise CastepAcceptanceError(
                "real CASTEP workspace identity changed after execution"
            ) from exc

        verification = verify_castep_acceptance_execution(
            plan=plan,
            source_spec=source_spec,
            store=store,
            public_preview=public_preview,
            public_execute=public_execute,
            preview_side_effect_free=preview_side_effect_free,
            public_tool_reused=public_tool_reused,
            runner_identity_valid=runner_identity_valid,
            real_environment=self._real_environment,
            execute_invocation_count=execute_invocation_count,
            gui_before=gui_before,
            gui_after=gui_after,
        )
        return CastepAcceptanceExecutionResult(
            plan=plan,
            workspace_root=workspace,
            source_spec=source_spec,
            public_preview=public_preview,
            public_execute=public_execute,
            verification=verification,
        )


__all__ = [
    "CastepAcceptanceError",
    "CastepAcceptanceExecutionResult",
    "CastepAcceptanceHarness",
    "CastepTool",
    "CastepToolResolver",
    "GuiBackendResolver",
]
