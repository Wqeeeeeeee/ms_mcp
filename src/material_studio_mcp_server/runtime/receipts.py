"""Pure evidence receipts for the internal domain-plugin runtime boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .contracts import (
    ADAPTER_CONTRACT_VERSION,
    RUNTIME_CONTRACT_VERSION,
    AmbiguityEvidence,
    BuildOutputKind,
    ContractDigest,
    FallbackEvidence,
    ForcedSelectionEvidence,
    FrozenContractModel,
    Identifier,
    MigrationMode,
    NoMatchEvidence,
    PluginId,
    RevisionIdentity,
    RuntimeIssue,
    RuntimeIssueKind,
    RuntimeOutcome,
    SemanticVersion,
    Sha256,
    StageName,
    StageStatus,
    require_digest_binding,
)


class SideEffectReceipt(FrozenContractModel):
    """Observed side effects for one plugin stage; evidence, not proof."""

    filesystem_read_count: int = Field(ge=0)
    filesystem_write_count: int = Field(ge=0)
    environment_read_count: int = Field(ge=0)
    process_launch_count: int = Field(ge=0)
    network_request_count: int = Field(ge=0)
    gui_action_count: int = Field(ge=0)
    revision_write_count: int = Field(ge=0)
    input_mutated: bool
    hidden_reference_access: bool
    wall_clock_read: bool
    randomness_used: bool
    undeclared_reference_source_ids: tuple[Identifier, ...]

    @property
    def is_pure(self) -> bool:
        return not any(
            (
                self.filesystem_read_count,
                self.filesystem_write_count,
                self.environment_read_count,
                self.process_launch_count,
                self.network_request_count,
                self.gui_action_count,
                self.revision_write_count,
                self.input_mutated,
                self.hidden_reference_access,
                self.wall_clock_read,
                self.randomness_used,
                bool(self.undeclared_reference_source_ids),
            )
        )


class EmittedArtifactReceipt(FrozenContractModel):
    """Payload-free binding for an explicit build output kind."""

    kind: BuildOutputKind
    digest: ContractDigest

    @model_validator(mode="after")
    def validate_kind_binding(self) -> "EmittedArtifactReceipt":
        expected_name = (
            "ModelSpec"
            if self.kind is BuildOutputKind.MODEL_SPEC
            else "SemanticPatch"
        )
        require_digest_binding(
            self.digest,
            expected_name,
            ADAPTER_CONTRACT_VERSION,
        )
        return self


_STAGE_INPUT_NAMES: dict[StageName, tuple[tuple[str, ...], ...]] = {
    StageName.MATCH: (("ModelingIntent",),),
    StageName.PLAN: (
        ("ModelingIntent",),
        ("ModelingIntent", "ModelState"),
    ),
    StageName.BUILD: (("ModelingPlan",),),
    StageName.VALIDATE: (("ModelSpec",),),
}

_STAGE_OUTPUT_NAMES: dict[StageName, tuple[str, ...]] = {
    StageName.MATCH: ("MatchResult",),
    StageName.PLAN: ("ModelingPlan",),
    StageName.BUILD: ("ModelSpec", "SemanticPatch"),
    StageName.VALIDATE: ("DomainValidationReport",),
}


def _digest_contract_version(contract_name: str) -> str:
    if contract_name in {"ModelSpec", "SemanticPatch"}:
        return ADAPTER_CONTRACT_VERSION
    return RUNTIME_CONTRACT_VERSION


class PluginStageReceipt(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    stage: StageName
    status: StageStatus
    plugin_id: PluginId
    plugin_contract_version: SemanticVersion
    plugin_implementation_version: SemanticVersion
    input_digests: tuple[ContractDigest, ...] = Field(min_length=1)
    output_digests: tuple[ContractDigest, ...]
    side_effects: SideEffectReceipt
    issues: tuple[RuntimeIssue, ...]

    @model_validator(mode="after")
    def validate_stage_receipt(self) -> "PluginStageReceipt":
        input_names = tuple(digest.contract_name for digest in self.input_digests)
        if input_names not in _STAGE_INPUT_NAMES[self.stage]:
            raise ValueError(f"{self.stage.value} input digest bindings are not exact")
        for digest in self.input_digests:
            require_digest_binding(
                digest,
                digest.contract_name,
                _digest_contract_version(digest.contract_name),
            )

        internal_error_observed = any(
            issue.kind is RuntimeIssueKind.INTERNAL_ERROR for issue in self.issues
        )
        if self.status is StageStatus.COMPLETED:
            if not self.side_effects.is_pure:
                raise ValueError("completed stage requires pure side effects")
            if len(self.output_digests) != 1:
                raise ValueError("completed stage requires exactly one output digest")
            if internal_error_observed:
                raise ValueError("completed stage cannot contain internal_error")
            output = self.output_digests[0]
            if output.contract_name not in _STAGE_OUTPUT_NAMES[self.stage]:
                raise ValueError(
                    f"{self.stage.value} output digest binding is not exact"
                )
            require_digest_binding(
                output,
                output.contract_name,
                _digest_contract_version(output.contract_name),
            )
        else:
            if self.output_digests:
                raise ValueError("failed stage must not contain an output digest")
            if not internal_error_observed and self.side_effects.is_pure:
                raise ValueError(
                    "failed stage requires internal_error or observed impurity"
                )
        return self


class DecisionDifference(FrozenContractModel):
    code: Identifier
    field_path: str = Field(min_length=1)
    legacy_value_sha256: Sha256 | None = None
    shadow_value_sha256: Sha256 | None = None
    summary: str = Field(min_length=1)


class ShadowComparisonReceipt(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    selected_plugin_id: PluginId
    selected_plugin_contract_version: SemanticVersion
    selected_plugin_implementation_version: SemanticVersion
    normalized_intent_digest: ContractDigest
    current_revision: RevisionIdentity | None
    authoritative_legacy_decision_digest: ContractDigest
    shadow_plan_digest: ContractDigest
    equivalent: bool
    differences: tuple[DecisionDifference, ...]

    @model_validator(mode="after")
    def validate_shadow_comparison(self) -> "ShadowComparisonReceipt":
        require_digest_binding(
            self.normalized_intent_digest,
            "ModelingIntent",
            RUNTIME_CONTRACT_VERSION,
        )
        require_digest_binding(
            self.shadow_plan_digest,
            "ModelingPlan",
            RUNTIME_CONTRACT_VERSION,
        )
        if self.equivalent is not (not self.differences):
            raise ValueError("equivalent must be true exactly when differences is empty")
        return self


class RuntimePluginReceipt(FrozenContractModel):
    """Decision-only receipt for off, shadow, and active migration modes."""

    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    receipt_id: Identifier
    migration_mode: MigrationMode
    outcome: RuntimeOutcome
    authoritative_path: Literal["legacy", "plugin"]
    normalized_intent_digest: ContractDigest
    current_revision: RevisionIdentity | None
    selected_plugin_id: PluginId | None
    selected_plugin_contract_version: SemanticVersion | None
    selected_plugin_implementation_version: SemanticVersion | None
    plan_digest: ContractDigest | None
    emitted_artifact: EmittedArtifactReceipt | None
    stage_receipts: tuple[PluginStageReceipt, ...]
    ambiguity: AmbiguityEvidence | None
    no_match: NoMatchEvidence | None
    forced_selection: ForcedSelectionEvidence | None
    fallback: FallbackEvidence | None
    shadow_comparison: ShadowComparisonReceipt | None
    issues: tuple[RuntimeIssue, ...]

    @model_validator(mode="after")
    def validate_runtime_receipt(self) -> "RuntimePluginReceipt":
        require_digest_binding(
            self.normalized_intent_digest,
            "ModelingIntent",
            RUNTIME_CONTRACT_VERSION,
        )
        if self.plan_digest is not None:
            require_digest_binding(
                self.plan_digest,
                "ModelingPlan",
                RUNTIME_CONTRACT_VERSION,
            )

        self._validate_stage_prefix_and_terminal_status()
        self._validate_selection_state()
        self._validate_mode_truth_table()
        self._validate_repeated_bindings()
        self._validate_outcome_issues()
        return self

    def _validate_stage_prefix_and_terminal_status(self) -> None:
        expected_order = (
            StageName.MATCH,
            StageName.PLAN,
            StageName.BUILD,
            StageName.VALIDATE,
        )
        observed = tuple(receipt.stage for receipt in self.stage_receipts)
        if observed != expected_order[: len(observed)]:
            raise ValueError("stage_receipts must be an ordered stage prefix")
        failed_indexes = tuple(
            index
            for index, receipt in enumerate(self.stage_receipts)
            if receipt.status is StageStatus.FAILED
        )
        if failed_indexes and failed_indexes != (len(self.stage_receipts) - 1,):
            raise ValueError("only the terminal stage may be failed")

        if self.migration_mode is MigrationMode.OFF:
            return
        routing_block = self.ambiguity is not None or self.no_match is not None
        if self.outcome is RuntimeOutcome.FAILED:
            if not self.stage_receipts or not failed_indexes:
                raise ValueError("failed outcome requires a terminal failed stage")
        elif self.outcome is RuntimeOutcome.BLOCKED:
            if routing_block:
                if self.stage_receipts:
                    raise ValueError("blocked routing decision cannot have stage receipts")
            elif not self.stage_receipts or failed_indexes:
                raise ValueError(
                    "semantic blocked outcome requires a completed terminal stage"
                )
        elif failed_indexes:
            raise ValueError("completed outcome cannot contain a failed stage")

    def _validate_selection_state(self) -> None:
        selected_values = (
            self.selected_plugin_id,
            self.selected_plugin_contract_version,
            self.selected_plugin_implementation_version,
        )
        selected_present = all(value is not None for value in selected_values)
        if any(value is not None for value in selected_values) and not selected_present:
            raise ValueError("selected plugin identity and versions are all-or-none")
        routing_evidence_count = int(self.ambiguity is not None) + int(
            self.no_match is not None
        )
        if routing_evidence_count > 1:
            raise ValueError("ambiguity and no_match are mutually exclusive")

        if self.migration_mode is MigrationMode.OFF:
            if selected_present or routing_evidence_count:
                raise ValueError("off mode cannot select or route a plugin")
        elif routing_evidence_count:
            if self.outcome is not RuntimeOutcome.BLOCKED or selected_present:
                raise ValueError(
                    "routing evidence requires blocked outcome with no selected plugin"
                )
        elif not selected_present:
            raise ValueError(
                "shadow/active require selected plugin unless routing is blocked"
            )

        if self.forced_selection is not None and self.fallback is not None:
            raise ValueError("forced_selection and fallback are mutually exclusive")
        if not selected_present and (
            self.forced_selection is not None or self.fallback is not None
        ):
            raise ValueError("selection evidence requires a selected plugin")
        if (
            self.forced_selection is not None
            and self.forced_selection.requested_plugin_id
            != self.selected_plugin_id
        ):
            raise ValueError("forced selection does not match selected plugin")
        if (
            self.fallback is not None
            and self.fallback.to_plugin_id != self.selected_plugin_id
        ):
            raise ValueError("fallback target does not match selected plugin")

    def _validate_mode_truth_table(self) -> None:
        if self.migration_mode is MigrationMode.OFF:
            if self.authoritative_path != "legacy":
                raise ValueError("off mode authoritative_path must be legacy")
            if any(
                value is not None
                for value in (
                    self.plan_digest,
                    self.emitted_artifact,
                    self.shadow_comparison,
                    self.forced_selection,
                    self.fallback,
                )
            ) or self.stage_receipts:
                raise ValueError("off mode cannot contain plugin runtime decisions")
            return

        if self.migration_mode is MigrationMode.SHADOW:
            if self.authoritative_path != "legacy":
                raise ValueError("shadow mode authoritative_path must be legacy")
            if self.emitted_artifact is not None:
                raise ValueError("shadow mode never contains an emitted artifact")
            if len(self.stage_receipts) > 2:
                raise ValueError("shadow mode allows only match/plan stages")
            if self.outcome is RuntimeOutcome.COMPLETED:
                if self.shadow_comparison is None:
                    raise ValueError(
                        "completed shadow outcome requires shadow_comparison"
                    )
                if tuple(stage.stage for stage in self.stage_receipts) != (
                    StageName.MATCH,
                    StageName.PLAN,
                ) or any(
                    stage.status is not StageStatus.COMPLETED
                    for stage in self.stage_receipts
                ):
                    raise ValueError(
                        "completed shadow outcome requires completed match/plan stages"
                    )
            elif self.shadow_comparison is not None:
                raise ValueError(
                    "shadow_comparison is allowed only for completed shadow outcome"
                )
            return

        if self.authoritative_path != "plugin":
            raise ValueError("active mode authoritative_path must be plugin")
        if self.shadow_comparison is not None:
            raise ValueError("active mode cannot contain shadow_comparison")
        if self.outcome is RuntimeOutcome.COMPLETED:
            if self.plan_digest is None or self.emitted_artifact is None:
                raise ValueError(
                    "completed active outcome requires plan and emitted artifact"
                )
            if tuple(stage.stage for stage in self.stage_receipts) != (
                StageName.MATCH,
                StageName.PLAN,
                StageName.BUILD,
                StageName.VALIDATE,
            ) or any(
                stage.status is not StageStatus.COMPLETED
                for stage in self.stage_receipts
            ):
                raise ValueError(
                    "completed active outcome requires all four completed stages"
                )

    def _validate_repeated_bindings(self) -> None:
        if self.selected_plugin_id is None:
            if (
                self.plan_digest is not None
                or self.emitted_artifact is not None
                or self.stage_receipts
                or self.shadow_comparison is not None
            ):
                raise ValueError(
                    "unselected routing decision cannot bind plugin stages, "
                    "plan, or emitted artifacts"
                )
            return

        for stage in self.stage_receipts:
            if (
                stage.plugin_id != self.selected_plugin_id
                or stage.plugin_contract_version
                != self.selected_plugin_contract_version
                or stage.plugin_implementation_version
                != self.selected_plugin_implementation_version
            ):
                raise ValueError("stage plugin binding does not match selection")

        stages = {stage.stage: stage for stage in self.stage_receipts}
        match_stage = stages.get(StageName.MATCH)
        if (
            match_stage is not None
            and match_stage.input_digests[0] != self.normalized_intent_digest
        ):
            raise ValueError("match intent digest does not match runtime receipt")

        plan_stage = stages.get(StageName.PLAN)
        if plan_stage is not None:
            if plan_stage.input_digests[0] != self.normalized_intent_digest:
                raise ValueError("plan intent digest does not match runtime receipt")
            has_state_input = len(plan_stage.input_digests) == 2
            if has_state_input is (self.current_revision is None):
                raise ValueError(
                    "plan ModelState input does not match current revision binding"
                )
            if plan_stage.status is StageStatus.COMPLETED:
                if self.plan_digest != plan_stage.output_digests[0]:
                    raise ValueError("plan digest does not match completed plan output")
            elif self.plan_digest is not None:
                raise ValueError("failed plan stage cannot bind plan_digest")
        elif self.plan_digest is not None:
            raise ValueError("plan_digest requires a completed plan stage")

        build_stage = stages.get(StageName.BUILD)
        if build_stage is not None:
            if self.plan_digest is None or build_stage.input_digests[0] != self.plan_digest:
                raise ValueError("build input does not match plan_digest")
            if build_stage.status is StageStatus.COMPLETED:
                if (
                    self.emitted_artifact is None
                    or self.emitted_artifact.digest != build_stage.output_digests[0]
                ):
                    raise ValueError(
                        "emitted artifact does not match completed build output"
                    )
                if (
                    self.emitted_artifact.kind is BuildOutputKind.SEMANTIC_PATCH
                    and self.current_revision is None
                ):
                    raise ValueError(
                        "semantic_patch artifact requires current_revision"
                    )
            elif self.emitted_artifact is not None:
                raise ValueError("failed build stage cannot bind emitted_artifact")
        elif self.emitted_artifact is not None:
            raise ValueError("emitted_artifact requires a completed build stage")

        validate_stage = stages.get(StageName.VALIDATE)
        if (
            validate_stage is not None
            and self.emitted_artifact is not None
            and self.emitted_artifact.kind is BuildOutputKind.MODEL_SPEC
            and validate_stage.input_digests[0] != self.emitted_artifact.digest
        ):
            raise ValueError(
                "validate ModelSpec input does not match emitted model_spec digest"
            )

        if self.shadow_comparison is not None:
            comparison = self.shadow_comparison
            if (
                comparison.selected_plugin_id != self.selected_plugin_id
                or comparison.selected_plugin_contract_version
                != self.selected_plugin_contract_version
                or comparison.selected_plugin_implementation_version
                != self.selected_plugin_implementation_version
                or comparison.normalized_intent_digest
                != self.normalized_intent_digest
                or comparison.current_revision != self.current_revision
                or comparison.shadow_plan_digest != self.plan_digest
            ):
                raise ValueError("shadow comparison repeated binding mismatch")

    def _validate_outcome_issues(self) -> None:
        all_issues = (
            *self.issues,
            *(
                issue
                for stage in self.stage_receipts
                for issue in stage.issues
            ),
        )
        blocking_semantic_issue = any(
            issue.kind
            in {
                RuntimeIssueKind.UNSUPPORTED,
                RuntimeIssueKind.INVALID_INPUT,
                RuntimeIssueKind.NEEDS_USER_INPUT,
            }
            for issue in all_issues
        )
        internal_error = any(
            issue.kind is RuntimeIssueKind.INTERNAL_ERROR for issue in all_issues
        )
        routing_block = self.ambiguity is not None or self.no_match is not None
        if self.outcome is RuntimeOutcome.COMPLETED and any(
            issue.is_blocking for issue in all_issues
        ):
            raise ValueError("completed runtime outcome cannot contain blocking issues")
        if self.outcome is RuntimeOutcome.BLOCKED and not (
            routing_block or blocking_semantic_issue
        ):
            raise ValueError("blocked outcome requires routing or semantic block evidence")
        if self.outcome is RuntimeOutcome.BLOCKED and internal_error:
            raise ValueError("internal_error requires failed runtime outcome")
        if (
            self.migration_mode is MigrationMode.OFF
            and self.outcome is RuntimeOutcome.FAILED
            and not internal_error
        ):
            raise ValueError("failed off outcome requires internal_error evidence")


__all__ = [
    "DecisionDifference",
    "EmittedArtifactReceipt",
    "PluginStageReceipt",
    "RuntimePluginReceipt",
    "ShadowComparisonReceipt",
    "SideEffectReceipt",
]
