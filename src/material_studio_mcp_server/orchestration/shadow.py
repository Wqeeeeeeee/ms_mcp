"""Bounded off/shadow migration evaluation for runtime domain plugins."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from material_studio_mcp_server.runtime import (
    RUNTIME_CONTRACT_VERSION,
    ContractDigest,
    DecisionDifference,
    MigrationMode,
    ModelState,
    ModelingIntent,
    NoMatchEvidence,
    RevisionIdentity,
    RuntimeIssue,
    RuntimeIssueKind,
    RuntimeOutcome,
    RuntimePluginReceipt,
    ShadowComparisonReceipt,
    canonical_json_bytes,
    contract_digest,
)

from .capability_registry import CapabilityRegistry
from .router import (
    FallbackRequest,
    ForcedSelectionRequest,
    RuntimeRouter,
    SideEffectProbe,
)


@dataclass(frozen=True, slots=True)
class ShadowComparisonObservations:
    """Explicit legacy-versus-shadow differences supplied by the caller."""

    differences: tuple[DecisionDifference, ...] = ()


class ShadowRouter:
    """Evaluate migration mode without taking authoritative model actions."""

    __slots__ = ("_router",)

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        side_effect_probe: SideEffectProbe | None = None,
    ) -> None:
        self._router = RuntimeRouter(
            registry,
            side_effect_probe=side_effect_probe,
        )

    def evaluate(
        self,
        intent: ModelingIntent,
        authoritative_legacy_decision_digest: ContractDigest,
        current_state: ModelState | None = None,
        *,
        migration_mode: MigrationMode = MigrationMode.SHADOW,
        differences: tuple[DecisionDifference, ...] = (),
        comparison_observations: ShadowComparisonObservations | None = None,
        forced_selection: ForcedSelectionRequest | None = None,
        fallback: FallbackRequest | None = None,
    ) -> RuntimePluginReceipt:
        _require_evaluation_inputs(
            intent,
            authoritative_legacy_decision_digest,
            current_state,
            migration_mode,
        )
        normalized_intent_digest = contract_digest(
            intent,
            contract_name="ModelingIntent",
            contract_version=RUNTIME_CONTRACT_VERSION,
        )
        current_revision = _revision_identity(current_state)
        state_issues = _state_relation_issues(intent, current_state)

        if migration_mode is MigrationMode.OFF:
            return _runtime_receipt(
                authoritative_legacy_decision_digest,
                contract_version=RUNTIME_CONTRACT_VERSION,
                migration_mode=MigrationMode.OFF,
                outcome=(
                    RuntimeOutcome.BLOCKED
                    if state_issues
                    else RuntimeOutcome.COMPLETED
                ),
                authoritative_path="legacy",
                normalized_intent_digest=normalized_intent_digest,
                current_revision=current_revision,
                selected_plugin_id=None,
                selected_plugin_contract_version=None,
                selected_plugin_implementation_version=None,
                plan_digest=None,
                emitted_artifact=None,
                stage_receipts=(),
                ambiguity=None,
                no_match=None,
                forced_selection=None,
                fallback=None,
                shadow_comparison=None,
                issues=state_issues,
            )

        if migration_mode is MigrationMode.ACTIVE:
            issue = RuntimeIssue(
                kind=RuntimeIssueKind.UNSUPPORTED,
                code="router.active_mode_unavailable",
                message="Active plugin routing is not enabled.",
                field_path="$.migration_mode",
            )
            return _runtime_receipt(
                authoritative_legacy_decision_digest,
                contract_version=RUNTIME_CONTRACT_VERSION,
                migration_mode=MigrationMode.ACTIVE,
                outcome=RuntimeOutcome.BLOCKED,
                authoritative_path="plugin",
                normalized_intent_digest=normalized_intent_digest,
                current_revision=current_revision,
                selected_plugin_id=None,
                selected_plugin_contract_version=None,
                selected_plugin_implementation_version=None,
                plan_digest=None,
                emitted_artifact=None,
                stage_receipts=(),
                ambiguity=None,
                no_match=NoMatchEvidence(
                    evaluated_plugin_ids=(),
                    reason_codes=("router.active_mode_unavailable",),
                    fail_closed=True,
                ),
                forced_selection=None,
                fallback=None,
                shadow_comparison=None,
                issues=(*state_issues, issue),
            )

        observed_differences = _resolve_differences(
            differences,
            comparison_observations,
        )
        routing = self._router.route(
            intent,
            current_state,
            forced_selection=forced_selection,
            fallback=fallback,
        )
        if routing.outcome is not RuntimeOutcome.COMPLETED:
            return _runtime_receipt(
                authoritative_legacy_decision_digest,
                contract_version=RUNTIME_CONTRACT_VERSION,
                migration_mode=MigrationMode.SHADOW,
                outcome=routing.outcome,
                authoritative_path="legacy",
                normalized_intent_digest=routing.normalized_intent_digest,
                current_revision=routing.current_revision,
                selected_plugin_id=routing.selected_plugin_id,
                selected_plugin_contract_version=(
                    routing.selected_plugin_contract_version
                ),
                selected_plugin_implementation_version=(
                    routing.selected_plugin_implementation_version
                ),
                plan_digest=None,
                emitted_artifact=None,
                stage_receipts=routing.stage_receipts,
                ambiguity=routing.ambiguity,
                no_match=routing.no_match,
                forced_selection=routing.forced_selection,
                fallback=routing.fallback,
                shadow_comparison=None,
                issues=routing.issues,
            )

        planning = self._router.plan_selected(routing, intent, current_state)
        stage_receipts = (*routing.stage_receipts, planning.stage_receipt)
        if planning.outcome is not RuntimeOutcome.COMPLETED:
            return _runtime_receipt(
                authoritative_legacy_decision_digest,
                contract_version=RUNTIME_CONTRACT_VERSION,
                migration_mode=MigrationMode.SHADOW,
                outcome=planning.outcome,
                authoritative_path="legacy",
                normalized_intent_digest=routing.normalized_intent_digest,
                current_revision=routing.current_revision,
                selected_plugin_id=routing.selected_plugin_id,
                selected_plugin_contract_version=(
                    routing.selected_plugin_contract_version
                ),
                selected_plugin_implementation_version=(
                    routing.selected_plugin_implementation_version
                ),
                plan_digest=planning.plan_digest,
                emitted_artifact=None,
                stage_receipts=stage_receipts,
                ambiguity=None,
                no_match=None,
                forced_selection=routing.forced_selection,
                fallback=routing.fallback,
                shadow_comparison=None,
                issues=routing.issues,
            )

        assert planning.plan_digest is not None
        assert routing.selected_plugin_id is not None
        assert routing.selected_plugin_contract_version is not None
        assert routing.selected_plugin_implementation_version is not None
        comparison = ShadowComparisonReceipt(
            contract_version=RUNTIME_CONTRACT_VERSION,
            selected_plugin_id=routing.selected_plugin_id,
            selected_plugin_contract_version=(
                routing.selected_plugin_contract_version
            ),
            selected_plugin_implementation_version=(
                routing.selected_plugin_implementation_version
            ),
            normalized_intent_digest=routing.normalized_intent_digest,
            current_revision=routing.current_revision,
            authoritative_legacy_decision_digest=(
                authoritative_legacy_decision_digest
            ),
            shadow_plan_digest=planning.plan_digest,
            equivalent=not observed_differences,
            differences=observed_differences,
        )
        return _runtime_receipt(
            authoritative_legacy_decision_digest,
            contract_version=RUNTIME_CONTRACT_VERSION,
            migration_mode=MigrationMode.SHADOW,
            outcome=RuntimeOutcome.COMPLETED,
            authoritative_path="legacy",
            normalized_intent_digest=routing.normalized_intent_digest,
            current_revision=routing.current_revision,
            selected_plugin_id=routing.selected_plugin_id,
            selected_plugin_contract_version=(
                routing.selected_plugin_contract_version
            ),
            selected_plugin_implementation_version=(
                routing.selected_plugin_implementation_version
            ),
            plan_digest=planning.plan_digest,
            emitted_artifact=None,
            stage_receipts=stage_receipts,
            ambiguity=None,
            no_match=None,
            forced_selection=routing.forced_selection,
            fallback=routing.fallback,
            shadow_comparison=comparison,
            issues=routing.issues,
        )

    def evaluate_off(
        self,
        intent: ModelingIntent,
        authoritative_legacy_decision_digest: ContractDigest,
        current_state: ModelState | None = None,
    ) -> RuntimePluginReceipt:
        return self.evaluate(
            intent,
            authoritative_legacy_decision_digest,
            current_state,
            migration_mode=MigrationMode.OFF,
        )

    def evaluate_shadow(
        self,
        intent: ModelingIntent,
        authoritative_legacy_decision_digest: ContractDigest,
        current_state: ModelState | None = None,
        *,
        differences: tuple[DecisionDifference, ...] = (),
        comparison_observations: ShadowComparisonObservations | None = None,
        forced_selection: ForcedSelectionRequest | None = None,
        fallback: FallbackRequest | None = None,
    ) -> RuntimePluginReceipt:
        return self.evaluate(
            intent,
            authoritative_legacy_decision_digest,
            current_state,
            migration_mode=MigrationMode.SHADOW,
            differences=differences,
            comparison_observations=comparison_observations,
            forced_selection=forced_selection,
            fallback=fallback,
        )


ShadowEvaluator = ShadowRouter


def evaluate_runtime_mode(
    registry: CapabilityRegistry,
    intent: ModelingIntent,
    authoritative_legacy_decision_digest: ContractDigest,
    current_state: ModelState | None = None,
    *,
    migration_mode: MigrationMode = MigrationMode.SHADOW,
    differences: tuple[DecisionDifference, ...] = (),
    comparison_observations: ShadowComparisonObservations | None = None,
    forced_selection: ForcedSelectionRequest | None = None,
    fallback: FallbackRequest | None = None,
    side_effect_probe: SideEffectProbe | None = None,
) -> RuntimePluginReceipt:
    """Stateless convenience wrapper around :class:`ShadowRouter`."""

    return ShadowRouter(
        registry,
        side_effect_probe=side_effect_probe,
    ).evaluate(
        intent,
        authoritative_legacy_decision_digest,
        current_state,
        migration_mode=migration_mode,
        differences=differences,
        comparison_observations=comparison_observations,
        forced_selection=forced_selection,
        fallback=fallback,
    )


def _require_evaluation_inputs(
    intent: ModelingIntent,
    legacy_digest: ContractDigest,
    current_state: ModelState | None,
    migration_mode: MigrationMode,
) -> None:
    if not isinstance(intent, ModelingIntent):
        raise TypeError("router.intent_type_invalid")
    if not isinstance(legacy_digest, ContractDigest):
        raise TypeError("router.legacy_decision_digest_type_invalid")
    if current_state is not None and not isinstance(current_state, ModelState):
        raise TypeError("router.current_state_type_invalid")
    if not isinstance(migration_mode, MigrationMode):
        raise TypeError("router.migration_mode_type_invalid")


def _resolve_differences(
    differences: tuple[DecisionDifference, ...],
    observations: ShadowComparisonObservations | None,
) -> tuple[DecisionDifference, ...]:
    if type(differences) is not tuple:
        raise TypeError("router.shadow_differences_must_be_tuple")
    if observations is not None and not isinstance(
        observations, ShadowComparisonObservations
    ):
        raise TypeError("router.shadow_observations_type_invalid")
    if observations is not None and differences:
        raise ValueError("router.shadow_observations_conflict")
    selected = observations.differences if observations is not None else differences
    validated: list[DecisionDifference] = []
    for difference in selected:
        if not isinstance(difference, DecisionDifference):
            raise TypeError("router.shadow_difference_type_invalid")
        validated.append(difference)
    payloads = tuple(canonical_json_bytes(difference) for difference in validated)
    if len(set(payloads)) != len(payloads):
        raise ValueError("router.shadow_duplicate_decision_difference")
    return tuple(
        difference
        for _, difference in sorted(
            zip(payloads, validated),
            key=lambda item: item[0],
        )
    )


def _revision_identity(current_state: ModelState | None) -> RevisionIdentity | None:
    if current_state is None:
        return None
    return RevisionIdentity(
        project_id=current_state.project_id,
        revision=current_state.revision,
        model_spec_digest=current_state.model_spec_digest,
    )


def _state_relation_issues(
    intent: ModelingIntent,
    current_state: ModelState | None,
) -> tuple[RuntimeIssue, ...]:
    if intent.requires_current_model and current_state is None:
        return (
            RuntimeIssue(
                kind=RuntimeIssueKind.NEEDS_USER_INPUT,
                code="router.current_model_required",
                message="The intent requires an immutable current model.",
                field_path="$.current_state",
            ),
        )
    if current_state is not None and current_state.model_kind is not intent.model_kind:
        return (
            RuntimeIssue(
                kind=RuntimeIssueKind.INVALID_INPUT,
                code="router.current_model_kind_mismatch",
                message="Current model kind does not match the intent.",
                field_path="$.current_state.model_kind",
            ),
        )
    return ()


def _runtime_receipt(
    authoritative_legacy_decision_digest: ContractDigest,
    **fields,
) -> RuntimePluginReceipt:
    identifier_payload = {
        "identifier_contract": "RuntimePluginReceiptId",
        "identifier_contract_version": RUNTIME_CONTRACT_VERSION,
        "authoritative_legacy_decision_digest": (
            authoritative_legacy_decision_digest
        ),
        "receipt_fields": fields,
    }
    receipt_id = "runtime.receipt." + hashlib.sha256(
        canonical_json_bytes(identifier_payload)
    ).hexdigest()
    return RuntimePluginReceipt(receipt_id=receipt_id, **fields)


__all__ = [
    "ShadowComparisonObservations",
    "ShadowEvaluator",
    "ShadowRouter",
    "evaluate_runtime_mode",
]
