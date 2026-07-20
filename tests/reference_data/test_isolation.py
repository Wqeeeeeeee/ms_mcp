from __future__ import annotations

import ast
import copy
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

import pytest

from material_studio_mcp_server.reference_data import ingest_reference, verify_ingestion

from conftest import SYNTHETIC_RAW_BYTES


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "reference_data"

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
    source_path = str(ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join((source_path, existing))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def test_blind_reference_ingestion_never_writes_candidate_sibling(
    tmp_path: Path,
    reference_source: object,
) -> None:
    reference_root = tmp_path / "references"
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    sentinel = candidate_root / "candidate.json"
    sentinel.write_bytes(b"candidate sentinel")
    before = tuple(
        (path.relative_to(candidate_root).as_posix(), path.read_bytes())
        for path in candidate_root.rglob("*")
        if path.is_file()
    )

    receipt = ingest_reference(
        reference_store_root=reference_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    verify_ingestion(reference_store_root=reference_root, receipt=receipt)

    after = tuple(
        (path.relative_to(candidate_root).as_posix(), path.read_bytes())
        for path in candidate_root.rglob("*")
        if path.is_file()
    )
    assert after == before
    assert "candidate" not in inspect.signature(ingest_reference).parameters
    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in inspect.signature(ingest_reference).parameters.values()
    )


def test_package_import_is_offline_silent_and_side_effect_free(tmp_path: Path) -> None:
    script = r'''
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

def blocked(*args, **kwargs):
    raise AssertionError("forbidden import side effect")

socket.create_connection = blocked
subprocess.Popen.__init__ = blocked
subprocess.run = blocked
urllib.request.urlopen = blocked
os.system = blocked

class BlockForbiddenModules:
    prefixes = (
        "material_studio_mcp_server.gui",
        "material_studio_mcp_server.state",
        "material_studio_mcp_server.runner",
        "material_studio_mcp_server.runners",
        "material_studio_mcp_server.translators",
        "material_studio_mcp_server.parsers",
        "material_studio_mcp_server.orchestration",
    )

    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(self.prefixes):
            raise AssertionError(f"forbidden module import: {fullname}")
        return None

sys.meta_path.insert(0, BlockForbiddenModules())
before = tuple(Path.cwd().iterdir())
import material_studio_mcp_server.reference_data
after = tuple(Path.cwd().iterdir())
assert after == before
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
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


def test_explicit_ingestion_does_not_call_network_process_gui_or_state(
    tmp_path: Path,
    reference_source: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden side-effect surface invoked")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(os, "system", blocked)
    forbidden_modules = (
        "material_studio_mcp_server.gui",
        "material_studio_mcp_server.state",
        "material_studio_mcp_server.runner",
        "material_studio_mcp_server.runners",
        "material_studio_mcp_server.translators",
        "material_studio_mcp_server.parsers",
        "material_studio_mcp_server.orchestration",
    )
    before_modules = {name for name in sys.modules if name.startswith(forbidden_modules)}
    receipt = ingest_reference(
        reference_store_root=tmp_path / "references",
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    verify_ingestion(reference_store_root=tmp_path / "references", receipt=receipt)
    after_modules = {name for name in sys.modules if name.startswith(forbidden_modules)}
    assert after_modules == before_modules


def test_reference_package_has_no_forbidden_internal_imports() -> None:
    forbidden = (
        "material_studio_mcp_server.gui",
        "material_studio_mcp_server.state",
        "material_studio_mcp_server.runner",
        "material_studio_mcp_server.runners",
        "material_studio_mcp_server.translators",
        "material_studio_mcp_server.parsers",
        "material_studio_mcp_server.orchestration",
        "material_studio_mcp_server.server",
        "material_studio_mcp_server.natural_language",
    )
    imported: set[str] = set()
    for path in REFERENCE_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(name.startswith(forbidden) for name in imported)


def test_reference_import_preserves_exact_public_tools_and_modeling_schema() -> None:
    script = r'''
import copy
import inspect
import json
import sys

from material_studio_mcp_server import server

assert "material_studio_mcp_server.reference_data" not in sys.modules

def public_surface():
    tools = server.mcp._tool_manager.list_tools()
    target = next(
        tool for tool in tools
        if tool.name == "material_studio_live_modeling_request"
    )
    return {
        "tool_names": sorted(tool.name for tool in tools),
        "input_schema": copy.deepcopy(target.parameters),
        "python_signature": str(inspect.signature(server.material_studio_live_modeling_request)),
    }

before = public_surface()
import material_studio_mcp_server.reference_data
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
    assert after["tool_names"] == before["tool_names"]
    assert after["input_schema"] == before["input_schema"]
    assert after["python_signature"] == before["python_signature"]
