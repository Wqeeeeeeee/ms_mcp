"""结构化工作流共享的通用 Pydantic 模型。

此模块定义了用于结构化 Materials Studio 工作流的通用数据模型。
"""

from __future__ import annotations

import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class StrictModel(BaseModel):
    """拒绝未知字段的基础模型。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExecutionMode(str, Enum):
    """执行模式枚举。"""

    PREVIEW = "preview"
    EXECUTE = "execute"


class ModelType(str, Enum):
    """模型类型枚举。"""

    MOLECULE = "molecule"
    CRYSTAL = "crystal"
    IMPORTED_STRUCTURE = "imported_structure"


class SimulationModule(str, Enum):
    """模拟模块枚举。"""

    FORCITE = "Forcite"
    CASTEP = "CASTEP"
    DMOL3 = "DMol3"
    DFTBPLUS = "DFTBPlus"


class ScriptLanguage(str, Enum):
    """脚本语言枚举。"""

    PERL = "perl"
    PYTHON = "python"


class UnitSystem(StrictModel):
    """单位系统。"""

    length: Literal["angstrom"] = "angstrom"
    energy: Literal["eV", "kcal/mol"] = "eV"
    temperature: Literal["K"] = "K"
    time: Literal["fs", "ps"] = "ps"


class LengthValue(StrictModel):
    """长度值。"""

    value: float
    unit: Literal["angstrom"] = "angstrom"

    @field_validator("value")
    @classmethod
    def positive(cls, value: float) -> float:
        return _require_finite(value, "length")


class EnergyValue(StrictModel):
    """能量值。"""

    value: float
    unit: Literal["eV", "kcal/mol"] = "eV"

    @field_validator("value")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite(value, "energy")


class TemperatureValue(StrictModel):
    """温度值。"""

    value: float = Field(gt=0)
    unit: Literal["K"] = "K"


class TimeValue(StrictModel):
    """时间值。"""

    value: float = Field(gt=0)
    unit: Literal["fs", "ps"] = "ps"


class Vector3(StrictModel):
    """三维向量。"""

    x: float
    y: float
    z: float

    @model_validator(mode="before")
    @classmethod
    def from_sequence(cls, value: Any) -> Any:
        """从序列创建向量。"""
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return {"x": value[0], "y": value[1], "z": value[2]}
        return value

    @field_validator("x", "y", "z")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite(value, "coordinate")

    def as_tuple(self) -> tuple[float, float, float]:
        """返回元组表示。"""
        return self.x, self.y, self.z


class FractionalVector3(Vector3):
    """分数坐标向量。"""

    allow_outside_cell: bool = False

    @field_validator("x", "y", "z")
    @classmethod
    def in_cell(cls, value: float) -> float:
        value = _require_finite(value, "fractional coordinate")
        if value < -1e-12 or value > 1 + 1e-12:
            raise ValueError("分数坐标必须在 [0, 1] 范围内")
        return value


class FileRef(StrictModel):
    """文件引用。"""

    path: str = Field(min_length=1)
    role: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def reject_empty_or_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("路径包含 NUL 字节")
        return value

    def as_path(self) -> Path:
        """返回 Path 对象。"""
        return Path(self.path).expanduser()


class AcceptanceCriteria(StrictModel):
    """验收标准。"""

    max_warnings: int = Field(default=0, ge=0)
    require_convergence: bool = False
    notes: list[str] = Field(default_factory=list)


def validate_element_symbol(value: str) -> str:
    """验证并标准化化学元素符号。

    参数:
        value: 元素符号

    返回:
        标准化后的元素符号

    异常:
        ValueError: 如果符号无效或未知
    """
    if not re.fullmatch(r"[A-Z][a-z]?", value or ""):
        raise ValueError(f"无效的元素符号: {value!r}")
    if value not in ELEMENTS:
        raise ValueError(f"未知的元素符号: {value!r}")
    return value


def _require_finite(value: float, label: str) -> float:
    """要求值为有限数。

    参数:
        value: 数值
        label: 标签

    返回:
        有限数

    异常:
        ValueError: 如果值不是有限数
    """
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} 必须是有限数")
    return float(value)
