from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


VALIDATOR_CONTRACT = "work_order_result_reconciliation_v1"
SCHEMA_PATH = Path(__file__).with_name("work_order.schema.json")


def canonical_sha256(document: dict[str, Any]) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _git_glob_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                parts.append(".*")
                index += 2
            else:
                parts.append("[^/]*")
                index += 1
        elif character == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(character))
            index += 1
    parts.append("$")
    return re.compile("".join(parts))


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_git_glob_regex(pattern).fullmatch(path) for pattern in patterns)


def _static_glob_prefix(pattern: str) -> str:
    wildcard_positions = [
        position for token in ("*", "?") if (position := pattern.find(token)) >= 0
    ]
    return pattern[: min(wildcard_positions)] if wildcard_positions else pattern


def _patterns_may_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_has_wildcard = "*" in left or "?" in left
    right_has_wildcard = "*" in right or "?" in right
    if not left_has_wildcard:
        return bool(_git_glob_regex(right).fullmatch(left))
    if not right_has_wildcard:
        return bool(_git_glob_regex(left).fullmatch(right))
    left_prefix = _static_glob_prefix(left)
    right_prefix = _static_glob_prefix(right)
    return left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)


def _schema_errors(document: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"schema:{'/'.join(str(part) for part in error.absolute_path)}:{error.message}"
        for error in sorted(
            validator.iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]


def reconcile_work_order(
    work_order: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    binding = receipt.get("work_order_binding", {})
    checks: dict[str, bool] = {}
    errors: list[str] = []

    checks["goal_id_matches"] = receipt.get("goal_id") == work_order.get("goal_id")
    checks["contract_version_matches"] = receipt.get(
        "contract_version"
    ) == work_order.get("contract_version")
    checks["work_order_id_matches"] = receipt.get("work_order_id") == work_order.get(
        "work_order_id"
    )
    checks["role_matches"] = receipt.get("role") == work_order.get("role")
    checks["base_sha_matches"] = receipt.get("base_sha") == work_order.get("base_sha")
    checks["branch_matches"] = receipt.get("branch") == work_order.get("expected_branch")

    expected_dependencies = work_order.get("dependencies", [])
    observed_dependencies = binding.get("dependencies", [])
    dependency_ids = [item.get("work_order_id", "") for item in expected_dependencies]
    dependency_duplicates = _duplicates(dependency_ids)
    checks["dependencies_reconciled"] = (
        not dependency_duplicates and observed_dependencies == expected_dependencies
    )
    if dependency_duplicates:
        errors.append(f"duplicate work-order dependency IDs: {dependency_duplicates}")

    work_order_tests = work_order.get("required_tests", [])
    receipt_tests = receipt.get("tests", [])
    expected_test_ids = [item.get("test_id", "") for item in work_order_tests]
    observed_test_ids = [item.get("test_id", "") for item in receipt_tests]
    test_duplicates = _duplicates(expected_test_ids) + _duplicates(observed_test_ids)
    expected_tests_by_id = {item.get("test_id"): item for item in work_order_tests}
    observed_tests_by_id = {item.get("test_id"): item for item in receipt_tests}
    compared_test_fields = ("category", "command", "required", "environment")
    checks["required_test_ids_complete"] = (
        not test_duplicates
        and set(expected_tests_by_id) == set(observed_tests_by_id)
        and all(
            all(
                observed_tests_by_id[test_id].get(field) == requirement.get(field)
                for field in compared_test_fields
            )
            for test_id, requirement in expected_tests_by_id.items()
        )
    )
    if test_duplicates:
        errors.append(f"duplicate test IDs: {sorted(set(test_duplicates))}")

    criteria = work_order.get("acceptance", {}).get("criteria", [])
    results = receipt.get("acceptance_results", [])
    expected_criterion_ids = [item.get("criterion_id", "") for item in criteria]
    observed_criterion_ids = [item.get("criterion_id", "") for item in results]
    criterion_duplicates = _duplicates(expected_criterion_ids) + _duplicates(
        observed_criterion_ids
    )
    criteria_by_id = {item.get("criterion_id"): item for item in criteria}
    results_by_id = {item.get("criterion_id"): item for item in results}
    checks["acceptance_criterion_ids_complete"] = (
        not criterion_duplicates
        and set(criteria_by_id) == set(results_by_id)
        and all(
            results_by_id[criterion_id].get("severity") == criterion.get("severity")
            for criterion_id, criterion in criteria_by_id.items()
        )
    )
    if criterion_duplicates:
        errors.append(f"duplicate acceptance criterion IDs: {sorted(set(criterion_duplicates))}")

    changed_paths = receipt.get("changed_paths", [])
    allowed_paths = work_order.get("allowed_paths", [])
    forbidden_paths = work_order.get("forbidden_paths", [])
    checks["changed_paths_within_allowed_paths"] = bool(changed_paths) and all(
        _matches_any(path, allowed_paths) for path in changed_paths
    )
    checks["forbidden_paths_untouched"] = all(
        not _matches_any(path, forbidden_paths) for path in changed_paths
    )
    checks["path_scopes_non_overlapping"] = not any(
        _patterns_may_overlap(allowed, forbidden)
        for allowed in allowed_paths
        for forbidden in forbidden_paths
    )

    reference_access = work_order.get("reference_access", {})
    reference_policy = reference_access.get("policy")
    receipt_policy = receipt.get("reference_isolation", {}).get("policy")
    receipt_sources = set(receipt.get("reference_sources", []))
    allowed_sources = set(reference_access.get("allowed_sources", []))
    source_authorization_matches = (
        not receipt_sources if reference_policy == "none" else receipt_sources <= allowed_sources
    )
    checks["reference_access_matches"] = (
        receipt_policy == reference_policy and source_authorization_matches
    )

    real_ms = receipt.get("real_materials_studio", {})
    real_castep = receipt.get("real_castep", {})
    for criterion in criteria:
        if not criterion.get("real_environment_required"):
            continue
        environment = criterion.get("required_real_environment")
        environment_result = real_ms if environment == "materials_studio_20_1" else real_castep
        if environment_result.get("status") not in {"PASS", "PASS_WITH_WARNINGS"}:
            errors.append(
                f"criterion {criterion.get('criterion_id')!r} requires real {environment} evidence"
            )

    for requirement in work_order_tests:
        test_id = requirement.get("test_id")
        result = observed_tests_by_id.get(test_id, {})
        if requirement.get("required") and result.get("status") not in {
            "PASS",
            "PASS_WITH_WARNINGS",
        }:
            errors.append(f"required test {test_id!r} did not pass")
        category = requirement.get("category")
        if result.get("status") == "NOT_RUN":
            continue
        if category == "real_ms_20_1" and real_ms.get("status") not in {
            "PASS",
            "PASS_WITH_WARNINGS",
        }:
            errors.append(f"test {test_id!r} claims a real MS run without real-MS evidence")
        if category == "real_castep_small_cell" and real_castep.get("status") not in {
            "PASS",
            "PASS_WITH_WARNINGS",
        }:
            errors.append(f"test {test_id!r} claims a real CASTEP run without real-CASTEP evidence")

    for criterion_id, criterion in criteria_by_id.items():
        result = results_by_id.get(criterion_id, {})
        if criterion.get("severity") == "hard_failure" and result.get("status") not in {
            "PASS",
            "PASS_WITH_WARNINGS",
        }:
            errors.append(f"hard acceptance criterion {criterion_id!r} did not pass")

    for summary_name in ("benchmark_before", "benchmark_after"):
        summary = receipt.get(summary_name, {})
        observed_total = sum(
            int(summary.get(field, 0))
            for field in ("passed", "passed_with_warnings", "failed", "not_run")
        )
        if observed_total != summary.get("case_count"):
            errors.append(f"{summary_name} case counts do not reconcile")
        if summary.get("status") == "NOT_RUN":
            expected_status = "NOT_RUN"
        elif int(summary.get("failed", 0)) > 0:
            expected_status = "FAIL"
        elif int(summary.get("not_run", 0)) > 0:
            expected_status = "NOT_RUN"
        elif int(summary.get("passed_with_warnings", 0)) > 0:
            expected_status = "PASS_WITH_WARNINGS"
        else:
            expected_status = "PASS"
        if summary.get("status") != expected_status:
            errors.append(
                f"{summary_name} status {summary.get('status')!r} does not match {expected_status!r}"
            )
        if summary_name == "benchmark_after" and summary.get("status") == "FAIL":
            errors.append("benchmark_after failed")

    if receipt.get("reference_isolation", {}).get("complied") is not True:
        errors.append("reference isolation did not comply")
    if receipt.get("overall_status") not in {"PASS", "PASS_WITH_WARNINGS"}:
        errors.append("receipt overall status is not merge-eligible")

    expected_work_order_sha256 = canonical_sha256(work_order)
    if binding.get("validator_contract") != VALIDATOR_CONTRACT:
        errors.append("unexpected validator contract")
    if binding.get("work_order_sha256") != expected_work_order_sha256:
        errors.append("work-order SHA-256 mismatch")
    for name, observed in checks.items():
        if binding.get(name) != observed:
            errors.append(f"binding field {name!r} does not match computed value {observed!r}")
        if not observed:
            errors.append(f"reconciliation check failed: {name}")

    if binding.get("dependencies") != expected_dependencies:
        errors.append("dependency binding does not match the Work Order")

    return {
        "validator_contract": VALIDATOR_CONTRACT,
        "ok": not errors,
        "work_order_sha256": expected_work_order_sha256,
        "checks": checks,
        "errors": errors,
    }


def validate_pair(
    work_order: dict[str, Any],
    receipt: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_schema = schema or json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_errors = [
        *[f"work_order:{message}" for message in _schema_errors(work_order, active_schema)],
        *[f"receipt:{message}" for message in _schema_errors(receipt, active_schema)],
    ]
    reconciliation = reconcile_work_order(work_order, receipt)
    errors = [*schema_errors, *reconciliation["errors"]]
    return {
        **reconciliation,
        "ok": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and reconcile a semiconductor Work Order and result receipt."
    )
    parser.add_argument("work_order", type=Path)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()

    work_order = json.loads(args.work_order.read_text(encoding="utf-8"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    report = validate_pair(work_order, receipt)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
