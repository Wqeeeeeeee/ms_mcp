from __future__ import annotations

import asyncio
import ast
import base64
import csv
import hashlib
import json
import marshal
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer tests")

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SCRIPTS = ROOT / "scripts" / "windows"
POWERSHELL = shutil.which("powershell.exe")
CMD = shutil.which("cmd.exe")
VERSION = "0.5.4"


def _run_ps(script: str, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL
    return subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_SCRIPTS / script),
            *map(str, arguments),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _run_bat(script: str, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert CMD
    return subprocess.run(
        [CMD, "/d", "/v:off", "/c", str(ROOT / script), *map(str, arguments)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _fake_runner(root: Path) -> Path:
    runner = root / "BIOVIA" / "Materials Studio 20.1" / "bin" / "RunMatScript.bat"
    runner.parent.mkdir(parents=True)
    runner.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    return runner


def _configure(
    base: Path,
    runner: Path,
    workspace: Path | None = None,
    *,
    force: bool = False,
    python_command: str | Path = sys.executable,
) -> subprocess.CompletedProcess[str]:
    workspace = workspace or (base.parent / "workspace 数据 & safe")
    arguments = [
        "-LocalAppDataRoot",
        str(base),
        "-PythonCommand",
        str(python_command),
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(workspace),
        "-NonInteractive",
    ]
    if force:
        arguments.append("-Force")
    return _run_ps("Configure-MS-MCP.ps1", *arguments)


def _wheel_record_digest(content: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")


def _build_minimal_dependency_wheel(
    destination: Path,
    *,
    distribution: str,
    version: str,
) -> Path:
    normalized = distribution.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    files: dict[str, bytes] = {
        f"{normalized}/__init__.py": b"\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {distribution}\n"
            f"Version: {version}\n\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: ms-mcp-installer-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
    }
    record_path = f"{dist_info}/RECORD"
    rows = [
        [name, _wheel_record_digest(content), str(len(content))]
        for name, content in sorted(files.items())
    ]
    rows.append([record_path, "", ""])
    from io import StringIO

    buffer = StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    files[record_path] = buffer.getvalue().encode()
    wheel = destination / f"{normalized}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


def _build_minimal_installer_wheel(destination: Path) -> Path:
    wheel = destination / f"materials_studio_mcp-{VERSION}-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    mcp_dist_info = "mcp-1.28.1.dist-info"
    mcp_files: dict[str, bytes] = {
        "mcp/__init__.py": b"\n",
        "mcp/server/__init__.py": b"\n",
        "mcp/server/fastmcp.py": b"\n",
        f"{mcp_dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: mcp\n"
            "Version: 1.28.1\n"
            "Provides-Extra: cli\n\n"
        ).encode(),
        f"{mcp_dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: ms-mcp-installer-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
    }
    mcp_record_path = f"{mcp_dist_info}/RECORD"
    mcp_rows = [
        [name, _wheel_record_digest(content), str(len(content))]
        for name, content in sorted(mcp_files.items())
    ]
    mcp_rows.append([mcp_record_path, "", ""])
    from io import StringIO

    mcp_record_buffer = StringIO(newline="")
    csv.writer(mcp_record_buffer, lineterminator="\n").writerows(mcp_rows)
    mcp_files[mcp_record_path] = mcp_record_buffer.getvalue().encode()
    mcp_wheel = destination / "mcp-1.28.1-py3-none-any.whl"
    with zipfile.ZipFile(mcp_wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in mcp_files.items():
            archive.writestr(name, content)
    _build_minimal_dependency_wheel(
        destination,
        distribution="comtypes",
        version="1.4.16",
    )
    _build_minimal_dependency_wheel(
        destination,
        distribution="pywinauto",
        version="0.6.9",
    )

    dist_info = f"materials_studio_mcp-{VERSION}.dist-info"
    data_root = f"materials_studio_mcp-{VERSION}.data"
    entrypoints = {
        "ms-mcp": "material_studio_mcp_server.server:main",
        "ms-mcp-config-doctor": "material_studio_mcp_server.codex_config:main",
        "ms-mcp-config-register": "material_studio_mcp_server.codex_registration:main",
        "ms-mcp-runtime-deploy": "material_studio_mcp_server.runtime_deployment:main",
        "ms-mcp-protocol-smoke": "material_studio_mcp_server.protocol_smoke:main",
        "ms-mcp-live-smoke": "material_studio_mcp_server.live_smoke:main",
        "ms-mcp-dashboard": "material_studio_mcp_server.read_only_dashboard:main",
    }
    files: dict[str, bytes] = {
        "material_studio_mcp_server/__init__.py": b"\n",
        "material_studio_mcp_server/server.py": (
            b"import os\n"
            b"import time\n"
            b"import warnings\n"
            b"warnings.warn('ms-mcp staging import probe', RuntimeWarning)\n"
            b"def main():\n"
            b"    hold = os.environ.get('MS_MCP_TEST_HOLD_SERVER_SECONDS')\n"
            b"    if hold:\n        time.sleep(float(hold))\n"
            b"    return 0\n"
        ),
        "material_studio_mcp_server/codex_config.py": b"def main():\n    return 0\n",
        "material_studio_mcp_server/codex_registration.py": b"def main():\n    return 0\n",
        "material_studio_mcp_server/runtime_deployment.py": b"def main():\n    return 0\n",
        "material_studio_mcp_server/protocol_smoke.py": b"def main():\n    return 0\n",
        "material_studio_mcp_server/live_smoke.py": b"def main():\n    return 0\n",
        "material_studio_mcp_server/read_only_dashboard.py": b"def main():\n    return 0\n",
        # Reproduce wheels such as pywin32 that install importable Python helpers
        # under venv/Scripts. pip compiles these with the staging co_filename; the
        # installer must recompile them against the final versioned runtime path.
        f"{data_root}/scripts/ms_mcp_runtime_probe.py": (
            b"def installed_scripts_probe():\n    return 'ok'\n"
        ),
        "mcp/__init__.py": b"__version__ = '1.99.0'\n",
        "mcp/server/__init__.py": b"\n",
        "mcp/server/fastmcp/__init__.py": b"\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: materials-studio-mcp\n"
            f"Version: {VERSION}\n"
            "Summary: installer integration fixture\n"
            "Requires-Dist: mcp[cli]>=1.12.4,<2\n"
            "Requires-Dist: comtypes==1.4.16; sys_platform == 'win32'\n"
            "Requires-Dist: pywinauto==0.6.9; sys_platform == 'win32'\n\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: ms-mcp-installer-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
        f"{dist_info}/entry_points.txt": (
            "[console_scripts]\n"
            + "".join(f"{name} = {value}\n" for name, value in entrypoints.items())
        ).encode(),
    }
    record_path = f"{dist_info}/RECORD"
    rows = [[name, _wheel_record_digest(content), str(len(content))] for name, content in sorted(files.items())]
    rows.append([record_path, "", ""])
    record_buffer = StringIO(newline="")
    csv.writer(record_buffer, lineterminator="\n").writerows(rows)
    files[record_path] = record_buffer.getvalue().encode()
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


def _install(base: Path, wheel: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    install_env = os.environ.copy()
    if env:
        install_env.update(env)
    if (wheel.parent / "mcp-1.28.1-py3-none-any.whl").is_file():
        install_env["PIP_NO_INDEX"] = "1"
        install_env["PIP_FIND_LINKS"] = str(wheel.parent)
    return _run_ps(
        "Install-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(base),
        "-WheelPath",
        str(wheel),
        "-WheelSha256",
        digest,
        "-NonInteractive",
        env=install_env,
    )


def test_configure_supports_spaces_cjk_and_does_not_touch_codex_config(tmp_path: Path) -> None:
    base = tmp_path / "Local App 数据"
    runner = _fake_runner(tmp_path)
    workspace = tmp_path / "工作 区 & $(no-eval)"
    user_profile = tmp_path / "User Profile"
    codex_config = user_profile / ".codex" / "config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text("# sentinel\n", encoding="utf-8")
    before = codex_config.read_bytes()
    env = os.environ.copy()
    env["USERPROFILE"] = str(user_profile)

    result = subprocess.run(
        [
        CMD,
        "/d",
        "/c",
        "Configure-MS-MCP.bat",
        "-LocalAppDataRoot",
        str(base),
        "-PythonCommand",
        sys.executable,
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(workspace),
        "-NonInteractive",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    product = base / "MaterialsStudioMCP"
    settings = json.loads((product / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["package_version"] == VERSION
    assert Path(settings["materials_studio"]["runner"]) == runner
    assert Path(settings["workspace"]) == workspace
    assert settings["safety"]["active_codex_config_modified"] is False
    assert codex_config.read_bytes() == before
    assert not (product / "config" / "active-runtime.json").exists()


def test_install_accepts_configured_python_executable_path_with_spaces(tmp_path: Path) -> None:
    bootstrap_root = tmp_path / "Python bootstrap with spaces"
    bootstrap = subprocess.run(
        [sys.executable, "-m", "venv", str(bootstrap_root)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    bootstrap_python = bootstrap_root / "Scripts" / "python.exe"
    assert bootstrap_python.is_file()

    # Keep the product root below the installer's explicit Windows path budget;
    # the bootstrap interpreter path itself still exercises embedded spaces.
    base = tmp_path / "local"
    runner = _fake_runner(tmp_path)
    configured = _configure(base, runner, python_command=bootstrap_python)
    assert configured.returncode == 0, configured.stderr
    settings = json.loads(
        (base / "MaterialsStudioMCP" / "config" / "settings.json").read_text(encoding="utf-8")
    )
    assert Path(settings["python"]["executable"]) == bootstrap_python.resolve()

    wheel = _build_minimal_installer_wheel(tmp_path / "release")
    installed = _install(base, wheel)
    assert installed.returncode == 0, installed.stderr
    assert (base / "MaterialsStudioMCP" / "runtimes" / VERSION / "runtime-manifest.json").is_file()


@pytest.mark.parametrize(
    ("python_command", "runner_name", "expected"),
    [
        (r"Z:\definitely-missing\python.exe", "RunMatScript.bat", "Python executable not found"),
        (sys.executable, "not-a-runner.bat", "RunMatScript.bat"),
    ],
)
def test_configure_fails_closed_for_missing_python_or_invalid_runner(
    tmp_path: Path, python_command: str, runner_name: str, expected: str
) -> None:
    runner = tmp_path / runner_name
    runner.write_text("@exit /b 0\n", encoding="ascii")
    result = _run_ps(
        "Configure-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(tmp_path / "local"),
        "-PythonCommand",
        python_command,
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(tmp_path / "workspace"),
        "-NonInteractive",
    )
    assert result.returncode != 0
    assert expected in result.stderr


def test_configure_rejects_python_below_310(tmp_path: Path) -> None:
    fake_python = tmp_path / "python-3.9.cmd"
    fake_python.write_text(
        '@echo off\r\necho {"version":[3,9,18],"executable":"C:\\\\fake\\\\python.exe"}\r\n',
        encoding="ascii",
    )
    runner = _fake_runner(tmp_path)
    result = _run_ps(
        "Configure-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(tmp_path / "local"),
        "-PythonCommand",
        str(fake_python),
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(tmp_path / "workspace"),
        "-NonInteractive",
    )
    assert result.returncode != 0
    assert "Python 3.10 or newer is required" in result.stderr


def test_configure_rejects_workspace_overlap_and_reparse_point(tmp_path: Path) -> None:
    base = tmp_path / "local"
    runner = _fake_runner(tmp_path)
    overlap = base / "MaterialsStudioMCP" / "runtimes" / "workspace"
    result = _configure(base, runner, overlap)
    assert result.returncode != 0
    assert "Workspace must not overlap" in result.stderr

    target = tmp_path / "real-workspace"
    target.mkdir()
    junction = tmp_path / "workspace-junction"
    linked = subprocess.run(
        [CMD, "/d", "/c", "mklink", "/J", str(junction), str(target)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if linked.returncode != 0:
        pytest.skip("junction creation is unavailable")
    reparse = _configure(tmp_path / "local2", runner, junction)
    assert reparse.returncode != 0
    assert "Reparse points are not allowed" in reparse.stderr


def test_configure_rejects_command_name_python_resolved_through_junction(tmp_path: Path) -> None:
    real_bin = tmp_path / "real-python-bin"
    real_bin.mkdir()
    proxy = real_bin / "python-proxy.cmd"
    proxy.write_text(f'@echo off\r\n"{sys.executable}" %*\r\n', encoding="utf-8")
    junction = tmp_path / "python-bin-junction"
    linked = subprocess.run(
        [CMD, "/d", "/c", "mklink", "/J", str(junction), str(real_bin)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if linked.returncode != 0:
        pytest.skip("junction creation is unavailable")

    env = os.environ.copy()
    env["PATH"] = str(junction) + os.pathsep + env.get("PATH", "")
    runner = _fake_runner(tmp_path)
    result = _run_ps(
        "Configure-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(tmp_path / "local"),
        "-PythonCommand",
        "python-proxy.cmd",
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(tmp_path / "workspace"),
        "-NonInteractive",
        env=env,
    )
    assert result.returncode != 0
    assert "Reparse points are not allowed" in result.stderr


def test_configure_rejects_py_launcher_resolved_through_junction(tmp_path: Path) -> None:
    real_bin = tmp_path / "real-py-launcher-bin"
    real_bin.mkdir()
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", real_bin / "py.exe")
    junction = tmp_path / "py-launcher-junction"
    linked = subprocess.run(
        [CMD, "/d", "/c", "mklink", "/J", str(junction), str(real_bin)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if linked.returncode != 0:
        pytest.skip("junction creation is unavailable")

    env = os.environ.copy()
    env["PATH"] = str(junction) + os.pathsep + env.get("PATH", "")
    runner = _fake_runner(tmp_path)
    result = _run_ps(
        "Configure-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(tmp_path / "local"),
        "-PythonCommand",
        "py -3",
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(tmp_path / "workspace"),
        "-NonInteractive",
        env=env,
    )
    assert result.returncode != 0
    assert "Reparse points are not allowed" in result.stderr


def test_long_paths_are_canonicalized_and_install_fails_closed_before_publish(
    tmp_path: Path,
) -> None:
    long_path = tmp_path.joinpath(*(["long-segment-1234567890"] * 15))
    quoted_long_path = str(long_path).replace("'", "''")
    command = (
        f". '{WINDOWS_SCRIPTS / 'WindowsInstaller.Common.ps1'}'; "
        f"$p=Resolve-MSFullPath -Path '{quoted_long_path}'; "
        "if ($p.Length -le 260) { exit 2 }; [Console]::Out.Write($p)"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(str(long_path))

    base = tmp_path / "long-local-app-data"
    projected = (
        base
        / "MaterialsStudioMCP"
        / "runtimes"
        / VERSION
        / ".venv"
        / "Lib"
        / "site-packages"
        / "material_studio_mcp_server"
        / "gui_view_replay_executor.py"
    )
    while len(str(projected)) < 240:
        base = base / "long-segment-1234567890"
        projected = (
            base
            / "MaterialsStudioMCP"
            / "runtimes"
            / VERSION
            / ".venv"
            / "Lib"
            / "site-packages"
            / "material_studio_mcp_server"
            / "gui_view_replay_executor.py"
        )
    runner = _fake_runner(tmp_path / "short-runner")
    configured = _configure(base, runner, tmp_path / "short-workspace")
    assert configured.returncode == 0, configured.stderr
    wheel = _build_minimal_installer_wheel(tmp_path / "long-path-release")
    installed = _install(base, wheel)
    assert installed.returncode != 0
    assert "runtime path is too long" in installed.stderr.lower()
    assert not (base / "MaterialsStudioMCP" / "runtimes" / VERSION).exists()


def test_install_rejects_bad_hash_conflicts_and_cleans_interruption(tmp_path: Path) -> None:
    base = tmp_path / "Local App 数据"
    runner = _fake_runner(tmp_path)
    configured = _configure(base, runner)
    assert configured.returncode == 0, configured.stderr
    wheel = _build_minimal_installer_wheel(tmp_path / "release")

    invalid = _run_ps(
        "Install-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(base),
        "-WheelPath",
        str(wheel),
        "-WheelSha256",
        "not-a-hash",
        "-NonInteractive",
    )
    assert invalid.returncode != 0
    assert "exactly 64 hexadecimal" in invalid.stderr

    mismatch = _run_ps(
        "Install-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(base),
        "-WheelPath",
        str(wheel),
        "-WheelSha256",
        "0" * 64,
        "-NonInteractive",
    )
    assert mismatch.returncode != 0
    assert "Wheel SHA-256 mismatch" in mismatch.stderr

    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    release_manifest = tmp_path / "release-manifest.json"
    release_manifest.write_text(
        json.dumps({"files": [{"path": wheel.name, "sha256": wheel_hash}]}),
        encoding="utf-8",
    )
    manifest_only = _run_ps(
        "Install-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(base),
        "-WheelPath",
        str(wheel),
        "-ReleaseManifestPath",
        str(release_manifest),
        "-PlanOnly",
        "-NonInteractive",
    )
    assert manifest_only.returncode == 0, manifest_only.stderr
    assert json.loads(manifest_only.stdout)["wheel_sha256"] == wheel_hash

    disagree = _run_ps(
        "Install-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(base),
        "-WheelPath",
        str(wheel),
        "-WheelSha256",
        "1" * 64,
        "-ReleaseManifestPath",
        str(release_manifest),
        "-PlanOnly",
        "-NonInteractive",
    )
    assert disagree.returncode != 0
    assert "sources disagree" in disagree.stderr

    env = os.environ.copy()
    env["MATERIAL_STUDIO_MCP_TEST_INTERRUPT_AFTER_VENV"] = "1"
    interrupted = _install(base, wheel, env=env)
    assert interrupted.returncode != 0
    assert "Simulated interrupted installation" in interrupted.stderr
    assert not (base / "MaterialsStudioMCP" / "runtimes" / VERSION).exists()

    conflict = base / "MaterialsStudioMCP" / "runtimes" / VERSION
    conflict.mkdir(parents=True)
    sentinel = conflict / "user-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    existing = _install(base, wheel)
    assert existing.returncode != 0
    assert "runtime-manifest.json" in existing.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_install_publishes_from_unique_sibling_and_preserves_foreign_staging(
    tmp_path: Path,
) -> None:
    short_root = Path(tempfile.mkdtemp(prefix="ms-atomic-", dir=tmp_path.parent))
    base = short_root / "Local App 数据 & bang!"
    runner = _fake_runner(short_root / "runner 数据 & bang!")
    configured = _configure(base, runner)
    assert configured.returncode == 0, configured.stderr
    wheel = _build_minimal_installer_wheel(short_root / "release")

    runtimes = base / "MaterialsStudioMCP" / "runtimes"
    runtimes.mkdir(parents=True, exist_ok=True)
    foreign_staging = runtimes / ".installing-orphan-from-hard-interrupt"
    foreign_staging.mkdir()
    foreign_sentinel = foreign_staging / "owned-by-another-process.txt"
    foreign_sentinel.write_text("preserve", encoding="utf-8")

    env = os.environ.copy()
    env["MATERIAL_STUDIO_MCP_TEST_INTERRUPT_AFTER_VENV"] = "1"
    interrupted = _install(base, wheel, env=env)
    assert interrupted.returncode != 0
    assert "Simulated interrupted installation" in interrupted.stderr
    target = runtimes / VERSION
    assert not target.exists(), "an interrupted build must never expose the final target"
    assert foreign_sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in runtimes.iterdir()) == [foreign_staging.name]

    installed = _install(base, wheel)
    assert installed.returncode == 0, installed.stderr
    combined_install_output = installed.stdout + installed.stderr
    assert "ms-mcp staging import probe" not in combined_install_output
    assert re.search(
        r"runtimes[\\/]\.i[0-9a-f]+[\\/].*\.py:\d+:.*Warning",
        combined_install_output,
        flags=re.IGNORECASE,
    ) is None
    assert (target / "runtime-manifest.json").is_file()
    manifest = json.loads((target / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert Path(manifest["runtime_root"]).resolve() == target.resolve()
    assert foreign_sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in runtimes.iterdir()) == [
        foreign_staging.name,
        VERSION,
    ]
    assert not any(".installing-" in str(path) for path in target.rglob("*"))

    scripts_probe = target / ".venv" / "Scripts" / "ms_mcp_runtime_probe.py"
    assert scripts_probe.is_file()
    scripts_probe_bytecode = next(
        (scripts_probe.parent / "__pycache__").glob("ms_mcp_runtime_probe.*.pyc")
    )
    scripts_probe_code = marshal.loads(scripts_probe_bytecode.read_bytes()[16:])
    assert Path(scripts_probe_code.co_filename) == (
        target / ".venv" / "Scripts" / scripts_probe.name
    )
    bytecode_file_names: list[str] = []
    for bytecode_path in target.rglob("*.pyc"):
        bytecode_code = marshal.loads(bytecode_path.read_bytes()[16:])
        assert isinstance(bytecode_code, types.CodeType)
        pending = [bytecode_code]
        while pending:
            code = pending.pop()
            bytecode_file_names.append(code.co_filename)
            pending.extend(
                value for value in code.co_consts if isinstance(value, types.CodeType)
            )
    assert bytecode_file_names
    assert not any(
        re.search(r"[\\/]runtimes[\\/]\.i[0-9a-f]+[\\/]", file_name, re.I)
        for file_name in bytecode_file_names
    )
    for installed_file in (path for path in target.rglob("*") if path.is_file()):
        installed_bytes = installed_file.read_bytes().lower()
        assert b"\\runtimes\\.i" not in installed_bytes
        assert b"/runtimes/.i" not in installed_bytes

    site_packages = target / ".venv" / "Lib" / "site-packages"
    record_paths = sorted(site_packages.glob("*.dist-info/RECORD"))
    assert record_paths
    for record_path in record_paths:
        with record_path.open(encoding="utf-8", newline="") as stream:
            record_rows = list(csv.reader(stream))
        assert record_rows
        record_name = record_path.relative_to(site_packages).as_posix()
        for relative, digest_text, size_text in record_rows:
            if relative.replace("\\", "/") == record_name:
                assert digest_text == size_text == ""
                continue
            recorded = (site_packages / Path(relative.replace("/", os.sep))).resolve(
                strict=True
            )
            assert os.path.commonpath((target.resolve(), recorded)) == str(
                target.resolve()
            )
            content = recorded.read_bytes()
            expected_digest = "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(content).digest()
            ).rstrip(b"=").decode("ascii")
            assert digest_text == expected_digest
            assert size_text == str(len(content))

    published_entrypoint = (
        target / ".venv" / "Scripts" / "ms-mcp-config-doctor.exe"
    )
    entrypoint_help = subprocess.run(
        [published_entrypoint, "--help"],
        cwd=target,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert entrypoint_help.returncode == 0, entrypoint_help.stderr


def test_installer_publish_race_reuses_only_a_verified_winner() -> None:
    installer = (WINDOWS_SCRIPTS / "Install-MS-MCP.ps1").read_text(
        encoding="utf-8"
    )
    assert "[System.IO.Directory]::Move($stagingRuntime, $target)" in installer
    race_handler = installer.split("catch [System.IO.IOException]", 1)[1]
    assert "Test-Path -LiteralPath $target -PathType Container" in race_handler
    assert (
        "Test-MSRuntime -RuntimeRoot $target -Version $version "
        "-ExpectedWheelSha256 $observedWheelHash"
    ) in race_handler
    assert "$runtimeReused = $true" in race_handler
    assert "Remove-Item -LiteralPath $target" not in installer


def test_install_reuses_verified_runtime_and_cache_launcher_binds_version(tmp_path: Path) -> None:
    base = tmp_path / "Local App 数据"
    runner = _fake_runner(tmp_path)
    configured = _configure(base, runner)
    assert configured.returncode == 0, configured.stderr
    wheel = _build_minimal_installer_wheel(tmp_path / "release")

    first = _install(base, wheel)
    assert first.returncode == 0, first.stderr
    second = _install(base, wheel)
    assert second.returncode == 0, second.stderr
    assert "Runtime reused: True" in second.stdout
    runtime = base / "MaterialsStudioMCP" / "runtimes" / VERSION
    manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["python_relative_path"] == ".venv/Scripts/python.exe"
    assert manifest["package_relative_path"] == ".venv/Lib/site-packages/material_studio_mcp_server"
    assert manifest["entrypoint"] == "material_studio_mcp_server.server:main"
    assert int(manifest["dependency_versions"]["mcp"].split(".")[0]) < 2
    assert manifest["dependency_versions"]["comtypes"] == "1.4.16"
    assert manifest["dependency_versions"]["pywinauto"] == "0.6.9"
    assert manifest["distribution"]["public_distribution_ready"] is True
    assert manifest["runtime_tree_excludes"] == ["runtime-manifest.json"]
    assert "configured_runner" not in manifest
    assert "configured_workspace" not in manifest
    assert all(".installing-" not in str(path) for path in runtime.rglob("*"))

    replacement_runner = _fake_runner(tmp_path / "replacement-runner")
    replacement_workspace = tmp_path / "replacement workspace"
    reconfigured = _configure(
        base,
        replacement_runner,
        replacement_workspace,
        force=True,
    )
    assert reconfigured.returncode == 0, reconfigured.stderr
    reused_after_reconfigure = _install(base, wheel)
    assert reused_after_reconfigure.returncode == 0, reused_after_reconfigure.stderr
    assert "Runtime reused: True" in reused_after_reconfigure.stdout
    cache = tmp_path / "Codex Cache 插件" / "materials-studio-mcp"
    shutil.copytree(ROOT / "plugins" / "materials-studio-mcp", cache)
    reconfigured_launch = subprocess.run(
        [
            CMD,
            "/d",
            "/c",
            "Run-MS-MCP.bat",
            "-LocalAppDataRoot",
            str(base),
            "-ValidateOnly",
        ],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert reconfigured_launch.returncode == 0, reconfigured_launch.stderr
    assert reconfigured_launch.stdout == ""

    runtime_manifest_path = runtime / "runtime-manifest.json"
    active_runtime_path = base / "MaterialsStudioMCP" / "config" / "active-runtime.json"
    original_runtime_manifest = runtime_manifest_path.read_bytes()
    original_active_runtime = active_runtime_path.read_bytes()
    stale_dependency_manifest = json.loads(original_runtime_manifest)
    stale_dependency_manifest["dependency_versions"]["mcp"] = "2.0.0"
    runtime_manifest_path.write_text(
        json.dumps(stale_dependency_manifest), encoding="utf-8"
    )
    active_payload = json.loads(original_active_runtime)
    active_payload["runtime_manifest_sha256"] = hashlib.sha256(
        runtime_manifest_path.read_bytes()
    ).hexdigest()
    active_runtime_path.write_text(json.dumps(active_payload), encoding="utf-8")
    stale_reuse = _install(base, wheel)
    assert stale_reuse.returncode != 0
    assert "manifest MCP SDK version" in stale_reuse.stderr

    stale_launch = subprocess.run(
        [
            CMD,
            "/d",
            "/c",
            "Run-MS-MCP.bat",
            "-LocalAppDataRoot",
            str(base),
            "-ValidateOnly",
        ],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert stale_launch.returncode != 0
    assert "manifest MCP SDK version" in stale_launch.stderr
    runtime_manifest_path.write_bytes(original_runtime_manifest)
    active_runtime_path.write_bytes(original_active_runtime)
    for dependency_name in ("comtypes", "pywinauto"):
        stale_uia_manifest = json.loads(original_runtime_manifest)
        stale_uia_manifest["dependency_versions"][dependency_name] = "9.9.9"
        runtime_manifest_path.write_text(
            json.dumps(stale_uia_manifest), encoding="utf-8"
        )
        stale_uia_active = json.loads(original_active_runtime)
        stale_uia_active["runtime_manifest_sha256"] = hashlib.sha256(
            runtime_manifest_path.read_bytes()
        ).hexdigest()
        active_runtime_path.write_text(
            json.dumps(stale_uia_active), encoding="utf-8"
        )
        stale_uia_reuse = _install(base, wheel)
        assert stale_uia_reuse.returncode != 0
        assert "manifest Windows UI dependency versions" in stale_uia_reuse.stderr
        stale_uia_launch = subprocess.run(
            [
                CMD,
                "/d",
                "/c",
                "Run-MS-MCP.bat",
                "-LocalAppDataRoot",
                str(base),
                "-ValidateOnly",
            ],
            cwd=cache,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        assert stale_uia_launch.returncode != 0
        assert "manifest Windows UI dependency versions" in stale_uia_launch.stderr
        runtime_manifest_path.write_bytes(original_runtime_manifest)
        active_runtime_path.write_bytes(original_active_runtime)
    launched = subprocess.run(
        [
            CMD,
            "/d",
            "/c",
            "Run-MS-MCP.bat",
            "-LocalAppDataRoot",
            str(base),
            "-ValidateOnly",
        ],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert launched.returncode == 0, launched.stderr
    assert launched.stdout == ""

    tamper = runtime / "unexpected-runtime-file.txt"
    tamper.write_text("tamper", encoding="utf-8")
    tampered = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(cache / "scripts" / "Run-MS-MCP.ps1"), "-LocalAppDataRoot", str(base), "-ValidateOnly"],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert tampered.returncode != 0
    assert "runtime tree SHA-256 mismatch" in tampered.stderr
    tamper.unlink()

    bytecode = next(runtime.rglob("*.pyc"))
    original_bytecode = bytecode.read_bytes()
    bytecode.write_bytes(original_bytecode + b"tamper")
    tampered_bytecode = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(cache / "scripts" / "Run-MS-MCP.ps1"), "-LocalAppDataRoot", str(base), "-ValidateOnly"],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert tampered_bytecode.returncode != 0
    assert "runtime tree SHA-256 mismatch" in tampered_bytecode.stderr
    bytecode.write_bytes(original_bytecode)

    settings_path = base / "MaterialsStudioMCP" / "config" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    original_settings = json.loads(json.dumps(settings))
    settings["schema"] = "stale_config_schema"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    stale_schema = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(cache / "scripts" / "Run-MS-MCP.ps1"), "-LocalAppDataRoot", str(base), "-ValidateOnly"],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert stale_schema.returncode != 0
    assert "configuration schema is stale" in stale_schema.stderr
    original_settings["package_version"] = "9.9.9"
    settings_path.write_text(json.dumps(original_settings), encoding="utf-8")
    stale_version = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(cache / "scripts" / "Run-MS-MCP.ps1"), "-LocalAppDataRoot", str(base), "-ValidateOnly"],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert stale_version.returncode != 0
    assert "versions differ" in stale_version.stderr
    settings_path.write_text(json.dumps(json.loads(json.dumps(settings)) | {"schema": "materials_studio_mcp_windows_config_v1", "package_version": VERSION}), encoding="utf-8")

    plugin_manifest = cache / ".codex-plugin" / "plugin.json"
    payload = json.loads(plugin_manifest.read_text(encoding="utf-8"))
    payload["version"] = "9.9.9"
    plugin_manifest.write_text(json.dumps(payload), encoding="utf-8")
    mismatch = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(cache / "scripts" / "Run-MS-MCP.ps1"),
            "-LocalAppDataRoot",
            str(base),
            "-ValidateOnly",
        ],
        cwd=cache,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert mismatch.returncode != 0
    assert "cache-local plugin version" in mismatch.stderr


def test_uninstall_dry_run_alias_rejects_traversal_and_preserves_workspace(tmp_path: Path) -> None:
    base = tmp_path / "local"
    runner = _fake_runner(tmp_path)
    workspace = tmp_path / "workspace"
    configured = _configure(base, runner, workspace)
    assert configured.returncode == 0, configured.stderr
    product = base / "MaterialsStudioMCP"
    runtime = product / "runtimes" / VERSION
    wheel = _build_minimal_installer_wheel(tmp_path / "release")
    installed = _install(base, wheel)
    assert installed.returncode == 0, installed.stderr
    sentinel = workspace / "revision-result.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    install_manifest = product / "config" / "install-manifest.json"
    install_payload = json.loads(install_manifest.read_text(encoding="utf-8"))

    dry_run = subprocess.run(
        [CMD, "/d", "/c", str(ROOT / "Uninstall-MS-MCP.bat"), "--dry-run", "-LocalAppDataRoot", str(base)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "DRY RUN" in dry_run.stdout
    assert runtime.exists() and sentinel.exists() and install_manifest.exists()

    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep", encoding="utf-8")
    install_payload["managed_runtime_roots"] = [str(victim)]
    install_manifest.write_text(json.dumps(install_payload), encoding="utf-8")
    traversal = _run_ps("Uninstall-MS-MCP.ps1", "-LocalAppDataRoot", str(base), "-DryRun")
    assert traversal.returncode != 0
    assert "escaped the runtimes root" in traversal.stderr
    assert (victim / "keep.txt").exists()

    forged_runtime = product / "runtimes" / "forged-victim"
    forged_runtime.mkdir()
    forged_sentinel = forged_runtime / "keep.txt"
    forged_sentinel.write_text("keep", encoding="utf-8")
    (forged_runtime / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "materials_studio_mcp_windows_runtime_v1",
                "version": "forged-victim",
                "runtime_root": str(forged_runtime),
                "runtime_tree_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    install_payload["managed_runtime_roots"] = [str(forged_runtime)]
    install_manifest.write_text(json.dumps(install_payload), encoding="utf-8")
    forged = _run_ps("Uninstall-MS-MCP.ps1", "-LocalAppDataRoot", str(base), "-DryRun")
    assert forged.returncode != 0
    assert "Runtime tree SHA-256 mismatch" in forged.stderr
    assert forged_sentinel.read_text(encoding="utf-8") == "keep"

    install_payload["managed_runtime_roots"] = [str(runtime)]
    install_manifest.write_text(json.dumps(install_payload), encoding="utf-8")
    removed = _run_ps(
        "Uninstall-MS-MCP.ps1",
        "-LocalAppDataRoot",
        str(base),
        "-Confirm",
        "-NonInteractive",
    )
    assert removed.returncode == 0, removed.stderr
    assert not runtime.exists()
    assert not install_manifest.exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_real_ms_and_dry_run_long_aliases_are_accepted_by_batch(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            CMD,
            "/d",
            "/c",
            str(ROOT / "Test-MS-MCP.bat"),
            "--real-ms",
            "-NonInteractive",
            "-LocalAppDataRoot",
            str(tmp_path / "missing"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode != 0
    assert "parameter cannot be found" not in result.stderr.lower()


def test_batch_wrappers_preserve_literal_special_character_arguments(
    tmp_path: Path,
) -> None:
    for name in (
        "Configure-MS-MCP.bat",
        "Install-MS-MCP.bat",
        "Test-MS-MCP.bat",
        "Uninstall-MS-MCP.bat",
    ):
        batch_text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "setlocal enableextensions disabledelayedexpansion" in batch_text

    short_root = Path(tempfile.mkdtemp(prefix="ms-bat-", dir=tmp_path.parent))
    base = short_root / "Local App 数据 & literal!"
    runner = _fake_runner(short_root / "Runner 路径 & literal!")
    workspace = short_root / "Workspace 数据 & literal!"
    configured = _run_bat(
        "Configure-MS-MCP.bat",
        "-LocalAppDataRoot",
        str(base),
        "-PythonCommand",
        sys.executable,
        "-Runner",
        str(runner),
        "-MaterialsStudioVersion",
        "20.1",
        "-Workspace",
        str(workspace),
        "-NonInteractive",
    )
    assert configured.returncode == 0, configured.stderr
    assert (base / "MaterialsStudioMCP" / "config" / "settings.json").is_file()

    wheel = _build_minimal_installer_wheel(short_root / "Release 路径 & literal!")
    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    planned = _run_bat(
        "Install-MS-MCP.bat",
        "-LocalAppDataRoot",
        str(base),
        "-WheelPath",
        str(wheel),
        "-WheelSha256",
        wheel_hash,
        "-PlanOnly",
        "-NonInteractive",
    )
    assert planned.returncode == 0, planned.stderr
    plan = json.loads(planned.stdout)
    assert Path(plan["runtime_target"]).resolve() == (
        base / "MaterialsStudioMCP" / "runtimes" / VERSION
    ).resolve()

    safe_failures = (
        _run_bat(
            "Test-MS-MCP.bat",
            "-LocalAppDataRoot",
            str(base),
            "-NonInteractive",
        ),
        _run_bat(
            "Uninstall-MS-MCP.bat",
            "--dry-run",
            "-LocalAppDataRoot",
            str(base),
        ),
    )
    for result in safe_failures:
        assert result.returncode != 0
        assert "parameter cannot be found" not in result.stderr.lower()

    sentinel = short_root / "batch-injection-sentinel.txt"
    injected_path = f"{short_root / 'invalid 数据'} & type nul > {sentinel} & rem literal!"
    injection_attempts = (
        _run_bat(
            "Configure-MS-MCP.bat",
            "-LocalAppDataRoot",
            injected_path,
            "-PythonCommand",
            sys.executable,
            "-Runner",
            str(runner),
            "-MaterialsStudioVersion",
            "20.1",
            "-Workspace",
            str(workspace),
            "-NonInteractive",
        ),
        _run_bat(
            "Install-MS-MCP.bat",
            "-LocalAppDataRoot",
            injected_path,
            "-WheelPath",
            str(wheel),
            "-WheelSha256",
            wheel_hash,
            "-PlanOnly",
            "-NonInteractive",
        ),
        _run_bat(
            "Test-MS-MCP.bat",
            "-LocalAppDataRoot",
            injected_path,
            "-NonInteractive",
        ),
        _run_bat(
            "Uninstall-MS-MCP.bat",
            "--dry-run",
            "-LocalAppDataRoot",
            injected_path,
        ),
    )
    assert all(result.returncode != 0 for result in injection_attempts)
    assert not sentinel.exists()


def test_safe_test_compileall_uses_an_isolated_pycache() -> None:
    text = (WINDOWS_SCRIPTS / "Test-MS-MCP.ps1").read_text(encoding="utf-8")
    assert '-X "pycache_prefix=$compileCache"' in text
    assert '[string]$server.default_tools_approval_mode -ne "prompt"' in text
    assert "must require prompt approval by default" in text
    assert "--invalidation-mode checked-hash" in text
    assert "Remove-Item -LiteralPath $compileCache -Recurse -Force" in text


def test_source_and_built_wheel_public_tools_match_dynamically(tmp_path: Path) -> None:
    wheels = sorted((ROOT / "dist").glob(f"materials_studio_mcp-{VERSION}-py3-none-any.whl"))
    if not wheels:
        pytest.skip("Run python -m build first; parity is exercised again by the release build gate")
    wheel_site = tmp_path / "wheel-site"
    wheel_site.mkdir()
    with zipfile.ZipFile(wheels[0]) as archive:
        for member in archive.infolist():
            target = (wheel_site / member.filename).resolve()
            assert os.path.commonpath((wheel_site.resolve(), target)) == str(
                wheel_site.resolve()
            )
        archive.extractall(wheel_site)

    def decorated_tool_names(source: str) -> set[str]:
        names: set[str] = set()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr != "tool" or not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "mcp":
                    continue
                public_name = node.name
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        public_name = keyword.value.value
                names.add(public_name)
        return names

    main_source = subprocess.run(
        ["git", "show", "origin/main:src/material_studio_mcp_server/server.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if main_source.returncode != 0:
        pytest.skip("origin/main is unavailable for the dynamic public-tool baseline")
    baseline_names = decorated_tool_names(main_source.stdout)
    source_names = decorated_tool_names((ROOT / "src" / "material_studio_mcp_server" / "server.py").read_text(encoding="utf-8"))
    release_tool_additions = {
        "material_studio_gui_loop_status",
        "material_studio_gui_loop_prepare",
        "material_studio_gui_loop_stop",
    }
    assert source_names == baseline_names | release_tool_additions
    probe = f"""
import asyncio, json, sys
sys.path.insert(0, {str(wheel_site)!r})
from material_studio_mcp_server.server import mcp
print(json.dumps(sorted(tool.name for tool in asyncio.run(mcp.list_tools()))))
"""
    encoded = base64.b64encode(probe.encode()).decode()
    command = f"import base64;exec(base64.b64decode('{encoded}'))"
    completed = subprocess.run(
        [sys.executable, "-I", "-c", command],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr
    wheel_names = set(json.loads(completed.stdout))
    assert wheel_names == source_names


def test_built_wheel_native_fit_probe_imports_in_isolated_mode(tmp_path: Path) -> None:
    wheels = sorted((ROOT / "dist").glob(f"materials_studio_mcp-{VERSION}-py3-none-any.whl"))
    if not wheels:
        pytest.skip("Build the project wheel before checking the native Fit probe")
    wheel = wheels[0]
    member = "material_studio_mcp_server/gui_fit_probe.py"
    with zipfile.ZipFile(wheel) as archive:
        assert member in archive.namelist()
    probe = f"""
import json, sys
sys.path.insert(0, {str(wheel)!r})
import material_studio_mcp_server.gui_fit_probe as module
print(json.dumps({{
    "callable": callable(module.inspect_native_fit_target),
    "file": module.__file__,
    "isolated": bool(sys.flags.isolated),
}}))
"""
    encoded = base64.b64encode(probe.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import base64;exec(base64.b64decode('{encoded}'))",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["isolated"] is True
    assert receipt["callable"] is True
    assert receipt["file"].replace("\\", "/") == f"{wheel}/{member}".replace("\\", "/")


def test_built_wheel_metadata_pins_mcp_below_2() -> None:
    wheels = sorted((ROOT / "dist").glob(f"materials_studio_mcp-{VERSION}-py3-none-any.whl"))
    if not wheels:
        pytest.skip("Build the project wheel before checking dependency metadata")
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    mcp_requirements = [line for line in metadata.splitlines() if line.lower().startswith("requires-dist: mcp")]
    assert len(mcp_requirements) == 1
    assert ">=1.12.4" in mcp_requirements[0]
    assert "<2" in mcp_requirements[0]
    requirements = [
        Requirement(line.removeprefix("Requires-Dist:").strip())
        for line in metadata.splitlines()
        if line.lower().startswith("requires-dist:")
    ]
    windows_uia = {requirement.name.lower(): requirement for requirement in requirements}
    assert str(windows_uia["comtypes"].specifier) == "==1.4.16"
    assert str(windows_uia["comtypes"].marker) == 'sys_platform == "win32"'
    assert str(windows_uia["pywinauto"].specifier) == "==0.6.9"
    assert str(windows_uia["pywinauto"].marker) == 'sys_platform == "win32"'


def test_real_wheel_guided_install_and_safe_cache_protocol_smoke(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    if os.environ.get("MS_MCP_RUN_CLEAN_INSTALL") != "1":
        pytest.skip("Set MS_MCP_RUN_CLEAN_INSTALL=1 for the release-grade clean install gate")
    wheels = sorted((ROOT / "dist").glob(f"materials_studio_mcp-{VERSION}-py3-none-any.whl"))
    if not wheels:
        pytest.skip("Build the project wheel before the clean install gate")
    clean_root = Path(tempfile.mkdtemp(prefix="MSMCP-Clean-"))
    request.addfinalizer(lambda: shutil.rmtree(clean_root, ignore_errors=True))
    base = clean_root / "Local 数据"
    runner = _fake_runner(clean_root)
    configured = _configure(base, runner, clean_root / "用户 workspace")
    assert configured.returncode == 0, configured.stderr
    installed = _install(base, wheels[0])
    assert installed.returncode == 0, f"stdout:\n{installed.stdout}\nstderr:\n{installed.stderr}"

    cache = clean_root / "Codex Plugin Cache" / "materials-studio-mcp"
    shutil.copytree(ROOT / "plugins" / "materials-studio-mcp", cache)
    tested = subprocess.run(
        [
            CMD,
            "/d",
            "/c",
            "Test-MS-MCP.bat",
            "-LocalAppDataRoot",
            str(base),
            "-PluginRoot",
            str(cache),
            "-ReleaseRoot",
            str(ROOT),
            "-NonInteractive",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert tested.returncode == 0, f"stdout:\n{tested.stdout}\nstderr:\n{tested.stderr}"
    assert "Real Materials Studio: NOT_RUN" in tested.stdout
    assert "Real CASTEP: NOT_RUN" in tested.stdout
    assert "material_studio_run_script is disabled" in tested.stdout
    assert "PASS: GUI status preserved the immutable runtime tree" in tested.stdout
