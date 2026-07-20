from __future__ import annotations

import hashlib
import ctypes
import os
import stat
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from material_studio_mcp_server.reference_data import (
    MAX_RAW_ARTIFACT_BYTES,
    ArtifactCorruptionError,
    DigestMismatchError,
    PartialPublicationError,
    PublicationConflictError,
    RawArtifactPolicyError,
    StoreConfinementError,
    build_reference_manifest,
    canonical_sha256,
    fingerprint_raw_bytes,
    ingest_reference,
    verify_ingestion,
)
from material_studio_mcp_server.reference_data.contracts import (
    IngestionReceipt,
    manifest_relative_path,
)
from material_studio_mcp_server.reference_data.manifest import (
    manifest_bytes,
    source_record_bytes,
)

from conftest import SYNTHETIC_RAW_BYTES


ROOT = Path(__file__).resolve().parents[2]


def _planned_paths(raw_bytes: bytes, source: object) -> tuple[str, str, str]:
    manifest = build_reference_manifest(fingerprint_raw_bytes(raw_bytes), source)  # type: ignore[arg-type]
    manifest_sha256 = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
    return (
        manifest.raw_artifact.relative_path,
        manifest.source_record_relative_path,
        manifest_relative_path(manifest_sha256),
    )


def _store_path(root: Path, relative_path: str) -> Path:
    return root.joinpath(*relative_path.split("/"))


def test_ingestion_preserves_and_rereads_exact_raw_bytes(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    receipt = ingest_reference(
        reference_store_root=reference_store_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    raw_path = _store_path(reference_store_root, receipt.raw_artifact_relative_path)
    assert raw_path.read_bytes() == SYNTHETIC_RAW_BYTES
    assert receipt.raw_artifact_sha256 == hashlib.sha256(SYNTHETIC_RAW_BYTES).hexdigest()
    projection = verify_ingestion(
        reference_store_root=reference_store_root,
        receipt=receipt,
    )
    assert projection.raw_artifact_byte_count == len(SYNTHETIC_RAW_BYTES)
    assert projection.raw_artifact_sha256 == receipt.raw_artifact_sha256


def test_digest_mismatch_fails_before_store_creation(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    with pytest.raises(DigestMismatchError):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
            expected_sha256="0" * 64,
        )
    assert not reference_store_root.exists()


def test_zero_length_policy_fails_before_publication(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    with pytest.raises(RawArtifactPolicyError):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=b"",
            source=reference_source,  # type: ignore[arg-type]
        )
    assert not reference_store_root.exists()


def test_bounded_size_policy_fails_before_publication(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    oversized = b"x" * (MAX_RAW_ARTIFACT_BYTES + 1)
    with pytest.raises(RawArtifactPolicyError):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=oversized,
            source=reference_source,  # type: ignore[arg-type]
        )
    assert not reference_store_root.exists()


def test_byte_identical_repeat_is_idempotent_and_receipt_is_stable(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    first = ingest_reference(
        reference_store_root=reference_store_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    before = {
        path.relative_to(reference_store_root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in reference_store_root.rglob("*")
        if path.is_file()
    }
    second = ingest_reference(
        reference_store_root=reference_store_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    after = {
        path.relative_to(reference_store_root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in reference_store_root.rglob("*")
        if path.is_file()
    }
    assert second == first
    assert after == before


def test_existing_file_collision_fails_closed_without_overwrite(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    raw_relative_path, _, _ = _planned_paths(SYNTHETIC_RAW_BYTES, reference_source)
    raw_path = _store_path(reference_store_root, raw_relative_path)
    raw_path.parent.mkdir(parents=True)
    collision = b"conflicting pre-existing content"
    raw_path.write_bytes(collision)

    with pytest.raises(PublicationConflictError):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )
    assert raw_path.read_bytes() == collision


def test_interrupted_publication_residue_is_preserved_and_not_repaired(
    reference_store_root: Path,
    reference_source: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.reference_data.store as store_module

    original = store_module.create_confined_file
    calls = 0

    def interrupt_second_create(root: Path, relative_path: str, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption")
        original(root, relative_path, content)

    monkeypatch.setattr(store_module, "create_confined_file", interrupt_second_create)
    with pytest.raises(OSError, match="simulated interruption"):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )

    raw_relative_path, source_relative_path, manifest_path = _planned_paths(
        SYNTHETIC_RAW_BYTES,
        reference_source,
    )
    raw_path = _store_path(reference_store_root, raw_relative_path)
    assert raw_path.read_bytes() == SYNTHETIC_RAW_BYTES
    monkeypatch.setattr(store_module, "create_confined_file", original)
    with pytest.raises(PartialPublicationError):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )
    assert raw_path.read_bytes() == SYNTHETIC_RAW_BYTES
    assert not _store_path(reference_store_root, source_relative_path).exists()
    assert not _store_path(reference_store_root, manifest_path).exists()


def test_concurrent_identical_ingestion_is_serialized_and_idempotent(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    def ingest() -> IngestionReceipt:
        return ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
            expected_sha256=hashlib.sha256(SYNTHETIC_RAW_BYTES).hexdigest(),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = tuple(executor.map(lambda _: ingest(), range(16)))
    assert len(set(receipts)) == 1
    verify_ingestion(reference_store_root=reference_store_root, receipt=receipts[0])


def test_concurrent_identical_ingestion_is_serialized_across_processes(
    tmp_path: Path,
    reference_source: object,
) -> None:
    root = tmp_path / "process-shared-reference-store"
    source_json = reference_source.model_dump_json()  # type: ignore[attr-defined]
    script = r'''
import hashlib
import sys
from material_studio_mcp_server.reference_data import (
    ReferenceSource,
    canonical_sha256,
    ingest_reference,
)

raw = b"data_fixture\r\n# exact bytes; not a structure\r\n"
source = ReferenceSource.model_validate_json(sys.argv[2])
receipt = ingest_reference(
    reference_store_root=sys.argv[1],
    raw_bytes=raw,
    source=source,
    expected_sha256=hashlib.sha256(raw).hexdigest(),
)
print(canonical_sha256(receipt))
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    def run_ingestion() -> str:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(root), source_json],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipt_hashes = tuple(executor.map(lambda _: run_ingestion(), range(8)))
    assert len(set(receipt_hashes)) == 1

    receipt = ingest_reference(
        reference_store_root=root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
        expected_sha256=hashlib.sha256(SYNTHETIC_RAW_BYTES).hexdigest(),
    )
    assert canonical_sha256(receipt) == receipt_hashes[0]
    verify_ingestion(reference_store_root=root, receipt=receipt)


def test_receipt_is_constructed_only_after_persisted_evidence_is_verified(
    reference_store_root: Path,
    reference_source: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.reference_data as reference_data
    import material_studio_mcp_server.reference_data.store as store_module

    assert not hasattr(reference_data, "build_ingestion_receipt")
    original = store_module._build_verified_ingestion_receipt
    calls = 0

    def guarded_builder(*args: object, **kwargs: object) -> IngestionReceipt:
        nonlocal calls
        calls += 1
        for relative_path in _planned_paths(SYNTHETIC_RAW_BYTES, reference_source):
            assert _store_path(reference_store_root, relative_path).is_file()
        return original(*args, **kwargs)

    monkeypatch.setattr(store_module, "_build_verified_ingestion_receipt", guarded_builder)
    ingest_reference(
        reference_store_root=reference_store_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    assert calls == 1


def test_corrupt_published_raw_artifact_is_never_repaired(
    reference_store_root: Path,
    reference_source: object,
) -> None:
    receipt = ingest_reference(
        reference_store_root=reference_store_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    raw_path = _store_path(reference_store_root, receipt.raw_artifact_relative_path)
    corruption = b"external corruption"
    raw_path.write_bytes(corruption)
    with pytest.raises(ArtifactCorruptionError):
        verify_ingestion(reference_store_root=reference_store_root, receipt=receipt)
    with pytest.raises(PublicationConflictError):
        ingest_reference(
            reference_store_root=reference_store_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )
    assert raw_path.read_bytes() == corruption


def test_confined_read_rejects_oversized_evidence_before_content_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import material_studio_mcp_server.reference_data._paths as paths_module

    root = paths_module.prepare_store_root(tmp_path / "bounded-store", create=True)
    relative_path = "raw/sha256/00/" + "0" * 64 + ".bin"
    paths_module.ensure_parent_directory(root, relative_path)
    _store_path(root, relative_path).write_bytes(b"oversized")

    def forbidden_read(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("oversized evidence must be rejected before os.read")

    monkeypatch.setattr(paths_module.os, "read", forbidden_read)
    with pytest.raises(ArtifactCorruptionError, match="read limit"):
        paths_module.read_confined_file(root, relative_path, max_bytes=1)


def test_non_directory_root_and_unexpected_internal_file_type_are_rejected(
    tmp_path: Path,
    reference_source: object,
) -> None:
    root_file = tmp_path / "not-a-directory"
    root_file.write_bytes(b"sentinel")
    with pytest.raises(StoreConfinementError):
        ingest_reference(
            reference_store_root=root_file,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )
    assert root_file.read_bytes() == b"sentinel"

    root = tmp_path / "reference-store"
    root.mkdir()
    (root / "raw").write_bytes(b"unexpected file")
    with pytest.raises(StoreConfinementError):
        ingest_reference(
            reference_store_root=root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )


def test_traversal_root_and_receipt_path_are_rejected(
    tmp_path: Path,
    reference_source: object,
) -> None:
    traversal_root = tmp_path / "selected" / ".." / "escape"
    with pytest.raises(StoreConfinementError):
        ingest_reference(
            reference_store_root=traversal_root,
            raw_bytes=SYNTHETIC_RAW_BYTES,
            source=reference_source,  # type: ignore[arg-type]
        )
    assert not (tmp_path / "escape").exists()

    valid_root = tmp_path / "valid-store"
    receipt = ingest_reference(
        reference_store_root=valid_root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    bypassed = receipt.model_copy(
        update={"manifest_relative_path": "../outside/manifest.json"}
    )
    with pytest.raises((StoreConfinementError, ArtifactCorruptionError)):
        verify_ingestion(reference_store_root=valid_root, receipt=bypassed)
    drive_qualified = receipt.model_copy(
        update={"manifest_relative_path": "C:/outside/manifest.json"}
    )
    with pytest.raises((StoreConfinementError, ArtifactCorruptionError)):
        verify_ingestion(reference_store_root=valid_root, receipt=drive_qualified)


def _make_directory_symlink(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
            raise
        _make_windows_junction(target, link)


def _make_windows_junction(target: Path, link: Path) -> None:
    """Create a directory junction without requiring symlink privilege."""

    from ctypes import wintypes

    target_text = str(target.resolve())
    substitute = ("\\??\\" + target_text).encode("utf-16-le")
    printable = target_text.encode("utf-16-le")
    path_buffer = substitute + b"\x00\x00" + printable + b"\x00\x00"
    data_length = 8 + len(path_buffer)
    reparse_data = struct.pack(
        "<LHHHHHH",
        0xA0000003,
        data_length,
        0,
        0,
        len(substitute),
        len(substitute) + 2,
        len(printable),
    ) + path_buffer

    link.mkdir()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(link),
        0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        link.rmdir()
        raise OSError(ctypes.get_last_error(), "CreateFileW failed for junction")
    try:
        returned = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(reparse_data)
        ok = kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            buffer,
            len(reparse_data),
            None,
            0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "FSCTL_SET_REPARSE_POINT failed")
    finally:
        kernel32.CloseHandle(handle)


def test_symlink_or_reparse_root_is_rejected(
    tmp_path: Path,
    reference_source: object,
) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    link = tmp_path / "linked-root"
    _make_directory_symlink(target, link)
    try:
        if os.name == "nt":
            attributes = getattr(link.lstat(), "st_file_attributes", 0)
            assert attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        with pytest.raises(StoreConfinementError):
            ingest_reference(
                reference_store_root=link,
                raw_bytes=SYNTHETIC_RAW_BYTES,
                source=reference_source,  # type: ignore[arg-type]
            )
        assert not tuple(target.iterdir())
    finally:
        link.rmdir()


def test_symlink_or_reparse_ancestor_is_rejected_without_target_write(
    tmp_path: Path,
    reference_source: object,
) -> None:
    target = tmp_path / "outside-parent"
    target.mkdir()
    link = tmp_path / "linked-parent"
    _make_directory_symlink(target, link)
    try:
        with pytest.raises(StoreConfinementError):
            ingest_reference(
                reference_store_root=link / "nested-reference-store",
                raw_bytes=SYNTHETIC_RAW_BYTES,
                source=reference_source,  # type: ignore[arg-type]
            )
        assert not tuple(target.iterdir())
    finally:
        link.rmdir()


def test_internal_symlink_escape_is_rejected_without_outside_write(
    tmp_path: Path,
    reference_source: object,
) -> None:
    root = tmp_path / "reference-store"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_directory_symlink(outside, root / "raw")
    try:
        with pytest.raises(StoreConfinementError):
            ingest_reference(
                reference_store_root=root,
                raw_bytes=SYNTHETIC_RAW_BYTES,
                source=reference_source,  # type: ignore[arg-type]
            )
        assert not tuple(outside.iterdir())
    finally:
        (root / "raw").rmdir()


def test_hard_linked_published_evidence_is_rejected(
    tmp_path: Path,
    reference_source: object,
) -> None:
    root = tmp_path / "reference-store"
    receipt = ingest_reference(
        reference_store_root=root,
        raw_bytes=SYNTHETIC_RAW_BYTES,
        source=reference_source,  # type: ignore[arg-type]
    )
    raw_path = _store_path(root, receipt.raw_artifact_relative_path)
    outside_link = tmp_path / "outside-hard-link.bin"
    os.link(raw_path, outside_link)
    try:
        assert raw_path.stat().st_nlink == 2
        with pytest.raises(StoreConfinementError):
            verify_ingestion(reference_store_root=root, receipt=receipt)
    finally:
        outside_link.unlink()
