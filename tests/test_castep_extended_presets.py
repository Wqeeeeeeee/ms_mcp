from __future__ import annotations

import pytest

from material_studio_mcp_server.castep_materialscript import (
    CASTEP_PREVIEW_ONLY_TASKS,
    build_castep_materialscript_plan,
    castep_structured_execution_tool,
    render_castep_run_snippet,
)
from material_studio_mcp_server.specs.castep import (
    CastepEnergySpec,
    CastepTask,
)
from material_studio_mcp_server.translators.castep_to_perl import (
    castep_calculation_preview_metadata,
    render_castep_electronic_script,
    render_castep_task_script,
)


@pytest.mark.parametrize(
    ("task", "property_settings"),
    [
        (
            CastepTask.FREQUENCY,
            (
                ("CalculatePhononDOS", "Full"),
                ("CalculatePhononDispersion", "DispersionAndDos"),
            ),
        ),
        (
            CastepTask.BAND_STRUCTURE_AND_DOS,
            (
                ("CalculateBandStructure", "DispersionAndDos"),
                ("CalculateDOS", "Full"),
            ),
        ),
        (
            CastepTask.CHARGE_DENSITY,
            (("CalculateChargeDensity", "FieldAndIsosurface"),),
        ),
        (
            CastepTask.DENSITY_DIFFERENCE,
            (("CalculateDensityDifference", "FieldAndIsosurface"),),
        ),
    ],
)
def test_extended_castep_presets_have_deterministic_property_mappings(
    task: CastepTask,
    property_settings: tuple[tuple[str, str], ...],
) -> None:
    spec = CastepEnergySpec(task=task)
    plan = build_castep_materialscript_plan(spec)
    script = render_castep_run_snippet(spec)

    assert plan.api_object == "Modules->CASTEP->Energy"
    assert plan.property_settings == property_settings
    assert plan.property_setting == property_settings[0]
    assert plan.summary()["property_settings"] == [
        {"name": name, "value": value} for name, value in property_settings
    ]
    assert plan.summary()["preview_only"] is True
    positions = [
        script.index(f"{name} => '{value}'")
        for name, value in property_settings
    ]
    assert positions == sorted(positions)
    assert task in CASTEP_PREVIEW_ONLY_TASKS


@pytest.mark.parametrize(
    "task",
    [
        CastepTask.FREQUENCY,
        CastepTask.BAND_STRUCTURE_AND_DOS,
        CastepTask.CHARGE_DENSITY,
        CastepTask.DENSITY_DIFFERENCE,
    ],
)
def test_extended_castep_presets_remain_preview_only_without_result_contract(
    task: CastepTask,
) -> None:
    spec = CastepEnergySpec(task=task)
    assert castep_structured_execution_tool(task) is None

    preview = castep_calculation_preview_metadata(
        spec,
        "structure.cif",
        project_id="preview_only",
        revision=7,
    )
    assert preview["execution_supported_by_structured_workflow"] is False
    assert preview["execution_tool"] is None
    assert preview["execution_handoff"]["execute_action"] is None
    assert (
        preview["execution_handoff"]["status"]
        == "preview_only_no_dedicated_execution_tool"
    )
    companion = render_castep_task_script(
        spec,
        "structure.cif",
        project_id="preview_only",
        revision=7,
    )
    assert "Modules->CASTEP->Energy->Run" in companion

    with pytest.raises(ValueError, match="electronic execution supports"):
        render_castep_electronic_script(
            spec,
            "input.cif",
            "result.cif",
            "report.txt",
            project_id="preview_only",
            base_revision=7,
        )


def test_band_structure_and_dos_accepts_both_reviewed_setting_families() -> None:
    spec = CastepEnergySpec(
        task="BandStructureAndDOS",
        properties_kpoint_separation=0.03,
        band_structure_energy_max_ev=10,
        band_structure_extra_bands=20,
        dos_energy_max_ev=8,
        dos_extra_bands=12,
        dos_smearing_width_ev=0.1,
    )
    settings = dict(build_castep_materialscript_plan(spec).settings)

    assert settings["BandStructureEmax"] == 10
    assert settings["BandStructureNumExtraBands"] == 20
    assert settings["DOSEmax"] == 8
    assert settings["DOSNumExtraBands"] == 12
    assert settings["DOSSmearingWidth"] == 0.1
    assert settings["CalculateBandStructure"] == "DispersionAndDos"
    assert settings["CalculateDOS"] == "Full"


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("frequencies", CastepTask.FREQUENCY),
        ("bands and dos", CastepTask.BAND_STRUCTURE_AND_DOS),
        ("charge density", CastepTask.CHARGE_DENSITY),
        ("density difference", CastepTask.DENSITY_DIFFERENCE),
    ],
)
def test_extended_castep_aliases_normalize(
    alias: str,
    expected: CastepTask,
) -> None:
    assert CastepEnergySpec(task=alias).task is expected
