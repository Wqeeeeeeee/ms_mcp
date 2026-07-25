from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from material_studio_mcp_server import roundtrip as roundtrip_module
from material_studio_mcp_server import server
from material_studio_mcp_server.gui import (
    MaterialsStudioGuiController,
    ProcessInfo,
    WindowInfo,
)
from material_studio_mcp_server.health import build_modeling_health
from material_studio_mcp_server.parsers.cif import parse_crystal_cif
from material_studio_mcp_server.roundtrip import (
    ROUNDTRIP_MAX_INPUT_BYTES,
    compare_cif_roundtrip,
    execute_roundtrip_audit,
    plan_roundtrip_audit,
)
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.translators import write_crystal_cif


def _silicon_spec(project_id: str = "roundtrip_test") -> ModelSpec:
    payload = json.loads(
        Path("src/material_studio_mcp_server/examples/silicon_diamond_spec.json")
        .read_text(encoding="utf-8")
    )
    payload["project_id"] = project_id
    return ModelSpec.model_validate(payload)


def _benzene_spec(project_id: str = "roundtrip_molecule_test") -> ModelSpec:
    payload = json.loads(
        Path("src/material_studio_mcp_server/examples/benzene_spec.json")
        .read_text(encoding="utf-8")
    )
    payload["project_id"] = project_id
    return ModelSpec.model_validate(payload)


def _render_cif(path: Path, atoms: list[dict], lattice: dict[str, float]) -> None:
    rows = [
        "data_roundtrip",
        f"_cell_length_a    {lattice['a']}",
        f"_cell_length_b    {lattice['b']}",
        f"_cell_length_c    {lattice['c']}",
        f"_cell_angle_alpha    {lattice['alpha']}",
        f"_cell_angle_beta    {lattice['beta']}",
        f"_cell_angle_gamma    {lattice['gamma']}",
        "loop_",
        "_atom_site_label",
        "_atom_site_type_symbol",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
    ]
    rows.extend(
        "{id} {element} {x:.12f} {y:.12f} {z:.12f}".format(
            id=atom["id"],
            element=atom["element"],
            x=float(atom["fractional"][0]),
            y=float(atom["fractional"][1]),
            z=float(atom["fractional"][2]),
        )
        for atom in atoms
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class _FakeRunner:
    def __init__(
        self,
        root: Path,
        *,
        change_gui: bool = False,
        backend: object | None = None,
    ) -> None:
        self.calls = 0
        self.change_gui = change_gui
        self.backend = backend
        runner_path = root / "fake-materials-studio-runner.bat"
        runner_path.parent.mkdir(parents=True, exist_ok=True)
        runner_path.write_text("fake runner", encoding="utf-8")
        self.config = SimpleNamespace(runner=runner_path, extra_runner_args=())

    def run_script(
        self,
        script: str,
        *,
        working_dir: str | Path,
        timeout_seconds: int | None,
        job_prefix: str,
        keep_script_name: str,
        direct_job_dir: bool = False,
    ):
        self.calls += 1
        assert direct_job_dir is True
        root = Path(working_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        script_path = root / keep_script_name
        script_path.write_text(script, encoding="utf-8")
        source_literal = re.search(r"my \$source = '([^']*)';", script)
        assert source_literal is not None
        source = source_literal.group(1)
        source = source.replace("\\'", "'").replace("\\\\", "\\")
        output = root / "roundtrip_output.cif"
        output.write_bytes(Path(source).read_bytes())
        if self.change_gui and self.backend is not None:
            self.backend.changed = True
        return SimpleNamespace(
            job_dir=root,
            script_path=script_path,
            success=True,
            timed_out=False,
            return_code=0,
            duration_seconds=0.01,
            completion_markers={
                "completion_status_ok": True,
                "matserver_exit_status_ok": True,
            },
            success_markers_required=False,
            created_files=[script_path, output],
            parsed_json={
                "source": str(Path(source).resolve()),
                "output": str(output),
                "document_name": "roundtrip_document",
            },
        )


class _MissingTaggedJsonRunner(_FakeRunner):
    def run_script(self, script: str, **kwargs):
        result = super().run_script(script, **kwargs)
        result.parsed_json = None
        return result


class _MissingTerminationMarkersRunner(_FakeRunner):
    def run_script(self, script: str, **kwargs):
        result = super().run_script(script, **kwargs)
        result.completion_markers = {
            "completion_status_ok": False,
            "matserver_exit_status_ok": False,
        }
        result.success_markers_required = True
        return result


class _OutsideScriptRunner(_FakeRunner):
    def run_script(self, script: str, **kwargs):
        result = super().run_script(script, **kwargs)
        outside_script = Path(kwargs["working_dir"]).resolve().parent / "outside-roundtrip.pl"
        outside_script.write_text(script, encoding="utf-8")
        result.script_path = outside_script
        return result


class _InventoryBackend:
    supported = True

    def __init__(self) -> None:
        self.changed = False

    def list_processes(self):
        processes = [SimpleNamespace(name="MatStudio.exe", pid=1001)]
        if self.changed:
            processes.append(SimpleNamespace(name="MatStudio.exe", pid=1002))
        return processes

    def list_windows(self):
        windows = [
            SimpleNamespace(
                pid=1001,
                handle=501,
                title="model.cif - Materials Studio",
                is_visible=True,
                is_minimized=False,
            )
        ]
        if self.changed:
            windows.append(
                SimpleNamespace(
                    pid=1002,
                    handle=502,
                    title="second - Materials Studio",
                    is_visible=True,
                    is_minimized=False,
                )
            )
        return windows


class _OpenGuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False
    startup_dialog_open_supported = False

    def __init__(self) -> None:
        self.window = WindowInfo(
            handle=701,
            title="roundtrip.cif - Materials Studio",
            pid=1701,
            rect=(0, 0, 1024, 768),
            is_visible=True,
            is_minimized=False,
            is_foreground=True,
        )
        self.opened: list[Path] = []

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(name="MatStudio.exe", pid=1701)]

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        if pid is not None and pid != self.window.pid:
            return []
        return [self.window]

    def find_window(self, pid: int | None = None) -> WindowInfo | None:
        if pid is not None and pid != self.window.pid:
            return None
        return self.window

    def activate_window(self, window: WindowInfo) -> bool:
        return window.handle == self.window.handle

    def open_file(self, path: Path) -> dict:
        resolved = path.resolve()
        self.opened.append(resolved)
        return {"method": "fake", "path": str(resolved), "pid": self.window.pid}

    def launch_app(self) -> dict:
        raise AssertionError("round-trip audit tests must not launch Materials Studio")


def test_compare_allows_reordering_labels_and_periodic_wrap(tmp_path: Path) -> None:
    spec = _silicon_spec()
    source = write_crystal_cif(spec.model, tmp_path / "source.cif")
    parsed = parse_crystal_cif(source)
    atoms = list(reversed(parsed["atoms"]))
    for index, atom in enumerate(atoms):
        atom = dict(atom)
        atom["id"] = f"renamed_{index}"
        atom["fractional"] = list(atom["fractional"])
        if index == 0:
            atom["fractional"][0] += 1.0
        atoms[index] = atom
    output = tmp_path / "output.cif"
    _render_cif(output, atoms, parsed["lattice"])

    comparison = compare_cif_roundtrip(source, output)

    assert comparison["passed"] is True
    assert comparison["mapping_coverage"] == 1.0
    assert comparison["maximum_displacement_angstrom"] == 0.0


def test_compare_rejects_geometry_and_composition_changes(tmp_path: Path) -> None:
    spec = _silicon_spec()
    source = write_crystal_cif(spec.model, tmp_path / "source.cif")
    parsed = parse_crystal_cif(source)
    atoms = [dict(atom, fractional=list(atom["fractional"])) for atom in parsed["atoms"]]
    atoms[0]["element"] = "P"
    atoms[0]["fractional"][0] += 0.25
    output = tmp_path / "tampered_composition.cif"
    _render_cif(output, atoms, parsed["lattice"])

    comparison = compare_cif_roundtrip(source, output)

    assert comparison["passed"] is False
    assert comparison["errors"]


def test_compare_rejects_lattice_changes(tmp_path: Path) -> None:
    spec = _silicon_spec()
    source = write_crystal_cif(spec.model, tmp_path / "source.cif")
    parsed = parse_crystal_cif(source)
    atoms = [dict(atom, fractional=list(atom["fractional"])) for atom in parsed["atoms"]]
    lattice = dict(parsed["lattice"])
    lattice["a"] += 1.0
    output = tmp_path / "tampered_lattice.cif"
    _render_cif(output, atoms, lattice)

    comparison = compare_cif_roundtrip(source, output)

    assert comparison["passed"] is False
    assert comparison["maximum_relative_lattice_error"] > 0.001


def test_compare_rejects_oversized_input_before_parsing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "oversized.cif"
    with oversized.open("wb") as handle:
        handle.seek(ROUNDTRIP_MAX_INPUT_BYTES)
        handle.write(b"x")
    output = tmp_path / "output.cif"
    output.write_text("not parsed", encoding="utf-8")

    def fail_if_parsed(_path):
        raise AssertionError("oversized input must be rejected before CIF parsing")

    monkeypatch.setattr(roundtrip_module, "parse_crystal_cif", fail_if_parsed)

    comparison = compare_cif_roundtrip(oversized, output)

    assert comparison["passed"] is False
    assert comparison["input_sha256"] is None
    assert any("exceeds" in error for error in comparison["errors"])


def test_preview_plan_is_side_effect_free_and_rejects_path_escape(tmp_path: Path) -> None:
    spec = _silicon_spec()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")

    plan = plan_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        gui_probe_planned=True,
    )

    assert plan["status"] == "preview_ready"
    assert plan["side_effects"] == {
        "files_written": False,
        "runner_called": False,
        "gui_input_performed": False,
    }
    assert not Path(plan["run_root"]).exists()

    outside = write_crystal_cif(spec.model, tmp_path / "outside.cif")
    escaped = plan_roundtrip_audit(
        spec,
        source_path=outside,
        output_dir=output_dir,
    )
    assert escaped["status"] == "blocked"
    assert any("escapes" in error for error in escaped["errors"])


def test_preview_script_safety_ignores_inert_path_text(tmp_path: Path) -> None:
    spec = _silicon_spec("path_literal_roundtrip")
    output_dir = tmp_path / "http-cache"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")

    plan = plan_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
    )

    assert plan["status"] == "preview_ready"
    assert plan["script_validation"]["forbidden_operations_absent"] is True


def test_preview_plan_rejects_preexisting_run_root(tmp_path: Path) -> None:
    spec = _silicon_spec("occupied_roundtrip_root")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    occupied = output_dir / "ms_roundtrip" / "occupied_001"
    occupied.mkdir(parents=True)

    plan = plan_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        run_id="occupied_001",
    )

    assert plan["status"] == "blocked"
    assert any("already exists" in error for error in plan["errors"])


def test_non_crystal_plan_is_explicitly_not_applicable(tmp_path: Path) -> None:
    spec = _benzene_spec()

    plan = plan_roundtrip_audit(
        spec,
        source_path=tmp_path / "not-materialized.xsd",
        output_dir=tmp_path,
        execution_mode="execute",
        gui_probe_planned=True,
    )

    assert plan["status"] == "not_applicable"
    assert plan["applicable"] is False
    assert plan["runner_call_planned"] is False
    assert plan["gui_probe_planned"] is False
    assert not (tmp_path / "ms_roundtrip").exists()


def test_execute_audit_binds_source_and_reports_unverified_runner(tmp_path: Path) -> None:
    spec = _silicon_spec()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    original = source.read_bytes()
    runner = _FakeRunner(tmp_path)

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=runner,
        run_id="attempt_001",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "passed"
    assert receipt["real_materials_studio_status"] == "NOT_RUN"
    assert receipt["scientific_correctness_established"] is False
    assert receipt["source_unchanged"] is True
    assert source.read_bytes() == original
    assert receipt["comparison"]["passed"] is True
    assert receipt["runner_return_code"] == 0
    assert all(receipt["runner_termination_markers"].values())
    assert receipt["runner_path_budget"]["direct_job_dir"] is True
    assert receipt["runner_path_budget"]["within_budget"] is True
    assert len(receipt["runner_script_bytes_sha256"]) == 64
    assert all(
        Path(path).parent == Path(receipt["run_root"])
        for path in receipt["runner_created_files"]
    )
    persisted_receipt = json.loads(
        Path(receipt["receipt_path"]).read_text(encoding="utf-8")
    )
    assert persisted_receipt == receipt
    assert runner.calls == 1


def test_execute_audit_rejects_source_changed_after_planning(
    monkeypatch,
    tmp_path: Path,
) -> None:
    spec = _silicon_spec("source_plan_binding")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    runner = _FakeRunner(tmp_path)
    original_plan = roundtrip_module.plan_roundtrip_audit

    def plan_then_mutate(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        source.write_text(source.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        return plan

    monkeypatch.setattr(roundtrip_module, "plan_roundtrip_audit", plan_then_mutate)

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=runner,
        run_id="source_binding_001",
    )

    assert receipt["status"] == "blocked"
    assert receipt["ok"] is False
    assert receipt["source_sha256_planned"] != receipt["source_sha256_before"]
    assert runner.calls == 0
    assert not Path(receipt["run_root"]).exists()


def test_real_ms_path_budget_block_prevents_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    spec = _silicon_spec("path_budget_block")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    runner = _FakeRunner(tmp_path)
    identity = {
        "path": str(runner.config.runner),
        "exists": True,
        "sha256": "a" * 64,
        "real_materials_studio_20_1": True,
        "runner_kind": "materials_studio_20_1",
    }
    monkeypatch.setattr(roundtrip_module, "_runner_identity", lambda _runner: identity)
    monkeypatch.setattr(
        roundtrip_module,
        "_roundtrip_runner_path_budget",
        lambda **_kwargs: {
            "schema_version": "materials_studio_20_1_path_budget_v1",
            "limit_characters": 240,
            "within_budget": False,
            "maximum_path_characters": 256,
            "longest_path_kind": "runner_log",
            "longest_path": "x" * 256,
            "direct_job_dir": True,
            "paths": {},
        },
    )

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=runner,
        run_id="path_budget_001",
    )

    assert receipt["status"] == "blocked"
    assert receipt["ok"] is False
    assert receipt["real_materials_studio_status"] == "FAIL"
    assert receipt["runner_path_budget"]["maximum_path_characters"] == 256
    assert receipt["runner_success"] is False
    assert runner.calls == 0
    assert not Path(receipt["run_root"]).exists()
    assert any("240-character budget" in error for error in receipt["errors"])


def test_execute_audit_rejects_runner_script_outside_run_root(tmp_path: Path) -> None:
    spec = _silicon_spec("script_confinement")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=_OutsideScriptRunner(tmp_path),
        run_id="script_confinement_001",
    )

    assert receipt["ok"] is False
    assert receipt["runner_script_confined"] is False
    assert any("script escaped" in error for error in receipt["errors"])


def test_execute_audit_rejects_new_gui_process(tmp_path: Path) -> None:
    spec = _silicon_spec("gui_roundtrip_test")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    backend = _InventoryBackend()
    runner = _FakeRunner(tmp_path, change_gui=True, backend=backend)

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=runner,
        run_id="attempt_gui_001",
        gui_backend=backend,
        require_single_window=True,
    )

    assert receipt["ok"] is False
    assert receipt["gui_invariant"]["passed"] is False
    assert receipt["gui_invariant"]["process_launched"] is True
    assert any("process/window identity" in error for error in receipt["errors"])


def test_execute_audit_requires_bound_tagged_json(tmp_path: Path) -> None:
    spec = _silicon_spec("missing_tagged_json")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    runner = _MissingTaggedJsonRunner(tmp_path)

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=runner,
        run_id="missing_tagged_001",
    )

    assert receipt["ok"] is False
    assert receipt["tagged_summary_verified"] is False
    assert any("tagged JSON" in error for error in receipt["errors"])


def test_real_ms_receipt_requires_both_termination_markers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    spec = _silicon_spec("missing_termination_markers")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    source = write_crystal_cif(spec.model, output_dir / "structure.cif")
    runner = _MissingTerminationMarkersRunner(tmp_path)
    identity = {
        "path": str(runner.config.runner),
        "exists": True,
        "sha256": "b" * 64,
        "real_materials_studio_20_1": True,
        "runner_kind": "materials_studio_20_1",
    }
    monkeypatch.setattr(roundtrip_module, "_runner_identity", lambda _runner: identity)

    receipt = execute_roundtrip_audit(
        spec,
        source_path=source,
        output_dir=output_dir,
        runner=runner,
        run_id="missing_termination_001",
    )

    assert receipt["status"] == "failed"
    assert receipt["ok"] is False
    assert receipt["runner_success"] is True
    assert receipt["runner_success_markers_required"] is True
    assert not any(receipt["runner_termination_markers"].values())
    assert receipt["real_materials_studio_status"] == "FAIL"
    assert any("termination markers" in error for error in receipt["errors"])


def test_roundtrip_health_ignores_not_applicable_audit() -> None:
    health = build_modeling_health(
        {
            "ok": True,
            "execution_mode": "execute",
            "result": {"success": True},
            "materials_studio_roundtrip_audit_requested": True,
            "materials_studio_roundtrip_audit": {
                "applicable": False,
                "status": "not_applicable",
                "ok": None,
                "real_materials_studio_status": "NOT_RUN",
                "errors": [],
                "warnings": [],
            },
        },
        execution_mode="execute",
    )

    assert health["ok"] is True
    assert not any(
        "does not establish real Materials Studio" in warning
        for warning in health["warnings"]
    )


def test_structured_create_default_does_not_call_roundtrip(monkeypatch, tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    monkeypatch.setattr(server, "runner", runner)
    spec = _silicon_spec("default_roundtrip_off")

    result = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        execution_mode="execute",
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert runner.calls == 0
    assert "materials_studio_roundtrip_audit" not in result
    metadata = json.loads(
        Path(result["result_metadata_path"]).read_text(encoding="utf-8")
    )
    assert "materials_studio_roundtrip_audit" not in metadata


def test_structured_create_execute_persists_roundtrip_receipt(monkeypatch, tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    monkeypatch.setattr(server, "runner", runner)
    spec = _silicon_spec("enabled_roundtrip")

    result = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        execution_mode="execute",
        verify_ms_roundtrip=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["materials_studio_roundtrip_audit_requested"] is True
    audit = result["materials_studio_roundtrip_audit"]
    assert audit["ok"] is True
    assert audit["real_materials_studio_status"] == "NOT_RUN"
    metadata = json.loads(
        Path(result["result_metadata_path"]).read_text(encoding="utf-8")
    )
    assert metadata["materials_studio_roundtrip_audit"]["project_id"] == spec.project_id
    assert metadata["materials_studio_roundtrip_audit"]["revision"] == 0

    status = server.material_studio_live_project_status(
        project_id=spec.project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
    )
    assert status["materials_studio_roundtrip_audit_requested"] is True
    assert status["materials_studio_roundtrip_audit"]["status"] == "passed"
    assert status["modeling_report"]["materials_studio_roundtrip_audit"][
        "script_identity_verified"
    ] is True

    compact = server.material_studio_live_project_status(
        project_id=spec.project_id,
        include_gui_status=False,
        working_dir=str(tmp_path),
        response_mode="compact",
    )
    compact_audit = compact["materials_studio_roundtrip_audit"]
    assert compact_audit["status"] == "passed"
    assert compact_audit["comparison"]["passed"] is True
    assert compact_audit["scientific_correctness_established"] is False
    assert compact_audit["runner_return_code"] == 0
    assert compact_audit["runner_path_budget"]["direct_job_dir"] is True
    assert all(compact_audit["runner_termination_markers"].values())
    assert len(compact_audit["runner_script_bytes_sha256"]) == 64
    assert "script" not in compact_audit


def test_non_crystal_execute_persists_not_applicable_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    spec = _benzene_spec("non_crystal_roundtrip")

    def execute_without_materials_studio(**_kwargs):
        return {
            "result": {
                "success": True,
                "returncode": 0,
                "execution_backend": "test_runner",
                "stdout": "",
                "stderr": "",
                "created_files": [],
                "duration_seconds": 0.0,
                "parsed_json": {"ok": True},
            }
        }

    monkeypatch.setattr(server, "_execute_structured_script", execute_without_materials_studio)

    result = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        execution_mode="execute",
        verify_ms_roundtrip=True,
        working_dir=str(tmp_path),
    )

    audit = result["materials_studio_roundtrip_audit"]
    assert audit["status"] == "not_applicable"
    assert audit["applicable"] is False
    assert audit["ok"] is None
    assert audit["real_materials_studio_status"] == "NOT_RUN"
    metadata = json.loads(
        Path(result["result_metadata_path"]).read_text(encoding="utf-8")
    )
    assert metadata["materials_studio_roundtrip_audit"] == audit


def test_failed_roundtrip_audit_prevents_gui_hotload(monkeypatch, tmp_path: Path) -> None:
    backend = _OpenGuiBackend()
    runner = _MissingTaggedJsonRunner(tmp_path)
    monkeypatch.setattr(server, "runner", runner)
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: MaterialsStudioGuiController(
            working_dir,
            backend=backend,
        ),
    )
    spec = _silicon_spec("failed_roundtrip_hotload")
    created = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    result = server.material_studio_gui_apply_current_revision(
        project_id=spec.project_id,
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=False,
        export_view_audit=False,
        verify_ms_roundtrip=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is False
    assert result["materials_studio_roundtrip_audit"]["ok"] is False
    assert result["result"]["success"] is False
    assert "gui_open" not in result
    assert backend.opened == []


def test_successful_roundtrip_audit_allows_existing_window_hotload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    backend = _OpenGuiBackend()
    runner = _FakeRunner(tmp_path)
    monkeypatch.setattr(server, "runner", runner)
    monkeypatch.setattr(
        server,
        "_gui_controller",
        lambda working_dir=None: MaterialsStudioGuiController(
            working_dir,
            backend=backend,
        ),
    )
    spec = _silicon_spec("successful_roundtrip_hotload")
    created = server.material_studio_model_create_from_spec(
        spec.model_dump(mode="json"),
        working_dir=str(tmp_path),
    )
    assert created["ok"] is True

    result = server.material_studio_gui_apply_current_revision(
        project_id=spec.project_id,
        execution_mode="execute",
        open_in_gui=True,
        take_snapshot=False,
        export_view_audit=False,
        verify_ms_roundtrip=True,
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["materials_studio_roundtrip_audit"]["ok"] is True
    assert result["materials_studio_roundtrip_audit"]["gui_invariant"][
        "passed"
    ] is True
    assert result["result"]["success"] is True
    assert result["gui_open"]["structure_path"].endswith(".cif")
    assert len(backend.opened) == 1
    assert backend.opened[0] == Path(result["planned_outputs"]["structure"]).resolve()
