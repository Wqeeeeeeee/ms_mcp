"""Strict immutable contracts for offline reference ingestion."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import Field, StrictBool, ValidationInfo, field_validator, model_validator

from material_studio_mcp_server.runtime.contracts import (
    FrozenContractModel,
    Identifier,
    Sha256,
    StrictFalse,
    StrictTrue,
    canonical_json_bytes,
)


REFERENCE_CONTRACT_VERSION = "1.0.0"
REFERENCE_MANIFEST_PROFILE = "material_studio_reference_manifest_v1"
REFERENCE_RECEIPT_PROFILE = "material_studio_reference_ingestion_receipt_v1"
REFERENCE_METADATA_PROFILE = "material_studio_reference_metadata_projection_v1"
MAX_RAW_ARTIFACT_BYTES = 16 * 1024 * 1024

StructureFormat = Literal["cif", "xsd", "poscar", "castep_cell"]
SafeRelativePath = str

_MEDIA_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_SAFE_REQUEST_HEADERS = frozenset({"accept", "user-agent"})
_SECRET_QUERY_KEYS = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "clientsecret",
        "cookie",
        "credential",
        "key",
        "password",
        "passwd",
        "proxyauth",
        "secret",
        "sig",
        "signature",
        "token",
    }
)
_SECRET_QUERY_MARKERS = (
    "accesskey",
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "password",
    "passwd",
    "proxyauth",
    "secret",
    "signature",
)
_SECRET_TEXT_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:authorization|proxy-auth|cookie|api[_-]?key|access[_-]?token|"
    r"client[_-]?secret|password|passwd|credential|signature|secret|"
    r"auth|token|key|sig)\s*[:=]"
)
_RFC3339_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_REDACTED_SECRET_INPUT = "[REDACTED_SECRET_BEARING_INPUT]"


def _reject_control_characters(value: str, label: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains a control character")
    return value


def _validate_bounded_text(
    value: str,
    *,
    label: str,
    minimum: int = 1,
    maximum: int,
    reject_secret_markers: bool = False,
) -> str:
    _reject_control_characters(value, label)
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} length must be between {minimum} and {maximum}")
    if reject_secret_markers and _SECRET_TEXT_PATTERN.search(value):
        raise ValueError(f"{label} must not contain credential-bearing text")
    return value


def _normalized_query_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_credential_query_key(key: str) -> bool:
    normalized = _normalized_query_key(key)
    return (
        normalized in _SECRET_QUERY_KEYS
        or normalized.endswith("token")
        or any(marker in normalized for marker in _SECRET_QUERY_MARKERS)
    )


def _is_secret_field_name(key: str) -> bool:
    normalized = _normalized_query_key(key)
    return normalized in _SECRET_QUERY_KEYS or normalized.endswith(
        (
            "accesskey",
            "accesstoken",
            "apikey",
            "authorization",
            "clientsecret",
            "cookie",
            "credential",
            "password",
            "passwd",
            "proxyauth",
            "secret",
            "signature",
            "token",
        )
    )


def _url_has_secret_container(value: str) -> bool:
    for candidate in _percent_decoded_variants(value):
        if _has_http_userinfo_like_syntax(candidate):
            return True
        try:
            parts = urlsplit(candidate)
        except ValueError:
            if "://" in candidate and "@" in candidate:
                return True
            continue
        if parts.username is not None or parts.password is not None:
            return True
        if any(
            _is_credential_query_key(key)
            for key, _ in parse_qsl(
                parts.query,
                keep_blank_values=True,
                strict_parsing=False,
            )
        ):
            return True
    return False


def _has_http_userinfo_like_syntax(value: str) -> bool:
    match = re.match(r"(?i)^https?:", value.lstrip())
    if match is None:
        return False
    remainder = value.lstrip()[match.end() :].lstrip("/\\")
    authority_like = re.split(r"[/\\?#]", remainder, maxsplit=1)[0]
    return "@" in authority_like


def _percent_decoded_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    current = value
    for _ in range(2):
        decoded = unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(variants)


def _query_has_secret_container(value: str) -> bool:
    for candidate in _percent_decoded_variants(value):
        if _SECRET_TEXT_PATTERN.search(candidate):
            return True
        if any(
            _is_credential_query_key(key)
            for key, _ in parse_qsl(
                candidate,
                keep_blank_values=True,
                strict_parsing=False,
            )
        ):
            return True
    return False


def _redact_secret_input(value: Any, *, parent_key: str | None = None) -> Any:
    """Replace rejected secret containers before Pydantic records error input."""

    if isinstance(value, Mapping):
        header_name = value.get("name")
        unsafe_header = isinstance(header_name, str) and header_name.casefold() not in (
            _SAFE_REQUEST_HEADERS
        )
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_field_name(key):
                result[key] = _REDACTED_SECRET_INPUT
            elif unsafe_header and key == "value":
                result[key] = _REDACTED_SECRET_INPUT
            else:
                result[key] = _redact_secret_input(item, parent_key=str(key))
        return result
    if isinstance(value, tuple):
        return tuple(_redact_secret_input(item, parent_key=parent_key) for item in value)
    if isinstance(value, list):
        return [_redact_secret_input(item, parent_key=parent_key) for item in value]
    if isinstance(value, str) and (
        _query_has_secret_container(value) or _url_has_secret_container(value)
    ):
        return _REDACTED_SECRET_INPUT
    return value


def validate_safe_https_url(value: str, *, label: str) -> str:
    """Validate a bounded, secret-free, direct HTTPS URL."""

    _validate_bounded_text(value, label=label, maximum=2048)
    if any(character.isspace() for character in value) or "\\" in value:
        raise ValueError(f"{label} contains unsafe URL characters")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{label} is malformed") from exc
    if parts.scheme != "https" or not parts.netloc or not parts.hostname:
        raise ValueError(f"{label} must be an absolute HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{label} must not contain URL userinfo")
    if parts.fragment:
        raise ValueError(f"{label} must not contain a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label} contains an invalid port")
    for decoded in _percent_decoded_variants(value):
        _reject_control_characters(decoded, label)
    if _url_has_secret_container(value):
        raise ValueError(f"{label} contains a credential-bearing query key")
    return value


def raw_artifact_relative_path(sha256: str) -> str:
    return f"raw/sha256/{sha256[:2]}/{sha256}.bin"


def source_record_relative_path(sha256: str) -> str:
    return f"sources/sha256/{sha256[:2]}/{sha256}.json"


def manifest_relative_path(sha256: str) -> str:
    return f"manifests/sha256/{sha256[:2]}/{sha256}.json"


def validate_relative_store_path(value: str, *, label: str) -> str:
    _validate_bounded_text(value, label=label, maximum=512)
    if "\\" in value:
        raise ValueError(f"{label} must use POSIX separators")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{label} must not be drive-qualified")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a confined relative path")
    if path.as_posix() != value:
        raise ValueError(f"{label} is not canonical")
    return value


class ReferenceContractModel(FrozenContractModel):
    """Reference contract base that redacts rejected secret input evidence."""

    @model_validator(mode="before")
    @classmethod
    def redact_secret_containers(cls, value: Any) -> Any:
        return _redact_secret_input(value)

    @field_validator("*", mode="before")
    @classmethod
    def reject_redacted_secret_input(cls, value: Any) -> Any:
        if value == _REDACTED_SECRET_INPUT:
            raise ValueError("secret-bearing input is forbidden")
        return value


class ReviewedRequestHeader(ReferenceContractModel):
    """One bounded, explicitly allowlisted non-secret retrieval header."""

    name: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_header(self) -> "ReviewedRequestHeader":
        _reject_control_characters(self.name, "request header name")
        _reject_control_characters(self.value, "request header value")
        if _HEADER_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("request header name is malformed")
        if self.name.casefold() not in _SAFE_REQUEST_HEADERS:
            raise ValueError("request header is not in the bounded safe allowlist")
        if _SECRET_TEXT_PATTERN.search(self.value):
            raise ValueError("request header value contains credential-bearing text")
        return self


class RetrievalContext(ReferenceContractModel):
    """Caller-supplied, reviewable acquisition context; never a fetch request."""

    retrieved_at: str = Field(min_length=20, max_length=32)
    retrieval_purpose: str = Field(min_length=1, max_length=512)
    query: str | None = Field(default=None, min_length=1, max_length=2048)
    request_headers: tuple[ReviewedRequestHeader, ...] = Field(
        default=(),
        max_length=8,
    )

    @field_validator("retrieved_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        _reject_control_characters(value, "retrieved_at")
        if _RFC3339_TIMESTAMP_PATTERN.fullmatch(value) is None:
            raise ValueError("retrieved_at must be an RFC 3339 timestamp with timezone")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("retrieved_at is not a valid calendar timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value

    @field_validator("request_headers", mode="before")
    @classmethod
    def restore_json_tuple_semantics(cls, value: Any, info: ValidationInfo) -> Any:
        if info.mode == "json" and isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_context(self) -> "RetrievalContext":
        _validate_bounded_text(
            self.retrieval_purpose,
            label="retrieval purpose",
            maximum=512,
            reject_secret_markers=True,
        )
        if self.query is not None:
            _validate_bounded_text(
                self.query,
                label="retrieval query",
                maximum=2048,
                reject_secret_markers=True,
            )
            if _query_has_secret_container(self.query):
                raise ValueError("retrieval query contains a credential-bearing key")
        names = tuple(header.name.casefold() for header in self.request_headers)
        if names != tuple(sorted(names)):
            raise ValueError("request headers must be sorted by case-insensitive name")
        if len(names) != len(set(names)):
            raise ValueError("request header names must be unique")
        return self


class ReferenceLicense(ReferenceContractModel):
    """Complete reviewed license and redistribution evidence."""

    name: str = Field(min_length=1, max_length=256)
    spdx_id: str | None = Field(default=None, min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    redistributable: StrictBool

    @model_validator(mode="after")
    def validate_license(self) -> "ReferenceLicense":
        _validate_bounded_text(self.name, label="license name", maximum=256)
        if self.spdx_id is not None:
            _validate_bounded_text(self.spdx_id, label="license SPDX ID", maximum=64)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", self.spdx_id) is None:
                raise ValueError("license SPDX ID is malformed")
        validate_safe_https_url(self.url, label="license URL")
        return self


class ReferenceSource(ReferenceContractModel):
    """Complete source, format, retrieval, citation, and license provenance."""

    source_id: Identifier
    provider: str = Field(min_length=1, max_length=256)
    provider_record_id: str = Field(min_length=1, max_length=256)
    provider_revision: str | None = Field(default=None, min_length=1, max_length=128)
    record_url: str = Field(min_length=1, max_length=2048)
    artifact_url: str = Field(min_length=1, max_length=2048)
    retrieval: RetrievalContext
    media_type: str = Field(min_length=3, max_length=127)
    structure_format: StructureFormat
    citation: str | None = Field(default=None, min_length=1, max_length=4096)
    license: ReferenceLicense

    @model_validator(mode="after")
    def validate_source(self) -> "ReferenceSource":
        for label, value, maximum in (
            ("provider", self.provider, 256),
            ("provider record ID", self.provider_record_id, 256),
        ):
            _validate_bounded_text(value, label=label, maximum=maximum)
        if self.provider_revision is not None:
            _validate_bounded_text(
                self.provider_revision,
                label="provider revision",
                maximum=128,
            )
        validate_safe_https_url(self.record_url, label="record URL")
        validate_safe_https_url(self.artifact_url, label="artifact URL")
        if self.record_url == self.artifact_url:
            raise ValueError("record URL and direct artifact URL must be distinct")
        if _MEDIA_TYPE_PATTERN.fullmatch(self.media_type) is None:
            raise ValueError("media type is malformed")
        if self.citation is not None:
            _validate_bounded_text(self.citation, label="citation", maximum=4096)
        return self


class RawArtifactFingerprint(ReferenceContractModel):
    """Exact raw-byte identity without a structural-equivalence claim."""

    algorithm: Literal["sha256"]
    sha256: Sha256
    byte_count: int = Field(ge=1, le=MAX_RAW_ARTIFACT_BYTES)


class RawArtifactRecord(ReferenceContractModel):
    """Immutable raw object identity and package-owned relative location."""

    fingerprint: RawArtifactFingerprint
    media_type: str = Field(min_length=3, max_length=127)
    structure_format: StructureFormat
    relative_path: SafeRelativePath

    @model_validator(mode="after")
    def validate_record(self) -> "RawArtifactRecord":
        if _MEDIA_TYPE_PATTERN.fullmatch(self.media_type) is None:
            raise ValueError("media type is malformed")
        validate_relative_store_path(self.relative_path, label="raw artifact path")
        if self.relative_path != raw_artifact_relative_path(self.fingerprint.sha256):
            raise ValueError("raw artifact path does not match its SHA-256")
        return self


class RawDeduplicationBoundary(ReferenceContractModel):
    """Explicitly limits v1 deduplication to exact raw byte identity."""

    basis: Literal["exact_raw_byte_length_and_sha256"]
    cif_parsing_performed: StrictFalse
    canonicalization_performed: StrictFalse
    structural_equivalence_claimed: StrictFalse


class ReferenceManifest(ReferenceContractModel):
    """Canonical immutable manifest for one reviewed raw reference."""

    contract_version: Literal[REFERENCE_CONTRACT_VERSION]
    manifest_profile: Literal[REFERENCE_MANIFEST_PROFILE]
    source: ReferenceSource
    source_record_sha256: Sha256
    source_record_relative_path: SafeRelativePath
    raw_artifact: RawArtifactRecord
    deduplication: RawDeduplicationBoundary

    @model_validator(mode="after")
    def validate_manifest_bindings(self) -> "ReferenceManifest":
        expected_source_sha256 = hashlib.sha256(
            canonical_json_bytes(self.source)
        ).hexdigest()
        if self.source_record_sha256 != expected_source_sha256:
            raise ValueError("source record SHA-256 does not match canonical provenance")
        validate_relative_store_path(
            self.source_record_relative_path,
            label="source record path",
        )
        if self.source_record_relative_path != source_record_relative_path(
            self.source_record_sha256
        ):
            raise ValueError("source record path does not match its SHA-256")
        if self.raw_artifact.media_type != self.source.media_type:
            raise ValueError("manifest media type does not match source provenance")
        if self.raw_artifact.structure_format != self.source.structure_format:
            raise ValueError("manifest format does not match source provenance")
        return self


class IngestionVerification(ReferenceContractModel):
    """Deterministic safety evidence returned without raw structure content."""

    exact_bytes_hashed_before_processing: StrictTrue
    raw_bytes_reread_and_matched: StrictTrue
    caller_digest_status: Literal["not_supplied", "matched"]
    source_license_query_complete: StrictTrue
    retrieval_context_secret_free: StrictTrue
    content_addressed_paths: StrictTrue
    create_only_publication: StrictTrue
    root_confinement_verified: StrictTrue
    raw_bytes_disclosed: StrictFalse
    atom_sites_disclosed: StrictFalse
    coordinates_disclosed: StrictFalse
    lattice_values_derived: StrictFalse
    cif_parsing_performed: StrictFalse
    canonicalization_performed: StrictFalse
    structural_equivalence_claimed: StrictFalse


class IngestionReceipt(ReferenceContractModel):
    """Store-state-independent receipt for one exact ingestion identity."""

    contract_version: Literal[REFERENCE_CONTRACT_VERSION]
    receipt_profile: Literal[REFERENCE_RECEIPT_PROFILE]
    source_id: Identifier
    source_record_sha256: Sha256
    source_record_relative_path: SafeRelativePath
    raw_artifact_sha256: Sha256
    raw_artifact_byte_count: int = Field(ge=1, le=MAX_RAW_ARTIFACT_BYTES)
    raw_artifact_relative_path: SafeRelativePath
    manifest_sha256: Sha256
    manifest_relative_path: SafeRelativePath
    verification: IngestionVerification

    @model_validator(mode="after")
    def validate_receipt_paths(self) -> "IngestionReceipt":
        path_bindings = (
            (
                self.source_record_relative_path,
                source_record_relative_path(self.source_record_sha256),
                "source record path",
            ),
            (
                self.raw_artifact_relative_path,
                raw_artifact_relative_path(self.raw_artifact_sha256),
                "raw artifact path",
            ),
            (
                self.manifest_relative_path,
                manifest_relative_path(self.manifest_sha256),
                "manifest path",
            ),
        )
        for observed, expected, label in path_bindings:
            validate_relative_store_path(observed, label=label)
            if observed != expected:
                raise ValueError(f"{label} does not match its SHA-256")
        return self


class ReferenceMetadataProjection(ReferenceContractModel):
    """Coordinate-free identity/license/hash/count/format projection."""

    contract_version: Literal[REFERENCE_CONTRACT_VERSION]
    projection_profile: Literal[REFERENCE_METADATA_PROFILE]
    source_id: Identifier
    provider: str
    provider_record_id: str
    provider_revision: str | None
    record_url: str
    artifact_url: str
    retrieved_at: str
    retrieval_purpose: str
    query: str | None
    citation: str | None
    license_name: str
    license_spdx_id: str | None
    license_url: str
    redistributable: StrictBool
    media_type: str
    structure_format: StructureFormat
    source_record_sha256: Sha256
    source_record_relative_path: SafeRelativePath
    raw_artifact_sha256: Sha256
    raw_artifact_byte_count: int = Field(ge=1, le=MAX_RAW_ARTIFACT_BYTES)
    raw_artifact_relative_path: SafeRelativePath
    manifest_sha256: Sha256
    manifest_relative_path: SafeRelativePath


class RawDeduplicationResult(ReferenceContractModel):
    """Raw-only duplicate result; byte-different inputs remain unresolved."""

    contract_version: Literal[REFERENCE_CONTRACT_VERSION]
    status: Literal["exact_raw_duplicate", "byte_different_unresolved"]
    duplicate: StrictBool
    byte_count_match: StrictBool
    sha256_match: StrictBool
    basis: Literal["exact_raw_byte_length_and_sha256"]
    cif_parsing_performed: StrictFalse
    canonicalization_performed: StrictFalse
    structural_equivalence_claimed: StrictFalse

    @model_validator(mode="after")
    def validate_result(self) -> "RawDeduplicationResult":
        exact = self.byte_count_match and self.sha256_match
        if self.duplicate is not exact:
            raise ValueError("duplicate must equal exact length-and-SHA identity")
        expected_status = "exact_raw_duplicate" if exact else "byte_different_unresolved"
        if self.status != expected_status:
            raise ValueError("deduplication status does not match exact-byte evidence")
        return self


__all__ = [
    "IngestionReceipt",
    "IngestionVerification",
    "MAX_RAW_ARTIFACT_BYTES",
    "REFERENCE_CONTRACT_VERSION",
    "REFERENCE_MANIFEST_PROFILE",
    "REFERENCE_METADATA_PROFILE",
    "REFERENCE_RECEIPT_PROFILE",
    "ReferenceContractModel",
    "RawArtifactFingerprint",
    "RawArtifactRecord",
    "RawDeduplicationBoundary",
    "RawDeduplicationResult",
    "ReferenceLicense",
    "ReferenceManifest",
    "ReferenceMetadataProjection",
    "ReferenceSource",
    "RetrievalContext",
    "ReviewedRequestHeader",
    "StructureFormat",
    "manifest_relative_path",
    "raw_artifact_relative_path",
    "source_record_relative_path",
    "validate_relative_store_path",
    "validate_safe_https_url",
]
