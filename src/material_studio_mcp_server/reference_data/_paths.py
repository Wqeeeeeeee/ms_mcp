"""Root-confined filesystem primitives for immutable reference evidence."""

from __future__ import annotations

import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

from .contracts import validate_relative_store_path
from .errors import (
    ArtifactCorruptionError,
    PublicationConflictError,
    StoreConfinementError,
)


_PROCESS_STORE_LOCK = threading.RLock()
_LOCK_RELATIVE_PATH = "control/store.lock"
_LOCK_CONTENT = b"\x00"


def _has_reparse_attribute(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _has_multiple_hard_links(file_stat: os.stat_result) -> bool:
    return getattr(file_stat, "st_nlink", 1) != 1


def _is_link_or_reparse(path: Path) -> bool:
    file_stat = path.lstat()
    return stat.S_ISLNK(file_stat.st_mode) or _has_reparse_attribute(file_stat)


def _root_argument_path(reference_store_root: str | os.PathLike[str]) -> Path:
    try:
        raw_value = os.fspath(reference_store_root)
    except TypeError as exc:
        raise TypeError("reference_store_root must be a filesystem path") from exc
    if not isinstance(raw_value, str) or not raw_value or "\x00" in raw_value:
        raise StoreConfinementError("reference store root is malformed")
    supplied = Path(raw_value)
    if ".." in supplied.parts:
        raise StoreConfinementError("reference store root must not contain traversal")
    return supplied.absolute()


def _assert_existing_path_chain_link_free(path: Path) -> None:
    """Reject links, reparse points, and non-directory ancestors."""

    chain = (*reversed(path.parents), path)
    for component in chain:
        try:
            file_stat = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StoreConfinementError(
                "reference store path component cannot be inspected"
            ) from exc
        if stat.S_ISLNK(file_stat.st_mode) or _has_reparse_attribute(file_stat):
            raise StoreConfinementError(
                "reference store path contains a link or reparse-point ancestor"
            )
        if component != path and not stat.S_ISDIR(file_stat.st_mode):
            raise StoreConfinementError(
                "reference store path contains a non-directory ancestor"
            )


def prepare_store_root(
    reference_store_root: str | os.PathLike[str],
    *,
    create: bool,
) -> Path:
    """Create only an explicitly requested root, then verify its exact type."""

    root = _root_argument_path(reference_store_root)
    _assert_existing_path_chain_link_free(root)
    if not root.exists() and not root.is_symlink():
        if not create:
            raise StoreConfinementError("reference store root does not exist")
        try:
            root.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            pass
    _assert_existing_path_chain_link_free(root)
    try:
        file_stat = root.lstat()
    except OSError as exc:
        raise StoreConfinementError("reference store root cannot be inspected") from exc
    if (
        not stat.S_ISDIR(file_stat.st_mode)
        or stat.S_ISLNK(file_stat.st_mode)
        or _has_reparse_attribute(file_stat)
    ):
        raise StoreConfinementError(
            "reference store root must be a real directory, not a link or reparse point"
        )
    root.resolve(strict=True)
    return root


def _assert_resolved_within(root: Path, candidate: Path) -> None:
    _assert_existing_path_chain_link_free(root)
    try:
        root_resolved = root.resolve(strict=True)
        candidate_resolved = candidate.resolve(strict=False)
        common = Path(os.path.commonpath((root_resolved, candidate_resolved)))
    except (OSError, ValueError) as exc:
        raise StoreConfinementError("store path resolution failed closed") from exc
    if os.path.normcase(str(common)) != os.path.normcase(str(root_resolved)):
        raise StoreConfinementError("store path resolves outside the reference root")


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    try:
        validate_relative_store_path(relative_path, label="store path")
    except (TypeError, ValueError) as exc:
        raise StoreConfinementError("store path is not a confined relative path") from exc
    return PurePosixPath(relative_path).parts


def ensure_directory(root: Path, relative_directory: str) -> Path:
    """Create fixed internal directories one component at a time, link-free."""

    current = root
    for component in _relative_parts(relative_directory):
        current = current / component
        _assert_resolved_within(root, current)
        if current.exists() or current.is_symlink():
            try:
                file_stat = current.lstat()
            except OSError as exc:
                raise StoreConfinementError("store directory cannot be inspected") from exc
            if (
                not stat.S_ISDIR(file_stat.st_mode)
                or stat.S_ISLNK(file_stat.st_mode)
                or _has_reparse_attribute(file_stat)
            ):
                raise StoreConfinementError(
                    "store directory component is a link, reparse point, or unexpected type"
                )
        else:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            file_stat = current.lstat()
            if not stat.S_ISDIR(file_stat.st_mode) or _is_link_or_reparse(current):
                raise StoreConfinementError("new store directory failed type verification")
        _assert_resolved_within(root, current)
    return current


def confined_file_path(root: Path, relative_path: str) -> Path:
    """Resolve one internal file path after verifying every existing ancestor."""

    parts = _relative_parts(relative_path)
    current = root
    for component in parts[:-1]:
        current = current / component
        _assert_resolved_within(root, current)
        try:
            file_stat = current.lstat()
        except OSError as exc:
            raise StoreConfinementError("store parent directory is missing or unsafe") from exc
        if (
            not stat.S_ISDIR(file_stat.st_mode)
            or stat.S_ISLNK(file_stat.st_mode)
            or _has_reparse_attribute(file_stat)
        ):
            raise StoreConfinementError("store parent is a link or unexpected file type")
    candidate = root.joinpath(*parts)
    _assert_resolved_within(root, candidate)
    if candidate.exists() or candidate.is_symlink():
        file_stat = candidate.lstat()
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_ISLNK(file_stat.st_mode)
            or _has_reparse_attribute(file_stat)
            or _has_multiple_hard_links(file_stat)
        ):
            raise StoreConfinementError(
                "store target is a link, reparse point, hard link, or unexpected file type"
            )
    return candidate


def ensure_parent_directory(root: Path, relative_path: str) -> None:
    parts = _relative_parts(relative_path)
    ensure_directory(root, PurePosixPath(*parts[:-1]).as_posix())
    confined_file_path(root, relative_path)


def read_confined_file(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int,
    expected_size: int | None = None,
) -> bytes:
    """Read one verified regular file and reject identity changes around the read."""

    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative strict integer")
    if expected_size is not None and (
        type(expected_size) is not int
        or expected_size < 0
        or expected_size > max_bytes
    ):
        raise ValueError("expected_size must be within the bounded read limit")
    path = confined_file_path(root, relative_path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ArtifactCorruptionError("published evidence file is missing or unreadable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _has_reparse_attribute(before)
            or _has_multiple_hard_links(before)
        ):
            raise StoreConfinementError("opened evidence is not a regular file")
        if before.st_size > max_bytes:
            raise ArtifactCorruptionError("published evidence exceeds its read limit")
        if expected_size is not None and before.st_size != expected_size:
            raise ArtifactCorruptionError("published evidence has an unexpected byte count")
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = max_bytes - total
            chunk = os.read(descriptor, min(1024 * 1024, remaining + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ArtifactCorruptionError("published evidence exceeds its read limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_nlink,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_nlink,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ArtifactCorruptionError("published evidence changed during verification")
    confined_file_path(root, relative_path)
    return b"".join(chunks)


def create_confined_file(root: Path, relative_path: str, content: bytes) -> None:
    """Create one immutable file exactly once; never truncate or replace it."""

    if type(content) is not bytes:
        raise TypeError("publication content must be exact bytes")
    path = confined_file_path(root, relative_path)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PublicationConflictError("create-only publication target already exists") from exc
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short write while publishing immutable evidence")
            offset += written
        os.fsync(descriptor)
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or _has_reparse_attribute(file_stat)
            or _has_multiple_hard_links(file_stat)
        ):
            raise StoreConfinementError("published target is not a regular file")
    finally:
        os.close(descriptor)
    if read_confined_file(
        root,
        relative_path,
        max_bytes=len(content),
        expected_size=len(content),
    ) != content:
        raise ArtifactCorruptionError("newly published evidence failed exact reread")


def _open_store_lock(root: Path, *, create: bool) -> int:
    if create:
        ensure_parent_directory(root, _LOCK_RELATIVE_PATH)
    path = confined_file_path(root, _LOCK_RELATIVE_PATH)
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise StoreConfinementError("reference store lock is unavailable") from exc
    file_stat = os.fstat(descriptor)
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or _has_reparse_attribute(file_stat)
        or _has_multiple_hard_links(file_stat)
    ):
        os.close(descriptor)
        raise StoreConfinementError("reference store lock has an unsafe file type")
    return descriptor


def _initialize_and_verify_locked_descriptor(descriptor: int, *, create: bool) -> None:
    file_stat = os.fstat(descriptor)
    if file_stat.st_size == 0 and create:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, _LOCK_CONTENT)
        os.fsync(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.read(descriptor, 2) != _LOCK_CONTENT:
        raise StoreConfinementError("reference store lock content is invalid")


def _lock_descriptor(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def store_transaction(root: Path, *, create_lock: bool) -> Iterator[None]:
    """Serialize all publication and verification in one explicit store root."""

    with _PROCESS_STORE_LOCK:
        descriptor = _open_store_lock(root, create=create_lock)
        locked = False
        try:
            _lock_descriptor(descriptor)
            locked = True
            _initialize_and_verify_locked_descriptor(descriptor, create=create_lock)
            confined_file_path(root, _LOCK_RELATIVE_PATH)
            yield
        finally:
            try:
                if locked:
                    _unlock_descriptor(descriptor)
            finally:
                os.close(descriptor)


__all__ = [
    "confined_file_path",
    "create_confined_file",
    "ensure_parent_directory",
    "prepare_store_root",
    "read_confined_file",
    "store_transaction",
]
