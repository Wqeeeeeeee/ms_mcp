from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from material_studio_mcp_server import server
import material_studio_mcp_server.gui as gui_module
from material_studio_mcp_server.gui import MaterialsStudioGuiController, ProcessInfo, WindowInfo
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.state.execution import (
    begin_execution_attempt,
    finish_execution_attempt,
    publish_terminal_execution_attempt,
)
from material_studio_mcp_server.state.store import ProjectStore
from material_studio_mcp_server.translators import render_model_to_perl


def _tiny_bmp() -> bytes:
    width = height = 2
    row_stride = 8
    pixel_data = b"\x30\x30\x30\x30\x30\x30\x30\x30" * height
    file_size = 54 + len(pixel_data)
    header = bytearray(54)
    header[0:2] = b"BM"
    header[2:6] = file_size.to_bytes(4, "little")
    header[10:14] = (54).to_bytes(4, "little")
    header[14:18] = (40).to_bytes(4, "little")
    header[18:22] = width.to_bytes(4, "little", signed=True)
    header[22:26] = height.to_bytes(4, "little", signed=True)
    header[26:28] = (1).to_bytes(2, "little")
    header[28:30] = (24).to_bytes(2, "little")
    header[34:38] = len(pixel_data).to_bytes(4, "little")
    assert row_stride * height == len(pixel_data)
    return bytes(header) + pixel_data


def _command_evidence() -> dict:
    contract = gui_module.VIEW_RUNTIME_ACCESSIBILITY_TOOLBAR_CONTRACTS["3D Viewer"]
    commands = [dict(item) for item in gui_module.MATERIALS_STUDIO_2020_VIEW_COMMANDS]
    return {
        "registry_found": True,
        "registry_path": r"C:\Materials Studio\#SVViewer3d.xml",
        "registry_sha256": "a" * 64,
        "registry_toolbar_parse_error": None,
        "registered_view_command_ids": [item["command_id"] for item in commands],
        "registry_toolbar_layouts": [
            {
                "registry_toolbar_name": contract["registry_toolbar_name"],
                "title": "3D Viewer",
                "entries": [
                    {"kind": kind, "command_id": command_id}
                    for kind, command_id in contract["entries"]
                ],
            }
        ],
    }


class _GuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False
    startup_dialog_open_supported = False

    def __init__(self) -> None:
        self.window = WindowInfo(
            handle=101,
            title="Untitled - Materials Studio",
            pid=202,
            rect=(0, 0, 800, 600),
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )
        self.extra_window: WindowInfo | None = None
        self.captured: list[Path] = []

    def list_processes(self) -> list[ProcessInfo]:
        processes = [ProcessInfo(name="MatStudio.exe", pid=self.window.pid or 202)]
        if self.extra_window is not None:
            processes.append(ProcessInfo(name="MatStudio.exe", pid=self.extra_window.pid or 303))
        return processes

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        windows = [self.window]
        if self.extra_window is not None:
            windows.append(self.extra_window)
        if pid is not None:
            return [item for item in windows if item.pid == pid]
        return windows

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and self.window.pid != pid:
            return None
        return self.window

    def activate_window(self, window: WindowInfo) -> bool:
        return window.handle == self.window.handle

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        assert window.handle == self.window.handle
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_tiny_bmp())
        self.captured.append(output_path)
        return output_path

    def open_file(self, path: Path) -> dict:
        raise AssertionError("Fit-to-View must not open a file")

    def open_file_in_existing_window(self, window: WindowInfo, path: Path) -> dict:
        raise AssertionError("Fit-to-View must not open a file")

    def launch_app(self) -> dict:
        raise AssertionError("Fit-to-View must not launch Materials Studio")


class _ReplayBackend:
    supported = True
    unavailable_reason = None

    def __init__(self) -> None:
        self.probe_calls: list[dict] = []
        self.execute_calls: list[dict] = []
        self.after_execute_probe_hook: object | None = None
        self.probe_call_hooks: dict[int, object] = {}
        self.gui_input_sent = False

    def probe(self, **kwargs: object) -> dict:
        raise AssertionError("Fit preflight must not use the full UIA probe")

    def probe_fit_to_view(self, **kwargs: object) -> dict:
        self.probe_calls.append(dict(kwargs))
        hook = self.probe_call_hooks.pop(len(self.probe_calls), None)
        if callable(hook):
            hook()
        command_labels = kwargs["command_labels"]
        assert command_labels == {
            "cmdViewer3DFitToView": "3D Viewer Fit to View"
        }
        return {
            "supported": True,
            "probe_kind": "bounded_native_fit_target",
            "fit_command_ready": True,
            "resolved_command_ids": ["cmdViewer3DFitToView"],
            "block_reasons": [],
            "viewport": None,
        }

    def execute_fit_to_view(self, **kwargs: object) -> dict:
        self.execute_calls.append(dict(kwargs))
        assert kwargs["registry_sha256"] == "a" * 64
        fresh_probe = self.probe_fit_to_view(
            window_handle=kwargs["window_handle"],
            expected_window_title=kwargs["expected_window_title"],
            expected_revision=kwargs["expected_revision"],
            toolbar_contracts=kwargs["toolbar_contracts"],
            command_labels=kwargs["command_labels"],
            expected_window_pid=kwargs["expected_window_pid"],
            expected_document_name=kwargs["expected_document_name"],
        )
        hook = self.after_execute_probe_hook
        if callable(hook):
            hook()
        immediate_gate = kwargs["pre_input_gate"]()
        gate_binding = immediate_gate.get("structure_binding") or {}
        binding_changed = (
            gate_binding.get("identity") != kwargs["expected_structure_binding"]
        )
        if immediate_gate["execution_ready"] is not True or binding_changed:
            return {
                "kind": "materials_studio_local_uia_fit_to_view",
                "command_id": "cmdViewer3DFitToView",
                "execution_succeeded": False,
                "gui_input_performed": False,
                "gui_modified": False,
                "gui_input_attempted": False,
                "side_effect_may_have_occurred": False,
                "automatic_retry_allowed": True,
                "structure_modified": False,
                "preflight_probe": fresh_probe,
                "immediate_pre_input_gate": immediate_gate,
                "error": (
                    "canonical structure binding changed"
                    if binding_changed
                    else "immediate controller gate blocked"
                ),
            }
        final_gate = kwargs["final_pre_dispatch_gate"]()
        final_proof_changed = (
            final_gate.get("proof_identity") != kwargs["expected_structure_proof"]
        )
        if final_gate["execution_ready"] is not True or final_proof_changed:
            return {
                "kind": "materials_studio_local_uia_fit_to_view",
                "command_id": "cmdViewer3DFitToView",
                "execution_succeeded": False,
                "gui_input_performed": False,
                "gui_modified": False,
                "gui_input_attempted": False,
                "side_effect_may_have_occurred": False,
                "automatic_retry_allowed": True,
                "structure_modified": False,
                "preflight_probe": fresh_probe,
                "immediate_pre_input_gate": immediate_gate,
                "final_pre_dispatch_gate": final_gate,
                "error": (
                    "final structure proof changed"
                    if final_proof_changed
                    else "final pre-dispatch gate blocked"
                ),
            }
        self.gui_input_sent = True
        return {
            "kind": "materials_studio_local_uia_fit_to_view",
            "command_id": "cmdViewer3DFitToView",
            "execution_succeeded": True,
            "gui_input_performed": True,
            "gui_modified": True,
            "gui_input_attempted": True,
            "side_effect_may_have_occurred": True,
            "automatic_retry_allowed": False,
            "structure_modified": False,
            "preflight_probe": fresh_probe,
            "immediate_pre_input_gate": immediate_gate,
            "final_pre_dispatch_gate": final_gate,
            "fit_command": {
                "target_kind": "verified_anonymous_toolbar_child",
                "invocation_method": "local_uia_invoke_pattern",
            },
        }


class _DedicatedReplayBackend(_ReplayBackend):
    def probe_fit_to_view(self, **kwargs: object) -> dict:
        self.probe_calls.append(dict(kwargs))
        return {
            "supported": True,
            "probe_kind": "bounded_native_fit_target",
            "fit_command_ready": True,
            "resolved_command_ids": ["cmdViewer3DFitToView"],
            "block_reasons": [],
            "viewport": None,
        }


class _LegacyReplayBackend:
    supported = True
    unavailable_reason = None
    miller_plane_transaction_supported = False

    def probe(self, **_kwargs: object) -> dict:
        return {"supported": True}

    def probe_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        expected_revision: int,
        toolbar_contracts: dict,
        command_labels: dict,
    ) -> dict:
        raise AssertionError("legacy Fit probe must not be called")

    def execute_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        toolbar_contracts: dict,
        command_labels: dict,
    ) -> dict:
        raise AssertionError("legacy Fit executor must not be called")


class _MissingFitProbeBackend:
    supported = True
    unavailable_reason = None

    def execute_fit_to_view(self, **kwargs: object) -> dict:
        return {}


class _MissingFitExecuteBackend:
    supported = True
    unavailable_reason = None

    def probe_fit_to_view(self, **kwargs: object) -> dict:
        return {}


class _LegacyStrictFitBackend:
    supported = True
    unavailable_reason = None

    def probe_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        expected_revision: int,
        toolbar_contracts: dict,
        command_labels: dict,
    ) -> dict:
        return {}

    def execute_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        toolbar_contracts: dict,
        command_labels: dict,
        registry_sha256: str | None = None,
    ) -> dict:
        return {}


class _CurrentStrictFitBackend:
    supported = True
    unavailable_reason = None

    def probe_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        expected_revision: int,
        toolbar_contracts: dict,
        command_labels: dict,
        expected_window_pid: int | None = None,
        expected_document_name: str | None = None,
    ) -> dict:
        return {}

    def execute_fit_to_view(
        self,
        *,
        window_handle: int,
        expected_window_title: str,
        toolbar_contracts: dict,
        command_labels: dict,
        registry_sha256: str | None = None,
        registry_path: str | Path | None = None,
        expected_revision: int | None = None,
        expected_window_pid: int | None = None,
        expected_document_name: str | None = None,
        expected_project_id: str | None = None,
        expected_structure_binding: dict | None = None,
        expected_structure_proof: dict | None = None,
        pre_input_gate: object | None = None,
        final_pre_dispatch_gate: object | None = None,
    ) -> dict:
        return {}


class _CapabilityController:
    def __init__(self, backend: object) -> None:
        self.view_replay_backend = backend

    def status(
        self,
        *,
        project_id: str | None = None,
        revision: int | None = None,
    ) -> dict:
        return {
            "ok": True,
            "supported": True,
            "requested_project_id": project_id,
            "requested_revision": revision,
            "local_uia_view_replay_supported": True,
            "local_uia_fit_to_view_supported": True,
            "capabilities": [
                "execute_standard_view_replay_with_local_uia",
                "execute_fit_to_view_with_local_uia",
            ],
        }


@pytest.mark.parametrize(
    ("backend", "fit_supported", "expected_reason"),
    [
        (
            _MissingFitProbeBackend(),
            False,
            "bounded_native_fit_probe_unavailable",
        ),
        (
            _MissingFitExecuteBackend(),
            False,
            "bounded_native_fit_execute_unavailable",
        ),
        (
            _LegacyStrictFitBackend(),
            False,
            "bounded_native_fit_probe_signature_incompatible",
        ),
        (_CurrentStrictFitBackend(), True, None),
        (_ReplayBackend(), True, None),
    ],
    ids=[
        "missing-probe",
        "missing-execute",
        "legacy-strict-signature",
        "current-strict-signature",
        "kwargs-compatible",
    ],
)
def test_server_gui_status_advertises_fit_only_for_compatible_backend(
    monkeypatch,
    tmp_path: Path,
    backend: object,
    fit_supported: bool,
    expected_reason: str | None,
) -> None:
    controller = _CapabilityController(backend)
    monkeypatch.setattr(server, "_gui_controller", lambda working_dir=None: controller)

    status = server.material_studio_gui_status(
        project_id="fit_capability_project",
        revision=0,
        working_dir=str(tmp_path),
    )

    assert status["ok"] is True
    assert status["local_uia_view_replay_supported"] is True
    assert status["local_uia_fit_to_view_supported"] is fit_supported
    assert (
        "execute_fit_to_view_with_local_uia" in status["capabilities"]
    ) is fit_supported
    contract = status["local_uia_fit_to_view_backend_contract"]
    assert contract["supported"] is fit_supported
    if expected_reason is None:
        assert status["local_uia_fit_to_view_unavailable_reason"] is None
    else:
        assert expected_reason in contract["block_reasons"]


def _write_completed_structure(
    store: ProjectStore,
    *,
    project_id: str,
    revision: int,
) -> Path:
    output_dir = store.outputs_dir(project_id, revision)
    structure = (output_dir / f"model_r{revision:03d}.cif").resolve()
    structure.write_text("data_model\n", encoding="utf-8")
    result_metadata_path = (output_dir / "result_metadata.json").resolve()
    project_dir = store.project_dir(project_id).resolve()
    lock_path = (output_dir / "revision_execution.lock").resolve()
    lock_path.write_bytes(b"\0")
    spec_path = (
        project_dir / "revisions" / f"r{revision:03d}_model_spec.json"
    ).resolve()
    script_path = (
        project_dir / "scripts" / f"r{revision:03d}_build.pl"
    ).resolve()
    script_path.parent.mkdir(parents=True, exist_ok=True)
    generated_script = render_model_to_perl(
        store.get_revision(project_id, revision), output_dir
    ).script
    script_path.write_text(generated_script, encoding="utf-8")
    spec_payload = json.loads(spec_path.read_text(encoding="utf-8"))
    started = begin_execution_attempt(
        output_dir,
        project_id=project_id,
        revision=revision,
        backend="test_materialization",
        lock_path=lock_path,
        spec_path=spec_path,
        spec_payload=spec_payload,
        script_path=script_path,
        script=script_path.read_text(encoding="utf-8"),
        planned_structure_path=str(structure),
        current_revision_at_start=revision,
    )
    attempt = finish_execution_attempt(
        started["attempt"],
        current_revision_after_execution=revision,
        current_revision_still_current=True,
        result_success=True,
        result_metadata_path=result_metadata_path,
    )
    publish_terminal_execution_attempt(
        output_dir,
        attempt.model_dump(mode="json"),
    )
    structure_sha256 = hashlib.sha256(structure.read_bytes()).hexdigest()
    store.write_result_metadata(
        project_id,
        revision,
        {
            "success": True,
            "planned_outputs": {"structure": str(structure)},
            "execution_attempt": attempt.model_dump(mode="json"),
            "structure_artifact_validation": {
                "ok": True,
                "status": "matched",
                "structure_path": str(structure),
                "sha256": structure_sha256,
                "file_size_bytes": structure.stat().st_size,
            },
        },
    )
    return structure


def _controller(tmp_path: Path) -> tuple[MaterialsStudioGuiController, _GuiBackend, _ReplayBackend, Path]:
    backend = _GuiBackend()
    replay = _ReplayBackend()
    controller = MaterialsStudioGuiController(
        tmp_path,
        backend=backend,
        view_replay_backend=replay,
    )
    spec_payload = json.loads(
        Path("src/material_studio_mcp_server/examples/benzene_spec.json").read_text(
            encoding="utf-8"
        )
    )
    store = ProjectStore(tmp_path)
    store.create_project(
        ModelSpec.model_validate({**spec_payload, "project_id": "fit_project"})
    )
    store.save_revision(
        "fit_project",
        store.load_current("fit_project"),
        action="test_revision_1",
        expected_revision=0,
        expected_new_revision=1,
    )
    store.save_revision(
        "fit_project",
        store.load_current("fit_project"),
        action="test_revision_2",
        expected_revision=1,
        expected_new_revision=2,
    )
    structure = _write_completed_structure(
        store,
        project_id="fit_project",
        revision=2,
    )
    wrapper = controller._create_project_wrapper(
        structure,
        project_id="fit_project",
        revision=2,
    )
    backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=202,
        rect=(0, 0, 800, 600),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    return controller, backend, replay, structure


def test_fit_to_view_preview_is_read_only_and_uses_fresh_fit_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, backend, replay, _structure = _controller(tmp_path)

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert result["execution_ready"] is True
    assert result["gui_input_performed"] is False
    assert result["structure_modified"] is False
    assert result["confirmation_action"]["payload"]["execution_mode"] == "execute"
    assert len(replay.probe_calls) == 1
    assert replay.execute_calls == []
    assert backend.captured == []
    assert not (tmp_path / "fit_project" / "gui_actions.jsonl").exists()


def test_fit_preview_accepts_journal_canonical_result_without_planned_outputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, structure = _controller(tmp_path)
    result_path = (
        tmp_path
        / "fit_project"
        / "outputs"
        / "r002"
        / "result_metadata.json"
    )
    result_metadata = json.loads(result_path.read_text(encoding="utf-8"))
    result_metadata.pop("planned_outputs")
    result_path.write_text(json.dumps(result_metadata), encoding="utf-8")

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert result["execution_ready"] is True
    binding = result["preflight"]["structure_binding"]
    assert binding["verified"] is True
    assert Path(binding["planned_structure_path"]).resolve() == structure.resolve()
    assert len(replay.probe_calls) == 1


def test_legacy_replay_backend_is_not_advertised_or_called_for_fit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, _replay, _structure = _controller(tmp_path)
    controller.view_replay_backend = _LegacyReplayBackend()

    status = controller.status(project_id="fit_project", revision=2)
    preview = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert status["local_uia_view_replay_supported"] is True
    assert status["local_uia_fit_to_view_supported"] is False
    assert "execute_fit_to_view_with_local_uia" not in status["capabilities"]
    assert preview["status"] == "blocked"
    assert preview["execution_ready"] is False
    assert "bounded_native_fit_probe_unavailable" in preview["preflight"][
        "block_reasons"
    ]


def test_fit_to_view_preview_prefers_bounded_native_probe_and_binds_document(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, _backend, _replay, _structure = _controller(tmp_path)
    dedicated = _DedicatedReplayBackend()
    controller.view_replay_backend = dedicated

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "preview_ready"
    assert len(dedicated.probe_calls) == 1
    call = dedicated.probe_calls[0]
    metadata = result["preflight"]["target_window_resolution"][
        "target_project_wrapper_metadata"
    ]
    assert call["expected_window_pid"] == 202
    assert call["expected_document_name"] == metadata["document_name"]
    assert result["preflight"]["local_uia_probe"]["viewport"] is None


def test_fit_to_view_execute_captures_evidence_and_preserves_structure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, backend, replay, structure = _controller(tmp_path)
    before_hash = hashlib.sha256(structure.read_bytes()).hexdigest()

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=True,
    )

    assert result["status"] == "executed"
    assert result["action_receipt"]["execution_succeeded"] is True
    assert result["gui_input_performed"] is True
    assert result["gui_modified"] is True
    assert result["structure_unchanged"] is True
    assert result["structure_sha256_before"] == before_hash
    assert result["structure_sha256_after"] == before_hash
    assert result["before_snapshot"]["analysis"]["readable"] is True
    assert result["after_snapshot"]["analysis"]["readable"] is True
    assert len(backend.captured) == 2
    assert len(replay.execute_calls) == 1
    log_path = Path(result["gui_log_path"])
    assert log_path.exists()
    assert "fit_to_view" in log_path.read_text(encoding="utf-8")


def test_fit_to_view_preserves_dispatch_receipt_when_post_hash_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, _replay, _structure = _controller(tmp_path)
    original_hash = gui_module._sha256_file
    def fail_second_hash(path: Path) -> tuple[str, int]:
        if _replay.gui_input_sent:
            raise OSError("post-dispatch hash unavailable")
        return original_hash(path)

    monkeypatch.setattr(gui_module, "_sha256_file", fail_second_hash)

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert result["structure_integrity_verified"] is False
    assert result["structure_modified"] is None
    assert "post-dispatch hash unavailable" in result["structure_evidence_error"]


def test_fit_to_view_preserves_dispatch_receipt_when_log_persistence_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, _replay, _structure = _controller(tmp_path)
    monkeypatch.setattr(
        controller,
        "_write_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("post-dispatch log unavailable")
        ),
    )

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert result["gui_log_persisted"] is False
    assert "post-dispatch log unavailable" in result["gui_log_warning"]


def test_fit_to_view_refuses_multiple_matstudio_processes_before_probe_or_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    controller, backend, replay, _structure = _controller(tmp_path)
    backend.extra_window = WindowInfo(
        handle=303,
        title="extra - Materials Studio",
        pid=303,
        rect=(0, 0, 600, 400),
        is_visible=True,
        is_minimized=False,
        is_foreground=False,
    )

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=True,
    )

    assert result["status"] == "blocked"
    assert result["execution_ready"] is False
    assert "exactly_one_matstudio_process_required" in result["preflight"]["block_reasons"]
    assert replay.probe_calls == []
    assert replay.execute_calls == []
    assert backend.captured == []


def test_fit_to_view_rejects_wrapper_source_outside_controller_workspace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    status = controller.status(project_id="fit_project", revision=2)
    metadata_path = Path(
        status["target_window_resolution"]["target_project_wrapper_metadata"][
            "metadata_path"
        ]
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    external_structure = tmp_path.parent / f"{tmp_path.name}_external.cif"
    external_structure.write_text("data_external\n", encoding="utf-8")
    metadata["source_path"] = str(external_structure.resolve())
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "blocked"
    assert result["execution_ready"] is False
    reasons = result["preflight"]["block_reasons"]
    assert "target_wrapper_provenance_unverified" in reasons
    assert "target_wrapper_source_not_inside_workspace" in reasons
    assert "target_wrapper_source_outside_controller_workspace" in reasons
    assert "target_wrapper_source_not_canonical_structure" in reasons
    assert replay.probe_calls == []


def test_fit_preview_blocks_when_authoritative_execution_journal_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    journal_path = (
        tmp_path
        / "fit_project"
        / "outputs"
        / "r002"
        / "execution_attempts.jsonl"
    )
    journal_path.unlink()

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "blocked"
    reasons = result["preflight"]["block_reasons"]
    assert "canonical_execution_history_inconsistent" in reasons
    assert "canonical_execution_journal_missing_or_invalid" in reasons
    assert replay.probe_calls == []


def test_fit_preview_blocks_result_and_wrapper_path_rewrite_without_journal_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    status = controller.status(project_id="fit_project", revision=2)
    metadata_path = Path(
        status["target_window_resolution"]["target_project_wrapper_metadata"][
            "metadata_path"
        ]
    )
    output_dir = tmp_path / "fit_project" / "outputs" / "r002"
    result_path = output_dir / "result_metadata.json"
    alternate = (output_dir / "alternate_r002.cif").resolve()
    alternate.write_text("data_alternate\n", encoding="utf-8")
    result_metadata = json.loads(result_path.read_text(encoding="utf-8"))
    result_metadata["execution_attempt"]["planned_structure_path"] = str(alternate)
    result_metadata["planned_outputs"]["structure"] = str(alternate)
    result_metadata["structure_artifact_validation"].update(
        {
            "structure_path": str(alternate),
            "sha256": hashlib.sha256(alternate.read_bytes()).hexdigest(),
            "file_size_bytes": alternate.stat().st_size,
        }
    )
    result_path.write_text(json.dumps(result_metadata), encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source_path"] = str(alternate)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "blocked"
    reasons = result["preflight"]["block_reasons"]
    assert "canonical_execution_history_inconsistent" in reasons
    assert "canonical_result_attempt_diverges_from_journal" in reasons
    assert replay.probe_calls == []


def test_fit_preview_blocks_structure_modified_after_artifact_validation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, structure = _controller(tmp_path)
    structure.write_text("data_tampered\n", encoding="utf-8")

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "blocked"
    reasons = result["preflight"]["block_reasons"]
    assert "structure_artifact_validation_sha256_mismatch" in reasons
    assert "structure_artifact_validation_size_mismatch" in reasons
    assert replay.probe_calls == []


def test_fit_preview_blocks_new_running_attempt_with_old_result_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, structure = _controller(tmp_path)
    store = ProjectStore(tmp_path)
    output_dir = store.outputs_dir("fit_project", 2)
    project_dir = store.project_dir("fit_project")
    spec_path = project_dir / "revisions" / "r002_model_spec.json"
    script_path = project_dir / "scripts" / "r002_build.pl"
    begin_execution_attempt(
        output_dir,
        project_id="fit_project",
        revision=2,
        backend="test_materialization",
        lock_path=output_dir / "revision_execution.lock",
        spec_path=spec_path,
        spec_payload=json.loads(spec_path.read_text(encoding="utf-8")),
        script_path=script_path,
        script=script_path.read_text(encoding="utf-8"),
        planned_structure_path=str(structure),
        current_revision_at_start=2,
    )

    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "blocked"
    reasons = result["preflight"]["block_reasons"]
    assert "canonical_execution_runtime_not_completed" in reasons
    assert "canonical_execution_attempt_not_from_journal" not in reasons
    assert replay.probe_calls == []


def test_fit_preview_rechecks_revision_after_bounded_native_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    store = ProjectStore(tmp_path)

    def advance_revision() -> None:
        store.save_revision(
            "fit_project",
            store.load_current("fit_project"),
            action="advance_during_preview_native_fit_probe",
            expected_revision=2,
            expected_new_revision=3,
        )

    replay.probe_call_hooks[1] = advance_revision
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="preview",
    )

    assert result["status"] == "blocked"
    assert result["execution_ready"] is False
    assert len(replay.probe_calls) == 1
    assert result["preflight"]["native_probe_performed"] is True
    post_gate = result["preflight"]["post_native_controller_gate"]
    assert post_gate["native_probe_performed"] is False
    assert "current_project_revision_advanced" in post_gate["block_reasons"]
    assert "current_project_revision_advanced" in result["preflight"][
        "block_reasons"
    ]


def test_fit_execute_rechecks_revision_after_fresh_native_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    store = ProjectStore(tmp_path)

    def advance_revision() -> None:
        replay.after_execute_probe_hook = None
        store.save_revision(
            "fit_project",
            store.load_current("fit_project"),
            action="advance_during_native_fit_probe",
            expected_revision=2,
            expected_new_revision=3,
        )

    replay.after_execute_probe_hook = advance_revision
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert result["side_effect_may_have_occurred"] is False
    assert result["automatic_retry_allowed"] is True
    assert len(replay.probe_calls) == 2
    gate = result["action_receipt"]["immediate_pre_input_gate"]
    assert gate["native_probe_performed"] is False
    assert "current_project_revision_advanced" in gate["block_reasons"]


def test_fit_execute_rechecks_process_count_after_fresh_native_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, backend, replay, _structure = _controller(tmp_path)

    def add_second_process() -> None:
        replay.after_execute_probe_hook = None
        backend.extra_window = WindowInfo(
            handle=303,
            title="extra - Materials Studio",
            pid=303,
            rect=(0, 0, 600, 400),
            is_visible=True,
            is_minimized=False,
            is_foreground=False,
        )

    replay.after_execute_probe_hook = add_second_process
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert len(replay.probe_calls) == 2
    gate = result["action_receipt"]["immediate_pre_input_gate"]
    assert gate["native_probe_performed"] is False
    assert "exactly_one_matstudio_process_required" in gate["block_reasons"]
    assert "exactly_one_matstudio_window_required" in gate["block_reasons"]


def test_fit_execute_rechecks_wrapper_metadata_after_fresh_native_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    status = controller.status(project_id="fit_project", revision=2)
    metadata_path = Path(
        status["target_window_resolution"]["target_project_wrapper_metadata"][
            "metadata_path"
        ]
    )
    unrelated = (tmp_path / "unrelated_workspace_file.cif").resolve()
    unrelated.write_text("data_unrelated\n", encoding="utf-8")

    def change_wrapper_source() -> None:
        replay.after_execute_probe_hook = None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["source_path"] = str(unrelated)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    replay.after_execute_probe_hook = change_wrapper_source
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert len(replay.probe_calls) == 2
    gate = result["action_receipt"]["immediate_pre_input_gate"]
    assert gate["native_probe_performed"] is False
    assert "target_wrapper_source_not_canonical_structure" in gate["block_reasons"]


def test_fit_execute_freezes_canonical_binding_across_native_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    status = controller.status(project_id="fit_project", revision=2)
    metadata_path = Path(
        status["target_window_resolution"]["target_project_wrapper_metadata"][
            "metadata_path"
        ]
    )
    output_dir = tmp_path / "fit_project" / "outputs" / "r002"
    result_path = output_dir / "result_metadata.json"
    alternate = (output_dir / "alternate_r002.cif").resolve()
    alternate.write_text("data_alternate\n", encoding="utf-8")

    def rewrite_result_and_wrapper() -> None:
        replay.after_execute_probe_hook = None
        result_metadata = json.loads(result_path.read_text(encoding="utf-8"))
        result_metadata["execution_attempt"]["planned_structure_path"] = str(
            alternate
        )
        result_metadata["planned_outputs"]["structure"] = str(alternate)
        result_metadata["structure_artifact_validation"].update(
            {
                "structure_path": str(alternate),
                "sha256": hashlib.sha256(alternate.read_bytes()).hexdigest(),
                "file_size_bytes": alternate.stat().st_size,
            }
        )
        result_path.write_text(json.dumps(result_metadata), encoding="utf-8")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["source_path"] = str(alternate)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    replay.after_execute_probe_hook = rewrite_result_and_wrapper
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert result["automatic_retry_allowed"] is True
    gate = result["action_receipt"]["immediate_pre_input_gate"]
    assert "canonical_execution_history_inconsistent" in gate["block_reasons"]
    assert "canonical_result_attempt_diverges_from_journal" in gate[
        "block_reasons"
    ]


def test_fit_execute_final_gate_catches_revision_change_after_structure_binding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, _structure = _controller(tmp_path)
    store = ProjectStore(tmp_path)
    original_binding = controller._fit_to_view_structure_binding
    binding_calls = 0

    def binding_then_advance(**kwargs: object) -> dict:
        nonlocal binding_calls
        binding_calls += 1
        receipt = original_binding(**kwargs)
        if binding_calls == 3:
            store.save_revision(
                "fit_project",
                store.load_current("fit_project"),
                action="advance_after_immediate_fit_binding",
                expected_revision=2,
                expected_new_revision=3,
            )
        return receipt

    monkeypatch.setattr(
        controller,
        "_fit_to_view_structure_binding",
        binding_then_advance,
    )
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert binding_calls == 3
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert replay.gui_input_sent is False
    gate = result["action_receipt"]["immediate_pre_input_gate"]
    assert gate["current_revision_before_return"] == 3
    assert "current_project_revision_changed_before_fit_gate_return" in gate[
        "block_reasons"
    ]


def test_fit_execute_final_gate_catches_second_window_after_structure_binding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, backend, replay, _structure = _controller(tmp_path)
    original_binding = controller._fit_to_view_structure_binding
    binding_calls = 0

    def binding_then_add_window(**kwargs: object) -> dict:
        nonlocal binding_calls
        binding_calls += 1
        receipt = original_binding(**kwargs)
        if binding_calls == 3:
            backend.extra_window = WindowInfo(
                handle=303,
                title="late extra - Materials Studio",
                pid=303,
                rect=(0, 0, 600, 400),
                is_visible=True,
                is_minimized=False,
                is_foreground=False,
            )
        return receipt

    monkeypatch.setattr(
        controller,
        "_fit_to_view_structure_binding",
        binding_then_add_window,
    )
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert binding_calls == 3
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert replay.gui_input_sent is False
    gate = result["action_receipt"]["immediate_pre_input_gate"]
    assert gate["final_status_recheck"]["process_count"] == 2
    assert gate["final_status_recheck"]["window_count"] == 2
    assert "process_count_changed_before_fit_gate_return" in gate["block_reasons"]
    assert "window_count_changed_before_fit_gate_return" in gate["block_reasons"]


def test_fit_final_proof_catches_in_place_structure_change_after_full_gate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, structure = _controller(tmp_path)
    original_binding = controller._fit_to_view_structure_binding
    binding_calls = 0

    def binding_then_modify_structure(**kwargs: object) -> dict:
        nonlocal binding_calls
        binding_calls += 1
        receipt = original_binding(**kwargs)
        if binding_calls == 3:
            structure.write_text("data_changed_after_full_gate\n", encoding="utf-8")
        return receipt

    monkeypatch.setattr(
        controller,
        "_fit_to_view_structure_binding",
        binding_then_modify_structure,
    )
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert binding_calls == 3
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert replay.gui_input_sent is False
    final_gate = result["action_receipt"]["final_pre_dispatch_gate"]
    assert "fit_final_file_proof_changed" in final_gate["block_reasons"]


def test_fit_final_proof_catches_new_running_attempt_after_full_gate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, structure = _controller(tmp_path)
    store = ProjectStore(tmp_path)
    output_dir = store.outputs_dir("fit_project", 2)
    project_dir = store.project_dir("fit_project")
    spec_path = project_dir / "revisions" / "r002_model_spec.json"
    script_path = project_dir / "scripts" / "r002_build.pl"
    original_binding = controller._fit_to_view_structure_binding
    binding_calls = 0

    def binding_then_start_attempt(**kwargs: object) -> dict:
        nonlocal binding_calls
        binding_calls += 1
        receipt = original_binding(**kwargs)
        if binding_calls == 3:
            begin_execution_attempt(
                output_dir,
                project_id="fit_project",
                revision=2,
                backend="test_materialization",
                lock_path=output_dir / "revision_execution.lock",
                spec_path=spec_path,
                spec_payload=json.loads(spec_path.read_text(encoding="utf-8")),
                script_path=script_path,
                script=script_path.read_text(encoding="utf-8"),
                planned_structure_path=str(structure),
                current_revision_at_start=2,
            )
        return receipt

    monkeypatch.setattr(
        controller,
        "_fit_to_view_structure_binding",
        binding_then_start_attempt,
    )
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert binding_calls == 3
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert replay.gui_input_sent is False
    final_gate = result["action_receipt"]["final_pre_dispatch_gate"]
    assert "fit_final_file_proof_changed" in final_gate["block_reasons"]


@pytest.mark.parametrize(
    "mutation_kind",
    ["structure", "running_attempt", "current_revision"],
)
def test_fit_final_second_proof_catches_mutation_after_preliminary_proof(
    monkeypatch,
    tmp_path: Path,
    mutation_kind: str,
) -> None:
    """Reproduce a change immediately after the fourth proof read returns."""

    monkeypatch.setattr(
        gui_module, "_materials_studio_view_command_evidence", _command_evidence
    )
    controller, _backend, replay, structure = _controller(tmp_path)
    store = ProjectStore(tmp_path)
    output_dir = store.outputs_dir("fit_project", 2)
    project_dir = store.project_dir("fit_project")
    spec_path = project_dir / "revisions" / "r002_model_spec.json"
    script_path = project_dir / "scripts" / "r002_build.pl"
    original_file_proof = controller._fit_to_view_file_proof
    proof_calls = 0

    def proof_then_mutate(**kwargs: object) -> dict:
        nonlocal proof_calls
        proof_calls += 1
        receipt = original_file_proof(**kwargs)
        if proof_calls == 4:
            if mutation_kind == "structure":
                structure.write_text(
                    "data_changed_between_final_proofs\n",
                    encoding="utf-8",
                )
            elif mutation_kind == "running_attempt":
                begin_execution_attempt(
                    output_dir,
                    project_id="fit_project",
                    revision=2,
                    backend="test_materialization",
                    lock_path=output_dir / "revision_execution.lock",
                    spec_path=spec_path,
                    spec_payload=json.loads(spec_path.read_text(encoding="utf-8")),
                    script_path=script_path,
                    script=script_path.read_text(encoding="utf-8"),
                    planned_structure_path=str(structure),
                    current_revision_at_start=2,
                )
            else:
                store.save_revision(
                    "fit_project",
                    store.load_current("fit_project"),
                    action="advance_between_final_fit_proofs",
                    expected_revision=2,
                    expected_new_revision=3,
                )
        return receipt

    monkeypatch.setattr(
        controller,
        "_fit_to_view_file_proof",
        proof_then_mutate,
    )
    result = controller.fit_to_view(
        project_id="fit_project",
        revision=2,
        execution_mode="execute",
        take_snapshot=False,
    )

    assert proof_calls == 5
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert replay.gui_input_sent is False
    final_gate = result["action_receipt"]["final_pre_dispatch_gate"]
    assert final_gate["preliminary_file_proof"]["verified"] is True
    assert final_gate["file_proof"]["identity"] != final_gate[
        "preliminary_file_proof"
    ]["identity"]
    assert "fit_final_file_proof_changed" in final_gate["block_reasons"]


def test_mcp_fit_to_view_defaults_preview_and_persists_execute_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gui_module, "_materials_studio_view_command_evidence", _command_evidence)
    spec_payload = json.loads(
        Path("src/material_studio_mcp_server/examples/benzene_spec.json").read_text(
            encoding="utf-8"
        )
    )
    spec = ModelSpec.model_validate(
        {
            **spec_payload,
            "project_id": "fit_server_project",
            "revision": 0,
        }
    )
    ProjectStore(tmp_path).create_project(spec)
    controller, backend, replay, _structure = _controller(tmp_path)
    structure = _write_completed_structure(
        ProjectStore(tmp_path),
        project_id="fit_server_project",
        revision=0,
    )
    wrapper = controller._create_project_wrapper(
        structure,
        project_id="fit_server_project",
        revision=0,
    )
    backend.window = WindowInfo(
        handle=101,
        title=f"{wrapper['project_name']} - Materials Studio",
        pid=202,
        rect=(0, 0, 800, 600),
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: controller,
    )

    preview = server.material_studio_gui_fit_to_view(
        project_id="fit_server_project",
        working_dir=str(tmp_path),
    )
    assert preview["ok"] is True
    assert preview["execution_mode"] == "preview"
    assert preview["gui_input_performed"] is False
    assert replay.execute_calls == []

    executed = server.material_studio_gui_fit_to_view(
        project_id="fit_server_project",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )
    assert executed["ok"] is True
    assert executed["status"] == "executed"
    assert executed["structure_unchanged"] is True
    assert executed["report_write_transaction"]["domain"] == "gui_artifact_report"
    assert Path(executed["report_json_path"]).exists()
    assert len(replay.execute_calls) == 1


class _DirectFitResultController:
    def __init__(self, result: dict) -> None:
        self.result = result

    def fit_to_view(self, **kwargs: object) -> dict:
        return {
            "project_id": kwargs["project_id"],
            "revision": kwargs["revision"],
            "execution_mode": kwargs["execution_mode"],
            "gui_input_performed": False,
            "gui_modified": False,
            "structure_modified": False,
            "structure_unchanged": True,
            **self.result,
        }


def _install_direct_fit_result_controller(
    monkeypatch,
    tmp_path: Path,
    result: dict,
) -> None:
    spec_payload = json.loads(
        Path("src/material_studio_mcp_server/examples/benzene_spec.json").read_text(
            encoding="utf-8"
        )
    )
    spec = ModelSpec.model_validate(
        {
            **spec_payload,
            "project_id": "direct_fit_result_project",
            "revision": 0,
        }
    )
    ProjectStore(tmp_path).create_project(spec)
    controller = _DirectFitResultController(result)
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: controller,
    )


def test_direct_mcp_fit_preview_block_is_not_reported_ok(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_direct_fit_result_controller(
        monkeypatch,
        tmp_path,
        {
            "status": "blocked",
            "execution_ready": False,
            "preflight": {"block_reasons": ["target_window_not_foreground"]},
            "recommended_tool": "material_studio_gui_activate",
        },
    )

    result = server.material_studio_gui_fit_to_view(
        project_id="direct_fit_result_project",
        execution_mode="preview",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["gui_input_attempted"] is False
    assert result["side_effect_may_have_occurred"] is False
    assert result["automatic_retry_allowed"] is False
    assert result["manual_review_required"] is False
    assert result["recommended_tool"] == "material_studio_gui_activate"


def test_direct_mcp_fit_pre_dispatch_failure_exposes_exact_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_direct_fit_result_controller(
        monkeypatch,
        tmp_path,
        {
            "status": "execution_failed",
            "action_receipt": {
                "execution_succeeded": False,
                "gui_input_attempted": False,
                "side_effect_may_have_occurred": False,
                "automatic_retry_allowed": True,
            },
            "gui_input_attempted": False,
            "side_effect_may_have_occurred": False,
            "automatic_retry_allowed": True,
        },
    )

    result = server.material_studio_gui_fit_to_view(
        project_id="direct_fit_result_project",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is False
    assert result["side_effect_may_have_occurred"] is False
    assert result["automatic_retry_allowed"] is True
    assert result["manual_review_required"] is False
    assert result["gui_fit_to_view_retry_tool"] == (
        "material_studio_gui_fit_to_view"
    )
    assert result["gui_fit_to_view_retry_payload"]["execution_mode"] == "execute"


def test_direct_mcp_fit_post_dispatch_failure_forbids_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_direct_fit_result_controller(
        monkeypatch,
        tmp_path,
        {
            "status": "execution_failed",
            "action_receipt": {
                "execution_succeeded": False,
                "gui_input_attempted": True,
                "side_effect_may_have_occurred": True,
                "automatic_retry_allowed": False,
            },
            "gui_input_attempted": True,
            "side_effect_may_have_occurred": True,
            "automatic_retry_allowed": False,
        },
    )

    result = server.material_studio_gui_fit_to_view(
        project_id="direct_fit_result_project",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "execution_failed"
    assert result["gui_input_attempted"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert result["manual_review_required"] is True
    assert result["recommended_tool"] == "material_studio_gui_status"
    assert "gui_fit_to_view_retry_tool" not in result
    assert "gui_fit_to_view_retry_payload" not in result


def test_direct_fit_conflicting_blocked_receipt_prefers_side_effect_evidence() -> None:
    result = server._direct_fit_to_view_failure_response(
        {
            "status": "blocked",
            "execution_mode": "execute",
            "gui_input_attempted": False,
            "side_effect_may_have_occurred": False,
            "automatic_retry_allowed": True,
            "action_receipt": {
                "gui_input_attempted": True,
                "side_effect_may_have_occurred": True,
                "automatic_retry_allowed": False,
            },
        },
        retry_payload={"execution_mode": "execute"},
        status_payload={"project_id": "project", "revision": 4},
    )

    assert result["ok"] is False
    assert result["gui_input_attempted"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert result["manual_review_required"] is True
    assert result["recommended_tool"] == "material_studio_gui_status"
    assert "gui_fit_to_view_retry_tool" not in result
    assert "gui_fit_to_view_retry_payload" not in result


def test_direct_mcp_fit_success_with_snapshot_warning_remains_ok(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_direct_fit_result_controller(
        monkeypatch,
        tmp_path,
        {
            "status": "executed",
            "snapshot_warning": "post-action snapshot unavailable",
            "gui_input_performed": True,
            "gui_input_attempted": True,
            "side_effect_may_have_occurred": True,
            "automatic_retry_allowed": False,
        },
    )

    result = server.material_studio_gui_fit_to_view(
        project_id="direct_fit_result_project",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["status"] == "executed"
    assert result["snapshot_warning"] == "post-action snapshot unavailable"
    assert result["gui_input_attempted"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False


@pytest.mark.parametrize(
    "failure_stage",
    ["snapshot_report", "transaction_receipt"],
)
def test_direct_mcp_fit_post_action_persistence_failure_preserves_no_retry_evidence(
    monkeypatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    fit_result = {
        "status": "executed",
        "gui_input_performed": True,
        "gui_input_attempted": True,
        "side_effect_may_have_occurred": True,
        "automatic_retry_allowed": False,
        "action_receipt": {
            "execution_succeeded": True,
            "gui_input_performed": True,
            "gui_input_attempted": True,
            "side_effect_may_have_occurred": True,
            "automatic_retry_allowed": False,
        },
    }
    if failure_stage == "snapshot_report":
        fit_result["before_snapshot"] = {
            "screenshot_path": str(tmp_path / "fit-before.bmp")
        }
        monkeypatch.setattr(
            server,
            "_persist_gui_snapshot_report",
            lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("snapshot report persistence failed")
            ),
        )
    else:
        monkeypatch.setattr(
            server,
            "_attach_gui_artifact_transaction",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("transaction receipt persistence failed")
            ),
        )
    _install_direct_fit_result_controller(monkeypatch, tmp_path, fit_result)

    result = server.material_studio_gui_fit_to_view(
        project_id="direct_fit_result_project",
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["status"] == "execution_failed"
    assert result["fit_action_status_before_persistence_failure"] == "executed"
    assert result["report_persistence_failed"] is True
    assert "persistence failed" in result["persistence_warning"]
    assert result["gui_input_performed"] is True
    assert result["gui_input_attempted"] is True
    assert result["side_effect_may_have_occurred"] is True
    assert result["automatic_retry_allowed"] is False
    assert result["manual_review_required"] is True
    assert result["recommended_tool"] == "material_studio_gui_status"
    assert result["action_receipt"]["automatic_retry_allowed"] is False
    assert "gui_fit_to_view_retry_tool" not in result
    assert "gui_fit_to_view_retry_payload" not in result
