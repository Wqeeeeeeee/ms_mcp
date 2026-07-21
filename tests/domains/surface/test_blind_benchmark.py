from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from material_studio_mcp_server.benchmark_evaluation import (
    CandidateSubmission,
    EvaluationRoots,
    SubmittedCandidateArtifact,
    TrustedDomainObservation,
    TrustedDomainObservations,
    assert_coordinate_free_payload,
    compile_coordinate_free_blind_task,
    evaluate_benchmark_case,
    load_benchmark_case,
    project_coordinate_free_contract,
)
from material_studio_mcp_server.canonicalization import (
    CANONICALIZATION_CONTRACT_VERSION,
    STRUCTURE_PROJECTION_PROFILE,
    CoordinateFreeStructureProjection,
    SpeciesCount,
    SymmetryClassification,
)


ROOT = Path(__file__).resolve().parents[3]
CASE_PATH = ROOT / "benchmarks" / "cases" / "sic_3c_surface" / "benchmark_case.json"
MODELER_PROCESS = Path(__file__).with_name("modeler_process.py")
SURFACE_VACUUM_METRIC = "surface.vacuum_absolute_error_angstrom"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_case() -> dict[str, object]:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def _reviewed_coordinate_free_projection() -> CoordinateFreeStructureProjection:
    return CoordinateFreeStructureProjection(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        projection_profile=STRUCTURE_PROJECTION_PROFILE,
        canonical_structure_sha256=(
            "8a476d868d74be492a59e3321de62e24f64e942accb48ae04ebae58eecae6e2c"
        ),
        settings_sha256=(
            "6c1aca62ddd2ce2862e670c259c0accfbc0789130fa973d65e75c307fc3161b8"
        ),
        mode="conventional",
        atom_count=10,
        composition=(
            SpeciesCount(species="C", count=4),
            SpeciesCount(species="H", count=2),
            SpeciesCount(species="Si", count=4),
        ),
        symmetry=SymmetryClassification(
            international_number=25,
            international_symbol="Pmm2",
            hall_number=125,
            hall_symbol="P 2 -2",
            choice="",
            point_group_symbol="mm2",
        ),
        contains_coordinates=False,
        contains_lattice_vectors=False,
    )


def _compile_modeler_task(case: dict[str, object]) -> dict[str, object]:
    compiled = compile_coordinate_free_blind_task(
        case,
        reference_projection=_reviewed_coordinate_free_projection(),
    )
    projected = project_coordinate_free_contract(compiled)
    assert_coordinate_free_payload(projected)
    return projected


def _run_modeler_process(
    compiled_task: dict[str, object],
    candidate_root: Path,
) -> dict[str, object]:
    request = {
        "compiled_task": compiled_task,
        "candidate_output": {
            "candidate_root": str(candidate_root.resolve()),
            "model_spec_name": "model_spec.json",
            "project_id": "sic_3c_surface_blind",
            "structure_name": "structure.cif",
        },
    }
    assert set(request) == {"compiled_task", "candidate_output"}
    serialized = json.dumps(request, ensure_ascii=True, sort_keys=True)
    folded = serialized.casefold()
    assert "analytical_oracle" not in folded
    assert "reference_root" not in folded
    assert "structure_artifacts" not in folded

    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(ROOT / "src") + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", str(MODELER_PROCESS)],
        input=serialized,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    receipt = json.loads(completed.stdout)
    assert isinstance(receipt, dict)
    return receipt


def _format_float(value: float) -> str:
    return f"{float(value):.10g}"


def _analytical_oracle_cif_bytes() -> bytes:
    lattice = 4.3596
    repeat = 2
    vacuum = 15.0
    carbon_hydrogen_bond = 1.09
    back_component = carbon_hydrogen_bond / math.sqrt(3.0)
    cell_a = repeat * lattice
    cell_b = repeat * lattice
    cell_c = vacuum + back_component + 7.0 * lattice / 4.0
    hydrogen_z = vacuum / 2.0
    bottom_carbon_z = hydrogen_z + back_component
    plane_spacing = lattice / 4.0
    plane_registries = (
        ("C", ((0.25, 0.25), (0.75, 0.75))),
        ("Si", ((0.0, 0.5), (0.5, 0.0))),
        ("C", ((0.25, 0.75), (0.75, 0.25))),
        ("Si", ((0.0, 0.0), (0.5, 0.5))),
        ("C", ((0.25, 0.25), (0.75, 0.75))),
        ("Si", ((0.0, 0.5), (0.5, 0.0))),
        ("C", ((0.25, 0.75), (0.75, 0.25))),
        ("Si", ((0.0, 0.0), (0.5, 0.5))),
    )

    atoms: list[tuple[str, float, float, float]] = []
    bottom_carbons: list[tuple[float, float]] = []
    for plane_index, (element, registry) in enumerate(plane_registries):
        sites = sorted(
            (
                (repeat_x + local_x) / repeat,
                (repeat_y + local_y) / repeat,
            )
            for repeat_x in range(repeat)
            for repeat_y in range(repeat)
            for local_x, local_y in registry
        )
        fractional_z = (bottom_carbon_z + plane_index * plane_spacing) / cell_c
        for fractional_x, fractional_y in sites:
            atoms.append((element, fractional_x, fractional_y, fractional_z))
            if plane_index == 0:
                bottom_carbons.append((fractional_x, fractional_y))

    for carbon_x, carbon_y in bottom_carbons:
        cartesian_x = carbon_x * cell_a
        cartesian_y = carbon_y * cell_b
        for sign in (-1.0, 1.0):
            atoms.append(
                (
                    "H",
                    ((cartesian_x + sign * back_component) % cell_a) / cell_a,
                    ((cartesian_y + sign * back_component) % cell_b) / cell_b,
                    hydrogen_z / cell_c,
                )
            )

    lines = [
        "data_independent_sic_3c_001_surface_oracle",
        "_symmetry_space_group_name_H-M 'P 1'",
        f"_cell_length_a    {_format_float(cell_a)}",
        f"_cell_length_b    {_format_float(cell_b)}",
        f"_cell_length_c    {_format_float(cell_c)}",
        "_cell_angle_alpha 90",
        "_cell_angle_beta  90",
        "_cell_angle_gamma 90",
        "",
        "loop_",
        "_space_group_symop_operation_xyz",
        "'x,y,z'",
        "",
        "loop_",
        "  _atom_site_label",
        "  _atom_site_type_symbol",
        "  _atom_site_fract_x",
        "  _atom_site_fract_y",
        "  _atom_site_fract_z",
        "  _atom_site_occupancy",
    ]
    element_counts: dict[str, int] = {}
    for element, x, y, z in atoms:
        element_counts[element] = element_counts.get(element, 0) + 1
        label = f"{element}{element_counts[element]:03d}"
        lines.append(
            f"  {label} {element} {_format_float(x)} "
            f"{_format_float(y)} {_format_float(z)} 1"
        )
    return ("\n".join(lines) + "\n").encode("ascii")


def _vacuum_error_from_frozen_candidate_cif(payload: bytes) -> float:
    lines = payload.decode("ascii").splitlines()
    cell_c = float(
        next(line.split()[1] for line in lines if line.startswith("_cell_length_c"))
    )
    atom_rows = [
        line.split()
        for line in lines
        if line.startswith("  ")
        and not line.lstrip().startswith("_")
        and len(line.split()) == 6
    ]
    fractional_z = [float(row[4]) for row in atom_rows]
    measured_vacuum = cell_c - (max(fractional_z) - min(fractional_z)) * cell_c
    return abs(measured_vacuum - 15.0)


def _require_vacuum_observation_bound_to_submission(
    submission: CandidateSubmission,
    observations: TrustedDomainObservations,
) -> None:
    matching = tuple(
        observation
        for observation in observations.observations
        if observation.metric == SURFACE_VACUUM_METRIC
    )
    if len(matching) != 1:
        raise ValueError("development harness requires one trusted vacuum observation")
    if matching[0].evidence_sha256 != submission.structure_sha256:
        raise ValueError(
            "development harness requires trusted vacuum evidence SHA-256 "
            "to equal the submitted structure SHA-256"
        )


def _evaluate_with_bound_vacuum_observation(
    case: dict[str, object],
    *,
    roots: EvaluationRoots,
    submission: CandidateSubmission,
    evaluation_run_id: str,
    observations: TrustedDomainObservations,
):
    _require_vacuum_observation_bound_to_submission(submission, observations)
    return evaluate_benchmark_case(
        case,
        roots=roots,
        submission=submission,
        evaluation_run_id=evaluation_run_id,
        trusted_domain_observations=observations,
    )


def test_benchmark_case_descriptor_is_schema_valid_and_coordinate_free() -> None:
    case = _load_case()
    schema = json.loads(
        (ROOT / "schemas" / "benchmark_case.schema.json").read_text(encoding="utf-8")
    )

    assert not list(Draft202012Validator(schema).iter_errors(case))
    assert load_benchmark_case(case).case_id == case["case_id"]
    assert "result" not in case
    compiled_payload = _compile_modeler_task(case)
    assert_coordinate_free_payload(compiled_payload)
    assert compiled_payload["reference_projection"]["contains_coordinates"] is False
    assert compiled_payload["reference_projection"]["contains_lattice_vectors"] is False
    assert case["task"]["input_artifacts"] == []
    assert case["task"]["includes_final_reference_coordinates"] is False
    assert case["reference"]["structure_artifacts"][0]["sha256"] == (
        "a798c4b6e4af7b5fdd299392d6fbd181a4f8e7e3c8e4d8667ebfb4f056d405a8"
    )


def test_development_harness_rejects_mismatched_vacuum_evidence_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structure_digest = "a" * 64
    submission = CandidateSubmission(
        structure_relative_path="structure.cif",
        structure_sha256=structure_digest,
        artifacts=(
            SubmittedCandidateArtifact(
                kind="model_spec",
                relative_path="model_spec.json",
                sha256="b" * 64,
            ),
            SubmittedCandidateArtifact(
                kind="structure",
                relative_path="structure.cif",
                sha256=structure_digest,
            ),
        ),
    )
    observations = TrustedDomainObservations(
        observations=(
            TrustedDomainObservation(
                metric=SURFACE_VACUUM_METRIC,
                observed=0.0,
                evidence_sha256="c" * 64,
            ),
        )
    )
    evaluator_called = False

    def forbidden_evaluator(*args, **kwargs):
        nonlocal evaluator_called
        evaluator_called = True
        raise AssertionError("shared evaluator must not run before harness binding")

    monkeypatch.setattr(
        sys.modules[__name__],
        "evaluate_benchmark_case",
        forbidden_evaluator,
    )
    with pytest.raises(ValueError, match="submitted structure SHA-256"):
        _evaluate_with_bound_vacuum_observation(
            {},
            roots=None,
            submission=submission,
            evaluation_run_id="mismatched-binding",
            observations=observations,
        )
    assert evaluator_called is False


def test_blind_candidate_is_frozen_before_independent_oracle_evaluation(
    tmp_path: Path,
) -> None:
    case = _load_case()
    candidate_root = tmp_path / "candidate" / "sic_3c_surface"
    reference_root = tmp_path / "reference" / "sic_3c_surface"
    evaluator_root = tmp_path / "evaluation" / "sic_3c_surface"
    candidate_root.mkdir(parents=True)
    evaluator_root.mkdir(parents=True)
    assert not reference_root.exists()

    compiled_payload = _compile_modeler_task(case)
    assert not reference_root.exists()
    process_receipt = _run_modeler_process(compiled_payload, candidate_root)
    assert process_receipt["pid"] != os.getpid()
    assert process_receipt["atom_count"] == 80
    assert process_receipt["validation_status"] == "pass_with_warnings"
    assert process_receipt["router_selected_plugin_id"] == (
        "sic_3c_001_si_face_surface"
    )
    assert not reference_root.exists()

    model_spec_path = candidate_root / "model_spec.json"
    structure_path = candidate_root / "structure.cif"
    assert sorted(path.name for path in candidate_root.iterdir()) == [
        "model_spec.json",
        "structure.cif",
    ]
    candidate_payloads_before = {
        path.name: path.read_bytes() for path in (model_spec_path, structure_path)
    }
    candidate_hashes_before = {
        name: _sha256(payload) for name, payload in candidate_payloads_before.items()
    }
    submission = CandidateSubmission(
        structure_relative_path="structure.cif",
        structure_sha256=candidate_hashes_before["structure.cif"],
        artifacts=(
            SubmittedCandidateArtifact(
                kind="model_spec",
                relative_path="model_spec.json",
                sha256=candidate_hashes_before["model_spec.json"],
            ),
            SubmittedCandidateArtifact(
                kind="structure",
                relative_path="structure.cif",
                sha256=candidate_hashes_before["structure.cif"],
            ),
        ),
    )
    vacuum_error = _vacuum_error_from_frozen_candidate_cif(
        candidate_payloads_before["structure.cif"]
    )

    # The coordinate-bearing auditor phase starts only after subprocess exit and freeze.
    reference_root.mkdir(parents=True)
    oracle_path = reference_root / "analytical_oracle.cif"
    oracle_bytes = _analytical_oracle_cif_bytes()
    oracle_path.write_bytes(oracle_bytes)
    persisted_oracle_bytes = oracle_path.read_bytes()
    assert persisted_oracle_bytes == oracle_bytes
    oracle_hash = _sha256(persisted_oracle_bytes)
    assert oracle_hash == case["reference"]["structure_artifacts"][0]["sha256"]

    observations = TrustedDomainObservations(
        observations=(
            TrustedDomainObservation(
                metric=SURFACE_VACUUM_METRIC,
                observed=vacuum_error,
                evidence_sha256=candidate_hashes_before["structure.cif"],
            ),
        )
    )
    outcome = _evaluate_with_bound_vacuum_observation(
        case,
        roots=EvaluationRoots(
            reference_root=reference_root,
            candidate_root=candidate_root,
            evaluator_output_root=evaluator_root,
        ),
        submission=submission,
        evaluation_run_id="sic-3c-surface-blind-dev-001",
        observations=observations,
    )

    assert outcome.report.states.model_dump() == {
        "structure_valid": "PASS",
        "semiconductor_domain_valid": "PASS",
        "ms_roundtrip_valid": "NOT_RUN",
        "calculation_evidence_valid": "NOT_RUN",
        "scientifically_verified": "NOT_RUN",
    }
    assert outcome.report.overall_status == "PASS"
    assert outcome.report.trusted_domain_metrics_evaluated == (
        "surface.vacuum_absolute_error_angstrom",
    )
    assert outcome.report.real_materials_studio == "NOT_RUN"
    assert outcome.report.real_castep == "NOT_RUN"
    assert outcome.report.candidate_immutable is True
    assert outcome.report.candidate_tree_before == outcome.report.candidate_tree_after
    candidate_payloads_after = {
        path.name: path.read_bytes() for path in (model_spec_path, structure_path)
    }
    assert candidate_payloads_after == candidate_payloads_before
    assert {
        name: _sha256(payload) for name, payload in candidate_payloads_after.items()
    } == candidate_hashes_before
