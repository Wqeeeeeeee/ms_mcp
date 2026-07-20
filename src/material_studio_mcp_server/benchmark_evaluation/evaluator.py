"""Offline path-bound blind structure evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from material_studio_mcp_server.canonicalization import (
    CanonicalizationError,
    CanonicalizationSettings,
    ComparatorSettings,
    canonicalize_cif_bytes,
    compare_structures,
    project_canonical_structure,
    project_structure_comparison,
)

from .aggregation import (
    aggregate_required_gates,
    derive_gate_status,
    evaluate_criterion,
    evaluate_structure_thresholds,
    structure_threshold_status,
)
from .contracts import (
    FIRST_PHASE_THRESHOLDS,
    VALIDITY_STATE_NAMES,
    BenchmarkCase,
    BenchmarkEvaluationOutcome,
    CandidateSubmission,
    CoordinateFreeEvaluationReport,
    EvaluationRoots,
    FiveValidityStates,
    TrustedDomainObservations,
    load_benchmark_case,
    load_candidate_submission,
)
from .errors import BenchmarkEvaluationError, EvaluationReason
from .paths import resolve_declared_artifact, verify_isolation_roots
from .projection import assert_coordinate_free_payload
from .semantic import validate_benchmark_case_semantics
from .task import compile_coordinate_free_blind_task
from .tree import CandidateTreeGuard, read_candidate_artifact


def _load_domain_observations(
    value: TrustedDomainObservations | None,
) -> TrustedDomainObservations | None:
    if value is None:
        return None
    try:
        if not isinstance(value, TrustedDomainObservations):
            raise TypeError
        payload = json.dumps(
            value.model_dump(mode="json", warnings=False),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        return TrustedDomainObservations.model_validate_json(payload, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID) from None


def _read_reference_artifact(
    *,
    path: Path,
    expected_sha256: str,
) -> bytes:
    try:
        before = path.stat(follow_symlinks=False)
        if (
            before.st_size < 1
            or before.st_size > 16 * 1024 * 1024
            or before.st_nlink != 1
        ):
            raise ValueError
        with path.open("rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size != before.st_size
            ):
                raise ValueError
            payload = handle.read()
            finished = os.fstat(handle.fileno())
        after = path.stat(follow_symlinks=False)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ) != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
        ):
            raise ValueError
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValueError
        return bytes(payload)
    except (OSError, ValueError):
        raise BenchmarkEvaluationError(
            EvaluationReason.ARTIFACT_IDENTITY_MISMATCH
        ) from None


def _select_reference_cif(case: BenchmarkCase):
    selected = tuple(
        artifact
        for artifact in case.reference.structure_artifacts
        if artifact.format == "cif" and artifact.canonical
    )
    if not selected:
        raise BenchmarkEvaluationError(EvaluationReason.REQUIRED_EVIDENCE_MISSING)
    if len(selected) != 1:
        raise BenchmarkEvaluationError(EvaluationReason.REFERENTIAL_INTEGRITY_INVALID)
    return selected[0]


def _domain_status(
    case: BenchmarkCase,
    observations: TrustedDomainObservations | None,
) -> tuple[str, tuple[str, ...]]:
    gate = case.gates.semiconductor_domain_valid
    if not gate.enabled:
        return "NOT_RUN", ()
    if observations is None:
        return "NOT_RUN", ()
    by_metric = {item.metric: item for item in observations.observations}
    result_statuses: dict[str, str] = {}
    used: list[str] = []
    for criterion in gate.criteria:
        observation = by_metric.get(criterion.metric)
        if observation is None:
            result_statuses[criterion.criterion_id] = "NOT_RUN"
            continue
        used.append(criterion.metric)
        if criterion.metric == "surface.vacuum_absolute_error_angstrom":
            value = observation.observed
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BenchmarkEvaluationError(EvaluationReason.NON_FINITE_VALUE)
            passed = (
                float(value)
                <= FIRST_PHASE_THRESHOLDS.vacuum_absolute_error_angstrom
            )
        else:
            passed = evaluate_criterion(criterion, observation.observed)
        result_statuses[criterion.criterion_id] = "PASS" if passed else "FAIL"
    return derive_gate_status(gate.criteria, result_statuses), tuple(sorted(used))


def evaluate_benchmark_case(
    case_value: Mapping[str, object] | BenchmarkCase,
    *,
    roots: EvaluationRoots,
    submission: CandidateSubmission,
    evaluation_run_id: str,
    trusted_domain_observations: TrustedDomainObservations | None = None,
) -> BenchmarkEvaluationOutcome:
    """Evaluate one immutable candidate without writing any input or output tree."""

    case = load_benchmark_case(case_value)
    if not isinstance(evaluation_run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", evaluation_run_id
    ):
        raise BenchmarkEvaluationError(EvaluationReason.CONTRACT_INVALID)
    trusted_domain_observations = _load_domain_observations(
        trusted_domain_observations
    )
    canonical_roots = verify_isolation_roots(roots)
    submission = load_candidate_submission(submission)
    guard = CandidateTreeGuard(canonical_roots.candidate_root)
    with guard:
        semantic = validate_benchmark_case_semantics(
            case,
            roots=canonical_roots,
            submission=submission,
        )
        if not semantic.valid:
            reason = (
                semantic.reason_codes[0]
                if semantic.reason_codes
                else EvaluationReason.CONTRACT_INVALID
            )
            raise BenchmarkEvaluationError(reason)
        guard.checkpoint()
        reference_artifact = _select_reference_cif(case)
        reference_path = resolve_declared_artifact(
            actual_root=canonical_roots.reference_root,
            declared_root=case.isolation.reference_root,
            artifact_path=reference_artifact.path,
        )
        reference_bytes = _read_reference_artifact(
            path=reference_path,
            expected_sha256=reference_artifact.sha256,
        )
        guard.checkpoint()
        candidate_payloads = {
            artifact.kind: read_candidate_artifact(
                canonical_roots.candidate_root,
                artifact.relative_path,
                artifact.sha256,
                guard.before,
            )
            for artifact in submission.artifacts
        }
        candidate_bytes = candidate_payloads["structure"]
        guard.checkpoint()
        settings = CanonicalizationSettings()
        try:
            reference_structure = canonicalize_cif_bytes(
                reference_bytes,
                settings=settings,
                expected_sha256=reference_artifact.sha256,
                expected_byte_count=len(reference_bytes),
            )
            reference_projection = project_canonical_structure(reference_structure)
        except Exception:
            raise BenchmarkEvaluationError(
                EvaluationReason.CANONICALIZATION_FAILED
            ) from None
        try:
            candidate_structure = canonicalize_cif_bytes(
                candidate_bytes,
                settings=settings,
                expected_sha256=submission.structure_sha256,
                expected_byte_count=len(candidate_bytes),
            )
        except CanonicalizationError:
            candidate_structure = None
        except Exception:
            raise BenchmarkEvaluationError(
                EvaluationReason.CANONICALIZATION_FAILED
            ) from None
        guard.checkpoint()
        comparison_projection = None
        threshold_results = None
        if candidate_structure is not None:
            try:
                comparison = compare_structures(
                    reference_structure,
                    candidate_structure,
                    ComparatorSettings(canonicalization=settings),
                )
                comparison_projection = project_structure_comparison(comparison)
                threshold_results = evaluate_structure_thresholds(
                    mapping_coverage=comparison_projection.mapping_coverage,
                    rms_displacement_angstrom=(
                        comparison_projection.rms_displacement_angstrom
                    ),
                    maximum_displacement_angstrom=(
                        comparison_projection.maximum_displacement_angstrom
                    ),
                    maximum_relative_lattice_error=(
                        comparison_projection.maximum_relative_lattice_error
                    ),
                )
            except CanonicalizationError:
                comparison_projection = None
                threshold_results = None
            except Exception:
                raise BenchmarkEvaluationError(
                    EvaluationReason.CANONICALIZATION_FAILED
                ) from None
        guard.checkpoint()
        compiled_task = compile_coordinate_free_blind_task(
            case,
            reference_projection=reference_projection,
        )
        if not case.gates.structure_valid.enabled:
            structure_status = "NOT_RUN"
        elif threshold_results is None:
            structure_status = "FAIL"
        else:
            structure_status = structure_threshold_status(threshold_results)
        domain_status, domain_metrics = _domain_status(
            case, trusted_domain_observations
        )
        states = FiveValidityStates(
            structure_valid=structure_status,
            semiconductor_domain_valid=domain_status,
            ms_roundtrip_valid="NOT_RUN",
            calculation_evidence_valid="NOT_RUN",
            scientifically_verified="NOT_RUN",
        )
        required_states = tuple(
            name
            for name in VALIDITY_STATE_NAMES
            if getattr(case.gates, name).required_for_overall_pass
        )
        hard_failure = structure_status == "FAIL" or domain_status == "FAIL"
        overall = aggregate_required_gates(
            states,
            required_states,
            hard_failure_present=hard_failure,
        )
        hard_reasons = (
            (EvaluationReason.THRESHOLD_FAILED,) if hard_failure else ()
        )
        if domain_status == "NOT_RUN" and case.gates.semiconductor_domain_valid.required_for_overall_pass:
            hard_reasons = (*hard_reasons, EvaluationReason.REQUIRED_EVIDENCE_MISSING)
        guard.checkpoint()

    if guard.after is None:
        raise BenchmarkEvaluationError(EvaluationReason.CANDIDATE_TREE_CHANGED)
    report = CoordinateFreeEvaluationReport(
        evaluation_run_id=evaluation_run_id,
        case_id=case.case_id,
        semantic_validation=semantic,
        states=states,
        overall_status=overall,
        structure_projection=reference_projection,
        comparison_projection=comparison_projection,
        structure_threshold_results=threshold_results,
        trusted_domain_metrics_evaluated=domain_metrics,
        hard_failure_reason_codes=hard_reasons,
        warning_reason_codes=(),
        candidate_tree_before=guard.before.summary,
        candidate_tree_after=guard.after.summary,
        candidate_immutable=True,
    )
    outcome = BenchmarkEvaluationOutcome(compiled_task=compiled_task, report=report)
    assert_coordinate_free_payload(outcome.model_dump(mode="json"))
    return outcome


__all__ = ["evaluate_benchmark_case"]
