"""Compile reviewed metadata into a coordinate-free blind task."""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping

from material_studio_mcp_server.canonicalization import (
    CoordinateFreeStructureProjection,
)

from .contracts import BenchmarkCase, CompiledBlindTask, load_benchmark_case
from .errors import BenchmarkEvaluationError, EvaluationReason


_COORDINATE_FIELD_PATTERN = re.compile(
    r"(?i)\b(?:fractional(?:_coordinates?)?|cartesian(?:_coordinates?)?|"
    r"lattice[_ ]vectors?|atom[_ ]mapping|displacement[_ ]vectors?|"
    r"atomic[_ ]sites?)\b"
)
_MANTISSA = r"(?:\d+(?:\.\d*)?|\.\d+)"
_NUMBER = (
    rf"[+-]?(?:{_MANTISSA}(?:\s*e\s*[+-]?\s*\d+)?|"
    r"\d+\s*/\s*\d+)"
)
_VECTOR_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9./]){_NUMBER}\s*(?:,\s*|\s+)"
    rf"{_NUMBER}\s*(?:,\s*|\s+){_NUMBER}(?![A-Za-z0-9./])",
    re.IGNORECASE,
)
_AXIS_LABEL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([xyz])(?![A-Za-z0-9])", re.IGNORECASE
)
_REVIEWED_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9 .,+()/\-]+$")


def _review_coordinate_free_text(values: tuple[str, ...]) -> None:
    for value in values:
        coordinate_axes = {
            match.group(1).casefold()
            for match in _AXIS_LABEL_PATTERN.finditer(value)
        }
        if (
            unicodedata.normalize("NFKC", value) != value
            or not value.isascii()
            or not _REVIEWED_TEXT_PATTERN.fullmatch(value)
            or _COORDINATE_FIELD_PATTERN.search(value)
            or _VECTOR_PATTERN.search(value)
            or coordinate_axes == {"x", "y", "z"}
        ):
            raise BenchmarkEvaluationError(EvaluationReason.COORDINATE_DISCLOSURE_RISK)


def compile_coordinate_free_blind_task(
    case_value: Mapping[str, object] | BenchmarkCase,
    *,
    reference_projection: CoordinateFreeStructureProjection,
) -> CompiledBlindTask:
    case = load_benchmark_case(case_value)
    if not isinstance(reference_projection, CoordinateFreeStructureProjection):
        raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID)
    if (
        reference_projection.contains_coordinates
        or reference_projection.contains_lattice_vectors
    ):
        raise BenchmarkEvaluationError(EvaluationReason.COORDINATE_DISCLOSURE_RISK)
    if case.task.input_artifacts:
        raise BenchmarkEvaluationError(EvaluationReason.COORDINATE_DISCLOSURE_RISK)
    _review_coordinate_free_text(
        (
            case.task.prompt,
            *case.task.semantic_requirements,
            *case.task.declared_assumptions,
        )
    )
    return CompiledBlindTask(
        case_id=case.case_id,
        task_id=case.task.task_id,
        domain=case.domain,
        scenario=case.scenario,
        material=case.material,
        prompt=case.task.prompt,
        semantic_requirements=case.task.semantic_requirements,
        declared_assumptions=case.task.declared_assumptions,
        expected_output_kind=case.task.expected_output_kind,
        reference_projection=reference_projection,
    )


__all__ = ["compile_coordinate_free_blind_task"]
