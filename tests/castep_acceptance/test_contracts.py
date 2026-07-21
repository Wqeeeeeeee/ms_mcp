from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceRequest,
    FixedCastepProfile,
)
from material_studio_mcp_server.castep_acceptance.contracts import (
    ACCEPTANCE_PROFILE,
    REAL_CASTEP_OPT_IN,
)
from material_studio_mcp_server.castep_acceptance.profile import (
    EXPECTED_SIMULATION_PAYLOAD,
    build_fixed_candidate,
    effective_settings_are_exact,
)

from ._helpers import synthetic_verification


def test_fixed_profile_is_exact_and_immutable() -> None:
    profile = FixedCastepProfile()
    assert profile.model_dump(mode="json") == {
        "profile": ACCEPTANCE_PROFILE,
        "source_plugin_id": "sic_3c_001_si_face_surface",
        "atom_count": 80,
        "task": "Energy",
        "functional": "PBE",
        "quality": "Medium",
        "cutoff_energy_ev": 300,
        "kpoints": [2, 2, 1],
        "dipole_correction": "Self-consistent",
        "open_in_gui": False,
        "take_snapshot": False,
        "export_view_audit": False,
        "response_mode": "full",
    }
    with pytest.raises(ValidationError):
        FixedCastepProfile(quality="Fine")
    with pytest.raises(ValidationError):
        FixedCastepProfile(extra_setting=True)


def test_exact_simulation_contract_rejects_every_profile_drift() -> None:
    assert effective_settings_are_exact(dict(EXPECTED_SIMULATION_PAYLOAD))
    for field, replacement in (
        ("task", "GeometryOptimization"),
        ("functional", "LDA"),
        ("quality", "Fine"),
        ("cutoff_energy_ev", 301),
        ("kpoints", [3, 3, 1]),
        ("dipole_correction", "None"),
        ("dos_energy_max_ev", 10.0),
    ):
        changed = dict(EXPECTED_SIMULATION_PAYLOAD)
        changed[field] = replacement
        assert effective_settings_are_exact(changed) is False
    widened = {**EXPECTED_SIMULATION_PAYLOAD, "new_setting": None}
    assert effective_settings_are_exact(widened) is False


def test_request_requires_literal_opt_in_and_preview_digest(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="literal --run-real-castep"):
        CastepAcceptanceRequest(
            request_id="missing-real-opt-in",
            workspace_root=tmp_path / "workspace",
            execution_mode="execute",
            expected_plan_sha256="a" * 64,
        )
    request = CastepAcceptanceRequest(
        request_id="authorized-real-run",
        workspace_root=tmp_path / "workspace",
        execution_mode="execute",
        expected_plan_sha256="a" * 64,
        real_opt_in=REAL_CASTEP_OPT_IN,
    )
    assert request.real_opt_in == "--run-real-castep"


def test_surface_candidate_is_the_existing_exact_80_atom_profile() -> None:
    candidate = build_fixed_candidate("sic_3c_castep_energy_acceptance")
    composition: dict[str, int] = {}
    for atom in candidate.model.basis_atoms:
        composition[atom.element] = composition.get(atom.element, 0) + 1
    assert candidate.revision == 0
    assert candidate.simulation is None
    assert len(candidate.model.basis_atoms) == 80
    assert composition == {"C": 32, "H": 16, "Si": 32}
    assert candidate.metadata["surface"]["face"] == "Si"
    assert candidate.metadata["surface"]["bilayer_count"] == 4
    assert candidate.metadata["surface"]["vacuum_angstrom"] == 15.0


def test_verification_status_contract_distinguishes_offline_from_real() -> None:
    assert synthetic_verification(real=False).status == "NOT_RUN"
    assert synthetic_verification(real=True).status == "PASS"
    with pytest.raises(ValidationError, match="PASS requires real execution"):
        payload = synthetic_verification(real=False).model_dump()
        payload["status"] = "PASS"
        synthetic_verification(real=False).__class__.model_validate(payload)
