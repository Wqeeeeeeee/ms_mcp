"""Validated CASTEP simulation specifications."""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator

from .common import SimulationModule, StrictModel


class CastepTask(str, Enum):
    """CASTEP tasks supported by the Materials Studio 20.1 renderer."""

    ENERGY = "Energy"
    GEOMETRY_OPTIMIZATION = "GeometryOptimization"
    BAND_STRUCTURE = "BandStructure"
    DENSITY_OF_STATES = "DensityOfStates"
    PROJECTED_DENSITY_OF_STATES = "ProjectedDensityOfStates"
    OPTICS = "Optics"
    PHONON = "Phonon"
    ELASTIC_CONSTANTS = "ElasticConstants"


def _normalized_task_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


_CASTEP_TASK_ALIASES: dict[str, CastepTask] = {
    _normalized_task_token(task.value): task for task in CastepTask
}
_CASTEP_TASK_ALIASES.update(
    {
        "singlepoint": CastepTask.ENERGY,
        "singlepointenergy": CastepTask.ENERGY,
        "staticenergy": CastepTask.ENERGY,
        "scf": CastepTask.ENERGY,
        "geometryoptimisation": CastepTask.GEOMETRY_OPTIMIZATION,
        "geomopt": CastepTask.GEOMETRY_OPTIMIZATION,
        "optimize": CastepTask.GEOMETRY_OPTIMIZATION,
        "optimise": CastepTask.GEOMETRY_OPTIMIZATION,
        "relax": CastepTask.GEOMETRY_OPTIMIZATION,
        "relaxation": CastepTask.GEOMETRY_OPTIMIZATION,
        "band": CastepTask.BAND_STRUCTURE,
        "bands": CastepTask.BAND_STRUCTURE,
        "bandgap": CastepTask.BAND_STRUCTURE,
        "dos": CastepTask.DENSITY_OF_STATES,
        "fulldos": CastepTask.DENSITY_OF_STATES,
        "pdos": CastepTask.PROJECTED_DENSITY_OF_STATES,
        "partialdos": CastepTask.PROJECTED_DENSITY_OF_STATES,
        "projecteddos": CastepTask.PROJECTED_DENSITY_OF_STATES,
        "optical": CastepTask.OPTICS,
        "opticalproperties": CastepTask.OPTICS,
        "phonons": CastepTask.PHONON,
        "phonondispersion": CastepTask.PHONON,
        "elastic": CastepTask.ELASTIC_CONSTANTS,
        "elasticity": CastepTask.ELASTIC_CONSTANTS,
    }
)


def normalize_castep_task(value: Any) -> CastepTask:
    """Normalize reviewed aliases and reject tasks without a verified API path."""

    if isinstance(value, CastepTask):
        return value
    if not isinstance(value, str):
        raise ValueError("CASTEP task must be a string")
    task = _CASTEP_TASK_ALIASES.get(_normalized_task_token(value.strip()))
    if task is None:
        supported = ", ".join(item.value for item in CastepTask)
        raise ValueError(f"Unsupported CASTEP task {value!r}; supported tasks: {supported}")
    return task


CastepTaskValue = Annotated[CastepTask, BeforeValidator(normalize_castep_task)]


class CastepEnergySpec(StrictModel):
    """CASTEP task settings rendered against the Materials Studio 20.1 API.

    The class name is retained for public compatibility. Property calculations
    are dispatched through the Energy task with documented property flags;
    geometry optimization and elastic constants use their dedicated task APIs.
    ``kpoint_separation`` controls the primary SCF grid, not the separate
    property-grid setting.
    """

    module: Literal[SimulationModule.CASTEP] = SimulationModule.CASTEP
    task: CastepTaskValue = CastepTask.ENERGY
    functional: str = Field(default="PBE", min_length=1, max_length=100)
    quality: str = Field(default="Medium", min_length=1, max_length=100)
    cutoff_energy_ev: int | None = Field(default=None, ge=1, le=100_000)
    kpoint_separation: float | None = Field(default=None, gt=0, le=10)
    kpoints: tuple[int, int, int] | None = None
    output_file: str | None = None

    @model_validator(mode="after")
    def validate_kpoints(self) -> "CastepEnergySpec":
        """Require one documented primary k-point derivation mode."""

        if self.kpoints is not None and any(value <= 0 for value in self.kpoints):
            raise ValueError("k-point grid values must be positive integers")
        if self.kpoints is not None and self.kpoint_separation is not None:
            raise ValueError("Use either kpoints or kpoint_separation, not both")
        return self
