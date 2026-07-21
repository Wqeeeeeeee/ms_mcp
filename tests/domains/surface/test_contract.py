from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from material_studio_mcp_server.domains.surface import PLUGIN, PLUGIN_MANIFEST
from material_studio_mcp_server.orchestration import CapabilityRegistry, RuntimeRouter
from material_studio_mcp_server.runtime import (
    RuntimeOutcome,
    SemanticParameter,
)


ROOT = Path(__file__).resolve().parents[3]


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        ((PLUGIN_MANIFEST, PLUGIN),),
        dependency_resolver=lambda dependency: dependency.required,
    )


def test_manifest_satisfies_frozen_json_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "domain_plugin.schema.json").read_text("utf-8"))
    payload = PLUGIN_MANIFEST.model_dump(mode="json", by_alias=True)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert errors == []


def test_manifest_and_plugin_register_in_capability_registry() -> None:
    registry = _registry()

    assert registry.plugin_ids == (PLUGIN.plugin_id,)
    assert registry.get_manifest(PLUGIN.plugin_id) == PLUGIN_MANIFEST
    assert registry.unresolved_optional_dependencies(PLUGIN.plugin_id) == ()
    assert all(
        callable(getattr(PLUGIN, stage))
        for stage in ("match", "plan", "build", "validate")
    )


def test_router_prefilter_accepts_declared_fixed_output_size(intent_factory) -> None:
    intent = intent_factory(
        parameters=(
            SemanticParameter(name="project_id", value="sic_surface_dev"),
            SemanticParameter(name="atom_count", value=80),
        )
    )
    prefilter = RuntimeRouter(_registry()).prefilter(intent)

    assert prefilter.issues == ()
    assert prefilter.eligible_plugin_ids == (PLUGIN.plugin_id,)


def test_router_selects_exact_create_intent_end_to_end(intent_factory) -> None:
    intent = intent_factory(
        parameters=(
            SemanticParameter(name="project_id", value="sic_surface_dev"),
            SemanticParameter(name="atom_count", value=80),
        )
    )
    decision = RuntimeRouter(_registry()).route(intent)

    assert decision.outcome is RuntimeOutcome.COMPLETED
    assert decision.selected_plugin_id == PLUGIN.plugin_id
    assert decision.no_match is None
    planning = RuntimeRouter(_registry()).plan_selected(decision, intent)
    assert planning.outcome is RuntimeOutcome.COMPLETED
    assert planning.plan is not None
    candidate = PLUGIN.build(planning.plan)
    assert len(candidate.model.basis_atoms) == 80
    assert PLUGIN.validate(candidate).preview_eligible is True


def test_router_rejects_boolean_and_wrong_atom_counts(intent_factory) -> None:
    router = RuntimeRouter(_registry())
    boolean_intent = intent_factory(
        parameters=(
            SemanticParameter(name="project_id", value="sic_surface_dev"),
            SemanticParameter(name="atom_count", value=True),
        )
    )
    wrong_count_intent = intent_factory(
        parameters=(
            SemanticParameter(name="project_id", value="sic_surface_dev"),
            SemanticParameter(name="atom_count", value=79),
        )
    )

    boolean_decision = router.route(boolean_intent)
    wrong_count_decision = router.route(wrong_count_intent)
    assert boolean_decision.outcome is RuntimeOutcome.BLOCKED
    assert boolean_decision.selected_plugin_id is None
    assert {issue.code for issue in boolean_decision.issues} == {
        "router.atom_count_type_invalid"
    }
    assert wrong_count_decision.outcome is RuntimeOutcome.BLOCKED
    assert wrong_count_decision.selected_plugin_id is None
    assert wrong_count_decision.no_match is not None
    assert "router.atom_count_below_minimum" in wrong_count_decision.no_match.reason_codes
