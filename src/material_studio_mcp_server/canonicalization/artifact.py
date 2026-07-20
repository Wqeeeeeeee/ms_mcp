"""Pure construction and verification of pinned canonical reference evidence."""

from __future__ import annotations

import hashlib
import re

import numpy as np
import spglib

from material_studio_mcp_server.reference_data.contracts import (
    ReferenceManifest,
    ReferenceSource,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from .canonicalize import (
    canonical_structure_sha256,
    canonicalization_settings_sha256,
    canonicalize_cif_bytes,
    project_canonical_structure,
)
from .contracts import (
    CANONICALIZATION_CONTRACT_VERSION,
    CANONICAL_ARTIFACT_PROFILE,
    IMPLEMENTATION_VERSION,
    CanonicalReferenceArtifact,
    CanonicalizationSettings,
    ImplementationBinding,
    ReferenceEvidenceBinding,
)
from .cif import MAX_CIF_BYTES
from .errors import ArtifactBindingError


MAX_REFERENCE_RECORD_BYTES = 1 * 1024 * 1024
MAX_CANONICAL_ARTIFACT_BYTES = 32 * 1024 * 1024


def _exact_bytes(value: bytes, label: str, *, max_bytes: int) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{label} must be an exact bytes instance")
    if not value:
        raise ArtifactBindingError(f"{label} must not be empty")
    if len(value) > max_bytes:
        raise ArtifactBindingError(f"{label} exceeds the {max_bytes}-byte limit")
    return value


def _canonical_record(
    content: bytes,
    model_type: type[ReferenceSource] | type[ReferenceManifest],
    label: str,
) -> ReferenceSource | ReferenceManifest:
    try:
        model = model_type.model_validate_json(content)
    except Exception as exc:
        raise ArtifactBindingError(f"{label} is not a valid reference record") from exc
    if canonical_json_bytes(model) != content:
        raise ArtifactBindingError(f"{label} is not canonical JSON")
    return model


def build_canonical_reference_artifact(
    *,
    raw_bytes: bytes,
    source_record_bytes: bytes,
    manifest_bytes: bytes,
    settings: CanonicalizationSettings | None = None,
) -> CanonicalReferenceArtifact:
    """Build one content-addressable artifact entirely from explicit bytes."""

    raw = _exact_bytes(raw_bytes, "raw_bytes", max_bytes=MAX_CIF_BYTES)
    source_content = _exact_bytes(
        source_record_bytes,
        "source_record_bytes",
        max_bytes=MAX_REFERENCE_RECORD_BYTES,
    )
    manifest_content = _exact_bytes(
        manifest_bytes,
        "manifest_bytes",
        max_bytes=MAX_REFERENCE_RECORD_BYTES,
    )
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    raw_byte_count = len(raw)
    source_sha256 = hashlib.sha256(source_content).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()

    source = _canonical_record(source_content, ReferenceSource, "source record")
    manifest = _canonical_record(manifest_content, ReferenceManifest, "manifest")
    if not isinstance(source, ReferenceSource) or not isinstance(
        manifest, ReferenceManifest
    ):
        raise ArtifactBindingError("reference record types do not match")
    if manifest.source != source:
        raise ArtifactBindingError("manifest source does not match source record")
    if manifest.source_record_sha256 != source_sha256:
        raise ArtifactBindingError("manifest does not bind source-record SHA-256")
    fingerprint = manifest.raw_artifact.fingerprint
    if fingerprint.sha256 != raw_sha256 or fingerprint.byte_count != raw_byte_count:
        raise ArtifactBindingError("manifest does not bind exact raw bytes")
    if source.structure_format != "cif" or source.media_type != "chemical/x-cif":
        raise ArtifactBindingError("canonical artifact requires reviewed CIF media")
    if source.license.redistributable is not True:
        raise ArtifactBindingError("canonical artifact requires redistributable evidence")

    if settings is None:
        settings = CanonicalizationSettings()
    if not isinstance(settings, CanonicalizationSettings):
        raise TypeError("settings must be CanonicalizationSettings")
    canonical_structure = canonicalize_cif_bytes(
        raw,
        settings=settings,
        expected_sha256=raw_sha256,
        expected_byte_count=raw_byte_count,
    )
    settings_sha256 = canonicalization_settings_sha256(settings)
    structure_sha256 = canonical_structure_sha256(canonical_structure)
    return CanonicalReferenceArtifact(
        contract_version=CANONICALIZATION_CONTRACT_VERSION,
        artifact_profile=CANONICAL_ARTIFACT_PROFILE,
        source=ReferenceEvidenceBinding(
            source_id=source.source_id,
            raw_artifact_sha256=raw_sha256,
            raw_artifact_byte_count=raw_byte_count,
            raw_artifact_relative_path=manifest.raw_artifact.relative_path,
            source_record_sha256=source_sha256,
            source_record_relative_path=manifest.source_record_relative_path,
            manifest_sha256=manifest_sha256,
            manifest_relative_path=(
                f"manifests/sha256/{manifest_sha256[:2]}/{manifest_sha256}.json"
            ),
            media_type="chemical/x-cif",
            structure_format="cif",
            license_spdx_id=source.license.spdx_id,
            redistributable=True,
        ),
        settings=settings,
        settings_sha256=settings_sha256,
        implementation=ImplementationBinding(
            implementation_version=IMPLEMENTATION_VERSION,
            numpy_version=np.__version__,
            spglib_version=spglib.__version__,
            crystallographic_kernel="spglib",
            crystallographic_kernel_license="BSD-3-Clause",
        ),
        canonical_structure=canonical_structure,
        canonical_structure_sha256=structure_sha256,
        coordinate_free_summary=project_canonical_structure(canonical_structure),
        original_artifact_preserved=True,
        candidate_template=False,
        hidden_holdout=False,
    )


def canonical_reference_artifact_bytes(
    artifact: CanonicalReferenceArtifact,
) -> bytes:
    if not isinstance(artifact, CanonicalReferenceArtifact):
        raise TypeError("artifact must be CanonicalReferenceArtifact")
    return canonical_json_bytes(artifact)


def canonical_reference_artifact_sha256(
    artifact: CanonicalReferenceArtifact,
) -> str:
    return hashlib.sha256(canonical_reference_artifact_bytes(artifact)).hexdigest()


def canonical_reference_artifact_relative_path(
    artifact: CanonicalReferenceArtifact,
) -> str:
    digest = canonical_reference_artifact_sha256(artifact)
    return f"canonical/sha256/{digest[:2]}/{digest}.json"


def verify_canonical_reference_artifact(
    *,
    artifact_bytes: bytes,
    raw_bytes: bytes,
    source_record_bytes: bytes,
    manifest_bytes: bytes,
    expected_artifact_sha256: str | None = None,
) -> CanonicalReferenceArtifact:
    """Rebuild and exactly verify a canonical artifact without filesystem access."""

    artifact_content = _exact_bytes(
        artifact_bytes,
        "artifact_bytes",
        max_bytes=MAX_CANONICAL_ARTIFACT_BYTES,
    )
    raw = _exact_bytes(raw_bytes, "raw_bytes", max_bytes=MAX_CIF_BYTES)
    source = _exact_bytes(
        source_record_bytes,
        "source_record_bytes",
        max_bytes=MAX_REFERENCE_RECORD_BYTES,
    )
    manifest = _exact_bytes(
        manifest_bytes,
        "manifest_bytes",
        max_bytes=MAX_REFERENCE_RECORD_BYTES,
    )
    before = tuple(hashlib.sha256(item).hexdigest() for item in (raw, source, manifest))
    artifact_sha256 = hashlib.sha256(artifact_content).hexdigest()
    if expected_artifact_sha256 is not None:
        if not isinstance(expected_artifact_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}", expected_artifact_sha256
        ) is None:
            raise TypeError("expected_artifact_sha256 must be lowercase SHA-256")
        if artifact_sha256 != expected_artifact_sha256:
            raise ArtifactBindingError("canonical artifact SHA-256 mismatch")
    try:
        artifact = CanonicalReferenceArtifact.model_validate_json(artifact_content)
    except Exception as exc:
        raise ArtifactBindingError("canonical artifact contract validation failed") from exc
    if canonical_reference_artifact_bytes(artifact) != artifact_content:
        raise ArtifactBindingError("canonical artifact bytes are not canonical JSON")
    rebuilt = build_canonical_reference_artifact(
        raw_bytes=raw,
        source_record_bytes=source,
        manifest_bytes=manifest,
        settings=artifact.settings,
    )
    if rebuilt != artifact:
        raise ArtifactBindingError("canonical artifact is not reproducible from source evidence")
    after = tuple(hashlib.sha256(item).hexdigest() for item in (raw, source, manifest))
    if after != before:
        raise ArtifactBindingError("source evidence changed during verification")
    return artifact


__all__ = [
    "MAX_CANONICAL_ARTIFACT_BYTES",
    "MAX_REFERENCE_RECORD_BYTES",
    "build_canonical_reference_artifact",
    "canonical_reference_artifact_bytes",
    "canonical_reference_artifact_relative_path",
    "canonical_reference_artifact_sha256",
    "verify_canonical_reference_artifact",
]
