"""Shared semiconductor workflow contract constants."""

from __future__ import annotations

from typing import Any


DIAMOND_NV_CENTER_VIRTUAL_TEMPLATE_ID = "diamond_nitrogen_vacancy_center"
DIAMOND_NV_CENTER_BASE_TEMPLATE_ID = "diamond_cubic"
DIAMOND_NV_CENTER_DEFAULT_SUPERCELL = (2, 2, 2)
# Compatibility alias for revisions and callers created before configurable
# diamond NV supercells were introduced.
DIAMOND_NV_CENTER_SUPERCELL = DIAMOND_NV_CENTER_DEFAULT_SUPERCELL
DIAMOND_NV_CENTER_MIN_CUBIC_REPEAT = 2
DIAMOND_NV_CENTER_MAX_CUBIC_REPEAT = 4
# Retained for revisions created before structured CASTEP charge/spin support.
DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS = (
    "unsupported_in_current_castep_schema"
)
DIAMOND_NV_CHARGE_SPIN_BINDING_REQUIRED_STATUS = (
    "requires_explicit_structured_castep_binding"
)
DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS = (
    "bound_by_structured_castep_settings_v1"
)
DIAMOND_NV_CHARGE_SPIN_API_CONTRACT = (
    "Materials Studio 20.1 CASTEP Charge/SpinTreatment/UseFormalSpin/"
    "InitialSpin/OptimizeTotalSpin"
)
DIAMOND_NV_CHARGE_SPIN_SETTING_FIELDS = (
    "total_charge",
    "spin_treatment",
    "use_formal_spin",
    "initial_spin",
    "optimize_total_spin",
)
DIAMOND_NV_CHARGE_SPIN_API_PROPERTIES = {
    "total_charge": "Charge",
    "spin_treatment": "SpinTreatment",
    "use_formal_spin": "UseFormalSpin",
    "initial_spin": "InitialSpin",
    "optimize_total_spin": "OptimizeTotalSpin",
}
DIAMOND_NV_REVIEWED_BACKEND_STATUSES = frozenset(
    {
        DIAMOND_NV_CHARGE_SPIN_BACKEND_STATUS,
        DIAMOND_NV_CHARGE_SPIN_BINDING_REQUIRED_STATUS,
        DIAMOND_NV_CHARGE_SPIN_BOUND_STATUS,
    }
)


def normalize_diamond_nv_supercell(
    matrix: tuple[int, int, int] | None,
) -> tuple[int, int, int]:
    """Return a reviewed cubic diamond NV supercell matrix."""

    selected = matrix or DIAMOND_NV_CENTER_DEFAULT_SUPERCELL
    if len(selected) != 3:
        raise ValueError("Diamond NV-center supercell must contain three repeats.")
    normalized = tuple(int(value) for value in selected)
    if len(set(normalized)) != 1:
        label = "x".join(str(value) for value in normalized)
        raise ValueError(
            "Diamond NV-center supercells must be cubic; "
            f"{label} is anisotropic."
        )
    repeat = normalized[0]
    if not (
        DIAMOND_NV_CENTER_MIN_CUBIC_REPEAT
        <= repeat
        <= DIAMOND_NV_CENTER_MAX_CUBIC_REPEAT
    ):
        raise ValueError(
            "Diamond NV-center cubic repeat must be between "
            f"{DIAMOND_NV_CENTER_MIN_CUBIC_REPEAT} and "
            f"{DIAMOND_NV_CENTER_MAX_CUBIC_REPEAT}; received {repeat}."
        )
    return normalized


def diamond_nv_expected_castep_settings(
    charge_state_label: str | None,
) -> dict[str, Any] | None:
    """Return the reviewed initial-state CASTEP settings for NV0 or NV-."""

    settings = {
        "NV0": {
            "total_charge": 0,
            "spin_treatment": "Collinear",
            "use_formal_spin": False,
            "initial_spin": 1,
            "optimize_total_spin": False,
        },
        "NV-": {
            "total_charge": -1,
            "spin_treatment": "Collinear",
            "use_formal_spin": False,
            "initial_spin": 2,
            "optimize_total_spin": False,
        },
    }.get(str(charge_state_label or ""))
    return dict(settings) if settings is not None else None


def diamond_nv_castep_binding_receipt(
    charge_state_label: str | None,
    simulation: Any,
) -> dict[str, Any]:
    """Compare one simulation object with the exact reviewed NV settings."""

    expected = diamond_nv_expected_castep_settings(charge_state_label)
    observed: dict[str, Any] = {}
    for field in DIAMOND_NV_CHARGE_SPIN_SETTING_FIELDS:
        value = getattr(simulation, field, None) if simulation is not None else None
        observed[field] = getattr(value, "value", value)
    field_matches = {
        field: observed.get(field) == expected.get(field)
        for field in DIAMOND_NV_CHARGE_SPIN_SETTING_FIELDS
    } if expected is not None else {}
    exact_match = bool(expected is not None and all(field_matches.values()))
    expected_api_settings = (
        {
            DIAMOND_NV_CHARGE_SPIN_API_PROPERTIES[field]: (
                "Yes"
                if expected[field] is True
                else "No"
                if expected[field] is False
                else expected[field]
            )
            for field in DIAMOND_NV_CHARGE_SPIN_SETTING_FIELDS
        }
        if expected is not None
        else None
    )
    observed_api_settings = {
        DIAMOND_NV_CHARGE_SPIN_API_PROPERTIES[field]: (
            "Yes"
            if observed[field] is True
            else "No"
            if observed[field] is False
            else observed[field]
        )
        for field in DIAMOND_NV_CHARGE_SPIN_SETTING_FIELDS
    }
    return {
        "schema_version": "diamond_nv_castep_charge_spin_binding_v1",
        "charge_state_label": charge_state_label,
        "charge_state_supported": expected is not None,
        "expected_settings": expected,
        "observed_settings": observed,
        "field_matches": field_matches,
        "exact_match": exact_match,
        "execution_ready": exact_match,
        "expected_api_settings": expected_api_settings,
        "observed_api_settings": observed_api_settings,
        "materials_studio_api_contract": DIAMOND_NV_CHARGE_SPIN_API_CONTRACT,
        "settings_are_initial_state_request_not_computed_result": True,
    }
