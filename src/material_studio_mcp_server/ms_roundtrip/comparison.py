"""Candidate identity and coordinate-free CIF round-trip comparison."""

from __future__ import annotations

import math
import shlex
from functools import lru_cache

from material_studio_mcp_server.canonicalization import (
    CANONICALIZATION_CONTRACT_VERSION,
    IMPLEMENTATION_VERSION as CANONICALIZATION_IMPLEMENTATION_VERSION,
    AtomSite,
    CanonicalStructure,
    CanonicalizationSettings,
    ComparatorSettings,
    PeriodicStructure,
    canonical_structure_sha256,
    canonicalize_periodic_crystal,
    compare_structures,
    parse_cif_structure,
    project_structure_comparison,
)
from material_studio_mcp_server.domains.surface import PLUGIN
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    ModelKind,
    ModelingIntent,
    ReferenceAccess,
    ReferenceAccessMode,
    RUNTIME_CONTRACT_VERSION,
    SemanticParameter,
)
from material_studio_mcp_server.specs import CrystalSpec, ModelSpec

from .contracts import CandidateValidationReceipt, RoundtripComparisonReceipt
from .errors import RoundtripError, RoundtripErrorCode
from .secure_io import sha256_bytes


EXPECTED_COMPOSITION = ("C:32", "H:16", "Si:32")
EXPECTED_VACUUM_ANGSTROM = 15.0
_FIXED_IDENTITY_MAXIMUM_DISPLACEMENT = 1.0e-7
_FIXED_IDENTITY_MAXIMUM_LATTICE_ERROR = 1.0e-10


def _canonicalizer_compatible_cif(payload: bytes) -> bytes:
    """Normalize the fixed translator's labels and omitted occupancy in memory."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "A CIF without occupancy must use the supported ASCII atom loop.",
        ) from exc
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    required = {
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
    }
    matches: list[tuple[int, int, tuple[str, ...]]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip().casefold() != "loop_":
            index += 1
            continue
        header_start = index + 1
        header_end = header_start
        while header_end < len(lines) and lines[header_end].lstrip().startswith("_"):
            header_end += 1
        headers = tuple(lines[item].strip().casefold() for item in range(header_start, header_end))
        if required.issubset(headers):
            data_end = header_end
            while data_end < len(lines):
                stripped = lines[data_end].strip()
                if not stripped:
                    break
                lowered = stripped.casefold()
                if (
                    stripped.startswith("_")
                    or lowered == "loop_"
                    or lowered.startswith("data_")
                    or lowered.startswith("save_")
                    or lowered == "stop_"
                    or stripped.startswith("#")
                ):
                    break
                data_end += 1
            matches.append((header_end, data_end, headers))
        index = max(header_end, index + 1)
    if len(matches) != 1:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "The CIF must contain one unambiguous fractional atom loop.",
        )
    header_end, data_end, headers = matches[0]
    if "_atom_site_label" not in headers or "_atom_site_type_symbol" not in headers:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "The supported atom loop requires label and type-symbol columns.",
        )
    column_count = len(headers)
    if data_end == header_end:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "The fractional atom loop contains no rows.",
        )
    normalized_rows: list[str] = []
    species_counts: dict[str, int] = {}
    label_index = headers.index("_atom_site_label")
    type_index = headers.index("_atom_site_type_symbol")
    occupancy_missing = "_atom_site_occupancy" not in headers
    for row in lines[header_end:data_end]:
        try:
            fields = shlex.split(row, comments=False, posix=True)
        except ValueError as exc:
            raise RoundtripError(
                RoundtripErrorCode.COMPARISON_FAILED,
                "The fractional atom loop contains an unsupported row.",
            ) from exc
        if len(fields) != column_count:
            raise RoundtripError(
                RoundtripErrorCode.COMPARISON_FAILED,
                "The fractional atom loop has inconsistent row widths.",
            )
        species = fields[type_index]
        if species not in {"C", "H", "Si"}:
            raise RoundtripError(
                RoundtripErrorCode.COMPARISON_FAILED,
                "The fixed round-trip atom loop contains an unsupported species.",
            )
        species_counts[species] = species_counts.get(species, 0) + 1
        fields[label_index] = f"{species}{species_counts[species]}"
        if occupancy_missing:
            fields.append("1.0")
        normalized_rows.append("  " + " ".join(fields))
    inserted_headers = (
        ["_atom_site_occupancy"] if occupancy_missing else []
    )
    augmented = [
        *lines[:header_end],
        *inserted_headers,
        *normalized_rows,
        *lines[data_end:],
    ]
    symmetry_tags = {
        "_space_group_symop_operation_xyz",
        "_symmetry_equiv_pos_as_xyz",
    }
    present_tags = {line.strip().casefold() for line in augmented if line.lstrip().startswith("_")}
    if not symmetry_tags.intersection(present_tags):
        augmented.extend(
            [
                "",
                "loop_",
                "_space_group_symop_operation_xyz",
                "'x,y,z'",
            ]
        )
    return ("\n".join(augmented) + "\n").encode("ascii")


def _parse_bound_cif(
    payload: bytes,
    *,
    expected_sha256: str,
) -> PeriodicStructure:
    """Parse bound bytes, tolerating the surface translator's narrow CIF dialect."""

    if sha256_bytes(payload) != expected_sha256:
        raise RoundtripError(
            RoundtripErrorCode.INPUT_IDENTITY_MISMATCH,
            "CIF bytes do not match their artifact binding.",
        )
    try:
        return parse_cif_structure(
            payload,
            expected_sha256=expected_sha256,
            expected_byte_count=len(payload),
        )
    except Exception as original_error:
        try:
            augmented = _canonicalizer_compatible_cif(payload)
            augmented_sha256 = sha256_bytes(augmented)
            return parse_cif_structure(
                augmented,
                expected_sha256=augmented_sha256,
                expected_byte_count=len(augmented),
            )
        except Exception:
            raise original_error


def _structures_from_bound_cif(
    payload: bytes,
    *,
    expected_sha256: str,
    settings: CanonicalizationSettings,
) -> tuple[PeriodicStructure, CanonicalStructure]:
    parsed = _parse_bound_cif(payload, expected_sha256=expected_sha256)
    canonical = canonicalize_periodic_crystal(parsed, settings=settings)
    return parsed, canonical


def _vector_cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(left, right))


def _norm(value: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(value, value))


def _cif_round(value: float) -> float:
    return float(f"{float(value):.10g}")


def _lattice_vectors(crystal: CrystalSpec) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    lattice = crystal.lattice
    alpha = math.radians(lattice.alpha)
    beta = math.radians(lattice.beta)
    gamma = math.radians(lattice.gamma)
    sine_gamma = math.sin(gamma)
    if abs(sine_gamma) <= 1.0e-15:
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "The fixed candidate lattice is singular.",
        )
    a = (_cif_round(lattice.a), 0.0, 0.0)
    b = (
        _cif_round(lattice.b * math.cos(gamma)),
        _cif_round(lattice.b * sine_gamma),
        0.0,
    )
    c_x = lattice.c * math.cos(beta)
    c_y = lattice.c * (
        math.cos(alpha) - math.cos(beta) * math.cos(gamma)
    ) / sine_gamma
    c_z_squared = lattice.c * lattice.c - c_x * c_x - c_y * c_y
    if c_z_squared <= 0.0:
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "The fixed candidate lattice is invalid.",
        )
    c = (_cif_round(c_x), _cif_round(c_y), _cif_round(math.sqrt(c_z_squared)))
    return a, b, c


def _periodic_structure_from_model(model: ModelSpec) -> PeriodicStructure:
    crystal = model.model
    if not isinstance(crystal, CrystalSpec):
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "The fixed surface plugin did not return a crystal.",
        )
    return PeriodicStructure(
        lattice=_lattice_vectors(crystal),
        sites=tuple(
            AtomSite(
                species=atom.element,
                fractional_coordinates=tuple(
                    _cif_round(component) for component in atom.fractional.as_tuple()
                ),
                occupancy=1.0,
                label=atom.id,
            )
            for atom in crystal.basis_atoms
        ),
    )


@lru_cache(maxsize=1)
def _expected_fixed_candidate() -> CanonicalStructure:
    intent = ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="ms-roundtrip-fixed-candidate",
        material="3C-SiC",
        scenario="surface_slab",
        operation="create_si_face_slab",
        model_kind=ModelKind.CRYSTAL,
        requires_current_model=False,
        output_kind=BuildOutputKind.MODEL_SPEC,
        parameters=(
            SemanticParameter(name="project_id", value="ms_roundtrip_identity"),
        ),
        semantic_requirements=(),
        declared_assumptions=(),
        reference_access=ReferenceAccess(
            mode=ReferenceAccessMode.TASK_ONLY,
            source_ids=("cod-1010995",),
            raw_structure_access=False,
            final_coordinate_access=False,
            hidden_holdout_access=False,
        ),
    )
    plan = PLUGIN.plan(intent, None)
    model = PLUGIN.build(plan)
    validation = PLUGIN.validate(model)
    if not validation.preview_eligible:
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "The fixed surface plugin failed its own validation.",
        )
    return canonicalize_periodic_crystal(
        _periodic_structure_from_model(model),
        settings=CanonicalizationSettings(),
    )


def _composition(
    structure: PeriodicStructure | CanonicalStructure,
) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for site in structure.sites:
        counts[site.species] = counts.get(site.species, 0) + 1
    return tuple(f"{species}:{count}" for species, count in sorted(counts.items()))


def full_extent_vacuum_angstrom(
    structure: PeriodicStructure | CanonicalStructure,
) -> float:
    """Measure the largest translation-invariant empty gap over cell axes."""

    vectors = tuple(tuple(float(value) for value in row) for row in structure.lattice)
    volume = abs(_dot(vectors[0], _vector_cross(vectors[1], vectors[2])))
    if not math.isfinite(volume) or volume <= 0.0:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "Canonical lattice volume is invalid.",
        )
    gaps: list[float] = []
    for axis in range(3):
        other = tuple(index for index in range(3) if index != axis)
        area = _norm(_vector_cross(vectors[other[0]], vectors[other[1]]))
        if not math.isfinite(area) or area <= 0.0:
            raise RoundtripError(
                RoundtripErrorCode.COMPARISON_FAILED,
                "Canonical lattice area is invalid.",
            )
        height = volume / area
        coordinates = sorted(
            {round(float(site.fractional_coordinates[axis]) % 1.0, 12) for site in structure.sites}
        )
        if not coordinates:
            raise RoundtripError(
                RoundtripErrorCode.COMPARISON_FAILED,
                "Canonical structure has no sites.",
            )
        fractional_gaps = [
            right - left for left, right in zip(coordinates, coordinates[1:])
        ]
        fractional_gaps.append(coordinates[0] + 1.0 - coordinates[-1])
        gaps.append(max(fractional_gaps) * height)
    vacuum = max(gaps)
    if not math.isfinite(vacuum) or vacuum < 0.0:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "Full-extent vacuum measurement is invalid.",
        )
    return vacuum


def validate_fixed_candidate_cif(
    payload: bytes,
    *,
    expected_sha256: str,
) -> CandidateValidationReceipt:
    if sha256_bytes(payload) != expected_sha256:
        raise RoundtripError(
            RoundtripErrorCode.INPUT_IDENTITY_MISMATCH,
            "Candidate CIF digest does not match the request binding.",
        )
    try:
        settings = CanonicalizationSettings()
        parsed_candidate, candidate = _structures_from_bound_cif(
            payload,
            settings=settings,
            expected_sha256=expected_sha256,
        )
        expected = _expected_fixed_candidate()
        comparison = compare_structures(
            expected,
            candidate,
            ComparatorSettings(canonicalization=settings),
        )
    except RoundtripError:
        raise
    except Exception as exc:
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "Candidate CIF could not be matched to the fixed surface profile.",
        ) from exc
    projection = project_structure_comparison(comparison)
    composition = _composition(parsed_candidate)
    vacuum = full_extent_vacuum_angstrom(parsed_candidate)
    fixed_match = (
        len(parsed_candidate.sites) == 80
        and composition == EXPECTED_COMPOSITION
        and projection.mapping_coverage == 1.0
        and projection.maximum_displacement_angstrom
        <= _FIXED_IDENTITY_MAXIMUM_DISPLACEMENT
        and projection.maximum_relative_lattice_error
        <= _FIXED_IDENTITY_MAXIMUM_LATTICE_ERROR
        and abs(vacuum - EXPECTED_VACUUM_ANGSTROM) <= 1.0e-6
    )
    if not fixed_match:
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "Only the deterministic revision-zero 3C-SiC surface candidate is supported.",
        )
    return CandidateValidationReceipt(
        plugin_id=PLUGIN.plugin_id,
        plugin_contract_version=PLUGIN.contract_version,
        plugin_implementation_version=PLUGIN.implementation_version,
        fixed_candidate_match=True,
        canonical_structure_sha256=canonical_structure_sha256(candidate),
        expected_canonical_structure_sha256=canonical_structure_sha256(expected),
        atom_count=80,
        composition=EXPECTED_COMPOSITION,
        vacuum_angstrom=vacuum,
        mapping_coverage=1.0,
        maximum_displacement_angstrom=projection.maximum_displacement_angstrom,
        maximum_relative_lattice_error=projection.maximum_relative_lattice_error,
    )


def compare_roundtrip_cif_bytes(
    input_payload: bytes,
    output_payload: bytes,
    *,
    expected_input_sha256: str,
    expected_output_sha256: str,
) -> RoundtripComparisonReceipt:
    if (
        sha256_bytes(input_payload) != expected_input_sha256
        or sha256_bytes(output_payload) != expected_output_sha256
    ):
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "Round-trip CIF bytes do not match their artifact bindings.",
        )
    try:
        settings = CanonicalizationSettings()
        input_parsed, input_structure = _structures_from_bound_cif(
            input_payload,
            settings=settings,
            expected_sha256=expected_input_sha256,
        )
        output_parsed, output_structure = _structures_from_bound_cif(
            output_payload,
            settings=settings,
            expected_sha256=expected_output_sha256,
        )
        comparison = compare_structures(
            input_structure,
            output_structure,
            ComparatorSettings(canonicalization=settings),
        )
        projection = project_structure_comparison(comparison)
        input_vacuum = full_extent_vacuum_angstrom(input_parsed)
        output_vacuum = full_extent_vacuum_angstrom(output_parsed)
    except RoundtripError:
        raise
    except Exception as exc:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "Canonical round-trip comparison failed.",
        ) from exc
    composition = _composition(output_parsed)
    if len(output_parsed.sites) != 80 or composition != EXPECTED_COMPOSITION:
        raise RoundtripError(
            RoundtripErrorCode.COMPARISON_FAILED,
            "Round-trip output atom count or composition changed.",
        )
    vacuum_error = abs(output_vacuum - input_vacuum)
    mapping_pass = projection.mapping_coverage == 1.0
    rms_pass = projection.rms_displacement_angstrom <= 0.05
    maximum_pass = projection.maximum_displacement_angstrom <= 0.15
    lattice_pass = projection.maximum_relative_lattice_error <= 0.001
    vacuum_pass = vacuum_error <= 0.1
    return RoundtripComparisonReceipt(
        canonicalization_contract_version=CANONICALIZATION_CONTRACT_VERSION,
        canonicalization_implementation_version=(
            CANONICALIZATION_IMPLEMENTATION_VERSION
        ),
        input_canonical_structure_sha256=canonical_structure_sha256(input_structure),
        output_canonical_structure_sha256=canonical_structure_sha256(output_structure),
        atom_count=80,
        composition=EXPECTED_COMPOSITION,
        mapping_coverage=projection.mapping_coverage,
        mapping_degenerate=projection.mapping_degenerate,
        rms_displacement_angstrom=projection.rms_displacement_angstrom,
        maximum_displacement_angstrom=projection.maximum_displacement_angstrom,
        maximum_relative_lattice_error=projection.maximum_relative_lattice_error,
        input_vacuum_angstrom=input_vacuum,
        output_vacuum_angstrom=output_vacuum,
        vacuum_absolute_error_angstrom=vacuum_error,
        input_candidate_unchanged_by_comparator=True,
        mapping_pass=mapping_pass,
        rms_pass=rms_pass,
        maximum_displacement_pass=maximum_pass,
        lattice_pass=lattice_pass,
        vacuum_pass=vacuum_pass,
        passed=all((mapping_pass, rms_pass, maximum_pass, lattice_pass, vacuum_pass)),
    )


__all__ = [
    "EXPECTED_COMPOSITION",
    "EXPECTED_VACUUM_ANGSTROM",
    "compare_roundtrip_cif_bytes",
    "full_extent_vacuum_angstrom",
    "validate_fixed_candidate_cif",
]
