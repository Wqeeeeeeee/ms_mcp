from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.benchmark_evaluation import (
    FIRST_PHASE_THRESHOLDS,
    BenchmarkCase,
    BenchmarkEvaluationError,
    CandidateSubmission,
    EvaluationReason,
    PrecisionThresholds,
    load_benchmark_case,
)


def test_json_contract_loads_into_frozen_tuple_backed_models(evaluation_fixture) -> None:
    case = load_benchmark_case(evaluation_fixture.case)
    assert isinstance(case, BenchmarkCase)
    assert isinstance(case.reference.sources, tuple)
    assert isinstance(case.gates.structure_valid.criteria, tuple)
    with pytest.raises(ValidationError):
        case.case_id = "changed"


def test_contract_forbids_extra_fields(evaluation_fixture) -> None:
    invalid = copy.deepcopy(evaluation_fixture.case)
    invalid["unexpected"] = True
    with pytest.raises(BenchmarkEvaluationError) as captured:
        load_benchmark_case(invalid)
    assert captured.value.reason is EvaluationReason.CONTRACT_INVALID
    assert str(captured.value) == "contract_invalid"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_criterion_numeric_values_reject_non_finite(
    evaluation_fixture, value: object
) -> None:
    invalid = copy.deepcopy(evaluation_fixture.case)
    invalid["gates"]["structure_valid"]["criteria"][0]["expected"] = value
    with pytest.raises(BenchmarkEvaluationError):
        load_benchmark_case(invalid)


def test_strict_boolean_fields_reject_integer_coercion(evaluation_fixture) -> None:
    invalid = copy.deepcopy(evaluation_fixture.case)
    invalid["gates"]["structure_valid"]["enabled"] = 1
    with pytest.raises(BenchmarkEvaluationError):
        load_benchmark_case(invalid)


def test_model_construct_does_not_bypass_boundary_revalidation(
    evaluation_fixture, capsys
) -> None:
    valid = load_benchmark_case(evaluation_fixture.case)
    forged = BenchmarkCase.model_construct(
        **{**valid.model_dump(), "contract_version": "invalid"}
    )
    with pytest.raises(BenchmarkEvaluationError) as captured:
        load_benchmark_case(forged)
    assert captured.value.reason is EvaluationReason.CONTRACT_INVALID
    assert capsys.readouterr() == ("", "")


def test_candidate_submission_is_strict_and_frozen() -> None:
    with pytest.raises(ValidationError):
        CandidateSubmission(
            structure_relative_path="structure.cif",
            structure_sha256="a" * 64,
            structure_format="xsd",
        )


def test_first_phase_thresholds_are_exactly_frozen() -> None:
    assert FIRST_PHASE_THRESHOLDS == PrecisionThresholds()
    assert FIRST_PHASE_THRESHOLDS.model_dump() == {
        "vacuum_absolute_error_angstrom": 0.1,
        "rms_displacement_angstrom": 0.05,
        "maximum_displacement_angstrom": 0.15,
        "maximum_relative_lattice_error": 0.001,
        "inclusive_lte_boundaries": True,
    }
    with pytest.raises(ValidationError):
        PrecisionThresholds(rms_displacement_angstrom=0.051)
