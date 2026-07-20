"""Explicit coordinate-free serialization boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .contracts import CompiledBlindTask, CoordinateFreeEvaluationReport
from .errors import BenchmarkEvaluationError, EvaluationReason


_FORBIDDEN_KEYS = {
    "atom_mapping",
    "cartesian",
    "cartesian_coordinates",
    "coordinates",
    "displacement_vectors",
    "displacements",
    "fractional",
    "fractional_coordinates",
    "lattice",
    "lattice_vectors",
    "raw_bytes",
    "sites",
}
_NEGATIVE_DISCLOSURE_FLAGS = {
    "contains_atom_mapping",
    "contains_coordinates",
    "contains_displacement_vectors",
    "contains_final_reference_coordinates",
    "contains_lattice_vectors",
    "contains_raw_artifact_bytes",
    "contains_raw_bytes",
}


def assert_coordinate_free_payload(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_KEYS:
                raise BenchmarkEvaluationError(
                    EvaluationReason.COORDINATE_DISCLOSURE_RISK
                )
            if normalized in _NEGATIVE_DISCLOSURE_FLAGS and item is not False:
                raise BenchmarkEvaluationError(
                    EvaluationReason.COORDINATE_DISCLOSURE_RISK
                )
            assert_coordinate_free_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_coordinate_free_payload(item)
    elif isinstance(value, bytes):
        raise BenchmarkEvaluationError(EvaluationReason.COORDINATE_DISCLOSURE_RISK)


def project_coordinate_free_contract(
    value: CompiledBlindTask | CoordinateFreeEvaluationReport,
) -> dict[str, Any]:
    if not isinstance(value, (CompiledBlindTask, CoordinateFreeEvaluationReport)):
        raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID)
    payload = value.model_dump(mode="json")
    assert_coordinate_free_payload(payload)
    return payload


__all__ = ["assert_coordinate_free_payload", "project_coordinate_free_contract"]
