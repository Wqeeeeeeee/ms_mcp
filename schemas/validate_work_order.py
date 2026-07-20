from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


VALIDATOR_CONTRACT_V1 = "work_order_result_reconciliation_v1"
VALIDATOR_CONTRACT_V2 = "work_order_result_reconciliation_v2"
VALIDATOR_CONTRACTS_BY_VERSION = {
    "1.0.0": VALIDATOR_CONTRACT_V1,
    "1.1.0": VALIDATOR_CONTRACT_V2,
}
SCHEMA_PATH = Path(__file__).with_name("work_order.schema.json")


def _validator_contract_for(work_order: dict[str, Any]) -> str | None:
    if not isinstance(work_order, dict):
        return None
    return VALIDATOR_CONTRACTS_BY_VERSION.get(work_order.get("contract_version"))


def canonical_sha256(document: dict[str, Any]) -> str:
    payload = json.dumps(
        document,
        allow_nan=False,
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


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _non_finite_number_errors(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    if isinstance(value, float) and not math.isfinite(value):
        location = "/".join(path) or "<root>"
        return [f"semantic:{location}:non-finite JSON number is not allowed"]
    if isinstance(value, dict):
        errors: list[str] = []
        for key, item in value.items():
            errors.extend(_non_finite_number_errors(item, (*path, str(key))))
        return errors
    if isinstance(value, list):
        errors = []
        for index, item in enumerate(value):
            errors.extend(_non_finite_number_errors(item, (*path, str(index))))
        return errors
    return []


def _strict_equal(observed: Any, expected: Any, tolerance: float | None) -> bool:
    if isinstance(observed, list) or isinstance(expected, list):
        return (
            tolerance is None
            and isinstance(observed, list)
            and isinstance(expected, list)
            and len(observed) == len(expected)
            and all(
                _strict_equal(observed_item, expected_item, None)
                for observed_item, expected_item in zip(observed, expected)
            )
        )
    observed_is_number = _is_number(observed)
    expected_is_number = _is_number(expected)
    if tolerance is not None and not _is_number(tolerance):
        return False
    if observed_is_number and expected_is_number:
        if tolerance is None:
            return observed == expected
        return abs(observed - expected) <= tolerance
    if tolerance is not None:
        return False
    return type(observed) is type(expected) and observed == expected


def _set_value_key(value: Any) -> tuple[str, Any]:
    if _is_number(value):
        return ("number", value)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, str):
        return ("string", value)
    if value is None:
        return ("null", None)
    return ("invalid", type(value).__name__)


def _source_pin_identity_matches(pin: dict[str, Any]) -> bool:
    if not isinstance(pin, dict):
        return False
    provider = pin.get("provider")
    artifact_url = pin.get("artifact_url", "")
    cod_url = re.match(
        r"https?://(?:www\.)?crystallography\.net/cod/",
        artifact_url,
        flags=re.IGNORECASE,
    )
    if provider != "Crystallography Open Database" and not cod_url:
        return True
    match = re.fullmatch(
        r"https://www\.crystallography\.net/cod/[0-9]+\.cif@([0-9]+)",
        artifact_url,
    )
    return bool(
        provider == "Crystallography Open Database"
        and match
        and match.group(1) == str(pin.get("provider_revision"))
    )


def _criterion_observation_matches(
    criterion: dict[str, Any],
    observed: Any,
) -> bool:
    operator = criterion.get("operator")
    expected = criterion.get("expected")
    tolerance = criterion.get("tolerance")

    if operator == "eq":
        return _strict_equal(observed, expected, tolerance)
    if operator == "ne":
        if tolerance is not None and not (_is_number(observed) and _is_number(expected)):
            return False
        return not _strict_equal(observed, expected, tolerance)
    if operator in {"lt", "lte", "gt", "gte"}:
        if tolerance is not None or not (_is_number(observed) and _is_number(expected)):
            return False
        if operator == "lt":
            return observed < expected
        if operator == "lte":
            return observed <= expected
        if operator == "gt":
            return observed > expected
        return observed >= expected
    if operator == "contains":
        if tolerance is not None:
            return False
        if isinstance(observed, str) and isinstance(expected, str):
            return expected in observed
        if isinstance(observed, list):
            expected_items = expected if isinstance(expected, list) else [expected]
            return all(
                any(_strict_equal(item, wanted, None) for item in observed)
                for wanted in expected_items
            )
        return False
    if operator == "set_eq":
        if tolerance is not None or not isinstance(observed, list) or not isinstance(expected, list):
            return False
        observed_set = {_set_value_key(item) for item in observed}
        expected_set = {_set_value_key(item) for item in expected}
        return observed_set == expected_set
    if operator == "present":
        return tolerance is None and expected is True and observed is not None
    return False


def reconcile_work_order(
    work_order: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    binding = receipt.get("work_order_binding", {})
    checks: dict[str, bool] = {}
    errors: list[str] = []
    contract_version = work_order.get("contract_version")
    strict_evidence_contract = contract_version == "1.1.0"
    expected_validator_contract = _validator_contract_for(work_order)
    if expected_validator_contract is None:
        errors.append(f"unsupported work-order contract version: {contract_version!r}")

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

    if strict_evidence_contract:
        checks["acceptance_observations_match"] = (
            checks["acceptance_criterion_ids_complete"]
            and all(
                (
                    result.get("status") == "NOT_RUN"
                    and "observed" not in result
                )
                or (
                    result.get("status") in {"PASS", "PASS_WITH_WARNINGS"}
                    and "observed" in result
                    and _criterion_observation_matches(
                        criteria_by_id[criterion_id],
                        result["observed"],
                    )
                )
                or (
                    result.get("status") == "FAIL"
                    and "observed" in result
                    and not _criterion_observation_matches(
                        criteria_by_id[criterion_id],
                        result["observed"],
                    )
                )
                for criterion_id, result in results_by_id.items()
            )
        )

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
    if strict_evidence_contract:
        raw_expected_source_pins = work_order.get("source_pins", [])
        expected_source_pins = (
            raw_expected_source_pins
            if isinstance(raw_expected_source_pins, list)
            else []
        )
        observed_source_pins = binding.get("source_pins", [])
        source_pin_contract_applies = (
            reference_policy == "reference_builder" or bool(expected_source_pins)
        )
        source_pin_ids = [
            item.get("source_id", "") if isinstance(item, dict) else ""
            for item in expected_source_pins
        ]
        source_pin_duplicates = _duplicates(source_pin_ids)
        source_pin_urls = [
            item.get("artifact_url", "")
            if isinstance(item, dict) and isinstance(item.get("artifact_url"), str)
            else ""
            for item in expected_source_pins
        ]
        source_url_duplicates = _duplicates(source_pin_urls)
        pinned_urls = set(source_pin_urls)
        if source_pin_contract_applies:
            checks["source_pins_reconciled"] = (
                not source_pin_duplicates
                and not source_url_duplicates
                and all(_source_pin_identity_matches(item) for item in expected_source_pins)
                and observed_source_pins == expected_source_pins
                and pinned_urls == allowed_sources
                and pinned_urls == receipt_sources
            )
            if source_pin_duplicates:
                errors.append(f"duplicate source pin IDs: {source_pin_duplicates}")
            if source_url_duplicates:
                errors.append(f"duplicate source pin URLs: {source_url_duplicates}")
            if not all(_source_pin_identity_matches(item) for item in expected_source_pins):
                errors.append(
                    "source pin provider revision does not match its canonical artifact URL"
                )
        else:
            checks["source_pins_reconciled"] = observed_source_pins == []

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
        if strict_evidence_contract and result.get("status") == "NOT_RUN":
            errors.append(f"acceptance criterion {criterion_id!r} was not run")
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
    if (
        expected_validator_contract is None
        or binding.get("validator_contract") != expected_validator_contract
    ):
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
        "validator_contract": expected_validator_contract,
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
        *[
            f"work_order:{message}"
            for message in _non_finite_number_errors(work_order)
        ],
        *[
            f"receipt:{message}"
            for message in _non_finite_number_errors(receipt)
        ],
    ]
    try:
        reconciliation = reconcile_work_order(work_order, receipt)
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as exc:
        return {
            "validator_contract": _validator_contract_for(work_order),
            "ok": False,
            "work_order_sha256": None,
            "checks": {},
            "errors": [
                *schema_errors,
                f"semantic:reconciliation failed closed ({type(exc).__name__})",
            ],
        }
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
