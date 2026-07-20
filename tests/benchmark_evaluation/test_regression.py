from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "benchmark_evaluation"


def _environment() -> dict[str, str]:
    value = os.environ.copy()
    value["PYTHONPATH"] = str(ROOT / "src")
    value["PYTHONDONTWRITEBYTECODE"] = "1"
    return value


def test_evaluator_import_does_not_change_public_mcp_inventory() -> None:
    script = r'''
import copy
import inspect
import json
from material_studio_mcp_server import server

def surface():
    tools = server.mcp._tool_manager.list_tools()
    target = next(tool for tool in tools if tool.name == "material_studio_live_modeling_request")
    return {
        "names": sorted(tool.name for tool in tools),
        "schema": copy.deepcopy(target.parameters),
        "signature": str(inspect.signature(server.material_studio_live_modeling_request)),
    }

before = surface()
import material_studio_mcp_server.benchmark_evaluation
after = surface()
print(json.dumps({"before": before, "after": after}, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["before"] == payload["after"]
    assert len(payload["after"]["names"]) == 40
    assert not any("benchmark" in name for name in payload["after"]["names"])


def test_evaluator_first_import_then_server_still_has_exactly_40_tools() -> None:
    script = r'''
import json
import material_studio_mcp_server.benchmark_evaluation
from material_studio_mcp_server import server
print(json.dumps(sorted(tool.name for tool in server.mcp._tool_manager.list_tools())))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert len(json.loads(completed.stdout)) == 40


def test_server_entry_path_does_not_import_evaluator() -> None:
    tree = ast.parse(
        (ROOT / "src" / "material_studio_mcp_server" / "server.py").read_text(
            encoding="utf-8"
        )
    )
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not any("benchmark_evaluation" in name for name in names)


def test_package_uses_only_canonicalization_package_root_exports() -> None:
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (
                node.module or ""
            ).startswith("material_studio_mcp_server.canonicalization"):
                assert node.module == "material_studio_mcp_server.canonicalization"
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith(
                        "material_studio_mcp_server.canonicalization."
                    )
                    for alias in node.names
                )


def test_package_does_not_import_server_gui_runner_state_or_spglib() -> None:
    forbidden = {
        "material_studio_mcp_server.gui",
        "material_studio_mcp_server.runner",
        "material_studio_mcp_server.runners",
        "material_studio_mcp_server.server",
        "material_studio_mcp_server.state",
        "spglib",
    }
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = (node.module or "",)
            else:
                continue
            assert not any(
                name == blocked or name.startswith(blocked + ".")
                for name in names
                for blocked in forbidden
            )
