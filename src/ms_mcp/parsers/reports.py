"""作业报告解析模块。

此模块提供了解析 Materials Studio 作业输出文件并生成报告的功能。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# 日志文件名列表
LOG_NAMES = ("stdout.log", "stderr.log", "ms_job.log", "gui_notes.md")
STRUCTURED_ARTIFACT_NAMES = ("result_metadata.json",)

# 错误关键词正则表达式
ERROR_RE = re.compile(r"\b(error|failed|failure|exception|traceback|fatal|license)\b", re.IGNORECASE)

# 警告关键词正则表达式
WARNING_RE = re.compile(r"\b(warning|warn|not converged|convergence|scf|overlap|too close)\b", re.IGNORECASE)

# 能量值正则表达式
ENERGY_RE = re.compile(
    r"\b(?:total\s+)?energy\s*[=:]\s*(-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)",
    re.IGNORECASE,
)


def parse_job_report(job_dir: Path) -> dict[str, Any]:
    """解析标准作业文件并总结有用的状态信号。

    参数:
        job_dir: 作业目录路径

    返回:
        报告字典，包含:
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
    job_dir = Path(job_dir)
    files_read: list[str] = []
    text_blocks: list[str] = []

    # 读取所有日志文件
    for path in _iter_log_files(job_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files_read.append(str(path))
        text_blocks.append(text)

    for path in _iter_structured_files(job_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        files_read.append(str(path))
        text_blocks.extend(_structured_text_blocks(payload))

    # 合并所有文本并提取信息
    combined = "\n".join(text_blocks)
    errors = _dedupe_preserve_order(_matching_lines(combined, ERROR_RE))
    warnings = _dedupe_preserve_order(_matching_lines(combined, WARNING_RE))
    energies = [float(match.group(1)) for match in ENERGY_RE.finditer(combined)]
    final_structure = _find_final_structure(job_dir)
    returncode = _read_returncode(job_dir)

    report = {
        "job_dir": str(job_dir),
        "exists": job_dir.exists(),
        "files_read": files_read,
        "returncode": returncode,
        "errors": errors,
        "warnings": warnings,
        "converged": _looks_converged(combined),
        "energy": energies[-1] if energies else None,
        "final_structure": str(final_structure) if final_structure else None,
    }

    report["status"] = _status_from_report(report)
    return report


def diagnose_report(report: dict[str, Any]) -> dict[str, Any]:
    """将解析后的报告分类为稳定的失败诊断。

    参数:
        report: 解析后的报告字典

    返回:
        诊断结果字典，包含:
        - category: 失败类别
        - evidence: 证据列表
        - recommended_action: 建议操作
        - safe_to_retry: 是否可以重试
        - patch_targets: 需要修补的目标文件
    """
    evidence = list(report.get("errors", [])) + list(report.get("warnings", []))
    joined = "\n".join(evidence).lower()
    category = "gui_unknown"
    recommended_action = "使用 request_gui_check 检查 Job Explorer 和任何错误对话框。"
    safe_to_retry = False
    patch_targets: list[str] = []

    # 许可证错误
    if "license" in joined or "flexlm" in joined or "checkout" in joined:
        category = "license_error"
        recommended_action = "重试前检查 Materials Studio 许可证服务器和模块授权。"
    # 模型几何错误
    elif "overlap" in joined or "too close" in joined or "close contact" in joined:
        category = "model_geometry_error"
        recommended_action = "修复近距离接触或使用更便宜的力场预优化。"
        safe_to_retry = True
    # 收敛错误
    elif "convergence" in joined or "scf" in joined or "not converged" in joined:
        category = "convergence_error"
        recommended_action = "调整收敛设置、优化步长或初始几何结构。"
        safe_to_retry = True
    # 脚本 API 错误
    elif (
        "traceback" in joined
        or "undefined subroutine" in joined
        or "can't locate" in joined
        or "attributeerror" in joined
        or "nameerror" in joined
        or "materialsscript" in joined
    ):
        category = "script_api_error"
        recommended_action = "从 Materials Studio Copy Script 输出重新生成 MaterialsScript 模板。"
        safe_to_retry = True
        patch_targets = [
            "src/ms_mcp/templates/ms_perl/geometry_opt.pl.j2",
            "src/ms_mcp/templates/ms_python/geometry_opt.py.j2",
        ]
    # 其他返回码错误
    elif report.get("returncode") not in (None, 0):
        category = "script_api_error"
        recommended_action = "检查 stderr.log 并验证 MS_SCRIPT_RUNNER 和模板语法。"
        safe_to_retry = True
        patch_targets = [
            "src/ms_mcp/templates/ms_perl/geometry_opt.pl.j2",
            "src/ms_mcp/templates/ms_python/geometry_opt.py.j2",
        ]

    # 无错误且成功
    if not evidence and report.get("status") == "success":
        category = "none"
        recommended_action = "未检测到失败。"
        safe_to_retry = False

    return {
        "category": category,
        "evidence": evidence[:20],
        "recommended_action": recommended_action,
        "safe_to_retry": safe_to_retry,
        "patch_targets": patch_targets,
    }


def write_report(job_dir: Path, report: dict[str, Any]) -> Path:
    """将报告写入 JSON 文件。

    参数:
        job_dir: 作业目录路径
        report: 报告字典

    返回:
        报告文件路径
    """
    path = Path(job_dir) / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _iter_log_files(job_dir: Path) -> list[Path]:
    """迭代作业目录中的日志文件。

    参数:
        job_dir: 作业目录路径

    返回:
        日志文件路径列表
    """
    if not job_dir.exists():
        return []
    paths = [job_dir / name for name in LOG_NAMES]
    paths.extend(sorted(job_dir.glob("*.log")))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _iter_structured_files(job_dir: Path) -> list[Path]:
    """Iterate structured metadata artifacts that can carry report signals."""
    if not job_dir.exists():
        return []
    paths = [job_dir / name for name in STRUCTURED_ARTIFACT_NAMES]
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _structured_text_blocks(payload: Any) -> list[str]:
    """Flatten structured metadata into text snippets for regex-based parsing."""
    blocks: list[str] = []
    if isinstance(payload, dict):
        for key in ("stdout", "stderr", "warning", "warnings", "error", "errors", "message", "messages"):
            value = payload.get(key)
            blocks.extend(_structured_text_blocks(value))
        for key in ("parsed_json", "details", "result", "diagnosis"):
            if key in payload:
                blocks.extend(_structured_text_blocks(payload[key]))
        for value in payload.values():
            if isinstance(value, (dict, list)):
                continue
            if isinstance(value, str):
                blocks.append(value)
    elif isinstance(payload, list):
        for item in payload:
            blocks.extend(_structured_text_blocks(item))
    elif isinstance(payload, str):
        blocks.append(payload)
    return blocks


def _matching_lines(text: str, pattern: re.Pattern[str]) -> list[str]:
    """提取匹配正则表达式的行。

    参数:
        text: 文本内容
        pattern: 正则表达式模式

    返回:
        匹配的行列表
    """
    matches: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and pattern.search(stripped):
            matches.append(stripped[:500])
    return matches


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Return a stable de-duplicated list."""

    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _find_final_structure(job_dir: Path) -> Path | None:
    """查找最终结构文件。

    参数:
        job_dir: 作业目录路径

    返回:
        最终结构文件路径，如果未找到则返回 None
    """
    for name in ("final_structure.xsd", "result.xsd", "final_structure.cif", "result.cif"):
        path = job_dir / name
        if path.exists():
            return path
    return None


def _read_returncode(job_dir: Path) -> int | None:
    """读取返回码。

    参数:
        job_dir: 作业目录路径

    返回:
        返回码，如果未找到则返回 None
    """
    path = job_dir / "returncode.txt"
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _looks_converged(text: str) -> bool:
    """判断是否收敛。

    参数:
        text: 文本内容

    返回:
        是否收敛
    """
    lowered = text.lower()
    if "not converged" in lowered:
        return False
    return "converged" in lowered or "successfully completed" in lowered


def _status_from_report(report: dict[str, Any]) -> str:
    """从报告中推断状态。

    参数:
        report: 报告字典

    返回:
        状态字符串
    """
    if report.get("returncode") not in (None, 0):
        return "failed"
    if report.get("errors"):
        return "failed"
    if report.get("converged") or report.get("final_structure"):
        return "success"
    if report.get("warnings"):
        return "warning"
    return "unknown"
