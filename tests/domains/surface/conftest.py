from __future__ import annotations

from collections.abc import Callable

import pytest

from material_studio_mcp_server.domains.surface import build, plan
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    ModelKind,
    ModelState,
    ModelingIntent,
    ReferenceAccess,
    ReferenceAccessMode,
    RUNTIME_CONTRACT_VERSION,
    SemanticParameter,
)
from material_studio_mcp_server.specs import ModelSpec


IntentFactory = Callable[..., ModelingIntent]


@pytest.fixture
def intent_factory() -> IntentFactory:
    def factory(
        *,
        parameters: tuple[SemanticParameter, ...] | None = None,
        material: str = "3C-SiC",
        scenario: str = "surface_slab",
        operation: str = "create_si_face_slab",
        model_kind: ModelKind = ModelKind.CRYSTAL,
        requires_current_model: bool = False,
        output_kind: BuildOutputKind = BuildOutputKind.MODEL_SPEC,
        semantic_requirements: tuple[str, ...] = (),
        declared_assumptions: tuple[str, ...] = (),
        reference_access: ReferenceAccess | None = None,
    ) -> ModelingIntent:
        return ModelingIntent(
            contract_version=RUNTIME_CONTRACT_VERSION,
            request_id="wo-surface-001",
            material=material,
            scenario=scenario,
            operation=operation,
            model_kind=model_kind,
            requires_current_model=requires_current_model,
            output_kind=output_kind,
            parameters=(
                (SemanticParameter(name="project_id", value="sic_surface_dev"),)
                if parameters is None
                else parameters
            ),
            semantic_requirements=semantic_requirements,
            declared_assumptions=declared_assumptions,
            reference_access=(
                ReferenceAccess(
                    mode=ReferenceAccessMode.TASK_ONLY,
                    source_ids=("cod-1010995",),
                    raw_structure_access=False,
                    final_coordinate_access=False,
                    hidden_holdout_access=False,
                )
                if reference_access is None
                else reference_access
            ),
        )

    return factory


@pytest.fixture
def exact_intent(intent_factory: IntentFactory) -> ModelingIntent:
    return intent_factory()


@pytest.fixture
def built_model(exact_intent: ModelingIntent) -> ModelSpec:
    return build(plan(exact_intent, None))


@pytest.fixture
def current_model_state(built_model: ModelSpec) -> ModelState:
    current = built_model.model_copy(update={"revision": 1}, deep=True)
    return ModelState.from_model_spec(current)
