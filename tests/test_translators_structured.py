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
    assert "CutoffEnergy => 520" in generated.script
    assert validate_generated_script(generated.script)["valid"] is True


def test_crystal_translator_is_preview_only(tmp_path: Path) -> None:
    spec = load_example("graphene_vacancy_spec.json")
    generated = render_model_to_perl(spec, tmp_path)

    assert generated.executable is False
    assert "preview-only" in generated.script
    assert generated.planned_outputs["structure"].endswith(".cif")


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
