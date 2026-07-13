"""Materials Studio MCP 服务器主模块。

此模块提供了用于自动化 BIOVIA Materials Studio 的 MCP 工具，包括：
- 结构构建和验证
- 几何优化
- 结果解析和诊断
- GUI 控制
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ms_mcp.adapters.materialscript_perl import MaterialsScriptPerlAdapter
from ms_mcp.adapters.materialscript_py import MaterialsScriptPythonAdapter
from ms_mcp.builders.crystal import write_cif
from ms_mcp.config import Settings, discover_materials_studio
from ms_mcp.interfaces.cu_sio2 import build_cu_sio2_interface_spec
from ms_mcp.parsers.reports import diagnose_report, parse_job_report, write_report
from ms_mcp.schemas import CrystalSpec, CuSiO2InterfaceSpec, RunSettings
from ms_mcp.validators.structure import validate_structure_file

# 配置日志
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# 创建 MCP 服务器实例
mcp = FastMCP(
    "materials-studio",
    instructions=(
        "优先使用结构化的 MaterialsScript 工具。"
        "仅在需要视觉验证、对话框故障排除或 MaterialsScript 无法暴露所需状态时使用 GUI/计算机视觉。"
        "除非 output_path 是明确的，否则不要覆盖用户文件。"
    ),
)


def get_adapter(settings: Settings | None = None):
    """根据配置获取适当的脚本适配器。

    参数:
        settings: 运行时设置，如果为 None 则从环境变量加载

    返回:
        MaterialsScriptPerlAdapter 或 MaterialsScriptPythonAdapter 实例

    异常:
        ValueError: 如果脚本模式不受支持
    """
    settings = settings or Settings.from_env()
    if settings.script_mode == "python":
        return MaterialsScriptPythonAdapter(settings)
    if settings.script_mode == "perl":
        return MaterialsScriptPerlAdapter(settings)
    raise ValueError(f"不支持的 MS_SCRIPT_MODE: {settings.script_mode}")


@mcp.tool()
def probe_environment() -> dict[str, Any]:
    """检查 Materials Studio runner、工作区、脚本模式和写入权限。

    返回:
        包含环境信息的字典，包括:
        - workspace: 工作区路径
        - workspace_writable: 工作区是否可写
        - script_mode: 脚本模式
        - script_runner: 脚本执行器路径
        - runner_exists: 执行器是否存在
        - version: 版本号
        - detected_materials_studio: 检测到的 Materials Studio 安装列表
    """
    settings = Settings.from_env()
    workspace = Path(settings.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    detected = discover_materials_studio()

    return {
        "workspace": str(workspace),
        "workspace_writable": os.access(workspace, os.W_OK),
        "script_mode": settings.script_mode,
        "script_runner": settings.script_runner,
        "runner_exists": Path(settings.script_runner).exists() if settings.script_runner else False,
        "version": settings.version,
        "detected_materials_studio": detected,
    }


@mcp.tool()
def build_crystal(spec: CrystalSpec) -> dict[str, Any]:
    """从晶格参数和分数坐标创建初始 CIF 文件。

    参数:
        spec: 晶体结构规格，包含:
            - name: 结构名称
            - lattice: 晶格参数 (a, b, c, alpha, beta, gamma)
            - atoms: 原子列表，每个原子包含元素和分数坐标
            - space_group: 空间群（可选）

    返回:
        包含以下信息的字典:
        - doc_id: 文档 ID
        - job_id: 作业 ID
        - job_dir: 作业目录
        - path: CIF 文件路径
        - spec_path: 规格文件路径
        - report_path: 报告文件路径
        - validation: 验证结果
    """
    settings = Settings.from_env()
    workspace = Path(settings.workspace)
    job_id = f"build_{_safe_id(spec.name)}_{uuid.uuid4().hex[:8]}"
    job_dir = workspace / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)

    # 保存规格文件
    spec_path = job_dir / "spec.json"
    spec_path.write_text(json.dumps(spec.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")

    # 生成 CIF 文件
    output_path = job_dir / "input.cif"
    write_cif(spec.model_dump(), output_path)
    validation = validate_structure_file(output_path)

    # 生成报告
    report = {
        "job_dir": str(job_dir),
        "status": "success" if validation["ok"] else "failed",
        "structure_path": str(output_path),
        "validation": validation,
    }
    write_report(job_dir, report)

    return {
        "doc_id": job_id,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "path": str(output_path),
        "spec_path": str(spec_path),
        "report_path": str(job_dir / "report.json"),
        "validation": validation,
    }


@mcp.tool()
def build_cu_sio2_interface(spec: CuSiO2InterfaceSpec | None = None) -> dict[str, Any]:
    """构建 Cu(100)/SiO2(100) 晶格匹配界面 CIF。

    参数:
        spec: 界面规格，包含:
            - name: 界面名称
            - cu_lattice: Cu 晶格常数
            - sio2_lattice: SiO2 晶格常数
            - cu_supercell_x/y: Cu 超胞尺寸
            - sio2_supercell_x/y: SiO2 超胞尺寸
            - cu_layers: Cu 层数
            - interface_gap: 界面间距
            - vacuum: 真空层厚度

    返回:
        包含以下信息的字典:
        - doc_id: 文档 ID
        - job_id: 作业 ID
        - job_dir: 作业目录
        - path: CIF 文件路径
        - spec_path: 规格文件路径
        - lattice_match_path: 晶格匹配文件路径
        - report_path: 报告文件路径
        - validation: 验证结果
        - lattice_match: 晶格匹配信息
    """
    settings = Settings.from_env()
    spec = spec or CuSiO2InterfaceSpec()
    workspace = Path(settings.workspace)
    job_id = f"interface_{_safe_id(spec.name)}_{uuid.uuid4().hex[:8]}"
    job_dir = workspace / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)

    # 构建界面规格
    interface_spec = build_cu_sio2_interface_spec(spec)
    cif_spec = {
        "name": interface_spec["name"],
        "lattice": interface_spec["lattice"],
        "atoms": interface_spec["atoms"],
        "space_group": interface_spec["space_group"],
        "output_format": interface_spec["output_format"],
    }

    # 保存规格文件
    spec_path = job_dir / "spec.json"
    spec_path.write_text(json.dumps(spec.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    lattice_match_path = job_dir / "lattice_match.json"
    lattice_match_path.write_text(
        json.dumps(interface_spec["lattice_match"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 生成 CIF 文件
    output_path = job_dir / "input.cif"
    write_cif(cif_spec, output_path)
    validation = validate_structure_file(output_path)

    # 生成报告
    report = {
        "job_dir": str(job_dir),
        "status": "success" if validation["ok"] else "failed",
        "structure_path": str(output_path),
        "validation": validation,
        "lattice_match": interface_spec["lattice_match"],
    }
    write_report(job_dir, report)

    return {
        "doc_id": job_id,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "path": str(output_path),
        "spec_path": str(spec_path),
        "lattice_match_path": str(lattice_match_path),
        "report_path": str(job_dir / "report.json"),
        "validation": validation,
        "lattice_match": interface_spec["lattice_match"],
    }


@mcp.tool()
def validate_structure(path: str) -> dict[str, Any]:
    """验证结构文件的原子数、晶格标记、重复原子和基本一致性。

    参数:
        path: 结构文件路径

    返回:
        验证结果字典，包含:
        - ok: 验证是否通过
        - path: 文件路径
        - format: 文件格式
        - problems: 问题列表
        - warnings: 警告列表
        - atom_count: 原子数
        - lattice: 晶格参数
    """
    return validate_structure_file(Path(path))


@mcp.tool()
def run_geometry_optimization(
    structure_path: str,
    settings: RunSettings,
) -> dict[str, Any]:
    """通过配置的 MaterialsScript 适配器运行几何优化。

    参数:
        structure_path: 结构文件路径
        settings: 运行设置，包含:
            - engine: 计算引擎 (Forcite, CASTEP, DMol3, DFTB)
            - task: 任务类型
            - quality: 计算质量
            - parameters: 额外参数
            - cores: 并行核心数

    返回:
        包含以下信息的字典:
        - job_id: 作业 ID
        - job_dir: 作业目录
        - report: 解析后的报告
        - report_path: 报告文件路径
    """
    structure = Path(structure_path)
    pre_validation = validate_structure_file(structure)
    adapter = get_adapter()
    result = adapter.run_geometry_optimization(
        structure_path=structure,
        settings=settings.model_dump(),
    )

    job_dir = Path(result["job_dir"])
    report = parse_job_report(job_dir)
    report["pre_validation"] = pre_validation
    write_report(job_dir, report)
    result["report"] = report
    result["report_path"] = str(job_dir / "report.json")
    return result


@mcp.tool()
def parse_results(job_dir: str) -> dict[str, Any]:
    """解析 Materials Studio 输出文件并总结收敛性和结果。

    参数:
        job_dir: 作业目录路径

    返回:
        解析后的报告字典，包含:
        - job_dir: 作业目录
        - exists: 目录是否存在
        - files_read: 已读取的文件列表
        - returncode: 返回码
        - errors: 错误列表
        - warnings: 警告列表
        - converged: 是否收敛
        - energy: 最终能量
        - final_structure: 最终结构路径
        - status: 状态
    """
    report = parse_job_report(Path(job_dir))
    write_report(Path(job_dir), report)
    return report


@mcp.tool()
def diagnose_failure(job_dir: str) -> dict[str, Any]:
    """检查作业日志并返回结构化的失败诊断。

    参数:
        job_dir: 作业目录路径

    返回:
        包含以下信息的字典:
        - report: 解析后的报告
        - diagnosis: 诊断结果，包含:
            - category: 失败类别
            - evidence: 证据列表
            - recommended_action: 建议操作
            - safe_to_retry: 是否可以重试
            - patch_targets: 需要修补的目标文件
    """
    report = parse_job_report(Path(job_dir))
    diagnosis = diagnose_report(report)
    return {
        "report": report,
        "diagnosis": diagnosis,
    }


@mcp.tool()
def request_gui_check(structure_path: str, checklist: list[str] | None = None) -> dict[str, Any]:
    """生成一个窄范围的 Computer Use 检查清单，不执行 GUI 操作。

    参数:
        structure_path: 结构文件路径
        checklist: 自定义检查清单（可选）

    返回:
        包含以下信息的字典:
        - target_app: 目标应用程序
        - structure_path: 结构文件路径
        - checklist: 检查清单
        - computer_use_prompt: Computer Use 提示
    """
    default_checklist = [
        "确认结构文件可以在 BIOVIA Materials Studio Visualizer 中打开。",
        "确认原子数和晶格参数与作业 spec.json 匹配。",
        "旋转模型并检查明显的重叠或周期性破坏。",
        "打开相关的 Calculation 对话框并将设置与生成的脚本进行比较。",
        "检查 Job Explorer 中是否有失败、错误或警告状态。",
        "如果出现对话框，请将确切文本复制到 workspace/screenshots/gui_notes.md。",
    ]

    selected_checklist = checklist or default_checklist
    return {
        "target_app": "BIOVIA Materials Studio",
        "structure_path": structure_path,
        "checklist": selected_checklist,
        "computer_use_prompt": (
            "在 Windows 上使用 @Computer。打开 BIOVIA Materials Studio，然后打开 "
            f"{structure_path}。严格按照此检查清单操作: "
            + " ".join(f"{idx + 1}. {item}" for idx, item in enumerate(selected_checklist))
            + " 除非明确指示，否则不要更改科学参数。"
            " Do not change scientific parameters unless explicitly instructed. "
            "不要删除或覆盖项目文件。"
        ),
    }


def main() -> None:
    """启动 MCP 服务器。"""
    mcp.run(transport="stdio")


def _safe_id(value: str) -> str:
    """将字符串转换为安全的 ID 格式。

    参数:
        value: 输入字符串

    返回:
        安全的 ID 字符串，只包含字母、数字和下划线
    """
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return safe.strip("_") or "job"


if __name__ == "__main__":
    main()
