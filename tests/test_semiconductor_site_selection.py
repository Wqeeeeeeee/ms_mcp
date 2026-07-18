from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path

from material_studio_mcp_server.diagnostics import model_view_audit, write_view_audit_bundle
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.semiconductor_site_selection import (
    PERIODIC_MAXIMIN_SCOPE,
    audit_periodic_maximin_selection,
    select_periodic_maximin_sites,
)
from material_studio_mcp_server.specs.crystal import BasisAtomSpec, CrystalSpec, LatticeSpec
from material_studio_mcp_server.specs.project import ModelSpec


def _crystal(*sites: tuple[str, float, float, float]) -> CrystalSpec:
    return CrystalSpec(
        name="site-selection-test",
        lattice=LatticeSpec(a=10.0, b=10.0, c=10.0, alpha=90.0, beta=90.0, gamma=90.0),
        basis_atoms=[
            BasisAtomSpec(id=atom_id, element="Si", fractional={"x": x, "y": y, "z": z})
            for atom_id, x, y, z in sites
        ],
    )


def _rehash_receipt(receipt: dict[str, object]) -> None:
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()


def test_periodic_maximin_is_deterministic_and_improves_over_atom_id_order() -> None:
    crystal = _crystal(
        ("Si1", 0.0, 0.0, 0.0),
        ("Si2", 0.1, 0.0, 0.0),
        ("Si3", 0.5, 0.0, 0.0),
        ("Si4", 0.6, 0.0, 0.0),
    )

    selected, receipt = select_periodic_maximin_sites(crystal, crystal.basis_atoms, 2)
    repeated, repeated_receipt = select_periodic_maximin_sites(crystal, crystal.basis_atoms, 2)

    assert [atom.id for atom in selected] == ["Si1", "Si3"]
    assert [atom.id for atom in repeated] == ["Si1", "Si3"]
    assert receipt == repeated_receipt
    assert receipt["scientific_scope"] == PERIODIC_MAXIMIN_SCOPE
    assert receipt["selected_pair_distance_stats_angstrom"]["minimum_angstrom"] == 5.0
    assert receipt["atom_id_order_pair_distance_stats_angstrom"]["minimum_angstrom"] == 1.0
    assert receipt["minimum_distance_improvement_over_atom_id_order_angstrom"] == 4.0
    audit = audit_periodic_maximin_selection(crystal, receipt)
    assert audit["integrity_ok"] is True
    assert audit["replay_verified"] is True


def test_periodic_maximin_uses_minimum_image_across_cell_boundary() -> None:
    crystal = _crystal(
        ("Si1", 0.02, 0.0, 0.0),
        ("Si2", 0.98, 0.0, 0.0),
        ("Si3", 0.50, 0.0, 0.0),
    )

    selected, receipt = select_periodic_maximin_sites(crystal, crystal.basis_atoms, 2)

    assert [atom.id for atom in selected] == ["Si1", "Si3"]
    assert receipt["selected_pair_distance_stats_angstrom"]["minimum_angstrom"] == 4.8
    assert receipt["candidate_pair_distance_stats_angstrom"]["minimum_angstrom"] == 0.4


def test_periodic_maximin_audit_rejects_tampering_and_degrades_on_geometry_change() -> None:
    crystal = _crystal(
        ("Si1", 0.0, 0.0, 0.0),
        ("Si2", 0.1, 0.0, 0.0),
        ("Si3", 0.5, 0.0, 0.0),
    )
    _, receipt = select_periodic_maximin_sites(crystal, crystal.basis_atoms, 2)
    tampered = {**receipt, "selected_atom_ids": ["Si1", "Si2"]}

    tampered_audit = audit_periodic_maximin_selection(crystal, tampered)
    assert tampered_audit["integrity_ok"] is False
    assert tampered_audit["receipt_sha256_verified"] is False
    assert tampered_audit["receipt_replay_verified"] is False

    forged_metrics = copy.deepcopy(receipt)
    forged_metrics["selected_pair_distance_stats_angstrom"]["minimum_angstrom"] = 9.0
    _rehash_receipt(forged_metrics)
    forged_audit = audit_periodic_maximin_selection(crystal, forged_metrics)
    assert forged_audit["receipt_sha256_verified"] is True
    assert forged_audit["receipt_replay_verified"] is False
    assert forged_audit["integrity_ok"] is False

    moved_atoms = [
        atom.model_copy(update={"fractional": atom.fractional.model_copy(update={"y": 0.2})})
        if atom.id == "Si2"
        else atom
        for atom in crystal.basis_atoms
    ]
    moved = crystal.model_copy(update={"basis_atoms": moved_atoms})
    moved_audit = audit_periodic_maximin_selection(moved, receipt)
    assert moved_audit["integrity_ok"] is True
    assert moved_audit["receipt_replay_verified"] is True
    assert moved_audit["geometry_unchanged"] is False
    assert moved_audit["replay_verified"] is False


def test_explicit_distributed_alloy_exports_selection_audit_and_preserves_default(tmp_path: Path) -> None:
    distributed_plan = infer_modeling_plan(
        "Build silicon crystal as a 2x2x1 supercell and uniformly distribute 25% Ge alloy."
    )
    distributed = ModelSpec.model_validate(distributed_plan.payload)
    record = distributed.metadata["last_applied_alloy"]
    assert record["selection_strategy"] == "periodic_maximin"
    assert record["selected_atom_ids"][:2] == ["Si1_000", "Si1_110"]

    audit = model_view_audit(distributed)
    summary = audit["health"]["semiconductor_health"]["alloy_summary"]
    assert summary["periodic_maximin_count"] == 1
    assert summary["site_selection_integrity_ok"] is True
    assert summary["site_selection_replay_verified"] is True
    assert summary["latest"]["selection_strategy"] == "periodic_maximin"
    assert summary["latest"]["site_selection_audit"]["receipt_sha256_verified"] is True
    assert summary["latest"]["selected_pair_minimum_angstrom"] > 0

    bundle = write_view_audit_bundle(tmp_path, distributed, audit)
    with Path(bundle["files"]["semiconductor_alloy_csv"]).open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["selection_strategy"] == "periodic_maximin"
    assert row["scientific_scope"] == PERIODIC_MAXIMIN_SCOPE
    assert row["site_selection_integrity_ok"] == "True"
    assert float(row["selected_pair_minimum_angstrom"]) > 0

    default_plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and make 25% Ge alloy."
    )
    default = ModelSpec.model_validate(default_plan.payload)
    default_record = default.metadata["last_applied_alloy"]
    assert default_record["selected_atom_ids"] == ["Si1_000", "Si1_100", "Si2_000", "Si2_100"]
    assert "selection_strategy" not in default_record
    assert "site_selection" not in default_record


def test_explicit_distributed_dopant_fraction_is_preview_metadata_only() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x2x1 supercell and spatially distribute 6.25% P dopants."
    )
    spec = ModelSpec.model_validate(plan.payload)
    record = spec.metadata["last_applied_dopant_fraction"]

    assert plan.kind == "spec"
    assert record["selection_strategy"] == "periodic_maximin"
    assert record["selected_atom_ids"] == ["Si1_000", "Si1_110"]
    summary = model_view_audit(spec)["health"]["semiconductor_health"]["dopant_fraction_summary"]
    assert summary["periodic_maximin_count"] == 1
    assert summary["site_selection_integrity_ok"] is True
    assert summary["site_selection_replay_verified"] is True


def test_diagnostic_audit_fails_tampered_receipt_but_not_later_geometry_drift() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x2x1 supercell and uniformly distribute 25% Ge alloy."
    )
    spec = ModelSpec.model_validate(plan.payload)

    tampered_payload = spec.model_dump(mode="json")
    tampered_record = tampered_payload["metadata"]["last_applied_alloy"]
    tampered_record["site_selection"]["selected_atom_ids"][1] = "Si1_100"
    tampered_payload["metadata"]["applied_alloy"][-1] = tampered_record
    tampered = ModelSpec.model_validate(tampered_payload)
    tampered_audit = model_view_audit(tampered)
    tampered_summary = tampered_audit["health"]["semiconductor_health"]["alloy_summary"]
    assert tampered_audit["health"]["ok"] is False
    assert tampered_summary["site_selection_integrity_ok"] is False
    assert tampered_summary["site_selection_error_count"] >= 1

    moved_payload = spec.model_dump(mode="json")
    receipt = moved_payload["metadata"]["last_applied_alloy"]["site_selection"]
    selected_ids = set(receipt["selected_atom_ids"])
    nonselected_id = next(
        site["atom_id"] for site in receipt["candidate_sites"] if site["atom_id"] not in selected_ids
    )
    moved_atom = next(atom for atom in moved_payload["model"]["basis_atoms"] if atom["id"] == nonselected_id)
    moved_atom["fractional"]["z"] = 0.1
    moved = ModelSpec.model_validate(moved_payload)
    moved_summary = model_view_audit(moved)["health"]["semiconductor_health"]["alloy_summary"]
    assert moved_summary["site_selection_integrity_ok"] is True
    assert moved_summary["site_selection_replay_verified"] is False
    assert moved_summary["latest"]["site_selection_geometry_unchanged"] is False
