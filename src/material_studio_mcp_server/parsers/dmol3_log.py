"""Strict validation for tagged DMol3 geometry-optimization results."""

from __future__ import annotations

import math
import os
import re
import stat
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    Field,
    FiniteFloat,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from ..dmol3_contract import (
    DMOL3_GEOMETRY_RESULT_SCHEMA,
    DMOL3_REVIEWED_RESULT_KEYS,
)
from ..specs.common import StrictModel, Vector3, validate_element_symbol
from ..specs.molecule import MoleculeSpec


_DMOL3_MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
_DMOL3_ATOM_TOKEN = re.compile(r"^MSMCPAtom[0-9]{6}$")
_DMOL3_XSD_COORDINATE_TOLERANCE_ANGSTROM = 1e-6


class DMol3OptimizedAtom(StrictModel):
    """Identity-bound Cartesian coordinate returned by the DMol3 script."""

    id: str = Field(min_length=1, max_length=50)
    element: str = Field(min_length=1, max_length=3)
    xyz_angstrom: Vector3

    @field_validator("element")
    @classmethod
    def known_element(cls, value: str) -> str:
        return validate_element_symbol(value)

    @field_validator("xyz_angstrom", mode="before")
    @classmethod
    def strict_cartesian_coordinates(cls, value: Any) -> Any:
        if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
            raise ValueError(
                "DMol3 optimized xyz_angstrom must contain exactly x, y, and z"
            )
        for axis in ("x", "y", "z"):
            coordinate = value[axis]
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not math.isfinite(float(coordinate))
            ):
                raise ValueError(
                    f"DMol3 optimized {axis} coordinate must be a finite number"
                )
        return value


class DMol3ResultDocuments(StrictModel):
    energy_chart: str | None = Field(
        default=None,
        alias="EnergyChart",
        min_length=1,
        max_length=500,
    )
    convergence_chart: str | None = Field(
        default=None,
        alias="ConvergenceChart",
        min_length=1,
        max_length=500,
    )


class DMol3GeometryResultPayload(StrictModel):
    """Tagged payload emitted by the reviewed standalone DMol3 script."""

    schema_version: Literal[DMOL3_GEOMETRY_RESULT_SCHEMA]
    project_id: str = Field(min_length=1, max_length=120)
    base_revision: int = Field(ge=0)
    script_kind: Literal["dmol3_geometry_optimization"]
    module: Literal["DMol3"]
    task: Literal["GeometryOptimization"]
    input_structure: str = Field(min_length=1, max_length=4096)
    output_structure: str = Field(min_length=1, max_length=4096)
    output_report: str = Field(min_length=1, max_length=4096)
    materials_studio_api_contract: Literal["Materials Studio 20.1"]
    result_keys: list[str]
    energy_evolution_charts_requested: StrictBool
    converged: StrictBool
    total_energy_kcal_per_mol: FiniteFloat
    optimized_atoms: list[DMol3OptimizedAtom] = Field(min_length=1)
    result_document_names: DMol3ResultDocuments

    @field_validator("total_energy_kcal_per_mol", mode="before")
    @classmethod
    def strict_total_energy(cls, value: Any) -> Any:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("DMol3 TotalEnergy must be a finite JSON number")
        return value

    @model_validator(mode="after")
    def validate_result_contract(self) -> "DMol3GeometryResultPayload":
        if tuple(self.result_keys) != DMOL3_REVIEWED_RESULT_KEYS:
            raise ValueError(
                "DMol3 geometry result_keys do not match the reviewed contract"
            )
        atom_ids = [atom.id for atom in self.optimized_atoms]
        if len(set(atom_ids)) != len(atom_ids):
            raise ValueError("DMol3 optimized atom IDs must be unique")
        if self.energy_evolution_charts_requested:
            if not self.result_document_names.energy_chart:
                raise ValueError(
                    "DMol3 result requires EnergyChart when chart creation is requested"
                )
            if not self.result_document_names.convergence_chart:
                raise ValueError(
                    "DMol3 result requires ConvergenceChart when chart creation is requested"
                )
        return self


def _read_bounded_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int = _DMOL3_MAX_EVIDENCE_BYTES,
) -> tuple[bytes | None, dict[str, Any], list[str]]:
    """Read one stable, bounded, non-link evidence file."""

    errors: list[str] = []
    receipt: dict[str, Any] = {
        "path": str(path),
        "exists": False,
        "regular_file": False,
        "link_or_reparse": False,
        "size_bytes": None,
        "max_bytes": max_bytes,
        "stable_descriptor_read": False,
    }
    try:
        before = path.lstat()
    except OSError as exc:
        errors.append(f"{label} could not be inspected: {exc}.")
        return None, receipt, errors
    attributes = int(getattr(before, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    link_or_reparse = path.is_symlink() or bool(attributes & reparse_flag)
    receipt.update(
        {
            "exists": True,
            "regular_file": stat.S_ISREG(before.st_mode),
            "link_or_reparse": link_or_reparse,
            "size_bytes": before.st_size,
        }
    )
    if link_or_reparse:
        errors.append(f"{label} must not be a symbolic link or reparse point.")
        return None, receipt, errors
    if not stat.S_ISREG(before.st_mode):
        errors.append(f"{label} is not a regular file.")
        return None, receipt, errors
    if before.st_size <= 0:
        errors.append(f"{label} is empty.")
        return None, receipt, errors
    if before.st_size > max_bytes:
        errors.append(f"{label} exceeds the {max_bytes}-byte evidence limit.")
        return None, receipt, errors
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            payload = handle.read(max_bytes + 1)
            after_read = os.fstat(handle.fileno())
        after_path = path.stat()
    except OSError as exc:
        errors.append(f"{label} could not be read: {exc}.")
        return None, receipt, errors
    if len(payload) > max_bytes:
        errors.append(f"{label} grew beyond the bounded read limit.")
        return None, receipt, errors
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    identities = [
        tuple(getattr(item, field, None) for field in identity_fields)
        for item in (before, opened, after_read, after_path)
    ]
    if len(set(identities)) != 1 or len(payload) != opened.st_size:
        errors.append(f"{label} changed during its bounded descriptor read.")
        return None, receipt, errors
    receipt["stable_descriptor_read"] = True
    return payload, receipt, errors


def validate_dmol3_output_evidence(
    parsed: DMol3GeometryResultPayload,
    *,
    source_molecule: MoleculeSpec,
    output_structure: str | Path,
    output_report: str | Path,
) -> dict[str, Any]:
    """Cross-check tagged coordinates against exported XSD and DMol3 report."""

    structure_path = Path(output_structure).expanduser().resolve()
    report_path = Path(output_report).expanduser().resolve()
    errors: list[str] = []

    structure_bytes, structure_receipt, structure_read_errors = (
        _read_bounded_regular_file(
            structure_path,
            label="Optimized DMol3 XSD",
        )
    )
    errors.extend(structure_read_errors)
    structure_errors: list[str] = []
    matched_atom_count = 0
    max_coordinate_delta = 0.0
    expected_tokens = [
        f"MSMCPAtom{index:06d}"
        for index in range(1, len(source_molecule.atoms) + 1)
    ]
    if structure_bytes is not None:
        if re.search(br"<!ENTITY\b", structure_bytes, flags=re.IGNORECASE):
            structure_errors.append(
                "Optimized DMol3 XSD contains a forbidden entity declaration."
            )
        else:
            try:
                root = ET.fromstring(structure_bytes)
            except ET.ParseError as exc:
                structure_errors.append(
                    f"Optimized DMol3 XSD is not well-formed XML: {exc}."
                )
            else:
                root_name = root.tag.rsplit("}", 1)[-1]
                if root_name != "XSD":
                    structure_errors.append(
                        "Optimized DMol3 structure root element is not XSD."
                    )
                if root.attrib.get("Version") != "20.1":
                    structure_errors.append(
                        "Optimized DMol3 XSD is not bound to Materials Studio 20.1."
                    )
                atom_nodes = [
                    item
                    for item in root.iter()
                    if item.tag.rsplit("}", 1)[-1] == "Atom3d"
                ]
                if len(atom_nodes) != len(source_molecule.atoms):
                    structure_errors.append(
                        "Optimized DMol3 XSD atom count differs from the source "
                        f"molecule: expected {len(source_molecule.atoms)}, "
                        f"found {len(atom_nodes)}."
                    )
                nodes_by_token: dict[str, ET.Element] = {}
                for node in atom_nodes:
                    token = str(node.attrib.get("Name") or "")
                    if not _DMOL3_ATOM_TOKEN.fullmatch(token):
                        structure_errors.append(
                            "Optimized DMol3 XSD contains an atom without its "
                            "deterministic MSMCP token."
                        )
                        continue
                    if token in nodes_by_token:
                        structure_errors.append(
                            f"Optimized DMol3 XSD duplicates atom token {token}."
                        )
                        continue
                    nodes_by_token[token] = node

                optimized_by_id = {
                    atom.id: atom for atom in parsed.optimized_atoms
                }
                for token, source_atom in zip(
                    expected_tokens,
                    source_molecule.atoms,
                    strict=True,
                ):
                    node = nodes_by_token.get(token)
                    optimized_atom = optimized_by_id.get(source_atom.id)
                    if node is None or optimized_atom is None:
                        structure_errors.append(
                            f"Optimized DMol3 XSD is missing source-bound token {token}."
                        )
                        continue
                    if node.attrib.get("Components") != source_atom.element:
                        structure_errors.append(
                            f"Optimized DMol3 XSD element mismatch for {token}."
                        )
                        continue
                    raw_xyz = node.attrib.get("XYZ", "0,0,0")
                    parts = [part.strip() for part in raw_xyz.split(",")]
                    if len(parts) != 3:
                        structure_errors.append(
                            f"Optimized DMol3 XSD XYZ is malformed for {token}."
                        )
                        continue
                    try:
                        observed_xyz = tuple(float(part) for part in parts)
                    except ValueError:
                        structure_errors.append(
                            f"Optimized DMol3 XSD XYZ is non-numeric for {token}."
                        )
                        continue
                    if not all(math.isfinite(value) for value in observed_xyz):
                        structure_errors.append(
                            f"Optimized DMol3 XSD XYZ is non-finite for {token}."
                        )
                        continue
                    expected_xyz = optimized_atom.xyz_angstrom.as_tuple()
                    delta = max(
                        abs(observed - expected)
                        for observed, expected in zip(
                            observed_xyz,
                            expected_xyz,
                            strict=True,
                        )
                    )
                    max_coordinate_delta = max(max_coordinate_delta, delta)
                    if delta > _DMOL3_XSD_COORDINATE_TOLERANCE_ANGSTROM:
                        structure_errors.append(
                            "Optimized DMol3 XSD coordinates differ from the "
                            f"tagged result for {token} by {delta:.12g} angstrom."
                        )
                        continue
                    matched_atom_count += 1
    errors.extend(structure_errors)
    structure_receipt.update(
        {
            "format": "Materials Studio XSD 20.1",
            "token_binding_required": True,
            "expected_atom_tokens": expected_tokens,
            "matched_atom_count": matched_atom_count,
            "expected_atom_count": len(source_molecule.atoms),
            "coordinate_tolerance_angstrom": (
                _DMOL3_XSD_COORDINATE_TOLERANCE_ANGSTROM
            ),
            "max_coordinate_delta_angstrom": max_coordinate_delta,
            "verified": not structure_read_errors and not structure_errors,
            "errors": structure_read_errors + structure_errors,
        }
    )

    report_bytes, report_receipt, report_read_errors = _read_bounded_regular_file(
        report_path,
        label="DMol3 geometry optimization report",
    )
    errors.extend(report_read_errors)
    report_errors: list[str] = []
    if report_bytes is not None:
        report_text = report_bytes.decode("latin-1")
        if "Materials Studio DMol^3" not in report_text:
            report_errors.append(
                "DMol3 report is missing the Materials Studio DMol^3 signature."
            )
        if (
            parsed.converged
            and "Geometry optimization completed successfully" not in report_text
        ):
            report_errors.append(
                "DMol3 report does not independently confirm successful "
                "geometry optimization."
            )
    errors.extend(report_errors)
    report_receipt.update(
        {
            "dmol3_signature_present": bool(
                report_bytes is not None
                and b"Materials Studio DMol^3" in report_bytes
            ),
            "geometry_convergence_marker_present": bool(
                report_bytes is not None
                and b"Geometry optimization completed successfully"
                in report_bytes
            ),
            "verified": not report_read_errors and not report_errors,
            "errors": report_read_errors + report_errors,
        }
    )

    return {
        "schema_version": "material_studio_dmol3_output_evidence_v1",
        "verified": not errors,
        "structure": structure_receipt,
        "report": report_receipt,
        "errors": errors,
    }


def validate_dmol3_geometry_result(
    payload: Any,
    *,
    project_id: str,
    base_revision: int,
    source_molecule: MoleculeSpec,
    input_structure: str | Path,
    output_structure: str | Path,
    output_report: str | Path,
) -> dict[str, Any]:
    """Validate DMol3 output artifacts and atom identity fail-closed."""

    expected_paths = {
        "input_structure": Path(input_structure).expanduser().resolve(),
        "output_structure": Path(output_structure).expanduser().resolve(),
        "output_report": Path(output_report).expanduser().resolve(),
    }
    errors: list[str] = []
    warnings: list[str] = []
    normalized: dict[str, Any] = {}
    parsed: DMol3GeometryResultPayload | None = None
    try:
        parsed = DMol3GeometryResultPayload.model_validate(payload)
        normalized = parsed.model_dump(mode="json", by_alias=True)
    except ValidationError as exc:
        errors.extend(
            f"DMol3 geometry result payload invalid: {item['loc']}: {item['msg']}"
            for item in exc.errors()
        )

    if normalized:
        for key, expected in {
            "project_id": project_id,
            "base_revision": base_revision,
        }.items():
            if normalized.get(key) != expected:
                errors.append(
                    f"DMol3 result {key} mismatch: expected {expected!r}, "
                    f"found {normalized.get(key)!r}."
                )
        for key, expected_path in expected_paths.items():
            try:
                actual_path = Path(str(normalized.get(key))).expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                actual_path = None
            if actual_path != expected_path:
                errors.append(
                    f"DMol3 result {key} is not bound to the planned workspace artifact."
                )

        source_atoms = {atom.id: atom for atom in source_molecule.atoms}
        optimized_atoms = {
            str(atom.get("id")): atom
            for atom in normalized.get("optimized_atoms", [])
            if isinstance(atom, dict)
        }
        missing_ids = sorted(set(source_atoms) - set(optimized_atoms))
        extra_ids = sorted(set(optimized_atoms) - set(source_atoms))
        if missing_ids or extra_ids:
            errors.append(
                "DMol3 optimized atom identities differ from the source: "
                f"missing={missing_ids}, extra={extra_ids}."
            )
        mismatches = [
            atom_id
            for atom_id in sorted(set(source_atoms) & set(optimized_atoms))
            if source_atoms[atom_id].element
            != optimized_atoms[atom_id].get("element")
        ]
        if mismatches:
            errors.append(
                "DMol3 optimized atom elements differ for IDs: "
                + ", ".join(mismatches)
                + "."
            )

    file_status = {key: path.is_file() for key, path in expected_paths.items()}
    if not file_status["input_structure"]:
        errors.append(
            f"DMol3 input structure was not found: {expected_paths['input_structure']}"
        )
    if not file_status["output_structure"]:
        errors.append(
            "Optimized DMol3 structure was not found: "
            f"{expected_paths['output_structure']}"
        )
    if not file_status["output_report"]:
        errors.append(
            f"DMol3 report was not found: {expected_paths['output_report']}"
        )
    if normalized and normalized.get("converged") is False:
        warnings.append(
            "DMol3 completed but did not meet the geometry convergence criteria."
        )

    output_evidence: dict[str, Any] | None = None
    if parsed is not None and file_status["output_structure"] and file_status["output_report"]:
        output_evidence = validate_dmol3_output_evidence(
            parsed,
            source_molecule=source_molecule,
            output_structure=expected_paths["output_structure"],
            output_report=expected_paths["output_report"],
        )
        errors.extend(
            f"DMol3 output evidence invalid: {error}"
            for error in output_evidence["errors"]
        )

    return {
        "schema_version": DMOL3_GEOMETRY_RESULT_SCHEMA,
        "ok": not errors,
        "converged": normalized.get("converged") is True,
        "atom_identity_preserved": bool(
            normalized
            and output_evidence
            and output_evidence.get("verified")
            and not any(
                "atom identit" in error or "atom elements" in error
                for error in errors
            )
        ),
        "geometry_evidence_verified": bool(
            output_evidence and output_evidence.get("verified")
        ),
        "output_evidence": output_evidence,
        "result": normalized or None,
        "input_structure": str(expected_paths["input_structure"]),
        "input_structure_exists": file_status["input_structure"],
        "output_structure": str(expected_paths["output_structure"]),
        "output_structure_exists": file_status["output_structure"],
        "output_report": str(expected_paths["output_report"]),
        "output_report_exists": file_status["output_report"],
        "errors": errors,
        "warnings": warnings,
    }
