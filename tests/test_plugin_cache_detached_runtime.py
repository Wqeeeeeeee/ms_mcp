from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows plugin cache acceptance")

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "materials-studio-mcp"
CMD = shutil.which("cmd.exe")
POWERSHELL = shutil.which("powershell.exe")


def _load_installer_helpers() -> ModuleType:
    """Load the installer fixtures without making tests an importable package."""

    helper_path = ROOT / "tests" / "test_windows_plugin_installer.py"
    spec = importlib.util.spec_from_file_location(
        "_ms_mcp_windows_installer_test_helpers", helper_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_cache_launcher(
    launcher: Path,
    *,
    local_app_data: Path,
    unrelated_cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    if launcher.suffix.lower() == ".bat":
        assert CMD is not None
        command = [
            CMD,
            "/d",
            "/q",
            "/c",
            "call",
            str(launcher),
            "-LocalAppDataRoot",
            str(local_app_data),
            "-ValidateOnly",
        ]
    else:
        assert POWERSHELL is not None
        command = [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
            "-LocalAppDataRoot",
            str(local_app_data),
            "-ValidateOnly",
        ]
    return subprocess.run(
        command,
        cwd=unrelated_cwd,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def test_detached_cache_launches_only_the_versioned_managed_runtime(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache copy must launch without its source copy or checkout as the cwd."""

    assert CMD is not None and POWERSHELL is not None
    tmp_path = Path(tempfile.mkdtemp(prefix="MSMCP-Cache-")).resolve()
    assert tmp_path.parent == Path(tempfile.gettempdir()).resolve()
    request.addfinalizer(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    plugin_manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    version = plugin_manifest["version"]

    # Reuse the reviewed offline installer fixture, but bind it to the current
    # manifest version instead of a historical hard-coded release number.
    helpers = _load_installer_helpers()
    helpers.VERSION = version
    local_app_data = tmp_path / "Local AppData 中文"
    workspace = tmp_path / "User Workspace 模型"
    user_profile = tmp_path / "User Profile 隔离"
    codex_config = user_profile / ".codex" / "config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_bytes(b"# must remain untouched\n")
    codex_config_before = codex_config.read_bytes()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("USERPROFILE", str(user_profile))
    runner = helpers._fake_runner(tmp_path / "Fake Materials Studio")
    configured = helpers._configure(local_app_data, runner, workspace)
    assert configured.returncode == 0, (
        f"configure stdout:\n{configured.stdout}\nconfigure stderr:\n{configured.stderr}"
    )

    wheel = helpers._build_minimal_installer_wheel(tmp_path / "wheelhouse")
    installed = helpers._install(local_app_data, wheel)
    assert installed.returncode == 0, (
        f"install stdout:\n{installed.stdout}\ninstall stderr:\n{installed.stderr}"
    )

    runtime_root = local_app_data / "MaterialsStudioMCP" / "runtimes" / version
    runtime_manifest = json.loads(
        (runtime_root / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    runtime_python = runtime_root / Path(runtime_manifest["python_relative_path"])
    runtime_package = runtime_root / Path(runtime_manifest["package_relative_path"])
    assert runtime_python.is_file()
    assert runtime_package.is_dir()
    assert runtime_manifest["entrypoint"] == "material_studio_mcp_server.server:main"
    assert Path(runtime_manifest["runtime_root"]).resolve() == runtime_root.resolve()

    # Model the marketplace's copy step, then detach that cache copy from the
    # temporary source used to populate it. The real worktree is never moved.
    temporary_source = tmp_path / "Temporary Release Source 临时"
    shutil.copytree(PLUGIN_ROOT, temporary_source)
    cache_root = (
        tmp_path
        / "Codex Plugin Cache 缓存 with spaces"
        / "marketplace"
        / "materials-studio-mcp"
        / version
    )
    shutil.copytree(temporary_source, cache_root)
    detached_source = temporary_source.with_name("Temporary Release Source.detached")
    temporary_source.rename(detached_source)
    assert not temporary_source.exists()
    assert cache_root.is_dir()

    unrelated_cwd = tmp_path / "Unrelated Working Directory 无关"
    unrelated_cwd.mkdir()
    poison_import = tmp_path / "Poison Python Path"
    poison_package = poison_import / "material_studio_mcp_server"
    poison_package.mkdir(parents=True)
    (poison_package / "__init__.py").write_text(
        "raise RuntimeError('source/PYTHONPATH fallback was used')\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env.update(
        {
            "LOCALAPPDATA": str(local_app_data),
            "USERPROFILE": str(user_profile),
            "PYTHONPATH": str(poison_import),
        }
    )

    launchers = (
        cache_root / "Run-MS-MCP.bat",
        cache_root / "scripts" / "Run-MS-MCP.ps1",
    )
    for launcher in launchers:
        result = _run_cache_launcher(
            launcher,
            local_app_data=local_app_data,
            unrelated_cwd=unrelated_cwd,
            env=env,
        )
        assert result.returncode == 0, (
            f"{launcher.name} stdout:\n{result.stdout}\n"
            f"{launcher.name} stderr:\n{result.stderr}"
        )
        assert result.stdout == "", "ValidateOnly must not pollute MCP JSON-RPC stdout"
        assert result.stderr == ""

    server = json.loads((cache_root / ".mcp.json").read_text(encoding="utf-8"))[
        "materials-studio"
    ]
    server_env = env.copy()
    server_env.update(server["env"])
    server_env["MS_MCP_TEST_HOLD_SERVER_SECONDS"] = "3"
    plugin_base = cache_root.parent
    refreshed_plugin_base = plugin_base.with_name(f"{plugin_base.name}.refresh-backup")
    process = subprocess.Popen(
        [
            server["command"],
            *server["args"],
            "-LocalAppDataRoot",
            str(local_app_data),
        ],
        cwd=cache_root,
        env=server_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    renamed_while_launcher_was_live = False
    try:
        comtypes_cache_root = local_app_data / "MaterialsStudioMCP" / "logs" / "comtypes-cache"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None:
            if any(comtypes_cache_root.glob("run-*")):
                plugin_base.rename(refreshed_plugin_base)
                renamed_while_launcher_was_live = process.poll() is None
                break
            time.sleep(0.02)
        stdout, stderr = process.communicate(timeout=180)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    assert renamed_while_launcher_was_live, (
        "the direct launcher never released the refreshable plugin cache while live"
    )
    assert process.returncode == 0, f"launcher stdout:\n{stdout}\nlauncher stderr:\n{stderr}"
    assert stdout == ""
    assert "ms-mcp staging import probe" in stderr
    assert refreshed_plugin_base.is_dir()

    assert codex_config.read_bytes() == codex_config_before
    assert list(workspace.iterdir()) == []
    assert detached_source.is_dir()
