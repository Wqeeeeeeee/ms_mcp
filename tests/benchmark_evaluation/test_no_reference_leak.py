from __future__ import annotations

import ast
import socket
import subprocess
from pathlib import Path

import pytest

import material_studio_mcp_server.benchmark_evaluation.evaluator as evaluator_module
from material_studio_mcp_server.benchmark_evaluation import (
    BenchmarkEvaluationError,
    EvaluationReason,
    evaluate_benchmark_case,
    validate_benchmark_case_semantics,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "benchmark_evaluation"


def _forbidden(*args, **kwargs):
    raise AssertionError("forbidden side effect")


def test_semantic_validation_is_silent_and_does_not_render_reference_content(
    evaluation_fixture, capsys
) -> None:
    report = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_canonicalization_canary_exception_never_crosses_boundary(
    evaluation_fixture, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    canary = "PRIVATE_REFERENCE_CANARY_8F2A"

    def fail(*args, **kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(evaluator_module, "canonicalize_cif_bytes", fail)
    with pytest.raises(BenchmarkEvaluationError) as captured:
        evaluator_module.evaluate_benchmark_case(
            evaluation_fixture.case,
            roots=evaluation_fixture.roots,
            submission=evaluation_fixture.submission,
            evaluation_run_id="redacted-canary-test",
        )
    assert captured.value.reason is EvaluationReason.CANONICALIZATION_FAILED
    assert canary not in str(captured.value)
    assert canary not in repr(captured.value)
    streams = capsys.readouterr()
    assert canary not in streams.out
    assert canary not in streams.err


def test_package_has_no_network_process_dynamic_execution_or_runtime_integrations() -> None:
    forbidden_roots = {"requests", "socket", "subprocess", "urllib"}
    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called.isdisjoint(forbidden_calls)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = (node.module or "",)
            else:
                continue
            assert not any(
                name.split(".", 1)[0] in forbidden_roots for name in names
            )


def test_semantic_core_uses_no_network_process_or_file_write(
    evaluation_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(Path, "write_bytes", _forbidden)
    monkeypatch.setattr(Path, "write_text", _forbidden)
    report = validate_benchmark_case_semantics(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
    )
    assert report.valid is True


def test_full_evaluator_uses_no_network_process_workspace_discovery_or_write(
    evaluation_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(Path, "write_bytes", _forbidden)
    monkeypatch.setattr(Path, "write_text", _forbidden)
    outcome = evaluate_benchmark_case(
        evaluation_fixture.case,
        roots=evaluation_fixture.roots,
        submission=evaluation_fixture.submission,
        evaluation_run_id="offline-side-effect-test",
    )
    assert outcome.report.real_materials_studio == "NOT_RUN"
    assert outcome.report.real_castep == "NOT_RUN"


def test_package_has_no_workspace_or_environment_discovery_calls() -> None:
    forbidden_attributes = {
        ("os", "getcwd"),
        ("os", "getenv"),
        ("Path", "cwd"),
        ("Path", "home"),
    }
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if isinstance(node.func.value, ast.Name):
                assert (node.func.value.id, node.func.attr) not in forbidden_attributes
