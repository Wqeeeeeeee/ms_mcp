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
