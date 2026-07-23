"""CIF 文件构建器。

此模块提供了从晶体结构规格生成 CIF 文件的功能。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def write_cif(spec: dict[str, Any], output_path: Path) -> Path:
    """使用分数坐标写入最小化的 CIF 文件。

    参数:
        spec: 晶体结构规格字典，包含:
            - name: 结构名称
            - lattice: 晶格参数 (a, b, c, alpha, beta, gamma)
            - atoms: 原子列表
            - space_group: 空间群（可选）
        output_path: 输出文件路径

    返回:
        输出文件路径
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    name = str(spec["name"])
    lattice = spec["lattice"]
    atoms = [_normalise_atom(atom, index + 1) for index, atom in enumerate(spec["atoms"])]
    space_group = spec.get("space_group") or "P 1"

    # 构建 CIF 文件内容
    lines = [
        f"data_{_safe_data_name(name)}",
        f"_symmetry_space_group_name_H-M '{space_group}'",
        f"_cell_length_a    {_format_float(lattice['a'])}",
        f"_cell_length_b    {_format_float(lattice['b'])}",
        f"_cell_length_c    {_format_float(lattice['c'])}",
        f"_cell_angle_alpha {_format_float(lattice['alpha'])}",
        f"_cell_angle_beta  {_format_float(lattice['beta'])}",
        f"_cell_angle_gamma {_format_float(lattice['gamma'])}",
        "",
        "loop_",
        "  _atom_site_label",
        "  _atom_site_type_symbol",
        "  _atom_site_fract_x",
        "  _atom_site_fract_y",
        "  _atom_site_fract_z",
    ]
    for atom in atoms:
        lines.append(
            "  {label} {element} {x} {y} {z}".format(
                label=atom["label"],
                element=atom["element"],
                x=_format_float(atom["x"]),
                y=_format_float(atom["y"]),
                z=_format_float(atom["z"]),
            )
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _normalise_atom(atom: dict[str, Any], index: int) -> dict[str, Any]:
    """标准化原子数据。

    参数:
        atom: 原子数据字典
        index: 原子索引

    返回:
        标准化后的原子数据字典

    异常:
        ValueError: 如果缺少元素信息
    """
    element = atom.get("element") or atom.get("symbol") or atom.get("type_symbol")
    if not element:
        raise ValueError(f"原子 {index} 缺少元素信息")
    element = str(element)
    label = atom.get("label") or f"{element}{index}"

    coords = _extract_fractional_coords(atom, index)
    return {
        "label": _safe_label(str(label)),
        "element": element,
        "x": coords[0],
        "y": coords[1],
        "z": coords[2],
    }


def _extract_fractional_coords(atom: dict[str, Any], index: int) -> tuple[float, float, float]:
    """提取分数坐标。

    参数:
        atom: 原子数据字典
        index: 原子索引

    返回:
        分数坐标元组 (x, y, z)

    异常:
        ValueError: 如果缺少分数坐标
    """
    if "fractional" in atom:
        coords = atom["fractional"]
        if not isinstance(coords, (list, tuple)) or len(coords) != 3:
            raise ValueError(f"原子 {index} 的分数坐标长度必须为 3")
        return float(coords[0]), float(coords[1]), float(coords[2])

    keys = (("x", "y", "z"), ("fract_x", "fract_y", "fract_z"))
    for keyset in keys:
        if all(key in atom for key in keyset):
            return float(atom[keyset[0]]), float(atom[keyset[1]]), float(atom[keyset[2]])

    raise ValueError(f"原子 {index} 缺少分数坐标")


def _safe_data_name(name: str) -> str:
    """生成安全的数据名称。

    参数:
        name: 原始名称

    返回:
        安全的数据名称，只包含字母、数字和下划线
    """
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    return safe.strip("_") or "structure"


def _safe_label(label: str) -> str:
    """生成安全的原子标签。

    参数:
        label: 原始标签

    返回:
        安全的原子标签，只包含字母、数字和下划线
    """
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", label.strip())
    return safe.strip("_") or "Atom"


def _format_float(value: Any) -> str:
    """格式化浮点数。

    参数:
        value: 数值

    返回:
        格式化后的字符串，最多 10 位有效数字
    """
    return f"{float(value):.10g}"
