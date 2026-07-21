from __future__ import annotations

from pathlib import Path

import pytest

from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceHarness,
)
from material_studio_mcp_server.castep_acceptance.profile import (
    EXPECTED_SIMULATION_PAYLOAD,
)


def test_preview_does_not_resolve_tool_gui_or_create_files(request_factory) -> None:
    calls: list[str] = []

    def forbidden_tool_resolver():
        calls.append("tool")
        raise AssertionError("preview must not resolve a CASTEP backend")

    def forbidden_gui_resolver():
        calls.append("gui")
        raise AssertionError("preview must not resolve a GUI backend")

    request = request_factory()
    workspace = request.workspace_root
    plan = CastepAcceptanceHarness(
        tool_resolver=forbidden_tool_resolver,
        gui_backend_resolver=forbidden_gui_resolver,
        real_environment=True,
    ).run(request)

    assert calls == []
    assert not workspace.exists()
    assert plan.preview_files_runner_or_gui_touched is False
    assert plan.backend_resolution_deferred is True
    assert plan.explicit_real_opt_in_required is True
    assert plan.public_tool == "material_studio_castep_run_current"


def test_preview_payload_is_the_only_frozen_public_call(request_factory) -> None:
    plan = CastepAcceptanceHarness(real_environment=False).run(request_factory())
    payload = plan.public_tool_payload
    assert payload == {
        "project_id": "sic_3c_castep_energy_acceptance",
        "task": "Energy",
        "quality": "Medium",
        "functional": "PBE",
        "cutoff_energy_ev": 300,
        "kpoint_separation": None,
        "kpoints": (2, 2, 1),
        "properties_kpoint_separation": None,
        "band_structure_energy_max_ev": None,
        "band_structure_extra_bands": None,
        "band_structure_energy_tolerance_ev": None,
        "dos_energy_max_ev": None,
        "dos_extra_bands": None,
        "dos_energy_tolerance_ev": None,
        "dos_smearing_width_ev": None,
        "dos_integration_method": None,
        "dipole_correction": "Self-consistent",
        "open_in_gui": False,
        "take_snapshot": False,
        "export_view_audit": False,
        "views": None,
        "working_dir": str(request_factory().workspace_root.resolve()),
        "timeout_seconds": 30,
        "expected_revision": 0,
        "response_mode": "full",
    }
    assert set(EXPECTED_SIMULATION_PAYLOAD).isdisjoint(
        {"geometry_optimization", "dos", "pdos", "band_structure"}
    )


def test_preview_rejects_repository_or_existing_workspace(
    request_factory,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "already-used"
    existing.mkdir()
    with pytest.raises(ValueError, match="must not already exist"):
        CastepAcceptanceHarness(real_environment=False).run(
            request_factory(selected_workspace=existing)
        )

    repository_workspace = Path(__file__).resolve().parents[2] / "forbidden-workspace"
    with pytest.raises(ValueError, match="outside the repository"):
        CastepAcceptanceHarness(real_environment=False).run(
            request_factory(selected_workspace=repository_workspace)
        )
