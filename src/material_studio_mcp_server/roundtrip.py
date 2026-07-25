"""Revision-bound Materials Studio structure round-trip auditing.

This module deliberately sits between revision execution and GUI hot-loading.
It verifies that a generated CIF can be imported and exported by the configured
Materials Studio runner without changing the source artifact or the existing
GUI process/window inventory.  It does not run calculations and it never
claims scientific correctness.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .parsers.cif import parse_crystal_cif
from .runner import MaterialStudioRunner
from .scripts import import_export_script
from .specs.crystal import CrystalSpec
from .specs.project import ModelSpec
from .state.execution import canonical_json_sha256
from .state.store import atomic_write_text
from .validators import validate_generated_script


ROUNDTRIP_AUDIT_SCHEMA_VERSION = "material_studio_revision_roundtrip_audit_v2"
ROUNDTRIP_AUDIT_PROFILE = (
    "generic_crystal_cif_import_export_with_visual_bonding_v2"
)
VISUAL_BONDED_ARTIFACT_SCHEMA_VERSION = (
    "material_studio_visual_bonded_artifact_v1"
)
GUI_HOTLOAD_STRUCTURE_SELECTION_SCHEMA_VERSION = (
    "material_studio_gui_hotload_structure_selection_v1"
)
ROUNDTRIP_MAX_INPUT_BYTES = 16 * 1024 * 1024
ROUNDTRIP_MAX_VISUAL_BYTES = 128 * 1024 * 1024
ROUNDTRIP_MAX_XML_PROLOG_BYTES = 1024 * 1024
ROUNDTRIP_MAX_ATOMS = 20_000
ROUNDTRIP_MS20_1_SAFE_PATH_LIMIT = 240
ROUNDTRIP_RMS_TOLERANCE_ANGSTROM = 0.05
ROUNDTRIP_MAX_DISPLACEMENT_TOLERANCE_ANGSTROM = 0.15
ROUNDTRIP_LATTICE_RELATIVE_TOLERANCE = 0.001
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")
_DANGEROUS_GENERATED_MARKERS = (
    re.compile(r"\bsystem\s*\(", re.IGNORECASE),
    re.compile(r"`[^`]*`"),
    re.compile(r"\bqx\s*[/({\[]", re.IGNORECASE),
    re.compile(r"\b(?:unlink|rmdir|remove|delete)\b", re.IGNORECASE),
    re.compile(r"\b(?:socket|http|https|ftp|LWP::|IO::Socket)", re.IGNORECASE),
    re.compile(r"\b(?:Forcite|CASTEP)\b", re.IGNORECASE),
    re.compile(r"\b(?:RunMatScript|RunMatserver)\b", re.IGNORECASE),
)


class RoundtripRunner(Protocol):
    """Minimal runner surface used by the audit and its offline tests."""

    config: Any

    def run_script(
        self,
        script: str,
        *,
        working_dir: str | Path | None = None,
        timeout_seconds: int | None = None,
        job_prefix: str = "msjob",
        keep_script_name: str = "script.pl",
        direct_job_dir: bool = False,
    ) -> Any: ...


class _RoundtripModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CifRoundtripComparison(_RoundtripModel):
    schema_version: Literal["material_studio_cif_roundtrip_comparison_v1"] = (
        "material_studio_cif_roundtrip_comparison_v1"
    )
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_atom_count: int | None = Field(default=None, ge=0)
    output_atom_count: int | None = Field(default=None, ge=0)
    input_element_counts: dict[str, int] = Field(default_factory=dict)
    output_element_counts: dict[str, int] = Field(default_factory=dict)
    mapping_coverage: float = Field(ge=0.0, le=1.0)
    mapping_degenerate: bool = False
    rms_displacement_angstrom: float | None = Field(default=None, ge=0.0)
    maximum_displacement_angstrom: float | None = Field(default=None, ge=0.0)
    maximum_relative_lattice_error: float | None = Field(default=None, ge=0.0)
    passed: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VisualBondedArtifactReceipt(_RoundtripModel):
    schema_version: Literal[VISUAL_BONDED_ARTIFACT_SCHEMA_VERSION] = (
        VISUAL_BONDED_ARTIFACT_SCHEMA_VERSION
    )
    requested: bool
    required: Literal[False] = False
    status: Literal["ready", "failed", "unavailable", "not_requested"]
    ok: bool | None
    criteria: dict[str, float] = Field(default_factory=dict)
    source_path: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confined: bool | None = None
    format_verified: bool | None = None
    root_tag: str | None = None
    tagged_summary_verified: bool | None = None
    atom_count: int | None = Field(default=None, ge=0)
    calculated_bond_count: int | None = Field(default=None, ge=0)
    bond_count: int | None = Field(default=None, ge=0)
    xsd_bond_element_count: int | None = Field(default=None, ge=0)
    atom_count_matches_source: bool | None = None
    bond_calculation_performed: bool = False
    visual_export_performed: bool = False
    structure_truth_authority: Literal[False] = False
    calculation_input_allowed: Literal[False] = False
    gui_hotload_candidate: bool = False
    failure_does_not_fail_roundtrip: Literal[True] = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RoundtripAuditPlan(_RoundtripModel):
    schema_version: Literal[ROUNDTRIP_AUDIT_SCHEMA_VERSION] = (
        ROUNDTRIP_AUDIT_SCHEMA_VERSION
    )
    profile: Literal[ROUNDTRIP_AUDIT_PROFILE] = ROUNDTRIP_AUDIT_PROFILE
    project_id: str
    revision: int = Field(ge=0)
    execution_mode: Literal["preview", "execute"]
    required: bool
    applicable: bool
    status: Literal[
        "preview_ready",
        "deferred_until_materialized",
        "not_applicable",
        "blocked",
    ]
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_id: str
    run_root: str | None = None
    output_path: str | None = None
    visual_output_path: str | None = None
    visual_bonding_planned: bool = False
    visual_bonding_required: Literal[False] = False
    script: str | None = None
    script_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    script_validation: dict[str, Any] = Field(default_factory=dict)
    gui_probe_planned: bool = False
    runner_call_planned: bool = False
    side_effects: dict[str, bool] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RoundtripAuditReceipt(_RoundtripModel):
    schema_version: Literal[ROUNDTRIP_AUDIT_SCHEMA_VERSION] = (
        ROUNDTRIP_AUDIT_SCHEMA_VERSION
    )
    profile: Literal[ROUNDTRIP_AUDIT_PROFILE] = ROUNDTRIP_AUDIT_PROFILE
    project_id: str
    revision: int = Field(ge=0)
    execution_mode: Literal["preview", "execute"]
    required: bool
    applicable: bool
    status: Literal[
        "passed",
        "failed",
        "not_applicable",
        "deferred_until_materialized",
        "blocked",
    ]
    ok: bool | None
    scientific_status: Literal["NOT_RUN"] = "NOT_RUN"
    scientific_correctness_established: Literal[False] = False
    calculation_performed: Literal[False] = False
    gui_input_performed: Literal[False] = False
    matstudio_process_launched: bool = False
    real_materials_studio_status: Literal["PASS", "FAIL", "NOT_RUN"] = "NOT_RUN"
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str | None = None
    source_sha256_planned: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_sha256_before: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_sha256_after: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_path: str | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_root: str | None = None
    script_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    receipt_path: str | None = None
    source_unchanged: bool | None = None
    output_confined: bool | None = None
    runner_script_confined: bool | None = None
    script_identity_verified: bool | None = None
    tagged_summary_verified: bool | None = None
    runner_success: bool | None = None
    runner_timed_out: bool | None = None
    runner_duration_seconds: float | None = Field(default=None, ge=0.0)
    runner_return_code: int | None = None
    runner_termination_markers: dict[str, bool] = Field(default_factory=dict)
    runner_success_markers_required: bool | None = None
    runner_script_bytes_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    runner_path_budget: dict[str, Any] = Field(default_factory=dict)
    runner_created_files: list[str] = Field(default_factory=list)
    runner_identity: dict[str, Any] = Field(default_factory=dict)
    gui_invariant: dict[str, Any] = Field(default_factory=dict)
    comparison: CifRoundtripComparison | None = None
    visual_bonded_artifact: VisualBondedArtifactReceipt = Field(
        default_factory=lambda: VisualBondedArtifactReceipt(
            requested=False,
            status="not_requested",
            ok=None,
        )
    )
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    size = path.stat().st_size
    if size > ROUNDTRIP_MAX_INPUT_BYTES:
        raise ValueError(
            f"Round-trip structure exceeds {ROUNDTRIP_MAX_INPUT_BYTES} bytes: {path}"
        )
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            bytes_read += len(chunk)
            if bytes_read > ROUNDTRIP_MAX_INPUT_BYTES:
                raise ValueError(
                    f"Round-trip structure exceeds {ROUNDTRIP_MAX_INPUT_BYTES} bytes: {path}"
                )
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_visual_file(path: Path) -> str:
    size = path.stat().st_size
    if size > ROUNDTRIP_MAX_VISUAL_BYTES:
        raise ValueError(
            "Visual bonded XSD exceeds "
            f"{ROUNDTRIP_MAX_VISUAL_BYTES} bytes: {path}"
        )
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            bytes_read += len(chunk)
            if bytes_read > ROUNDTRIP_MAX_VISUAL_BYTES:
                raise ValueError(
                    "Visual bonded XSD exceeds "
                    f"{ROUNDTRIP_MAX_VISUAL_BYTES} bytes: {path}"
                )
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_visual_xsd(path: Path) -> dict[str, Any]:
    prolog = bytearray()
    root_found = False
    rolling_tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            upper_chunk = chunk.upper()
            if root_found:
                marker_scan = rolling_tail + upper_chunk
                if b"<!DOCTYPE" in marker_scan or b"<!ENTITY" in marker_scan:
                    raise ValueError(
                        "Visual bonded XSD contains a declaration after the "
                        "root element."
                    )
                rolling_tail = marker_scan[-16:]
                continue
            prolog.extend(chunk)
            root_match = re.search(
                br"<XSD(?:\s|>)",
                bytes(prolog),
                flags=re.IGNORECASE,
            )
            if root_match is None:
                if len(prolog) > ROUNDTRIP_MAX_XML_PROLOG_BYTES:
                    raise ValueError(
                        "Visual bonded XSD root was not found within the "
                        f"{ROUNDTRIP_MAX_XML_PROLOG_BYTES}-byte prolog limit."
                    )
                continue
            root_offset = root_match.start()
            if root_offset > ROUNDTRIP_MAX_XML_PROLOG_BYTES:
                raise ValueError(
                    "Visual bonded XSD root was not found within the "
                    f"{ROUNDTRIP_MAX_XML_PROLOG_BYTES}-byte prolog limit."
                )
            root_found = True
            declaration_bytes = bytes(prolog[:root_offset])
            after_root = bytes(prolog[root_offset:]).upper()
            if b"<!DOCTYPE" in after_root or b"<!ENTITY" in after_root:
                raise ValueError(
                    "Visual bonded XSD contains a declaration after the root "
                    "element."
                )
            rolling_tail = after_root[-16:]

    if not root_found:
        raise ValueError("Visual bonded XSD has no XSD root element.")
    prefix = declaration_bytes.upper()
    if b"<!ENTITY" in prefix:
        raise ValueError(
            "Visual bonded XSD contains a forbidden entity declaration."
        )
    prefix_text = prefix.decode("latin1", errors="replace")
    for declaration in re.findall(
        r"<!DOCTYPE\b[^>]*>",
        prefix_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        if " ".join(declaration.split()).casefold() != "<!doctype xsd []>":
            raise ValueError(
                "Visual bonded XSD contains an unsupported external or "
                "non-empty DTD declaration."
            )
    if b"<!DOCTYPE" in prefix and not re.search(
        br"<!DOCTYPE\b[^>]*>",
        prefix,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raise ValueError("Visual bonded XSD contains an incomplete DTD declaration.")
    root_tag: str | None = None
    bond_count = 0
    for event, element in ET.iterparse(path, events=("start", "end")):
        local_name = str(element.tag).rsplit("}", 1)[-1]
        if event == "start" and root_tag is None:
            root_tag = local_name
        if event == "end":
            if local_name == "Bond":
                bond_count += 1
            element.clear()
    return {
        "root_tag": root_tag,
        "format_verified": root_tag == "XSD",
        "xsd_bond_element_count": bond_count,
    }


def _canonical_spec_sha256(spec: ModelSpec) -> str:
    return canonical_json_sha256(spec.model_dump(mode="json"))


def _ensure_inside(root: Path, candidate: Path, *, label: str) -> Path:
    root_resolved = root.expanduser().resolve()
    candidate_resolved = candidate.expanduser().resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the revision output directory") from exc
    return candidate_resolved


def _validate_run_id(run_id: str) -> str:
    value = str(run_id).strip()
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError("round-trip run_id contains unsupported path characters")
    return value


def _lattice_vectors(lattice: dict[str, Any]) -> tuple[tuple[float, float, float], ...]:
    a = float(lattice["a"])
    b = float(lattice["b"])
    c = float(lattice["c"])
    alpha = math.radians(float(lattice["alpha"]))
    beta = math.radians(float(lattice["beta"]))
    gamma = math.radians(float(lattice["gamma"]))
    sin_gamma = math.sin(gamma)
    if abs(sin_gamma) < 1.0e-12:
        raise ValueError("CIF lattice has a singular gamma angle")
    ax = a
    bx = b * math.cos(gamma)
    by = b * sin_gamma
    cx = c * math.cos(beta)
    cy = c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / sin_gamma
    cz2 = c * c - cx * cx - cy * cy
    if cz2 <= 0.0:
        raise ValueError("CIF lattice is not positive definite")
    return ((ax, 0.0, 0.0), (bx, by, 0.0), (cx, cy, math.sqrt(cz2)))


def _cartesian_delta(
    left: list[float],
    right: list[float],
    lattice: tuple[tuple[float, float, float], ...],
) -> float:
    fractional = [float(b) - float(a) for a, b in zip(left, right)]
    fractional = [value - round(value) for value in fractional]
    cartesian = tuple(
        sum(fractional[index] * lattice[index][axis] for index in range(3))
        for axis in range(3)
    )
    return math.sqrt(sum(value * value for value in cartesian))


def _matrix_inverse(
    matrix: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    """Return a 3x3 inverse for the row-vector lattice convention."""

    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(determinant) < 1.0e-14:
        raise ValueError("lattice matrix is singular")
    return (
        (
            (e * i - f * h) / determinant,
            (c * h - b * i) / determinant,
            (b * f - c * e) / determinant,
        ),
        (
            (f * g - d * i) / determinant,
            (a * i - c * g) / determinant,
            (c * d - a * f) / determinant,
        ),
        (
            (d * h - e * g) / determinant,
            (b * g - a * h) / determinant,
            (a * e - b * d) / determinant,
        ),
    )


def _fractional_search_bounds(
    lattice: tuple[tuple[float, float, float], ...],
    cartesian_radius: float,
) -> tuple[float, float, float]:
    """Bound fractional-coordinate differences for a Cartesian sphere."""

    inverse = _matrix_inverse(lattice)
    # For d_cart = d_frac * lattice, each fractional component is bounded by
    # the corresponding inverse-matrix column's 1-norm times |d_cart|.
    return tuple(
        cartesian_radius
        * sum(abs(inverse[row][axis]) for row in range(3))
        for axis in range(3)
    )


def _bucketed_candidates(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    lattice: tuple[tuple[float, float, float], ...],
    *,
    radius: float,
) -> list[list[tuple[float, int]]]:
    """Generate exact-distance candidates using periodic fractional buckets."""

    if not actual:
        return [[] for _ in expected]
    try:
        bounds = _fractional_search_bounds(lattice, radius)
    except ValueError:
        # The caller will report a lattice comparison failure for singular
        # cells; retaining a bounded fallback keeps this helper total.
        bounds = (1.0, 1.0, 1.0)
    bounds = tuple(max(float(value), 1.0e-12) for value in bounds)
    bin_counts = tuple(
        1
        if bound >= 0.5
        else min(256, max(1, int(math.floor(1.0 / (2.0 * bound)))))
        for bound in bounds
    )
    buckets: dict[tuple[int, int, int], list[int]] = {}

    def normalized(value: Any) -> float:
        return float(value) % 1.0

    def key(fractional: list[float]) -> tuple[int, int, int]:
        return tuple(
            int(math.floor(normalized(fractional[axis]) * bin_counts[axis]))
            % bin_counts[axis]
            for axis in range(3)
        )

    for index, atom in enumerate(actual):
        buckets.setdefault(key(list(atom["fractional"])), []).append(index)

    candidates_by_expected: list[list[tuple[float, int]]] = []
    for atom in expected:
        fractional = [float(value) for value in atom["fractional"]]
        center = key(fractional)
        offsets = tuple(
            range(
                -min(bin_counts[axis], int(math.ceil(bounds[axis] * bin_counts[axis])) + 1),
                min(bin_counts[axis], int(math.ceil(bounds[axis] * bin_counts[axis])) + 1)
                + 1,
            )
            for axis in range(3)
        )
        candidate_buckets = {
            (
                (center[0] + dx) % bin_counts[0],
                (center[1] + dy) % bin_counts[1],
                (center[2] + dz) % bin_counts[2],
            )
            for dx in offsets[0]
            for dy in offsets[1]
            for dz in offsets[2]
        }
        seen: set[int] = set()
        candidates: list[tuple[float, int]] = []
        for bucket in candidate_buckets:
            for index in buckets.get(bucket, []):
                if index in seen:
                    continue
                seen.add(index)
                distance = _cartesian_delta(
                    fractional,
                    list(actual[index]["fractional"]),
                    lattice,
                )
                if distance <= radius:
                    candidates.append((distance, index))
        candidates.sort()
        candidates_by_expected.append(candidates)
    return candidates_by_expected


def _element_counts(atoms: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in atoms:
        element = str(atom.get("element") or "")
        counts[element] = counts.get(element, 0) + 1
    return dict(sorted(counts.items()))


def compare_cif_roundtrip(
    input_path: str | Path,
    output_path: str | Path,
    *,
    rms_tolerance_angstrom: float = ROUNDTRIP_RMS_TOLERANCE_ANGSTROM,
    maximum_displacement_tolerance_angstrom: float = (
        ROUNDTRIP_MAX_DISPLACEMENT_TOLERANCE_ANGSTROM
    ),
    lattice_relative_tolerance: float = ROUNDTRIP_LATTICE_RELATIVE_TOLERANCE,
) -> dict[str, Any]:
    """Compare two CIFs while allowing atom ordering, labels, and periodic wraps."""

    input_resolved = Path(input_path).expanduser().resolve()
    output_resolved = Path(output_path).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    input_sha: str | None = None
    output_sha: str | None = None
    try:
        input_sha = _sha256_file(input_resolved)
    except Exception as exc:
        errors.append(f"Failed to read input CIF: {exc}")
    try:
        output_sha = _sha256_file(output_resolved)
    except Exception as exc:
        errors.append(f"Failed to read output CIF: {exc}")
    if errors:
        return CifRoundtripComparison.model_validate(
            {
                "input_sha256": input_sha,
                "output_sha256": output_sha,
                "input_atom_count": None,
                "output_atom_count": None,
                "input_element_counts": {},
                "output_element_counts": {},
                "mapping_coverage": 0.0,
                "passed": False,
                "errors": errors,
                "warnings": warnings,
            }
        ).model_dump(mode="json")

    parsed_input = parse_crystal_cif(input_resolved)
    parsed_output = parse_crystal_cif(output_resolved)
    errors.extend(str(item) for item in parsed_input.get("errors") or [])
    errors.extend(str(item) for item in parsed_output.get("errors") or [])
    input_atoms = list(parsed_input.get("atoms") or [])
    output_atoms = list(parsed_output.get("atoms") or [])
    input_counts = _element_counts(input_atoms)
    output_counts = _element_counts(output_atoms)
    receipt: dict[str, Any] = {
        "input_sha256": input_sha,
        "output_sha256": output_sha,
        "input_atom_count": len(input_atoms) if parsed_input.get("exists") else None,
        "output_atom_count": len(output_atoms) if parsed_output.get("exists") else None,
        "input_element_counts": input_counts,
        "output_element_counts": output_counts,
        "mapping_coverage": 0.0,
        "mapping_degenerate": False,
        "rms_displacement_angstrom": None,
        "maximum_displacement_angstrom": None,
        "maximum_relative_lattice_error": None,
        "passed": False,
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        return CifRoundtripComparison.model_validate(receipt).model_dump(mode="json")
    if len(input_atoms) > ROUNDTRIP_MAX_ATOMS or len(output_atoms) > ROUNDTRIP_MAX_ATOMS:
        receipt["errors"].append(
            f"CIF atom count exceeds the bounded audit limit of {ROUNDTRIP_MAX_ATOMS}."
        )
        return CifRoundtripComparison.model_validate(receipt).model_dump(mode="json")
    if input_counts != output_counts or len(input_atoms) != len(output_atoms):
        receipt["errors"].append("CIF atom count or element composition changed.")
        return CifRoundtripComparison.model_validate(receipt).model_dump(mode="json")

    try:
        input_lattice = parsed_input["lattice"]
        output_lattice = parsed_output["lattice"]
        lattice_fields = ("a", "b", "c", "alpha", "beta", "gamma")
        relative_errors = [
            abs(float(output_lattice[field]) - float(input_lattice[field]))
            / max(abs(float(input_lattice[field])), 1.0e-9)
            for field in lattice_fields
        ]
        max_lattice_error = max(relative_errors, default=0.0)
        receipt["maximum_relative_lattice_error"] = max_lattice_error
        input_vectors = _lattice_vectors(input_lattice)
    except Exception as exc:
        receipt["errors"].append(f"CIF lattice comparison failed: {exc}")
        return CifRoundtripComparison.model_validate(receipt).model_dump(mode="json")

    input_by_element: dict[str, list[dict[str, Any]]] = {}
    output_by_element: dict[str, list[dict[str, Any]]] = {}
    for atom in input_atoms:
        input_by_element.setdefault(str(atom["element"]), []).append(atom)
    for atom in output_atoms:
        output_by_element.setdefault(str(atom["element"]), []).append(atom)

    matched_distances: list[float] = []
    mapping_degenerate = False
    for element in sorted(input_by_element):
        expected = input_by_element[element]
        actual = output_by_element[element]
        adjacency: list[list[tuple[float, int]]] = []
        close_candidates = _bucketed_candidates(
            expected,
            actual,
            input_vectors,
            radius=max(maximum_displacement_tolerance_angstrom * 4.0, 1.0),
        )
        for close in close_candidates:
            if (
                sum(
                    1
                    for distance, _ in close
                    if distance <= maximum_displacement_tolerance_angstrom
                )
                > 1
            ):
                mapping_degenerate = True
            adjacency.append(close)

        matched_output: dict[int, int] = {}

        def augment(expected_index: int, seen: set[int]) -> bool:
            for distance, output_index in adjacency[expected_index]:
                if output_index in seen:
                    continue
                seen.add(output_index)
                prior = matched_output.get(output_index)
                if prior is None or augment(prior, seen):
                    matched_output[output_index] = expected_index
                    return True
            return False

        matched_count = 0
        for expected_index in sorted(
            range(len(expected)),
            key=lambda index: len(adjacency[index]),
        ):
            if augment(expected_index, set()):
                matched_count += 1
        for output_index, expected_index in matched_output.items():
            matched_distances.append(
                adjacency[expected_index][
                    next(
                        position
                        for position, item in enumerate(adjacency[expected_index])
                        if item[1] == output_index
                    )
                ][0]
            )
        if matched_count != len(expected):
            receipt["errors"].append(
                f"Could not map all {element} atoms after Materials Studio export."
            )
    total_atoms = len(input_atoms)
    receipt["mapping_coverage"] = (
        len(matched_distances) / total_atoms if total_atoms else 0.0
    )
    receipt["mapping_degenerate"] = mapping_degenerate
    if matched_distances:
        receipt["rms_displacement_angstrom"] = math.sqrt(
            sum(value * value for value in matched_distances) / len(matched_distances)
        )
        receipt["maximum_displacement_angstrom"] = max(matched_distances)
    elif total_atoms:
        receipt["errors"].append("No atom mappings survived the round-trip comparison.")

    if (
        receipt["maximum_relative_lattice_error"] is not None
        and receipt["maximum_relative_lattice_error"]
        > lattice_relative_tolerance
    ):
        receipt["errors"].append("CIF lattice changed beyond the round-trip tolerance.")
    if (
        receipt["maximum_displacement_angstrom"] is not None
        and receipt["maximum_displacement_angstrom"]
        > maximum_displacement_tolerance_angstrom
    ):
        receipt["errors"].append(
            "At least one atom moved beyond the round-trip displacement tolerance."
        )
    if (
        receipt["rms_displacement_angstrom"] is not None
        and receipt["rms_displacement_angstrom"] > rms_tolerance_angstrom
    ):
        receipt["errors"].append("RMS atom displacement exceeded the round-trip tolerance.")
    receipt["passed"] = (
        not receipt["errors"]
        and receipt["mapping_coverage"] == 1.0
        and receipt["rms_displacement_angstrom"] is not None
        and receipt["rms_displacement_angstrom"] <= rms_tolerance_angstrom
        and receipt["maximum_displacement_angstrom"] is not None
        and receipt["maximum_displacement_angstrom"]
        <= maximum_displacement_tolerance_angstrom
        and receipt["maximum_relative_lattice_error"] is not None
        and receipt["maximum_relative_lattice_error"]
        <= lattice_relative_tolerance
    )
    if not receipt["passed"] and not receipt["errors"]:
        receipt["errors"].append("Round-trip comparison failed its bounded checks.")
    return CifRoundtripComparison.model_validate(receipt).model_dump(mode="json")


def _script_safety(
    source_path: Path,
    output_path: Path,
    visual_output_path: Path,
    script: str,
) -> dict[str, Any]:
    expected = import_export_script(
        source_path,
        output_path,
        visual_output_file=visual_output_path,
    )
    validation = validate_generated_script(script)
    errors = [str(item) for item in validation.get("errors", []) or []]
    warnings = [str(item) for item in validation.get("warnings", []) or []]
    source_literal = "'" + str(source_path).replace("\\", "\\\\").replace("'", "\\'") + "'"
    output_literal = "'" + str(output_path).replace("\\", "\\\\").replace("'", "\\'") + "'"
    visual_output_literal = (
        "'"
        + str(visual_output_path).replace("\\", "\\\\").replace("'", "\\'")
        + "'"
    )
    operation_text = (
        script.replace(source_literal, "''")
        .replace(output_literal, "''")
        .replace(visual_output_literal, "''")
    )
    forbidden_errors: list[str] = []
    for pattern in _DANGEROUS_GENERATED_MARKERS:
        if pattern.search(operation_text):
            forbidden_errors.append(
                f"Generated round-trip script contains a forbidden marker: {pattern.pattern}"
            )
    errors.extend(forbidden_errors)
    if script != expected:
        errors.append("Generated round-trip script is not the deterministic import/export template.")
    if script.count(source_literal) != 1:
        errors.append("Round-trip script must bind the source path exactly once.")
    if script.count(output_literal) != 1:
        errors.append("Round-trip script must bind the output path exactly once.")
    if script.count(visual_output_literal) != 1:
        errors.append("Round-trip script must bind the visual output path exactly once.")
    return {
        "valid": not errors,
        "deterministic": script == expected,
        "source_bound_once": script.count(source_literal) == 1,
        "output_bound_once": script.count(output_literal) == 1,
        "visual_output_bound_once": script.count(visual_output_literal) == 1,
        "forbidden_operations_absent": not forbidden_errors,
        "errors": errors,
        "warnings": warnings,
    }


def _runner_identity(runner: RoundtripRunner) -> dict[str, Any]:
    config = getattr(runner, "config", None)
    raw_path = getattr(config, "runner", None)
    path = Path(raw_path).expanduser().resolve() if raw_path else None
    exists = bool(path and path.is_file())
    digest = _sha256_file(path) if exists and path is not None else None
    components = {part.casefold() for part in path.parts} if path else set()
    real = bool(
        exists
        and path is not None
        and path.name.casefold() == "runmatscript.bat"
        and ({"materials studio 20.1", "materials studio 20.1 x64 server"} & components)
        and not os.environ.get("MATERIAL_STUDIO_COMMAND_TEMPLATE")
        and not list(getattr(config, "extra_runner_args", ()) or ())
    )
    return {
        "path": str(path) if path else None,
        "exists": exists,
        "sha256": digest,
        "real_materials_studio_20_1": real,
        "runner_kind": "materials_studio_20_1" if real else "unverified_or_fake_runner",
    }


def _roundtrip_runner_path_budget(
    *,
    source: Path,
    run_root: Path,
    output_path: Path,
    visual_output_path: Path,
) -> dict[str, Any]:
    """Describe the direct-job paths that legacy MatServer 20.1 will expand."""

    paths = {
        "source_cif": source.expanduser().resolve(),
        "output_cif": output_path.expanduser().resolve(),
        "visual_bonded_xsd": visual_output_path.expanduser().resolve(),
        "runner_script": (run_root / "roundtrip.pl").expanduser().resolve(),
        "runner_output": (run_root / "roundtrip.pl.out").expanduser().resolve(),
        "runner_log": (run_root / "roundtripMatStudioLog.htm").expanduser().resolve(),
    }
    path_lengths = {name: len(str(path)) for name, path in paths.items()}
    longest_name = max(path_lengths, key=path_lengths.__getitem__)
    longest_length = path_lengths[longest_name]
    return {
        "schema_version": "materials_studio_20_1_path_budget_v1",
        "limit_characters": ROUNDTRIP_MS20_1_SAFE_PATH_LIMIT,
        "within_budget": longest_length <= ROUNDTRIP_MS20_1_SAFE_PATH_LIMIT,
        "maximum_path_characters": longest_length,
        "longest_path_kind": longest_name,
        "longest_path": str(paths[longest_name]),
        "direct_job_dir": True,
        "paths": {
            name: {"path": str(paths[name]), "characters": path_lengths[name]}
            for name in sorted(paths)
        },
    }


def capture_gui_inventory(backend: Any) -> dict[str, Any]:
    """Capture identity-only GUI evidence without activation or input."""

    if backend is None or not bool(getattr(backend, "supported", False)):
        return {
            "available": False,
            "usable_single_window": False,
            "process_count": 0,
            "window_count": 0,
            "process_identity_sha256": None,
            "window_identity_sha256": None,
            "visible_window_count": 0,
        }
    processes = list(backend.list_processes())
    list_windows = getattr(backend, "list_windows", None)
    windows = list(list_windows() if callable(list_windows) else [])
    process_identity = [
        {"name": str(getattr(item, "name", "")), "pid": int(getattr(item, "pid", 0))}
        for item in processes
    ]
    window_identity = [
        {
            "pid": int(getattr(item, "pid", 0)),
            "handle": int(getattr(item, "handle", 0)),
            "title": str(getattr(item, "title", "")),
        }
        for item in windows
    ]
    process_identity.sort(key=lambda item: (item["name"].casefold(), item["pid"]))
    window_identity.sort(key=lambda item: (item["pid"], item["handle"], item["title"]))
    process_pids = {item["pid"] for item in process_identity}
    visible_windows = [
        item
        for item in windows
        if bool(getattr(item, "is_visible", True))
        and int(getattr(item, "pid", 0)) in process_pids
    ]
    usable = len(processes) == 1 and len(visible_windows) == 1
    return {
        "available": True,
        "usable_single_window": usable,
        "process_count": len(processes),
        "window_count": len(windows),
        "visible_window_count": len(visible_windows),
        "process_identity_sha256": canonical_json_sha256(process_identity),
        "window_identity_sha256": canonical_json_sha256(window_identity),
        "window_minimized": (
            bool(getattr(visible_windows[0], "is_minimized", False))
            if len(visible_windows) == 1
            else None
        ),
    }


def _gui_invariant(before: dict[str, Any], after: dict[str, Any], *, required: bool) -> dict[str, Any]:
    unchanged = (
        before.get("process_identity_sha256") == after.get("process_identity_sha256")
        and before.get("window_identity_sha256") == after.get("window_identity_sha256")
        and before.get("process_count") == after.get("process_count")
        and before.get("window_count") == after.get("window_count")
    )
    single_window_ok = (
        not required
        or bool(before.get("usable_single_window"))
        and bool(after.get("usable_single_window"))
    )
    return {
        "required": required,
        "before": before,
        "after": after,
        "identity_unchanged": unchanged,
        "single_window_ok": single_window_ok,
        "process_count_before_after": [before.get("process_count"), after.get("process_count")],
        "window_count_before_after": [before.get("window_count"), after.get("window_count")],
        "process_launched": before.get("process_count") != after.get("process_count"),
        "window_changed": before.get("window_identity_sha256") != after.get("window_identity_sha256"),
        "passed": unchanged and single_window_ok,
    }


def plan_roundtrip_audit(
    spec: ModelSpec,
    *,
    source_path: str | Path,
    output_dir: str | Path,
    run_id: str = "preview",
    execution_mode: Literal["preview", "execute"] = "preview",
    required: bool = True,
    gui_probe_planned: bool = False,
) -> dict[str, Any]:
    """Build a side-effect-free round-trip plan for one immutable revision."""

    if not isinstance(spec, ModelSpec):
        raise TypeError("spec must be a ModelSpec")
    run_id = _validate_run_id(run_id)
    spec_sha = _canonical_spec_sha256(spec)
    source = Path(source_path).expanduser().resolve()
    revision_output = Path(output_dir).expanduser().resolve()
    run_root = _ensure_inside(revision_output, revision_output / "ms_roundtrip" / run_id, label="round-trip run root")
    output_path = _ensure_inside(run_root, run_root / "roundtrip_output.cif", label="round-trip output")
    visual_output_path = _ensure_inside(
        run_root,
        run_root / f"{source.stem}_visual_bonded.xsd",
        label="visual bonded output",
    )
    base: dict[str, Any] = {
        "project_id": spec.project_id,
        "revision": spec.revision,
        "execution_mode": execution_mode,
        "required": required,
        "spec_sha256": spec_sha,
        "run_id": run_id,
        "run_root": str(run_root),
        "output_path": str(output_path),
        "visual_output_path": (
            str(visual_output_path) if isinstance(spec.model, CrystalSpec) else None
        ),
        "visual_bonding_planned": isinstance(spec.model, CrystalSpec),
        "visual_bonding_required": False,
        "gui_probe_planned": gui_probe_planned,
        "runner_call_planned": execution_mode == "execute",
        "side_effects": {
            "files_written": False,
            "runner_called": False,
            "gui_input_performed": False,
        },
        "errors": [],
        "warnings": [],
    }
    if not isinstance(spec.model, CrystalSpec):
        base.update(
            {
                "applicable": False,
                "status": "not_applicable",
                "gui_probe_planned": False,
                "runner_call_planned": False,
                "warnings": [
                    "Materials Studio CIF round-trip auditing currently supports CrystalSpec revisions only."
                ],
            }
        )
        return RoundtripAuditPlan.model_validate(base).model_dump(mode="json")
    try:
        _ensure_inside(revision_output, source, label="round-trip source")
    except ValueError as exc:
        base.update({"applicable": True, "status": "blocked", "errors": [str(exc)]})
        return RoundtripAuditPlan.model_validate(base).model_dump(mode="json")
    if source.suffix.casefold() != ".cif":
        base.update(
            {
                "applicable": True,
                "status": "blocked",
                "errors": ["Crystal round-trip auditing requires a CIF source artifact."],
            }
        )
        return RoundtripAuditPlan.model_validate(base).model_dump(mode="json")
    if not source.is_file():
        base.update(
            {
                "applicable": True,
                "status": "deferred_until_materialized",
                "warnings": [
                    "The revision structure is not materialized yet; execute the structure backend before auditing."
                ],
            }
        )
        return RoundtripAuditPlan.model_validate(base).model_dump(mode="json")
    try:
        source_sha = _sha256_file(source)
        script = import_export_script(
            source,
            output_path,
            visual_output_file=visual_output_path,
        )
        script_validation = _script_safety(
            source,
            output_path,
            visual_output_path,
            script,
        )
    except Exception as exc:
        base.update({"applicable": True, "status": "blocked", "errors": [str(exc)]})
        return RoundtripAuditPlan.model_validate(base).model_dump(mode="json")
    base.update(
        {
            "applicable": True,
            "status": "preview_ready" if script_validation["valid"] else "blocked",
            "source_path": str(source),
            "source_sha256": source_sha,
            "script": script,
            "script_sha256": _sha256_bytes(script.encode("utf-8")),
            "script_validation": script_validation,
            "errors": list(script_validation.get("errors", []) or []),
            "warnings": list(script_validation.get("warnings", []) or []),
        }
    )
    if run_root.exists():
        base["status"] = "blocked"
        base["errors"].append("The round-trip run directory already exists; refusing to overwrite it.")
    return RoundtripAuditPlan.model_validate(base).model_dump(mode="json")


def not_applicable_roundtrip_receipt(
    spec: ModelSpec,
    *,
    execution_mode: Literal["preview", "execute"] = "execute",
    required: bool = True,
) -> dict[str, Any]:
    """Return an explicit receipt when CIF round-trip auditing cannot apply."""

    return RoundtripAuditReceipt(
        project_id=spec.project_id,
        revision=spec.revision,
        execution_mode=execution_mode,
        required=required,
        applicable=False,
        status="not_applicable",
        ok=None,
        spec_sha256=_canonical_spec_sha256(spec),
        warnings=[
            "Materials Studio CIF round-trip auditing currently supports CrystalSpec revisions only."
        ],
    ).model_dump(mode="json")


def _failure_receipt_from_plan(plan: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    visual_requested = bool(
        plan.get("visual_bonding_planned") and plan.get("visual_output_path")
    )
    receipt = {
        "project_id": plan["project_id"],
        "revision": plan["revision"],
        "execution_mode": plan["execution_mode"],
        "required": plan["required"],
        "applicable": plan["applicable"],
        "status": status or plan["status"],
        "ok": None if plan["status"] == "deferred_until_materialized" else False,
        "spec_sha256": plan["spec_sha256"],
        "source_path": plan.get("source_path"),
        "source_sha256_planned": plan.get("source_sha256"),
        "source_sha256_before": plan.get("source_sha256"),
        "output_path": plan.get("output_path"),
        "run_root": plan.get("run_root"),
        "script_sha256": plan.get("script_sha256"),
        "visual_bonded_artifact": {
            "requested": visual_requested,
            "status": "unavailable" if visual_requested else "not_requested",
            "ok": None,
            "source_path": plan.get("source_path"),
            "source_sha256": plan.get("source_sha256"),
            "path": plan.get("visual_output_path"),
            "warnings": (
                [
                    "Visual bonded XSD generation did not run because the core "
                    "round-trip audit was not executable."
                ]
                if visual_requested
                else []
            ),
        },
        "errors": list(plan.get("errors") or []),
        "warnings": list(plan.get("warnings") or []),
    }
    if plan["status"] == "not_applicable":
        receipt["status"] = "not_applicable"
        receipt["ok"] = None
    return RoundtripAuditReceipt.model_validate(receipt).model_dump(mode="json")


def _tagged_true(value: Any) -> bool:
    return value is True or (type(value) is int and value == 1)


def _tagged_nonnegative_int(value: Any) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _visual_bonded_artifact_receipt(
    *,
    source: Path,
    source_sha256: str,
    visual_output_path: Path,
    run_root: Path,
    tagged: Any,
    comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    nested = tagged.get("visual_bonded") if isinstance(tagged, dict) else None
    requested = False
    calculate_ok = False
    export_ok = False
    atom_count: int | None = None
    calculated_bond_count: int | None = None
    unit_cell_bond_count: int | None = None
    criteria: dict[str, float] = {}
    tagged_summary_verified = False

    if isinstance(nested, dict):
        requested = _tagged_true(nested.get("requested"))
        calculate_ok = _tagged_true(nested.get("calculate_bonds_ok"))
        export_ok = _tagged_true(nested.get("visual_export_ok"))
        atom_count = _tagged_nonnegative_int(nested.get("atom_count"))
        calculated_bond_count = _tagged_nonnegative_int(
            nested.get("calculated_bond_count")
        )
        unit_cell_bond_count = _tagged_nonnegative_int(
            nested.get("unit_cell_bond_count")
        )
        raw_criteria = nested.get("criteria")
        if isinstance(raw_criteria, dict):
            try:
                criteria = {
                    "min_bond_length": float(raw_criteria["min_bond_length"]),
                    "max_bond_length": float(raw_criteria["max_bond_length"]),
                }
            except (KeyError, TypeError, ValueError):
                criteria = {}
        tagged_summary_verified = (
            requested
            and str(nested.get("output") or "") == str(visual_output_path)
            and criteria == {
                "min_bond_length": 0.6,
                "max_bond_length": 1.15,
            }
        )
        calculate_error = str(nested.get("calculate_error") or "").strip()
        export_error = str(nested.get("export_error") or "").strip()
        if calculate_error:
            errors.append(
                f"Materials Studio CalculateBonds failed: {calculate_error}"
            )
        if export_error:
            errors.append(
                f"Materials Studio visual XSD export failed: {export_error}"
            )
    else:
        errors.append("The tagged JSON summary has no visual_bonded receipt.")

    if not tagged_summary_verified:
        errors.append(
            "The visual bonded tagged JSON summary is not bound to the "
            "deterministic path and bond criteria."
        )
    if not calculate_ok:
        errors.append("Materials Studio did not confirm CalculateBonds success.")
    if not export_ok:
        errors.append("Materials Studio did not confirm visual XSD export success.")
    if atom_count is None:
        errors.append("The visual bonded summary has no valid atom count.")
    if calculated_bond_count is None:
        errors.append("The visual bonded summary has no valid calculated bond count.")
    if unit_cell_bond_count is None:
        errors.append("The visual bonded summary has no valid unit-cell bond count.")

    confined: bool | None = None
    sha256: str | None = None
    format_verified: bool | None = None
    root_tag: str | None = None
    xsd_bond_element_count: int | None = None
    if not visual_output_path.is_file():
        errors.append("Materials Studio did not create the visual bonded XSD.")
    elif visual_output_path.is_symlink():
        confined = False
        errors.append("The visual bonded XSD must not be a symbolic link.")
    else:
        try:
            resolved_visual = visual_output_path.resolve(strict=True)
            resolved_visual.relative_to(run_root.resolve(strict=True))
            confined = True
        except (OSError, ValueError):
            confined = False
            errors.append("The visual bonded XSD escaped the round-trip run root.")
        if confined:
            try:
                sha256 = _sha256_visual_file(visual_output_path)
                inspected = _inspect_visual_xsd(visual_output_path)
                format_verified = bool(inspected["format_verified"])
                root_tag = inspected["root_tag"]
                xsd_bond_element_count = int(
                    inspected["xsd_bond_element_count"]
                )
                if not format_verified:
                    errors.append(
                        "The visual bonded artifact is not a Materials Studio XSD document."
                    )
            except Exception as exc:
                errors.append(f"Visual bonded XSD verification failed: {exc}")

    expected_atom_count = (
        comparison.get("input_atom_count")
        if isinstance(comparison, dict)
        else None
    )
    atom_count_matches_source = (
        atom_count == expected_atom_count
        if atom_count is not None and isinstance(expected_atom_count, int)
        else None
    )
    if atom_count_matches_source is False:
        errors.append(
            "The visual bonded XSD atom count does not match the canonical source CIF."
        )
    if (
        unit_cell_bond_count is not None
        and unit_cell_bond_count > 0
        and xsd_bond_element_count == 0
    ):
        errors.append(
            "The visual bonded summary reports bonds but the XSD contains no Bond element."
        )
    if unit_cell_bond_count == 0:
        warnings.append(
            "CalculateBonds produced zero unit-cell bonds; GUI hot-load will use "
            "the canonical CIF instead."
        )

    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    ok = not errors
    gui_hotload_candidate = bool(
        ok
        and unit_cell_bond_count is not None
        and unit_cell_bond_count > 0
        and xsd_bond_element_count is not None
        and xsd_bond_element_count > 0
    )
    return VisualBondedArtifactReceipt(
        requested=True,
        status="ready" if ok else "failed",
        ok=ok,
        criteria=criteria,
        source_path=str(source),
        source_sha256=source_sha256,
        path=str(visual_output_path),
        sha256=sha256,
        confined=confined,
        format_verified=format_verified,
        root_tag=root_tag,
        tagged_summary_verified=tagged_summary_verified,
        atom_count=atom_count,
        calculated_bond_count=calculated_bond_count,
        bond_count=unit_cell_bond_count,
        xsd_bond_element_count=xsd_bond_element_count,
        atom_count_matches_source=atom_count_matches_source,
        bond_calculation_performed=calculate_ok,
        visual_export_performed=export_ok,
        gui_hotload_candidate=gui_hotload_candidate,
        errors=errors,
        warnings=warnings,
    ).model_dump(mode="json")


def _same_resolved_path(left: Any, right: Any) -> bool:
    try:
        return (
            Path(str(left)).expanduser().resolve()
            == Path(str(right)).expanduser().resolve()
        )
    except Exception:
        return str(left) == str(right)


def verify_visual_bonded_hotload_selection(
    audit: Any,
    *,
    canonical_structure_path: str | Path,
    project_id: str,
    revision: int,
    roundtrip_audit_required: bool = False,
) -> dict[str, Any]:
    """Verify a visual XSD and decide whether canonical fallback is safe."""

    canonical_raw = Path(canonical_structure_path).expanduser()
    canonical = canonical_raw.resolve()
    canonical_blocking_reasons: list[str] = []
    visual_fallback_reasons: list[str] = []
    canonical_sha256: str | None = None
    if not canonical.is_file():
        canonical_blocking_reasons.append("canonical_structure_missing")
    elif canonical_raw.is_symlink():
        canonical_blocking_reasons.append("canonical_structure_is_symlink")
    else:
        try:
            canonical_sha256 = _sha256_visual_file(canonical)
        except Exception:
            canonical_blocking_reasons.append(
                "canonical_structure_hash_failed"
            )

    visual: dict[str, Any] = {}
    if not isinstance(audit, dict):
        visual_fallback_reasons.append("roundtrip_audit_missing")
        if roundtrip_audit_required:
            canonical_blocking_reasons.append(
                "roundtrip_audit_required_but_missing"
            )
    else:
        raw_visual = audit.get("visual_bonded_artifact")
        visual = raw_visual if isinstance(raw_visual, dict) else {}
        canonical_expected_fields = {
            "project_id": project_id,
            "revision": revision,
            "status": "passed",
            "ok": True,
            "source_unchanged": True,
        }
        for field, expected in canonical_expected_fields.items():
            if audit.get(field) != expected:
                canonical_blocking_reasons.append(
                    f"roundtrip_{field}_mismatch"
                )
        if not _same_resolved_path(audit.get("source_path"), canonical):
            canonical_blocking_reasons.append(
                "roundtrip_source_path_mismatch"
            )
        for field in (
            "source_sha256_planned",
            "source_sha256_before",
            "source_sha256_after",
        ):
            if not canonical_sha256 or audit.get(field) != canonical_sha256:
                canonical_blocking_reasons.append(
                    f"roundtrip_{field}_mismatch"
                )
        comparison = (
            audit.get("comparison")
            if isinstance(audit.get("comparison"), dict)
            else {}
        )
        if comparison.get("passed") is not True:
            canonical_blocking_reasons.append(
                "roundtrip_comparison_not_passed"
            )
        if (
            canonical_sha256
            and comparison.get("input_sha256") != canonical_sha256
        ):
            canonical_blocking_reasons.append(
                "roundtrip_comparison_source_hash_mismatch"
            )
        if audit.get("schema_version") != ROUNDTRIP_AUDIT_SCHEMA_VERSION:
            visual_fallback_reasons.append(
                "roundtrip_schema_version_mismatch"
            )
        if audit.get("profile") != ROUNDTRIP_AUDIT_PROFILE:
            visual_fallback_reasons.append("roundtrip_profile_mismatch")

    if visual:
        if (
            visual.get("schema_version")
            != VISUAL_BONDED_ARTIFACT_SCHEMA_VERSION
        ):
            visual_fallback_reasons.append(
                "visual_schema_version_mismatch"
            )
        try:
            VisualBondedArtifactReceipt.model_validate(visual)
        except Exception:
            visual_fallback_reasons.append(
                "visual_receipt_schema_invalid"
            )
        if visual.get("criteria") != {
            "min_bond_length": 0.6,
            "max_bond_length": 1.15,
        }:
            visual_fallback_reasons.append(
                "visual_bond_criteria_mismatch"
            )
        required_visual_fields = {
            "requested": True,
            "required": False,
            "status": "ready",
            "ok": True,
            "confined": True,
            "format_verified": True,
            "root_tag": "XSD",
            "tagged_summary_verified": True,
            "atom_count_matches_source": True,
            "bond_calculation_performed": True,
            "visual_export_performed": True,
            "structure_truth_authority": False,
            "calculation_input_allowed": False,
            "gui_hotload_candidate": True,
            "failure_does_not_fail_roundtrip": True,
        }
        for field, expected in required_visual_fields.items():
            if visual.get(field) != expected:
                visual_fallback_reasons.append(
                    f"visual_{field}_mismatch"
                )
        if canonical_sha256 and visual.get("source_sha256") != canonical_sha256:
            visual_fallback_reasons.append("visual_source_hash_mismatch")
        if not _same_resolved_path(visual.get("source_path"), canonical):
            visual_fallback_reasons.append("visual_source_path_mismatch")
        for field in ("atom_count", "calculated_bond_count", "bond_count"):
            value = visual.get(field)
            if type(value) is not int or value < 0:
                visual_fallback_reasons.append(
                    f"visual_{field}_invalid"
                )
        for field in ("bond_count", "xsd_bond_element_count"):
            value = visual.get(field)
            if type(value) is not int or value <= 0:
                visual_fallback_reasons.append(
                    f"visual_{field}_not_positive"
                )
    elif isinstance(audit, dict):
        visual_fallback_reasons.append("visual_bonded_receipt_missing")

    visual_path: Path | None = None
    visual_sha256: str | None = None
    raw_visual_path = visual.get("path")
    raw_run_root = audit.get("run_root") if isinstance(audit, dict) else None
    if not visual:
        pass
    elif not raw_visual_path:
        visual_fallback_reasons.append("visual_path_missing")
    elif not raw_run_root:
        visual_fallback_reasons.append("roundtrip_run_root_missing")
    else:
        try:
            visual_raw_path = Path(str(raw_visual_path)).expanduser()
            visual_path = visual_raw_path.resolve(strict=True)
            run_root = Path(str(raw_run_root)).expanduser().resolve(
                strict=True
            )
            visual_path.relative_to(run_root)
            run_root.relative_to(
                (canonical.parent / "ms_roundtrip").resolve(strict=True)
            )
            if visual_raw_path.is_symlink():
                visual_fallback_reasons.append("visual_path_is_symlink")
            if visual_path.suffix.casefold() != ".xsd":
                visual_fallback_reasons.append("visual_path_not_xsd")
            visual_sha256 = _sha256_visual_file(visual_path)
            if visual_sha256 != visual.get("sha256"):
                visual_fallback_reasons.append(
                    "visual_sha256_mismatch"
                )
        except (OSError, ValueError):
            visual_fallback_reasons.append(
                "visual_path_not_confined_or_readable"
            )

    canonical_blocking_reasons = list(
        dict.fromkeys(canonical_blocking_reasons)
    )
    visual_fallback_reasons = list(
        dict.fromkeys(visual_fallback_reasons)
    )
    canonical_verified = not canonical_blocking_reasons
    visual_verified = bool(
        canonical_verified
        and not visual_fallback_reasons
        and visual_path is not None
    )
    selected_path = visual_path if visual_verified else canonical
    hotload_allowed = bool(visual_verified or canonical_verified)
    return {
        "schema_version": GUI_HOTLOAD_STRUCTURE_SELECTION_SCHEMA_VERSION,
        "project_id": project_id,
        "revision": revision,
        "status": (
            "verified_visual_selected"
            if visual_verified
            else "canonical_fallback_ready"
            if canonical_verified
            else "blocked"
        ),
        "hotload_allowed": hotload_allowed,
        "canonical_verified": canonical_verified,
        "canonical_blocking_reasons": canonical_blocking_reasons,
        "canonical_structure_path": str(canonical),
        "canonical_structure_sha256": canonical_sha256,
        "roundtrip_audit_required": roundtrip_audit_required,
        "visual_bonded_requested": bool(
            isinstance(audit, dict) or roundtrip_audit_required
        ),
        "visual_bonded_verified": visual_verified,
        "selected_source": (
            "verified_visual_bonded_xsd"
            if visual_verified
            else "canonical_cif_fallback"
            if canonical_verified
            else "blocked"
        ),
        "selected_structure_path": str(selected_path),
        "selected_structure_sha256": (
            visual_sha256 if visual_verified else canonical_sha256
        ),
        "visual_structure_path": str(visual_path) if visual_path else None,
        "visual_structure_sha256": visual_sha256,
        "visual_receipt_sha256": (
            canonical_json_sha256(visual) if visual else None
        ),
        "visual_fallback_reasons": visual_fallback_reasons,
        "fallback_reasons": [
            *canonical_blocking_reasons,
            *visual_fallback_reasons,
        ],
        "structure_truth_path": str(canonical),
        "calculation_input_path": str(canonical),
        "visual_derivative_is_structure_truth": False,
        "visual_derivative_is_calculation_input": False,
    }


def execute_roundtrip_audit(
    spec: ModelSpec,
    *,
    source_path: str | Path,
    output_dir: str | Path,
    runner: RoundtripRunner | MaterialStudioRunner,
    run_id: str | None = None,
    timeout_seconds: int | None = None,
    gui_backend: Any = None,
    require_single_window: bool = False,
) -> dict[str, Any]:
    """Execute one confined import/export audit and persist its receipt."""

    effective_run_id = _validate_run_id(run_id or uuid.uuid4().hex)
    plan = plan_roundtrip_audit(
        spec,
        source_path=source_path,
        output_dir=output_dir,
        run_id=effective_run_id,
        execution_mode="execute",
        required=True,
        gui_probe_planned=gui_backend is not None,
    )
    if plan["status"] != "preview_ready":
        return _failure_receipt_from_plan(plan)
    run_root = Path(plan["run_root"])
    output_path = Path(plan["output_path"])
    visual_output_path = Path(plan["visual_output_path"])
    source = Path(plan["source_path"])
    try:
        source_before = _sha256_file(source)
    except Exception as exc:
        receipt = _failure_receipt_from_plan(plan, status="blocked")
        receipt["errors"].append(
            f"Failed to re-read the planned source CIF before execution: {exc}"
        )
        return RoundtripAuditReceipt.model_validate(receipt).model_dump(mode="json")
    if source_before != plan.get("source_sha256"):
        receipt = _failure_receipt_from_plan(plan, status="blocked")
        receipt["source_sha256_before"] = source_before
        receipt["source_unchanged"] = False
        receipt["errors"].append(
            "The source CIF changed after planning and before round-trip execution."
        )
        return RoundtripAuditReceipt.model_validate(receipt).model_dump(mode="json")

    runner_identity = _runner_identity(runner)
    runner_path_budget = _roundtrip_runner_path_budget(
        source=source,
        run_root=run_root,
        output_path=output_path,
        visual_output_path=visual_output_path,
    )
    if (
        runner_identity.get("real_materials_studio_20_1")
        and not runner_path_budget["within_budget"]
    ):
        receipt = _failure_receipt_from_plan(plan, status="blocked")
        receipt.update(
            {
                "real_materials_studio_status": "FAIL",
                "source_unchanged": True,
                "runner_success": False,
                "runner_success_markers_required": True,
                "runner_termination_markers": {
                    "completion_status_ok": False,
                    "matserver_exit_status_ok": False,
                },
                "runner_path_budget": runner_path_budget,
                "runner_identity": {
                    "before": runner_identity,
                    "after": runner_identity,
                    "unchanged": True,
                },
            }
        )
        receipt["errors"].append(
            "Materials Studio 20.1 round-trip paths exceed the safe "
            f"{ROUNDTRIP_MS20_1_SAFE_PATH_LIMIT}-character budget; "
            "the runner was not started."
        )
        return RoundtripAuditReceipt.model_validate(receipt).model_dump(mode="json")
    before_gui = capture_gui_inventory(gui_backend) if gui_backend is not None else capture_gui_inventory(None)
    if require_single_window and not before_gui.get("usable_single_window"):
        receipt = _failure_receipt_from_plan(
            plan,
            status="blocked",
        )
        receipt["errors"].append(
            "Round-trip execution requires exactly one existing visible Materials Studio window."
        )
        return RoundtripAuditReceipt.model_validate(receipt).model_dump(mode="json")

    run_root.mkdir(parents=True, exist_ok=False)
    runner_sha_before = runner_identity.get("sha256")
    errors: list[str] = []
    warnings: list[str] = []
    result: Any = None
    try:
        result = runner.run_script(
            str(plan["script"]),
            working_dir=run_root,
            timeout_seconds=timeout_seconds,
            job_prefix=f"{spec.project_id}_r{spec.revision:03d}_roundtrip",
            keep_script_name="roundtrip.pl",
            direct_job_dir=True,
        )
    except Exception as exc:
        errors.append(f"Materials Studio round-trip runner failed: {exc}")

    after_gui = capture_gui_inventory(gui_backend) if gui_backend is not None else capture_gui_inventory(None)
    gui_invariant = _gui_invariant(before_gui, after_gui, required=require_single_window)
    if not gui_invariant["passed"]:
        errors.append("Materials Studio process/window identity changed during round-trip execution.")
    source_after: str | None = None
    try:
        source_after = _sha256_file(source)
    except Exception as exc:
        errors.append(f"Failed to re-read the source CIF after execution: {exc}")
    source_unchanged = source_after == source_before
    if not source_unchanged:
        errors.append("The bound source CIF changed during round-trip execution.")

    runner_success = bool(getattr(result, "success", False)) if result is not None else False
    runner_timed_out = bool(getattr(result, "timed_out", False)) if result is not None else None
    runner_return_code = (
        int(getattr(result, "return_code"))
        if result is not None and getattr(result, "return_code", None) is not None
        else None
    )
    runner_termination_markers = (
        dict(getattr(result, "completion_markers", {}) or {})
        if result is not None
        else {}
    )
    runner_success_markers_required = (
        bool(getattr(result, "success_markers_required", False))
        if result is not None
        else None
    )
    if not runner_success:
        errors.append("Materials Studio runner did not report a successful import/export.")
    if runner_timed_out:
        errors.append("Materials Studio round-trip runner timed out.")
    if runner_identity.get("real_materials_studio_20_1") and not all(
        runner_termination_markers.get(name) is True
        for name in ("completion_status_ok", "matserver_exit_status_ok")
    ):
        errors.append(
            "Materials Studio 20.1 did not emit both required successful "
            "MatServer termination markers."
        )

    created_files: list[str] = []
    output_confined = False
    runner_script_confined = False
    script_identity_verified = False
    runner_script_bytes_sha256: str | None = None
    if result is not None:
        job_dir_raw = getattr(result, "job_dir", None)
        job_dir = Path(job_dir_raw).expanduser().resolve() if job_dir_raw else None
        if job_dir is not None:
            try:
                job_dir.relative_to(run_root)
            except ValueError:
                errors.append("Runner job directory escaped the round-trip run root.")
        for raw_path in list(getattr(result, "created_files", []) or []):
            candidate = Path(raw_path).expanduser().resolve()
            try:
                candidate.relative_to(run_root)
            except ValueError:
                errors.append(f"Runner-created artifact escaped the round-trip run root: {candidate}")
                continue
            created_files.append(str(candidate))
        script_path_raw = getattr(result, "script_path", None)
        if script_path_raw is not None:
            script_path = Path(script_path_raw).expanduser().resolve()
            try:
                script_path.relative_to(run_root)
                runner_script_confined = True
            except ValueError:
                errors.append("Runner-saved script escaped the round-trip run root.")
            if runner_script_confined:
                try:
                    script_text = script_path.read_text(encoding="utf-8")
                    script_identity_verified = script_text == plan["script"]
                    runner_script_bytes_sha256 = _sha256_file(script_path)
                except (OSError, UnicodeError):
                    script_identity_verified = False
        if not script_identity_verified:
            errors.append("The runner-saved round-trip script does not match the deterministic plan.")

    if output_path.is_file():
        try:
            resolved_output = output_path.resolve(strict=True)
            resolved_output.relative_to(run_root.resolve(strict=True))
            output_confined = True
        except (OSError, ValueError):
            errors.append("Round-trip output escaped the run root.")
    else:
        errors.append("Materials Studio did not create the bound round-trip output CIF.")
    output_sha = None
    comparison: dict[str, Any] | None = None
    if output_path.is_file() and output_confined:
        try:
            output_sha = _sha256_file(output_path)
            comparison = compare_cif_roundtrip(source, output_path)
            if not comparison.get("passed"):
                errors.extend(str(item) for item in comparison.get("errors", []) or [])
        except Exception as exc:
            errors.append(f"Round-trip CIF comparison failed: {exc}")

    tagged = getattr(result, "parsed_json", None) if result is not None else None
    tagged_verified = False
    if isinstance(tagged, dict):
        tagged_verified = (
            str(tagged.get("source") or "") == str(source)
            and str(tagged.get("output") or "") == str(output_path)
            and bool(str(tagged.get("document_name") or "").strip())
        )
    if not tagged_verified:
        errors.append("The runner did not return a bound tagged JSON import/export summary.")

    visual_bonded_artifact = _visual_bonded_artifact_receipt(
        source=source,
        source_sha256=source_before,
        visual_output_path=visual_output_path,
        run_root=run_root,
        tagged=tagged,
        comparison=comparison,
    )
    warnings.extend(
        f"Visual bonded XSD: {message}"
        for message in visual_bonded_artifact.get("errors", [])
    )
    warnings.extend(
        str(message)
        for message in visual_bonded_artifact.get("warnings", [])
    )

    try:
        runner_identity_after = _runner_identity(runner)
    except Exception as exc:
        runner_identity_after = {"inspection_error": str(exc)}
        errors.append(f"Failed to re-inspect the Materials Studio runner: {exc}")
    if runner_sha_before and runner_identity_after.get("sha256") != runner_sha_before:
        errors.append("The Materials Studio runner executable changed during execution.")
    elif runner_identity_after != runner_identity:
        errors.append("The Materials Studio runner identity changed during execution.")
    real_status: Literal["PASS", "FAIL", "NOT_RUN"]
    if runner_identity.get("real_materials_studio_20_1"):
        real_status = "PASS" if not errors else "FAIL"
    else:
        real_status = "NOT_RUN"
        warnings.append(
            "Round-trip structural evidence was produced by an unverified/fake runner; it does not establish real Materials Studio 20.1 execution."
        )
    receipt: dict[str, Any] = {
        "project_id": spec.project_id,
        "revision": spec.revision,
        "execution_mode": "execute",
        "required": True,
        "applicable": True,
        "status": "passed" if not errors else "failed",
        "ok": not errors,
        "matstudio_process_launched": bool(gui_invariant.get("process_launched")),
        "real_materials_studio_status": real_status,
        "spec_sha256": plan["spec_sha256"],
        "source_path": str(source),
        "source_sha256_planned": plan.get("source_sha256"),
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "output_path": str(output_path),
        "output_sha256": output_sha,
        "run_root": str(run_root),
        "script_sha256": plan.get("script_sha256"),
        "source_unchanged": source_unchanged,
        "output_confined": output_confined,
        "runner_script_confined": runner_script_confined,
        "script_identity_verified": script_identity_verified,
        "tagged_summary_verified": tagged_verified,
        "runner_success": runner_success,
        "runner_timed_out": runner_timed_out,
        "runner_duration_seconds": float(getattr(result, "duration_seconds", 0.0) or 0.0)
        if result is not None
        else None,
        "runner_return_code": runner_return_code,
        "runner_termination_markers": runner_termination_markers,
        "runner_success_markers_required": runner_success_markers_required,
        "runner_script_bytes_sha256": runner_script_bytes_sha256,
        "runner_path_budget": runner_path_budget,
        "runner_created_files": created_files,
        "runner_identity": {
            "before": runner_identity,
            "after": runner_identity_after,
            "unchanged": runner_identity == runner_identity_after,
        },
        "gui_invariant": gui_invariant,
        "comparison": comparison,
        "visual_bonded_artifact": visual_bonded_artifact,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }
    receipt_path = run_root / "roundtrip_audit.json"
    receipt["receipt_path"] = str(receipt_path)
    validated_receipt = RoundtripAuditReceipt.model_validate(receipt).model_dump(
        mode="json"
    )
    atomic_write_text(
        receipt_path,
        json.dumps(validated_receipt, indent=2, ensure_ascii=False),
    )
    return validated_receipt


__all__ = [
    "CifRoundtripComparison",
    "GUI_HOTLOAD_STRUCTURE_SELECTION_SCHEMA_VERSION",
    "ROUNDTRIP_AUDIT_PROFILE",
    "ROUNDTRIP_AUDIT_SCHEMA_VERSION",
    "ROUNDTRIP_MS20_1_SAFE_PATH_LIMIT",
    "VISUAL_BONDED_ARTIFACT_SCHEMA_VERSION",
    "RoundtripAuditPlan",
    "RoundtripAuditReceipt",
    "VisualBondedArtifactReceipt",
    "capture_gui_inventory",
    "compare_cif_roundtrip",
    "execute_roundtrip_audit",
    "not_applicable_roundtrip_receipt",
    "plan_roundtrip_audit",
    "verify_visual_bonded_hotload_selection",
]
