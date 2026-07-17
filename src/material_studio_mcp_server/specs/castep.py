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


class CastepDipoleCorrection(str, Enum):
    """Dipole-correction modes documented by Materials Studio 20.1."""

    NONE = "None"
    NON_SELF_CONSISTENT = "Non self-consistent"
    SELF_CONSISTENT = "Self-consistent"


class CastepCellOptimization(str, Enum):
    """Geometry-optimization cell modes documented by MS 20.1."""

    NONE = "None"
    FULL = "Full"
    FIXED_VOLUME = "Fixed Volume"
    FIXED_SHAPE = "Fixed Shape"


class CastepOptimizationAlgorithm(str, Enum):
    """Geometry-optimization algorithms documented by MS 20.1."""

    LBFGS = "LBFGS"
    BFGS = "BFGS"
    DAMPED_MD = "Damped MD"
    TPSD = "TPSD"


class CastepDosIntegrationMethod(str, Enum):
    """DOS integration methods documented by Materials Studio 20.1."""

    SMEARING = "Smearing"
    INTERPOLATION = "Interpolation"


CASTEP_DIPOLE_CORRECTION_API_PROPERTY = "DipoleCorrection"
CASTEP_DIPOLE_CORRECTION_API_CONTRACT = "Materials Studio 20.1 CASTEP DipoleCorrection"
CASTEP_DIPOLE_MINIMUM_VACUUM_ANGSTROM = 8.0


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


def normalize_castep_dipole_correction(value: Any) -> CastepDipoleCorrection:
    """Normalize reviewed aliases to the exact MaterialsScript enum strings."""

    if isinstance(value, CastepDipoleCorrection):
        return value
    if not isinstance(value, str):
        raise ValueError("CASTEP dipole correction must be a string")
    token = _normalized_task_token(value.strip())
    aliases = {
        "none": CastepDipoleCorrection.NONE,
        "off": CastepDipoleCorrection.NONE,
        "disabled": CastepDipoleCorrection.NONE,
        "selfconsistent": CastepDipoleCorrection.SELF_CONSISTENT,
        "selfconsistentdipole": CastepDipoleCorrection.SELF_CONSISTENT,
        "nonselfconsistent": CastepDipoleCorrection.NON_SELF_CONSISTENT,
        "nonselfconsistentdipole": CastepDipoleCorrection.NON_SELF_CONSISTENT,
        "static": CastepDipoleCorrection.NON_SELF_CONSISTENT,
    }
    mode = aliases.get(token)
    if mode is None:
        supported = ", ".join(item.value for item in CastepDipoleCorrection)
        raise ValueError(
            f"Unsupported CASTEP dipole correction {value!r}; supported modes: {supported}"
        )
    return mode


CastepDipoleCorrectionValue = Annotated[
    CastepDipoleCorrection,
    BeforeValidator(normalize_castep_dipole_correction),
]


def _normalize_documented_enum(
    value: Any,
    *,
    enum_type: type[Enum],
    label: str,
) -> Enum:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    token = _normalized_task_token(value.strip())
    aliases = {
        _normalized_task_token(str(item.value)): item for item in enum_type
    }
    normalized = aliases.get(token)
    if normalized is None:
        supported = ", ".join(str(item.value) for item in enum_type)
        raise ValueError(f"Unsupported {label} {value!r}; supported values: {supported}")
    return normalized


def normalize_castep_cell_optimization(value: Any) -> CastepCellOptimization:
    return _normalize_documented_enum(
        value,
        enum_type=CastepCellOptimization,
        label="CASTEP cell optimization",
    )  # type: ignore[return-value]


def normalize_castep_optimization_algorithm(value: Any) -> CastepOptimizationAlgorithm:
    return _normalize_documented_enum(
        value,
        enum_type=CastepOptimizationAlgorithm,
        label="CASTEP optimization algorithm",
    )  # type: ignore[return-value]


def normalize_castep_dos_integration_method(value: Any) -> CastepDosIntegrationMethod:
    return _normalize_documented_enum(
        value,
        enum_type=CastepDosIntegrationMethod,
        label="CASTEP DOS integration method",
    )  # type: ignore[return-value]


CastepCellOptimizationValue = Annotated[
    CastepCellOptimization,
    BeforeValidator(normalize_castep_cell_optimization),
]
CastepOptimizationAlgorithmValue = Annotated[
    CastepOptimizationAlgorithm,
    BeforeValidator(normalize_castep_optimization_algorithm),
]
CastepDosIntegrationMethodValue = Annotated[
    CastepDosIntegrationMethod,
    BeforeValidator(normalize_castep_dos_integration_method),
]


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
    properties_kpoint_separation: float | None = Field(default=None, gt=0, le=10)
    band_structure_energy_max_ev: float | None = Field(default=None, ge=0, le=100)
    band_structure_extra_bands: int | None = Field(default=None, ge=0, le=999)
    band_structure_energy_tolerance_ev: float | None = Field(
        default=None,
        gt=1.0e-8,
        le=100,
    )
    dos_energy_max_ev: float | None = Field(default=None, ge=0, le=100)
    dos_extra_bands: int | None = Field(default=None, ge=0, le=999)
    dos_energy_tolerance_ev: float | None = Field(
        default=None,
        gt=1.0e-8,
        le=100,
    )
    dos_smearing_width_ev: float | None = Field(default=None, ge=0.005, le=100)
    dos_integration_method: CastepDosIntegrationMethodValue | None = None
    dipole_correction: CastepDipoleCorrectionValue | None = None
    max_iterations: int | None = Field(default=None, ge=3, le=1_000_000)
    displacement_convergence_angstrom: float | None = Field(
        default=None,
        gt=1.0e-8,
        le=100,
    )
    energy_convergence_ev_per_atom: float | None = Field(
        default=None,
        gt=1.0e-9,
        le=100,
    )
    force_convergence_ev_per_angstrom: float | None = Field(
        default=None,
        gt=1.0e-7,
        le=100,
    )
    cell_optimization: CastepCellOptimizationValue | None = None
    optimization_algorithm: CastepOptimizationAlgorithmValue | None = None
    output_file: str | None = None

    @model_validator(mode="after")
    def validate_kpoints(self) -> "CastepEnergySpec":
        """Require one documented primary k-point derivation mode."""

        if self.kpoints is not None and any(value <= 0 for value in self.kpoints):
            raise ValueError("k-point grid values must be positive integers")
        if self.kpoints is not None and self.kpoint_separation is not None:
            raise ValueError("Use either kpoints or kpoint_separation, not both")
        property_tasks = {
            CastepTask.BAND_STRUCTURE,
            CastepTask.DENSITY_OF_STATES,
            CastepTask.PROJECTED_DENSITY_OF_STATES,
            CastepTask.OPTICS,
            CastepTask.PHONON,
        }
        if (
            self.properties_kpoint_separation is not None
            and self.task not in property_tasks
        ):
            raise ValueError(
                "properties_kpoint_separation requires a CASTEP property task"
            )
        band_fields = {
            "band_structure_energy_max_ev": self.band_structure_energy_max_ev,
            "band_structure_extra_bands": self.band_structure_extra_bands,
            "band_structure_energy_tolerance_ev": (
                self.band_structure_energy_tolerance_ev
            ),
        }
        supplied_band_fields = [
            name for name, value in band_fields.items() if value is not None
        ]
        if supplied_band_fields and self.task is not CastepTask.BAND_STRUCTURE:
            raise ValueError(
                "CASTEP band-structure settings require task BandStructure: "
                + ", ".join(supplied_band_fields)
            )
        dos_fields = {
            "dos_energy_max_ev": self.dos_energy_max_ev,
            "dos_extra_bands": self.dos_extra_bands,
            "dos_energy_tolerance_ev": self.dos_energy_tolerance_ev,
            "dos_smearing_width_ev": self.dos_smearing_width_ev,
            "dos_integration_method": self.dos_integration_method,
        }
        supplied_dos_fields = [
            name for name, value in dos_fields.items() if value is not None
        ]
        if supplied_dos_fields and self.task not in {
            CastepTask.DENSITY_OF_STATES,
            CastepTask.PROJECTED_DENSITY_OF_STATES,
        }:
            raise ValueError(
                "CASTEP DOS settings require task DensityOfStates or "
                "ProjectedDensityOfStates: "
                + ", ".join(supplied_dos_fields)
            )
        if (
            self.dipole_correction is CastepDipoleCorrection.NON_SELF_CONSISTENT
            and self.task is not CastepTask.ENERGY
        ):
            raise ValueError(
                "Non self-consistent CASTEP dipole correction is supported only for the Energy task"
            )
        geometry_only_values = {
            "max_iterations": self.max_iterations,
            "displacement_convergence_angstrom": self.displacement_convergence_angstrom,
            "energy_convergence_ev_per_atom": self.energy_convergence_ev_per_atom,
            "force_convergence_ev_per_angstrom": self.force_convergence_ev_per_angstrom,
            "cell_optimization": self.cell_optimization,
            "optimization_algorithm": self.optimization_algorithm,
        }
        supplied_geometry_fields = [
            name for name, value in geometry_only_values.items() if value is not None
        ]
        if supplied_geometry_fields and self.task is not CastepTask.GEOMETRY_OPTIMIZATION:
            raise ValueError(
                "CASTEP geometry-optimization settings require task GeometryOptimization: "
                + ", ".join(supplied_geometry_fields)
            )
        if (
            self.cell_optimization is not None
            and self.cell_optimization is not CastepCellOptimization.NONE
            and self.optimization_algorithm is CastepOptimizationAlgorithm.DAMPED_MD
        ):
            raise ValueError(
                "CASTEP Damped MD is not supported when cell optimization is requested"
            )
        return self
