"""Command-line smoke runner for semiconductor live Materials Studio workflows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from . import server
from .specs.common import ExecutionMode


SCENARIO_REQUESTS = {
    "sic_mos": {
        "preview": "Build 4H-SiC MOS capacitor and export current view parameters and check whether the model is normal.",
        "hotload": (
            "Build 4H-SiC MOS capacitor and hot-load it in Materials Studio, "
            "export current view parameters and check whether the model is normal."
        ),
    },
    "sic_4h_c_face_mos": {
        "preview": (
            "Build an Al/SiO2/4H-SiC(000-1) C-face MOS capacitor and export gate-stack, "
            "interface, and view diagnostics."
        ),
        "hotload": (
            "Build an Al/SiO2/4H-SiC(000-1) C-face MOS capacitor and hot-load it in Materials Studio, "
            "export gate-stack, interface, and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_4h_oxide_interface": {
        "preview": (
            "Build a SiO2/4H-SiC(0001) Si-face interface and export semiconductor-oxide interface "
            "and view diagnostics."
        ),
        "hotload": (
            "Build a SiO2/4H-SiC(0001) Si-face interface and hot-load it in Materials Studio, "
            "export semiconductor-oxide interface and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_4h_c_face_oxide_interface": {
        "preview": (
            "Build a SiO2/4H-SiC(000-1) C-face interface and export semiconductor-oxide interface "
            "and view diagnostics."
        ),
        "hotload": (
            "Build a SiO2/4H-SiC(000-1) C-face interface and hot-load it in Materials Studio, "
            "export semiconductor-oxide interface and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_6h_mos": {
        "preview": (
            "Build an Al/SiO2/6H-SiC(0001) Si-face MOS capacitor and export gate-stack, "
            "interface, and view diagnostics."
        ),
        "hotload": (
            "Build an Al/SiO2/6H-SiC(0001) Si-face MOS capacitor and hot-load it in Materials Studio, "
            "export gate-stack, interface, and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_6h_c_face_mos": {
        "preview": (
            "Build an Al/SiO2/6H-SiC(000-1) C-face MOS capacitor and export gate-stack, "
            "interface, and view diagnostics."
        ),
        "hotload": (
            "Build an Al/SiO2/6H-SiC(000-1) C-face MOS capacitor and hot-load it in Materials Studio, "
            "export gate-stack, interface, and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_6h_oxide_interface": {
        "preview": (
            "Build a SiO2/6H-SiC(0001) Si-face interface and export semiconductor-oxide interface "
            "and view diagnostics."
        ),
        "hotload": (
            "Build a SiO2/6H-SiC(0001) Si-face interface and hot-load it in Materials Studio, "
            "export semiconductor-oxide interface and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_6h_c_face_oxide_interface": {
        "preview": (
            "Build a SiO2/6H-SiC(000-1) C-face interface and export semiconductor-oxide interface "
            "and view diagnostics."
        ),
        "hotload": (
            "Build a SiO2/6H-SiC(000-1) C-face interface and hot-load it in Materials Studio, "
            "export semiconductor-oxide interface and view diagnostics, and check whether the model is normal."
        ),
    },
    "mos2": {
        "preview": "Build MoS2 monolayer and export current view parameters and check whether the model is normal.",
        "hotload": (
            "Build MoS2 monolayer and hot-load it in Materials Studio, "
            "export current view parameters and check whether the model is normal."
        ),
    },
    "gan_hemt": {
        "preview": "Build an AlGaN/GaN HEMT heterostructure and export 2DEG diagnostics.",
        "hotload": (
            "Build an AlGaN/GaN HEMT heterostructure and hot-load it in Materials Studio, "
            "export 2DEG diagnostics and check whether the model is normal."
        ),
    },
    "gan_sapphire_interface": {
        "preview": "Build a GaN on sapphire interface scaffold and export interface scaffold diagnostics.",
        "hotload": (
            "Build a GaN on sapphire interface scaffold and hot-load it in Materials Studio, "
            "export interface scaffold diagnostics and check whether the model is normal."
        ),
    },
    "gan_sapphire_interface_cjk": {
        "preview": (
            "\u6784\u5efa\u6c2e\u5316\u9553\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a"
            "\u7684\u754c\u9762\u6a21\u578b\uff0c\u5bfc\u51fa\u754c\u9762"
            "\u811a\u624b\u67b6\u8bca\u65ad\u548c\u5404\u79cd\u89c6\u89d2"
            "\u6a21\u578b\u53c2\u6570\u3002"
        ),
        "hotload": (
            "\u6784\u5efa\u6c2e\u5316\u9553\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a"
            "\u7684\u754c\u9762\u6a21\u578b\u5e76\u70ed\u52a0\u8f7d\u5230 Materials Studio\uff0c"
            "\u5bfc\u51fa\u754c\u9762\u811a\u624b\u67b6\u8bca\u65ad\u5e76"
            "\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38\u3002"
        ),
    },
    "aln_sapphire_interface": {
        "preview": "Build an AlN on sapphire interface scaffold and export interface scaffold diagnostics.",
        "hotload": (
            "Build an AlN on sapphire interface scaffold and hot-load it in Materials Studio, "
            "export interface scaffold diagnostics and check whether the model is normal."
        ),
    },
    "aln_sapphire_interface_cjk": {
        "preview": (
            "\u6784\u5efa\u6c2e\u5316\u94dd\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a"
            "\u7684\u754c\u9762\u6a21\u578b\uff0c\u5bfc\u51fa\u754c\u9762"
            "\u811a\u624b\u67b6\u8bca\u65ad\u548c\u5404\u79cd\u89c6\u89d2"
            "\u6a21\u578b\u53c2\u6570\u3002"
        ),
        "hotload": (
            "\u6784\u5efa\u6c2e\u5316\u94dd\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a"
            "\u7684\u754c\u9762\u6a21\u578b\u5e76\u70ed\u52a0\u8f7d\u5230 Materials Studio\uff0c"
            "\u5bfc\u51fa\u754c\u9762\u811a\u624b\u67b6\u8bca\u65ad\u5e76"
            "\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38\u3002"
        ),
    },
    "p_gan_hemt": {
        "preview": "Build a p-GaN gate AlGaN/GaN HEMT and export 2DEG and p-GaN gate diagnostics.",
        "hotload": (
            "Build a p-GaN gate AlGaN/GaN HEMT and hot-load it in Materials Studio, "
            "export 2DEG, p-GaN gate, and view diagnostics and check whether the model is normal."
        ),
    },
    "silicon": {
        "preview": "Build silicon crystal as a 2x1x1 supercell and export current view parameters.",
        "hotload": (
            "Build silicon crystal as a 2x1x1 supercell and hot-load it in Materials Studio, "
            "export current view parameters and check whether the model is normal."
        ),
    },
    "diamond": {
        "preview": "Build diamond semiconductor crystal and export current view parameters.",
        "hotload": (
            "Build diamond semiconductor crystal and hot-load it in Materials Studio, "
            "export current view parameters and check whether the model is normal."
        ),
    },
    "silicon_pn_junction": {
        "preview": (
            "Build a silicon p-n junction, export doping diagnostics and current view parameters, "
            "and check whether the model is normal."
        ),
        "hotload": (
            "Build a silicon p-n junction and hot-load it in Materials Studio, "
            "export doping diagnostics and current view parameters, and check whether the model is normal."
        ),
    },
    "gaas": {
        "preview": "Build GaAs zinc blende semiconductor crystal and export current view parameters.",
        "hotload": (
            "Build GaAs zinc blende semiconductor crystal and hot-load it in Materials Studio, "
            "export current view parameters and check whether the model is normal."
        ),
    },
    "beta_ga2o3_contact": {
        "preview": "Build an Au/beta-Ga2O3(010) Schottky contact and export contact and view diagnostics.",
        "hotload": (
            "Build an Au/beta-Ga2O3(010) Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_3c_slab": {
        "preview": "Build a 3C-SiC(001) Si-face slab and export surface and all-view diagnostics.",
        "hotload": (
            "Build a 3C-SiC(001) Si-face slab and hot-load it in Materials Studio, "
            "export surface and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_3c_c_face_slab": {
        "preview": "Build a 3C-SiC(00-1) C-face slab and export surface and all-view diagnostics.",
        "hotload": (
            "Build a 3C-SiC(00-1) C-face slab and hot-load it in Materials Studio, "
            "export surface and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_3c_contact": {
        "preview": "Build an Au/3C-SiC(001) Si-face Schottky contact and export contact and view diagnostics.",
        "hotload": (
            "Build an Au/3C-SiC(001) Si-face Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_3c_c_face_contact": {
        "preview": (
            "Build an Au/3C-SiC(00-1) C-face Schottky contact and export contact and view diagnostics."
        ),
        "hotload": (
            "Build an Au/3C-SiC(00-1) C-face Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_4h_contact": {
        "preview": "Build an Au/4H-SiC(0001) Si-face Schottky contact and export contact and view diagnostics.",
        "hotload": (
            "Build an Au/4H-SiC(0001) Si-face Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_4h_c_face_contact": {
        "preview": (
            "Build an Au/4H-SiC(000-1) C-face Schottky contact and export contact and view diagnostics."
        ),
        "hotload": (
            "Build an Au/4H-SiC(000-1) C-face Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_4h_slab": {
        "preview": "Build a 4H-SiC(0001) Si-face slab and export surface and all-view diagnostics.",
        "hotload": (
            "Build a 4H-SiC(0001) Si-face slab and hot-load it in Materials Studio, "
            "export surface and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_4h_c_face_slab": {
        "preview": "Build a 4H-SiC(000-1) C-face slab and export surface and all-view diagnostics.",
        "hotload": (
            "Build a 4H-SiC(000-1) C-face slab and hot-load it in Materials Studio, "
            "export surface and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_6h_slab": {
        "preview": "Build a 6H-SiC(0001) Si-face slab and export surface and all-view diagnostics.",
        "hotload": (
            "Build a 6H-SiC(0001) Si-face slab and hot-load it in Materials Studio, "
            "export surface and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_6h_c_face_slab": {
        "preview": "Build a 6H-SiC(000-1) C-face slab and export surface and all-view diagnostics.",
        "hotload": (
            "Build a 6H-SiC(000-1) C-face slab and hot-load it in Materials Studio, "
            "export surface and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_6h_contact": {
        "preview": "Build an Au/6H-SiC(0001) Si-face Schottky contact and export contact and view diagnostics.",
        "hotload": (
            "Build an Au/6H-SiC(0001) Si-face Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "sic_6h_c_face_contact": {
        "preview": (
            "Build an Au/6H-SiC(000-1) C-face Schottky contact and export contact and view diagnostics."
        ),
        "hotload": (
            "Build an Au/6H-SiC(000-1) C-face Schottky contact and hot-load it in Materials Studio, "
            "export contact and all-view diagnostics and check whether the model is normal."
        ),
    },
    "mapbi3_alloy_cjk": {
        "preview": (
            "\u5c06 MAPbI3 \u4e2d 33% \u7898\u66ff\u6362\u4e3a\u6eb4\uff0c\u5bfc\u51fa\u5404\u4e2a\u89c6\u89d2"
            "\u6a21\u578b\u53c2\u6570\u548c\u5408\u91d1\u8bca\u65ad\uff0c\u5e76\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38\u3002"
        ),
        "hotload": (
            "\u5c06 MAPbI3 \u4e2d 33% \u7898\u66ff\u6362\u4e3a\u6eb4\u5e76\u70ed\u52a0\u8f7d\u5230\u5f53\u524d Materials Studio "
            "\u7a97\u53e3\uff0c\u5bfc\u51fa\u5404\u4e2a\u89c6\u89d2\u6a21\u578b\u53c2\u6570\u548c\u5408\u91d1\u8bca\u65ad\uff0c"
            "\u5e76\u68c0\u67e5\u6a21\u578b\u662f\u5426\u6b63\u5e38\u3002"
        ),
    },
}


SCENARIO_VIRTUAL_TEMPLATE_IDS = {
    "aln_sapphire_interface": "aluminum_nitride_on_sapphire_interface_scaffold",
    "aln_sapphire_interface_cjk": "aluminum_nitride_on_sapphire_interface_scaffold",
    "gan_sapphire_interface": "gallium_nitride_on_sapphire_interface_scaffold",
    "gan_sapphire_interface_cjk": "gallium_nitride_on_sapphire_interface_scaffold",
    "p_gan_hemt": "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_p_gan_gate",
    "beta_ga2o3_contact": "metal_beta_gallium_oxide_010_schottky_contact",
    "sic_mos": "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor",
    "sic_3c_slab": "silicon_carbide_3c_001_si_face_slab",
    "sic_3c_c_face_slab": "silicon_carbide_3c_00m1_c_face_slab",
    "sic_3c_contact": "metal_silicon_carbide_3c_001_si_face_schottky_contact",
    "sic_3c_c_face_contact": "metal_silicon_carbide_3c_00m1_c_face_schottky_contact",
    "sic_4h_contact": "metal_silicon_carbide_4h_0001_schottky_contact",
    "sic_4h_c_face_contact": "metal_silicon_carbide_4h_000m1_c_face_schottky_contact",
    "sic_4h_slab": "silicon_carbide_4h_0001_si_face_slab",
    "sic_4h_c_face_slab": "silicon_carbide_4h_000m1_c_face_slab",
    "sic_4h_oxide_interface": "silicon_dioxide_silicon_carbide_4h_0001_si_face_interface",
    "sic_4h_c_face_oxide_interface": "silicon_dioxide_silicon_carbide_4h_000m1_c_face_interface",
    "sic_4h_c_face_mos": "aluminum_silicon_dioxide_silicon_carbide_4h_000m1_c_face_mos_capacitor",
    "sic_6h_slab": "silicon_carbide_6h_0001_si_face_slab",
    "sic_6h_c_face_slab": "silicon_carbide_6h_000m1_c_face_slab",
    "sic_6h_contact": "metal_silicon_carbide_6h_0001_schottky_contact",
    "sic_6h_c_face_contact": "metal_silicon_carbide_6h_000m1_c_face_schottky_contact",
    "sic_6h_oxide_interface": "silicon_dioxide_silicon_carbide_6h_0001_interface",
    "sic_6h_c_face_oxide_interface": "silicon_dioxide_silicon_carbide_6h_000m1_c_face_interface",
    "sic_6h_mos": "aluminum_silicon_dioxide_silicon_carbide_6h_mos_capacitor",
    "sic_6h_c_face_mos": "aluminum_silicon_dioxide_silicon_carbide_6h_000m1_c_face_mos_capacitor",
}


SCENARIO_EXPECTATIONS = {
    "sic_mos": {
        "row_counts": {
            "semiconductor_gate_stack": 1,
            "semiconductor_heterostructure": 1,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_gate_stack_csv",
            "semiconductor_heterostructure_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "mos2": {
        "row_counts": {
            "semiconductor_surface_model": 1,
            "semiconductor_surface_polarity": 1,
            "semiconductor_surface_termination": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_surface_model_csv",
            "semiconductor_surface_polarity_csv",
            "semiconductor_surface_termination_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "gan_hemt": {
        "row_counts": {
            "semiconductor_polarization_2deg": 1,
            "semiconductor_band_alignment": 1,
            "semiconductor_quantum_well": 1,
            "semiconductor_heterostructure": 1,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_alloy": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_polarization_2deg_csv",
            "semiconductor_band_alignment_csv",
            "semiconductor_quantum_well_csv",
            "semiconductor_heterostructure_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_alloy_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "gan_sapphire_interface": {
        "row_counts": {
            "semiconductor_interface_scaffold": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_interface_scaffold_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "gan_sapphire_interface_cjk": {
        "row_counts": {
            "semiconductor_interface_scaffold": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_interface_scaffold_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "aln_sapphire_interface": {
        "row_counts": {
            "semiconductor_interface_scaffold": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_interface_scaffold_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "aln_sapphire_interface_cjk": {
        "row_counts": {
            "semiconductor_interface_scaffold": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_interface_scaffold_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "p_gan_hemt": {
        "row_counts": {
            "semiconductor_p_gan_gate_cap": 1,
            "semiconductor_polarization_2deg": 1,
            "semiconductor_band_alignment": 1,
            "semiconductor_quantum_well": 1,
            "semiconductor_heterostructure": 1,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_alloy": 1,
            "semiconductor_dopants": 1,
            "semiconductor_dopant_sites": 1,
            "semiconductor_finite_size": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_p_gan_gate_cap_csv",
            "semiconductor_polarization_2deg_csv",
            "semiconductor_band_alignment_csv",
            "semiconductor_quantum_well_csv",
            "semiconductor_heterostructure_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_alloy_csv",
            "semiconductor_dopants_csv",
            "semiconductor_dopant_sites_csv",
            "semiconductor_finite_size_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "silicon": {
        "row_counts": {
            "semiconductor_lattice": 1,
            "semiconductor_composition": 1,
            "semiconductor_local_environment": 1,
            "semiconductor_neighbor_pairs": 1,
            "semiconductor_reciprocal_lattice": 1,
            "semiconductor_band_path": 1,
            "semiconductor_calculation_preflight": 1,
            "semiconductor_calculation_readiness": 1,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_lattice_csv",
            "semiconductor_composition_csv",
            "semiconductor_local_environment_csv",
            "semiconductor_neighbor_pairs_csv",
            "semiconductor_reciprocal_lattice_csv",
            "semiconductor_band_path_csv",
            "semiconductor_calculation_preflight_csv",
            "semiconductor_calculation_readiness_csv",
        ],
    },
    "diamond": {
        "row_counts": {
            "semiconductor_lattice": 1,
            "semiconductor_composition": 1,
            "semiconductor_local_environment": 1,
            "semiconductor_neighbor_pairs": 1,
            "semiconductor_reciprocal_lattice": 1,
            "semiconductor_band_path": 1,
            "semiconductor_calculation_preflight": 1,
            "semiconductor_calculation_readiness": 1,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_lattice_csv",
            "semiconductor_composition_csv",
            "semiconductor_local_environment_csv",
            "semiconductor_neighbor_pairs_csv",
            "semiconductor_reciprocal_lattice_csv",
            "semiconductor_band_path_csv",
            "semiconductor_calculation_preflight_csv",
            "semiconductor_calculation_readiness_csv",
        ],
    },
    "silicon_pn_junction": {
        "row_counts": {
            "semiconductor_junctions": 1,
            "semiconductor_dopants": 2,
            "semiconductor_dopant_sites": 2,
            "semiconductor_finite_size": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_junctions_csv",
            "semiconductor_dopants_csv",
            "semiconductor_dopant_sites_csv",
            "semiconductor_finite_size_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "gaas": {
        "row_counts": {
            "semiconductor_lattice": 1,
            "semiconductor_composition": 1,
            "semiconductor_local_environment": 1,
            "semiconductor_neighbor_pairs": 1,
            "semiconductor_reciprocal_lattice": 1,
            "semiconductor_band_path": 1,
            "semiconductor_calculation_preflight": 1,
            "semiconductor_calculation_readiness": 1,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_lattice_csv",
            "semiconductor_composition_csv",
            "semiconductor_local_environment_csv",
            "semiconductor_neighbor_pairs_csv",
            "semiconductor_reciprocal_lattice_csv",
            "semiconductor_band_path_csv",
            "semiconductor_calculation_preflight_csv",
            "semiconductor_calculation_readiness_csv",
        ],
    },
    "beta_ga2o3_contact": {
        "row_counts": {
            "semiconductor_contact": 2,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_surface_polarity": 1,
            "semiconductor_calculation_preflight": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_contact_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_surface_polarity_csv",
            "semiconductor_calculation_preflight_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "sic_4h_contact": {
        "row_counts": {
            "semiconductor_contact": 2,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_surface_polarity": 1,
            "semiconductor_calculation_preflight": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_contact_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_surface_polarity_csv",
            "semiconductor_calculation_preflight_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "sic_6h_mos": {
        "row_counts": {
            "semiconductor_gate_stack": 1,
            "semiconductor_heterostructure": 1,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_oxide_interface_geometry": 39,
            "semiconductor_oxide_interface_health": 3,
            "semiconductor_calculation_preflight": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_gate_stack_csv",
            "semiconductor_heterostructure_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_oxide_interface_geometry_csv",
            "semiconductor_oxide_interface_health_csv",
            "semiconductor_calculation_preflight_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "sic_6h_oxide_interface": {
        "row_counts": {
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_oxide_interface_geometry": 38,
            "semiconductor_oxide_interface_health": 3,
            "semiconductor_calculation_preflight": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_oxide_interface_geometry_csv",
            "semiconductor_oxide_interface_health_csv",
            "semiconductor_calculation_preflight_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "sic_6h_slab": {
        "row_counts": {
            "semiconductor_surface_model": 1,
            "semiconductor_surface_termination": 1,
            "semiconductor_surface_polarity": 1,
            "semiconductor_calculation_preflight": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_surface_model_csv",
            "semiconductor_surface_termination_csv",
            "semiconductor_surface_polarity_csv",
            "semiconductor_calculation_preflight_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "sic_6h_contact": {
        "row_counts": {
            "semiconductor_contact": 2,
            "semiconductor_interface_profile": 1,
            "semiconductor_interface_quality": 1,
            "semiconductor_surface_polarity": 1,
            "semiconductor_calculation_preflight": 1,
            "requested_diagnostic_focus_status": 2,
            "view_summary": 1,
            "view_quality": 1,
            "view_projections": 1,
        },
        "files": [
            "semiconductor_contact_csv",
            "semiconductor_interface_profile_csv",
            "semiconductor_interface_quality_csv",
            "semiconductor_surface_polarity_csv",
            "semiconductor_calculation_preflight_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
    "mapbi3_alloy_cjk": {
        "row_counts": {
            "semiconductor_alloy": 1,
            "semiconductor_lattice": 1,
            "semiconductor_composition": 1,
            "semiconductor_local_environment": 1,
            "semiconductor_neighbor_pairs": 1,
            "semiconductor_reciprocal_lattice": 1,
            "semiconductor_band_path": 1,
            "semiconductor_calculation_preflight": 1,
            "semiconductor_calculation_readiness": 1,
            "semiconductor_normality_diagnosis": 1,
            "requested_diagnostic_focus_status": 5,
            "view_summary": 7,
            "view_quality": 7,
            "view_projections": 84,
        },
        "files": [
            "semiconductor_alloy_csv",
            "semiconductor_lattice_csv",
            "semiconductor_composition_csv",
            "semiconductor_local_environment_csv",
            "semiconductor_neighbor_pairs_csv",
            "semiconductor_reciprocal_lattice_csv",
            "semiconductor_band_path_csv",
            "semiconductor_calculation_preflight_csv",
            "semiconductor_calculation_readiness_csv",
            "semiconductor_normality_diagnosis_csv",
            "requested_diagnostic_focus_status_json",
        ],
    },
}

for _target_scenario, _source_scenario in (
    ("sic_3c_slab", "sic_6h_slab"),
    ("sic_3c_c_face_slab", "sic_6h_slab"),
    ("sic_3c_contact", "sic_4h_contact"),
    ("sic_3c_c_face_contact", "sic_4h_contact"),
    ("sic_4h_c_face_contact", "sic_4h_contact"),
    ("sic_4h_slab", "sic_6h_slab"),
    ("sic_4h_c_face_slab", "sic_6h_slab"),
    ("sic_4h_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_4h_c_face_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_mos", "sic_6h_mos"),
    ("sic_4h_c_face_mos", "sic_6h_mos"),
    ("sic_6h_c_face_slab", "sic_6h_slab"),
    ("sic_6h_c_face_contact", "sic_6h_contact"),
    ("sic_6h_c_face_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_6h_c_face_mos", "sic_6h_mos"),
):
    _source_expectation = SCENARIO_EXPECTATIONS[_source_scenario]
    SCENARIO_EXPECTATIONS[_target_scenario] = {
        "row_counts": dict(_source_expectation["row_counts"]),
        "files": list(_source_expectation["files"]),
    }
del _target_scenario, _source_scenario, _source_expectation


FOLLOW_UP_REQUESTS = {
    "silicon": {
        "p_dopant": (
            "Make it n-type with one P dopant and hot-load it in Materials Studio, "
            "export front top isometric view parameters, dopant diagnostics, and check whether the model is normal."
        ),
        "vacancy": (
            "Create a Si vacancy and hot-load it in Materials Studio, "
            "export front top isometric view parameters, defect diagnostics, and check whether the model is normal."
        ),
    },
    "diamond": {
        "b_dopant": (
            "Make p-type diamond with one B dopant and hot-load it in Materials Studio, "
            "export front top isometric view parameters, dopant diagnostics, and check whether the model is normal."
        ),
        "c_vacancy": (
            "Create a C vacancy in diamond and hot-load it in Materials Studio, "
            "export front top isometric view parameters, defect diagnostics, and check whether the model is normal."
        ),
    },
    "mos2": {
        "s_vacancy": (
            "Create an S vacancy and hot-load it in Materials Studio, "
            "export front top isometric view parameters, defect diagnostics, and check whether the model is normal."
        ),
        "cl_dopant": (
            "Dope the S sublattice with Cl and hot-load it in Materials Studio, "
            "export front top isometric view parameters, dopant diagnostics, and check whether the model is normal."
        ),
    },
    "gan_hemt": {
        "mg_acceptor": (
            "Make the GaN region p-type with Mg_Ga dopant and hot-load it in Materials Studio, "
            "export front top isometric view parameters, dopant diagnostics, and check whether the model is normal."
        ),
    },
    "gan_sapphire_interface": {
        "interface_gap_2p5": (
            "Set the semiconductor interface scaffold gap to 2.5 angstrom and hot-load it in Materials Studio, "
            "export front top isometric view parameters and interface scaffold diagnostics, "
            "and check whether the model is normal."
        ),
    },
    "gan_sapphire_interface_cjk": {
        "interface_gap_2p5": (
            "\u628a\u754c\u9762\u95f4\u8ddd\u8c03\u5230 2.5 \u57c3\u5e76"
            "\u70ed\u52a0\u8f7d\u5230 Materials Studio\uff0c\u5bfc\u51fa"
            "\u6b63\u89c6\u3001\u4fef\u89c6\u548c\u7b49\u8f74\u6d4b"
            "\u89c6\u89d2\u53c2\u6570\u4ee5\u53ca\u754c\u9762\u811a"
            "\u624b\u67b6\u8bca\u65ad\uff0c\u68c0\u67e5\u6a21\u578b"
            "\u662f\u5426\u6b63\u5e38\u3002"
        ),
    },
    "aln_sapphire_interface": {
        "interface_gap_2p5": (
            "Set the semiconductor interface scaffold gap to 2.5 angstrom and hot-load it in Materials Studio, "
            "export front top isometric view parameters and interface scaffold diagnostics, "
            "and check whether the model is normal."
        ),
    },
    "aln_sapphire_interface_cjk": {
        "interface_gap_2p5": (
            "\u628a\u754c\u9762\u95f4\u8ddd\u8c03\u5230 2.5 \u57c3\u5e76"
            "\u70ed\u52a0\u8f7d\u5230 Materials Studio\uff0c\u5bfc\u51fa"
            "\u6b63\u89c6\u3001\u4fef\u89c6\u548c\u7b49\u8f74\u6d4b"
            "\u89c6\u89d2\u53c2\u6570\u4ee5\u53ca\u754c\u9762\u811a"
            "\u624b\u67b6\u8bca\u65ad\uff0c\u68c0\u67e5\u6a21\u578b"
            "\u662f\u5426\u6b63\u5e38\u3002"
        ),
    },
    "p_gan_hemt": {
        "gate_thickness_2nm": (
            "Set the p-GaN gate thickness to 2 nm and hot-load it in Materials Studio, "
            "export front top isometric view parameters, p-GaN gate diagnostics, and check whether the model is normal."
        ),
    },
    "gaas": {
        "si_ga_dopant": (
            "Make n-type GaAs with Si_Ga dopant and hot-load it in Materials Studio, "
            "export front top isometric view parameters, dopant diagnostics, and check whether the model is normal."
        ),
        "as_vacancy": (
            "Create an As vacancy and hot-load it in Materials Studio, "
            "export front top isometric view parameters, defect diagnostics, and check whether the model is normal."
        ),
    },
    "sic_6h_mos": {
        "interface_gaps_2p0_2p5": (
            "Set the semiconductor-oxide interface gap to 2.0 angstrom and the oxide-gate interface gap "
            "to 2.5 angstrom, then hot-load it in Materials Studio and export gate-stack, interface, "
            "and view diagnostics."
        ),
    },
    "sic_6h_c_face_mos": {
        "interface_gaps_2p0_2p5": (
            "Set the semiconductor-oxide interface gap to 2.0 angstrom and the oxide-gate interface gap "
            "to 2.5 angstrom, then hot-load it in Materials Studio and export gate-stack, interface, "
            "and view diagnostics."
        ),
    },
    "sic_6h_oxide_interface": {
        "o_vacancy": (
            "Create an O vacancy and hot-load it in Materials Studio, export defect, "
            "semiconductor-oxide interface, and view diagnostics, and check whether the model is normal."
        ),
    },
    "sic_6h_c_face_oxide_interface": {
        "o_vacancy": (
            "Create an O vacancy and hot-load it in Materials Studio, export defect, "
            "semiconductor-oxide interface, and view diagnostics, and check whether the model is normal."
        ),
    },
}

for _target_scenario, _source_scenario in (
    ("sic_mos", "sic_6h_mos"),
    ("sic_4h_c_face_mos", "sic_6h_mos"),
    ("sic_4h_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_4h_c_face_oxide_interface", "sic_6h_oxide_interface"),
):
    FOLLOW_UP_REQUESTS[_target_scenario] = dict(FOLLOW_UP_REQUESTS[_source_scenario])
del _target_scenario, _source_scenario


FOLLOW_UP_EXPECTATIONS = {
    "silicon": {
        "p_dopant": {
            "row_counts": {
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "semiconductor_carrier_intents": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_dopants_csv",
                "semiconductor_dopant_sites_csv",
                "semiconductor_carrier_intents_csv",
                "semiconductor_finite_size_csv",
            ],
        },
        "vacancy": {
            "row_counts": {
                "semiconductor_defects": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_defects_csv", "semiconductor_finite_size_csv"],
        },
    },
    "diamond": {
        "b_dopant": {
            "row_counts": {
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "semiconductor_carrier_intents": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_dopants_csv",
                "semiconductor_dopant_sites_csv",
                "semiconductor_carrier_intents_csv",
                "semiconductor_finite_size_csv",
            ],
        },
        "c_vacancy": {
            "row_counts": {
                "semiconductor_defects": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_defects_csv", "semiconductor_finite_size_csv"],
        },
    },
    "mos2": {
        "s_vacancy": {
            "row_counts": {
                "semiconductor_defects": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_defects_csv", "semiconductor_finite_size_csv"],
        },
        "cl_dopant": {
            "row_counts": {
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "semiconductor_carrier_intents": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_dopants_csv",
                "semiconductor_dopant_sites_csv",
                "semiconductor_carrier_intents_csv",
                "semiconductor_finite_size_csv",
            ],
        },
    },
    "gan_hemt": {
        "mg_acceptor": {
            "row_counts": {
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "semiconductor_carrier_intents": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_dopants_csv",
                "semiconductor_dopant_sites_csv",
                "semiconductor_carrier_intents_csv",
                "semiconductor_finite_size_csv",
            ],
        },
    },
    "gan_sapphire_interface": {
        "interface_gap_2p5": {
            "row_counts": {
                "semiconductor_interface_scaffold": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_interface_scaffold_csv"],
        },
    },
    "gan_sapphire_interface_cjk": {
        "interface_gap_2p5": {
            "row_counts": {
                "semiconductor_interface_scaffold": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_interface_scaffold_csv"],
        },
    },
    "aln_sapphire_interface": {
        "interface_gap_2p5": {
            "row_counts": {
                "semiconductor_interface_scaffold": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_interface_scaffold_csv"],
        },
    },
    "aln_sapphire_interface_cjk": {
        "interface_gap_2p5": {
            "row_counts": {
                "semiconductor_interface_scaffold": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_interface_scaffold_csv"],
        },
    },
    "p_gan_hemt": {
        "gate_thickness_2nm": {
            "row_counts": {
                "semiconductor_p_gan_gate_cap": 1,
                "semiconductor_polarization_2deg": 1,
                "semiconductor_quantum_well": 1,
                "semiconductor_band_alignment": 1,
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_p_gan_gate_cap_csv",
                "semiconductor_polarization_2deg_csv",
                "semiconductor_quantum_well_csv",
                "semiconductor_band_alignment_csv",
                "semiconductor_dopants_csv",
                "semiconductor_dopant_sites_csv",
            ],
        },
    },
    "gaas": {
        "si_ga_dopant": {
            "row_counts": {
                "semiconductor_dopants": 1,
                "semiconductor_dopant_sites": 1,
                "semiconductor_carrier_intents": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_dopants_csv",
                "semiconductor_dopant_sites_csv",
                "semiconductor_carrier_intents_csv",
                "semiconductor_finite_size_csv",
            ],
        },
        "as_vacancy": {
            "row_counts": {
                "semiconductor_defects": 1,
                "semiconductor_finite_size": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": ["semiconductor_defects_csv", "semiconductor_finite_size_csv"],
        },
    },
    "sic_6h_mos": {
        "interface_gaps_2p0_2p5": {
            "row_counts": {
                "semiconductor_gate_stack": 3,
                "semiconductor_interface_profile": 1,
                "semiconductor_interface_quality": 3,
                "semiconductor_oxide_interface_geometry": 39,
                "semiconductor_oxide_interface_health": 3,
                "requested_diagnostic_focus_status": 1,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_gate_stack_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_oxide_interface_geometry_csv",
                "semiconductor_oxide_interface_health_csv",
            ],
        },
    },
    "sic_6h_oxide_interface": {
        "o_vacancy": {
            "row_counts": {
                "semiconductor_defects": 1,
                "semiconductor_finite_size": 1,
                "semiconductor_interface_profile": 1,
                "semiconductor_interface_quality": 1,
                "semiconductor_oxide_interface_geometry": 33,
                "semiconductor_oxide_interface_health": 4,
                "requested_diagnostic_focus_status": 3,
                "view_summary": 1,
                "view_quality": 1,
                "view_projections": 1,
            },
            "files": [
                "semiconductor_defects_csv",
                "semiconductor_finite_size_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_oxide_interface_geometry_csv",
                "semiconductor_oxide_interface_health_csv",
            ],
        },
    },
}

for _target_scenario, _source_scenario in (
    ("sic_mos", "sic_6h_mos"),
    ("sic_4h_c_face_mos", "sic_6h_mos"),
    ("sic_4h_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_4h_c_face_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_6h_c_face_oxide_interface", "sic_6h_oxide_interface"),
    ("sic_6h_c_face_mos", "sic_6h_mos"),
):
    FOLLOW_UP_EXPECTATIONS[_target_scenario] = {
        preset: {
            "row_counts": dict(expectation["row_counts"]),
            "files": list(expectation["files"]),
        }
        for preset, expectation in FOLLOW_UP_EXPECTATIONS[_source_scenario].items()
    }
del _target_scenario, _source_scenario


def default_request_for_scenario(scenario: str, *, hotload: bool = False) -> str:
    """Return a deterministic semiconductor smoke request."""

    try:
        requests = SCENARIO_REQUESTS[scenario]
    except KeyError as exc:
        raise ValueError(f"Unknown live smoke scenario: {scenario}") from exc
    return requests["hotload" if hotload else "preview"]


def default_follow_up_request_for_scenario(scenario: str, preset: str) -> str:
    """Return a deterministic semiconductor follow-up edit request."""

    try:
        requests = FOLLOW_UP_REQUESTS[scenario]
    except KeyError as exc:
        available = ", ".join(sorted(FOLLOW_UP_REQUESTS))
        raise ValueError(f"Scenario {scenario!r} has no follow-up presets. Available scenarios: {available}") from exc
    try:
        return requests[preset]
    except KeyError as exc:
        available = ", ".join(sorted(requests))
        raise ValueError(f"Unknown follow-up preset {preset!r} for scenario {scenario!r}. Available presets: {available}") from exc


def _effective_views_from_live_response(
    live: dict[str, Any],
    explicit_views: list[str] | None,
) -> list[str] | None:
    """Keep bundle re-export aligned with the views selected by the live request."""

    if explicit_views is not None:
        return list(explicit_views)
    report = live.get("modeling_report") if isinstance(live.get("modeling_report"), dict) else {}
    summary = live.get("live_summary") if isinstance(live.get("live_summary"), dict) else {}
    if not summary and isinstance(report.get("live_summary"), dict):
        summary = report["live_summary"]
    for candidate in (
        summary.get("live_request_requested_views"),
        summary.get("view_request_exported_names"),
        summary.get("view_names"),
        live.get("live_request_requested_views"),
        live.get("view_request_exported_names"),
    ):
        if isinstance(candidate, list) and candidate:
            return list(dict.fromkeys(str(value) for value in candidate if str(value)))

    audit = live.get("view_audit") if isinstance(live.get("view_audit"), dict) else {}
    audit_views = [
        str(view.get("name"))
        for view in audit.get("views", []) or []
        if isinstance(view, dict) and view.get("name")
    ]
    return list(dict.fromkeys(audit_views)) or None


_POSTEXECUTION_ACTIVATE_PAYLOAD_KEYS = frozenset(
    {"project_id", "revision", "take_snapshot", "views", "working_dir"}
)
_POSTEXECUTION_OPEN_PAYLOAD_KEYS = frozenset(
    {
        "structure_path",
        "project_id",
        "revision",
        "take_snapshot",
        "export_view_audit",
        "reuse_existing_window_only",
        "views",
        "working_dir",
        "fit_to_view_after_open",
        "prepare_view_replay_after_open",
    }
)
_PREEXECUTION_APPLY_PAYLOAD_KEYS = frozenset(
    {
        "project_id",
        "execution_mode",
        "open_in_gui",
        "take_snapshot",
        "fit_to_view_after_open",
        "prepare_view_replay_after_open",
        "export_view_audit",
        "views",
        "working_dir",
        "timeout_seconds",
        "response_mode",
    }
)


def _path_identity(value: Any) -> str | None:
    """Return a stable local path identity without requiring the path to exist."""

    if not isinstance(value, (str, Path)) or not str(value).strip():
        return None
    try:
        return os.path.normcase(str(Path(value).expanduser().resolve()))
    except (OSError, RuntimeError, ValueError):
        return None


def _revision_identity(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        revision = int(value)
    except (TypeError, ValueError):
        return None
    return revision if revision >= 0 else None


def _execution_result_succeeded(response: dict[str, Any]) -> bool:
    """Accept full or compact execution receipts, but never conflicting ones."""

    receipts = [
        response[field]
        for field in ("result", "execution_result")
        if field in response
    ]
    return bool(receipts) and all(
        isinstance(receipt, dict) and receipt.get("success") is True
        for receipt in receipts
    )


def _continuation_failure(reason: str, **details: Any) -> dict[str, Any]:
    return {"type": reason, **details}


def _validate_preexecution_execution_block(
    response: dict[str, Any],
    *,
    working_dir: str | None,
) -> dict[str, Any]:
    """Validate one exact server-issued activate/apply continuation contract."""

    failures: list[dict[str, Any]] = []

    def require(condition: bool, reason: str, **details: Any) -> None:
        if not condition:
            failures.append(_continuation_failure(reason, **details))

    project_id = response.get("project_id")
    project_id = project_id if isinstance(project_id, str) and project_id else None
    response_revisions = {
        key: _revision_identity(response.get(key))
        for key in ("revision", "new_revision")
        if response.get(key) is not None
    }
    revision_values = {value for value in response_revisions.values() if value is not None}
    revision = next(iter(revision_values)) if len(revision_values) == 1 else None
    require(project_id is not None, "response_project_id_missing")
    require(bool(response_revisions), "response_revision_missing")
    require(
        len(revision_values) == 1 and all(value is not None for value in response_revisions.values()),
        "response_revision_identity_invalid",
        observed=response_revisions,
    )

    expected_response_fields = {
        "ok": False,
        "status": "gui_activation_required_before_execution",
        "execution_started": False,
        "execution_deferred": True,
        "runner_invoked": False,
        "structure_materialization_started": False,
        "gui_input_started": False,
        "gui_process_launched": False,
        "structure_reopened": False,
        "prepared_revision_retained": True,
        "gui_activation_retry_tool": "material_studio_gui_activate",
        "execution_retry_tool": "material_studio_gui_apply_current_revision",
    }
    for field, expected in expected_response_fields.items():
        observed = response.get(field)
        require(
            observed == expected and type(observed) is type(expected),
            "preexecution_response_field_mismatch",
            field=field,
            expected=expected,
            observed=observed,
        )

    execution_mode = response.get("execution_mode") or _dict(
        response.get("modeling_report")
    ).get("execution_mode")
    execution_mode = getattr(execution_mode, "value", execution_mode)
    require(
        execution_mode == ExecutionMode.EXECUTE.value,
        "preexecution_response_not_execute",
        observed=execution_mode,
    )
    require(
        response.get("result") is None or response.get("result") == {},
        "preexecution_result_already_present",
    )
    require(
        response.get("execution_result") is None
        or response.get("execution_result") == {},
        "preexecution_compact_result_already_present",
    )
    execution_transaction = _dict(response.get("execution_transaction"))
    require(
        execution_transaction.get("execution_started") is not True
        and execution_transaction.get("execution_completed") is not True,
        "preexecution_transaction_already_started",
    )
    require(
        not _dict(response.get("execution_attempt")),
        "preexecution_attempt_already_present",
    )

    block = _dict(response.get("gui_preexecution_block"))
    require(bool(block), "preexecution_block_missing")
    expected_block_fields = {
        "blocked": True,
        "reason": "target_window_activation_required",
        "recommended_tool": "material_studio_gui_activate",
        "recommended_action": "activate_exact_existing_window_before_revision_execution",
        "execution_retry_tool": "material_studio_gui_apply_current_revision",
        "same_window_required": True,
        "reuse_existing_window_only": True,
        "gui_process_launch_allowed": False,
    }
    for field, expected in expected_block_fields.items():
        observed = block.get(field)
        require(
            observed == expected and type(observed) is type(expected),
            "preexecution_block_field_mismatch",
            field=field,
            expected=expected,
            observed=observed,
        )
    require(
        block.get("project_id") == project_id,
        "preexecution_block_project_mismatch",
        expected=project_id,
        observed=block.get("project_id"),
    )
    require(
        _revision_identity(block.get("revision")) == revision,
        "preexecution_block_revision_mismatch",
        expected=revision,
        observed=block.get("revision"),
    )

    activation_payload = _dict(response.get("gui_activation_retry_payload"))
    execution_payload = _dict(response.get("execution_retry_payload"))
    require(bool(activation_payload), "activation_payload_missing")
    require(bool(execution_payload), "execution_payload_missing")
    require(
        not (set(activation_payload) - _POSTEXECUTION_ACTIVATE_PAYLOAD_KEYS),
        "activation_payload_has_unexpected_fields",
        fields=sorted(set(activation_payload) - _POSTEXECUTION_ACTIVATE_PAYLOAD_KEYS),
    )
    require(
        not (set(execution_payload) - _PREEXECUTION_APPLY_PAYLOAD_KEYS),
        "execution_payload_has_unexpected_fields",
        fields=sorted(set(execution_payload) - _PREEXECUTION_APPLY_PAYLOAD_KEYS),
    )
    require(
        activation_payload == _dict(block.get("activation_payload")),
        "activation_payload_not_exact_block_payload",
    )
    require(
        execution_payload == _dict(block.get("execution_retry_payload")),
        "execution_payload_not_exact_block_payload",
    )
    require(
        activation_payload.get("project_id") == project_id,
        "activation_payload_project_mismatch",
        expected=project_id,
        observed=activation_payload.get("project_id"),
    )
    require(
        _revision_identity(activation_payload.get("revision")) == revision,
        "activation_payload_revision_mismatch",
        expected=revision,
        observed=activation_payload.get("revision"),
    )
    require(
        activation_payload.get("take_snapshot") is True,
        "activation_payload_snapshot_gate_missing",
    )
    require(
        execution_payload.get("project_id") == project_id,
        "execution_payload_project_mismatch",
        expected=project_id,
        observed=execution_payload.get("project_id"),
    )
    require(
        execution_payload.get("execution_mode") == ExecutionMode.EXECUTE.value,
        "execution_payload_not_execute",
        observed=execution_payload.get("execution_mode"),
    )
    require(
        execution_payload.get("open_in_gui") is True,
        "execution_payload_gui_open_gate_missing",
    )
    for field in (
        "take_snapshot",
        "fit_to_view_after_open",
        "prepare_view_replay_after_open",
        "export_view_audit",
    ):
        require(
            type(execution_payload.get(field)) is bool,
            "execution_payload_boolean_field_invalid",
            field=field,
            observed=execution_payload.get(field),
        )
    if "timeout_seconds" in execution_payload:
        timeout_seconds = execution_payload.get("timeout_seconds")
        require(
            isinstance(timeout_seconds, int)
            and not isinstance(timeout_seconds, bool)
            and timeout_seconds > 0,
            "execution_payload_timeout_invalid",
            observed=timeout_seconds,
        )
    if "response_mode" in execution_payload:
        require(
            execution_payload.get("response_mode") == "compact",
            "execution_payload_response_mode_invalid",
            observed=execution_payload.get("response_mode"),
        )

    activation_views = activation_payload.get("views")
    execution_views = execution_payload.get("views")
    require(
        activation_views == execution_views,
        "continuation_payload_views_mismatch",
        activation_views=activation_views,
        execution_views=execution_views,
    )

    activation_has_workspace = "working_dir" in activation_payload
    execution_has_workspace = "working_dir" in execution_payload
    require(
        activation_has_workspace and execution_has_workspace,
        "continuation_payload_workspace_missing",
    )
    activation_workspace = _path_identity(activation_payload.get("working_dir"))
    execution_workspace = _path_identity(execution_payload.get("working_dir"))
    workspace_identity = activation_workspace or execution_workspace
    require(
        activation_workspace is not None
        and activation_workspace == execution_workspace,
        "continuation_payload_workspace_mismatch",
        activation_workspace=activation_workspace,
        execution_workspace=execution_workspace,
    )
    requested_workspace = _path_identity(working_dir) if working_dir is not None else None
    if requested_workspace is not None:
        require(
            workspace_identity == requested_workspace,
            "continuation_payload_requested_workspace_mismatch",
            expected=requested_workspace,
            observed=workspace_identity,
        )
    response_workspace = _path_identity(response.get("working_dir"))
    if response_workspace is not None:
        require(
            workspace_identity == response_workspace,
            "continuation_payload_response_workspace_mismatch",
            expected=response_workspace,
            observed=workspace_identity,
        )

    planned_structure = _dict(response.get("planned_outputs")).get("structure")
    require(
        _path_identity(planned_structure) is not None,
        "preexecution_planned_structure_missing",
    )

    return {
        "ok": not failures,
        "project_id": project_id,
        "revision": revision,
        "workspace_identity": workspace_identity or requested_workspace,
        "activation_payload": activation_payload,
        "execution_payload": execution_payload,
        "failures": failures,
    }


def _validate_current_revision_continuation_receipt(
    current: dict[str, Any],
    *,
    project_id: str,
    revision: int,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    spec = _dict(current.get("spec"))
    checks = (
        (current.get("ok") is True, "current_revision_lookup_failed"),
        (current.get("project_id") == project_id, "current_revision_project_mismatch"),
        (
            _revision_identity(current.get("revision")) == revision,
            "current_revision_mismatch",
        ),
        (spec.get("project_id") == project_id, "current_spec_project_mismatch"),
        (
            _revision_identity(spec.get("revision")) == revision,
            "current_spec_revision_mismatch",
        ),
    )
    for condition, reason in checks:
        if not condition:
            failures.append(_continuation_failure(reason))
    return failures


def _validate_postexecution_hotload_block(
    response: dict[str, Any],
    *,
    working_dir: str | None,
) -> dict[str, Any]:
    """Validate the exact server-issued activate/open continuation contract."""

    failures: list[dict[str, Any]] = []

    def require(condition: bool, reason: str, **details: Any) -> None:
        if not condition:
            failures.append(_continuation_failure(reason, **details))

    project_id = response.get("project_id")
    project_id = project_id if isinstance(project_id, str) and project_id else None
    response_revisions = {
        key: _revision_identity(response.get(key))
        for key in ("revision", "new_revision")
        if response.get(key) is not None
    }
    revision_values = {value for value in response_revisions.values() if value is not None}
    revision = next(iter(revision_values)) if len(revision_values) == 1 else None
    require(project_id is not None, "response_project_id_missing")
    require(bool(response_revisions), "response_revision_missing")
    require(
        len(revision_values) == 1 and all(value is not None for value in response_revisions.values()),
        "response_revision_identity_invalid",
        observed=response_revisions,
    )

    expected_response_fields = {
        "ok": False,
        "partial_success": True,
        "status": "execution_completed_gui_activation_required",
        "execution_completed_before_gui_activation": True,
        "execution_must_not_repeat": True,
        "execution_retry_allowed": False,
        "gui_input_started": False,
        "gui_process_launched": False,
        "structure_reopened": False,
        "gui_activation_retry_tool": "material_studio_gui_activate",
        "gui_open_retry_tool": "material_studio_gui_open_structure",
    }
    for field, expected in expected_response_fields.items():
        observed = response.get(field)
        require(
            observed == expected and type(observed) is type(expected),
            "postexecution_response_field_mismatch",
            field=field,
            expected=expected,
            observed=observed,
        )

    execution_mode = response.get("execution_mode") or _dict(
        response.get("modeling_report")
    ).get("execution_mode")
    execution_mode = getattr(execution_mode, "value", execution_mode)
    require(
        execution_mode == ExecutionMode.EXECUTE.value,
        "postexecution_response_not_execute",
        observed=execution_mode,
    )
    require(
        _execution_result_succeeded(response),
        "postexecution_result_not_successful",
    )

    block = _dict(response.get("gui_postexecution_block"))
    require(bool(block), "postexecution_block_missing")
    expected_block_fields = {
        "blocked": True,
        "reason": "target_window_activation_required_after_execution",
        "execution_already_completed": True,
        "execution_retry_allowed": False,
        "result_artifacts_preserved": True,
        "recommended_tool": "material_studio_gui_activate",
        "gui_open_retry_tool": "material_studio_gui_open_structure",
        "same_window_required": True,
        "reuse_existing_window_only": True,
        "gui_process_launch_allowed": False,
    }
    for field, expected in expected_block_fields.items():
        observed = block.get(field)
        require(
            observed == expected and type(observed) is type(expected),
            "postexecution_block_field_mismatch",
            field=field,
            expected=expected,
            observed=observed,
        )
    require(
        block.get("project_id") == project_id,
        "postexecution_block_project_mismatch",
        expected=project_id,
        observed=block.get("project_id"),
    )
    require(
        _revision_identity(block.get("revision")) == revision,
        "postexecution_block_revision_mismatch",
        expected=revision,
        observed=block.get("revision"),
    )

    activation_payload = _dict(response.get("gui_activation_retry_payload"))
    open_payload = _dict(response.get("gui_open_retry_payload"))
    require(bool(activation_payload), "activation_payload_missing")
    require(bool(open_payload), "open_payload_missing")
    require(
        not (set(activation_payload) - _POSTEXECUTION_ACTIVATE_PAYLOAD_KEYS),
        "activation_payload_has_unexpected_fields",
        fields=sorted(set(activation_payload) - _POSTEXECUTION_ACTIVATE_PAYLOAD_KEYS),
    )
    require(
        not (set(open_payload) - _POSTEXECUTION_OPEN_PAYLOAD_KEYS),
        "open_payload_has_unexpected_fields",
        fields=sorted(set(open_payload) - _POSTEXECUTION_OPEN_PAYLOAD_KEYS),
    )
    require(
        activation_payload == _dict(block.get("activation_payload")),
        "activation_payload_not_exact_block_payload",
    )
    require(
        open_payload == _dict(block.get("gui_open_retry_payload")),
        "open_payload_not_exact_block_payload",
    )
    require(
        activation_payload.get("project_id") == project_id,
        "activation_payload_project_mismatch",
        expected=project_id,
        observed=activation_payload.get("project_id"),
    )
    require(
        _revision_identity(activation_payload.get("revision")) == revision,
        "activation_payload_revision_mismatch",
        expected=revision,
        observed=activation_payload.get("revision"),
    )
    require(
        activation_payload.get("take_snapshot") is True,
        "activation_payload_snapshot_gate_missing",
    )
    require(
        open_payload.get("project_id") == project_id,
        "open_payload_project_mismatch",
        expected=project_id,
        observed=open_payload.get("project_id"),
    )
    require(
        _revision_identity(open_payload.get("revision")) == revision,
        "open_payload_revision_mismatch",
        expected=revision,
        observed=open_payload.get("revision"),
    )
    require(
        open_payload.get("export_view_audit") is True,
        "open_payload_view_audit_gate_missing",
    )
    require(
        open_payload.get("reuse_existing_window_only") is True,
        "open_payload_single_window_gate_missing",
    )

    activation_views = activation_payload.get("views")
    open_views = open_payload.get("views")
    require(
        activation_views == open_views,
        "continuation_payload_views_mismatch",
        activation_views=activation_views,
        open_views=open_views,
    )

    activation_has_workspace = "working_dir" in activation_payload
    open_has_workspace = "working_dir" in open_payload
    require(
        activation_has_workspace == open_has_workspace,
        "continuation_payload_workspace_presence_mismatch",
    )
    activation_workspace = _path_identity(activation_payload.get("working_dir"))
    open_workspace = _path_identity(open_payload.get("working_dir"))
    workspace_identity = activation_workspace or open_workspace
    if activation_has_workspace and open_has_workspace:
        require(
            activation_workspace is not None and activation_workspace == open_workspace,
            "continuation_payload_workspace_mismatch",
            activation_workspace=activation_workspace,
            open_workspace=open_workspace,
        )
    requested_workspace = _path_identity(working_dir) if working_dir is not None else None
    if requested_workspace is not None:
        require(
            activation_has_workspace and workspace_identity == requested_workspace,
            "continuation_payload_requested_workspace_mismatch",
            expected=requested_workspace,
            observed=workspace_identity,
        )
    response_workspace = _path_identity(response.get("working_dir"))
    if response_workspace is not None:
        require(
            workspace_identity == response_workspace,
            "continuation_payload_response_workspace_mismatch",
            expected=response_workspace,
            observed=workspace_identity,
        )

    planned_structure = _dict(response.get("planned_outputs")).get("structure")
    planned_structure_identity = _path_identity(planned_structure)
    open_structure_identity = _path_identity(open_payload.get("structure_path"))
    require(
        planned_structure_identity is not None,
        "postexecution_planned_structure_missing",
    )
    require(
        planned_structure_identity == open_structure_identity,
        "open_payload_structure_mismatch",
        expected=planned_structure_identity,
        observed=open_structure_identity,
    )

    return {
        "ok": not failures,
        "project_id": project_id,
        "revision": revision,
        "workspace_identity": workspace_identity or requested_workspace,
        "structure_identity": planned_structure_identity,
        "activation_payload": activation_payload,
        "open_payload": open_payload,
        "failures": failures,
    }


def _validate_gui_transaction_hotload_block(
    response: dict[str, Any],
    *,
    working_dir: str | None,
) -> dict[str, Any]:
    """Validate an execution-complete GUI report-transaction retry contract."""

    failures: list[dict[str, Any]] = []

    def require(condition: bool, reason: str, **details: Any) -> None:
        if not condition:
            failures.append(_continuation_failure(reason, **details))

    project_id = response.get("project_id")
    project_id = project_id if isinstance(project_id, str) and project_id else None
    response_revisions = {
        key: _revision_identity(response.get(key))
        for key in ("revision", "new_revision")
        if response.get(key) is not None
    }
    revision_values = {value for value in response_revisions.values() if value is not None}
    revision = next(iter(revision_values)) if len(revision_values) == 1 else None
    require(project_id is not None, "response_project_id_missing")
    require(bool(response_revisions), "response_revision_missing")
    require(
        len(revision_values) == 1 and all(value is not None for value in response_revisions.values()),
        "response_revision_identity_invalid",
        observed=response_revisions,
    )

    expected_fields = {
        "ok": False,
        "report_persistence_deferred": True,
        "execution_completed_before_gui_transaction": True,
        "structure_ready_for_gui_retry": True,
        "execution_started": True,
        "recommended_tool": "material_studio_gui_open_structure",
        "gui_open_retry_tool": "material_studio_gui_open_structure",
    }
    for field, expected in expected_fields.items():
        observed = response.get(field)
        require(
            observed == expected and type(observed) is type(expected),
            "gui_transaction_response_field_mismatch",
            field=field,
            expected=expected,
            observed=observed,
        )

    execution_mode = response.get("execution_mode") or _dict(
        response.get("modeling_report")
    ).get("execution_mode")
    execution_mode = getattr(execution_mode, "value", execution_mode)
    require(
        execution_mode == ExecutionMode.EXECUTE.value,
        "gui_transaction_response_not_execute",
        observed=execution_mode,
    )
    require(
        _execution_result_succeeded(response),
        "gui_transaction_result_not_successful",
    )
    transaction_error = response.get("gui_action_transaction_error")
    require(
        isinstance(transaction_error, str)
        and "GUI artifact report write transaction is busy" in transaction_error,
        "gui_transaction_busy_error_missing",
        observed=transaction_error,
    )
    gui_open = response.get("gui_open")
    require(
        gui_open is None or gui_open == {},
        "gui_transaction_open_already_present",
    )
    require(
        response.get("gui_input_started") is None
        or response.get("gui_input_started") is False,
        "gui_transaction_gui_input_already_started",
    )

    open_payload = _dict(response.get("gui_open_retry_payload"))
    require(bool(open_payload), "gui_transaction_open_payload_missing")
    require(
        not (set(open_payload) - _POSTEXECUTION_OPEN_PAYLOAD_KEYS),
        "gui_transaction_open_payload_has_unexpected_fields",
        fields=sorted(set(open_payload) - _POSTEXECUTION_OPEN_PAYLOAD_KEYS),
    )
    require(
        open_payload.get("project_id") == project_id,
        "gui_transaction_open_payload_project_mismatch",
        expected=project_id,
        observed=open_payload.get("project_id"),
    )
    require(
        _revision_identity(open_payload.get("revision")) == revision,
        "gui_transaction_open_payload_revision_mismatch",
        expected=revision,
        observed=open_payload.get("revision"),
    )
    require(
        open_payload.get("reuse_existing_window_only") is True,
        "gui_transaction_open_payload_single_window_gate_missing",
    )
    for field in ("take_snapshot", "export_view_audit"):
        require(
            type(open_payload.get(field)) is bool,
            "gui_transaction_open_payload_boolean_field_invalid",
            field=field,
            observed=open_payload.get(field),
        )
    require(
        open_payload.get("export_view_audit") is True,
        "gui_transaction_open_payload_view_audit_gate_missing",
    )
    for field in (
        "fit_to_view_after_open",
        "prepare_view_replay_after_open",
    ):
        if field in open_payload:
            require(
                open_payload.get(field) is True,
                "gui_transaction_open_payload_optional_boolean_invalid",
                field=field,
                observed=open_payload.get(field),
            )

    planned_structure = _dict(response.get("planned_outputs")).get("structure")
    planned_structure_identity = _path_identity(planned_structure)
    open_structure_identity = _path_identity(open_payload.get("structure_path"))
    require(
        planned_structure_identity is not None,
        "gui_transaction_planned_structure_missing",
    )
    require(
        planned_structure_identity == open_structure_identity,
        "gui_transaction_open_payload_structure_mismatch",
        expected=planned_structure_identity,
        observed=open_structure_identity,
    )
    try:
        structure_exists = bool(planned_structure and Path(str(planned_structure)).is_file())
    except (OSError, RuntimeError, ValueError):
        structure_exists = False
    require(structure_exists, "gui_transaction_structure_artifact_missing")

    payload_has_workspace = "working_dir" in open_payload
    payload_workspace = _path_identity(open_payload.get("working_dir"))
    require(
        payload_has_workspace and payload_workspace is not None,
        "gui_transaction_open_payload_workspace_missing",
        observed=open_payload.get("working_dir"),
    )
    requested_workspace = _path_identity(working_dir) if working_dir is not None else None
    if working_dir is not None:
        require(
            requested_workspace is not None,
            "gui_transaction_requested_workspace_invalid",
            observed=working_dir,
        )
    if requested_workspace is not None:
        require(
            payload_has_workspace and payload_workspace == requested_workspace,
            "gui_transaction_open_payload_requested_workspace_mismatch",
            expected=requested_workspace,
            observed=payload_workspace,
        )
    response_has_workspace = "working_dir" in response
    response_workspace = _path_identity(response.get("working_dir"))
    if response_has_workspace:
        require(
            response_workspace is not None,
            "gui_transaction_response_workspace_invalid",
            observed=response.get("working_dir"),
        )
    if response_workspace is not None:
        require(
            payload_has_workspace and payload_workspace == response_workspace,
            "gui_transaction_open_payload_response_workspace_mismatch",
            expected=response_workspace,
            observed=payload_workspace,
        )

    fit_receipt = _dict(response.get("post_hotload_fit_to_view"))
    fit_requested = bool(
        response.get("post_hotload_fit_to_view_requested") is True
        or fit_receipt.get("requested") is True
    )
    require(
        (open_payload.get("fit_to_view_after_open") is True) == fit_requested,
        "gui_transaction_fit_to_view_payload_mismatch",
        requested=fit_requested,
        observed=open_payload.get("fit_to_view_after_open"),
    )
    if fit_requested:
        require(
            fit_receipt.get("status") == "deferred_gui_transaction_busy",
            "gui_transaction_fit_to_view_receipt_invalid",
            observed=fit_receipt.get("status"),
        )
        require(
            _dict(fit_receipt.get("followup_payload")) == open_payload,
            "gui_transaction_fit_to_view_followup_payload_mismatch",
        )

    replay_receipt = _dict(response.get("post_hotload_view_replay_prepare"))
    replay_requested = bool(
        response.get("post_hotload_view_replay_prepare_requested") is True
        or replay_receipt.get("requested") is True
    )
    require(
        (open_payload.get("prepare_view_replay_after_open") is True)
        == replay_requested,
        "gui_transaction_view_replay_payload_mismatch",
        requested=replay_requested,
        observed=open_payload.get("prepare_view_replay_after_open"),
    )
    if replay_requested:
        require(
            replay_receipt.get("status") == "deferred_gui_transaction_busy",
            "gui_transaction_view_replay_receipt_invalid",
            observed=replay_receipt.get("status"),
        )
        require(
            _dict(replay_receipt.get("followup_payload")) == open_payload,
            "gui_transaction_view_replay_followup_payload_mismatch",
        )

    return {
        "ok": not failures,
        "project_id": project_id,
        "revision": revision,
        "workspace_identity": payload_workspace or requested_workspace,
        "structure_identity": planned_structure_identity,
        "open_payload": open_payload,
        "failures": failures,
    }


def _validate_activation_continuation_receipt(
    activation: dict[str, Any],
    *,
    project_id: str,
    revision: int,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    context = _dict(activation.get("gui_action_context"))
    window_management = _dict(activation.get("window_management"))
    checks = (
        (activation.get("ok") is True, "activation_tool_failed"),
        (activation.get("activation_verified") is True, "activation_not_verified"),
        (
            activation.get("window_identity_stable_after_activation") is True,
            "activation_window_identity_changed",
        ),
        (activation.get("single_window_policy_ok") is True, "activation_single_window_policy_failed"),
        (
            window_management.get("activation_required_before_capture_or_input") is False,
            "activation_still_required_after_activate",
        ),
        (
            window_management.get("single_window_policy_ok") is True,
            "activation_window_management_single_window_failed",
        ),
        (
            activation.get("snapshot_status") != "deferred_before_capture",
            "activation_snapshot_deferred_before_capture",
        ),
        (
            activation.get("snapshot_deferred") is not True,
            "activation_snapshot_deferred",
        ),
        (
            activation.get("snapshot_focus_lost_after_activation") is not True,
            "activation_snapshot_focus_lost",
        ),
        (context.get("project_id") == project_id, "activation_project_binding_mismatch"),
        (
            _revision_identity(context.get("revision")) == revision,
            "activation_revision_binding_mismatch",
        ),
    )
    for condition, reason in checks:
        if not condition:
            failures.append(_continuation_failure(reason))
    return failures


def _validate_open_continuation_receipt(
    opened: dict[str, Any],
    *,
    project_id: str,
    revision: int,
    structure_identity: str,
    open_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    gui_open = _dict(opened.get("gui_open"))
    sync_context = _dict(opened.get("structured_sync_context"))
    open_result = _dict(opened.get("open_result"))
    gui_open_result = _dict(gui_open.get("open_result"))
    report_gui = _dict(_dict(opened.get("modeling_report")).get("gui"))

    checks = (
        (opened.get("ok") is True, "open_continuation_tool_failed"),
        (opened.get("project_id") == project_id, "open_continuation_project_mismatch"),
        (
            _revision_identity(opened.get("revision")) == revision,
            "open_continuation_revision_mismatch",
        ),
        (
            _path_identity(opened.get("structure_path")) == structure_identity,
            "open_continuation_structure_mismatch",
        ),
        (
            opened.get("reuse_existing_window_only") is True,
            "open_continuation_reuse_gate_missing",
        ),
        (opened.get("single_window_policy_ok") is True, "open_continuation_single_window_failed"),
        (
            opened.get("post_open_single_window_policy_ok") is True,
            "open_continuation_post_open_single_window_failed",
        ),
        (not list(open_result.get("spawned_process_ids") or []), "open_continuation_spawned_process"),
        (bool(gui_open), "open_continuation_gui_open_receipt_missing"),
        (gui_open.get("project_id") == project_id, "gui_open_project_mismatch"),
        (
            _revision_identity(gui_open.get("revision")) == revision,
            "gui_open_revision_mismatch",
        ),
        (
            _path_identity(gui_open.get("structure_path")) == structure_identity,
            "gui_open_structure_mismatch",
        ),
        (gui_open.get("reuse_existing_window_only") is True, "gui_open_reuse_gate_missing"),
        (gui_open.get("single_window_policy_ok") is True, "gui_open_single_window_failed"),
        (
            gui_open.get("post_open_single_window_policy_ok") is True,
            "gui_open_post_open_single_window_failed",
        ),
        (
            not list(gui_open_result.get("spawned_process_ids") or []),
            "gui_open_spawned_process",
        ),
        (sync_context.get("available") is True, "open_structured_sync_unavailable"),
        (sync_context.get("project_id") == project_id, "open_structured_sync_project_mismatch"),
        (
            _revision_identity(sync_context.get("revision")) == revision,
            "open_structured_sync_revision_mismatch",
        ),
        (report_gui.get("loaded_current_revision") is True, "open_report_current_revision_not_loaded"),
    )
    for condition, reason in checks:
        if not condition:
            failures.append(_continuation_failure(reason))

    if open_payload.get("fit_to_view_after_open") is True:
        fit = _dict(opened.get("post_hotload_fit_to_view"))
        fit_checks = (
            (fit.get("completed") is True, "post_open_fit_to_view_incomplete"),
            (fit.get("structure_unchanged") is True, "post_open_fit_to_view_structure_not_verified"),
            (fit.get("final_snapshot_bound") is True, "post_open_fit_to_view_snapshot_not_bound"),
        )
        for condition, reason in fit_checks:
            if not condition:
                failures.append(_continuation_failure(reason))
    if open_payload.get("prepare_view_replay_after_open") is True:
        replay = _dict(opened.get("post_hotload_view_replay_prepare"))
        replay_checks = (
            (replay.get("status") == "prepared", "post_open_view_replay_not_prepared"),
            (replay.get("prepared") is True, "post_open_view_replay_prepare_failed"),
            (
                _revision_identity(replay.get("prepared_revision")) == revision,
                "post_open_view_replay_revision_mismatch",
            ),
        )
        for condition, reason in replay_checks:
            if not condition:
                failures.append(_continuation_failure(reason))
    return failures


def _merge_postexecution_hotload_response(
    blocked: dict[str, Any],
    opened: dict[str, Any],
    *,
    completed: bool,
    failure_status: str | None = None,
) -> dict[str, Any]:
    """Preserve execution metadata while making the artifact-open result authoritative."""

    merged = {**blocked, **opened}
    final_report = _dict(opened.get("modeling_report"))
    for field in (
        "live_summary",
        "live_request_summary",
        "live_hotload_preflight",
        "live_gui_acceptance",
        "gui_current_revision",
        "next_action_plan",
        "visual_diagnostics_next_action_plan",
        "coordinated_next_action_plan",
        "next_action_tracks",
        "next_action",
        "normality_gate",
        "normality_explanation",
        "visual_normality_summary",
        "view_parameter_summary",
    ):
        final_value = opened.get(field)
        if final_value is None:
            final_value = final_report.get(field)
        if final_value is None:
            merged.pop(field, None)
        else:
            merged[field] = final_value
    merged["execution_completed_before_gui_activation"] = True
    merged["execution_must_not_repeat"] = True
    merged["execution_retry_allowed"] = False
    merged["postexecution_hotload_original_status"] = blocked.get("status")
    merged["postexecution_hotload_continuation_completed"] = completed
    gui_open_observed = isinstance(opened.get("gui_open"), dict)
    merged["gui_postexecution_block"] = (
        None
        if completed
        else opened.get("gui_postexecution_block", blocked.get("gui_postexecution_block"))
    )
    merged["gui_input_started"] = gui_open_observed
    merged["structure_reopened"] = bool(completed or gui_open_observed)
    if completed:
        merged["ok"] = True
        merged["partial_success"] = False
        merged["status"] = "postexecution_hotload_continuation_completed"
        for field in (
            "error",
            "required_next_step",
            "gui_open_warning",
            "recommended_tool",
            "recommended_action",
            "gui_activation_retry_tool",
            "gui_activation_retry_payload",
            "gui_open_retry_tool",
            "gui_open_retry_payload",
        ):
            merged.pop(field, None)
    else:
        merged["ok"] = False
        merged["partial_success"] = True
        if failure_status:
            merged["status"] = failure_status
    return merged


def _merge_gui_transaction_hotload_response(
    blocked: dict[str, Any],
    opened: dict[str, Any],
    *,
    completed: bool,
    failure_status: str | None = None,
) -> dict[str, Any]:
    merged = _merge_postexecution_hotload_response(
        blocked,
        opened,
        completed=completed,
        failure_status=failure_status,
    )
    merged.pop("execution_completed_before_gui_activation", None)
    merged["execution_completed_before_gui_transaction"] = True
    merged["postexecution_hotload_original_status"] = (
        "execution_completed_gui_transaction_required"
    )
    merged["gui_transaction_hotload_continuation_completed"] = completed
    if completed:
        merged["report_persistence_deferred"] = False
        merged["gui_transaction_report_persistence_resolved"] = True
        for field in (
            "gui_action_transaction_error",
            "gui_open_retry_tool",
            "gui_open_retry_payload",
            "structure_ready_for_gui_retry",
        ):
            merged.pop(field, None)
    return merged


def _resume_gui_transaction_hotload(
    response: dict[str, Any],
    *,
    phase: str,
    working_dir: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume one exact report-lock continuation without rerunning execution."""

    receipt: dict[str, Any] = {
        "phase": phase,
        "requested": True,
        "continuation_kind": "gui_transaction_report_persistence",
        "eligible": False,
        "attempted": False,
        "completed": False,
        "status": "contract_validation_started",
        "failures": [],
        "modeling_request_reinvoked": False,
        "execution_repeated": False,
        "runner_reinvoked": False,
        "open_structure_invoked": False,
        "open_structure_call_count": 0,
        "gui_process_launch_allowed": False,
        "original_response_status": "execution_completed_gui_transaction_required",
        "original_server_status": response.get("status"),
        "original_report_persistence_deferred": response.get(
            "report_persistence_deferred"
        ),
    }
    contract = _validate_gui_transaction_hotload_block(
        response,
        working_dir=working_dir,
    )
    receipt["contract"] = contract
    receipt["failures"] = list(contract["failures"])
    if not contract["ok"]:
        receipt["status"] = "contract_rejected"
        return response, receipt

    project_id = str(contract["project_id"])
    revision = int(contract["revision"])
    open_payload = dict(contract["open_payload"])
    current_payload: dict[str, Any] = {"project_id": project_id}
    if "working_dir" in open_payload:
        current_payload["working_dir"] = open_payload["working_dir"]
    elif working_dir is not None:
        current_payload["working_dir"] = working_dir
    receipt.update(
        {
            "eligible": True,
            "attempted": True,
            "status": "current_revision_check_started",
            "project_id": project_id,
            "revision": revision,
            "workspace_identity": contract.get("workspace_identity"),
            "structure_identity": contract.get("structure_identity"),
            "open_payload": open_payload,
            "current_revision_payload": current_payload,
        }
    )
    try:
        current = server.material_studio_model_get_current(**current_payload)
    except Exception as exc:
        receipt["status"] = "current_revision_check_failed"
        receipt["failures"].append(
            _continuation_failure("current_revision_lookup_call_error", error=str(exc))
        )
        return response, receipt
    receipt["current_revision_receipt"] = current
    current_failures = _validate_current_revision_continuation_receipt(
        current,
        project_id=project_id,
        revision=revision,
    )
    if current_failures:
        receipt["status"] = "current_revision_check_failed"
        receipt["failures"].extend(current_failures)
        return response, receipt

    receipt["current_revision_verified_before_open"] = True
    receipt["status"] = "open_started"
    receipt["open_structure_invoked"] = True
    receipt["open_structure_call_count"] = 1
    try:
        opened = server.material_studio_gui_open_structure(**open_payload)
    except Exception as exc:
        receipt["status"] = "open_failed"
        receipt["failures"].append(
            _continuation_failure("open_call_error", error=str(exc))
        )
        return response, receipt
    receipt["open_continuation_receipt"] = opened
    open_failures = _validate_open_continuation_receipt(
        opened,
        project_id=project_id,
        revision=revision,
        structure_identity=str(contract["structure_identity"]),
        open_payload=open_payload,
    )
    if open_failures:
        receipt["status"] = (
            "open_failed" if opened.get("ok") is not True else "open_verification_failed"
        )
        receipt["failures"].extend(open_failures)
        effective = _merge_gui_transaction_hotload_response(
            response,
            opened,
            completed=False,
            failure_status="gui_transaction_hotload_continuation_failed",
        )
        return effective, receipt

    receipt.update(
        {
            "status": "completed",
            "completed": True,
            "exact_payload_used": True,
            "open_completed": True,
            "fit_to_view_after_open": (
                open_payload.get("fit_to_view_after_open") is True
            ),
            "prepare_view_replay_after_open": (
                open_payload.get("prepare_view_replay_after_open") is True
            ),
        }
    )
    effective = _merge_gui_transaction_hotload_response(
        response,
        opened,
        completed=True,
    )
    return effective, receipt


def _resume_postexecution_hotload(
    response: dict[str, Any],
    *,
    enabled: bool,
    phase: str,
    working_dir: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume one exact post-execution continuation without rerunning modeling."""

    receipt: dict[str, Any] = {
        "phase": phase,
        "requested": bool(enabled),
        "eligible": False,
        "attempted": False,
        "completed": False,
        "status": "disabled" if not enabled else "not_required",
        "failures": [],
        "modeling_request_reinvoked": False,
        "execution_repeated": False,
        "runner_reinvoked": False,
        "gui_process_launch_allowed": False,
    }
    if not enabled:
        return response, receipt
    if response.get("preexecution_execution_continuation_completed") is False:
        receipt["status"] = "preexecution_execution_continuation_not_verified"
        receipt["failures"].append(
            _continuation_failure(
                "preexecution_execution_continuation_not_completed"
            )
        )
        return response, receipt
    if (
        response.get("report_persistence_deferred") is True
        and response.get("execution_completed_before_gui_transaction") is True
    ):
        return _resume_gui_transaction_hotload(
            response,
            phase=phase,
            working_dir=working_dir,
        )
    if response.get("status") != "execution_completed_gui_activation_required":
        if response.get("ok") is not True:
            receipt["status"] = "not_applicable_failed_response"
        return response, receipt

    receipt["original_response_status"] = response.get("status")
    receipt["original_block"] = _dict(response.get("gui_postexecution_block"))
    receipt["execution_must_not_repeat"] = response.get("execution_must_not_repeat")
    contract = _validate_postexecution_hotload_block(response, working_dir=working_dir)
    receipt["contract"] = contract
    receipt["failures"] = list(contract["failures"])
    if not contract["ok"]:
        receipt["status"] = "contract_rejected"
        return response, receipt

    project_id = str(contract["project_id"])
    revision = int(contract["revision"])
    activation_payload = dict(contract["activation_payload"])
    open_payload = dict(contract["open_payload"])
    receipt.update(
        {
            "eligible": True,
            "attempted": True,
            "status": "activation_started",
            "project_id": project_id,
            "revision": revision,
            "workspace_identity": contract.get("workspace_identity"),
            "structure_identity": contract.get("structure_identity"),
            "activation_payload": activation_payload,
            "open_payload": open_payload,
        }
    )
    try:
        activation = server.material_studio_gui_activate(**activation_payload)
    except Exception as exc:
        receipt["status"] = "activation_failed"
        receipt["failures"].append(
            _continuation_failure("activation_call_error", error=str(exc))
        )
        return response, receipt
    receipt["activation_receipt"] = activation
    activation_failures = _validate_activation_continuation_receipt(
        activation,
        project_id=project_id,
        revision=revision,
    )
    if activation_failures:
        receipt["status"] = "activation_failed"
        receipt["failures"].extend(activation_failures)
        return response, receipt

    receipt["activation_completed"] = True
    receipt["status"] = "open_started"
    try:
        opened = server.material_studio_gui_open_structure(**open_payload)
    except Exception as exc:
        receipt["status"] = "open_failed"
        receipt["failures"].append(
            _continuation_failure("open_call_error", error=str(exc))
        )
        return response, receipt
    receipt["open_continuation_receipt"] = opened
    open_failures = _validate_open_continuation_receipt(
        opened,
        project_id=project_id,
        revision=revision,
        structure_identity=str(contract["structure_identity"]),
        open_payload=open_payload,
    )
    if open_failures:
        receipt["status"] = (
            "open_failed" if opened.get("ok") is not True else "open_verification_failed"
        )
        receipt["failures"].extend(open_failures)
        effective = _merge_postexecution_hotload_response(
            response,
            opened,
            completed=False,
            failure_status="postexecution_hotload_continuation_failed",
        )
        return effective, receipt

    receipt.update(
        {
            "status": "completed",
            "completed": True,
            "exact_payloads_used": True,
            "activation_completed": True,
            "open_completed": True,
            "fit_to_view_after_open": open_payload.get("fit_to_view_after_open") is True,
            "prepare_view_replay_after_open": (
                open_payload.get("prepare_view_replay_after_open") is True
            ),
        }
    )
    effective = _merge_postexecution_hotload_response(
        response,
        opened,
        completed=True,
    )
    return effective, receipt


def _merge_preexecution_execution_response(
    blocked: dict[str, Any],
    applied: dict[str, Any],
    *,
    completed: bool,
    failure_status: str | None = None,
) -> dict[str, Any]:
    """Preserve the prepared revision while making the one-shot apply authoritative."""

    merged = {**blocked, **applied}
    for field in (
        "status",
        "error",
        "required_next_step",
        "recommended_tool",
        "recommended_action",
        "gui_open_warning",
        "gui_activation_retry_tool",
        "gui_activation_retry_payload",
        "execution_retry_tool",
        "execution_retry_payload",
    ):
        if field not in applied:
            merged.pop(field, None)
    for field in (
        "runner_invoked",
        "structure_materialization_started",
        "gui_input_started",
        "gui_process_launched",
        "structure_reopened",
    ):
        if field not in applied:
            merged.pop(field, None)
    merged["gui_preexecution_block"] = None
    merged["preexecution_execution_continuation_completed"] = completed
    merged["execution_repeated"] = False
    merged["modeling_request_reinvoked"] = False
    if completed:
        merged["execution_deferred"] = False
    else:
        merged["ok"] = False
        if failure_status:
            merged["status"] = failure_status
    return merged


def _resume_preexecution_execution(
    response: dict[str, Any],
    *,
    enabled: bool,
    execution_authorized: bool,
    phase: str,
    working_dir: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume one exact activate/apply continuation without creating a revision."""

    receipt: dict[str, Any] = {
        "phase": phase,
        "requested": bool(enabled),
        "explicit_execute_authorized": bool(execution_authorized),
        "eligible": False,
        "attempted": False,
        "completed": False,
        "status": "disabled" if not enabled else "not_required",
        "failures": [],
        "modeling_request_reinvoked": False,
        "revision_created": False,
        "execution_repeated": False,
        "runner_reinvoked": False,
        "apply_current_revision_invoked": False,
        "apply_current_revision_call_count": 0,
        "gui_process_launch_allowed": False,
    }
    if not enabled:
        return response, receipt
    if response.get("status") != "gui_activation_required_before_execution":
        if response.get("ok") is not True:
            receipt["status"] = "not_applicable_failed_response"
        return response, receipt

    receipt["original_response_status"] = response.get("status")
    receipt["original_block"] = _dict(response.get("gui_preexecution_block"))
    if not execution_authorized:
        receipt["status"] = "explicit_execute_required"
        receipt["failures"].append(
            _continuation_failure(
                "explicit_execute_authorization_missing",
                required_execution_mode=ExecutionMode.EXECUTE.value,
            )
        )
        return response, receipt

    contract = _validate_preexecution_execution_block(
        response,
        working_dir=working_dir,
    )
    receipt["contract"] = contract
    receipt["failures"] = list(contract["failures"])
    if not contract["ok"]:
        receipt["status"] = "contract_rejected"
        return response, receipt

    project_id = str(contract["project_id"])
    revision = int(contract["revision"])
    activation_payload = dict(contract["activation_payload"])
    execution_payload = dict(contract["execution_payload"])
    current_payload = {
        "project_id": project_id,
        "working_dir": execution_payload["working_dir"],
    }
    receipt.update(
        {
            "eligible": True,
            "status": "current_revision_check_started",
            "project_id": project_id,
            "revision": revision,
            "workspace_identity": contract.get("workspace_identity"),
            "activation_payload": activation_payload,
            "execution_payload": execution_payload,
            "current_revision_payload": current_payload,
        }
    )

    try:
        current_before = server.material_studio_model_get_current(**current_payload)
    except Exception as exc:
        receipt["status"] = "current_revision_check_failed"
        receipt["failures"].append(
            _continuation_failure("current_revision_lookup_call_error", error=str(exc))
        )
        return response, receipt
    receipt["current_revision_before_activation"] = current_before
    current_failures = _validate_current_revision_continuation_receipt(
        current_before,
        project_id=project_id,
        revision=revision,
    )
    if current_failures:
        receipt["status"] = "current_revision_check_failed"
        receipt["failures"].extend(current_failures)
        return response, receipt

    receipt["attempted"] = True
    receipt["current_revision_verified_before_activation"] = True
    receipt["status"] = "activation_started"
    try:
        activation = server.material_studio_gui_activate(**activation_payload)
    except Exception as exc:
        receipt["status"] = "activation_failed"
        receipt["failures"].append(
            _continuation_failure("activation_call_error", error=str(exc))
        )
        return response, receipt
    receipt["activation_receipt"] = activation
    activation_failures = _validate_activation_continuation_receipt(
        activation,
        project_id=project_id,
        revision=revision,
    )
    if activation_failures:
        receipt["status"] = "activation_failed"
        receipt["failures"].extend(activation_failures)
        return response, receipt

    receipt["activation_completed"] = True
    receipt["status"] = "current_revision_recheck_started"
    try:
        current_after_activation = server.material_studio_model_get_current(
            **current_payload
        )
    except Exception as exc:
        receipt["status"] = "current_revision_recheck_failed"
        receipt["failures"].append(
            _continuation_failure("current_revision_recheck_call_error", error=str(exc))
        )
        return response, receipt
    receipt["current_revision_after_activation"] = current_after_activation
    current_failures = _validate_current_revision_continuation_receipt(
        current_after_activation,
        project_id=project_id,
        revision=revision,
    )
    if current_failures:
        receipt["status"] = "current_revision_recheck_failed"
        receipt["failures"].extend(current_failures)
        return response, receipt

    receipt["current_revision_verified_after_activation"] = True
    receipt["status"] = "apply_started"
    receipt["apply_current_revision_invoked"] = True
    receipt["apply_current_revision_call_count"] = 1
    try:
        applied = server.material_studio_gui_apply_current_revision(
            **execution_payload
        )
    except Exception as exc:
        receipt["status"] = "apply_failed"
        receipt["failures"].append(
            _continuation_failure("apply_current_revision_call_error", error=str(exc))
        )
        failed = {
            "ok": False,
            "project_id": project_id,
            "revision": revision,
            "status": "preexecution_execution_continuation_apply_failed",
            "error": str(exc),
            "execution_started": False,
            "execution_deferred": True,
            "recommended_tool": "material_studio_live_project_status",
        }
        return (
            _merge_preexecution_execution_response(
                response,
                failed,
                completed=False,
                failure_status=failed["status"],
            ),
            receipt,
        )
    receipt["apply_current_revision_receipt"] = applied

    applied_project = applied.get("project_id")
    applied_revisions = {
        value
        for value in (
            _revision_identity(applied.get("revision")),
            _revision_identity(applied.get("new_revision")),
        )
        if value is not None
    }
    if applied_project != project_id:
        receipt["failures"].append(
            _continuation_failure(
                "apply_response_project_mismatch",
                expected=project_id,
                observed=applied_project,
            )
        )
    if applied_revisions != {revision}:
        receipt["failures"].append(
            _continuation_failure(
                "apply_response_revision_mismatch",
                expected=revision,
                observed=sorted(applied_revisions),
            )
        )

    applied_mode = applied.get("execution_mode") or _dict(
        applied.get("modeling_report")
    ).get("execution_mode")
    applied_mode = getattr(applied_mode, "value", applied_mode)
    if applied_mode != ExecutionMode.EXECUTE.value:
        receipt["failures"].append(
            _continuation_failure(
                "apply_response_not_execute",
                observed=applied_mode,
            )
        )

    gui_transaction_deferred = bool(
        applied.get("report_persistence_deferred") is True
        and applied.get("execution_completed_before_gui_transaction") is True
    )
    postexecution_deferred = (
        applied.get("status") == "execution_completed_gui_activation_required"
    )
    if gui_transaction_deferred:
        gui_transaction_contract = _validate_gui_transaction_hotload_block(
            applied,
            working_dir=str(execution_payload["working_dir"]),
        )
        receipt["gui_transaction_contract"] = gui_transaction_contract
        receipt["failures"].extend(gui_transaction_contract["failures"])
    elif postexecution_deferred:
        postexecution_contract = _validate_postexecution_hotload_block(
            applied,
            working_dir=str(execution_payload["working_dir"]),
        )
        receipt["postexecution_contract"] = postexecution_contract
        receipt["failures"].extend(postexecution_contract["failures"])
    elif applied.get("ok") is True:
        if applied.get("execution_started") is not True:
            receipt["failures"].append(
                _continuation_failure(
                    "apply_response_execution_not_started",
                    observed=applied.get("execution_started"),
                )
            )
        if not _execution_result_succeeded(applied):
            receipt["failures"].append(
                _continuation_failure("apply_response_result_not_successful")
            )
    else:
        receipt["status"] = "apply_failed"
        receipt["failures"].append(
            _continuation_failure(
                "apply_current_revision_failed",
                observed_status=applied.get("status"),
                error=applied.get("error"),
            )
        )
        return (
            _merge_preexecution_execution_response(
                response,
                applied,
                completed=False,
                failure_status=(
                    str(applied.get("status"))
                    if applied.get("status")
                    else "preexecution_execution_continuation_apply_failed"
                ),
            ),
            receipt,
        )

    if receipt["failures"]:
        receipt["status"] = "apply_verification_failed"
        return (
            _merge_preexecution_execution_response(
                response,
                applied,
                completed=False,
                failure_status="preexecution_execution_continuation_verification_failed",
            ),
            receipt,
        )

    receipt.update(
        {
            "status": (
                "execution_completed_gui_transaction_required"
                if gui_transaction_deferred
                else (
                    "execution_completed_gui_activation_required"
                    if postexecution_deferred
                    else "completed"
                )
            ),
            "completed": True,
            "exact_payloads_used": True,
            "execution_started_by_apply": True,
            "postexecution_hotload_deferred": bool(
                postexecution_deferred or gui_transaction_deferred
            ),
            "gui_transaction_hotload_deferred": gui_transaction_deferred,
            "fit_to_view_after_open": (
                execution_payload.get("fit_to_view_after_open") is True
            ),
            "prepare_view_replay_after_open": (
                execution_payload.get("prepare_view_replay_after_open") is True
            ),
        }
    )
    effective = _merge_preexecution_execution_response(
        response,
        applied,
        completed=True,
    )
    return effective, receipt


def _skipped_followup_preexecution_continuation_receipt(
    *,
    enabled: bool,
    execution_authorized: bool,
) -> dict[str, Any]:
    return {
        "phase": "followup",
        "requested": bool(enabled),
        "explicit_execute_authorized": bool(execution_authorized),
        "eligible": False,
        "attempted": False,
        "completed": False,
        "status": "base_request_not_ready",
        "failures": [],
        "modeling_request_reinvoked": False,
        "revision_created": False,
        "execution_repeated": False,
        "runner_reinvoked": False,
        "apply_current_revision_invoked": False,
        "apply_current_revision_call_count": 0,
        "gui_process_launch_allowed": False,
    }


def _preexecution_execution_continuation_summary(
    base: dict[str, Any] | None,
    followup: dict[str, Any] | None,
) -> dict[str, Any]:
    receipts = [item for item in (base, followup) if isinstance(item, dict)]
    failures: list[dict[str, Any]] = []
    for item in receipts:
        for failure in item.get("failures") or []:
            failures.append({"phase": item.get("phase"), **_dict(failure)})
    required = [item for item in receipts if item.get("original_response_status")]
    requested = any(item.get("requested") is True for item in receipts)
    attempted = any(item.get("attempted") is True for item in receipts)
    completed = bool(required) and all(item.get("completed") is True for item in required)
    if failures:
        status = "failed"
    elif completed:
        status = "completed"
    elif requested:
        status = "not_required"
    else:
        status = "disabled"
    return {
        "requested": requested,
        "attempted": attempted,
        "completed": completed,
        "status": status,
        "required_phase_count": len(required),
        "completed_phase_count": sum(item.get("completed") is True for item in required),
        "failures": failures,
        "base_status": (base or {}).get("status"),
        "followup_status": (followup or {}).get("status"),
        "apply_current_revision_call_count": sum(
            int(item.get("apply_current_revision_call_count") or 0)
            for item in receipts
        ),
        "execution_repeated": False,
        "runner_reinvoked": False,
        "gui_process_launch_allowed": False,
    }


def _skipped_followup_continuation_receipt(*, enabled: bool) -> dict[str, Any]:
    return {
        "phase": "followup",
        "requested": bool(enabled),
        "eligible": False,
        "attempted": False,
        "completed": False,
        "status": "base_request_not_ready",
        "failures": [],
        "modeling_request_reinvoked": False,
        "execution_repeated": False,
        "runner_reinvoked": False,
        "gui_process_launch_allowed": False,
    }


def _postexecution_hotload_continuation_summary(
    base: dict[str, Any] | None,
    followup: dict[str, Any] | None,
) -> dict[str, Any]:
    receipts = [item for item in (base, followup) if isinstance(item, dict)]
    failures: list[dict[str, Any]] = []
    for item in receipts:
        for failure in item.get("failures") or []:
            failures.append({"phase": item.get("phase"), **_dict(failure)})
    required = [item for item in receipts if item.get("original_response_status")]
    requested = any(item.get("requested") is True for item in receipts)
    attempted = any(item.get("attempted") is True for item in receipts)
    completed = bool(required) and all(item.get("completed") is True for item in required)
    if failures:
        status = "failed"
    elif completed:
        status = "completed"
    elif requested:
        status = "not_required"
    else:
        status = "disabled"
    return {
        "requested": requested,
        "attempted": attempted,
        "completed": completed,
        "status": status,
        "required_phase_count": len(required),
        "completed_phase_count": sum(item.get("completed") is True for item in required),
        "failures": failures,
        "base_status": (base or {}).get("status"),
        "followup_status": (followup or {}).get("status"),
        "execution_repeated": False,
        "runner_reinvoked": False,
        "gui_process_launch_allowed": False,
    }


def run_live_smoke(
    *,
    request: str | None = None,
    follow_up_request: str | None = None,
    follow_up_preset: str | None = None,
    scenario: str | None = "sic_mos",
    hotload: bool = False,
    execution_mode: str = "auto",
    working_dir: str | None = None,
    include_gui_status: bool = True,
    take_snapshot: bool = True,
    export_bundle: bool = True,
    views: list[str] | None = None,
    timeout_seconds: int | None = None,
    resume_deferred_execution: bool = False,
    resume_deferred_hotload: bool = False,
) -> dict[str, Any]:
    """Run preflight -> live request -> safe continuation -> status -> bundle."""

    resolved_scenario = scenario or "sic_mos"
    resolved_request = request or default_request_for_scenario(resolved_scenario, hotload=hotload)
    if follow_up_request is not None and follow_up_preset is not None:
        raise ValueError("Use either follow_up_request or follow_up_preset, not both.")
    resolved_follow_up_request = follow_up_request
    if resolved_follow_up_request is None and follow_up_preset:
        if scenario is None:
            raise ValueError("follow_up_preset requires an explicit scenario when request is supplied directly.")
        resolved_follow_up_request = default_follow_up_request_for_scenario(resolved_scenario, follow_up_preset)
    mode = _execution_mode_arg(execution_mode)
    explicit_execution_authorized = execution_mode == ExecutionMode.EXECUTE.value
    preflight = server.material_studio_live_session_preflight(
        working_dir=working_dir,
        include_latest_project=True,
        include_gui_status=include_gui_status,
    )
    initial_base_live = server.material_studio_live_modeling_request(
        resolved_request,
        execution_mode=mode,
        open_in_gui=True,
        take_snapshot=take_snapshot,
        export_view_audit=True,
        views=views,
        working_dir=working_dir,
        timeout_seconds=timeout_seconds,
    )
    live, base_preexecution_execution_continuation = (
        _resume_preexecution_execution(
            initial_base_live,
            enabled=resume_deferred_execution,
            execution_authorized=explicit_execution_authorized,
            phase="base",
            working_dir=working_dir,
        )
    )
    live, base_hotload_continuation = _resume_postexecution_hotload(
        live,
        enabled=resume_deferred_hotload,
        phase="base",
        working_dir=working_dir,
    )
    base_live = live
    followup_live: dict[str, Any] | None = None
    followup_preexecution_execution_continuation: dict[str, Any] | None = None
    followup_hotload_continuation: dict[str, Any] | None = None
    if resolved_follow_up_request and live.get("ok") and live.get("project_id"):
        initial_followup_live = server.material_studio_live_modeling_request(
            resolved_follow_up_request,
            project_id=str(live["project_id"]),
            execution_mode=mode,
            open_in_gui=True,
            take_snapshot=take_snapshot,
            export_view_audit=True,
            views=views,
            working_dir=working_dir,
            timeout_seconds=timeout_seconds,
        )
        followup_live, followup_preexecution_execution_continuation = (
            _resume_preexecution_execution(
                initial_followup_live,
                enabled=resume_deferred_execution,
                execution_authorized=explicit_execution_authorized,
                phase="followup",
                working_dir=working_dir,
            )
        )
        followup_live, followup_hotload_continuation = _resume_postexecution_hotload(
            followup_live,
            enabled=resume_deferred_hotload,
            phase="followup",
            working_dir=working_dir,
        )
        live = followup_live
    elif resolved_follow_up_request:
        followup_preexecution_execution_continuation = (
            _skipped_followup_preexecution_continuation_receipt(
                enabled=resume_deferred_execution,
                execution_authorized=explicit_execution_authorized,
            )
        )
        followup_hotload_continuation = _skipped_followup_continuation_receipt(
            enabled=resume_deferred_hotload,
        )

    project_id = live.get("project_id") if isinstance(live, dict) else None
    status: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    if project_id:
        status = server.material_studio_live_project_status(
            project_id=str(project_id),
            include_gui_status=include_gui_status,
            working_dir=working_dir,
        )
        if export_bundle:
            effective_views = _effective_views_from_live_response(live, views)
            bundle = server.material_studio_model_export_view_bundle(
                project_id=str(project_id),
                views=effective_views,
                include_gui_snapshot=bool(take_snapshot and include_gui_status),
                working_dir=working_dir,
            )
        else:
            effective_views = _effective_views_from_live_response(live, views)
    else:
        effective_views = _effective_views_from_live_response(live, views)

    scenario_for_summary = resolved_scenario if request is None else scenario
    scenario_expectation = resolved_scenario if request is None and resolved_follow_up_request is None else ""
    mode_value = getattr(mode, "value", mode)
    summary = build_live_smoke_summary(
        preflight=preflight,
        live=live,
        status=status,
        bundle=bundle,
        base_live=base_live if resolved_follow_up_request else None,
        scenario=scenario_for_summary,
        scenario_expectation=scenario_expectation,
        follow_up_preset=follow_up_preset,
        hotload_expected=bool((hotload or resolved_follow_up_request) and mode_value != ExecutionMode.PREVIEW.value),
        snapshot_expected=bool(take_snapshot),
        base_preexecution_execution_continuation=(
            base_preexecution_execution_continuation
        ),
        followup_preexecution_execution_continuation=(
            followup_preexecution_execution_continuation
        ),
        base_hotload_continuation=base_hotload_continuation,
        followup_hotload_continuation=followup_hotload_continuation,
    )

    return {
        "ok": _overall_ok(preflight=preflight, live=live, base_live=base_live, status=status, bundle=bundle, summary=summary),
        "request": resolved_request,
        "follow_up_request": resolved_follow_up_request,
        "follow_up_preset": follow_up_preset,
        "available_follow_up_presets": sorted(FOLLOW_UP_REQUESTS.get(resolved_scenario, {})) if scenario is not None else [],
        "scenario": scenario_for_summary,
        "hotload_requested": bool(hotload),
        "resume_deferred_execution_requested": bool(resume_deferred_execution),
        "resume_deferred_hotload_requested": bool(resume_deferred_hotload),
        "execution_mode_argument": execution_mode,
        "working_dir": str(Path(working_dir).expanduser()) if working_dir else None,
        "effective_views": effective_views,
        "summary": summary,
        "preflight": preflight,
        "base_live": base_live if resolved_follow_up_request else None,
        "live": live,
        "followup_live": followup_live,
        "base_preexecution_execution_continuation": (
            base_preexecution_execution_continuation
        ),
        "followup_preexecution_execution_continuation": (
            followup_preexecution_execution_continuation
        ),
        "base_hotload_continuation": base_hotload_continuation,
        "followup_hotload_continuation": followup_hotload_continuation,
        "status": status,
        "bundle": bundle,
    }


def build_live_smoke_summary(
    *,
    preflight: dict[str, Any],
    live: dict[str, Any],
    status: dict[str, Any] | None = None,
    bundle: dict[str, Any] | None = None,
    base_live: dict[str, Any] | None = None,
    scenario: str | None = None,
    scenario_expectation: str | None = None,
    follow_up_preset: str | None = None,
    hotload_expected: bool = False,
    snapshot_expected: bool = True,
    base_preexecution_execution_continuation: dict[str, Any] | None = None,
    followup_preexecution_execution_continuation: dict[str, Any] | None = None,
    base_hotload_continuation: dict[str, Any] | None = None,
    followup_hotload_continuation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact, stable acceptance summary for a live smoke run."""

    preexecution_continuation_summary = (
        _preexecution_execution_continuation_summary(
            base_preexecution_execution_continuation,
            followup_preexecution_execution_continuation,
        )
    )
    continuation_summary = _postexecution_hotload_continuation_summary(
        base_hotload_continuation,
        followup_hotload_continuation,
    )
    live_report = _dict(live.get("modeling_report"))
    nl_plan = _dict(live.get("nl_plan"))
    live_summary = _dict(live.get("live_summary")) or _dict(live_report.get("live_summary"))
    live_request_summary = _dict(live.get("live_request_summary")) or _dict(live_report.get("live_request_summary"))
    live_hotload_preflight = _dict(live.get("live_hotload_preflight")) or _dict(
        live_report.get("live_hotload_preflight")
    )
    status_report = _dict(status.get("modeling_report")) if isinstance(status, dict) else {}
    status_summary = _dict(status.get("live_summary")) if isinstance(status, dict) else {}
    if not status_summary:
        status_summary = _dict(status_report.get("live_summary"))
    status_request_summary = _dict((status or {}).get("live_request_summary")) or _dict(
        status_report.get("live_request_summary")
    )
    status_hotload_preflight = _dict((status or {}).get("live_hotload_preflight")) or _dict(
        status_report.get("live_hotload_preflight")
    )

    report = status_report or live_report
    summary = status_summary or live_summary
    request_summary = status_request_summary or live_request_summary
    hotload_preflight = status_hotload_preflight or live_hotload_preflight
    gate = _dict(report.get("normality_gate")) or _dict(live.get("normality_gate"))
    delivery = _dict(report.get("live_delivery")) or _dict(summary.get("live_delivery"))
    contract = _dict(report.get("live_modeling_contract")) or _dict(summary.get("live_modeling_contract"))
    visual_normality = _dict(report.get("visual_normality_summary")) or _dict(
        summary.get("visual_normality_summary")
    )
    live_gui_window_binding = _dict(summary.get("live_gui_window_binding")) or _dict(
        report.get("live_gui_window_binding")
    )
    mcp_client_readiness = _dict(report.get("mcp_client_readiness")) or _dict(
        summary.get("mcp_client_readiness")
    )
    live_gui_acceptance = _dict(live.get("live_gui_acceptance")) or _dict(report.get("live_gui_acceptance"))
    diagnostics = _dict(report.get("diagnostics"))
    gui = _dict(report.get("gui"))
    gui_current_revision = _dict(report.get("gui_current_revision")) or _dict(
        live.get("gui_current_revision")
    )
    health = _dict((status or {}).get("modeling_health")) or _dict(live.get("modeling_health"))
    template_profile = _dict(live.get("semiconductor_template_profile")) or _dict(
        report.get("semiconductor_template_profile")
    )
    semiconductor_template_id = _first_not_none(
        summary.get("semiconductor_template_id"),
        report.get("semiconductor_template_id"),
        template_profile.get("template_id"),
        nl_plan.get("template_id"),
    )
    base_report = _dict((base_live or {}).get("modeling_report"))
    base_summary = _dict((base_live or {}).get("live_summary")) or _dict(base_report.get("live_summary"))
    base_nl_plan = _dict((base_live or {}).get("nl_plan"))
    base_template_profile = _dict((base_live or {}).get("semiconductor_template_profile")) or _dict(
        base_report.get("semiconductor_template_profile")
    )
    base_semiconductor_template_id = _first_not_none(
        base_summary.get("semiconductor_template_id"),
        base_report.get("semiconductor_template_id"),
        base_template_profile.get("template_id"),
        base_nl_plan.get("template_id"),
    )
    scenario_virtual_template_id = SCENARIO_VIRTUAL_TEMPLATE_IDS.get(str(scenario or ""))
    scenario_semiconductor_template_id = _first_not_none(
        scenario_virtual_template_id,
        base_semiconductor_template_id,
        semiconductor_template_id,
    )
    base_semiconductor_virtual_template_id = scenario_virtual_template_id if base_live is not None else None
    base_effective_semiconductor_template_id = _first_not_none(
        base_semiconductor_virtual_template_id,
        base_semiconductor_template_id,
    )
    bundle_files = _dict((bundle or {}).get("files")) or _dict((bundle or {}).get("view_bundle_files"))
    live_files = _dict(live.get("view_bundle_files")) or _dict(diagnostics)
    manifest_path = (
        (bundle or {}).get("manifest_path")
        or (bundle or {}).get("view_bundle_manifest_path")
        or live.get("view_bundle_manifest_path")
        or diagnostics.get("view_bundle_manifest_path")
    )
    view_bundle_files = _compact_file_map(bundle_files or live_files)
    view_bundle_row_counts = _dict(
        (bundle or {}).get("row_counts")
        or (bundle or {}).get("view_bundle_row_counts")
        or live.get("view_bundle_row_counts")
        or diagnostics.get("view_bundle_row_counts")
        or {}
    )
    expected_diagnostics = _follow_up_expected_diagnostics(
        scenario=scenario,
        follow_up_preset=follow_up_preset,
        row_counts=view_bundle_row_counts,
        files=view_bundle_files,
        bundle_available=bundle is not None,
    )
    scenario_expected_diagnostics = _scenario_expected_diagnostics(
        scenario=scenario_expectation if scenario_expectation is not None else scenario,
        row_counts=view_bundle_row_counts,
        files=view_bundle_files,
        bundle_available=bundle is not None,
    )
    execution_mode = live.get("execution_mode") or report.get("execution_mode")
    gui_hot_loaded = _first_not_none(gui.get("hot_loaded"), summary.get("hot_loaded"))
    current_revision_loaded_in_gui = _first_not_none(
        summary.get("current_revision_loaded_in_gui"),
        summary.get("loaded_current_revision"),
        summary.get("mcp_current_revision_loaded_in_gui"),
        gui.get("loaded_current_revision"),
        summary.get("gui_loaded_current_revision"),
    )
    gui_loaded_current_revision = current_revision_loaded_in_gui
    snapshot_viewport_likely_visible_model = _first_not_none(
        gui.get("snapshot_viewport_likely_visible_model"),
        summary.get("snapshot_viewport_likely_visible_model"),
    )
    snapshot_viewport_capture_limitation_possible = _first_not_none(
        gui.get("snapshot_viewport_capture_limitation_possible"),
        summary.get("snapshot_viewport_capture_limitation_possible"),
    )
    gui_window_identity_verification = gui.get("window_identity_verification")
    current_revision_gui_evidence_applicable = _first_not_none(
        summary.get("gui_current_revision_gui_evidence_applicable"),
        gui_current_revision.get("current_revision_gui_evidence_applicable"),
        gui.get("current_revision_gui_evidence_applicable"),
    )
    current_revision_gui_evidence_status = _first_not_none(
        summary.get("gui_current_revision_gui_evidence_status"),
        gui_current_revision.get("current_revision_gui_evidence_status"),
        gui.get("current_revision_gui_evidence_status"),
    )
    if current_revision_gui_evidence_status is None and current_revision_gui_evidence_applicable is not None:
        current_revision_gui_evidence_status = (
            "bound_to_current_revision"
            if current_revision_gui_evidence_applicable
            else "not_bound_to_current_revision"
        )
    current_revision_gui_evidence_sources = _first_not_none(
        summary.get("gui_current_revision_gui_evidence_sources"),
        gui_current_revision.get("current_revision_gui_evidence_sources"),
        gui.get("current_revision_gui_evidence_sources"),
    ) or []
    current_revision_gui_window_identity_verification = (
        "not_applicable_to_current_revision"
        if current_revision_gui_evidence_applicable is False
        else gui_window_identity_verification
    )
    single_window_policy_ok = _first_not_none(
        gui.get("single_window_policy_ok"),
        summary.get("gui_single_window_policy_ok"),
        summary.get("mcp_single_window_policy_ok"),
        summary.get("live_gui_window_binding_single_window_policy_ok"),
        live_gui_window_binding.get("single_window_policy_ok"),
        mcp_client_readiness.get("single_window_policy_ok"),
        live_gui_acceptance.get("single_window_policy_ok"),
    )
    single_window_violation_reasons = _dedupe_strings(
        list(gui.get("single_window_violation_reasons") or [])
        + list(summary.get("gui_single_window_violation_reasons") or [])
        + list(summary.get("mcp_single_window_violation_reasons") or [])
        + list(summary.get("live_gui_window_binding_single_window_violation_reasons") or [])
        + list(live_gui_window_binding.get("single_window_violation_reasons") or [])
        + list(mcp_client_readiness.get("single_window_violation_reasons") or [])
        + list(live_gui_acceptance.get("single_window_violation_reasons") or [])
    )
    same_window_required = _first_not_none(
        summary.get("live_gui_window_binding_same_window_required"),
        live_gui_window_binding.get("same_window_required"),
        summary.get("mcp_must_reuse_existing_gui_window"),
        mcp_client_readiness.get("must_reuse_existing_gui_window"),
    )
    auto_launch_allowed = _first_not_none(
        summary.get("live_gui_window_binding_auto_launch_allowed"),
        live_gui_window_binding.get("auto_launch_allowed"),
        summary.get("mcp_auto_launch_during_hotload_allowed"),
        mcp_client_readiness.get("auto_launch_during_hotload_allowed"),
    )
    gui_process_count = _first_not_none(
        summary.get("live_gui_window_binding_process_count"),
        summary.get("mcp_gui_process_count"),
        live_gui_window_binding.get("process_count"),
        mcp_client_readiness.get("gui_process_count"),
        gui.get("window_management_process_count"),
        gui.get("post_open_window_management_process_count"),
    )
    gui_window_count = _first_not_none(
        summary.get("live_gui_window_binding_window_count"),
        summary.get("mcp_gui_window_count"),
        live_gui_window_binding.get("window_count"),
        mcp_client_readiness.get("gui_window_count"),
        gui.get("window_management_window_count"),
        gui.get("post_open_window_management_window_count"),
    )
    gui_target_window_found = _first_not_none(
        summary.get("live_gui_window_binding_target_window_found"),
        summary.get("mcp_gui_target_window_found"),
        live_gui_window_binding.get("target_window_found"),
        mcp_client_readiness.get("gui_target_window_found"),
        gui.get("target_window_matched_project_window"),
    )
    gui_target_window_handle = _first_not_none(
        summary.get("live_gui_window_binding_target_window_handle"),
        summary.get("mcp_gui_target_window_handle"),
        live_gui_window_binding.get("target_window_handle"),
        mcp_client_readiness.get("gui_target_window_handle"),
        gui.get("target_window_handle"),
        gui.get("window_management_target_window_handle"),
    )
    gui_target_window_title = _first_not_none(
        summary.get("live_gui_window_binding_target_window_title"),
        summary.get("mcp_gui_target_window_title"),
        live_gui_window_binding.get("target_window_title"),
        mcp_client_readiness.get("gui_target_window_title"),
        gui.get("target_window_title"),
        gui.get("window_management_target_window_title"),
    )
    gui_target_window_is_selected = _first_not_none(
        summary.get("live_gui_window_binding_target_window_is_selected"),
        summary.get("mcp_gui_target_window_is_selected"),
        live_gui_window_binding.get("target_window_is_selected"),
        mcp_client_readiness.get("gui_target_window_is_selected"),
        gui.get("target_window_is_selected"),
        gui.get("window_management_target_window_is_selected"),
    )
    can_hotload_without_new_window = _first_not_none(
        summary.get("live_gui_window_binding_can_hotload_without_new_window"),
        summary.get("mcp_can_accept_hotload_request_without_new_window"),
        live_gui_window_binding.get("can_hotload_without_new_window"),
        mcp_client_readiness.get("can_accept_hotload_request_without_new_window"),
    )
    can_apply_current_revision_without_new_window = _first_not_none(
        summary.get("live_gui_window_binding_can_apply_current_revision_without_new_window"),
        summary.get("mcp_can_apply_current_revision_without_new_window"),
        live_gui_window_binding.get("can_apply_current_revision_without_new_window"),
        mcp_client_readiness.get("can_apply_current_revision_without_new_window"),
    )
    live_request_explicit_hotload_requested = request_summary.get("explicit_hotload_requested")
    live_hotload_preflight_current_revision_loaded = hotload_preflight.get("current_revision_loaded")
    hotload_acceptance_expected = bool(
        hotload_expected
        or (
            live_request_explicit_hotload_requested
            and execution_mode != ExecutionMode.PREVIEW.value
        )
    )
    hotload_acceptance = _hotload_acceptance(
        hotload_expected=hotload_acceptance_expected,
        snapshot_expected=snapshot_expected,
        execution_mode=execution_mode,
        gui_hot_loaded=gui_hot_loaded,
        gui_loaded_current_revision=gui_loaded_current_revision,
        gui_window_identity_verification=gui_window_identity_verification,
        live_hotload_preflight_current_revision_loaded=live_hotload_preflight_current_revision_loaded,
        snapshot_viewport_likely_visible_model=snapshot_viewport_likely_visible_model,
        snapshot_viewport_capture_limitation_possible=snapshot_viewport_capture_limitation_possible,
        single_window_policy_ok=single_window_policy_ok,
        single_window_violation_reasons=single_window_violation_reasons,
    )
    gui_hotload_gate = _gui_hotload_gate_summary(
        hotload_acceptance=hotload_acceptance,
        live_request_summary=request_summary,
        hotload_preflight=hotload_preflight,
        single_window_policy_ok=single_window_policy_ok,
        single_window_violation_reasons=single_window_violation_reasons,
        execution_mode=execution_mode,
        gui_hot_loaded=gui_hot_loaded,
        gui_loaded_current_revision=gui_loaded_current_revision,
    )
    diagnostic_acceptance = _diagnostic_acceptance_summary(
        manifest_path=manifest_path,
        row_counts=view_bundle_row_counts,
        files=view_bundle_files,
        scenario_expected=scenario_expected_diagnostics,
        follow_up_expected=expected_diagnostics,
        normality=report.get("normality") or summary.get("normality"),
        normality_gate=gate,
        visual_normality=visual_normality,
    )

    return {
        "preflight_state": preflight.get("state"),
        "preflight_recommended_tool": preflight.get("recommended_tool"),
        "preflight_blocking_reasons": preflight.get("blocking_reasons") or [],
        "preflight_review_reasons": preflight.get("review_reasons") or [],
        "live_request_ok": bool(live.get("ok")),
        "status_ok": None if status is None else bool(status.get("ok")),
        "bundle_ok": None if bundle is None else bool(bundle.get("ok")),
        "preexecution_execution_continuation": preexecution_continuation_summary,
        "preexecution_execution_continuation_requested": (
            preexecution_continuation_summary["requested"]
        ),
        "preexecution_execution_continuation_attempted": (
            preexecution_continuation_summary["attempted"]
        ),
        "preexecution_execution_continuation_completed": (
            preexecution_continuation_summary["completed"]
        ),
        "preexecution_execution_continuation_status": (
            preexecution_continuation_summary["status"]
        ),
        "preexecution_execution_continuation_failures": (
            preexecution_continuation_summary["failures"]
        ),
        "preexecution_execution_continuation_apply_call_count": (
            preexecution_continuation_summary[
                "apply_current_revision_call_count"
            ]
        ),
        "base_preexecution_execution_continuation_status": (
            preexecution_continuation_summary["base_status"]
        ),
        "followup_preexecution_execution_continuation_status": (
            preexecution_continuation_summary["followup_status"]
        ),
        "postexecution_hotload_continuation": continuation_summary,
        "postexecution_hotload_continuation_requested": continuation_summary[
            "requested"
        ],
        "postexecution_hotload_continuation_attempted": continuation_summary[
            "attempted"
        ],
        "postexecution_hotload_continuation_completed": continuation_summary[
            "completed"
        ],
        "postexecution_hotload_continuation_status": continuation_summary[
            "status"
        ],
        "postexecution_hotload_continuation_failures": continuation_summary[
            "failures"
        ],
        "base_postexecution_hotload_continuation_status": continuation_summary[
            "base_status"
        ],
        "followup_postexecution_hotload_continuation_status": (
            continuation_summary["followup_status"]
        ),
        "project_id": live.get("project_id") or (status or {}).get("project_id"),
        "scenario": scenario,
        "base_project_id": (base_live or {}).get("project_id"),
        "base_revision": (base_live or {}).get("new_revision", (base_live or {}).get("revision")),
        "base_workflow": (base_live or {}).get("workflow") or base_report.get("workflow"),
        "base_nl_plan_template_id": base_nl_plan.get("template_id"),
        "base_semiconductor_template_id": base_semiconductor_template_id,
        "base_semiconductor_virtual_template_id": base_semiconductor_virtual_template_id,
        "base_effective_semiconductor_template_id": base_effective_semiconductor_template_id,
        "follow_up_requested": base_live is not None,
        "follow_up_preset": follow_up_preset,
        "revision": live.get("new_revision", live.get("revision", (status or {}).get("revision"))),
        "workflow": live.get("workflow") or report.get("workflow"),
        "execution_mode": execution_mode,
        "execution_mode_source": live.get("execution_mode_source") or report.get("execution_mode_source"),
        "nl_plan_kind": nl_plan.get("kind"),
        "nl_plan_template_id": nl_plan.get("template_id"),
        "live_request_state": request_summary.get("state"),
        "live_request_explicit_hotload_requested": live_request_explicit_hotload_requested,
        "live_request_hotload_safe_to_attempt": request_summary.get("hotload_safe_to_attempt"),
        "live_request_recommended_tool": request_summary.get("recommended_tool"),
        "live_hotload_preflight_status": hotload_preflight.get("status"),
        "live_hotload_preflight_safe_to_attempt": hotload_preflight.get("safe_to_attempt_hotload"),
        "live_hotload_preflight_gui_verified": hotload_preflight.get("gui_preflight_verified"),
        "live_hotload_preflight_gui_required": hotload_preflight.get("gui_preflight_required"),
        "live_hotload_preflight_gui_reasons": hotload_preflight.get("gui_preflight_reasons") or [],
        "live_hotload_preflight_model_ready": hotload_preflight.get("model_ready_for_hotload"),
        "live_hotload_preflight_current_revision_loaded": live_hotload_preflight_current_revision_loaded,
        "live_hotload_preflight_recommended_tool": hotload_preflight.get("recommended_tool"),
        "live_hotload_preflight_blocking_reasons": hotload_preflight.get("blocking_reasons") or [],
        "execution_backend": _dict(live.get("result")).get("execution_backend") or report.get("execution_backend"),
        "semiconductor_template_id": semiconductor_template_id,
        "semiconductor_virtual_template_id": scenario_virtual_template_id,
        "scenario_semiconductor_template_id": scenario_semiconductor_template_id,
        "effective_semiconductor_template_id": scenario_semiconductor_template_id,
        "recommended_diagnostic_focuses": (
            live.get("recommended_diagnostic_focuses")
            or report.get("recommended_diagnostic_focuses")
            or summary.get("recommended_diagnostic_focuses")
            or []
        ),
        "unrequested_recommended_diagnostic_focuses": (
            live.get("unrequested_recommended_diagnostic_focuses")
            or report.get("unrequested_recommended_diagnostic_focuses")
            or summary.get("unrequested_recommended_diagnostic_focuses")
            or []
        ),
        "normality": report.get("normality") or summary.get("normality"),
        "normality_gate_status": gate.get("status"),
        "can_claim_model_normal": gate.get("can_claim_model_normal"),
        "can_claim_live_gui_normal": gate.get("can_claim_live_gui_normal"),
        "visual_normality_status": _first_not_none(
            summary.get("visual_normality_status"),
            summary.get("mcp_visual_normality_status"),
            visual_normality.get("status"),
        ),
        "visual_can_report_model_normal": _first_not_none(
            summary.get("visual_can_report_model_normal"),
            summary.get("mcp_visual_can_report_model_normal"),
            visual_normality.get("can_report_model_normal"),
        ),
        "visual_clean_view_available": _first_not_none(
            summary.get("visual_clean_view_available"),
            summary.get("mcp_visual_clean_view_available"),
            visual_normality.get("clean_view_available"),
        ),
        "visual_clean_view_count": _first_not_none(
            summary.get("visual_clean_view_count"),
            visual_normality.get("clean_view_count"),
        ),
        "visual_recommended_view_name": _first_not_none(
            summary.get("visual_recommended_view_name"),
            summary.get("mcp_visual_recommended_view_name"),
            visual_normality.get("recommended_view_name"),
        ),
        "visual_note_reasons": summary.get("visual_note_reasons")
        or visual_normality.get("visual_note_reasons")
        or [],
        "visual_blocking_reasons": summary.get("visual_blocking_reasons")
        or visual_normality.get("blocking_reasons")
        or [],
        "normality_gate_next_action": gate.get("next_action"),
        "normality_gate_calculation_only_reasons": gate.get("calculation_only_review_reasons") or [],
        "calculation_only_review_reasons": summary.get("calculation_only_review_reasons")
        or gate.get("calculation_only_review_reasons")
        or [],
        "live_delivery_status": summary.get("live_delivery_status") or delivery.get("status"),
        "live_delivery_calculation_review_required": _first_not_none(
            summary.get("live_delivery_calculation_review_required"),
            delivery.get("calculation_review_required"),
        ),
        "live_modeling_contract_status": summary.get("live_modeling_contract_status") or contract.get("status"),
        "live_modeling_contract_calculation_review_required": _first_not_none(
            summary.get("live_modeling_contract_calculation_review_required"),
            _dict(contract.get("normality")).get("calculation_review_required"),
        ),
        "modeling_health_verdict": health.get("verdict") or report.get("health_verdict"),
        "ready_for_next_edit": summary.get("ready_for_next_edit"),
        "ready_for_calculation": summary.get("ready_for_calculation"),
        "gui_hot_loaded": gui_hot_loaded,
        "current_revision_loaded_in_gui": current_revision_loaded_in_gui,
        "loaded_current_revision": current_revision_loaded_in_gui,
        "gui_loaded_current_revision": gui_loaded_current_revision,
        "single_window_policy_ok": single_window_policy_ok,
        "single_window_violation_reasons": single_window_violation_reasons,
        "same_window_required": same_window_required,
        "auto_launch_during_hotload_allowed": auto_launch_allowed,
        "can_hotload_without_new_window": can_hotload_without_new_window,
        "can_apply_current_revision_without_new_window": can_apply_current_revision_without_new_window,
        "gui_process_count": gui_process_count,
        "gui_window_count": gui_window_count,
        "gui_target_window_found": gui_target_window_found,
        "gui_target_window_handle": gui_target_window_handle,
        "gui_target_window_title": gui_target_window_title,
        "gui_target_window_is_selected": gui_target_window_is_selected,
        "snapshot_viewport_likely_visible_model": snapshot_viewport_likely_visible_model,
        "snapshot_viewport_foreground_ratio": _first_not_none(
            gui.get("snapshot_viewport_foreground_ratio"),
            summary.get("snapshot_viewport_foreground_ratio"),
        ),
        "snapshot_viewport_capture_limitation_possible": snapshot_viewport_capture_limitation_possible,
        "snapshot_viewport_capture_diagnostic": _first_not_none(
            gui.get("snapshot_viewport_capture_diagnostic"),
            summary.get("snapshot_viewport_capture_diagnostic"),
        ),
        "current_revision_gui_evidence_applicable": current_revision_gui_evidence_applicable,
        "current_revision_gui_evidence_status": current_revision_gui_evidence_status,
        "current_revision_gui_evidence_sources": current_revision_gui_evidence_sources,
        "current_revision_gui_window_identity_verification": (
            current_revision_gui_window_identity_verification
        ),
        "gui_window_identity_verification": gui_window_identity_verification,
        "hotload_acceptance": hotload_acceptance,
        "hotload_acceptance_ok": hotload_acceptance.get("ok"),
        "hotload_acceptance_failures": hotload_acceptance.get("failures") or [],
        "gui_hotload_gate": gui_hotload_gate,
        "gui_hotload_gate_status": gui_hotload_gate.get("status"),
        "gui_hotload_gate_ok": gui_hotload_gate.get("ok"),
        "gui_hotload_gate_blocking_reasons": gui_hotload_gate.get("blocking_reasons") or [],
        "gui_hotload_gate_recommended_tool": gui_hotload_gate.get("recommended_tool"),
        "structure_path": _structure_path(live, report, summary),
        "report_json_path": live.get("report_json_path") or diagnostics.get("report_json_path"),
        "view_audit_report_path": live.get("view_audit_report_path") or diagnostics.get("view_audit_report_path"),
        "view_bundle_manifest_path": manifest_path,
        "view_bundle_manifest_exists": _path_exists(manifest_path),
        "view_bundle_files": view_bundle_files,
        "view_bundle_row_counts": view_bundle_row_counts,
        "diagnostic_acceptance": diagnostic_acceptance,
        "diagnostic_acceptance_status": diagnostic_acceptance.get("status"),
        "diagnostic_acceptance_ok": diagnostic_acceptance.get("ok"),
        "diagnostic_can_check_model_normality": diagnostic_acceptance.get("can_check_model_normality"),
        "diagnostic_basic_view_tables_ok": diagnostic_acceptance.get("basic_view_tables_ok"),
        "diagnostic_basic_view_table_failures": diagnostic_acceptance.get("basic_view_table_failures") or [],
        "diagnostic_row_count_total": diagnostic_acceptance.get("row_count_total"),
        "diagnostic_row_count_keys": diagnostic_acceptance.get("row_count_keys") or [],
        "scenario_expected_diagnostics": scenario_expected_diagnostics,
        "scenario_expected_diagnostics_ok": scenario_expected_diagnostics.get("ok"),
        "scenario_expected_diagnostic_failures": scenario_expected_diagnostics.get("failures") or [],
        "follow_up_expected_diagnostics": expected_diagnostics,
        "follow_up_expected_diagnostics_ok": expected_diagnostics.get("ok"),
        "follow_up_expected_diagnostic_failures": expected_diagnostics.get("failures") or [],
        "next_action_tool": (
            hotload_preflight.get("recommended_tool")
            if hotload_preflight.get("gui_preflight_required")
            else _next_action_tool(summary=summary, report=report, status=status)
        ),
        "next_action": report.get("next_action") or live.get("next_action") or (status or {}).get("next_action"),
        "errors": _collect_errors(preflight, live, status, bundle),
        "warnings": _collect_warnings(preflight, live, status, bundle),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the live smoke CLI and write JSON to stdout or a file."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    result = run_live_smoke(
        request=args.request,
        follow_up_request=args.follow_up_request,
        follow_up_preset=args.follow_up_preset,
        scenario=args.scenario,
        hotload=args.hotload,
        execution_mode=args.execution_mode,
        working_dir=args.working_dir,
        include_gui_status=args.include_gui_status,
        take_snapshot=args.take_snapshot,
        export_bundle=args.export_bundle,
        views=args.views,
        timeout_seconds=args.timeout_seconds,
        resume_deferred_execution=args.resume_deferred_execution,
        resume_deferred_hotload=args.resume_deferred_hotload,
    )
    payload = result if args.include_raw else {"ok": result["ok"], **result["summary"]}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a semiconductor Materials Studio live workflow smoke check.",
    )
    parser.add_argument("--request", help="Natural-language request to send to material_studio_live_modeling_request.")
    parser.add_argument(
        "--follow-up-request",
        help="Optional second natural-language request to apply to the current project after the base request.",
    )
    parser.add_argument(
        "--follow-up-preset",
        help="Named semiconductor follow-up preset for the selected scenario, e.g. silicon:p_dopant or mos2:s_vacancy.",
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIO_REQUESTS),
        default=None,
        help="Named deterministic scenario. Defaults to sic_mos only when --request is omitted.",
    )
    parser.add_argument(
        "--hotload",
        action="store_true",
        help="Use the scenario's hot-load request. With --execution-mode auto this may execute and open the GUI.",
    )
    parser.add_argument("--execution-mode", choices=["auto", "preview", "execute"], default="auto")
    parser.add_argument("--working-dir", help="Optional structured workspace root for the smoke run.")
    parser.add_argument(
        "--views",
        nargs="*",
        help=(
            "Optional audit view names, e.g. front top isometric, crystal_001 crystal_110, "
            "crystal_plane_100 crystal_plane_0001, surface_normal surface_in_plane_1, "
            "or interface_normal interface_in_plane_1."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, help="Optional execution timeout.")
    parser.add_argument(
        "--resume-deferred-execution",
        action="store_true",
        help=(
            "With explicit --execution-mode execute, consume one exact pre-execution "
            "activate/apply continuation after verifying the current revision twice; "
            "never recreate the revision or retry apply automatically."
        ),
    )
    parser.add_argument(
        "--resume-deferred-hotload",
        action="store_true",
        help=(
            "After execution completes, consume one exact activation/open or GUI "
            "report-transaction artifact-open continuation; never rerun modeling, "
            "MaterialsScript, or the open call automatically."
        ),
    )
    parser.add_argument("--output", help="Optional path to write the JSON result.")
    parser.add_argument("--include-raw", action="store_true", help="Include full preflight/live/status/bundle payloads.")
    parser.add_argument("--export-bundle", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-gui-status", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--take-snapshot", action=argparse.BooleanOptionalAction, default=True)
    return parser


def _execution_mode_arg(value: str) -> ExecutionMode | None:
    if value == "auto":
        return None
    return ExecutionMode(value)


def _overall_ok(
    *,
    preflight: dict[str, Any],
    live: dict[str, Any],
    base_live: dict[str, Any] | None = None,
    status: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
) -> bool:
    if not preflight.get("ok") or not live.get("ok"):
        return False
    if base_live is not None and not base_live.get("ok"):
        return False
    if status is not None and not status.get("ok"):
        return False
    if bundle is not None and not bundle.get("ok"):
        return False
    acceptance = _dict((summary or {}).get("hotload_acceptance"))
    if acceptance and acceptance.get("ok") is False:
        return False
    expected = _dict((summary or {}).get("scenario_expected_diagnostics"))
    if expected and expected.get("ok") is False:
        return False
    expected = _dict((summary or {}).get("follow_up_expected_diagnostics"))
    if expected and expected.get("ok") is False:
        return False
    return True


def _hotload_acceptance(
    *,
    hotload_expected: bool,
    snapshot_expected: bool,
    execution_mode: Any,
    gui_hot_loaded: Any,
    gui_loaded_current_revision: Any,
    gui_window_identity_verification: Any,
    live_hotload_preflight_current_revision_loaded: Any,
    snapshot_viewport_likely_visible_model: Any,
    snapshot_viewport_capture_limitation_possible: Any,
    single_window_policy_ok: Any,
    single_window_violation_reasons: list[str],
) -> dict[str, Any]:
    """Return a strict receipt for smoke runs that requested live GUI hot-loading."""

    if not hotload_expected:
        return {"available": False, "reason": "hotload_not_requested"}

    failures: list[dict[str, Any]] = []
    if execution_mode != "execute":
        failures.append({"type": "execution_mode_not_execute", "observed": execution_mode})
    if gui_hot_loaded is not True:
        failures.append({"type": "gui_not_hot_loaded", "observed": gui_hot_loaded})
    if gui_loaded_current_revision is not True:
        failures.append({"type": "gui_current_revision_not_loaded", "observed": gui_loaded_current_revision})
    if live_hotload_preflight_current_revision_loaded is not True:
        failures.append(
            {
                "type": "hotload_preflight_current_revision_not_loaded",
                "observed": live_hotload_preflight_current_revision_loaded,
            }
        )
    if gui_window_identity_verification != "verified":
        failures.append(
            {
                "type": "gui_window_identity_not_verified",
                "observed": gui_window_identity_verification,
            }
        )
    if single_window_policy_ok is not True:
        failures.append(
            {
                "type": "single_window_policy_not_verified",
                "observed": single_window_policy_ok,
                "reasons": single_window_violation_reasons,
            }
        )
    if snapshot_expected and snapshot_viewport_likely_visible_model is not True:
        failures.append(
            {
                "type": "snapshot_viewport_model_not_visible",
                "observed": snapshot_viewport_likely_visible_model,
            }
        )
    if snapshot_expected and snapshot_viewport_capture_limitation_possible is True:
        failures.append(
            {
                "type": "snapshot_viewport_capture_limitation_possible",
                "observed": snapshot_viewport_capture_limitation_possible,
            }
        )

    return {
        "available": True,
        "ok": not failures,
        "status": "passed" if not failures else "failed",
        "snapshot_expected": bool(snapshot_expected),
        "required": {
            "execution_mode": "execute",
            "gui_hot_loaded": True,
            "gui_loaded_current_revision": True,
            "live_hotload_preflight_current_revision_loaded": True,
            "gui_window_identity_verification": "verified",
            "single_window_policy_ok": True,
            "snapshot_viewport_likely_visible_model": True if snapshot_expected else None,
            "snapshot_viewport_capture_limitation_possible": False if snapshot_expected else None,
        },
        "observed": {
            "execution_mode": execution_mode,
            "gui_hot_loaded": gui_hot_loaded,
            "gui_loaded_current_revision": gui_loaded_current_revision,
            "live_hotload_preflight_current_revision_loaded": live_hotload_preflight_current_revision_loaded,
            "gui_window_identity_verification": gui_window_identity_verification,
            "single_window_policy_ok": single_window_policy_ok,
            "single_window_violation_reasons": single_window_violation_reasons,
            "snapshot_viewport_likely_visible_model": snapshot_viewport_likely_visible_model,
            "snapshot_viewport_capture_limitation_possible": snapshot_viewport_capture_limitation_possible,
        },
        "failures": failures,
    }


def _gui_hotload_gate_summary(
    *,
    hotload_acceptance: dict[str, Any],
    live_request_summary: dict[str, Any],
    hotload_preflight: dict[str, Any],
    single_window_policy_ok: Any,
    single_window_violation_reasons: list[str],
    execution_mode: Any,
    gui_hot_loaded: Any,
    gui_loaded_current_revision: Any,
) -> dict[str, Any]:
    """Return one compact decision surface for live GUI hot-load readiness."""

    acceptance_available = hotload_acceptance.get("available") is True
    acceptance_ok = hotload_acceptance.get("ok")
    gui_preflight_required = bool(hotload_preflight.get("gui_preflight_required"))
    safe_to_attempt = _first_not_none(
        hotload_preflight.get("safe_to_attempt_hotload"),
        live_request_summary.get("hotload_safe_to_attempt"),
    )
    current_revision_loaded = _first_not_none(
        gui_loaded_current_revision,
        hotload_preflight.get("current_revision_loaded"),
    )
    recommended_tool = _first_not_none(
        hotload_preflight.get("recommended_tool"),
        live_request_summary.get("recommended_tool"),
    )

    blocking_reasons: list[Any] = []
    if single_window_policy_ok is False:
        blocking_reasons.extend(single_window_violation_reasons or ["single_window_policy_not_verified"])
        recommended_tool = "material_studio_gui_status"
    blocking_reasons.extend(hotload_preflight.get("blocking_reasons") or [])
    if acceptance_available and acceptance_ok is False:
        for failure in hotload_acceptance.get("failures") or []:
            if isinstance(failure, dict):
                blocking_reasons.append(failure.get("type"))
            else:
                blocking_reasons.append(failure)
    blocking_reasons = _dedupe_strings(blocking_reasons)

    if acceptance_available and acceptance_ok is True:
        status = "accepted"
        ok: bool | None = True
        next_action = "current_revision_loaded"
    elif blocking_reasons:
        status = "blocked"
        ok = False
        next_action = "resolve_blocking_reasons"
    elif gui_hot_loaded is True and current_revision_loaded is True:
        status = "current_revision_loaded"
        ok = True
        next_action = "ready_for_visual_review_or_next_edit"
    elif gui_preflight_required:
        status = "preflight_required"
        ok = False
        recommended_tool = "material_studio_gui_status"
        next_action = "verify_single_window_gui_preflight"
    elif safe_to_attempt is True:
        status = "ready_to_attempt"
        ok = True
        recommended_tool = recommended_tool or "material_studio_gui_apply_current_revision"
        next_action = "apply_current_revision_with_execute_when_confirmed"
    else:
        status = "not_requested"
        ok = None
        next_action = "request_hotload_or_continue_preview"

    return {
        "status": status,
        "ok": ok,
        "acceptance_available": acceptance_available,
        "acceptance_ok": acceptance_ok,
        "safe_to_attempt_hotload": safe_to_attempt,
        "gui_preflight_verified": hotload_preflight.get("gui_preflight_verified"),
        "gui_preflight_required": gui_preflight_required,
        "gui_preflight_reasons": hotload_preflight.get("gui_preflight_reasons") or [],
        "model_ready_for_hotload": hotload_preflight.get("model_ready_for_hotload"),
        "execution_mode": execution_mode,
        "gui_hot_loaded": gui_hot_loaded,
        "gui_loaded_current_revision": gui_loaded_current_revision,
        "current_revision_loaded": current_revision_loaded,
        "single_window_policy_ok": single_window_policy_ok,
        "single_window_violation_reasons": single_window_violation_reasons,
        "blocking_reasons": blocking_reasons,
        "recommended_tool": recommended_tool,
        "next_action": next_action,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _next_action_tool(
    *,
    summary: dict[str, Any],
    report: dict[str, Any],
    status: dict[str, Any] | None,
) -> str | None:
    if summary.get("next_action_tool"):
        return str(summary["next_action_tool"])
    report_plan = _dict(report.get("next_action_plan"))
    if report_plan.get("recommended_tool"):
        return str(report_plan["recommended_tool"])
    status_plan = _dict((status or {}).get("next_action_plan"))
    if status_plan.get("recommended_tool"):
        return str(status_plan["recommended_tool"])
    return None


def _structure_path(live: dict[str, Any], report: dict[str, Any], summary: dict[str, Any]) -> str | None:
    structure = _dict(report.get("structure"))
    planned_outputs = _dict(live.get("planned_outputs"))
    outputs = _dict(live.get("outputs"))
    return (
        summary.get("structure_path")
        or structure.get("path")
        or planned_outputs.get("structure")
        or outputs.get("structure_path")
    )


def _compact_file_map(files: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in files.items() if isinstance(value, str)}


def _path_exists(path: Any) -> bool:
    if not path:
        return False
    try:
        return Path(str(path)).exists()
    except OSError:
        return False


def _follow_up_expected_diagnostics(
    *,
    scenario: str | None,
    follow_up_preset: str | None,
    row_counts: dict[str, Any],
    files: dict[str, str],
    bundle_available: bool,
) -> dict[str, Any]:
    """Evaluate preset-specific diagnostic exports from a smoke bundle."""

    if not scenario or not follow_up_preset:
        return {"available": False, "reason": "no_follow_up_preset"}
    expectation = _dict(_dict(FOLLOW_UP_EXPECTATIONS.get(scenario)).get(follow_up_preset))
    if not expectation:
        return {
            "available": False,
            "reason": "no_expectation_for_preset",
            "scenario": scenario,
            "follow_up_preset": follow_up_preset,
        }
    return _evaluate_expected_diagnostics(
        expectation=expectation,
        row_counts=row_counts,
        files=files,
        bundle_available=bundle_available,
        scenario=scenario,
        follow_up_preset=follow_up_preset,
    )


def _scenario_expected_diagnostics(
    *,
    scenario: str | None,
    row_counts: dict[str, Any],
    files: dict[str, str],
    bundle_available: bool,
) -> dict[str, Any]:
    """Evaluate scenario-level diagnostic exports from a smoke bundle."""

    if not scenario:
        return {"available": False, "reason": "no_scenario"}
    expectation = _dict(SCENARIO_EXPECTATIONS.get(scenario))
    if not expectation:
        return {
            "available": False,
            "reason": "no_expectation_for_scenario",
            "scenario": scenario,
        }
    return _evaluate_expected_diagnostics(
        expectation=expectation,
        row_counts=row_counts,
        files=files,
        bundle_available=bundle_available,
        scenario=scenario,
    )


def _evaluate_expected_diagnostics(
    *,
    expectation: dict[str, Any],
    row_counts: dict[str, Any],
    files: dict[str, str],
    bundle_available: bool,
    scenario: str,
    follow_up_preset: str | None = None,
) -> dict[str, Any]:
    if not bundle_available:
        response = {
            "available": True,
            "ok": None,
            "status": "not_evaluated",
            "reason": "bundle_export_disabled",
            "scenario": scenario,
            "expected_row_counts": _dict(expectation.get("row_counts")),
            "expected_files": list(expectation.get("files") or []),
            "failures": [],
        }
        if follow_up_preset is not None:
            response["follow_up_preset"] = follow_up_preset
        return response

    expected_row_counts = {str(key): int(value) for key, value in _dict(expectation.get("row_counts")).items()}
    expected_files = [str(item) for item in expectation.get("files") or []]
    failures: list[dict[str, Any]] = []
    observed_row_counts: dict[str, Any] = {}
    observed_files: dict[str, str] = {}

    for key, minimum in expected_row_counts.items():
        observed = row_counts.get(key, 0)
        try:
            observed_int = int(observed)
        except (TypeError, ValueError):
            observed_int = 0
        observed_row_counts[key] = observed_int
        if observed_int < minimum:
            failures.append(
                {
                    "type": "row_count_below_minimum",
                    "key": key,
                    "expected_minimum": minimum,
                    "observed": observed,
                }
            )

    for key in expected_files:
        path = files.get(key)
        if path:
            observed_files[key] = path
        if not path or not _path_exists(path):
            failures.append(
                {
                    "type": "missing_file",
                    "key": key,
                    "path": path,
                }
            )

    return {
        "available": True,
        "ok": not failures,
        "status": "passed" if not failures else "failed",
        "scenario": scenario,
        **({"follow_up_preset": follow_up_preset} if follow_up_preset is not None else {}),
        "expected_row_counts": expected_row_counts,
        "observed_row_counts": observed_row_counts,
        "expected_files": expected_files,
        "observed_files": observed_files,
        "failures": failures,
    }


def _diagnostic_acceptance_summary(
    *,
    manifest_path: str | Path | None,
    row_counts: dict[str, Any],
    files: dict[str, str],
    scenario_expected: dict[str, Any],
    follow_up_expected: dict[str, Any],
    normality: Any,
    normality_gate: dict[str, Any],
    visual_normality: dict[str, Any],
) -> dict[str, Any]:
    """Return one compact gate for view-bundle diagnostics and model-normality evidence."""

    basic_requirements = {"view_summary": 1, "view_quality": 1, "view_projections": 1}
    basic_failures: list[dict[str, Any]] = []
    observed_basic: dict[str, int] = {}
    for key, minimum in basic_requirements.items():
        observed_raw = row_counts.get(key, 0)
        try:
            observed = int(observed_raw)
        except (TypeError, ValueError):
            observed = 0
        observed_basic[key] = observed
        if observed < minimum:
            basic_failures.append(
                {
                    "type": "row_count_below_minimum",
                    "key": key,
                    "expected_minimum": minimum,
                    "observed": observed_raw,
                }
            )

    scenario_failures = _expected_diagnostic_failures("scenario", scenario_expected)
    follow_up_failures = _expected_diagnostic_failures("follow_up", follow_up_expected)
    expected_not_evaluated = any(
        _dict(item).get("status") == "not_evaluated"
        for item in (scenario_expected, follow_up_expected)
        if _dict(item).get("available") is True
    )
    manifest_exists = _path_exists(manifest_path)
    exported = bool(manifest_exists or row_counts or files)
    normality_signal_available = bool(
        normality
        or normality_gate.get("status")
        or visual_normality.get("status")
    )
    failures = [*basic_failures, *scenario_failures, *follow_up_failures]

    if not exported:
        ok: bool | None = False
        status = "diagnostics_not_exported"
    elif failures:
        ok = False
        status = "diagnostics_failed"
    elif expected_not_evaluated:
        ok = None
        status = "diagnostics_exported_expected_checks_not_evaluated"
    else:
        ok = True
        status = "diagnostics_ready"

    return {
        "available": True,
        "ok": ok,
        "status": status,
        "can_check_model_normality": bool(ok is True and normality_signal_available),
        "normality_signal_available": normality_signal_available,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_exists": manifest_exists,
        "file_count": len(files),
        "row_count_total": _row_count_total(row_counts),
        "row_count_keys": sorted(str(key) for key in row_counts),
        "basic_view_requirements": basic_requirements,
        "basic_view_observed_row_counts": observed_basic,
        "basic_view_tables_ok": not basic_failures,
        "basic_view_table_failures": basic_failures,
        "scenario_expected_status": scenario_expected.get("status"),
        "scenario_expected_ok": scenario_expected.get("ok"),
        "follow_up_expected_status": follow_up_expected.get("status"),
        "follow_up_expected_ok": follow_up_expected.get("ok"),
        "expected_checks_not_evaluated": expected_not_evaluated,
        "failure_count": len(failures),
        "failures": failures,
    }


def _expected_diagnostic_failures(prefix: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return []
    failures: list[dict[str, Any]] = []
    for failure in payload.get("failures") or []:
        if isinstance(failure, dict):
            failures.append({"source": prefix, **failure})
        else:
            failures.append({"source": prefix, "type": "failure", "value": str(failure)})
    return failures


def _row_count_total(row_counts: dict[str, Any]) -> int:
    total = 0
    for value in row_counts.values():
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
    return total


def _collect_errors(*payloads: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            errors.append(str(payload["error"]))
        for item in payload.get("errors") or []:
            errors.append(str(item))
    return errors


def _collect_warnings(*payloads: dict[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("warnings", "blocking_reasons", "review_reasons"):
            for item in payload.get(key) or []:
                warning = str(item)
                if warning in seen:
                    continue
                seen.add(warning)
                warnings.append(warning)
    return warnings


if __name__ == "__main__":
    raise SystemExit(main())
