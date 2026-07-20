from __future__ import annotations

from typing import Any

import pytest

from material_studio_mcp_server.canonicalization import (
    AtomSite,
    CanonicalReferenceArtifact,
    PeriodicStructure,
    build_canonical_reference_artifact,
)
from material_studio_mcp_server.reference_data import (
    ReferenceLicense,
    ReferenceSource,
    RetrievalContext,
    build_reference_manifest,
    fingerprint_raw_bytes,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes


SYNTHETIC_CIF_BYTES = b"""data_synthetic
_cell_length_a 4.0
_cell_length_b 4.0
_cell_length_c 4.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0 0 0 1
C2 C 0 0.5 0.5 1
C3 C 0.5 0 0.5 1
C4 C 0.5 0.5 0 1
Si1 Si 0.25 0.25 0.25 1
Si2 Si 0.25 0.75 0.75 1
Si3 Si 0.75 0.25 0.75 1
Si4 Si 0.75 0.75 0.25 1
"""


def zincblende_structure(*, lattice_constant: float = 4.0) -> PeriodicStructure:
    lattice = (
        (lattice_constant, 0.0, 0.0),
        (0.0, lattice_constant, 0.0),
        (0.0, 0.0, lattice_constant),
    )
    sublattice = (
        (0.0, 0.0, 0.0),
        (0.0, 0.5, 0.5),
        (0.5, 0.0, 0.5),
        (0.5, 0.5, 0.0),
    )
    sites = [
        AtomSite(
            species="C",
            fractional_coordinates=coordinates,
            occupancy=1.0,
            label=None,
        )
        for coordinates in sublattice
    ]
    sites.extend(
        AtomSite(
            species="Si",
            fractional_coordinates=tuple(value + 0.25 for value in coordinates),
            occupancy=1.0,
            label=None,
        )
        for coordinates in sublattice
    )
    return PeriodicStructure(lattice=lattice, sites=tuple(sites))


def synthetic_reference_artifact() -> CanonicalReferenceArtifact:
    source = ReferenceSource(
        source_id="synthetic-reference",
        provider="Offline Synthetic Provider",
        provider_record_id="synthetic-1",
        provider_revision="1",
        record_url="https://reference.test/records/synthetic-1",
        artifact_url="https://reference.test/artifacts/synthetic-1.cif",
        retrieval=RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Isolated canonicalization contract test",
        ),
        media_type="chemical/x-cif",
        structure_format="cif",
        citation="Synthetic test evidence",
        license=ReferenceLicense(
            name="CC0 1.0 Universal",
            spdx_id="CC0-1.0",
            url="https://creativecommons.org/publicdomain/zero/1.0/",
            redistributable=True,
        ),
    )
    fingerprint = fingerprint_raw_bytes(SYNTHETIC_CIF_BYTES)
    manifest = build_reference_manifest(fingerprint, source)
    return build_canonical_reference_artifact(
        raw_bytes=SYNTHETIC_CIF_BYTES,
        source_record_bytes=canonical_json_bytes(source),
        manifest_bytes=canonical_json_bytes(manifest),
    )


def revalidate(model: Any, **changes: object) -> Any:
    payload = model.model_dump(mode="json")
    payload.update(changes)
    return type(model).model_validate(payload)


@pytest.fixture
def canonical_artifact() -> CanonicalReferenceArtifact:
    return synthetic_reference_artifact()
