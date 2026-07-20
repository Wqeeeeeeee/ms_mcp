from __future__ import annotations

import copy

import pytest

from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkEvaluationError,
    EvaluationReason,
    compile_coordinate_free_blind_task,
    project_coordinate_free_contract,
)
from material_studio_mcp_server.canonicalization import (
    canonicalize_cif_bytes,
    project_canonical_structure,
)

from .conftest import SYNTHETIC_CIF_BYTES


def _projection():
    return project_canonical_structure(canonicalize_cif_bytes(SYNTHETIC_CIF_BYTES))


def test_compiled_task_contains_reviewed_metadata_and_safe_projection(
    evaluation_fixture,
) -> None:
    task = compile_coordinate_free_blind_task(
        evaluation_fixture.case, reference_projection=_projection()
    )
    payload = project_coordinate_free_contract(task)
    assert payload["contract_version"] == "benchmark_coordinate_free_task_v1"
    assert payload["contains_final_reference_coordinates"] is False
    assert payload["contains_lattice_vectors"] is False
    assert payload["contains_atom_mapping"] is False
    assert payload["contains_raw_artifact_bytes"] is False
    assert "input_artifacts" not in payload


@pytest.mark.parametrize(
    "text",
    [
        "fractional_coordinates: hidden",
        "lattice vectors = hidden",
        "candidate vector [0.125, 0.250, 0.375]",
        "Place Si at (0, 0, 0) and C at (1/4, 1/4, 1/4)",
        "Place Si at 0 0 0",
        "Use basis 1/4, 1/4, 1/4",
        "Use basis 1 / 4, 1 / 4, 1 / 4",
        "Use trial vector 1e-1 2e-1 3e-1",
        "Use trial vector 1 e - 1, 2 e - 1, 3 e - 1",
        "Use x 0 y 0 z 0",
        "Use x value 0 y value 0 z value 0",
        "Use lattice vectors from the reference",
        "Use fullwidth digit １",
    ],
)
def test_compiler_rejects_coordinate_bearing_text(
    evaluation_fixture, text: str
) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["task"]["prompt"] = text
    with pytest.raises(BenchmarkEvaluationError) as captured:
        compile_coordinate_free_blind_task(case, reference_projection=_projection())
    assert captured.value.reason is EvaluationReason.COORDINATE_DISCLOSURE_RISK


def test_compiler_does_not_forward_input_artifact_bindings(evaluation_fixture) -> None:
    case = copy.deepcopy(evaluation_fixture.case)
    case["task"]["input_artifacts"] = [
        {"path": "reference/sic/reference.cif", "sha256": "a" * 64}
    ]
    with pytest.raises(BenchmarkEvaluationError):
        compile_coordinate_free_blind_task(case, reference_projection=_projection())
