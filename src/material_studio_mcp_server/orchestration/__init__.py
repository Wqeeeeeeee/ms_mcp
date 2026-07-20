"""Internal capability routing with no public MCP registration side effects."""

from .capability_registry import (
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilitySnapshot,
    DependencyResolver,
    PluginRegistration,
    RegistrationInput,
)
from .router import (
    CandidateEvaluation,
    CandidatePrefilter,
    FallbackRequest,
    ForcedSelectionRequest,
    PlanningDecision,
    PrefilterResult,
    ResolvedRoutingInputs,
    RoutingDecision,
    RuntimeRouter,
    SideEffectProbe,
    empty_side_effect_receipt,
)
from .shadow import (
    ShadowComparisonObservations,
    ShadowEvaluator,
    ShadowRouter,
    evaluate_runtime_mode,
)


__all__ = [
    "CandidateEvaluation",
    "CandidatePrefilter",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilitySnapshot",
    "DependencyResolver",
    "FallbackRequest",
    "ForcedSelectionRequest",
    "PlanningDecision",
    "PluginRegistration",
    "PrefilterResult",
    "RegistrationInput",
    "ResolvedRoutingInputs",
    "RoutingDecision",
    "RuntimeRouter",
    "ShadowComparisonObservations",
    "ShadowEvaluator",
    "ShadowRouter",
    "SideEffectProbe",
    "empty_side_effect_receipt",
    "evaluate_runtime_mode",
]
