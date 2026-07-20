from __future__ import annotations

import pytest

from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkEvaluationError,
    EvaluationReason,
    assert_coordinate_free_payload,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"coordinates": []},
        {"nested": {"lattice_vectors": []}},
        {"atom_mapping": [1]},
        {"raw_bytes": "00"},
        {"safe": b"bytes"},
        {"contains_coordinates": True},
        {"contains_raw_artifact_bytes": None},
    ],
)
def test_projection_boundary_rejects_coordinate_or_byte_fields(payload) -> None:
    with pytest.raises(BenchmarkEvaluationError) as captured:
        assert_coordinate_free_payload(payload)
    assert captured.value.reason is EvaluationReason.COORDINATE_DISCLOSURE_RISK


def test_projection_boundary_accepts_fixed_coordinate_free_metrics() -> None:
    assert_coordinate_free_payload(
        {
            "rms_displacement_angstrom": 0.01,
            "maximum_relative_lattice_error": 0.0001,
            "contains_coordinates": False,
        }
    )
