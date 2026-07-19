from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

import material_studio_mcp_server.runtime.receipts as receipt_models
from material_studio_mcp_server.runtime import (
    ADAPTER_CONTRACT_VERSION,
    RUNTIME_CONTRACT_VERSION,
    AmbiguityEvidence,
    BuildOutputKind,
    DecisionDifference,
    EmittedArtifactReceipt,
    FallbackEvidence,
    ForcedSelectionEvidence,
    MatchKind,
    MigrationMode,
    NoMatchEvidence,
    PluginStageReceipt,
    RevisionIdentity,
    RuntimeIssue,
    RuntimeIssueKind,
    RuntimeOutcome,
    RuntimePluginReceipt,
    ShadowComparisonReceipt,
    SideEffectReceipt,
    StageName,
    StageStatus,
    contract_digest,
)


PLUGIN_ID = "sic_surface"
PLUGIN_CONTRACT_VERSION = "1.0.0"
PLUGIN_IMPLEMENTATION_VERSION = "1.2.0"


def _contract_version(contract_name: str) -> str:
    if contract_name in {"ModelSpec", "SemanticPatch"}:
        return ADAPTER_CONTRACT_VERSION
    return RUNTIME_CONTRACT_VERSION


def _digest(
    contract_name: str,
    marker: str | None = None,
    *,
    contract_version: str | None = None,
):
    return contract_digest(
        {"marker": marker or contract_name},
        contract_name=contract_name,
        contract_version=contract_version or _contract_version(contract_name),
    )


def _intent_digest(marker: str = "intent"):
    return _digest("ModelingIntent", marker)


def _plan_digest(marker: str = "plan"):
    return _digest("ModelingPlan", marker)


def _model_digest(marker: str = "model"):
    return _digest("ModelSpec", marker)


def _pure_side_effects(**updates) -> SideEffectReceipt:
    payload = {
        "filesystem_read_count": 0,
        "filesystem_write_count": 0,
        "environment_read_count": 0,
        "process_launch_count": 0,
        "network_request_count": 0,
        "gui_action_count": 0,
        "revision_write_count": 0,
        "input_mutated": False,
        "hidden_reference_access": False,
        "wall_clock_read": False,
        "randomness_used": False,
        "undeclared_reference_source_ids": (),
    }
    payload.update(updates)
    return SideEffectReceipt(**payload)


def _issue(kind: RuntimeIssueKind) -> RuntimeIssue:
    return RuntimeIssue(
        kind=kind,
        code=f"runtime.{kind.value}",
        message=f"Observed {kind.value}.",
        field_path="$.runtime",
    )


def _revision(marker: str = "current-model") -> RevisionIdentity:
    return RevisionIdentity(
        project_id="runtime_project",
        revision=7,
        model_spec_digest=_model_digest(marker),
    )


def _default_inputs(
    stage: StageName,
    *,
    include_model_state: bool = False,
) -> tuple:
    if stage is StageName.MATCH:
        return (_intent_digest(),)
    if stage is StageName.PLAN:
        values = [_intent_digest()]
        if include_model_state:
            values.append(_digest("ModelState"))
        return tuple(values)
    if stage is StageName.BUILD:
        return (_plan_digest(),)
    return (_model_digest(),)


def _default_output(stage: StageName) -> tuple:
    outputs = {
        StageName.MATCH: _digest("MatchResult"),
        StageName.PLAN: _plan_digest(),
        StageName.BUILD: _model_digest(),
        StageName.VALIDATE: _digest("DomainValidationReport"),
    }
    return (outputs[stage],)


def _stage(
    stage: StageName,
    *,
    status: StageStatus = StageStatus.COMPLETED,
    include_model_state: bool = False,
    input_digests: tuple | None = None,
    output_digests: tuple | None = None,
    side_effects: SideEffectReceipt | None = None,
    issues: tuple[RuntimeIssue, ...] = (),
    plugin_id: str = PLUGIN_ID,
    plugin_contract_version: str = PLUGIN_CONTRACT_VERSION,
    plugin_implementation_version: str = PLUGIN_IMPLEMENTATION_VERSION,
) -> PluginStageReceipt:
    if input_digests is None:
        input_digests = _default_inputs(
            stage,
            include_model_state=include_model_state,
        )
    if output_digests is None:
        output_digests = (
            _default_output(stage)
            if status is StageStatus.COMPLETED
            else ()
        )
    return PluginStageReceipt(
        contract_version=RUNTIME_CONTRACT_VERSION,
        stage=stage,
        status=status,
        plugin_id=plugin_id,
        plugin_contract_version=plugin_contract_version,
        plugin_implementation_version=plugin_implementation_version,
        input_digests=input_digests,
        output_digests=output_digests,
        side_effects=side_effects or _pure_side_effects(),
        issues=issues,
    )


def _artifact(
    kind: BuildOutputKind = BuildOutputKind.MODEL_SPEC,
    *,
    digest=None,
) -> EmittedArtifactReceipt:
    expected_name = (
        "ModelSpec"
        if kind is BuildOutputKind.MODEL_SPEC
        else "SemanticPatch"
    )
    return EmittedArtifactReceipt(
        kind=kind,
        digest=digest or _digest(expected_name),
    )


def _difference() -> DecisionDifference:
    return DecisionDifference(
        code="comparison.output_kind",
        field_path="$.output_kind",
        legacy_value_sha256="a" * 64,
        shadow_value_sha256="b" * 64,
        summary="The planned output kinds differ.",
    )


def _shadow_comparison(
    *,
    current_revision: RevisionIdentity | None = None,
    equivalent: bool = True,
    differences: tuple[DecisionDifference, ...] = (),
) -> ShadowComparisonReceipt:
    return ShadowComparisonReceipt(
        contract_version=RUNTIME_CONTRACT_VERSION,
        selected_plugin_id=PLUGIN_ID,
        selected_plugin_contract_version=PLUGIN_CONTRACT_VERSION,
        selected_plugin_implementation_version=PLUGIN_IMPLEMENTATION_VERSION,
        normalized_intent_digest=_intent_digest(),
        current_revision=current_revision,
        authoritative_legacy_decision_digest=_digest("LegacyDecision"),
        shadow_plan_digest=_plan_digest(),
        equivalent=equivalent,
        differences=differences,
    )


def _runtime(
    *,
    migration_mode: MigrationMode,
    outcome: RuntimeOutcome,
    authoritative_path: str,
    selected: bool,
    current_revision: RevisionIdentity | None = None,
    plan_digest=None,
    emitted_artifact: EmittedArtifactReceipt | None = None,
    stage_receipts: tuple[PluginStageReceipt, ...] = (),
    ambiguity: AmbiguityEvidence | None = None,
    no_match: NoMatchEvidence | None = None,
    forced_selection: ForcedSelectionEvidence | None = None,
    fallback: FallbackEvidence | None = None,
    shadow_comparison: ShadowComparisonReceipt | None = None,
    issues: tuple[RuntimeIssue, ...] = (),
) -> RuntimePluginReceipt:
    return RuntimePluginReceipt(
        contract_version=RUNTIME_CONTRACT_VERSION,
        receipt_id="receipt.runtime-001",
        migration_mode=migration_mode,
        outcome=outcome,
        authoritative_path=authoritative_path,
        normalized_intent_digest=_intent_digest(),
        current_revision=current_revision,
        selected_plugin_id=PLUGIN_ID if selected else None,
        selected_plugin_contract_version=(
            PLUGIN_CONTRACT_VERSION if selected else None
        ),
        selected_plugin_implementation_version=(
            PLUGIN_IMPLEMENTATION_VERSION if selected else None
        ),
        plan_digest=plan_digest,
        emitted_artifact=emitted_artifact,
        stage_receipts=stage_receipts,
        ambiguity=ambiguity,
        no_match=no_match,
        forced_selection=forced_selection,
        fallback=fallback,
        shadow_comparison=shadow_comparison,
        issues=issues,
    )


def _off_receipt(
    *,
    outcome: RuntimeOutcome = RuntimeOutcome.COMPLETED,
    issues: tuple[RuntimeIssue, ...] = (),
) -> RuntimePluginReceipt:
    return _runtime(
        migration_mode=MigrationMode.OFF,
        outcome=outcome,
        authoritative_path="legacy",
        selected=False,
        issues=issues,
    )


def _shadow_completed(
    *,
    current_revision: RevisionIdentity | None = None,
) -> RuntimePluginReceipt:
    stages = (
        _stage(StageName.MATCH),
        _stage(
            StageName.PLAN,
            include_model_state=current_revision is not None,
        ),
    )
    return _runtime(
        migration_mode=MigrationMode.SHADOW,
        outcome=RuntimeOutcome.COMPLETED,
        authoritative_path="legacy",
        selected=True,
        current_revision=current_revision,
        plan_digest=_plan_digest(),
        stage_receipts=stages,
        shadow_comparison=_shadow_comparison(current_revision=current_revision),
    )


def _active_completed(
    *,
    current_revision: RevisionIdentity | None = None,
    match_issues: tuple[RuntimeIssue, ...] = (),
    runtime_issues: tuple[RuntimeIssue, ...] = (),
) -> RuntimePluginReceipt:
    artifact = _artifact()
    stages = (
        _stage(StageName.MATCH, issues=match_issues),
        _stage(
            StageName.PLAN,
            include_model_state=current_revision is not None,
        ),
        _stage(StageName.BUILD, output_digests=(artifact.digest,)),
        _stage(StageName.VALIDATE, input_digests=(artifact.digest,)),
    )
    return _runtime(
        migration_mode=MigrationMode.ACTIVE,
        outcome=RuntimeOutcome.COMPLETED,
        authoritative_path="plugin",
        selected=True,
        current_revision=current_revision,
        plan_digest=_plan_digest(),
        emitted_artifact=artifact,
        stage_receipts=stages,
        issues=runtime_issues,
    )


def _semantic_blocked(
    kind: RuntimeIssueKind,
    *,
    migration_mode: MigrationMode = MigrationMode.ACTIVE,
) -> RuntimePluginReceipt:
    return _runtime(
        migration_mode=migration_mode,
        outcome=RuntimeOutcome.BLOCKED,
        authoritative_path=(
            "legacy" if migration_mode is MigrationMode.SHADOW else "plugin"
        ),
        selected=True,
        stage_receipts=(
            _stage(StageName.MATCH, issues=(_issue(kind),)),
        ),
    )


def _failed_receipt(
    *,
    migration_mode: MigrationMode = MigrationMode.ACTIVE,
) -> RuntimePluginReceipt:
    return _runtime(
        migration_mode=migration_mode,
        outcome=RuntimeOutcome.FAILED,
        authoritative_path=(
            "legacy" if migration_mode is MigrationMode.SHADOW else "plugin"
        ),
        selected=True,
        stage_receipts=(
            _stage(
                StageName.MATCH,
                status=StageStatus.FAILED,
                issues=(_issue(RuntimeIssueKind.INTERNAL_ERROR),),
            ),
        ),
    )


def _ambiguity() -> AmbiguityEvidence:
    return AmbiguityEvidence(
        tied_plugin_ids=("sic_alpha", "sic_beta"),
        match_kind=MatchKind.EXACT,
        specificity=500,
        priority=100,
        fail_closed=True,
    )


def _no_match() -> NoMatchEvidence:
    return NoMatchEvidence(
        evaluated_plugin_ids=("sic_alpha", "sic_beta"),
        reason_codes=("match.none",),
        fail_closed=True,
    )


def _routing_blocked(
    *,
    migration_mode: MigrationMode,
    ambiguity: AmbiguityEvidence | None = None,
    no_match: NoMatchEvidence | None = None,
) -> RuntimePluginReceipt:
    return _runtime(
        migration_mode=migration_mode,
        outcome=RuntimeOutcome.BLOCKED,
        authoritative_path=(
            "legacy" if migration_mode is MigrationMode.SHADOW else "plugin"
        ),
        selected=False,
        ambiguity=ambiguity,
        no_match=no_match,
    )


SIDE_EFFECT_OBSERVATIONS = (
    ("filesystem_read_count", 1),
    ("filesystem_write_count", 1),
    ("environment_read_count", 1),
    ("process_launch_count", 1),
    ("network_request_count", 1),
    ("gui_action_count", 1),
    ("revision_write_count", 1),
    ("input_mutated", True),
    ("hidden_reference_access", True),
    ("wall_clock_read", True),
    ("randomness_used", True),
    ("undeclared_reference_source_ids", ("source.hidden",)),
)


@pytest.mark.parametrize(("field", "observed"), SIDE_EFFECT_OBSERVATIONS)
def test_every_observed_side_effect_is_impure_and_fails_completed_stage(
    field: str,
    observed,
) -> None:
    assert _pure_side_effects().is_pure is True
    side_effects = _pure_side_effects(**{field: observed})
    assert side_effects.is_pure is False

    with pytest.raises(ValidationError, match="pure side effects"):
        _stage(StageName.MATCH, side_effects=side_effects)

    failed = _stage(
        StageName.MATCH,
        status=StageStatus.FAILED,
        side_effects=side_effects,
    )
    assert failed.status is StageStatus.FAILED


def test_side_effect_receipt_is_closed_strict_and_nonnegative() -> None:
    payload = _pure_side_effects().model_dump(mode="python")
    with pytest.raises(ValidationError):
        SideEffectReceipt.model_validate({**payload, "filesystem_read_count": "0"})
    with pytest.raises(ValidationError):
        SideEffectReceipt.model_validate({**payload, "filesystem_read_count": -1})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SideEffectReceipt.model_validate({**payload, "filesystem_probe": True})


@pytest.mark.parametrize(
    "kind",
    (
        RuntimeIssueKind.UNSUPPORTED,
        RuntimeIssueKind.INVALID_INPUT,
        RuntimeIssueKind.NEEDS_USER_INPUT,
        RuntimeIssueKind.PREVIEW_WARNING,
    ),
)
def test_completed_stage_can_return_semantic_outcomes(kind: RuntimeIssueKind) -> None:
    receipt = _stage(StageName.MATCH, issues=(_issue(kind),))
    assert receipt.status is StageStatus.COMPLETED
    assert len(receipt.output_digests) == 1


def test_completed_and_failed_stage_truth_tables() -> None:
    with pytest.raises(ValidationError, match="internal_error"):
        _stage(
            StageName.MATCH,
            issues=(_issue(RuntimeIssueKind.INTERNAL_ERROR),),
        )
    with pytest.raises(ValidationError, match="exactly one"):
        _stage(StageName.MATCH, output_digests=())
    with pytest.raises(ValidationError, match="exactly one"):
        _stage(
            StageName.MATCH,
            output_digests=(_digest("MatchResult", "a"), _digest("MatchResult", "b")),
        )

    _stage(
        StageName.MATCH,
        status=StageStatus.FAILED,
        issues=(_issue(RuntimeIssueKind.INTERNAL_ERROR),),
    )
    with pytest.raises(ValidationError, match="internal_error or observed impurity"):
        _stage(StageName.MATCH, status=StageStatus.FAILED)
    with pytest.raises(ValidationError, match="must not contain an output"):
        _stage(
            StageName.MATCH,
            status=StageStatus.FAILED,
            output_digests=(_digest("MatchResult"),),
            issues=(_issue(RuntimeIssueKind.INTERNAL_ERROR),),
        )


def test_stage_contract_names_and_optional_model_state_are_exact() -> None:
    for stage in StageName:
        _stage(stage)
    _stage(StageName.PLAN, include_model_state=True)

    with pytest.raises(ValidationError, match="input digest bindings"):
        _stage(StageName.MATCH, input_digests=(_digest("ModelState"),))
    with pytest.raises(ValidationError, match="input digest bindings"):
        _stage(
            StageName.PLAN,
            input_digests=(_digest("ModelState"), _intent_digest()),
        )
    with pytest.raises(ValidationError, match="output digest binding"):
        _stage(
            StageName.BUILD,
            output_digests=(_digest("DomainValidationReport"),),
        )


def test_stage_model_spec_and_patch_use_adapter_contract_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_version = "9.8.7"
    monkeypatch.setattr(
        receipt_models,
        "ADAPTER_CONTRACT_VERSION",
        adapter_version,
    )
    model = _digest(
        "ModelSpec",
        contract_version=adapter_version,
    )
    patch = _digest(
        "SemanticPatch",
        contract_version=adapter_version,
    )

    _stage(StageName.BUILD, output_digests=(model,))
    _stage(StageName.BUILD, output_digests=(patch,))
    _stage(StageName.VALIDATE, input_digests=(model,))
    _artifact(BuildOutputKind.MODEL_SPEC, digest=model)
    _artifact(BuildOutputKind.SEMANTIC_PATCH, digest=patch)

    with pytest.raises(ValidationError, match=adapter_version):
        _stage(
            StageName.VALIDATE,
            input_digests=(
                _digest(
                    "ModelSpec",
                    contract_version=RUNTIME_CONTRACT_VERSION,
                ),
            ),
        )
    with pytest.raises(ValidationError, match=RUNTIME_CONTRACT_VERSION):
        _stage(
            StageName.MATCH,
            output_digests=(
                _digest("MatchResult", contract_version=adapter_version),
            ),
        )


def test_emitted_artifact_kind_is_digest_bound_and_payload_free() -> None:
    _artifact(BuildOutputKind.MODEL_SPEC)
    _artifact(BuildOutputKind.SEMANTIC_PATCH)
    with pytest.raises(ValidationError):
        EmittedArtifactReceipt(
            kind=BuildOutputKind.MODEL_SPEC,
            digest=_digest("SemanticPatch"),
        )
    payload = _artifact().model_dump(mode="json")
    payload["payload"] = {"model": "forbidden"}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EmittedArtifactReceipt.model_validate_json(json.dumps(payload))


def test_shadow_comparison_truth_table_and_digest_bindings() -> None:
    _shadow_comparison()
    _shadow_comparison(
        equivalent=False,
        differences=(_difference(),),
    )
    with pytest.raises(ValidationError):
        _shadow_comparison(equivalent=False)
    with pytest.raises(ValidationError):
        _shadow_comparison(
            equivalent=True,
            differences=(_difference(),),
        )

    payload = _shadow_comparison().model_dump(mode="json")
    payload["normalized_intent_digest"] = _digest("ModelingPlan").model_dump(
        mode="json"
    )
    with pytest.raises(ValidationError):
        ShadowComparisonReceipt.model_validate_json(json.dumps(payload))
    payload = _shadow_comparison().model_dump(mode="json")
    payload["shadow_plan_digest"] = _intent_digest().model_dump(mode="json")
    with pytest.raises(ValidationError):
        ShadowComparisonReceipt.model_validate_json(json.dumps(payload))


def test_all_receipt_contract_versions_are_required() -> None:
    receipts = (
        _stage(StageName.MATCH),
        _shadow_comparison(),
        _off_receipt(),
    )
    for receipt in receipts:
        assert "contract_version" in type(receipt).model_json_schema()["required"]
        payload = receipt.model_dump(mode="json")
        payload.pop("contract_version")
        with pytest.raises(ValidationError):
            type(receipt).model_validate_json(json.dumps(payload))


def test_runtime_mode_truth_tables_accept_valid_receipts() -> None:
    receipts = [
        _off_receipt(),
        _shadow_completed(),
        _shadow_completed(current_revision=_revision()),
        _active_completed(),
        _active_completed(current_revision=_revision()),
        _active_completed(
            match_issues=(_issue(RuntimeIssueKind.PREVIEW_WARNING),),
            runtime_issues=(_issue(RuntimeIssueKind.PREVIEW_WARNING),),
        ),
        _failed_receipt(migration_mode=MigrationMode.SHADOW),
        _failed_receipt(migration_mode=MigrationMode.ACTIVE),
    ]
    for kind in (
        RuntimeIssueKind.UNSUPPORTED,
        RuntimeIssueKind.INVALID_INPUT,
        RuntimeIssueKind.NEEDS_USER_INPUT,
    ):
        receipts.append(_semantic_blocked(kind, migration_mode=MigrationMode.SHADOW))
        receipts.append(_semantic_blocked(kind, migration_mode=MigrationMode.ACTIVE))
    for mode in (MigrationMode.SHADOW, MigrationMode.ACTIVE):
        receipts.append(_routing_blocked(migration_mode=mode, ambiguity=_ambiguity()))
        receipts.append(_routing_blocked(migration_mode=mode, no_match=_no_match()))

    for receipt in receipts:
        restored = RuntimePluginReceipt.model_validate_json(receipt.model_dump_json())
        assert restored == receipt


@pytest.mark.parametrize(
    ("outcome", "issues"),
    (
        (RuntimeOutcome.COMPLETED, ()),
        (
            RuntimeOutcome.COMPLETED,
            (_issue(RuntimeIssueKind.PREVIEW_WARNING),),
        ),
        (
            RuntimeOutcome.BLOCKED,
            (_issue(RuntimeIssueKind.UNSUPPORTED),),
        ),
        (
            RuntimeOutcome.BLOCKED,
            (_issue(RuntimeIssueKind.INVALID_INPUT),),
        ),
        (
            RuntimeOutcome.BLOCKED,
            (_issue(RuntimeIssueKind.NEEDS_USER_INPUT),),
        ),
        (
            RuntimeOutcome.FAILED,
            (_issue(RuntimeIssueKind.INTERNAL_ERROR),),
        ),
    ),
)
def test_off_mode_outcome_issue_truth_table_accepts_consistent_receipts(
    outcome: RuntimeOutcome,
    issues: tuple[RuntimeIssue, ...],
) -> None:
    receipt = _off_receipt(outcome=outcome, issues=issues)
    assert receipt.outcome is outcome


@pytest.mark.parametrize(
    ("outcome", "issues"),
    (
        (
            RuntimeOutcome.COMPLETED,
            (_issue(RuntimeIssueKind.UNSUPPORTED),),
        ),
        (
            RuntimeOutcome.COMPLETED,
            (_issue(RuntimeIssueKind.INTERNAL_ERROR),),
        ),
        (RuntimeOutcome.BLOCKED, ()),
        (
            RuntimeOutcome.BLOCKED,
            (_issue(RuntimeIssueKind.PREVIEW_WARNING),),
        ),
        (
            RuntimeOutcome.BLOCKED,
            (
                _issue(RuntimeIssueKind.UNSUPPORTED),
                _issue(RuntimeIssueKind.INTERNAL_ERROR),
            ),
        ),
        (RuntimeOutcome.FAILED, ()),
        (
            RuntimeOutcome.FAILED,
            (_issue(RuntimeIssueKind.INVALID_INPUT),),
        ),
        (
            RuntimeOutcome.FAILED,
            (_issue(RuntimeIssueKind.PREVIEW_WARNING),),
        ),
    ),
)
def test_off_mode_outcome_issue_truth_table_rejects_inconsistent_receipts(
    outcome: RuntimeOutcome,
    issues: tuple[RuntimeIssue, ...],
) -> None:
    with pytest.raises(ValidationError):
        _off_receipt(outcome=outcome, issues=issues)


def test_completed_stage_semantic_block_requires_blocked_runtime_outcome() -> None:
    for kind in (
        RuntimeIssueKind.UNSUPPORTED,
        RuntimeIssueKind.INVALID_INPUT,
        RuntimeIssueKind.NEEDS_USER_INPUT,
    ):
        with pytest.raises(ValidationError, match="blocking issues"):
            _active_completed(match_issues=(_issue(kind),))
    with pytest.raises(ValidationError, match="semantic block evidence"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=True,
            stage_receipts=(
                _stage(
                    StageName.MATCH,
                    issues=(_issue(RuntimeIssueKind.PREVIEW_WARNING),),
                ),
            ),
        )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda payload: payload.update({"authoritative_path": "plugin"}),
        lambda payload: payload.update({"selected_plugin_id": PLUGIN_ID}),
        lambda payload: payload.update({"plan_digest": _plan_digest().model_dump(mode="json")}),
        lambda payload: payload.update({"stage_receipts": [_stage(StageName.MATCH).model_dump(mode="json")]}),
    ),
)
def test_off_mode_rejects_plugin_runtime_state(mutator) -> None:
    payload = _off_receipt().model_dump(mode="json")
    mutator(payload)
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(payload))


def test_shadow_and_active_mode_specific_rejections() -> None:
    shadow = _shadow_completed().model_dump(mode="json")
    shadow["authoritative_path"] = "plugin"
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(shadow))

    shadow = _shadow_completed().model_dump(mode="json")
    shadow["emitted_artifact"] = _artifact().model_dump(mode="json")
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(shadow))

    shadow = _shadow_completed().model_dump(mode="json")
    shadow["shadow_comparison"] = None
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(shadow))

    active = _active_completed().model_dump(mode="json")
    active["authoritative_path"] = "legacy"
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(active))

    active = _active_completed().model_dump(mode="json")
    active["stage_receipts"].pop()
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(active))

    active = _active_completed().model_dump(mode="json")
    active["emitted_artifact"] = None
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(active))


def test_blocked_and_failed_terminal_status_rules() -> None:
    with pytest.raises(ValidationError, match="terminal failed stage"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.FAILED,
            authoritative_path="plugin",
            selected=True,
            stage_receipts=(_stage(StageName.MATCH),),
        )
    with pytest.raises(ValidationError, match="completed terminal stage"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=True,
            stage_receipts=(
                _stage(
                    StageName.MATCH,
                    status=StageStatus.FAILED,
                    issues=(_issue(RuntimeIssueKind.INTERNAL_ERROR),),
                ),
            ),
            issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
        )
    with pytest.raises(ValidationError, match="blocked routing decision"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=False,
            stage_receipts=(_stage(StageName.MATCH),),
            ambiguity=_ambiguity(),
        )


def test_stage_receipts_must_be_an_ordered_prefix_without_duplicates() -> None:
    for stages, error in (
        ((_stage(StageName.PLAN),), "ordered stage prefix"),
        (
            (_stage(StageName.MATCH), _stage(StageName.MATCH)),
            "duplicate values",
        ),
        (
            (_stage(StageName.MATCH), _stage(StageName.BUILD)),
            "ordered stage prefix",
        ),
    ):
        with pytest.raises(ValidationError, match=error):
            _runtime(
                migration_mode=MigrationMode.ACTIVE,
                outcome=RuntimeOutcome.BLOCKED,
                authoritative_path="plugin",
                selected=True,
                stage_receipts=stages,
                issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
            )


def test_routing_and_selection_evidence_truth_tables() -> None:
    forced = ForcedSelectionEvidence(
        requested_plugin_id=PLUGIN_ID,
        capability_match=True,
        reason="Explicit reviewed selection.",
    )
    fallback = FallbackEvidence(
        from_plugin_id="sic_primary",
        to_plugin_id=PLUGIN_ID,
        reason_code="fallback.reviewed",
        target_independently_matched=True,
    )
    _runtime(
        migration_mode=MigrationMode.ACTIVE,
        outcome=RuntimeOutcome.BLOCKED,
        authoritative_path="plugin",
        selected=True,
        stage_receipts=(
            _stage(
                StageName.MATCH,
                issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
            ),
        ),
        forced_selection=forced,
    )
    _runtime(
        migration_mode=MigrationMode.ACTIVE,
        outcome=RuntimeOutcome.BLOCKED,
        authoritative_path="plugin",
        selected=True,
        stage_receipts=(
            _stage(
                StageName.MATCH,
                issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
            ),
        ),
        fallback=fallback,
    )

    with pytest.raises(ValidationError, match="mutually exclusive"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=True,
            stage_receipts=(
                _stage(
                    StageName.MATCH,
                    issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
                ),
            ),
            forced_selection=forced,
            fallback=fallback,
        )
    with pytest.raises(ValidationError, match="forced selection"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=True,
            stage_receipts=(
                _stage(
                    StageName.MATCH,
                    issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
                ),
            ),
            forced_selection=ForcedSelectionEvidence(
                requested_plugin_id="sic_other",
                capability_match=True,
                reason="Explicit reviewed selection.",
            ),
        )
    with pytest.raises(ValidationError, match="fallback target"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=True,
            stage_receipts=(
                _stage(
                    StageName.MATCH,
                    issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
                ),
            ),
            fallback=FallbackEvidence(
                from_plugin_id="sic_primary",
                to_plugin_id="sic_other",
                reason_code="fallback.reviewed",
                target_independently_matched=True,
            ),
        )

    with pytest.raises(ValidationError, match="mutually exclusive"):
        _routing_blocked(
            migration_mode=MigrationMode.ACTIVE,
            ambiguity=_ambiguity(),
            no_match=_no_match(),
        )
    with pytest.raises(ValidationError, match="no selected plugin"):
        _runtime(
            migration_mode=MigrationMode.ACTIVE,
            outcome=RuntimeOutcome.BLOCKED,
            authoritative_path="plugin",
            selected=True,
            ambiguity=_ambiguity(),
        )


def test_semantic_patch_may_validate_a_separately_materialized_model_spec() -> None:
    current_revision = _revision()
    artifact = _artifact(BuildOutputKind.SEMANTIC_PATCH)
    receipt = _runtime(
        migration_mode=MigrationMode.ACTIVE,
        outcome=RuntimeOutcome.COMPLETED,
        authoritative_path="plugin",
        selected=True,
        current_revision=current_revision,
        plan_digest=_plan_digest(),
        emitted_artifact=artifact,
        stage_receipts=(
            _stage(StageName.MATCH),
            _stage(StageName.PLAN, include_model_state=True),
            _stage(StageName.BUILD, output_digests=(artifact.digest,)),
            _stage(
                StageName.VALIDATE,
                input_digests=(_model_digest("materialized-patch-result"),),
            ),
        ),
    )
    assert receipt.emitted_artifact == artifact


def _assert_runtime_mutation_is_invalid(
    receipt: RuntimePluginReceipt,
    mutator,
) -> None:
    payload = copy.deepcopy(receipt.model_dump(mode="json"))
    mutator(payload)
    with pytest.raises(ValidationError):
        RuntimePluginReceipt.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value.update(
            {"normalized_intent_digest": _intent_digest("other").model_dump(mode="json")}
        ),
        lambda value: value["stage_receipts"][0].update({"plugin_id": "sic_other"}),
        lambda value: value["stage_receipts"][1].update(
            {"plugin_contract_version": "2.0.0"}
        ),
        lambda value: value["stage_receipts"][2].update(
            {"plugin_implementation_version": "2.0.0"}
        ),
        lambda value: value["stage_receipts"][0]["input_digests"].__setitem__(
            0, _intent_digest("other").model_dump(mode="json")
        ),
        lambda value: value["stage_receipts"][1]["input_digests"].__setitem__(
            0, _intent_digest("other").model_dump(mode="json")
        ),
        lambda value: value["stage_receipts"][1]["input_digests"].append(
            _digest("ModelState").model_dump(mode="json")
        ),
        lambda value: value.update(
            {"plan_digest": _plan_digest("other").model_dump(mode="json")}
        ),
        lambda value: value["stage_receipts"][1]["output_digests"].__setitem__(
            0, _plan_digest("other").model_dump(mode="json")
        ),
        lambda value: value["stage_receipts"][2]["input_digests"].__setitem__(
            0, _plan_digest("other").model_dump(mode="json")
        ),
        lambda value: value["stage_receipts"][2]["output_digests"].__setitem__(
            0, _model_digest("other").model_dump(mode="json")
        ),
        lambda value: value["emitted_artifact"].update(
            {"digest": _model_digest("other").model_dump(mode="json")}
        ),
        lambda value: value["stage_receipts"][3]["input_digests"].__setitem__(
            0, _model_digest("other").model_dump(mode="json")
        ),
        lambda value: value.update(
            {"current_revision": _revision().model_dump(mode="json")}
        ),
    ),
)
def test_active_receipt_rejects_every_repeated_binding_mismatch(mutator) -> None:
    _assert_runtime_mutation_is_invalid(_active_completed(), mutator)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value["shadow_comparison"].update(
            {"selected_plugin_id": "sic_other"}
        ),
        lambda value: value["shadow_comparison"].update(
            {"selected_plugin_contract_version": "2.0.0"}
        ),
        lambda value: value["shadow_comparison"].update(
            {"selected_plugin_implementation_version": "2.0.0"}
        ),
        lambda value: value["shadow_comparison"].update(
            {
                "normalized_intent_digest": _intent_digest("other").model_dump(
                    mode="json"
                )
            }
        ),
        lambda value: value["shadow_comparison"].update(
            {"current_revision": _revision().model_dump(mode="json")}
        ),
        lambda value: value["shadow_comparison"].update(
            {"shadow_plan_digest": _plan_digest("other").model_dump(mode="json")}
        ),
    ),
)
def test_shadow_receipt_rejects_every_repeated_binding_mismatch(mutator) -> None:
    _assert_runtime_mutation_is_invalid(_shadow_completed(), mutator)


def test_runtime_receipt_has_exact_fields_and_is_frozen() -> None:
    receipt = _active_completed()
    assert set(type(receipt).model_fields) == {
        "contract_version",
        "receipt_id",
        "migration_mode",
        "outcome",
        "authoritative_path",
        "normalized_intent_digest",
        "current_revision",
        "selected_plugin_id",
        "selected_plugin_contract_version",
        "selected_plugin_implementation_version",
        "plan_digest",
        "emitted_artifact",
        "stage_receipts",
        "ambiguity",
        "no_match",
        "forced_selection",
        "fallback",
        "shadow_comparison",
        "issues",
    }
    with pytest.raises(ValidationError, match="frozen_instance"):
        receipt.outcome = RuntimeOutcome.BLOCKED


def test_receipt_json_roundtrip() -> None:
    values = (
        _pure_side_effects(),
        _artifact(),
        _stage(StageName.MATCH),
        _difference(),
        _shadow_comparison(),
        _off_receipt(),
        _shadow_completed(current_revision=_revision()),
        _active_completed(current_revision=_revision()),
    )
    for value in values:
        restored = type(value).model_validate_json(value.model_dump_json())
        assert restored == value
