"""结构文件验证模块。

此模块提供了轻量级的结构文件验证功能，支持 CIF 格式。
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any


# 化学元素集合（1-118 号元素）
ELEMENTS = {
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "I",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
    "Po",
    "At",
    "Rn",
    "Fr",
    "Ra",
    "Ac",
    "Th",
    "Pa",
    "U",
    "Np",
    "Pu",
    "Am",
    "Cm",
    "Bk",
    "Cf",
    "Es",
    "Fm",
    "Md",
    "No",
    "Lr",
    "Rf",
    "Db",
    "Sg",
    "Bh",
    "Hs",
    "Mt",
    "Ds",
    "Rg",
    "Cn",
    "Nh",
    "Fl",
    "Mc",
    "Lv",
    "Ts",
    "Og",
}


def validate_structure_file(path: Path) -> dict[str, Any]:
    """使用轻量级、无依赖的检查验证结构文件。

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
    path = Path(path)
    problems: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "format": path.suffix.lower().lstrip("."),
            "problems": [f"文件未找到: {path}"],
            "warnings": [],
        }

    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        problems.append("结构文件为空。")

    suffix = path.suffix.lower()
    lattice: dict[str, float] = {}
    atoms: list[dict[str, Any]] = []

    if suffix == ".cif":
        lattice = _parse_cif_lattice(text, problems)
        atoms = _parse_cif_atoms(text, problems, warnings)
        _validate_atom_sites(atoms, problems, warnings)
    elif suffix in {".xsd", ".xyz", ".mol"}:
        warnings.append(f"仅实现了 {suffix} 格式的存在性和非空检查。")
    else:
        warnings.append(f"未知的结构格式: {suffix or '<none>'}。")

    return {
        "ok": not problems,
        "path": str(path),
        "format": suffix.lstrip("."),
        "problems": problems,
        "warnings": warnings,
        "atom_count": len(atoms),
        "lattice": lattice,
    }


def _parse_cif_lattice(text: str, problems: list[str]) -> dict[str, float]:
    """解析 CIF 文件中的晶格参数。

    参数:
        text: CIF 文件内容
        problems: 问题列表（会被修改）

    返回:
        晶格参数字典
    """
    markers = {
        "a": "_cell_length_a",
        "b": "_cell_length_b",
        "c": "_cell_length_c",
        "alpha": "_cell_angle_alpha",
        "beta": "_cell_angle_beta",
        "gamma": "_cell_angle_gamma",
    }
    lattice: dict[str, float] = {}
    for key, marker in markers.items():
        match = re.search(rf"^{re.escape(marker)}\s+([^\s#]+)", text, re.MULTILINE)
        if not match:
            problems.append(f"Missing CIF marker: {marker}")
            continue
        try:
            value = float(_strip_uncertainty(match.group(1)))
        except ValueError:
            problems.append(f"{marker} 的数值无效: {match.group(1)}")
            continue
        if value <= 0:
            problems.append(f"CIF 晶格值必须为正数: {marker}={value}")
        lattice[key] = value
    return lattice


def _parse_cif_atoms(
    text: str,
    problems: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """解析 CIF 文件中的原子坐标。

    参数:
        text: CIF 文件内容
        problems: 问题列表（会被修改）
        warnings: 警告列表（会被修改）

    返回:
        原子数据列表
    """
    lines = text.splitlines()
    atoms: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip().lower() != "loop_":
            index += 1
            continue

        headers: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip().startswith("_"):
            headers.append(lines[index].strip())
            index += 1

        if not any(header.startswith("_atom_site_") for header in headers):
            continue

        atom_rows: list[str] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or stripped.lower() == "loop_" or stripped.startswith("_") or stripped.startswith("data_"):
                break
            if not stripped.startswith("#"):
                atom_rows.append(stripped)
            index += 1

        atoms.extend(_rows_to_atoms(headers, atom_rows, warnings))
    if not atoms:
        problems.append("No atom sites found in CIF.")
    return atoms


def _rows_to_atoms(
    headers: list[str],
    rows: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """将 CIF 原子行转换为原子数据字典。

    参数:
        headers: CIF 循环头列表
        rows: 原子数据行列表
        warnings: 警告列表（会被修改）

    返回:
        原子数据列表
    """
    header_map = {header: idx for idx, header in enumerate(headers)}
    required = [
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
    ]
    if not all(field in header_map for field in required):
        warnings.append("CIF 原子循环未暴露分数 x/y/z 坐标。")
        return []

    atoms: list[dict[str, Any]] = []
    for row in rows:
        parts = row.split()
        if len(parts) < len(headers):
            warnings.append(f"跳过过短的 CIF 原子行: {row}")
            continue
        element = _extract_element(headers, header_map, parts)
        try:
            x = float(_strip_uncertainty(parts[header_map["_atom_site_fract_x"]]))
            y = float(_strip_uncertainty(parts[header_map["_atom_site_fract_y"]]))
            z = float(_strip_uncertainty(parts[header_map["_atom_site_fract_z"]]))
        except ValueError:
            warnings.append(f"跳过坐标无效的 CIF 原子行: {row}")
            continue
        atoms.append({"element": element, "x": x, "y": y, "z": z})
    return atoms


def _extract_element(headers: list[str], header_map: dict[str, int], parts: list[str]) -> str:
    """从 CIF 原子行中提取元素符号。

    参数:
        headers: CIF 循环头列表
        header_map: 头到索引的映射
        parts: 原子行数据

    返回:
        元素符号
    """
    if "_atom_site_type_symbol" in header_map:
        return _clean_element(parts[header_map["_atom_site_type_symbol"]])
    if "_atom_site_label" in header_map:
        label = parts[header_map["_atom_site_label"]]
        match = re.match(r"([A-Z][a-z]?)", label)
        if match:
            return match.group(1)
    return ""


def _validate_atom_sites(
    atoms: list[dict[str, Any]],
    problems: list[str],
    warnings: list[str],
) -> None:
    """验证原子位置的有效性。

    参数:
        atoms: 原子数据列表
        problems: 问题列表（会被修改）
        warnings: 警告列表（会被修改）
    """
    seen_coords: list[tuple[str, float, float, float]] = []
    for index, atom in enumerate(atoms, start=1):
        element = atom.get("element", "")
        if element not in ELEMENTS:
            problems.append(f"原子 {index} 的元素符号无效或未知: {element!r}")
        for axis in ("x", "y", "z"):
            value = float(atom[axis])
            if not math.isfinite(value):
                problems.append(f"原子 {index} 的坐标 {axis} 不是有限数。")
            elif value < -1e-6 or value > 1 + 1e-6:
                warnings.append(f"原子 {index} 的分数坐标 {axis} 超出 [0, 1] 范围。")

        coord = (element, round(float(atom["x"]), 8), round(float(atom["y"]), 8), round(float(atom["z"]), 8))
        if coord in seen_coords:
            problems.append(f"在分数坐标 {coord[1:]} 附近检测到重复的原子位置。")
        seen_coords.append(coord)


def _strip_uncertainty(value: str) -> str:
    """移除 CIF 值中的不确定度标记。

    参数:
        value: 原始值字符串

    返回:
        移除不确定度后的值字符串
    """
    return re.sub(r"\([^)]*\)$", "", value.strip("'\""))


def _clean_element(value: str) -> str:
    """清理元素符号。

    参数:
        value: 原始值

    返回:
        清理后的元素符号
    """
    value = value.strip("'\"")
    match = re.match(r"([A-Z][a-z]?)", value)
    return match.group(1) if match else value
