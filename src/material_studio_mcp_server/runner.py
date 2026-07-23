"""MaterialsScript 作业的子进程运行器。

此模块提供了通过本地 MS runner 执行 MaterialsScript Perl 程序的功能。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from html import unescape
from typing import Any

from .config import MaterialStudioConfig, _split_windows_args, resolve_config, runner_candidates
from .parsers.tagged_json import extract_any_tagged_json


# 标记 JSON 的开始和结束标记
JSON_BEGIN = "__MATERIAL_STUDIO_MCP_JSON_BEGIN__"
JSON_END = "__MATERIAL_STUDIO_MCP_JSON_END__"

# 默认作业目录
DEFAULT_JOBS_DIR = ".material-studio-mcp/jobs"


class MaterialStudioError(RuntimeError):
    """当 Materials Studio 自动化无法完成时引发。"""


@dataclass(frozen=True)
class ScriptRunResult:
    """MaterialsScript 子进程运行结果。

    属性:
        command: 执行的命令
        job_id: 作业 ID
        job_dir: 作业目录
        script_path: 脚本路径
        return_code: 返回码
        stdout: 标准输出
        stderr: 标准错误
        output_file: 输出文件
        log_file: 日志文件
        materials_output: Materials Studio 输出
        materials_log: Materials Studio 日志
        success: 是否成功
        timed_out: 是否超时
        parsed_json: 解析的 JSON
        created_files: 创建的文件列表
        duration_seconds: 持续时间（秒）
    """

    command: list[str]
    job_id: str
    job_dir: Path
    script_path: Path
    return_code: int
    stdout: str
    stderr: str
    output_file: Path | None
    log_file: Path | None
    materials_output: str
    materials_log: str
    success: bool
    timed_out: bool
    parsed_json: Any | None
    created_files: list[Path]
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 可序列化的表示。"""

        return {
            "command": self.command,
            "job_id": self.job_id,
            "job_dir": str(self.job_dir),
            "script_path": str(self.script_path),
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_file": str(self.output_file) if self.output_file else None,
            "log_file": str(self.log_file) if self.log_file else None,
            "materials_output": self.materials_output,
            "materials_log": self.materials_log,
            "success": self.success,
            "timed_out": self.timed_out,
            "parsed_json": self.parsed_json,
            "created_files": [str(path) for path in self.created_files],
            "duration_seconds": self.duration_seconds,
        }


class MaterialStudioRunner:
    """通过本地 MS runner 启动 MaterialsScript Perl 程序。"""

    def __init__(self, config: MaterialStudioConfig | None = None) -> None:
        """初始化 runner。

        参数:
            config: Materials Studio 配置，如果为 None 则自动解析
        """
        self.config = config or resolve_config()

    def status(self) -> dict[str, Any]:
        """返回 runner 检测和工作区状态。

        返回:
            状态字典
        """
        runner = self.config.runner
        return {
            "connected": bool(runner and runner.exists()),
            "runner": str(runner) if runner else None,
            "runner_exists": bool(runner and runner.exists()),
            "runner_source": self.config.runner_source,
            "install_home": str(self.config.install_home) if self.config.install_home else None,
            "workspace_root": str(self.config.workspace_root),
            "default_timeout_seconds": self.config.default_timeout_seconds,
            "extra_runner_args": list(self.config.extra_runner_args),
            "searched_candidates": [str(path) for path in runner_candidates()[:25]],
            "searched_candidate_count": len(runner_candidates()),
            "notes": [
                "Materials Studio 2020 通过 MaterialsScript Perl 启动器支持。",
                "如果 runner 安装在自定义位置，请设置 MATERIAL_STUDIO_RUNNER。",
            ],
        }

    def run_script(
        self,
        script: str,
        *,
        args: list[str] | None = None,
        working_dir: str | Path | None = None,
        timeout_seconds: int | None = None,
        job_prefix: str = "msjob",
        keep_script_name: str = "script.pl",
    ) -> ScriptRunResult:
        """将脚本写入隔离的作业目录并启动它。

        参数:
            script: 脚本内容
            args: 命令行参数
            working_dir: 工作目录
            timeout_seconds: 超时时间
            job_prefix: 作业前缀
            keep_script_name: 保存的脚本名称

        返回:
            ScriptRunResult 实例

        异常:
            MaterialStudioError: 如果 runner 未找到
        """
        runner = self.config.runner
        if not runner or not runner.exists():
            raise MaterialStudioError(
                "未找到 Materials Studio runner。请为您的 Materials Studio 2020 安装设置 "
                "MATERIAL_STUDIO_RUNNER 为 RunMatserver.bat 或 RunMatScript.bat。"
            )

        # 创建作业目录
        job_dir = self._create_job_dir(working_dir, job_prefix)
        before_files = _snapshot_files(job_dir)
        script_path = job_dir / keep_script_name
        script_path.write_text(script, encoding="utf-8")

        # 构建命令
        command = self._build_command(runner, script_path, args or [])
        timeout = timeout_seconds or self.config.default_timeout_seconds
        env = os.environ.copy()
        env.setdefault("MATERIAL_STUDIO_MCP_JOB_DIR", str(job_dir))

        # 执行脚本
        start_time = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=str(job_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            return_code = -1
            timed_out = True
        duration_seconds = time.monotonic() - start_time

        # 读取输出文件
        output_file = script_path.with_suffix(script_path.suffix + ".out")
        log_file = job_dir / f"{script_path.stem}MatStudioLog.htm"
        materials_output = _read_text_if_exists(output_file)
        materials_log = _read_text_if_exists(log_file)
        combined_output = "\n".join(part for part in (stdout, materials_output) if part)
        success = (not timed_out) and _materials_run_succeeded(return_code, materials_output, materials_log)
        created_files = sorted(_snapshot_files(job_dir) - before_files)

        return ScriptRunResult(
            command=command,
            job_id=job_dir.name,
            job_dir=job_dir,
            script_path=script_path,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            output_file=output_file if output_file.exists() else None,
            log_file=log_file if log_file.exists() else None,
            materials_output=materials_output,
            materials_log=_html_to_text(materials_log),
            success=success,
            timed_out=timed_out,
            parsed_json=extract_tagged_json(combined_output),
            created_files=created_files,
            duration_seconds=duration_seconds,
        )

    def _build_command(self, runner: Path, script_path: Path, args: list[str]) -> list[str]:
        """构建命令行。

        参数:
            runner: runner 路径
            script_path: 脚本路径
            args: 命令行参数

        返回:
            命令行列表
        """
        template = os.environ.get("MATERIAL_STUDIO_COMMAND_TEMPLATE")
        if template:
            mapping = {
                "runner": str(runner),
                "script": script_path.stem,
                "script_path": str(script_path),
                "args": subprocess.list2cmdline(args),
            }
            command_line = template.format(**mapping)
            return _split_windows_args(command_line)

        script_arg = script_path.stem if runner.name.lower() == "runmatscript.bat" else str(script_path)
        command = [str(runner), *self.config.extra_runner_args, script_arg]
        if args:
            command.append("--")
            command.extend(args)
        return command

    def _create_job_dir(self, working_dir: str | Path | None, job_prefix: str) -> Path:
        """创建作业目录。

        参数:
            working_dir: 工作目录
            job_prefix: 作业前缀

        返回:
            作业目录路径
        """
        base = Path(working_dir).expanduser().resolve() if working_dir else self.config.workspace_root
        jobs_root = base / DEFAULT_JOBS_DIR
        jobs_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", job_prefix).strip("._") or "msjob"
        job_dir = jobs_root / f"{safe_prefix}-{stamp}-{uuid.uuid4().hex[:8]}"
        job_dir.mkdir(parents=False, exist_ok=False)
        return job_dir


def extract_tagged_json(output: str) -> Any | None:
    """提取在 MCP 标记之间发出的 JSON 块。

    参数:
        output: 输出文本

    返回:
        解析的 JSON，如果未找到则返回 None
    """
    return extract_any_tagged_json(output)


def perl_string(value: str | Path) -> str:
    """返回 Perl 单引号字符串字面量。

    参数:
        value: 输入值

    返回:
        Perl 字符串字面量
    """
    raw = str(value)
    return "'" + raw.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _read_text_if_exists(path: Path) -> str:
    """如果文件存在则读取文本。

    参数:
        path: 文件路径

    返回:
        文件内容，如果文件不存在则返回空字符串
    """
    if not path.exists():
        return ""
    for encoding in ("utf-8", "mbcs", "latin-1"):
        try:
            return path.read_text(encoding=encoding, errors="replace")
        except LookupError:
            continue
        except OSError:
            return ""
    return ""


def _snapshot_files(path: Path) -> set[Path]:
    """获取目录中的文件快照。

    参数:
        path: 目录路径

    返回:
        文件路径集合
    """
    if not path.exists():
        return set()
    return {item.resolve() for item in path.rglob("*") if item.is_file()}


def _materials_run_succeeded(return_code: int, output: str, log_html: str) -> bool:
    """判断 Materials Studio 运行是否成功。

    参数:
        return_code: 返回码
        output: 输出文本
        log_html: 日志 HTML

    返回:
        是否成功
    """
    if return_code != 0:
        return False
    combined = f"{output}\n{log_html}".lower()
    failure_markers = (
        "completion status: (fail)",
        "exiting matserver: status failed",
        "couldn't parse the script",
        "syntax error",
        "execution of -e aborted",
    )
    return not any(marker in combined for marker in failure_markers)


def _html_to_text(value: str) -> str:
    """将 HTML 转换为文本。

    参数:
        value: HTML 文本

    返回:
        纯文本
    """
    if not value:
        return ""
    text = re.sub(r"(?is)<br\s*/?>", "\n", value)
    text = re.sub(r"(?is)</tr>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
