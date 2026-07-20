"""Internal, offline reference ingestion with no public MCP registration."""

from .contracts import (
    MAX_RAW_ARTIFACT_BYTES,
    REFERENCE_CONTRACT_VERSION,
    IngestionReceipt,
    RawArtifactFingerprint,
    RawDeduplicationResult,
    ReferenceLicense,
    ReferenceManifest,
    ReferenceMetadataProjection,
    ReferenceSource,
    RetrievalContext,
    ReviewedRequestHeader,
)
from .deduplication import (
    compare_raw_bytes,
    compare_raw_fingerprints,
    fingerprint_raw_bytes,
)
from .errors import (
    ArtifactCorruptionError,
    DigestMismatchError,
    PartialPublicationError,
    PublicationConflictError,
    RawArtifactPolicyError,
    ReferenceDataError,
    StoreConfinementError,
)
from .manifest import (
    build_reference_manifest,
    canonical_sha256,
)
from .store import ingest_reference, verify_ingestion


__all__ = [
    "ArtifactCorruptionError",
    "DigestMismatchError",
    "IngestionReceipt",
    "MAX_RAW_ARTIFACT_BYTES",
    "PartialPublicationError",
    "PublicationConflictError",
    "REFERENCE_CONTRACT_VERSION",
    "RawArtifactFingerprint",
    "RawArtifactPolicyError",
    "RawDeduplicationResult",
    "ReferenceDataError",
    "ReferenceLicense",
    "ReferenceManifest",
    "ReferenceMetadataProjection",
    "ReferenceSource",
    "RetrievalContext",
    "ReviewedRequestHeader",
    "StoreConfinementError",
    "build_reference_manifest",
    "canonical_sha256",
    "compare_raw_bytes",
    "compare_raw_fingerprints",
    "fingerprint_raw_bytes",
    "ingest_reference",
    "verify_ingestion",
]
