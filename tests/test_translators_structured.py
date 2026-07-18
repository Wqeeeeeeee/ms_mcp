from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.translators import render_model_to_perl, write_crystal_cif
from material_studio_mcp_server.validators import validate_generated_script


def load_example(name: str) -> ModelSpec:
    path = Path("src/material_studio_mcp_server/examples") / name
    return ModelSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_molecule_translator_generates_safe_perl(tmp_path: Path) -> None:
    spec = load_example("benzene_spec.json")
    generated = render_model_to_perl(spec, tmp_path)

    assert "use MaterialsScript qw(:all);" in generated.script
    assert "Modules->Forcite->GeometryOptimization->Run" in generated.script
    assert "Convergence =>" not in generated.script
    assert "__MS_MCP_JSON_START__" in generated.script
    assert validate_generated_script(generated.script)["valid"] is True


def test_castep_translator_generates_energy_script(tmp_path: Path) -> None:
    spec = load_example("castep_energy_spec.json")
    generated = render_model_to_perl(spec, tmp_path)

    assert "Modules->CASTEP->Energy->Run" in generated.script
    assert "UseCustomEnergyCutoff => 'Yes'" in generated.script
    assert "EnergyCutoff => 520" in generated.script
    assert "KPointDerivation => 'CustomGrid'" in generated.script
    assert "ParameterA => 3" in generated.script
    assert "Task =>" not in generated.script
    assert "KPoints =>" not in generated.script
    assert validate_generated_script(generated.script)["valid"] is True


def test_crystal_translator_is_preview_only(tmp_path: Path) -> None:
    spec = load_example("graphene_vacancy_spec.json")
    generated = render_model_to_perl(spec, tmp_path)

    assert generated.executable is False
    assert "preview-only" in generated.script
    assert len(generated.warnings) == 1
    assert "Modules->CASTEP" not in generated.script
    assert generated.planned_outputs["structure"].endswith(".cif")
    assert generated.calculation_preview_script is not None
    assert "Documents->Import" in generated.calculation_preview_script
    assert "Modules->CASTEP->Energy->Run" in generated.calculation_preview_script
    assert "EnergyCutoff => 400" in generated.calculation_preview_script
    assert "__MS_MCP_JSON_START__" in generated.calculation_preview_script
    assert validate_generated_script(generated.calculation_preview_script)["valid"] is True
    assert generated.calculation_preview is not None
    assert (
        generated.calculation_preview["input_structure"]
        == generated.planned_outputs["structure"]
    )
    assert generated.calculation_preview["task"] == "Energy"
    assert generated.calculation_preview["execution_policy"] == "preview_only"
    assert generated.calculation_preview["separate_execution_policy"] == (
        "explicit_execute_only"
    )
    assert generated.calculation_preview["execution_supported_by_structured_workflow"] is True
    assert generated.calculation_preview["execution_tool"] == (
        "material_studio_castep_run_current"
    )
    handoff = generated.calculation_preview["execution_handoff"]
    assert handoff["status"] == "explicit_execution_available"
    assert handoff["source_revision"] == spec.revision
    assert handoff["preview_action"]["payload_hint"] == {
        "project_id": spec.project_id,
        "expected_revision": spec.revision,
        "execution_mode": "preview",
        "task": "Energy",
        "open_in_gui": False,
        "take_snapshot": False,
        "export_view_audit": True,
        "response_mode": "compact",
    }
    assert handoff["preview_action"]["safe_to_call_without_confirmation"] is True
    assert handoff["execute_action"]["needs_user_confirmation"] is True
    assert generated.calculation_preview["calculation_executed"] is False
    assert (
        generated.calculation_preview["structure_materialization_executes_calculation"]
        is False
    )


def test_crystal_castep_handoff_maps_geometry_and_keeps_optics_preview_only(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        Path(
            "src/material_studio_mcp_server/examples/silicon_diamond_spec.json"
        ).read_text(encoding="utf-8")
    )
    payload["project_id"] = "castep_handoff_task_map"
    payload["simulation"]["task"] = "GeometryOptimization"
    geometry = render_model_to_perl(ModelSpec.model_validate(payload), tmp_path)
    assert geometry.calculation_preview is not None
    assert geometry.calculation_preview["execution_tool"] == (
        "material_studio_castep_relax_current"
    )
    geometry_payload = geometry.calculation_preview["execution_handoff"][
        "preview_action"
    ]["payload_hint"]
    assert geometry_payload["expected_revision"] == 0
    assert "task" not in geometry_payload

    payload["simulation"]["task"] = "Optics"
    optics = render_model_to_perl(ModelSpec.model_validate(payload), tmp_path)
    assert optics.calculation_preview is not None
    assert optics.calculation_preview["execution_policy"] == "preview_only"
    assert optics.calculation_preview["separate_execution_policy"] == "unavailable"
    assert optics.calculation_preview["execution_supported_by_structured_workflow"] is False
    assert optics.calculation_preview["execution_tool"] is None
    optics_handoff = optics.calculation_preview["execution_handoff"]
    assert optics_handoff["status"] == "preview_only_no_dedicated_execution_tool"
    assert optics_handoff["execute_action"] is None
    assert optics_handoff["preview_action"]["recommended_tool"] == (
        "material_studio_model_preview_script"
    )


def test_crystal_cif_writer_materializes_fractional_structure(tmp_path: Path) -> None:
    spec = load_example("silicon_diamond_spec.json")
    output = write_crystal_cif(spec.model, tmp_path / "silicon.cif")
    text = output.read_text(encoding="utf-8")

    assert output.exists()
    assert "_cell_length_a    5.431" in text
    assert "_atom_site_fract_x" in text
    assert "Si1 Si 0 0 0" in text
    assert "Si8 Si 0.75 0.75 0.25" in text


def test_script_safety_rejects_shell_calls() -> None:
    script = "use strict;\nuse MaterialsScript qw(:all);\nmy $doc = Documents->New('x.xsd');\nsystem('del x');\n"
    validation = validate_generated_script(script)
    assert validation["valid"] is False
