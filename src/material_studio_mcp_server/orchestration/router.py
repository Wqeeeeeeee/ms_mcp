"""Deterministic capability prefiltering and runtime plugin routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, TypeAlias

from material_studio_mcp_server.runtime import (
    RUNTIME_CONTRACT_VERSION,
    AmbiguityEvidence,
    BuildOutputKind,
    ContractDigest,
    FallbackEvidence,
    ForcedSelectionEvidence,
    MatchKind,
    MatchResult,
    ModelKind,
    ModelState,
    ModelingIntent,
    ModelingPlan,
    NoMatchEvidence,
    PluginStageReceipt,
    RevisionIdentity,
    RuntimeIssue,
    RuntimeIssueKind,
    RuntimeOutcome,
    SideEffectReceipt,
    StageName,
    StageStatus,
    canonical_json_bytes,
    contract_digest,
)

from .capability_registry import CapabilityRegistry, _RegistryEntry


SideEffectProbe: TypeAlias = Callable[[str, StageName], SideEffectReceipt]
PluginIdentity: TypeAlias = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class ForcedSelectionRequest:
    plugin_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class FallbackRequest:
    from_plugin_id: str
    to_plugin_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class ResolvedRoutingInputs:
    atom_count: int | None
    periodicity_dimension: int | None
    requires_calculation_plan: bool


@dataclass(frozen=True, slots=True)
class CandidatePrefilter:
    plugin_id: str
    eligible: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrefilterResult:
    resolved_inputs: ResolvedRoutingInputs | None
    candidates: tuple[CandidatePrefilter, ...]
    issues: tuple[RuntimeIssue, ...]

    @property
    def eligible_plugin_ids(self) -> tuple[str, ...]:
        return tuple(
            candidate.plugin_id for candidate in self.candidates if candidate.eligible
        )


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    plugin_id: str
    match_result: MatchResult | None
    failure_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    outcome: RuntimeOutcome
    normalized_intent_digest: ContractDigest
    current_revision: RevisionIdentity | None
    selected_plugin_id: str | None
    selected_plugin_contract_version: str | None
    selected_plugin_implementation_version: str | None
    match_result: MatchResult | None
    stage_receipts: tuple[PluginStageReceipt, ...]
    prefilter: PrefilterResult
    candidate_evaluations: tuple[CandidateEvaluation, ...]
    ambiguity: AmbiguityEvidence | None
    no_match: NoMatchEvidence | None
    forced_selection: ForcedSelectionEvidence | None
    fallback: FallbackEvidence | None
    issues: tuple[RuntimeIssue, ...]

    @property
    def selected(self) -> bool:
        return self.selected_plugin_id is not None


@dataclass(frozen=True, slots=True)
class PlanningDecision:
    outcome: RuntimeOutcome
    plan: ModelingPlan | None
    plan_digest: ContractDigest | None
    stage_receipt: PluginStageReceipt
    issues: tuple[RuntimeIssue, ...]


@dataclass(frozen=True, slots=True)
class _MatchCall:
    entry: _RegistryEntry
    result: MatchResult | None
    receipt: PluginStageReceipt
    failure_reason_codes: tuple[str, ...]

    @property
    def failed(self) -> bool:
        return self.receipt.status is StageStatus.FAILED


@dataclass(frozen=True, slots=True)
class _ProbeObservation:
    side_effects: SideEffectReceipt
    issues: tuple[RuntimeIssue, ...]


class RuntimeRouter:
    """Pure router over one immutable :class:`CapabilityRegistry`."""

    __slots__ = ("_registry", "_side_effect_probe")

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        side_effect_probe: SideEffectProbe | None = None,
    ) -> None:
        if side_effect_probe is not None and not callable(side_effect_probe):
            raise TypeError("router.side_effect_probe_not_callable")
        self._registry = registry
        self._side_effect_probe = side_effect_probe

    def prefilter(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None = None,
    ) -> PrefilterResult:
        _require_typed_inputs(intent, current_state)
        issues = list(_state_issues(intent, current_state))
        resolved, parameter_issues = _resolve_routing_inputs(intent, current_state)
        issues.extend(parameter_issues)
        if issues:
            return PrefilterResult(
                resolved_inputs=None,
                candidates=(),
                issues=_unique_issues(tuple(issues)),
            )

        assert resolved is not None
        candidates = tuple(
            CandidatePrefilter(
                plugin_id=entry.manifest.plugin_id,
                eligible=not reasons,
                reason_codes=reasons,
            )
            for entry in self._registry._routing_entries()
            for reasons in (_prefilter_reasons(entry, intent, current_state, resolved),)
        )
        return PrefilterResult(
            resolved_inputs=resolved,
            candidates=candidates,
            issues=(),
        )

    def filter_candidates(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None = None,
    ) -> PrefilterResult:
        return self.prefilter(intent, current_state)

    def route(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None = None,
        *,
        forced_selection: ForcedSelectionRequest | None = None,
        fallback: FallbackRequest | None = None,
    ) -> RoutingDecision:
        _require_typed_inputs(intent, current_state)
        intent_digest = contract_digest(
            intent,
            contract_name="ModelingIntent",
            contract_version=RUNTIME_CONTRACT_VERSION,
        )
        current_revision = _revision_identity(current_state)

        option_issues = _selection_option_issues(forced_selection, fallback)
        if option_issues:
            return _unselected_decision(
                outcome=RuntimeOutcome.BLOCKED,
                intent_digest=intent_digest,
                current_revision=current_revision,
                prefilter=PrefilterResult(None, (), option_issues),
                reason_codes=tuple(issue.code for issue in option_issues),
                issues=option_issues,
            )

        prefilter = self.prefilter(intent, current_state)
        if prefilter.issues:
            return _unselected_decision(
                outcome=RuntimeOutcome.BLOCKED,
                intent_digest=intent_digest,
                current_revision=current_revision,
                prefilter=prefilter,
                reason_codes=tuple(issue.code for issue in prefilter.issues),
                issues=prefilter.issues,
            )

        eligible_ids = prefilter.eligible_plugin_ids
        entries_by_id = {
            entry.manifest.plugin_id: entry
            for entry in self._registry._routing_entries()
        }
        target_ids, target_failure = _routing_targets(
            eligible_ids=eligible_ids,
            registered_ids=tuple(entries_by_id),
            prefilter=prefilter,
            forced_selection=forced_selection,
            fallback=fallback,
        )
        if target_failure:
            return _unselected_decision(
                outcome=RuntimeOutcome.BLOCKED,
                intent_digest=intent_digest,
                current_revision=current_revision,
                prefilter=prefilter,
                reason_codes=target_failure,
                issues=(),
            )

        calls = tuple(
            self._invoke_match(entries_by_id[plugin_id], intent, intent_digest)
            for plugin_id in target_ids
        )
        evaluations = tuple(
            CandidateEvaluation(
                plugin_id=call.entry.manifest.plugin_id,
                match_result=call.result,
                failure_reason_codes=call.failure_reason_codes,
            )
            for call in calls
        )
        failed_calls = tuple(call for call in calls if call.failed)
        if failed_calls:
            failed = failed_calls[0]
            return _failed_routing_decision(
                entry=failed.entry,
                intent_digest=intent_digest,
                current_revision=current_revision,
                receipt=failed.receipt,
                prefilter=prefilter,
                evaluations=evaluations,
            )

        if forced_selection is not None:
            return _forced_decision(
                call=calls[0],
                request=forced_selection,
                intent_digest=intent_digest,
                current_revision=current_revision,
                prefilter=prefilter,
                evaluations=evaluations,
            )
        if fallback is not None:
            return _fallback_decision(
                call=calls[0],
                request=fallback,
                intent_digest=intent_digest,
                current_revision=current_revision,
                prefilter=prefilter,
                evaluations=evaluations,
            )

        matching_calls = tuple(
            call
            for call in calls
            if call.result is not None and call.result.kind is not MatchKind.NONE
        )
        if not matching_calls:
            reason_codes = tuple(
                reason
                for call in calls
                if call.result is not None
                for reason in call.result.reason_codes
            )
            prefilter_reasons = tuple(
                reason
                for candidate in prefilter.candidates
                for reason in candidate.reason_codes
            )
            return _unselected_decision(
                outcome=RuntimeOutcome.BLOCKED,
                intent_digest=intent_digest,
                current_revision=current_revision,
                prefilter=prefilter,
                reason_codes=(*reason_codes, *prefilter_reasons, "router.no_match"),
                issues=_match_result_issues(calls),
                evaluations=evaluations,
                evaluated_plugin_ids=tuple(call.entry.manifest.plugin_id for call in calls),
            )

        ranked = tuple(
            (call, _semantic_rank(call)) for call in matching_calls
        )
        best_rank = max(rank for _, rank in ranked)
        tied = tuple(call for call, rank in ranked if rank == best_rank)
        if len(tied) > 1:
            match_kind = tied[0].result.kind
            specificity = tied[0].result.specificity
            priority = tied[0].entry.manifest.routing.priority
            return RoutingDecision(
                outcome=RuntimeOutcome.BLOCKED,
                normalized_intent_digest=intent_digest,
                current_revision=current_revision,
                selected_plugin_id=None,
                selected_plugin_contract_version=None,
                selected_plugin_implementation_version=None,
                match_result=None,
                stage_receipts=(),
                prefilter=prefilter,
                candidate_evaluations=evaluations,
                ambiguity=AmbiguityEvidence(
                    tied_plugin_ids=tuple(
                        sorted(call.entry.manifest.plugin_id for call in tied)
                    ),
                    match_kind=match_kind,
                    specificity=specificity,
                    priority=priority,
                    fail_closed=True,
                ),
                no_match=None,
                forced_selection=None,
                fallback=None,
                issues=_match_result_issues(calls),
            )

        return _selected_decision(
            call=tied[0],
            intent_digest=intent_digest,
            current_revision=current_revision,
            prefilter=prefilter,
            evaluations=evaluations,
            forced_selection=None,
            fallback=None,
        )

    def select(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None = None,
        *,
        forced_selection: ForcedSelectionRequest | None = None,
        fallback: FallbackRequest | None = None,
    ) -> RoutingDecision:
        return self.route(
            intent,
            current_state,
            forced_selection=forced_selection,
            fallback=fallback,
        )

    def plan_selected(
        self,
        routing: RoutingDecision,
        intent: ModelingIntent,
        current_state: ModelState | None = None,
    ) -> PlanningDecision:
        _require_typed_inputs(intent, current_state)
        if routing.outcome is not RuntimeOutcome.COMPLETED or not routing.selected:
            raise ValueError("router.routing_not_plan_eligible")
        if routing.normalized_intent_digest != contract_digest(
            intent,
            contract_name="ModelingIntent",
            contract_version=RUNTIME_CONTRACT_VERSION,
        ):
            raise ValueError("router.routing_intent_binding_mismatch")
        if routing.current_revision != _revision_identity(current_state):
            raise ValueError("router.routing_state_binding_mismatch")

        entry = self._registry._entry_for(routing.selected_plugin_id or "")
        if entry is None:
            raise ValueError("router.selected_plugin_missing")

        input_digests = [routing.normalized_intent_digest]
        if current_state is not None:
            input_digests.append(
                contract_digest(
                    current_state,
                    contract_name="ModelState",
                    contract_version=RUNTIME_CONTRACT_VERSION,
                )
            )
        input_digest_tuple = tuple(input_digests)

        plugin_identity = _capture_plugin_identity(entry, StageName.PLAN)
        if isinstance(plugin_identity, RuntimeIssue):
            receipt = _failed_stage_receipt(
                entry,
                StageName.PLAN,
                input_digest_tuple,
                _zero_side_effect_receipt(),
                (plugin_identity,),
            )
            return PlanningDecision(
                outcome=RuntimeOutcome.FAILED,
                plan=None,
                plan_digest=None,
                stage_receipt=receipt,
                issues=(plugin_identity,),
            )

        before_digests = input_digest_tuple
        raw_plan: object | None = None
        call_issue: RuntimeIssue | None = None
        try:
            raw_plan = entry.plugin.plan(intent, current_state)
        except Exception:
            call_issue = _internal_issue(
                "router.plan_exception",
                "Plugin plan raised an exception.",
                "$.plan",
            )
        identity_after_call_issue = _identity_after_call_issue(
            entry,
            StageName.PLAN,
            plugin_identity,
        )

        input_mutated = _inputs_mutated(
            before_digests,
            (("ModelingIntent", intent),)
            + (
                (("ModelState", current_state),)
                if current_state is not None
                else ()
            ),
        )
        observation = self._observe_side_effects(
            entry.manifest.plugin_id,
            StageName.PLAN,
            input_mutated=input_mutated,
        )
        failure_issues = list(observation.issues)
        if call_issue is not None:
            failure_issues.append(call_issue)
        if identity_after_call_issue is not None:
            failure_issues.append(identity_after_call_issue)
        if input_mutated:
            failure_issues.append(
                _internal_issue(
                    "router.plan_input_mutated",
                    "Plugin plan mutated a typed input.",
                    "$.plan.inputs",
                )
            )
        if not observation.side_effects.is_pure:
            failure_issues.append(
                _internal_issue(
                    "router.plan_side_effect_observed",
                    "Plugin plan produced a non-pure side-effect observation.",
                    "$.plan.side_effects",
                )
            )

        plan: ModelingPlan | None = None
        if not failure_issues:
            try:
                if not isinstance(raw_plan, ModelingPlan):
                    raise TypeError
                plan = ModelingPlan.model_validate(raw_plan)
            except Exception:
                failure_issues.append(
                    _internal_issue(
                        "router.plan_output_invalid",
                        "Plugin plan returned an invalid ModelingPlan.",
                        "$.plan.output",
                    )
                )

        if plan is not None and any(
            issue.kind is RuntimeIssueKind.INTERNAL_ERROR for issue in plan.issues
        ):
            failure_issues.append(
                _internal_issue(
                    "router.plan_internal_error",
                    "Plugin plan reported an internal error.",
                    "$.plan.output.issues",
                )
            )

        if plan is not None and not failure_issues:
            binding_code = _plan_binding_error(
                plan,
                entry,
                routing,
                intent,
                current_state,
            )
            if binding_code is not None:
                failure_issues.append(
                    _internal_issue(
                        binding_code,
                        "Plugin plan output binding was inconsistent.",
                        "$.plan.output",
                    )
                )

        if plan is not None and not failure_issues:
            try:
                plan = _bind_selection_evidence(plan, routing)
            except Exception:
                failure_issues.append(
                    _internal_issue(
                        "router.plan_selection_evidence_mismatch",
                        "Plugin plan selection evidence was inconsistent.",
                        "$.plan.output",
                    )
                )

        if failure_issues:
            issues = _unique_issues(tuple(failure_issues))
            receipt = _failed_stage_receipt(
                entry,
                StageName.PLAN,
                input_digest_tuple,
                observation.side_effects,
                issues,
            )
            return PlanningDecision(
                outcome=RuntimeOutcome.FAILED,
                plan=None,
                plan_digest=None,
                stage_receipt=receipt,
                issues=issues,
            )

        assert plan is not None
        plan_digest = contract_digest(
            plan,
            contract_name="ModelingPlan",
            contract_version=RUNTIME_CONTRACT_VERSION,
        )
        stage_issues = list(plan.issues)
        if not plan.build_eligible and not any(issue.is_blocking for issue in stage_issues):
            if plan.questions:
                stage_issues.append(
                    RuntimeIssue(
                        kind=RuntimeIssueKind.NEEDS_USER_INPUT,
                        code="router.plan_questions_pending",
                        message="Plugin plan requires user input.",
                        field_path="$.plan.questions",
                    )
                )
            else:
                stage_issues.append(
                    RuntimeIssue(
                        kind=RuntimeIssueKind.UNSUPPORTED,
                        code="router.plan_not_build_eligible",
                        message="Plugin plan is not build eligible.",
                        field_path="$.plan",
                    )
                )
        receipt = PluginStageReceipt(
            contract_version=RUNTIME_CONTRACT_VERSION,
            stage=StageName.PLAN,
            status=StageStatus.COMPLETED,
            plugin_id=entry.manifest.plugin_id,
            plugin_contract_version=entry.manifest.contract_version,
            plugin_implementation_version=entry.manifest.implementation_version,
            input_digests=input_digest_tuple,
            output_digests=(plan_digest,),
            side_effects=observation.side_effects,
            issues=_unique_issues(tuple(stage_issues)),
        )
        outcome = (
            RuntimeOutcome.BLOCKED
            if not plan.build_eligible or any(issue.is_blocking for issue in receipt.issues)
            else RuntimeOutcome.COMPLETED
        )
        return PlanningDecision(
            outcome=outcome,
            plan=plan,
            plan_digest=plan_digest,
            stage_receipt=receipt,
            issues=receipt.issues,
        )

    def _invoke_match(
        self,
        entry: _RegistryEntry,
        intent: ModelingIntent,
        intent_digest: ContractDigest,
    ) -> _MatchCall:
        plugin_identity = _capture_plugin_identity(entry, StageName.MATCH)
        if isinstance(plugin_identity, RuntimeIssue):
            receipt = _failed_stage_receipt(
                entry,
                StageName.MATCH,
                (intent_digest,),
                _zero_side_effect_receipt(),
                (plugin_identity,),
            )
            return _MatchCall(entry, None, receipt, (plugin_identity.code,))

        raw_result: object | None = None
        call_issue: RuntimeIssue | None = None
        try:
            raw_result = entry.plugin.match(intent)
        except Exception:
            call_issue = _internal_issue(
                "router.match_exception",
                "Plugin match raised an exception.",
                "$.match",
            )
        identity_after_call_issue = _identity_after_call_issue(
            entry,
            StageName.MATCH,
            plugin_identity,
        )

        input_mutated = _inputs_mutated(
            (intent_digest,),
            (("ModelingIntent", intent),),
        )
        observation = self._observe_side_effects(
            entry.manifest.plugin_id,
            StageName.MATCH,
            input_mutated=input_mutated,
        )
        failure_issues = list(observation.issues)
        if call_issue is not None:
            failure_issues.append(call_issue)
        if identity_after_call_issue is not None:
            failure_issues.append(identity_after_call_issue)
        if input_mutated:
            failure_issues.append(
                _internal_issue(
                    "router.match_input_mutated",
                    "Plugin match mutated the ModelingIntent.",
                    "$.match.inputs",
                )
            )
        if not observation.side_effects.is_pure:
            failure_issues.append(
                _internal_issue(
                    "router.match_side_effect_observed",
                    "Plugin match produced a non-pure side-effect observation.",
                    "$.match.side_effects",
                )
            )

        result: MatchResult | None = None
        if not failure_issues:
            try:
                if not isinstance(raw_result, MatchResult):
                    raise TypeError
                result = MatchResult.model_validate(raw_result)
            except Exception:
                failure_issues.append(
                    _internal_issue(
                        "router.match_output_invalid",
                        "Plugin match returned an invalid MatchResult.",
                        "$.match.output",
                    )
                )

        if result is not None and result.plugin_id != entry.manifest.plugin_id:
            failure_issues.append(
                _internal_issue(
                    "router.match_plugin_id_mismatch",
                    "Plugin match returned a mismatched plugin identity.",
                    "$.match.output.plugin_id",
                )
            )
        if result is not None and any(
            issue.kind is RuntimeIssueKind.INTERNAL_ERROR for issue in result.issues
        ):
            failure_issues.append(
                _internal_issue(
                    "router.match_internal_error",
                    "Plugin match reported an internal error.",
                    "$.match.output.issues",
                )
            )

        if failure_issues:
            issues = _unique_issues(tuple(failure_issues))
            receipt = _failed_stage_receipt(
                entry,
                StageName.MATCH,
                (intent_digest,),
                observation.side_effects,
                issues,
            )
            return _MatchCall(
                entry=entry,
                result=None,
                receipt=receipt,
                failure_reason_codes=tuple(issue.code for issue in issues),
            )

        assert result is not None
        result_digest = contract_digest(
            result,
            contract_name="MatchResult",
            contract_version=RUNTIME_CONTRACT_VERSION,
        )
        receipt = PluginStageReceipt(
            contract_version=RUNTIME_CONTRACT_VERSION,
            stage=StageName.MATCH,
            status=StageStatus.COMPLETED,
            plugin_id=entry.manifest.plugin_id,
            plugin_contract_version=entry.manifest.contract_version,
            plugin_implementation_version=entry.manifest.implementation_version,
            input_digests=(intent_digest,),
            output_digests=(result_digest,),
            side_effects=observation.side_effects,
            issues=result.issues,
        )
        return _MatchCall(entry, result, receipt, ())

    def _observe_side_effects(
        self,
        plugin_id: str,
        stage: StageName,
        *,
        input_mutated: bool,
    ) -> _ProbeObservation:
        observed = _zero_side_effect_receipt()
        issues: tuple[RuntimeIssue, ...] = ()
        if self._side_effect_probe is not None:
            try:
                raw_observed = self._side_effect_probe(plugin_id, stage)
                if not isinstance(raw_observed, SideEffectReceipt):
                    raise TypeError
                observed = SideEffectReceipt.model_validate(raw_observed)
            except Exception:
                issues = (
                    _internal_issue(
                        f"router.{stage.value}_side_effect_probe_invalid",
                        "Side-effect probe did not return valid evidence.",
                        f"$.{stage.value}.side_effects",
                    ),
                )
                observed = _zero_side_effect_receipt()

        if input_mutated and not observed.input_mutated:
            payload = observed.model_dump()
            payload["input_mutated"] = True
            observed = SideEffectReceipt(**payload)
        return _ProbeObservation(observed, issues)


def _require_typed_inputs(
    intent: ModelingIntent,
    current_state: ModelState | None,
) -> None:
    if not isinstance(intent, ModelingIntent):
        raise TypeError("router.intent_type_invalid")
    if current_state is not None and not isinstance(current_state, ModelState):
        raise TypeError("router.current_state_type_invalid")


def _state_issues(
    intent: ModelingIntent,
    current_state: ModelState | None,
) -> tuple[RuntimeIssue, ...]:
    issues: list[RuntimeIssue] = []
    if intent.requires_current_model and current_state is None:
        issues.append(
            RuntimeIssue(
                kind=RuntimeIssueKind.NEEDS_USER_INPUT,
                code="router.current_model_required",
                message="The intent requires an immutable current model.",
                field_path="$.current_state",
            )
        )
    if current_state is not None and current_state.model_kind is not intent.model_kind:
        issues.append(
            RuntimeIssue(
                kind=RuntimeIssueKind.INVALID_INPUT,
                code="router.current_model_kind_mismatch",
                message="Current model kind does not match the intent.",
                field_path="$.current_state.model_kind",
            )
        )
    return tuple(issues)


def _resolve_routing_inputs(
    intent: ModelingIntent,
    current_state: ModelState | None,
) -> tuple[ResolvedRoutingInputs | None, tuple[RuntimeIssue, ...]]:
    parameters = {parameter.name: parameter.value for parameter in intent.parameters}
    issues: list[RuntimeIssue] = []

    atom_count: int | None = None
    if "atom_count" in parameters:
        value = parameters["atom_count"]
        if type(value) is not int:
            issues.append(
                _invalid_parameter_issue(
                    "router.atom_count_type_invalid",
                    "atom_count must be a strict non-boolean integer.",
                    "atom_count",
                )
            )
        elif value < 0:
            issues.append(
                _invalid_parameter_issue(
                    "router.atom_count_range_invalid",
                    "atom_count must be greater than or equal to zero.",
                    "atom_count",
                )
            )
        else:
            atom_count = value
    elif current_state is not None and current_state.model_kind in {
        ModelKind.MOLECULE,
        ModelKind.CRYSTAL,
    }:
        model = current_state.parse_model_spec().model
        atoms = model.atoms if current_state.model_kind is ModelKind.MOLECULE else model.basis_atoms
        atom_count = len(tuple(atoms))

    periodicity_dimension: int | None = None
    if "periodicity_dimension" in parameters:
        value = parameters["periodicity_dimension"]
        if type(value) is not int:
            issues.append(
                _invalid_parameter_issue(
                    "router.periodicity_dimension_type_invalid",
                    "periodicity_dimension must be a strict non-boolean integer.",
                    "periodicity_dimension",
                )
            )
        elif value < 0 or value > 3:
            issues.append(
                _invalid_parameter_issue(
                    "router.periodicity_dimension_range_invalid",
                    "periodicity_dimension must be in the range zero through three.",
                    "periodicity_dimension",
                )
            )
        else:
            periodicity_dimension = value
    elif intent.model_kind is ModelKind.MOLECULE:
        periodicity_dimension = 0
    elif intent.model_kind is ModelKind.CRYSTAL:
        periodicity_dimension = 3

    requires_calculation_plan = False
    if "requires_calculation_plan" in parameters:
        value = parameters["requires_calculation_plan"]
        if type(value) is not bool:
            issues.append(
                _invalid_parameter_issue(
                    "router.requires_calculation_plan_type_invalid",
                    "requires_calculation_plan must be a strict boolean.",
                    "requires_calculation_plan",
                )
            )
        else:
            requires_calculation_plan = value

    if issues:
        return None, _unique_issues(tuple(issues))
    return (
        ResolvedRoutingInputs(
            atom_count=atom_count,
            periodicity_dimension=periodicity_dimension,
            requires_calculation_plan=requires_calculation_plan,
        ),
        (),
    )


def _invalid_parameter_issue(code: str, message: str, name: str) -> RuntimeIssue:
    return RuntimeIssue(
        kind=RuntimeIssueKind.INVALID_INPUT,
        code=code,
        message=message,
        field_path=f"$.parameters.{name}",
    )


def _prefilter_reasons(
    entry: _RegistryEntry,
    intent: ModelingIntent,
    current_state: ModelState | None,
    resolved: ResolvedRoutingInputs,
) -> tuple[str, ...]:
    manifest = entry.manifest
    reasons: list[str] = []
    if intent.material not in manifest.capabilities.materials:
        reasons.append("router.material_unsupported")
    if intent.scenario not in manifest.capabilities.scenarios:
        reasons.append("router.scenario_unsupported")
    if intent.operation not in manifest.capabilities.operations:
        reasons.append("router.operation_unsupported")
    if intent.model_kind not in manifest.limits.supported_model_kinds:
        reasons.append("router.model_kind_unsupported")
    if (
        intent.output_kind is BuildOutputKind.MODEL_SPEC
        and not manifest.limits.supports_create
    ):
        reasons.append("router.create_unsupported")
    if (
        intent.output_kind is BuildOutputKind.SEMANTIC_PATCH
        and not manifest.limits.supports_patch
    ):
        reasons.append("router.patch_unsupported")
    if manifest.limits.requires_current_model and (
        not intent.requires_current_model or current_state is None
    ):
        reasons.append("router.current_model_requirement_mismatch")
    if intent.reference_access.mode not in manifest.reference_policy.allowed_access_modes:
        reasons.append("router.reference_access_unsupported")
    if (
        resolved.requires_calculation_plan
        and not manifest.limits.supports_calculation_plan
    ):
        reasons.append("router.calculation_plan_unsupported")
    if set(intent.semantic_requirements).intersection(
        manifest.limits.unsupported_capabilities
    ):
        reasons.append("router.semantic_requirement_unsupported")

    if resolved.atom_count is None:
        if manifest.limits.min_atoms != 0 or manifest.limits.max_atoms is not None:
            reasons.append("router.atom_count_required")
    else:
        if resolved.atom_count < manifest.limits.min_atoms:
            reasons.append("router.atom_count_below_minimum")
        if (
            manifest.limits.max_atoms is not None
            and resolved.atom_count > manifest.limits.max_atoms
        ):
            reasons.append("router.atom_count_above_maximum")

    supported_periodicity = set(manifest.limits.supported_periodicity_dimensions)
    if resolved.periodicity_dimension is None:
        if supported_periodicity != {0, 1, 2, 3}:
            reasons.append("router.periodicity_dimension_required")
    elif resolved.periodicity_dimension not in supported_periodicity:
        reasons.append("router.periodicity_dimension_unsupported")
    return tuple(sorted(set(reasons)))


def _selection_option_issues(
    forced_selection: ForcedSelectionRequest | None,
    fallback: FallbackRequest | None,
) -> tuple[RuntimeIssue, ...]:
    issues: list[RuntimeIssue] = []
    if forced_selection is not None and not isinstance(
        forced_selection, ForcedSelectionRequest
    ):
        issues.append(
            _selection_issue(
                "router.forced_selection_type_invalid",
                "Forced selection must use ForcedSelectionRequest.",
                "$.forced_selection",
            )
        )
    if fallback is not None and not isinstance(fallback, FallbackRequest):
        issues.append(
            _selection_issue(
                "router.fallback_type_invalid",
                "Fallback must use FallbackRequest.",
                "$.fallback",
            )
        )
    if issues:
        return tuple(issues)
    if forced_selection is not None and fallback is not None:
        issues.append(
            _selection_issue(
                "router.selection_options_conflict",
                "Forced selection and fallback are mutually exclusive.",
                "$.selection",
            )
        )
    if forced_selection is not None:
        if not _valid_plugin_id(forced_selection.plugin_id):
            issues.append(
                _selection_issue(
                    "router.forced_plugin_id_invalid",
                    "Forced selection plugin ID is invalid.",
                    "$.forced_selection.plugin_id",
                )
            )
        if type(forced_selection.reason) is not str or not forced_selection.reason:
            issues.append(
                _selection_issue(
                    "router.forced_reason_invalid",
                    "Forced selection reason must be nonempty text.",
                    "$.forced_selection.reason",
                )
            )
    if fallback is not None:
        if not _valid_plugin_id(fallback.from_plugin_id) or not _valid_plugin_id(
            fallback.to_plugin_id
        ):
            issues.append(
                _selection_issue(
                    "router.fallback_plugin_id_invalid",
                    "Fallback plugin IDs are invalid.",
                    "$.fallback",
                )
            )
        if fallback.from_plugin_id == fallback.to_plugin_id:
            issues.append(
                _selection_issue(
                    "router.fallback_target_matches_source",
                    "Fallback target must differ from its source.",
                    "$.fallback.to_plugin_id",
                )
            )
        if type(fallback.reason_code) is not str or re.fullmatch(
            r"[a-z][a-z0-9_.-]{1,127}", fallback.reason_code
        ) is None:
            issues.append(
                _selection_issue(
                    "router.fallback_reason_code_invalid",
                    "Fallback reason code is invalid.",
                    "$.fallback.reason_code",
                )
            )
    return _unique_issues(tuple(issues))


def _selection_issue(code: str, message: str, field_path: str) -> RuntimeIssue:
    return RuntimeIssue(
        kind=RuntimeIssueKind.INVALID_INPUT,
        code=code,
        message=message,
        field_path=field_path,
    )


def _valid_plugin_id(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[a-z][a-z0-9_]{2,63}", value) is not None


def _routing_targets(
    *,
    eligible_ids: tuple[str, ...],
    registered_ids: tuple[str, ...],
    prefilter: PrefilterResult,
    forced_selection: ForcedSelectionRequest | None,
    fallback: FallbackRequest | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    target_id: str | None = None
    missing_code: str | None = None
    filtered_code: str | None = None
    if forced_selection is not None:
        target_id = forced_selection.plugin_id
        missing_code = "router.forced_plugin_not_registered"
        filtered_code = "router.forced_plugin_prefilter_rejected"
    elif fallback is not None:
        target_id = fallback.to_plugin_id
        missing_code = "router.fallback_target_not_registered"
        filtered_code = "router.fallback_target_prefilter_rejected"

    if target_id is None:
        if eligible_ids:
            return eligible_ids, ()
        reasons = tuple(
            reason for candidate in prefilter.candidates for reason in candidate.reason_codes
        )
        if not registered_ids:
            reasons = (*reasons, "router.no_registered_plugins")
        return (), tuple(sorted(set((*reasons, "router.no_match"))))
    if target_id not in registered_ids:
        return (), (missing_code or "router.no_match",)
    if target_id not in eligible_ids:
        candidate = next(
            item for item in prefilter.candidates if item.plugin_id == target_id
        )
        return (), tuple(
            sorted(set((*candidate.reason_codes, filtered_code or "router.no_match")))
        )
    return (target_id,), ()


def _forced_decision(
    *,
    call: _MatchCall,
    request: ForcedSelectionRequest,
    intent_digest: ContractDigest,
    current_revision: RevisionIdentity | None,
    prefilter: PrefilterResult,
    evaluations: tuple[CandidateEvaluation, ...],
) -> RoutingDecision:
    assert call.result is not None
    if call.result.kind is MatchKind.NONE:
        return _unselected_decision(
            outcome=RuntimeOutcome.BLOCKED,
            intent_digest=intent_digest,
            current_revision=current_revision,
            prefilter=prefilter,
            reason_codes=(*call.result.reason_codes, "router.forced_plugin_no_match"),
            issues=_match_result_issues((call,)),
            evaluations=evaluations,
            evaluated_plugin_ids=(call.entry.manifest.plugin_id,),
        )
    evidence = ForcedSelectionEvidence(
        requested_plugin_id=request.plugin_id,
        capability_match=True,
        reason=request.reason,
    )
    return _selected_decision(
        call=call,
        intent_digest=intent_digest,
        current_revision=current_revision,
        prefilter=prefilter,
        evaluations=evaluations,
        forced_selection=evidence,
        fallback=None,
    )


def _fallback_decision(
    *,
    call: _MatchCall,
    request: FallbackRequest,
    intent_digest: ContractDigest,
    current_revision: RevisionIdentity | None,
    prefilter: PrefilterResult,
    evaluations: tuple[CandidateEvaluation, ...],
) -> RoutingDecision:
    assert call.result is not None
    if call.result.kind is MatchKind.NONE:
        return _unselected_decision(
            outcome=RuntimeOutcome.BLOCKED,
            intent_digest=intent_digest,
            current_revision=current_revision,
            prefilter=prefilter,
            reason_codes=(*call.result.reason_codes, "router.fallback_target_no_match"),
            issues=_match_result_issues((call,)),
            evaluations=evaluations,
            evaluated_plugin_ids=(call.entry.manifest.plugin_id,),
        )
    evidence = FallbackEvidence(
        from_plugin_id=request.from_plugin_id,
        to_plugin_id=request.to_plugin_id,
        reason_code=request.reason_code,
        target_independently_matched=True,
    )
    return _selected_decision(
        call=call,
        intent_digest=intent_digest,
        current_revision=current_revision,
        prefilter=prefilter,
        evaluations=evaluations,
        forced_selection=None,
        fallback=evidence,
    )


def _selected_decision(
    *,
    call: _MatchCall,
    intent_digest: ContractDigest,
    current_revision: RevisionIdentity | None,
    prefilter: PrefilterResult,
    evaluations: tuple[CandidateEvaluation, ...],
    forced_selection: ForcedSelectionEvidence | None,
    fallback: FallbackEvidence | None,
) -> RoutingDecision:
    assert call.result is not None
    blocking = any(issue.is_blocking for issue in call.result.issues)
    return RoutingDecision(
        outcome=RuntimeOutcome.BLOCKED if blocking else RuntimeOutcome.COMPLETED,
        normalized_intent_digest=intent_digest,
        current_revision=current_revision,
        selected_plugin_id=call.entry.manifest.plugin_id,
        selected_plugin_contract_version=call.entry.manifest.contract_version,
        selected_plugin_implementation_version=call.entry.manifest.implementation_version,
        match_result=call.result,
        stage_receipts=(call.receipt,),
        prefilter=prefilter,
        candidate_evaluations=evaluations,
        ambiguity=None,
        no_match=None,
        forced_selection=forced_selection,
        fallback=fallback,
        issues=(),
    )


def _failed_routing_decision(
    *,
    entry: _RegistryEntry,
    intent_digest: ContractDigest,
    current_revision: RevisionIdentity | None,
    receipt: PluginStageReceipt,
    prefilter: PrefilterResult,
    evaluations: tuple[CandidateEvaluation, ...],
) -> RoutingDecision:
    return RoutingDecision(
        outcome=RuntimeOutcome.FAILED,
        normalized_intent_digest=intent_digest,
        current_revision=current_revision,
        selected_plugin_id=entry.manifest.plugin_id,
        selected_plugin_contract_version=entry.manifest.contract_version,
        selected_plugin_implementation_version=entry.manifest.implementation_version,
        match_result=None,
        stage_receipts=(receipt,),
        prefilter=prefilter,
        candidate_evaluations=evaluations,
        ambiguity=None,
        no_match=None,
        forced_selection=None,
        fallback=None,
        issues=(),
    )


def _unselected_decision(
    *,
    outcome: RuntimeOutcome,
    intent_digest: ContractDigest,
    current_revision: RevisionIdentity | None,
    prefilter: PrefilterResult,
    reason_codes: tuple[str, ...],
    issues: tuple[RuntimeIssue, ...],
    evaluations: tuple[CandidateEvaluation, ...] = (),
    evaluated_plugin_ids: tuple[str, ...] = (),
) -> RoutingDecision:
    unique_reasons = tuple(sorted(set(reason_codes))) or ("router.no_match",)
    return RoutingDecision(
        outcome=outcome,
        normalized_intent_digest=intent_digest,
        current_revision=current_revision,
        selected_plugin_id=None,
        selected_plugin_contract_version=None,
        selected_plugin_implementation_version=None,
        match_result=None,
        stage_receipts=(),
        prefilter=prefilter,
        candidate_evaluations=evaluations,
        ambiguity=None,
        no_match=NoMatchEvidence(
            evaluated_plugin_ids=tuple(sorted(set(evaluated_plugin_ids))),
            reason_codes=unique_reasons,
            fail_closed=True,
        ),
        forced_selection=None,
        fallback=None,
        issues=issues,
    )


def _match_result_issues(calls: tuple[_MatchCall, ...]) -> tuple[RuntimeIssue, ...]:
    return _unique_issues(
        tuple(
            issue
            for call in calls
            if call.result is not None
            for issue in call.result.issues
        )
    )


def _semantic_rank(call: _MatchCall) -> tuple[int, int, int]:
    assert call.result is not None
    kind_rank = 2 if call.result.kind is MatchKind.EXACT else 1
    return (
        kind_rank,
        call.result.specificity,
        call.entry.manifest.routing.priority,
    )


def _read_plugin_identity(
    entry: _RegistryEntry,
    stage: StageName,
) -> PluginIdentity | RuntimeIssue:
    observed: list[object] = []
    unreadable = False
    for attribute in (
        "plugin_id",
        "contract_version",
        "implementation_version",
    ):
        try:
            observed.append(getattr(entry.plugin, attribute))
        except Exception:
            unreadable = True
            observed.append(None)
    if unreadable:
        return _internal_issue(
            f"router.{stage.value}_plugin_identity_unreadable",
            "Plugin identity could not be read at invocation time.",
            f"$.{stage.value}.plugin_identity",
        )
    if any(type(value) is not str for value in observed):
        return _internal_issue(
            f"router.{stage.value}_plugin_identity_mismatch",
            "Plugin identity changed after registration.",
            f"$.{stage.value}.plugin_identity",
        )
    return (observed[0], observed[1], observed[2])


def _capture_plugin_identity(
    entry: _RegistryEntry,
    stage: StageName,
) -> PluginIdentity | RuntimeIssue:
    identity = _read_plugin_identity(entry, stage)
    if isinstance(identity, RuntimeIssue):
        return identity
    expected = (
        entry.manifest.plugin_id,
        entry.manifest.contract_version,
        entry.manifest.implementation_version,
    )
    if identity != expected:
        return _internal_issue(
            f"router.{stage.value}_plugin_identity_mismatch",
            "Plugin identity changed after registration.",
            f"$.{stage.value}.plugin_identity",
        )
    return identity


def _identity_after_call_issue(
    entry: _RegistryEntry,
    stage: StageName,
    before: PluginIdentity,
) -> RuntimeIssue | None:
    after = _read_plugin_identity(entry, stage)
    if isinstance(after, RuntimeIssue):
        return after
    if after != before:
        return _internal_issue(
            f"router.{stage.value}_plugin_identity_changed",
            "Plugin identity changed during invocation.",
            f"$.{stage.value}.plugin_identity",
        )
    return None


def _plan_binding_error(
    plan: ModelingPlan,
    entry: _RegistryEntry,
    routing: RoutingDecision,
    intent: ModelingIntent,
    current_state: ModelState | None,
) -> str | None:
    if plan.plugin_id != entry.manifest.plugin_id:
        return "router.plan_plugin_id_mismatch"
    if plan.plugin_contract_version != entry.manifest.contract_version:
        return "router.plan_contract_version_mismatch"
    if plan.plugin_implementation_version != entry.manifest.implementation_version:
        return "router.plan_implementation_version_mismatch"
    if plan.normalized_intent_digest != routing.normalized_intent_digest:
        return "router.plan_intent_digest_mismatch"
    if plan.current_revision != _revision_identity(current_state):
        return "router.plan_current_revision_mismatch"
    if plan.output_kind is not intent.output_kind:
        return "router.plan_output_kind_mismatch"
    if plan.forced_selection not in (None, routing.forced_selection):
        return "router.plan_forced_selection_mismatch"
    if plan.fallback not in (None, routing.fallback):
        return "router.plan_fallback_mismatch"
    return None


def _bind_selection_evidence(
    plan: ModelingPlan,
    routing: RoutingDecision,
) -> ModelingPlan:
    if (
        plan.forced_selection == routing.forced_selection
        and plan.fallback == routing.fallback
    ):
        return plan
    payload = plan.model_dump()
    payload["forced_selection"] = routing.forced_selection
    payload["fallback"] = routing.fallback
    return ModelingPlan(**payload)


def _inputs_mutated(
    before: tuple[ContractDigest, ...],
    inputs: tuple[tuple[str, object], ...],
) -> bool:
    try:
        after = tuple(
            contract_digest(
                value,
                contract_name=name,
                contract_version=RUNTIME_CONTRACT_VERSION,
            )
            for name, value in inputs
        )
    except Exception:
        return True
    return after != before


def _revision_identity(current_state: ModelState | None) -> RevisionIdentity | None:
    if current_state is None:
        return None
    return RevisionIdentity(
        project_id=current_state.project_id,
        revision=current_state.revision,
        model_spec_digest=current_state.model_spec_digest,
    )


def _zero_side_effect_receipt() -> SideEffectReceipt:
    return SideEffectReceipt(
        filesystem_read_count=0,
        filesystem_write_count=0,
        environment_read_count=0,
        process_launch_count=0,
        network_request_count=0,
        gui_action_count=0,
        revision_write_count=0,
        input_mutated=False,
        hidden_reference_access=False,
        wall_clock_read=False,
        randomness_used=False,
        undeclared_reference_source_ids=(),
    )


def empty_side_effect_receipt() -> SideEffectReceipt:
    """Return the default in-process observation used by pure fake plugins."""

    return _zero_side_effect_receipt()


def _failed_stage_receipt(
    entry: _RegistryEntry,
    stage: StageName,
    input_digests: tuple[ContractDigest, ...],
    side_effects: SideEffectReceipt,
    issues: tuple[RuntimeIssue, ...],
) -> PluginStageReceipt:
    return PluginStageReceipt(
        contract_version=RUNTIME_CONTRACT_VERSION,
        stage=stage,
        status=StageStatus.FAILED,
        plugin_id=entry.manifest.plugin_id,
        plugin_contract_version=entry.manifest.contract_version,
        plugin_implementation_version=entry.manifest.implementation_version,
        input_digests=input_digests,
        output_digests=(),
        side_effects=side_effects,
        issues=issues,
    )


def _internal_issue(code: str, message: str, field_path: str) -> RuntimeIssue:
    return RuntimeIssue(
        kind=RuntimeIssueKind.INTERNAL_ERROR,
        code=code,
        message=message,
        field_path=field_path,
    )


def _unique_issues(issues: tuple[RuntimeIssue, ...]) -> tuple[RuntimeIssue, ...]:
    by_payload = {canonical_json_bytes(issue): issue for issue in issues}
    return tuple(by_payload[key] for key in sorted(by_payload))


__all__ = [
    "CandidateEvaluation",
    "CandidatePrefilter",
    "FallbackRequest",
    "ForcedSelectionRequest",
    "PlanningDecision",
    "PrefilterResult",
    "ResolvedRoutingInputs",
    "RoutingDecision",
    "RuntimeRouter",
    "SideEffectProbe",
    "empty_side_effect_receipt",
]
