"""Materials Studio 配置和 runner 检测模块。

此模块负责：
1. 从环境变量解析 Materials Studio 配置
2. 检测本机安装的 Materials Studio
3. 查找可用的脚本执行器
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Runner 相关的环境变量
RUNNER_ENV_VARS = (
    "MATERIAL_STUDIO_RUNNER",
    "MS_RUNNER",
    "BIOVIA_MATERIALS_STUDIO_RUNNER",
)

# 安装目录相关的环境变量
HOME_ENV_VARS = (
    "MATERIAL_STUDIO_HOME",
    "MS_HOME",
    "BIOVIA_MATERIALS_STUDIO_HOME",
)

# 可能的 runner 文件名
RUNNER_NAMES = (
    "RunMatserver.bat",
    "RunMatServer.bat",
    "RunMatScript.bat",
    "MaterialsScript.bat",
)

# 常见的安装根目录
COMMON_INSTALL_ROOTS = (
    r"C:\Program Files\BIOVIA",
    r"C:\Program Files (x86)\BIOVIA",
    r"D:\Program Files\BIOVIA",
    r"D:\Program Files (x86)\BIOVIA",
    r"E:\Program Files\BIOVIA",
    r"E:\Program Files (x86)\BIOVIA",
    r"C:\Program Files\Dassault Systemes",
    r"C:\Program Files (x86)\Dassault Systemes",
    r"D:\Program Files\Dassault Systemes",
    r"D:\Program Files (x86)\Dassault Systemes",
    r"C:\Program Files\Accelrys",
    r"C:\Program Files (x86)\Accelrys",
    r"D:\Program Files\Accelrys",
    r"D:\Program Files (x86)\Accelrys",
)

# 已知的版本名称
VERSION_NAMES = (
    "Materials Studio 20.1 x64 Server",
    "Materials Studio 20.1",
    "Materials Studio 2020",
    "Materials Studio 2020 x64 Server",
    "MaterialsStudio2020",
    "Materials Studio 2020 Client",
)

GUI_HOTLOAD_TRANSPORTS = ("auto", "loop", "dialog")
DEFAULT_GUI_HOTLOAD_TRANSPORT = "auto"
DEFAULT_GUI_LOOP_TIMEOUT_SECONDS = 45
DEFAULT_GUI_LOOP_HEARTBEAT_TTL_SECONDS = 10


@dataclass(frozen=True)
class MaterialStudioConfig:
    """解析后的 Materials Studio 配置。

    属性:
        runner: runner 可执行文件路径
        workspace_root: 工作区根目录
        default_timeout_seconds: 默认超时时间（秒）
        install_home: 安装目录
        runner_source: runner 来源
        extra_runner_args: 额外的 runner 参数
    """

    runner: Path | None
    workspace_root: Path
    default_timeout_seconds: int
    install_home: Path | None
    runner_source: str
    extra_runner_args: tuple[str, ...]
    gui_hotload_transport: str = DEFAULT_GUI_HOTLOAD_TRANSPORT
    gui_loop_timeout_seconds: int = DEFAULT_GUI_LOOP_TIMEOUT_SECONDS
    gui_loop_heartbeat_ttl_seconds: int = DEFAULT_GUI_LOOP_HEARTBEAT_TTL_SECONDS


def resolve_config(cwd: Path | None = None) -> MaterialStudioConfig:
    """从环境变量和常见路径解析 Materials Studio 配置。

    参数:
        cwd: 当前工作目录，默认为进程当前目录

    返回:
        MaterialStudioConfig 实例
    """
    cwd = (cwd or Path.cwd()).resolve()
    workspace_value = (
        os.environ.get("MATERIAL_STUDIO_WORKSPACE")
        or os.environ.get("MATERIAL_STUDIO_MCP_WORKSPACE")
        or str(cwd)
    )
    workspace_root = Path(workspace_value).expanduser().resolve()
    timeout = _parse_timeout(os.environ.get("MATERIAL_STUDIO_SCRIPT_TIMEOUT"))
    gui_hotload_transport = _parse_gui_hotload_transport(
        os.environ.get("MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT")
    )
    gui_loop_timeout_seconds = _parse_positive_int(
        os.environ.get("MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS"),
        default=DEFAULT_GUI_LOOP_TIMEOUT_SECONDS,
    )
    gui_loop_heartbeat_ttl_seconds = _parse_positive_int(
        os.environ.get("MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS"),
        default=DEFAULT_GUI_LOOP_HEARTBEAT_TTL_SECONDS,
    )
    install_home = _resolve_install_home()
    runner, source = _resolve_runner(install_home)
    extra_runner_args = tuple(_split_windows_args(os.environ.get("MATERIAL_STUDIO_RUNNER_ARGS", "")))
    return MaterialStudioConfig(
        runner=runner,
        workspace_root=workspace_root,
        default_timeout_seconds=timeout,
        install_home=install_home,
        runner_source=source,
        extra_runner_args=extra_runner_args,
        gui_hotload_transport=gui_hotload_transport,
        gui_loop_timeout_seconds=gui_loop_timeout_seconds,
        gui_loop_heartbeat_ttl_seconds=gui_loop_heartbeat_ttl_seconds,
    )


def runner_candidates() -> list[Path]:
    """返回可能的 Materials Studio runner 路径，不检查是否存在。

    返回:
        候选路径列表
    """
    candidates: list[Path] = []
    for env_var in RUNNER_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            candidates.append(Path(value))

    install_home = _resolve_install_home()
    if install_home:
        candidates.extend(_runner_candidates_for_home(install_home))

    for root in COMMON_INSTALL_ROOTS:
        root_path = Path(root)
        for version_name in VERSION_NAMES:
            candidates.extend(_runner_candidates_for_home(root_path / version_name))
        if root_path.exists():
            try:
                homes = root_path.glob("Materials Studio*")
            except OSError:
                homes = []
            for home in homes:
                candidates.extend(_runner_candidates_for_home(home))

    return _dedupe_paths(candidates)


def _resolve_runner(install_home: Path | None) -> tuple[Path | None, str]:
    """解析 runner 路径。

    参数:
        install_home: 安装目录

    返回:
        (runner 路径, 来源) 元组
    """
    for env_var in RUNNER_ENV_VARS:
        value = os.environ.get(env_var)
        if not value:
            continue
        runner = Path(value).expanduser().resolve()
        if runner.exists():
            return runner, env_var
        return runner, f"{env_var} (missing)"

    if install_home:
        found = _first_existing(_runner_candidates_for_home(install_home))
        if found:
            return found, "MATERIAL_STUDIO_HOME"

    found = _first_existing(runner_candidates())
    if found:
        return found, "common_install_paths"
    return None, "not_found"


def _resolve_install_home() -> Path | None:
    """解析安装目录。

    返回:
        安装目录路径，如果未找到则返回 None
    """
    for env_var in HOME_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return Path(value).expanduser().resolve()
    return None


def _runner_candidates_for_home(home: Path) -> list[Path]:
    """为给定的安装目录生成 runner 候选路径。

    参数:
        home: 安装目录

    返回:
        候选路径列表
    """
    subdirs = (
        Path("etc") / "Scripting" / "bin",
        Path("bin"),
        Path("share") / "bin",
        Path("Scripts"),
        Path(""),
    )
    return [home / subdir / name for subdir in subdirs for name in RUNNER_NAMES]


def _first_existing(paths: Iterable[Path]) -> Path | None:
    """返回第一个存在的路径。

    参数:
        paths: 路径迭代器

    返回:
        第一个存在的路径，如果都不存在则返回 None
    """
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists():
            return resolved
    return None


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    """去重路径列表。

    参数:
        paths: 路径迭代器

    返回:
        去重后的路径列表
    """
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _parse_timeout(raw: str | None) -> int:
    """解析超时时间。

    参数:
        raw: 原始超时字符串

    返回:
        超时时间（秒），默认 3600
    """
    if raw is None or not raw.strip():
        return 3600
    try:
        value = int(raw)
    except ValueError:
        return 3600
    return max(1, min(value, 7 * 24 * 3600))


def _parse_gui_hotload_transport(raw: str | None) -> str:
    if raw is None:
        return DEFAULT_GUI_HOTLOAD_TRANSPORT
    value = raw.strip().lower()
    if value in GUI_HOTLOAD_TRANSPORTS:
        return value
    return DEFAULT_GUI_HOTLOAD_TRANSPORT


def _parse_positive_int(raw: str | None, *, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _split_windows_args(raw: str) -> list[str]:
    """分割 Windows 命令行参数。

    参数:
        raw: 原始命令行字符串

    返回:
        参数列表
    """
    if not raw.strip():
        return []
    if os.name == "nt":
        return _command_line_to_argv(raw)

    import shlex

    return shlex.split(raw)


def _command_line_to_argv(raw: str) -> list[str]:
    """使用 Windows shell 解析器解析命令行参数。

    参数:
        raw: 原始命令行字符串

    返回:
        参数列表
    """
    import ctypes

    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = shell32.CommandLineToArgvW(raw, ctypes.byref(argc))
    if not argv:
        raise ValueError("无法解析命令行参数")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        kernel32.LocalFree(argv)
