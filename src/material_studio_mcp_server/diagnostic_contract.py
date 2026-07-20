"""Versioned contract for persisted model diagnostic bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


DIAGNOSTIC_EXPORT_SCHEMA_VERSION = "material_studio_diagnostic_export_v2"
VIEW_BUNDLE_SCHEMA_VERSION = "material_studio_view_bundle_v2"
DIAGNOSTIC_EXPORT_CONTRACT_VERSION = 2

REQUIRED_DIAGNOSTIC_ARTIFACT_KEYS = (
    "view_summary_csv",
    "view_projections_csv",
    "view_overlaps_csv",
    "view_quality_csv",
    "view_reference_atlas_svg",
    "view_reference_manifest_json",
    "view_reference_index_csv",
)
REQUIRED_DIAGNOSTIC_ROW_COUNTS = {
    "view_summary": 1,
    "view_projections": 1,
    "view_quality": 1,
    "view_reference_views": 1,
}


def assess_diagnostic_export_contract(
    diagnostics: Mapping[str, Any] | None,
    *,
    diagnostic_requested: bool = False,
) -> dict[str, Any]:
    """Assess whether persisted diagnostics satisfy the current runtime contract."""

    values = diagnostics if isinstance(diagnostics, Mapping) else {}
    row_counts = values.get("view_bundle_row_counts")
    if not isinstance(row_counts, Mapping):
        row_counts = {}

    existing_artifact_keys = [
        key for key in REQUIRED_DIAGNOSTIC_ARTIFACT_KEYS if _artifact_path_exists(values.get(key))
    ]
    missing_artifact_keys = [
        key for key in REQUIRED_DIAGNOSTIC_ARTIFACT_KEYS if key not in existing_artifact_keys
    ]
    missing_row_count_keys = [
        key
        for key, minimum in REQUIRED_DIAGNOSTIC_ROW_COUNTS.items()
        if _numeric_row_count(row_counts.get(key)) < minimum
    ]
    has_any_export = bool(
        existing_artifact_keys
        or row_counts
        or _artifact_path_exists(values.get("view_bundle_manifest_path"))
        or _artifact_path_exists(values.get("diagnostic_export_manifest_json"))
    )

    raw_version = values.get("view_bundle_contract_version")
    observed_version = _contract_version(raw_version)
    observed_schema = values.get("view_bundle_schema_version")
    version_valid = raw_version is None or observed_version is not None

    if not has_any_export:
        status = "not_exported"
    elif raw_version is not None and observed_version is None:
        status = "invalid_version"
    elif observed_version is None:
        status = "legacy_unversioned"
    elif observed_version > DIAGNOSTIC_EXPORT_CONTRACT_VERSION:
        status = "newer_than_runtime"
    elif observed_version < DIAGNOSTIC_EXPORT_CONTRACT_VERSION:
        status = "outdated"
    elif missing_artifact_keys or missing_row_count_keys:
        status = "incomplete"
    else:
        status = "current"

    current = status == "current"
    refresh_allowed = status != "newer_than_runtime"
    refresh_required = bool(
        refresh_allowed
        and status in {"legacy_unversioned", "invalid_version", "outdated", "incomplete"}
    )
    if status == "not_exported" and diagnostic_requested:
        refresh_required = True

    return {
        "schema_version": DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
        "current_contract_version": DIAGNOSTIC_EXPORT_CONTRACT_VERSION,
        "current_view_bundle_schema_version": VIEW_BUNDLE_SCHEMA_VERSION,
        "observed_contract_version": observed_version,
        "observed_contract_version_raw": raw_version,
        "observed_view_bundle_schema_version": observed_schema,
        "version_valid": version_valid,
        "status": status,
        "current": current,
        "compatible_with_runtime": status != "newer_than_runtime",
        "has_any_export": has_any_export,
        "refresh_required": refresh_required,
        "refresh_allowed": refresh_allowed,
        "required_artifact_keys": list(REQUIRED_DIAGNOSTIC_ARTIFACT_KEYS),
        "existing_required_artifact_keys": existing_artifact_keys,
        "missing_required_artifact_keys": missing_artifact_keys,
        "required_row_counts": dict(REQUIRED_DIAGNOSTIC_ROW_COUNTS),
        "missing_required_row_count_keys": missing_row_count_keys,
        "diagnostic_requested": bool(diagnostic_requested),
    }


def _contract_version(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _numeric_row_count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(float(value)), 0)
        except ValueError:
            return 0
    return 0


def _artifact_path_exists(value: Any) -> bool:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        return False
    try:
        return Path(value).expanduser().is_file()
    except (OSError, ValueError):
        return False
