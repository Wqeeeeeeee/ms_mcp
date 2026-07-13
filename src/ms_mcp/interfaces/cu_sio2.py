"""Cu/SiO2 界面构建模块。

此模块提供了构建 Cu(100)/beta-cristobalite SiO2(100) 晶格匹配界面的功能。
"""

from __future__ import annotations

from collections import Counter
from math import sqrt
from typing import Any

from ms_mcp.schemas import CuSiO2InterfaceSpec


def build_cu_sio2_interface_spec(spec: CuSiO2InterfaceSpec) -> dict[str, Any]:
    """构建理想的 Cu(100)/beta-cristobalite SiO2(100) 界面。

    默认匹配将 Cu(100) 2x2 应变到 beta-cristobalite SiO2(100) 1x1。
    这保持了第一个模型的小尺寸，同时产生约 1% 的面内 Cu 压缩。

    参数:
        spec: 界面规格

    返回:
        包含界面信息的字典
    """
    # 计算自然晶格尺寸
    natural_cu_a = spec.cu_lattice * spec.cu_supercell_x
    natural_cu_b = spec.cu_lattice * spec.cu_supercell_y
    natural_sio2_a = spec.sio2_lattice * spec.sio2_supercell_x
    natural_sio2_b = spec.sio2_lattice * spec.sio2_supercell_y
    cell_a = _matched_length(natural_cu_a, natural_sio2_a, spec.match_to)
    cell_b = _matched_length(natural_cu_b, natural_sio2_b, spec.match_to)

    # 计算 Cu 层参数
    cu_ax = cell_a / spec.cu_supercell_x
    cu_ay = cell_b / spec.cu_supercell_y
    cu_layer_spacing = (cu_ax + cu_ay) / 4.0
    cu_z0 = spec.bottom_padding
    cu_top = cu_z0 + (spec.cu_layers - 1) * cu_layer_spacing

    # 计算 SiO2 层参数
    sio2_z0 = cu_top + spec.interface_gap
    sio2_thickness = spec.sio2_lattice
    cell_c = sio2_z0 + sio2_thickness + spec.vacuum

    # 构建原子
    atoms = []
    atoms.extend(_build_cu_100_slab(spec, cell_a, cell_b, cu_z0, cu_layer_spacing, cell_c))
    atoms.extend(_build_beta_cristobalite_sio2(spec, cell_a, cell_b, sio2_z0, cell_c))

    # 统计原子数
    counts = Counter(atom["element"] for atom in atoms)
    lattice_match = {
        "interface": "Cu(100) 2x2 / ideal beta-cristobalite SiO2(100) 1x1",
        "match_to": spec.match_to,
        "cell_a": cell_a,
        "cell_b": cell_b,
        "cell_c": cell_c,
        "cu_natural_a": natural_cu_a,
        "cu_natural_b": natural_cu_b,
        "sio2_natural_a": natural_sio2_a,
        "sio2_natural_b": natural_sio2_b,
        "cu_strain_a_percent": _percent_strain(cell_a, natural_cu_a),
        "cu_strain_b_percent": _percent_strain(cell_b, natural_cu_b),
        "sio2_strain_a_percent": _percent_strain(cell_a, natural_sio2_a),
        "sio2_strain_b_percent": _percent_strain(cell_b, natural_sio2_b),
        "cu_strained_lattice_a": cu_ax,
        "cu_strained_lattice_b": cu_ay,
        "cu_layer_spacing": cu_layer_spacing,
        "interface_gap": spec.interface_gap,
        "vacuum": spec.vacuum,
        "atom_counts": dict(counts),
        "notes": [
            "用于 Materials Studio 清理/优化的第一遍晶格匹配界面。",
            "SiO2 是理想的 beta-cristobalite 类金刚石 Si 网络，O 位于 Si-Si 键中点。",
            "在生产计算之前使用 Materials Studio 几何优化。",
        ],
    }

    return {
        "name": spec.name,
        "lattice": {
            "a": cell_a,
            "b": cell_b,
            "c": cell_c,
            "alpha": 90.0,
            "beta": 90.0,
            "gamma": 90.0,
        },
        "atoms": atoms,
        "space_group": "P 1",
        "output_format": "cif",
        "lattice_match": lattice_match,
    }


def _matched_length(cu_length: float, sio2_length: float, match_to: str) -> float:
    """计算匹配长度。

    参数:
        cu_length: Cu 自然长度
        sio2_length: SiO2 自然长度
        match_to: 匹配目标

    返回:
        匹配后的长度
    """
    if match_to == "cu":
        return cu_length
    if match_to == "average":
        return (cu_length + sio2_length) / 2.0
    return sio2_length


def _percent_strain(target: float, natural: float) -> float:
    """计算应变百分比。

    参数:
        target: 目标长度
        natural: 自然长度

    返回:
        应变百分比
    """
    return (target / natural - 1.0) * 100.0


def _build_cu_100_slab(
    spec: CuSiO2InterfaceSpec,
    cell_a: float,
    cell_b: float,
    z0: float,
    layer_spacing: float,
    cell_c: float,
) -> list[dict[str, Any]]:
    """构建 Cu(100) 板。

    参数:
        spec: 界面规格
        cell_a: 单胞 a 轴长度
        cell_b: 单胞 b 轴长度
        z0: 起始 z 坐标
        layer_spacing: 层间距
        cell_c: 单胞 c 轴长度

    返回:
        Cu 原子列表
    """
    ax = cell_a / spec.cu_supercell_x
    by = cell_b / spec.cu_supercell_y
    atoms: list[dict[str, Any]] = []
    count = 1

    for layer in range(spec.cu_layers):
        z = z0 + layer * layer_spacing
        # 交替层使用不同的基矢
        if layer % 2 == 0:
            basis = ((0.0, 0.0), (0.5, 0.5))
        else:
            basis = ((0.5, 0.0), (0.0, 0.5))

        for ix in range(spec.cu_supercell_x):
            for iy in range(spec.cu_supercell_y):
                for bx, by_frac in basis:
                    x = (ix + bx) * ax
                    y = (iy + by_frac) * by
                    atoms.append(
                        {
                            "label": f"Cu{count}",
                            "element": "Cu",
                            "x": x / cell_a,
                            "y": y / cell_b,
                            "z": z / cell_c,
                        }
                    )
                    count += 1
    return atoms


def _build_beta_cristobalite_sio2(
    spec: CuSiO2InterfaceSpec,
    cell_a: float,
    cell_b: float,
    z0: float,
    cell_c: float,
) -> list[dict[str, Any]]:
    """构建 beta-cristobalite SiO2。

    参数:
        spec: 界面规格
        cell_a: 单胞 a 轴长度
        cell_b: 单胞 b 轴长度
        z0: 起始 z 坐标
        cell_c: 单胞 c 轴长度

    返回:
        SiO2 原子列表
    """
    atoms: list[dict[str, Any]] = []
    si_count = 1
    o_count = 1

    for sx in range(spec.sio2_supercell_x):
        for sy in range(spec.sio2_supercell_y):
            x_offset = sx * spec.sio2_lattice
            y_offset = sy * spec.sio2_lattice
            # 添加 Si 原子
            for fx, fy, fz in _diamond_si_fractional_positions():
                atoms.append(
                    {
                        "label": f"Si{si_count}",
                        "element": "Si",
                        "x": (x_offset + fx * spec.sio2_lattice) / cell_a,
                        "y": (y_offset + fy * spec.sio2_lattice) / cell_b,
                        "z": (z0 + fz * spec.sio2_lattice) / cell_c,
                    }
                )
                si_count += 1

            # 添加 O 原子（位于 Si-Si 键中点）
            for fx, fy, fz in _diamond_bond_midpoints():
                atoms.append(
                    {
                        "label": f"O{o_count}",
                        "element": "O",
                        "x": (x_offset + fx * spec.sio2_lattice) / cell_a,
                        "y": (y_offset + fy * spec.sio2_lattice) / cell_b,
                        "z": (z0 + fz * spec.sio2_lattice) / cell_c,
                    }
                )
                o_count += 1
    return atoms


def _diamond_si_fractional_positions() -> list[tuple[float, float, float]]:
    """返回金刚石结构中 Si 原子的分数坐标。

    返回:
        分数坐标列表
    """
    return [
        (0.0, 0.0, 0.0),
        (0.0, 0.5, 0.5),
        (0.5, 0.0, 0.5),
        (0.5, 0.5, 0.0),
        (0.25, 0.25, 0.25),
        (0.25, 0.75, 0.75),
        (0.75, 0.25, 0.75),
        (0.75, 0.75, 0.25),
    ]


def _diamond_bond_midpoints() -> list[tuple[float, float, float]]:
    """返回金刚石结构中 Si-Si 键中点的分数坐标。

    返回:
        分数坐标列表（应包含 16 个 O 位置）

    异常:
        RuntimeError: 如果未找到 16 个 O 位置
    """
    positions = _diamond_si_fractional_positions()
    nearest = sqrt(3.0) / 4.0
    midpoints: set[tuple[float, float, float]] = set()

    for i, start in enumerate(positions):
        for j, end in enumerate(positions):
            if i == j:
                continue
            for tx in (-1, 0, 1):
                for ty in (-1, 0, 1):
                    for tz in (-1, 0, 1):
                        delta = (
                            end[0] + tx - start[0],
                            end[1] + ty - start[1],
                            end[2] + tz - start[2],
                        )
                        distance = sqrt(delta[0] ** 2 + delta[1] ** 2 + delta[2] ** 2)
                        if abs(distance - nearest) > 1e-8:
                            continue
                        midpoint = (
                            (start[0] + delta[0] / 2.0) % 1.0,
                            (start[1] + delta[1] / 2.0) % 1.0,
                            (start[2] + delta[2] / 2.0) % 1.0,
                        )
                        midpoints.add(tuple(round(value, 10) for value in midpoint))

    ordered = sorted(midpoints)
    if len(ordered) != 16:
        raise RuntimeError(f"预期 16 个 beta-cristobalite O 位置，实际找到 {len(ordered)} 个。")
    return ordered
