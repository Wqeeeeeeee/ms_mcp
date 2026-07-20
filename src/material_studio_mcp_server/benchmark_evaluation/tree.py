"""Bounded candidate-tree identity snapshots and immutability guard."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .contracts import CandidateTreeSummary
from .errors import BenchmarkEvaluationError, EvaluationReason
from .paths import (
    path_has_only_default_data_stream,
    resolve_root_relative_path,
    validate_relative_posix_path,
)


MAX_TREE_FILES = 4096
MAX_TREE_DIRECTORIES = 1024
MAX_TREE_DEPTH = 32
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TREE_BYTES = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    relative_path: str
    kind: str
    size: int
    digest_sha256: str | None
    device: int
    inode: int
    mode: int
    links: int
    modified_ns: int
    changed_ns: int
    file_attributes: int


@dataclass(frozen=True, slots=True)
class CandidateTreeSnapshot:
    root_device: int
    root_inode: int
    entries: tuple[_TreeEntry, ...]
    summary: CandidateTreeSummary


def _failure(reason: EvaluationReason) -> BenchmarkEvaluationError:
    return BenchmarkEvaluationError(reason)


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _is_reparse(value: os.stat_result) -> bool:
    return bool(int(getattr(value, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE)


def _hash_open_file(handle: BinaryIO, size: int) -> str:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = handle.read(min(_READ_CHUNK_BYTES, remaining))
        if not block:
            raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
        digest.update(block)
        remaining -= len(block)
    if handle.read(1):
        raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
    return digest.hexdigest()


def _snapshot_file(path: Path, relative_path: str) -> _TreeEntry:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink() or _is_reparse(before):
            raise ValueError
        if not path_has_only_default_data_stream(path):
            raise ValueError
        if before.st_size < 0 or before.st_size > MAX_FILE_BYTES:
            raise ValueError
        if before.st_nlink != 1:
            raise ValueError
        with path.open("rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if _identity(opened) != _identity(before):
                raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
            digest = _hash_open_file(handle, int(before.st_size))
            finished = os.fstat(handle.fileno())
        after = path.lstat()
        if _identity(before) != _identity(finished) or _identity(before) != _identity(after):
            raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
        return _TreeEntry(
            relative_path=relative_path,
            kind="file",
            size=int(before.st_size),
            digest_sha256=digest,
            device=int(before.st_dev),
            inode=int(before.st_ino),
            mode=int(before.st_mode),
            links=int(before.st_nlink),
            modified_ns=int(before.st_mtime_ns),
            changed_ns=int(before.st_ctime_ns),
            file_attributes=int(getattr(before, "st_file_attributes", 0)),
        )
    except BenchmarkEvaluationError:
        raise
    except (OSError, ValueError):
        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID) from None


def _snapshot_directory(path: Path, relative_path: str) -> _TreeEntry:
    try:
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or path.is_symlink() or _is_reparse(value):
            raise ValueError
        if not path_has_only_default_data_stream(path):
            raise ValueError
        return _TreeEntry(
            relative_path=relative_path,
            kind="directory",
            size=0,
            digest_sha256=None,
            device=int(value.st_dev),
            inode=int(value.st_ino),
            mode=int(value.st_mode),
            links=int(value.st_nlink),
            modified_ns=int(value.st_mtime_ns),
            changed_ns=int(value.st_ctime_ns),
            file_attributes=int(getattr(value, "st_file_attributes", 0)),
        )
    except (OSError, ValueError):
        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID) from None


def _entry_payload(entry: _TreeEntry) -> dict[str, object]:
    return {
        "changed_ns": entry.changed_ns,
        "device": entry.device,
        "digest_sha256": entry.digest_sha256,
        "file_attributes": entry.file_attributes,
        "inode": entry.inode,
        "kind": entry.kind,
        "links": entry.links,
        "mode": entry.mode,
        "modified_ns": entry.modified_ns,
        "relative_path": entry.relative_path,
        "size": entry.size,
    }


def snapshot_candidate_tree(root: Path) -> CandidateTreeSnapshot:
    """Hash a complete bounded tree without returning paths or bytes publicly."""

    try:
        root = Path(root)
        if not root.is_absolute():
            raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
        root_entry = _snapshot_directory(root, "")
        resolved = root.resolve(strict=True)
        if os.path.normcase(str(resolved)) != os.path.normcase(str(root)):
            raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
        entries: list[_TreeEntry] = [root_entry]
        queue: list[tuple[Path, str, int]] = [(root, "", 0)]
        file_count = 0
        directory_count = 1
        total_bytes = 0
        while queue:
            directory, relative_directory, depth = queue.pop(0)
            if depth > MAX_TREE_DEPTH:
                raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
            entry_budget = (
                MAX_TREE_FILES
                - file_count
                + MAX_TREE_DIRECTORIES
                - directory_count
            )
            child_buffer = []
            with os.scandir(directory) as iterator:
                for child in iterator:
                    if len(child_buffer) >= entry_budget:
                        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
                    child_buffer.append(child)
            children = sorted(
                child_buffer, key=lambda item: (item.name.casefold(), item.name)
            )
            for child in children:
                child_relative = (
                    f"{relative_directory}/{child.name}"
                    if relative_directory
                    else child.name
                )
                validate_relative_posix_path(child_relative)
                child_path = directory / child.name
                child_stat = child_path.lstat()
                if child_path.is_symlink() or _is_reparse(child_stat):
                    raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
                if stat.S_ISDIR(child_stat.st_mode):
                    directory_count += 1
                    if directory_count > MAX_TREE_DIRECTORIES:
                        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
                    entries.append(_snapshot_directory(child_path, child_relative))
                    queue.append((child_path, child_relative, depth + 1))
                elif stat.S_ISREG(child_stat.st_mode):
                    file_count += 1
                    if file_count > MAX_TREE_FILES:
                        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
                    entry = _snapshot_file(child_path, child_relative)
                    total_bytes += entry.size
                    if total_bytes > MAX_TREE_BYTES:
                        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
                    entries.append(entry)
                else:
                    raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
        if file_count < 1 or total_bytes < 1:
            raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID)
        ordered = tuple(sorted(entries, key=lambda item: (item.relative_path, item.kind)))
        payload = {
            "entries": [_entry_payload(entry) for entry in ordered],
            "profile": "candidate_tree_identity_v1",
            "root_device": root_entry.device,
            "root_inode": root_entry.inode,
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        return CandidateTreeSnapshot(
            root_device=root_entry.device,
            root_inode=root_entry.inode,
            entries=ordered,
            summary=CandidateTreeSummary(
                digest_sha256=digest,
                file_count=file_count,
                directory_count=directory_count,
                total_bytes=total_bytes,
            ),
        )
    except BenchmarkEvaluationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _failure(EvaluationReason.CANDIDATE_TREE_INVALID) from None


def snapshots_match(left: CandidateTreeSnapshot, right: CandidateTreeSnapshot) -> bool:
    return (
        left.root_device == right.root_device
        and left.root_inode == right.root_inode
        and left.entries == right.entries
        and left.summary == right.summary
    )


def read_candidate_artifact(
    root: Path,
    relative_path: str,
    expected_sha256: str,
    snapshot: CandidateTreeSnapshot,
) -> bytes:
    """Read one snapshotted file and bind it to the immutable tree identity."""

    path = resolve_root_relative_path(root, relative_path)
    relative = validate_relative_posix_path(relative_path).as_posix()
    matching = tuple(
        entry
        for entry in snapshot.entries
        if entry.kind == "file" and entry.relative_path == relative
    )
    if len(matching) != 1 or matching[0].digest_sha256 != expected_sha256:
        raise _failure(EvaluationReason.ARTIFACT_IDENTITY_MISMATCH)
    try:
        before = path.lstat()
        expected = matching[0]
        if (
            int(before.st_dev) != expected.device
            or int(before.st_ino) != expected.inode
            or int(before.st_size) != expected.size
        ):
            raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
        with path.open("rb", buffering=0) as handle:
            opened = os.fstat(handle.fileno())
            if _identity(opened) != _identity(before):
                raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
            payload = handle.read()
            finished = os.fstat(handle.fileno())
        after = path.lstat()
        if _identity(before) != _identity(finished) or _identity(before) != _identity(after):
            raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
    except BenchmarkEvaluationError:
        raise
    except OSError:
        raise _failure(EvaluationReason.ARTIFACT_IDENTITY_MISMATCH) from None
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise _failure(EvaluationReason.ARTIFACT_IDENTITY_MISMATCH)
    if len(payload) != expected.size:
        raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
    return bytes(payload)


class CandidateTreeGuard:
    """Recheck a candidate tree at every evaluator stage and on exit."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self.before = snapshot_candidate_tree(self._root)
        self.after: CandidateTreeSnapshot | None = None

    def checkpoint(self) -> CandidateTreeSnapshot:
        current = snapshot_candidate_tree(self._root)
        if not snapshots_match(self.before, current):
            raise _failure(EvaluationReason.CANDIDATE_TREE_CHANGED)
        self.after = current
        return current

    def __enter__(self) -> "CandidateTreeGuard":
        self.checkpoint()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            self.checkpoint()
        except BenchmarkEvaluationError:
            raise
        return False


__all__ = [
    "CandidateTreeGuard",
    "CandidateTreeSnapshot",
    "MAX_FILE_BYTES",
    "MAX_TREE_BYTES",
    "MAX_TREE_DEPTH",
    "MAX_TREE_DIRECTORIES",
    "MAX_TREE_FILES",
    "read_candidate_artifact",
    "snapshot_candidate_tree",
    "snapshots_match",
]
