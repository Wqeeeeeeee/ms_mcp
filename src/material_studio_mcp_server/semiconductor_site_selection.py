"""Deterministic spatial site selection for periodic semiconductor models."""

from __future__ import annotations

import hashlib
import json
import math
import re
from itertools import combinations, product
from typing import Any, Mapping, Sequence

from .specs.crystal import BasisAtomSpec, CrystalSpec, LatticeSpec


PERIODIC_MAXIMIN_SCHEMA = "materials-studio-periodic-site-selection/v1"
PERIODIC_MAXIMIN_STRATEGY = "periodic_maximin"
PERIODIC_MAXIMIN_SCOPE = "deterministic_spatial_separation_heuristic_not_sqs"
PERIODIC_DISTANCE_MODE = "periodic_minimum_image_3x3"
MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES = 512
SITE_PAIR_DISTRIBUTION_SCHEMA = "materials-studio-site-pair-distribution/v1"
SITE_PAIR_DISTRIBUTION_SCOPE = "finite_supercell_pair_distribution_descriptive_not_sqs"
MAX_REPORTED_PAIR_DISTANCE_SHELLS = 24
MAX_PAIR_EXAMPLES_PER_SHELL = 12
PAIR_SHELL_ABSOLUTE_TOLERANCE_ANGSTROM = 1e-5
PAIR_SHELL_RELATIVE_TOLERANCE = 1e-6
_DISTANCE_TOLERANCE = 1e-8


def select_periodic_maximin_sites(
    crystal: CrystalSpec,
    candidates: Sequence[BasisAtomSpec],
    count: int,
) -> tuple[list[BasisAtomSpec], dict[str, Any]]:
    """Select periodic sites by deterministic farthest-point sampling.

    The first site is the naturally lowest atom ID. Each later site maximizes
    its minimum periodic distance to the already selected set, with atom ID as
    the stable tie-break. This is a spatial preflight heuristic, not an SQS.
    """

    ordered = _validated_candidates(candidates)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("periodic maximin site count must be a positive integer.")
    if count > len(ordered):
        raise ValueError("periodic maximin site count exceeds the candidate-site count.")
    if len(ordered) > MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES:
        raise ValueError(
            "periodic maximin exact audit supports at most "
            f"{MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES} candidate sites; use a smaller supercell or explicit sites."
        )

    lattice = _lattice_payload(crystal.lattice)
    candidate_sites = [_candidate_payload(atom) for atom in ordered]
    selected_sites, selection_steps = _select_from_payload(lattice, candidate_sites, count)
    selected_ids = [str(site["atom_id"]) for site in selected_sites]
    by_id = {atom.id: atom for atom in ordered}
    selected = [by_id[atom_id] for atom_id in selected_ids]

    baseline_sites = candidate_sites[:count]
    candidate_pair_stats = _pair_distance_stats(lattice, candidate_sites)
    selected_pair_stats = _pair_distance_stats(lattice, selected_sites)
    baseline_pair_stats = _pair_distance_stats(lattice, baseline_sites)
    candidate_nearest = candidate_pair_stats.get("minimum_angstrom")
    near_nearest_count = _pairs_near_distance(
        lattice,
        selected_sites,
        candidate_nearest,
    )
    selected_minimum = selected_pair_stats.get("minimum_angstrom")
    baseline_minimum = baseline_pair_stats.get("minimum_angstrom")
    improvement = (
        _round_distance(float(selected_minimum) - float(baseline_minimum))
        if selected_minimum is not None and baseline_minimum is not None
        else None
    )

    geometry_payload = {
        "lattice": lattice,
        "candidate_sites": candidate_sites,
    }
    receipt: dict[str, Any] = {
        "schema": PERIODIC_MAXIMIN_SCHEMA,
        "strategy": PERIODIC_MAXIMIN_STRATEGY,
        "strategy_version": 1,
        "distance_mode": PERIODIC_DISTANCE_MODE,
        "scientific_scope": PERIODIC_MAXIMIN_SCOPE,
        "maximum_exact_candidate_sites": MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES,
        "lattice": lattice,
        "candidate_site_count": len(candidate_sites),
        "target_site_count": count,
        "candidate_sites": candidate_sites,
        "candidate_geometry_sha256": _canonical_sha256(geometry_payload),
        "selected_atom_ids": selected_ids,
        "selection_steps": selection_steps,
        "selected_pair_distance_stats_angstrom": selected_pair_stats,
        "atom_id_order_baseline_atom_ids": [str(site["atom_id"]) for site in baseline_sites],
        "atom_id_order_pair_distance_stats_angstrom": baseline_pair_stats,
        "candidate_pair_distance_stats_angstrom": candidate_pair_stats,
        "minimum_distance_improvement_over_atom_id_order_angstrom": improvement,
        "selected_pairs_at_candidate_nearest_distance": near_nearest_count,
    }
    receipt["receipt_sha256"] = _receipt_sha256(receipt)
    return selected, receipt


def audit_periodic_maximin_selection(
    crystal: CrystalSpec,
    receipt: Mapping[str, Any],
    *,
    expected_selected_element: str | None = None,
) -> dict[str, Any]:
    """Audit a persisted maximin receipt against itself and current geometry."""

    errors: list[str] = []
    warnings: list[str] = [
        "Periodic maximin is a deterministic spatial-separation heuristic, not an SQS or thermodynamic alloy model."
    ]
    raw = dict(receipt) if isinstance(receipt, Mapping) else {}
    _check_contract(raw, errors)

    recorded_digest = raw.get("receipt_sha256")
    try:
        computed_digest = _receipt_sha256(raw)
    except (TypeError, ValueError):
        computed_digest = None
    digest_verified = isinstance(recorded_digest, str) and recorded_digest == computed_digest
    if not digest_verified:
        errors.append("Periodic maximin receipt SHA-256 does not match its recorded payload.")

    lattice = raw.get("lattice")
    candidate_sites = raw.get("candidate_sites")
    parsed_lattice = _validated_lattice_mapping(lattice, errors)
    parsed_candidates = _validated_candidate_payloads(candidate_sites, errors)
    target_count = _positive_int(raw.get("target_site_count"))
    if target_count is None:
        errors.append("Periodic maximin target_site_count must be a positive integer.")
    elif parsed_candidates is not None and target_count > len(parsed_candidates):
        errors.append("Periodic maximin target_site_count exceeds the recorded candidate-site count.")

    if parsed_candidates is not None:
        recorded_candidate_count = _nonnegative_int(raw.get("candidate_site_count"))
        if recorded_candidate_count != len(parsed_candidates):
            errors.append("Periodic maximin candidate_site_count does not match candidate_sites.")

    geometry_digest_verified = False
    receipt_replay_verified = False
    replay_selected_ids: list[str] = []
    if parsed_lattice is not None and parsed_candidates is not None:
        geometry_digest_verified = raw.get("candidate_geometry_sha256") == _canonical_sha256(
            {"lattice": parsed_lattice, "candidate_sites": parsed_candidates}
        )
        if not geometry_digest_verified:
            errors.append("Periodic maximin candidate geometry SHA-256 does not match its recorded geometry.")
        if target_count is not None and target_count <= len(parsed_candidates):
            replay_sites, replay_steps = _select_from_payload(parsed_lattice, parsed_candidates, target_count)
            replay_selected_ids = [str(site["atom_id"]) for site in replay_sites]
            replay_baseline = parsed_candidates[:target_count]
            replay_candidate_stats = _pair_distance_stats(parsed_lattice, parsed_candidates)
            replay_selected_stats = _pair_distance_stats(parsed_lattice, replay_sites)
            replay_baseline_stats = _pair_distance_stats(parsed_lattice, replay_baseline)
            selected_minimum = replay_selected_stats.get("minimum_angstrom")
            baseline_minimum = replay_baseline_stats.get("minimum_angstrom")
            replay_improvement = (
                _round_distance(float(selected_minimum) - float(baseline_minimum))
                if selected_minimum is not None and baseline_minimum is not None
                else None
            )
            replay_candidate_nearest = replay_candidate_stats.get("minimum_angstrom")
            replay_near_nearest_count = _pairs_near_distance(
                parsed_lattice,
                replay_sites,
                replay_candidate_nearest,
            )
            receipt_replay_verified = (
                replay_selected_ids == _string_list(raw.get("selected_atom_ids"))
                and replay_steps == raw.get("selection_steps")
                and replay_selected_stats == raw.get("selected_pair_distance_stats_angstrom")
                and [str(site["atom_id"]) for site in replay_baseline]
                == _string_list(raw.get("atom_id_order_baseline_atom_ids"))
                and replay_baseline_stats == raw.get("atom_id_order_pair_distance_stats_angstrom")
                and replay_candidate_stats == raw.get("candidate_pair_distance_stats_angstrom")
                and replay_improvement
                == raw.get("minimum_distance_improvement_over_atom_id_order_angstrom")
                and replay_near_nearest_count
                == raw.get("selected_pairs_at_candidate_nearest_distance")
            )
            if not receipt_replay_verified:
                errors.append(
                    "Periodic maximin recorded selection or distance metrics do not replay from its bound candidate geometry."
                )

    selected_ids = _string_list(raw.get("selected_atom_ids"))
    if len(selected_ids) != len(set(selected_ids)):
        errors.append("Periodic maximin selected_atom_ids contains duplicates.")
    if target_count is not None and len(selected_ids) != target_count:
        errors.append("Periodic maximin selected_atom_ids count does not match target_site_count.")
    candidate_ids = [str(site["atom_id"]) for site in parsed_candidates or []]
    if any(atom_id not in candidate_ids for atom_id in selected_ids):
        errors.append("Periodic maximin selected_atom_ids contains a site outside candidate_sites.")

    current_by_id = {atom.id: atom for atom in crystal.basis_atoms}
    missing_selected = [atom_id for atom_id in selected_ids if atom_id not in current_by_id]
    if missing_selected:
        errors.append(
            "Periodic maximin selected sites are missing from the current crystal: "
            + ", ".join(missing_selected)
            + "."
        )
    element_mismatches: list[str] = []
    if expected_selected_element:
        element_mismatches = [
            atom_id
            for atom_id in selected_ids
            if atom_id in current_by_id and current_by_id[atom_id].element != expected_selected_element
        ]
        if element_mismatches:
            errors.append(
                f"Periodic maximin selected sites no longer contain expected element {expected_selected_element}: "
                + ", ".join(element_mismatches)
                + "."
            )

    current_candidate_sites: list[dict[str, Any]] | None = None
    missing_candidates = [atom_id for atom_id in candidate_ids if atom_id not in current_by_id]
    if missing_candidates:
        warnings.append(
            "Current crystal is missing recorded periodic maximin candidate sites; geometry replay is unavailable."
        )
    elif candidate_ids:
        current_candidate_sites = [_candidate_payload(current_by_id[atom_id]) for atom_id in candidate_ids]
    try:
        current_lattice = _lattice_payload(crystal.lattice)
    except ValueError as exc:
        current_lattice = None
        errors.append(f"Current crystal lattice is invalid for periodic maximin audit: {exc}")
    current_geometry_sha256 = (
        _canonical_sha256({"lattice": current_lattice, "candidate_sites": current_candidate_sites})
        if current_lattice is not None and current_candidate_sites is not None
        else None
    )
    geometry_unchanged = bool(
        current_geometry_sha256
        and current_geometry_sha256 == raw.get("candidate_geometry_sha256")
    )
    current_replay_verified = False
    if (
        geometry_unchanged
        and current_lattice is not None
        and current_candidate_sites is not None
        and target_count is not None
    ):
        current_selected, _ = _select_from_payload(current_lattice, current_candidate_sites, target_count)
        current_replay_verified = [str(site["atom_id"]) for site in current_selected] == selected_ids
        if not current_replay_verified:
            errors.append("Current unchanged geometry does not replay the recorded periodic maximin selection.")
    elif parsed_candidates is not None:
        warnings.append(
            "Current lattice or candidate coordinates differ from the selection receipt; historical selection remains auditable but current-geometry replay is unavailable."
        )

    current_selected_sites = [
        _candidate_payload(current_by_id[atom_id])
        for atom_id in selected_ids
        if atom_id in current_by_id
    ]
    current_pair_stats = (
        _pair_distance_stats(current_lattice, current_selected_sites)
        if current_lattice is not None
        else _empty_pair_distance_stats()
    )
    candidate_nearest = None
    if isinstance(raw.get("candidate_pair_distance_stats_angstrom"), Mapping):
        candidate_nearest = _finite_float(
            raw["candidate_pair_distance_stats_angstrom"].get("minimum_angstrom")
        )
    near_nearest_count = (
        _pairs_near_distance(
            current_lattice,
            current_selected_sites,
            candidate_nearest,
        )
        if current_lattice is not None
        else 0
    )
    adjacent_pair_review_required = near_nearest_count > 0
    if adjacent_pair_review_required:
        warnings.append(
            "Selected substituted sites include candidate-nearest periodic pairs; review the composition and local environments."
        )

    return {
        "available": True,
        "schema": raw.get("schema"),
        "strategy": raw.get("strategy"),
        "scientific_scope": raw.get("scientific_scope"),
        "integrity_ok": not errors,
        "receipt_sha256_verified": digest_verified,
        "candidate_geometry_sha256_verified": geometry_digest_verified,
        "receipt_replay_verified": receipt_replay_verified,
        "geometry_unchanged": geometry_unchanged,
        "current_geometry_replay_verified": current_replay_verified,
        "replay_verified": bool(receipt_replay_verified and geometry_unchanged and current_replay_verified),
        "expected_selected_element": expected_selected_element,
        "selected_atom_ids": selected_ids,
        "replay_selected_atom_ids": replay_selected_ids,
        "missing_selected_atom_ids": missing_selected,
        "element_mismatch_atom_ids": element_mismatches,
        "current_selected_pair_distance_stats_angstrom": current_pair_stats,
        "selected_pairs_at_candidate_nearest_distance": near_nearest_count,
        "adjacent_pair_review_required": adjacent_pair_review_required,
        "minimum_distance_improvement_over_atom_id_order_angstrom": raw.get(
            "minimum_distance_improvement_over_atom_id_order_angstrom"
        ),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def analyze_periodic_site_pair_distribution(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Describe selected-site pairs by periodic distance shell.

    The fixed-composition expectation is the exact probability that two
    distinct candidate sites are both selected when choosing K of N sites:
    K(K-1) / N(N-1). The result is descriptive finite-cell evidence and does
    not establish statistical significance, SQS quality, or thermodynamics.
    """

    errors: list[str] = []
    warnings: list[str] = [
        "Pair-distribution values describe one finite periodic supercell and do not establish SQS quality or thermodynamic randomness.",
        "Distance shells use numerical distance tolerance and are not crystallographic symmetry-orbit classifications.",
    ]
    raw = dict(receipt) if isinstance(receipt, Mapping) else {}
    _check_contract(raw, errors)

    try:
        computed_receipt_sha256 = _receipt_sha256(raw)
    except (TypeError, ValueError):
        computed_receipt_sha256 = None
    receipt_sha256_verified = bool(
        isinstance(raw.get("receipt_sha256"), str)
        and raw.get("receipt_sha256") == computed_receipt_sha256
    )
    if not receipt_sha256_verified:
        errors.append("Site pair-distribution input receipt SHA-256 is invalid.")

    lattice = _validated_lattice_mapping(raw.get("lattice"), errors)
    candidates = _validated_candidate_payloads(raw.get("candidate_sites"), errors)
    geometry_sha256_verified = False
    if lattice is not None and candidates is not None:
        geometry_sha256_verified = raw.get("candidate_geometry_sha256") == _canonical_sha256(
            {"lattice": lattice, "candidate_sites": candidates}
        )
        if not geometry_sha256_verified:
            errors.append("Site pair-distribution candidate geometry SHA-256 is invalid.")

    selected_ids = _string_list(raw.get("selected_atom_ids"))
    baseline_ids = _string_list(raw.get("atom_id_order_baseline_atom_ids"))
    target_count = _positive_int(raw.get("target_site_count"))
    candidate_ids = [str(site["atom_id"]) for site in candidates or []]
    candidate_id_set = set(candidate_ids)
    recorded_candidate_count = _nonnegative_int(raw.get("candidate_site_count"))
    if candidates is not None and recorded_candidate_count != len(candidates):
        errors.append("Site pair-distribution candidate_site_count does not match candidate_sites.")
    if not selected_ids:
        errors.append("Site pair-distribution input has no selected_atom_ids.")
    if len(selected_ids) != len(set(selected_ids)):
        errors.append("Site pair-distribution selected_atom_ids contains duplicates.")
    if len(baseline_ids) != len(set(baseline_ids)):
        errors.append("Site pair-distribution atom-ID-order baseline contains duplicates.")
    if target_count is None:
        errors.append("Site pair-distribution target_site_count must be a positive integer.")
    else:
        if candidates is not None and target_count > len(candidates):
            errors.append("Site pair-distribution target_site_count exceeds candidate_sites.")
        if len(selected_ids) != target_count:
            errors.append("Site pair-distribution selected-site count does not match target_site_count.")
        if len(baseline_ids) != target_count:
            errors.append("Site pair-distribution baseline-site count does not match target_site_count.")
    if any(atom_id not in candidate_id_set for atom_id in selected_ids):
        errors.append("Site pair-distribution selected_atom_ids contains a non-candidate site.")
    if any(atom_id not in candidate_id_set for atom_id in baseline_ids):
        errors.append("Site pair-distribution baseline atom IDs contain a non-candidate site.")

    baseline_order_verified = False
    selection_replay_verified = False
    if (
        lattice is not None
        and candidates is not None
        and target_count is not None
        and target_count <= len(candidates)
    ):
        expected_baseline_ids = candidate_ids[:target_count]
        baseline_order_verified = baseline_ids == expected_baseline_ids
        if not baseline_order_verified:
            errors.append(
                "Site pair-distribution atom-ID-order baseline does not match the deterministic candidate order."
            )
        replay_sites, _ = _select_from_payload(lattice, candidates, target_count)
        selection_replay_verified = [str(site["atom_id"]) for site in replay_sites] == selected_ids
        if not selection_replay_verified:
            errors.append(
                "Site pair-distribution selected_atom_ids do not replay under periodic maximin."
            )

    base_result: dict[str, Any] = {
        "available": bool(raw),
        "schema": SITE_PAIR_DISTRIBUTION_SCHEMA,
        "scientific_scope": SITE_PAIR_DISTRIBUTION_SCOPE,
        "geometry_basis": "recorded_candidate_geometry",
        "distance_mode": PERIODIC_DISTANCE_MODE,
        "source_receipt_sha256": raw.get("receipt_sha256"),
        "source_receipt_sha256_verified": receipt_sha256_verified,
        "candidate_geometry_sha256": raw.get("candidate_geometry_sha256"),
        "candidate_geometry_sha256_verified": geometry_sha256_verified,
        "selection_replay_verified": selection_replay_verified,
        "atom_id_order_baseline_verified": baseline_order_verified,
        "candidate_site_count": len(candidate_ids),
        "selected_site_count": len(selected_ids),
        "selected_atom_ids": selected_ids,
        "atom_id_order_baseline_atom_ids": baseline_ids,
        "shell_absolute_tolerance_angstrom": PAIR_SHELL_ABSOLUTE_TOLERANCE_ANGSTROM,
        "shell_relative_tolerance": PAIR_SHELL_RELATIVE_TOLERANCE,
        "maximum_reported_shells": MAX_REPORTED_PAIR_DISTANCE_SHELLS,
    }
    if errors or lattice is None or candidates is None:
        return {
            **base_result,
            "integrity_ok": False,
            "shell_count": 0,
            "reported_shell_count": 0,
            "shells_truncated": False,
            "shells": [],
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "analysis_sha256": None,
        }

    selected_set = set(selected_ids)
    baseline_set = set(baseline_ids)
    vectors = _lattice_vectors(lattice)
    pair_rows: list[tuple[float, str, str]] = []
    for left, right in combinations(candidates, 2):
        pair_rows.append(
            (
                _site_distance(left, right, vectors),
                str(left["atom_id"]),
                str(right["atom_id"]),
            )
        )
    pair_rows.sort(key=lambda item: (item[0], _natural_atom_id_key(item[1]), _natural_atom_id_key(item[2])))
    distance_shells = _group_pair_distance_shells(pair_rows)

    candidate_count = len(candidates)
    selected_count = len(selected_ids)
    candidate_pair_count_expected = candidate_count * (candidate_count - 1) // 2
    selected_pair_count_expected = selected_count * (selected_count - 1) // 2
    fixed_composition_pair_probability = (
        selected_count * (selected_count - 1) / (candidate_count * (candidate_count - 1))
        if candidate_count > 1
        else 0.0
    )

    shell_rows: list[dict[str, Any]] = []
    selected_pair_count_total = 0
    baseline_pair_count_total = 0
    selected_weighted_squared_deviation = 0.0
    baseline_weighted_squared_deviation = 0.0
    for shell_index, shell_pairs in enumerate(distance_shells, start=1):
        distances = [item[0] for item in shell_pairs]
        selected_pairs = [
            item for item in shell_pairs if item[1] in selected_set and item[2] in selected_set
        ]
        baseline_pairs = [
            item for item in shell_pairs if item[1] in baseline_set and item[2] in baseline_set
        ]
        candidate_pair_count = len(shell_pairs)
        selected_pair_count = len(selected_pairs)
        baseline_pair_count = len(baseline_pairs)
        selected_pair_fraction = selected_pair_count / candidate_pair_count
        baseline_pair_fraction = baseline_pair_count / candidate_pair_count
        expected_selected_pair_count = candidate_pair_count * fixed_composition_pair_probability
        selected_pair_count_total += selected_pair_count
        baseline_pair_count_total += baseline_pair_count
        selected_weighted_squared_deviation += candidate_pair_count * (
            selected_pair_fraction - fixed_composition_pair_probability
        ) ** 2
        baseline_weighted_squared_deviation += candidate_pair_count * (
            baseline_pair_fraction - fixed_composition_pair_probability
        ) ** 2
        shell_rows.append(
            {
                "shell_index": shell_index,
                "distance_min_angstrom": _round_distance(min(distances)),
                "distance_mean_angstrom": _round_distance(sum(distances) / len(distances)),
                "distance_max_angstrom": _round_distance(max(distances)),
                "candidate_pair_count": candidate_pair_count,
                "coordination_number_per_candidate": _round_distance(
                    2.0 * candidate_pair_count / candidate_count
                ),
                "selected_pair_count": selected_pair_count,
                "selected_pair_fraction": _round_probability(selected_pair_fraction),
                "baseline_pair_count": baseline_pair_count,
                "baseline_pair_fraction": _round_probability(baseline_pair_fraction),
                "fixed_composition_expected_pair_count": _round_distance(expected_selected_pair_count),
                "fixed_composition_expected_pair_fraction": _round_probability(
                    fixed_composition_pair_probability
                ),
                "selected_minus_expected_pair_count": _round_distance(
                    selected_pair_count - expected_selected_pair_count
                ),
                "baseline_minus_expected_pair_count": _round_distance(
                    baseline_pair_count - expected_selected_pair_count
                ),
                "selected_pair_avoidance_fraction": _pair_avoidance_fraction(
                    selected_pair_count,
                    expected_selected_pair_count,
                ),
                "selected_pair_expectation_class": _pair_expectation_class(
                    selected_pair_count,
                    expected_selected_pair_count,
                ),
                "baseline_pair_expectation_class": _pair_expectation_class(
                    baseline_pair_count,
                    expected_selected_pair_count,
                ),
                "selected_pair_examples": [
                    f"{left_id}-{right_id}"
                    for _, left_id, right_id in selected_pairs[:MAX_PAIR_EXAMPLES_PER_SHELL]
                ],
                "selected_pair_examples_truncated": len(selected_pairs) > MAX_PAIR_EXAMPLES_PER_SHELL,
                "baseline_pair_examples": [
                    f"{left_id}-{right_id}"
                    for _, left_id, right_id in baseline_pairs[:MAX_PAIR_EXAMPLES_PER_SHELL]
                ],
                "baseline_pair_examples_truncated": len(baseline_pairs) > MAX_PAIR_EXAMPLES_PER_SHELL,
            }
        )

    pair_conservation_verified = (
        len(pair_rows) == candidate_pair_count_expected
        and selected_pair_count_total == selected_pair_count_expected
        and baseline_pair_count_total == selected_pair_count_expected
    )
    if not pair_conservation_verified:
        errors.append("Site pair-distribution pair-count conservation failed.")

    total_candidate_pairs = max(len(pair_rows), 1)
    selected_rmse = math.sqrt(selected_weighted_squared_deviation / total_candidate_pairs)
    baseline_rmse = math.sqrt(baseline_weighted_squared_deviation / total_candidate_pairs)
    nearest_shell = shell_rows[0] if shell_rows else {}
    nearest_selected = int(nearest_shell.get("selected_pair_count") or 0)
    nearest_baseline = int(nearest_shell.get("baseline_pair_count") or 0)
    nearest_expected = float(nearest_shell.get("fixed_composition_expected_pair_count") or 0.0)
    nearest_class = str(nearest_shell.get("selected_pair_expectation_class") or "unavailable")
    nearest_shell_pair_excess_review_required = nearest_class == "above_fixed_composition_expectation"
    nearest_shell_pair_avoidance_observed = nearest_class == "below_fixed_composition_expectation"
    if nearest_shell_pair_excess_review_required:
        warnings.append(
            "The nearest candidate-site distance shell has more selected-selected pairs than the fixed-composition expectation; review local clustering."
        )

    result = {
        **base_result,
        "integrity_ok": not errors,
        "selected_fraction": _round_probability(selected_count / candidate_count),
        "fixed_composition_expected_pair_probability": _round_probability(
            fixed_composition_pair_probability
        ),
        "candidate_pair_count": len(pair_rows),
        "expected_candidate_pair_count": candidate_pair_count_expected,
        "selected_pair_count": selected_pair_count_total,
        "expected_selected_pair_count": selected_pair_count_expected,
        "baseline_pair_count": baseline_pair_count_total,
        "pair_conservation_verified": pair_conservation_verified,
        "shell_count": len(shell_rows),
        "reported_shell_count": min(len(shell_rows), MAX_REPORTED_PAIR_DISTANCE_SHELLS),
        "shells_truncated": len(shell_rows) > MAX_REPORTED_PAIR_DISTANCE_SHELLS,
        "unreported_shell_count": max(len(shell_rows) - MAX_REPORTED_PAIR_DISTANCE_SHELLS, 0),
        "shells": shell_rows[:MAX_REPORTED_PAIR_DISTANCE_SHELLS],
        "nearest_shell_distance_mean_angstrom": nearest_shell.get("distance_mean_angstrom"),
        "nearest_shell_candidate_pair_count": nearest_shell.get("candidate_pair_count"),
        "nearest_shell_selected_pair_count": nearest_selected,
        "nearest_shell_baseline_pair_count": nearest_baseline,
        "nearest_shell_fixed_composition_expected_pair_count": _round_distance(nearest_expected),
        "nearest_shell_pair_count_reduction_vs_atom_id_order": nearest_baseline - nearest_selected,
        "nearest_shell_selected_pair_avoidance_fraction": nearest_shell.get(
            "selected_pair_avoidance_fraction"
        ),
        "nearest_shell_pair_expectation_class": nearest_class,
        "nearest_shell_pair_excess_review_required": nearest_shell_pair_excess_review_required,
        "nearest_shell_pair_avoidance_observed": nearest_shell_pair_avoidance_observed,
        "selection_reduces_nearest_shell_pairs_vs_atom_id_order": nearest_selected < nearest_baseline,
        "selected_pair_first_occupied_shell_index": _first_occupied_shell_index(
            shell_rows,
            "selected_pair_count",
        ),
        "baseline_pair_first_occupied_shell_index": _first_occupied_shell_index(
            shell_rows,
            "baseline_pair_count",
        ),
        "selected_pair_fraction_rmse_from_fixed_composition_expectation": _round_probability(
            selected_rmse
        ),
        "baseline_pair_fraction_rmse_from_fixed_composition_expectation": _round_probability(
            baseline_rmse
        ),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    result["analysis_sha256"] = _canonical_sha256(result)
    return result


def _group_pair_distance_shells(
    pair_rows: Sequence[tuple[float, str, str]],
) -> list[list[tuple[float, str, str]]]:
    shells: list[list[tuple[float, str, str]]] = []
    for pair in pair_rows:
        if not shells:
            shells.append([pair])
            continue
        anchor = shells[-1][0][0]
        tolerance = max(
            PAIR_SHELL_ABSOLUTE_TOLERANCE_ANGSTROM,
            PAIR_SHELL_RELATIVE_TOLERANCE * max(abs(anchor), abs(pair[0])),
        )
        if abs(pair[0] - anchor) <= tolerance:
            shells[-1].append(pair)
        else:
            shells.append([pair])
    return shells


def _pair_avoidance_fraction(observed: int, expected: float) -> float | None:
    if expected <= 0.0:
        return None
    return _round_probability(1.0 - observed / expected)


def _pair_expectation_class(observed: int, expected: float) -> str:
    if expected <= 0.0:
        return "zero_fixed_composition_expectation"
    tolerance = max(0.25, 0.1 * expected)
    if observed > expected + tolerance:
        return "above_fixed_composition_expectation"
    if observed < expected - tolerance:
        return "below_fixed_composition_expectation"
    return "near_fixed_composition_expectation"


def _first_occupied_shell_index(shells: Sequence[Mapping[str, Any]], field: str) -> int | None:
    for shell in shells:
        if int(shell.get(field) or 0) > 0:
            return int(shell["shell_index"])
    return None


def _check_contract(receipt: Mapping[str, Any], errors: list[str]) -> None:
    expected = {
        "schema": PERIODIC_MAXIMIN_SCHEMA,
        "strategy": PERIODIC_MAXIMIN_STRATEGY,
        "strategy_version": 1,
        "distance_mode": PERIODIC_DISTANCE_MODE,
        "scientific_scope": PERIODIC_MAXIMIN_SCOPE,
        "maximum_exact_candidate_sites": MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES,
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            errors.append(f"Periodic maximin {field} must equal {value!r}.")


def _validated_candidates(candidates: Sequence[BasisAtomSpec]) -> list[BasisAtomSpec]:
    ordered = sorted(candidates, key=lambda atom: _natural_atom_id_key(atom.id))
    if not ordered:
        raise ValueError("periodic maximin selection requires at least one candidate site.")
    ids = [atom.id for atom in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("periodic maximin candidate atom IDs must be unique.")
    for atom in ordered:
        values = (atom.fractional.x, atom.fractional.y, atom.fractional.z)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError(f"periodic maximin candidate {atom.id} has non-finite coordinates.")
    return ordered


def _select_from_payload(
    lattice: Mapping[str, float],
    candidate_sites: Sequence[Mapping[str, Any]],
    count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(
        (dict(site) for site in candidate_sites),
        key=lambda site: _natural_atom_id_key(str(site["atom_id"])),
    )
    vectors = _lattice_vectors(lattice)
    selected = [ordered[0]]
    steps: list[dict[str, Any]] = [
        {
            "step": 1,
            "atom_id": str(ordered[0]["atom_id"]),
            "minimum_distance_to_selected_angstrom": None,
            "tie_break": "natural_atom_id_seed",
        }
    ]
    remaining = {str(site["atom_id"]): site for site in ordered[1:]}
    minimum_distance_by_id = {
        atom_id: _site_distance(site, selected[0], vectors)
        for atom_id, site in remaining.items()
    }
    while len(selected) < count:
        choices: list[tuple[float, tuple[Any, ...], dict[str, Any]]] = [
            (-round(minimum_distance_by_id[atom_id], 12), _natural_atom_id_key(atom_id), site)
            for atom_id, site in remaining.items()
        ]
        choices.sort(key=lambda item: (item[0], item[1]))
        chosen = choices[0][2]
        chosen_id = str(chosen["atom_id"])
        chosen_minimum = -choices[0][0]
        selected.append(chosen)
        remaining.pop(chosen_id)
        minimum_distance_by_id.pop(chosen_id)
        for atom_id, site in remaining.items():
            minimum_distance_by_id[atom_id] = min(
                minimum_distance_by_id[atom_id],
                _site_distance(site, chosen, vectors),
            )
        steps.append(
            {
                "step": len(selected),
                "atom_id": str(chosen["atom_id"]),
                "minimum_distance_to_selected_angstrom": _round_distance(chosen_minimum),
                "tie_break": "maximum_minimum_periodic_distance_then_natural_atom_id",
            }
        )
    return selected, steps


def _pair_distance_stats(
    lattice: Mapping[str, float],
    sites: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(sites) < 2:
        return _empty_pair_distance_stats()
    vectors = _lattice_vectors(lattice)
    distances = [_site_distance(left, right, vectors) for left, right in combinations(sites, 2)]
    return {
        "pair_count": len(distances),
        "minimum_angstrom": _round_distance(min(distances)),
        "mean_angstrom": _round_distance(sum(distances) / len(distances)),
        "maximum_angstrom": _round_distance(max(distances)),
    }


def _pairs_near_distance(
    lattice: Mapping[str, float],
    sites: Sequence[Mapping[str, Any]],
    distance: float | None,
) -> int:
    if len(sites) < 2 or distance is None or not math.isfinite(float(distance)):
        return 0
    vectors = _lattice_vectors(lattice)
    return sum(
        1
        for left, right in combinations(sites, 2)
        if _site_distance(left, right, vectors) <= float(distance) + _DISTANCE_TOLERANCE
    )


def _empty_pair_distance_stats() -> dict[str, Any]:
    return {
        "pair_count": 0,
        "minimum_angstrom": None,
        "mean_angstrom": None,
        "maximum_angstrom": None,
    }


def _site_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    vectors: Sequence[Sequence[float]],
) -> float:
    left_fractional = [float(value) for value in left["fractional"]]
    right_fractional = [float(value) for value in right["fractional"]]
    delta = [right_fractional[index] - left_fractional[index] for index in range(3)]
    best = math.inf
    for offset in product((-1, 0, 1), repeat=3):
        fractional = [delta[index] + offset[index] for index in range(3)]
        cartesian = [
            sum(fractional[basis] * vectors[basis][axis] for basis in range(3))
            for axis in range(3)
        ]
        best = min(best, math.sqrt(sum(value * value for value in cartesian)))
    return best


def _lattice_vectors(lattice: Mapping[str, float]) -> tuple[tuple[float, float, float], ...]:
    a = float(lattice["a"])
    b = float(lattice["b"])
    c = float(lattice["c"])
    alpha = math.radians(float(lattice["alpha"]))
    beta = math.radians(float(lattice["beta"]))
    gamma = math.radians(float(lattice["gamma"]))
    sin_gamma = math.sin(gamma)
    if abs(sin_gamma) <= 1e-12:
        raise ValueError("periodic maximin lattice gamma produces a degenerate cell.")
    vector_a = (a, 0.0, 0.0)
    vector_b = (b * math.cos(gamma), b * sin_gamma, 0.0)
    c_x = c * math.cos(beta)
    c_y = c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / sin_gamma
    c_z_squared = c * c - c_x * c_x - c_y * c_y
    if c_z_squared <= 1e-12:
        raise ValueError("periodic maximin lattice parameters produce a degenerate cell.")
    vector_c = (c_x, c_y, math.sqrt(c_z_squared))
    return vector_a, vector_b, vector_c


def _lattice_payload(lattice: LatticeSpec) -> dict[str, float]:
    payload = {
        "a": float(lattice.a),
        "b": float(lattice.b),
        "c": float(lattice.c),
        "alpha": float(lattice.alpha),
        "beta": float(lattice.beta),
        "gamma": float(lattice.gamma),
    }
    _lattice_vectors(payload)
    return payload


def _candidate_payload(atom: BasisAtomSpec) -> dict[str, Any]:
    return {
        "atom_id": atom.id,
        "fractional": [
            _round_fractional(atom.fractional.x),
            _round_fractional(atom.fractional.y),
            _round_fractional(atom.fractional.z),
        ],
    }


def _validated_lattice_mapping(
    value: Any,
    errors: list[str],
) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        errors.append("Periodic maximin receipt is missing a lattice mapping.")
        return None
    try:
        parsed = LatticeSpec.model_validate(dict(value))
        return _lattice_payload(parsed)
    except (TypeError, ValueError) as exc:
        errors.append(f"Periodic maximin receipt lattice is invalid: {exc}")
        return None


def _validated_candidate_payloads(
    value: Any,
    errors: list[str],
) -> list[dict[str, Any]] | None:
    if not isinstance(value, list) or not value:
        errors.append("Periodic maximin receipt candidate_sites must be a non-empty list.")
        return None
    if len(value) > MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES:
        errors.append(
            "Periodic maximin receipt exceeds the exact candidate-site audit limit of "
            f"{MAX_EXACT_PERIODIC_MAXIMIN_CANDIDATES}."
        )
        return None
    parsed: list[dict[str, Any]] = []
    ids: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            errors.append(f"Periodic maximin candidate_sites[{index}] must be an object.")
            continue
        atom_id = item.get("atom_id")
        fractional = item.get("fractional")
        if not isinstance(atom_id, str) or not atom_id:
            errors.append(f"Periodic maximin candidate_sites[{index}] has an invalid atom_id.")
            continue
        if not isinstance(fractional, list) or len(fractional) != 3:
            errors.append(f"Periodic maximin candidate_sites[{index}] must have three fractional values.")
            continue
        values = [_finite_float(component) for component in fractional]
        if any(component is None for component in values):
            errors.append(f"Periodic maximin candidate_sites[{index}] has non-finite fractional values.")
            continue
        if any(float(component) < 0.0 or float(component) > 1.0 for component in values):
            errors.append(f"Periodic maximin candidate_sites[{index}] has fractional values outside [0, 1].")
            continue
        ids.append(atom_id)
        parsed.append(
            {
                "atom_id": atom_id,
                "fractional": [_round_fractional(float(component)) for component in values],
            }
        )
    if len(parsed) != len(value):
        return None
    if len(ids) != len(set(ids)):
        errors.append("Periodic maximin candidate_sites contains duplicate atom IDs.")
        return None
    return sorted(parsed, key=lambda site: _natural_atom_id_key(str(site["atom_id"])))


def _receipt_sha256(receipt: Mapping[str, Any]) -> str:
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    return _canonical_sha256(payload)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _natural_atom_id_key(atom_id: str) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", atom_id)
    return tuple(int(part) if part.isdigit() else part.casefold() for part in parts)


def _round_fractional(value: float) -> float:
    return round(float(value), 12)


def _round_distance(value: float) -> float:
    return round(float(value), 9)


def _round_probability(value: float) -> float:
    return round(float(value), 12)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    return list(value)
