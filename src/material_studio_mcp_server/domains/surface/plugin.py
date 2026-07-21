"""Passive runtime implementation for the fixed 3C-SiC(001) surface."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    DomainValidationReport,
    MatchKind,
    MatchResult,
    ModelKind,
    ModelState,
    ModelingIntent,
    ModelingPlan,
    ModelingQuestion,
    PlanStep,
    ReferenceAccessMode,
    ResolvedAssumption,
    RuntimeIssue,
    RuntimeIssueKind,
    RUNTIME_CONTRACT_VERSION,
    SemanticParameter,
    contract_digest,
)
from material_studio_mcp_server.specs import ModelSpec

from .constants import (
    ATOMIC_PLANE_COUNT,
    BILAYER_COUNT,
    CARBON_HYDROGEN_BOND_ANGSTROM,
    CONTRACT_VERSION,
    HYDROGENS_PER_BOTTOM_CARBON,
    IMPLEMENTATION_VERSION,
    IN_PLANE_REPEAT,
    MATERIAL,
    OPERATION,
    PLUGIN_ID,
    SCENARIO,
    SOURCE_ID,
    TOTAL_ATOM_COUNT,
    VACUUM_ANGSTROM,
)
from .geometry import build_fixed_model
from .validation import validate_fixed_model


_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


@dataclass(frozen=True)
class _ParameterRule:
    expected: object
    predicate: Callable[[object], bool]
    units: tuple[str | None, ...] = (None,)


def _is_exact_string(expected: str) -> Callable[[object], bool]:
    return lambda value: type(value) is str and value == expected


def _is_one_of_strings(*expected: str) -> Callable[[object], bool]:
    return lambda value: type(value) is str and value in expected


def _is_exact_integer(expected: int) -> Callable[[object], bool]:
    return lambda value: type(value) is int and value == expected


def _is_exact_real(expected: float) -> Callable[[object], bool]:
    def predicate(value: object) -> bool:
        return (
            type(value) in (int, float)
            and math.isfinite(float(value))
            and float(value) == expected
        )

    return predicate


def _is_exact_boolean(expected: bool) -> Callable[[object], bool]:
    return lambda value: type(value) is bool and value is expected


_FIXED_PARAMETER_RULES: dict[str, _ParameterRule] = {
    "atom_count": _ParameterRule(
        TOTAL_ATOM_COUNT,
        _is_exact_integer(TOTAL_ATOM_COUNT),
    ),
    "miller_indices": _ParameterRule(
        "(001)", _is_one_of_strings("(001)", "001")
    ),
    "surface_orientation": _ParameterRule(
        "(001)", _is_one_of_strings("(001)", "001", "3C-SiC(001)")
    ),
    "surface_face": _ParameterRule("Si", _is_exact_string("Si")),
    "face": _ParameterRule("Si", _is_exact_string("Si")),
    "in_plane_repeat": _ParameterRule(2, _is_exact_integer(2)),
    "in_plane_supercell": _ParameterRule("2x2", _is_exact_string("2x2")),
    "bilayer_count": _ParameterRule(4, _is_exact_integer(4)),
    "bilayers": _ParameterRule(4, _is_exact_integer(4)),
    "atomic_plane_count": _ParameterRule(8, _is_exact_integer(8)),
    "plane_count": _ParameterRule(8, _is_exact_integer(8)),
    "bottom_termination": _ParameterRule("C", _is_exact_string("C")),
    "top_termination": _ParameterRule("Si", _is_exact_string("Si")),
    "passivation_element": _ParameterRule("H", _is_exact_string("H")),
    "hydrogens_per_bottom_carbon": _ParameterRule(
        2, _is_exact_integer(2)
    ),
    "carbon_hydrogen_bond_angstrom": _ParameterRule(
        1.09,
        _is_exact_real(1.09),
        (None, "angstrom"),
    ),
    "c_h_bond_length": _ParameterRule(
        1.09,
        _is_exact_real(1.09),
        ("angstrom",),
    ),
    "vacuum_angstrom": _ParameterRule(
        15.0,
        _is_exact_real(15.0),
        (None, "angstrom"),
    ),
    "vacuum": _ParameterRule(
        15.0,
        _is_exact_real(15.0),
        ("angstrom",),
    ),
    "vacuum_definition": _ParameterRule(
        "total_gap_over_full_atomic_extent",
        _is_exact_string("total_gap_over_full_atomic_extent"),
    ),
    "full_atom_extent_centered": _ParameterRule(
        True, _is_exact_boolean(True)
    ),
    "ideal": _ParameterRule(True, _is_exact_boolean(True)),
    "unreconstructed": _ParameterRule(True, _is_exact_boolean(True)),
    "relaxed": _ParameterRule(False, _is_exact_boolean(False)),
    "unrelaxed": _ParameterRule(True, _is_exact_boolean(True)),
    "simulation_count": _ParameterRule(0, _is_exact_integer(0)),
    "revision": _ParameterRule(0, _is_exact_integer(0)),
}


_CANONICAL_STEP_PARAMETERS = (
    SemanticParameter(name="atom_count", value=TOTAL_ATOM_COUNT),
    SemanticParameter(name="miller_indices", value="(001)"),
    SemanticParameter(name="surface_face", value="Si"),
    SemanticParameter(name="in_plane_repeat", value=IN_PLANE_REPEAT),
    SemanticParameter(name="bilayer_count", value=BILAYER_COUNT),
    SemanticParameter(name="atomic_plane_count", value=ATOMIC_PLANE_COUNT),
    SemanticParameter(name="bottom_termination", value="C"),
    SemanticParameter(name="top_termination", value="Si"),
    SemanticParameter(
        name="hydrogens_per_bottom_carbon",
        value=HYDROGENS_PER_BOTTOM_CARBON,
    ),
    SemanticParameter(
        name="carbon_hydrogen_bond_angstrom",
        value=CARBON_HYDROGEN_BOND_ANGSTROM,
        unit="angstrom",
    ),
    SemanticParameter(
        name="vacuum_angstrom",
        value=VACUUM_ANGSTROM,
        unit="angstrom",
    ),
    SemanticParameter(
        name="vacuum_definition",
        value="total_gap_over_full_atomic_extent",
    ),
    SemanticParameter(name="full_atom_extent_centered", value=True),
    SemanticParameter(name="ideal", value=True),
    SemanticParameter(name="unreconstructed", value=True),
    SemanticParameter(name="relaxed", value=False),
    SemanticParameter(name="simulation_count", value=0),
    SemanticParameter(name="revision", value=0),
)


def _issue(
    kind: RuntimeIssueKind,
    code: str,
    message: str,
    field_path: str,
) -> RuntimeIssue:
    return RuntimeIssue(
        kind=kind,
        code=code,
        message=message,
        field_path=field_path,
    )


def _project_parameter(intent: ModelingIntent) -> SemanticParameter | None:
    return next(
        (
            parameter
            for parameter in intent.parameters
            if parameter.name == "project_id"
        ),
        None,
    )


def _routing_issues(intent: ModelingIntent) -> tuple[RuntimeIssue, ...]:
    issues: list[RuntimeIssue] = []
    expected_fields = (
        ("material", intent.material, MATERIAL),
        ("scenario", intent.scenario, SCENARIO),
        ("operation", intent.operation, OPERATION),
        ("model_kind", intent.model_kind, ModelKind.CRYSTAL),
        (
            "requires_current_model",
            intent.requires_current_model,
            False,
        ),
        ("output_kind", intent.output_kind, BuildOutputKind.MODEL_SPEC),
    )
    for field_name, observed, expected in expected_fields:
        if type(observed) is not type(expected) or observed != expected:
            expected_value = getattr(expected, "value", expected)
            issues.append(
                _issue(
                    RuntimeIssueKind.UNSUPPORTED,
                    f"unsupported_{field_name}",
                    f"{field_name} must be exactly {expected_value!r}",
                    field_name,
                )
            )
    if (
        intent.reference_access.mode is not ReferenceAccessMode.TASK_ONLY
        or intent.reference_access.source_ids != (SOURCE_ID,)
    ):
        issues.append(
            _issue(
                RuntimeIssueKind.UNSUPPORTED,
                "unsupported_reference_access",
                "The fixed profile requires task-only access bound to cod-1010995.",
                "reference_access",
            )
        )
    for index, _requirement in enumerate(intent.semantic_requirements):
        issues.append(
            _issue(
                RuntimeIssueKind.UNSUPPORTED,
                "unsupported_semantic_requirement",
                "Additional semantic requirements are outside the fixed profile; "
                "supply supported geometry as typed parameters.",
                f"semantic_requirements.{index}",
            )
        )
    for index, _assumption in enumerate(intent.declared_assumptions):
        issues.append(
            _issue(
                RuntimeIssueKind.UNSUPPORTED,
                "unsupported_declared_assumption",
                "Caller-declared assumptions are outside the fixed profile.",
                f"declared_assumptions.{index}",
            )
        )
    return tuple(issues)


def _parameter_issues(intent: ModelingIntent) -> tuple[RuntimeIssue, ...]:
    issues: list[RuntimeIssue] = []
    project_parameter = _project_parameter(intent)
    if project_parameter is None:
        issues.append(
            _issue(
                RuntimeIssueKind.NEEDS_USER_INPUT,
                "project_id_required",
                "An explicit project_id semantic parameter is required.",
                "parameters.project_id",
            )
        )
    elif (
        type(project_parameter.value) is not str
        or _PROJECT_ID_PATTERN.fullmatch(project_parameter.value) is None
        or project_parameter.unit is not None
    ):
        issues.append(
            _issue(
                RuntimeIssueKind.INVALID_INPUT,
                "invalid_project_id",
                "project_id must be a unitless string containing only letters, "
                "digits, underscores, or hyphens.",
                "parameters.project_id",
            )
        )

    for parameter in intent.parameters:
        if parameter.name == "project_id":
            continue
        rule = _FIXED_PARAMETER_RULES.get(parameter.name)
        if rule is None:
            issues.append(
                _issue(
                    RuntimeIssueKind.UNSUPPORTED,
                    "unsupported_parameter",
                    f"Parameter {parameter.name!r} is outside the fixed profile.",
                    f"parameters.{parameter.name}",
                )
            )
            continue
        if not rule.predicate(parameter.value) or parameter.unit not in rule.units:
            issues.append(
                _issue(
                    RuntimeIssueKind.INVALID_INPUT,
                    "conflicting_fixed_parameter",
                    f"Parameter {parameter.name!r} conflicts with the fixed "
                    f"value {rule.expected!r}.",
                    f"parameters.{parameter.name}",
                )
            )
    return tuple(issues)


def match(intent: ModelingIntent) -> MatchResult:
    """Match only the exact create-new fixed-profile intent."""

    if not isinstance(intent, ModelingIntent):
        raise TypeError("match requires a ModelingIntent")
    routing = _routing_issues(intent)
    parameter_issues = _parameter_issues(intent)
    blocking = tuple(
        issue
        for issue in (*routing, *parameter_issues)
        if issue.kind is not RuntimeIssueKind.NEEDS_USER_INPUT
    )
    if blocking:
        reason_codes = tuple(sorted({issue.code for issue in blocking}))
        return MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id=PLUGIN_ID,
            kind=MatchKind.NONE,
            specificity=0,
            reason_codes=reason_codes,
            issues=blocking,
        )

    needs_project = any(
        issue.kind is RuntimeIssueKind.NEEDS_USER_INPUT
        for issue in parameter_issues
    )
    reason_codes = (
        ("exact_fixed_profile", "project_id_required")
        if needs_project
        else ("exact_fixed_profile",)
    )
    return MatchResult(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id=PLUGIN_ID,
        kind=MatchKind.EXACT,
        specificity=1000,
        reason_codes=reason_codes,
        issues=parameter_issues,
    )


def plan(
    intent: ModelingIntent,
    current_state: ModelState | None,
) -> ModelingPlan:
    """Create a deterministic preview plan without reading or mutating state."""

    if not isinstance(intent, ModelingIntent):
        raise TypeError("plan requires a ModelingIntent")
    if current_state is not None and not isinstance(current_state, ModelState):
        raise TypeError("current_state must be a ModelState or None")

    issues = list((*_routing_issues(intent), *_parameter_issues(intent)))
    if current_state is not None:
        issues.append(
            _issue(
                RuntimeIssueKind.UNSUPPORTED,
                "current_model_not_allowed",
                "This create-only plugin requires no current model.",
                "current_state",
            )
        )

    project_parameter = _project_parameter(intent)
    questions: tuple[ModelingQuestion, ...] = ()
    if project_parameter is None:
        questions = (
            ModelingQuestion(
                question_id="project_id_required",
                prompt="What project_id should identify the preview model?",
                parameter_name="project_id",
                choices=None,
            ),
        )

    has_blocking = any(issue.is_blocking for issue in issues)
    steps: tuple[PlanStep, ...] = ()
    assumptions: tuple[ResolvedAssumption, ...] = ()
    if not has_blocking and project_parameter is not None:
        steps = (
            PlanStep(
                step_id="build_fixed_slab",
                operation=OPERATION,
                parameters=(
                    SemanticParameter(name="project_id", value=project_parameter.value),
                    *_CANONICAL_STEP_PARAMETERS,
                ),
            ),
        )
        assumptions = (
            ResolvedAssumption(
                code="fixed_surface_profile",
                statement=(
                    "Use the ideal unreconstructed unrelaxed 3C-SiC(001) "
                    "Si-face 2x2 four-bilayer profile."
                ),
                source="declared_default",
            ),
            ResolvedAssumption(
                code="fixed_bottom_passivation",
                statement=(
                    "Terminate each of the eight bottom carbon atoms with two "
                    "1.09 angstrom hydrogen back bonds."
                ),
                source="declared_default",
            ),
            ResolvedAssumption(
                code="preview_only",
                statement="Create revision zero with no simulations or execution.",
                source="declared_default",
            ),
        )

    return ModelingPlan(
        contract_version=RUNTIME_CONTRACT_VERSION,
        plugin_id=PLUGIN_ID,
        plugin_contract_version=CONTRACT_VERSION,
        plugin_implementation_version=IMPLEMENTATION_VERSION,
        normalized_intent_digest=contract_digest(
            intent,
            contract_name="ModelingIntent",
            contract_version=RUNTIME_CONTRACT_VERSION,
        ),
        current_revision=None,
        output_kind=BuildOutputKind.MODEL_SPEC,
        steps=steps,
        assumptions=assumptions,
        questions=questions,
        issues=tuple(issues),
        forced_selection=None,
        fallback=None,
        build_eligible=bool(steps) and not questions and not has_blocking,
    )


def _step_parameter_map(step: PlanStep) -> dict[str, SemanticParameter]:
    return {parameter.name: parameter for parameter in step.parameters}


def build(plan_value: ModelingPlan) -> ModelSpec:
    """Materialize the fixed ModelSpec from an eligible canonical plan."""

    if not isinstance(plan_value, ModelingPlan):
        raise TypeError("build requires a ModelingPlan")
    if (
        plan_value.plugin_id != PLUGIN_ID
        or plan_value.plugin_contract_version != CONTRACT_VERSION
        or plan_value.plugin_implementation_version != IMPLEMENTATION_VERSION
    ):
        raise ValueError("plan plugin identity does not match this plugin")
    if not plan_value.build_eligible:
        raise ValueError("plan is not build eligible")
    if (
        plan_value.output_kind is not BuildOutputKind.MODEL_SPEC
        or plan_value.current_revision is not None
        or len(plan_value.steps) != 1
    ):
        raise ValueError("plan does not describe one create-new ModelSpec step")

    step = plan_value.steps[0]
    if step.step_id != "build_fixed_slab" or step.operation != OPERATION:
        raise ValueError("plan step is not the fixed surface build step")
    parameters = _step_parameter_map(step)
    expected_names = {
        "project_id",
        *(parameter.name for parameter in _CANONICAL_STEP_PARAMETERS),
    }
    if set(parameters) != expected_names:
        raise ValueError("plan step parameters do not match the fixed profile")

    project_parameter = parameters["project_id"]
    if (
        type(project_parameter.value) is not str
        or _PROJECT_ID_PATTERN.fullmatch(project_parameter.value) is None
        or project_parameter.unit is not None
    ):
        raise ValueError("plan project_id is invalid")
    for expected in _CANONICAL_STEP_PARAMETERS:
        if parameters[expected.name] != expected:
            raise ValueError(
                f"plan parameter {expected.name!r} does not match the fixed profile"
            )
    return build_fixed_model(project_parameter.value)


@dataclass(frozen=True)
class _SurfacePlugin:
    plugin_id: str = PLUGIN_ID
    contract_version: str = CONTRACT_VERSION
    implementation_version: str = IMPLEMENTATION_VERSION

    def match(self, intent: ModelingIntent) -> MatchResult:
        return match(intent)

    def plan(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None,
    ) -> ModelingPlan:
        return plan(intent, current_state)

    def build(self, plan_value: ModelingPlan) -> ModelSpec:
        return build(plan_value)

    def validate(self, model: ModelSpec) -> DomainValidationReport:
        return validate_fixed_model(model)


PLUGIN = _SurfacePlugin()


__all__ = ["PLUGIN", "build", "match", "plan"]
