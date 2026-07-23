"""Materials Studio runner 输出解析器。

此模块提供了各种解析功能。
"""

from .castep_log import (
    CASTEP_ELECTRONIC_RESULT_SCHEMA,
    CastepElectronicResultPayload,
    parse_castep_energy,
    validate_castep_electronic_result,
    validate_castep_geometry_result,
)
from .castep_native import (
    CASTEP_NATIVE_OUTPUT_AUDIT_LEGACY_SCHEMA,
    CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA,
    CASTEP_NATIVE_OUTPUT_AUDIT_SUPPORTED_SCHEMAS,
    CASTEP_SAMPLED_BAND_EDGE_SCHEMA,
    DEFAULT_FERMI_TOLERANCE_EV,
    HARTREE_TO_EV,
    CastepBandKPoint,
    CastepBandsData,
    analyze_castep_sampled_band_edges,
    audit_castep_native_artifacts,
    parse_castep_bands_file,
    parse_castep_bands_text,
    parse_castep_output_file,
    parse_castep_output_text,
    write_castep_band_eigenvalues_csv,
    write_castep_gaussian_dos_csv,
)
from .cif import parse_crystal_cif, validate_crystal_cif_against_spec
from .copy_script import analyze_reviewed_copy_script
from .forcite_log import parse_forcite_convergence
from .structure_summary import parse_structure_summary
from .tagged_json import NEW_JSON_BEGIN, NEW_JSON_END, extract_any_tagged_json

__all__ = [
    "NEW_JSON_BEGIN",
    "NEW_JSON_END",
    "extract_any_tagged_json",
    "analyze_reviewed_copy_script",
    "parse_castep_energy",
    "CASTEP_ELECTRONIC_RESULT_SCHEMA",
    "CastepElectronicResultPayload",
    "CastepBandKPoint",
    "CastepBandsData",
    "audit_castep_native_artifacts",
    "analyze_castep_sampled_band_edges",
    "CASTEP_NATIVE_OUTPUT_AUDIT_LEGACY_SCHEMA",
    "CASTEP_NATIVE_OUTPUT_AUDIT_SCHEMA",
    "CASTEP_NATIVE_OUTPUT_AUDIT_SUPPORTED_SCHEMAS",
    "CASTEP_SAMPLED_BAND_EDGE_SCHEMA",
    "DEFAULT_FERMI_TOLERANCE_EV",
    "HARTREE_TO_EV",
    "parse_castep_bands_file",
    "parse_castep_bands_text",
    "validate_castep_electronic_result",
    "parse_castep_output_file",
    "parse_castep_output_text",
    "parse_crystal_cif",
    "parse_forcite_convergence",
    "parse_structure_summary",
    "validate_crystal_cif_against_spec",
    "validate_castep_geometry_result",
    "write_castep_band_eigenvalues_csv",
    "write_castep_gaussian_dos_csv",
]
