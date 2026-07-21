"""Fail-closed errors for the private Materials Studio round-trip adapter."""

from __future__ import annotations

from enum import Enum


class RoundtripErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_CANDIDATE = "unsupported_candidate"
    INPUT_IDENTITY_MISMATCH = "input_identity_mismatch"
    INPUT_MUTATED = "input_mutated"
    OUTPUT_CONFINEMENT_FAILED = "output_confinement_failed"
    OUTPUT_ALREADY_EXISTS = "output_already_exists"
    OUTPUT_MISSING = "output_missing"
    OUTPUT_IDENTITY_MISMATCH = "output_identity_mismatch"
    SCRIPT_SAFETY_FAILED = "script_safety_failed"
    EXECUTE_MODE_REQUIRED = "execute_mode_required"
    RUNNER_IDENTITY_INVALID = "runner_identity_invalid"
    RUNNER_MUTATED = "runner_mutated"
    RUNNER_FAILED = "runner_failed"
    RUNNER_ARTIFACT_INVALID = "runner_artifact_invalid"
    GUI_PRECONDITION_FAILED = "gui_precondition_failed"
    GUI_INVARIANT_FAILED = "gui_invariant_failed"
    TAGGED_SUMMARY_INVALID = "tagged_summary_invalid"
    COMPARISON_FAILED = "comparison_failed"
    THRESHOLD_FAILED = "threshold_failed"
    RECEIPT_PERSISTENCE_FAILED = "receipt_persistence_failed"
    BENCHMARK_BINDING_INVALID = "benchmark_binding_invalid"


class RoundtripError(RuntimeError):
    """An adapter failure with a stable, path-free reason code."""

    def __init__(self, code: RoundtripErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


__all__ = ["RoundtripError", "RoundtripErrorCode"]
