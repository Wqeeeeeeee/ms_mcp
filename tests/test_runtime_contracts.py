from __future__ import annotations

import copy
import inspect
import json
import math
from pathlib import Path
from typing import Literal, get_origin, get_type_hints

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from material_studio_mcp_server.runtime import (
    ADAPTER_CONTRACT_VERSION,
    HASH_PROFILE,
    RUNTIME_CONTRACT_VERSION,
    AmbiguityEvidence,
    BuildOutputKind,
    ContractDigest,
    DomainFact,
    DomainPluginManifest,
    DomainValidationReport,
    FallbackEvidence,
    ForcedSelectionEvidence,
    FrozenContractModel,
    MatchKind,
    MatchResult,
    ModelKind,
    ModelState,
    ModelingIntent,
    ModelingPlan,
    ModelingQuestion,
    NoMatchEvidence,
    PlanStep,
    PluginReferencePolicy,
    PluginRouting,
    ReferenceAccess,
    ReferenceAccessMode,
    ResolvedAssumption,
    RevisionIdentity,
    RuntimeIssue,
    RuntimeIssueKind,
    SemanticParameter,
    SemiconductorDomainPlugin,
    ValidationStatus,
    canonical_json,
    canonical_json_bytes,
    contract_digest,
    model_spec_digest,
    semantic_patch_digest,
)
from material_studio_mcp_server.specs import (
    FileRef,
    ImportedStructureSpec,
    ModelSpec,
    ModelType,
    SemanticPatch,
    SemanticPatchOperation,
)


ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 64


def _model_spec(*, revision: int = 1) -> ModelSpec:
    return ModelSpec(
        project_id="runtime_project",
        revision=revision,
        model_type=ModelType.IMPORTED_STRUCTURE,
        model=ImportedStructureSpec(
            name="3C-SiC candidate",
            source_file=FileRef(path="candidate/runtime_project.xsd"),
            format="xsd",
        ),
        metadata={"material": "3C-SiC", "surface": None},
    )


def _semantic_patch() -> SemanticPatch:
    return SemanticPatch(
        project_id="runtime_project",
        base_revision=1,
        operations=[
            SemanticPatchOperation(
                type="set_metadata",
                metadata_updates={"termination": "Si"},
            )
        ],
    )


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
    output_kind: BuildOutputKind = BuildOutputKind.MODEL_SPEC,
    requires_current_model: bool = False,
) -> ModelingIntent:
    return ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="request.surface-001",
        material="3C-SiC",
        scenario="surface.slab",
        operation="surface.create",
        model_kind=ModelKind.CRYSTAL,
        requires_current_model=requires_current_model,
        output_kind=output_kind,
        parameters=(SemanticParameter(name="layers", value=8, unit=None),),
        semantic_requirements=("preserve stoichiometry",),
        declared_assumptions=("ideal unreconstructed surface",),
        reference_access=_reference_access(),
    )


def _issue(kind: RuntimeIssueKind) -> RuntimeIssue:
    return RuntimeIssue(
        kind=kind,
        code=f"runtime.{kind.value}",
        message=f"Observed {kind.value}.",
        field_path="$.parameters",
    )


def _revision_identity() -> RevisionIdentity:
    spec = _model_spec()
    return RevisionIdentity(
        project_id=spec.project_id,
        revision=spec.revision,
        model_spec_digest=model_spec_digest(spec),
    )


def _plan(
    *,
    output_kind: BuildOutputKind = BuildOutputKind.MODEL_SPEC,
    current_revision: RevisionIdentity | None = None,
    steps: tuple[PlanStep, ...] | None = None,
    questions: tuple[ModelingQuestion, ...] = (),
    issues: tuple[RuntimeIssue, ...] = (),
    forced_selection: ForcedSelectionEvidence | None = None,
    fallback: FallbackEvidence | None = None,
    build_eligible: bool | None = None,
) -> ModelingPlan:
    actual_steps = steps if steps is not None else (
        PlanStep(
            step_id="step.build",
            operation="surface.cut",
            parameters=(SemanticParameter(name="layers", value=8, unit=None),),
        ),
    )
    expected = bool(actual_steps) and not questions and not any(
        issue.is_blocking for issue in issues
    )
    return ModelingPlan(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        plugin_contract_version="1.0.0",
        plugin_implementation_version="1.2.0",
        normalized_intent_digest=contract_digest(
            _intent(
                output_kind=output_kind,
                requires_current_model=current_revision is not None,
            ),
            contract_name="ModelingIntent",
            contract_version="1.0.0",
        ),
        current_revision=current_revision,
        output_kind=output_kind,
        steps=actual_steps,
        assumptions=(
            ResolvedAssumption(
                code="assumption.termination",
                statement="Use ideal Si termination.",
                source="declared_default",
            ),
        ),
        questions=questions,
        issues=issues,
        forced_selection=forced_selection,
        fallback=fallback,
        build_eligible=expected if build_eligible is None else build_eligible,
    )


def _stage(callable_name: str, inputs: list[str], outputs: list[str]) -> dict:
    return {
        "callable": callable_name,
        "input_contracts": inputs,
        "output_contracts": outputs,
        "deterministic": True,
        "filesystem_side_effects": False,
        "process_side_effects": False,
        "network_access": False,
        "gui_access": False,
    }


def _manifest_payload() -> dict:
    return {
        "plugin_id": "sic_surface",
        "contract_version": "1.0.0",
        "implementation_version": "1.2.0",
        "description": "Deterministic 3C-SiC surface planner.",
        "capabilities": {
            "materials": ["3C-SiC"],
            "scenarios": ["surface_slab"],
            "operations": ["create_si_face_slab"],
        },
        "limits": {
            "min_atoms": 1,
            "max_atoms": 10000,
            "supported_periodicity_dimensions": [2, 3],
            "supported_model_kinds": ["crystal"],
            "requires_current_model": False,
            "supports_create": True,
            "supports_patch": False,
            "supports_calculation_plan": False,
            "unsupported_capabilities": ["reconstruction_search"],
        },
        "routing": {
            "priority": 100,
            "ambiguity_policy": "fail_closed",
            "forced_selection_requires_capability_match": True,
        },
        "reference_policy": {
            "allowed_access_modes": ["none", "metadata_only", "task_only"],
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
            "match": _stage(
                "sic_surface.match", ["ModelingIntent"], ["MatchResult"]
            ),
            "plan": _stage(
                "sic_surface.plan",
                ["ModelingIntent", "ModelState"],
                ["ModelingPlan"],
            ),
            "build": _stage(
                "sic_surface.build",
                ["ModelingPlan"],
                ["ModelSpec", "SemanticPatch"],
            ),
            "validate": _stage(
                "sic_surface.validate",
                ["ModelSpec"],
                ["DomainValidationReport"],
            ),
        },
        "dependencies": [],
    }


def _all_contract_models() -> set[type[FrozenContractModel]]:
    found: set[type[FrozenContractModel]] = set()
    pending = [FrozenContractModel]
    while pending:
        parent = pending.pop()
        for child in parent.__subclasses__():
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def test_all_runtime_models_use_exact_strict_frozen_config() -> None:
    expected = {
        "extra": "forbid",
        "frozen": True,
        "strict": True,
        "allow_inf_nan": False,
        "validate_default": True,
        "revalidate_instances": "always",
    }
    models = _all_contract_models()
    assert models
    for model in models:
        assert {key: model.model_config.get(key) for key in expected} == expected
        for name, field in model.model_fields.items():
            if name == "contract_version" or get_origin(field.annotation) is Literal:
                assert field.is_required(), f"{model.__name__}.{name} has a default"

    parameter = SemanticParameter(name="layers", value=8, unit=None)
    with pytest.raises(ValidationError, match="frozen_instance"):
        parameter.value = 9
    assert isinstance(_intent().parameters, tuple)


def test_runtime_contracts_reject_missing_extra_and_coerced_values() -> None:
    payload = _intent().model_dump(mode="json")
    payload.pop("material")
    with pytest.raises(ValidationError):
        ModelingIntent.model_validate(payload)

    payload = _intent().model_dump(mode="json")
    payload["execution_mode"] = "execute"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ModelingIntent.model_validate_json(json.dumps(payload))

    with pytest.raises(ValidationError):
        ModelingIntent(
            **{
                **_intent().model_dump(),
                "model_kind": "crystal",
            }
        )
    with pytest.raises(ValidationError):
        MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            kind=MatchKind.EXACT,
            specificity="10",
            reason_codes=("match.exact",),
            issues=(),
        )
    with pytest.raises(ValidationError):
        ReferenceAccess(
            mode=ReferenceAccessMode.NONE,
            source_ids=[],
            raw_structure_access=False,
            final_coordinate_access=False,
            hidden_holdout_access=False,
        )


def test_all_reviewed_contract_constants_are_required() -> None:
    digest = contract_digest(
        {"intent": "surface"},
        contract_name="ModelingIntent",
        contract_version=RUNTIME_CONTRACT_VERSION,
    )
    match = MatchResult(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        kind=MatchKind.EXACT,
        specificity=100,
        reason_codes=("match.exact",),
        issues=(),
    )
    report = DomainValidationReport(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        plugin_contract_version="1.0.0",
        plugin_implementation_version="1.2.0",
        model_spec_digest=model_spec_digest(_model_spec()),
        status=ValidationStatus.PASS,
        facts=(),
        issues=(),
        preview_eligible=True,
    )
    cases = (
        (digest, ("hash_profile", "algorithm")),
        (
            _reference_access(),
            (
                "raw_structure_access",
                "final_coordinate_access",
                "hidden_holdout_access",
            ),
        ),
        (_intent(), ("contract_version",)),
        (
            ModelState.from_model_spec(_model_spec()),
            ("contract_version", "immutable", "observed_as_current"),
        ),
        (match, ("contract_version",)),
        (_plan(), ("contract_version",)),
        (report, ("contract_version",)),
        (
            ForcedSelectionEvidence(
                requested_plugin_id="sic_surface",
                capability_match=True,
                reason="Explicit reviewed selection.",
            ),
            ("capability_match",),
        ),
        (
            FallbackEvidence(
                from_plugin_id="sic_primary",
                to_plugin_id="sic_surface",
                reason_code="fallback.reviewed",
                target_independently_matched=True,
            ),
            ("target_independently_matched",),
        ),
        (
            AmbiguityEvidence(
                tied_plugin_ids=("sic_alpha", "sic_beta"),
                match_kind=MatchKind.EXACT,
                specificity=100,
                priority=10,
                fail_closed=True,
            ),
            ("fail_closed",),
        ),
        (
            NoMatchEvidence(
                evaluated_plugin_ids=("sic_alpha", "sic_beta"),
                reason_codes=("match.none",),
                fail_closed=True,
            ),
            ("fail_closed",),
        ),
    )

    for value, required_fields in cases:
        schema_required = set(type(value).model_json_schema().get("required", ()))
        payload = value.model_dump(mode="json", by_alias=True)
        for field in required_fields:
            assert field in schema_required
            missing = copy.deepcopy(payload)
            missing.pop(field)
            with pytest.raises(ValidationError):
                type(value).model_validate_json(json.dumps(missing))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_numbers_are_rejected(value: float) -> None:
    with pytest.raises((ValidationError, ValueError)):
        SemanticParameter(name="distance", value=value, unit="angstrom")
    with pytest.raises((ValidationError, ValueError)):
        DomainFact(code="fact.distance", value=value, unit="angstrom")
    with pytest.raises(ValueError, match="NaN and Infinity"):
        contract_digest(
            {"nested": [value]},
            contract_name="ModelingIntent",
            contract_version="1.0.0",
        )


def test_canonical_digest_golden_profile_and_order_rules() -> None:
    payload = {
        "z": None,
        "unicode": "\u78b3\u5316\u7845",
        "ordered": [1, 2],
        "defaults": {"enabled": False},
    }
    digest = contract_digest(
        payload,
        contract_name="ModelingIntent",
        contract_version="1.0.0",
    )
    assert digest == ContractDigest(
        hash_profile=HASH_PROFILE,
        contract_name="ModelingIntent",
        contract_version="1.0.0",
        algorithm="sha256",
        sha256="6fb3b8513aee8f264ff0c609f711ec21cc1d2bdfab464d96952ead6d2b1557c9",
    )
    assert digest.hash_profile == HASH_PROFILE
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert b"\\u78b3" not in canonical_json_bytes(payload)

    reordered = {
        "defaults": {"enabled": False},
        "ordered": [1, 2],
        "unicode": "\u78b3\u5316\u7845",
        "z": None,
    }
    assert contract_digest(
        reordered,
        contract_name="ModelingIntent",
        contract_version="1.0.0",
    ) == digest
    assert contract_digest(
        {**payload, "ordered": [2, 1]},
        contract_name="ModelingIntent",
        contract_version="1.0.0",
    ) != digest
    assert contract_digest(
        payload,
        contract_name="ModelingPlan",
        contract_version="1.0.0",
    ) != digest
    assert contract_digest(
        payload,
        contract_name="ModelingIntent",
        contract_version="2.0.0",
    ) != digest
    assert contract_digest(
        {key: value for key, value in payload.items() if key != "z"},
        contract_name="ModelingIntent",
        contract_version="1.0.0",
    ) != digest


def test_canonical_profile_rejects_non_json_and_unordered_inputs() -> None:
    with pytest.raises(TypeError, match="string mapping keys"):
        canonical_json({1: "value"})
    with pytest.raises(TypeError, match="unordered sets"):
        canonical_json({"values": {1, 2}})
    with pytest.raises(TypeError, match="does not support"):
        canonical_json({"value": object()})
    with pytest.raises(ValueError):
        contract_digest({}, contract_name="x", contract_version="1.0.0")
    with pytest.raises(ValueError):
        contract_digest({}, contract_name="ValidName", contract_version="01.0.0")


@pytest.mark.parametrize(
    "unordered",
    (
        {"alpha", "beta"},
        frozenset(("alpha", "beta")),
    ),
)
def test_base_model_preflight_rejects_nested_unordered_any_values(
    unordered,
) -> None:
    spec = _model_spec()
    spec.metadata["nested"] = {"values": unordered}
    with pytest.raises(TypeError, match="unordered sets"):
        model_spec_digest(spec)

    patch = SemanticPatch(
        project_id="runtime_project",
        base_revision=1,
        operations=[
            SemanticPatchOperation(
                type="set_metadata",
                metadata_updates={"nested": {"values": unordered}},
            )
        ],
    )
    with pytest.raises(TypeError, match="unordered sets"):
        semantic_patch_digest(patch)


@pytest.mark.parametrize(
    ("forbidden", "error"),
    (
        ({1: "value"}, "string mapping keys"),
        (math.nan, "NaN and Infinity"),
        (object(), "does not support values of type object"),
    ),
)
def test_base_model_preflight_rejects_other_nested_non_json_values(
    forbidden,
    error: str,
) -> None:
    spec = _model_spec()
    spec.metadata["nested"] = forbidden
    with pytest.raises((TypeError, ValueError), match=error):
        model_spec_digest(spec)


def test_base_model_hashing_includes_none_and_explicit_literal_fields() -> None:
    access = _reference_access()
    explicit = {
        "mode": "none",
        "source_ids": [],
        "raw_structure_access": False,
        "final_coordinate_access": False,
        "hidden_holdout_access": False,
    }
    assert contract_digest(
        access,
        contract_name="ReferenceAccess",
        contract_version="1.0.0",
    ) == contract_digest(
        explicit,
        contract_name="ReferenceAccess",
        contract_version="1.0.0",
    )
    assert contract_digest(
        SemanticParameter(name="layers", value=8, unit=None),
        contract_name="SemanticParameter",
        contract_version="1.0.0",
    ) != contract_digest(
        {"name": "layers", "value": 8},
        contract_name="SemanticParameter",
        contract_version="1.0.0",
    )


def test_existing_spec_and_patch_adapters_bind_contract_and_revision() -> None:
    first = model_spec_digest(_model_spec(revision=1))
    second = model_spec_digest(_model_spec(revision=2))
    assert first.contract_name == "ModelSpec"
    assert first.contract_version == ADAPTER_CONTRACT_VERSION
    assert first != second

    patch = _semantic_patch()
    patch_digest = semantic_patch_digest(patch)
    assert patch_digest.contract_name == "SemanticPatch"
    assert patch_digest.contract_version == ADAPTER_CONTRACT_VERSION
    changed = patch.model_copy(update={"base_revision": 2})
    assert semantic_patch_digest(changed) != patch_digest


def test_model_state_is_canonical_identity_bound_and_reference_isolated() -> None:
    state = ModelState.from_model_spec(_model_spec())
    assert state.model_spec_digest == model_spec_digest(_model_spec())
    assert state.canonical_model_spec_json == canonical_json(_model_spec())
    assert set(type(state).model_fields) == {
        "contract_version",
        "project_id",
        "revision",
        "model_kind",
        "canonical_model_spec_json",
        "model_spec_digest",
        "immutable",
        "observed_as_current",
    }

    first = state.parse_model_spec()
    second = state.parse_model_spec()
    assert first is not second
    first.metadata["mutated"] = True
    assert "mutated" not in second.metadata

    with pytest.raises(ValidationError):
        ModelState(
            contract_version=RUNTIME_CONTRACT_VERSION,
            project_id=state.project_id,
            revision=state.revision,
            model_kind=state.model_kind,
            canonical_model_spec_json=state.canonical_model_spec_json + "\n",
            model_spec_digest=state.model_spec_digest,
            immutable=True,
            observed_as_current=True,
        )
    with pytest.raises(ValidationError):
        ModelState(
            contract_version=RUNTIME_CONTRACT_VERSION,
            project_id="other_project",
            revision=state.revision,
            model_kind=state.model_kind,
            canonical_model_spec_json=state.canonical_model_spec_json,
            model_spec_digest=state.model_spec_digest,
            immutable=True,
            observed_as_current=True,
        )
    with pytest.raises(ValidationError):
        ModelState(
            contract_version=RUNTIME_CONTRACT_VERSION,
            project_id=state.project_id,
            revision=state.revision,
            model_kind=state.model_kind,
            canonical_model_spec_json=state.canonical_model_spec_json,
            model_spec_digest=contract_digest(
                _model_spec(),
                contract_name="ModelingPlan",
                contract_version="1.0.0",
            ),
            immutable=True,
            observed_as_current=True,
        )
    with pytest.raises(ValidationError):
        ModelState.from_model_spec(_model_spec(revision=0))


def test_reference_isolation_rejects_hidden_or_payload_access() -> None:
    with pytest.raises(ValidationError):
        ReferenceAccess(
            mode=ReferenceAccessMode.NONE,
            source_ids=("source.task",),
            raw_structure_access=False,
            final_coordinate_access=False,
            hidden_holdout_access=False,
        )
    for field in (
        "raw_structure_access",
        "final_coordinate_access",
        "hidden_holdout_access",
    ):
        gates = {
            "raw_structure_access": False,
            "final_coordinate_access": False,
            "hidden_holdout_access": False,
        }
        gates[field] = True
        with pytest.raises(ValidationError):
            ReferenceAccess(
                mode=ReferenceAccessMode.TASK_ONLY,
                source_ids=("source.task",),
                **gates,
            )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReferenceAccess.model_validate_json(
            json.dumps(
                {
                    "mode": "task_only",
                    "source_ids": ["source.task"],
                    "raw_structure_access": False,
                    "final_coordinate_access": False,
                    "hidden_holdout_access": False,
                    "reference_coordinates": [[0.0, 0.0, 0.0]],
                }
            )
        )


def test_match_result_preserves_match_and_issue_semantics() -> None:
    assert _issue(RuntimeIssueKind.PREVIEW_WARNING).is_blocking is False
    assert _issue(RuntimeIssueKind.NEEDS_USER_INPUT).is_blocking is True

    MatchResult(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        kind=MatchKind.NONE,
        specificity=0,
        reason_codes=("match.none",),
        issues=(_issue(RuntimeIssueKind.UNSUPPORTED),),
    )
    MatchResult(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        kind=MatchKind.COMPATIBLE,
        specificity=1,
        reason_codes=("match.compatible",),
        issues=(_issue(RuntimeIssueKind.PREVIEW_WARNING),),
    )
    with pytest.raises(ValidationError):
        MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            kind=MatchKind.NONE,
            specificity=1,
            reason_codes=("match.none",),
            issues=(),
        )
    with pytest.raises(ValidationError):
        MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            kind=MatchKind.EXACT,
            specificity=1001,
            reason_codes=("match.exact",),
            issues=(),
        )
    with pytest.raises(ValidationError):
        MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            kind=MatchKind.EXACT,
            specificity=10,
            reason_codes=("match.exact",),
            issues=(_issue(RuntimeIssueKind.INVALID_INPUT),),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            kind=MatchKind.EXACT,
            specificity=10,
            reason_codes=("match.exact", "match.exact"),
            issues=(),
        )


def test_plan_truth_table_selection_and_patch_constraints() -> None:
    assert _plan().build_eligible is True
    assert _plan(steps=()).build_eligible is False
    assert _plan(issues=(_issue(RuntimeIssueKind.PREVIEW_WARNING),)).build_eligible
    assert not _plan(issues=(_issue(RuntimeIssueKind.INVALID_INPUT),)).build_eligible
    question = ModelingQuestion(
        question_id="question.termination",
        prompt="Choose a termination.",
        parameter_name="termination",
        choices=("Si", "C"),
    )
    assert not _plan(questions=(question,)).build_eligible

    with pytest.raises(ValidationError):
        _plan(issues=(_issue(RuntimeIssueKind.INVALID_INPUT),), build_eligible=True)
    with pytest.raises(ValidationError):
        _plan(output_kind=BuildOutputKind.SEMANTIC_PATCH)
    assert _plan(
        output_kind=BuildOutputKind.SEMANTIC_PATCH,
        current_revision=_revision_identity(),
    ).build_eligible

    forced = ForcedSelectionEvidence(
        requested_plugin_id="sic_surface",
        capability_match=True,
        reason="Explicit reviewed selection.",
    )
    fallback = FallbackEvidence(
        from_plugin_id="sic_primary",
        to_plugin_id="sic_surface",
        reason_code="fallback.reviewed",
        target_independently_matched=True,
    )
    with pytest.raises(ValidationError):
        _plan(forced_selection=forced, fallback=fallback)
    with pytest.raises(ValidationError):
        ForcedSelectionEvidence(
            requested_plugin_id="sic_surface",
            capability_match=False,
            reason="Unverified.",
        )


def test_ambiguity_and_no_match_evidence_are_sorted_and_unique() -> None:
    AmbiguityEvidence(
        tied_plugin_ids=("sic_alpha", "sic_beta"),
        match_kind=MatchKind.EXACT,
        specificity=100,
        priority=10,
        fail_closed=True,
    )
    NoMatchEvidence(
        evaluated_plugin_ids=("sic_alpha", "sic_beta"),
        reason_codes=("match.none",),
        fail_closed=True,
    )
    with pytest.raises(ValidationError):
        AmbiguityEvidence(
            tied_plugin_ids=("sic_beta", "sic_alpha"),
            match_kind=MatchKind.EXACT,
            specificity=100,
            priority=10,
            fail_closed=True,
        )
    with pytest.raises(ValidationError, match="duplicate"):
        NoMatchEvidence(
            evaluated_plugin_ids=("sic_alpha", "sic_alpha"),
            reason_codes=("match.none",),
            fail_closed=True,
        )
    for match_kind, specificity in (
        (MatchKind.NONE, 1),
        (MatchKind.COMPATIBLE, 0),
    ):
        with pytest.raises(ValidationError):
            AmbiguityEvidence(
                tied_plugin_ids=("sic_alpha", "sic_beta"),
                match_kind=match_kind,
                specificity=specificity,
                priority=10,
                fail_closed=True,
            )


@pytest.mark.parametrize(
    ("issues", "status", "preview_eligible"),
    [
        ((), ValidationStatus.PASS, True),
        (
            (_issue(RuntimeIssueKind.PREVIEW_WARNING),),
            ValidationStatus.PASS_WITH_WARNINGS,
            True,
        ),
        (
            (_issue(RuntimeIssueKind.INVALID_INPUT),),
            ValidationStatus.FAIL,
            False,
        ),
    ],
)
def test_domain_validation_truth_table(
    issues: tuple[RuntimeIssue, ...],
    status: ValidationStatus,
    preview_eligible: bool,
) -> None:
    DomainValidationReport(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        plugin_contract_version="1.0.0",
        plugin_implementation_version="1.2.0",
        model_spec_digest=model_spec_digest(_model_spec()),
        status=status,
        facts=(DomainFact(code="fact.layers", value=8, unit=None),),
        issues=issues,
        preview_eligible=preview_eligible,
    )
    wrong_status = (
        ValidationStatus.FAIL
        if status is not ValidationStatus.FAIL
        else ValidationStatus.PASS
    )
    with pytest.raises(ValidationError):
        DomainValidationReport(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            plugin_contract_version="1.0.0",
            plugin_implementation_version="1.2.0",
            model_spec_digest=model_spec_digest(_model_spec()),
            status=wrong_status,
            facts=(),
            issues=issues,
            preview_eligible=preview_eligible,
        )
    with pytest.raises(ValidationError):
        DomainValidationReport(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id="sic_surface",
            plugin_contract_version="1.0.0",
            plugin_implementation_version="1.2.0",
            model_spec_digest=model_spec_digest(_model_spec()),
            status=status,
            facts=(),
            issues=issues,
            preview_eligible=not preview_eligible,
        )


def test_domain_validation_cannot_claim_other_validation_layers() -> None:
    payload = {
        "contract_version": "1.0.0",
        "plugin_id": "sic_surface",
        "plugin_contract_version": "1.0.0",
        "plugin_implementation_version": "1.2.0",
        "model_spec_digest": model_spec_digest(_model_spec()).model_dump(mode="json"),
        "status": "pass",
        "facts": [],
        "issues": [],
        "preview_eligible": True,
        "scientifically_verified": True,
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DomainValidationReport.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.pop("description"),
        lambda value: value.update({"extra": True}),
        lambda value: value.update({"contract_version": "01.0.0"}),
        lambda value: value["capabilities"].update(
            {"materials": ["3C-SiC", "3C-SiC"]}
        ),
        lambda value: value["limits"].update(
            {"supported_periodicity_dimensions": [4]}
        ),
        lambda value: value["routing"].update({"priority": 1001}),
        lambda value: value["contracts"]["match"].update(
            {"output_contracts": ["ModelingPlan"]}
        ),
        lambda value: value["contracts"]["build"].update(
            {"output_contracts": ["ModelSpec"]}
        ),
    ],
)
def test_manifest_pydantic_matches_repository_schema_rejections(mutator) -> None:
    schema = json.loads(
        (ROOT / "schemas" / "domain_plugin.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    payload = _manifest_payload()
    assert not list(validator.iter_errors(payload))
    manifest = DomainPluginManifest.model_validate_json(json.dumps(payload))
    assert manifest.model_dump(mode="json", by_alias=True) == payload

    invalid = copy.deepcopy(payload)
    mutator(invalid)
    assert list(validator.iter_errors(invalid))
    with pytest.raises(ValidationError):
        DomainPluginManifest.model_validate_json(json.dumps(invalid))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"plugin_id": "sic_surface\n"}),
        lambda value: value.update({"contract_version": "1.0.0\n"}),
        lambda value: value.update({"implementation_version": "1.2.0\n"}),
        lambda value: value["capabilities"].update(
            {"scenarios": ["surface_slab\n"]}
        ),
        lambda value: value["contracts"]["match"].update(
            {"callable": "sic_surface.match\n"}
        ),
        lambda value: value.update(
            {
                "dependencies": [
                    {
                        "dependency_id": "runtime_contracts\n",
                        "kind": "shared_contract",
                        "version_constraint": ">=1.0.0",
                        "required": True,
                    }
                ]
            }
        ),
    ],
)
def test_manifest_pattern_semantics_match_repository_schema_for_final_newline(
    mutator,
) -> None:
    schema = json.loads(
        (ROOT / "schemas" / "domain_plugin.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    payload = _manifest_payload()
    mutator(payload)

    assert not list(validator.iter_errors(payload))
    manifest = DomainPluginManifest.model_validate_json(json.dumps(payload))
    assert manifest.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            PluginRouting,
            {
                "priority": 100,
                "ambiguity_policy": "fail_closed",
                "forced_selection_requires_capability_match": 1,
            },
        ),
        (
            PluginReferencePolicy,
            {
                "allowed_access_modes": ["none"],
                "hidden_holdout_access": 0,
                "final_reference_coordinate_access": 0,
            },
        ),
        (
            AmbiguityEvidence,
            {
                "tied_plugin_ids": ["sic_alpha", "sic_beta"],
                "match_kind": "exact",
                "specificity": 500,
                "priority": 100,
                "fail_closed": 1,
            },
        ),
    ],
)
def test_boolean_literal_contracts_reject_integer_lookalikes(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


def test_contract_digest_uses_repository_pattern_semantics() -> None:
    digest = contract_digest(
        {"marker": "newline"},
        contract_name="Demo.Contract\n",
        contract_version="1.0.0\n",
    )
    assert digest.contract_name == "Demo.Contract\n"
    assert digest.contract_version == "1.0.0\n"


def test_manifest_adds_only_declared_cross_field_checks() -> None:
    invalid = _manifest_payload()
    invalid["limits"].update({"min_atoms": 10, "max_atoms": 5})
    with pytest.raises(ValidationError, match="min_atoms"):
        DomainPluginManifest.model_validate_json(json.dumps(invalid))

    invalid = _manifest_payload()
    invalid["limits"].update({"supports_create": False, "supports_patch": False})
    with pytest.raises(ValidationError, match="at least one"):
        DomainPluginManifest.model_validate_json(json.dumps(invalid))

    properties = DomainPluginManifest.model_json_schema(by_alias=True)["properties"]
    assert set(properties) == set(_manifest_payload())
    contract_properties = properties["contracts"]
    assert "$ref" in contract_properties


def test_manifest_integral_json_numbers_match_draft_integer_semantics() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "domain_plugin.schema.json").read_text(encoding="utf-8")
    )
    payload = _manifest_payload()
    payload["limits"].update(
        {
            "min_atoms": 1.0,
            "max_atoms": 10000.0,
            "supported_periodicity_dimensions": [2.0, 3.0],
        }
    )
    payload["routing"]["priority"] = 100.0

    assert not list(Draft202012Validator(schema).iter_errors(payload))
    manifest = DomainPluginManifest.model_validate_json(json.dumps(payload))
    assert type(manifest.limits.min_atoms) is int
    assert type(manifest.limits.max_atoms) is int
    assert all(
        type(value) is int
        for value in manifest.limits.supported_periodicity_dimensions
    )
    assert type(manifest.routing.priority) is int

    strict_limits = manifest.limits.model_dump(mode="python")
    strict_limits["min_atoms"] = 1.0
    with pytest.raises(ValidationError):
        type(manifest.limits).model_validate(strict_limits)
    strict_routing = manifest.routing.model_dump(mode="python")
    strict_routing["priority"] = 100.0
    with pytest.raises(ValidationError):
        type(manifest.routing).model_validate(strict_routing)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value["limits"].update({"min_atoms": 1.5}),
        lambda value: value["limits"].update({"max_atoms": 10000.5}),
        lambda value: value["limits"].update(
            {"supported_periodicity_dimensions": [2.5]}
        ),
        lambda value: value["routing"].update({"priority": 100.5}),
        lambda value: value["limits"].update({"min_atoms": True}),
        lambda value: value["routing"].update({"priority": "100"}),
    ),
)
def test_manifest_noninteger_and_nonnumber_values_remain_invalid(mutator) -> None:
    schema = json.loads(
        (ROOT / "schemas" / "domain_plugin.schema.json").read_text(encoding="utf-8")
    )
    payload = _manifest_payload()
    mutator(payload)
    assert list(Draft202012Validator(schema).iter_errors(payload))
    with pytest.raises(ValidationError):
        DomainPluginManifest.model_validate_json(json.dumps(payload))


def test_semiconductor_plugin_protocol_has_exact_stage_signatures() -> None:
    methods = {
        name: inspect.signature(getattr(SemiconductorDomainPlugin, name))
        for name in ("match", "plan", "build", "validate")
    }
    assert tuple(methods["match"].parameters) == ("self", "intent")
    assert tuple(methods["plan"].parameters) == (
        "self",
        "intent",
        "current_state",
    )
    assert tuple(methods["build"].parameters) == ("self", "plan")
    assert tuple(methods["validate"].parameters) == ("self", "model")

    hints = get_type_hints(SemiconductorDomainPlugin.match)
    assert hints == {"intent": ModelingIntent, "return": MatchResult}
    plan_hints = get_type_hints(SemiconductorDomainPlugin.plan)
    assert plan_hints["intent"] is ModelingIntent
    assert plan_hints["return"] is ModelingPlan


def test_runtime_contract_json_roundtrip() -> None:
    match = MatchResult(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        kind=MatchKind.EXACT,
        specificity=100,
        reason_codes=("match.exact",),
        issues=(),
    )
    report = DomainValidationReport(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id="sic_surface",
        plugin_contract_version="1.0.0",
        plugin_implementation_version="1.2.0",
        model_spec_digest=model_spec_digest(_model_spec()),
        status=ValidationStatus.PASS,
        facts=(DomainFact(code="fact.layers", value=8, unit=None),),
        issues=(),
        preview_eligible=True,
    )
    values = (
        _intent(),
        ModelState.from_model_spec(_model_spec()),
        match,
        _plan(),
        report,
        DomainPluginManifest.model_validate_json(json.dumps(_manifest_payload())),
    )
    for value in values:
        restored = type(value).model_validate_json(value.model_dump_json(by_alias=True))
        assert restored == value
