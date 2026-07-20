"""Fail-closed errors for internal reference-data ingestion."""

from __future__ import annotations


class ReferenceDataError(RuntimeError):
    """Base class for reference ingestion and verification failures."""


class RawArtifactPolicyError(ReferenceDataError):
    """The supplied byte sequence violates the fixed raw-artifact policy."""


class DigestMismatchError(ReferenceDataError):
    """A caller-supplied digest does not match the exact raw bytes."""


class StoreConfinementError(ReferenceDataError):
    """A store path, link, reparse point, or file type is unsafe."""


class PublicationConflictError(ReferenceDataError):
    """A create-only target already exists with conflicting content."""


class PartialPublicationError(ReferenceDataError):
    """Only part of an immutable publication transaction exists."""


class ArtifactCorruptionError(ReferenceDataError):
    """Published evidence cannot be verified exactly."""


__all__ = [
    "ArtifactCorruptionError",
    "DigestMismatchError",
    "PartialPublicationError",
    "PublicationConflictError",
    "RawArtifactPolicyError",
    "ReferenceDataError",
    "StoreConfinementError",
]
