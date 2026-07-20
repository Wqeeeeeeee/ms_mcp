from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.canonicalization import (
    AtomDisplacement,
    AtomMapping,
    AtomSite,
    ArtifactBindingError,
    CanonicalContractModel,
    CanonicalReferenceArtifact,
    CanonicalizationSettings,
    CoordinateFreeStructureProjection,
    LatticeMetrics,
    MinimumImageResult,
    PeriodicStructure,
    SpeciesCount,
    StructureComparison,
)

from .conftest import revalidate


@pytest.mark.parametrize(
    ("operation_name", "input_name", "limit_name"),
    (
        ("build", "raw_bytes", "MAX_CIF_BYTES"),
        ("build", "source_record_bytes", "MAX_REFERENCE_RECORD_BYTES"),
        ("build", "manifest_bytes", "MAX_REFERENCE_RECORD_BYTES"),
        ("verify", "artifact_bytes", "MAX_CANONICAL_ARTIFACT_BYTES"),
        ("verify", "raw_bytes", "MAX_CIF_BYTES"),
        ("verify", "source_record_bytes", "MAX_REFERENCE_RECORD_BYTES"),
        ("verify", "manifest_bytes", "MAX_REFERENCE_RECORD_BYTES"),
    ),
)
def test_artifact_byte_limits_are_enforced_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
    operation_name: str,
    input_name: str,
    limit_name: str,
) -> None:
    import material_studio_mcp_server.canonicalization.artifact as implementation

    def unexpected_hash(*args: object, **kwargs: object) -> object:
        raise AssertionError("hashing must not run for oversized artifact input")

    monkeypatch.setattr(implementation.hashlib, "sha256", unexpected_hash)
    kwargs = {
        "raw_bytes": b"x",
        "source_record_bytes": b"x",
        "manifest_bytes": b"x",
    }
    if operation_name == "verify":
        kwargs["artifact_bytes"] = b"x"
        operation = implementation.verify_canonical_reference_artifact
    else:
        operation = implementation.build_canonical_reference_artifact
    limit = getattr(implementation, limit_name)
    kwargs[input_name] = b"x" * (limit + 1)
    with pytest.raises(ArtifactBindingError):
        operation(**kwargs)


def test_contract_base_is_strict_frozen_closed_and_permits_repeated_components() -> None:
    assert CanonicalContractModel.model_config["strict"] is True
    assert CanonicalContractModel.model_config["frozen"] is True
    assert CanonicalContractModel.model_config["extra"] == "forbid"
    assert CanonicalContractModel.model_config["allow_inf_nan"] is False
    structure = PeriodicStructure(
        lattice=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0)),
        sites=(
            AtomSite(
                species="Si",
                fractional_coordinates=(0.0, 0.0, 0.0),
                occupancy=1.0,
                label=None,
            ),
        ),
    )
    assert structure.sites[0].fractional_coordinates == (0.0, 0.0, 0.0)
    with pytest.raises(ValidationError):
        structure.lattice = structure.lattice  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PeriodicStructure.model_validate(
            {
                **structure.model_dump(),
                "extra_field": True,
            }
        )
    with pytest.raises(ValidationError):
        AtomSite(
            species="Si",
            fractional_coordinates=[0.0, 0.0, 0.0],  # type: ignore[arg-type]
            occupancy=1.0,
            label=None,
        )


def test_evidence_booleans_reject_integer_and_string_coercion(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    with pytest.raises(ValidationError):
        CanonicalizationSettings(no_idealize=1)  # type: ignore[arg-type]
    payload = canonical_artifact.coordinate_free_summary.model_dump(mode="json")
    payload["contains_coordinates"] = 0
    with pytest.raises(ValidationError):
        CoordinateFreeStructureProjection.model_validate(payload)
    artifact_payload = canonical_artifact.model_dump(mode="json")
    artifact_payload["candidate_template"] = "false"
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(artifact_payload)


@pytest.mark.parametrize(
    "field_name",
    (
        "settings_sha256",
        "canonical_structure_sha256",
    ),
)
def test_artifact_rejects_top_level_hash_mismatch(
    canonical_artifact: CanonicalReferenceArtifact,
    field_name: str,
) -> None:
    payload = canonical_artifact.model_dump(mode="json")
    payload[field_name] = "0" * 64
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)


def test_artifact_rejects_nested_settings_and_summary_identity_mismatch(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    payload = canonical_artifact.model_dump(mode="json")
    payload["canonical_structure"]["settings_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)

    for field_name, replacement in (
        ("canonical_structure_sha256", "0" * 64),
        ("settings_sha256", "0" * 64),
        ("atom_count", canonical_artifact.coordinate_free_summary.atom_count + 1),
        ("mode", "primitive"),
    ):
        payload = canonical_artifact.model_dump(mode="json")
        payload["coordinate_free_summary"][field_name] = replacement
        with pytest.raises(ValidationError):
            CanonicalReferenceArtifact.model_validate(payload)

    payload = canonical_artifact.model_dump(mode="json")
    payload["coordinate_free_summary"]["composition"] = [
        {"species": "C", "count": canonical_artifact.coordinate_free_summary.atom_count}
    ]
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)

    payload = canonical_artifact.model_dump(mode="json")
    payload["coordinate_free_summary"]["symmetry"]["international_number"] = 1
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "replacement",
    (
        "/absolute/object.bin",
        "C:/object.bin",
        "raw\\object.bin",
        "raw/../object.bin",
        "raw/./object.bin",
        "raw//object.bin",
        "raw/object.bin\x00",
    ),
)
def test_reference_binding_rejects_unsafe_or_noncanonical_paths(
    canonical_artifact: CanonicalReferenceArtifact,
    replacement: str,
) -> None:
    payload = canonical_artifact.model_dump(mode="json")
    payload["source"]["raw_artifact_relative_path"] = replacement
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)


def test_reference_binding_rejects_content_address_mismatch(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    payload = canonical_artifact.model_dump(mode="json")
    payload["source"]["raw_artifact_relative_path"] = (
        "raw/sha256/00/" + "0" * 64 + ".bin"
    )
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)


@pytest.mark.parametrize(
    "replacement",
    (
        "raw/sha256/00/hidden_holdout.json",
        "raw/sha256/00/validation.json",
        "raw/sha256/00/object:stream",
        "raw/sha256/00/CON.json",
    ),
)
def test_reference_binding_rejects_forbidden_path_semantics(
    canonical_artifact: CanonicalReferenceArtifact,
    replacement: str,
) -> None:
    payload = canonical_artifact.model_dump(mode="json")
    payload["source"]["raw_artifact_relative_path"] = replacement
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)


def test_artifact_rejects_structure_mode_different_from_settings(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    from material_studio_mcp_server.canonicalization import canonical_sha256

    payload = canonical_artifact.model_dump(mode="json")
    payload["settings"]["mode"] = "primitive"
    settings = CanonicalizationSettings.model_validate(payload["settings"])
    digest = canonical_sha256(settings)
    payload["settings_sha256"] = digest
    payload["canonical_structure"]["settings_sha256"] = digest
    payload["coordinate_free_summary"]["settings_sha256"] = digest
    with pytest.raises(ValidationError):
        CanonicalReferenceArtifact.model_validate(payload)


def test_atom_mapping_rejects_empty_duplicate_out_of_range_and_bad_coverage(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    site = canonical_artifact.canonical_structure.sites[0]
    displacement = AtomDisplacement(
        reference_index=0,
        candidate_index=0,
        species=site.species,
        fractional_displacement=(0.0, 0.0, 0.0),
        cartesian_displacement_angstrom=(0.0, 0.0, 0.0),
        distance_angstrom=0.0,
    )
    base = {
        "global_origin_shift_fractional": (0.0, 0.0, 0.0),
        "reference_atom_count": 1,
        "candidate_atom_count": 1,
        "displacements": (displacement,),
        "coverage": 1.0,
        "mapping_degenerate": False,
        "equivalent_mapping_count": 1,
        "semantic_identity_preserved": True,
    }
    AtomMapping(**base)
    for origin_shift in ((-1.0e-15, 0.0, 0.0), (1.0, 0.0, 0.0)):
        with pytest.raises(ValidationError):
            AtomMapping(**{**base, "global_origin_shift_fractional": origin_shift})
    with pytest.raises(ValidationError):
        AtomMapping(**{**base, "displacements": ()})
    duplicate = displacement.model_copy(update={"candidate_index": 0})
    with pytest.raises(ValidationError):
        AtomMapping(
            **{
                **base,
                "reference_atom_count": 2,
                "candidate_atom_count": 2,
                "displacements": (displacement, duplicate),
            }
        )
    with pytest.raises(ValidationError):
        AtomMapping(**{**base, "candidate_atom_count": 2})
    with pytest.raises(ValidationError):
        AtomMapping(**{**base, "coverage": 0.5})


def test_comparison_rejects_count_metric_and_species_mismatch(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    from material_studio_mcp_server.canonicalization import compare_structures

    comparison = compare_structures(
        canonical_artifact.canonical_structure,
        canonical_artifact.canonical_structure,
    )
    mapping_payload = comparison.mapping.model_dump(mode="json")
    mapping_payload["displacements"] = list(
        reversed(mapping_payload["displacements"])
    )
    with pytest.raises(ValidationError):
        AtomMapping.model_validate(mapping_payload)

    payload = comparison.model_dump(mode="json")
    payload["composition"][0]["count"] += 1
    with pytest.raises(ValidationError):
        StructureComparison.model_validate(payload)

    for field_name in ("rms_displacement_angstrom", "maximum_displacement_angstrom"):
        payload = comparison.model_dump(mode="json")
        payload[field_name] = 1.0
        with pytest.raises(ValidationError):
            StructureComparison.model_validate(payload)

    payload = comparison.model_dump(mode="json")
    payload["mapping"]["displacements"][0]["species"] = "H"
    with pytest.raises(ValidationError):
        StructureComparison.model_validate(payload)


def test_model_copy_is_not_used_as_contract_revalidation(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    with pytest.raises(ValidationError):
        revalidate(canonical_artifact, settings_sha256="0" * 64)


def test_minimum_image_contract_rejects_distance_inconsistency_and_norm_overflow() -> None:
    with pytest.raises(ValidationError):
        MinimumImageResult(
            fractional_displacement=(0.0, 0.0, 0.0),
            cartesian_displacement_angstrom=(1.0, 0.0, 0.0),
            distance_angstrom=2.0,
            lattice_translation=(0, 0, 0),
            candidates_examined=1,
            distance_degenerate=False,
        )
    with pytest.raises(ValidationError):
        MinimumImageResult(
            fractional_displacement=(0.0, 0.0, 0.0),
            cartesian_displacement_angstrom=(0.0, 0.0, 0.0),
            distance_angstrom=5.0e-13,
            lattice_translation=(0, 0, 0),
            candidates_examined=1,
            distance_degenerate=False,
        )
    with pytest.raises(ValidationError):
        AtomDisplacement(
            reference_index=0,
            candidate_index=0,
            species="C",
            fractional_displacement=(0.0, 0.0, 0.0),
            cartesian_displacement_angstrom=(1.7e308, 1.7e308, 0.0),
            distance_angstrom=1.7e308,
        )


def test_lattice_metric_contract_rederives_relative_and_angle_values() -> None:
    metrics = LatticeMetrics(
        reference_lengths_angstrom=(1.0, 2.0, 3.0),
        candidate_lengths_angstrom=(1.1, 2.0, 3.0),
        reference_angles_degrees=(90.0, 90.0, 90.0),
        candidate_angles_degrees=(91.0, 90.0, 90.0),
        relative_length_errors=(0.1, 0.0, 0.0),
        angle_differences_degrees=(1.0, 0.0, 0.0),
        maximum_relative_lattice_error=0.1,
        deformation_gradient=((1.1, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        symmetric_strain=((0.1, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        determinant_ratio=1.1,
    )
    payload = metrics.model_dump(mode="json")
    payload["relative_length_errors"][0] = 0.2
    with pytest.raises(ValidationError):
        LatticeMetrics.model_validate(payload)
    payload = metrics.model_dump(mode="json")
    payload["angle_differences_degrees"][0] = 2.0
    with pytest.raises(ValidationError):
        LatticeMetrics.model_validate(payload)
    payload = metrics.model_dump(mode="json")
    payload["reference_lengths_angstrom"][0] = 1.0e-308
    payload["candidate_lengths_angstrom"][0] = 1.0e308
    with pytest.raises(ValidationError):
        LatticeMetrics.model_validate(payload)


def test_element_symbol_maps_are_immutable_and_not_exported() -> None:
    import material_studio_mcp_server.canonicalization._elements as elements

    assert "ATOMIC_NUMBER_BY_SYMBOL" not in elements.__all__
    with pytest.raises(TypeError):
        elements._ATOMIC_NUMBER_BY_SYMBOL["X"] = 999  # type: ignore[index]


def test_canonical_structure_contract_enforces_wrapping_and_site_order(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    payload = canonical_artifact.canonical_structure.model_dump(mode="json")
    payload["sites"][0]["fractional_coordinates"][0] = 1.0
    with pytest.raises(ValidationError):
        type(canonical_artifact.canonical_structure).model_validate(payload)
    payload = canonical_artifact.canonical_structure.model_dump(mode="json")
    payload["sites"] = list(reversed(payload["sites"]))
    with pytest.raises(ValidationError):
        type(canonical_artifact.canonical_structure).model_validate(payload)


def test_canonical_structure_contract_binds_equivalence_class_partition(
    canonical_artifact: CanonicalReferenceArtifact,
) -> None:
    structure_type = type(canonical_artifact.canonical_structure)
    base = canonical_artifact.canonical_structure.model_dump(mode="json")

    gap = copy.deepcopy(base)
    first_second_class = next(
        index
        for index, site in enumerate(gap["sites"])
        if site["equivalence_class"] == 1
    )
    gap["sites"][first_second_class]["equivalence_class"] = 2
    with pytest.raises(ValidationError):
        structure_type.model_validate(gap)

    reordered = copy.deepcopy(base)
    for site in reordered["sites"]:
        site["equivalence_class"] = 1 - site["equivalence_class"]
    with pytest.raises(ValidationError):
        structure_type.model_validate(reordered)

    mixed = copy.deepcopy(base)
    mixed["sites"][first_second_class]["equivalence_class"] = 0
    with pytest.raises(ValidationError):
        structure_type.model_validate(mixed)
