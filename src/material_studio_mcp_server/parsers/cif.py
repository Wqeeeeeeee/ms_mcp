"""Deterministic CIF parsing and ModelSpec round-trip validation."""

from __future__ import annotations

import hashlib
import math
import re
import shlex
import sys
from collections import Counter, deque
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Literal

from material_studio_mcp_server.specs.crystal import CrystalSpec


_LATTICE_TAGS = {
    "a": "_cell_length_a",
    "b": "_cell_length_b",
    "c": "_cell_length_c",
    "alpha": "_cell_angle_alpha",
    "beta": "_cell_angle_beta",
    "gamma": "_cell_angle_gamma",
}
_ATOM_HEADERS = {
    "label": "_atom_site_label",
    "element": "_atom_site_type_symbol",
    "x": "_atom_site_fract_x",
    "y": "_atom_site_fract_y",
    "z": "_atom_site_fract_z",
}
_CIF_NUMBER_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?(?:\(\d+\))?$"
)
_CifValidationPolicy = Literal["strict", "materials_studio_20_1_export"]
_MS_EXPORT_POLICY = "materials_studio_20_1_export"
_MS_EXPORT_FLOAT_EPSILON = 1e-12
_MS_EXPORT_FRACTIONAL_TOLERANCE_CAP = 0.000005000001
_MS_EXPORT_LATTICE_TOLERANCE_CAP = 0.000050000001
_MS_EXPORT_MAPPING_METHOD = "deterministic_same_element_periodic_bipartite"


def parse_crystal_cif(path: str | Path) -> dict[str, Any]:
    """Parse the lattice and fractional atom loop from a generated-style CIF."""

    cif_path = Path(path).expanduser().resolve()
    result: dict[str, Any] = {
        "parser": "generated_p1_cif_v1",
        "path": str(cif_path),
        "exists": cif_path.exists(),
        "lattice": {},
        "atoms": [],
        "errors": [],
        "warnings": [],
    }
    if not cif_path.exists():
        result["errors"].append(f"CIF file does not exist: {cif_path}")
        result["ok"] = False
        return result
    if not cif_path.is_file():
        result["errors"].append(f"CIF path is not a file: {cif_path}")
        result["ok"] = False
        return result
    if cif_path.suffix.lower() != ".cif":
        result["errors"].append(f"Unsupported structure format for CIF parser: {cif_path.suffix or '<none>'}")
        result["ok"] = False
        return result

    try:
        raw = cif_path.read_bytes()
        text = raw.decode("utf-8-sig")
    except Exception as exc:
        result["errors"].append(f"Failed to read CIF: {exc}")
        result["ok"] = False
        return result

    result["file_size_bytes"] = len(raw)
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    logical_lines = _tokenize_cif_lines(text, result["errors"])
    scalars: dict[str, str] = {}
    atom_rows: list[dict[str, Any]] = []
    index = 0
    while index < len(logical_lines):
        tokens = logical_lines[index]
        if not tokens:
            index += 1
            continue
        first = tokens[0].lower()
        if first == "loop_":
            headers, data_tokens, index = _parse_loop_tokens(logical_lines, index + 1)
            lowered_headers = [header.lower() for header in headers]
            if set(_ATOM_HEADERS.values()) <= set(lowered_headers):
                atom_rows.extend(
                    _parse_atom_loop(lowered_headers, data_tokens, result["errors"])
                )
            continue
        if first.startswith("_"):
            if len(tokens) < 2:
                result["errors"].append(f"CIF scalar {tokens[0]} has no value.")
            else:
                scalars[first] = tokens[1]
        index += 1

    lattice: dict[str, float] = {}
    lattice_lexemes: dict[str, str] = {}
    for key, tag in _LATTICE_TAGS.items():
        raw_value = scalars.get(tag)
        if raw_value is None:
            result["errors"].append(f"Required CIF lattice field is missing: {tag}")
            continue
        parsed = _parse_cif_number(raw_value)
        if parsed is None:
            result["errors"].append(f"Invalid CIF number for {tag}: {raw_value}")
            continue
        lattice[key] = parsed
        lattice_lexemes[key] = raw_value

    if not atom_rows:
        result["errors"].append("Required CIF fractional atom loop is missing or empty.")
    for row_index, atom in enumerate(atom_rows):
        atom["row_index"] = row_index
    labels = [str(atom["id"]) for atom in atom_rows]
    duplicate_labels = sorted(label for label, count in Counter(labels).items() if count > 1)
    if duplicate_labels:
        result["errors"].append("Duplicate CIF atom labels: " + ", ".join(duplicate_labels))

    result["lattice"] = lattice
    result["lattice_lexemes"] = lattice_lexemes
    result["atoms"] = atom_rows
    result["atom_count"] = len(atom_rows)
    result["element_counts"] = dict(sorted(Counter(str(atom["element"]) for atom in atom_rows).items()))
    result["ok"] = not result["errors"]
    return result


def validate_crystal_cif_against_spec(
    crystal: CrystalSpec,
    path: str | Path,
    *,
    coordinate_tolerance: float = 1e-8,
    lattice_tolerance: float = 1e-7,
    policy: _CifValidationPolicy = "strict",
) -> dict[str, Any]:
    """Compare a materialized CIF with the crystal source of truth."""

    if policy not in {"strict", _MS_EXPORT_POLICY}:
        raise ValueError(f"Unsupported CIF validation policy: {policy!r}")

    parsed = parse_crystal_cif(path)
    receipt: dict[str, Any] = {
        "applicable": True,
        "format": "cif",
        "policy": policy,
        "parser": parsed.get("parser"),
        "structure_path": parsed.get("path"),
        "exists": parsed.get("exists"),
        "file_size_bytes": parsed.get("file_size_bytes"),
        "sha256": parsed.get("sha256"),
        "coordinate_tolerance": coordinate_tolerance,
        "lattice_tolerance": lattice_tolerance,
        "expected_atom_count": len(crystal.basis_atoms),
        "actual_atom_count": parsed.get("atom_count"),
        "expected_element_counts": _element_counts(crystal),
        "actual_element_counts": parsed.get("element_counts") or {},
        "atom_count_matches": False,
        "element_counts_match": False,
        "atom_ids_match": False,
        "atom_elements_match": False,
        "fractional_coordinates_match": False,
        "lattice_matches": False,
        "max_fractional_delta": None,
        "max_lattice_delta": None,
        "mapping_method": (
            _MS_EXPORT_MAPPING_METHOD
            if policy == _MS_EXPORT_POLICY
            else "atom_label_identity"
        ),
        "mapping_coverage": 0.0,
        "mapping_ambiguous": False,
        "labels_are_diagnostic_only": policy == _MS_EXPORT_POLICY,
        "label_set_preserved": None,
        "labels_preserved": None,
        "label_preservation_status": "unverified",
        "tolerance_derived_from_exported_lexemes": False,
        "rejection_reasons": [],
        "missing_atom_ids": [],
        "extra_atom_ids": [],
        "element_mismatches": [],
        "fractional_coordinate_mismatches": [],
        "lattice_mismatches": [],
        "errors": list(parsed.get("errors") or []),
        "warnings": list(parsed.get("warnings") or []),
    }
    if not parsed.get("ok"):
        receipt["status"] = "missing" if not parsed.get("exists") else "parse_failed"
        receipt["rejection_reasons"] = [
            "cif_missing" if not parsed.get("exists") else "cif_parse_failed"
        ]
        receipt["ok"] = False
        return receipt

    if policy == _MS_EXPORT_POLICY:
        return _validate_materials_studio_20_1_export(crystal, parsed, receipt)

    expected_atoms = {atom.id: atom for atom in crystal.basis_atoms}
    actual_atoms = {str(atom["id"]): atom for atom in parsed.get("atoms", [])}
    expected_ids = set(expected_atoms)
    actual_ids = set(actual_atoms)
    missing_ids = sorted(expected_ids - actual_ids)
    extra_ids = sorted(actual_ids - expected_ids)
    receipt["missing_atom_ids"] = missing_ids
    receipt["extra_atom_ids"] = extra_ids
    receipt["atom_ids_match"] = not missing_ids and not extra_ids
    receipt["mapping_coverage"] = len(expected_ids & actual_ids) / len(expected_ids)
    receipt["label_set_preserved"] = receipt["atom_ids_match"]
    receipt["labels_preserved"] = receipt["atom_ids_match"]
    receipt["label_preservation_status"] = (
        "preserved" if receipt["atom_ids_match"] else "mismatch"
    )
    receipt["atom_count_matches"] = len(expected_atoms) == len(actual_atoms)
    receipt["element_counts_match"] = receipt["expected_element_counts"] == receipt["actual_element_counts"]

    element_mismatches: list[dict[str, Any]] = []
    coordinate_mismatches: list[dict[str, Any]] = []
    max_fractional_delta = 0.0
    for atom_id in sorted(expected_ids & actual_ids):
        expected = expected_atoms[atom_id]
        actual = actual_atoms[atom_id]
        if expected.element != actual.get("element"):
            element_mismatches.append(
                {
                    "atom_id": atom_id,
                    "expected": expected.element,
                    "actual": actual.get("element"),
                }
            )
        expected_fractional = (
            float(expected.fractional.x),
            float(expected.fractional.y),
            float(expected.fractional.z),
        )
        actual_fractional = tuple(float(value) for value in actual["fractional"])
        deltas = tuple(
            _periodic_fractional_delta(expected_value, actual_value)
            for expected_value, actual_value in zip(expected_fractional, actual_fractional)
        )
        atom_max_delta = max(deltas)
        max_fractional_delta = max(max_fractional_delta, atom_max_delta)
        if atom_max_delta > coordinate_tolerance:
            coordinate_mismatches.append(
                {
                    "atom_id": atom_id,
                    "expected": list(expected_fractional),
                    "actual": list(actual_fractional),
                    "delta": list(deltas),
                    "max_delta": atom_max_delta,
                }
            )
    receipt["element_mismatches"] = element_mismatches
    receipt["fractional_coordinate_mismatches"] = coordinate_mismatches
    receipt["atom_elements_match"] = receipt["atom_ids_match"] and not element_mismatches
    receipt["fractional_coordinates_match"] = receipt["atom_ids_match"] and not coordinate_mismatches
    receipt["max_fractional_delta"] = max_fractional_delta

    lattice_mismatches: list[dict[str, Any]] = []
    max_lattice_delta = 0.0
    parsed_lattice = parsed.get("lattice") or {}
    for key in _LATTICE_TAGS:
        expected_value = float(getattr(crystal.lattice, key))
        actual_value = float(parsed_lattice[key])
        delta = abs(expected_value - actual_value)
        max_lattice_delta = max(max_lattice_delta, delta)
        tolerance = max(lattice_tolerance, abs(expected_value) * 1e-9)
        if delta > tolerance:
            lattice_mismatches.append(
                {
                    "field": key,
                    "expected": expected_value,
                    "actual": actual_value,
                    "delta": delta,
                    "tolerance": tolerance,
                }
            )
    receipt["lattice_mismatches"] = lattice_mismatches
    receipt["lattice_matches"] = not lattice_mismatches
    receipt["max_lattice_delta"] = max_lattice_delta

    required_checks = (
        "atom_count_matches",
        "element_counts_match",
        "atom_ids_match",
        "atom_elements_match",
        "fractional_coordinates_match",
        "lattice_matches",
    )
    receipt["ok"] = all(bool(receipt[key]) for key in required_checks)
    receipt["status"] = "matched" if receipt["ok"] else "mismatch"
    if not receipt["ok"]:
        receipt["rejection_reasons"] = _strict_rejection_reasons(receipt)
        receipt["errors"].append(
            "Materialized CIF content does not match the current CrystalSpec."
        )
    return receipt


def _validate_materials_studio_20_1_export(
    crystal: CrystalSpec,
    parsed: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    expected_atoms = list(crystal.basis_atoms)
    actual_atoms = list(parsed.get("atoms") or [])
    expected_ids = {atom.id for atom in expected_atoms}
    actual_ids = {str(atom["id"]) for atom in actual_atoms}

    receipt["missing_atom_ids"] = sorted(expected_ids - actual_ids)
    receipt["extra_atom_ids"] = sorted(actual_ids - expected_ids)
    receipt["atom_ids_match"] = expected_ids == actual_ids
    receipt["atom_count_matches"] = len(expected_atoms) == len(actual_atoms)
    receipt["element_counts_match"] = (
        receipt["expected_element_counts"] == receipt["actual_element_counts"]
    )
    receipt["atom_elements_match"] = receipt["element_counts_match"]

    tolerance_by_actual_index: dict[int, tuple[float, float, float]] = {}
    fractional_token_receipts: list[dict[str, Any]] = []
    all_fractional_tolerances: list[float] = []
    for actual_index, actual in enumerate(actual_atoms):
        lexemes = tuple(str(value) for value in actual["fractional_lexemes"])
        tolerances = tuple(
            _exported_numeric_tolerance(
                lexeme,
                cap=_MS_EXPORT_FRACTIONAL_TOLERANCE_CAP,
            )
            for lexeme in lexemes
        )
        tolerance_by_actual_index[actual_index] = tolerances
        all_fractional_tolerances.extend(tolerances)
        fractional_token_receipts.append(
            {
                "exported_row_index": int(actual["row_index"]),
                "exported_atom_label": str(actual["id"]),
                "element": str(actual["element"]),
                "lexemes": list(lexemes),
                "tolerances": list(tolerances),
            }
        )

    lattice_lexemes = parsed.get("lattice_lexemes") or {}
    lattice_tolerances: dict[str, float] = {}
    lattice_mismatches: list[dict[str, Any]] = []
    max_lattice_delta_decimal = Decimal(0)
    for key in _LATTICE_TAGS:
        lexeme = str(lattice_lexemes[key])
        tolerance = _exported_numeric_tolerance(
            lexeme,
            cap=_MS_EXPORT_LATTICE_TOLERANCE_CAP,
        )
        lattice_tolerances[key] = tolerance
        expected_value = float(getattr(crystal.lattice, key))
        expected_decimal = Decimal(str(expected_value))
        actual_decimal = Decimal(re.sub(r"\(\d+\)$", "", lexeme))
        delta_decimal = abs(expected_decimal - actual_decimal)
        max_lattice_delta_decimal = max(
            max_lattice_delta_decimal,
            delta_decimal,
        )
        if delta_decimal > Decimal(str(tolerance)):
            lattice_mismatches.append(
                {
                    "field": key,
                    "expected": expected_value,
                    "actual": _decimal_to_finite_float(actual_decimal),
                    "exported_lexeme": lexeme,
                    "delta": _decimal_to_finite_float(delta_decimal),
                    "delta_decimal": str(delta_decimal),
                    "tolerance": tolerance,
                }
            )

    maximum_fractional_tolerance = max(all_fractional_tolerances, default=0.0)
    maximum_lattice_tolerance = max(lattice_tolerances.values(), default=0.0)
    receipt.update(
        {
            "tolerance_derived_from_exported_lexemes": True,
            "fractional_tolerance_cap": _MS_EXPORT_FRACTIONAL_TOLERANCE_CAP,
            "lattice_tolerance_cap": _MS_EXPORT_LATTICE_TOLERANCE_CAP,
            "maximum_fractional_export_tolerance": maximum_fractional_tolerance,
            "maximum_lattice_export_tolerance": maximum_lattice_tolerance,
            "tolerance_derivation": {
                "method": "half_last_printed_decimal_unit_plus_epsilon",
                "source": "exported_cif_numeric_lexemes",
                "floating_point_epsilon": _MS_EXPORT_FLOAT_EPSILON,
                "fractional_cap": _MS_EXPORT_FRACTIONAL_TOLERANCE_CAP,
                "lattice_cap": _MS_EXPORT_LATTICE_TOLERANCE_CAP,
                "fractional_tokens": fractional_token_receipts,
                "lattice_tokens": {
                    key: {
                        "lexeme": str(lattice_lexemes[key]),
                        "tolerance": lattice_tolerances[key],
                    }
                    for key in _LATTICE_TAGS
                },
            },
            "lattice_mismatches": lattice_mismatches,
            "lattice_matches": not lattice_mismatches,
            "max_lattice_delta": _decimal_to_finite_float(
                max_lattice_delta_decimal
            ),
        }
    )

    mapping: dict[int, int] = {}
    pair_evidence: dict[tuple[int, int], dict[str, Any]] = {}
    mapping_ambiguous = False
    if receipt["atom_count_matches"] and receipt["element_counts_match"]:
        (
            mapping,
            pair_evidence,
            mapping_ambiguous,
        ) = _deterministic_periodic_atom_mapping(
            expected_atoms, actual_atoms, tolerance_by_actual_index
        )

    atom_mapping: list[dict[str, Any]] = []
    max_fractional_delta = 0.0
    for expected_index in sorted(
        mapping,
        key=lambda index: (expected_atoms[index].element, index),
    ):
        actual_index = mapping[expected_index]
        expected = expected_atoms[expected_index]
        actual = actual_atoms[actual_index]
        evidence = pair_evidence[(expected_index, actual_index)]
        max_fractional_delta = max(max_fractional_delta, evidence["max_delta"])
        atom_mapping.append(
            {
                "expected_atom_id": expected.id,
                "exported_atom_label": str(actual["id"]),
                "exported_row_index": int(actual["row_index"]),
                "element": expected.element,
                "labels_match": (
                    None
                    if mapping_ambiguous
                    else expected.id == str(actual["id"])
                ),
                "delta": list(evidence["delta"]),
                "tolerance": list(evidence["tolerance"]),
                "max_delta": evidence["max_delta"],
            }
        )

    unmatched_expected = [
        index for index in range(len(expected_atoms)) if index not in mapping
    ]
    mapped_actual_indexes = set(mapping.values())
    unmatched_actual = [
        index for index in range(len(actual_atoms)) if index not in mapped_actual_indexes
    ]
    coordinate_mismatches: list[dict[str, Any]] = []
    for expected_index in unmatched_expected:
        expected = expected_atoms[expected_index]
        candidates = [
            (actual_index, evidence)
            for (candidate_expected, actual_index), evidence in pair_evidence.items()
            if candidate_expected == expected_index
        ]
        if not candidates:
            coordinate_mismatches.append(
                {
                    "atom_id": expected.id,
                    "element": expected.element,
                    "reason": "no_same_element_exported_candidate",
                }
            )
            continue
        actual_index, closest = min(
            candidates,
            key=lambda item: (item[1]["max_delta"], item[0]),
        )
        actual = actual_atoms[actual_index]
        max_fractional_delta = max(max_fractional_delta, closest["max_delta"])
        coordinate_mismatches.append(
            {
                "atom_id": expected.id,
                "element": expected.element,
                "closest_exported_atom_label": str(actual["id"]),
                "closest_exported_row_index": int(actual["row_index"]),
                "delta": list(closest["delta"]),
                "tolerance": list(closest["tolerance"]),
                "max_delta": closest["max_delta"],
                "reason": "periodic_delta_exceeds_exported_lexeme_tolerance",
            }
        )

    expected_atom_count = len(expected_atoms)
    mapping_coverage = len(mapping) / expected_atom_count
    mapping_complete = (
        receipt["atom_count_matches"]
        and receipt["element_counts_match"]
        and len(mapping) == expected_atom_count
    )
    labels_preserved: bool | None = None
    if mapping_complete and not mapping_ambiguous:
        labels_preserved = all(item["labels_match"] for item in atom_mapping)
    receipt.update(
        {
            "mapping_coverage": mapping_coverage,
            "mapping_ambiguous": mapping_ambiguous,
            "atom_mapping": atom_mapping,
            "unmapped_expected_atom_ids": [
                expected_atoms[index].id for index in unmatched_expected
            ],
            "unmapped_exported_atom_labels": [
                str(actual_atoms[index]["id"]) for index in unmatched_actual
            ],
            "label_set_preserved": receipt["atom_ids_match"],
            "labels_preserved": labels_preserved,
            "label_preservation_status": (
                "ambiguous"
                if mapping_complete and mapping_ambiguous
                else "preserved"
                if labels_preserved is True
                else "regenerated"
                if labels_preserved is False
                else "unverified"
            ),
            "fractional_coordinate_mismatches": coordinate_mismatches,
            "fractional_coordinates_match": mapping_complete,
            "max_fractional_delta": max_fractional_delta,
        }
    )

    receipt["ok"] = bool(mapping_complete and receipt["lattice_matches"])
    receipt["status"] = "matched" if receipt["ok"] else "mismatch"
    if not receipt["ok"]:
        rejection_reasons: list[str] = []
        if not receipt["atom_count_matches"]:
            rejection_reasons.append("atom_count_mismatch")
        if not receipt["element_counts_match"]:
            rejection_reasons.append("element_composition_mismatch")
        if (
            receipt["atom_count_matches"]
            and receipt["element_counts_match"]
            and not mapping_complete
        ):
            rejection_reasons.append("periodic_fractional_geometry_mismatch")
        if not receipt["lattice_matches"]:
            rejection_reasons.append("lattice_geometry_mismatch")
        receipt["rejection_reasons"] = rejection_reasons
        receipt["errors"].append(
            "Materialized CIF content does not match the current CrystalSpec."
        )
    return receipt


def _deterministic_periodic_atom_mapping(
    expected_atoms: list[Any],
    actual_atoms: list[dict[str, Any]],
    tolerance_by_actual_index: dict[int, tuple[float, float, float]],
) -> tuple[
    dict[int, int],
    dict[tuple[int, int], dict[str, Any]],
    bool,
]:
    pair_evidence: dict[tuple[int, int], dict[str, Any]] = {}
    candidate_edges: dict[int, list[int]] = {}
    expected_order = sorted(
        range(len(expected_atoms)),
        key=lambda index: (expected_atoms[index].element, index),
    )
    for expected_index in expected_order:
        expected = expected_atoms[expected_index]
        candidates: list[int] = []
        for actual_index, actual in enumerate(actual_atoms):
            if expected.element != actual.get("element"):
                continue
            evidence = _periodic_pair_evidence(
                expected,
                actual,
                tolerance_by_actual_index[actual_index],
            )
            pair_evidence[(expected_index, actual_index)] = evidence
            if evidence["within_tolerance"]:
                candidates.append(actual_index)
        candidate_edges[expected_index] = candidates

    expected_to_actual: dict[int, int] = {}
    actual_to_expected: dict[int, int] = {}
    for root_expected in expected_order:
        queue: deque[int] = deque([root_expected])
        visited_expected = {root_expected}
        parent_actual: dict[int, int] = {}
        free_actual: int | None = None
        while queue and free_actual is None:
            expected_index = queue.popleft()
            for actual_index in candidate_edges[expected_index]:
                if actual_index in parent_actual:
                    continue
                parent_actual[actual_index] = expected_index
                matched_expected = actual_to_expected.get(actual_index)
                if matched_expected is None:
                    free_actual = actual_index
                    break
                if matched_expected not in visited_expected:
                    visited_expected.add(matched_expected)
                    queue.append(matched_expected)
        if free_actual is None:
            continue

        actual_index = free_actual
        while True:
            expected_index = parent_actual[actual_index]
            previous_actual = expected_to_actual.get(expected_index)
            expected_to_actual[expected_index] = actual_index
            actual_to_expected[actual_index] = expected_index
            if previous_actual is None:
                break
            actual_index = previous_actual

    mapping_ambiguous = bool(
        len(expected_to_actual) == len(expected_atoms)
        and _mapping_has_alternating_cycle(
            candidate_edges,
            expected_to_actual,
            actual_to_expected,
        )
    )
    return expected_to_actual, pair_evidence, mapping_ambiguous


def _mapping_has_alternating_cycle(
    candidate_edges: dict[int, list[int]],
    expected_to_actual: dict[int, int],
    actual_to_expected: dict[int, int],
) -> bool:
    adjacency: dict[int, set[int]] = {
        expected_index: set() for expected_index in expected_to_actual
    }
    for expected_index, actual_indexes in candidate_edges.items():
        matched_actual = expected_to_actual.get(expected_index)
        for actual_index in actual_indexes:
            if actual_index == matched_actual:
                continue
            matched_expected = actual_to_expected.get(actual_index)
            if matched_expected is not None:
                adjacency[expected_index].add(matched_expected)

    indegree = {expected_index: 0 for expected_index in adjacency}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    queue: deque[int] = deque(
        sorted(index for index, degree in indegree.items() if degree == 0)
    )
    visited_count = 0
    while queue:
        expected_index = queue.popleft()
        visited_count += 1
        for target in sorted(adjacency[expected_index]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited_count != len(adjacency)


def _periodic_pair_evidence(
    expected: Any,
    actual: dict[str, Any],
    tolerance: tuple[float, float, float],
) -> dict[str, Any]:
    expected_fractional = (
        float(expected.fractional.x),
        float(expected.fractional.y),
        float(expected.fractional.z),
    )
    actual_lexemes = tuple(str(value) for value in actual["fractional_lexemes"])
    deltas = tuple(
        _decimal_periodic_fractional_delta(expected_value, actual_lexeme)
        for expected_value, actual_lexeme in zip(
            expected_fractional,
            actual_lexemes,
        )
    )
    return {
        "delta": deltas,
        "tolerance": tolerance,
        "max_delta": max(deltas),
        "within_tolerance": all(
            delta <= axis_tolerance
            for delta, axis_tolerance in zip(deltas, tolerance)
        ),
    }


def _decimal_periodic_fractional_delta(expected: float, actual_lexeme: str) -> float:
    expected_decimal = Decimal(str(expected))
    actual_decimal = Decimal(
        re.sub(r"\(\d+\)$", "", str(actual_lexeme).strip())
    )
    precision = max(
        50,
        len(expected_decimal.as_tuple().digits),
        len(actual_decimal.as_tuple().digits),
    )
    with localcontext() as context:
        context.prec = precision
        expected_residue = _decimal_fractional_residue(expected_decimal)
        actual_residue = _decimal_fractional_residue(actual_decimal)
        delta = abs(expected_residue - actual_residue)
        wrapped = min(delta, Decimal(1) - delta)
    return float(wrapped)


def _decimal_fractional_residue(value: Decimal) -> Decimal:
    sign, digits, exponent = value.as_tuple()
    if exponent >= 0:
        return Decimal(0)
    fractional_digit_count = -exponent
    fractional_digits = (
        digits[-fractional_digit_count:]
        if fractional_digit_count < len(digits)
        else digits
    )
    residue = Decimal((0, fractional_digits or (0,), -fractional_digit_count))
    if sign and residue:
        return Decimal(1) - residue
    return residue


def _decimal_to_finite_float(value: Decimal) -> float:
    converted = float(value)
    if math.isfinite(converted):
        return converted
    return math.copysign(sys.float_info.max, converted)


def _exported_numeric_tolerance(lexeme: str, *, cap: float) -> float:
    token = re.sub(r"\(\d+\)$", "", str(lexeme).strip())
    mantissa, *exponent_parts = re.split(r"[Ee]", token, maxsplit=1)
    exponent = int(exponent_parts[0]) if exponent_parts else 0
    decimal_places = len(mantissa.partition(".")[2]) if "." in mantissa else 0
    last_digit_exponent = exponent - decimal_places
    try:
        half_last_digit = 0.5 * (10.0**last_digit_exponent)
    except OverflowError:
        half_last_digit = float("inf")
    return min(half_last_digit + _MS_EXPORT_FLOAT_EPSILON, cap)


def _strict_rejection_reasons(receipt: dict[str, Any]) -> list[str]:
    checks = (
        ("atom_count_matches", "atom_count_mismatch"),
        ("element_counts_match", "element_composition_mismatch"),
        ("atom_ids_match", "atom_label_mismatch"),
        ("atom_elements_match", "atom_element_mismatch"),
        ("fractional_coordinates_match", "fractional_geometry_mismatch"),
        ("lattice_matches", "lattice_geometry_mismatch"),
    )
    return [reason for key, reason in checks if not receipt.get(key)]


def _tokenize_cif_lines(text: str, errors: list[str]) -> list[list[str]]:
    lines: list[list[str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        try:
            lines.append(shlex.split(raw_line, comments=True, posix=True))
        except ValueError as exc:
            errors.append(f"CIF tokenization failed on line {line_number}: {exc}")
            lines.append([])
    return lines


def _parse_loop_tokens(
    logical_lines: list[list[str]],
    start: int,
) -> tuple[list[str], list[str], int]:
    headers: list[str] = []
    index = start
    while index < len(logical_lines):
        tokens = logical_lines[index]
        if not tokens:
            index += 1
            continue
        if not tokens[0].startswith("_"):
            break
        headers.extend(tokens)
        index += 1

    data_tokens: list[str] = []
    while index < len(logical_lines):
        tokens = logical_lines[index]
        if not tokens:
            index += 1
            continue
        first = tokens[0].lower()
        if first in {"loop_", "stop_"} or first.startswith("_") or first.startswith("data_"):
            break
        data_tokens.extend(tokens)
        index += 1
    return headers, data_tokens, index


def _parse_atom_loop(
    headers: list[str],
    data_tokens: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    width = len(headers)
    if width == 0:
        return []
    if len(data_tokens) % width:
        errors.append(
            f"CIF atom loop token count {len(data_tokens)} is not divisible by header count {width}."
        )
        return []
    header_index = {header: index for index, header in enumerate(headers)}
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(data_tokens), width):
        values = data_tokens[offset : offset + width]
        coordinate_lexemes = [
            values[header_index[_ATOM_HEADERS[key]]] for key in ("x", "y", "z")
        ]
        coordinates = [_parse_cif_number(value) for value in coordinate_lexemes]
        if any(value is None for value in coordinates):
            errors.append(
                "Invalid fractional coordinate in CIF atom row: " + " ".join(values)
            )
            continue
        rows.append(
            {
                "id": values[header_index[_ATOM_HEADERS["label"]]],
                "element": values[header_index[_ATOM_HEADERS["element"]]],
                "fractional": [float(value) for value in coordinates if value is not None],
                "fractional_lexemes": coordinate_lexemes,
            }
        )
    return rows


def _parse_cif_number(value: str) -> float | None:
    token = str(value).strip()
    if token in {".", "?"} or not _CIF_NUMBER_RE.fullmatch(token):
        return None
    token = re.sub(r"\(\d+\)$", "", token)
    try:
        return float(token)
    except ValueError:
        return None


def _periodic_fractional_delta(expected: float, actual: float) -> float:
    wrapped = abs(expected - actual) % 1.0
    return min(wrapped, 1.0 - wrapped)


def _element_counts(crystal: CrystalSpec) -> dict[str, int]:
    return dict(sorted(Counter(atom.element for atom in crystal.basis_atoms).items()))
