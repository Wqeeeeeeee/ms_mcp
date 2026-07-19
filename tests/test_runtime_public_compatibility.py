from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "runtime"

EXPECTED_PUBLIC_TOOL_NAMES = (
    "material_studio_build_molecule",
    "material_studio_build_tnt",
    "material_studio_castep_energy_script",
    "material_studio_castep_relax_current",
    "material_studio_castep_run_current",
    "material_studio_forcite_dynamics_from_spec",
    "material_studio_forcite_geometry_optimization",
    "material_studio_get_status",
    "material_studio_gui_activate",
    "material_studio_gui_apply_current_revision",
    "material_studio_gui_copy_script_assist",
    "material_studio_gui_execute_view_replay",
    "material_studio_gui_fit_to_view",
    "material_studio_gui_launch",
    "material_studio_gui_open_structure",
    "material_studio_gui_prepare_view_replay",
    "material_studio_gui_record_view_replay",
    "material_studio_gui_record_visual_confirmation",
    "material_studio_gui_snapshot",
    "material_studio_gui_status",
    "material_studio_import_export",
    "material_studio_list_script_templates",
    "material_studio_live_capabilities",
    "material_studio_live_modeling_request",
    "material_studio_live_project_status",
    "material_studio_live_session_preflight",
    "material_studio_live_update_with_patch",
    "material_studio_model_create_from_spec",
    "material_studio_model_export_view_audit",
    "material_studio_model_export_view_bundle",
    "material_studio_model_get_current",
    "material_studio_model_modify_with_patch",
    "material_studio_model_preview_script",
    "material_studio_model_validate",
    "material_studio_project_history",
    "material_studio_project_reconcile_dopant_metadata",
    "material_studio_project_rollback",
    "material_studio_run_script",
    "material_studio_structure_summary",
    "material_studio_validate_script",
)


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    source_path = str(ROOT / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else os.pathsep.join((source_path, existing_pythonpath))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _file_state(root: Path) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        sorted(
            (
                str(path.relative_to(root)),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_runtime_import_preserves_exact_public_tool_names_and_input_schema() -> None:
    script = r'''
import copy
import inspect
import json
import sys

from material_studio_mcp_server import server

runtime_module = "material_studio_mcp_server.runtime"
assert runtime_module not in sys.modules

def public_surface():
    tools = server.mcp._tool_manager.list_tools()
    target = next(
        tool
        for tool in tools
        if tool.name == "material_studio_live_modeling_request"
    )
    return {
        "tool_names": sorted(tool.name for tool in tools),
        "input_schema": copy.deepcopy(target.parameters),
        "python_signature": str(
            inspect.signature(server.material_studio_live_modeling_request)
        ),
    }

before = public_surface()
import material_studio_mcp_server.runtime
after = public_surface()
print(json.dumps({"before": before, "after": after}, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=_subprocess_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    before = result["before"]
    after = result["after"]

    assert tuple(before["tool_names"]) == EXPECTED_PUBLIC_TOOL_NAMES
    assert len(before["tool_names"]) == 40
    assert tuple(after["tool_names"]) == EXPECTED_PUBLIC_TOOL_NAMES
    assert after["input_schema"] == before["input_schema"]
    assert after["python_signature"] == before["python_signature"]


def test_runtime_import_is_silent_and_creates_no_files(tmp_path: Path) -> None:
    before = _file_state(RUNTIME_PACKAGE)
    completed = subprocess.run(
        [sys.executable, "-c", "import material_studio_mcp_server.runtime"],
        cwd=tmp_path,
        env=_subprocess_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not tuple(tmp_path.iterdir())
    assert _file_state(RUNTIME_PACKAGE) == before
