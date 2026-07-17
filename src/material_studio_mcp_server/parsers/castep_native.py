"""Strict parsers and deterministic exports for native CASTEP text artifacts."""

from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, TextIO

from pydantic import Field, FiniteFloat, model_validator

from ..specs.common import StrictModel


HARTREE_TO_EV = 27.211386245988
CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA = "material_studio_castep_native_output_audit_v1"
MAX_CASTEP_BANDS_BYTES = 512 * 1024 * 1024
MAX_CASTEP_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_CASTEP_BAND_VALUES = 10_000_000
DEFAULT_DOS_GRID_POINTS = 2001

_FLOAT_TOKEN = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[EeDd][+-]?\d+)?"
_FLOAT_RE = re.compile(rf"^{_FLOAT_TOKEN}$")
_SCF_ROW_RE = re.compile(
    rf"^\s*(?P<iteration>\d+)\s+(?P<energy>{_FLOAT_TOKEN})(?P<rest>.*?)<--\s*SCF\s*$",
    re.IGNORECASE,
)
_FINAL_ENERGY_RE = re.compile(
    rf"^\s*Final energy(?:,\s*E)?\s*=\s*(?P<value>{_FLOAT_TOKEN})\s+eV\s*$",
    re.IGNORECASE,
)
_FINAL_FREE_ENERGY_RE = re.compile(
    rf"^\s*Final free energy\s*\(E-TS\)\s*=\s*(?P<value>{_FLOAT_TOKEN})\s+eV\s*$",
    re.IGNORECASE,
)
_TOTAL_TIME_RE = re.compile(
    rf"^\s*Total time\s*=\s*(?P<value>{_FLOAT_TOKEN})\s*s\s*$",
    re.IGNORECASE,
)
_MAX_SCF_RE = re.compile(
    r"max\.\s*number of SCF cycles\s*:\s*(?P<value>\d+)",
    re.IGNORECASE,
)
_SCF_WINDOW_RE = re.compile(
    r"convergence tolerance window\s*:\s*(?P<value>\d+)",
    re.IGNORECASE,
)
_SCF_TOLERANCE_RE = re.compile(
    rf"total energy\s*/\s*atom convergence tol\.\s*:\s*(?P<value>{_FLOAT_TOKEN})\s+eV",
    re.IGNORECASE,
)
_FATAL_PATTERNS = (
    re.compile(r"^\s*(?:ERROR|FATAL)\b", re.IGNORECASE),
    re.compile(r"\bError in\b", re.IGNORECASE),
    re.compile(r"\baborting\b", re.IGNORECASE),
    re.compile(r"\bfailed to converge\b", re.IGNORECASE),
    re.compile(r"\bnot converged\b", re.IGNORECASE),
    re.compile(r"\bcannot continue\b", re.IGNORECASE),
)


class CastepBandKPoint(StrictModel):
    index: int = Field(ge=1)
    fractional: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    weight: FiniteFloat = Field(ge=0)
    eigenvalues_hartree: list[list[FiniteFloat]]


class CastepBandsData(StrictModel):
    number_of_kpoints: int = Field(ge=1)
    number_of_spin_components: int = Field(ge=1, le=2)
    electrons_per_spin: list[FiniteFloat]
    eigenvalues_per_spin: list[int]
    fermi_energy_hartree_per_spin: list[FiniteFloat]
    unit_cell_vectors: tuple[
        tuple[FiniteFloat, FiniteFloat, FiniteFloat],
        tuple[FiniteFloat, FiniteFloat, FiniteFloat],
        tuple[FiniteFloat, FiniteFloat, FiniteFloat],
    ]
    kpoints: list[CastepBandKPoint]

    @model_validator(mode="after")
    def validate_counts(self) -> "CastepBandsData":
        spins = self.number_of_spin_components
        if len(self.electrons_per_spin) not in (1, spins):
            raise ValueError("Number of electron counts must be one or match spin components")
        if len(self.eigenvalues_per_spin) != spins:
            raise ValueError("Eigenvalue counts must match spin components")
        if len(self.fermi_energy_hartree_per_spin) not in (1, spins):
            raise ValueError("Fermi-energy counts must be one or match spin components")
        if len(self.kpoints) != self.number_of_kpoints:
            raise ValueError("Parsed k-point count does not match the header")
        expected_indices = list(range(1, self.number_of_kpoints + 1))
        if [item.index for item in self.kpoints] != expected_indices:
            raise ValueError("K-point indices must be consecutive and one-based")
        total_values = 0
        for point in self.kpoints:
            if len(point.eigenvalues_hartree) != spins:
                raise ValueError("K-point spin-component count does not match the header")
            for spin_index, values in enumerate(point.eigenvalues_hartree):
                if len(values) != self.eigenvalues_per_spin[spin_index]:
                    raise ValueError("K-point eigenvalue count does not match the header")
                total_values += len(values)
        if total_values > MAX_CASTEP_BAND_VALUES:
            raise ValueError("CASTEP .bands file exceeds the supported eigenvalue limit")
        return self


class _LineCursor:
    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()
        self._index = 0

    def next_nonempty(self) -> str:
        while self._index < len(self._lines):
            value = self._lines[self._index].strip()
            self._index += 1
            if value:
                return value
        raise ValueError("Unexpected end of CASTEP .bands data")

    def remaining_nonempty(self) -> list[str]:
        values = [line.strip() for line in self._lines[self._index :] if line.strip()]
        self._index = len(self._lines)
        return values


def parse_castep_bands_file(path: str | Path) -> CastepBandsData:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"CASTEP .bands file was not found: {source}")
    size = source.stat().st_size
    if size <= 0 or size > MAX_CASTEP_BANDS_BYTES:
        raise ValueError(f"CASTEP .bands file size is unsupported: {size} bytes")
    return parse_castep_bands_text(source.read_text(encoding="utf-8"))


def parse_castep_bands_text(text: str) -> CastepBandsData:
    cursor = _LineCursor(text)
    number_of_kpoints = _parse_header_int(
        cursor.next_nonempty(), "Number of k-points"
    )
    number_of_spin_components = _parse_header_int(
        cursor.next_nonempty(), "Number of spin components"
    )
    if number_of_spin_components not in (1, 2):
        raise ValueError("CASTEP .bands supports only one or two spin components")
    electrons = _parse_header_numbers(cursor.next_nonempty(), "Number of electrons")
    eigenvalue_counts = [
        _parse_integer_token(value, "Number of eigenvalues")
        for value in _parse_header_tokens(
            cursor.next_nonempty(), "Number of eigenvalues"
        )
    ]
    if len(eigenvalue_counts) == 1 and number_of_spin_components == 2:
        eigenvalue_counts *= 2
    fermi_energies = _parse_fermi_header(cursor.next_nonempty())
    if cursor.next_nonempty().lower() != "unit cell vectors":
        raise ValueError("CASTEP .bands is missing the Unit cell vectors header")
    unit_cell = tuple(
        _parse_vector3(cursor.next_nonempty(), "unit cell vector") for _ in range(3)
    )

    declared_values = number_of_kpoints * sum(eigenvalue_counts)
    if declared_values > MAX_CASTEP_BAND_VALUES:
        raise ValueError("CASTEP .bands declares too many eigenvalues")
    kpoints: list[CastepBandKPoint] = []
    point_pattern = re.compile(
        rf"^K-point\s+(?P<index>\d+)\s+"
        rf"(?P<x>{_FLOAT_TOKEN})\s+(?P<y>{_FLOAT_TOKEN})\s+"
        rf"(?P<z>{_FLOAT_TOKEN})\s+(?P<weight>{_FLOAT_TOKEN})$",
        re.IGNORECASE,
    )
    for expected_index in range(1, number_of_kpoints + 1):
        point_line = cursor.next_nonempty()
        point_match = point_pattern.fullmatch(point_line)
        if point_match is None:
            raise ValueError(f"Invalid CASTEP K-point record: {point_line!r}")
        point_index = int(point_match.group("index"))
        if point_index != expected_index:
            raise ValueError("CASTEP K-point indices are not consecutive")
        spin_values: list[list[float]] = []
        for spin_index in range(1, number_of_spin_components + 1):
            spin_line = cursor.next_nonempty()
            if spin_line.lower() != f"spin component {spin_index}":
                raise ValueError(
                    f"Expected Spin component {spin_index}, found {spin_line!r}"
                )
            values = [
                _parse_float_token(cursor.next_nonempty(), "band eigenvalue")
                for _ in range(eigenvalue_counts[spin_index - 1])
            ]
            spin_values.append(values)
        kpoints.append(
            CastepBandKPoint(
                index=point_index,
                fractional=(
                    _parse_float_token(point_match.group("x"), "K-point x"),
                    _parse_float_token(point_match.group("y"), "K-point y"),
                    _parse_float_token(point_match.group("z"), "K-point z"),
                ),
                weight=_parse_float_token(point_match.group("weight"), "K-point weight"),
                eigenvalues_hartree=spin_values,
            )
        )
    trailing = cursor.remaining_nonempty()
    if trailing:
        raise ValueError(f"Unexpected trailing CASTEP .bands data: {trailing[0]!r}")
    return CastepBandsData(
        number_of_kpoints=number_of_kpoints,
        number_of_spin_components=number_of_spin_components,
        electrons_per_spin=electrons,
        eigenvalues_per_spin=eigenvalue_counts,
        fermi_energy_hartree_per_spin=fermi_energies,
        unit_cell_vectors=unit_cell,
        kpoints=kpoints,
    )


def parse_castep_output_file(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"CASTEP output file was not found: {source}")
    size = source.stat().st_size
    if size <= 0 or size > MAX_CASTEP_OUTPUT_BYTES:
        raise ValueError(f"CASTEP output file size is unsupported: {size} bytes")
    result = parse_castep_output_text(source.read_text(encoding="utf-8"))
    result.update(
        {
            "source_path": str(source),
            "source_sha256": _file_sha256(source),
            "source_size_bytes": size,
        }
    )
    return result


def parse_castep_output_text(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    max_scf_cycles: int | None = None
    convergence_window: int | None = None
    energy_tolerance_ev_per_atom: float | None = None
    final_energies: list[float] = []
    final_free_energies: list[float] = []
    total_times: list[float] = []
    warnings: list[str] = []
    fatal_markers: list[str] = []
    scf_blocks: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] | None = None

    for line_number, line in enumerate(lines, start=1):
        if "SCF loop" in line and "<-- SCF" in line:
            if current_rows:
                scf_blocks.append(_summarize_scf_rows(current_rows))
            current_rows = []
        row_match = _SCF_ROW_RE.match(line)
        if row_match is not None:
            if current_rows is None:
                current_rows = []
            rest_numbers = _numbers_from_text(row_match.group("rest"))
            current_rows.append(
                {
                    "iteration": int(row_match.group("iteration")),
                    "energy_ev": _parse_float_token(
                        row_match.group("energy"), "SCF energy"
                    ),
                    "energy_gain_ev_per_atom": (
                        rest_numbers[-2] if len(rest_numbers) >= 2 else None
                    ),
                    "line_number": line_number,
                }
            )
        match = _MAX_SCF_RE.search(line)
        if match is not None:
            max_scf_cycles = int(match.group("value"))
        match = _SCF_WINDOW_RE.search(line)
        if match is not None:
            convergence_window = int(match.group("value"))
        match = _SCF_TOLERANCE_RE.search(line)
        if match is not None:
            energy_tolerance_ev_per_atom = _parse_float_token(
                match.group("value"), "SCF tolerance"
            )
        match = _FINAL_ENERGY_RE.match(line)
        if match is not None:
            final_energies.append(
                _parse_float_token(match.group("value"), "final energy")
            )
        match = _FINAL_FREE_ENERGY_RE.match(line)
        if match is not None:
            final_free_energies.append(
                _parse_float_token(match.group("value"), "final free energy")
            )
        match = _TOTAL_TIME_RE.match(line)
        if match is not None:
            total_times.append(
                _parse_float_token(match.group("value"), "total time")
            )
        stripped = line.strip()
        if re.search(r"\bwarning\b", stripped, re.IGNORECASE):
            _append_unique_limited(warnings, stripped, limit=100)
        if any(pattern.search(line) for pattern in _FATAL_PATTERNS):
            _append_unique_limited(fatal_markers, stripped, limit=100)
    if current_rows:
        scf_blocks.append(_summarize_scf_rows(current_rows))

    maximum_cycles_reached = bool(
        max_scf_cycles is not None
        and any(
            int(block.get("last_iteration") or 0) >= max_scf_cycles
            for block in scf_blocks
        )
    )
    run_completed = bool(final_energies and total_times and not fatal_markers)
    if fatal_markers:
        status = "fatal_error"
    elif maximum_cycles_reached:
        status = "maximum_scf_cycles_reached"
    elif run_completed and scf_blocks:
        status = "completed_below_max_cycles"
    elif scf_blocks or final_energies:
        status = "incomplete_output"
    else:
        status = "scf_not_observed"
    return {
        "schema_version": CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA,
        "status": status,
        "run_completed": run_completed,
        "scientific_convergence_verified": False,
        "convergence_interpretation": (
            "CASTEP output completed below the declared maximum SCF cycles; "
            "this is structured review evidence, not an independent convergence boolean."
            if status == "completed_below_max_cycles"
            else "No scientific convergence claim is made from this output."
        ),
        "max_scf_cycles": max_scf_cycles,
        "convergence_window": convergence_window,
        "energy_tolerance_ev_per_atom": energy_tolerance_ev_per_atom,
        "scf_block_count": len(scf_blocks),
        "scf_blocks": scf_blocks,
        "last_scf_iteration": (
            scf_blocks[-1].get("last_iteration") if scf_blocks else None
        ),
        "maximum_scf_cycles_reached": maximum_cycles_reached,
        "final_energy_ev": final_energies[-1] if final_energies else None,
        "final_free_energy_ev": (
            final_free_energies[-1] if final_free_energies else None
        ),
        "total_time_seconds": total_times[-1] if total_times else None,
        "warning_count": len(warnings),
        "warnings": warnings,
        "fatal_marker_count": len(fatal_markers),
        "fatal_markers": fatal_markers,
    }


def write_castep_band_eigenvalues_csv(
    data: CastepBandsData,
    path: str | Path,
) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    fermi_values = _expand_spin_values(
        data.fermi_energy_hartree_per_spin,
        data.number_of_spin_components,
    )
    headers = [
        "kpoint_index",
        "kx_fractional",
        "ky_fractional",
        "kz_fractional",
        "kpoint_weight",
        "spin_component",
        "band_index",
        "eigenvalue_hartree",
        "eigenvalue_ev",
        "fermi_energy_hartree",
        "fermi_energy_ev",
        "energy_minus_fermi_ev",
    ]

    def rows() -> Iterable[list[Any]]:
        for point in data.kpoints:
            for spin_index, eigenvalues in enumerate(point.eigenvalues_hartree):
                fermi = fermi_values[spin_index]
                for band_index, eigenvalue in enumerate(eigenvalues, start=1):
                    yield [
                        point.index,
                        *point.fractional,
                        point.weight,
                        spin_index + 1,
                        band_index,
                        eigenvalue,
                        eigenvalue * HARTREE_TO_EV,
                        fermi,
                        fermi * HARTREE_TO_EV,
                        (eigenvalue - fermi) * HARTREE_TO_EV,
                    ]

    row_count = _atomic_write_csv(target, headers, rows())
    return _artifact_receipt(
        target,
        row_count=row_count,
        artifact_kind="castep_band_eigenvalues_csv",
    )


def write_castep_gaussian_dos_csv(
    data: CastepBandsData,
    path: str | Path,
    *,
    smearing_width_ev: float,
    energy_max_ev: float | None,
    grid_points: int = DEFAULT_DOS_GRID_POINTS,
) -> dict[str, Any]:
    if not math.isfinite(smearing_width_ev) or smearing_width_ev <= 0:
        raise ValueError("DOS smearing width must be a positive finite value")
    if grid_points < 101 or grid_points > 10_001:
        raise ValueError("DOS grid_points must be between 101 and 10001")
    weight_sum = sum(float(point.weight) for point in data.kpoints)
    if weight_sum <= 0:
        raise ValueError("CASTEP .bands k-point weights cannot generate a DOS curve")
    fermi_values = _expand_spin_values(
        data.fermi_energy_hartree_per_spin,
        data.number_of_spin_components,
    )
    energies_by_spin: list[list[tuple[float, float]]] = [
        [] for _ in range(data.number_of_spin_components)
    ]
    spin_degeneracy = 2.0 if data.number_of_spin_components == 1 else 1.0
    for point in data.kpoints:
        normalized_weight = float(point.weight) / weight_sum * spin_degeneracy
        for spin_index, values in enumerate(point.eigenvalues_hartree):
            fermi = fermi_values[spin_index]
            energies_by_spin[spin_index].extend(
                ((value - fermi) * HARTREE_TO_EV, normalized_weight)
                for value in values
            )
    all_energies = [value for values in energies_by_spin for value, _ in values]
    if not all_energies:
        raise ValueError("CASTEP .bands contains no eigenvalues for DOS export")
    energy_min = min(all_energies) - 5.0 * smearing_width_ev
    natural_max = max(all_energies) + 5.0 * smearing_width_ev
    energy_max = natural_max if energy_max_ev is None else float(energy_max_ev)
    if not math.isfinite(energy_max) or energy_max <= energy_min:
        raise ValueError("DOS energy maximum must exceed the derived minimum")
    step = (energy_max - energy_min) / (grid_points - 1)
    grid = [energy_min + index * step for index in range(grid_points)]
    dos_by_spin = [
        _gaussian_dos_on_grid(
            values,
            energy_min=energy_min,
            step=step,
            grid_points=grid_points,
            sigma=smearing_width_ev,
        )
        for values in energies_by_spin
    ]
    headers = ["energy_minus_fermi_ev", "total_dos_states_per_ev"] + [
        f"spin_{index}_dos_states_per_ev"
        for index in range(1, data.number_of_spin_components + 1)
    ]

    def rows() -> Iterable[list[Any]]:
        for index, energy in enumerate(grid):
            spin_values = [values[index] for values in dos_by_spin]
            yield [energy, sum(spin_values), *spin_values]

    target = Path(path).expanduser().resolve()
    row_count = _atomic_write_csv(target, headers, rows())
    receipt = _artifact_receipt(
        target,
        row_count=row_count,
        artifact_kind="castep_gaussian_total_dos_csv",
    )
    receipt.update(
        {
            "derivation": "mcp_gaussian_smearing_from_native_castep_bands",
            "smearing_width_ev": smearing_width_ev,
            "grid_points": grid_points,
            "energy_min_ev": energy_min,
            "energy_max_ev": energy_max,
            "kpoint_weight_sum_before_normalization": weight_sum,
            "spin_degeneracy": spin_degeneracy,
        }
    )
    return receipt


def audit_castep_native_artifacts(
    native_artifacts: list[dict[str, Any]],
    *,
    task: str,
    destination: str | Path,
    dos_integration_method: str | None = None,
    dos_smearing_width_ev: float | None = None,
    dos_energy_max_ev: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse trusted native files and export bounded, provenance-rich CSV data."""

    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise ValueError(f"CASTEP native audit destination already exists: {target}")
    candidates = _verified_native_candidates(native_artifacts)
    castep_paths = [path for path in candidates if path.suffix.lower() == ".castep"]
    bands_paths = [path for path in candidates if path.suffix.lower() == ".bands"]
    warnings: list[str] = []
    errors: list[str] = []
    output_audit: dict[str, Any] | None = None
    bands_summary: dict[str, Any] | None = None
    parsed_bands: CastepBandsData | None = None
    derived_artifacts: list[dict[str, Any]] = []

    if len(castep_paths) == 1:
        try:
            output_audit = parse_castep_output_file(castep_paths[0])
        except (OSError, ValueError) as exc:
            errors.append(f"Native CASTEP output parse failed: {exc}")
    elif not castep_paths:
        warnings.append("Runner artifacts did not include a native .castep output file.")
    else:
        errors.append(
            "Runner artifacts contained multiple .castep files; no output was selected."
        )

    if len(bands_paths) == 1:
        try:
            parsed_bands = parse_castep_bands_file(bands_paths[0])
            bands_summary = _bands_summary(parsed_bands, bands_paths[0])
        except (OSError, ValueError) as exc:
            errors.append(f"Native CASTEP .bands parse failed: {exc}")
    elif not bands_paths:
        warnings.append("Runner artifacts did not include the documented native .bands file.")
    else:
        errors.append(
            "Runner artifacts contained multiple .bands files; no numeric source was selected."
        )

    numeric_curve_data_exported = False
    numeric_curve_kind: str | None = None
    native_band_kpoint_path_exported = False
    if parsed_bands is not None and task in {
        "BandStructure",
        "DensityOfStates",
        "ProjectedDensityOfStates",
    }:
        target.mkdir(parents=True, exist_ok=False)
        band_receipt = write_castep_band_eigenvalues_csv(
            parsed_bands,
            target / "band_eigenvalues.csv",
        )
        derived_artifacts.append(band_receipt)
        native_band_kpoint_path_exported = task == "BandStructure"
        if task == "BandStructure":
            numeric_curve_data_exported = True
            numeric_curve_kind = "native_castep_band_eigenvalues"
        elif task == "DensityOfStates":
            if (
                dos_integration_method == "Smearing"
                and isinstance(dos_smearing_width_ev, (int, float))
            ):
                dos_receipt = write_castep_gaussian_dos_csv(
                    parsed_bands,
                    target / "total_dos_gaussian.csv",
                    smearing_width_ev=float(dos_smearing_width_ev),
                    energy_max_ev=dos_energy_max_ev,
                )
                derived_artifacts.append(dos_receipt)
                numeric_curve_data_exported = True
                numeric_curve_kind = "mcp_gaussian_total_dos_from_native_bands"
            else:
                warnings.append(
                    "Numeric total DOS export requires the reviewed Smearing method "
                    "and an explicit DOS smearing width."
                )
        else:
            warnings.append(
                "The .pdos_weights layout is not documented sufficiently for a "
                "trusted numeric PDOS export; only native band eigenvalues were exported."
            )

    if task in {"BandStructure", "DensityOfStates", "ProjectedDensityOfStates"}:
        if parsed_bands is None:
            warnings.append(
                "The requested property has no trusted numeric export because native "
                ".bands evidence was unavailable or ambiguous."
            )
    if output_audit is not None and output_audit.get("warning_count"):
        warnings.append(
            "Native CASTEP output contains warnings; inspect the persisted SCF audit."
        )
    if output_audit is not None and output_audit.get("fatal_marker_count"):
        errors.append("Native CASTEP output contains a fatal marker.")
    if output_audit is not None and output_audit.get("status") in {
        "maximum_scf_cycles_reached",
        "incomplete_output",
        "scf_not_observed",
    }:
        errors.append(
            "Native CASTEP output does not show a completed SCF run below the "
            "configured maximum cycle count."
        )

    available_components = sum(
        value is not None for value in (output_audit, bands_summary)
    )
    if errors:
        status = "review_required"
    elif available_components == 2:
        status = "complete"
    elif available_components:
        status = "partial"
    else:
        status = "unavailable"
    audit = {
        "schema_version": CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA,
        "status": status,
        "task": task,
        "native_artifact_count": len(candidates),
        "native_castep_candidate_count": len(castep_paths),
        "native_bands_candidate_count": len(bands_paths),
        "castep_output_audit": output_audit,
        "bands_summary": bands_summary,
        "scientific_convergence_verified": False,
        "numeric_curve_data_exported": numeric_curve_data_exported,
        "numeric_curve_kind": numeric_curve_kind,
        "native_band_kpoint_path_exported": native_band_kpoint_path_exported,
        "pdos_projection_weights_exported": False,
        "derived_artifact_count": len(derived_artifacts),
        "derived_artifacts": derived_artifacts,
        "warnings": warnings,
        "errors": errors,
    }
    return audit, derived_artifacts


def _parse_header_int(line: str, label: str) -> int:
    tokens = _parse_header_tokens(line, label)
    if len(tokens) != 1:
        raise ValueError(f"{label} must contain exactly one integer")
    return _parse_integer_token(tokens[0], label)


def _parse_header_numbers(line: str, label: str) -> list[float]:
    return [_parse_float_token(value, label) for value in _parse_header_tokens(line, label)]


def _parse_header_tokens(line: str, label: str) -> list[str]:
    match = re.fullmatch(rf"{re.escape(label)}\s+(.+)", line, re.IGNORECASE)
    if match is None:
        raise ValueError(f"Expected CASTEP .bands header {label!r}, found {line!r}")
    tokens = match.group(1).split()
    if not tokens:
        raise ValueError(f"CASTEP .bands header {label!r} has no values")
    return tokens


def _parse_fermi_header(line: str) -> list[float]:
    match = re.fullmatch(
        r"Fermi energ(?:y|ies) \(in atomic units\)\s+(.+)",
        line,
        re.IGNORECASE,
    )
    if match is None:
        raise ValueError(f"Invalid CASTEP Fermi-energy header: {line!r}")
    return [
        _parse_float_token(value, "Fermi energy")
        for value in match.group(1).split()
    ]


def _parse_vector3(line: str, label: str) -> tuple[float, float, float]:
    values = line.split()
    if len(values) != 3:
        raise ValueError(f"CASTEP {label} must contain three values")
    return tuple(_parse_float_token(value, label) for value in values)  # type: ignore[return-value]


def _parse_integer_token(value: str, label: str) -> int:
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"CASTEP {label} must be an integer")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"CASTEP {label} must be positive")
    return parsed


def _parse_float_token(value: str, label: str) -> float:
    stripped = value.strip()
    if _FLOAT_RE.fullmatch(stripped) is None:
        raise ValueError(f"CASTEP {label} is not numeric: {value!r}")
    parsed = float(stripped.replace("D", "E").replace("d", "e"))
    if not math.isfinite(parsed):
        raise ValueError(f"CASTEP {label} must be finite")
    return parsed


def _numbers_from_text(value: str) -> list[float]:
    return [
        _parse_float_token(match.group(0), "SCF row value")
        for match in re.finditer(_FLOAT_TOKEN, value)
    ]


def _summarize_scf_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "first_iteration": rows[0]["iteration"],
        "last_iteration": rows[-1]["iteration"],
        "last_energy_ev": rows[-1]["energy_ev"],
        "last_energy_gain_ev_per_atom": rows[-1]["energy_gain_ev_per_atom"],
    }


def _append_unique_limited(values: list[str], value: str, *, limit: int) -> None:
    if value and value not in values and len(values) < limit:
        values.append(value)


def _expand_spin_values(values: list[float], spins: int) -> list[float]:
    if len(values) == spins:
        return [float(value) for value in values]
    if len(values) == 1:
        return [float(values[0])] * spins
    raise ValueError("CASTEP spin-dependent value count is inconsistent")


def _gaussian_dos_on_grid(
    values: list[tuple[float, float]],
    *,
    energy_min: float,
    step: float,
    grid_points: int,
    sigma: float,
) -> list[float]:
    histogram = [0.0] * grid_points
    for energy, weight in values:
        position = (energy - energy_min) / step
        lower = math.floor(position)
        fraction = position - lower
        if 0 <= lower < grid_points:
            histogram[lower] += weight * (1.0 - fraction)
        upper = lower + 1
        if 0 <= upper < grid_points:
            histogram[upper] += weight * fraction
    radius = min(grid_points - 1, max(1, math.ceil(5.0 * sigma / step)))
    normalization = 1.0 / (sigma * math.sqrt(2.0 * math.pi))
    kernel = [
        normalization * math.exp(-0.5 * ((offset * step) / sigma) ** 2)
        for offset in range(-radius, radius + 1)
    ]
    output = [0.0] * grid_points
    nonzero = [(index, value) for index, value in enumerate(histogram) if value]
    for source_index, source_value in nonzero:
        start = max(0, source_index - radius)
        stop = min(grid_points, source_index + radius + 1)
        for target_index in range(start, stop):
            kernel_index = target_index - source_index + radius
            output[target_index] += source_value * kernel[kernel_index]
    return output


def _atomic_write_csv(
    target: Path,
    headers: list[str],
    rows: Iterable[list[Any]],
) -> int:
    if target.exists():
        raise ValueError(f"Refusing to overwrite CASTEP derived artifact: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    handle: TextIO | None = None
    temporary_name: str | None = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_name = handle.name
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
            row_count += 1
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if handle is not None:
            handle.close()
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return row_count


def _artifact_receipt(
    path: Path,
    *,
    row_count: int,
    artifact_kind: str,
) -> dict[str, Any]:
    return {
        "artifact_kind": artifact_kind,
        "path": str(path),
        "sha256": _file_sha256(path),
        "size_bytes": path.stat().st_size,
        "row_count": row_count,
    }


def _verified_native_candidates(
    artifacts: list[dict[str, Any]],
) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for artifact in artifacts:
        raw_path = artifact.get("path")
        raw_digest = artifact.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(raw_digest, str):
            raise ValueError("CASTEP native artifact manifest is missing path or SHA-256")
        path = Path(raw_path).expanduser().resolve()
        key = str(path).lower()
        if key in seen:
            raise ValueError(f"Duplicate CASTEP native artifact path: {path}")
        seen.add(key)
        if not path.is_file() or _file_sha256(path) != raw_digest:
            raise ValueError(f"CASTEP native artifact binding mismatch: {path}")
        paths.append(path)
    return sorted(paths, key=lambda value: str(value).lower())


def _bands_summary(data: CastepBandsData, path: Path) -> dict[str, Any]:
    values = [
        float(value)
        for point in data.kpoints
        for spin_values in point.eigenvalues_hartree
        for value in spin_values
    ]
    return {
        "source_path": str(path),
        "source_sha256": _file_sha256(path),
        "source_size_bytes": path.stat().st_size,
        "number_of_kpoints": data.number_of_kpoints,
        "number_of_spin_components": data.number_of_spin_components,
        "electrons_per_spin": list(data.electrons_per_spin),
        "eigenvalues_per_spin": list(data.eigenvalues_per_spin),
        "fermi_energy_hartree_per_spin": list(
            data.fermi_energy_hartree_per_spin
        ),
        "fermi_energy_ev_per_spin": [
            value * HARTREE_TO_EV
            for value in data.fermi_energy_hartree_per_spin
        ],
        "kpoint_weight_sum": sum(float(point.weight) for point in data.kpoints),
        "eigenvalue_count": len(values),
        "eigenvalue_min_hartree": min(values),
        "eigenvalue_max_hartree": max(values),
        "eigenvalue_min_ev": min(values) * HARTREE_TO_EV,
        "eigenvalue_max_ev": max(values) * HARTREE_TO_EV,
    }


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
