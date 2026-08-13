from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import material_studio_mcp_server.server as server
from material_studio_mcp_server.gui import (
    MaterialsStudioGuiController,
    ProcessInfo,
    WindowInfo,
)
from material_studio_mcp_server.gui_loop import GuiLoopError


class _Backend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False

    def __init__(self) -> None:
        self.window = WindowInfo(
            handle=777,
            title="Untitled - Materials Studio",
            pid=2468,
            rect=(0, 0, 900, 700),
        )
        self.opened: list[Path] = []
        self.activated: list[int] = []

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=2468)]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and pid != self.window.pid:
            return []
        return [self.window]

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and pid != self.window.pid:
            return None
        return self.window

    def activate_window(self, window: WindowInfo) -> bool:
        self.activated.append(window.handle)
        return window.handle == self.window.handle

    def open_file(self, path: Path) -> dict:
        self.opened.append(path)
        return {"method": "test_dialog", "path": str(path)}

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        raise AssertionError("snapshot is disabled in these tests")

    def launch_app(self) -> dict:
        raise AssertionError("GUI-loop hot-load must never launch Materials Studio")


class _ReadyLoop:
    def __init__(self) -> None:
        self.bindings: list[dict] = []
        self.enqueued: list[dict] = []

    def status(self, binding: dict, *, job_id: str | None = None) -> dict:
        self.bindings.append(dict(binding))
        return {
            "ok": True,
            "status": "running",
            "loop_ready": True,
            "current_revision": int(binding["revision"]),
        }

    def enqueue_and_wait(
        self,
        structure_path: str | Path,
        binding: dict,
        target_revision: int,
        timeout_seconds: float = 30.0,
        *,
        document_name: str | None = None,
    ) -> dict:
        path = Path(structure_path).resolve()
        receipt = {
            "ok": True,
            "status": "done",
            "job_id": "a" * 32,
            "expected_revision": int(binding["revision"]),
            "target_revision": int(target_revision),
            "structure_path": str(path),
            "structure_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "document_name": document_name or path.name,
            "imported_document_name": document_name or path.name,
            "result": {
                "status": "done",
                "current_revision": int(target_revision),
                "current_document_name": document_name or path.name,
                "structure_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            },
        }
        self.enqueued.append(receipt)
        return receipt


class _UnavailableLoop:
    def status(self, binding: dict, *, job_id: str | None = None) -> dict:
        return {
            "ok": True,
            "status": "not_prepared",
            "loop_ready": False,
            "loop_lock_present": False,
            "queue": {
                "staging": [],
                "pending": [],
                "running": [],
                "done": [],
                "failed": [],
            },
        }


class _UncertainLoop:
    def __init__(self, receipt: dict) -> None:
        self.receipt = receipt

    def status(self, binding: dict, *, job_id: str | None = None) -> dict:
        return dict(self.receipt)


def _controller_with_wrapper(
    tmp_path: Path,
    *,
    loop_manager: object,
) -> tuple[MaterialsStudioGuiController, _Backend, dict]:
    backend = _Backend()
    controller = MaterialsStudioGuiController(
        tmp_path,
        backend=backend,
        gui_loop_manager=loop_manager,  # type: ignore[arg-type]
        gui_hotload_transport="auto",
    )
    initial = tmp_path / "initial.cif"
    initial.write_text("data_initial\n", encoding="utf-8")
    wrapper = controller._create_project_wrapper(
        initial,
        project_id="loop_proj",
        revision=0,
    )
    backend.window = WindowInfo(
        handle=777,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=2468,
        rect=(0, 0, 900, 700),
    )
    return controller, backend, wrapper


def test_auto_transport_hotloads_through_ready_loop_and_advances_live_binding(
    tmp_path: Path,
) -> None:
    loop = _ReadyLoop()
    controller, backend, wrapper = _controller_with_wrapper(
        tmp_path,
        loop_manager=loop,
    )
    target = tmp_path / "revision_1.cif"
    target.write_text("data_revision_1\n", encoding="utf-8")

    opened = controller.open_structure(
        target,
        project_id="loop_proj",
        revision=1,
        take_snapshot=False,
    )

    assert opened["hotload_transport_used"] == "loop"
    assert opened["gui_loop_used"] is True
    assert opened["open_result"]["method"] == "verified_gui_loop_import"
    assert opened["same_window_open_used"] is True
    assert opened["window"]["handle"] == 777
    assert backend.opened == []
    assert len(loop.enqueued) == 1
    static_metadata = json.loads(
        Path(wrapper["metadata_path"]).read_text(encoding="utf-8")
    )
    assert static_metadata["revision"] == 0
    metadata = controller._project_wrapper_metadata_for_window(backend.window)
    assert metadata is not None
    assert metadata["revision"] == 1
    assert metadata["wrapper_initial_revision"] == 0
    assert metadata["wrapper_binding_mode"] == "verified_gui_loop_live_binding"
    assert metadata["source_path"] == str(target.resolve())


def test_auto_transport_falls_back_only_when_loop_unavailable_before_enqueue(
    tmp_path: Path,
) -> None:
    controller, backend, _ = _controller_with_wrapper(
        tmp_path,
        loop_manager=_UnavailableLoop(),
    )
    target = tmp_path / "same_revision.cif"
    target.write_text("data_same_revision\n", encoding="utf-8")

    opened = controller.open_structure(
        target,
        project_id="loop_proj",
        revision=0,
        take_snapshot=False,
    )

    assert opened["hotload_transport_used"] == "dialog"
    assert opened["gui_loop_used"] is False
    assert backend.opened == [target.resolve()]


def test_auto_transport_keeps_legacy_dialog_path_without_any_loop_binding(
    tmp_path: Path,
) -> None:
    backend = _Backend()
    controller = MaterialsStudioGuiController(
        tmp_path,
        backend=backend,
        gui_loop_manager=_UnavailableLoop(),  # type: ignore[arg-type]
        gui_hotload_transport="auto",
    )
    target = tmp_path / "unbound.cif"
    target.write_text("data_unbound\n", encoding="utf-8")

    opened = controller.open_structure(
        target,
        project_id="unbound_proj",
        revision=1,
        take_snapshot=False,
    )

    assert opened["hotload_transport_used"] == "dialog"
    assert opened["gui_loop_used"] is False
    assert backend.opened == [target.resolve()]


@pytest.mark.parametrize(
    ("loop_receipt", "expected_status", "expected_job_ids"),
    [
        (
            {
                "ok": True,
                "status": "not_ready",
                "loop_ready": False,
                "loop_lock_present": True,
                "queue": {
                    "staging": [],
                    "pending": ["b" * 32],
                    "running": [],
                },
            },
            "loop_job_in_flight",
            ["b" * 32],
        ),
        (
            {
                "ok": True,
                "status": "stale",
                "loop_ready": False,
                "loop_lock_present": True,
                "heartbeat_present": True,
                "heartbeat_signature_valid": True,
                "queue": {"staging": [], "pending": [], "running": []},
            },
            "loop_preflight_uncertain",
            [],
        ),
        (
            {
                "ok": False,
                "status": "prepared",
                "loop_ready": False,
                "loop_lock_present": False,
                "prepared": True,
                "current_state_signature_valid": False,
                "current_state_error": "signed state mismatch",
                "queue": {"staging": [], "pending": [], "running": []},
            },
            "loop_preflight_uncertain",
            [],
        ),
    ],
)
def test_auto_transport_fails_closed_for_uncertain_or_active_loop_state(
    tmp_path: Path,
    loop_receipt: dict,
    expected_status: str,
    expected_job_ids: list[str],
) -> None:
    controller, backend, _ = _controller_with_wrapper(
        tmp_path,
        loop_manager=_UncertainLoop(loop_receipt),
    )
    target = tmp_path / "revision_1.cif"
    target.write_text("data_revision_1\n", encoding="utf-8")

    with pytest.raises(GuiLoopError) as exc:
        controller.open_structure(
            target,
            project_id="loop_proj",
            revision=1,
            take_snapshot=False,
        )

    assert exc.value.receipt["status"] == expected_status
    assert exc.value.receipt["active_job_ids"] == expected_job_ids
    assert exc.value.receipt["side_effect_may_have_occurred"] is True
    assert exc.value.receipt["automatic_dialog_fallback_allowed"] is False
    assert exc.value.receipt["gui_open_retry_allowed"] is False
    assert backend.opened == []


def test_explicit_loop_transport_fails_closed_when_not_started(tmp_path: Path) -> None:
    controller, backend, _ = _controller_with_wrapper(
        tmp_path,
        loop_manager=_UnavailableLoop(),
    )
    target = tmp_path / "revision_1.cif"
    target.write_text("data_revision_1\n", encoding="utf-8")

    with pytest.raises(GuiLoopError) as exc:
        controller.open_structure(
            target,
            project_id="loop_proj",
            revision=1,
            take_snapshot=False,
            hotload_transport="loop",
        )

    assert exc.value.receipt["status"] == "loop_start_required"
    assert exc.value.receipt["gui_input_started"] is False
    assert backend.opened == []


def test_prepare_loop_binds_loaded_revision_when_requested_revision_is_newer(
    tmp_path: Path,
) -> None:
    class PreparingLoop(_UnavailableLoop):
        def __init__(self) -> None:
            self.prepared_binding: dict | None = None

        def prepare(self, binding: dict) -> dict:
            self.prepared_binding = dict(binding)
            return {
                "ok": True,
                "status": "prepared",
                "queue_root": str(tmp_path / "gui_loop"),
                "loop_script_path": str(tmp_path / "materials_studio_gui_loop.pl"),
            }

    loop = PreparingLoop()
    controller, _, wrapper = _controller_with_wrapper(tmp_path, loop_manager=loop)

    prepared = controller.prepare_gui_loop(project_id="loop_proj", revision=1)

    assert prepared["revision"] == 0
    assert prepared["requested_revision"] == 1
    assert prepared["requested_revision_loaded"] is False
    assert loop.prepared_binding is not None
    assert loop.prepared_binding["revision"] == 0
    assert loop.prepared_binding["base_revision"] == 0
    assert loop.prepared_binding["initial_document_name"] == Path(
        wrapper["document_path"]
    ).stem


def test_gui_loop_mcp_tools_expose_controller_receipts(monkeypatch, tmp_path: Path) -> None:
    class ToolController:
        def gui_loop_status(self, **kwargs):
            return {"ok": True, "status": "running", "loop_ready": True, "kwargs": kwargs}

        def prepare_gui_loop(self, **kwargs):
            return {"status": "prepared", "loop_started": False, "kwargs": kwargs}

        def stop_gui_loop(self, **kwargs):
            return {"status": "stop_requested", "kwargs": kwargs}

    controller = ToolController()
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: controller)
    monkeypatch.setattr(
        server,
        "_resolve_gui_action_context",
        lambda **kwargs: {
            "resolved": True,
            "project_id": "loop_proj",
            "revision": 3,
            "reason": "explicit_project_revision",
        },
    )

    status = server.material_studio_gui_loop_status.__wrapped__(
        project_id="loop_proj", revision=3, working_dir=str(tmp_path)
    )
    prepared = server.material_studio_gui_loop_prepare.__wrapped__(
        project_id="loop_proj", revision=3, working_dir=str(tmp_path)
    )
    stopped = server.material_studio_gui_loop_stop.__wrapped__(
        project_id="loop_proj", revision=3, working_dir=str(tmp_path)
    )

    assert status["ok"] is True
    assert status["loop_ready"] is True
    assert prepared["ok"] is True
    assert prepared["loop_started"] is False
    assert stopped["ok"] is True
    assert stopped["status"] == "stop_requested"


def test_open_structure_mcp_forwards_explicit_loop_transport(
    monkeypatch,
    tmp_path: Path,
) -> None:
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    class ToolController:
        def __init__(self) -> None:
            self.open_kwargs: dict | None = None

        def status(self, **kwargs):
            return {
                "ok": True,
                "single_window_policy_ok": True,
                "single_window_violation_reasons": [],
                "window_management": {
                    "single_window_policy_ok": True,
                    "single_window_violation_reasons": [],
                },
            }

        def open_structure(self, path, **kwargs):
            self.open_kwargs = dict(kwargs)
            return {
                "project_id": kwargs.get("project_id"),
                "revision": kwargs.get("revision"),
                "structure_path": str(Path(path).resolve()),
                "open_result": {"method": "verified_gui_loop_import"},
                "single_window_policy_ok": True,
            }

    controller = ToolController()
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: controller)

    result = server.material_studio_gui_open_structure.__wrapped__(
        str(structure),
        project_id="loop_proj",
        revision=1,
        export_view_audit=False,
        take_snapshot=False,
        hotload_transport=server.GuiHotloadTransport.LOOP,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert controller.open_kwargs is not None
    assert controller.open_kwargs["hotload_transport"] == "loop"


def _high_level_gui_loop_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    response_mode: str,
) -> tuple[dict, _Backend]:
    backend = _Backend()
    controller = MaterialsStudioGuiController(tmp_path, backend=backend)
    job_id = "c" * 32

    def fail_after_enqueue(*args, **kwargs):
        raise GuiLoopError(
            "Timed out after the signed GUI-loop job was enqueued",
            {
                "status": "timeout",
                "job_id": job_id,
                "expected_revision": 0,
                "target_revision": 0,
                "side_effect_may_have_occurred": True,
                "automatic_dialog_fallback_allowed": False,
            },
        )

    monkeypatch.setattr(controller, "open_structure", fail_after_enqueue)
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: controller,
    )

    def fake_execute_structured_script(*, store, spec, script, timeout_seconds):
        output = (
            store.project_dir(spec.project_id)
            / "outputs"
            / f"r{spec.revision:03d}"
            / f"{spec.project_id}_r{spec.revision:03d}.xsd"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("high-level GUI-loop timeout fixture", encoding="utf-8")
        return {
            "result": {"success": True, "created_files": [str(output)]},
            "result_metadata_path": str(output.parent / "result_metadata.json"),
        }

    monkeypatch.setattr(
        server,
        "_execute_structured_script",
        fake_execute_structured_script,
    )
    spec = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/benzene_spec.json"
        ).read_text(encoding="utf-8")
    )
    spec["project_id"] = f"gui_loop_high_level_{response_mode}"
    result = server.material_studio_live_modeling_request(
        "Build benzene and hot-load it in Materials Studio.",
        spec=spec,
        execution_mode="execute",
        take_snapshot=False,
        response_mode=response_mode,
        working_dir=str(tmp_path),
    )
    return result, backend


def _assert_exact_gui_loop_failure_continuation(result: dict) -> None:
    job_id = "c" * 32
    expected_payload = {
        "project_id": result["project_id"],
        "revision": 0,
        "job_id": job_id,
        "working_dir": result["gui_loop_status_payload"]["working_dir"],
    }

    assert result["ok"] is False
    assert result["partial_success"] is True
    assert result["status"] == "timeout"
    assert result["job_id"] == job_id
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_dialog_fallback_allowed"] is False
    assert result["gui_open_retry_allowed"] is False
    assert result["execution_must_not_repeat"] is True
    assert result["gui_loop_failure"]["job_id"] == job_id
    assert result["gui_loop_status_tool"] == "material_studio_gui_loop_status"
    assert result["gui_loop_status_payload"] == expected_payload
    assert result["recommended_tool"] == "material_studio_gui_loop_status"
    assert result["next_action_plan"]["action_id"] == (
        "inspect_exact_signed_gui_loop_job"
    )
    assert result["next_action_plan"]["recommended_tool"] == (
        "material_studio_gui_loop_status"
    )
    assert result["next_action_plan"]["payload_hint"] == expected_payload
    assert result["next_action_plan"]["safe_to_call_without_confirmation"] is True
    assert "gui_open_retry_tool" not in result
    assert "gui_open_retry_payload" not in result


def test_high_level_gui_loop_timeout_persists_exact_full_continuation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result, backend = _high_level_gui_loop_timeout(
        monkeypatch,
        tmp_path,
        response_mode="full",
    )

    _assert_exact_gui_loop_failure_continuation(result)
    assert backend.opened == []
    assert result["modeling_report"]["gui_loop_status_payload"] == (
        result["gui_loop_status_payload"]
    )
    assert result["modeling_report"]["next_action_plan"]["recommended_tool"] == (
        "material_studio_gui_loop_status"
    )
    persisted = json.loads(
        Path(result["report_json_path"]).read_text(encoding="utf-8")
    )
    assert persisted["modeling_report"]["gui_loop_status_payload"] == (
        result["gui_loop_status_payload"]
    )
    assert persisted["modeling_report"]["next_action_plan"]["recommended_tool"] == (
        "material_studio_gui_loop_status"
    )


def test_high_level_gui_loop_timeout_survives_compact_hard_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result, backend = _high_level_gui_loop_timeout(
        monkeypatch,
        tmp_path,
        response_mode="compact",
    )

    _assert_exact_gui_loop_failure_continuation(result)
    assert backend.opened == []
    assert result["response_compaction"]["hard_budget_applied"] is True
    assert result["response_compaction"]["semantic_core_preserved"] is True
