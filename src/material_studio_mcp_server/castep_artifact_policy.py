"""Task-bound acceptance policy for native artifacts from CASTEP Energy runs."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .ms_roundtrip.errors import RoundtripError, RoundtripErrorCode
from .ms_roundtrip.secure_io import (
    reject_link_or_reparse_components,
    resolve_existing_directory,
)

if TYPE_CHECKING:
    from .specs.project import ModelSpec


CASTEP_ENERGY_ARTIFACT_POLICY_SCHEMA = (
    "material_studio_castep_energy_artifact_policy_v1"
)

REASON_VERIFIED_RECEIPT_REQUIRED = "verified_electronic_receipt_required"
REASON_NON_CANONICAL_RECEIPT = "non_canonical_receipt_or_summary"
REASON_RECEIPT_CHANGED_DURING_VERIFICATION = (
    "receipt_changed_during_verification"
)
REASON_ENERGY_TASK_REQUIRED = "energy_task_required"
REASON_RECEIPT_AUDIT_TASK_MISMATCH = "receipt_audit_task_mismatch"
REASON_NATIVE_ARTIFACT_ROOT_INVALID = "native_artifact_root_invalid"
REASON_NATIVE_ARTIFACT_ROOT_SCAN_FAILED = "native_artifact_root_scan_failed"
REASON_NATIVE_ARTIFACT_MANIFEST_INVALID = "native_artifact_manifest_invalid"
REASON_NATIVE_ARTIFACT_COUNT_MISMATCH = "native_artifact_count_mismatch"
REASON_NATIVE_AUDIT_ARTIFACT_COUNT_MISMATCH = (
    "native_audit_artifact_count_mismatch"
)
REASON_NATIVE_ARTIFACT_DUPLICATE = "native_artifact_duplicate"
REASON_NATIVE_ARTIFACT_PATH_OUTSIDE_ROOT = (
    "native_artifact_path_outside_root"
)
REASON_NATIVE_ARTIFACT_NOT_REGULAR_OR_UNSAFE = (
    "native_artifact_not_regular_or_unsafe"
)
REASON_NATIVE_ARTIFACT_BINDING_UNSTABLE = "native_artifact_binding_unstable"
REASON_NATIVE_ARTIFACT_SIZE_MISMATCH = "native_artifact_size_mismatch"
REASON_NATIVE_ARTIFACT_SHA256_MISMATCH = "native_artifact_sha256_mismatch"
REASON_NATIVE_BANDS_MANIFEST_MISMATCH = "native_bands_manifest_mismatch"
REASON_NATIVE_BANDS_ARTIFACT_AMBIGUOUS = "native_bands_artifact_ambiguous"
REASON_NATIVE_BANDS_AUDIT_BINDING_MISMATCH = (
    "native_bands_audit_binding_mismatch"
)
REASON_DERIVED_ARTIFACTS_PRESENT = "derived_artifacts_present"
REASON_BAND_KPOINT_PATH_EXPORTED = "band_kpoint_path_exported"
REASON_NUMERIC_CURVE_EXPORTED = "numeric_curve_exported"
REASON_NUMERIC_CURVE_KIND_PRESENT = "numeric_curve_kind_present"
REASON_SCIENTIFIC_BAND_GAP_CLAIMED = "scientific_band_gap_claimed"
REASON_SCIENTIFIC_BAND_GAP_VERIFIED = "scientific_band_gap_verified"
REASON_SCIENTIFIC_CONVERGENCE_CLAIMED = "scientific_convergence_claimed"
REASON_SCIENTIFIC_CONVERGENCE_VERIFIED = "scientific_convergence_verified"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RelativeArtifactPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        pattern=r"^[^\\:\x00]+$",
    ),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class CastepEnergyArtifactBinding(_FrozenModel):
    relative_path: RelativeArtifactPath
    sha256: Sha256
    size_bytes: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        parts = value.split("/")
        if value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("artifact path must be a normalized relative path")
        return value


class CastepEnergyArtifactPolicyReceipt(_FrozenModel):
    schema_version: Literal[CASTEP_ENERGY_ARTIFACT_POLICY_SCHEMA] = (
        CASTEP_ENERGY_ARTIFACT_POLICY_SCHEMA
    )
    status: Literal["PASS", "FAIL"]
    reason_codes: tuple[str, ...]
    task: str | None
    verified_electronic_receipt: bool
    electronic_receipt_sha256: Sha256 | None
    verified_summary_sha256: Sha256 | None
    native_artifact_count: int = Field(ge=0)
    native_bands_artifact_count: int = Field(ge=0)
    derived_artifact_count: int | None = Field(default=None, ge=0)
    native_band_kpoint_path_exported: bool | None
    numeric_curve_data_exported: bool | None
    numeric_curve_kind: str | None
    scientific_band_gap_claimed: bool | None
    scientific_band_gap_verified: bool | None
    scientific_convergence_claimed: bool | None
    scientific_convergence_verified: bool | None
    native_artifact_manifest_sha256: Sha256 | None
    native_artifacts: tuple[CastepEnergyArtifactBinding, ...]
    native_bands_artifacts: tuple[CastepEnergyArtifactBinding, ...]

    @model_validator(mode="after")
    def validate_status(self) -> "CastepEnergyArtifactPolicyReceipt":
        if self.status == "PASS" and self.reason_codes:
            raise ValueError("PASS cannot contain artifact-policy failures")
        if self.status == "FAIL" and not self.reason_codes:
            raise ValueError("FAIL requires at least one artifact-policy reason")
        return self


@dataclass(frozen=True)
class _OpenArtifact:
    path: Path
    descriptor: int
    parent_directory: _OpenDirectory
    entry_name: str
    identity: tuple[int, ...]
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _OpenDirectory:
    path: Path
    descriptor: int = field(compare=False, repr=False)
    parent_descriptor: int | None = field(compare=False, repr=False)
    entry_name: str | None
    backend: Literal["windows", "posix"]
    file_id: tuple[int, int]
    identity: tuple[int, ...]


@dataclass(frozen=True)
class _ArtifactTreeSnapshot:
    directories: tuple[_OpenDirectory, ...]
    bands_paths: tuple[tuple[str, Path], ...]


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


def validate_castep_energy_artifact_policy(
    *,
    spec: "ModelSpec",
    native_artifact_root: str | Path,
) -> CastepEnergyArtifactPolicyReceipt:
    """Validate the latest bound Energy receipt from one immutable ModelSpec."""

    from .castep_electronic import verify_castep_electronic_receipt

    metadata = getattr(spec, "metadata", None)
    receipt_before = (
        metadata.get("last_castep_electronic_calculation")
        if isinstance(metadata, Mapping)
        else None
    )
    receipt_before_sha256 = (
        _canonical_mapping_sha256(receipt_before)
        if isinstance(receipt_before, Mapping)
        else None
    )
    try:
        verified_summary = verify_castep_electronic_receipt(spec)
    except (OSError, TypeError, ValueError):
        verified_summary = None
    metadata_after = getattr(spec, "metadata", None)
    raw_receipt = (
        metadata_after.get("last_castep_electronic_calculation")
        if isinstance(metadata_after, Mapping)
        else None
    )
    receipt_after_sha256 = (
        _canonical_mapping_sha256(raw_receipt)
        if isinstance(raw_receipt, Mapping)
        else None
    )
    return _validate_castep_energy_artifact_policy_inputs(
        electronic_receipt=raw_receipt,
        verified_receipt_summary=verified_summary,
        native_artifact_root=native_artifact_root,
        receipt_binding_consistent=bool(
            receipt_before_sha256 is not None
            and receipt_before_sha256 == receipt_after_sha256
        ),
    )


def _validate_castep_energy_artifact_policy_inputs(
    *,
    electronic_receipt: Mapping[str, Any] | object,
    verified_receipt_summary: Mapping[str, Any] | object,
    native_artifact_root: str | Path,
    receipt_binding_consistent: bool,
) -> CastepEnergyArtifactPolicyReceipt:
    reasons: list[str] = []

    def reject(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    receipt = (
        dict(electronic_receipt)
        if isinstance(electronic_receipt, Mapping)
        else {}
    )
    summary = (
        dict(verified_receipt_summary)
        if isinstance(verified_receipt_summary, Mapping)
        else {}
    )
    audit = (
        dict(receipt.get("native_output_audit"))
        if isinstance(receipt.get("native_output_audit"), Mapping)
        else {}
    )
    receipt_sha256 = _canonical_mapping_sha256(receipt)
    summary_sha256 = _canonical_mapping_sha256(summary)
    if receipt_sha256 is None or summary_sha256 is None:
        reject(REASON_NON_CANONICAL_RECEIPT)
    if not receipt_binding_consistent:
        reject(REASON_RECEIPT_CHANGED_DURING_VERIFICATION)

    verified_receipt = bool(
        receipt_binding_consistent
        and summary.get("binding_verified") is True
        and summary.get("status") == "verified"
    )
    if not verified_receipt:
        reject(REASON_VERIFIED_RECEIPT_REQUIRED)

    raw_task = receipt.get("task")
    task = "Energy" if raw_task == "Energy" else None
    if raw_task != "Energy" or summary.get("task") != "Energy":
        reject(REASON_ENERGY_TASK_REQUIRED)
    if (
        audit.get("task") != raw_task
        or summary.get("native_output_audit") != audit
    ):
        reject(REASON_RECEIPT_AUDIT_TASK_MISMATCH)

    root: Path | None
    try:
        root = resolve_existing_directory(Path(native_artifact_root))
    except (OSError, TypeError, ValueError, RoundtripError):
        root = None
        reject(REASON_NATIVE_ARTIFACT_ROOT_INVALID)

    raw_manifest = receipt.get("native_artifacts")
    manifest = raw_manifest if isinstance(raw_manifest, list) else []
    if not isinstance(raw_manifest, list):
        reject(REASON_NATIVE_ARTIFACT_MANIFEST_INVALID)

    declared_native_count = _strict_non_negative_int(
        receipt.get("native_artifact_count")
    )
    if declared_native_count != len(manifest):
        reject(REASON_NATIVE_ARTIFACT_COUNT_MISMATCH)
    if _strict_non_negative_int(summary.get("native_artifact_count")) != len(
        manifest
    ):
        reject(REASON_NATIVE_ARTIFACT_COUNT_MISMATCH)
    if _strict_non_negative_int(audit.get("native_artifact_count")) != len(
        manifest
    ):
        reject(REASON_NATIVE_AUDIT_ARTIFACT_COUNT_MISMATCH)

    normalized: list[CastepEnergyArtifactBinding] = []
    manifest_band_paths: set[str] = set()
    seen_paths: set[str] = set()
    observed_band_paths: set[str] = set()
    band_bindings: list[CastepEnergyArtifactBinding] = []
    open_artifacts: list[_OpenArtifact] = []
    tree_snapshot: _ArtifactTreeSnapshot | None = None
    try:
        if root is not None:
            try:
                tree_snapshot = _scan_artifact_tree(root)
            except (OSError, RoundtripError):
                reject(REASON_NATIVE_ARTIFACT_ROOT_SCAN_FAILED)
        for item in manifest:
            if not isinstance(item, Mapping):
                reject(REASON_NATIVE_ARTIFACT_MANIFEST_INVALID)
                continue
            raw_path = item.get("path")
            raw_digest = item.get("sha256")
            raw_size = _strict_non_negative_int(item.get("size_bytes"))
            if (
                not isinstance(raw_path, str)
                or not _is_sha256(raw_digest)
                or raw_size is None
            ):
                reject(REASON_NATIVE_ARTIFACT_MANIFEST_INVALID)
                continue
            candidate_path = Path(raw_path)
            if not candidate_path.is_absolute():
                reject(REASON_NATIVE_ARTIFACT_MANIFEST_INVALID)
                continue
            if root is None or tree_snapshot is None:
                continue
            try:
                (
                    path,
                    relative_path,
                    parent_directory,
                    entry_name,
                ) = _tree_file_binding(
                    root,
                    candidate_path,
                    tree_snapshot,
                )
            except RoundtripError as exc:
                if exc.code == RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED:
                    reject(REASON_NATIVE_ARTIFACT_PATH_OUTSIDE_ROOT)
                else:
                    reject(REASON_NATIVE_ARTIFACT_NOT_REGULAR_OR_UNSAFE)
                continue
            except (OSError, TypeError, ValueError):
                reject(REASON_NATIVE_ARTIFACT_NOT_REGULAR_OR_UNSAFE)
                continue

            path_key = relative_path.casefold()
            if path_key in seen_paths:
                reject(REASON_NATIVE_ARTIFACT_DUPLICATE)
                continue
            seen_paths.add(path_key)
            try:
                opened = _open_stable_artifact(
                    path,
                    parent_directory=parent_directory,
                    entry_name=entry_name,
                )
            except (OSError, RoundtripError):
                reject(REASON_NATIVE_ARTIFACT_BINDING_UNSTABLE)
                continue
            open_artifacts.append(opened)
            if opened.size_bytes != raw_size:
                reject(REASON_NATIVE_ARTIFACT_SIZE_MISMATCH)
                continue
            if opened.sha256 != raw_digest:
                reject(REASON_NATIVE_ARTIFACT_SHA256_MISMATCH)
                continue
            binding = CastepEnergyArtifactBinding(
                relative_path=relative_path,
                sha256=opened.sha256,
                size_bytes=opened.size_bytes,
            )
            normalized.append(binding)
            if path.suffix.casefold() == ".bands":
                manifest_band_paths.add(path_key)

        normalized.sort(key=lambda item: item.relative_path.casefold())
        band_bindings = [
            item
            for item in normalized
            if Path(item.relative_path).suffix.casefold() == ".bands"
        ]
        bound_by_path = (
            {
                item.path.relative_to(root).as_posix().casefold(): item
                for item in open_artifacts
            }
            if root is not None
            else {}
        )
        if root is not None:
            try:
                if tree_snapshot is None:
                    raise RoundtripError(
                        RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                        "The native artifact tree is unavailable.",
                    )
                for relative_key, candidate in tree_snapshot.bands_paths:
                    (
                        resolved,
                        _relative_path,
                        parent_directory,
                        entry_name,
                    ) = _tree_file_binding(
                        root,
                        candidate,
                        tree_snapshot,
                    )
                    observed_band_paths.add(relative_key)
                    if relative_key not in bound_by_path:
                        scanned = _open_stable_artifact(
                            resolved,
                            parent_directory=parent_directory,
                            entry_name=entry_name,
                        )
                        open_artifacts.append(scanned)
                        bound_by_path[relative_key] = scanned
            except (OSError, RoundtripError):
                reject(REASON_NATIVE_ARTIFACT_ROOT_SCAN_FAILED)
            if manifest_band_paths:
                bands_summary = audit.get("bands_summary")
                if not _bands_summary_matches(
                    bands_summary,
                    root=root,
                    bands_bindings=band_bindings,
                ):
                    reject(REASON_NATIVE_BANDS_AUDIT_BINDING_MISMATCH)
            elif audit.get("bands_summary") is not None:
                reject(REASON_NATIVE_BANDS_AUDIT_BINDING_MISMATCH)
            if (
                tree_snapshot is None
                or not _artifact_set_still_stable(
                    root,
                    tree_snapshot=tree_snapshot,
                    artifacts=open_artifacts,
                )
            ):
                reject(REASON_NATIVE_ARTIFACT_BINDING_UNSTABLE)
    finally:
        for opened in open_artifacts:
            _close_descriptor(opened.descriptor)
        if tree_snapshot is not None:
            _close_tree_snapshot(tree_snapshot)

    if observed_band_paths != manifest_band_paths:
        reject(REASON_NATIVE_BANDS_MANIFEST_MISMATCH)
    if len(manifest_band_paths) > 1:
        reject(REASON_NATIVE_BANDS_ARTIFACT_AMBIGUOUS)

    raw_derived = receipt.get("derived_artifacts")
    summary_derived = summary.get("derived_artifacts")
    audit_derived = audit.get("derived_artifacts")
    derived_count = _strict_non_negative_int(
        receipt.get("derived_artifact_count")
    )
    derived_contract_ok = bool(
        isinstance(raw_derived, list)
        and isinstance(summary_derived, list)
        and isinstance(audit_derived, list)
        and raw_derived == []
        and summary_derived == []
        and audit_derived == []
        and derived_count == 0
        and _strict_non_negative_int(summary.get("derived_artifact_count")) == 0
        and _strict_non_negative_int(audit.get("derived_artifact_count")) == 0
    )
    if not derived_contract_ok:
        reject(REASON_DERIVED_ARTIFACTS_PRESENT)

    native_band_path_exported = _strict_bool(
        receipt.get("native_band_kpoint_path_exported")
    )
    if (
        native_band_path_exported is not False
        or summary.get("native_band_kpoint_path_exported") is not False
        or audit.get("native_band_kpoint_path_exported") is not False
    ):
        reject(REASON_BAND_KPOINT_PATH_EXPORTED)

    numeric_curve_exported = _strict_bool(
        receipt.get("numeric_curve_data_exported")
    )
    if (
        numeric_curve_exported is not False
        or summary.get("numeric_curve_data_exported") is not False
        or audit.get("numeric_curve_data_exported") is not False
    ):
        reject(REASON_NUMERIC_CURVE_EXPORTED)
    numeric_curve_kind = receipt.get("numeric_curve_kind")
    if (
        numeric_curve_kind is not None
        or summary.get("numeric_curve_kind") is not None
        or audit.get("numeric_curve_kind") is not None
    ):
        reject(REASON_NUMERIC_CURVE_KIND_PRESENT)

    band_gap_claimed = _strict_bool(receipt.get("scientific_band_gap_claimed"))
    if (
        band_gap_claimed is not False
        or summary.get("scientific_band_gap_claimed") not in (None, False)
        or audit.get("scientific_band_gap_claimed") not in (None, False)
    ):
        reject(REASON_SCIENTIFIC_BAND_GAP_CLAIMED)
    band_gap_verified = _strict_bool(
        receipt.get("scientific_band_gap_verified")
    )
    if (
        band_gap_verified is not False
        or summary.get("scientific_band_gap_verified") is not False
        or audit.get("scientific_band_gap_verified") is not False
    ):
        reject(REASON_SCIENTIFIC_BAND_GAP_VERIFIED)

    convergence_claimed = _strict_bool(
        receipt.get("scientific_convergence_claimed")
    )
    if (
        convergence_claimed is not False
        or summary.get("scientific_convergence_claimed") not in (None, False)
        or audit.get("scientific_convergence_claimed") not in (None, False)
    ):
        reject(REASON_SCIENTIFIC_CONVERGENCE_CLAIMED)
    convergence_verified = _strict_bool(
        receipt.get("scientific_convergence_verified")
    )
    if (
        convergence_verified is not False
        or summary.get("scientific_convergence_verified") is not False
        or audit.get("scientific_convergence_verified") is not False
    ):
        reject(REASON_SCIENTIFIC_CONVERGENCE_VERIFIED)

    manifest_payload = [item.model_dump(mode="json") for item in normalized]
    manifest_fully_bound = bool(
        root is not None
        and len(normalized) == len(manifest)
        and declared_native_count == len(manifest)
    )
    return CastepEnergyArtifactPolicyReceipt(
        status="PASS" if not reasons else "FAIL",
        reason_codes=tuple(reasons),
        task=task,
        verified_electronic_receipt=verified_receipt,
        electronic_receipt_sha256=receipt_sha256,
        verified_summary_sha256=summary_sha256,
        native_artifact_count=len(manifest),
        native_bands_artifact_count=len(band_bindings),
        derived_artifact_count=derived_count,
        native_band_kpoint_path_exported=native_band_path_exported,
        numeric_curve_data_exported=numeric_curve_exported,
        numeric_curve_kind=None,
        scientific_band_gap_claimed=band_gap_claimed,
        scientific_band_gap_verified=band_gap_verified,
        scientific_convergence_claimed=convergence_claimed,
        scientific_convergence_verified=convergence_verified,
        native_artifact_manifest_sha256=(
            _canonical_payload_sha256(manifest_payload)
            if manifest_fully_bound
            else None
        ),
        native_artifacts=tuple(normalized),
        native_bands_artifacts=tuple(band_bindings),
    )


def _bands_summary_matches(
    value: Any,
    *,
    root: Path,
    bands_bindings: list[CastepEnergyArtifactBinding],
) -> bool:
    if not isinstance(value, Mapping) or len(bands_bindings) != 1:
        return False
    source_path = value.get("source_path")
    source_digest = value.get("source_sha256")
    source_size = _strict_non_negative_int(value.get("source_size_bytes"))
    if (
        not isinstance(source_path, str)
        or not _is_sha256(source_digest)
        or source_size is None
        or not Path(source_path).is_absolute()
    ):
        return False
    try:
        relative = _relative_artifact_path(root, Path(source_path))
    except (TypeError, ValueError, RoundtripError):
        return False
    binding = bands_bindings[0]
    return bool(
        relative.as_posix().casefold() == binding.relative_path.casefold()
        and source_digest == binding.sha256
        and source_size == binding.size_bytes
    )


def _tree_file_binding(
    root: Path,
    candidate: Path,
    tree_snapshot: _ArtifactTreeSnapshot,
) -> tuple[Path, str, _OpenDirectory, str]:
    relative = _relative_artifact_path(root, candidate)
    parent_path = root.joinpath(*relative.parts[:-1])
    parent_key = _path_comparison_key(parent_path)
    parent_directory = next(
        (
            directory
            for directory in tree_snapshot.directories
            if _path_comparison_key(directory.path) == parent_key
        ),
        None,
    )
    if parent_directory is None:
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
            "A native artifact parent directory is not bound.",
        )
    normalized = parent_path / relative.name
    return (
        normalized,
        relative.as_posix(),
        parent_directory,
        relative.name,
    )


def _relative_artifact_path(root: Path, candidate: Path) -> Path:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "A native artifact path escaped its root.",
        ) from exc
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or (os.name == "nt" and ":" in relative.name)
    ):
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "A native artifact path is not normalized.",
        )
    return relative


def _path_comparison_key(path: Path) -> str:
    value = path.as_posix()
    return value.casefold() if os.name == "nt" else value


def _stat_directory_entry(
    path: Path,
    *,
    parent_directory: _OpenDirectory,
    entry_name: str,
) -> os.stat_result:
    if parent_directory.backend == "windows":
        return path.stat(follow_symlinks=False)
    return os.stat(
        entry_name,
        dir_fd=parent_directory.descriptor,
        follow_symlinks=False,
    )


def _required_entry_name(value: str | None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
            "A directory entry name is invalid.",
        )
    return value


def _open_stable_artifact(
    path: Path,
    *,
    parent_directory: _OpenDirectory,
    entry_name: str,
) -> _OpenArtifact:
    descriptor: int | None = None
    try:
        if parent_directory.backend == "windows":
            reject_link_or_reparse_components(path)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = (
            _open_windows_artifact_descriptor(path)
            if parent_directory.backend == "windows"
            else os.open(
                entry_name,
                flags,
                dir_fd=parent_directory.descriptor,
            )
        )
        before = os.fstat(descriptor)
        path_before = _stat_directory_entry(
            path,
            parent_directory=parent_directory,
            entry_name=entry_name,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or int(before.st_nlink) != 1
            or int(path_before.st_nlink) != 1
            or _stat_identity(before) != _stat_identity(path_before)
        ):
            raise RoundtripError(
                RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                "A native artifact handle is not bound to one regular file.",
            )
        digest = _descriptor_sha256(descriptor)
        after = os.fstat(descriptor)
        if parent_directory.backend == "windows":
            reject_link_or_reparse_components(path)
        path_after = _stat_directory_entry(
            path,
            parent_directory=parent_directory,
            entry_name=entry_name,
        )
    except OSError as exc:
        if descriptor is not None:
            _close_descriptor(descriptor)
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
            "A native artifact could not be read safely.",
        ) from exc
    except RoundtripError:
        if descriptor is not None:
            _close_descriptor(descriptor)
        raise
    if (
        int(before.st_nlink) != 1
        or int(after.st_nlink) != 1
        or int(path_after.st_nlink) != 1
        or _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(path_after)
    ):
        _close_descriptor(descriptor)
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
            "A native artifact changed while it was being verified.",
        )
    return _OpenArtifact(
        path=path,
        descriptor=descriptor,
        parent_directory=parent_directory,
        entry_name=entry_name,
        identity=_stat_identity(after),
        sha256=digest,
        size_bytes=int(after.st_size),
    )


def _artifact_set_still_stable(
    root: Path,
    *,
    tree_snapshot: _ArtifactTreeSnapshot,
    artifacts: list[_OpenArtifact],
) -> bool:
    rescanned: _ArtifactTreeSnapshot | None = None
    try:
        rescanned = _scan_artifact_tree(root)
        if rescanned != tree_snapshot:
            return False
        for artifact in artifacts:
            if artifact.parent_directory.backend == "windows":
                reject_link_or_reparse_components(artifact.path)
            handle_value = os.fstat(artifact.descriptor)
            path_value = _stat_directory_entry(
                artifact.path,
                parent_directory=artifact.parent_directory,
                entry_name=artifact.entry_name,
            )
            if (
                _stat_identity(handle_value) != artifact.identity
                or _stat_identity(path_value) != artifact.identity
                or _descriptor_sha256(artifact.descriptor) != artifact.sha256
                or _stat_identity(os.fstat(artifact.descriptor))
                != artifact.identity
            ):
                return False
        if not _tree_directories_still_stable(tree_snapshot):
            return False
    except (OSError, RoundtripError):
        return False
    finally:
        if rescanned is not None:
            _close_tree_snapshot(rescanned)
    return True


def _tree_directories_still_stable(
    tree_snapshot: _ArtifactTreeSnapshot,
) -> bool:
    for directory in tree_snapshot.directories:
        if not _directory_binding_still_stable(directory):
            return False
    return True


def _scan_artifact_tree(root: Path) -> _ArtifactTreeSnapshot:
    root_value = root.stat(follow_symlinks=False)
    pending = [(root, _file_id(root_value), None, None)]
    directories: list[_OpenDirectory] = []
    bands_paths: dict[str, Path] = {}
    try:
        while pending:
            (
                directory,
                expected_file_id,
                parent_descriptor,
                entry_name,
            ) = pending.pop()
            opened = _open_directory_binding(
                directory,
                expected_file_id=expected_file_id,
                parent_descriptor=parent_descriptor,
                entry_name=entry_name,
            )
            directories.append(opened)
            rows: list[tuple[str, Path, os.stat_result]] = []
            scan_target: str | Path | int = (
                directory
                if opened.backend == "windows"
                else opened.descriptor
            )
            with os.scandir(scan_target) as entries:
                for entry in entries:
                    path = directory / entry.name
                    rows.append(
                        (
                            entry.name,
                            path,
                            (
                                path.stat(follow_symlinks=False)
                                if opened.backend == "windows"
                                else entry.stat(follow_symlinks=False)
                            ),
                        )
                    )
            if not _directory_binding_still_stable(opened):
                raise RoundtripError(
                    RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                    "A native artifact directory changed during enumeration.",
                )
            for _name, path, value in sorted(
                rows,
                key=lambda item: item[0].casefold(),
            ):
                if stat.S_ISLNK(value.st_mode) or _is_reparse(value):
                    raise RoundtripError(
                        RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                        "A native artifact tree contains a link or reparse point.",
                    )
                if stat.S_ISDIR(value.st_mode):
                    pending.append(
                        (
                            path,
                            _file_id(value),
                            (
                                opened.descriptor
                                if opened.backend == "posix"
                                else None
                            ),
                            (
                                _name
                                if opened.backend == "posix"
                                else None
                            ),
                        )
                    )
                    continue
                if path.suffix.casefold() != ".bands":
                    continue
                if not stat.S_ISREG(value.st_mode):
                    raise RoundtripError(
                        RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                        "A native .bands artifact is not a regular file.",
                    )
                relative_key = path.relative_to(root).as_posix().casefold()
                if relative_key in bands_paths:
                    raise RoundtripError(
                        RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                        "Native .bands artifact paths are ambiguous.",
                    )
                bands_paths[relative_key] = path
        return _ArtifactTreeSnapshot(
            directories=tuple(
                sorted(
                    directories,
                    key=lambda item: item.path.as_posix().casefold(),
                )
            ),
            bands_paths=tuple(sorted(bands_paths.items())),
        )
    except BaseException:
        for opened in directories:
            _close_directory_binding(opened)
        raise


def _open_directory_binding(
    path: Path,
    *,
    expected_file_id: tuple[int, int],
    parent_descriptor: int | None,
    entry_name: str | None,
) -> _OpenDirectory:
    if os.name == "nt":
        reject_link_or_reparse_components(path)
        return _open_windows_directory_binding(
            path,
            expected_file_id=expected_file_id,
        )
    if parent_descriptor is None:
        reject_link_or_reparse_components(path)
    return _open_posix_directory_binding(
        path,
        expected_file_id=expected_file_id,
        parent_descriptor=parent_descriptor,
        entry_name=entry_name,
    )


def _open_windows_directory_binding(
    path: Path,
    *,
    expected_file_id: tuple[int, int],
) -> _OpenDirectory:
    kernel32 = _windows_kernel32()
    desired_access = 0x0001 | 0x0080
    share_read = 0x00000001
    open_existing = 3
    open_flags = 0x02000000 | 0x00200000
    raw_handle = kernel32.CreateFileW(
        str(path),
        desired_access,
        share_read,
        None,
        open_existing,
        open_flags,
        None,
    )
    descriptor = int(raw_handle)
    invalid_handle = ctypes.c_void_p(-1).value
    if descriptor == invalid_handle:
        _raise_windows_error()
    try:
        information = _windows_directory_information(
            kernel32,
            descriptor,
        )
        file_id = _windows_file_id(information)
        value = path.stat(follow_symlinks=False)
        if (
            not information.file_attributes & 0x00000010
            or information.file_attributes & 0x00000400
            or not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or _is_reparse(value)
            or file_id != expected_file_id
            or _file_id(value) != file_id
        ):
            raise RoundtripError(
                RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                "A native artifact directory handle is unsafe.",
            )
        return _OpenDirectory(
            path=path,
            descriptor=descriptor,
            parent_descriptor=None,
            entry_name=None,
            backend="windows",
            file_id=file_id,
            identity=_stat_identity(value),
        )
    except BaseException:
        _close_windows_handle(descriptor)
        raise


def _open_windows_artifact_descriptor(path: Path) -> int:
    import msvcrt

    kernel32 = _windows_kernel32()
    generic_read = 0x80000000
    share_read = 0x00000001
    open_existing = 3
    open_reparse_point = 0x00200000
    raw_handle = kernel32.CreateFileW(
        str(path),
        generic_read,
        share_read,
        None,
        open_existing,
        open_reparse_point,
        None,
    )
    handle = int(raw_handle)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        _raise_windows_error()
    try:
        information = _windows_directory_information(kernel32, handle)
        if (
            information.file_attributes & 0x00000010
            or information.file_attributes & 0x00000400
        ):
            raise RoundtripError(
                RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                "A native artifact handle is not a regular file.",
            )
        return int(
            msvcrt.open_osfhandle(
                handle,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0),
            )
        )
    except BaseException:
        _close_windows_handle(handle)
        raise


def _open_posix_directory_binding(
    path: Path,
    *,
    expected_file_id: tuple[int, int],
    parent_descriptor: int | None,
    entry_name: str | None,
) -> _OpenDirectory:
    descriptor: int | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = (
            os.open(path, flags)
            if parent_descriptor is None
            else os.open(
                _required_entry_name(entry_name),
                flags,
                dir_fd=parent_descriptor,
            )
        )
        handle_value = os.fstat(descriptor)
        path_value = (
            path.stat(follow_symlinks=False)
            if parent_descriptor is None
            else os.stat(
                _required_entry_name(entry_name),
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        if (
            not stat.S_ISDIR(handle_value.st_mode)
            or not stat.S_ISDIR(path_value.st_mode)
            or stat.S_ISLNK(path_value.st_mode)
            or _is_reparse(path_value)
            or _file_id(handle_value) != expected_file_id
            or _file_id(path_value) != expected_file_id
            or _stat_identity(handle_value) != _stat_identity(path_value)
        ):
            raise RoundtripError(
                RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
                "A native artifact directory handle is unsafe.",
            )
        return _OpenDirectory(
            path=path,
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            entry_name=entry_name,
            backend="posix",
            file_id=expected_file_id,
            identity=_stat_identity(path_value),
        )
    except BaseException:
        if descriptor is not None:
            _close_descriptor(descriptor)
        raise


def _directory_binding_still_stable(directory: _OpenDirectory) -> bool:
    try:
        if directory.backend == "windows":
            reject_link_or_reparse_components(directory.path)
            path_value = directory.path.stat(follow_symlinks=False)
        elif directory.parent_descriptor is None:
            reject_link_or_reparse_components(directory.path)
            path_value = directory.path.stat(follow_symlinks=False)
        else:
            path_value = os.stat(
                _required_entry_name(directory.entry_name),
                dir_fd=directory.parent_descriptor,
                follow_symlinks=False,
            )
        if (
            not stat.S_ISDIR(path_value.st_mode)
            or stat.S_ISLNK(path_value.st_mode)
            or _is_reparse(path_value)
            or _file_id(path_value) != directory.file_id
            or _stat_identity(path_value) != directory.identity
        ):
            return False
        if directory.backend == "windows":
            information = _windows_directory_information(
                _windows_kernel32(),
                directory.descriptor,
            )
            return bool(
                information.file_attributes & 0x00000010
                and not information.file_attributes & 0x00000400
                and _windows_file_id(information) == directory.file_id
            )
        handle_value = os.fstat(directory.descriptor)
        return bool(
            stat.S_ISDIR(handle_value.st_mode)
            and _file_id(handle_value) == directory.file_id
            and _stat_identity(handle_value) == directory.identity
        )
    except (OSError, RoundtripError):
        return False


def _windows_kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_directory_information(
    kernel32: Any,
    descriptor: int,
) -> _WindowsFileInformation:
    information = _WindowsFileInformation()
    if not kernel32.GetFileInformationByHandle(
        wintypes.HANDLE(descriptor),
        ctypes.byref(information),
    ):
        _raise_windows_error()
    return information


def _windows_file_id(
    information: _WindowsFileInformation,
) -> tuple[int, int]:
    index = (
        int(information.file_index_high) << 32
    ) | int(information.file_index_low)
    return int(information.volume_serial_number), index


def _raise_windows_error() -> None:
    error = ctypes.get_last_error()
    raise OSError(error, ctypes.FormatError(error))


def _close_tree_snapshot(snapshot: _ArtifactTreeSnapshot) -> None:
    for directory in snapshot.directories:
        _close_directory_binding(directory)


def _close_directory_binding(directory: _OpenDirectory) -> None:
    if directory.backend == "windows":
        _close_windows_handle(directory.descriptor)
    else:
        _close_descriptor(directory.descriptor)


def _close_windows_handle(descriptor: int) -> None:
    try:
        _windows_kernel32().CloseHandle(wintypes.HANDLE(descriptor))
    except OSError:
        pass


def _file_id(value: Any) -> tuple[int, int]:
    return int(value.st_dev), int(value.st_ino)


def _descriptor_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _stat_identity(value: Any) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
        int(value.st_nlink),
    )


def _strict_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strict_bool(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _is_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_mapping_sha256(value: Mapping[str, Any]) -> str | None:
    try:
        return _canonical_payload_sha256(dict(value))
    except (TypeError, ValueError):
        return None


def _canonical_payload_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "CASTEP_ENERGY_ARTIFACT_POLICY_SCHEMA",
    "CastepEnergyArtifactBinding",
    "CastepEnergyArtifactPolicyReceipt",
    "validate_castep_energy_artifact_policy",
]
