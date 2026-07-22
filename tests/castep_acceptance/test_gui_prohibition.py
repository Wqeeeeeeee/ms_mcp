from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ._helpers import run_fake_acceptance


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "castep_acceptance"
_PROHIBITED_CALLS = {
    "activate_window",
    "capture_window",
    "close_window",
    "launch_app",
    "open_file",
    "open_file_in_existing_window",
    "press",
    "send_keys",
    "write",
}


def _terminal_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_source_has_no_gui_input_launch_open_or_hotload_primitive() -> None:
    forbidden_import_roots = {
        "computer_use",
        "material_studio_mcp_server.gui_uia",
        "pyautogui",
        "pywinauto",
    }
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = (node.module or "",)
            else:
                modules = ()
            assert not any(
                module == root or module.startswith(root + ".")
                for module in modules
                for root in forbidden_import_roots
            ), path
            if isinstance(node, ast.Call):
                assert _terminal_name(node.func) not in _PROHIBITED_CALLS, path


def test_fake_energy_execution_uses_inventory_only_and_no_gui_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from material_studio_mcp_server import server

    called_gui_tools: list[str] = []

    def forbidden_gui_tool(*args, **kwargs):
        called_gui_tools.append("public_gui_tool")
        raise AssertionError("CASTEP acceptance must not call a public GUI tool")

    for name in tuple(vars(server)):
        if name.startswith("material_studio_gui_"):
            monkeypatch.setattr(server, name, forbidden_gui_tool)

    result, fake_runner, gui = run_fake_acceptance(monkeypatch, tmp_path)
    assert fake_runner.call_count == 1
    assert called_gui_tools == []
    assert gui.calls == [
        ("list_processes", None),
        ("list_windows", 4101),
        ("list_processes", None),
        ("list_windows", 4101),
    ]
    assert result.plan.profile.open_in_gui is False
    assert result.plan.profile.take_snapshot is False
    assert result.plan.profile.export_view_audit is False
    assert result.public_execute["hotload_attempted"] is False
    assert result.public_execute["diagnostic_export_requested"] is False
    assert "gui_status" not in result.public_execute
    assert result.verification.gui.process_count_before_after == (1, 1)
    assert result.verification.gui.window_count_before_after == (1, 1)
    assert result.verification.gui.identity_unchanged is True
    assert result.verification.gui.process_launched is False
    assert result.verification.gui.gui_input_activation_open_or_hotload_count == 0
    assert not tuple(result.workspace_root.rglob("*.bands"))
