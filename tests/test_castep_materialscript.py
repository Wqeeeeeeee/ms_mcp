from __future__ import annotations

import pytest
from pydantic import ValidationError

from material_studio_mcp_server import server
from material_studio_mcp_server.castep_materialscript import (
    CASTEP_MATERIALSCRIPT_CONTRACT,
    build_castep_materialscript_plan,
    render_castep_run_snippet,
)
from material_studio_mcp_server.scripts import castep_energy_script
from material_studio_mcp_server.specs.castep import CastepEnergySpec, CastepTask
from material_studio_mcp_server.specs.patch import SemanticPatchOperation
from material_studio_mcp_server.validators import validate_generated_script


@pytest.mark.parametrize(
    ("task", "api_object", "property_setting"),
    [
        ("Energy", "Modules->CASTEP->Energy", None),
        ("GeometryOptimization", "Modules->CASTEP->GeometryOptimization", None),
        ("BandStructure", "Modules->CASTEP->Energy", "CalculateBandStructure => 'Dispersion'"),
        ("DensityOfStates", "Modules->CASTEP->Energy", "CalculateDOS => 'Full'"),
        ("ProjectedDensityOfStates", "Modules->CASTEP->Energy", "CalculateDOS => 'Partial'"),
        ("Optics", "Modules->CASTEP->Energy", "CalculateOptics => 'Full'"),
        ("Phonon", "Modules->CASTEP->Energy", "CalculatePhononDispersion => 'Dispersion'"),
        ("ElasticConstants", "Modules->CASTEP->ElasticConstants", None),
    ],
)
def test_castep_task_dispatch_uses_ms_20_1_api(
    task: str,
    api_object: str,
    property_setting: str | None,
) -> None:
    spec = CastepEnergySpec(task=task)
    plan = build_castep_materialscript_plan(spec)
    script = render_castep_run_snippet(spec)

    assert plan.api_object == api_object
    assert plan.summary()["materials_studio_api_contract"] == CASTEP_MATERIALSCRIPT_CONTRACT
    assert f"{api_object}->Run($doc, Settings(" in script
    if property_setting is not None:
        assert property_setting in script
    assert "    Task =>" not in script
    assert "    CutoffEnergy =>" not in script
    assert "    KPoints =>" not in script

    full_script = castep_energy_script(
        "input.xsd",
        quality="Medium",
        task=task,
        functional="PBE",
        cutoff_energy_ev=None,
        kpoint_separation=None,
    )
    assert validate_generated_script(full_script)["valid"] is True


def test_castep_custom_cutoff_and_separation_use_documented_switches() -> None:
    script = render_castep_run_snippet(
        CastepEnergySpec(cutoff_energy_ev=520, kpoint_separation=0.03)
    )

    assert "UseCustomEnergyCutoff => 'Yes'" in script
    assert "EnergyCutoff => 520" in script
    assert "KPointDerivation => 'Separation'" in script
    assert "KPointSeparation => 0.03" in script
    assert "ParameterA =>" not in script


def test_castep_custom_grid_uses_parameter_axes() -> None:
    script = render_castep_run_snippet(CastepEnergySpec(kpoints=(6, 5, 4)))

    assert "KPointDerivation => 'CustomGrid'" in script
    assert "ParameterA => 6" in script
    assert "ParameterB => 5" in script
    assert "ParameterC => 4" in script
    assert "KPointSeparation =>" not in script


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("single point energy", CastepTask.ENERGY),
        ("geometry optimisation", CastepTask.GEOMETRY_OPTIMIZATION),
        ("band gap", CastepTask.BAND_STRUCTURE),
        ("DOS", CastepTask.DENSITY_OF_STATES),
        ("PDOS", CastepTask.PROJECTED_DENSITY_OF_STATES),
        ("optical properties", CastepTask.OPTICS),
        ("phonon dispersion", CastepTask.PHONON),
        ("Elastic", CastepTask.ELASTIC_CONSTANTS),
    ],
)
def test_castep_task_aliases_normalize(alias: str, canonical: CastepTask) -> None:
    assert CastepEnergySpec(task=alias).task is canonical


def test_castep_rejects_unknown_tasks_and_conflicting_kpoint_modes() -> None:
    with pytest.raises(ValidationError, match="Unsupported CASTEP task"):
        CastepEnergySpec(task="InventedTask")
    with pytest.raises(ValidationError, match="either kpoints or kpoint_separation"):
        CastepEnergySpec(kpoints=(4, 4, 4), kpoint_separation=0.05)


def test_castep_patch_operation_normalizes_and_rejects_tasks_early() -> None:
    operation = SemanticPatchOperation.model_validate(
        {"type": "set_castep_energy", "task": "Elastic"}
    )
    assert operation.task is CastepTask.ELASTIC_CONSTANTS

    with pytest.raises(ValidationError, match="Unsupported CASTEP task"):
        SemanticPatchOperation.model_validate(
            {"type": "set_castep_energy", "task": "InventedTask"}
        )


def test_castep_renderer_rejects_untrusted_perl_variable_names() -> None:
    with pytest.raises(ValueError, match="Perl scalar identifier"):
        render_castep_run_snippet(CastepEnergySpec(), document_variable="$doc; system('cmd')")


def test_legacy_castep_script_function_uses_shared_dispatch() -> None:
    script = castep_energy_script(
        "input.xsd",
        quality="Fine",
        task="Elastic",
        functional="PBE",
        cutoff_energy_ev=600,
        kpoint_separation=None,
        kpoints=(4, 4, 2),
    )

    assert "Modules->CASTEP->ElasticConstants->Run" in script
    assert "KPointDerivation => 'CustomGrid'" in script
    assert "CASTEP ElasticConstants finished" in script
    assert "    Task =>" not in script


def test_castep_mcp_preview_reports_resolved_dispatch() -> None:
    result = server.material_studio_castep_energy_script(
        "input.xsd",
        task="PDOS",
        cutoff_energy_ev=520,
        kpoints=(6, 6, 4),
    )

    assert result["ok"] is True
    assert result["execution_mode"] == "preview"
    assert result["executes_castep"] is False
    assert result["castep_dispatch"]["task"] == "ProjectedDensityOfStates"
    assert result["castep_dispatch"]["run_method"] == "Modules->CASTEP->Energy->Run"
    assert result["castep_dispatch"]["property_setting"] == {
        "name": "CalculateDOS",
        "value": "Partial",
    }
    assert "CalculateDOS => 'Partial'" in result["script"]


def test_castep_mcp_input_schema_exposes_only_canonical_tasks() -> None:
    schema = server.CastepEnergyInput.model_json_schema()

    assert schema["properties"]["task"]["$ref"] == "#/$defs/CastepTask"
    assert schema["$defs"]["CastepTask"]["enum"] == [task.value for task in CastepTask]
    assert "kpoints" in schema["properties"]
