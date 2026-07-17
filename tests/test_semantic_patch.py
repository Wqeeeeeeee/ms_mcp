from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from material_studio_mcp_server.diagnostics import model_view_audit
from material_studio_mcp_server.natural_language import infer_modeling_plan
from material_studio_mcp_server.specs import SemanticPatch, apply_semantic_patch
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state.diff import summarize_spec_delta


def load_example(name: str) -> ModelSpec:
    path = Path("src/material_studio_mcp_server/examples") / name
    return ModelSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_molecule_patch_does_not_mutate_original() -> None:
    base = load_example("benzene_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[
            {"type": "delete_atom", "atom_id": "H1"},
            {"type": "add_atom", "id": "N1", "element": "N", "xyz_angstrom": [2.7, 0, 0]},
            {"type": "add_atom", "id": "O1", "element": "O", "xyz_angstrom": [3.2, 0.6, 0]},
            {"type": "add_atom", "id": "O2", "element": "O", "xyz_angstrom": [3.2, -0.6, 0]},
            {"type": "add_bond", "atom1": "C1", "atom2": "N1", "bond_type": "Single"},
            {"type": "add_bond", "atom1": "N1", "atom2": "O1", "bond_type": "Double"},
            {"type": "add_bond", "atom1": "N1", "atom2": "O2", "bond_type": "Partial double"},
        ],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert base.revision == 0
    assert new_spec.revision == 1
    assert "delete_atom H1" in diff
    assert any(atom.id == "N1" for atom in new_spec.model.atoms)
    assert all(atom.id != "N1" for atom in base.model.atoms)


def test_crystal_supercell_patch() -> None:
    base = load_example("graphene_vacancy_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[{"type": "make_supercell", "matrix": [2, 1, 1]}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert "make_supercell 2x1x1" in diff
    assert new_spec.model.lattice.a == base.model.lattice.a * 2
    assert len(new_spec.model.basis_atoms) == len(base.model.basis_atoms) * 2
    delta = summarize_spec_delta(base, new_spec, diff=diff)
    assert delta["atom_count_delta"] == len(base.model.basis_atoms)
    assert delta["element_count_delta"] == {"C": 3}
    assert delta["crystal"]["lattice_changed"] is True
    assert delta["crystal"]["lattice_delta"]["a"] == base.model.lattice.a
    assert len(delta["crystal"]["deleted_atoms"]) == 3
    assert len(delta["crystal"]["added_atoms"]) == 6
    assert any(atom["atom_id"].endswith("_100") for atom in delta["crystal"]["added_atoms"])


def test_crystal_atom_group_translation_wraps_periodically_without_mutating_base() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[
            {
                "type": "translate_crystal_atoms",
                "atom_ids": ["Si1", "Si7"],
                "axis": "x",
                "distance_angstrom": -0.5,
            }
        ],
    )

    translated, diff = apply_semantic_patch(base, patch)

    expected_x = 1.0 - 0.5 / base.model.lattice.a
    assert diff == ["translate_crystal_atoms 2 a -0.5A wrapped 1"]
    assert next(atom for atom in translated.model.basis_atoms if atom.id == "Si1").fractional.x == pytest.approx(expected_x)
    assert next(atom for atom in translated.model.basis_atoms if atom.id == "Si7").fractional.x == pytest.approx(0.5 - 0.5 / base.model.lattice.a)
    assert next(atom for atom in base.model.basis_atoms if atom.id == "Si1").fractional.x == 0.0
    assert base.revision == 0
    assert translated.revision == 1


def test_crystal_atom_group_translation_rejects_invalid_targets_and_unwrapped_escape() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")

    with pytest.raises(ValueError, match="unique identifiers"):
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "translate_crystal_atoms",
                    "atom_ids": ["Si1", "Si1"],
                    "axis": "a",
                    "distance_angstrom": 0.5,
                }
            ],
        )

    missing = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[
            {
                "type": "translate_crystal_atoms",
                "atom_ids": ["Missing1"],
                "axis": "a",
                "distance_angstrom": 0.5,
            }
        ],
    )
    with pytest.raises(ValueError, match="missing atom IDs: Missing1"):
        apply_semantic_patch(base, missing)

    no_wrap = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[
            {
                "type": "translate_crystal_atoms",
                "atom_ids": ["Si1"],
                "axis": "a",
                "distance_angstrom": -0.5,
                "wrap_fractional": False,
            }
        ],
    )
    with pytest.raises(ValueError, match="outside the unit cell"):
        apply_semantic_patch(base, no_wrap)


def test_crystal_atom_group_rotation_is_rigid_and_does_not_mutate_base() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")
    base_positions = {
        atom.id: atom.fractional.as_tuple()
        for atom in base.model.basis_atoms
        if atom.id in {"Si3", "Si5"}
    }
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[
            {
                "type": "rotate_crystal_atoms",
                "atom_ids": ["Si3", "Si5"],
                "axis": "c",
                "angle_degrees": 90.0,
                "pivot_fractional": [0.25, 0.25, 0.244891],
            }
        ],
    )

    rotated, diff = apply_semantic_patch(base, patch)

    rotated_positions = {
        atom.id: atom.fractional.as_tuple()
        for atom in rotated.model.basis_atoms
        if atom.id in {"Si3", "Si5"}
    }
    assert diff == ["rotate_crystal_atoms 2 c 90deg pivot 0.25,0.25,0.244891 wrapped 0"]
    assert rotated_positions["Si3"] == pytest.approx((0.0, 0.0, 0.244891))
    assert rotated_positions["Si5"] == pytest.approx((0.5, 0.5, 0.244891))
    assert base_positions == {
        "Si3": (0.0, 0.5, 0.244891),
        "Si5": (0.5, 0.0, 0.244891),
    }
    before_distance = math.hypot(0.5 * base.model.lattice.a, 0.5 * base.model.lattice.b)
    after_distance = math.hypot(
        (rotated_positions["Si5"][0] - rotated_positions["Si3"][0]) * base.model.lattice.a,
        (rotated_positions["Si5"][1] - rotated_positions["Si3"][1]) * base.model.lattice.b,
    )
    assert after_distance == pytest.approx(before_distance)
    assert base.revision == 0
    assert rotated.revision == 1


def test_crystal_atom_group_rotation_rejects_invalid_targets_and_unwrapped_escape() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")

    with pytest.raises(ValueError, match="unique identifiers"):
        SemanticPatch(
            project_id=base.project_id,
            base_revision=base.revision,
            operations=[
                {
                    "type": "rotate_crystal_atoms",
                    "atom_ids": ["Si3", "Si3"],
                    "axis": "c",
                    "angle_degrees": 5.0,
                }
            ],
        )

    missing = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[
            {
                "type": "rotate_crystal_atoms",
                "atom_ids": ["Missing1"],
                "axis": "c",
                "angle_degrees": 5.0,
            }
        ],
    )
    with pytest.raises(ValueError, match="missing atom IDs: Missing1"):
        apply_semantic_patch(base, missing)

    no_wrap = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[
            {
                "type": "rotate_crystal_atoms",
                "atom_ids": ["Si1"],
                "axis": "c",
                "angle_degrees": 45.0,
                "pivot_fractional": [0.5, 0.5, 0.0],
                "wrap_fractional": False,
            }
        ],
    )
    with pytest.raises(ValueError, match="outside the unit cell"):
        apply_semantic_patch(base, no_wrap)


def test_semiconductor_layer_translation_infers_explicit_atom_ids_in_english_and_chinese() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")

    english = infer_modeling_plan(
        "Shift layer 3 by 0.5 angstrom along x and hot-load it in Materials Studio.",
        current_spec=base,
    )
    assert english.kind == "patch"
    assert english.template_id == "crystal_layer_translation"
    assert english.payload["operations"][0] == {
        "type": "translate_crystal_atoms",
        "atom_ids": ["Si3", "Si5"],
        "axis": "a",
        "distance_angstrom": 0.5,
        "wrap_fractional": True,
    }
    english_record = english.payload["operations"][1]["metadata_updates"]["last_crystal_layer_translation"]
    assert english_record["layer_index"] == 3
    assert english_record["layer_count"] == 8
    assert english_record["profile_axis"] == "c"
    assert english_record["in_plane_translation"] is True

    chinese = infer_modeling_plan(
        "将顶层沿 y 方向平移 -0.25 埃并热加载到 Materials Studio。",
        current_spec=base,
    )
    assert chinese.kind == "patch"
    assert chinese.template_id == "crystal_layer_translation"
    chinese_record = chinese.payload["operations"][1]["metadata_updates"]["last_crystal_layer_translation"]
    assert chinese_record["layer_index"] == 8
    assert chinese_record["translation_axis"] == "b"
    assert chinese_record["distance_angstrom"] == -0.25


def test_semiconductor_layer_translation_rejects_profile_axis_and_bad_layer_index() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")

    normal_axis = infer_modeling_plan(
        "Shift layer 3 by 0.5 angstrom along z.",
        current_spec=base,
    )
    assert normal_axis.kind == "unsupported"
    assert normal_axis.template_id == "crystal_layer_translation"
    assert "profile axis c" in normal_axis.notes[1]

    bad_index = infer_modeling_plan(
        "Shift layer 99 by 0.5 angstrom along x.",
        current_spec=base,
    )
    assert bad_index.kind == "unsupported"
    assert "available range 1..8" in bad_index.notes[1]


def test_new_semiconductor_template_applies_inline_layer_translation() -> None:
    plan = infer_modeling_plan(
        "Build a Si/Ge heterostructure and shift layer 3 by 0.5 angstrom along x, then prepare preview."
    )

    assert plan.kind == "spec"
    assert plan.template_id == "silicon_germanium_001_heterostructure"
    spec = ModelSpec.model_validate(plan.payload)
    record = spec.metadata["last_crystal_layer_translation"]
    assert record["layer_index"] == 3
    assert record["atom_ids"] == ["Si3", "Si5"]
    assert record["translation_axis"] == "a"
    assert record["distance_angstrom"] == 0.5
    assert next(atom for atom in spec.model.basis_atoms if atom.id == "Si3").fractional.x == pytest.approx(
        0.5 / spec.model.lattice.a
    )
    assert any("translate_crystal_atoms 2 a 0.5A wrapped 0" in note for note in plan.notes)


def test_semiconductor_layer_rotation_infers_explicit_atom_ids_in_english_and_chinese() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")

    english = infer_modeling_plan(
        "Twist layer 3 by 5 degrees and hot-load it in Materials Studio.",
        current_spec=base,
    )
    assert english.kind == "patch"
    assert english.template_id == "crystal_layer_rotation"
    assert english.payload["operations"][0] == {
        "type": "rotate_crystal_atoms",
        "atom_ids": ["Si3", "Si5"],
        "axis": "c",
        "angle_degrees": 5.0,
        "pivot_fractional": [0.25, 0.25, 0.244891],
        "wrap_fractional": True,
    }
    english_record = english.payload["operations"][1]["metadata_updates"]["last_crystal_layer_rotation"]
    assert english_record["layer_index"] == 3
    assert english_record["layer_count"] == 8
    assert english_record["rotation_axis"] == "c"
    assert english_record["rotation_axis_source"] == "profile_axis_default"
    assert english_record["commensurability_verified"] is False
    assert english_record["calculation_ready"] is False
    assert len(english_record["post_rotation_atom_coordinate_sha256"]) == 64

    chinese = infer_modeling_plan(
        "\u5c06\u9876\u5c42\u7ed5 c \u8f74\u65cb\u8f6c -3 \u5ea6\u5e76\u70ed\u52a0\u8f7d\u5230 Materials Studio\u3002",
        current_spec=base,
    )
    assert chinese.kind == "patch"
    assert chinese.template_id == "crystal_layer_rotation"
    chinese_record = chinese.payload["operations"][1]["metadata_updates"]["last_crystal_layer_rotation"]
    assert chinese_record["layer_index"] == 8
    assert chinese_record["rotation_axis"] == "c"
    assert chinese_record["rotation_axis_source"] == "explicit"
    assert chinese_record["angle_degrees"] == -3.0


def test_semiconductor_layer_rotation_rejects_tilt_axis_and_bad_layer_index() -> None:
    base = load_example("silicon_germanium_001_heterostructure_spec.json")

    tilt_axis = infer_modeling_plan(
        "Rotate layer 3 by 5 degrees around x axis.",
        current_spec=base,
    )
    assert tilt_axis.kind == "unsupported"
    assert tilt_axis.template_id == "crystal_layer_rotation"
    assert "profile axis c" in tilt_axis.notes[1]
    assert "tilt the layer" in tilt_axis.notes[1]

    bad_index = infer_modeling_plan(
        "Twist layer 99 by 5 degrees.",
        current_spec=base,
    )
    assert bad_index.kind == "unsupported"
    assert "available range 1..8" in bad_index.notes[1]


def test_new_semiconductor_template_applies_inline_layer_rotation_scaffold() -> None:
    plan = infer_modeling_plan(
        "Build a Si/Ge heterostructure and twist layer 3 by 5 degrees, then prepare preview."
    )

    assert plan.kind == "spec"
    assert plan.template_id == "silicon_germanium_001_heterostructure"
    spec = ModelSpec.model_validate(plan.payload)
    record = spec.metadata["last_crystal_layer_rotation"]
    assert record["layer_index"] == 3
    assert record["atom_ids"] == ["Si3", "Si5"]
    assert record["rotation_axis"] == "c"
    assert record["angle_degrees"] == 5.0
    assert record["visual_review_only"] is True
    assert record["requires_commensurate_supercell"] is True
    assert next(atom for atom in spec.model.basis_atoms if atom.id == "Si3").fractional.x > 0.0
    assert any("rotate_crystal_atoms 2 c 5deg" in note for note in plan.notes)


def test_crystal_restore_dopant_reconciles_current_state_metadata_without_mutating_base() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    base = ModelSpec.model_validate(plan.payload)
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[{"type": "substitute_atom", "atom_id": "Si1_000", "new_element": "Si"}],
    )

    restored, diff = apply_semantic_patch(base, patch)

    assert next(atom for atom in base.model.basis_atoms if atom.id == "Si1_000").element == "P"
    assert base.metadata["semiconductor_dopant_sites"][0]["dopant_element"] == "P"
    assert next(atom for atom in restored.model.basis_atoms if atom.id == "Si1_000").element == "Si"
    assert "semiconductor_dopant_sites" not in restored.metadata
    assert "last_semiconductor_dopant_site" not in restored.metadata
    assert diff == [
        "substitute_atom Si1_000->Si",
        "reconcile_metadata semiconductor_dopant_sites raw=1 current=0 removed=1 expanded=0",
    ]


def test_crystal_supercell_expands_concrete_dopant_site_metadata() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    base = ModelSpec.model_validate(plan.payload)
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[{"type": "make_supercell", "matrix": [2, 1, 1]}],
    )

    expanded, diff = apply_semantic_patch(base, patch)

    dopant_atoms = [atom for atom in expanded.model.basis_atoms if atom.element == "P"]
    dopant_records = expanded.metadata["semiconductor_dopant_sites"]
    assert [atom.id for atom in dopant_atoms] == ["Si1_000_000", "Si1_000_100"]
    assert [record["atom_id"] for record in dopant_records] == ["Si1_000_000", "Si1_000_100"]
    assert expanded.metadata["last_semiconductor_dopant_site"]["atom_id"] == "Si1_000_100"
    assert base.metadata["semiconductor_dopant_sites"][0]["atom_id"] == "Si1_000"
    assert diff[-1] == "reconcile_metadata semiconductor_dopant_sites raw=1 current=2 removed=0 expanded=1"


def test_explicit_dopant_metadata_reconcile_creates_metadata_only_revision() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    doped = ModelSpec.model_validate(plan.payload)
    stale_atoms = [
        atom.model_copy(update={"element": "Si"}) if atom.id == "Si1_000" else atom
        for atom in doped.model.basis_atoms
    ]
    stale = doped.model_copy(update={"model": doped.model.model_copy(update={"basis_atoms": stale_atoms})})
    patch = SemanticPatch(
        project_id=stale.project_id,
        base_revision=stale.revision,
        operations=[{"type": "reconcile_dopant_metadata"}],
    )

    reconciled, diff = apply_semantic_patch(stale, patch)

    assert reconciled.revision == stale.revision + 1
    assert reconciled.model == stale.model
    assert reconciled.simulation == stale.simulation
    assert "semiconductor_dopant_sites" not in reconciled.metadata
    assert "last_semiconductor_dopant_site" not in reconciled.metadata
    assert stale.metadata["semiconductor_dopant_sites"][0]["dopant_element"] == "P"
    assert diff == [
        "reconcile_metadata semiconductor_dopant_sites raw=1 current=0 removed=1 expanded=0"
    ]


def test_reconcile_dopant_metadata_natural_language_catches_cleanup_phrasing() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    base = ModelSpec.model_validate(plan.payload)

    follow_up = infer_modeling_plan(
        "Clean up the stale dopant metadata and re-audit the model.",
        current_spec=base,
    )

    assert follow_up.kind == "patch"
    assert follow_up.template_id == "reconcile_dopant_metadata"
    assert follow_up.payload == {"operations": [{"type": "reconcile_dopant_metadata"}]}


def test_reconcile_dopant_metadata_natural_language_catches_reaudit_phrasing() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    base = ModelSpec.model_validate(plan.payload)

    follow_up = infer_modeling_plan(
        "Re-audit the current dopant metadata and check whether the model is normal.",
        current_spec=base,
    )

    assert follow_up.kind == "patch"
    assert follow_up.template_id == "reconcile_dopant_metadata"
    assert follow_up.payload == {"operations": [{"type": "reconcile_dopant_metadata"}]}


def test_reconcile_dopant_metadata_natural_language_catches_current_model_fix_phrasing() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    base = ModelSpec.model_validate(plan.payload)

    follow_up = infer_modeling_plan(
        "Fix the current model and re-audit the current structure.",
        current_spec=base,
    )

    assert follow_up.kind == "patch"
    assert follow_up.template_id == "reconcile_dopant_metadata"
    assert follow_up.payload == {"operations": [{"type": "reconcile_dopant_metadata"}]}


def test_crystal_dopant_reconcile_prunes_stale_auto_selected_metadata() -> None:
    plan = infer_modeling_plan(
        "Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then prepare preview."
    )
    doped = ModelSpec.model_validate(plan.payload)
    stale_atoms = [
        atom.model_copy(update={"element": "Si"}) if atom.id == "Si1_000" else atom
        for atom in doped.model.basis_atoms
    ]
    stale_metadata = dict(doped.metadata)
    stale_metadata["nl_auto_selected_sites"] = [
        {
            "operation": "dopant",
            "atom_id": "Si1_000",
            "site_element": "Si",
            "auto_selected_site": True,
            "source": "natural_language_auto_site",
            "new_element": "P",
        }
    ]
    stale = doped.model_copy(
        update={
            "model": doped.model.model_copy(update={"basis_atoms": stale_atoms}),
            "metadata": stale_metadata,
        }
    )
    patch = SemanticPatch(
        project_id=stale.project_id,
        base_revision=stale.revision,
        operations=[{"type": "reconcile_dopant_metadata"}],
    )

    reconciled, diff = apply_semantic_patch(stale, patch)

    assert reconciled.revision == stale.revision + 1
    assert reconciled.model == stale.model
    assert reconciled.simulation == stale.simulation
    assert "semiconductor_dopant_sites" not in reconciled.metadata
    assert "last_semiconductor_dopant_site" not in reconciled.metadata
    assert "nl_auto_selected_sites" not in reconciled.metadata
    assert diff == [
        "reconcile_metadata semiconductor_dopant_sites raw=1 current=0 removed=1 expanded=0"
    ]


def test_explicit_dopant_metadata_reconcile_rejects_already_consistent_or_noncrystal_model() -> None:
    crystal = load_example("silicon_diamond_spec.json")
    crystal_patch = SemanticPatch(
        project_id=crystal.project_id,
        base_revision=crystal.revision,
        operations=[{"type": "reconcile_dopant_metadata"}],
    )
    with pytest.raises(ValueError, match="没有需要调和"):
        apply_semantic_patch(crystal, crystal_patch)

    molecule = load_example("benzene_spec.json")
    molecule_patch = SemanticPatch(
        project_id=molecule.project_id,
        base_revision=molecule.revision,
        operations=[{"type": "reconcile_dopant_metadata"}],
    )
    with pytest.raises(ValueError, match="对分子模型无效"):
        apply_semantic_patch(molecule, molecule_patch)


def test_crystal_patch_rejects_missing_atom_id() -> None:
    base = load_example("silicon_diamond_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[{"type": "substitute_atom", "atom_id": "Missing1", "new_element": "P"}],
    )

    with pytest.raises(ValueError, match="Missing1"):
        apply_semantic_patch(base, patch)


def test_crystal_add_and_set_fractional_patch() -> None:
    base = load_example("silicon_100_slab_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[
            {"type": "add_atom", "id": "Htop1", "element": "H", "fractional": [0.5, 0.5, 0.24]},
            {"type": "set_atom_position", "atom_id": "Si1", "fractional": [0.0, 0.0, 0.02]},
        ],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert "add_atom Htop1" in diff
    assert "set_atom_position Si1" in diff
    assert new_spec.revision == 1
    assert len(new_spec.model.basis_atoms) == len(base.model.basis_atoms) + 1
    assert any(atom.id == "Htop1" and atom.element == "H" for atom in new_spec.model.basis_atoms)
    assert next(atom for atom in new_spec.model.basis_atoms if atom.id == "Si1").fractional.z == 0.02
    assert all(atom.id != "Htop1" for atom in base.model.basis_atoms)
    assert next(atom for atom in base.model.basis_atoms if atom.id == "Si1").fractional.z == 0.0


def test_crystal_add_vacuum_preserves_cartesian_axis_positions() -> None:
    base = load_example("silicon_diamond_spec.json")
    old_c = base.model.lattice.c
    old_atom = next(atom for atom in base.model.basis_atoms if atom.fractional.z > 0)
    old_z = old_atom.fractional.z * old_c
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[{"type": "add_vacuum", "axis": "z", "thickness_angstrom": 10.0}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["add_vacuum z 10.0"]
    assert new_spec.model.lattice.c == old_c + 10.0
    new_atom = next(atom for atom in new_spec.model.basis_atoms if atom.id == old_atom.id)
    assert abs(new_atom.fractional.z * new_spec.model.lattice.c - old_z) < 1e-9
    assert new_spec.metadata["surface_axis"] == "c"
    assert new_spec.metadata["vacuum_angstrom"] == 10.0
    assert new_spec.metadata["slab_thickness_angstrom"] == old_c
    delta = summarize_spec_delta(base, new_spec, diff=diff)
    crystal_delta = delta["crystal"]
    assert crystal_delta["lattice_delta"] == {"c": 10.0}
    assert crystal_delta["fractional_coordinate_update_count"] == 6
    assert crystal_delta["cartesian_moved_atom_count"] == 0
    assert crystal_delta["cartesian_preserved_atom_count"] == 6
    assert crystal_delta["max_cartesian_displacement_angstrom"] == 0.0
    assert crystal_delta["fractional_rescale_preserved_cartesian"] is True

    second_patch = SemanticPatch(
        project_id=new_spec.project_id,
        base_revision=1,
        operations=[{"type": "add_vacuum", "axis": "c", "thickness_angstrom": 2.0}],
    )
    second_spec, _ = apply_semantic_patch(new_spec, second_patch)
    second_atom = next(atom for atom in second_spec.model.basis_atoms if atom.id == old_atom.id)
    assert abs(second_atom.fractional.z * second_spec.model.lattice.c - old_z) < 1e-9
    assert second_spec.metadata["vacuum_angstrom"] == 12.0
    assert second_spec.metadata["slab_thickness_angstrom"] == old_c


def test_crystal_set_vacuum_resizes_slab_cell_and_preserves_cartesian_positions() -> None:
    base = load_example("silicon_100_slab_spec.json")
    old_c = base.model.lattice.c
    old_atom = next(atom for atom in base.model.basis_atoms if atom.fractional.z > 0)
    old_z = old_atom.fractional.z * old_c
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[{"type": "set_vacuum", "axis": "z", "thickness_angstrom": 12.0}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["set_vacuum z 12.0"]
    assert new_spec.model.lattice.c == 17.431
    new_atom = next(atom for atom in new_spec.model.basis_atoms if atom.id == old_atom.id)
    assert abs(new_atom.fractional.z * new_spec.model.lattice.c - old_z) < 1e-9
    assert new_spec.metadata["surface_axis"] == "c"
    assert new_spec.metadata["vacuum_angstrom"] == 12.0
    assert new_spec.metadata["slab_thickness_angstrom"] == 5.431
    assert base.metadata["vacuum_angstrom"] == 19.569
    delta = summarize_spec_delta(base, new_spec, diff=diff)
    crystal_delta = delta["crystal"]
    assert crystal_delta["lattice_delta"] == {"c": -7.569}
    assert crystal_delta["fractional_coordinate_update_count"] == 6
    assert crystal_delta["cartesian_moved_atom_count"] == 0
    assert crystal_delta["cartesian_preserved_atom_count"] == 6
    assert crystal_delta["max_cartesian_displacement_angstrom"] == 0.0
    assert crystal_delta["fractional_rescale_preserved_cartesian"] is True


def test_crystal_center_slab_balances_vacuum_without_resizing_cell() -> None:
    base = load_example("silicon_100_slab_spec.json")
    old_c = base.model.lattice.c
    old_values = [atom.fractional.z for atom in base.model.basis_atoms]
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[{"type": "center_slab", "axis": "z"}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["center_slab z"]
    assert new_spec.model.lattice.c == old_c
    new_values = [atom.fractional.z for atom in new_spec.model.basis_atoms]
    assert min(new_values) == pytest.approx(0.418535)
    assert max(new_values) == pytest.approx(0.581465)
    assert new_spec.metadata["surface_axis"] == "c"
    centering = new_spec.metadata["slab_centering"]
    assert centering["axis"] == "c"
    assert centering["old_fractional_min"] == 0.0
    assert centering["old_fractional_max"] == 0.16293
    assert centering["new_fractional_min"] == 0.418535
    assert centering["new_fractional_max"] == 0.581465
    assert centering["shift_fractional"] == 0.418535
    assert centering["bottom_vacuum_angstrom"] == 10.463375
    assert centering["top_vacuum_angstrom"] == 10.463375
    assert min(old_values) == 0.0
    assert base.metadata.get("slab_centering") is None
    delta = summarize_spec_delta(base, new_spec, diff=diff)
    crystal_delta = delta["crystal"]
    assert crystal_delta["lattice_delta"] == {}
    assert crystal_delta["fractional_coordinate_update_count"] == 8
    assert crystal_delta["cartesian_moved_atom_count"] == 8
    assert crystal_delta["fractional_rescale_preserved_cartesian"] is False


def test_interface_scaffold_gap_patch_moves_film_and_updates_metadata() -> None:
    plan = infer_modeling_plan("Build a GaN on sapphire interface scaffold and prepare preview.")
    base = ModelSpec.model_validate(plan.payload)
    old_c = base.model.lattice.c
    old_gap = base.metadata["interface_gap_angstrom"]
    bottom = base.metadata["bottom_vacuum_angstrom"]
    substrate_thickness = base.metadata["substrate_thickness_angstrom"]
    old_film_start = bottom + substrate_thickness + old_gap
    film_atom = next(atom for atom in base.model.basis_atoms if "F" in atom.id)
    substrate_atom = next(atom for atom in base.model.basis_atoms if "S" in atom.id)
    old_film_offset = film_atom.fractional.z * old_c - old_film_start
    old_substrate_z = substrate_atom.fractional.z * old_c

    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[{"type": "set_interface_gap", "axis": "z", "thickness_angstrom": 2.5}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["set_interface_gap c 2.5"]
    assert new_spec.revision == 1
    assert new_spec.model.lattice.c == pytest.approx(old_c - 0.5)
    assert base.metadata["interface_gap_angstrom"] == 3.0
    assert new_spec.metadata["interface_gap_angstrom"] == 2.5
    assert new_spec.metadata["slab_thickness_angstrom"] == pytest.approx(
        base.metadata["substrate_thickness_angstrom"] + 2.5 + base.metadata["film_thickness_angstrom"]
    )
    assert new_spec.metadata["last_interface_gap_adjustment"]["source"] == "semantic_patch_set_interface_gap"
    assert new_spec.metadata["last_interface_gap_adjustment"]["moved_film_atom_count"] == 36
    new_film_atom = next(atom for atom in new_spec.model.basis_atoms if atom.id == film_atom.id)
    new_substrate_atom = next(atom for atom in new_spec.model.basis_atoms if atom.id == substrate_atom.id)
    new_film_start = bottom + substrate_thickness + 2.5
    assert new_film_atom.fractional.z * new_spec.model.lattice.c == pytest.approx(
        new_film_start + old_film_offset
    )
    assert new_substrate_atom.fractional.z * new_spec.model.lattice.c == pytest.approx(old_substrate_z)

    scaffold = model_view_audit(new_spec)["health"]["semiconductor_health"]["interface_scaffold_summary"]
    assert scaffold["interface_gap_angstrom"] == 2.5
    assert scaffold["visual_hotload_ready"] is True
    assert scaffold["requires_geometry_relaxation"] is True


def test_interface_scaffold_gap_follow_up_text_infers_patch_before_show_current() -> None:
    plan = infer_modeling_plan("Build a GaN on sapphire interface scaffold and prepare preview.")
    base = ModelSpec.model_validate(plan.payload)

    follow_up = infer_modeling_plan(
        (
            "Set the semiconductor interface scaffold gap to 2.5 angstrom and hot-load it in Materials Studio, "
            "export front top isometric view parameters and interface scaffold diagnostics, "
            "and check whether the model is normal."
        ),
        current_spec=base,
    )

    assert follow_up.kind == "patch"
    assert follow_up.template_id == "interface_scaffold_gap"
    assert follow_up.payload["operations"] == [
        {"type": "set_interface_gap", "axis": "c", "thickness_angstrom": 2.5}
    ]


def test_gate_stack_thickness_patch_updates_oxide_geometry_and_metadata() -> None:
    base = load_example("titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[{"type": "set_gate_stack_thickness", "target_layer": "oxide", "thickness_angstrom": 6.72}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["set_gate_stack_thickness oxide HfO2 6.72A"]
    assert new_spec.revision == 1
    assert base.metadata["oxide_thickness_angstrom"] == 4.84
    assert new_spec.metadata["oxide_thickness_angstrom"] == 6.72
    edit = new_spec.metadata["last_gate_stack_thickness_edit"]
    assert edit["target_layer"] == "oxide"
    assert edit["target_material"] == "HfO2"
    assert edit["old_center_span_angstrom"] == 3.36
    assert edit["new_center_span_angstrom"] == 6.72
    assert edit["shifted_above_atom_count"] == 8

    assert next(atom for atom in base.model.basis_atoms if atom.id == "HfOx3").fractional.z == 0.55
    assert next(atom for atom in new_spec.model.basis_atoms if atom.id == "HfOx3").fractional.z == pytest.approx(0.67)
    assert next(atom for atom in base.model.basis_atoms if atom.id == "TiGate1").fractional.z == 0.64
    assert next(atom for atom in new_spec.model.basis_atoms if atom.id == "TiGate1").fractional.z == pytest.approx(0.76)

    gate_stack = model_view_audit(new_spec)["health"]["semiconductor_health"]["gate_stack_summary"]
    assert gate_stack["quality"] == "complete"
    assert gate_stack["material_sequence"] == ["Si", "HfO2", "TiN"]
    assert gate_stack["declared_oxide_thickness_angstrom"] == 6.72
    assert gate_stack["oxide_center_span_angstrom"] == 6.72
    assert gate_stack["sequence_matches_expected"] is True


def test_p_gan_gate_cap_thickness_patch_rebuilds_cap_without_mutating_base() -> None:
    plan = infer_modeling_plan("Build a p-GaN gate AlGaN/GaN HEMT and export 2DEG diagnostics.")
    base = ModelSpec.model_validate(plan.payload)
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[{"type": "set_p_gan_gate_cap_thickness", "thickness_angstrom": 20.0}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["set_p_gan_gate_cap_thickness 20A actual 19.134A layers 24 Mg:MgPGaN1_1"]
    assert base.revision == 0
    assert new_spec.revision == 1
    assert base.metadata["p_gan_gate_cap"]["layer_count"] == 4
    assert base.model.lattice.c == 13.559
    assert len(base.model.basis_atoms) == 48
    assert new_spec.model.lattice.c == 29.504
    assert len(new_spec.model.basis_atoms) == 128

    cap = new_spec.metadata["p_gan_gate_cap"]
    assert cap["source"] == "semantic_patch_set_p_gan_gate_cap_thickness"
    assert cap["requested_thickness_angstrom"] == 20.0
    assert cap["actual_thickness_angstrom"] == 19.134
    assert cap["thickness_error_angstrom"] == -0.866
    assert cap["layer_count"] == 24
    assert cap["layer_spacing_angstrom"] == 0.79725
    assert cap["dopant_atom_id"] == "MgPGaN1_1"
    assert cap["dopant_fraction_of_cap_cations"] == pytest.approx(1 / 48, abs=1e-6)
    assert len(cap["layers"]) == 24
    assert new_spec.metadata["last_p_gan_gate_cap_thickness_edit"]["new_layer_count"] == 24
    assert new_spec.metadata["semiconductor_dopant_sites"] == [
        {
            "site_id": "MgPGaN1_1",
            "atom_id": "MgPGaN1_1",
            "site_element": "Ga",
            "dopant_element": "Mg",
            "new_element": "Mg",
            "fractional": [0.166666, 0.333334, 0.351478],
            "auto_selected_site": True,
            "source": "semantic_patch_set_p_gan_gate_cap_thickness",
        }
    ]

    semiconductor = model_view_audit(new_spec)["health"]["semiconductor_health"]
    cap_summary = semiconductor["p_gan_gate_cap_summary"]
    assert cap_summary["quality"] == "complete"
    assert cap_summary["layer_count"] == 24
    assert cap_summary["matched_layer_count"] == 24
    assert cap_summary["dopant_site_found"] is True
    assert semiconductor["dopant_site_summary"]["latest"]["atom_id"] == "MgPGaN1_1"


def test_quantum_well_thickness_patch_updates_hemt_barrier_without_mutating_base() -> None:
    base = load_example("aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=base.revision,
        operations=[{"type": "set_quantum_well_thickness", "target_layer": "barrier", "thickness_angstrom": 150.0}],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert diff == ["set_quantum_well_thickness barrier Al0.25Ga0.75N 150A actual 148.978A layers 188"]
    assert base.revision == 0
    assert new_spec.revision == 1
    assert base.model.lattice.c == 10.37
    assert len(base.model.basis_atoms) == 32
    assert new_spec.model.lattice.c == 152.16725
    assert len(new_spec.model.basis_atoms) == 768

    layer_request = new_spec.metadata["quantum_well_layer_request"]
    assert layer_request["source"] == "semantic_patch_set_quantum_well_thickness"
    assert layer_request["well_material"] == "GaN"
    assert layer_request["barrier_material"] == "Al0.25Ga0.75N"
    assert layer_request["well_layer_count"] == 4
    assert layer_request["barrier_layer_count"] == 188
    assert layer_request["requested_barrier_thickness_angstrom"] == 150.0
    assert layer_request["actual_barrier_thickness_angstrom"] == 148.97825
    assert layer_request["barrier_thickness_error_angstrom"] == -1.02175

    semiconductor = model_view_audit(new_spec)["health"]["semiconductor_health"]
    quantum_well = semiconductor["quantum_well_summary"]
    assert quantum_well["requested_barrier_layer_count"] == 188
    assert quantum_well["requested_barrier_thickness_angstrom"] == 150.0
    assert quantum_well["barrier_materials"] == ["Al0.25Ga0.75N"]
    assert semiconductor["polarization_2deg_summary"]["quality"] == "complete"
    assert semiconductor["polarization_2deg_summary"]["barrier_materials"] == ["Al0.25Ga0.75N"]


def test_crystal_delta_reports_cartesian_displacement_from_lattice_strain() -> None:
    base = load_example("silicon_diamond_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[
            {
                "type": "set_lattice",
                "lattice": {
                    "a": base.model.lattice.a * 1.02,
                    "b": base.model.lattice.b,
                    "c": base.model.lattice.c,
                    "alpha": base.model.lattice.alpha,
                    "beta": base.model.lattice.beta,
                    "gamma": base.model.lattice.gamma,
                },
            }
        ],
    )

    new_spec, diff = apply_semantic_patch(base, patch)
    delta = summarize_spec_delta(base, new_spec, diff=diff)
    crystal_delta = delta["crystal"]

    assert crystal_delta["lattice_delta"] == {"a": 0.10862}
    assert crystal_delta["fractional_coordinate_update_count"] == 0
    assert crystal_delta["cartesian_moved_atom_count"] == 6
    assert crystal_delta["cartesian_preserved_atom_count"] == 0
    assert crystal_delta["max_cartesian_displacement_angstrom"] > 0.0
    assert crystal_delta["fractional_rescale_preserved_cartesian"] is False


def test_crystal_metadata_patch_updates_surface_termination() -> None:
    base = load_example("silicon_100_slab_spec.json")
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[
            {
                "type": "set_metadata",
                "metadata_updates": {
                    "termination": "hydrogen_passivated_top",
                    "passivation": {"element": "H", "surfaces": ["top"]},
                },
            }
        ],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert "set_metadata passivation,termination" in diff
    assert new_spec.metadata["termination"] == "hydrogen_passivated_top"
    assert new_spec.metadata["passivation"]["surfaces"] == ["top"]
    assert base.metadata["termination"] == "unpassivated"


def test_castep_patch_can_update_task_without_mutating_original() -> None:
    base = load_example("silicon_diamond_spec.json")
    assert base.simulation is not None
    patch = SemanticPatch(
        project_id=base.project_id,
        base_revision=0,
        operations=[
            {
                "type": "set_castep_energy",
                "task": "BandStructure",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 520,
                "kpoint_separation": 0.04,
            }
        ],
    )

    new_spec, diff = apply_semantic_patch(base, patch)

    assert "set_castep_energy" in diff
    assert new_spec.simulation is not None
    assert new_spec.simulation.task == "BandStructure"
    assert new_spec.simulation.cutoff_energy_ev == 520
    assert base.simulation.task == "Energy"
