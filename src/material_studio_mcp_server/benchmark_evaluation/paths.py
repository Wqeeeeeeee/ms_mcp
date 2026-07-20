"""Fail-closed lexical and physical path isolation."""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Iterable

from .contracts import EvaluationRoots
from .errors import BenchmarkEvaluationError, EvaluationReason


_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RESERVED_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_FORBIDDEN_ROOT_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\??\\", "\\DEVICE\\")


def _fixed_failure(reason: EvaluationReason) -> BenchmarkEvaluationError:
    return BenchmarkEvaluationError(reason)


def validate_relative_posix_path(value: str) -> PurePosixPath:
    """Validate a canonical relative path with Windows aliases closed."""

    try:
        if type(value) is not str or not value or len(value) > 512:
            raise ValueError
        if value != value.strip() or "\\" in value or "\x00" in value:
            raise ValueError
        if unicodedata.normalize("NFKC", value) != value:
            raise ValueError
        if value.startswith(("/", "//")) or "//" in value or ":" in value:
            raise ValueError
        if any(character in '<>"|?*' for character in value):
            raise ValueError
        if any(ord(character) < 32 for character in value):
            raise ValueError
        path = PurePosixPath(value)
        if path.is_absolute() or path.as_posix() != value:
            raise ValueError
        if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError
        for part in path.parts:
            if part.endswith((" ", ".")):
                raise ValueError
            if re.search(r"~[0-9]+(?:\.|$)", part, re.IGNORECASE):
                raise ValueError
            device_stem = part.split(".", 1)[0].upper()
            if device_stem in _RESERVED_DEVICE_NAMES:
                raise ValueError
        return path
    except (TypeError, ValueError):
        raise _fixed_failure(EvaluationReason.ARTIFACT_ROOT_BINDING_INVALID) from None


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = int(getattr(stat_result, "st_file_attributes", 0))
    return bool(attributes & _REPARSE_ATTRIBUTE)


def path_has_only_default_data_stream(path: Path) -> bool:
    """Reject hidden NTFS alternate streams that directory walks cannot see."""

    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        class StreamData(ctypes.Structure):
            _fields_ = (
                ("stream_size", ctypes.c_longlong),
                ("stream_name", wintypes.WCHAR * 296),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        find_first = kernel32.FindFirstStreamW
        find_first.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(StreamData),
            wintypes.DWORD,
        )
        find_first.restype = wintypes.HANDLE
        find_next = kernel32.FindNextStreamW
        find_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(StreamData))
        find_next.restype = wintypes.BOOL
        data = StreamData()
        handle = find_first(str(path), 0, ctypes.byref(data), 0)
        if handle == wintypes.HANDLE(-1).value:
            return ctypes.get_last_error() == 38 and path.is_dir()
        names: list[str] = []
        try:
            names.append(data.stream_name)
            while find_next(handle, ctypes.byref(data)):
                names.append(data.stream_name)
            if ctypes.get_last_error() not in {18, 38}:
                return False
        finally:
            kernel32.FindClose(handle)
        return bool(names) and all(name == "::$DATA" for name in names)
    except (OSError, TypeError, ValueError):
        return False


def _path_identity(path: Path) -> tuple[int, int, int]:
    try:
        value = path.stat(follow_symlinks=False)
    except (OSError, ValueError):
        raise _fixed_failure(EvaluationReason.ISOLATION_ROOT_INVALID) from None
    return (int(value.st_dev), int(value.st_ino), stat.S_IFMT(value.st_mode))


def _lexical_ancestors(path: Path) -> tuple[Path, ...]:
    anchor = Path(path.anchor)
    current = anchor
    ancestors: list[Path] = []
    for part in path.parts[1:]:
        current = current / part
        ancestors.append(current)
    return tuple(ancestors)


def _validate_root(path: Path) -> Path:
    try:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError
        original = str(path)
        upper = original.upper()
        if upper.startswith(_FORBIDDEN_ROOT_PREFIXES):
            raise ValueError
        if original.startswith("\\\\") or path.drive.startswith("\\\\"):
            raise ValueError
        for part in path.parts[1:]:
            if (
                unicodedata.normalize("NFKC", part) != part
                or part.endswith((" ", "."))
                or ":" in part
                or re.search(r"~[0-9]+(?:\.|$)", part, re.IGNORECASE)
            ):
                raise ValueError
        if os.name == "nt":
            from ctypes import create_unicode_buffer, windll

            root_text = f"{path.drive}\\"
            if windll.kernel32.GetDriveTypeW(root_text) != 3:
                raise ValueError
            target = create_unicode_buffer(32768)
            if not windll.kernel32.QueryDosDeviceW(path.drive, target, len(target)):
                raise ValueError
            if target.value.upper().startswith(("\\??\\", "\\DOSDEVICES\\")):
                raise ValueError
            canonical_text = create_unicode_buffer(32768)
            if not windll.kernel32.GetLongPathNameW(
                original, canonical_text, len(canonical_text)
            ):
                raise ValueError
            if os.path.normpath(canonical_text.value) != os.path.normpath(original):
                raise ValueError
        normalized = Path(os.path.normpath(original))
        if not normalized.is_absolute() or not normalized.exists() or not normalized.is_dir():
            raise ValueError
        for ancestor in _lexical_ancestors(normalized):
            item_stat = ancestor.lstat()
            if ancestor.is_symlink() or _is_reparse(item_stat):
                raise ValueError
            if not path_has_only_default_data_stream(ancestor):
                raise ValueError
        resolved = normalized.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError
        root_stat = resolved.stat(follow_symlinks=False)
        if _is_reparse(root_stat):
            raise ValueError
        return resolved
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _fixed_failure(EvaluationReason.ISOLATION_ROOT_INVALID) from None


def _folded_path(path: Path) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _paths_collide(left: Path, right: Path) -> bool:
    left_text = _folded_path(left)
    right_text = _folded_path(right)
    if left_text == right_text:
        return True
    if left_text.startswith(right_text) or right_text.startswith(left_text):
        return True
    try:
        if os.path.commonpath((str(left), str(right))).casefold() in {
            str(left).casefold(),
            str(right).casefold(),
        }:
            return True
    except ValueError:
        pass
    return _path_identity(left) == _path_identity(right)


def verify_isolation_roots(roots: EvaluationRoots) -> EvaluationRoots:
    """Return canonical roots only when all three are physically disjoint."""

    try:
        if not isinstance(roots, EvaluationRoots):
            raise TypeError
        reference = _validate_root(Path(roots.reference_root))
        candidate = _validate_root(Path(roots.candidate_root))
        evaluator = _validate_root(Path(roots.evaluator_output_root))
        pairs = (
            (reference, candidate),
            (reference, evaluator),
            (candidate, evaluator),
        )
        if any(_paths_collide(left, right) for left, right in pairs):
            raise _fixed_failure(EvaluationReason.ISOLATION_ROOTS_NOT_DISJOINT)
        return EvaluationRoots(
            reference_root=reference,
            candidate_root=candidate,
            evaluator_output_root=evaluator,
        )
    except BenchmarkEvaluationError:
        raise
    except (OSError, TypeError, ValueError):
        raise _fixed_failure(EvaluationReason.ISOLATION_ROOT_INVALID) from None


def declared_roots_are_disjoint(values: Iterable[str]) -> bool:
    try:
        paths = tuple(validate_relative_posix_path(value) for value in values)
    except BenchmarkEvaluationError:
        return False
    if len(paths) != 3:
        return False
    folded = tuple(path.as_posix().casefold() for path in paths)
    for index, left in enumerate(folded):
        for right in folded[index + 1 :]:
            if left == right or left.startswith(right) or right.startswith(left):
                return False
            left_parts = PurePosixPath(left).parts
            right_parts = PurePosixPath(right).parts
            shortest = min(len(left_parts), len(right_parts))
            if left_parts[:shortest] == right_parts[:shortest]:
                return False
    return True


def relative_path_under_declared_root(path: str, declared_root: str) -> PurePosixPath:
    artifact = validate_relative_posix_path(path)
    root = validate_relative_posix_path(declared_root)
    artifact_parts = tuple(part.casefold() for part in artifact.parts)
    root_parts = tuple(part.casefold() for part in root.parts)
    if len(artifact_parts) <= len(root_parts) or artifact_parts[: len(root_parts)] != root_parts:
        raise _fixed_failure(EvaluationReason.ARTIFACT_ROOT_BINDING_INVALID)
    return PurePosixPath(*artifact.parts[len(root.parts) :])


def resolve_root_relative_path(
    root: Path,
    relative_path: str,
    *,
    require_file: bool = True,
) -> Path:
    relative = validate_relative_posix_path(relative_path)
    canonical_root = _validate_root(root)
    candidate = canonical_root.joinpath(*relative.parts)
    try:
        current = canonical_root
        for part in relative.parts:
            with os.scandir(current) as iterator:
                exact_names = tuple(item.name for item in iterator if item.name == part)
            if exact_names != (part,):
                raise ValueError
            current = current / exact_names[0]
            item_stat = current.lstat()
            if current.is_symlink() or _is_reparse(item_stat):
                raise ValueError
            if not path_has_only_default_data_stream(current):
                raise ValueError
        resolved = candidate.resolve(strict=True)
        if resolved.parent != canonical_root and canonical_root not in resolved.parents:
            raise ValueError
        if require_file and not resolved.is_file():
            raise ValueError
        if not require_file and not resolved.exists():
            raise ValueError
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise _fixed_failure(EvaluationReason.ARTIFACT_ROOT_BINDING_INVALID) from None


def resolve_declared_artifact(
    *,
    actual_root: Path,
    declared_root: str,
    artifact_path: str,
) -> Path:
    relative = relative_path_under_declared_root(artifact_path, declared_root)
    return resolve_root_relative_path(actual_root, relative.as_posix())


__all__ = [
    "declared_roots_are_disjoint",
    "path_has_only_default_data_stream",
    "relative_path_under_declared_root",
    "resolve_declared_artifact",
    "resolve_root_relative_path",
    "validate_relative_posix_path",
    "verify_isolation_roots",
]
