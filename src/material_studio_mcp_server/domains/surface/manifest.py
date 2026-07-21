"""Declarative manifest for the fixed 3C-SiC surface plugin."""

from material_studio_mcp_server.runtime import (
    DependencyKind,
    DomainPluginManifest,
    ModelKind,
    PluginCapabilities,
    PluginDependency,
    PluginLimits,
    PluginReferencePolicy,
    PluginRouting,
    PluginRuntimeBehavior,
    PluginStageContract,
    ReferenceAccessMode,
)

from .constants import (
    CONTRACT_VERSION,
    IMPLEMENTATION_VERSION,
    MATERIAL,
    OPERATION,
    PLUGIN_ID,
    SCENARIO,
    TOTAL_ATOM_COUNT,
)


def _stage(callable_name: str, inputs: tuple[str, ...], outputs: tuple[str, ...]):
    return PluginStageContract(
        callable=callable_name,
        input_contracts=inputs,
        output_contracts=outputs,
        deterministic=True,
        filesystem_side_effects=False,
        process_side_effects=False,
        network_access=False,
        gui_access=False,
    )


PLUGIN_MANIFEST = DomainPluginManifest(
    plugin_id=PLUGIN_ID,
    contract_version=CONTRACT_VERSION,
    implementation_version=IMPLEMENTATION_VERSION,
    description=(
        "Pure preview-only builder for the fixed ideal 3C-SiC(001) Si-face "
        "2x2 four-bilayer hydrogen-backed slab."
    ),
    capabilities=PluginCapabilities(
        materials=(MATERIAL,),
        scenarios=(SCENARIO,),
        operations=(OPERATION,),
    ),
    limits=PluginLimits(
        min_atoms=TOTAL_ATOM_COUNT,
        max_atoms=TOTAL_ATOM_COUNT,
        supported_periodicity_dimensions=(3,),
        supported_model_kinds=(ModelKind.CRYSTAL,),
        requires_current_model=False,
        supports_create=True,
        supports_patch=False,
        supports_calculation_plan=False,
        unsupported_capabilities=(
            "C-face surfaces",
            "arbitrary facets",
            "arbitrary polytypes",
            "arbitrary layer counts",
            "reconstructions and relaxation",
            "defects and adsorbates",
            "interfaces contacts and oxides",
            "backend execution and GUI control",
        ),
    ),
    routing=PluginRouting(
        priority=500,
        ambiguity_policy="fail_closed",
        forced_selection_requires_capability_match=True,
    ),
    reference_policy=PluginReferencePolicy(
        allowed_access_modes=(ReferenceAccessMode.TASK_ONLY,),
        hidden_holdout_access=False,
        final_reference_coordinate_access=False,
    ),
    runtime_behavior=PluginRuntimeBehavior(
        deterministic=True,
        preview_first=True,
        mutates_input_model=False,
        owns_revision_state=False,
        executes_backend_directly=False,
        registers_public_mcp_tools=False,
        owns_gui_session=False,
        network_access_during_match_plan_build_validate=False,
    ),
    contracts={
        "match": _stage(
            "material_studio_mcp_server.domains.surface:match",
            ("ModelingIntent",),
            ("MatchResult",),
        ),
        "plan": _stage(
            "material_studio_mcp_server.domains.surface:plan",
            ("ModelingIntent", "ModelState"),
            ("ModelingPlan",),
        ),
        "build": _stage(
            "material_studio_mcp_server.domains.surface:build",
            ("ModelingPlan",),
            ("ModelSpec", "SemanticPatch"),
        ),
        "validate": _stage(
            "material_studio_mcp_server.domains.surface:validate",
            ("ModelSpec",),
            ("DomainValidationReport",),
        ),
    },
    dependencies=(
        PluginDependency(
            dependency_id="runtime-contract",
            kind=DependencyKind.SHARED_CONTRACT,
            version_constraint="==1.0.0",
            required=True,
        ),
        PluginDependency(
            dependency_id="model-spec-contract",
            kind=DependencyKind.SHARED_CONTRACT,
            version_constraint="==1.0.0",
            required=True,
        ),
    ),
)


__all__ = ["PLUGIN_MANIFEST"]
