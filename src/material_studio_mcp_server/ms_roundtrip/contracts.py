"""Strict immutable contracts for the private MS 20.1 round-trip path."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from material_studio_mcp_server.benchmark_evaluation import FiveValidityStates


CONTRACT_VERSION = "1.0.0"
IMPLEMENTATION_VERSION = "1.0.0"
ROUNDTRIP_PROFILE = "sic_3c_001_si_face_ms_roundtrip_v1"
RECEIPT_PROFILE = "ms_roundtrip_result_receipt_v1"
BENCHMARK_PROFILE = "ms_roundtrip_benchmark_acceptance_v1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$",
    ),
]
RelativePath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        pattern=r"^[^\\\r\n]+$",
    ),
]

Status = Literal["PASS", "FAIL"]
RealBackendStatus = Literal["PASS", "FAIL", "NOT_RUN"]
ExecutionMode = Literal["preview", "execute"]


class RoundtripContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
    )


class CandidateBinding(RoundtripContractModel):
    plugin_id: Literal["sic_3c_001_si_face_surface"] = (
        "sic_3c_001_si_face_surface"
    )
    plugin_contract_version: Literal["1.0.0"] = "1.0.0"
    plugin_implementation_version: Literal["1.0.0"] = "1.0.0"
    material: Literal["3C-SiC"] = "3C-SiC"
    scenario: Literal["surface_slab"] = "surface_slab"
    operation: Literal["create_si_face_slab"] = "create_si_face_slab"
    revision: Literal[0] = 0
    structure_path: Path
    expected_structure_sha256: Sha256


class RoundtripRequest(RoundtripContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    request_id: Identifier
    run_id: Identifier
    candidate: CandidateBinding
    output_root: Path
    execution_mode: ExecutionMode = "preview"
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class ExternalArtifactDigest(RoundtripContractModel):
    role: Literal["input_cif", "runner_executable"]
    location_sha256: Sha256
    sha256: Sha256
    byte_count: int = Field(ge=1)


class RunArtifactDigest(RoundtripContractModel):
    role: Literal[
        "script",
        "runner_artifact",
        "roundtrip_output",
        "result_receipt",
    ]
    relative_path: RelativePath
    sha256: Sha256
    byte_count: int = Field(ge=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        components = value.split("/")
        if value.startswith("/") or any(part in {"", ".", ".."} for part in components):
            raise ValueError("relative path is not confined")
        return value


class ScriptSafetyReceipt(RoundtripContractModel):
    template: Literal["import_export_script"] = "import_export_script"
    deterministic: Literal[True]
    exact_reviewed_template: Literal[True]
    materialscript_validation_passed: Literal[True]
    source_bound_once: Literal[True]
    output_bound_once: Literal[True]
    forbidden_operations_absent: Literal[True]
    script_source_sha256: Sha256
    script_artifact_sha256: Sha256


class CandidateValidationReceipt(RoundtripContractModel):
    plugin_id: Literal["sic_3c_001_si_face_surface"]
    plugin_contract_version: Literal["1.0.0"]
    plugin_implementation_version: Literal["1.0.0"]
    fixed_candidate_match: Literal[True]
    canonical_structure_sha256: Sha256
    expected_canonical_structure_sha256: Sha256
    atom_count: Literal[80]
    composition: tuple[Literal["C:32"], Literal["H:16"], Literal["Si:32"]]
    vacuum_angstrom: float = Field(ge=0.0)
    mapping_coverage: Literal[1.0]
    maximum_displacement_angstrom: float = Field(ge=0.0)
    maximum_relative_lattice_error: float = Field(ge=0.0)


class RoundtripPlan(RoundtripContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    implementation_version: Literal[IMPLEMENTATION_VERSION] = IMPLEMENTATION_VERSION
    profile: Literal[ROUNDTRIP_PROFILE] = ROUNDTRIP_PROFILE
    status: Literal["preview_ready"] = "preview_ready"
    execution_mode: ExecutionMode
    request_digest_sha256: Sha256
    input_artifact: ExternalArtifactDigest
    candidate_validation: CandidateValidationReceipt
    run_root: Path
    output_path: Path
    output_confined: Literal[True]
    output_absent: Literal[True]
    script_text: str = Field(min_length=1, max_length=128 * 1024)
    script_safety: ScriptSafetyReceipt
    files_written: Literal[False] = False
    runner_called: Literal[False] = False
    gui_probed: Literal[False] = False
    gui_input_sent: Literal[False] = False


class GuiInventoryReceipt(RoundtripContractModel):
    process_count: int = Field(ge=0)
    window_count: int = Field(ge=0)
    usable_single_window: StrictBool
    process_identity_sha256: Sha256 | None
    window_identity_sha256: Sha256 | None
    window_title_sha256: Sha256 | None
    window_visible: StrictBool | None
    window_minimized: StrictBool | None
    window_foreground: StrictBool | None
    contains_pid: Literal[False] = False
    contains_window_handle: Literal[False] = False
    contains_window_title: Literal[False] = False
    read_only_probe: Literal[True] = True

    @model_validator(mode="after")
    def validate_usable_inventory(self) -> "GuiInventoryReceipt":
        if self.usable_single_window:
            if self.process_count != 1 or self.window_count != 1:
                raise ValueError("usable inventory must contain one process and window")
            if (
                self.process_identity_sha256 is None
                or self.window_identity_sha256 is None
                or self.window_title_sha256 is None
                or self.window_visible is not True
            ):
                raise ValueError("usable inventory is missing bound visible-window evidence")
        return self


class GuiInvariantReceipt(RoundtripContractModel):
    before: GuiInventoryReceipt
    after: GuiInventoryReceipt
    matstudio_process_count_before_after: tuple[int, int]
    matstudio_window_count_before_after: tuple[int, int]
    process_identity_unchanged: StrictBool
    window_identity_unchanged: StrictBool
    matstudio_pid_and_window_handle_unchanged: StrictBool
    matstudio_process_launched: StrictBool
    invariant_passed: StrictBool
    gui_input_activation_open_or_hotload_called: Literal[False] = False

    @model_validator(mode="after")
    def validate_counts(self) -> "GuiInvariantReceipt":
        if self.matstudio_process_count_before_after != (
            self.before.process_count,
            self.after.process_count,
        ):
            raise ValueError("process count pair does not match inventories")
        if self.matstudio_window_count_before_after != (
            self.before.window_count,
            self.after.window_count,
        ):
            raise ValueError("window count pair does not match inventories")
        expected_process_unchanged = (
            self.before.process_identity_sha256
            == self.after.process_identity_sha256
        )
        expected_window_unchanged = (
            self.before.window_identity_sha256
            == self.after.window_identity_sha256
        )
        if self.process_identity_unchanged != expected_process_unchanged:
            raise ValueError("process identity flag does not match inventories")
        if self.window_identity_unchanged != expected_window_unchanged:
            raise ValueError("window identity flag does not match inventories")
        if self.matstudio_pid_and_window_handle_unchanged != (
            expected_process_unchanged and expected_window_unchanged
        ):
            raise ValueError("combined GUI identity flag does not match inventories")
        expected_pass = (
            self.before.usable_single_window
            and self.after.usable_single_window
            and expected_process_unchanged
            and expected_window_unchanged
            and not self.matstudio_process_launched
        )
        if self.invariant_passed != expected_pass:
            raise ValueError("GUI invariant status is inconsistent")
        return self


class RunnerIdentityReceipt(RoundtripContractModel):
    runner_identity: Literal[
        "materials_studio_20.1_runmatscript.bat",
        "offline_fake_runner",
    ]
    real_environment: StrictBool
    executable: ExternalArtifactDigest
    exact_runmatscript_name: StrictBool
    materials_studio_20_1_install: StrictBool
    command_template_override_absent: StrictBool
    extra_runner_args_absent: StrictBool
    identity_valid: Literal[True]

    @model_validator(mode="after")
    def validate_environment_label(self) -> "RunnerIdentityReceipt":
        if self.real_environment != (
            self.runner_identity == "materials_studio_20.1_runmatscript.bat"
        ):
            raise ValueError("runner identity and environment label disagree")
        if self.real_environment and not (
            self.exact_runmatscript_name
            and self.materials_studio_20_1_install
            and self.command_template_override_absent
            and self.extra_runner_args_absent
        ):
            raise ValueError("real runner identity is incomplete")
        return self


class RunnerExecutionReceipt(RoundtripContractModel):
    success: StrictBool
    timed_out: StrictBool
    return_code: int | None
    duration_seconds: float = Field(ge=0.0)
    command_sha256: Sha256 | None
    stdout_sha256: Sha256 | None
    stderr_sha256: Sha256 | None
    materials_output_sha256: Sha256 | None
    materials_log_sha256: Sha256 | None
    artifacts: tuple[RunArtifactDigest, ...]
    all_artifacts_confined: StrictBool

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> "RunnerExecutionReceipt":
        execution_hashes = (
            self.command_sha256,
            self.stdout_sha256,
            self.stderr_sha256,
            self.materials_output_sha256,
            self.materials_log_sha256,
        )
        has_execution = self.return_code is not None
        if has_execution != all(value is not None for value in execution_hashes):
            raise ValueError("runner execution hashes must be present as one group")
        if not has_execution and any(value is not None for value in execution_hashes):
            raise ValueError("runner execution hashes require a return code")
        if self.success and (self.timed_out or self.return_code != 0):
            raise ValueError("successful runner execution must return zero without timeout")
        if self.timed_out and self.success:
            raise ValueError("timed-out runner execution cannot be successful")

        relative_paths = tuple(artifact.relative_path for artifact in self.artifacts)
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError("runner artifact paths must be unique")
        if any(
            artifact.role not in {"script", "runner_artifact"}
            for artifact in self.artifacts
        ):
            raise ValueError("runner execution contains a non-runner artifact")
        script_count = sum(artifact.role == "script" for artifact in self.artifacts)
        if script_count > 1:
            raise ValueError("runner execution may bind only one script")
        if self.all_artifacts_confined and has_execution and script_count != 1:
            raise ValueError("confined runner execution must bind exactly one script")
        return self


class TaggedSummaryReceipt(RoundtripContractModel):
    source_path_sha256: Sha256
    output_path_sha256: Sha256
    document_name_sha256: Sha256
    source_matches_input: Literal[True]
    output_matches_fresh_output: Literal[True]
    tagged_json_matches_input_output: Literal[True]


class RoundtripThresholds(RoundtripContractModel):
    mapping_coverage: Literal[1.0] = 1.0
    rms_displacement_angstrom: Literal[0.05] = 0.05
    maximum_displacement_angstrom: Literal[0.15] = 0.15
    maximum_relative_lattice_error: Literal[0.001] = 0.001
    vacuum_absolute_error_angstrom: Literal[0.1] = 0.1
    inclusive_lte_boundaries: Literal[True] = True


class RoundtripComparisonReceipt(RoundtripContractModel):
    canonicalization_contract_version: str = Field(min_length=1, max_length=32)
    canonicalization_implementation_version: str = Field(min_length=1, max_length=32)
    input_canonical_structure_sha256: Sha256
    output_canonical_structure_sha256: Sha256
    atom_count: Literal[80]
    composition: tuple[Literal["C:32"], Literal["H:16"], Literal["Si:32"]]
    mapping_coverage: float = Field(ge=0.0, le=1.0)
    mapping_degenerate: StrictBool
    rms_displacement_angstrom: float = Field(ge=0.0)
    maximum_displacement_angstrom: float = Field(ge=0.0)
    maximum_relative_lattice_error: float = Field(ge=0.0)
    input_vacuum_angstrom: float = Field(ge=0.0)
    output_vacuum_angstrom: float = Field(ge=0.0)
    vacuum_absolute_error_angstrom: float = Field(ge=0.0)
    input_candidate_unchanged_by_comparator: Literal[True]
    thresholds: RoundtripThresholds = Field(default_factory=RoundtripThresholds)
    mapping_pass: StrictBool
    rms_pass: StrictBool
    maximum_displacement_pass: StrictBool
    lattice_pass: StrictBool
    vacuum_pass: StrictBool
    passed: StrictBool
    scientific_status: Literal["NOT_RUN"] = "NOT_RUN"

    @model_validator(mode="after")
    def validate_threshold_decision(self) -> "RoundtripComparisonReceipt":
        expected = (
            self.mapping_coverage == self.thresholds.mapping_coverage,
            self.rms_displacement_angstrom
            <= self.thresholds.rms_displacement_angstrom,
            self.maximum_displacement_angstrom
            <= self.thresholds.maximum_displacement_angstrom,
            self.maximum_relative_lattice_error
            <= self.thresholds.maximum_relative_lattice_error,
            self.vacuum_absolute_error_angstrom
            <= self.thresholds.vacuum_absolute_error_angstrom,
        )
        observed = (
            self.mapping_pass,
            self.rms_pass,
            self.maximum_displacement_pass,
            self.lattice_pass,
            self.vacuum_pass,
        )
        if observed != expected or self.passed != all(expected):
            raise ValueError("round-trip threshold decision is inconsistent")
        return self


class RoundtripReceipt(RoundtripContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    implementation_version: Literal[IMPLEMENTATION_VERSION] = IMPLEMENTATION_VERSION
    receipt_profile: Literal[RECEIPT_PROFILE] = RECEIPT_PROFILE
    request_id: Identifier
    run_id: Identifier
    request_digest_sha256: Sha256
    plan_digest_sha256: Sha256
    started_at: str = Field(min_length=20, max_length=64)
    completed_at: str = Field(min_length=20, max_length=64)
    status: Status
    real_environment: StrictBool
    real_materials_studio_status: RealBackendStatus
    input_artifact: ExternalArtifactDigest
    input_candidate_immutable: StrictBool
    candidate_validation: CandidateValidationReceipt
    script_safety: ScriptSafetyReceipt
    runner_identity: RunnerIdentityReceipt
    runner_execution: RunnerExecutionReceipt
    runner_executable_unchanged: StrictBool
    output_artifact: RunArtifactDigest | None
    output_confined_and_fresh: StrictBool
    tagged_summary: TaggedSummaryReceipt | None
    gui_invariant: GuiInvariantReceipt
    comparison: RoundtripComparisonReceipt | None
    failure_codes: tuple[str, ...]
    calculation_evidence_status: Literal["NOT_RUN"] = "NOT_RUN"
    scientific_status: Literal["NOT_RUN"] = "NOT_RUN"
    gui_input_activation_open_or_hotload_called: Literal[False] = False

    @field_validator("failure_codes")
    @classmethod
    def validate_unique_failure_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("failure codes must be unique")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("receipt timestamp must be ISO 8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("receipt timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_status(self) -> "RoundtripReceipt":
        started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
        if completed < started:
            raise ValueError("receipt completion precedes its start")
        if self.input_artifact.role != "input_cif":
            raise ValueError("receipt input artifact has the wrong role")
        if self.real_environment != self.runner_identity.real_environment:
            raise ValueError("receipt and runner environment labels disagree")
        if self.runner_identity.executable.role != "runner_executable":
            raise ValueError("receipt runner executable has the wrong role")
        if self.tagged_summary is not None and (
            self.tagged_summary.source_path_sha256
            != self.input_artifact.location_sha256
        ):
            raise ValueError("tagged summary source does not bind the input location")

        output_present = self.output_artifact is not None
        if self.output_confined_and_fresh != output_present:
            raise ValueError("output freshness and artifact presence disagree")
        if self.output_artifact is not None and (
            self.output_artifact.role != "roundtrip_output"
        ):
            raise ValueError("receipt output artifact has the wrong role")
        if self.comparison is not None and (
            not output_present or not self.input_candidate_immutable
        ):
            raise ValueError("comparison requires immutable input and bound output")

        script_artifacts = tuple(
            artifact
            for artifact in self.runner_execution.artifacts
            if artifact.role == "script"
        )
        script_bound = (
            len(script_artifacts) == 1
            and script_artifacts[0].sha256
            == self.script_safety.script_artifact_sha256
        )
        if self.runner_execution.all_artifacts_confined and not script_bound:
            raise ValueError("runner script does not match the reviewed script")

        failure_code_set = set(self.failure_codes)
        expected_failure_conditions = {
            "runner_failed": not self.runner_execution.success,
            "gui_invariant_failed": not self.gui_invariant.invariant_passed,
            "input_mutated": not self.input_candidate_immutable,
            "runner_mutated": not self.runner_executable_unchanged,
            "tagged_summary_invalid": self.tagged_summary is None,
        }
        for code, failed in expected_failure_conditions.items():
            if (code in failure_code_set) != failed:
                raise ValueError(f"failure code {code} disagrees with receipt evidence")
        if self.status == "FAIL" and not self.failure_codes:
            raise ValueError("failed receipt must include at least one failure code")

        passed = (
            self.input_candidate_immutable
            and self.runner_execution.success
            and self.runner_executable_unchanged
            and self.output_confined_and_fresh
            and self.tagged_summary is not None
            and self.gui_invariant.invariant_passed
            and self.comparison is not None
            and self.comparison.passed
            and not self.failure_codes
        )
        if (self.status == "PASS") != passed:
            raise ValueError("receipt status is inconsistent with evidence")
        expected_real = (
            "PASS"
            if self.real_environment and passed
            else "FAIL"
            if self.real_environment
            else "NOT_RUN"
        )
        if self.real_materials_studio_status != expected_real:
            raise ValueError("real Materials Studio status is inconsistent")
        return self


class RoundtripExecutionResult(RoundtripContractModel):
    status: Status
    run_root: Path
    output_path: Path | None
    receipt_path: Path
    receipt_artifact: RunArtifactDigest
    receipt: RoundtripReceipt

    @model_validator(mode="after")
    def validate_result_status(self) -> "RoundtripExecutionResult":
        if self.status != self.receipt.status:
            raise ValueError("execution result and receipt status disagree")
        if self.receipt_artifact.role != "result_receipt":
            raise ValueError("receipt artifact has the wrong role")
        return self


class RoundtripBenchmarkAcceptance(RoundtripContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    implementation_version: Literal[IMPLEMENTATION_VERSION] = IMPLEMENTATION_VERSION
    benchmark_profile: Literal[BENCHMARK_PROFILE] = BENCHMARK_PROFILE
    evaluation_run_id: Identifier
    shared_evaluator_report_sha256: Sha256
    shared_evaluator_report_unmodified: Literal[True]
    shared_evaluator_states: FiveValidityStates
    states: FiveValidityStates
    overall_status: Literal["PASS", "FAIL", "NOT_RUN", "PASS_WITH_WARNINGS"]
    ms_roundtrip_structure_sha256: Sha256
    roundtrip_receipt_sha256: Sha256
    comparison: RoundtripComparisonReceipt
    candidate_immutable: Literal[True]
    real_materials_studio: RealBackendStatus
    calculation_evidence_valid: Literal["NOT_RUN"] = "NOT_RUN"
    scientifically_verified: Literal["NOT_RUN"] = "NOT_RUN"
    contains_coordinates: Literal[False] = False
    contains_lattice_vectors: Literal[False] = False
    contains_atom_mapping: Literal[False] = False
    contains_displacement_vectors: Literal[False] = False
    contains_raw_artifact_bytes: Literal[False] = False
    contains_absolute_paths: Literal[False] = False
    contains_pid: Literal[False] = False
    contains_window_handle: Literal[False] = False

    @model_validator(mode="after")
    def validate_derived_acceptance(self) -> "RoundtripBenchmarkAcceptance":
        shared = self.shared_evaluator_states
        states = self.states
        if (
            shared.ms_roundtrip_valid != "NOT_RUN"
            or shared.calculation_evidence_valid != "NOT_RUN"
            or shared.scientifically_verified != "NOT_RUN"
        ):
            raise ValueError("shared evaluator states exceed the PR-7 boundary")
        if (
            states.structure_valid != shared.structure_valid
            or states.semiconductor_domain_valid
            != shared.semiconductor_domain_valid
        ):
            raise ValueError("derived acceptance changed shared evaluator states")
        if (
            states.calculation_evidence_valid != self.calculation_evidence_valid
            or states.scientifically_verified != self.scientifically_verified
        ):
            raise ValueError("later-stage validity states are inconsistent")
        if states.ms_roundtrip_valid != self.real_materials_studio:
            raise ValueError("round-trip state and real Materials Studio status disagree")
        if self.real_materials_studio == "PASS" and not self.comparison.passed:
            raise ValueError("real Materials Studio PASS requires a passing comparison")

        required = (
            states.structure_valid,
            states.semiconductor_domain_valid,
            states.ms_roundtrip_valid,
        )
        expected_overall = (
            "FAIL"
            if "FAIL" in states.as_dict().values()
            else "NOT_RUN"
            if "NOT_RUN" in required
            else "PASS_WITH_WARNINGS"
            if "PASS_WITH_WARNINGS" in required
            else "PASS"
        )
        if self.overall_status != expected_overall:
            raise ValueError("benchmark overall status is inconsistent")
        return self


__all__ = [
    "BENCHMARK_PROFILE",
    "CONTRACT_VERSION",
    "CandidateBinding",
    "CandidateValidationReceipt",
    "ExternalArtifactDigest",
    "GuiInventoryReceipt",
    "GuiInvariantReceipt",
    "IMPLEMENTATION_VERSION",
    "RECEIPT_PROFILE",
    "ROUNDTRIP_PROFILE",
    "RoundtripBenchmarkAcceptance",
    "RoundtripComparisonReceipt",
    "RoundtripContractModel",
    "RoundtripExecutionResult",
    "RoundtripPlan",
    "RoundtripReceipt",
    "RoundtripRequest",
    "RoundtripThresholds",
    "RunArtifactDigest",
    "RunnerExecutionReceipt",
    "RunnerIdentityReceipt",
    "ScriptSafetyReceipt",
    "TaggedSummaryReceipt",
]
