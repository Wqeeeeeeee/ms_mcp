from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from material_studio_mcp_server.diagnostics import (
    MAX_PROJECTED_ATOMS,
    model_view_audit,
    write_view_audit_bundle,
)
from material_studio_mcp_server import server
from material_studio_mcp_server.specs.crystal import BasisAtomSpec, CrystalSpec, LatticeSpec
from material_studio_mcp_server.specs.project import ModelSpec, ModelType


EXAMPLES = Path(__file__).parents[1] / "src" / "material_studio_mcp_server" / "examples"


def _load_example(name: str) -> ModelSpec:
    return ModelSpec.model_validate_json((EXAMPLES / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_view_bundle_exports_deterministic_spec_projection_reference_atlas(
    tmp_path: Path,
) -> None:
    spec = _load_example("gallium_arsenide_zincblende_spec.json")
    views = ["crystal_plane_100", "crystal_plane_110", "crystal_plane_111"]
    audit = model_view_audit(spec, views)

    first = write_view_audit_bundle(tmp_path / "first", spec, audit)
    second = write_view_audit_bundle(tmp_path / "second", spec, audit)

    atlas_path = Path(first["files"]["view_reference_atlas_svg"])
    manifest_path = Path(first["files"]["view_reference_manifest_json"])
    index_path = Path(first["files"]["view_reference_index_csv"])
    assert atlas_path.exists()
    assert manifest_path.exists()
    assert index_path.exists()
    assert _sha256(atlas_path) == _sha256(
        Path(second["files"]["view_reference_atlas_svg"])
    )

    svg = atlas_path.read_text(encoding="utf-8")
    assert svg.startswith("<?xml")
    assert "spec projection reference" in svg
    assert "not GUI evidence" in svg
    assert all(view_name in svg for view_name in views)
    assert svg.count('data-atom-id=') == len(spec.model.basis_atoms) * len(views)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "ms_mcp_view_reference_v1"
    assert manifest["artifact_role"] == "deterministic_spec_projection_reference"
    assert manifest["project_id"] == spec.project_id
    assert manifest["revision"] == spec.revision
    assert manifest["spec_fingerprint"] == audit["spec_fingerprint"]
    assert manifest["atlas_sha256"] == _sha256(atlas_path)
    assert manifest["view_count"] == len(views)
    assert manifest["rendered_view_count"] == len(views)
    assert manifest["unsupported_view_count"] == 0
    assert manifest["all_supported_projections_complete"] is True
    assert manifest["gui_evidence_policy"] == {
        "counts_as_gui_screenshot": False,
        "counts_as_visual_confirmation": False,
        "requires_fresh_materials_studio_screenshot_for_acceptance": True,
    }
    assert [item["view"] for item in manifest["views"]] == views
    assert all(item["projection_complete"] is True for item in manifest["views"])
    assert all(len(item["camera_direction"]) == 3 for item in manifest["views"])

    rows = list(csv.DictReader(index_path.open(encoding="utf-8", newline="")))
    assert [row["view"] for row in rows] == views
    assert all(row["supported"] == "True" for row in rows)
    assert all(row["projection_complete"] == "True" for row in rows)
    assert all(row["counts_as_visual_confirmation"] == "False" for row in rows)
    assert first["row_counts"]["view_reference_views"] == len(views)


def test_view_reference_atlas_escapes_labels_and_marks_unsupported_views(
    tmp_path: Path,
) -> None:
    spec = _load_example("benzene_spec.json")
    audit = model_view_audit(spec, ["front", "unknown<&view"])

    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    atlas_path = Path(bundle["files"]["view_reference_atlas_svg"])
    manifest = json.loads(
        Path(bundle["files"]["view_reference_manifest_json"]).read_text(
            encoding="utf-8"
        )
    )

    svg = atlas_path.read_text(encoding="utf-8")
    assert "unknown&lt;&amp;view" in svg
    assert "unknown<&view" not in svg
    assert manifest["view_count"] == 2
    assert manifest["rendered_view_count"] == 2
    assert manifest["supported_view_count"] == 1
    assert manifest["unsupported_view_count"] == 1
    assert manifest["all_supported_projections_complete"] is True
    unsupported = manifest["views"][1]
    assert unsupported["view"] == "unknown<&view"
    assert unsupported["supported"] is False
    assert unsupported["projection_complete"] is False
    assert unsupported["warning"] == "Unknown view name."


def test_view_reference_manifest_marks_truncated_atom_projection(
    tmp_path: Path,
) -> None:
    atoms = [
        BasisAtomSpec(
            id=f"Si{index + 1}",
            element="Si",
            fractional=((index % 25) / 25, ((index // 25) % 25) / 25, index / (MAX_PROJECTED_ATOMS + 1)),
        )
        for index in range(MAX_PROJECTED_ATOMS + 1)
    ]
    spec = ModelSpec(
        project_id="projection_truncation",
        model_type=ModelType.CRYSTAL,
        model=CrystalSpec(
            name="projection_truncation",
            lattice=LatticeSpec(a=10, b=10, c=10, alpha=90, beta=90, gamma=90),
            basis_atoms=atoms,
        ),
    )
    audit = model_view_audit(spec, ["isometric"])

    bundle = write_view_audit_bundle(tmp_path, spec, audit)
    manifest = json.loads(
        Path(bundle["files"]["view_reference_manifest_json"]).read_text(
            encoding="utf-8"
        )
    )
    view = manifest["views"][0]

    assert view["atom_projection_count"] == MAX_PROJECTED_ATOMS + 1
    assert view["rendered_atom_count"] == MAX_PROJECTED_ATOMS
    assert view["projection_complete"] is False
    assert view["projection_truncated"] is True
    assert manifest["all_supported_projections_complete"] is False
    assert manifest["reference_limitations"] == [
        "one_or_more_supported_views_use_truncated_atom_projection_data"
    ]
    svg = Path(bundle["files"]["view_reference_atlas_svg"]).read_text(
        encoding="utf-8"
    )
    assert svg.count('data-atom-id=') == MAX_PROJECTED_ATOMS


def test_view_reference_artifacts_are_exposed_by_full_and_compact_mcp_responses(
    tmp_path: Path,
) -> None:
    spec = _load_example("benzene_spec.json").model_dump(mode="json")
    full = server.material_studio_model_export_view_bundle(
        spec=spec,
        include_gui_snapshot=False,
        working_dir=str(tmp_path / "full"),
        response_mode="full",
    )

    assert full["ok"] is True
    keys = {
        "view_reference_atlas_svg",
        "view_reference_manifest_json",
        "view_reference_index_csv",
    }
    assert keys <= set(full["view_bundle_files"])
    assert keys <= set(full["modeling_report"]["diagnostics"])
    assert keys <= set(full["modeling_report"]["change_receipt"]["artifacts"])
    for key in keys:
        assert Path(full["view_bundle_files"][key]).is_file()
        assert full[key] == full["view_bundle_files"][key]
        assert full[f"mcp_{key}"] == full["view_bundle_files"][key]
        assert full["live_summary"][f"mcp_{key}"] == full["view_bundle_files"][key]
    assert full["live_summary"]["mcp_view_reference_row_count"] == 7
    assert full["diagnostic_export_manifest"]["categories"]["view_parameters"][
        "files"
    ]["view_reference_atlas_svg"] == full["view_bundle_files"][
        "view_reference_atlas_svg"
    ]

    compact = server.material_studio_model_export_view_bundle(
        spec=spec,
        include_gui_snapshot=False,
        working_dir=str(tmp_path / "compact"),
        response_mode="compact",
    )
    assert compact["ok"] is True
    compact_keys = {
        "view_reference_atlas_svg",
        "view_reference_manifest_json",
    }
    assert compact_keys <= set(compact["artifacts"])
    for key in compact_keys:
        assert Path(compact["artifacts"][key]).is_file()
    assert compact["view_bundle_row_counts"]["view_reference_views"] == 7
