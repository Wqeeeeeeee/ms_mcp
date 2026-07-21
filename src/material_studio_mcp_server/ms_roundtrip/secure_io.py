"""Confined path, immutable read, digest, and atomic publication helpers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import RunArtifactDigest
from .errors import RoundtripError, RoundtripErrorCode


MAX_CIF_BYTES = 16 * 1024 * 1024
MAX_RUNNER_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    payload: bytes
    sha256: str
    byte_count: int
    identity: tuple[int, int, int, int, int, int]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json_bytes(value: Any, *, trailing_newline: bool = False) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return encoded + (b"\n" if trailing_newline else b"")


def _is_reparse(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def reject_link_or_reparse_components(path: Path) -> None:
    absolute = _absolute_without_resolution(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            value = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise RoundtripError(
                RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
                "A path component could not be inspected safely.",
            ) from exc
        if stat.S_ISLNK(value.st_mode) or _is_reparse(value):
            raise RoundtripError(
                RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
                "Symlink and reparse-point path components are not allowed.",
            )


def _reject_alternate_data_stream(path: Path) -> None:
    if os.name == "nt" and ":" in path.name:
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "Alternate data stream paths are not allowed.",
        )


def resolve_existing_regular_file(
    path: Path,
    *,
    code: RoundtripErrorCode,
) -> Path:
    _reject_alternate_data_stream(path)
    reject_link_or_reparse_components(path)
    try:
        resolved = path.expanduser().resolve(strict=True)
        value = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise RoundtripError(code, "The required file is unavailable.") from exc
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or _is_reparse(value):
        raise RoundtripError(code, "The required path is not a regular file.")
    return resolved


def resolve_existing_directory(path: Path) -> Path:
    _reject_alternate_data_stream(path)
    reject_link_or_reparse_components(path)
    try:
        resolved = path.expanduser().resolve(strict=True)
        value = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "The output root is unavailable.",
        ) from exc
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode) or _is_reparse(value):
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "The output root is not a regular directory.",
        )
    return resolved


def ensure_inside(root: Path, path: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "A run artifact escaped the confined run root.",
        ) from exc


def stable_read_file(
    path: Path,
    *,
    expected_sha256: str | None = None,
    max_bytes: int = MAX_CIF_BYTES,
    require_single_link: bool = True,
    code: RoundtripErrorCode = RoundtripErrorCode.INPUT_IDENTITY_MISMATCH,
) -> FileSnapshot:
    resolved = resolve_existing_regular_file(path, code=code)
    try:
        before = resolved.stat(follow_symlinks=False)
        if (
            before.st_size < 1
            or before.st_size > max_bytes
            or (require_single_link and before.st_nlink != 1)
        ):
            raise ValueError
        with resolved.open("rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size != before.st_size
            ):
                raise ValueError
            payload = handle.read()
            finished = os.fstat(handle.fileno())
        after = resolved.stat(follow_symlinks=False)
        identity = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_size),
            int(before.st_mtime_ns),
            int(before.st_ctime_ns),
            int(before.st_nlink),
        )
        if identity != (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_size),
            int(after.st_mtime_ns),
            int(after.st_ctime_ns),
            int(after.st_nlink),
        ) or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ) != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
        ):
            raise ValueError
        digest = sha256_bytes(payload)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError
        return FileSnapshot(
            path=resolved,
            payload=bytes(payload),
            sha256=digest,
            byte_count=len(payload),
            identity=identity,
        )
    except (OSError, ValueError) as exc:
        raise RoundtripError(code, "File identity or digest verification failed.") from exc


def snapshot_unchanged(before: FileSnapshot, after: FileSnapshot) -> bool:
    return (
        before.path == after.path
        and before.sha256 == after.sha256
        and before.byte_count == after.byte_count
        and before.identity == after.identity
    )


def native_text_bytes(value: str) -> bytes:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", os.linesep).encode("utf-8")


def relative_run_path(run_root: Path, path: Path) -> str:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
            "A runner artifact is unavailable.",
        ) from exc
    ensure_inside(run_root, resolved)
    relative = resolved.relative_to(run_root).as_posix()
    if not relative or relative.startswith("../"):
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
            "A runner artifact path is invalid.",
        )
    return relative


def digest_run_artifact(
    *,
    run_root: Path,
    path: Path,
    role: str,
    expected_sha256: str | None = None,
) -> RunArtifactDigest:
    relative = relative_run_path(run_root, path)
    snapshot = stable_read_file(
        path,
        expected_sha256=expected_sha256,
        max_bytes=MAX_RUNNER_ARTIFACT_BYTES,
        code=RoundtripErrorCode.RUNNER_ARTIFACT_INVALID,
    )
    return RunArtifactDigest(
        role=role,
        relative_path=relative,
        sha256=snapshot.sha256,
        byte_count=snapshot.byte_count,
    )


def atomic_write_json(path: Path, payload: Any) -> FileSnapshot:
    destination = path.expanduser().resolve(strict=False)
    reject_link_or_reparse_components(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise RoundtripError(
            RoundtripErrorCode.RECEIPT_PERSISTENCE_FAILED,
            "The result receipt already exists.",
        )
    content = canonical_json_bytes(payload, trailing_newline=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb", buffering=0) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists():
            raise FileExistsError(destination)
        os.replace(temporary, destination)
        return stable_read_file(
            destination,
            expected_sha256=sha256_bytes(content),
            max_bytes=max(len(content), 1),
            code=RoundtripErrorCode.RECEIPT_PERSISTENCE_FAILED,
        )
    except RoundtripError:
        raise
    except OSError as exc:
        raise RoundtripError(
            RoundtripErrorCode.RECEIPT_PERSISTENCE_FAILED,
            "The result receipt could not be published atomically.",
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "FileSnapshot",
    "MAX_CIF_BYTES",
    "MAX_RUNNER_ARTIFACT_BYTES",
    "atomic_write_json",
    "canonical_json_bytes",
    "digest_run_artifact",
    "ensure_inside",
    "native_text_bytes",
    "reject_link_or_reparse_components",
    "relative_run_path",
    "resolve_existing_directory",
    "resolve_existing_regular_file",
    "sha256_bytes",
    "sha256_text",
    "snapshot_unchanged",
    "stable_read_file",
]
