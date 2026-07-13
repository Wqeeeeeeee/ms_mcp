"""Materials Studio 配置和 runner 检测模块。

此模块负责：
1. 从环境变量加载运行时配置
2. 自动探测本机安装的 Materials Studio
3. 查找可用的脚本执行器 (RunMatScript.bat 等)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


# 脚本模式类型定义：支持 Perl 和 Python 两种模式
ScriptMode = Literal["perl", "python"]


@dataclass(frozen=True)
class Settings:
    """从环境变量加载的运行时设置。

    属性:
        workspace: 工作目录路径，用于存放生成的作业文件
        script_mode: 脚本执行模式，可选 "perl" 或 "python"
        script_runner: 脚本执行器路径（如 RunMatScript.bat）
        version: Materials Studio 版本号
    """

    workspace: str
    script_mode: ScriptMode = "perl"
    script_runner: str | None = None
    version: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量创建 Settings 实例。

        环境变量:
            MS_WORKSPACE: 工作目录路径
            MS_SCRIPT_MODE: 脚本模式 ("perl" 或 "python")
            MS_SCRIPT_RUNNER: 脚本执行器路径
            MS_VERSION: Materials Studio 版本号

        返回:
            Settings 实例
        """
        workspace = os.environ.get("MS_WORKSPACE") or str(Path.cwd() / "workspace")
        script_mode = os.environ.get("MS_SCRIPT_MODE", "perl").strip().lower()
        if script_mode not in {"perl", "python"}:
            raise ValueError(f"不支持的 MS_SCRIPT_MODE: {script_mode}")

        configured_runner = os.environ.get("MS_SCRIPT_RUNNER")
        script_runner = configured_runner or discover_default_script_runner(script_mode)
        version = os.environ.get("MS_VERSION")

        return cls(
            workspace=workspace,
            script_mode=script_mode,  # type: ignore[arg-type]
            script_runner=script_runner,
            version=version,
        )


def discover_default_script_runner(script_mode: str = "perl") -> str | None:
    """返回请求的脚本模式的第一个检测到的执行器。

    参数:
        script_mode: 脚本模式，"perl" 或 "python"

    返回:
        执行器路径，如果未找到则返回 None
    """
    detected = discover_materials_studio()
    runner_key = "perl_runner" if script_mode == "perl" else "python_runner"
    for install in detected:
        runner = install.get(runner_key)
        if runner:
            return str(runner)
    return None


def discover_materials_studio() -> list[dict[str, Any]]:
    """检测已安装的 BIOVIA Materials Studio 根目录，不使用硬编码路径。

    返回:
        包含安装信息的字典列表，每个字典包含:
        - root: 安装根目录
        - version_hint: 版本提示
        - matstudio_exe: MatStudio.exe 路径
        - perl_runner: Perl 执行器路径
        - python_runner: Python 执行器路径
    """
    candidates: list[dict[str, Any]] = []
    candidates.extend(_discover_from_registry())
    candidates.extend(_discover_from_environment())

    # 去重处理
    seen: set[str] = set()
    detected: list[dict[str, Any]] = []
    for candidate in candidates:
        root = candidate.get("root")
        key = str(root).lower() if root else repr(candidate)
        if key in seen:
            continue
        seen.add(key)
        detected.append(candidate)
    return detected


def _discover_from_environment() -> list[dict[str, Any]]:
    """从环境变量探测 Materials Studio 安装。

    检查的环境变量:
        - MS_INSTALL_ROOT
        - BIOVIA_MS_ROOT

    返回:
        安装信息列表
    """
    roots: list[Path] = []
    for key in ("MS_INSTALL_ROOT", "BIOVIA_MS_ROOT"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    return [_describe_root(root) for root in roots if root.exists()]


def _discover_from_registry() -> list[dict[str, Any]]:
    """从 Windows 注册表探测 Materials Studio 安装。

    仅在 Windows 系统上有效。

    返回:
        安装信息列表
    """
    if os.name != "nt":
        return []

    try:
        import winreg
    except ImportError:
        return []

    # 卸载注册表路径
    uninstall_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    ]

    found: list[dict[str, Any]] = []
    for hive, key_path in uninstall_roots:
        try:
            with winreg.OpenKey(hive, key_path) as uninstall_key:
                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(uninstall_key, index)
                    except OSError:
                        break
                    index += 1
                    found.extend(_read_uninstall_entry(winreg, uninstall_key, subkey_name))
        except OSError:
            continue
    return found


def _read_uninstall_entry(winreg: Any, uninstall_key: Any, subkey_name: str) -> list[dict[str, Any]]:
    """读取单个卸载注册表条目。

    参数:
        winreg: Windows 注册表模块
        uninstall_key: 卸载注册表键
        subkey_name: 子键名称

    返回:
        安装信息列表
    """
    try:
        with winreg.OpenKey(uninstall_key, subkey_name) as subkey:
            display_name = _query_registry_value(winreg, subkey, "DisplayName")
            if not display_name or "materials studio" not in display_name.lower():
                return []
            display_version = _query_registry_value(winreg, subkey, "DisplayVersion")
            install_location = _query_registry_value(winreg, subkey, "InstallLocation")
    except OSError:
        return []

    roots = _candidate_roots_from_install_location(install_location)
    described = []
    for root in roots:
        detail = _describe_root(root)
        detail["display_name"] = display_name
        detail["display_version"] = display_version
        detail["install_location"] = install_location
        described.append(detail)
    return described


def _query_registry_value(winreg: Any, key: Any, name: str) -> str | None:
    """查询注册表值。

    参数:
        winreg: Windows 注册表模块
        key: 注册表键
        name: 值名称

    返回:
        值字符串，如果不存在则返回 None
    """
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value is not None else None


def _candidate_roots_from_install_location(install_location: str | None) -> list[Path]:
    """从安装位置推断候选根目录。

    参数:
        install_location: 安装位置路径

    返回:
        候选根目录列表
    """
    if not install_location:
        return []

    root = Path(install_location)
    roots: list[Path] = []
    if root.exists() and "materials studio" in root.name.lower():
        roots.append(root)
    if root.exists():
        roots.extend(
            child
            for child in root.iterdir()
            if child.is_dir() and child.name.lower().startswith("materials studio")
        )
    return roots


def _describe_root(root: Path) -> dict[str, Any]:
    """描述一个 Materials Studio 安装根目录。

    参数:
        root: 安装根目录路径

    返回:
        包含安装信息的字典
    """
    perl_runner = root / "etc" / "Scripting" / "bin" / "RunMatScript.bat"
    python_runner = root / "etc" / "Scripting" / "bin" / "RunMatScript.py"
    matstudio_exe = root / "bin" / "MatStudio.exe"
    version = _version_from_root_name(root.name)

    return {
        "root": str(root),
        "version_hint": version,
        "matstudio_exe": str(matstudio_exe) if matstudio_exe.exists() else None,
        "perl_runner": str(perl_runner) if perl_runner.exists() else None,
        "python_runner": str(python_runner) if python_runner.exists() else None,
    }


def _version_from_root_name(name: str) -> str | None:
    """从根目录名称提取版本号。

    参数:
        name: 目录名称

    返回:
        版本号字符串，如果未找到则返回 None
    """
    match = re.search(r"(\d+(?:\.\d+)?)", name)
    return match.group(1) if match else None
