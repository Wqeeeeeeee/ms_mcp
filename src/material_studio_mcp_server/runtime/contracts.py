"""Frozen runtime contracts for internal semiconductor domain plugins."""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import Enum
from typing import Annotated, Any, Literal, Mapping, Protocol, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from material_studio_mcp_server.specs import ModelSpec, SemanticPatch


RUNTIME_CONTRACT_VERSION = "1.0.0"
HASH_PROFILE = "material_studio_runtime_contract_hash_v1"
ADAPTER_CONTRACT_VERSION = "1.0.0"

IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_.-]{1,127}$"
PLUGIN_ID_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
SEMANTIC_VERSION_PATTERN = (
    r"^[1-9][0-9]*\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
)
CONTRACT_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
PROJECT_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
MANIFEST_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"
MANIFEST_CALLABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.:]*$"
DEPENDENCY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$"


def _validate_schema_pattern(
    value: str,
    *,
    pattern: str,
    label: str,
) -> str:
    if re.search(pattern, value) is None:
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _schema_pattern_validator(pattern: str, label: str) -> AfterValidator:
    def validate(value: str) -> str:
        return _validate_schema_pattern(value, pattern=pattern, label=label)

    return AfterValidator(validate)


def _strict_boolean_literal(expected: bool) -> BeforeValidator:
    def validate(value: Any) -> bool:
        if type(value) is not bool or value is not expected:
            raise ValueError(f"value must be the boolean literal {expected!r}")
        return value

    return BeforeValidator(validate)


Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
PluginId = Annotated[
    str,
    _schema_pattern_validator(PLUGIN_ID_PATTERN, "plugin ID"),
    WithJsonSchema({"type": "string", "pattern": PLUGIN_ID_PATTERN}),
]
SemanticVersion = Annotated[
    str,
    _schema_pattern_validator(SEMANTIC_VERSION_PATTERN, "semantic version"),
    WithJsonSchema({"type": "string", "pattern": SEMANTIC_VERSION_PATTERN}),
]
ContractName = Annotated[
    str,
    _schema_pattern_validator(CONTRACT_NAME_PATTERN, "contract name"),
    WithJsonSchema({"type": "string", "pattern": CONTRACT_NAME_PATTERN}),
]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProjectId = Annotated[
    str,
    Field(min_length=1, max_length=120, pattern=PROJECT_ID_PATTERN),
]
StrictScalar: TypeAlias = str | bool | int | float
StrictTrue = Annotated[Literal[True], _strict_boolean_literal(True)]
StrictFalse = Annotated[Literal[False], _strict_boolean_literal(False)]


def _preflight_json_value(
    value: Any,
    active_container_ids: set[int] | None = None,
) -> None:
    """Reject raw values that Pydantic could lossy-convert during serialization."""

    if active_container_ids is None:
        active_container_ids = set()
    if isinstance(value, Enum):
        _preflight_json_value(value.value, active_container_ids)
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON rejects NaN and Infinity")
        return
    if isinstance(value, (set, frozenset)):
        raise TypeError("canonical JSON rejects unordered sets")

    is_model = isinstance(value, BaseModel)
    is_mapping = isinstance(value, Mapping)
    is_array = isinstance(value, (list, tuple))
    if not (is_model or is_mapping or is_array):
        raise TypeError(
            "canonical JSON does not support values of type "
            f"{type(value).__name__}"
        )

    container_id = id(value)
    if container_id in active_container_ids:
        raise TypeError("canonical JSON rejects cyclic values")
    active_container_ids.add(container_id)
    try:
        if is_model:
            for field_name in type(value).model_fields:
                _preflight_json_value(
                    getattr(value, field_name),
                    active_container_ids,
                )
        elif is_mapping:
            keys = tuple(value.keys())
            if any(not isinstance(key, str) for key in keys):
                raise TypeError("canonical JSON requires string mapping keys")
            for key in sorted(keys):
                _preflight_json_value(value[key], active_container_ids)
        else:
            for item in value:
                _preflight_json_value(item, active_container_ids)
    finally:
        active_container_ids.remove(container_id)


def _json_compatible(value: Any) -> Any:
    """Return a JSON-compatible copy under the runtime canonical profile."""

    if isinstance(value, BaseModel):
        return _json_compatible(
            value.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=False,
                exclude_defaults=False,
                exclude_unset=False,
            )
        )
    if isinstance(value, Enum):
        return _json_compatible(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON rejects NaN and Infinity")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON requires string mapping keys")
            result[key] = _json_compatible(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("canonical JSON rejects unordered sets")
    raise TypeError(
        f"canonical JSON does not support values of type {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one payload as compact, sorted-key UTF-8 without a BOM."""

    _preflight_json_value(value)
    return json.dumps(
        _json_compatible(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    """Return the canonical payload JSON text used by the runtime profile."""

    return canonical_json_bytes(value).decode("utf-8")


def _tuple_item_key(value: Any) -> bytes:
    return canonical_json_bytes(value)


class FrozenContractModel(BaseModel):
    """Closed, strict, immutable base for every runtime contract model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
    )

    @field_validator("*", mode="after")
    @classmethod
    def reject_duplicate_tuple_values(cls, value: Any) -> Any:
        if not isinstance(value, tuple):
            return value
        seen: set[bytes] = set()
        for item in value:
            key = _tuple_item_key(item)
            if key in seen:
                raise ValueError("tuple fields must not contain duplicate values")
            seen.add(key)
        return value


class MatchKind(str, Enum):
    NONE = "none"
    COMPATIBLE = "compatible"
    EXACT = "exact"


class RuntimeIssueKind(str, Enum):
    UNSUPPORTED = "unsupported"
    INVALID_INPUT = "invalid_input"
    NEEDS_USER_INPUT = "needs_user_input"
    PREVIEW_WARNING = "preview_warning"
    INTERNAL_ERROR = "internal_error"


class MigrationMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


class RuntimeOutcome(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ValidationStatus(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"


class ModelKind(str, Enum):
    MOLECULE = "molecule"
    CRYSTAL = "crystal"
    IMPORTED_STRUCTURE = "imported_structure"


class BuildOutputKind(str, Enum):
    MODEL_SPEC = "model_spec"
    SEMANTIC_PATCH = "semantic_patch"


class ReferenceAccessMode(str, Enum):
    NONE = "none"
    METADATA_ONLY = "metadata_only"
    TASK_ONLY = "task_only"


class DependencyKind(str, Enum):
    PYTHON_PACKAGE = "python_package"
    SHARED_CONTRACT = "shared_contract"
    SHARED_SERVICE = "shared_service"


class StageName(str, Enum):
    MATCH = "match"
    PLAN = "plan"
    BUILD = "build"
    VALIDATE = "validate"


class StageStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class ContractDigest(FrozenContractModel):
    """SHA-256 binding for one named and versioned contract payload."""

    hash_profile: Literal[HASH_PROFILE]
    contract_name: ContractName
    contract_version: SemanticVersion
    algorithm: Literal["sha256"]
    sha256: Sha256


def contract_digest(
    payload: Any,
    *,
    contract_name: str,
    contract_version: str,
) -> ContractDigest:
    """Hash a payload in the exact named/versioned runtime envelope."""

    _validate_schema_pattern(
        contract_name,
        pattern=CONTRACT_NAME_PATTERN,
        label="contract name",
    )
    _validate_schema_pattern(
        contract_version,
        pattern=SEMANTIC_VERSION_PATTERN,
        label="semantic contract version",
    )
    envelope = {
        "hash_profile": HASH_PROFILE,
        "contract_name": contract_name,
        "contract_version": contract_version,
        "payload": payload,
    }
    sha256 = hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()
    return ContractDigest(
        hash_profile=HASH_PROFILE,
        contract_name=contract_name,
        contract_version=contract_version,
        algorithm="sha256",
        sha256=sha256,
    )


def model_spec_digest(spec: ModelSpec) -> ContractDigest:
    """Hash an existing ModelSpec through the v1 adapter contract."""

    if not isinstance(spec, ModelSpec):
        raise TypeError("model_spec_digest requires a ModelSpec")
    return contract_digest(
        spec,
        contract_name="ModelSpec",
        contract_version=ADAPTER_CONTRACT_VERSION,
    )


def semantic_patch_digest(patch: SemanticPatch) -> ContractDigest:
    """Hash an existing SemanticPatch through the v1 adapter contract."""

    if not isinstance(patch, SemanticPatch):
        raise TypeError("semantic_patch_digest requires a SemanticPatch")
    return contract_digest(
        patch,
        contract_name="SemanticPatch",
        contract_version=ADAPTER_CONTRACT_VERSION,
    )


def require_digest_binding(
    digest: ContractDigest,
    contract_name: str,
    contract_version: str = RUNTIME_CONTRACT_VERSION,
) -> None:
    """Reject a digest whose declared contract identity is not exact."""

    if (
        digest.contract_name != contract_name
        or digest.contract_version != contract_version
    ):
        raise ValueError(
            "digest binding must name "
            f"{contract_name} version {contract_version}"
        )


class SemanticParameter(FrozenContractModel):
    name: Identifier
    value: StrictScalar
    unit: str | None = None


class ReferenceAccess(FrozenContractModel):
    mode: ReferenceAccessMode
    source_ids: tuple[Identifier, ...]
    raw_structure_access: StrictFalse
    final_coordinate_access: StrictFalse
    hidden_holdout_access: StrictFalse

    @model_validator(mode="after")
    def validate_none_mode(self) -> "ReferenceAccess":
        if self.mode is ReferenceAccessMode.NONE and self.source_ids:
            raise ValueError("reference mode none requires empty source_ids")
        return self


class ModelingIntent(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    request_id: Identifier
    material: str = Field(min_length=1)
    scenario: Identifier
    operation: Identifier
    model_kind: ModelKind
    requires_current_model: bool
    output_kind: BuildOutputKind
    parameters: tuple[SemanticParameter, ...]
    semantic_requirements: tuple[str, ...]
    declared_assumptions: tuple[str, ...]
    reference_access: ReferenceAccess

    @model_validator(mode="after")
    def validate_intent(self) -> "ModelingIntent":
        names = tuple(parameter.name for parameter in self.parameters)
        if len(set(names)) != len(names):
            raise ValueError("ModelingIntent parameter names must be unique")
        if (
            self.output_kind is BuildOutputKind.SEMANTIC_PATCH
            and not self.requires_current_model
        ):
            raise ValueError(
                "semantic_patch output requires requires_current_model=true"
            )
        return self


class ModelState(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    project_id: ProjectId
    revision: int = Field(ge=1)
    model_kind: ModelKind
    canonical_model_spec_json: str
    model_spec_digest: ContractDigest
    immutable: StrictTrue
    observed_as_current: StrictTrue

    @classmethod
    def from_model_spec(cls, spec: ModelSpec) -> "ModelState":
        if not isinstance(spec, ModelSpec):
            raise TypeError("ModelState.from_model_spec requires a ModelSpec")
        return cls(
            contract_version=RUNTIME_CONTRACT_VERSION,
            project_id=spec.project_id,
            revision=spec.revision,
            model_kind=ModelKind(spec.model_type.value),
            canonical_model_spec_json=canonical_json(spec),
            model_spec_digest=model_spec_digest(spec),
            immutable=True,
            observed_as_current=True,
        )

    def parse_model_spec(self) -> ModelSpec:
        """Parse and return a fresh mutable ModelSpec instance."""

        return ModelSpec.model_validate_json(self.canonical_model_spec_json)

    @model_validator(mode="after")
    def validate_model_state(self) -> "ModelState":
        require_digest_binding(
            self.model_spec_digest,
            "ModelSpec",
            ADAPTER_CONTRACT_VERSION,
        )
        try:
            spec = ModelSpec.model_validate_json(self.canonical_model_spec_json)
        except Exception as exc:
            raise ValueError("canonical_model_spec_json is not a ModelSpec") from exc
        if canonical_json(spec) != self.canonical_model_spec_json:
            raise ValueError("canonical_model_spec_json is not byte-for-byte canonical")
        if spec.project_id != self.project_id:
            raise ValueError("ModelState project_id does not match ModelSpec")
        if spec.revision != self.revision:
            raise ValueError("ModelState revision does not match ModelSpec")
        if spec.model_type.value != self.model_kind.value:
            raise ValueError("ModelState model_kind does not match ModelSpec")
        if model_spec_digest(spec) != self.model_spec_digest:
            raise ValueError("ModelState ModelSpec digest mismatch")
        return self


class RuntimeIssue(FrozenContractModel):
    kind: RuntimeIssueKind
    code: Identifier
    message: str = Field(min_length=1)
    field_path: str = Field(min_length=1)

    @property
    def is_blocking(self) -> bool:
        return self.kind is not RuntimeIssueKind.PREVIEW_WARNING


class MatchResult(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    plugin_id: PluginId
    kind: MatchKind
    specificity: int = Field(ge=0, le=1000)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    issues: tuple[RuntimeIssue, ...]

    @model_validator(mode="after")
    def validate_match(self) -> "MatchResult":
        if self.kind is MatchKind.NONE and self.specificity != 0:
            raise ValueError("none match requires specificity=0")
        if self.kind is not MatchKind.NONE and self.specificity < 1:
            raise ValueError("compatible/exact match requires specificity>=1")
        forbidden = {
            RuntimeIssueKind.UNSUPPORTED,
            RuntimeIssueKind.INVALID_INPUT,
            RuntimeIssueKind.INTERNAL_ERROR,
        }
        if self.kind is not MatchKind.NONE and any(
            issue.kind in forbidden for issue in self.issues
        ):
            raise ValueError(
                "compatible/exact match cannot contain unsupported, "
                "invalid_input, or internal_error issues"
            )
        return self


class ModelingQuestion(FrozenContractModel):
    question_id: Identifier
    prompt: str = Field(min_length=1)
    parameter_name: Identifier
    choices: tuple[StrictScalar, ...] | None = None


class ResolvedAssumption(FrozenContractModel):
    code: Identifier
    statement: str = Field(min_length=1)
    source: Literal["user", "declared_default", "current_state"]


class PlanStep(FrozenContractModel):
    step_id: Identifier
    operation: Identifier
    parameters: tuple[SemanticParameter, ...]

    @model_validator(mode="after")
    def validate_parameter_names(self) -> "PlanStep":
        names = tuple(parameter.name for parameter in self.parameters)
        if len(set(names)) != len(names):
            raise ValueError("PlanStep parameter names must be unique")
        return self


class RevisionIdentity(FrozenContractModel):
    project_id: ProjectId
    revision: int = Field(ge=1)
    model_spec_digest: ContractDigest

    @model_validator(mode="after")
    def validate_digest(self) -> "RevisionIdentity":
        require_digest_binding(
            self.model_spec_digest,
            "ModelSpec",
            ADAPTER_CONTRACT_VERSION,
        )
        return self


class ForcedSelectionEvidence(FrozenContractModel):
    requested_plugin_id: PluginId
    capability_match: StrictTrue
    reason: str = Field(min_length=1)


class FallbackEvidence(FrozenContractModel):
    from_plugin_id: PluginId
    to_plugin_id: PluginId
    reason_code: Identifier
    target_independently_matched: StrictTrue


class AmbiguityEvidence(FrozenContractModel):
    tied_plugin_ids: tuple[PluginId, ...] = Field(min_length=2)
    match_kind: MatchKind
    specificity: int = Field(ge=1, le=1000)
    priority: int = Field(ge=-1000, le=1000)
    fail_closed: StrictTrue

    @model_validator(mode="after")
    def validate_lexical_order(self) -> "AmbiguityEvidence":
        if self.match_kind is MatchKind.NONE:
            raise ValueError("ambiguity requires compatible or exact match_kind")
        if self.tied_plugin_ids != tuple(sorted(self.tied_plugin_ids)):
            raise ValueError("tied_plugin_ids must be lexically sorted")
        return self


class NoMatchEvidence(FrozenContractModel):
    evaluated_plugin_ids: tuple[PluginId, ...]
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    fail_closed: StrictTrue

    @model_validator(mode="after")
    def validate_lexical_order(self) -> "NoMatchEvidence":
        if self.evaluated_plugin_ids != tuple(sorted(self.evaluated_plugin_ids)):
            raise ValueError("evaluated_plugin_ids must be lexically sorted")
        return self


class ModelingPlan(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    plugin_id: PluginId
    plugin_contract_version: SemanticVersion
    plugin_implementation_version: SemanticVersion
    normalized_intent_digest: ContractDigest
    current_revision: RevisionIdentity | None
    output_kind: BuildOutputKind
    steps: tuple[PlanStep, ...]
    assumptions: tuple[ResolvedAssumption, ...]
    questions: tuple[ModelingQuestion, ...]
    issues: tuple[RuntimeIssue, ...]
    forced_selection: ForcedSelectionEvidence | None
    fallback: FallbackEvidence | None
    build_eligible: bool

    @model_validator(mode="after")
    def validate_plan(self) -> "ModelingPlan":
        require_digest_binding(
            self.normalized_intent_digest,
            "ModelingIntent",
            RUNTIME_CONTRACT_VERSION,
        )
        expected_build_eligible = bool(self.steps) and not self.questions and not any(
            issue.is_blocking for issue in self.issues
        )
        if self.build_eligible is not expected_build_eligible:
            raise ValueError("build_eligible does not match the plan truth table")
        if (
            self.output_kind is BuildOutputKind.SEMANTIC_PATCH
            and self.current_revision is None
        ):
            raise ValueError("semantic_patch plan requires current_revision")
        if self.forced_selection is not None and self.fallback is not None:
            raise ValueError("forced_selection and fallback are mutually exclusive")
        if (
            self.forced_selection is not None
            and self.forced_selection.requested_plugin_id != self.plugin_id
        ):
            raise ValueError("forced selection plugin does not match plan plugin")
        if self.fallback is not None and self.fallback.to_plugin_id != self.plugin_id:
            raise ValueError("fallback target does not match plan plugin")
        return self


class DomainFact(FrozenContractModel):
    code: Identifier
    value: StrictScalar
    unit: str | None = None


class DomainValidationReport(FrozenContractModel):
    contract_version: Literal[RUNTIME_CONTRACT_VERSION]
    plugin_id: PluginId
    plugin_contract_version: SemanticVersion
    plugin_implementation_version: SemanticVersion
    model_spec_digest: ContractDigest
    status: ValidationStatus
    facts: tuple[DomainFact, ...]
    issues: tuple[RuntimeIssue, ...]
    preview_eligible: bool

    @model_validator(mode="after")
    def validate_report(self) -> "DomainValidationReport":
        require_digest_binding(
            self.model_spec_digest,
            "ModelSpec",
            ADAPTER_CONTRACT_VERSION,
        )
        has_blocking = any(issue.is_blocking for issue in self.issues)
        expected_status = (
            ValidationStatus.FAIL
            if has_blocking
            else (
                ValidationStatus.PASS_WITH_WARNINGS
                if self.issues
                else ValidationStatus.PASS
            )
        )
        if self.status is not expected_status:
            raise ValueError("validation status does not match issue truth table")
        if self.preview_eligible is has_blocking:
            raise ValueError("preview_eligible does not match issue truth table")
        return self


ManifestMaterial = Annotated[str, Field(min_length=1, max_length=128)]
ManifestName = Annotated[
    str,
    _schema_pattern_validator(MANIFEST_NAME_PATTERN, "manifest name"),
    WithJsonSchema({"type": "string", "pattern": MANIFEST_NAME_PATTERN}),
]
ManifestCapability = Annotated[str, Field(min_length=1)]
ManifestCallable = Annotated[
    str,
    _schema_pattern_validator(MANIFEST_CALLABLE_PATTERN, "manifest callable"),
    WithJsonSchema({"type": "string", "pattern": MANIFEST_CALLABLE_PATTERN}),
]
DependencyId = Annotated[
    str,
    _schema_pattern_validator(DEPENDENCY_ID_PATTERN, "dependency ID"),
    WithJsonSchema({"type": "string", "pattern": DEPENDENCY_ID_PATTERN}),
]


class PluginCapabilities(FrozenContractModel):
    materials: tuple[ManifestMaterial, ...] = Field(min_length=1)
    scenarios: tuple[ManifestName, ...] = Field(min_length=1)
    operations: tuple[ManifestName, ...] = Field(min_length=1)


class PluginLimits(FrozenContractModel):
    min_atoms: int = Field(ge=0)
    max_atoms: int | None = Field(ge=1)
    supported_periodicity_dimensions: tuple[
        Annotated[int, Field(ge=0, le=3)], ...
    ] = Field(min_length=1)
    supported_model_kinds: tuple[ModelKind, ...] = Field(min_length=1)
    requires_current_model: bool
    supports_create: bool
    supports_patch: bool
    supports_calculation_plan: bool
    unsupported_capabilities: tuple[ManifestCapability, ...]

    @field_validator("min_atoms", "max_atoms", mode="before")
    @classmethod
    def normalize_json_integers(cls, value: Any, info: ValidationInfo) -> Any:
        if (
            info.mode == "json"
            and isinstance(value, float)
            and math.isfinite(value)
            and value.is_integer()
        ):
            return int(value)
        return value

    @field_validator("supported_periodicity_dimensions", mode="before")
    @classmethod
    def normalize_json_integer_items(
        cls,
        value: Any,
        info: ValidationInfo,
    ) -> Any:
        if info.mode != "json" or not isinstance(value, list):
            return value
        return tuple(
            int(item)
            if (
                isinstance(item, float)
                and math.isfinite(item)
                and item.is_integer()
            )
            else item
            for item in value
        )

    @model_validator(mode="after")
    def validate_limits(self) -> "PluginLimits":
        if self.max_atoms is not None and self.min_atoms > self.max_atoms:
            raise ValueError("min_atoms must not exceed max_atoms")
        if not self.supports_create and not self.supports_patch:
            raise ValueError("at least one of supports_create/supports_patch is required")
        return self


class PluginRouting(FrozenContractModel):
    priority: int = Field(ge=-1000, le=1000)
    ambiguity_policy: Literal["fail_closed"]
    forced_selection_requires_capability_match: StrictTrue

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_json_integer(cls, value: Any, info: ValidationInfo) -> Any:
        if (
            info.mode == "json"
            and isinstance(value, float)
            and math.isfinite(value)
            and value.is_integer()
        ):
            return int(value)
        return value


class PluginReferencePolicy(FrozenContractModel):
    allowed_access_modes: tuple[ReferenceAccessMode, ...] = Field(min_length=1)
    hidden_holdout_access: StrictFalse
    final_reference_coordinate_access: StrictFalse


class PluginRuntimeBehavior(FrozenContractModel):
    deterministic: StrictTrue
    preview_first: StrictTrue
    mutates_input_model: StrictFalse
    owns_revision_state: StrictFalse
    executes_backend_directly: StrictFalse
    registers_public_mcp_tools: StrictFalse
    owns_gui_session: StrictFalse
    network_access_during_match_plan_build_validate: StrictFalse


class PluginStageContract(FrozenContractModel):
    callable: ManifestCallable
    input_contracts: tuple[ContractName, ...] = Field(min_length=1)
    output_contracts: tuple[ContractName, ...] = Field(min_length=1)
    deterministic: StrictTrue
    filesystem_side_effects: StrictFalse
    process_side_effects: StrictFalse
    network_access: StrictFalse
    gui_access: StrictFalse


class PluginContracts(FrozenContractModel):
    match: PluginStageContract
    plan: PluginStageContract
    build: PluginStageContract
    validate_stage: PluginStageContract = Field(alias="validate")

    @property
    def validate(self) -> PluginStageContract:
        return self.validate_stage

    @model_validator(mode="after")
    def validate_exact_stage_bindings(self) -> "PluginContracts":
        expected = {
            "match": (("ModelingIntent",), ("MatchResult",)),
            "plan": (("ModelingIntent", "ModelState"), ("ModelingPlan",)),
            "build": (("ModelingPlan",), ("ModelSpec", "SemanticPatch")),
            "validate": (("ModelSpec",), ("DomainValidationReport",)),
        }
        for name, (inputs, outputs) in expected.items():
            stage = getattr(self, name)
            if stage.input_contracts != inputs or stage.output_contracts != outputs:
                raise ValueError(f"{name} stage contract binding is not exact")
        return self


class PluginDependency(FrozenContractModel):
    dependency_id: DependencyId
    kind: DependencyKind
    version_constraint: str = Field(min_length=1)
    required: bool


class DomainPluginManifest(FrozenContractModel):
    plugin_id: PluginId
    contract_version: SemanticVersion
    implementation_version: SemanticVersion
    description: str = Field(min_length=1, max_length=1024)
    capabilities: PluginCapabilities
    limits: PluginLimits
    routing: PluginRouting
    reference_policy: PluginReferencePolicy
    runtime_behavior: PluginRuntimeBehavior
    contracts: PluginContracts
    dependencies: tuple[PluginDependency, ...]


class SemiconductorDomainPlugin(Protocol):
    plugin_id: str
    contract_version: str
    implementation_version: str

    def match(self, intent: ModelingIntent) -> MatchResult: ...

    def plan(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None,
    ) -> ModelingPlan: ...

    def build(self, plan: ModelingPlan) -> ModelSpec | SemanticPatch: ...

    def validate(self, model: ModelSpec) -> DomainValidationReport: ...


__all__ = [
    "ADAPTER_CONTRACT_VERSION",
    "AmbiguityEvidence",
    "BuildOutputKind",
    "ContractDigest",
    "DependencyKind",
    "DomainFact",
    "DomainPluginManifest",
    "DomainValidationReport",
    "FallbackEvidence",
    "ForcedSelectionEvidence",
    "FrozenContractModel",
    "HASH_PROFILE",
    "MatchKind",
    "MatchResult",
    "MigrationMode",
    "ModelKind",
    "ModelState",
    "ModelingIntent",
    "ModelingPlan",
    "ModelingQuestion",
    "NoMatchEvidence",
    "PlanStep",
    "PluginCapabilities",
    "PluginContracts",
    "PluginDependency",
    "PluginLimits",
    "PluginReferencePolicy",
    "PluginRouting",
    "PluginRuntimeBehavior",
    "PluginStageContract",
    "RUNTIME_CONTRACT_VERSION",
    "ReferenceAccess",
    "ReferenceAccessMode",
    "ResolvedAssumption",
    "RevisionIdentity",
    "RuntimeIssue",
    "RuntimeIssueKind",
    "RuntimeOutcome",
    "SemanticParameter",
    "SemiconductorDomainPlugin",
    "StageName",
    "StageStatus",
    "ValidationStatus",
    "canonical_json",
    "canonical_json_bytes",
    "contract_digest",
    "model_spec_digest",
    "require_digest_binding",
    "semantic_patch_digest",
]
