"""Materials Studio Python 脚本适配器。

此模块提供了执行 Materials Studio Python MaterialsScript 的功能。
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ms_mcp.adapters.base import MaterialsStudioAdapter
from ms_mcp.config import Settings
from ms_mcp.parsers.reports import parse_job_report, write_report


class MaterialsScriptPythonAdapter(MaterialsStudioAdapter):
    """未来兼容的 Materials Studio Python 脚本适配器。

    此适配器使用 Jinja2 模板生成 Python 脚本，并通过 RunMatScript.py 执行。
    """

    def __init__(self, settings: Settings):
        """初始化 Python 适配器。

        参数:
            settings: 运行时设置
        """
        self.settings = settings
        template_dir = Path(__file__).parents[1] / "templates" / "ms_python"
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def create_structure(self, spec: dict[str, Any], job_dir: Path) -> Path:
        """创建结构文件（未实现）。

        参数:
            spec: 结构规格字典
            job_dir: 作业目录路径

        异常:
            NotImplementedError: 在 CIF-first MVP 中未实现
        """
        raise NotImplementedError("Python create_structure 在 CIF-first MVP 中未实现。")

    def render_geometry_optimization_script(
        self,
        structure_path: Path,
        settings: dict[str, Any],
        job_dir: Path,
    ) -> str:
        """渲染几何优化 Python 脚本。

        参数:
            structure_path: 结构文件路径
            settings: 运行设置
            job_dir: 作业目录路径

        返回:
            生成的 Python 脚本内容
        """
        return self.env.get_template("geometry_opt.py.j2").render(
            structure_path=str(structure_path),
            settings=settings,
            job_dir=str(job_dir),
            result_path=str(job_dir / "result.xsd"),
        )

    def run_geometry_optimization(
        self,
        structure_path: Path,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """运行几何优化。

        参数:
            structure_path: 结构文件路径
            settings: 运行设置

        返回:
            包含作业信息的字典
        """
        job_id = f"opt_{uuid.uuid4().hex[:8]}"
        job_dir = Path(self.settings.workspace) / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # 保存规格文件
        (job_dir / "spec.json").write_text(
            json.dumps(
                {
                    "structure_path": str(structure_path),
                    "settings": settings,
                    "script_mode": "python",
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 生成并保存脚本
        script_text = self.render_geometry_optimization_script(structure_path, settings, job_dir)
        script_path = job_dir / "geometry_opt.py"
        script_path.write_text(script_text, encoding="utf-8")

        # 执行脚本
        result = self.run_script(script_path, job_dir, timeout_sec=7200)
        result["job_id"] = job_id
        result["job_dir"] = str(job_dir)

        # 解析并保存报告
        report = parse_job_report(job_dir)
        write_report(job_dir, report)
        result["report"] = report
        result["report_path"] = str(job_dir / "report.json")
        return result

    def run_script(
        self,
        script_path: Path,
        job_dir: Path,
        timeout_sec: int = 3600,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行 Python 脚本。

        参数:
            script_path: 脚本文件路径
            job_dir: 作业目录路径
            timeout_sec: 超时时间（秒）
            args: 命令行参数列表

        返回:
            包含执行结果的字典
        """
        runner = self.settings.script_runner
        if not runner:
            return _write_skipped_execution(job_dir, script_path, "MS_SCRIPT_RUNNER 未配置。")
        if not Path(runner).exists():
            return _write_skipped_execution(job_dir, script_path, f"MS_SCRIPT_RUNNER 不存在: {runner}")

        command = [runner] + (args or [str(script_path)])
        try:
            completed = subprocess.run(
                command,
                cwd=str(job_dir),
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            stderr = stderr + f"\n超时: {timeout_sec} 秒。"
            returncode = 124

        _write_execution_logs(job_dir, stdout, stderr, returncode)
        return {
            "returncode": returncode,
            "stdout_log": str(job_dir / "stdout.log"),
            "stderr_log": str(job_dir / "stderr.log"),
            "script": str(script_path),
            "command": command,
            "expected_output": str(job_dir / "result.xsd"),
        }


def _write_skipped_execution(job_dir: Path, script_path: Path, message: str) -> dict[str, Any]:
    """写入跳过的执行记录。

    参数:
        job_dir: 作业目录路径
        script_path: 脚本文件路径
        message: 跳过原因

    返回:
        包含执行信息的字典
    """
    _write_execution_logs(job_dir, "", message, 127)
    return {
        "returncode": 127,
        "stdout_log": str(job_dir / "stdout.log"),
        "stderr_log": str(job_dir / "stderr.log"),
        "script": str(script_path),
        "command": None,
        "expected_output": str(job_dir / "result.xsd"),
    }


def _write_execution_logs(job_dir: Path, stdout: str, stderr: str, returncode: int) -> None:
    """写入执行日志。

    参数:
        job_dir: 作业目录路径
        stdout: 标准输出内容
        stderr: 标准错误内容
        returncode: 返回码
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (job_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    (job_dir / "returncode.txt").write_text(str(returncode), encoding="utf-8")
