"""Strict private contracts for the fixed CASTEP acceptance harness."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from material_studio_mcp_server.benchmark_evaluation import FiveValidityStates
from material_studio_mcp_server.state.execution import canonical_json_sha256


CONTRACT_VERSION = "1.0.0"
IMPLEMENTATION_VERSION = "1.0.0"
ACCEPTANCE_PROFILE = "sic_3c_001_si_face_castep_energy_acceptance_v1"
EVIDENCE_PROFILE = "sic_3c_001_si_face_castep_energy_real_castep_v1"
FIXED_PROJECT_ID = "sic_3c_castep_energy_acceptance"
REAL_CASTEP_OPT_IN = "--run-real-castep"
PUBLIC_CASTEP_TOOL = "material_studio_castep_run_current"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$",
    ),
]


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class FixedCastepProfile(FrozenContract):
    profile: Literal[ACCEPTANCE_PROFILE] = ACCEPTANCE_PROFILE
    source_plugin_id: Literal["sic_3c_001_si_face_surface"] = (
        "sic_3c_001_si_face_surface"
    )
    atom_count: Literal[80] = 80
    task: Literal["Energy"] = "Energy"
    functional: Literal["PBE"] = "PBE"
    quality: Literal["Medium"] = "Medium"
    cutoff_energy_ev: Literal[300] = 300
    kpoints: tuple[Literal[2], Literal[2], Literal[1]] = (2, 2, 1)
    dipole_correction: Literal["Self-consistent"] = "Self-consistent"
    open_in_gui: Literal[False] = False
    take_snapshot: Literal[False] = False
    export_view_audit: Literal[False] = False
    response_mode: Literal["full"] = "full"


class CastepAcceptanceRequest(FrozenContract):
    request_id: Identifier
    workspace_root: Path
    project_id: Literal[FIXED_PROJECT_ID] = FIXED_PROJECT_ID
    execution_mode: Literal["preview", "execute"] = "preview"
    expected_plan_sha256: Sha256 | None = None
    real_opt_in: Literal[REAL_CASTEP_OPT_IN] | None = None
    timeout_seconds: int = Field(default=86_400, ge=1, le=7 * 24 * 3600)

    @model_validator(mode="after")
    def validate_execution_authorization(self) -> "CastepAcceptanceRequest":
        if self.execution_mode == "preview":
            if self.expected_plan_sha256 is not None or self.real_opt_in is not None:
                raise ValueError("preview must not contain execution authorization")
        elif (
            self.expected_plan_sha256 is None
            or self.real_opt_in != REAL_CASTEP_OPT_IN
        ):
            raise ValueError(
                "execute requires the preview plan digest and literal "
                f"{REAL_CASTEP_OPT_IN} authorization"
            )
        return self


class CastepAcceptancePlan(FrozenContract):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    implementation_version: Literal[IMPLEMENTATION_VERSION] = IMPLEMENTATION_VERSION
    request_id: Identifier
    project_id: Literal[FIXED_PROJECT_ID] = FIXED_PROJECT_ID
    base_revision: Literal[0] = 0
    profile: FixedCastepProfile
    candidate_model_spec_sha256: Sha256
    source_structure_sha256: Sha256
    source_profile_exact: Literal[True] = True
    public_tool: Literal[PUBLIC_CASTEP_TOOL] = PUBLIC_CASTEP_TOOL
    public_tool_payload: dict[str, Any]
    plan_sha256: Sha256
    explicit_real_opt_in_required: Literal[True] = True
    backend_resolution_deferred: Literal[True] = True
    preview_files_runner_or_gui_touched: Literal[False] = False
    automatic_retry_allowed: Literal[False] = False


class GuiInvariantProjection(FrozenContract):
    process_count_before_after: tuple[int, int]
    window_count_before_after: tuple[int, int]
    process_inventory_sha256_before_after: tuple[Sha256 | None, Sha256 | None]
    window_inventory_sha256_before_after: tuple[Sha256 | None, Sha256 | None]
    identity_unchanged: bool
    process_launched: bool
    gui_input_activation_open_or_hotload_count: Literal[0] = 0


class CastepVerificationReport(FrozenContract):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    profile: Literal[ACCEPTANCE_PROFILE] = ACCEPTANCE_PROFILE
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    failure_codes: tuple[str, ...]
    real_environment: bool
    source_profile_exact: bool
    effective_settings_exact: bool
    preview_side_effect_free: bool
    explicit_real_opt_in_required: Literal[True] = True
    public_tool_reused: bool
    public_mcp_inventory_unchanged: Literal[True] = True
    runner_identity_valid: bool
    runner_success: bool
    execute_invocation_count: int = Field(ge=0, le=1)
    backend_execution_count: int = Field(ge=0, le=1)
    revision_execution_lock_verified: bool
    execution_attempt_event_types: tuple[str, ...]
    execution_attempt_binding_verified: bool
    electronic_receipt_binding_verified: bool
    native_castep_file_count: int = Field(ge=0)
    native_scf_status: str | None
    native_scf_audit_valid: bool
    total_energy_kcal_per_mol: float | None
    total_energy_finite: bool
    structure_unchanged: bool
    metadata_only_result_revision_verified: bool
    source_revision: Literal[0] = 0
    result_revision: int | None = Field(default=None, ge=1)
    gui: GuiInvariantProjection
    reference_store_accessed: Literal[False] = False
    scientific_claims_changed: Literal[False] = False
    scientific_convergence_verified: Literal[False] = False
    scientifically_verified: Literal[False] = False
    automatic_retry_count: Literal[0] = 0
    workspace_preserved: Literal[True] = True
    candidate_model_spec_sha256: Sha256
    source_structure_sha256: Sha256
    electronic_receipt_sha256: Sha256 | None
    execution_attempt_sha256: Sha256 | None
    native_castep_sha256: Sha256 | None

    def complete_real_acceptance_checks(self) -> bool:
        energy = self.total_energy_kcal_per_mol
        process_digests = self.gui.process_inventory_sha256_before_after
        window_digests = self.gui.window_inventory_sha256_before_after
        return bool(
            self.real_environment
            and self.source_profile_exact
            and self.effective_settings_exact
            and self.preview_side_effect_free
            and self.public_tool_reused
            and self.public_mcp_inventory_unchanged
            and self.runner_identity_valid
            and self.runner_success
            and self.execute_invocation_count == 1
            and self.backend_execution_count == 1
            and self.revision_execution_lock_verified
            and self.execution_attempt_event_types == ("started", "completed")
            and self.execution_attempt_binding_verified
            and self.electronic_receipt_binding_verified
            and self.native_castep_file_count == 1
            and self.native_scf_status == "completed_below_max_cycles"
            and self.native_scf_audit_valid
            and type(energy) is float
            and math.isfinite(energy)
            and self.total_energy_finite
            and self.structure_unchanged
            and self.metadata_only_result_revision_verified
            and self.source_revision == 0
            and self.result_revision == 1
            and self.gui.process_count_before_after == (1, 1)
            and self.gui.window_count_before_after == (1, 1)
            and process_digests[0] is not None
            and process_digests[0] == process_digests[1]
            and window_digests[0] is not None
            and window_digests[0] == window_digests[1]
            and self.gui.identity_unchanged
            and not self.gui.process_launched
            and self.gui.gui_input_activation_open_or_hotload_count == 0
            and not self.reference_store_accessed
            and not self.scientific_claims_changed
            and not self.scientific_convergence_verified
            and not self.scientifically_verified
            and self.automatic_retry_count == 0
            and self.workspace_preserved
            and self.electronic_receipt_sha256 is not None
            and self.execution_attempt_sha256 is not None
            and self.native_castep_sha256 is not None
        )

    @model_validator(mode="after")
    def validate_status(self) -> "CastepVerificationReport":
        if self.status == "PASS" and (
            self.failure_codes or not self.real_environment
        ):
            raise ValueError("PASS requires real execution with no failures")
        if self.status == "NOT_RUN" and (
            self.failure_codes or self.real_environment
        ):
            raise ValueError("NOT_RUN is reserved for successful offline evidence")
        if self.status == "FAIL" and not self.failure_codes:
            raise ValueError("FAIL requires at least one stable failure code")
        if self.status == "PASS" and not self.complete_real_acceptance_checks():
            raise ValueError("PASS requires every real acceptance check")
        return self


class CastepBenchmarkAcceptance(FrozenContract):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    evaluation_run_id: Identifier
    shared_evaluator_report_sha256: Sha256
    shared_evaluator_report_unmodified: Literal[True] = True
    shared_evaluator_states: FiveValidityStates
    states: FiveValidityStates
    overall_status: Literal["PASS", "FAIL", "NOT_RUN"]
    calculation_evidence_sha256: Sha256
    candidate_immutable: Literal[True] = True
    real_castep: Literal["PASS", "FAIL", "NOT_RUN"]
    scientific_status: Literal["NOT_RUN"] = "NOT_RUN"


class CastepAcceptanceEvidence(FrozenContract):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    evidence_profile: Literal[EVIDENCE_PROFILE] = EVIDENCE_PROFILE
    environment: Literal["real_castep"] = "real_castep"
    profile: FixedCastepProfile
    verification: CastepVerificationReport
    benchmark_acceptance: CastepBenchmarkAcceptance
    contains_coordinates: Literal[False] = False
    contains_lattice_vectors: Literal[False] = False
    contains_atom_mapping: Literal[False] = False
    contains_displacement_vectors: Literal[False] = False
    contains_raw_native_output: Literal[False] = False
    contains_absolute_paths: Literal[False] = False
    contains_commands: Literal[False] = False
    contains_process_ids: Literal[False] = False
    contains_window_handles: Literal[False] = False
    contains_usernames_or_hostnames: Literal[False] = False
    contains_environment_values: Literal[False] = False

    @model_validator(mode="after")
    def validate_real_acceptance(self) -> "CastepAcceptanceEvidence":
        verification_sha256 = canonical_json_sha256(
            self.verification.model_dump(mode="json")
        )
        states = self.benchmark_acceptance.states
        shared_states = self.benchmark_acceptance.shared_evaluator_states
        if (
            self.verification.status != "PASS"
            or not self.verification.real_environment
            or not self.verification.complete_real_acceptance_checks()
            or self.benchmark_acceptance.overall_status != "PASS"
            or self.benchmark_acceptance.real_castep != "PASS"
            or self.benchmark_acceptance.calculation_evidence_sha256
            != verification_sha256
            or states.calculation_evidence_valid != "PASS"
            or states.structure_valid != "PASS"
            or states.semiconductor_domain_valid != "PASS"
            or states.ms_roundtrip_valid != "NOT_RUN"
            or states.scientifically_verified != "NOT_RUN"
            or shared_states.structure_valid != "PASS"
            or shared_states.semiconductor_domain_valid != "PASS"
            or shared_states.calculation_evidence_valid != "NOT_RUN"
            or shared_states.ms_roundtrip_valid != "NOT_RUN"
            or shared_states.scientifically_verified != "NOT_RUN"
        ):
            raise ValueError(
                "real CASTEP evidence requires a cross-bound calculation PASS "
                "with round-trip and scientific states NOT_RUN"
            )
        return self


__all__ = [
    "ACCEPTANCE_PROFILE",
    "CONTRACT_VERSION",
    "CastepAcceptanceEvidence",
    "CastepAcceptancePlan",
    "CastepAcceptanceRequest",
    "CastepBenchmarkAcceptance",
    "CastepVerificationReport",
    "EVIDENCE_PROFILE",
    "FIXED_PROJECT_ID",
    "FixedCastepProfile",
    "GuiInvariantProjection",
    "IMPLEMENTATION_VERSION",
    "PUBLIC_CASTEP_TOOL",
    "REAL_CASTEP_OPT_IN",
    "Sha256",
]
