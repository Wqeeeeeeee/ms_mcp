"""Independent semantic recomputation for benchmark_case.schema.json."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from material_studio_mcp_server.canonicalization import (
    CANONICALIZATION_PROFILE,
    IMPLEMENTATION_VERSION,
    CanonicalizationSettings,
    canonicalization_settings_sha256,
)

from .aggregation import (
    aggregate_required_gates,
    derive_gate_status,
    evaluate_criterion,
)
from .contracts import (
    FIRST_PHASE_THRESHOLDS,
    VALIDITY_STATE_NAMES,
    BenchmarkCase,
    CandidateSubmission,
    EvaluationRoots,
    GateCriterion,
    SemanticValidationReport,
    Status,
    StoredCoordinateFreeReportArtifact,
    ValidityStateName,
    load_benchmark_case,
    load_candidate_submission,
    parse_contract_datetime,
)
from .errors import BenchmarkEvaluationError, EvaluationReason
from .paths import (
    declared_roots_are_disjoint,
    relative_path_under_declared_root,
    resolve_declared_artifact,
    resolve_root_relative_path,
    verify_isolation_roots,
)
from .projection import assert_coordinate_free_payload
from .tree import snapshot_candidate_tree, snapshots_match


_GATE_EVIDENCE_KIND = {
    "structure_valid": "structure",
    "semiconductor_domain_valid": "semiconductor_domain",
    "ms_roundtrip_valid": "ms_roundtrip",
    "calculation_evidence_valid": "calculation",
    "scientifically_verified": "scientific",
}
_FROZEN_CRITERIA: dict[str, tuple[str, float, str]] = {
    "structure.mapping_coverage": ("eq", 1.0, "fraction"),
    "structure.rms_displacement_angstrom": (
        "lte",
        FIRST_PHASE_THRESHOLDS.rms_displacement_angstrom,
        "angstrom",
    ),
    "structure.maximum_displacement_angstrom": (
        "lte",
        FIRST_PHASE_THRESHOLDS.maximum_displacement_angstrom,
        "angstrom",
    ),
    "structure.maximum_relative_lattice_error": (
        "lte",
        FIRST_PHASE_THRESHOLDS.maximum_relative_lattice_error,
        "fraction",
    ),
    "surface.vacuum_absolute_error_angstrom": (
        "lte",
        FIRST_PHASE_THRESHOLDS.vacuum_absolute_error_angstrom,
        "angstrom",
    ),
}


def _unique(values: Iterable[object]) -> bool:
    items = tuple(values)
    return len(items) == len(set(items))


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_nlink),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _read_verified_bytes(path: Path, expected_sha256: str) -> bytes | None:
    try:
        before = path.stat(follow_symlinks=False)
        if (
            before.st_size < 1
            or before.st_size > 16 * 1024 * 1024
            or before.st_nlink != 1
        ):
            return None
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        with path.open("rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if _file_identity(opened) != _file_identity(before):
                return None
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                chunks.append(block)
            finished = os.fstat(handle.fileno())
        after = path.stat(follow_symlinks=False)
        if (
            _file_identity(before) != _file_identity(finished)
            or _file_identity(before) != _file_identity(after)
            or digest.hexdigest() != expected_sha256
        ):
            return None
        return b"".join(chunks)
    except OSError:
        return None


def _artifact_root_kind(case: BenchmarkCase, path: str) -> str | None:
    roots = {
        "reference": case.isolation.reference_root,
        "candidate": case.isolation.candidate_root,
        "evaluator": case.isolation.evaluator_output_root,
    }
    matches: list[str] = []
    for name, declared in roots.items():
        try:
            relative_path_under_declared_root(path, declared)
        except BenchmarkEvaluationError:
            continue
        matches.append(name)
    return matches[0] if len(matches) == 1 else None


def _bound_bytes(
    *,
    actual_root: Path,
    declared_root: str,
    path: str,
    sha256: str,
) -> bytes | None:
    try:
        resolved = resolve_declared_artifact(
            actual_root=actual_root,
            declared_root=declared_root,
            artifact_path=path,
        )
    except BenchmarkEvaluationError:
        return None
    return _read_verified_bytes(resolved, sha256)


def _bound_and_matching(
    *,
    actual_root: Path,
    declared_root: str,
    path: str,
    sha256: str,
) -> bool:
    return (
        _bound_bytes(
            actual_root=actual_root,
            declared_root=declared_root,
            path=path,
            sha256=sha256,
        )
        is not None
    )


def _gate_items(case: BenchmarkCase) -> tuple[tuple[ValidityStateName, Any], ...]:
    return tuple((name, getattr(case.gates, name)) for name in VALIDITY_STATE_NAMES)


def _all_criteria(case: BenchmarkCase) -> tuple[GateCriterion, ...]:
    return tuple(
        criterion
        for _, gate in _gate_items(case)
        for criterion in gate.criteria
    )


def _false_report(reason: EvaluationReason) -> SemanticValidationReport:
    return SemanticValidationReport(
        valid=False,
        reason_codes=(reason,),
        gate_state_bindings_complete=False,
        required_gate_truth_table_satisfied=False,
        criterion_results_complete=False,
        criterion_bindings_complete=False,
        hard_failure_results_complete=False,
        hard_failure_rule_bindings_complete=False,
        backend_evidence_bindings_complete=False,
        isolation_roots_disjoint=False,
        reference_artifacts_bound_to_reference_root=False,
        candidate_artifacts_bound_to_candidate_root=False,
        evaluator_artifacts_bound_to_evaluator_root=False,
        candidate_root_matches_declared_candidate=False,
        duplicate_ids_rejected=False,
        counts_reconciled=False,
        disabled_gates_not_run=False,
        candidate_tree_immutable=False,
        result_identity_bindings_complete=False,
        canonicalization_declaration_matches=False,
    )


def _frozen_criterion_declaration_matches(criterion: GateCriterion) -> bool:
    frozen = _FROZEN_CRITERIA.get(criterion.metric)
    if frozen is None:
        return True
    operator, expected, unit = frozen
    return (
        criterion.comparison_basis == "threshold"
        and criterion.operator == operator
        and type(criterion.expected) in {int, float}
        and not isinstance(criterion.expected, bool)
        and float(criterion.expected) == expected
        and (criterion.tolerance is None or criterion.tolerance == 0.0)
        and criterion.unit == unit
    )


def _recompute_criterion_status(
    criterion: GateCriterion,
    observed: Any,
) -> Status:
    if observed is None:
        return "NOT_RUN"
    frozen = _FROZEN_CRITERIA.get(criterion.metric)
    if frozen is not None:
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            return "FAIL"
        operator, expected, _ = frozen
        value = float(observed)
        passed = value == expected if operator == "eq" else value <= expected
    else:
        try:
            passed = evaluate_criterion(criterion, observed)
        except BenchmarkEvaluationError:
            return "FAIL"
    return "PASS" if passed else "FAIL"


def _load_stored_report(payload: bytes) -> StoredCoordinateFreeReportArtifact | None:
    try:
        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        def reject_nonstandard_constant(value: str) -> None:
            raise ValueError

        decoded = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_constant,
        )
        if not isinstance(decoded, dict):
            return None
        report = StoredCoordinateFreeReportArtifact.model_validate(
            decoded, strict=True
        )
        assert_coordinate_free_payload(report.model_dump(mode="json"))
        return report
    except (BenchmarkEvaluationError, TypeError, UnicodeError, ValueError):
        return None


def validate_benchmark_case_semantics(
    case_value: Mapping[str, object] | BenchmarkCase,
    *,
    roots: EvaluationRoots | None,
    submission: CandidateSubmission | None = None,
) -> SemanticValidationReport:
    """Recompute every semantic receipt field from declarations and artifacts."""

    try:
        case = load_benchmark_case(case_value)
    except BenchmarkEvaluationError:
        return _false_report(EvaluationReason.CONTRACT_INVALID)
    if case.split != "development":
        return _false_report(EvaluationReason.SPLIT_ACCESS_NOT_AUTHORIZED)
    if submission is None:
        return _false_report(EvaluationReason.REQUIRED_EVIDENCE_MISSING)
    try:
        validated_submission = load_candidate_submission(submission)
    except BenchmarkEvaluationError:
        return _false_report(EvaluationReason.CONTRACT_INVALID)

    reasons: set[EvaluationReason] = set()
    physical_roots: EvaluationRoots | None = None
    if roots is None:
        reasons.add(EvaluationReason.PHYSICAL_ROOTS_REQUIRED)
    else:
        try:
            physical_roots = verify_isolation_roots(roots)
        except BenchmarkEvaluationError as exc:
            reasons.add(exc.reason)
    isolation_roots_disjoint = physical_roots is not None

    declared_roots = (
        case.isolation.reference_root,
        case.isolation.candidate_root,
        case.isolation.evaluator_output_root,
    )
    declared_disjoint = declared_roots_are_disjoint(declared_roots)
    if not declared_disjoint:
        reasons.add(EvaluationReason.DECLARED_ROOTS_NOT_DISJOINT)
        isolation_roots_disjoint = False

    candidate_before = None
    candidate_tree_immutable = False
    if physical_roots is not None:
        try:
            candidate_before = snapshot_candidate_tree(physical_roots.candidate_root)
            candidate_tree_immutable = True
        except BenchmarkEvaluationError as exc:
            reasons.add(exc.reason)

    candidate_root_matches = (
        case.candidate.root.casefold() == case.isolation.candidate_root.casefold()
    )
    if not candidate_root_matches:
        reasons.add(EvaluationReason.CANDIDATE_ROOT_DECLARATION_MISMATCH)

    sources_unique = _unique(item.source_id for item in case.reference.sources)
    artifacts_unique = _unique(
        item.artifact_id for item in case.reference.structure_artifacts
    ) and _unique(item.path.casefold() for item in case.reference.structure_artifacts)
    criteria = _all_criteria(case)
    criteria_unique = _unique(item.criterion_id for item in criteria)
    rules_unique = _unique(item.rule_id for item in case.hard_failure_rules)
    result_criteria_unique = True
    hard_results_unique = True
    if case.result is not None:
        result_criteria_unique = _unique(
            item.criterion_id for item in case.result.criterion_results
        )
        hard_results_unique = _unique(
            (item.rule_id, item.validity_state)
            for item in case.result.hard_failures
        )
    duplicate_ids_rejected = all(
        (
            sources_unique,
            artifacts_unique,
            criteria_unique,
            rules_unique,
            result_criteria_unique,
            hard_results_unique,
        )
    )
    if not duplicate_ids_rejected:
        reasons.add(EvaluationReason.DUPLICATE_IDENTIFIER)

    source_ids = {item.source_id for item in case.reference.sources}
    source_refs_complete = all(
        item.source_id in source_ids for item in case.reference.structure_artifacts
    )
    if not source_refs_complete:
        reasons.add(EvaluationReason.REFERENTIAL_INTEGRITY_INVALID)

    canonicalization = case.reference.canonicalization
    canonicalization_matches = (
        canonicalization.method == CANONICALIZATION_PROFILE
        and canonicalization.method_version == IMPLEMENTATION_VERSION
        and canonicalization.settings_sha256
        == canonicalization_settings_sha256(CanonicalizationSettings())
    )
    if not canonicalization_matches:
        reasons.add(EvaluationReason.CANONICALIZATION_DECLARATION_MISMATCH)

    gate_state_bindings = True
    disabled_gates_not_run = True
    for state_name, gate in _gate_items(case):
        if gate.state_name != state_name:
            gate_state_bindings = False
        if gate.enabled:
            if not gate.criteria or gate.not_run_reason is not None:
                gate_state_bindings = False
        elif (
            gate.required_for_overall_pass
            or gate.criteria
            or gate.not_run_reason is None
        ):
            disabled_gates_not_run = False
        expected_kind = _GATE_EVIDENCE_KIND[state_name]
        if any(item.evidence_kind != expected_kind for item in gate.criteria):
            gate_state_bindings = False
        if any(not _frozen_criterion_declaration_matches(item) for item in gate.criteria):
            gate_state_bindings = False
        if state_name in {
            "ms_roundtrip_valid",
            "calculation_evidence_valid",
            "scientifically_verified",
        } and gate.enabled:
            gate_state_bindings = False
            reasons.add(EvaluationReason.BACKEND_EVIDENCE_NOT_AUTHORIZED)
    if not gate_state_bindings:
        reasons.add(EvaluationReason.GATE_BINDING_INVALID)
    if not disabled_gates_not_run:
        reasons.add(EvaluationReason.DISABLED_GATE_INVALID)

    reference_bound = physical_roots is not None and declared_disjoint
    if reference_bound and physical_roots is not None:
        for artifact in case.reference.structure_artifacts:
            if not _bound_and_matching(
                actual_root=physical_roots.reference_root,
                declared_root=case.isolation.reference_root,
                path=artifact.path,
                sha256=artifact.sha256,
            ):
                reference_bound = False
                break
    if not reference_bound:
        reasons.add(EvaluationReason.ARTIFACT_ROOT_BINDING_INVALID)

    selected_references = tuple(
        item
        for item in case.reference.structure_artifacts
        if item.format == "cif" and item.canonical
    )
    if len(selected_references) != 1:
        source_refs_complete = False
        reasons.add(EvaluationReason.REFERENTIAL_INTEGRITY_INVALID)
    selected_reference_sha = (
        selected_references[0].sha256 if len(selected_references) == 1 else None
    )

    candidate_bound = physical_roots is not None and declared_disjoint
    submitted_kinds = {item.kind for item in validated_submission.artifacts}
    if submitted_kinds != set(case.candidate.required_artifacts):
        candidate_bound = False
    if physical_roots is None:
        candidate_bound = False
    else:
        for artifact in validated_submission.artifacts:
            try:
                resolved = resolve_root_relative_path(
                    physical_roots.candidate_root, artifact.relative_path
                )
            except BenchmarkEvaluationError:
                candidate_bound = False
                continue
            if _read_verified_bytes(resolved, artifact.sha256) is None:
                candidate_bound = False
                reasons.add(EvaluationReason.ARTIFACT_IDENTITY_MISMATCH)

    evaluator_bound = physical_roots is not None and declared_disjoint
    criterion_bindings = True
    criterion_results_complete = True
    hard_failure_results_complete = True
    hard_failure_rule_bindings_complete = True
    backend_bindings = True
    required_truth_table = True
    result_identity_bindings = case.result is None
    counts_reconciled = source_refs_complete and duplicate_ids_rejected

    result = case.result
    if case.task.input_artifacts:
        criterion_bindings = False
        reasons.add(EvaluationReason.COORDINATE_DISCLOSURE_RISK)
    if result is not None:
        result_identity_bindings = (
            validated_submission is not None
            and selected_reference_sha is not None
            and result.candidate_sha256 == validated_submission.structure_sha256
            and result.reference_sha256 == selected_reference_sha
        )
        if not result_identity_bindings:
            reasons.add(EvaluationReason.ARTIFACT_IDENTITY_MISMATCH)

        criterion_by_id = {item.criterion_id: item for item in criteria}
        result_by_id = {item.criterion_id: item for item in result.criterion_results}
        criterion_results_complete = set(criterion_by_id) == set(result_by_id)
        counts_reconciled = counts_reconciled and criterion_results_complete
        if not criterion_results_complete:
            reasons.add(EvaluationReason.DERIVED_COUNT_MISMATCH)

        recomputed_statuses: dict[str, Status] = {}
        criterion_states: dict[str, ValidityStateName] = {}
        for state_name, gate in _gate_items(case):
            for criterion in gate.criteria:
                criterion_states[criterion.criterion_id] = state_name
        for criterion_id, result_item in result_by_id.items():
            criterion = criterion_by_id.get(criterion_id)
            if criterion is None:
                criterion_bindings = False
                continue
            recomputed = _recompute_criterion_status(criterion, result_item.observed)
            recomputed_statuses[criterion_id] = recomputed
            if (
                result_item.validity_state != criterion_states.get(criterion_id)
                or result_item.severity != criterion.severity
                or result_item.status != recomputed
            ):
                criterion_bindings = False
            if recomputed == "NOT_RUN":
                if result_item.evidence:
                    criterion_bindings = False
            elif not result_item.evidence:
                criterion_bindings = False
            for evidence in result_item.evidence:
                kind = _artifact_root_kind(case, evidence.path)
                if kind == "candidate" and physical_roots is not None:
                    candidate_bound = candidate_bound and _bound_and_matching(
                        actual_root=physical_roots.candidate_root,
                        declared_root=case.isolation.candidate_root,
                        path=evidence.path,
                        sha256=evidence.sha256,
                    )
                elif kind == "evaluator" and physical_roots is not None:
                    evaluator_bound = evaluator_bound and _bound_and_matching(
                        actual_root=physical_roots.evaluator_output_root,
                        declared_root=case.isolation.evaluator_output_root,
                        path=evidence.path,
                        sha256=evidence.sha256,
                    )
                else:
                    criterion_bindings = False

        derived_states: dict[ValidityStateName, Status] = {}
        for state_name, gate in _gate_items(case):
            if not gate.enabled:
                derived_states[state_name] = "NOT_RUN"
            else:
                try:
                    derived_states[state_name] = derive_gate_status(
                        gate.criteria, recomputed_statuses
                    )
                except BenchmarkEvaluationError:
                    required_truth_table = False

        rule_by_id = {item.rule_id: item for item in case.hard_failure_rules}
        triggered_rule_states: set[tuple[str, ValidityStateName]] = set()
        for rule in case.hard_failure_rules:
            for state_name in rule.applies_to_states:
                if rule.trigger == "state_failed":
                    triggered = derived_states.get(state_name) == "FAIL"
                elif rule.trigger == "criterion_failed":
                    triggered = any(
                        criterion_states.get(identifier) == state_name
                        and status == "FAIL"
                        for identifier, status in recomputed_statuses.items()
                    )
                elif rule.trigger == "required_evidence_missing":
                    triggered = derived_states.get(state_name) == "NOT_RUN"
                else:
                    triggered = False
                if triggered:
                    triggered_rule_states.add((rule.rule_id, state_name))

        for hard_result in result.hard_failures:
            rule = rule_by_id.get(hard_result.rule_id)
            pair = (hard_result.rule_id, hard_result.validity_state)
            if rule is None or pair not in triggered_rule_states:
                hard_failure_rule_bindings_complete = False
            for evidence in hard_result.evidence:
                kind = _artifact_root_kind(case, evidence.path)
                if kind not in {"candidate", "evaluator"} or physical_roots is None:
                    hard_failure_rule_bindings_complete = False
                    continue
                actual_root = (
                    physical_roots.candidate_root
                    if kind == "candidate"
                    else physical_roots.evaluator_output_root
                )
                declared_root = (
                    case.isolation.candidate_root
                    if kind == "candidate"
                    else case.isolation.evaluator_output_root
                )
                bound = _bound_and_matching(
                    actual_root=actual_root,
                    declared_root=declared_root,
                    path=evidence.path,
                    sha256=evidence.sha256,
                )
                if kind == "candidate":
                    candidate_bound = candidate_bound and bound
                else:
                    evaluator_bound = evaluator_bound and bound

        hard_missing_states = {
            criterion_states[identifier]
            for identifier, status in recomputed_statuses.items()
            if criterion_by_id[identifier].severity == "hard_failure"
            and status in {"FAIL", "NOT_RUN"}
        }
        recorded_rule_states = {
            (item.rule_id, item.validity_state) for item in result.hard_failures
        }
        recorded_hard_states = {item.validity_state for item in result.hard_failures}
        hard_failure_results_complete = (
            recorded_rule_states == triggered_rule_states
            and hard_missing_states <= recorded_hard_states
        )
        counts_reconciled = counts_reconciled and hard_failure_results_complete
        if not hard_failure_results_complete:
            reasons.add(EvaluationReason.REQUIRED_EVIDENCE_MISSING)

        if len(result.report_artifacts) != 1:
            evaluator_bound = False
        else:
            report_artifact = result.report_artifacts[0]
            report_payload = None
            if physical_roots is not None and report_artifact.path.endswith(".json"):
                report_payload = _bound_bytes(
                    actual_root=physical_roots.evaluator_output_root,
                    declared_root=case.isolation.evaluator_output_root,
                    path=report_artifact.path,
                    sha256=report_artifact.sha256,
                )
            stored_report = (
                _load_stored_report(report_payload)
                if report_payload is not None
                else None
            )
            if stored_report is None or (
                stored_report.evaluation_run_id != result.evaluation_run_id
                or stored_report.case_id != case.case_id
                or stored_report.candidate_sha256 != result.candidate_sha256
                or stored_report.reference_sha256 != result.reference_sha256
                or stored_report.states != result.states
                or stored_report.overall_status != result.overall_status
            ):
                evaluator_bound = False

        for backend in (result.real_materials_studio, result.real_castep):
            if backend.real_environment or backend.status != "NOT_RUN" or backend.evidence:
                backend_bindings = False
                reasons.add(EvaluationReason.BACKEND_EVIDENCE_NOT_AUTHORIZED)

        try:
            if parse_contract_datetime(result.completed_at) < parse_contract_datetime(
                result.started_at
            ):
                raise BenchmarkEvaluationError(EvaluationReason.TIME_ORDER_INVALID)
        except BenchmarkEvaluationError as exc:
            reasons.add(exc.reason)

        if derived_states != result.states.as_dict():
            required_truth_table = False
        required_states = tuple(
            state_name
            for state_name, gate in _gate_items(case)
            if gate.required_for_overall_pass
        )
        failed_warning = any(
            criterion_by_id[identifier].severity == "warning" and status == "FAIL"
            for identifier, status in recomputed_statuses.items()
        )
        try:
            expected_overall = aggregate_required_gates(
                result.states,
                required_states,
                hard_failure_present=bool(result.hard_failures or hard_missing_states),
                failed_warning_present=failed_warning,
            )
            if expected_overall != result.overall_status:
                required_truth_table = False
        except BenchmarkEvaluationError:
            required_truth_table = False

    if physical_roots is not None and candidate_before is not None:
        try:
            candidate_after = snapshot_candidate_tree(physical_roots.candidate_root)
            candidate_tree_immutable = snapshots_match(candidate_before, candidate_after)
        except BenchmarkEvaluationError:
            candidate_tree_immutable = False
        if not candidate_tree_immutable:
            reasons.add(EvaluationReason.CANDIDATE_TREE_CHANGED)
            candidate_bound = False

    if not criterion_bindings:
        reasons.add(EvaluationReason.RESULT_BINDING_INVALID)
    if not hard_failure_rule_bindings_complete:
        reasons.add(EvaluationReason.REFERENTIAL_INTEGRITY_INVALID)
        counts_reconciled = False
    if not required_truth_table:
        reasons.add(EvaluationReason.AGGREGATION_INVALID)
    if not candidate_bound or not evaluator_bound:
        reasons.add(EvaluationReason.ARTIFACT_ROOT_BINDING_INVALID)

    flags = (
        gate_state_bindings,
        required_truth_table,
        criterion_results_complete,
        criterion_bindings,
        hard_failure_results_complete,
        hard_failure_rule_bindings_complete,
        backend_bindings,
        isolation_roots_disjoint,
        reference_bound,
        candidate_bound,
        evaluator_bound,
        candidate_root_matches,
        duplicate_ids_rejected,
        counts_reconciled,
        disabled_gates_not_run,
        candidate_tree_immutable,
        result_identity_bindings,
        canonicalization_matches,
    )
    valid = all(flags) and source_refs_complete and not reasons
    return SemanticValidationReport(
        valid=valid,
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        gate_state_bindings_complete=gate_state_bindings,
        required_gate_truth_table_satisfied=required_truth_table,
        criterion_results_complete=criterion_results_complete,
        criterion_bindings_complete=criterion_bindings,
        hard_failure_results_complete=hard_failure_results_complete,
        hard_failure_rule_bindings_complete=hard_failure_rule_bindings_complete,
        backend_evidence_bindings_complete=backend_bindings,
        isolation_roots_disjoint=isolation_roots_disjoint,
        reference_artifacts_bound_to_reference_root=reference_bound,
        candidate_artifacts_bound_to_candidate_root=candidate_bound,
        evaluator_artifacts_bound_to_evaluator_root=evaluator_bound,
        candidate_root_matches_declared_candidate=candidate_root_matches,
        duplicate_ids_rejected=duplicate_ids_rejected,
        counts_reconciled=counts_reconciled,
        disabled_gates_not_run=disabled_gates_not_run,
        candidate_tree_immutable=candidate_tree_immutable,
        result_identity_bindings_complete=result_identity_bindings,
        canonicalization_declaration_matches=canonicalization_matches,
    )


__all__ = ["validate_benchmark_case_semantics"]
