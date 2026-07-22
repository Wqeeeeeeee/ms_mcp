"""Verify one persisted CASTEP run through existing state and receipt contracts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from material_studio_mcp_server.castep_electronic import (
    verify_castep_electronic_receipt,
)
from material_studio_mcp_server.castep_relaxation import crystal_structure_sha256
from material_studio_mcp_server.gui import _workspace_advisory_lock_status
from material_studio_mcp_server.ms_roundtrip.gui_inventory import (
    GuiObservation,
    compare_gui_inventories,
)
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.execution import (
    canonical_json_sha256,
    inspect_execution_runtime,
)
from material_studio_mcp_server.state.store import ProjectStore

from .contracts import (
    CastepAcceptancePlan,
    CastepVerificationReport,
    GuiInvariantProjection,
)
from .profile import effective_settings_are_exact, source_profile_is_exact


_CHECK_FAILURES = {
    "source_profile_exact": "source_profile_mismatch",
    "effective_settings_exact": "effective_settings_mismatch",
    "preview_side_effect_free": "preview_side_effect_detected",
    "public_tool_reused": "public_tool_not_reused",
    "runner_identity_valid": "real_runner_identity_invalid",
    "runner_success": "runner_failed",
    "single_execute": "backend_execution_count_mismatch",
    "revision_execution_lock_verified": "revision_execution_lock_invalid",
    "execution_attempt_history_exact": "execution_attempt_history_invalid",
    "execution_attempt_binding_verified": "execution_attempt_binding_invalid",
    "electronic_receipt_binding_verified": "electronic_receipt_binding_invalid",
    "native_castep_file_exact": "native_castep_file_count_mismatch",
    "native_scf_audit_valid": "native_scf_audit_invalid",
    "total_energy_finite": "total_energy_not_finite",
    "structure_unchanged": "structure_changed",
    "metadata_only_result_revision_verified": "result_revision_invalid",
    "gui_invariant": "matstudio_process_or_window_changed",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _inside_workspace(path_value: Any, workspace: Path) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("artifact path is missing")
    path = Path(path_value).expanduser().resolve()
    path.relative_to(workspace)
    return path


def _gui_projection(
    before: GuiObservation,
    after: GuiObservation,
) -> tuple[GuiInvariantProjection, bool]:
    invariant = compare_gui_inventories(before, after)
    projection = GuiInvariantProjection(
        process_count_before_after=invariant.matstudio_process_count_before_after,
        window_count_before_after=invariant.matstudio_window_count_before_after,
        process_inventory_sha256_before_after=(
            invariant.before.process_identity_sha256,
            invariant.after.process_identity_sha256,
        ),
        window_inventory_sha256_before_after=(
            invariant.before.window_identity_sha256,
            invariant.after.window_identity_sha256,
        ),
        identity_unchanged=invariant.matstudio_pid_and_window_handle_unchanged,
        process_launched=invariant.matstudio_process_launched,
    )
    return projection, invariant.invariant_passed


def verify_castep_acceptance_execution(
    *,
    plan: CastepAcceptancePlan,
    source_spec: ModelSpec,
    store: ProjectStore,
    public_preview: dict[str, Any],
    public_execute: dict[str, Any],
    preview_side_effect_free: bool,
    public_tool_reused: bool,
    runner_identity_valid: bool,
    real_environment: bool,
    execute_invocation_count: int,
    gui_before: GuiObservation,
    gui_after: GuiObservation,
) -> CastepVerificationReport:
    """Reconcile public response data with immutable on-disk evidence."""

    workspace = store.workspace_root.resolve()
    candidate_digest = canonical_json_sha256(source_spec.model_dump(mode="json"))
    source_structure_digest = crystal_structure_sha256(source_spec.model)
    gui, gui_invariant_ok = _gui_projection(gui_before, gui_after)

    current_spec: ModelSpec | None = None
    receipt_summary: dict[str, Any] | None = None
    electronic_receipt: dict[str, Any] | None = None
    runtime: dict[str, Any] = {}
    source_metadata: dict[str, Any] = {}
    final_metadata: dict[str, Any] = {}
    native_castep_paths: list[Path] = []

    result_revision = public_execute.get("new_revision")
    if type(result_revision) is not int or result_revision < 1:
        result_revision = None
    try:
        if result_revision is not None:
            current_spec = store.get_revision(plan.project_id, result_revision)
            receipt_summary = verify_castep_electronic_receipt(current_spec)
            raw_receipt = (current_spec.metadata or {}).get(
                "last_castep_electronic_calculation"
            )
            if isinstance(raw_receipt, dict):
                electronic_receipt = dict(raw_receipt)

        source_metadata_path = _inside_workspace(
            public_execute.get("result_metadata_path"), workspace
        )
        source_metadata = _read_json(source_metadata_path)
        final_metadata_path = _inside_workspace(
            (public_execute.get("planned_outputs") or {}).get("result_metadata"),
            workspace,
        )
        final_metadata = _read_json(final_metadata_path)
        run_dir = _inside_workspace(public_execute.get("run_directory"), workspace)
        script_path = _inside_workspace(public_execute.get("script_path"), workspace)
        lock_path = store.outputs_dir(plan.project_id, 0) / "revision_execution.lock"
        runtime = inspect_execution_runtime(
            run_dir,
            project_id=plan.project_id,
            revision=0,
            result_metadata=source_metadata,
            lock_probe=lambda: _workspace_advisory_lock_status(
                lock_path,
                workspace_root=workspace,
            ),
            expected_spec_payload=source_spec.model_dump(mode="json"),
            expected_script=public_execute.get("script"),
            expected_script_path=script_path,
            expected_lock_path=lock_path,
            expected_result_metadata_path=source_metadata_path,
        )
        for artifact in (electronic_receipt or {}).get("native_artifacts", []):
            if not isinstance(artifact, dict):
                continue
            artifact_path = _inside_workspace(artifact.get("path"), workspace)
            if artifact_path.suffix.casefold() == ".castep":
                native_castep_paths.append(artifact_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        runtime = runtime or {}

    effective_settings_exact = bool(
        effective_settings_are_exact(public_preview.get("simulation"))
        and effective_settings_are_exact(public_execute.get("simulation"))
        and current_spec is not None
        and current_spec.simulation is not None
        and effective_settings_are_exact(
            current_spec.simulation.model_dump(mode="json")
        )
        and electronic_receipt is not None
        and effective_settings_are_exact(electronic_receipt.get("simulation"))
    )
    source_profile_exact = bool(
        source_profile_is_exact(source_spec)
        and candidate_digest == plan.candidate_model_spec_sha256
        and source_structure_digest == plan.source_structure_sha256
    )
    runner_success = bool(
        public_execute.get("ok") is True
        and public_execute.get("status") == "castep_electronic_result_recorded"
        and public_execute.get("execution_started") is True
        and (public_execute.get("result") or {}).get("success") is True
    )

    journal = runtime.get("journal") if isinstance(runtime.get("journal"), dict) else {}
    recent_events = journal.get("recent_events") if isinstance(journal, dict) else []
    event_types = tuple(
        str(event.get("event_type"))
        for event in recent_events or []
        if isinstance(event, dict)
    )
    backend_execution_count = int(journal.get("attempt_count") or 0)
    history_exact = bool(
        runtime.get("status") == "completed"
        and (runtime.get("consistency") or {}).get("ok") is True
        and journal.get("event_count") == 2
        and backend_execution_count == 1
        and event_types == ("started", "completed")
    )

    transaction = (
        public_execute.get("execution_transaction")
        if isinstance(public_execute.get("execution_transaction"), dict)
        else {}
    )
    expected_lock_path = store.outputs_dir(plan.project_id, 0) / "revision_execution.lock"
    lock_verified = bool(
        transaction.get("domain") == "revision_execution"
        and transaction.get("project_id") == plan.project_id
        and transaction.get("revision") == 0
        and Path(str(transaction.get("path"))).resolve() == expected_lock_path.resolve()
        and expected_lock_path.is_file()
        and runtime.get("lock_observation_stable") is True
        and (runtime.get("lock_probe_before") or {}).get("active") is False
        and (runtime.get("lock_probe_after") or {}).get("active") is False
    )

    terminal_attempt = public_execute.get("execution_attempt")
    latest_attempt = runtime.get("latest_attempt")
    attempt_binding_verified = bool(
        isinstance(terminal_attempt, dict)
        and terminal_attempt.get("status") == "completed"
        and terminal_attempt.get("result_success") is True
        and terminal_attempt == latest_attempt
        and terminal_attempt == source_metadata.get("execution_attempt")
        and terminal_attempt == final_metadata.get("execution_attempt")
    )
    receipt_binding_verified = bool(
        receipt_summary
        and receipt_summary.get("binding_verified") is True
        and electronic_receipt == public_execute.get("electronic_receipt")
    )

    native_audit = (
        receipt_summary.get("native_output_audit")
        if isinstance(receipt_summary, dict)
        and isinstance(receipt_summary.get("native_output_audit"), dict)
        else {}
    )
    scf_audit = (
        native_audit.get("castep_output_audit")
        if isinstance(native_audit.get("castep_output_audit"), dict)
        else {}
    )
    last_iteration = scf_audit.get("last_scf_iteration")
    max_cycles = scf_audit.get("max_scf_cycles")
    native_scf_valid = bool(
        scf_audit.get("status") == "completed_below_max_cycles"
        and scf_audit.get("run_completed") is True
        and scf_audit.get("maximum_scf_cycles_reached") is False
        and scf_audit.get("fatal_marker_count") == 0
        and type(last_iteration) is int
        and last_iteration > 0
        and (max_cycles is None or (type(max_cycles) is int and last_iteration < max_cycles))
        and scf_audit.get("scientific_convergence_verified") is False
    )

    energy = (
        receipt_summary.get("total_energy_kcal_per_mol")
        if isinstance(receipt_summary, dict)
        else None
    )
    energy_finite = bool(
        type(energy) in (int, float) and math.isfinite(float(energy))
    )
    structure_unchanged = bool(
        current_spec is not None
        and current_spec.model.model_dump(mode="json")
        == source_spec.model.model_dump(mode="json")
        and electronic_receipt is not None
        and electronic_receipt.get("structure_unchanged") is True
        and electronic_receipt.get("source_structure_sha256")
        == source_structure_digest
        and electronic_receipt.get("target_structure_sha256")
        == source_structure_digest
        and (receipt_summary or {}).get("checks", {}).get("structure_artifact")
        is True
    )
    metadata_revision_verified = bool(
        current_spec is not None
        and result_revision == 1
        and current_spec.revision == 1
        and electronic_receipt is not None
        and electronic_receipt.get("source_revision") == 0
        and electronic_receipt.get("target_revision") == 1
        and structure_unchanged
        and store.load_current(plan.project_id).revision == 1
    )

    checks = {
        "source_profile_exact": source_profile_exact,
        "effective_settings_exact": effective_settings_exact,
        "preview_side_effect_free": preview_side_effect_free,
        "public_tool_reused": public_tool_reused,
        "runner_identity_valid": runner_identity_valid or not real_environment,
        "runner_success": runner_success,
        "single_execute": execute_invocation_count == 1
        and backend_execution_count == 1,
        "revision_execution_lock_verified": lock_verified,
        "execution_attempt_history_exact": history_exact,
        "execution_attempt_binding_verified": attempt_binding_verified,
        "electronic_receipt_binding_verified": receipt_binding_verified,
        "native_castep_file_exact": len(native_castep_paths) == 1,
        "native_scf_audit_valid": native_scf_valid,
        "total_energy_finite": energy_finite,
        "structure_unchanged": structure_unchanged,
        "metadata_only_result_revision_verified": metadata_revision_verified,
        "gui_invariant": gui_invariant_ok,
    }
    failure_codes = tuple(
        failure
        for name, failure in _CHECK_FAILURES.items()
        if not checks[name]
    )
    status = "FAIL" if failure_codes else "PASS" if real_environment else "NOT_RUN"
    native_digest = None
    if len(native_castep_paths) == 1:
        native_digest = __import__("hashlib").sha256(
            native_castep_paths[0].read_bytes()
        ).hexdigest()

    return CastepVerificationReport(
        status=status,
        failure_codes=failure_codes,
        real_environment=real_environment,
        source_profile_exact=source_profile_exact,
        effective_settings_exact=effective_settings_exact,
        preview_side_effect_free=preview_side_effect_free,
        public_tool_reused=public_tool_reused,
        runner_identity_valid=runner_identity_valid,
        runner_success=runner_success,
        execute_invocation_count=execute_invocation_count,
        backend_execution_count=backend_execution_count,
        revision_execution_lock_verified=lock_verified,
        execution_attempt_event_types=event_types,
        execution_attempt_binding_verified=attempt_binding_verified,
        electronic_receipt_binding_verified=receipt_binding_verified,
        native_castep_file_count=len(native_castep_paths),
        native_scf_status=(
            str(scf_audit.get("status")) if scf_audit.get("status") else None
        ),
        native_scf_audit_valid=native_scf_valid,
        total_energy_kcal_per_mol=(float(energy) if energy_finite else None),
        total_energy_finite=energy_finite,
        structure_unchanged=structure_unchanged,
        metadata_only_result_revision_verified=metadata_revision_verified,
        result_revision=result_revision,
        gui=gui,
        candidate_model_spec_sha256=candidate_digest,
        source_structure_sha256=source_structure_digest,
        electronic_receipt_sha256=(
            canonical_json_sha256(electronic_receipt)
            if electronic_receipt is not None
            else None
        ),
        execution_attempt_sha256=(
            canonical_json_sha256(terminal_attempt)
            if isinstance(terminal_attempt, dict)
            else None
        ),
        native_castep_sha256=native_digest,
    )


__all__ = ["verify_castep_acceptance_execution"]
