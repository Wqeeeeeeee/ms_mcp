from __future__ import annotations

import ctypes
import os
import stat
import struct
from pathlib import Path

import pytest

import material_studio_mcp_server.benchmark_evaluation.tree as tree_module
from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkEvaluationError,
    CandidateTreeGuard,
    EvaluationReason,
    EvaluationRoots,
    declared_roots_are_disjoint,
    relative_path_under_declared_root,
    resolve_root_relative_path,
    snapshot_candidate_tree,
    validate_relative_posix_path,
    verify_isolation_roots,
)


@pytest.mark.parametrize(
    "value",
    [
        "file:stream",
        "file::$DATA",
        "CON",
        "aux.txt",
        "COM1.dat",
        "safe/NUL.json",
        "../escape",
        "safe/../escape",
        "safe\\file",
        "/absolute",
        "C:/absolute",
        "safe//file",
        "safe/./file",
        "safe/trailing.",
        "safe/trailing ",
        "CANDID~1/model.cif",
        "LONGFI~10/model.cif",
        "bad?/model.cif",
        "bad*/model.cif",
        "bad|name/model.cif",
        "bad<name>/model.cif",
        'bad"name/model.cif',
        "cafe\u0301/model.cif",
        "COM\u00b9.txt",
        "LPT\u00b2.log",
    ],
)
def test_relative_path_rejects_aliases_and_root_escape(value: str) -> None:
    with pytest.raises(BenchmarkEvaluationError) as captured:
        validate_relative_posix_path(value)
    assert captured.value.reason is EvaluationReason.ARTIFACT_ROOT_BINDING_INVALID


def test_relative_path_accepts_one_canonical_posix_spelling() -> None:
    assert validate_relative_posix_path("candidate/sic/model.json").as_posix() == (
        "candidate/sic/model.json"
    )


def test_declared_roots_reject_equality_ancestry_and_prefix_collision() -> None:
    assert declared_roots_are_disjoint(("ref/a", "candidate/a", "output/a"))
    assert not declared_roots_are_disjoint(("same", "same", "output"))
    assert not declared_roots_are_disjoint(("candidate", "candidate/run", "output"))
    assert not declared_roots_are_disjoint(("cand", "candidate", "output"))


def test_artifact_binding_requires_a_strict_descendant() -> None:
    relative = relative_path_under_declared_root(
        "reference/sic/reference.cif", "reference/sic"
    )
    assert relative.as_posix() == "reference.cif"
    with pytest.raises(BenchmarkEvaluationError):
        relative_path_under_declared_root("reference/sic", "reference/sic")
    with pytest.raises(BenchmarkEvaluationError):
        relative_path_under_declared_root("reference/other.cif", "reference/sic")


@pytest.mark.skipif(os.name != "nt", reason="Windows path casing contract")
def test_root_relative_resolution_rejects_case_alias(evaluation_fixture) -> None:
    with pytest.raises(BenchmarkEvaluationError):
        resolve_root_relative_path(
            evaluation_fixture.roots.candidate_root, "STRUCTURE.CIF"
        )


def test_physical_roots_are_canonical_and_pairwise_disjoint(evaluation_fixture) -> None:
    verified = verify_isolation_roots(evaluation_fixture.roots)
    assert verified.reference_root == evaluation_fixture.roots.reference_root.resolve()
    assert verified.candidate_root == evaluation_fixture.roots.candidate_root.resolve()


def test_physical_root_prefix_collision_fails(tmp_path: Path) -> None:
    reference = tmp_path / "cand"
    candidate = tmp_path / "candidate"
    evaluator = tmp_path / "out"
    for item in (reference, candidate, evaluator):
        item.mkdir()
    with pytest.raises(BenchmarkEvaluationError) as captured:
        verify_isolation_roots(
            EvaluationRoots(
                reference_root=reference,
                candidate_root=candidate,
                evaluator_output_root=evaluator,
            )
        )
    assert captured.value.reason is EvaluationReason.ISOLATION_ROOTS_NOT_DISJOINT


def _make_windows_junction(target: Path, link: Path) -> None:
    from ctypes import wintypes

    target_text = str(target.resolve())
    substitute = ("\\??\\" + target_text).encode("utf-16-le")
    printable = target_text.encode("utf-16-le")
    path_buffer = substitute + b"\x00\x00" + printable + b"\x00\x00"
    data = struct.pack(
        "<LHHHHHH",
        0xA0000003,
        8 + len(path_buffer),
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
    if handle == wintypes.HANDLE(-1).value:
        link.rmdir()
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    try:
        returned = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(data)
        if not kernel32.DeviceIoControl(
            handle,
            0x000900A4,
            buffer,
            len(data),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "junction creation failed")
    finally:
        kernel32.CloseHandle(handle)


def _make_directory_link(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
            raise
        _make_windows_junction(target, link)


def test_symlink_root_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    _make_directory_link(target, link)
    other = tmp_path / "other"
    output = tmp_path / "output"
    other.mkdir()
    output.mkdir()
    try:
        if os.name == "nt":
            assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        with pytest.raises(BenchmarkEvaluationError):
            verify_isolation_roots(
                EvaluationRoots(
                    reference_root=link,
                    candidate_root=other,
                    evaluator_output_root=output,
                )
            )
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()


def test_candidate_snapshot_binds_empty_directories_metadata_and_bytes(
    evaluation_fixture,
) -> None:
    empty = evaluation_fixture.roots.candidate_root / "empty"
    empty.mkdir()
    before = snapshot_candidate_tree(evaluation_fixture.roots.candidate_root)
    assert before.summary.file_count == 2
    assert before.summary.directory_count == 2
    evaluation_fixture.candidate_path.write_bytes(b"changed")
    after = snapshot_candidate_tree(evaluation_fixture.roots.candidate_root)
    assert before.summary.digest_sha256 != after.summary.digest_sha256


def test_candidate_snapshot_includes_empty_files(evaluation_fixture) -> None:
    (evaluation_fixture.roots.candidate_root / "empty.log").write_bytes(b"")
    snapshot = snapshot_candidate_tree(evaluation_fixture.roots.candidate_root)
    assert snapshot.summary.file_count == 3
    assert snapshot.summary.total_bytes == (
        evaluation_fixture.candidate_path.stat().st_size
        + evaluation_fixture.model_spec_path.stat().st_size
    )


def test_candidate_snapshot_bounds_directory_enumeration_before_sort(
    evaluation_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_budget = tree_module.MAX_TREE_FILES + tree_module.MAX_TREE_DIRECTORIES - 1

    class SyntheticEntry:
        def __init__(self, index: int) -> None:
            self.name = f"entry-{index:05d}"

    class OversizedDirectory:
        emitted = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def __iter__(self):
            for index in range(entry_budget + 1):
                self.emitted += 1
                yield SyntheticEntry(index)
            raise AssertionError("directory iterator was consumed past its budget")

    oversized = OversizedDirectory()
    monkeypatch.setattr(tree_module.os, "scandir", lambda path: oversized)
    with pytest.raises(BenchmarkEvaluationError) as captured:
        snapshot_candidate_tree(evaluation_fixture.roots.candidate_root)
    assert captured.value.reason is EvaluationReason.CANDIDATE_TREE_INVALID
    assert oversized.emitted == entry_budget + 1


def test_candidate_guard_detects_path_set_change(evaluation_fixture) -> None:
    guard = CandidateTreeGuard(evaluation_fixture.roots.candidate_root)
    with pytest.raises(BenchmarkEvaluationError) as captured:
        with guard:
            (evaluation_fixture.roots.candidate_root / "new.bin").write_bytes(b"x")
    assert captured.value.reason is EvaluationReason.CANDIDATE_TREE_CHANGED


def test_candidate_tree_rejects_hard_link_alias(evaluation_fixture) -> None:
    alias = evaluation_fixture.roots.candidate_root / "alias.cif"
    try:
        os.link(evaluation_fixture.candidate_path, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    with pytest.raises(BenchmarkEvaluationError) as captured:
        snapshot_candidate_tree(evaluation_fixture.roots.candidate_root)
    assert captured.value.reason is EvaluationReason.CANDIDATE_TREE_INVALID


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate stream contract")
def test_candidate_tree_rejects_existing_alternate_data_stream(
    evaluation_fixture,
) -> None:
    stream = Path(str(evaluation_fixture.candidate_path) + ":hidden")
    try:
        stream.write_bytes(b"hidden")
    except OSError:
        pytest.skip("alternate streams are unavailable on this volume")
    with pytest.raises(BenchmarkEvaluationError) as captured:
        snapshot_candidate_tree(evaluation_fixture.roots.candidate_root)
    assert captured.value.reason is EvaluationReason.CANDIDATE_TREE_INVALID
