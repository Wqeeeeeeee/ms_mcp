from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.domains.surface import build, match, plan
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    MatchKind,
    ModelKind,
    ModelingQuestion,
    ReferenceAccess,
    ReferenceAccessMode,
    RuntimeIssueKind,
    SemanticParameter,
    canonical_json_bytes,
    contract_digest,
)


def _codes(value) -> set[str]:
    return {issue.code for issue in value.issues}


def test_exact_match_plan_and_build_are_deterministic_and_input_immutable(
    exact_intent,
) -> None:
    matched = match(exact_intent)
    assert matched.kind is MatchKind.EXACT
    assert matched.specificity == 1000
    assert matched.reason_codes == ("exact_fixed_profile",)
    assert matched.issues == ()

    planned = plan(exact_intent, None)
    before = contract_digest(
        planned,
        contract_name="ModelingPlan",
        contract_version="1.0.0",
    )
    assert planned.build_eligible is True
    assert planned.current_revision is None
    assert len(planned.steps) == 1

    first = build(planned)
    second = build(planned)
    after = contract_digest(
        planned,
        contract_name="ModelingPlan",
        contract_version="1.0.0",
    )
    assert before == after
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first is not second
    assert first.model is not second.model
    assert first.model.basis_atoms is not second.model.basis_atoms
    with pytest.raises(ValidationError, match="frozen_instance"):
        planned.build_eligible = False


def test_missing_project_id_returns_exact_match_and_typed_question(
    intent_factory,
) -> None:
    intent = intent_factory(parameters=())
    matched = match(intent)
    planned = plan(intent, None)

    assert matched.kind is MatchKind.EXACT
    assert matched.reason_codes == ("exact_fixed_profile", "project_id_required")
    assert _codes(matched) == {"project_id_required"}
    assert matched.issues[0].kind is RuntimeIssueKind.NEEDS_USER_INPUT
    assert planned.build_eligible is False
    assert planned.steps == ()
    assert planned.questions == (
        ModelingQuestion(
            question_id="project_id_required",
            prompt="What project_id should identify the preview model?",
            parameter_name="project_id",
            choices=None,
        ),
    )
    with pytest.raises(ValueError, match="not build eligible"):
        build(planned)


@pytest.mark.parametrize(
    ("parameter", "expected_code"),
    [
        (SemanticParameter(name="surface_face", value="C"), "conflicting_fixed_parameter"),
        (SemanticParameter(name="bilayers", value=6), "conflicting_fixed_parameter"),
        (SemanticParameter(name="polytype", value="4H"), "unsupported_parameter"),
        (SemanticParameter(name="adsorbate", value="O"), "unsupported_parameter"),
    ],
)
def test_conflicting_and_unsupported_parameters_fail_closed(
    intent_factory,
    parameter: SemanticParameter,
    expected_code: str,
) -> None:
    intent = intent_factory(
        parameters=(
            SemanticParameter(name="project_id", value="sic_surface_dev"),
            SemanticParameter(name="bilayer_count", value=4),
            parameter,
        )
    )
    matched = match(intent)
    planned = plan(intent, None)

    assert matched.kind is MatchKind.NONE
    assert expected_code in matched.reason_codes
    assert expected_code in _codes(planned)
    assert planned.build_eligible is False


@pytest.mark.parametrize(
    "parameter",
    [
        SemanticParameter(name="bilayer_count", value=True),
        SemanticParameter(name="vacuum_angstrom", value=True),
        SemanticParameter(name="full_atom_extent_centered", value=1),
    ],
)
def test_boolean_and_numeric_values_are_not_interchangeable(
    intent_factory,
    parameter: SemanticParameter,
) -> None:
    intent = intent_factory(
        parameters=(
            SemanticParameter(name="project_id", value="sic_surface_dev"),
            parameter,
        )
    )
    matched = match(intent)

    assert matched.kind is MatchKind.NONE
    assert matched.reason_codes == ("conflicting_fixed_parameter",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"material": "4H-SiC"},
        {"scenario": "bulk_crystal"},
        {"operation": "create_c_face_slab"},
        {"model_kind": ModelKind.MOLECULE},
        {
            "requires_current_model": True,
            "output_kind": BuildOutputKind.SEMANTIC_PATCH,
        },
    ],
)
def test_unsupported_routing_profiles_do_not_match(intent_factory, overrides) -> None:
    intent = intent_factory(**overrides)
    assert match(intent).kind is MatchKind.NONE


def test_current_model_is_rejected_by_create_only_planner(
    exact_intent,
    current_model_state,
) -> None:
    state_before = copy.deepcopy(current_model_state.model_dump(mode="json"))
    planned = plan(exact_intent, current_model_state)

    assert planned.build_eligible is False
    assert planned.steps == ()
    assert "current_model_not_allowed" in _codes(planned)
    assert current_model_state.model_dump(mode="json") == state_before


@pytest.mark.parametrize(
    ("intent_overrides", "expected_code"),
    [
        (
            {"semantic_requirements": ("Also reconstruct the surface.",)},
            "unsupported_semantic_requirement",
        ),
        (
            {"declared_assumptions": ("Assume a relaxed surface.",)},
            "unsupported_declared_assumption",
        ),
        (
            {
                "reference_access": ReferenceAccess(
                    mode=ReferenceAccessMode.METADATA_ONLY,
                    source_ids=("cod-1010995",),
                    raw_structure_access=False,
                    final_coordinate_access=False,
                    hidden_holdout_access=False,
                )
            },
            "unsupported_reference_access",
        ),
        (
            {
                "reference_access": ReferenceAccess(
                    mode=ReferenceAccessMode.TASK_ONLY,
                    source_ids=("different-source",),
                    raw_structure_access=False,
                    final_coordinate_access=False,
                    hidden_holdout_access=False,
                )
            },
            "unsupported_reference_access",
        ),
    ],
)
def test_extra_semantics_assumptions_and_wrong_reference_access_fail_closed(
    intent_factory,
    intent_overrides,
    expected_code: str,
) -> None:
    intent = intent_factory(**intent_overrides)
    matched = match(intent)
    planned = plan(intent, None)

    assert matched.kind is MatchKind.NONE
    assert expected_code in matched.reason_codes
    assert expected_code in _codes(planned)
    assert planned.build_eligible is False


def test_build_rejects_tampered_canonical_plan(exact_intent) -> None:
    planned = plan(exact_intent, None)
    step = planned.steps[0]
    parameters = list(step.parameters)
    index = next(
        i for i, parameter in enumerate(parameters) if parameter.name == "bilayer_count"
    )
    parameters[index] = SemanticParameter(name="bilayer_count", value=5)
    tampered_step = step.model_copy(update={"parameters": tuple(parameters)})
    tampered = planned.model_copy(update={"steps": (tampered_step,)})

    with pytest.raises(ValueError, match="does not match the fixed profile"):
        build(tampered)
