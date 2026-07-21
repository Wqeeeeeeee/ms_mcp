"""Private, preview-first Materials Studio 20.1 CIF round-trip adapter."""

from .adapter import MaterialsStudioRoundtripAdapter, run_roundtrip
from .benchmark import evaluate_roundtrip_benchmark, roundtrip_receipt_sha256
from .comparison import (
    compare_roundtrip_cif_bytes,
    full_extent_vacuum_angstrom,
    validate_fixed_candidate_cif,
)
from .contracts import (
    BENCHMARK_PROFILE,
    CONTRACT_VERSION,
    IMPLEMENTATION_VERSION,
    RECEIPT_PROFILE,
    ROUNDTRIP_PROFILE,
    CandidateBinding,
    CandidateValidationReceipt,
    ExternalArtifactDigest,
    GuiInventoryReceipt,
    GuiInvariantReceipt,
    RoundtripBenchmarkAcceptance,
    RoundtripComparisonReceipt,
    RoundtripContractModel,
    RoundtripExecutionResult,
    RoundtripPlan,
    RoundtripReceipt,
    RoundtripRequest,
    RoundtripThresholds,
    RunArtifactDigest,
    RunnerExecutionReceipt,
    RunnerIdentityReceipt,
    ScriptSafetyReceipt,
    TaggedSummaryReceipt,
)
from .errors import RoundtripError, RoundtripErrorCode
from .gui_inventory import (
    capture_gui_inventory,
    compare_gui_inventories,
)
from .planning import plan_digest, plan_roundtrip


__all__ = [
    "BENCHMARK_PROFILE",
    "CONTRACT_VERSION",
    "CandidateBinding",
    "CandidateValidationReceipt",
    "ExternalArtifactDigest",
    "GuiInventoryReceipt",
    "GuiInvariantReceipt",
    "IMPLEMENTATION_VERSION",
    "MaterialsStudioRoundtripAdapter",
    "RECEIPT_PROFILE",
    "ROUNDTRIP_PROFILE",
    "RoundtripBenchmarkAcceptance",
    "RoundtripComparisonReceipt",
    "RoundtripContractModel",
    "RoundtripError",
    "RoundtripErrorCode",
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
    "capture_gui_inventory",
    "compare_gui_inventories",
    "compare_roundtrip_cif_bytes",
    "evaluate_roundtrip_benchmark",
    "full_extent_vacuum_angstrom",
    "plan_digest",
    "plan_roundtrip",
    "roundtrip_receipt_sha256",
    "run_roundtrip",
    "validate_fixed_candidate_cif",
]
