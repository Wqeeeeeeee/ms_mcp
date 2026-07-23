"""Validated DMol3 geometry-optimization specifications."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from .common import SimulationModule, StrictModel


class DMol3Quality(str, Enum):
    """Reviewed Materials Studio 20.1 DMol3 quality presets."""

    COARSE = "Coarse"
    MEDIUM = "Medium"
    FINE = "Fine"


class DMol3TheoryLevel(str, Enum):
    """Theory levels documented for the MS 20.1 DMol3 task."""

    LDA = "LDA"
    GGA = "GGA"
    HYBRID = "Hybrid"
    META_GGA = "m-GGA"
    HARTREE_FOCK = "HF"


class DMol3YesNo(str, Enum):
    """Exact MaterialsScript toggle strings."""

    YES = "Yes"
    NO = "No"


class DMol3GeometryOptimizationSpec(StrictModel):
    """Strict settings for ``Modules->DMol3->GeometryOptimization``.

    The model intentionally exposes only reviewed Materials Studio 20.1
    settings. In particular, it has no arbitrary ``extraSettings`` escape
    hatch.
    """

    module: Literal[SimulationModule.DMOL3] = SimulationModule.DMOL3
    task: Literal["GeometryOptimization"] = "GeometryOptimization"
    quality: DMol3Quality = DMol3Quality.MEDIUM
    theory_level: DMol3TheoryLevel = DMol3TheoryLevel.GGA
    geometry_optimization_quality: DMol3Quality | None = None
    charge: int = Field(default=0, ge=-1000, le=1000, strict=True)
    use_symmetry: DMol3YesNo = DMol3YesNo.NO
    create_energy_evolution_chart: DMol3YesNo = DMol3YesNo.YES
