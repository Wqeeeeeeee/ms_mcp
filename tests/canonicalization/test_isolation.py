from __future__ import annotations

import ast
import builtins
import socket
import subprocess
from pathlib import Path

import pytest

from material_studio_mcp_server.canonicalization import (
    canonicalize_periodic_crystal,
    compare_structures,
    project_canonical_structure,
    project_structure_comparison,
)
from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from .conftest import zincblende_structure


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "src" / "material_studio_mcp_server" / "canonicalization"


def _forbidden(*args: object, **kwargs: object) -> object:
    raise AssertionError("forbidden side effect")


def test_blind_comparison_keeps_candidate_byte_identical_and_returns_safe_projection() -> None:
    reference = zincblende_structure()
    candidate = zincblende_structure(lattice_constant=4.04)
    before = canonical_json_bytes(candidate)
    comparison = compare_structures(reference, candidate)
    projection = project_structure_comparison(comparison)
    assert canonical_json_bytes(candidate) == before
    assert comparison.candidate_input_unchanged is True
    assert projection.contains_coordinates is False
    assert projection.contains_atom_mapping is False
    assert "displacements" not in projection.model_dump()


def test_blind_core_calls_do_not_use_network_process_or_file_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(Path, "write_bytes", _forbidden)
    monkeypatch.setattr(Path, "write_text", _forbidden)
    reference = zincblende_structure()
    candidate = zincblende_structure(lattice_constant=4.04)
    canonical = canonicalize_periodic_crystal(reference)
    comparison = compare_structures(reference, candidate)
    assert project_canonical_structure(canonical).contains_coordinates is False
    assert project_structure_comparison(comparison).contains_atom_mapping is False


def test_package_source_has_no_forbidden_runtime_integration_imports() -> None:
    forbidden_roots = {
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    forbidden_internal = {
        "material_studio_mcp_server.gui",
        "material_studio_mcp_server.runner",
        "material_studio_mcp_server.runners",
        "material_studio_mcp_server.state",
        "material_studio_mcp_server.translators",
    }
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = (node.module or "",)
            else:
                continue
            assert not any(name.split(".", 1)[0] in forbidden_roots for name in names)
            assert not any(
                name == blocked or name.startswith(blocked + ".")
                for name in names
                for blocked in forbidden_internal
            )


def test_server_entry_path_does_not_import_canonicalization() -> None:
    server_source = (
        ROOT / "src" / "material_studio_mcp_server" / "server.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(server_source)
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
    assert not any("canonicalization" in name for name in imported_modules)


def test_core_has_no_dynamic_code_execution_calls() -> None:
    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called.isdisjoint(forbidden_calls)


def test_core_calls_emit_no_reference_content(capsys: pytest.CaptureFixture[str]) -> None:
    reference = zincblende_structure()
    canonicalize_periodic_crystal(reference)
    compare_structures(reference, reference)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
