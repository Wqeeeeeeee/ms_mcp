from __future__ import annotations

import ast
import builtins
import inspect
import json
import os
from pathlib import Path
import socket

import pytest

from material_studio_mcp_server.orchestration.capability_registry import (
    CapabilityRegistry,
)
from material_studio_mcp_server.orchestration.router import (
    FallbackRequest,
    ForcedSelectionRequest,
)
from material_studio_mcp_server.orchestration.shadow import (
    ShadowComparisonObservations,
    ShadowRouter,
)
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    DecisionDifference,
    DomainPluginManifest,
    MatchKind,
    MatchResult,
    MigrationMode,
    ModelKind,
    ModelState,
    ModelingIntent,
    ModelingPlan,
    PlanStep,
    RUNTIME_CONTRACT_VERSION,
    ReferenceAccess,
    ReferenceAccessMode,
    ResolvedAssumption,
    RuntimeIssue,
    RuntimeIssueKind,
    RuntimeOutcome,
    StageName,
    StageStatus,
    canonical_json,
    contract_digest,
)
from material_studio_mcp_server.specs import (
    BasisAtomSpec,
    CrystalSpec,
    LatticeSpec,
    ModelSpec,
    ModelType,
)


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = ROOT / "src" / "material_studio_mcp_server" / "orchestration"


def _stage(name: str, inputs: list[str], outputs: list[str]) -> dict:
    return {
        "callable": f"shadow_fake.{name}",
        "input_contracts": inputs,
        "output_contracts": outputs,
        "deterministic": True,
        "filesystem_side_effects": False,
        "process_side_effects": False,
        "network_access": False,
        "gui_access": False,
    }


def _manifest(
    plugin_id: str = "sic_shadow",
    *,
    priority: int = 0,
) -> DomainPluginManifest:
    payload = {
        "plugin_id": plugin_id,
        "contract_version": "1.0.0",
        "implementation_version": "1.2.0",
        "description": "Pure shadow fake.",
        "capabilities": {
            "materials": ["3C-SiC"],
            "scenarios": ["surface_slab"],
            "operations": ["create_surface_slab"],
        },
        "limits": {
            "min_atoms": 0,
            "max_atoms": None,
            "supported_periodicity_dimensions": [3],
            "supported_model_kinds": ["crystal"],
            "requires_current_model": False,
            "supports_create": True,
            "supports_patch": False,
            "supports_calculation_plan": False,
            "unsupported_capabilities": [],
        },
        "routing": {
            "priority": priority,
            "ambiguity_policy": "fail_closed",
            "forced_selection_requires_capability_match": True,
        },
        "reference_policy": {
            "allowed_access_modes": ["none"],
            "hidden_holdout_access": False,
            "final_reference_coordinate_access": False,
        },
        "runtime_behavior": {
            "deterministic": True,
            "preview_first": True,
            "mutates_input_model": False,
            "owns_revision_state": False,
            "executes_backend_directly": False,
            "registers_public_mcp_tools": False,
            "owns_gui_session": False,
            "network_access_during_match_plan_build_validate": False,
        },
        "contracts": {
            "match": _stage("match", ["ModelingIntent"], ["MatchResult"]),
            "plan": _stage(
                "plan", ["ModelingIntent", "ModelState"], ["ModelingPlan"]
            ),
            "build": _stage(
                "build", ["ModelingPlan"], ["ModelSpec", "SemanticPatch"]
            ),
            "validate": _stage(
                "validate", ["ModelSpec"], ["DomainValidationReport"]
            ),
        },
        "dependencies": [],
    }
    return DomainPluginManifest.model_validate_json(json.dumps(payload))


def _intent(*, requires_current_model: bool = False) -> ModelingIntent:
    return ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="request.shadow-001",
        material="3C-SiC",
        scenario="surface_slab",
        operation="create_surface_slab",
        model_kind=ModelKind.CRYSTAL,
        requires_current_model=requires_current_model,
        output_kind=BuildOutputKind.MODEL_SPEC,
        parameters=(),
        semantic_requirements=(),
        declared_assumptions=(),
        reference_access=ReferenceAccess(
            mode=ReferenceAccessMode.NONE,
            source_ids=(),
            raw_structure_access=False,
            final_coordinate_access=False,
            hidden_holdout_access=False,
        ),
    )


def _current_state() -> ModelState:
    spec = ModelSpec(
        project_id="shadow_project",
        revision=3,
        model_type=ModelType.CRYSTAL,
        model=CrystalSpec(
            name="3C-SiC",
            lattice=LatticeSpec(
                a=4.36,
                b=4.36,
                c=4.36,
                alpha=90.0,
                beta=90.0,
                gamma=90.0,
            ),
            basis_atoms=[
                BasisAtomSpec(id="Si1", element="Si", fractional=(0.0, 0.0, 0.0)),
                BasisAtomSpec(id="C1", element="C", fractional=(0.25, 0.25, 0.25)),
            ],
        ),
    )
    return ModelState.from_model_spec(spec)


def _legacy_digest():
    return contract_digest(
        {"legacy_path": "surface_slab", "decision": "preview"},
        contract_name="LegacyDecision",
        contract_version="1.0.0",
    )


class ShadowPlugin:
    def __init__(
        self,
        manifest: DomainPluginManifest,
        *,
        match_kind: MatchKind = MatchKind.EXACT,
        match_issues: tuple[RuntimeIssue, ...] = (),
        plan_issues: tuple[RuntimeIssue, ...] = (),
    ) -> None:
        self.plugin_id = manifest.plugin_id
        self.contract_version = manifest.contract_version
        self.implementation_version = manifest.implementation_version
        self.match_kind = match_kind
        self.match_issues = match_issues
        self.plan_issues = plan_issues
        self.match_calls = 0
        self.plan_calls = 0
        self.build_calls = 0
        self.validate_calls = 0
        self.raise_match = False
        self.raise_plan = False
        self.mutate_plan_input = False

    def match(self, intent: ModelingIntent) -> MatchResult:
        self.match_calls += 1
        if self.raise_match:
            raise RuntimeError("private match exception")
        return MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id=self.plugin_id,
            kind=self.match_kind,
            specificity=100 if self.match_kind is not MatchKind.NONE else 0,
            reason_codes=(f"match.{self.match_kind.value}",),
            issues=self.match_issues,
        )

    def plan(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None,
    ) -> ModelingPlan:
        self.plan_calls += 1
        if self.mutate_plan_input:
            object.__setattr__(intent, "operation", "mutated")
        if self.raise_plan:
            raise RuntimeError("private plan exception")
        current_revision = None
        if current_state is not None:
            from material_studio_mcp_server.runtime import RevisionIdentity

            current_revision = RevisionIdentity(
                project_id=current_state.project_id,
                revision=current_state.revision,
                model_spec_digest=current_state.model_spec_digest,
            )
        build_eligible = not any(issue.is_blocking for issue in self.plan_issues)
        return ModelingPlan(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id=self.plugin_id,
            plugin_contract_version=self.contract_version,
            plugin_implementation_version=self.implementation_version,
            normalized_intent_digest=contract_digest(
                intent,
                contract_name="ModelingIntent",
                contract_version=RUNTIME_CONTRACT_VERSION,
            ),
            current_revision=current_revision,
            output_kind=intent.output_kind,
            steps=(
                PlanStep(
                    step_id="step.shadow",
                    operation="surface.plan",
                    parameters=(),
                ),
            ),
            assumptions=(
                ResolvedAssumption(
                    code="assumption.shadow",
                    statement="Use deterministic fake settings.",
                    source="declared_default",
                ),
            ),
            questions=(),
            issues=self.plan_issues,
            forced_selection=None,
            fallback=None,
            build_eligible=build_eligible,
        )

    def build(self, plan):
        self.build_calls += 1
        raise AssertionError("shadow must not build")

    def validate(self, model):
        self.validate_calls += 1
        raise AssertionError("shadow must not validate")


def _evaluator(
    plugin: ShadowPlugin | None = None,
    manifest: DomainPluginManifest | None = None,
) -> tuple[ShadowRouter, ShadowPlugin]:
    actual_manifest = manifest or _manifest()
    actual_plugin = plugin or ShadowPlugin(actual_manifest)
    return (
        ShadowRouter(CapabilityRegistry(((actual_manifest, actual_plugin),))),
        actual_plugin,
    )


class ExplodingRegistry:
    def __init__(self) -> None:
        self.access_count = 0

    def _routing_entries(self):
        self.access_count += 1
        raise AssertionError("registry accessed")

    def _entry_for(self, plugin_id):
        self.access_count += 1
        raise AssertionError("registry accessed")


def test_off_mode_short_circuits_before_registry_and_plugin_access() -> None:
    registry = ExplodingRegistry()
    receipt = ShadowRouter(registry).evaluate(
        _intent(),
        _legacy_digest(),
        migration_mode=MigrationMode.OFF,
        forced_selection=ForcedSelectionRequest("sic_shadow", "Ignored in off mode."),
    )
    assert registry.access_count == 0
    assert receipt.migration_mode is MigrationMode.OFF
    assert receipt.outcome is RuntimeOutcome.COMPLETED
    assert receipt.authoritative_path == "legacy"
    assert receipt.selected_plugin_id is None
    assert receipt.stage_receipts == ()
    assert receipt.plan_digest is None
    assert receipt.emitted_artifact is None
    assert receipt.ambiguity is None
    assert receipt.no_match is None
    assert receipt.forced_selection is None
    assert receipt.fallback is None
    assert receipt.shadow_comparison is None


def test_off_mode_enforces_state_relation_without_registry_access() -> None:
    registry = ExplodingRegistry()
    receipt = ShadowRouter(registry).evaluate(
        _intent(requires_current_model=True),
        _legacy_digest(),
        migration_mode=MigrationMode.OFF,
    )
    assert registry.access_count == 0
    assert receipt.outcome is RuntimeOutcome.BLOCKED
    assert receipt.stage_receipts == ()
    assert tuple(issue.code for issue in receipt.issues) == (
        "router.current_model_required",
    )


def test_active_mode_is_explicitly_unavailable_before_registry_access() -> None:
    registry = ExplodingRegistry()
    receipt = ShadowRouter(registry).evaluate(
        _intent(),
        _legacy_digest(),
        migration_mode=MigrationMode.ACTIVE,
    )
    assert registry.access_count == 0
    assert receipt.migration_mode is MigrationMode.ACTIVE
    assert receipt.outcome is RuntimeOutcome.BLOCKED
    assert receipt.authoritative_path == "plugin"
    assert receipt.no_match.reason_codes == ("router.active_mode_unavailable",)
    assert receipt.issues[0].kind is RuntimeIssueKind.UNSUPPORTED
    assert receipt.stage_receipts == ()


def test_completed_shadow_calls_match_and_plan_only_and_binds_comparison() -> None:
    evaluator, plugin = _evaluator()
    legacy = _legacy_digest()
    receipt = evaluator.evaluate_shadow(_intent(), legacy)
    assert receipt.outcome is RuntimeOutcome.COMPLETED
    assert receipt.authoritative_path == "legacy"
    assert tuple(stage.stage for stage in receipt.stage_receipts) == (
        StageName.MATCH,
        StageName.PLAN,
    )
    assert all(stage.status is StageStatus.COMPLETED for stage in receipt.stage_receipts)
    assert plugin.match_calls == 1
    assert plugin.plan_calls == 1
    assert plugin.build_calls == 0
    assert plugin.validate_calls == 0
    comparison = receipt.shadow_comparison
    assert comparison.authoritative_legacy_decision_digest == legacy
    assert comparison.normalized_intent_digest == receipt.normalized_intent_digest
    assert comparison.shadow_plan_digest == receipt.plan_digest
    assert comparison.equivalent is True
    assert comparison.differences == ()


def test_shadow_differences_are_typed_sorted_and_drive_equivalence() -> None:
    evaluator, _ = _evaluator()
    differences = (
        DecisionDifference(
            code="shadow.zeta",
            field_path="$.zeta",
            legacy_value_sha256="a" * 64,
            shadow_value_sha256="b" * 64,
            summary="Zeta differs.",
        ),
        DecisionDifference(
            code="shadow.alpha",
            field_path="$.alpha",
            legacy_value_sha256=None,
            shadow_value_sha256="c" * 64,
            summary="Alpha differs.",
        ),
    )
    receipt = evaluator.evaluate_shadow(
        _intent(),
        _legacy_digest(),
        comparison_observations=ShadowComparisonObservations(differences),
    )
    assert receipt.shadow_comparison.equivalent is False
    assert tuple(item.code for item in receipt.shadow_comparison.differences) == (
        "shadow.alpha",
        "shadow.zeta",
    )
    assert "shadow_plan_digest" not in inspect.signature(
        evaluator.evaluate_shadow
    ).parameters


def test_duplicate_shadow_differences_fail_before_plugin_invocation() -> None:
    evaluator, plugin = _evaluator()
    difference = DecisionDifference(
        code="shadow.duplicate",
        field_path="$.plan",
        legacy_value_sha256=None,
        shadow_value_sha256="a" * 64,
        summary="Duplicate difference.",
    )

    with pytest.raises(ValueError, match="duplicate"):
        evaluator.evaluate_shadow(
            _intent(),
            _legacy_digest(),
            differences=(difference, difference),
        )

    assert plugin.match_calls == 0
    assert plugin.plan_calls == 0


def test_no_match_runtime_receipt_preserves_typed_match_issues() -> None:
    issues = (
        RuntimeIssue(
            kind=RuntimeIssueKind.UNSUPPORTED,
            code="plugin.reconstruction_unsupported",
            message="Reconstruction is unsupported.",
            field_path="$.parameters.reconstruction",
        ),
        RuntimeIssue(
            kind=RuntimeIssueKind.INVALID_INPUT,
            code="plugin.orientation_invalid",
            message="Orientation is invalid.",
            field_path="$.parameters.orientation",
        ),
        RuntimeIssue(
            kind=RuntimeIssueKind.NEEDS_USER_INPUT,
            code="plugin.termination_required",
            message="Termination is required.",
            field_path="$.parameters.termination",
        ),
    )
    manifest = _manifest()
    plugin = ShadowPlugin(
        manifest,
        match_kind=MatchKind.NONE,
        match_issues=issues,
    )
    evaluator, _ = _evaluator(plugin, manifest)

    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())

    assert receipt.outcome is RuntimeOutcome.BLOCKED
    assert receipt.no_match is not None
    assert tuple(issue.code for issue in receipt.issues) == (
        "plugin.orientation_invalid",
        "plugin.reconstruction_unsupported",
        "plugin.termination_required",
    )


def test_ambiguity_runtime_receipt_preserves_typed_match_issues() -> None:
    issue = RuntimeIssue(
        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
        code="plugin.orientation_required",
        message="Orientation is required.",
        field_path="$.parameters.orientation",
    )
    alpha_manifest = _manifest("sic_alpha", priority=7)
    beta_manifest = _manifest("sic_beta", priority=7)
    alpha = ShadowPlugin(alpha_manifest, match_issues=(issue,))
    beta = ShadowPlugin(beta_manifest, match_issues=(issue,))
    evaluator = ShadowRouter(
        CapabilityRegistry(
            ((beta_manifest, beta), (alpha_manifest, alpha))
        )
    )

    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())

    assert receipt.outcome is RuntimeOutcome.BLOCKED
    assert receipt.ambiguity is not None
    assert tuple(issue.code for issue in receipt.issues) == (
        "plugin.orientation_required",
    )


def test_repeated_equal_shadow_evaluations_are_byte_identical() -> None:
    evaluator, plugin = _evaluator()
    intent = _intent()
    legacy = _legacy_digest()
    first = evaluator.evaluate_shadow(intent, legacy)
    second = evaluator.evaluate_shadow(intent, legacy)
    assert canonical_json(first) == canonical_json(second)
    assert first.receipt_id == second.receipt_id
    assert plugin.match_calls == 2
    assert plugin.plan_calls == 2


def test_receipt_id_binds_legacy_and_comparison_observations() -> None:
    evaluator, _ = _evaluator()
    intent = _intent()
    first = evaluator.evaluate_shadow(intent, _legacy_digest())
    other_legacy = contract_digest(
        {"legacy_path": "different"},
        contract_name="LegacyDecision",
        contract_version="1.0.0",
    )
    second = evaluator.evaluate_shadow(intent, other_legacy)
    difference = DecisionDifference(
        code="shadow.plan_difference",
        field_path="$.plan",
        legacy_value_sha256=None,
        shadow_value_sha256=None,
        summary="Plan differs.",
    )
    third = evaluator.evaluate_shadow(intent, _legacy_digest(), differences=(difference,))
    assert len({first.receipt_id, second.receipt_id, third.receipt_id}) == 3


def test_blocking_match_stops_before_plan_with_valid_blocked_receipt() -> None:
    issue = RuntimeIssue(
        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
        code="plugin.orientation_required",
        message="Orientation is required.",
        field_path="$.parameters.orientation",
    )
    manifest = _manifest()
    plugin = ShadowPlugin(manifest, match_issues=(issue,))
    evaluator, _ = _evaluator(plugin, manifest)
    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())
    assert receipt.outcome is RuntimeOutcome.BLOCKED
    assert tuple(stage.stage for stage in receipt.stage_receipts) == (StageName.MATCH,)
    assert plugin.match_calls == 1
    assert plugin.plan_calls == 0
    assert receipt.shadow_comparison is None


def test_blocking_plan_stops_with_match_plan_prefix_and_no_comparison() -> None:
    issue = RuntimeIssue(
        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
        code="plugin.termination_required",
        message="Termination is required.",
        field_path="$.parameters.termination",
    )
    manifest = _manifest()
    plugin = ShadowPlugin(manifest, plan_issues=(issue,))
    evaluator, _ = _evaluator(plugin, manifest)
    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())
    assert receipt.outcome is RuntimeOutcome.BLOCKED
    assert tuple(stage.stage for stage in receipt.stage_receipts) == (
        StageName.MATCH,
        StageName.PLAN,
    )
    assert receipt.plan_digest is not None
    assert receipt.shadow_comparison is None
    assert plugin.build_calls == 0


@pytest.mark.parametrize("stage", ["match", "plan"])
def test_plugin_exception_produces_failed_terminal_stage(stage: str) -> None:
    manifest = _manifest()
    plugin = ShadowPlugin(manifest)
    if stage == "match":
        plugin.raise_match = True
    else:
        plugin.raise_plan = True
    evaluator, _ = _evaluator(plugin, manifest)
    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())
    assert receipt.outcome is RuntimeOutcome.FAILED
    assert receipt.stage_receipts[-1].stage.value == stage
    assert receipt.stage_receipts[-1].status is StageStatus.FAILED
    assert any(
        issue.kind is RuntimeIssueKind.INTERNAL_ERROR
        for issue in receipt.stage_receipts[-1].issues
    )
    assert receipt.shadow_comparison is None


def test_plan_internal_error_returns_failed_runtime_receipt_without_digest() -> None:
    issue = RuntimeIssue(
        kind=RuntimeIssueKind.INTERNAL_ERROR,
        code="plugin.private_failure",
        message="Private plugin details.",
        field_path="$.private",
    )
    manifest = _manifest()
    plugin = ShadowPlugin(manifest, plan_issues=(issue,))
    evaluator, _ = _evaluator(plugin, manifest)

    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())

    assert receipt.outcome is RuntimeOutcome.FAILED
    assert receipt.plan_digest is None
    assert receipt.stage_receipts[-1].stage is StageName.PLAN
    assert receipt.stage_receipts[-1].status is StageStatus.FAILED
    assert tuple(issue.code for issue in receipt.stage_receipts[-1].issues) == (
        "router.plan_internal_error",
    )


def test_observed_plan_mutation_fails_and_stops_pipeline() -> None:
    manifest = _manifest()
    plugin = ShadowPlugin(manifest)
    plugin.mutate_plan_input = True
    evaluator, _ = _evaluator(plugin, manifest)
    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())
    assert receipt.outcome is RuntimeOutcome.FAILED
    assert receipt.stage_receipts[-1].side_effects.input_mutated is True
    assert plugin.build_calls == 0
    assert plugin.validate_calls == 0


def test_current_revision_is_repeated_in_plan_receipt_and_comparison() -> None:
    evaluator, _ = _evaluator()
    state = _current_state()
    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest(), state)
    assert receipt.outcome is RuntimeOutcome.COMPLETED
    assert receipt.current_revision is not None
    assert receipt.current_revision.project_id == state.project_id
    assert receipt.current_revision.revision == state.revision
    assert receipt.shadow_comparison.current_revision == receipt.current_revision
    plan_stage = receipt.stage_receipts[1]
    assert tuple(digest.contract_name for digest in plan_stage.input_digests) == (
        "ModelingIntent",
        "ModelState",
    )


def test_forced_and_fallback_evidence_are_preserved_in_shadow_receipts() -> None:
    evaluator, _ = _evaluator()
    forced = evaluator.evaluate_shadow(
        _intent(),
        _legacy_digest(),
        forced_selection=ForcedSelectionRequest("sic_shadow", "Reviewed cohort."),
    )
    assert forced.outcome is RuntimeOutcome.COMPLETED
    assert forced.forced_selection.capability_match is True

    fallback = evaluator.evaluate_shadow(
        _intent(),
        _legacy_digest(),
        fallback=FallbackRequest(
            from_plugin_id="sic_source",
            to_plugin_id="sic_shadow",
            reason_code="fallback.reviewed",
        ),
    )
    assert fallback.outcome is RuntimeOutcome.COMPLETED
    assert fallback.fallback.target_independently_matched is True


def test_reference_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    source_files = tuple(
        ORCHESTRATION / name
        for name in ("capability_registry.py", "router.py", "shadow.py")
    )
    trees = tuple(ast.parse(path.read_text(encoding="utf-8")) for path in source_files)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules = {
        (node.module or "")
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported_roots.intersection(
        {"os", "pathlib", "socket", "subprocess", "requests", "urllib"}
    )
    imported_leaf_names = {
        component
        for module in imported_modules
        for component in module.split(".")
    }
    assert not imported_leaf_names.intersection(
        {
            "reference_adapters",
            "natural_language",
            "server",
            "runner",
            "translators",
            "gui",
            "gui_uia",
        }
    )

    evaluate_parameters = inspect.signature(ShadowRouter.evaluate).parameters
    assert not {
        "raw_request",
        "raw_coordinates",
        "final_coordinates",
        "reference_structure",
        "hidden_holdout",
        "execution_mode",
    }.intersection(evaluate_parameters)

    def forbidden(*args, **kwargs):
        raise AssertionError("external state accessed")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    evaluator, plugin = _evaluator()
    receipt = evaluator.evaluate_shadow(_intent(), _legacy_digest())
    assert receipt.outcome is RuntimeOutcome.COMPLETED
    assert plugin.match_calls == 1
    assert plugin.plan_calls == 1
    assert all(stage.side_effects.is_pure for stage in receipt.stage_receipts)
