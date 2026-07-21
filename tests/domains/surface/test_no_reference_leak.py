from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path

from material_studio_mcp_server.domains.surface import PLUGIN
from material_studio_mcp_server.runtime import MatchKind, ValidationStatus


ROOT = Path(__file__).resolve().parents[3]
SURFACE_PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "domains" / "surface"
PROTECTED_REFERENCE_ROOT = ROOT / "benchmarks" / ("ref" + "erences")
MODELER_HELPER = Path(__file__).with_name("modeler_process.py")


def _is_protected(path: object) -> bool:
    if isinstance(path, int):
        return False
    try:
        observed = os.path.normcase(os.path.abspath(os.fspath(path)))
    except TypeError:
        return False
    protected = os.path.normcase(os.path.abspath(os.fspath(PROTECTED_REFERENCE_ROOT)))
    return observed == protected or observed.startswith(protected + os.sep)


def test_surface_plugin_has_no_reference_store_import_or_path_literal() -> None:
    for path in sorted(SURFACE_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                modules = []
            assert all("reference_data" not in module for module in modules)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                normalized = node.value.replace("\\", "/").casefold()
                assert "benchmarks/references" not in normalized


def test_analytical_oracle_is_independent_of_plugin_geometry_helpers() -> None:
    paths = (
        Path(__file__).with_name("test_blind_benchmark.py"),
        MODELER_HELPER,
    )
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("domains.surface.geometry")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                normalized = node.value.replace("\\", "/").casefold()
                assert "benchmarks/references" not in normalized


def test_modeler_helper_contains_no_auditor_or_reference_artifact_logic() -> None:
    source = MODELER_HELPER.read_text(encoding="utf-8")
    folded = source.replace("\\", "/").casefold()
    for forbidden in (
        "analytical_oracle",
        "reference_root",
        "structure_artifacts",
        "benchmarks/references",
    ):
        assert forbidden not in folded

    tree = ast.parse(source)
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
    assert all("reference_data" not in module for module in imported_modules)
    assert all("canonicalization" not in module for module in imported_modules)


def test_match_plan_build_validate_never_touch_workspace_reference_store(
    exact_intent,
    monkeypatch,
) -> None:
    original_builtin_open = builtins.open
    original_path_open = Path.open
    original_scandir = os.scandir
    original_listdir = os.listdir
    attempted: list[str] = []

    def reject(path: object) -> None:
        if _is_protected(path):
            attempted.append(os.fspath(path))
            raise AssertionError("surface plugin attempted protected reference access")

    def guarded_builtin_open(file, *args, **kwargs):
        reject(file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_path_open(path: Path, *args, **kwargs):
        reject(path)
        return original_path_open(path, *args, **kwargs)

    def guarded_scandir(path="."):
        reject(path)
        return original_scandir(path)

    def guarded_listdir(path="."):
        reject(path)
        return original_listdir(path)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(os, "scandir", guarded_scandir)
    monkeypatch.setattr(os, "listdir", guarded_listdir)

    matched = PLUGIN.match(exact_intent)
    assert matched.kind is MatchKind.EXACT
    planned = PLUGIN.plan(exact_intent, None)
    candidate = PLUGIN.build(planned)
    report = PLUGIN.validate(candidate)

    assert report.status is ValidationStatus.PASS_WITH_WARNINGS
    assert attempted == []


def test_case_modeler_projection_does_not_name_workspace_reference_store() -> None:
    case_path = ROOT / "benchmarks" / "cases" / "sic_3c_surface" / "benchmark_case.json"
    source = case_path.read_text(encoding="utf-8")
    normalized = source.replace("\\", "/").casefold()

    assert "benchmarks/references" not in normalized
    assert str(PROTECTED_REFERENCE_ROOT).casefold() not in normalized
