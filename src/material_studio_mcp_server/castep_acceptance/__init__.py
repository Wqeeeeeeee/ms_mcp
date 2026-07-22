"""Private CASTEP acceptance exports; no MCP tool registration."""

from .adapter import (
    CastepAcceptanceError,
    CastepAcceptanceExecutionResult,
    CastepAcceptanceHarness,
)
from .benchmark import evaluate_castep_acceptance_benchmark
from .contracts import (
    ACCEPTANCE_PROFILE,
    CastepAcceptanceEvidence,
    CastepAcceptancePlan,
    CastepAcceptanceRequest,
    CastepBenchmarkAcceptance,
    CastepVerificationReport,
    FixedCastepProfile,
    REAL_CASTEP_OPT_IN,
)
from .evidence import (
    canonical_evidence_sha256,
    project_real_evidence,
    validate_external_evidence_path,
    validate_evidence_projection,
    write_external_evidence,
)
from .profile import build_fixed_candidate, plan_acceptance


__all__ = [
    "ACCEPTANCE_PROFILE",
    "CastepAcceptanceError",
    "CastepAcceptanceEvidence",
    "CastepAcceptanceExecutionResult",
    "CastepAcceptanceHarness",
    "CastepAcceptancePlan",
    "CastepAcceptanceRequest",
    "CastepBenchmarkAcceptance",
    "CastepVerificationReport",
    "FixedCastepProfile",
    "REAL_CASTEP_OPT_IN",
    "build_fixed_candidate",
    "canonical_evidence_sha256",
    "evaluate_castep_acceptance_benchmark",
    "plan_acceptance",
    "project_real_evidence",
    "validate_external_evidence_path",
    "validate_evidence_projection",
    "write_external_evidence",
]
