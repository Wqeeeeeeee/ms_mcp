from __future__ import annotations

import json

import pytest

from material_studio_mcp_server.orchestration import capability_registry as registry_module
from material_studio_mcp_server.orchestration.router import (
    FallbackRequest,
    ForcedSelectionRequest,
    RuntimeRouter,
    empty_side_effect_receipt,
)
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    DomainPluginManifest,
    MatchKind,
    MatchResult,
    ModelKind,
    ModelState,
    ModelingIntent,
    ModelingPlan,
    ModelingQuestion,
    PlanStep,
    RUNTIME_CONTRACT_VERSION,
    ReferenceAccess,
    ReferenceAccessMode,
    ResolvedAssumption,
    RuntimeIssue,
    RuntimeIssueKind,
    RuntimeOutcome,
    SemanticParameter,
    SideEffectReceipt,
    StageStatus,
    contract_digest,
)
from material_studio_mcp_server.specs import (
    AtomSpec,
    BasisAtomSpec,
    CrystalSpec,
    LatticeSpec,
    ModelSpec,
    ModelType,
    MoleculeSpec,
)


def _stage(name: str, inputs: list[str], outputs: list[str]) -> dict:
    return {
        "callable": f"fake.{name}",
        "input_contracts": inputs,
        "output_contracts": outputs,
        "deterministic": True,
        "filesystem_side_effects": False,
        "process_side_effects": False,
        "network_access": False,
        "gui_access": False,
    }


def _manifest(
    plugin_id: str = "sic_surface",
    *,
    material: str = "3C-SiC",
    scenario: str = "surface_slab",
    operation: str = "create_surface_slab",
    priority: int = 0,
    min_atoms: int = 0,
    max_atoms: int | None = None,
    periodicity: tuple[int, ...] = (3,),
    model_kinds: tuple[str, ...] = ("crystal",),
    requires_current_model: bool = False,
    supports_create: bool = True,
    supports_patch: bool = False,
    supports_calculation_plan: bool = False,
    unsupported_capabilities: tuple[str, ...] = (),
    access_modes: tuple[str, ...] = ("none",),
) -> DomainPluginManifest:
    payload = {
        "plugin_id": plugin_id,
        "contract_version": "1.0.0",
        "implementation_version": "1.2.0",
        "description": "Pure fake surface plugin.",
        "capabilities": {
            "materials": [material],
            "scenarios": [scenario],
            "operations": [operation],
        },
        "limits": {
            "min_atoms": min_atoms,
            "max_atoms": max_atoms,
            "supported_periodicity_dimensions": list(periodicity),
            "supported_model_kinds": list(model_kinds),
            "requires_current_model": requires_current_model,
            "supports_create": supports_create,
            "supports_patch": supports_patch,
            "supports_calculation_plan": supports_calculation_plan,
            "unsupported_capabilities": list(unsupported_capabilities),
        },
        "routing": {
            "priority": priority,
            "ambiguity_policy": "fail_closed",
            "forced_selection_requires_capability_match": True,
        },
        "reference_policy": {
            "allowed_access_modes": list(access_modes),
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


def _reference_access(
    mode: ReferenceAccessMode = ReferenceAccessMode.NONE,
) -> ReferenceAccess:
    return ReferenceAccess(
        mode=mode,
        source_ids=() if mode is ReferenceAccessMode.NONE else ("source.task",),
        raw_structure_access=False,
        final_coordinate_access=False,
        hidden_holdout_access=False,
    )


def _intent(
    *,
    material: str = "3C-SiC",
    scenario: str = "surface_slab",
    operation: str = "create_surface_slab",
    model_kind: ModelKind = ModelKind.CRYSTAL,
    requires_current_model: bool = False,
    output_kind: BuildOutputKind = BuildOutputKind.MODEL_SPEC,
    parameters: tuple[SemanticParameter, ...] = (),
    semantic_requirements: tuple[str, ...] = (),
    reference_mode: ReferenceAccessMode = ReferenceAccessMode.NONE,
) -> ModelingIntent:
    return ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="request.surface-001",
        material=material,
        scenario=scenario,
        operation=operation,
        model_kind=model_kind,
        requires_current_model=requires_current_model,
        output_kind=output_kind,
        parameters=parameters,
        semantic_requirements=semantic_requirements,
        declared_assumptions=(),
        reference_access=_reference_access(reference_mode),
    )


def _crystal_state(atom_count: int = 2) -> ModelState:
    atoms = [
        BasisAtomSpec(
            id=f"Si{index + 1}",
            element="Si",
            fractional=(index / max(atom_count, 1), 0.0, 0.0),
        )
        for index in range(atom_count)
    ]
    spec = ModelSpec(
        project_id="runtime_project",
        revision=1,
        model_type=ModelType.CRYSTAL,
        model=CrystalSpec(
            name="3C-SiC test",
            lattice=LatticeSpec(
                a=4.3,
                b=4.3,
                c=4.3,
                alpha=90.0,
                beta=90.0,
                gamma=90.0,
            ),
            basis_atoms=atoms,
        ),
    )
    return ModelState.from_model_spec(spec)


def _molecule_state(atom_count: int = 2) -> ModelState:
    atoms = [
        AtomSpec(
            id=f"H{index + 1}",
            element="H",
            xyz_angstrom=(float(index), 0.0, 0.0),
        )
        for index in range(atom_count)
    ]
    spec = ModelSpec(
        project_id="runtime_project",
        revision=1,
        model_type=ModelType.MOLECULE,
        model=MoleculeSpec(name="hydrogen", atoms=atoms),
    )
    return ModelState.from_model_spec(spec)


class FakePlugin:
    def __init__(
        self,
        manifest: DomainPluginManifest,
        *,
        match_kind: MatchKind = MatchKind.EXACT,
        specificity: int = 100,
        match_issues: tuple[RuntimeIssue, ...] = (),
        plan_issues: tuple[RuntimeIssue, ...] = (),
    ) -> None:
        self.plugin_id = manifest.plugin_id
        self.contract_version = manifest.contract_version
        self.implementation_version = manifest.implementation_version
        self.match_kind = match_kind
        self.specificity = specificity
        self.match_issues = match_issues
        self.match_calls = 0
        self.plan_calls = 0
        self.build_calls = 0
        self.validate_calls = 0
        self.raise_match = False
        self.malformed_match = False
        self.match_plugin_id = manifest.plugin_id
        self.mutate_match_input = False
        self.mutate_match_identity = False
        self.raise_plan = False
        self.malformed_plan = False
        self.mutate_plan_input = False
        self.mutate_plan_identity = False
        self.plan_plugin_id = manifest.plugin_id
        self.plan_questions: tuple[ModelingQuestion, ...] = ()
        self.plan_issues = plan_issues
        self.plan_steps = True

    def match(self, intent: ModelingIntent):
        self.match_calls += 1
        if self.mutate_match_input:
            object.__setattr__(intent, "material", "mutated")
        if self.raise_match:
            raise RuntimeError("private plugin failure")
        if self.malformed_match:
            return {"kind": "exact"}
        result = MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id=self.match_plugin_id,
            kind=self.match_kind,
            specificity=self.specificity if self.match_kind is not MatchKind.NONE else 0,
            reason_codes=(f"match.{self.match_kind.value}",),
            issues=self.match_issues,
        )
        if self.mutate_match_identity:
            self.plugin_id = "sic_changed"
            self.contract_version = "9.0.0"
            self.implementation_version = "9.0.0"
        return result

    def plan(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None,
    ):
        self.plan_calls += 1
        if self.mutate_plan_input:
            object.__setattr__(intent, "operation", "mutated")
        if self.raise_plan:
            raise RuntimeError("private plan failure")
        if self.malformed_plan:
            return {"steps": []}
        current_revision = None
        if current_state is not None:
            from material_studio_mcp_server.runtime import RevisionIdentity

            current_revision = RevisionIdentity(
                project_id=current_state.project_id,
                revision=current_state.revision,
                model_spec_digest=current_state.model_spec_digest,
            )
        steps = (
            (
                PlanStep(
                    step_id="step.surface",
                    operation="surface.build",
                    parameters=(),
                ),
            )
            if self.plan_steps
            else ()
        )
        build_eligible = bool(steps) and not self.plan_questions and not any(
            issue.is_blocking for issue in self.plan_issues
        )
        result = ModelingPlan(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id=self.plan_plugin_id,
            plugin_contract_version=self.contract_version,
            plugin_implementation_version=self.implementation_version,
            normalized_intent_digest=contract_digest(
                intent,
                contract_name="ModelingIntent",
                contract_version=RUNTIME_CONTRACT_VERSION,
            ),
            current_revision=current_revision,
            output_kind=intent.output_kind,
            steps=steps,
            assumptions=(
                ResolvedAssumption(
                    code="assumption.surface",
                    statement="Use the declared surface settings.",
                    source="declared_default",
                ),
            ),
            questions=self.plan_questions,
            issues=self.plan_issues,
            forced_selection=None,
            fallback=None,
            build_eligible=build_eligible,
        )
        if self.mutate_plan_identity:
            self.plugin_id = "sic_changed"
            self.contract_version = "9.0.0"
            self.implementation_version = "9.0.0"
        return result

    def build(self, plan):
        self.build_calls += 1
        raise AssertionError("router must not build")

    def validate(self, model):
        self.validate_calls += 1
        raise AssertionError("router must not validate")


def _registry(*plugins: FakePlugin) -> registry_module.CapabilityRegistry:
    return registry_module.CapabilityRegistry(
        tuple(
            (_manifest_for_plugin(plugin), plugin)
            for plugin in plugins
        )
    )


def _manifest_for_plugin(plugin: FakePlugin) -> DomainPluginManifest:
    return plugin._manifest


def _plugin(manifest: DomainPluginManifest, **kwargs) -> FakePlugin:
    plugin = FakePlugin(manifest, **kwargs)
    plugin._manifest = manifest
    return plugin


def _route_single(
    manifest: DomainPluginManifest,
    intent: ModelingIntent,
    current_state: ModelState | None = None,
) -> tuple[FakePlugin, object]:
    plugin = _plugin(manifest)
    decision = RuntimeRouter(
        registry_module.CapabilityRegistry(((manifest, plugin),))
    ).route(intent, current_state)
    return plugin, decision


@pytest.mark.parametrize(
    ("manifest", "intent", "expected_reason"),
    [
        (_manifest(material="3c-SiC"), _intent(), "router.material_unsupported"),
        (_manifest(scenario="surface_other"), _intent(), "router.scenario_unsupported"),
        (_manifest(operation="surface_other"), _intent(), "router.operation_unsupported"),
        (
            _manifest(model_kinds=("molecule",), periodicity=(0,)),
            _intent(),
            "router.model_kind_unsupported",
        ),
        (
            _manifest(supports_create=False, supports_patch=True),
            _intent(),
            "router.create_unsupported",
        ),
        (
            _manifest(
                supports_create=False,
                supports_patch=True,
                requires_current_model=True,
            ),
            _intent(),
            "router.current_model_requirement_mismatch",
        ),
        (
            _manifest(access_modes=("metadata_only",)),
            _intent(),
            "router.reference_access_unsupported",
        ),
        (
            _manifest(supports_calculation_plan=False),
            _intent(
                parameters=(
                    SemanticParameter(
                        name="requires_calculation_plan", value=True, unit=None
                    ),
                )
            ),
            "router.calculation_plan_unsupported",
        ),
        (
            _manifest(unsupported_capabilities=("surface_reconstruction",)),
            _intent(semantic_requirements=("surface_reconstruction",)),
            "router.semantic_requirement_unsupported",
        ),
        (
            _manifest(max_atoms=5),
            _intent(parameters=(SemanticParameter(name="atom_count", value=6, unit=None),)),
            "router.atom_count_above_maximum",
        ),
        (
            _manifest(periodicity=(3,)),
            _intent(
                parameters=(
                    SemanticParameter(name="periodicity_dimension", value=2, unit=None),
                )
            ),
            "router.periodicity_dimension_unsupported",
        ),
    ],
)
def test_every_manifest_prefilter_runs_before_match(
    manifest: DomainPluginManifest,
    intent: ModelingIntent,
    expected_reason: str,
) -> None:
    plugin, decision = _route_single(manifest, intent)
    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert expected_reason in decision.no_match.reason_codes
    assert plugin.match_calls == 0


def test_patch_support_prefilter_is_exact() -> None:
    state = _crystal_state()
    intent = _intent(
        requires_current_model=True,
        output_kind=BuildOutputKind.SEMANTIC_PATCH,
    )
    plugin, decision = _route_single(_manifest(supports_patch=False), intent, state)
    assert "router.patch_unsupported" in decision.no_match.reason_codes
    assert plugin.match_calls == 0


def test_string_and_unsupported_capability_matching_is_case_sensitive() -> None:
    manifest = _manifest(unsupported_capabilities=("surface_reconstruction",))
    plugin, decision = _route_single(
        manifest,
        _intent(semantic_requirements=("Surface_Reconstruction",)),
    )
    assert decision.selected_plugin_id == "sic_surface"
    assert plugin.match_calls == 1


@pytest.mark.parametrize("present", [False, True])
def test_false_or_absent_calculation_plan_parameter_adds_no_filter(
    present: bool,
) -> None:
    parameters = (
        (SemanticParameter(name="requires_calculation_plan", value=False, unit=None),)
        if present
        else ()
    )
    plugin, decision = _route_single(
        _manifest(supports_calculation_plan=False),
        _intent(parameters=parameters),
    )
    assert decision.selected
    assert plugin.match_calls == 1


@pytest.mark.parametrize(
    ("name", "value", "reason_code"),
    [
        ("atom_count", True, "router.atom_count_type_invalid"),
        ("atom_count", -1, "router.atom_count_range_invalid"),
        ("atom_count", 1.0, "router.atom_count_type_invalid"),
        (
            "periodicity_dimension",
            False,
            "router.periodicity_dimension_type_invalid",
        ),
        (
            "periodicity_dimension",
            4,
            "router.periodicity_dimension_range_invalid",
        ),
        (
            "requires_calculation_plan",
            1,
            "router.requires_calculation_plan_type_invalid",
        ),
    ],
)
def test_invalid_reserved_parameters_block_before_registry_invocation(
    name: str,
    value,
    reason_code: str,
) -> None:
    manifest = _manifest()
    plugin = _plugin(manifest)
    decision = RuntimeRouter(
        registry_module.CapabilityRegistry(((manifest, plugin),))
    ).route(
        _intent(parameters=(SemanticParameter(name=name, value=value, unit=None),))
    )
    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert decision.issues[0].kind is RuntimeIssueKind.INVALID_INPUT
    assert reason_code in tuple(issue.code for issue in decision.issues)
    assert plugin.match_calls == 0


@pytest.mark.parametrize(
    ("min_atoms", "max_atoms", "eligible"),
    [(0, None, True), (0, 100, False), (1, None, False), (1, 100, False)],
)
def test_unknown_atom_count_truth_table(
    min_atoms: int,
    max_atoms: int | None,
    eligible: bool,
) -> None:
    plugin, decision = _route_single(
        _manifest(
            min_atoms=min_atoms,
            max_atoms=max_atoms,
            model_kinds=("imported_structure",),
            periodicity=(0, 1, 2, 3),
        ),
        _intent(model_kind=ModelKind.IMPORTED_STRUCTURE),
    )
    assert decision.selected is eligible
    assert plugin.match_calls == int(eligible)
    if not eligible:
        assert "router.atom_count_required" in decision.no_match.reason_codes


@pytest.mark.parametrize(
    ("periodicity", "eligible"),
    [((0, 1, 2, 3), True), ((0, 1, 2), False), ((3,), False)],
)
def test_unknown_periodicity_truth_table(
    periodicity: tuple[int, ...],
    eligible: bool,
) -> None:
    plugin, decision = _route_single(
        _manifest(
            model_kinds=("imported_structure",),
            periodicity=periodicity,
        ),
        _intent(model_kind=ModelKind.IMPORTED_STRUCTURE),
    )
    assert decision.selected is eligible
    assert plugin.match_calls == int(eligible)
    if not eligible:
        assert "router.periodicity_dimension_required" in decision.no_match.reason_codes


@pytest.mark.parametrize(
    ("state_factory", "kind", "periodicity"),
    [(_molecule_state, ModelKind.MOLECULE, 0), (_crystal_state, ModelKind.CRYSTAL, 3)],
)
def test_atom_count_and_periodicity_derive_from_typed_current_state(
    state_factory,
    kind: ModelKind,
    periodicity: int,
) -> None:
    state = state_factory(3)
    manifest = _manifest(
        min_atoms=3,
        max_atoms=3,
        model_kinds=(kind.value,),
        periodicity=(periodicity,),
    )
    plugin, decision = _route_single(
        manifest,
        _intent(model_kind=kind),
        state,
    )
    assert decision.prefilter.resolved_inputs.atom_count == 3
    assert decision.prefilter.resolved_inputs.periodicity_dimension == periodicity
    assert decision.selected
    assert plugin.match_calls == 1


def test_explicit_reserved_values_override_current_state_derivation() -> None:
    state = _crystal_state(2)
    manifest = _manifest(min_atoms=7, max_atoms=7, periodicity=(2,))
    intent = _intent(
        parameters=(
            SemanticParameter(name="atom_count", value=7, unit=None),
            SemanticParameter(name="periodicity_dimension", value=2, unit=None),
        )
    )
    plugin, decision = _route_single(manifest, intent, state)
    assert decision.selected
    assert plugin.match_calls == 1


def test_missing_required_state_and_kind_mismatch_block_before_match() -> None:
    patch_manifest = _manifest(
        supports_create=False,
        supports_patch=True,
        requires_current_model=True,
    )
    plugin = _plugin(patch_manifest)
    router = RuntimeRouter(
        registry_module.CapabilityRegistry(((patch_manifest, plugin),))
    )
    missing = router.route(
        _intent(
            requires_current_model=True,
            output_kind=BuildOutputKind.SEMANTIC_PATCH,
        )
    )
    assert "router.current_model_required" in tuple(issue.code for issue in missing.issues)
    assert plugin.match_calls == 0

    mismatch = router.route(_intent(), _molecule_state())
    assert "router.current_model_kind_mismatch" in tuple(
        issue.code for issue in mismatch.issues
    )
    assert plugin.match_calls == 0


def test_all_prefiltered_candidates_are_matched_exactly_once() -> None:
    plugins = (
        _plugin(_manifest("sic_alpha"), match_kind=MatchKind.NONE, specificity=0),
        _plugin(_manifest("sic_beta"), match_kind=MatchKind.COMPATIBLE, specificity=20),
        _plugin(_manifest("sic_gamma"), match_kind=MatchKind.EXACT, specificity=10),
    )
    decision = RuntimeRouter(_registry(*reversed(plugins))).route(_intent())
    assert decision.selected_plugin_id == "sic_gamma"
    assert tuple(plugin.match_calls for plugin in plugins) == (1, 1, 1)
    assert tuple(item.plugin_id for item in decision.candidate_evaluations) == (
        "sic_alpha",
        "sic_beta",
        "sic_gamma",
    )


def test_match_ranking_exact_then_specificity_then_larger_priority() -> None:
    compatible = _plugin(
        _manifest("sic_compatible", priority=1000),
        match_kind=MatchKind.COMPATIBLE,
        specificity=1000,
    )
    exact = _plugin(
        _manifest("sic_exact", priority=-1000),
        match_kind=MatchKind.EXACT,
        specificity=1,
    )
    assert RuntimeRouter(_registry(compatible, exact)).route(
        _intent()
    ).selected_plugin_id == "sic_exact"

    low_specificity = _plugin(
        _manifest("sic_low_specificity", priority=1000), specificity=10
    )
    high_specificity = _plugin(
        _manifest("sic_high_specificity", priority=-1000), specificity=11
    )
    assert RuntimeRouter(_registry(low_specificity, high_specificity)).route(
        _intent()
    ).selected_plugin_id == "sic_high_specificity"

    low_priority = _plugin(_manifest("sic_low_priority", priority=-1))
    high_priority = _plugin(_manifest("sic_high_priority", priority=2))
    assert RuntimeRouter(_registry(low_priority, high_priority)).route(
        _intent()
    ).selected_plugin_id == "sic_high_priority"


def test_complete_semantic_tie_fails_closed_without_plugin_id_tiebreak() -> None:
    alpha = _plugin(_manifest("sic_alpha", priority=7), specificity=50)
    beta = _plugin(_manifest("sic_beta", priority=7), specificity=50)
    decision = RuntimeRouter(_registry(beta, alpha)).route(_intent())
    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert decision.selected_plugin_id is None
    assert decision.ambiguity.tied_plugin_ids == ("sic_alpha", "sic_beta")
    assert decision.ambiguity.priority == 7
    assert decision.stage_receipts == ()


def test_no_match_preserves_deduplicated_typed_match_issues() -> None:
    needs_input = RuntimeIssue(
        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
        code="plugin.surface_orientation_required",
        message="Surface orientation is required.",
        field_path="$.parameters.orientation",
    )
    unsupported = RuntimeIssue(
        kind=RuntimeIssueKind.UNSUPPORTED,
        code="plugin.reconstruction_unsupported",
        message="Reconstruction is unsupported.",
        field_path="$.parameters.reconstruction",
    )
    first = _plugin(
        _manifest("sic_first"),
        match_kind=MatchKind.NONE,
        match_issues=(needs_input, unsupported),
    )
    second = _plugin(
        _manifest("sic_second"),
        match_kind=MatchKind.NONE,
        match_issues=(unsupported, needs_input),
    )

    decision = RuntimeRouter(_registry(first, second)).route(_intent())

    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert tuple(issue.code for issue in decision.issues) == (
        "plugin.reconstruction_unsupported",
        "plugin.surface_orientation_required",
    )
    assert decision.no_match is not None


def test_ambiguity_preserves_deduplicated_typed_match_issues() -> None:
    needs_input = RuntimeIssue(
        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
        code="plugin.surface_orientation_required",
        message="Surface orientation is required.",
        field_path="$.parameters.orientation",
    )
    alpha = _plugin(
        _manifest("sic_alpha", priority=7),
        specificity=50,
        match_issues=(needs_input,),
    )
    beta = _plugin(
        _manifest("sic_beta", priority=7),
        specificity=50,
        match_issues=(needs_input,),
    )

    decision = RuntimeRouter(_registry(beta, alpha)).route(_intent())

    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert tuple(issue.code for issue in decision.issues) == (
        "plugin.surface_orientation_required",
    )
    assert decision.ambiguity is not None


def test_no_match_evidence_is_sorted_unique_and_excludes_prefiltered_plugins() -> None:
    zeta = _plugin(_manifest("sic_zeta"), match_kind=MatchKind.NONE, specificity=0)
    alpha = _plugin(_manifest("sic_alpha"), match_kind=MatchKind.NONE, specificity=0)
    filtered_manifest = _manifest("sic_filtered", material="other")
    filtered = _plugin(filtered_manifest)
    decision = RuntimeRouter(_registry(zeta, filtered, alpha)).route(_intent())
    assert decision.no_match.evaluated_plugin_ids == ("sic_alpha", "sic_zeta")
    assert decision.no_match.reason_codes == tuple(
        sorted(set(decision.no_match.reason_codes))
    )
    assert filtered.match_calls == 0


def test_forced_selection_matches_only_target_and_records_evidence() -> None:
    alpha = _plugin(_manifest("sic_alpha", priority=100))
    beta = _plugin(_manifest("sic_beta", priority=-100), match_kind=MatchKind.COMPATIBLE)
    decision = RuntimeRouter(_registry(alpha, beta)).route(
        _intent(),
        forced_selection=ForcedSelectionRequest(
            plugin_id="sic_beta",
            reason="Architect-reviewed rollout cohort.",
        ),
    )
    assert decision.selected_plugin_id == "sic_beta"
    assert decision.forced_selection.capability_match is True
    assert alpha.match_calls == 0
    assert beta.match_calls == 1


def test_forced_selection_cannot_override_prefilter_or_none_match() -> None:
    filtered_manifest = _manifest("sic_filtered", material="other")
    filtered = _plugin(filtered_manifest)
    router = RuntimeRouter(_registry(filtered))
    rejected = router.route(
        _intent(),
        forced_selection=ForcedSelectionRequest("sic_filtered", "Reviewed."),
    )
    assert rejected.selected_plugin_id is None
    assert filtered.match_calls == 0

    issue = RuntimeIssue(
        kind=RuntimeIssueKind.INVALID_INPUT,
        code="plugin.orientation_invalid",
        message="Orientation is invalid.",
        field_path="$.parameters.orientation",
    )
    none_plugin = _plugin(
        _manifest("sic_none"),
        match_kind=MatchKind.NONE,
        specificity=0,
        match_issues=(issue,),
    )
    rejected = RuntimeRouter(_registry(none_plugin)).route(
        _intent(),
        forced_selection=ForcedSelectionRequest("sic_none", "Reviewed."),
    )
    assert rejected.selected_plugin_id is None
    assert rejected.forced_selection is None
    assert rejected.issues == (issue,)
    assert none_plugin.match_calls == 1


def test_fallback_target_matches_independently_and_records_evidence() -> None:
    other = _plugin(_manifest("sic_other", priority=100))
    target = _plugin(_manifest("sic_target", priority=-100))
    decision = RuntimeRouter(_registry(other, target)).route(
        _intent(),
        fallback=FallbackRequest(
            from_plugin_id="sic_source",
            to_plugin_id="sic_target",
            reason_code="fallback.reviewed",
        ),
    )
    assert decision.selected_plugin_id == "sic_target"
    assert decision.fallback.target_independently_matched is True
    assert other.match_calls == 0
    assert target.match_calls == 1


def test_fallback_none_match_preserves_typed_issue() -> None:
    issue = RuntimeIssue(
        kind=RuntimeIssueKind.UNSUPPORTED,
        code="plugin.reconstruction_unsupported",
        message="Reconstruction is unsupported.",
        field_path="$.parameters.reconstruction",
    )
    target = _plugin(
        _manifest("sic_target"),
        match_kind=MatchKind.NONE,
        specificity=0,
        match_issues=(issue,),
    )

    decision = RuntimeRouter(_registry(target)).route(
        _intent(),
        fallback=FallbackRequest(
            from_plugin_id="sic_source",
            to_plugin_id="sic_target",
            reason_code="fallback.reviewed",
        ),
    )

    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert decision.fallback is None
    assert decision.issues == (issue,)
    assert target.match_calls == 1


def test_fallback_rejects_same_source_target_before_plugin_invocation() -> None:
    plugin = _plugin(_manifest("sic_target"))
    decision = RuntimeRouter(_registry(plugin)).route(
        _intent(),
        fallback=FallbackRequest(
            from_plugin_id="sic_target",
            to_plugin_id="sic_target",
            reason_code="fallback.reviewed",
        ),
    )
    assert decision.outcome is RuntimeOutcome.BLOCKED
    assert "router.fallback_target_matches_source" in tuple(
        issue.code for issue in decision.issues
    )
    assert plugin.match_calls == 0


@pytest.mark.parametrize("failure", ["exception", "malformed", "identity"])
def test_plugin_match_failures_are_internal_errors_not_no_match(failure: str) -> None:
    plugin = _plugin(_manifest())
    if failure == "exception":
        plugin.raise_match = True
    elif failure == "malformed":
        plugin.malformed_match = True
    else:
        plugin.match_plugin_id = "sic_other"
    decision = RuntimeRouter(_registry(plugin)).route(_intent())
    assert decision.outcome is RuntimeOutcome.FAILED
    assert decision.no_match is None
    assert decision.stage_receipts[-1].status is StageStatus.FAILED
    assert all(
        issue.kind is RuntimeIssueKind.INTERNAL_ERROR
        for issue in decision.stage_receipts[-1].issues
    )


def test_match_identity_mutation_after_invocation_fails_closed() -> None:
    plugin = _plugin(_manifest())
    plugin.mutate_match_identity = True

    decision = RuntimeRouter(_registry(plugin)).route(_intent())

    assert decision.outcome is RuntimeOutcome.FAILED
    assert decision.stage_receipts[-1].status is StageStatus.FAILED
    assert "router.match_plugin_identity_changed" in tuple(
        issue.code for issue in decision.stage_receipts[-1].issues
    )
    assert plugin.plan_calls == 0


def test_match_input_mutation_and_non_pure_probe_fail_closed() -> None:
    mutator = _plugin(_manifest("sic_mutator"))
    mutator.mutate_match_input = True
    mutated = RuntimeRouter(_registry(mutator)).route(_intent())
    assert mutated.outcome is RuntimeOutcome.FAILED
    assert mutated.stage_receipts[0].side_effects.input_mutated is True

    plugin = _plugin(_manifest("sic_probe"))
    payload = empty_side_effect_receipt().model_dump()
    payload["filesystem_read_count"] = 1
    impure = SideEffectReceipt(**payload)
    observed = RuntimeRouter(
        _registry(plugin), side_effect_probe=lambda plugin_id, stage: impure
    ).route(_intent())
    assert observed.outcome is RuntimeOutcome.FAILED
    assert observed.stage_receipts[0].side_effects.filesystem_read_count == 1


def test_blocking_match_issue_selects_plugin_but_stops_before_plan() -> None:
    issue = RuntimeIssue(
        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
        code="plugin.surface_orientation_required",
        message="Surface orientation is required.",
        field_path="$.parameters.orientation",
    )
    plugin = _plugin(_manifest(), match_issues=(issue,))
    router = RuntimeRouter(_registry(plugin))
    routing = router.route(_intent())
    assert routing.outcome is RuntimeOutcome.BLOCKED
    assert routing.selected_plugin_id == "sic_surface"
    assert plugin.plan_calls == 0
    with pytest.raises(ValueError, match="router.routing_not_plan_eligible"):
        router.plan_selected(routing, _intent())


def test_plan_is_called_once_and_selection_evidence_is_bound() -> None:
    plugin = _plugin(_manifest())
    router = RuntimeRouter(_registry(plugin))
    intent = _intent()
    routing = router.route(
        intent,
        forced_selection=ForcedSelectionRequest("sic_surface", "Reviewed."),
    )
    planning = router.plan_selected(routing, intent)
    assert planning.outcome is RuntimeOutcome.COMPLETED
    assert planning.plan.forced_selection == routing.forced_selection
    assert planning.stage_receipt.status is StageStatus.COMPLETED
    assert plugin.match_calls == 1
    assert plugin.plan_calls == 1
    assert plugin.build_calls == 0
    assert plugin.validate_calls == 0


@pytest.mark.parametrize("failure", ["exception", "malformed", "identity", "mutation"])
def test_plan_failures_stop_pipeline_with_internal_error(failure: str) -> None:
    plugin = _plugin(_manifest())
    if failure == "exception":
        plugin.raise_plan = True
    elif failure == "malformed":
        plugin.malformed_plan = True
    elif failure == "identity":
        plugin.plan_plugin_id = "sic_other"
    else:
        plugin.mutate_plan_input = True
    router = RuntimeRouter(_registry(plugin))
    intent = _intent()
    routing = router.route(intent)
    planning = router.plan_selected(routing, intent)
    assert planning.outcome is RuntimeOutcome.FAILED
    assert planning.plan is None
    assert planning.stage_receipt.status is StageStatus.FAILED
    assert any(
        issue.kind is RuntimeIssueKind.INTERNAL_ERROR
        for issue in planning.stage_receipt.issues
    )
    assert plugin.plan_calls == 1
    assert plugin.build_calls == 0


def test_plan_identity_mutation_after_invocation_fails_closed() -> None:
    plugin = _plugin(_manifest())
    router = RuntimeRouter(_registry(plugin))
    intent = _intent()
    routing = router.route(intent)
    plugin.mutate_plan_identity = True

    planning = router.plan_selected(routing, intent)

    assert planning.outcome is RuntimeOutcome.FAILED
    assert planning.plan is None
    assert planning.plan_digest is None
    assert planning.stage_receipt.status is StageStatus.FAILED
    assert "router.plan_plugin_identity_changed" in tuple(
        issue.code for issue in planning.stage_receipt.issues
    )


def test_plan_internal_error_issue_is_terminal_failed_stage() -> None:
    internal = RuntimeIssue(
        kind=RuntimeIssueKind.INTERNAL_ERROR,
        code="plugin.secret_internal_failure",
        message="private plugin details",
        field_path="$.private",
    )
    plugin = _plugin(_manifest(), plan_issues=(internal,))
    router = RuntimeRouter(_registry(plugin))
    intent = _intent()

    planning = router.plan_selected(router.route(intent), intent)

    assert planning.outcome is RuntimeOutcome.FAILED
    assert planning.plan is None
    assert planning.plan_digest is None
    assert planning.stage_receipt.status is StageStatus.FAILED
    assert tuple(issue.code for issue in planning.stage_receipt.issues) == (
        "router.plan_internal_error",
    )
    assert all(
        issue.kind is RuntimeIssueKind.INTERNAL_ERROR
        for issue in planning.stage_receipt.issues
    )


def test_non_build_eligible_plan_is_blocked_with_corresponding_issue() -> None:
    plugin = _plugin(_manifest())
    plugin.plan_steps = False
    router = RuntimeRouter(_registry(plugin))
    intent = _intent()
    planning = router.plan_selected(router.route(intent), intent)
    assert planning.outcome is RuntimeOutcome.BLOCKED
    assert "router.plan_not_build_eligible" in tuple(
        issue.code for issue in planning.stage_receipt.issues
    )


def test_router_uses_only_typed_intent_and_optional_state() -> None:
    router = RuntimeRouter(registry_module.CapabilityRegistry())
    with pytest.raises(TypeError, match="router.intent_type_invalid"):
        router.route({"request": "raw natural language"})
    with pytest.raises(TypeError, match="router.current_state_type_invalid"):
        router.route(_intent(), {"revision": 1})
