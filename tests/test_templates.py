from __future__ import annotations

from pathlib import Path

from ms_mcp.adapters.materialscript_perl import MaterialsScriptPerlAdapter
from ms_mcp.adapters.materialscript_py import MaterialsScriptPythonAdapter
from ms_mcp.config import Settings


def render_settings() -> dict:
    return {
        "engine": "Forcite",
        "task": "GeometryOptimization",
        "quality": "Medium",
        "parameters": {"Forcefield": "COMPASS"},
        "cores": None,
    }


def test_perl_template_renders_forcite(tmp_path):
    adapter = MaterialsScriptPerlAdapter(Settings(workspace=str(tmp_path), script_runner=None))

    script = adapter.render_geometry_optimization_script(
        Path("input.cif"),
        render_settings(),
        tmp_path,
    )

    assert "Modules->Forcite->GeometryOptimization->Run" in script
    assert 'Quality => "Medium"' in script
    assert 'Forcefield => "COMPASS"' in script
    assert "Copy Script output" in script


def test_python_template_renders_forcite(tmp_path):
    adapter = MaterialsScriptPythonAdapter(Settings(workspace=str(tmp_path), script_runner=None, script_mode="python"))

    script = adapter.render_geometry_optimization_script(
        Path("input.cif"),
        render_settings(),
        tmp_path,
    )

    assert "Modules.Forcite.GeometryOptimization.Run" in script
    assert 'Quality="Medium"' in script
    assert 'Forcefield="COMPASS"' in script
    assert "Copy Script output" in script
