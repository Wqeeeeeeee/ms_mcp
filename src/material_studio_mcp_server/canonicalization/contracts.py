"""Strict immutable contracts for internal periodic-structure evaluation."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from material_studio_mcp_server.runtime.contracts import canonical_json_bytes

from ._elements import atomic_number


CANONICALIZATION_CONTRACT_VERSION = "1.0.0"
CANONICALIZATION_PROFILE = "material_studio_periodic_canonicalization_v1"
COMPARISON_PROFILE = "material_studio_periodic_structure_comparison_v1"
STRUCTURE_PROJECTION_PROFILE = "material_studio_structure_projection_v1"
COMPARISON_PROJECTION_PROFILE = "material_studio_comparison_projection_v1"
CANONICAL_ARTIFACT_PROFILE = "material_studio_reference_canonical_artifact_v1"
IMPLEMENTATION_VERSION = "1.0.0"

SHA256_PATTERN = r"^[0-9a-f]{64}$"
ELEMENT_PATTERN = r"^[A-Z][a-z]?$"
WYCKOFF_PATTERN = r"^[a-z]$"
MAX_STRUCTURE_SITES = 100_000

Vector3: TypeAlias = tuple[float, float, float]
IntVector3: TypeAlias = tuple[int, int, int]
Matrix3: TypeAlias = tuple[Vector3, Vector3, Vector3]
IntMatrix3: TypeAlias = tuple[IntVector3, IntVector3, IntVector3]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


def _strict_boolean_literal(expected: bool) -> BeforeValidator:
    def validate(value: Any) -> bool:
        if type(value) is not bool or value is not expected:
            raise ValueError(f"value must be the boolean literal {expected!r}")
        return value

    return BeforeValidator(validate)


StrictTrue = Annotated[Literal[True], _strict_boolean_literal(True)]
StrictFalse = Annotated[Literal[False], _strict_boolean_literal(False)]


def _determinant(matrix: Matrix3 | IntMatrix3) -> float:
    a, b, c = matrix
    return float(
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _validate_vector(value: Vector3, label: str) -> Vector3:
    if not all(math.isfinite(component) for component in value):
        raise ValueError(f"{label} must contain only finite values")
    return value


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_relative_path(value: str, label: str) -> str:
    if not value or len(value) > 512:
        raise ValueError(f"{label} length is outside the supported bound")
    if (
        "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} contains an unsafe character")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{label} must not be drive-qualified")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError(f"{label} must be a canonical confined relative path")
    forbidden_parts = {"validation", "hidden_holdout"}
    reserved_names = {"con", "prn", "aux", "nul"} | {
        f"{prefix}{number}"
        for prefix in ("com", "lpt")
        for number in range(1, 10)
    }
    for part in path.parts:
        folded = part.casefold()
        if folded in forbidden_parts or folded.split(".", 1)[0] in reserved_names:
            raise ValueError(f"{label} contains a forbidden path component")
    return value


def _composition_counts(
    composition: tuple["SpeciesCount", ...],
) -> dict[str, int]:
    return {item.species: item.count for item in composition}


def _validate_composition(
    composition: tuple["SpeciesCount", ...],
    atom_count: int,
) -> None:
    species = tuple(item.species for item in composition)
    if species != tuple(sorted(species)) or len(species) != len(set(species)):
        raise ValueError("composition must contain unique lexically sorted species")
    if sum(item.count for item in composition) != atom_count:
        raise ValueError("composition counts must sum to atom_count")


class CanonicalContractModel(BaseModel):
    """Package-local base that permits repeated numeric tuple components."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
    )


class RationalValue(CanonicalContractModel):
    numerator: int
    denominator: int = Field(gt=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_reduced(self) -> "RationalValue":
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("rational value must be reduced")
        return self


class AffineSymmetryOperation(CanonicalContractModel):
    rotation: IntMatrix3
    translation: tuple[RationalValue, RationalValue, RationalValue]

    @model_validator(mode="after")
    def validate_rotation(self) -> "AffineSymmetryOperation":
        if any(abs(component) > 1 for row in self.rotation for component in row):
            raise ValueError("symmetry rotation coefficients must be -1, 0, or 1")
        if abs(round(_determinant(self.rotation))) != 1:
            raise ValueError("symmetry rotation must be unimodular")
        return self


class AtomSite(CanonicalContractModel):
    species: str = Field(pattern=ELEMENT_PATTERN)
    fractional_coordinates: Vector3
    occupancy: float = Field(gt=0.0, le=1.0)
    label: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("fractional_coordinates")
    @classmethod
    def validate_coordinates(cls, value: Vector3) -> Vector3:
        return _validate_vector(value, "fractional coordinates")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("atom label contains a control character")
        return value


class PeriodicStructure(CanonicalContractModel):
    lattice: Matrix3
    sites: tuple[AtomSite, ...] = Field(min_length=1, max_length=MAX_STRUCTURE_SITES)

    @model_validator(mode="after")
    def validate_lattice(self) -> "PeriodicStructure":
        for row in self.lattice:
            _validate_vector(row, "lattice")
        determinant = _determinant(self.lattice)
        if not math.isfinite(determinant) or determinant <= 1.0e-12:
            raise ValueError(
                "lattice must be nonsingular and right-handed with positive determinant"
            )
        return self


class ParsedCif(CanonicalContractModel):
    raw_sha256: Sha256
    raw_byte_count: int = Field(ge=1, le=16 * 1024 * 1024)
    data_block_name: str = Field(min_length=1, max_length=128)
    lattice: Matrix3
    asymmetric_sites: tuple[AtomSite, ...] = Field(
        min_length=1,
        max_length=MAX_STRUCTURE_SITES,
    )
    symmetry_operations: tuple[AffineSymmetryOperation, ...] = Field(
        min_length=1,
        max_length=4096,
    )

    @model_validator(mode="after")
    def validate_lattice(self) -> "ParsedCif":
        PeriodicStructure(lattice=self.lattice, sites=self.asymmetric_sites)
        return self


CanonicalMode = Literal["conventional", "primitive"]


class CanonicalizationSettings(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION] = (
        CANONICALIZATION_CONTRACT_VERSION
    )
    mode: CanonicalMode = "conventional"
    no_idealize: StrictTrue = True
    symprec_angstrom: float = Field(default=1.0e-5, gt=0.0, le=0.1)
    angle_tolerance_degrees: float = Field(default=-1.0, ge=-1.0, le=180.0)
    fractional_wrap: Literal["zero_to_one"] = "zero_to_one"
    lattice_convention: Literal["right_handed_row_vectors"] = (
        "right_handed_row_vectors"
    )
    quantization_decimals: int = Field(default=12, ge=8, le=15)
    max_fractional_quantization_error: float = Field(
        default=5.1e-13,
        gt=0.0,
        le=1.0e-8,
    )
    max_lattice_quantization_error_angstrom: float = Field(
        default=5.1e-13,
        gt=0.0,
        le=1.0e-8,
    )
    duplicate_site_tolerance_angstrom: float = Field(
        default=1.0e-7,
        gt=0.0,
        le=1.0e-3,
    )
    max_duplicate_site_checks: int = Field(
        default=10_000_000,
        ge=1,
        le=1_000_000_000,
    )
    max_sites: int = Field(default=MAX_STRUCTURE_SITES, ge=1, le=MAX_STRUCTURE_SITES)


class ComparatorSettings(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION] = (
        CANONICALIZATION_CONTRACT_VERSION
    )
    canonicalization: CanonicalizationSettings = Field(
        default_factory=CanonicalizationSettings
    )
    max_minimum_image_candidates: int = Field(
        default=1_000_000,
        ge=1,
        le=100_000_000,
    )
    assignment_numeric_tolerance: float = Field(
        default=1.0e-12,
        gt=0.0,
        le=1.0e-6,
    )
    degenerate_metric_tolerance: float = Field(
        default=1.0e-10,
        gt=0.0,
        le=1.0e-5,
    )
    max_assignment_sites: int = Field(default=512, ge=1, le=4096)
    max_assignment_work: int = Field(
        default=50_000_000,
        ge=1,
        le=2_000_000_000,
    )
    max_equivalent_mappings: int = Field(default=10_000, ge=1, le=1_000_000)


class DeterministicAssignment(CanonicalContractModel):
    column_by_row: tuple[int, ...] = Field(min_length=1)
    total_cost: float = Field(ge=0.0)
    assignment_degenerate: StrictBool
    equivalent_assignment_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_assignment(self) -> "DeterministicAssignment":
        expected = tuple(range(len(self.column_by_row)))
        if tuple(sorted(self.column_by_row)) != expected:
            raise ValueError("column_by_row must be a complete permutation")
        if self.assignment_degenerate is False and self.equivalent_assignment_count != 1:
            raise ValueError("non-degenerate assignment requires one equivalent")
        if self.assignment_degenerate is True and self.equivalent_assignment_count < 2:
            raise ValueError("degenerate assignment requires multiple equivalents")
        return self


class CanonicalSite(CanonicalContractModel):
    species: str = Field(pattern=ELEMENT_PATTERN)
    fractional_coordinates: Vector3
    wyckoff_letter: str = Field(pattern=WYCKOFF_PATTERN)
    equivalence_class: int = Field(ge=0)

    @field_validator("fractional_coordinates")
    @classmethod
    def validate_canonical_coordinates(cls, value: Vector3) -> Vector3:
        _validate_vector(value, "canonical coordinates")
        if any(component < 0.0 or component >= 1.0 for component in value):
            raise ValueError("canonical coordinates must be wrapped into [0, 1)")
        return value


class SymmetryClassification(CanonicalContractModel):
    international_number: int = Field(ge=1, le=230)
    international_symbol: str = Field(min_length=1, max_length=32)
    hall_number: int = Field(ge=1, le=530)
    hall_symbol: str = Field(min_length=1, max_length=64)
    choice: str = Field(max_length=32)
    point_group_symbol: str = Field(min_length=1, max_length=32)


class CanonicalStructure(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION]
    canonical_profile: Literal[CANONICALIZATION_PROFILE]
    settings_sha256: Sha256
    mode: CanonicalMode
    lattice: Matrix3
    sites: tuple[CanonicalSite, ...] = Field(
        min_length=1,
        max_length=MAX_STRUCTURE_SITES,
    )
    symmetry: SymmetryClassification

    @model_validator(mode="after")
    def validate_structure(self) -> "CanonicalStructure":
        for row in self.lattice:
            _validate_vector(row, "canonical lattice")
        determinant = _determinant(self.lattice)
        if not math.isfinite(determinant) or determinant <= 1.0e-12:
            raise ValueError("canonical lattice must be right-handed")
        for site in self.sites:
            _validate_vector(site.fractional_coordinates, "canonical coordinates")
        site_keys = tuple(
            (atomic_number(site.species), *site.fractional_coordinates)
            for site in self.sites
        )
        if site_keys != tuple(sorted(site_keys)):
            raise ValueError("canonical sites are not in deterministic order")
        if len(site_keys) != len(set(site_keys)):
            raise ValueError("canonical structure contains duplicate site identities")
        class_signatures: dict[int, tuple[str, str]] = {}
        for site in self.sites:
            class_index = site.equivalence_class
            signature = (site.species, site.wyckoff_letter)
            if class_index not in class_signatures:
                if class_index != len(class_signatures):
                    raise ValueError(
                        "equivalence classes must use canonical first-occurrence numbering"
                    )
                class_signatures[class_index] = signature
            elif class_signatures[class_index] != signature:
                raise ValueError(
                    "one equivalence class cannot mix species or Wyckoff letters"
                )
        return self


class SpeciesCount(CanonicalContractModel):
    species: str = Field(pattern=ELEMENT_PATTERN)
    count: int = Field(ge=1)


class CoordinateFreeStructureProjection(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION]
    projection_profile: Literal[STRUCTURE_PROJECTION_PROFILE]
    canonical_structure_sha256: Sha256
    settings_sha256: Sha256
    mode: CanonicalMode
    atom_count: int = Field(ge=1)
    composition: tuple[SpeciesCount, ...] = Field(min_length=1)
    symmetry: SymmetryClassification
    contains_coordinates: StrictFalse
    contains_lattice_vectors: StrictFalse

    @model_validator(mode="after")
    def validate_projection(self) -> "CoordinateFreeStructureProjection":
        _validate_composition(self.composition, self.atom_count)
        return self


class MinimumImageResult(CanonicalContractModel):
    fractional_displacement: Vector3
    cartesian_displacement_angstrom: Vector3
    distance_angstrom: float = Field(ge=0.0)
    lattice_translation: IntVector3
    candidates_examined: int = Field(ge=1)
    distance_degenerate: StrictBool

    @model_validator(mode="after")
    def validate_distance(self) -> "MinimumImageResult":
        _validate_vector(self.fractional_displacement, "fractional displacement")
        _validate_vector(self.cartesian_displacement_angstrom, "Cartesian displacement")
        norm = math.hypot(*self.cartesian_displacement_angstrom)
        if not math.isfinite(norm):
            raise ValueError("Cartesian displacement norm is not finite")
        if not math.isclose(
            norm,
            self.distance_angstrom,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            raise ValueError("minimum-image distance is inconsistent")
        return self


class AtomDisplacement(CanonicalContractModel):
    reference_index: int = Field(ge=0)
    candidate_index: int = Field(ge=0)
    species: str = Field(pattern=ELEMENT_PATTERN)
    fractional_displacement: Vector3
    cartesian_displacement_angstrom: Vector3
    distance_angstrom: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_distance(self) -> "AtomDisplacement":
        _validate_vector(self.fractional_displacement, "fractional displacement")
        _validate_vector(self.cartesian_displacement_angstrom, "Cartesian displacement")
        norm = math.hypot(*self.cartesian_displacement_angstrom)
        if not math.isfinite(norm):
            raise ValueError("Cartesian displacement norm is not finite")
        if not math.isclose(
            norm,
            self.distance_angstrom,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            raise ValueError("distance does not match Cartesian displacement")
        return self


class AtomMapping(CanonicalContractModel):
    global_origin_shift_fractional: Vector3
    reference_atom_count: int = Field(ge=1)
    candidate_atom_count: int = Field(ge=1)
    displacements: tuple[AtomDisplacement, ...] = Field(min_length=1)
    coverage: float = Field(ge=0.0, le=1.0)
    mapping_degenerate: StrictBool
    equivalent_mapping_count: int = Field(ge=1)
    semantic_identity_preserved: StrictTrue

    @model_validator(mode="after")
    def validate_mapping(self) -> "AtomMapping":
        _validate_vector(self.global_origin_shift_fractional, "global origin shift")
        if any(
            component < 0.0 or component >= 1.0
            for component in self.global_origin_shift_fractional
        ):
            raise ValueError("global origin shift must be wrapped into [0, 1)")
        if self.reference_atom_count != self.candidate_atom_count:
            raise ValueError("mapping requires equal reference and candidate atom counts")
        reference_indices = tuple(item.reference_index for item in self.displacements)
        candidate_indices = tuple(item.candidate_index for item in self.displacements)
        if len(set(reference_indices)) != len(reference_indices):
            raise ValueError("mapping contains duplicate reference indices")
        if len(set(candidate_indices)) != len(candidate_indices):
            raise ValueError("mapping contains duplicate candidate indices")
        if any(index >= self.reference_atom_count for index in reference_indices):
            raise ValueError("mapping reference index is outside reference_atom_count")
        if any(index >= self.candidate_atom_count for index in candidate_indices):
            raise ValueError("mapping candidate index is outside candidate_atom_count")
        expected_coverage = len(self.displacements) / self.reference_atom_count
        if self.coverage != expected_coverage:
            raise ValueError("mapping coverage is inconsistent with mapped atom count")
        expected = tuple(range(self.reference_atom_count))
        if reference_indices != expected:
            raise ValueError(
                "mapping displacements must be stored in reference-index order"
            )
        if tuple(sorted(candidate_indices)) != expected:
            raise ValueError("mapping does not cover every candidate atom")
        if self.mapping_degenerate is False and self.equivalent_mapping_count != 1:
            raise ValueError("non-degenerate mapping requires one equivalent mapping")
        if self.mapping_degenerate is True and self.equivalent_mapping_count < 2:
            raise ValueError("degenerate mapping requires at least two equivalents")
        return self


class LatticeMetrics(CanonicalContractModel):
    reference_lengths_angstrom: Vector3
    candidate_lengths_angstrom: Vector3
    reference_angles_degrees: Vector3
    candidate_angles_degrees: Vector3
    relative_length_errors: Vector3
    angle_differences_degrees: Vector3
    maximum_relative_lattice_error: float = Field(ge=0.0)
    deformation_gradient: Matrix3
    symmetric_strain: Matrix3
    determinant_ratio: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_metrics(self) -> "LatticeMetrics":
        if any(value <= 0.0 for value in self.reference_lengths_angstrom):
            raise ValueError("reference lattice lengths must be positive")
        if any(value <= 0.0 for value in self.candidate_lengths_angstrom):
            raise ValueError("candidate lattice lengths must be positive")
        expected_relative: list[float] = []
        for reference, candidate in zip(
            self.reference_lengths_angstrom,
            self.candidate_lengths_angstrom,
            strict=True,
        ):
            value = candidate / reference - 1.0
            if not math.isfinite(value):
                raise ValueError("derived relative lattice error is not finite")
            expected_relative.append(value)
        expected_angle = tuple(
            candidate - reference
            for reference, candidate in zip(
                self.reference_angles_degrees,
                self.candidate_angles_degrees,
                strict=True,
            )
        )
        if not all(math.isfinite(value) for value in expected_angle):
            raise ValueError("derived lattice angle difference is not finite")
        if any(
            not math.isclose(observed, expected, rel_tol=1.0e-12, abs_tol=0.0)
            for observed, expected in zip(
                self.relative_length_errors,
                expected_relative,
                strict=True,
            )
        ):
            raise ValueError("relative lattice errors are inconsistent")
        if any(
            not math.isclose(observed, expected, rel_tol=1.0e-12, abs_tol=0.0)
            for observed, expected in zip(
                self.angle_differences_degrees,
                expected_angle,
                strict=True,
            )
        ):
            raise ValueError("lattice angle differences are inconsistent")
        maximum = max(abs(value) for value in expected_relative)
        if not math.isfinite(maximum):
            raise ValueError("maximum relative lattice error is not finite")
        if not math.isclose(
            maximum,
            self.maximum_relative_lattice_error,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            raise ValueError("maximum lattice error is inconsistent")
        for row in self.deformation_gradient:
            _validate_vector(row, "deformation gradient")
        for row in self.symmetric_strain:
            _validate_vector(row, "symmetric strain")
        determinant = _determinant(self.deformation_gradient)
        if not math.isfinite(determinant) or determinant <= 0.0 or not math.isclose(
            determinant,
            self.determinant_ratio,
            rel_tol=1.0e-10,
            abs_tol=0.0,
        ):
            raise ValueError("deformation determinant is inconsistent")
        for row_index in range(3):
            for column_index in range(3):
                expected = 0.5 * (
                    self.deformation_gradient[row_index][column_index]
                    + self.deformation_gradient[column_index][row_index]
                ) - (1.0 if row_index == column_index else 0.0)
                observed = self.symmetric_strain[row_index][column_index]
                if not math.isfinite(expected):
                    raise ValueError("derived symmetric strain is not finite")
                if not math.isclose(expected, observed, rel_tol=1.0e-10, abs_tol=0.0):
                    raise ValueError("symmetric strain is inconsistent")
        return self


class StructureComparison(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION]
    comparison_profile: Literal[COMPARISON_PROFILE]
    settings_sha256: Sha256
    reference_structure_sha256: Sha256
    candidate_structure_sha256: Sha256
    atom_count: int = Field(ge=1)
    composition: tuple[SpeciesCount, ...] = Field(min_length=1)
    mapping: AtomMapping
    lattice_metrics: LatticeMetrics
    rms_displacement_angstrom: float = Field(ge=0.0)
    maximum_displacement_angstrom: float = Field(ge=0.0)
    candidate_input_unchanged: StrictTrue
    scientifically_verified: Literal["not_assessed"]

    @model_validator(mode="after")
    def validate_comparison(self) -> "StructureComparison":
        _validate_composition(self.composition, self.atom_count)
        if (
            self.mapping.reference_atom_count != self.atom_count
            or self.mapping.candidate_atom_count != self.atom_count
            or not math.isclose(self.mapping.coverage, 1.0, rel_tol=0.0, abs_tol=0.0)
        ):
            raise ValueError("mapping coverage and atom_count are inconsistent")
        observed_counts: dict[str, int] = {}
        distances: list[float] = []
        for displacement in self.mapping.displacements:
            observed_counts[displacement.species] = (
                observed_counts.get(displacement.species, 0) + 1
            )
            distances.append(displacement.distance_angstrom)
        if observed_counts != _composition_counts(self.composition):
            raise ValueError("mapping species do not match comparison composition")
        expected_maximum = max(distances)
        expected_rms = math.hypot(*distances) / math.sqrt(len(distances))
        if not math.isfinite(expected_maximum) or not math.isfinite(expected_rms):
            raise ValueError("derived displacement metric is not finite")
        if not math.isclose(
            expected_maximum,
            self.maximum_displacement_angstrom,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            raise ValueError("maximum displacement is inconsistent")
        if not math.isclose(
            expected_rms,
            self.rms_displacement_angstrom,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            raise ValueError("RMS displacement is inconsistent")
        return self


class CoordinateFreeComparisonProjection(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION]
    projection_profile: Literal[COMPARISON_PROJECTION_PROFILE]
    settings_sha256: Sha256
    reference_structure_sha256: Sha256
    candidate_structure_sha256: Sha256
    atom_count: int = Field(ge=1)
    composition: tuple[SpeciesCount, ...] = Field(min_length=1)
    mapping_coverage: float = Field(ge=0.0, le=1.0)
    mapping_degenerate: StrictBool
    rms_displacement_angstrom: float = Field(ge=0.0)
    maximum_displacement_angstrom: float = Field(ge=0.0)
    maximum_relative_lattice_error: float = Field(ge=0.0)
    contains_coordinates: StrictFalse
    contains_atom_mapping: StrictFalse
    scientifically_verified: Literal["not_assessed"]

    @model_validator(mode="after")
    def validate_projection(self) -> "CoordinateFreeComparisonProjection":
        _validate_composition(self.composition, self.atom_count)
        return self


class ReferenceEvidenceBinding(CanonicalContractModel):
    source_id: str = Field(min_length=1, max_length=128)
    raw_artifact_sha256: Sha256
    raw_artifact_byte_count: int = Field(ge=1, le=16 * 1024 * 1024)
    raw_artifact_relative_path: str = Field(min_length=1, max_length=512)
    source_record_sha256: Sha256
    source_record_relative_path: str = Field(min_length=1, max_length=512)
    manifest_sha256: Sha256
    manifest_relative_path: str = Field(min_length=1, max_length=512)
    media_type: Literal["chemical/x-cif"]
    structure_format: Literal["cif"]
    license_spdx_id: str | None = Field(default=None, min_length=1, max_length=64)
    redistributable: StrictTrue

    @field_validator(
        "raw_artifact_relative_path",
        "source_record_relative_path",
        "manifest_relative_path",
    )
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_relative_path(value, "reference evidence path")

    @model_validator(mode="after")
    def validate_content_addressed_paths(self) -> "ReferenceEvidenceBinding":
        expected = (
            (
                self.raw_artifact_relative_path,
                f"raw/sha256/{self.raw_artifact_sha256[:2]}/{self.raw_artifact_sha256}.bin",
            ),
            (
                self.source_record_relative_path,
                "sources/sha256/"
                f"{self.source_record_sha256[:2]}/{self.source_record_sha256}.json",
            ),
            (
                self.manifest_relative_path,
                "manifests/sha256/"
                f"{self.manifest_sha256[:2]}/{self.manifest_sha256}.json",
            ),
        )
        if any(observed != required for observed, required in expected):
            raise ValueError("reference evidence paths do not match their SHA-256 values")
        paths = tuple(observed for observed, _ in expected)
        if len(set(paths)) != len(paths):
            raise ValueError("reference evidence paths must be distinct")
        return self


class ImplementationBinding(CanonicalContractModel):
    implementation_version: Literal[IMPLEMENTATION_VERSION]
    numpy_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)?$")
    spglib_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)?$")
    crystallographic_kernel: Literal["spglib"]
    crystallographic_kernel_license: Literal["BSD-3-Clause"]


class CanonicalReferenceArtifact(CanonicalContractModel):
    contract_version: Literal[CANONICALIZATION_CONTRACT_VERSION]
    artifact_profile: Literal[CANONICAL_ARTIFACT_PROFILE]
    source: ReferenceEvidenceBinding
    settings: CanonicalizationSettings
    settings_sha256: Sha256
    implementation: ImplementationBinding
    canonical_structure: CanonicalStructure
    canonical_structure_sha256: Sha256
    coordinate_free_summary: CoordinateFreeStructureProjection
    original_artifact_preserved: StrictTrue
    candidate_template: StrictFalse
    hidden_holdout: StrictFalse

    @model_validator(mode="after")
    def validate_bindings(self) -> "CanonicalReferenceArtifact":
        expected_settings_sha256 = _canonical_sha256(self.settings)
        if self.settings_sha256 != expected_settings_sha256:
            raise ValueError("settings SHA-256 does not match canonical settings")
        if self.canonical_structure.settings_sha256 != self.settings_sha256:
            raise ValueError("canonical structure settings binding does not match")
        if self.canonical_structure.mode != self.settings.mode:
            raise ValueError("canonical structure mode does not match settings")
        decimals = self.settings.quantization_decimals
        numeric_values = tuple(
            value for row in self.canonical_structure.lattice for value in row
        ) + tuple(
            value
            for site in self.canonical_structure.sites
            for value in site.fractional_coordinates
        )
        if any(
            value != round(value, decimals)
            or (value == 0.0 and math.copysign(1.0, value) < 0.0)
            for value in numeric_values
        ):
            raise ValueError("canonical structure values do not match settings quantization")
        expected_structure_sha256 = _canonical_sha256(self.canonical_structure)
        if self.canonical_structure_sha256 != expected_structure_sha256:
            raise ValueError("canonical structure SHA-256 does not match")
        summary = self.coordinate_free_summary
        expected_counts: dict[str, int] = {}
        for site in self.canonical_structure.sites:
            expected_counts[site.species] = expected_counts.get(site.species, 0) + 1
        expected_composition = tuple(
            SpeciesCount(species=species, count=count)
            for species, count in sorted(expected_counts.items())
        )
        if (
            summary.canonical_structure_sha256 != self.canonical_structure_sha256
            or summary.settings_sha256 != self.settings_sha256
            or summary.mode != self.canonical_structure.mode
            or summary.atom_count != len(self.canonical_structure.sites)
            or summary.composition != expected_composition
            or summary.symmetry != self.canonical_structure.symmetry
        ):
            raise ValueError("coordinate-free summary does not bind canonical structure")
        return self


__all__ = [
    "AffineSymmetryOperation",
    "AtomDisplacement",
    "AtomMapping",
    "AtomSite",
    "CANONICALIZATION_CONTRACT_VERSION",
    "CANONICALIZATION_PROFILE",
    "CANONICAL_ARTIFACT_PROFILE",
    "COMPARISON_PROFILE",
    "COMPARISON_PROJECTION_PROFILE",
    "CanonicalContractModel",
    "CanonicalMode",
    "CanonicalReferenceArtifact",
    "CanonicalSite",
    "CanonicalStructure",
    "CanonicalizationSettings",
    "ComparatorSettings",
    "CoordinateFreeComparisonProjection",
    "CoordinateFreeStructureProjection",
    "DeterministicAssignment",
    "IMPLEMENTATION_VERSION",
    "ImplementationBinding",
    "IntMatrix3",
    "IntVector3",
    "LatticeMetrics",
    "MAX_STRUCTURE_SITES",
    "Matrix3",
    "MinimumImageResult",
    "ParsedCif",
    "PeriodicStructure",
    "RationalValue",
    "ReferenceEvidenceBinding",
    "STRUCTURE_PROJECTION_PROFILE",
    "Sha256",
    "SpeciesCount",
    "StrictFalse",
    "StrictTrue",
    "StructureComparison",
    "SymmetryClassification",
    "Vector3",
]
