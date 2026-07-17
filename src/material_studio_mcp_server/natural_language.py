"""Conservative local natural-language planning for live modeling tools."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .specs.castep import CastepTask, normalize_castep_task
from .specs.common import ELEMENTS
from .specs.crystal import BasisAtomSpec, CrystalSpec, LatticeSpec
from .specs.molecule import MoleculeSpec
from .specs.patch import (
    SemanticPatch,
    apply_semantic_patch,
    commensurate_twist_angle_degrees,
    rotate_crystal_atom_set,
)
from .specs.project import ModelSpec


EXAMPLES_DIR = Path(__file__).with_name("examples")
_CJK_SURFACE_INTENT_TERMS: tuple[str, ...] = ("\u8868\u9762", "surface", "slab", "(001)", "(0001)")

TEMPLATE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "template_id": "benzene",
        "example": "benzene_spec.json",
        "terms": ('benzene', 'c6h6'),
        "notes": "Planar benzene molecule with Forcite geometry optimization settings.",
    },
    {
        "template_id": "water",
        "example": "forcite_opt_spec.json",
        "terms": ('water', 'h2o'),
        "notes": "Water molecule with Forcite geometry optimization settings.",
    },
    {
        "template_id": "methane",
        "example": "methane_spec.json",
        "terms": ('methane', 'ch4'),
        "notes": "Tetrahedral methane molecule with Forcite geometry optimization settings.",
    },
    {
        "template_id": "ammonia",
        "example": "ammonia_spec.json",
        "terms": ('ammonia', 'nh3'),
        "notes": "Trigonal-pyramidal ammonia molecule with Forcite geometry optimization settings.",
    },
    {
        "template_id": "carbon_dioxide",
        "example": "carbon_dioxide_spec.json",
        "terms": ('carbon dioxide', 'co2'),
        "notes": "Linear carbon dioxide molecule with Forcite geometry optimization settings.",
    },
    {
        "template_id": "graphene_vacancy",
        "example": "graphene_vacancy_spec.json",
        "terms": ('graphene vacancy', 'vacancy graphene', 'graphene defect'),
        "notes": "Small graphene-vacancy crystal example with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_100_slab",
        "example": "silicon_100_slab_spec.json",
        "terms": (
            'silicon surface',
            'silicon slab',
            'silicon 100',
            'silicon (100)',
            'si surface',
            'si slab',
            'si 100',
            'si(100)',
            'si (100)',
        ),
        "notes": "Unpassivated Si(100) slab starting point with vacuum along c.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_arsenide_001_slab",
        "example": "gallium_arsenide_001_slab_spec.json",
        "terms": (
            'gaas surface',
            'gaas slab',
            'gaas 001',
            'gaas(001)',
            'gaas (001)',
            'gallium arsenide surface',
            'gallium arsenide slab',
            'gallium arsenide 001',
            'gallium arsenide (001)',
        ),
        "notes": "Unpassivated GaAs(001) slab starting point with vacuum along c.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_nitride_0001_slab",
        "example": "gallium_nitride_0001_slab_spec.json",
        "terms": (
            'gan surface',
            'gan slab',
            'gan 0001',
            'gan(0001)',
            'gan (0001)',
            'gallium nitride surface',
            'gallium nitride slab',
            'gallium nitride 0001',
            'gallium nitride (0001)',
        ),
        "notes": "Unpassivated GaN(0001) slab starting point with vacuum along c.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_nitride_0001_slab",
        "example": "aluminum_nitride_0001_slab_spec.json",
        "terms": (
            'aln surface',
            'aln slab',
            'aln 0001',
            'aln(0001)',
            'aln (0001)',
            'aluminum nitride surface',
            'aluminum nitride slab',
            'aluminum nitride 0001',
            'aluminum nitride (0001)',
            'aluminium nitride surface',
            'aluminium nitride slab',
        ),
        "notes": "Unpassivated AlN(0001) slab starting point with vacuum along c.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_nitride_0001_slab",
        "example": "indium_nitride_0001_slab_spec.json",
        "terms": (
            'inn surface',
            'inn slab',
            'inn 0001',
            'inn(0001)',
            'inn (0001)',
            'indium nitride surface',
            'indium nitride slab',
            'indium nitride 0001',
            'indium nitride (0001)',
        ),
        "notes": "Unpassivated InN(0001) slab starting point with vacuum along c.",
        "domain": "semiconductor",
    },
    {
        "template_id": "zinc_oxide_0001_slab",
        "example": "zinc_oxide_0001_slab_spec.json",
        "terms": (
            'zno surface',
            'zno slab',
            'zno 0001',
            'zno(0001)',
            'zno (0001)',
            'zinc oxide surface',
            'zinc oxide slab',
            'zinc oxide 0001',
            'zinc oxide (0001)',
        ),
        "notes": "Unpassivated ZnO(0001) slab starting point with vacuum along c.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_germanium_001_heterostructure",
        "example": "silicon_germanium_001_heterostructure_spec.json",
        "terms": (
            'silicon germanium',
            'silicon-germanium',
            'si ge',
            'si/ge',
            'sige',
            'si-ge',
            'silicon germanium heterostructure',
            'silicon germanium interface',
            'silicon germanium superlattice',
            'silicon germanium quantum well',
            'silicon germanium mqw',
            'sige heterostructure',
            'sige interface',
            'sige superlattice',
            'sige quantum well',
            'sige mqw',
            'si ge heterostructure',
            'si ge interface',
            'si ge superlattice',
            'si ge quantum well',
            'si ge mqw',
            'si/ge heterostructure',
            'si/ge interface',
            'si/ge superlattice',
            'si/ge quantum well',
            'si/ge mqw',
            '\u7845\u9517',
            '\u7845/\u9517',
            '\u7845-\u9517',
            '\u7845 \u9517',
            '\u9517\u7845',
            '\u7845\u9517\u5f02\u8d28\u7ed3',
            '\u7845\u9517\u5f02\u8d28\u7ed3\u6784',
            '\u7845\u9517\u754c\u9762',
            '\u7845\u9517\u8d85\u6676\u683c',
            '\u7845\u9517\u91cf\u5b50\u9631',
            '\u7845/\u9517\u5f02\u8d28\u7ed3',
            '\u7845/\u9517\u754c\u9762',
            '\u7845-\u9517\u8d85\u6676\u683c',
        ),
        "notes": "Coherent Si/Ge(001) diamond-cubic heterostructure starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "copper_silicon_dioxide_100_interface",
        "example": "copper_silicon_dioxide_100_interface_spec.json",
        "terms": (
            'cu/sio2',
            'cu-sio2',
            'cu sio2',
            'cu silicon dioxide',
            'cu/silicon dioxide',
            'copper sio2',
            'copper/sio2',
            'copper silicon dioxide',
            'copper/silicon dioxide',
            'copper silicon dioxide interface',
            'copper/silicon dioxide interface',
            'copper silicon dioxide heterostructure',
            'cu/sio2 interface',
            'cu-sio2 interface',
            'cu sio2 interface',
            '\u94dc/\u4e8c\u6c27\u5316\u7845',
            '\u94dc-\u4e8c\u6c27\u5316\u7845',
            '\u94dc\u4e8c\u6c27\u5316\u7845',
            '\u94dc/\u4e8c\u6c27\u5316\u7845\u754c\u9762',
            '\u94dc\u4e8c\u6c27\u5316\u7845\u754c\u9762',
        ),
        "notes": "Cu(100)/beta-cristobalite SiO2(100) metal/oxide interface starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_silicon_100_schottky_contact",
        "example": "aluminum_silicon_100_schottky_contact_spec.json",
        "terms": (
            'al/si schottky',
            'al-si schottky',
            'al si schottky',
            'aluminum silicon schottky',
            'aluminum/silicon schottky',
            'aluminum-silicon schottky',
            'al/si schottky contact',
            'al-si schottky contact',
            'al si schottky contact',
            'aluminum silicon schottky contact',
            'aluminum silicon schottky diode',
            'aluminum silicon contact',
            'al/si contact',
            'al-si contact',
            'al si contact',
            'aluminum silicon metal semiconductor contact',
            'metal semiconductor contact',
            'metal-semiconductor contact',
            'schottky contact',
            'schottky diode',
            '\u94dd/\u7845\u8096\u7279\u57fa',
            '\u94dd-\u7845\u8096\u7279\u57fa',
            '\u94dd\u7845\u8096\u7279\u57fa',
            '\u94dd/\u7845\u8096\u7279\u57fa\u63a5\u89e6',
            '\u94dd\u7845\u8096\u7279\u57fa\u63a5\u89e6',
            '\u91d1\u5c5e\u534a\u5bfc\u4f53\u63a5\u89e6',
            '\u91d1\u5c5e-\u534a\u5bfc\u4f53\u63a5\u89e6',
            '\u91d1\u5c5e/\u534a\u5bfc\u4f53\u63a5\u89e6',
            '\u91d1\u5c5e \u534a\u5bfc\u4f53\u63a5\u89e6',
            '\u91d1\u534a\u63a5\u89e6',
            '\u91d1\u534a\u8096\u7279\u57fa',
            '\u91d1\u5c5e\u534a\u5bfc\u4f53\u8096\u7279\u57fa\u63a5\u89e6',
            '\u8096\u7279\u57fa\u63a5\u89e6',
            '\u8096\u7279\u57fa\u4e8c\u6781\u7ba1',
        ),
        "notes": "Al/Si(100) Schottky metal/semiconductor contact starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor",
        "example": "titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor_spec.json",
        "terms": (
            'tin/hfo2/si',
            'tin-hfo2-si',
            'tin hfo2 si',
            'titanium nitride hafnium dioxide silicon',
            'titanium nitride/hafnium dioxide/silicon',
            'titanium nitride gate hfo2 silicon',
            'tin gate hfo2 si',
            'tin hfo2 silicon',
            'hfo2 mos capacitor',
            'hfo2 gate stack',
            'hfo2 high-k gate stack',
            'hfo2 mosfet',
            'hfo2 mosfet gate stack',
            'high-k mosfet',
            'high k mosfet',
            'high-k mosfet gate stack',
            'high k mosfet gate stack',
            'high-k mos capacitor',
            'high k mos capacitor',
            'high-k gate stack',
            'high k gate stack',
            'high-k oxide gate stack',
            'hafnium dioxide mos capacitor',
            'hafnium dioxide gate stack',
            '\u6c2e\u5316\u949b/\u4e8c\u6c27\u5316\u94ea/\u7845',
            '\u6c2e\u5316\u949b-\u4e8c\u6c27\u5316\u94ea-\u7845',
            '\u6c2e\u5316\u949b\u4e8c\u6c27\u5316\u94ea\u7845',
            '\u6c27\u5316\u94ea',
            '\u6c27\u5316\u94ea\u6805\u4ecb\u8d28',
            '\u6c27\u5316\u94ea mosfet',
            '\u6c27\u5316\u94ea mosfet\u6805\u5806',
            '\u6c27\u5316\u94ea\u9ad8k mosfet',
            '\u6c27\u5316\u94ea\u9ad8k\u6805\u4ecb\u8d28',
            '\u6c27\u5316\u94ea\u9ad8k\u6805\u5806',
            '\u4e8c\u6c27\u5316\u94ea\u6805\u5806\u53e0',
            '\u4e8c\u6c27\u5316\u94ea\u6805\u4ecb\u8d28',
            '\u9ad8k\u6805\u5806\u53e0',
            '\u9ad8-k\u6805\u5806\u53e0',
            '\u9ad8k\u6805\u4ecb\u8d28',
            '\u9ad8-k\u6805\u4ecb\u8d28',
            '\u9ad8\u4ecb\u7535\u6805\u5806',
            '\u9ad8\u4ecb\u7535\u6805\u4ecb\u8d28',
            '\u9ad8\u4ecb\u7535\u5e38\u6570\u6805\u5806',
            '\u9ad8\u4ecb\u7535\u5e38\u6570\u6805\u4ecb\u8d28',
            '\u9ad8\u4ecb\u7535\u5e38\u6570 mos\u7535\u5bb9',
            '\u9ad8\u4ecb\u7535\u5e38\u6570mos\u7535\u5bb9',
            '\u9ad8k mos\u7535\u5bb9',
            '\u9ad8k mosfet',
            '\u9ad8-k mosfet',
            '\u9ad8k mosfet\u6805\u5806',
            '\u9ad8k\u91d1\u6c27\u534a\u7535\u5bb9',
        ),
        "notes": "TiN/HfO2/Si high-k MOS capacitor gate-stack starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor",
        "example": "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor_spec.json",
        "terms": (
            'al/sio2/4h-sic',
            'al-sio2-4h-sic',
            'al sio2 4h sic',
            'al/sio2/sic',
            'al-sio2-sic',
            'al sio2 sic',
            '4h-sic mos capacitor',
            '4h sic mos capacitor',
            'sic mos capacitor',
            'silicon carbide mos capacitor',
            'silicon-carbide mos capacitor',
            '4h-sic gate oxide',
            '4h sic gate oxide',
            'sic gate oxide',
            'sic/sio2 gate stack',
            'sic-sio2 gate stack',
            'sic sio2 gate stack',
            'sic/sio2 mos',
            'sic-sio2 mos',
            'sic sio2 mos',
            '4H-SiC MOS\u7535\u5bb9',
            '4h-sic mos\u7535\u5bb9',
            'sic mos\u7535\u5bb9',
            '\u78b3\u5316\u7845mos\u7535\u5bb9',
            '\u78b3\u5316\u7845 mos\u7535\u5bb9',
            '\u78b3\u5316\u7845\u91d1\u6c27\u534a\u7535\u5bb9',
            '\u78b3\u5316\u7845\u6805\u6c27',
            '\u78b3\u5316\u7845\u6805\u4ecb\u8d28',
            '\u78b3\u5316\u7845/\u4e8c\u6c27\u5316\u7845',
            '\u78b3\u5316\u7845-\u4e8c\u6c27\u5316\u7845',
        ),
        "notes": "Al/SiO2/4H-SiC MOS capacitor gate-stack starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_silicon_dioxide_silicon_mos_capacitor",
        "example": "aluminum_silicon_dioxide_silicon_mos_capacitor_spec.json",
        "terms": (
            'al/sio2/si',
            'al-sio2-si',
            'al sio2 si',
            'aluminum/sio2/silicon',
            'aluminum-sio2-silicon',
            'aluminum sio2 silicon',
            'aluminum silicon dioxide silicon',
            'aluminum/silicon dioxide/silicon',
            'aluminum gate oxide silicon',
            'al gate oxide silicon',
            'aluminum gate sio2 si',
            'al gate sio2 si',
            'metal gate oxide silicon',
            'metal gate sio2 si',
            'mos capacitor',
            'mos gate stack',
            'mos stack',
            'mosfet',
            'silicon mosfet',
            'si mosfet',
            'mosfet gate stack',
            'silicon mosfet gate stack',
            'si mosfet gate stack',
            'mos field effect transistor',
            'mos field-effect transistor',
            'gate stack',
            'gate oxide stack',
            '\u94dd/\u4e8c\u6c27\u5316\u7845/\u7845',
            '\u94dd-\u4e8c\u6c27\u5316\u7845-\u7845',
            '\u94dd\u4e8c\u6c27\u5316\u7845\u7845',
            '\u94dd\u6805\u4e8c\u6c27\u5316\u7845\u7845',
            '\u6805\u5806\u53e0',
            '\u6805\u6781\u5806\u53e0',
            '\u91d1\u5c5e\u6805',
            '\u91d1\u5c5e\u6805\u6c27\u7845',
            '\u91d1\u5c5e\u6c27\u5316\u7269\u534a\u5bfc\u4f53\u7535\u5bb9',
            '\u91d1\u6c27\u534a\u7535\u5bb9',
            'mosfet',
            '\u7845 mosfet',
            '\u7845mosfet',
            'mosfet\u6805\u5806',
            '\u7845 mosfet\u6805\u5806',
            '\u7845mosfet\u6805\u5806',
            'mos \u6676\u4f53\u7ba1',
            'mos\u6676\u4f53\u7ba1',
            '\u573a\u6548\u5e94\u6676\u4f53\u7ba1',
            '\u91d1\u6c27\u534a\u573a\u6548\u5e94\u6676\u4f53\u7ba1',
        ),
        "notes": "Al/SiO2/Si MOS capacitor gate-stack starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_silicon_dioxide_100_interface",
        "example": "silicon_silicon_dioxide_100_interface_spec.json",
        "terms": (
            'si/sio2',
            'si-sio2',
            'si sio2',
            'si/sio2 interface',
            'si-sio2 interface',
            'si sio2 interface',
            'silicon/sio2',
            'silicon-sio2',
            'silicon sio2',
            'silicon/sio2 interface',
            'silicon-sio2 interface',
            'silicon sio2 interface',
            'silicon/silicon dioxide',
            'silicon silicon dioxide',
            'silicon/silicon dioxide interface',
            'silicon silicon dioxide interface',
            'silicon oxide interface',
            'silicon gate oxide',
            'gate oxide interface',
            'mos interface',
            'mos gate oxide',
            '\u7845/\u4e8c\u6c27\u5316\u7845',
            '\u7845-\u4e8c\u6c27\u5316\u7845',
            '\u7845\u4e8c\u6c27\u5316\u7845',
            '\u7845/\u4e8c\u6c27\u5316\u7845\u754c\u9762',
            '\u7845\u4e8c\u6c27\u5316\u7845\u754c\u9762',
            '\u7845\u6c27',
            '\u7845\u6c27\u754c\u9762',
            '\u7845\u6c27\u5316\u5c42',
            '\u7845\u6c27\u5316\u5c42\u754c\u9762',
            '\u7845\u6c27\u5316\u7269\u754c\u9762',
            '\u7845\u6805\u6c27',
            '\u7845\u6805\u6c27\u5c42',
            '\u7845\u6805\u4ecb\u8d28',
            '\u6805\u6c27',
            '\u6805\u6c27\u754c\u9762',
        ),
        "notes": "Si(100)/SiO2 gate-oxide interface starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_carbide_6h_hexagonal",
        "example": "silicon_carbide_6h_hexagonal_spec.json",
        "terms": (
            '6h-sic',
            '6h sic',
            '6h silicon carbide',
            '6h silicon-carbide',
            'sic 6h',
            'silicon carbide 6h',
            'silicon-carbide 6h',
            '6H\u78b3\u5316\u7845',
            '6h\u78b3\u5316\u7845',
            '\u78b3\u5316\u78456h',
            '\u78b3\u5316\u7845 6h',
        ),
        "notes": "6H silicon carbide P63mc hP12 bulk crystal from a reviewed SCXRD refinement with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_carbide_4h_hexagonal",
        "example": "silicon_carbide_4h_hexagonal_spec.json",
        "terms": (
            '4h-sic',
            '4h sic',
            '4h silicon carbide',
            '4h silicon-carbide',
            'hexagonal silicon carbide',
            'hexagonal sic',
            'sic 4h',
            'silicon carbide 4h',
            'silicon-carbide 4h',
            '4H\u78b3\u5316\u7845',
            '4h\u78b3\u5316\u7845',
            '\u516d\u65b9\u78b3\u5316\u7845',
        ),
        "notes": "4H silicon carbide hexagonal hP8 starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_carbide_3c_zincblende",
        "example": "silicon_carbide_3c_zincblende_spec.json",
        "terms": (
            'silicon carbide',
            'silicon-carbide',
            'cubic silicon carbide',
            '3c silicon carbide',
            '3c-sic',
            '3c sic',
            'beta silicon carbide',
            'beta-sic',
        ),
        "notes": "3C silicon carbide zinc-blende conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "hexagonal_boron_nitride_2d_hbn_monolayer",
        "example": "hexagonal_boron_nitride_2d_hbn_monolayer_spec.json",
        "terms": (
            'hexagonal boron nitride',
            'hexagonal boron nitride monolayer',
            'hbn',
            'h-bn',
            'hbn monolayer',
            'h-bn monolayer',
            '2d hbn',
            '2d h-bn',
            'boron nitride monolayer',
            'bn monolayer',
            'hexagonal bn',
            '\u516d\u65b9\u6c2e\u5316\u787c',
            '\u516d\u65b9\u6c2e\u5316\u787c\u5355\u5c42',
            '\u5355\u5c42\u6c2e\u5316\u787c',
            '\u4e8c\u7ef4\u6c2e\u5316\u787c',
        ),
        "notes": "2D h-BN monolayer 2x2 cell with vacuum along c and CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "black_phosphorus_2d_phosphorene_monolayer",
        "example": "black_phosphorus_2d_phosphorene_monolayer_spec.json",
        "terms": (
            'black phosphorus',
            'black phosphorus monolayer',
            'phosphorene',
            'phosphorene monolayer',
            'monolayer phosphorene',
            '2d phosphorene',
            'phosphorene 2d',
            'phosphorene sheet',
            'black phosphorus sheet',
            'black phosphorus semiconductor',
            'phosphorene semiconductor',
            '\u9ed1\u78f7',
            '\u9ed1\u78f7\u5355\u5c42',
            '\u5355\u5c42\u9ed1\u78f7',
            '\u78f7\u70ef',
            '\u78f7\u70ef\u5355\u5c42',
            '\u5355\u5c42\u78f7\u70ef',
            '\u4e8c\u7ef4\u9ed1\u78f7',
            '\u4e8c\u7ef4\u78f7\u70ef',
        ),
        "notes": "2D puckered phosphorene monolayer scaffold with vacuum along c and CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "methylammonium_lead_iodide_mapbi3_perovskite",
        "example": "methylammonium_lead_iodide_mapbi3_perovskite_spec.json",
        "terms": (
            'mapbi3',
            'ma pbi3',
            'ch3nh3pbi3',
            'methylammonium lead iodide',
            'methyl ammonium lead iodide',
            'hybrid halide perovskite',
            'organic inorganic perovskite',
            'organometal halide perovskite',
            'lead iodide perovskite',
            'perovskite solar absorber',
            'perovskite absorber',
            'perovskite solar cell absorber',
            '\u9499\u949b\u77ff',
            '\u9499\u949b\u77ff\u5438\u6536\u5c42',
            '\u9499\u949b\u77ff\u5149\u4f0f',
            '\u94c5\u7898\u9499\u949b\u77ff',
            '\u7532\u80fa\u94c5\u7898',
            '\u7532\u57fa\u94f5\u94c5\u7898',
            '\u7532\u80fa\u94c5\u7898\u9499\u949b\u77ff',
        ),
        "notes": "Conservative cubic MAPbI3 hybrid halide perovskite solar-absorber scaffold with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "boron_nitride_zincblende",
        "example": "boron_nitride_zincblende_spec.json",
        "terms": (
            'boron nitride',
            'cubic boron nitride',
            'zinc blende boron nitride',
            'zinc blende bn',
            'c-bn',
            'cbn',
            'bn',
            'bn crystal',
        ),
        "notes": "Cubic boron nitride zinc-blende conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_pn_junction",
        "example": "silicon_diamond_spec.json",
        "terms": (
            'silicon p-n junction',
            'silicon pn junction',
            'silicon p n junction',
            'si p-n junction',
            'si pn junction',
            'si p n junction',
            'p-n junction silicon',
            'pn junction silicon',
            "\u7845pn\u7ed3",
            "\u7845 pn \u7ed3",
            "\u7845p-n\u7ed3",
            "\u7845 p-n \u7ed3",
            "pn\u7ed3\u7845",
            'pn结',
            'pn 结',
            'p-n结',
            '硅pn结',
            '硅 pn 结',
            '硅 p-n 结',
        ),
        "notes": "Diamond-cubic silicon p-n junction start with deterministic B/P region dopants.",
        "domain": "semiconductor",
    },
    {
        "template_id": "silicon_diamond",
        "example": "silicon_diamond_spec.json",
        "terms": ('silicon', 'silicon crystal', 'si crystal', 'diamond silicon', 'diamond cubic silicon'),
        "notes": "Diamond-cubic silicon conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "germanium_diamond",
        "example": "germanium_diamond_spec.json",
        "terms": ('germanium', 'germanium crystal', 'ge crystal', 'diamond germanium', 'diamond cubic germanium'),
        "notes": "Diamond-cubic germanium conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "diamond_cubic",
        "example": "diamond_cubic_spec.json",
        "terms": (
            'diamond',
            'diamond crystal',
            'diamond semiconductor',
            'diamond cubic carbon',
            'diamond carbon',
            'carbon diamond',
            'carbon diamond crystal',
            'c diamond',
            '\u91d1\u521a\u77f3',
            '\u91d1\u521a\u77f3\u6676\u4f53',
            '\u91d1\u521a\u77f3\u534a\u5bfc\u4f53',
        ),
        "notes": "Diamond-cubic carbon wide-bandgap semiconductor conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_arsenide_aluminum_arsenide_001_heterostructure",
        "example": "gallium_arsenide_aluminum_arsenide_001_heterostructure_spec.json",
        "terms": (
            'gaas alas',
            'gaas/alas',
            'gaas-alas',
            'gaas alas heterostructure',
            'gaas alas interface',
            'gaas alas superlattice',
            'gaas alas quantum well',
            'gaas alas mqw',
            'gaas/alas heterostructure',
            'gaas/alas interface',
            'gaas/alas superlattice',
            'gaas/alas quantum well',
            'gaas/alas mqw',
            'gallium arsenide aluminum arsenide',
            'gallium arsenide aluminium arsenide',
            'gallium arsenide aluminum arsenide heterostructure',
            'gallium arsenide aluminum arsenide superlattice',
            'gallium arsenide aluminum arsenide quantum well',
            'gallium arsenide aluminium arsenide quantum well',
            '\u7837\u5316\u9553/\u7837\u5316\u94dd',
            '\u7837\u5316\u9553-\u7837\u5316\u94dd',
            '\u7837\u5316\u9553\u7837\u5316\u94dd',
            '\u7837\u5316\u9553/\u7837\u5316\u94dd\u5f02\u8d28\u7ed3',
            '\u7837\u5316\u9553\u7837\u5316\u94dd\u5f02\u8d28\u7ed3',
            '\u7837\u5316\u9553/\u7837\u5316\u94dd\u8d85\u6676\u683c',
            '\u7837\u5316\u9553\u7837\u5316\u94dd\u8d85\u6676\u683c',
            '\u7837\u5316\u9553/\u7837\u5316\u94dd\u91cf\u5b50\u9631',
            '\u7837\u5316\u9553\u7837\u5316\u94dd\u91cf\u5b50\u9631',
            '\u94dd\u7837\u5316\u9553/\u7837\u5316\u9553',
            '\u94dd\u7837\u5316\u9553\u7837\u5316\u9553',
            '\u94dd\u9553\u7837/\u7837\u5316\u9553',
            '\u94dd\u9553\u7837\u7837\u5316\u9553',
        ),
        "notes": "Coherent GaAs/AlAs(001) zinc-blende heterostructure starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure",
        "example": "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json",
        "terms": (
            'algan gan',
            'algan/gan',
            'algan-gan',
            'algan gan heterostructure',
            'algan gan interface',
            'algan gan superlattice',
            'algan gan quantum well',
            'algan gan mqw',
            'algan gan hemt',
            'algan gan 2deg',
            'algan gan two dimensional electron gas',
            'algan gan two-dimensional electron gas',
            'algan gan high electron mobility transistor',
            'algan/gan 2deg',
            'algan/gan two dimensional electron gas',
            'algan/gan two-dimensional electron gas',
            'algan/gan high electron mobility transistor',
            'aluminum gallium nitride gallium nitride',
            'aluminum gallium nitride gallium nitride heterostructure',
            'aluminum gallium nitride gallium nitride quantum well',
            'aluminum gallium nitride gallium nitride 2deg',
            'aluminum gallium nitride gallium nitride high electron mobility transistor',
            'al0.25ga0.75n/gan',
            'al0.25ga0.75n gan',
            'al0.25ga0.75n/gan heterostructure',
            'al0.25ga0.75n/gan hemt',
            'al0.25ga0.75n/gan 2deg',
        ),
        "notes": "Coherent Al0.25Ga0.75N/GaN(0001) wurtzite heterostructure starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_nitride_gallium_nitride_0001_heterostructure",
        "example": "aluminum_nitride_gallium_nitride_0001_heterostructure_spec.json",
        "terms": (
            'aln gan',
            'aln/gan',
            'aln-gan',
            'aln gan heterostructure',
            'aln gan interface',
            'aln gan superlattice',
            'aln gan quantum well',
            'aln gan mqw',
            'aln gan hemt',
            'aln gan 2deg',
            'aln gan two dimensional electron gas',
            'aln gan two-dimensional electron gas',
            'aln gan high electron mobility transistor',
            'aln/gan 2deg',
            'aln/gan two dimensional electron gas',
            'aln/gan two-dimensional electron gas',
            'aln/gan high electron mobility transistor',
            'aluminum nitride gallium nitride',
            'aluminium nitride gallium nitride',
            'aluminum nitride gallium nitride heterostructure',
            'aluminum nitride gallium nitride quantum well',
            'aluminum nitride gallium nitride 2deg',
            'aluminum nitride gallium nitride high electron mobility transistor',
            'aln/gan heterostructure',
            'aln/gan hemt',
        ),
        "notes": "Coherent AlN/GaN(0001) wurtzite heterostructure starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_gallium_nitride_gallium_nitride_0001_heterostructure",
        "example": "indium_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json",
        "terms": (
            'ingan gan',
            'ingan/gan',
            'ingan-gan',
            'ingan gan heterostructure',
            'ingan gan interface',
            'ingan gan superlattice',
            'ingan gan quantum well',
            'ingan gan mqw',
            'indium gallium nitride gallium nitride',
            'indium gallium nitride gallium nitride heterostructure',
            'indium gallium nitride gallium nitride quantum well',
            'in0.25ga0.75n/gan',
            'in0.25ga0.75n gan',
            'in0.25ga0.75n/gan heterostructure',
            'in0.25ga0.75n/gan quantum well',
            'in0.25ga0.75n/gan mqw',
        ),
        "notes": "Coherent In0.25Ga0.75N/GaN(0001) wurtzite quantum-well starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_gallium_arsenide_indium_phosphide_001_heterostructure",
        "example": "indium_gallium_arsenide_indium_phosphide_001_heterostructure_spec.json",
        "terms": (
            'ingaas/inp',
            'ingaas-inp',
            'ingaas inp',
            'in ga as inp',
            'in ga as / in p',
            'indium gallium arsenide indium phosphide',
            'indium gallium arsenide/indium phosphide',
            'indium gallium arsenide on indium phosphide',
            'ingaas/inp quantum well',
            'ingaas inp quantum well',
            'ingaas/inp heterostructure',
            'ingaas inp heterostructure',
            'ingaas/inp superlattice',
            'ingaas inp superlattice',
            'InGaAs/InP\u91cf\u5b50\u9631',
            'InGaAs/InP\u5f02\u8d28\u7ed3',
            'InGaAs/InP\u8d85\u6676\u683c',
            '\u94df\u9553\u7837/\u78f7\u5316\u94df',
            '\u94df\u9553\u7837\u78f7\u5316\u94df',
            '\u94df\u9553\u7837/\u78f7\u5316\u94df\u91cf\u5b50\u9631',
            '\u94df\u9553\u7837\u78f7\u5316\u94df\u91cf\u5b50\u9631',
            '\u94df\u9553\u7837/\u78f7\u5316\u94df\u5f02\u8d28\u7ed3',
        ),
        "notes": "Coherent InGaAs/InP(001) zinc-blende quantum-well starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_arsenide_gallium_antimonide_001_heterostructure",
        "example": "indium_arsenide_gallium_antimonide_001_heterostructure_spec.json",
        "terms": (
            'inas/gasb',
            'inas-gasb',
            'inas gasb',
            'indium arsenide gallium antimonide',
            'indium arsenide/gallium antimonide',
            'indium arsenide on gallium antimonide',
            'inas/gasb heterostructure',
            'inas gasb heterostructure',
            'inas/gasb quantum well',
            'inas gasb quantum well',
            'inas/gasb superlattice',
            'inas gasb superlattice',
            'inas/gasb mqw',
            'inas/gasb type ii',
            'inas/gasb type-II',
            'inas/gasb broken gap',
            'inas/gasb broken-gap',
            'broken gap inas gasb',
            'type ii inas gasb quantum well',
            'type-II inas gasb quantum well',
            'InAs/GaSb\u91cf\u5b50\u9631',
            'InAs/GaSb\u5f02\u8d28\u7ed3',
            'InAs/GaSb\u8d85\u6676\u683c',
            '\u7837\u5316\u94df/\u9511\u5316\u9553',
            '\u7837\u5316\u94df-\u9511\u5316\u9553',
            '\u7837\u5316\u94df\u9511\u5316\u9553',
            '\u7837\u5316\u94df/\u9511\u5316\u9553\u91cf\u5b50\u9631',
            '\u7837\u5316\u94df\u9511\u5316\u9553\u91cf\u5b50\u9631',
            '\u7837\u5316\u94df/\u9511\u5316\u9553\u5f02\u8d28\u7ed3',
            '\u7837\u5316\u94df\u9511\u5316\u9553\u5f02\u8d28\u7ed3',
            '\u7837\u5316\u94df/\u9511\u5316\u9553\u8d85\u6676\u683c',
            '\u7837\u5316\u94df\u9511\u5316\u9553\u8d85\u6676\u683c',
            '\u7834\u7f3a\u5e26\u9699InAs/GaSb',
            '\u7834\u7f3a\u5e26\u9699\u7837\u5316\u94df\u9511\u5316\u9553',
            '\u4e8c\u578bInAs/GaSb\u91cf\u5b50\u9631',
        ),
        "notes": "Coherent InAs/GaSb(001) type-II zinc-blende quantum-well starting point with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_arsenide_zincblende",
        "example": "gallium_arsenide_zincblende_spec.json",
        "terms": (
            'gallium arsenide',
            'gaas',
            'gaas crystal',
            'zinc blende gaas',
            '\u7837\u5316\u9553',
            '\u7837\u5316\u9553\u6676\u4f53',
            '\u7837\u5316\u9553\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende gallium arsenide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_arsenide_zincblende",
        "example": "aluminum_arsenide_zincblende_spec.json",
        "terms": (
            'aluminum arsenide',
            'aluminium arsenide',
            'alas',
            'alas crystal',
            'zinc blende alas',
            'zinc blende aluminum arsenide',
            'zinc blende aluminium arsenide',
            '\u7837\u5316\u94dd',
            '\u7837\u5316\u94dd\u6676\u4f53',
            '\u7837\u5316\u94dd\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende aluminum arsenide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_phosphide_zincblende",
        "example": "aluminum_phosphide_zincblende_spec.json",
        "terms": (
            'aluminum phosphide',
            'aluminium phosphide',
            'alp',
            'alp crystal',
            'zinc blende alp',
            'zinc blende aluminum phosphide',
            'zinc blende aluminium phosphide',
            '\u78f7\u5316\u94dd',
            '\u78f7\u5316\u94dd\u6676\u4f53',
            '\u78f7\u5316\u94dd\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende aluminum phosphide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_antimonide_zincblende",
        "example": "aluminum_antimonide_zincblende_spec.json",
        "terms": (
            'aluminum antimonide',
            'aluminium antimonide',
            'alsb',
            'alsb crystal',
            'zinc blende alsb',
            'zinc blende aluminum antimonide',
            'zinc blende aluminium antimonide',
            '\u9511\u5316\u94dd',
            '\u9511\u5316\u94dd\u6676\u4f53',
            '\u9511\u5316\u94dd\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende aluminum antimonide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_phosphide_zincblende",
        "example": "gallium_phosphide_zincblende_spec.json",
        "terms": (
            'gallium phosphide',
            'gap crystal',
            'gap semiconductor',
            'gap zinc blende',
            'zinc blende gap',
            'zinc blende gallium phosphide',
            '\u78f7\u5316\u9553',
            '\u78f7\u5316\u9553\u6676\u4f53',
            '\u78f7\u5316\u9553\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende gallium phosphide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_antimonide_zincblende",
        "example": "gallium_antimonide_zincblende_spec.json",
        "terms": (
            'gallium antimonide',
            'gasb',
            'gasb crystal',
            'zinc blende gasb',
            'zinc blende gallium antimonide',
            '\u9511\u5316\u9553',
            '\u9511\u5316\u9553\u6676\u4f53',
            '\u9511\u5316\u9553\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende gallium antimonide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_phosphide_zincblende",
        "example": "indium_phosphide_zincblende_spec.json",
        "terms": (
            'indium phosphide',
            'inp',
            'inp crystal',
            'zinc blende inp',
            'zinc blende indium phosphide',
            '\u78f7\u5316\u94df',
            '\u78f7\u5316\u94df\u6676\u4f53',
            '\u78f7\u5316\u94df\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende indium phosphide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_arsenide_zincblende",
        "example": "indium_arsenide_zincblende_spec.json",
        "terms": (
            'indium arsenide',
            'inas',
            'inas crystal',
            'zinc blende inas',
            'zinc blende indium arsenide',
            '\u7837\u5316\u94df',
            '\u7837\u5316\u94df\u6676\u4f53',
            '\u7837\u5316\u94df\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende indium arsenide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_antimonide_zincblende",
        "example": "indium_antimonide_zincblende_spec.json",
        "terms": (
            'indium antimonide',
            'insb',
            'insb crystal',
            'zinc blende insb',
            'zinc blende indium antimonide',
            '\u9511\u5316\u94df',
            '\u9511\u5316\u94df\u6676\u4f53',
            '\u9511\u5316\u94df\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende indium antimonide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "cadmium_telluride_zincblende",
        "example": "cadmium_telluride_zincblende_spec.json",
        "terms": (
            'cadmium telluride',
            'cdte',
            'cdte crystal',
            'zinc blende cdte',
            'zinc blende cadmium telluride',
            '\u78b2\u5316\u9549',
            '\u78b2\u5316\u9549\u6676\u4f53',
            '\u78b2\u5316\u9549\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende cadmium telluride conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "zinc_sulfide_zincblende",
        "example": "zinc_sulfide_zincblende_spec.json",
        "terms": (
            'zinc sulfide',
            'zinc sulphide',
            'zns',
            'zns crystal',
            'zinc blende zns',
            'zinc blende zinc sulfide',
            'zinc blende zinc sulphide',
            '\u786b\u5316\u950c',
            '\u786b\u5316\u950c\u6676\u4f53',
            '\u786b\u5316\u950c\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende zinc sulfide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "zinc_selenide_zincblende",
        "example": "zinc_selenide_zincblende_spec.json",
        "terms": (
            'zinc selenide',
            'znse',
            'znse crystal',
            'zinc blende znse',
            'zinc blende zinc selenide',
            '\u7852\u5316\u950c',
            '\u7852\u5316\u950c\u6676\u4f53',
            '\u7852\u5316\u950c\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende zinc selenide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "zinc_telluride_zincblende",
        "example": "zinc_telluride_zincblende_spec.json",
        "terms": (
            'zinc telluride',
            'znte',
            'znte crystal',
            'zinc blende znte',
            'zinc blende zinc telluride',
            '\u78b2\u5316\u950c',
            '\u78b2\u5316\u950c\u6676\u4f53',
            '\u78b2\u5316\u950c\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende zinc telluride conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "cadmium_sulfide_zincblende",
        "example": "cadmium_sulfide_zincblende_spec.json",
        "terms": (
            'cadmium sulfide',
            'cadmium sulphide',
            'cds',
            'cds crystal',
            'zinc blende cds',
            'zinc blende cadmium sulfide',
            'zinc blende cadmium sulphide',
            '\u786b\u5316\u9549',
            '\u786b\u5316\u9549\u6676\u4f53',
            '\u786b\u5316\u9549\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende cadmium sulfide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "cadmium_selenide_zincblende",
        "example": "cadmium_selenide_zincblende_spec.json",
        "terms": (
            'cadmium selenide',
            'cdse',
            'cdse crystal',
            'zinc blende cdse',
            'zinc blende cadmium selenide',
            '\u7852\u5316\u9549',
            '\u7852\u5316\u9549\u6676\u4f53',
            '\u7852\u5316\u9549\u534a\u5bfc\u4f53',
        ),
        "notes": "Zinc-blende cadmium selenide conventional cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "gallium_nitride_wurtzite",
        "example": "gallium_nitride_wurtzite_spec.json",
        "terms": (
            'gallium nitride',
            'gan',
            'gan crystal',
            'wurtzite gan',
            '\u6c2e\u5316\u9553',
            '\u6c2e\u5316\u9553\u6676\u4f53',
            '\u6c2e\u5316\u9553\u534a\u5bfc\u4f53',
        ),
        "notes": "Wurtzite gallium nitride cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "aluminum_nitride_wurtzite",
        "example": "aluminum_nitride_wurtzite_spec.json",
        "terms": (
            'aluminum nitride',
            'aluminium nitride',
            'aln',
            'aln crystal',
            'wurtzite aln',
            'wurtzite aluminum nitride',
            'wurtzite aluminium nitride',
            '\u6c2e\u5316\u94dd',
            '\u6c2e\u5316\u94dd\u6676\u4f53',
            '\u6c2e\u5316\u94dd\u534a\u5bfc\u4f53',
        ),
        "notes": "Wurtzite aluminum nitride cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "indium_nitride_wurtzite",
        "example": "indium_nitride_wurtzite_spec.json",
        "terms": (
            'indium nitride',
            'inn',
            'inn crystal',
            'wurtzite inn',
            'wurtzite indium nitride',
            '\u6c2e\u5316\u94df',
            '\u6c2e\u5316\u94df\u6676\u4f53',
            '\u6c2e\u5316\u94df\u534a\u5bfc\u4f53',
        ),
        "notes": "Wurtzite indium nitride cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "zinc_oxide_wurtzite",
        "example": "zinc_oxide_wurtzite_spec.json",
        "terms": (
            'zinc oxide',
            'zno',
            'zno crystal',
            'wurtzite zno',
            'wurtzite zinc oxide',
            '\u6c27\u5316\u950c',
            '\u6c27\u5316\u950c\u6676\u4f53',
            '\u6c27\u5316\u950c\u534a\u5bfc\u4f53',
        ),
        "notes": "Wurtzite zinc oxide cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "beta_gallium_oxide_monoclinic",
        "example": "beta_gallium_oxide_monoclinic_spec.json",
        "terms": (
            'gallium oxide',
            'gallium oxide crystal',
            'gallium oxide semiconductor',
            'ga2o3',
            'ga2o3 crystal',
            'ga2o3 semiconductor',
            'beta gallium oxide',
            'beta gallium oxide crystal',
            'beta gallium oxide semiconductor',
            'beta-ga2o3',
            'beta ga2o3',
            'monoclinic ga2o3',
            'monoclinic gallium oxide',
            '\u03b2-ga2o3',
            '\u03b2 ga2o3',
            '\u03b2-gallium oxide',
            '\u03b2 gallium oxide',
            '\u6c27\u5316\u9553',
            '\u6c27\u5316\u9553\u6676\u4f53',
            '\u6c27\u5316\u9553\u534a\u5bfc\u4f53',
            '\u03b2-\u6c27\u5316\u9553',
            '\u03b2\u6c27\u5316\u9553',
            '\u03b2-ga2o3\u6c27\u5316\u9553',
        ),
        "notes": "Monoclinic beta-Ga2O3 conventional C2/m cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "beta_gallium_oxide_010_slab",
        "example": "beta_gallium_oxide_010_slab_spec.json",
        "terms": (
            'beta ga2o3 surface',
            'beta-ga2o3 surface',
            'beta ga2o3 slab',
            'beta-ga2o3 slab',
            'beta ga2o3 010',
            'beta-ga2o3 010',
            'beta ga2o3 (010)',
            'beta-ga2o3 (010)',
            'ga2o3 surface',
            'ga2o3 slab',
            'ga2o3 010',
            'ga2o3 (010)',
            'gallium oxide surface',
            'gallium oxide slab',
            'gallium oxide 010',
            'gallium oxide (010)',
            'beta gallium oxide surface',
            'beta gallium oxide slab',
            'beta gallium oxide 010',
            'beta gallium oxide (010)',
            '\u03b2-ga2o3\u8868\u9762',
            '\u03b2 ga2o3\u8868\u9762',
            '\u03b2-ga2o3 slab',
            '\u03b2-ga2o3(010)',
            '\u03b2-ga2o3 (010)',
            '\u6c27\u5316\u9553\u8868\u9762',
            '\u6c27\u5316\u9553slab',
            '\u6c27\u5316\u9553 slab',
            '\u6c27\u5316\u9553(010)',
            '\u6c27\u5316\u9553 (010)',
            '\u03b2-\u6c27\u5316\u9553\u8868\u9762',
            '\u03b2\u6c27\u5316\u9553\u8868\u9762',
            '\u03b2-\u6c27\u5316\u9553(010)',
            '\u03b2\u6c27\u5316\u9553(010)',
        ),
        "notes": "Unrelaxed beta-Ga2O3(010) slab starting point with vacuum along b and CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "alpha_alumina_sapphire_substrate",
        "example": "alpha_alumina_sapphire_substrate_spec.json",
        "terms": (
            'sapphire',
            'sapphire substrate',
            'c-plane sapphire',
            'c plane sapphire',
            'alpha alumina',
            'alpha-alumina',
            'alpha aluminum oxide',
            'alpha aluminium oxide',
            'aluminum oxide substrate',
            'aluminium oxide substrate',
            'al2o3',
            'alpha-al2o3',
            'alpha al2o3',
            '\u03b1-al2o3',
            '\u03b1 al2o3',
            '\u84dd\u5b9d\u77f3',
            '\u84dd\u5b9d\u77f3\u886c\u5e95',
            '\u84dd\u5b9d\u77f3\u57fa\u5e95',
            '\u6c27\u5316\u94dd',
            '\u03b1-\u6c27\u5316\u94dd',
            '\u03b1\u6c27\u5316\u94dd',
            '\u521a\u7389',
            '\u521a\u7389\u6c27\u5316\u94dd',
        ),
        "notes": "Corundum alpha-Al2O3 sapphire substrate conventional hexagonal cell with CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "molybdenum_disulfide_2d_mos2_monolayer",
        "example": "molybdenum_disulfide_2d_mos2_monolayer_spec.json",
        "terms": (
            'molybdenum disulfide',
            'molybdenum disulphide',
            'mos2',
            'mos2 monolayer',
            'monolayer mos2',
            '2d mos2',
            'mos2 2d',
            'mos2 sheet',
            'mos2 semiconductor',
            'mos2 monolayer semiconductor',
            'molybdenum disulfide monolayer',
            '\u4e8c\u786b\u5316\u94bc',
            '\u4e8c\u786b\u5316\u94bc\u5355\u5c42',
            '\u5355\u5c42\u4e8c\u786b\u5316\u94bc',
            '\u4e8c\u7ef4\u4e8c\u786b\u5316\u94bc',
        ),
        "notes": "2D 2H-MoS2 monolayer 2x2 cell with vacuum along c and CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "tungsten_disulfide_2d_ws2_monolayer",
        "example": "tungsten_disulfide_2d_ws2_monolayer_spec.json",
        "terms": (
            'tungsten disulfide',
            'tungsten disulphide',
            'ws2',
            'ws2 monolayer',
            'monolayer ws2',
            '2d ws2',
            'ws2 2d',
            'ws2 sheet',
            'ws2 semiconductor',
            'ws2 monolayer semiconductor',
            'tungsten disulfide monolayer',
            '\u4e8c\u786b\u5316\u94a8',
            '\u4e8c\u786b\u5316\u94a8\u5355\u5c42',
            '\u5355\u5c42\u4e8c\u786b\u5316\u94a8',
            '\u4e8c\u7ef4\u4e8c\u786b\u5316\u94a8',
        ),
        "notes": "2D 2H-WS2 monolayer 2x2 cell with vacuum along c and CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "molybdenum_diselenide_2d_mose2_monolayer",
        "example": "molybdenum_diselenide_2d_mose2_monolayer_spec.json",
        "terms": (
            'molybdenum diselenide',
            'mose2',
            'mose2 monolayer',
            'monolayer mose2',
            '2d mose2',
            'mose2 2d',
            'mose2 sheet',
            'mose2 semiconductor',
            'mose2 monolayer semiconductor',
            'molybdenum diselenide monolayer',
            '\u4e8c\u7852\u5316\u94bc',
            '\u4e8c\u7852\u5316\u94bc\u5355\u5c42',
            '\u5355\u5c42\u4e8c\u7852\u5316\u94bc',
            '\u4e8c\u7ef4\u4e8c\u7852\u5316\u94bc',
        ),
        "notes": "2D 2H-MoSe2 monolayer 2x2 cell with vacuum along c and CASTEP energy settings.",
        "domain": "semiconductor",
    },
    {
        "template_id": "tungsten_diselenide_2d_wse2_monolayer",
        "example": "tungsten_diselenide_2d_wse2_monolayer_spec.json",
        "terms": (
            'tungsten diselenide',
            'wse2',
            'wse2 monolayer',
            'monolayer wse2',
            '2d wse2',
            'wse2 2d',
            'wse2 sheet',
            'wse2 semiconductor',
            'wse2 monolayer semiconductor',
            'tungsten diselenide monolayer',
            '\u4e8c\u7852\u5316\u94a8',
            '\u4e8c\u7852\u5316\u94a8\u5355\u5c42',
            '\u5355\u5c42\u4e8c\u7852\u5316\u94a8',
            '\u4e8c\u7ef4\u4e8c\u7852\u5316\u94a8',
        ),
        "notes": "2D 2H-WSe2 monolayer 2x2 cell with vacuum along c and CASTEP energy settings.",
        "domain": "semiconductor",
    },
)

ELEMENT_ALIASES = {
    "hydrogen": "H",
    "carbon": "C",
    "nitrogen": "N",
    "oxygen": "O",
    "fluorine": "F",
    "fluoride": "F",
    "chlorine": "Cl",
    "chloride": "Cl",
    "bromine": "Br",
    "bromide": "Br",
    "iodine": "I",
    "iodide": "I",
    "sulfur": "S",
    "zinc": "Zn",
    "phosphorus": "P",
    "boron": "B",
    "aluminum": "Al",
    "aluminium": "Al",
    "silicon": "Si",
    "gallium": "Ga",
    "germanium": "Ge",
    "arsenic": "As",
    "indium": "In",
    "selenium": "Se",
    "cadmium": "Cd",
    "antimony": "Sb",
    "tellurium": "Te",
    "tin": "Sn",
    "titanium": "Ti",
    "nickel": "Ni",
    "copper": "Cu",
    "hafnium": "Hf",
    "molybdenum": "Mo",
    "tungsten": "W",
    "palladium": "Pd",
    "silver": "Ag",
    "platinum": "Pt",
    "gold": "Au",
    "niobium": "Nb",
    "tantalum": "Ta",
    "rhenium": "Re",
    "\u6c22": "H",
    "\u78b3": "C",
    "\u6c2e": "N",
    "\u6c27": "O",
    "\u6c1f": "F",
    "\u6c1f\u5316\u7269": "F",
    "\u6c2f": "Cl",
    "\u6c2f\u5316\u7269": "Cl",
    "\u6eb4": "Br",
    "\u6eb4\u5316\u7269": "Br",
    "\u7898": "I",
    "\u7898\u5316\u7269": "I",
    "\u786b": "S",
    "\u78f7": "P",
    "\u787c": "B",
    "\u94dd": "Al",
    "\u7845": "Si",
    "\u9553": "Ga",
    "\u953a": "Ge",
    "\u9517": "Ge",
    "\u7837": "As",
    "\u94df": "In",
    "\u950c": "Zn",
    "\u7852": "Se",
    "\u78b2": "Te",
    "\u9549": "Cd",
    "\u9511": "Sb",
    "\u949b": "Ti",
    "\u954d": "Ni",
    "\u94dc": "Cu",
    "\u94ea": "Hf",
    "\u94af": "Pd",
    "\u94f6": "Ag",
    "\u94c2": "Pt",
    "\u91d1": "Au",
}

CASE_SENSITIVE_MATERIAL_FORMULA_LOWERCASES = {"gap"}
ELEMENT_TERM_PATTERN = (
    r"hydrogen|carbon|nitrogen|oxygen|fluorine|fluoride|chlorine|chloride|sulfur|phosphorus|"
    r"bromine|bromide|iodine|iodide|"
    r"boron|aluminum|aluminium|silicon|gallium|germanium|arsenic|indium|antimony|tin|"
    r"titanium|nickel|copper|hafnium|molybdenum|tungsten|palladium|silver|platinum|gold|niobium|tantalum|rhenium|"
    r"\u6c22|\u78b3|\u6c2e|\u6c27|\u6c1f\u5316\u7269|\u6c1f|\u6c2f\u5316\u7269|\u6c2f|\u6eb4\u5316\u7269|\u6eb4|\u7898\u5316\u7269|\u7898|\u786b|\u78f7|\u787c|\u94dd|\u7845|\u9553|\u953a|\u9517|\u7837|\u94df|\u950c|\u7852|\u78b2|\u9549|\u9511|\u949b|\u954d|\u94dc|\u94ea|\u94af|\u94f6|\u94c2|\u91d1|"
    r"[A-Za-z]{1,2}"
)
CONTACT_METAL_WORK_FUNCTION_EV: dict[str, float] = {
    "Al": 4.28,
    "Ti": 4.33,
    "Ni": 5.15,
    "Cu": 4.65,
    "Mo": 4.6,
    "W": 4.55,
    "Pd": 5.12,
    "Ag": 4.26,
    "Pt": 5.65,
    "Au": 5.1,
}
GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_gallium_arsenide_001_schottky_contact"
GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_gallium_nitride_0001_schottky_contact"
ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_zinc_oxide_0001_schottky_contact"
BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_beta_gallium_oxide_010_schottky_contact"
SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_silicon_carbide_4h_0001_schottky_contact"
SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID = "silicon_carbide_6h_0001_si_face_slab"
SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_silicon_carbide_6h_0001_schottky_contact"
INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_indium_phosphide_001_schottky_contact"
INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_indium_arsenide_001_schottky_contact"
ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_aluminum_arsenide_001_schottky_contact"
CDTE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_cadmium_telluride_001_schottky_contact"
ZNS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_zinc_sulfide_001_schottky_contact"
ZNSE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_zinc_selenide_001_schottky_contact"
ZNTE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_zinc_telluride_001_schottky_contact"
CDS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_cadmium_sulfide_001_schottky_contact"
CDSE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_cadmium_selenide_001_schottky_contact"
GAP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_gallium_phosphide_001_schottky_contact"
GASB_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_gallium_antimonide_001_schottky_contact"
ALP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_aluminum_phosphide_001_schottky_contact"
ALSB_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_aluminum_antimonide_001_schottky_contact"
INSB_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID = "metal_indium_antimonide_001_schottky_contact"
GAAS_ELECTRON_AFFINITY_EV = 4.07
GAAS_BAND_GAP_EV = 1.42
GAN_ELECTRON_AFFINITY_EV = 4.1
GAN_BAND_GAP_EV = 3.4
# Literature screening values used only for Schottky-Mott metadata preflight.
ZNO_ELECTRON_AFFINITY_EV = 4.35
ZNO_BAND_GAP_EV = 3.37
ZNO_REFERENCE_LATTICE_A_ANGSTROM = 3.2495
ZNO_REFERENCE_LATTICE_C_ANGSTROM = 5.2069
ZNO_INTERNAL_PARAMETER_U = 0.3825
# Device-model screening values used only for Schottky-Mott metadata preflight.
BETA_GA2O3_ELECTRON_AFFINITY_EV = 4.0
BETA_GA2O3_BAND_GAP_EV = 4.8
BETA_GA2O3_CONTACT_CELL_B_ANGSTROM = 32.0
# Device-screening values for 4H-SiC; these are metadata, not calculated results.
SIC_4H_ELECTRON_AFFINITY_EV = 3.6
SIC_4H_BAND_GAP_EV = 3.26
SIC_4H_CONTACT_CELL_C_ANGSTROM = 32.0
SIC_4H_SI_FACE_CUT_ORIGIN = 0.9375
# Device-model values used only for 6H-SiC metadata preflight.
SIC_6H_ELECTRON_AFFINITY_EV = 3.85
SIC_6H_BAND_GAP_EV = 3.0
SIC_6H_SURFACE_CELL_C_ANGSTROM = 36.0
SIC_6H_CONTACT_CELL_C_ANGSTROM = 36.0
SIC_6H_SI_FACE_CUT_ORIGIN = 0.8746
SIC_6H_BACK_SURFACE_H_BOND_ANGSTROM = 1.1
SIC_6H_SI_FACE_P_TYPE_SBH_EV: dict[str, float] = {
    "Al": 1.09,
    "Ti": 1.0,
    "Ni": 1.18,
    "Cu": 1.41,
    "Pt": 1.28,
    "Au": 1.05,
}
INP_ELECTRON_AFFINITY_EV = 4.38
INP_BAND_GAP_EV = 1.34
INAS_ELECTRON_AFFINITY_EV = 4.9
INAS_BAND_GAP_EV = 0.36
ALAS_ELECTRON_AFFINITY_EV = 3.5
ALAS_BAND_GAP_EV = 2.16
CDTE_ELECTRON_AFFINITY_EV = 4.3
CDTE_BAND_GAP_EV = 1.5
ZNS_ELECTRON_AFFINITY_EV = 3.9
ZNS_BAND_GAP_EV = 3.7
ZNSE_ELECTRON_AFFINITY_EV = 4.09
ZNSE_BAND_GAP_EV = 2.7
ZNTE_ELECTRON_AFFINITY_EV = 3.5
ZNTE_BAND_GAP_EV = 2.26
CDS_ELECTRON_AFFINITY_EV = 4.5
CDS_BAND_GAP_EV = 2.42
CDSE_ELECTRON_AFFINITY_EV = 4.9
CDSE_BAND_GAP_EV = 1.74
GAP_ELECTRON_AFFINITY_EV = 3.8
GAP_BAND_GAP_EV = 2.26
GASB_ELECTRON_AFFINITY_EV = 4.06
GASB_BAND_GAP_EV = 0.73
ALP_ELECTRON_AFFINITY_EV = 3.5
ALP_BAND_GAP_EV = 2.45
ALSB_ELECTRON_AFFINITY_EV = 3.6
ALSB_BAND_GAP_EV = 1.62
INSB_ELECTRON_AFFINITY_EV = 4.59
INSB_BAND_GAP_EV = 0.17


@dataclass(frozen=True)
class Sic6hSiFaceAssembly:
    source_spec: ModelSpec
    source_model: CrystalSpec
    cell_c: float
    lattice_a: float
    lattice_b: float
    semiconductor_atoms: tuple[BasisAtomSpec, ...]
    atoms: tuple[BasisAtomSpec, ...]
    top_registry: tuple[tuple[float, float], ...]
    semiconductor_bottom_fractional: float
    semiconductor_top_fractional: float
    semiconductor_thickness_angstrom: float


@dataclass(frozen=True)
class ZincblendeSchottkyContactProfile:
    material: str
    template_id: str
    base_template_id: str
    cation: str
    anion: str
    lattice_a: float
    electron_affinity_ev: float
    band_gap_ev: float
    cutoff_energy_ev: int
    material_terms: tuple[str, ...]
    excluded_metals: tuple[str, ...] = ()


GENERIC_ZINCBLENDE_SCHOTTKY_CONTACT_PROFILES: tuple[ZincblendeSchottkyContactProfile, ...] = (
    ZincblendeSchottkyContactProfile(
        material="CdTe",
        template_id=CDTE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="cadmium_telluride_zincblende",
        cation="Cd",
        anion="Te",
        lattice_a=6.482,
        electron_affinity_ev=CDTE_ELECTRON_AFFINITY_EV,
        band_gap_ev=CDTE_BAND_GAP_EV,
        cutoff_energy_ev=600,
        material_terms=("CdTe", "cadmium telluride", "\u78b2\u5316\u9549"),
    ),
    ZincblendeSchottkyContactProfile(
        material="ZnS",
        template_id=ZNS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="zinc_sulfide_zincblende",
        cation="Zn",
        anion="S",
        lattice_a=5.4093,
        electron_affinity_ev=ZNS_ELECTRON_AFFINITY_EV,
        band_gap_ev=ZNS_BAND_GAP_EV,
        cutoff_energy_ev=600,
        material_terms=("ZnS", "zinc sulfide", "\u786b\u5316\u950c"),
    ),
    ZincblendeSchottkyContactProfile(
        material="ZnSe",
        template_id=ZNSE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="zinc_selenide_zincblende",
        cation="Zn",
        anion="Se",
        lattice_a=5.6676,
        electron_affinity_ev=ZNSE_ELECTRON_AFFINITY_EV,
        band_gap_ev=ZNSE_BAND_GAP_EV,
        cutoff_energy_ev=600,
        material_terms=("ZnSe", "zinc selenide", "\u7852\u5316\u950c"),
    ),
    ZincblendeSchottkyContactProfile(
        material="ZnTe",
        template_id=ZNTE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="zinc_telluride_zincblende",
        cation="Zn",
        anion="Te",
        lattice_a=6.101,
        electron_affinity_ev=ZNTE_ELECTRON_AFFINITY_EV,
        band_gap_ev=ZNTE_BAND_GAP_EV,
        cutoff_energy_ev=600,
        material_terms=("ZnTe", "zinc telluride", "\u78b2\u5316\u950c"),
    ),
    ZincblendeSchottkyContactProfile(
        material="CdS",
        template_id=CDS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="cadmium_sulfide_zincblende",
        cation="Cd",
        anion="S",
        lattice_a=5.832,
        electron_affinity_ev=CDS_ELECTRON_AFFINITY_EV,
        band_gap_ev=CDS_BAND_GAP_EV,
        cutoff_energy_ev=600,
        material_terms=("CdS", "cadmium sulfide", "\u786b\u5316\u9549"),
    ),
    ZincblendeSchottkyContactProfile(
        material="CdSe",
        template_id=CDSE_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="cadmium_selenide_zincblende",
        cation="Cd",
        anion="Se",
        lattice_a=6.052,
        electron_affinity_ev=CDSE_ELECTRON_AFFINITY_EV,
        band_gap_ev=CDSE_BAND_GAP_EV,
        cutoff_energy_ev=600,
        material_terms=("CdSe", "cadmium selenide", "\u7852\u5316\u9549"),
    ),
    ZincblendeSchottkyContactProfile(
        material="GaP",
        template_id=GAP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="gallium_phosphide_zincblende",
        cation="Ga",
        anion="P",
        lattice_a=5.4505,
        electron_affinity_ev=GAP_ELECTRON_AFFINITY_EV,
        band_gap_ev=GAP_BAND_GAP_EV,
        cutoff_energy_ev=560,
        material_terms=("GaP", "gallium phosphide", "\u78f7\u5316\u9553"),
    ),
    ZincblendeSchottkyContactProfile(
        material="GaSb",
        template_id=GASB_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="gallium_antimonide_zincblende",
        cation="Ga",
        anion="Sb",
        lattice_a=6.0959,
        electron_affinity_ev=GASB_ELECTRON_AFFINITY_EV,
        band_gap_ev=GASB_BAND_GAP_EV,
        cutoff_energy_ev=560,
        material_terms=("GaSb", "gallium antimonide", "\u9511\u5316\u9553"),
    ),
    ZincblendeSchottkyContactProfile(
        material="AlP",
        template_id=ALP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="aluminum_phosphide_zincblende",
        cation="Al",
        anion="P",
        lattice_a=5.451,
        electron_affinity_ev=ALP_ELECTRON_AFFINITY_EV,
        band_gap_ev=ALP_BAND_GAP_EV,
        cutoff_energy_ev=560,
        material_terms=("AlP", "aluminum phosphide", "aluminium phosphide", "\u78f7\u5316\u94dd"),
        excluded_metals=("Al",),
    ),
    ZincblendeSchottkyContactProfile(
        material="AlSb",
        template_id=ALSB_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="aluminum_antimonide_zincblende",
        cation="Al",
        anion="Sb",
        lattice_a=6.1355,
        electron_affinity_ev=ALSB_ELECTRON_AFFINITY_EV,
        band_gap_ev=ALSB_BAND_GAP_EV,
        cutoff_energy_ev=560,
        material_terms=("AlSb", "aluminum antimonide", "aluminium antimonide", "\u9511\u5316\u94dd"),
        excluded_metals=("Al",),
    ),
    ZincblendeSchottkyContactProfile(
        material="InSb",
        template_id=INSB_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        base_template_id="indium_antimonide_zincblende",
        cation="In",
        anion="Sb",
        lattice_a=6.4794,
        electron_affinity_ev=INSB_ELECTRON_AFFINITY_EV,
        band_gap_ev=INSB_BAND_GAP_EV,
        cutoff_energy_ev=560,
        material_terms=("InSb", "indium antimonide", "\u9511\u5316\u94df"),
    ),
)
NON_SILICON_SEMICONDUCTOR_ALIASES: tuple[str, ...] = (
    "GaAs",
    "GaN",
    "AlN",
    "InN",
    "InP",
    "InAs",
    "InSb",
    "GaP",
    "GaSb",
    "AlAs",
    "AlP",
    "AlSb",
    "ZnO",
    "ZnS",
    "ZnSe",
    "ZnTe",
    "Ga2O3",
    "Al2O3",
    "CdTe",
    "CdS",
    "CdSe",
    "SiC",
    "Ge",
    "MoS2",
    "WS2",
    "MoSe2",
    "WSe2",
    "BN",
)
CJK_NON_SILICON_SEMICONDUCTOR_TERMS: tuple[str, ...] = (
    "\u7837\u5316\u9553",
    "\u78f7\u5316\u9553",
    "\u9511\u5316\u9553",
    "\u7837\u5316\u94dd",
    "\u78f7\u5316\u94dd",
    "\u9511\u5316\u94dd",
    "\u78f7\u5316\u94df",
    "\u7837\u5316\u94df",
    "\u9511\u5316\u94df",
    "\u6c2e\u5316\u9553",
    "\u6c2e\u5316\u94dd",
    "\u6c2e\u5316\u94df",
    "\u78b3\u5316\u7845",
    "\u9517",
    "\u6c27\u5316\u950c",
    "\u6c27\u5316\u9553",
    "\u6c27\u5316\u94dd",
    "\u84dd\u5b9d\u77f3",
    "\u786b\u5316\u950c",
    "\u7852\u5316\u950c",
    "\u78b2\u5316\u950c",
    "\u6c2e\u5316\u787c",
    "\u786b\u5316\u9549",
    "\u78b2\u5316\u9549",
    "\u7852\u5316\u9549",
    "\u78b2\u5316\u954d",
    "\u4e8c\u786b\u5316\u94bc",
    "\u4e8c\u786b\u5316\u94a8",
    "\u4e8c\u7852\u5316\u94bc",
    "\u4e8c\u7852\u5316\u94a8",
)
TMD_METALS = {"Mo", "W"}
TMD_CHALCOGENS = {"S", "Se", "Te"}
TMD_COMMENSURATE_TWIST_DEFAULT_INTERLAYER_ANGSTROM = {
    "mos2": 6.15,
    "ws2": 6.18,
    "mose2": 6.47,
    "wse2": 6.49,
}
TMD_TEMPLATE_BY_MATERIAL = {
    "MoS2": "molybdenum_disulfide_2d_mos2_monolayer",
    "WS2": "tungsten_disulfide_2d_ws2_monolayer",
    "MoSe2": "molybdenum_diselenide_2d_mose2_monolayer",
    "WSe2": "tungsten_diselenide_2d_wse2_monolayer",
}
TMD_EXAMPLE_BY_MATERIAL = {
    "MoS2": "molybdenum_disulfide_2d_mos2_monolayer_spec.json",
    "WS2": "tungsten_disulfide_2d_ws2_monolayer_spec.json",
    "MoSe2": "molybdenum_diselenide_2d_mose2_monolayer_spec.json",
    "WSe2": "tungsten_diselenide_2d_wse2_monolayer_spec.json",
}
COMMENSURATE_HETEROBILAYER_DEFAULT_MAX_STRAIN_PERCENT = 3.0
COMMENSURATE_TWIST_DEFAULT_MAX_ATOMS = 2_000
COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES = 0.1
TMD_METAL_SITE_DOPANTS = {"Mo", "W", "Nb", "Ta", "Re"}
TMD_CHALCOGEN_SITE_DOPANTS = {"O", "S", "Se", "Te", "F", "Cl", "Br", "I", "N", "P", "As", "Sb"}
BOND_TYPE_ALIASES = {
    "single": "Single",
    "single bond": "Single",
    "double": "Double",
    "double bond": "Double",
    "triple": "Triple",
    "triple bond": "Triple",
    "aromatic": "Aromatic",
    "aromatic bond": "Aromatic",
    "partial double": "Partial double",
    "partial double bond": "Partial double",
    "\u5355\u952e": "Single",
    "\u53cc\u952e": "Double",
    "\u4e09\u952e": "Triple",
    "\u82b3\u9999\u952e": "Aromatic",
}
FUNCTIONAL_GROUP_ALIASES = {
    "nitro": "nitro",
    "nitro group": "nitro",
    "no2": "nitro",
    "nitrobenzene": "nitro",
    "\u785d\u57fa": "nitro",
    "\u785d\u57fa\u82ef": "nitro",
    "hydroxyl": "hydroxyl",
    "hydroxyl group": "hydroxyl",
    "oh": "hydroxyl",
    "phenol": "hydroxyl",
    "\u7f9f\u57fa": "hydroxyl",
    "\u82ef\u915a": "hydroxyl",
    "amino": "amino",
    "amino group": "amino",
    "nh2": "amino",
    "aniline": "amino",
    "\u6c28\u57fa": "amino",
    "\u82ef\u80fa": "amino",
    "methyl": "methyl",
    "methyl group": "methyl",
    "ch3": "methyl",
    "toluene": "methyl",
    "methylbenzene": "methyl",
    "\u7532\u57fa": "methyl",
    "\u7532\u82ef": "methyl",
}
SUBSTITUTED_BENZENE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "nitrobenzene",
        "group": "nitro",
        "terms": ('nitrobenzene', 'nitro benzene', 'benzene with nitro', 'benzene with no2'),
        "notes": "Nitrobenzene generated by replacing one benzene hydrogen with an NO2 group.",
    },
    {
        "template_id": "phenol",
        "group": "hydroxyl",
        "terms": ('phenol', 'hydroxybenzene', 'hydroxy benzene', 'benzene with hydroxyl', 'benzene with oh'),
        "notes": "Phenol generated by replacing one benzene hydrogen with an OH group.",
    },
    {
        "template_id": "aniline",
        "group": "amino",
        "terms": ('aniline', 'aminobenzene', 'amino benzene', 'benzene with amino', 'benzene with nh2'),
        "notes": "Aniline generated by replacing one benzene hydrogen with an NH2 group.",
    },
    {
        "template_id": "toluene",
        "group": "methyl",
        "terms": ('toluene', 'methylbenzene', 'methyl benzene', 'benzene with methyl', 'benzene with ch3'),
        "notes": "Toluene generated by replacing one benzene hydrogen with a CH3 group.",
    },
)


@dataclass(frozen=True)
class NaturalLanguagePlan:
    """A deterministic local plan inferred from a modeling request."""

    kind: str
    payload: dict[str, Any] | None
    confidence: float
    template_id: str | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "confidence": self.confidence,
            "template_id": self.template_id,
            "notes": self.notes,
            "supported_templates": supported_template_ids(),
        }


def supported_template_ids() -> list[str]:
    """Return supported local natural-language templates."""

    return [str(item["template_id"]) for item in TEMPLATE_SPECS] + [
        str(item["template_id"]) for item in SUBSTITUTED_BENZENE_TEMPLATES
    ]


def supported_semiconductor_template_ids() -> list[str]:
    """Return local templates intended for semiconductor material workflows."""

    return [str(item["template_id"]) for item in TEMPLATE_SPECS if item.get("domain") == "semiconductor"]


def supported_semiconductor_template_profiles() -> list[dict[str, Any]]:
    """Return machine-readable profiles for semiconductor template selection."""

    profiles: list[dict[str, Any]] = []
    for item in TEMPLATE_SPECS:
        if item.get("domain") != "semiconductor":
            continue
        template_id = str(item["template_id"])
        example = str(item["example"])
        payload = _load_example(example)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
        simulation = payload.get("simulation") if isinstance(payload.get("simulation"), dict) else {}
        profiles.append(
            {
                "template_id": template_id,
                "example": example,
                "terms": list(item["terms"]),
                "notes": str(item["notes"]),
                "model_type": str(payload.get("model_type") or ""),
                "model_name": str(model.get("name") or ""),
                "structure_family": metadata.get("structure_family"),
                "material": metadata.get("material"),
                "materials": metadata.get("materials"),
                "polytype": metadata.get("polytype"),
                "interface": metadata.get("interface"),
                "interface_orientation": metadata.get("interface_orientation"),
                "surface_orientation": metadata.get("surface_orientation"),
                "surface_axis": metadata.get("surface_axis"),
                "simulation_module": simulation.get("module"),
                "simulation_task": simulation.get("task"),
                "execute_backend": "crystal_cif_materialize_for_gui_hotload",
                "default_diagnostic_focuses": _semiconductor_template_default_focuses(template_id, metadata),
            }
        )
    return profiles


def supported_semiconductor_virtual_template_profiles() -> list[dict[str, Any]]:
    """Return discoverable semiconductor variants generated by deterministic modifiers."""

    base_template_id = "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"
    base_profiles = {str(item.get("template_id")): item for item in supported_semiconductor_template_profiles()}
    base = base_profiles.get(base_template_id, {})
    sapphire_base_template_id = "alpha_alumina_sapphire_substrate"
    sapphire_base = base_profiles.get(sapphire_base_template_id, {})
    return [
        {
            "template_id": f"{base_template_id}_p_gan_gate",
            "base_template_id": base_template_id,
            "variant_kind": "inline_modifier",
            "inline_modifiers": ["p_gan_gate_cap"],
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": base_template_id,
            "example_request": "Build a p-GaN gate AlGaN/GaN HEMT and export 2DEG diagnostics.",
            "terms": [
                "p-GaN gate AlGaN/GaN HEMT",
                "p-GaN gate Al0.25Ga0.75N/GaN HEMT",
                "AlGaN/GaN HEMT with p-type GaN cap layer",
                "AlGaN/GaN HEMT with p-GaN gate cap",
                "p-GaN gate high electron mobility transistor",
                "\u6784\u5efa p-GaN \u6805 AlGaN/GaN HEMT",
                "\u6784\u5efa p-GaN \u6805\u5e3d AlGaN/GaN HEMT",
            ],
            "notes": (
                "Deterministic p-GaN/Mg gate-cap variant of the Al0.25Ga0.75N/GaN(0001) "
                "HEMT preflight template. The live tool returns the base template_id plus "
                "nl_composite_operations containing p_gan_gate_cap."
            ),
            "model_type": base.get("model_type", "crystal"),
            "model_name": f"{base_template_id}_p_gan_gate",
            "structure_family": "wurtzite p-GaN gate HEMT heterostructure",
            "materials": ["GaN", "Al0.25Ga0.75N", "p-GaN"],
            "interface": "GaN/Al0.25Ga0.75N/p-GaN",
            "interface_orientation": base.get("interface_orientation", "(0001)"),
            "surface_orientation": base.get("surface_orientation"),
            "surface_axis": base.get("surface_axis"),
            "simulation_module": base.get("simulation_module"),
            "simulation_task": base.get("simulation_task"),
            "execute_backend": base.get("execute_backend", "crystal_cif_materialize_for_gui_hotload"),
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "iii_nitride_hemt_2deg",
                    "quantum_well_heterostructure",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "dopant_site_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "p_gan_gate_cap_summary",
                "polarization_2deg_summary",
                "quantum_well_summary",
                "band_alignment_summary",
                "dopant_site_summary",
            ],
            "required_csv_keys": [
                "semiconductor_p_gan_gate_cap_csv",
                "semiconductor_polarization_2deg_csv",
                "semiconductor_quantum_well_csv",
                "semiconductor_band_alignment_csv",
                "semiconductor_dopant_sites_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "gallium_arsenide_zincblende",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/GaAs Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/GaAs Schottky contact",
                "Pt/GaAs Schottky contact",
                "metal/GaAs Schottky contact",
                "gold on GaAs contact",
                "platinum on gallium arsenide contact",
                "GaAs metal-semiconductor contact",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold for a metal/GaAs(001) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "and Schottky-Mott metadata preflight before reviewed interface relaxation."
            ),
            "model_type": "crystal",
            "model_name": GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "zinc blende GaAs metal semiconductor schottky contact scaffold",
            "materials": ["GaAs", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/GaAs",
            "interface_orientation": "metal contact / GaAs(001)",
            "surface_orientation": "GaAs(001)",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "gallium_nitride_wurtzite",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/GaN Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/GaN Schottky contact",
                "Pt/GaN Schottky contact",
                "metal/GaN Schottky contact",
                "gold on GaN contact",
                "platinum on gallium nitride contact",
                "GaN metal-semiconductor contact",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold for a metal/GaN(0001) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "and Schottky-Mott metadata preflight before reviewed interface relaxation."
            ),
            "model_type": "crystal",
            "model_name": GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "wurtzite GaN metal semiconductor schottky contact scaffold",
            "materials": ["GaN", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/GaN",
            "interface_orientation": "metal contact / GaN(0001)",
            "surface_orientation": "GaN(0001)",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "zinc_oxide_wurtzite",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/ZnO Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/ZnO Schottky contact",
                "Pt/ZnO Schottky contact",
                "metal/ZnO Schottky contact",
                "gold on ZnO contact",
                "platinum on zinc oxide contact",
                "ZnO metal-semiconductor contact",
                "\u91d1/\u6c27\u5316\u950c\u8096\u7279\u57fa\u63a5\u89e6",
            ],
            "notes": (
                "Programmatic oxygen-terminated pre-relaxation scaffold for a metal/ZnO(0001) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "surface-polarity review, and Schottky-Mott metadata preflight before reviewed interface relaxation."
            ),
            "model_type": "crystal",
            "model_name": ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "wurtzite ZnO metal semiconductor schottky contact scaffold",
            "materials": ["ZnO", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/ZnO",
            "interface_orientation": "metal contact / ZnO(0001)",
            "surface_orientation": "ZnO(0001)",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "surface_polarity_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_surface_polarity_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "beta_gallium_oxide_010_slab",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/beta-Ga2O3(010) Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/beta-Ga2O3 Schottky contact",
                "Pt/beta-Ga2O3 Schottky contact",
                "metal/beta-Ga2O3(010) Schottky contact",
                "gold on beta gallium oxide contact",
                "platinum on beta gallium oxide contact",
                "beta-Ga2O3 metal-semiconductor contact",
                "\u91d1/\u03b2-\u6c27\u5316\u9553(010)\u8096\u7279\u57fa\u63a5\u89e6",
            ],
            "notes": (
                "Programmatic centered pre-relaxation scaffold for a metal/beta-Ga2O3(010) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "surface-asymmetry review, and Schottky-Mott metadata preflight before reviewed interface relaxation."
            ),
            "model_type": "crystal",
            "model_name": BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "monoclinic beta-Ga2O3 metal semiconductor schottky contact scaffold",
            "materials": ["beta-Ga2O3", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/beta-Ga2O3",
            "interface_orientation": "metal contact / beta-Ga2O3(010)",
            "surface_orientation": "beta-Ga2O3(010)",
            "surface_axis": "b",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "surface_polarity_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_surface_polarity_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "silicon_carbide_6h_hexagonal",
            "variant_kind": "surface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build a 6H-SiC(0001) Si-face slab and export surface diagnostics.",
            "terms": [
                "6H-SiC(0001) Si-face slab",
                "6H-SiC(0001) surface",
                "silicon-terminated 6H-SiC surface",
                "6H-SiC Si-face surface model",
                "6H-SiC six-bilayer slab",
                "6H-\u78b3\u5316\u7845(0001)\u7845\u9762\u8868\u9762",
            ],
            "notes": (
                "Programmatic centered 2x2 six-bilayer 6H-SiC(0001) Si-face slab with a hydrogen-saturated "
                "C-terminated back surface. It is an unreconstructed pre-relaxation scaffold for same-window "
                "visualization, surface diagnostics, and reviewed CASTEP setup."
            ),
            "model_type": "crystal",
            "model_name": SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
            "structure_family": "hexagonal 6H-SiC(0001) Si-face surface slab scaffold",
            "materials": ["6H-SiC", "H"],
            "polytype": "6H",
            "surface_orientation": "6H-SiC(0001) Si-face",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "surface_termination_summary",
                "surface_polarity_summary",
                "surface_orientation_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_surface_termination_csv",
                "semiconductor_surface_polarity_csv",
                "semiconductor_surface_model_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
            "source_references": [
                "10.2138/am.2007.2346",
                "10.2320/matertrans.47.2690",
                "10.3390/ma10060583",
            ],
        },
        {
            "template_id": SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "silicon_carbide_6h_hexagonal",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/6H-SiC(0001) Si-face Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/6H-SiC Schottky contact",
                "Pt/6H-SiC Schottky contact",
                "metal/6H-SiC(0001) Schottky contact",
                "gold on 6H-SiC Si-face contact",
                "platinum on 6H silicon carbide contact",
                "6H-SiC metal-semiconductor contact",
                "\u91d1/6H-\u78b3\u5316\u7845(0001)\u7845\u9762\u8096\u7279\u57fa\u63a5\u89e6",
            ],
            "notes": (
                "Programmatic centered 2x2 six-bilayer metal/6H-SiC(0001) Si-face pre-relaxation scaffold "
                "with a hydrogen-saturated C-terminated back surface. The generated revision supports same-window "
                "visualization, contact geometry diagnostics, polar-surface review, and metadata preflight."
            ),
            "model_type": "crystal",
            "model_name": SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "hexagonal 6H-SiC metal semiconductor schottky contact scaffold",
            "materials": ["6H-SiC", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "polytype": "6H",
            "interface": "metal/6H-SiC",
            "interface_orientation": "metal contact / 6H-SiC(0001) Si-face",
            "surface_orientation": "6H-SiC(0001) Si-face",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "surface_polarity_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_surface_polarity_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
            "source_references": [
                "10.2138/am.2007.2346",
                "10.2320/matertrans.47.2690",
                "10.3390/ma10060583",
            ],
        },
        {
            "template_id": SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "silicon_carbide_4h_hexagonal",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/4H-SiC(0001) Si-face Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/4H-SiC Schottky contact",
                "Pt/4H-SiC Schottky contact",
                "metal/4H-SiC(0001) Schottky contact",
                "gold on 4H-SiC contact",
                "platinum on 4H silicon carbide contact",
                "4H-SiC metal-semiconductor contact",
                "\u91d1/4H-\u78b3\u5316\u7845(0001)\u8096\u7279\u57fa\u63a5\u89e6",
            ],
            "notes": (
                "Programmatic centered Si-terminated pre-relaxation scaffold for a metal/4H-SiC(0001) "
                "Schottky contact. The generated revision is suitable for same-window visualization, contact geometry "
                "diagnostics, polar-surface review, and Schottky-Mott metadata preflight before reviewed relaxation."
            ),
            "model_type": "crystal",
            "model_name": SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "hexagonal 4H-SiC metal semiconductor schottky contact scaffold",
            "materials": ["4H-SiC", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/4H-SiC",
            "interface_orientation": "metal contact / 4H-SiC(0001) Si-face",
            "surface_orientation": "4H-SiC(0001) Si-face",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "surface_polarity_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_surface_polarity_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "indium_phosphide_zincblende",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/InP Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/InP Schottky contact",
                "Pt/InP Schottky contact",
                "metal/InP Schottky contact",
                "gold on InP contact",
                "platinum on indium phosphide contact",
                "InP metal-semiconductor contact",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold for a metal/InP(001) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "and Schottky-Mott metadata preflight before reviewed interface relaxation."
            ),
            "model_type": "crystal",
            "model_name": INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "zinc blende InP metal semiconductor schottky contact scaffold",
            "materials": ["InP", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/InP",
            "interface_orientation": "metal contact / InP(001)",
            "surface_orientation": "InP(001)",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "indium_arsenide_zincblende",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/InAs Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/InAs Schottky contact",
                "Pt/InAs Schottky contact",
                "metal/InAs Schottky contact",
                "gold on InAs contact",
                "platinum on indium arsenide contact",
                "InAs metal-semiconductor contact",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold for a metal/InAs(001) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "and Schottky-Mott metadata preflight before reviewed interface relaxation."
            ),
            "model_type": "crystal",
            "model_name": INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "zinc blende InAs metal semiconductor schottky contact scaffold",
            "materials": ["InAs", "Au/Pt/Al/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/InAs",
            "interface_orientation": "metal contact / InAs(001)",
            "surface_orientation": "InAs(001)",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "base_template_id": "aluminum_arsenide_zincblende",
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "example_request": "Build an Au/AlAs Schottky contact and export contact diagnostics.",
            "terms": [
                "Au/AlAs Schottky contact",
                "Pt/AlAs Schottky contact",
                "metal/AlAs Schottky contact",
                "gold on AlAs contact",
                "platinum on aluminum arsenide contact",
                "AlAs metal-semiconductor contact",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold for a metal/AlAs(001) Schottky contact. "
                "The generated revision is suitable for same-window visualization, contact geometry diagnostics, "
                "and Schottky-Mott metadata preflight before reviewed interface relaxation. "
                "Al/AlAs is intentionally rejected until same-element metal/semiconductor region tagging is reviewed."
            ),
            "model_type": "crystal",
            "model_name": ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
            "structure_family": "zinc blende AlAs metal semiconductor schottky contact scaffold",
            "materials": ["AlAs", "Au/Pt/Ti/Ni/Cu/Mo/W/Pd/Ag"],
            "interface": "metal/AlAs",
            "interface_orientation": "metal contact / AlAs(001)",
            "surface_orientation": "AlAs(001)",
            "surface_axis": "c",
            "simulation_module": "CASTEP",
            "simulation_task": "Energy",
            "execute_backend": "crystal_cif_materialize_for_gui_hotload",
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "metal_semiconductor_contact",
                    "band_alignment",
                    "epitaxial_strain_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "metal_semiconductor_contact_summary",
                "interface_profile_summary",
                "interface_quality_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_contact_csv",
                "semiconductor_interface_profile_csv",
                "semiconductor_interface_quality_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        *_zincblende_schottky_virtual_template_profiles(),
        {
            "template_id": "gallium_nitride_on_sapphire_epitaxy_preflight",
            "base_template_id": sapphire_base_template_id,
            "variant_kind": "epitaxy_preflight",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": sapphire_base_template_id,
            "example_request": "Build GaN on sapphire substrate and export epitaxy preflight diagnostics.",
            "terms": [
                "GaN on sapphire",
                "GaN/sapphire epitaxy",
                "gallium nitride on sapphire substrate",
                "GaN on Al2O3 substrate",
                "\u6c2e\u5316\u9553\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a\u5916\u5ef6",
                "\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a\u7684\u6c2e\u5316\u9553\u5916\u5ef6",
            ],
            "notes": (
                "Routes GaN-on-sapphire requests to the alpha-Al2O3 substrate precursor and "
                "substrate_epitaxy_preflight diagnostics. It does not fabricate an atomistic interface."
            ),
            "model_type": sapphire_base.get("model_type", "crystal"),
            "model_name": "gallium_nitride_on_sapphire_epitaxy_preflight",
            "structure_family": "corundum sapphire substrate epitaxy preflight",
            "materials": ["Al2O3", "GaN"],
            "substrate": "Al2O3",
            "epitaxy_target": "GaN",
            "interface_orientation": "Al2O3(0001)//GaN(0001)",
            "simulation_module": sapphire_base.get("simulation_module"),
            "simulation_task": sapphire_base.get("simulation_task"),
            "execute_backend": sapphire_base.get("execute_backend", "crystal_cif_materialize_for_gui_hotload"),
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "substrate_epitaxy_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "substrate_epitaxy_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_substrate_epitaxy_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": "gallium_nitride_on_sapphire_interface_scaffold",
            "base_template_id": sapphire_base_template_id,
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": sapphire_base_template_id,
            "example_request": "Build a GaN on sapphire interface scaffold and prepare preview.",
            "terms": [
                "GaN on sapphire interface model",
                "GaN/sapphire atomic interface",
                "GaN on sapphire interface scaffold",
                "hot-load GaN on sapphire in Materials Studio",
                "\u6c2e\u5316\u9553\u84dd\u5b9d\u77f3\u754c\u9762\u6a21\u578b",
                "\u6784\u5efa\u6c2e\u5316\u9553\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a\u7684\u754c\u9762\u6a21\u578b",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold using a 2x2 sapphire domain and a 3x3 GaN domain. "
                "Useful for same-window visualization and diagnostics before reviewed relaxation."
            ),
            "model_type": "crystal",
            "model_name": "gallium_nitride_on_sapphire_interface_scaffold",
            "structure_family": "wurtzite GaN on c-plane sapphire interface scaffold",
            "materials": ["Al2O3", "GaN"],
            "substrate": "Al2O3",
            "epitaxy_target": "GaN",
            "interface_orientation": "Al2O3(0001)//GaN(0001)",
            "simulation_module": "CASTEP",
            "simulation_task": "GeometryOptimization",
            "execute_backend": sapphire_base.get("execute_backend", "crystal_cif_materialize_for_gui_hotload"),
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "interface_scaffold_preflight",
                    "substrate_epitaxy_preflight",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "interface_scaffold_summary",
                "substrate_epitaxy_preflight_summary",
                "surface_model_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_interface_scaffold_csv",
                "semiconductor_substrate_epitaxy_preflight_csv",
                "semiconductor_surface_model_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": "aluminum_nitride_on_sapphire_epitaxy_preflight",
            "base_template_id": sapphire_base_template_id,
            "variant_kind": "epitaxy_preflight",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": sapphire_base_template_id,
            "example_request": "Build AlN on sapphire substrate and export epitaxy preflight diagnostics.",
            "terms": [
                "AlN on sapphire",
                "AlN/sapphire epitaxy",
                "aluminum nitride on sapphire substrate",
                "AlN on Al2O3 substrate",
                "\u6c2e\u5316\u94dd\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a\u5916\u5ef6",
                "\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a\u7684\u6c2e\u5316\u94dd\u5916\u5ef6",
            ],
            "notes": (
                "Routes AlN-on-sapphire requests to the alpha-Al2O3 substrate precursor and "
                "substrate_epitaxy_preflight diagnostics. It does not fabricate an atomistic interface."
            ),
            "model_type": sapphire_base.get("model_type", "crystal"),
            "model_name": "aluminum_nitride_on_sapphire_epitaxy_preflight",
            "structure_family": "corundum sapphire substrate epitaxy preflight",
            "materials": ["Al2O3", "AlN"],
            "substrate": "Al2O3",
            "epitaxy_target": "AlN",
            "interface_orientation": "Al2O3(0001)//AlN(0001)",
            "simulation_module": sapphire_base.get("simulation_module"),
            "simulation_task": sapphire_base.get("simulation_task"),
            "execute_backend": sapphire_base.get("execute_backend", "crystal_cif_materialize_for_gui_hotload"),
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "substrate_epitaxy_preflight",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "substrate_epitaxy_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_substrate_epitaxy_preflight_csv",
                "view_quality_csv",
            ],
        },
        {
            "template_id": "aluminum_nitride_on_sapphire_interface_scaffold",
            "base_template_id": sapphire_base_template_id,
            "variant_kind": "interface_scaffold",
            "generated_by_tool": "material_studio_live_modeling_request",
            "response_template_id": sapphire_base_template_id,
            "example_request": "Build an AlN on sapphire interface scaffold and prepare preview.",
            "terms": [
                "AlN on sapphire interface model",
                "AlN/sapphire atomic interface",
                "AlN on sapphire interface scaffold",
                "hot-load AlN on sapphire in Materials Studio",
                "\u6c2e\u5316\u94dd\u84dd\u5b9d\u77f3\u754c\u9762\u6a21\u578b",
                "\u6784\u5efa\u6c2e\u5316\u94dd\u5728\u84dd\u5b9d\u77f3\u886c\u5e95\u4e0a\u7684\u754c\u9762\u6a21\u578b",
            ],
            "notes": (
                "Programmatic pre-relaxation scaffold using a 2x2 sapphire domain and a 3x3 AlN domain. "
                "Useful for same-window visualization and diagnostics before reviewed relaxation."
            ),
            "model_type": "crystal",
            "model_name": "aluminum_nitride_on_sapphire_interface_scaffold",
            "structure_family": "wurtzite AlN on c-plane sapphire interface scaffold",
            "materials": ["Al2O3", "AlN"],
            "substrate": "Al2O3",
            "epitaxy_target": "AlN",
            "interface_orientation": "Al2O3(0001)//AlN(0001)",
            "simulation_module": "CASTEP",
            "simulation_task": "GeometryOptimization",
            "execute_backend": sapphire_base.get("execute_backend", "crystal_cif_materialize_for_gui_hotload"),
            "default_diagnostic_focuses": _unique_preserving_order(
                [
                    "semiconductor_structure_health",
                    "interface_scaffold_preflight",
                    "substrate_epitaxy_preflight",
                    "surface_slab_polarity",
                    "electronic_structure_preflight",
                    "view_quality",
                ]
            ),
            "required_summary_keys": [
                "interface_scaffold_summary",
                "substrate_epitaxy_preflight_summary",
                "surface_model_summary",
                "calculation_preflight_summary",
            ],
            "required_csv_keys": [
                "semiconductor_interface_scaffold_csv",
                "semiconductor_substrate_epitaxy_preflight_csv",
                "semiconductor_surface_model_csv",
                "semiconductor_calculation_preflight_csv",
                "view_quality_csv",
            ],
        },
    ]


def _zincblende_schottky_virtual_template_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for profile in GENERIC_ZINCBLENDE_SCHOTTKY_CONTACT_PROFILES:
        metals = [metal for metal in CONTACT_METAL_WORK_FUNCTION_EV if metal not in set(profile.excluded_metals)]
        profiles.append(
            {
                "template_id": profile.template_id,
                "base_template_id": profile.base_template_id,
                "variant_kind": "interface_scaffold",
                "generated_by_tool": "material_studio_live_modeling_request",
                "response_template_id": profile.template_id,
                "example_request": f"Build an Au/{profile.material} Schottky contact and export contact diagnostics.",
                "terms": [
                    f"Au/{profile.material} Schottky contact",
                    f"Pt/{profile.material} Schottky contact",
                    f"metal/{profile.material} Schottky contact",
                    f"gold on {profile.material} contact",
                    f"platinum on {profile.material} contact",
                    f"{profile.material} metal-semiconductor contact",
                ],
                "notes": (
                    f"Profile-driven pre-relaxation scaffold for a metal/{profile.material}(001) "
                    "Schottky contact. The generated revision is suitable for same-window visualization, "
                    "contact geometry diagnostics, and Schottky-Mott metadata preflight before reviewed interface relaxation."
                ),
                "model_type": "crystal",
                "model_name": profile.template_id,
                "structure_family": f"zinc blende {profile.material} metal semiconductor schottky contact scaffold",
                "materials": [profile.material, "/".join(metals)],
                "interface": f"metal/{profile.material}",
                "interface_orientation": f"metal contact / {profile.material}(001)",
                "surface_orientation": f"{profile.material}(001)",
                "surface_axis": "c",
                "simulation_module": "CASTEP",
                "simulation_task": "Energy",
                "execute_backend": "crystal_cif_materialize_for_gui_hotload",
                "default_diagnostic_focuses": _unique_preserving_order(
                    [
                        "semiconductor_structure_health",
                        "metal_semiconductor_contact",
                        "band_alignment",
                        "epitaxial_strain_preflight",
                        "electronic_structure_preflight",
                        "view_quality",
                    ]
                ),
                "required_summary_keys": [
                    "metal_semiconductor_contact_summary",
                    "interface_profile_summary",
                    "interface_quality_summary",
                    "calculation_preflight_summary",
                ],
                "required_csv_keys": [
                    "semiconductor_contact_csv",
                    "semiconductor_interface_profile_csv",
                    "semiconductor_interface_quality_csv",
                    "semiconductor_calculation_preflight_csv",
                    "view_quality_csv",
                ],
            }
        )
    return profiles


def _semiconductor_template_default_focuses(template_id: str, metadata: dict[str, Any]) -> list[str]:
    """Return diagnostic focuses that are normally relevant for a template."""

    family = str(metadata.get("structure_family") or "").lower()
    material = str(metadata.get("material") or "").lower()
    materials = [str(item).lower() for item in (metadata.get("materials") or []) if item is not None]
    focuses: list[str] = ["semiconductor_structure_health", "electronic_structure_preflight", "view_quality"]
    gate_stack_like = bool(
        metadata.get("high_k_gate_stack")
        or metadata.get("metal_gate_stack")
        or metadata.get("semiconductor_oxide_interface")
        or metadata.get("oxide_interface")
        or "mos" in family
        or "gate stack" in family
    )
    surface_like = bool(
        "slab" in family
        or "monolayer" in family
        or metadata.get("slab_thickness_angstrom") is not None
        or metadata.get("surface_model") is not None
    )

    if "tmd" in family or metadata.get("tmd_phase") or any(item in {"mos2", "ws2", "mose2", "wse2"} for item in materials + [material]):
        focuses.append("tmd_2d_monolayer")
    if (
        metadata.get("halide_perovskite")
        or metadata.get("perovskite_abx3")
        or "perovskite" in family
        or any("perovskite" in item or item in {"mapbi3", "ch3nh3pbi3"} for item in materials + [material])
    ):
        focuses.append("halide_perovskite_absorber")
    if (
        "monolayer" in family
        or "2d" in family
        or str(metadata.get("layered_insulator") or "").lower() == "true"
        or metadata.get("puckered_layered_semiconductor")
        or any(
            item in {"mos2", "ws2", "mose2", "wse2", "h-bn", "hbn", "phosphorene", "black phosphorus"}
            for item in materials + [material]
        )
    ):
        focuses.append("2d_layered_material")
    if surface_like:
        focuses.append("surface_slab_polarity")
    if gate_stack_like:
        focuses.append("mos_gate_stack")
    if metadata.get("metal_semiconductor_interface") or metadata.get("schottky_contact") or "schottky" in family:
        focuses.append("metal_semiconductor_contact")
    if metadata.get("substrate") is True or metadata.get("insulating_substrate") or "substrate" in family:
        if metadata.get("epitaxy_targets") or metadata.get("substrate_epitaxy_targets"):
            _append_unique(focuses, "substrate_epitaxy_preflight")
        _append_unique(focuses, "epitaxial_strain_preflight")
    if metadata.get("interface") and "mos_gate_stack" not in focuses and "metal_semiconductor_contact" not in focuses:
        focuses.extend(["quantum_well_heterostructure", "band_alignment", "epitaxial_strain_preflight"])
    if metadata.get("band_alignment_model") or metadata.get("band_alignment_reference"):
        _append_unique(focuses, "band_alignment")
    if metadata.get("coherent_strain_model") or metadata.get("applied_strain"):
        _append_unique(focuses, "epitaxial_strain_preflight")
    if metadata.get("applied_alloy") or metadata.get("last_applied_alloy") or metadata.get("formula_alloy_request"):
        focuses.append("alloy_composition_preflight")
    if metadata.get("pn_junction") or "pn_junction" in template_id:
        focuses.append("pn_junction_and_doping")
    if metadata.get("defect_summary") or "vacancy" in template_id or "defect" in template_id:
        focuses.append("defects")
    if (
        "gan" in "".join(materials)
        and any(marker in "".join(materials) for marker in ("al", "in"))
        and ("heterostructure" in family or "quantum" in family)
    ):
        focuses.append("iii_nitride_hemt_2deg")

    return _unique_preserving_order(focuses)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _unique_preserving_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def supported_cjk_semiconductor_aliases() -> list[dict[str, Any]]:
    """Return deterministic Chinese semiconductor aliases exposed to clients."""

    aliases: list[dict[str, Any]] = []
    for terms, template_id, surface_template_id in _CJK_SEMICONDUCTOR_TEMPLATE_ALIASES:
        aliases.append(
            {
                "terms": list(terms),
                "template_id": template_id,
                "surface_template_id": surface_template_id,
                "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS) if surface_template_id else [],
            }
        )
    for item in _CJK_SEMICONDUCTOR_DISCOVERY_ALIASES:
        alias = {
            "terms": list(item["terms"]),
            "template_id": str(item["template_id"]),
            "surface_template_id": item.get("surface_template_id"),
            "surface_intent_terms": list(item.get("surface_intent_terms") or ()),
        }
        if item.get("intent_terms"):
            alias["intent_terms"] = list(item["intent_terms"])
        if item.get("notes"):
            alias["notes"] = str(item["notes"])
        aliases.append(alias)
    return aliases


def supported_templates() -> list[dict[str, Any]]:
    """Return supported deterministic new-structure templates."""

    templates = [
        {
            "template_id": str(item["template_id"]),
            "terms": list(item["terms"]),
            "example": str(item["example"]),
            "notes": str(item["notes"]),
            "domain": str(item.get("domain", "general")),
        }
        for item in TEMPLATE_SPECS
    ]
    templates.extend(
        {
            "template_id": str(item["template_id"]),
            "terms": list(item["terms"]),
            "example": "benzene_spec.json + functional-group patch",
            "notes": str(item["notes"]),
        }
        for item in SUBSTITUTED_BENZENE_TEMPLATES
    )
    return templates


def supported_patch_commands() -> list[dict[str, Any]]:
    """Return supported conservative local patch command patterns."""

    return [
        {
            "template_id": "delete_atom",
            "operations": ["delete_atom"],
            "requires_existing_project": True,
            "pattern": "Delete/remove atom by explicit atom id, e.g. 'Delete H1'.",
        },
        {
            "template_id": "substitute_atom",
            "operations": ["substitute_atom"],
            "requires_existing_project": True,
            "pattern": "Replace/substitute/change atom id with an element, e.g. 'Replace H1 with N'.",
        },
        {
            "template_id": "set_atom_position",
            "operations": ["set_atom_position"],
            "requires_existing_project": True,
            "pattern": "Move/set/place atom id to explicit Cartesian coordinates, e.g. 'Move H1 to 2.6, 0, 0'.",
        },
        {
            "template_id": "add_atom",
            "operations": ["add_atom", "add_bond"],
            "requires_existing_project": True,
            "pattern": "Add atom at explicit Cartesian coordinates, optionally bonded to an atom id.",
        },
        {
            "template_id": "add_bond",
            "operations": ["add_bond"],
            "requires_existing_project": True,
            "pattern": "Add/create a bond between explicit atom ids, optionally with single/double/triple/aromatic type.",
        },
        {
            "template_id": "delete_bond",
            "operations": ["delete_bond"],
            "requires_existing_project": True,
            "pattern": "Delete/remove a bond between explicit atom ids.",
        },
        {
            "template_id": "set_bond_type",
            "operations": ["set_bond_type"],
            "requires_existing_project": True,
            "pattern": "Change an existing bond between explicit atom ids to single/double/triple/aromatic type.",
        },
        {
            "template_id": "functional_group",
            "operations": ["delete_atom", "add_atom", "add_bond"],
            "requires_existing_project": True,
            "pattern": "Replace a bonded H or substituted site with nitro, hydroxyl, amino, or methyl group.",
        },
        {
            "template_id": "crystal_supercell",
            "operations": ["make_supercell"],
            "requires_existing_project": True,
            "pattern": "Make an explicit crystal supercell, e.g. 'make 2x2x2 supercell'.",
        },
        {
            "template_id": "crystal_superlattice_period",
            "operations": ["make_supercell", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Repeat a semiconductor heterostructure/superlattice along its interface axis, e.g. 'make 3-period superlattice'.",
        },
        {
            "template_id": "crystal_vacancy",
            "operations": ["delete_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Create a vacancy by explicit crystal atom id or deterministic auto-site selection, e.g. 'create vacancy at Si1' or 'create a Si vacancy'.",
        },
        {
            "template_id": "crystal_antisite",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Create an antisite defect by explicit crystal atom id, e.g. 'create As antisite at Ga1'.",
        },
        {
            "template_id": "crystal_dopant",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Substitute a crystal site with a dopant by explicit atom id or deterministic auto-site selection, e.g. 'dope Si1 with P' or 'dope with P'.",
        },
        {
            "template_id": "reconcile_dopant_metadata",
            "operations": ["reconcile_dopant_metadata"],
            "requires_existing_project": True,
            "pattern": "Create a metadata-only revision that removes or expands stale concrete dopant-site records, e.g. 'repair current dopant metadata' or '\u4fee\u590d\u5f53\u524d\u63ba\u6742\u4f4d\u70b9\u5143\u6570\u636e'.",
        },
        {
            "template_id": "crystal_sublattice_dopant",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Substitute the first deterministic host-sublattice site, e.g. 'Si_Ga dopant', 'Si on Ga site', 'dope Ga sublattice with Si', or 'dope S sublattice with Cl' for MoS2.",
        },
        {
            "template_id": "crystal_dopant_fraction",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Create a deterministic dopant concentration by replacing host sublattice sites, e.g. 'dope 6.25% P', 'replace 25% Si with P dopants', or 'dope 12.5% Cl' in a TMD.",
        },
        {
            "template_id": "crystal_dopant_dilution",
            "operations": ["substitute_atom", "make_supercell", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Dilute an existing dopant into an explicit supercell while keeping one dopant, e.g. 'dilute the P dopant into a 2x2x2 supercell and keep one P dopant'.",
        },
        {
            "template_id": "semiconductor_carrier_type",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": (
                "Map conservative semiconductor carrier intent to dopants, e.g. 'n-type silicon' -> P, "
                "'electron-doped GaAs' -> Si_Ga, 'acceptor-doped GaN' -> Mg_Ga, or 'p-type GaN' -> Mg_Ga."
            ),
        },
        {
            "template_id": "semiconductor_pn_junction",
            "operations": ["make_supercell", "substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": (
                "Create deterministic p/n region dopants using the current semiconductor mapping, "
                "e.g. 'make it a p-n junction', 'Build GaAs PN junction', or 'Build 4H-SiC PN junction'."
            ),
        },
        {
            "template_id": "crystal_alloy_fraction",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Create a deterministic alloy fraction by replacing host sublattice sites, e.g. 'make 25% Ge alloy' or 'replace 25% Si with Ge'.",
        },
        {
            "template_id": "crystal_vacuum",
            "operations": ["add_vacuum", "set_vacuum"],
            "requires_existing_project": True,
            "pattern": "Add or set vacuum along a lattice axis, e.g. 'add 15 angstrom vacuum along z' or 'set vacuum to 20 angstrom'.",
        },
        {
            "template_id": "crystal_center_slab",
            "operations": ["center_slab"],
            "requires_existing_project": True,
            "pattern": "Center a slab in its vacuum region, e.g. 'center the slab in the vacuum'.",
        },
        {
            "template_id": "interface_scaffold_gap",
            "operations": ["set_interface_gap"],
            "requires_existing_project": True,
            "pattern": "Adjust an existing semiconductor interface scaffold gap, e.g. 'set interface gap to 2.5 angstrom' or '\u628a\u754c\u9762\u95f4\u8ddd\u8c03\u5230 2.5 \u57c3'.",
        },
        {
            "template_id": "gate_stack_thickness",
            "operations": ["set_gate_stack_thickness"],
            "requires_existing_project": True,
            "pattern": "Adjust a MOS gate-stack layer thickness, e.g. 'set HfO2 thickness to 6 angstrom' or 'make TiN gate thickness 2 angstrom'.",
        },
        {
            "template_id": "quantum_well_thickness",
            "operations": ["set_quantum_well_thickness"],
            "requires_existing_project": True,
            "pattern": "Adjust an existing semiconductor well/barrier thickness, e.g. 'set barrier thickness to 15 nm'.",
        },
        {
            "template_id": "p_gan_gate_cap_thickness",
            "operations": ["set_p_gan_gate_cap_thickness"],
            "requires_existing_project": True,
            "pattern": "Adjust an existing p-GaN HEMT gate/cap thickness, e.g. 'set p-GaN gate thickness to 2 nm'.",
        },
        {
            "template_id": "metal_semiconductor_contact_parameters",
            "operations": ["set_metadata"],
            "requires_existing_project": True,
            "pattern": "Update metal/semiconductor contact preflight metadata, e.g. 'set metal work function to 4.6 eV, electron affinity to 4.0 eV, band gap to 1.2 eV, interface gap to 2.4 angstrom', or 'set n-type Schottky barrier to 0.45 eV'.",
        },
        {
            "template_id": "metal_semiconductor_contact_metal",
            "operations": ["substitute_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Replace the metal contact layer in an existing metal/semiconductor contact, e.g. 'change the metal contact to Pt' or 'replace Al contact with Au'.",
        },
        {
            "template_id": "metal_semiconductor_contact_gap",
            "operations": ["set_atom_position", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Move the metal contact layer to set a real interface/contact gap, e.g. 'set the interface gap to 3.0 angstrom'.",
        },
        {
            "template_id": "metal_semiconductor_contact_thickness",
            "operations": ["set_atom_position", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Scale the metal contact layer thickness in an existing metal/semiconductor contact, e.g. 'set metal contact thickness to 2.8 angstrom'.",
        },
        {
            "template_id": "castep_settings",
            "operations": ["set_castep_energy"],
            "requires_existing_project": True,
            "pattern": "Update CASTEP task/cutoff/k-point settings without rebuilding geometry, e.g. 'set CASTEP cutoff to 600 eV and kpoint separation 0.03', 'calculate the band gap', or 'set up PDOS'.",
        },
        {
            "template_id": "apply_recommended_semiconductor_kpoint_grid",
            "operations": ["set_castep_energy"],
            "requires_existing_project": True,
            "requires_current_diagnostic_recommendation": True,
            "requires_explicit_confirmation": True,
            "pattern": "Apply the current revision's recommended semiconductor k-point grid after reviewing the exact confirmation payload.",
        },
        {
            "template_id": "crystal_strain",
            "operations": ["set_lattice", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Apply deterministic lattice strain, e.g. 'apply 2% tensile strain in-plane' or 'apply 1% strain along c'.",
        },
        {
            "template_id": "crystal_lattice_parameters",
            "operations": ["set_lattice", "set_metadata"],
            "requires_existing_project": True,
            "pattern": (
                "Set explicit crystal lattice lengths or angles while preserving fractional coordinates, "
                "e.g. 'set lattice parameters a=3.189 and c=5.185 angstrom' or "
                "'\u628a\u6676\u683c\u53c2\u6570 a \u548c b \u8bbe\u4e3a 3.189 \u57c3'."
            ),
        },
        {
            "template_id": "crystal_layer_translation",
            "operations": ["translate_crystal_atoms", "set_metadata"],
            "requires_existing_project": True,
            "pattern": (
                "Rigidly translate an explicit semiconductor layer laterally with periodic wrapping, "
                "e.g. 'shift layer 3 by 0.5 angstrom along x' or '将顶层沿 y 方向平移 -0.25 埃'."
            ),
        },
        {
            "template_id": "crystal_layer_rotation",
            "operations": ["rotate_crystal_atoms", "set_metadata"],
            "requires_existing_project": True,
            "pattern": (
                "Rigidly rotate a semiconductor layer around its orthogonal profile axis as a visual-review "
                "twist scaffold, e.g. 'twist the top layer by 3 degrees' or "
                "'\u5c06\u7b2c 3 \u5c42\u7ed5 c \u8f74\u65cb\u8f6c 5 \u5ea6'."
            ),
        },
        {
            "template_id": "commensurate_tmd_heterobilayer",
            "operations": ["make_commensurate_tmd_heterobilayer"],
            "requires_existing_project": False,
            "pattern": (
                "Build a strain-controlled exact integer coincidence TMD heterobilayer from two reviewed "
                "MoS2/WS2/MoSe2/WSe2 monolayers, e.g. 'build MoS2/WS2 commensurate twisted "
                "heterobilayer with m=2, n=1' or '构建 MoS2/WSe2 共格扭转异质双层，m=2,n=1'."
            ),
        },
        {
            "template_id": "commensurate_tmd_twisted_bilayer",
            "operations": ["make_commensurate_twisted_bilayer"],
            "requires_existing_project": True,
            "pattern": (
                "Build an exact integer commensurate MX2 TMD homobilayer from a pristine periodic monolayer, "
                "e.g. 'make a commensurate twisted bilayer with m=2, n=1' or "
                "'\u6784\u5efa m=2,n=1 \u7684\u5171\u683c\u626d\u8f6c\u53cc\u5c42'."
            ),
        },
        {
            "template_id": "crystal_add_atom_fractional",
            "operations": ["add_atom"],
            "requires_existing_project": True,
            "pattern": "Add a crystal atom at fractional coordinates, e.g. 'add H at fractional 0.5 0.5 0.2'.",
        },
        {
            "template_id": "crystal_interstitial_fractional",
            "operations": ["add_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Add an interstitial crystal atom at fractional coordinates, e.g. 'add Si interstitial at fractional 0.5 0.5 0.5'.",
        },
        {
            "template_id": "crystal_set_atom_fractional",
            "operations": ["set_atom_position"],
            "requires_existing_project": True,
            "pattern": "Move a crystal atom to fractional coordinates, e.g. 'move Si1 to fractional 0.5 0.5 0.3'.",
        },
        {
            "template_id": "crystal_hydrogen_passivation",
            "operations": ["add_atom", "set_metadata"],
            "requires_existing_project": True,
            "pattern": "Hydrogen-passivate the top or both slab surfaces, e.g. 'hydrogen passivate both surfaces'.",
        },
        {
            "template_id": "crystal_surface_preparation",
            "operations": ["set_vacuum", "add_vacuum", "center_slab", "translate_crystal_atoms", "add_atom", "set_metadata", "set_castep_energy"],
            "requires_existing_project": True,
            "pattern": "Combine deterministic slab preparation edits, e.g. 'center the slab, fully hydrogen-passivate both surfaces, set CASTEP cutoff to 600 eV, and hot-load it'.",
        },
        {
            "template_id": "crystal_composite_edit",
            "operations": ["make_supercell", "translate_crystal_atoms", "substitute_atom", "delete_atom", "set_metadata", "set_castep_energy"],
            "requires_existing_project": True,
            "pattern": "Combine deterministic current-crystal edits, e.g. 'make 2x1x1 supercell, dope with P, and set CASTEP cutoff to 600 eV'.",
        },
    ]


def infer_modeling_plan(
    user_request: str,
    *,
    project_id: str | None = None,
    current_spec: ModelSpec | None = None,
) -> NaturalLanguagePlan:
    """Infer a local structured payload from a natural-language request.

    The planner is intentionally conservative. It only returns a payload for
    exact local templates or precise atom-level patch commands.
    """

    text = " ".join(user_request.strip().lower().split())
    prefer_new_structure = _looks_like_new_structure_request(text)
    if current_spec is not None and not prefer_new_structure:
        redo_plan = _infer_redo_plan(text, current_spec)
        if redo_plan is not None:
            return redo_plan
        rollback_plan = _infer_rollback_plan(text, current_spec)
        if rollback_plan is not None:
            return rollback_plan
        recommended_kpoint_plan = _infer_recommended_kpoint_remediation_plan(
            text,
            current_spec,
        )
        if recommended_kpoint_plan is not None:
            return recommended_kpoint_plan
        patch_plan = _infer_patch(user_request, current_spec)
        if patch_plan is not None:
            return patch_plan
        replay_plan = _infer_continue_view_replay_plan(text, current_spec)
        if replay_plan is not None:
            return replay_plan
        show_current_plan = _infer_show_current_plan(text, current_spec)
        if show_current_plan is not None:
            return show_current_plan
        inspect_current_plan = _infer_inspect_current_plan(text, current_spec)
        if inspect_current_plan is not None:
            return inspect_current_plan

    template_plan = _infer_template(text, user_request=user_request, project_id=project_id)
    if template_plan is not None:
        return template_plan

    return NaturalLanguagePlan(
        kind="unsupported",
        payload=None,
        confidence=0.0,
        template_id=None,
        notes=[
            "No conservative local template matched the request.",
            "Provide a ModelSpec for new structures or a SemanticPatch for modifications.",
        ],
    )


def _infer_recommended_kpoint_remediation_plan(
    text: str,
    current_spec: ModelSpec,
) -> NaturalLanguagePlan | None:
    """Infer a request to apply the current diagnostic k-point recommendation."""

    if not _looks_like_apply_recommended_kpoint_request(text):
        return None
    return NaturalLanguagePlan(
        kind="apply_recommended_kpoint_grid",
        payload={
            "project_id": current_spec.project_id,
            "revision": current_spec.revision,
            "action_id": "apply_recommended_semiconductor_kpoint_grid",
            "requires_explicit_confirmation": True,
        },
        confidence=0.94,
        template_id="apply_recommended_semiconductor_kpoint_grid",
        notes=[
            (
                "Resolve the exact slab-aware k-point patch from current project "
                f"{current_spec.project_id} r{current_spec.revision:03d}."
            ),
            "Do not create a revision until the returned confirmation payload is explicitly approved.",
            "Re-export electronic diagnostics after applying the simulation-only patch.",
        ],
    )


def _looks_like_apply_recommended_kpoint_request(text: str) -> bool:
    """Return whether text asks to apply, rather than merely inspect, a recommendation."""

    if not text:
        return False
    lowered = " ".join(text.lower().split())
    compact = re.sub(r"[\s,.;:!?()\[\]{}_-]+", "", lowered)
    has_kpoint = bool(
        re.search(r"\bk\s*[- ]?points?\b|\bkpoints?\s+grid\b|\bk\s+grid\b", lowered)
    ) or any(
        token in compact
        for token in (
            "k\u70b9",
            "k\u70b9\u7f51\u683c",
            "k\u7f51\u683c",
        )
    )
    has_recommendation = bool(
        re.search(r"\b(?:recommended|recommendation|suggested|suggestion)\b", lowered)
    ) or any(token in compact for token in ("\u63a8\u8350", "\u5efa\u8bae"))
    has_action = bool(
        re.search(
            r"\b(?:apply|accept|adopt|use|confirm|approve|set|switch|update)\b",
            lowered,
        )
    ) or any(
        token in compact
        for token in (
            "\u5e94\u7528",
            "\u91c7\u7528",
            "\u4f7f\u7528",
            "\u63a5\u53d7",
            "\u786e\u8ba4",
            "\u540c\u610f",
            "\u6267\u884c",
            "\u8bbe\u7f6e",
            "\u6539\u4e3a",
            "\u66ff\u6362\u4e3a",
        )
    )
    return has_kpoint and has_recommendation and has_action


def _infer_continue_view_replay_plan(
    text: str,
    current_spec: ModelSpec,
) -> NaturalLanguagePlan | None:
    """Infer an explicit request to resume prepared GUI view replay."""

    if not _looks_like_continue_view_replay_request(text):
        return None
    return NaturalLanguagePlan(
        kind="continue_view_replay",
        payload={
            "project_id": current_spec.project_id,
            "revision": current_spec.revision,
        },
        confidence=0.92,
        template_id="continue_view_replay",
        notes=[
            f"Continue the prepared GUI view replay for {current_spec.project_id} r{current_spec.revision:03d}.",
            "Do not create a structural revision or issue GUI input until the next execution recipe is reviewed.",
        ],
    )


def _looks_like_continue_view_replay_request(text: str) -> bool:
    """Return whether text explicitly requests replay continuation rather than diagnostics export."""

    explicit_terms = (
        "continue the next gui view replay",
        "continue the next view replay",
        "resume the next gui view replay",
        "resume the next view replay",
        "continue prepared gui view replay",
        "resume prepared gui view replay",
    )
    if any(term in text for term in explicit_terms):
        return True

    english_replay = bool(
        re.search(
            r"\b(?:continue|resume|proceed(?:\s+with)?|next)\b.{0,48}"
            r"\b(?:gui\s+)?(?:view|camera)\s+replay\b",
            text,
        )
        or re.search(
            r"\b(?:continue|resume|validate)\b.{0,48}"
            r"\b(?:next\s+)?(?:gui\s+view|view\s+orientation|camera\s+view)\b",
            text,
        )
    )
    cjk_replay_terms = (
        "继续视角回放",
        "恢复视角回放",
        "继续回放视角",
        "回放下一个视角",
        "继续验证下一个视角",
        "继续验证下一个gui视角",
        "继续验证下一个 gui 视角",
        "恢复gui视角验证",
        "恢复 gui 视角验证",
    )
    return english_replay or any(term in text for term in cjk_replay_terms)


def _infer_inspect_current_plan(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    """Infer a request to inspect/export diagnostics for the current revision."""

    calculation_readiness = _looks_like_calculation_readiness_request(text)
    if not _looks_like_inspect_current_request(text) and not calculation_readiness:
        return None
    explicit_export = _looks_like_export_diagnostics_request(text)
    normality_check = _looks_like_normality_check_request(text)
    export_diagnostics = explicit_export or normality_check or calculation_readiness
    if calculation_readiness and not explicit_export:
        template_id = "inspect_current_calculation_preflight"
    else:
        template_id = "export_current_diagnostics" if explicit_export else "inspect_current_revision"
    return NaturalLanguagePlan(
        kind="inspect_current",
        payload={
            "project_id": current_spec.project_id,
            "revision": current_spec.revision,
            "export_diagnostics": export_diagnostics,
        },
        confidence=0.84,
        template_id=template_id,
        notes=[
            f"Inspect current project {current_spec.project_id} r{current_spec.revision:03d}.",
            "No structural patch is created; status uses the current revision as the source of truth.",
            "Export electronic-structure calculation preflight diagnostics for the current revision."
            if calculation_readiness and not explicit_export
            else (
            "Export view-bundle diagnostics for the normality check."
            if normality_check and not explicit_export
            else ("Export view-bundle diagnostics." if export_diagnostics else "Return live project status.")
            ),
        ],
    )


def _looks_like_inspect_current_request(text: str) -> bool:
    """Return True for requests to check current model health/normality."""

    if not text:
        return False
    current_terms = (
        "current",
        "latest",
        "this",
        "model",
        "structure",
        "revision",
        "project",
        "\u5f53\u524d",
        "\u6700\u65b0",
        "\u8fd9\u4e2a",
        "\u6a21\u578b",
        "\u7ed3\u6784",
        "\u9879\u76ee",
    )
    inspect_terms = (
        "status",
        "health",
        "normal",
        "normality",
        "diagnostic",
        "diagnostics",
        "inspect",
        "inspection",
        "check",
        "validate",
        "verify",
        "ready",
        "view audit",
        "view bundle",
        "\u72b6\u6001",
        "\u5065\u5eb7",
        "\u6b63\u5e38",
        "\u662f\u5426\u6b63\u5e38",
        "\u8bca\u65ad",
        "\u68c0\u67e5",
        "\u68c0\u6d4b",
        "\u4f53\u68c0",
        "\u8d28\u91cf",
        "\u8d28\u91cf\u68c0\u67e5",
        "\u5efa\u6a21\u8d28\u91cf",
        "\u6a21\u578b\u8d28\u91cf",
        "\u7ed3\u6784\u8d28\u91cf",
        "\u6821\u9a8c",
        "\u9a8c\u8bc1",
        "\u662f\u5426\u53ef\u7528",
        "\u662f\u5426\u51c6\u5907\u597d",
        "\u89c6\u89d2\u53c2\u6570",
        "\u6a21\u578b\u53c2\u6570",
    )
    if any(term in text for term in current_terms) and any(term in text for term in inspect_terms):
        return True
    if _looks_like_calculation_readiness_request(text) and any(term in text for term in current_terms):
        return True
    return _looks_like_export_diagnostics_request(text) and any(term in text for term in current_terms)


def _looks_like_export_diagnostics_request(text: str) -> bool:
    """Return True for current-model diagnostic artifact export requests."""

    if not text:
        return False
    export_terms = (
        "export",
        "write",
        "dump",
        "generate",
        "save",
        "view bundle",
        "view audit",
        "csv",
        "diagnostic bundle",
        "\u5bfc\u51fa",
        "\u751f\u6210",
        "\u4fdd\u5b58",
        "\u5199\u51fa",
        "\u89c6\u89d2\u53c2\u6570",
        "\u89c6\u56fe\u53c2\u6570",
        "\u8bca\u65ad\u8868",
        "\u8bca\u65ad\u6587\u4ef6",
        "\u6a21\u578b\u53c2\u6570",
    )
    diagnostic_terms = (
        "diagnostic",
        "diagnostics",
        "inspection",
        "view",
        "audit",
        "bundle",
        "csv",
        "parameter",
        "parameters",
        "\u8bca\u65ad",
        "\u68c0\u67e5",
        "\u89c6\u89d2",
        "\u89c6\u56fe",
        "\u53c2\u6570",
        "\u8868",
        "\u6587\u4ef6",
    )
    return any(term in text for term in export_terms) and any(term in text for term in diagnostic_terms)


def _looks_like_calculation_readiness_request(text: str) -> bool:
    """Return True for DFT/CASTEP calculation-readiness questions."""

    if not text:
        return False
    compact = re.sub(r"[\s,，、/\\._-]+", "", text.lower())
    lowered = " ".join(text.lower().split())
    readiness_terms = (
        "ready for calculation",
        "calculation readiness",
        "calculation ready",
        "calculation preflight",
        "preflight calculation",
        "pre-calculation",
        "before calculation",
        "ready for dft",
        "dft ready",
        "dft readiness",
        "before dft",
        "suitable for dft",
        "safe for dft",
        "ready for castep",
        "castep ready",
        "castep readiness",
        "before castep",
        "suitable for castep",
        "safe for castep",
        "can it run dft",
        "can we run dft",
        "can i run dft",
        "can this run dft",
        "can it run castep",
        "can we run castep",
        "can i run castep",
        "can this run castep",
        "can it do band structure",
        "can this do band structure",
    )
    if any(term in lowered for term in readiness_terms):
        return True
    compact_terms = (
        "\u8ba1\u7b97\u5c31\u7eea",
        "\u8ba1\u7b97\u524d\u68c0\u67e5",
        "\u8ba1\u7b97\u524d\u9884\u68c0",
        "\u8ba1\u7b97\u9884\u68c0",
        "\u8ba1\u7b97\u51c6\u5907",
        "\u8ba1\u7b97\u53ef\u7528\u6027",
        "\u9002\u5408\u505a\u8ba1\u7b97",
        "\u9002\u4e0d\u9002\u5408\u505a\u8ba1\u7b97",
        "\u80fd\u4e0d\u80fd\u505a\u8ba1\u7b97",
        "\u80fd\u5426\u505a\u8ba1\u7b97",
        "\u80fd\u4e0d\u80fd\u7b97",
        "\u80fd\u5426\u8ba1\u7b97",
        "\u662f\u5426\u53ef\u4ee5\u505a\u8ba1\u7b97",
        "\u53ef\u4e0d\u53ef\u4ee5\u505a\u8ba1\u7b97",
        "dft\u9884\u68c0",
        "dft\u8ba1\u7b97\u524d\u68c0\u67e5",
        "castep\u9884\u68c0",
        "castep\u8ba1\u7b97\u524d\u68c0\u67e5",
        "\u7b2c\u4e00\u6027\u539f\u7406\u9884\u68c0",
        "\u7b2c\u4e00\u6027\u539f\u7406\u8ba1\u7b97\u524d\u68c0\u67e5",
        "\u80fd\u4e0d\u80fd\u505adft",
        "\u80fd\u5426\u505adft",
        "\u662f\u5426\u9002\u5408\u505adft",
        "\u9002\u5408\u505adft",
        "\u80fd\u4e0d\u80fd\u505acastep",
        "\u80fd\u5426\u505acastep",
        "\u662f\u5426\u9002\u5408\u505acastep",
        "\u9002\u5408\u505acastep",
        "\u80fd\u4e0d\u80fd\u505a\u80fd\u5e26\u8ba1\u7b97",
        "\u80fd\u5426\u505a\u80fd\u5e26\u8ba1\u7b97",
        "\u9002\u5408\u505a\u80fd\u5e26\u8ba1\u7b97",
        "\u80fd\u4e0d\u80fd\u7b97\u5e26\u9699",
        "\u80fd\u5426\u7b97\u5e26\u9699",
    )
    if any(term in compact for term in compact_terms):
        return True
    domain_terms = ("dft", "castep", "\u7b2c\u4e00\u6027\u539f\u7406", "\u80fd\u5e26\u8ba1\u7b97", "\u5e26\u9699\u8ba1\u7b97")
    readiness_markers = (
        "ready",
        "preflight",
        "\u9884\u68c0",
        "\u524d\u68c0\u67e5",
        "\u5c31\u7eea",
        "\u9002\u5408",
        "\u80fd\u4e0d\u80fd",
        "\u80fd\u5426",
        "\u662f\u5426\u53ef\u4ee5",
        "\u53ef\u4e0d\u53ef\u4ee5",
    )
    return any(term in compact for term in domain_terms) and any(
        marker in compact for marker in readiness_markers
    )


def _looks_like_normality_check_request(text: str) -> bool:
    """Return True when the request asks whether a model is normal/healthy."""

    if not text:
        return False
    check_terms = (
        "check",
        "inspect",
        "verify",
        "validate",
        "confirm",
        "whether",
        "if",
        "is it",
        "is the",
        "\u68c0\u67e5",
        "\u9a8c\u8bc1",
        "\u6821\u9a8c",
        "\u786e\u8ba4",
        "\u4f53\u68c0",
        "\u8d28\u91cf\u68c0\u67e5",
        "\u8d28\u91cf\u8bc4\u4f30",
        "\u662f\u5426",
        "\u770b\u770b",
        "\u6b63\u5e38\u5417",
        "\u5f53\u524d\u6a21\u578b\u6b63\u5e38\u5417",
    )
    normality_terms = (
        "normal",
        "normality",
        "evidence-based normality",
        "evidence based normality",
        "healthy",
        "health",
        "sanity",
        "valid",
        "ok",
        "okay",
        "\u6b63\u5e38",
        "\u5065\u5eb7",
        "\u53ef\u7528",
        "\u4f53\u68c0",
        "\u8d28\u91cf",
        "\u5efa\u6a21\u8d28\u91cf",
        "\u6a21\u578b\u8d28\u91cf",
        "\u7ed3\u6784\u8d28\u91cf",
    )
    return any(term in text for term in normality_terms) and (
        any(term in text for term in check_terms) or "?" in text
    )


def _infer_show_current_plan(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    """Infer a request to show or refresh the current revision in the live GUI."""

    if not _looks_like_show_current_request(text):
        return None
    export_diagnostics = _looks_like_export_diagnostics_request(text)
    return NaturalLanguagePlan(
        kind="show_current",
        payload={
            "project_id": current_spec.project_id,
            "revision": current_spec.revision,
            "export_diagnostics": export_diagnostics,
        },
        confidence=0.82,
        template_id="show_current_revision_with_diagnostics" if export_diagnostics else "show_current_revision",
        notes=[
            f"Open or refresh current project {current_spec.project_id} r{current_spec.revision:03d} in Materials Studio.",
            "No structural patch is created; the current revision remains the source of truth.",
            "Export view-bundle diagnostics." if export_diagnostics else "Use existing diagnostic export settings.",
        ],
    )


def _looks_like_show_current_request(text: str) -> bool:
    """Return True for conversation-style requests to load the current revision."""

    if not text:
        return False
    if any(
        term in text
        for term in (
            "显示当前模型",
            "显示当前界面",
            "打开当前模型",
            "刷新当前模型",
            "热加载当前模型",
            "把当前模型推到 Materials Studio",
            "把当前模型推到当前窗口",
            "把它推到当前窗口",
        )
    ):
        return True
    current_terms = (
        "current",
        "latest",
        "this",
        "it",
        "model",
        "structure",
        "revision",
        "project",
        "\u5f53\u524d",
        "\u5f53\u524d\u89c6\u56fe\u53c2\u6570",
        "\u5f53\u524d\u6a21\u578b\u89c6\u56fe\u53c2\u6570",
        "\u5f53\u524d\u6a21\u578b\u89c6\u89d2\u53c2\u6570",
        "\u5f53\u524d\u7a97\u53e3",
        "\u5f53\u524d\u754c\u9762",
        "\u6700\u65b0",
        "\u8fd9\u4e2a",
        "\u5b83",
        "\u6a21\u578b",
        "\u7ed3\u6784",
        "\u9879\u76ee",
        "当前模型",
        "当前界面",
    )
    action_terms = (
        "show",
        "display",
        "view",
        "see",
        "open",
        "load",
        "reload",
        "refresh",
        "hot-load",
        "hot load",
        "hotload",
        "live-load",
        "live load",
        "push",
        "send",
        "sync",
        "update",
        "push to",
        "push into",
        "send to",
        "send into",
        "sync to",
        "sync into",
        "\u663e\u793a",
        "\u5c55\u793a",
        "\u770b\u5230",
        "\u770b\u89c1",
        "\u6253\u5f00",
        "\u52a0\u8f7d",
        "\u8f7d\u5165",
        "\u91cd\u65b0\u6253\u5f00",
        "\u5237\u65b0",
        "\u70ed\u52a0\u8f7d",
        "\u91cd\u65b0\u70ed\u52a0\u8f7d",
        "\u5b9e\u65f6\u70ed\u52a0\u8f7d",
        "\u63a8\u9001",
        "\u63a8\u5230",
        "\u63a8\u5165",
        "\u53d1\u9001",
        "\u53d1\u5230",
        "\u9001\u5230",
        "推到",
        "\u540c\u6b65",
        "\u66f4\u65b0",
    )
    live_load_terms = (
        "hot-load",
        "hot load",
        "hotload",
        "live-load",
        "live load",
        "\u70ed\u52a0\u8f7d",
        "\u5b9e\u65f6\u70ed\u52a0\u8f7d",
    )
    gui_terms = (
        "materials studio",
        "material studio",
        "gui",
        "matstudio",
        "\u754c\u9762",
        "\u7a97\u53e3",
    )
    lowered = text.lower()
    padded = f" {lowered.replace('.', ' ').replace(',', ' ')} "
    mentions_gui = any(term in lowered for term in gui_terms) or " ms " in padded
    has_action = any(term in lowered for term in action_terms) or any(term in text for term in action_terms if term != term.lower())
    has_current = any(term in lowered for term in current_terms) or any(term in text for term in current_terms if term != term.lower())
    has_live_load = any(term in lowered for term in live_load_terms) or any(term in text for term in live_load_terms if term != term.lower())
    if has_live_load and has_current:
        return True
    if mentions_gui and has_action and (has_current or "again" in lowered):
        return True
    if has_action and any(term in lowered for term in ("current gui", "open gui", "live gui", "\u5f53\u524d gui", "\u5df2\u6253\u5f00")):
        return True
    return False

def _infer_redo_plan(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    """Infer a live-session redo command."""

    if not _looks_like_redo_request(text):
        return None
    return NaturalLanguagePlan(
        kind="redo",
        payload={},
        confidence=0.84,
        template_id="redo_revision",
        notes=[
            f"Redo the last rollback from current revision r{current_spec.revision:03d}.",
            "The server resolves the redo target from non-destructive revision history.",
        ],
    )


def _looks_like_redo_request(text: str) -> bool:
    """Return True for redo/reapply live-session requests."""

    terms = (
        "redo",
        "re-do",
        "reapply",
        "re-apply",
        "restore undone",
        "restore the undone",
        "bring back the change",
        "apply the undone",
        "\u91cd\u505a",
        "\u91cd\u505a\u4e0a\u4e00\u6b65",
        "\u91cd\u505a\u521a\u624d",
        "\u6062\u590d\u64a4\u9500",
        "\u6062\u590d\u64a4\u9500\u7684",
        "\u6062\u590d\u521a\u624d\u64a4\u9500",
        "\u91cd\u65b0\u5e94\u7528",
        "\u518d\u6b21\u5e94\u7528",
        "\u5e94\u7528\u64a4\u9500\u7684",
    )
    return any(term in text for term in terms)


def _infer_rollback_plan(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    """Infer a live-session rollback command."""

    if not _looks_like_rollback_request(text):
        return None
    target_revision = _rollback_target_revision(text, current_spec.revision)
    if target_revision is None:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id="rollback_revision",
            notes=[
                "Rollback was requested, but the current project has no earlier revision to restore.",
                "Create or modify the model first, then request undo/rollback.",
            ],
        )
    if target_revision >= current_spec.revision:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id="rollback_revision",
            notes=[
                f"Rollback target r{target_revision:03d} is not older than the current revision r{current_spec.revision:03d}.",
                "Choose an earlier revision, for example 'rollback to r000'.",
            ],
        )
    template_id = "restore_revision" if _looks_like_explicit_restore_request(text) else "rollback_revision"
    verb = "Restore" if template_id == "restore_revision" else "Rollback"
    return NaturalLanguagePlan(
        kind="rollback",
        payload={"target_revision": target_revision},
        confidence=0.9,
        template_id=template_id,
        notes=[
            f"{verb} current project from r{current_spec.revision:03d} to r{target_revision:03d}.",
            "This creates a new revision and does not delete history.",
        ],
    )


def _looks_like_rollback_request(text: str) -> bool:
    """Return True for undo/rollback live-session requests."""

    terms = (
        "undo",
        "revert",
        "rollback",
        "roll back",
        "go back",
        "restore",
        "restore previous",
        "load revision",
        "load version",
        "load r",
        "switch to revision",
        "switch to version",
        "switch to r",
        "checkout revision",
        "checkout r",
        "previous revision",
        "previous version",
        "last revision",
        "last version",
        "\u64a4\u9500",
        "\u64a4\u56de",
        "\u56de\u9000",
        "\u56de\u6eda",
        "\u9000\u56de",
        "\u56de\u5230\u4e0a\u4e00\u4e2a",
        "\u56de\u5230\u4e0a\u4e00\u7248",
        "\u56de\u5230\u524d\u4e00\u7248",
        "\u8fd4\u56de\u4e0a\u4e00\u4e2a",
        "\u8fd4\u56de\u4e0a\u4e00\u7248",
        "\u4e0a\u4e00\u4e2a revision",
        "\u524d\u4e00\u4e2a revision",
        "\u4e0a\u4e00\u4e2a\u7248\u672c",
        "\u524d\u4e00\u4e2a\u7248\u672c",
        "\u6062\u590d r",
        "\u6062\u590d\u5230 r",
        "\u52a0\u8f7d r",
        "\u5207\u6362\u5230 r",
        "\u56de\u5230 r",
        "\u56de\u9000\u5230 r",
    )
    return any(term in text for term in terms)


def _looks_like_explicit_restore_request(text: str) -> bool:
    """Return True for explicit revision-loading commands."""

    if _explicit_revision_target(text) is None:
        return False
    if any(term in text for term in ("\u64a4\u9500", "\u56de\u9000", "\u56de\u6eda", "\u9000\u56de")):
        return False
    if any(term in text for term in ("\u6062\u590d", "\u6062\u590d\u5230", "\u52a0\u8f7d", "\u5207\u6362", "\u5207\u6362\u5230")):
        return True
    restore_terms = (
        "restore",
        "load",
        "switch",
        "checkout",
    )
    rollback_terms = ("undo", "rollback", "roll back", "revert", "鎾ら攢", "鍥為€€", "鍥炴粴")
    return any(term in text for term in restore_terms) and not any(term in text for term in rollback_terms)


def _rollback_target_revision(text: str, current_revision: int) -> int | None:
    """Return the requested rollback target revision."""

    explicit = _explicit_revision_target(text)
    if explicit is not None:
        return explicit
    if current_revision <= 0:
        return None
    return current_revision - 1


def _explicit_revision_target(text: str) -> int | None:
    """Return an explicit revision number in text, if present."""

    for pattern in (
        r"\br\s*0*(\d+)\b",
        r"\brev(?:ision)?\s*0*(\d+)\b",
        r"\bversion\s*0*(\d+)\b",
        r"(?:revision|rev|version|r|v)\s*#?\s*0*(\d+)",
        r"(?:\u4fee\u8ba2|\u4fee\u8ba2\u7248|\u7248\u672c|\u7248)\s*0*(\d+)",
        r"\u7b2c\s*0*(\d+)\s*(?:\u4fee\u8ba2|\u4fee\u8ba2\u7248|\u7248\u672c|\u7248)",
    ):
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _looks_like_new_structure_request(text: str) -> bool:
    """Return True when text appears to ask for a new model instead of editing the current one."""

    if _match_commensurate_tmd_heterobilayer(text) is not None and (
        re.search(r"\b(?:current|existing)\s+(?:model|structure|project)\b", text, flags=re.IGNORECASE)
        or any(
            term in text
            for term in (
                "\u5f53\u524d\u6a21\u578b",
                "\u73b0\u6709\u6a21\u578b",
                "\u5f53\u524d\u7ed3\u6784",
                "\u73b0\u6709\u7ed3\u6784",
                "\u5f53\u524d\u9879\u76ee",
            )
        )
    ):
        return False
    if _looks_like_current_crystal_modifier_request(text):
        return False
    if _contains_new_structure_verb(text) and (
        _matches_template_term(text)
        or (_looks_like_semiconductor_pn_junction_text(text) and _text_mentions_silicon(text))
    ):
        return True
    if any(
        matcher(text) is not None
        for matcher in (
            _match_make_supercell,
            _match_superlattice_period,
            _match_set_vacuum,
            _match_add_vacuum,
            _match_center_slab,
            _match_gate_stack_thickness,
            _match_current_quantum_well_thickness,
            _match_crystal_lattice_parameters,
            _match_crystal_layer_translation,
            _match_crystal_layer_rotation,
            _match_commensurate_tmd_heterobilayer,
            _match_commensurate_tmd_twisted_bilayer,
            _match_crystal_strain,
            _match_crystal_vacancy,
            _match_crystal_auto_vacancy,
            _match_crystal_antisite,
            _match_crystal_dopant,
            _match_crystal_sublattice_dopant,
            _match_crystal_auto_dopant,
            _match_crystal_dopant_fraction,
            _match_semiconductor_carrier_type,
            _match_semiconductor_pn_junction,
            _match_crystal_alloy_fraction,
            _match_crystal_hydrogen_passivation_request,
            _match_crystal_interstitial_fractional,
            _match_crystal_add_atom_fractional,
            _match_crystal_set_atom_fractional,
        )
    ):
        return False
    if _contains_new_structure_verb(text):
        return True
    if re.search(r"^\s*model\s+(?!it\b|this\b|the\s+current\b|current\b|live\b)", text, flags=re.IGNORECASE):
        return True
    if any(term in text for term in ("鏋勫缓", "鍒涘缓", "鐢熸垚", "寤虹珛", "鏂板缓", "鎼缓")):
        return True
    if re.search(r"\bmake\s+(?!it\b|this\b)", text, flags=re.IGNORECASE):
        return True
    return False


def _looks_like_current_crystal_modifier_request(text: str) -> bool:
    """Return True for concise crystal modifier requests that should patch the current model."""

    if _contains_explicit_new_model_noun(text):
        return False
    return any(
        matcher(text) is not None
        for matcher in (
            _match_crystal_vacancy,
            _match_crystal_auto_vacancy,
            _match_crystal_antisite,
            _match_set_vacuum,
            _match_center_slab,
            _match_gate_stack_thickness,
            _match_current_quantum_well_thickness,
            _match_current_p_gan_gate_cap_thickness,
            _match_crystal_lattice_parameters,
            _match_crystal_layer_translation,
            _match_crystal_layer_rotation,
            _match_commensurate_tmd_twisted_bilayer,
            _match_crystal_interstitial_fractional,
            _match_crystal_add_atom_fractional,
            _match_crystal_set_atom_fractional,
        )
    )



def _contains_explicit_new_model_noun(text: str) -> bool:
    """Return True when text explicitly names a structure class/template."""

    return bool(
        re.search(
            r"\b(?:crystal|surface|slab|bilayer|heterostructure|interface|superlattice|quantum\s+well|mqw|gate\s+stack|mos\s+capacitor|hemt|2deg|two[-\s]+dimensional\s+electron\s+gas|high\s+electron\s+mobility\s+transistor)\b",
            text,
            flags=re.IGNORECASE,
        )
        or any(term in text for term in ("\u53cc\u5c42", "\u4e8c\u7ef4\u7535\u5b50\u6c14", "\u9ad8\u7535\u5b50\u8fc1\u79fb\u7387\u6676\u4f53\u7ba1"))
    )



def _contains_new_structure_verb(text: str) -> bool:
    if re.search(r"\b(?:build|create|generate|construct)\b", text, flags=re.IGNORECASE):
        return True
    return any(term in text for term in ("\u6784\u5efa", "\u521b\u5efa", "\u751f\u6210", "\u65b0\u5efa"))


def _matches_template_term(text: str) -> bool:
    return any(_template_matches_text(template, text) for template in TEMPLATE_SPECS) or any(
        any(_contains_term(text, str(term)) for term in template["terms"])
        for template in SUBSTITUTED_BENZENE_TEMPLATES
    )


def _template_matches_text(template: dict[str, Any], text: str) -> bool:
    template_id = str(template.get("template_id") or "")
    if template_id == "silicon_pn_junction" and not _silicon_pn_junction_template_context_ok(text):
        return False
    if any(
        _contains_term(text, str(term))
        for term in sorted(template["terms"], key=lambda value: len(str(value)), reverse=True)
    ):
        return True
    if template_id == "gallium_arsenide_aluminum_arsenide_001_heterostructure":
        return _material_alias_present(text, "GaAs") and _material_alias_present(text, "AlAs")
    if template_id == "silicon_germanium_001_heterostructure":
        return _material_alias_present(text, "Si") and _material_alias_present(text, "Ge")
    if template_id == "silicon_carbide_3c_zincblende":
        return _material_alias_present(text, "SiC")
    if template_id == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure":
        return (_material_alias_present(text, "AlGaN") or _match_algan_formula_alloy(text) is not None) and _material_alias_present(text, "GaN")
    if template_id == "aluminum_nitride_gallium_nitride_0001_heterostructure":
        return _material_alias_present(text, "AlN") and _material_alias_present(text, "GaN")
    if template_id == "indium_gallium_nitride_gallium_nitride_0001_heterostructure":
        return (_material_alias_present(text, "InGaN") or _match_ingan_formula_alloy(text) is not None) and _material_alias_present(text, "GaN")
    if template_id == "indium_gallium_arsenide_indium_phosphide_001_heterostructure":
        return (_material_alias_present(text, "InGaAs") or _match_ingaas_formula_alloy(text) is not None) and _material_alias_present(text, "InP")
    if template_id == "indium_arsenide_gallium_antimonide_001_heterostructure":
        return _material_alias_present(text, "InAs") and _material_alias_present(text, "GaSb")
    return False


def _material_alias_present(text: str, material: str) -> bool:
    for alias in _material_text_aliases(material):
        flags = 0 if _case_sensitive_material_formula(alias) else re.IGNORECASE
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, flags=flags):
            return True
    return False


def _silicon_pn_junction_template_context_ok(text: str) -> bool:
    """Return whether a p-n junction request can safely use the silicon template."""

    if not _looks_like_semiconductor_pn_junction_text(text):
        return True
    if _text_mentions_non_silicon_pn_host(text):
        return False
    return _text_mentions_silicon(text)


def _text_mentions_non_silicon_pn_host(text: str) -> bool:
    """Return True when a p-n request names a non-silicon semiconductor host."""

    return _text_mentions_non_silicon_semiconductor_material(text)


def _text_mentions_non_silicon_semiconductor_material(text: str) -> bool:
    """Return True when a request names a semiconductor not covered by the Si contact template."""

    if re.search(r"\bgermanium\b", text, flags=re.IGNORECASE):
        return True
    for alias in NON_SILICON_SEMICONDUCTOR_ALIASES:
        if alias == "GaP":
            if re.search(r"(?<![A-Za-z0-9])GaP(?![A-Za-z0-9])", text) or re.search(
                r"\bgallium\s+phosphide\b",
                text,
                flags=re.IGNORECASE,
            ):
                return True
            continue
        if _material_alias_present(text, alias):
            return True
    if any(term in text for term in CJK_NON_SILICON_SEMICONDUCTOR_TERMS):
        return True
    return False


def _looks_like_metal_semiconductor_contact_text(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:schottky|metal[-\s]?semiconductor|contact\s+barrier|ohmic\s+contact|schottky\s+diode)\b",
            text,
            flags=re.IGNORECASE,
        )
        or any(
            term in text
            for term in (
                "\u8096\u7279\u57fa",
                "\u91d1\u5c5e\u534a\u5bfc\u4f53\u63a5\u89e6",
                "\u91d1\u5c5e-\u534a\u5bfc\u4f53\u63a5\u89e6",
                "\u91d1\u5c5e/\u534a\u5bfc\u4f53\u63a5\u89e6",
                "\u91d1\u534a\u63a5\u89e6",
                "\u63a5\u89e6\u52bf\u5792",
            )
        )
    )


def _mentions_sic_6h(text: str) -> bool:
    """Return whether a request explicitly names the 6H-SiC polytype."""

    return bool(
        re.search(
            r"(?<![A-Za-z0-9])6h[-\s]*(?:sic|silicon[-\s]+carbide|\u78b3\u5316\u7845)(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"(?:sic|silicon[-\s]+carbide|\u78b3\u5316\u7845)[-\s]*6h(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
    )


def _infer_unsupported_sic_6h_derived_structure_request(text: str) -> NaturalLanguagePlan | None:
    """Reject 6H-SiC geometries outside the reviewed bulk, Si-face slab, and contact set."""

    if not _mentions_sic_6h(text):
        return None
    english_derived_geometry = bool(
        re.search(
            r"\b(?:slab|interface|contact|schottky|mos(?:\s+capacitor)?|gate[-\s]+(?:stack|oxide)|heterostructure)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\bsurface\s+(?:structure|model|cell|slab)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(?:build|create|construct|generate)\b.{0,96}\bsurface\b"
            r"(?![-\s]+(?:normal|view|projection|parameter|diagnostic))",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\(\s*0001\s*\).{0,24}\bsurface\b(?![-\s]+(?:normal|view|projection|parameter))",
            text,
            flags=re.IGNORECASE,
        )
    )
    cjk_surface_geometry = bool(
        re.search(
            r"\u8868\u9762(?!\u6cd5\u5411|\u89c6\u56fe|\u89c6\u89d2|\u6295\u5f71|\u53c2\u6570|\u8bca\u65ad)",
            text,
        )
    )
    cjk_derived_geometry = cjk_surface_geometry or any(
        term in text
        for term in (
            "\u8868\u9762\u6a21\u578b",
            "\u8868\u9762\u7ed3\u6784",
            "\u8868\u9762slab",
            "\u754c\u9762",
            "\u63a5\u89e6",
            "\u8096\u7279\u57fa",
            "mos\u7535\u5bb9",
            "\u6805\u5806",
            "\u5f02\u8d28\u7ed3",
        )
    )
    if not english_derived_geometry and not cjk_derived_geometry:
        return None
    return NaturalLanguagePlan(
        kind="unsupported",
        payload=None,
        confidence=0.0,
        template_id=None,
        notes=[
            "A 6H-SiC derived-geometry request was recognized outside the reviewed local template set.",
            "Reviewed 6H-SiC starts cover P63mc bulk, the (0001) Si-face six-bilayer slab, and metal contacts on that Si face.",
            "No 3C-SiC, 4H-SiC, or silicon substitute was selected.",
            "Provide a reviewed ModelSpec for other 6H-SiC surfaces, MOS stacks, interfaces, or device geometries before live loading.",
        ],
    )


def _infer_unsupported_metal_semiconductor_contact_request(text: str) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _text_mentions_non_silicon_semiconductor_material(text):
        return None
    return NaturalLanguagePlan(
        kind="unsupported",
        payload=None,
        confidence=0.0,
        template_id=None,
        notes=[
            "A metal/semiconductor contact request named a non-silicon semiconductor host.",
            "The local deterministic Schottky contact templates currently cover Si(100), GaAs(001), GaN(0001), ZnO(0001), beta-Ga2O3(010), 4H-SiC(0001) Si-face, InP(001), InAs(001), AlAs(001), GaP(001), GaSb(001), AlP(001), AlSb(001), InSb(001), CdTe(001), ZnS(001), ZnSe(001), ZnTe(001), CdS(001), and CdSe(001) scaffold geometry; other hosts need a reviewed structure.",
            "Provide a reviewed ModelSpec or SemanticPatch for this material-specific contact before live loading or execution.",
        ],
    )


def _infer_gaas_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _material_alias_present(text, "GaAs") and "gallium arsenide" not in text:
        return None

    metal = _match_gaas_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The GaAs Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _gaas_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic pre-relaxation metal/GaAs(001) Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact geometry diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_gaas_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    gaas = r"(?:gaas|gallium\s+arsenide|\u7837\u5316\u9553)"
    patterns = [
        rf"\b{metal}\s*/\s*{gaas}\b",
        rf"\b{metal}\s*[- ]\s*{gaas}\b",
        rf"\b{gaas}\s*/\s*{metal}\b",
        rf"\b{gaas}\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+{gaas}\b",
        rf"\b{metal}\s+{gaas}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{gaas}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?{gaas}\b",
        rf"{metal}\s*/\s*(?:GaAs|gaas|\u7837\u5316\u9553)",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:GaAs|gaas|\u7837\u5316\u9553)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _gaas_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    cell_c = 32.0
    lattice_a = 5.6533
    interface_gap = 2.5
    metal_thickness = 2.56
    gaas_layer_z = [0.08, 0.17, 0.26, 0.35]
    metal_start_z = gaas_layer_z[-1] + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    in_plane_pairs = [
        (0.0, 0.0, 0.25, 0.25),
        (0.5, 0.5, 0.75, 0.75),
    ]
    atoms: list[BasisAtomSpec] = []
    for layer_index, z_value in enumerate(gaas_layer_z, start=1):
        for pair_index, (ga_x, ga_y, as_x, as_y) in enumerate(in_plane_pairs, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"GaAsGa{layer_index}_{pair_index}",
                    element="Ga",
                    fractional=[ga_x, ga_y, z_value],
                )
            )
            atoms.append(
                BasisAtomSpec(
                    id=f"GaAsAs{layer_index}_{pair_index}",
                    element="As",
                    fractional=[as_x, as_y, z_value],
                )
            )
    metal_positions = [(0.0, 0.0), (0.5, 0.5), (0.25, 0.25), (0.75, 0.75)]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(metal_positions, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_positions) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_thickness = (gaas_layer_z[-1] - gaas_layer_z[0]) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "zinc blende GaAs metal semiconductor schottky contact scaffold",
        "material": f"{metal}/GaAs",
        "materials": ["GaAs", metal],
        "stack_sequence": ["GaAs", metal],
        "interface": f"{metal}/GaAs",
        "interface_orientation": f"{metal} contact / GaAs(001)",
        "interface_axis": "c",
        "substrate": "GaAs",
        "surface_axis": "c",
        "surface_orientation": "GaAs(001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": lattice_a,
        "gaas_reference_lattice_angstrom": lattice_a,
        "coherent_strain_model": "matched_to_gaas_001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "GaAs",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": GAAS_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": GAAS_BAND_GAP_EV,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            "Ga": "GaAs",
            "As": "GaAs",
            "As;Ga": "GaAs",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "nl_template": GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "gaas_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic matched-cell scaffold for live visualization and diagnostics.",
            "Relax the interface and review termination/stoichiometry before quantitative Schottky or device conclusions.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=GAAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(a=lattice_a, b=lattice_a, c=cell_c, alpha=90.0, beta=90.0, gamma=90.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 520,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    "Metal/GaAs(001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This is an unrelaxed deterministic interface scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_gan_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _material_alias_present(text, "GaN") and "gallium nitride" not in text and "\u6c2e\u5316\u9553" not in text:
        return None

    metal = _match_gan_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The GaN Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _gan_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic pre-relaxation metal/GaN(0001) Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact geometry diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_gan_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    gan = r"(?:gan|gallium\s+nitride|\u6c2e\u5316\u9553)"
    patterns = [
        rf"\b{metal}\s*/\s*{gan}\b",
        rf"\b{metal}\s*[- ]\s*{gan}\b",
        rf"\b{gan}\s*/\s*{metal}\b",
        rf"\b{gan}\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+{gan}\b",
        rf"\b{metal}\s+{gan}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{gan}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?{gan}\b",
        rf"{metal}\s*/\s*(?:GaN|gan|\u6c2e\u5316\u9553)",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:GaN|gan|\u6c2e\u5316\u9553)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _gan_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    cell_c = 32.0
    lattice_a = 3.189
    interface_gap = 2.4
    metal_thickness = 2.56
    gan_layer_z = [0.08, 0.16, 0.24, 0.32]
    metal_start_z = gan_layer_z[-1] + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    in_plane_pairs = [
        (0.333333, 0.666667, 0.333333, 0.666667),
        (0.666667, 0.333333, 0.666667, 0.333333),
    ]
    atoms: list[BasisAtomSpec] = []
    for layer_index, z_value in enumerate(gan_layer_z, start=1):
        for pair_index, (ga_x, ga_y, n_x, n_y) in enumerate(in_plane_pairs, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"GaNGa{layer_index}_{pair_index}",
                    element="Ga",
                    fractional=[ga_x, ga_y, z_value],
                )
            )
            atoms.append(
                BasisAtomSpec(
                    id=f"GaNN{layer_index}_{pair_index}",
                    element="N",
                    fractional=[n_x, n_y, z_value],
                )
            )
    metal_positions = [(0.0, 0.0), (0.5, 0.5), (0.333333, 0.666667), (0.666667, 0.333333)]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(metal_positions, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_positions) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_thickness = (gan_layer_z[-1] - gan_layer_z[0]) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "wurtzite GaN metal semiconductor schottky contact scaffold",
        "material": f"{metal}/GaN",
        "materials": ["GaN", metal],
        "stack_sequence": ["GaN", metal],
        "interface": f"{metal}/GaN",
        "interface_orientation": f"{metal} contact / GaN(0001)",
        "interface_axis": "c",
        "substrate": "GaN",
        "surface_axis": "c",
        "surface_orientation": "GaN(0001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": lattice_a,
        "gan_reference_lattice_angstrom": lattice_a,
        "coherent_strain_model": "matched_to_gan_0001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "GaN",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": GAN_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": GAN_BAND_GAP_EV,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            "Ga": "GaN",
            "N": "GaN",
            "Ga;N": "GaN",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "surface_model": "GaN(0001) metal contact scaffold",
        "termination": "mixed_pre_relaxation_scaffold",
        "nl_template": GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "gan_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic matched-cell scaffold for live visualization and diagnostics.",
            "Relax the interface and review Ga/N termination before quantitative Schottky or device conclusions.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=GAN_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(a=lattice_a, b=lattice_a, c=cell_c, alpha=90.0, beta=90.0, gamma=120.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    "Metal/GaN(0001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This is an unrelaxed deterministic interface scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_zno_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not (
        _material_alias_present(text, "ZnO")
        or "zinc oxide" in text.lower()
        or "\u6c27\u5316\u950c" in text
    ):
        return None

    metal = _match_zno_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The ZnO Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _zno_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic oxygen-terminated metal/ZnO(0001) pre-relaxation Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact and surface-polarity diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_zno_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    zno = r"(?:zno|zinc\s+oxide|\u6c27\u5316\u950c)"
    patterns = [
        rf"\b{metal}\s*/\s*{zno}\b",
        rf"\b{metal}\s*[- ]\s*{zno}\b",
        rf"\b{zno}\s*/\s*{metal}\b",
        rf"\b{zno}\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+{zno}\b",
        rf"\b{metal}\s+{zno}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{zno}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?{zno}\b",
        rf"{metal}\s*/\s*(?:ZnO|zno|\u6c27\u5316\u950c)",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:ZnO|zno|\u6c27\u5316\u950c)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _zno_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    cell_c = 32.0
    lattice_a = 2.0 * ZNO_REFERENCE_LATTICE_A_ANGSTROM
    interface_gap = 2.4
    metal_thickness = 2.56
    base_z = 0.05
    repeat_fraction = ZNO_REFERENCE_LATTICE_C_ANGSTROM / cell_c
    oxygen_offset_fraction = ZNO_INTERNAL_PARAMETER_U * repeat_fraction
    half_repeat_fraction = 0.5 * repeat_fraction
    sublattice_a = [
        (0.166666, 0.333334),
        (0.166666, 0.833333),
        (0.666667, 0.333334),
        (0.666667, 0.833333),
    ]
    sublattice_b = [
        (0.333334, 0.166666),
        (0.333334, 0.666667),
        (0.833333, 0.166666),
        (0.833333, 0.666667),
    ]
    atoms: list[BasisAtomSpec] = []
    semiconductor_plane_z: list[float] = []
    for repeat_index in range(2):
        repeat_base_z = base_z + repeat_index * repeat_fraction
        planes = [
            ("Zn", "ZnA", sublattice_a, repeat_base_z),
            ("O", "OA", sublattice_a, repeat_base_z + oxygen_offset_fraction),
            ("Zn", "ZnB", sublattice_b, repeat_base_z + half_repeat_fraction),
            ("O", "OB", sublattice_b, repeat_base_z + half_repeat_fraction + oxygen_offset_fraction),
        ]
        for element, plane_label, positions, z_value in planes:
            semiconductor_plane_z.append(z_value)
            for site_index, (x_value, y_value) in enumerate(positions, start=1):
                atoms.append(
                    BasisAtomSpec(
                        id=f"ZnO{plane_label}{repeat_index + 1}_{site_index}",
                        element=element,
                        fractional=[x_value, y_value, _round_fractional(z_value)],
                    )
                )

    top_semiconductor_z = max(semiconductor_plane_z)
    metal_start_z = top_semiconductor_z + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(sublattice_b, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(sublattice_b) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_thickness = (top_semiconductor_z - min(semiconductor_plane_z)) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "wurtzite ZnO metal semiconductor schottky contact scaffold",
        "material": f"{metal}/ZnO",
        "materials": ["ZnO", metal],
        "stack_sequence": ["ZnO", metal],
        "interface": f"{metal}/ZnO",
        "interface_orientation": f"{metal} contact / ZnO(0001)",
        "interface_axis": "c",
        "substrate": "ZnO",
        "surface_axis": "c",
        "surface_orientation": "ZnO(0001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": lattice_a,
        "reference_lattice_angstrom": {
            "a": ZNO_REFERENCE_LATTICE_A_ANGSTROM,
            "c": ZNO_REFERENCE_LATTICE_C_ANGSTROM,
        },
        "internal_parameter_u": ZNO_INTERNAL_PARAMETER_U,
        "template_supercell": [2, 2, 2],
        "coherent_strain_model": "matched_to_zno_0001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "ZnO",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": ZNO_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": ZNO_BAND_GAP_EV,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            "Zn": "ZnO",
            "O": "ZnO",
            "O;Zn": "ZnO",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "surface_model": "ZnO(0001) oxygen-terminated metal contact scaffold",
        "termination": "oxygen_terminated_pre_relaxation_scaffold",
        "contact_registry": "metal_on_top_of_oxygen_terminated_zno_0001",
        "nl_template": ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "zno_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic matched-cell scaffold for live visualization and diagnostics.",
            "Relax the interface and review polar Zn/O termination before quantitative Schottky or device conclusions.",
            "Electronic affinity and band-gap values are metadata-only literature screening references, not calculated results.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=ZNO_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(a=lattice_a, b=lattice_a, c=cell_c, alpha=90.0, beta=90.0, gamma=120.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 12,
                "require_convergence": False,
                "notes": [
                    "Metal/ZnO(0001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This oxygen-terminated polar interface is an unrelaxed deterministic scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_beta_ga2o3_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)
    if not (
        "ga2o3" in compact
        or "gallium oxide" in lowered
        or "beta gallium oxide" in lowered
        or "\u6c27\u5316\u9553" in text
    ):
        return None

    metal = _match_beta_ga2o3_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The beta-Ga2O3 Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _beta_ga2o3_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic centered metal/beta-Ga2O3(010) pre-relaxation Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact, surface-asymmetry, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_beta_ga2o3_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    host = (
        r"(?:beta[-\s]*ga2o3|\u03b2[-\s]*ga2o3|ga2o3|beta[-\s]+gallium\s+oxide|"
        r"gallium\s+oxide|\u03b2[-\s]*\u6c27\u5316\u9553|\u6c27\u5316\u9553)"
    )
    patterns = [
        rf"(?<![A-Za-z0-9]){metal}\s*/\s*{host}",
        rf"(?<![A-Za-z0-9]){metal}\s*[- ]\s*{host}",
        rf"{host}\s*/\s*{metal}(?![A-Za-z0-9])",
        rf"{host}\s*[- ]\s*{metal}(?![A-Za-z0-9])",
        rf"(?<![A-Za-z0-9]){metal}\s+(?:on|over)\s+{host}",
        rf"(?<![A-Za-z0-9]){metal}\s+{host}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"{host}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?{host}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _beta_ga2o3_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    source_spec = ModelSpec.model_validate(_load_example("beta_gallium_oxide_010_slab_spec.json"))
    if not isinstance(source_spec.model, CrystalSpec):
        raise ValueError("beta-Ga2O3(010) contact source must be a crystal spec")

    interface_gap = _match_contact_length_value(
        user_request,
        [
            r"(?:interface|contact)\s+(?:gap|spacing|distance)",
            r"(?:metal[-\s]?semiconductor|schottky)\s+(?:gap|spacing|distance)",
            r"(?:\u754c\u9762|\u63a5\u89e6)\s*(?:\u95f4\u8ddd|\u8ddd\u79bb|\u7a7a\u9699)",
        ],
    ) or 2.4
    metal_thickness = _match_contact_length_value(
        user_request,
        [
            r"(?:metal\s+)?(?:contact|electrode|metal)\s+(?:layer\s+)?thickness",
            r"(?:schottky|metal[-\s]?semiconductor)\s+(?:metal\s+)?(?:contact\s+)?thickness",
            r"(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u91d1\u5c5e\u5c42|\u7535\u6781)\s*(?:\u5c42)?\s*\u539a\u5ea6",
        ],
    ) or 2.56
    source_model = source_spec.model
    source_axis_length = float(source_model.lattice.b)
    cell_b = BETA_GA2O3_CONTACT_CELL_B_ANGSTROM
    atoms: list[BasisAtomSpec] = []
    for atom in source_model.basis_atoms:
        x_value, y_value, z_value = _basis_atom_fractional_tuple(atom)
        y_value = y_value * source_axis_length / cell_b
        atoms.append(
            BasisAtomSpec(
                id=atom.id,
                element=atom.element,
                fractional=[x_value, y_value, z_value],
            )
        )

    semiconductor_axis_values = [_basis_atom_fractional_tuple(atom)[1] for atom in atoms]
    semiconductor_min = min(semiconductor_axis_values)
    semiconductor_top = max(semiconductor_axis_values)
    metal_start = semiconductor_top + float(interface_gap) / cell_b
    metal_layer_positions = [metal_start, metal_start + float(metal_thickness) / cell_b]
    metal_grid = [(x_value, z_value) for x_value in (0.125, 0.375, 0.625, 0.875) for z_value in (0.25, 0.75)]
    for layer_index, axis_value in enumerate(metal_layer_positions, start=1):
        for site_index, (x_value, z_value) in enumerate(metal_grid, start=1):
            if layer_index == 2:
                x_value = (x_value + 0.125) % 1.0
                z_value = (z_value + 0.25) % 1.0
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_grid) + site_index}",
                    element=metal,
                    fractional=[x_value, axis_value, z_value],
                )
            )

    all_axis_values = [_basis_atom_fractional_tuple(atom)[1] for atom in atoms]
    center_shift = 0.5 - (min(all_axis_values) + max(all_axis_values)) / 2.0
    centered_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        x_value, y_value, z_value = _basis_atom_fractional_tuple(atom)
        centered_atoms.append(
            BasisAtomSpec(
                id=atom.id,
                element=atom.element,
                fractional=[x_value, round(y_value + center_shift, 8), z_value],
            )
        )

    centered_axis_values = [_basis_atom_fractional_tuple(atom)[1] for atom in centered_atoms]
    assembly_extent = (max(centered_axis_values) - min(centered_axis_values)) * cell_b
    semiconductor_thickness = (semiconductor_top - semiconductor_min) * cell_b
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    source_metadata = dict(source_spec.metadata or {})
    source_metadata.pop("slab_thickness_angstrom", None)
    source_metadata.pop("vacuum_angstrom", None)
    metadata = {
        **source_metadata,
        "source": "local_dynamic_template",
        "source_reference": source_metadata.get("source_reference"),
        "domain": "semiconductor",
        "structure_family": "monoclinic beta-Ga2O3 metal semiconductor schottky contact scaffold",
        "material": f"{metal}/beta-Ga2O3",
        "materials": ["beta-Ga2O3", metal],
        "stack_sequence": ["beta-Ga2O3", metal],
        "interface": f"{metal}/beta-Ga2O3",
        "interface_orientation": f"{metal} contact / beta-Ga2O3(010)",
        "interface_axis": "b",
        "substrate": "beta-Ga2O3",
        "surface_axis": "b",
        "surface_orientation": "beta-Ga2O3(010)",
        "slab_centering": {
            "axis": "b",
            "shift_fractional": round(center_shift, 8),
            "source": "dynamic_contact_assembly_centering",
        },
        "contact_cell_axis_length_angstrom": cell_b,
        "contact_assembly_extent_angstrom": round(assembly_extent, 6),
        "contact_total_vacuum_angstrom": round(cell_b - assembly_extent, 6),
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "beta-Ga2O3",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "beta-Ga2O3_device_model_screening_values",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": BETA_GA2O3_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": BETA_GA2O3_BAND_GAP_EV,
        "electronic_screening_reference": {
            "usage": "metadata_only_not_calculated",
            "reference": "Chinese Physics B 30 (2021) 027301, Ga2O3 device-model parameter table",
            "electron_affinity_ev": BETA_GA2O3_ELECTRON_AFFINITY_EV,
            "band_gap_ev": BETA_GA2O3_BAND_GAP_EV,
        },
        "interface_gap_angstrom": round(float(interface_gap), 6),
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": round(float(metal_thickness), 6),
        "material_marker_map": {
            "Ga": "beta-Ga2O3",
            "O": "beta-Ga2O3",
            "Ga;O": "beta-Ga2O3",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "mixed_layers_expected": True,
        "mixed_layers_expected_reason": "beta-Ga2O3(010)_planes_contain_stoichiometric_Ga_and_O",
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "surface_model": "beta-Ga2O3(010) metal contact scaffold",
        "termination": "metal_contact_pre_relaxation_scaffold",
        "surface_asymmetry_expected": True,
        "surface_asymmetry_expected_reason": "single_sided_metal_contact_on_beta-Ga2O3_010_slab",
        "contact_registry": "two_layer_metal_grid_on_beta_ga2o3_010",
        "base_template_id": "beta_gallium_oxide_010_slab",
        "nl_template": BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "beta_ga2o3_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic centered beta-Ga2O3(010) contact scaffold for live visualization and diagnostics.",
            "Relax the interface and review the asymmetric metal/slab surfaces before quantitative Schottky or device conclusions.",
            "Electron-affinity and band-gap values are metadata-only device-model screening references, not calculated results.",
        ],
    }
    lattice = source_model.lattice
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=BETA_GA2O3_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(
                    a=lattice.a,
                    b=cell_b,
                    c=lattice.c,
                    alpha=lattice.alpha,
                    beta=lattice.beta,
                    gamma=lattice.gamma,
                ),
                basis_atoms=centered_atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 14,
                "require_convergence": False,
                "notes": [
                    "Metal/beta-Ga2O3(010) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This centered asymmetric interface is an unrelaxed scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _sic_6h_c_face_requested(text: str) -> bool:
    return bool(
        re.search(r"\(\s*0\s*0\s*0\s*[-\u2212]\s*1\s*\)", text)
        or re.search(r"\b000[-\u2212]1\b", text)
        or re.search(r"\b(?:c[-\s]?face|carbon[-\s]+terminated)\b", text, flags=re.IGNORECASE)
        or any(term in text for term in ("\u78b3\u9762", "\u78b3\u7ec8\u6b62"))
    )


def _sic_6h_si_face_requested(text: str) -> bool:
    return bool(
        re.search(r"\(\s*0\s*0\s*0\s*1\s*\)", text)
        or re.search(r"\b(?:si[-\s]?face|silicon[-\s]+terminated)\b", text, flags=re.IGNORECASE)
        or any(term in text for term in ("\u7845\u9762", "\u7845\u7ec8\u6b62"))
    )


def _looks_like_sic_6h_surface_geometry_request(text: str) -> bool:
    if not _mentions_sic_6h(text) or _looks_like_metal_semiconductor_contact_text(text):
        return False
    return bool(
        re.search(r"\bslab\b", text, flags=re.IGNORECASE)
        or re.search(
            r"\bsurface\s+(?:structure|model|cell|slab)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(?:build|create|construct|generate)\b.{0,96}\bsurface\b"
            r"(?![-\s]+(?:normal|view|projection|parameter|diagnostic))",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\(\s*0001\s*\).{0,24}\bsurface\b(?![-\s]+(?:normal|view|projection|parameter))",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\u8868\u9762(?!\u6cd5\u5411|\u89c6\u56fe|\u89c6\u89d2|\u6295\u5f71|\u53c2\u6570|\u8bca\u65ad)",
            text,
        )
    )


def _infer_sic_6h_si_face_slab_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_sic_6h_surface_geometry_request(text):
        return None
    if _sic_6h_c_face_requested(text):
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                "The reviewed 6H-SiC surface constructor covers only the Si-terminated (0001) face.",
                "A C-terminated 6H-SiC(000-1) slab was not substituted with the Si-face scaffold.",
                "Provide a reviewed C-face ModelSpec before preview or live loading.",
            ],
        )
    if not _sic_6h_si_face_requested(text):
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                "A 6H-SiC surface request was recognized, but its orientation and termination are ambiguous.",
                "The reviewed local surface scaffold is specifically 6H-SiC(0001) Si-face.",
                "Request the (0001) Si face explicitly; no 3C-SiC, 4H-SiC, or silicon substitute was selected.",
            ],
        )

    chosen_project_id = project_id or _project_id(SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _sic_6h_si_face_slab_spec(
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic centered 2x2 six-bilayer 6H-SiC(0001) Si-face slab.",
        "The C-terminated back surface is hydrogen-saturated; the Si-face remains unreconstructed for reviewed relaxation.",
    ]
    confidence = 0.9
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic surface patch operations during planning: " + ", ".join(diff) + ".")
        confidence = 0.86

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _infer_sic_6h_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text) or not _mentions_sic_6h(text):
        return None
    if _sic_6h_c_face_requested(text):
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                "The reviewed 6H-SiC Schottky scaffold covers only the Si-terminated (0001) face.",
                "A C-terminated 6H-SiC(000-1) contact was not substituted with the Si-face scaffold.",
                "Provide a reviewed C-face ModelSpec before preview or live loading.",
            ],
        )

    metal = _match_sic_6h_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The 6H-SiC Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _sic_6h_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic centered metal/6H-SiC(0001) Si-face pre-relaxation Schottky contact scaffold.",
        "The six-bilayer 2x2 slab has a hydrogen-saturated C back face and is intended for visualization and preflight before relaxation.",
    ]
    confidence = 0.89
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during planning: " + ", ".join(diff) + ".")
        confidence = 0.85

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_sic_6h_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    host = (
        r"(?:6h[-\s]*(?:sic|silicon[-\s]+carbide|\u78b3\u5316\u7845)|"
        r"(?:sic|silicon[-\s]+carbide|\u78b3\u5316\u7845)[-\s]*6h)"
    )
    patterns = [
        rf"(?<![A-Za-z0-9]){metal}\s*/\s*{host}",
        rf"(?<![A-Za-z0-9]){metal}\s*[- ]\s*{host}",
        rf"{host}\s*/\s*{metal}(?![A-Za-z0-9])",
        rf"{host}\s*[- ]\s*{metal}(?![A-Za-z0-9])",
        rf"(?<![A-Za-z0-9]){metal}\s+(?:on|over)\s+{host}",
        rf"(?<![A-Za-z0-9]){metal}\s+{host}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"{host}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?{host}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _sic_6h_si_face_assembly(*, cell_c: float) -> Sic6hSiFaceAssembly:
    source_spec = ModelSpec.model_validate(_load_example("silicon_carbide_6h_hexagonal_spec.json"))
    if not isinstance(source_spec.model, CrystalSpec):
        raise ValueError("6H-SiC surface source must be a crystal spec")

    source_model = source_spec.model
    source_c = float(source_model.lattice.c)
    source_positions: list[tuple[BasisAtomSpec, float]] = []
    for source_atom in source_model.basis_atoms:
        _, _, source_z = _basis_atom_fractional_tuple(source_atom)
        # Reverse the bulk c ordering so the retained slab has a C back face and
        # a top Si face, with three bulk bonds retained at each surface atom.
        wrapped_z = (SIC_6H_SI_FACE_CUT_ORIGIN - source_z) % 1.0
        if abs(wrapped_z - 1.0) < 1e-10:
            wrapped_z = 0.0
        source_positions.append((source_atom, wrapped_z))

    lattice_a = float(source_model.lattice.a) * 2.0
    lattice_b = float(source_model.lattice.b) * 2.0
    back_surface_offset = SIC_6H_BACK_SURFACE_H_BOND_ANGSTROM / cell_c
    semiconductor_atoms: list[BasisAtomSpec] = []
    for repeat_a in range(2):
        for repeat_b in range(2):
            for source_atom, wrapped_z in source_positions:
                source_x, source_y, _ = _basis_atom_fractional_tuple(source_atom)
                semiconductor_atoms.append(
                    BasisAtomSpec(
                        id=f"{source_atom.id}_{repeat_a}{repeat_b}",
                        element=source_atom.element,
                        fractional=[
                            _round_fractional((source_x + repeat_a) / 2.0),
                            _round_fractional((source_y + repeat_b) / 2.0),
                            _round_fractional(wrapped_z * source_c / cell_c + back_surface_offset),
                        ],
                    )
                )

    semiconductor_z = [_basis_atom_fractional_tuple(atom)[2] for atom in semiconductor_atoms]
    semiconductor_bottom = min(semiconductor_z)
    semiconductor_top = max(semiconductor_z)
    bottom_atoms = [
        atom
        for atom in semiconductor_atoms
        if abs(_basis_atom_fractional_tuple(atom)[2] - semiconductor_bottom) < 1e-7
    ]
    top_atoms = [
        atom
        for atom in semiconductor_atoms
        if abs(_basis_atom_fractional_tuple(atom)[2] - semiconductor_top) < 1e-7
    ]
    if {atom.element for atom in bottom_atoms} != {"C"} or len(bottom_atoms) != 4:
        raise ValueError("6H-SiC Si-face cut did not produce the expected four-atom C bottom layer")
    if {atom.element for atom in top_atoms} != {"Si"} or len(top_atoms) != 4:
        raise ValueError("6H-SiC Si-face cut did not produce the expected four-atom Si top layer")

    layer_elements: list[str] = []
    for layer_z in sorted({round(value, 8) for value in semiconductor_z}):
        elements = {
            atom.element
            for atom in semiconductor_atoms
            if abs(_basis_atom_fractional_tuple(atom)[2] - layer_z) < 1e-7
        }
        if len(elements) != 1:
            raise ValueError("6H-SiC surface assembly produced a mixed-element bilayer plane")
        layer_elements.append(next(iter(elements)))
    if layer_elements != ["C", "Si"] * 6:
        raise ValueError("6H-SiC surface assembly did not preserve six C-Si bilayers")

    atoms = list(semiconductor_atoms)
    back_hydrogen_z = semiconductor_bottom - SIC_6H_BACK_SURFACE_H_BOND_ANGSTROM / cell_c
    for index, bottom_atom in enumerate(sorted(bottom_atoms, key=lambda atom: atom.id), start=1):
        x_value, y_value, _ = _basis_atom_fractional_tuple(bottom_atom)
        atoms.append(
            BasisAtomSpec(
                id=f"HBack{index}",
                element="H",
                fractional=[x_value, y_value, _round_fractional(back_hydrogen_z)],
            )
        )

    top_registry = tuple(
        sorted((_basis_atom_fractional_tuple(atom)[0], _basis_atom_fractional_tuple(atom)[1]) for atom in top_atoms)
    )
    return Sic6hSiFaceAssembly(
        source_spec=source_spec,
        source_model=source_model,
        cell_c=cell_c,
        lattice_a=lattice_a,
        lattice_b=lattice_b,
        semiconductor_atoms=tuple(semiconductor_atoms),
        atoms=tuple(atoms),
        top_registry=top_registry,
        semiconductor_bottom_fractional=semiconductor_bottom,
        semiconductor_top_fractional=semiconductor_top,
        semiconductor_thickness_angstrom=(semiconductor_top - semiconductor_bottom) * cell_c,
    )


def _center_sic_6h_atoms(
    atoms: Sequence[BasisAtomSpec],
    *,
    cell_c: float,
) -> tuple[list[BasisAtomSpec], float, float]:
    if not atoms:
        raise ValueError("Cannot center an empty 6H-SiC assembly")
    all_z = [_basis_atom_fractional_tuple(atom)[2] for atom in atoms]
    center_shift = 0.5 - (min(all_z) + max(all_z)) / 2.0
    centered_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        x_value, y_value, z_value = _basis_atom_fractional_tuple(atom)
        centered_z = round(z_value + center_shift, 8)
        if centered_z < 0.0 or centered_z > 1.0:
            raise ValueError("6H-SiC centered assembly exceeds the c-axis cell")
        centered_atoms.append(
            BasisAtomSpec(
                id=atom.id,
                element=atom.element,
                fractional=[x_value, y_value, centered_z],
            )
        )
    centered_z_values = [_basis_atom_fractional_tuple(atom)[2] for atom in centered_atoms]
    assembly_extent = (max(centered_z_values) - min(centered_z_values)) * cell_c
    return centered_atoms, center_shift, assembly_extent


def _sic_6h_common_surface_metadata(
    assembly: Sic6hSiFaceAssembly,
    *,
    center_shift: float,
    assembly_extent: float,
) -> dict[str, Any]:
    source_metadata = dict(assembly.source_spec.metadata or {})
    return {
        **source_metadata,
        "source": "local_dynamic_template_from_reviewed_6H-SiC_bulk",
        "domain": "semiconductor",
        "material": "6H-SiC",
        "polytype": "6H",
        "space_group": "P63mc",
        "space_group_number": 186,
        "surface_axis": "c",
        "surface_normal_cell_axis": "c",
        "surface_orientation": "6H-SiC(0001) Si-face",
        "surface_orientation_basis": "parent_bulk_mapped_to_cell_axis",
        "surface_axis_reoriented": True,
        "surface_axis_reorientation": "bulk_fractional_z_reflected_to_place_6H-SiC_0001_Si_face_at_top",
        "surface_face": "Si-face",
        "surface_context": True,
        "surface_model": "6H-SiC(0001) silicon-terminated six-bilayer slab scaffold",
        "surface_cell_axis_length_angstrom": assembly.cell_c,
        "slab_centering": {
            "axis": "c",
            "shift_fractional": round(center_shift, 8),
            "source": "dynamic_6H-SiC_surface_assembly_centering",
        },
        "slab_thickness_angstrom": round(assembly_extent, 6),
        "semiconductor_slab_thickness_angstrom": round(assembly.semiconductor_thickness_angstrom, 6),
        "vacuum_angstrom": round(assembly.cell_c - assembly_extent, 6),
        "reference_lattice_angstrom": {
            "a": float(assembly.source_model.lattice.a),
            "c": float(assembly.source_model.lattice.c),
        },
        "template_supercell": [2, 2, 1],
        "sic_bilayer_count": 6,
        "back_surface_hydrogen_count": 4,
        "back_surface_hydrogen_bond_angstrom": SIC_6H_BACK_SURFACE_H_BOND_ANGSTROM,
        "termination": "silicon_terminated_top_carbon_terminated_hydrogen_passivated_bottom",
        "bottom_termination": "carbon_terminated_hydrogen_passivated",
        "top_semiconductor_termination": "silicon_terminated",
        "passivation": {
            "surfaces": ["bottom"],
            "element": "H",
            "added_atom_count": 4,
            "bond_length_angstrom": SIC_6H_BACK_SURFACE_H_BOND_ANGSTROM,
            "full_passivation_requested": False,
            "source": "Tanaka_et_al_2006_back_surface_model",
        },
        "polar_surface": True,
        "surface_asymmetry_expected": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_surface": True,
        "unreconstructed_surface": True,
        "requires_geometry_relaxation": True,
        "surface_reconstruction_review_required": True,
        "material_marker_map": {
            "C": "6H-SiC",
            "Si": "6H-SiC",
            "C;Si": "6H-SiC",
            "Si;C": "6H-SiC",
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "semiconductor_electron_affinity_ev": SIC_6H_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": SIC_6H_BAND_GAP_EV,
        "electronic_screening_reference": {
            "usage": "metadata_only_not_calculated",
            "reference": "Li et al., Photoelectric Properties of Si Doping Superlattice Structure on 6H-SiC(0001)",
            "doi": "10.3390/ma10060583",
            "electron_affinity_ev": SIC_6H_ELECTRON_AFFINITY_EV,
            "band_gap_ev": SIC_6H_BAND_GAP_EV,
            "scope": "TCAD device-model input; not a reconstructed-surface electron-affinity calculation",
        },
        "bulk_structure_reference": {
            "reference": "Capitani et al., The 6H-SiC structure model",
            "doi": "10.2138/am.2007.2346",
            "url": "https://rruff.geo.arizona.edu/doclib/am/vol92/AM92_403.pdf",
        },
        "surface_scaffold_reference": {
            "reference": "Tanaka et al., First-Principles Calculations of Schottky Barrier Heights of Monolayer Metal/6H-SiC{0001} Interfaces",
            "doi": "10.2320/matertrans.47.2690",
            "model_scope": "six C-Si bilayers in a 2x2 cell with hydrogen-saturated back dangling bonds",
        },
        "surface_reconstruction_caveat": (
            "6H-SiC(0001) surface electronic properties depend on preparation and reconstruction; "
            "the generated ideal termination is a pre-relaxation scaffold."
        ),
        "base_template_id": "silicon_carbide_6h_hexagonal",
    }


def _sic_6h_si_face_slab_spec(*, user_request: str, project_id: str) -> ModelSpec:
    assembly = _sic_6h_si_face_assembly(cell_c=SIC_6H_SURFACE_CELL_C_ANGSTROM)
    centered_atoms, center_shift, assembly_extent = _center_sic_6h_atoms(
        assembly.atoms,
        cell_c=assembly.cell_c,
    )
    metadata = {
        **_sic_6h_common_surface_metadata(
            assembly,
            center_shift=center_shift,
            assembly_extent=assembly_extent,
        ),
        "structure_family": "hexagonal 6H-SiC(0001) Si-face surface slab scaffold",
        "materials": ["6H-SiC"],
        "surface_asymmetry_expected_reason": "bare_Si_top_and_hydrogen_passivated_C_bottom_on_polar_6H-SiC_0001",
        "nl_template": SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
        "nl_source": "sic_6h_si_face_surface_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic centered 2x2 six-bilayer 6H-SiC(0001) Si-face slab for live visualization and diagnostics.",
            "The C-terminated back surface is hydrogen-saturated and the exposed Si face is unreconstructed.",
            "Relax and review the polar surface before quantitative surface-energy or electronic conclusions.",
            "Electron-affinity and band-gap values are metadata-only device screening references, not calculated results.",
        ],
    }
    lattice = assembly.source_model.lattice
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(
                    a=assembly.lattice_a,
                    b=assembly.lattice_b,
                    c=assembly.cell_c,
                    alpha=lattice.alpha,
                    beta=lattice.beta,
                    gamma=lattice.gamma,
                ),
                basis_atoms=centered_atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 14,
                "require_convergence": False,
                "notes": [
                    "6H-SiC(0001) Si-face slab scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This polar unreconstructed slab requires reviewed relaxation before production calculations.",
                ],
            },
            "metadata": metadata,
        }
    )


def _sic_6h_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    interface_gap = _match_contact_length_value(
        user_request,
        [
            r"(?:interface|contact)\s+(?:gap|spacing|distance)",
            r"(?:metal[-\s]?semiconductor|schottky)\s+(?:gap|spacing|distance)",
            r"(?:\u754c\u9762|\u63a5\u89e6)\s*(?:\u95f4\u8ddd|\u8ddd\u79bb|\u7a7a\u9699)",
        ],
    ) or 2.4
    metal_thickness = _match_contact_length_value(
        user_request,
        [
            r"(?:metal\s+)?(?:contact|electrode|metal)\s+(?:layer\s+)?thickness",
            r"(?:schottky|metal[-\s]?semiconductor)\s+(?:metal\s+)?(?:contact\s+)?thickness",
            r"(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u91d1\u5c5e\u5c42|\u7535\u6781)\s*(?:\u5c42)?\s*\u539a\u5ea6",
        ],
    ) or 2.56

    assembly = _sic_6h_si_face_assembly(cell_c=SIC_6H_CONTACT_CELL_C_ANGSTROM)
    required_cell_c = round(
        assembly.semiconductor_thickness_angstrom
        + SIC_6H_BACK_SURFACE_H_BOND_ANGSTROM
        + float(interface_gap)
        + float(metal_thickness)
        + 12.0,
        6,
    )
    if required_cell_c > assembly.cell_c:
        assembly = _sic_6h_si_face_assembly(cell_c=required_cell_c)

    atoms = list(assembly.atoms)
    metal_start = assembly.semiconductor_top_fractional + float(interface_gap) / assembly.cell_c
    metal_layer_positions = [metal_start, metal_start + float(metal_thickness) / assembly.cell_c]
    for layer_index, z_value in enumerate(metal_layer_positions, start=1):
        for site_index, (x_value, y_value) in enumerate(assembly.top_registry, start=1):
            if layer_index == 2:
                x_value = (x_value + 1.0 / 6.0) % 1.0
                y_value = (y_value + 1.0 / 6.0) % 1.0
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(assembly.top_registry) + site_index}",
                    element=metal,
                    fractional=[_round_fractional(x_value), _round_fractional(y_value), _round_fractional(z_value)],
                )
            )

    centered_atoms, center_shift, assembly_extent = _center_sic_6h_atoms(
        atoms,
        cell_c=assembly.cell_c,
    )
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    literature_sbh = SIC_6H_SI_FACE_P_TYPE_SBH_EV.get(metal)
    metadata = {
        **_sic_6h_common_surface_metadata(
            assembly,
            center_shift=center_shift,
            assembly_extent=assembly_extent,
        ),
        "structure_family": "hexagonal 6H-SiC metal semiconductor schottky contact scaffold",
        "material": f"{metal}/6H-SiC",
        "materials": ["6H-SiC", metal],
        "stack_sequence": ["6H-SiC", metal],
        "interface": f"{metal}/6H-SiC",
        "interface_orientation": f"{metal} contact / 6H-SiC(0001) Si-face",
        "interface_axis": "c",
        "substrate": "6H-SiC",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "6H-SiC",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "6H-SiC_device_screening_values",
        "metal_work_function_ev": metal_work_function,
        "interface_gap_angstrom": round(float(interface_gap), 6),
        "semiconductor_channel_thickness_angstrom": round(assembly.semiconductor_thickness_angstrom, 6),
        "metal_contact_thickness_angstrom": round(float(metal_thickness), 6),
        "metal_contact_layer_count": 2,
        "reference_interface_metal_layer_count": 1,
        "interface_scaffold": True,
        "unrelaxed_interface": True,
        "surface_asymmetry_expected_reason": "metal_contacted_Si_top_and_hydrogen_passivated_C_bottom_on_polar_6H-SiC_0001",
        "contact_registry": "two_layer_metal_grid_with_first_layer_on_top_of_si_terminated_6H-SiC_0001",
        "material_marker_map": {
            "C": "6H-SiC",
            "Si": "6H-SiC",
            "C;Si": "6H-SiC",
            "Si;C": "6H-SiC",
            metal: metal,
        },
        "interface_reference": {
            "reference": "Tanaka et al., First-Principles Calculations of Schottky Barrier Heights of Monolayer Metal/6H-SiC{0001} Interfaces",
            "doi": "10.2320/matertrans.47.2690",
            "reference_geometry": "2x2 six-bilayer Si-terminated interface with one top-site metal monolayer and hydrogen-saturated back surface",
            "generated_geometry_difference": "A second shifted metal layer is added for contact-thickness visualization and diagnostics.",
            "si_face_p_type_sbh_ev": literature_sbh,
            "sbh_usage": "literature_context_only_not_a_generated_or_calculated_result",
        },
        "nl_template": SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "sic_6h_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic centered 2x2 six-bilayer 6H-SiC(0001) Si-face contact scaffold for live visualization and diagnostics.",
            "The C-terminated back surface is hydrogen-saturated and the contacted semiconductor surface is Si-terminated.",
            "The source interface study used one metal monolayer; this visualization scaffold adds a second layer to expose contact thickness.",
            "Relax the interface and review surface reconstruction, registry, and lattice mismatch before quantitative Schottky conclusions.",
            "Schottky-Mott and literature barrier values are metadata-only screening references, not calculated results.",
        ],
    }
    lattice = assembly.source_model.lattice
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=SIC_6H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(
                    a=assembly.lattice_a,
                    b=assembly.lattice_b,
                    c=assembly.cell_c,
                    alpha=lattice.alpha,
                    beta=lattice.beta,
                    gamma=lattice.gamma,
                ),
                basis_atoms=centered_atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 16,
                "require_convergence": False,
                "notes": [
                    "Metal/6H-SiC(0001) Si-face Schottky scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This centered polar asymmetric interface is an unrelaxed visualization and preflight model, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_sic_4h_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _mentions_sic_4h_contact_host(text):
        return None

    metal = _match_sic_4h_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The 4H-SiC Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _sic_4h_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic centered metal/4H-SiC(0001) Si-face pre-relaxation Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact and polar-surface diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.86
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.84

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _mentions_sic_4h_contact_host(text: str) -> bool:
    return bool(
        re.search(
            r"(?<![A-Za-z0-9])4h[-\s]*(?:sic|silicon\s+carbide)(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(r"4h[-\s]*\u78b3\u5316\u7845", text, flags=re.IGNORECASE)
    )


def _match_sic_4h_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    host = r"(?:4h[-\s]*(?:sic|silicon\s+carbide|\u78b3\u5316\u7845))"
    patterns = [
        rf"(?<![A-Za-z0-9]){metal}\s*/\s*{host}",
        rf"(?<![A-Za-z0-9]){metal}\s*[- ]\s*{host}",
        rf"{host}\s*/\s*{metal}(?![A-Za-z0-9])",
        rf"{host}\s*[- ]\s*{metal}(?![A-Za-z0-9])",
        rf"(?<![A-Za-z0-9]){metal}\s+(?:on|over)\s+{host}",
        rf"(?<![A-Za-z0-9]){metal}\s+{host}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"{host}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?{host}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _sic_4h_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    source_spec = ModelSpec.model_validate(_load_example("silicon_carbide_4h_hexagonal_spec.json"))
    if not isinstance(source_spec.model, CrystalSpec):
        raise ValueError("4H-SiC contact source must be a crystal spec")

    interface_gap = _match_contact_length_value(
        user_request,
        [
            r"(?:interface|contact)\s+(?:gap|spacing|distance)",
            r"(?:metal[-\s]?semiconductor|schottky)\s+(?:gap|spacing|distance)",
            r"(?:\u754c\u9762|\u63a5\u89e6)\s*(?:\u95f4\u8ddd|\u8ddd\u79bb|\u7a7a\u9699)",
        ],
    ) or 2.4
    metal_thickness = _match_contact_length_value(
        user_request,
        [
            r"(?:metal\s+)?(?:contact|electrode|metal)\s+(?:layer\s+)?thickness",
            r"(?:schottky|metal[-\s]?semiconductor)\s+(?:metal\s+)?(?:contact\s+)?thickness",
            r"(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u91d1\u5c5e\u5c42|\u7535\u6781)\s*(?:\u5c42)?\s*\u539a\u5ea6",
        ],
    ) or 2.56

    source_model = source_spec.model
    source_c = float(source_model.lattice.c)
    cut_origin = SIC_4H_SI_FACE_CUT_ORIGIN
    source_positions: list[tuple[BasisAtomSpec, float]] = []
    for source_atom in source_model.basis_atoms:
        _, _, source_z = _basis_atom_fractional_tuple(source_atom)
        wrapped_z = (source_z - cut_origin) % 1.0
        if abs(wrapped_z - 1.0) < 1e-10:
            wrapped_z = 0.0
        source_positions.append((source_atom, wrapped_z))

    slab_extent_angstrom = (max(item[1] for item in source_positions) - min(item[1] for item in source_positions)) * source_c
    cell_c = max(
        SIC_4H_CONTACT_CELL_C_ANGSTROM,
        round(slab_extent_angstrom + float(interface_gap) + float(metal_thickness) + 12.0, 6),
    )
    lattice_a = float(source_model.lattice.a) * 2.0
    lattice_b = float(source_model.lattice.b) * 2.0
    semiconductor_atoms: list[BasisAtomSpec] = []
    for repeat_a in range(2):
        for repeat_b in range(2):
            for source_atom, wrapped_z in source_positions:
                source_x, source_y, _ = _basis_atom_fractional_tuple(source_atom)
                semiconductor_atoms.append(
                    BasisAtomSpec(
                        id=f"{source_atom.id}_{repeat_a}{repeat_b}",
                        element=source_atom.element,
                        fractional=[
                            _round_fractional((source_x + repeat_a) / 2.0),
                            _round_fractional((source_y + repeat_b) / 2.0),
                            _round_fractional(wrapped_z * source_c / cell_c),
                        ],
                    )
                )

    semiconductor_z = [_basis_atom_fractional_tuple(atom)[2] for atom in semiconductor_atoms]
    semiconductor_bottom = min(semiconductor_z)
    semiconductor_top = max(semiconductor_z)
    bottom_elements = {
        atom.element
        for atom in semiconductor_atoms
        if abs(_basis_atom_fractional_tuple(atom)[2] - semiconductor_bottom) < 1e-7
    }
    top_atoms = [
        atom
        for atom in semiconductor_atoms
        if abs(_basis_atom_fractional_tuple(atom)[2] - semiconductor_top) < 1e-7
    ]
    top_elements = {atom.element for atom in top_atoms}
    if bottom_elements != {"C"} or top_elements != {"Si"} or len(top_atoms) != 4:
        raise ValueError("4H-SiC Si-face cut did not produce the expected C-bottom/Si-top 2x2 termination")

    atoms = list(semiconductor_atoms)
    metal_start = semiconductor_top + float(interface_gap) / cell_c
    metal_layer_positions = [metal_start, metal_start + float(metal_thickness) / cell_c]
    top_registry = sorted(
        [(_basis_atom_fractional_tuple(atom)[0], _basis_atom_fractional_tuple(atom)[1]) for atom in top_atoms]
    )
    for layer_index, z_value in enumerate(metal_layer_positions, start=1):
        for site_index, (x_value, y_value) in enumerate(top_registry, start=1):
            if layer_index == 2:
                x_value = (x_value + 1.0 / 6.0) % 1.0
                y_value = (y_value + 1.0 / 6.0) % 1.0
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(top_registry) + site_index}",
                    element=metal,
                    fractional=[_round_fractional(x_value), _round_fractional(y_value), _round_fractional(z_value)],
                )
            )

    all_z = [_basis_atom_fractional_tuple(atom)[2] for atom in atoms]
    center_shift = 0.5 - (min(all_z) + max(all_z)) / 2.0
    centered_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        x_value, y_value, z_value = _basis_atom_fractional_tuple(atom)
        centered_atoms.append(
            BasisAtomSpec(
                id=atom.id,
                element=atom.element,
                fractional=[x_value, y_value, round(z_value + center_shift, 8)],
            )
        )

    centered_z = [_basis_atom_fractional_tuple(atom)[2] for atom in centered_atoms]
    assembly_extent = (max(centered_z) - min(centered_z)) * cell_c
    semiconductor_thickness = (semiconductor_top - semiconductor_bottom) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    source_metadata = dict(source_spec.metadata or {})
    metadata = {
        **source_metadata,
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "hexagonal 4H-SiC metal semiconductor schottky contact scaffold",
        "material": f"{metal}/4H-SiC",
        "materials": ["4H-SiC", metal],
        "stack_sequence": ["4H-SiC", metal],
        "interface": f"{metal}/4H-SiC",
        "interface_orientation": f"{metal} contact / 4H-SiC(0001) Si-face",
        "interface_axis": "c",
        "substrate": "4H-SiC",
        "surface_axis": "c",
        "surface_orientation": "4H-SiC(0001) Si-face",
        "surface_face": "Si-face",
        "slab_centering": {
            "axis": "c",
            "shift_fractional": round(center_shift, 8),
            "source": "dynamic_contact_assembly_centering",
        },
        "contact_cell_axis_length_angstrom": cell_c,
        "contact_assembly_extent_angstrom": round(assembly_extent, 6),
        "contact_total_vacuum_angstrom": round(cell_c - assembly_extent, 6),
        "reference_lattice_angstrom": {"a": float(source_model.lattice.a), "c": source_c},
        "template_supercell": [2, 2, 1],
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "4H-SiC",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "4H-SiC_detector_device_screening_values",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": SIC_4H_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": SIC_4H_BAND_GAP_EV,
        "electronic_screening_reference": {
            "usage": "metadata_only_not_calculated",
            "reference": "Xin et al., Demonstration of the First 4H-SiC EUV Detector with Large Detection Area",
            "electron_affinity_ev": SIC_4H_ELECTRON_AFFINITY_EV,
            "band_gap_ev": SIC_4H_BAND_GAP_EV,
        },
        "interface_gap_angstrom": round(float(interface_gap), 6),
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": round(float(metal_thickness), 6),
        "material_marker_map": {
            "C": "4H-SiC",
            "Si": "4H-SiC",
            "C;Si": "4H-SiC",
            "Si;C": "4H-SiC",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "surface_model": "4H-SiC(0001) Si-terminated metal contact scaffold",
        "termination": "silicon_terminated_pre_relaxation_scaffold",
        "bottom_termination": "carbon_terminated",
        "top_semiconductor_termination": "silicon_terminated",
        "polar_surface": True,
        "surface_asymmetry_expected": True,
        "surface_asymmetry_expected_reason": "single_sided_metal_contact_on_polar_4H-SiC_0001_Si_face",
        "contact_registry": "two_layer_metal_grid_on_top_of_si_terminated_4H-SiC_0001",
        "base_template_id": "silicon_carbide_4h_hexagonal",
        "nl_template": SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "sic_4h_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic centered 4H-SiC(0001) Si-face contact scaffold for live visualization and diagnostics.",
            "The bottom surface is C-terminated and the contacted top semiconductor surface is Si-terminated by construction.",
            "Relax the interface and review the polar asymmetric slab before quantitative Schottky or device conclusions.",
            "Electron-affinity and band-gap values are metadata-only device screening references, not calculated results.",
        ],
    }
    lattice = source_model.lattice
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=SIC_4H_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(
                    a=lattice_a,
                    b=lattice_b,
                    c=cell_c,
                    alpha=lattice.alpha,
                    beta=lattice.beta,
                    gamma=lattice.gamma,
                ),
                basis_atoms=centered_atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 14,
                "require_convergence": False,
                "notes": [
                    "Metal/4H-SiC(0001) Si-face Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This centered polar asymmetric interface is an unrelaxed scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )



def _infer_inp_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _material_alias_present(text, "InP") and "indium phosphide" not in text and "\u78f7\u5316\u94df" not in text:
        return None

    metal = _match_inp_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The InP Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _inp_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic pre-relaxation metal/InP(001) Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact geometry diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_inp_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    inp = r"(?:inp|indium\s+phosphide|\u78f7\u5316\u94df)"
    patterns = [
        rf"\b{metal}\s*/\s*{inp}\b",
        rf"\b{metal}\s*[- ]\s*{inp}\b",
        rf"\b{inp}\s*/\s*{metal}\b",
        rf"\b{inp}\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+{inp}\b",
        rf"\b{metal}\s+{inp}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{inp}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?{inp}\b",
        rf"{metal}\s*/\s*(?:InP|inp|\u78f7\u5316\u94df)",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:InP|inp|\u78f7\u5316\u94df)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _inp_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    cell_c = 32.0
    lattice_a = 5.8687
    interface_gap = 2.5
    metal_thickness = 2.56
    inp_layer_z = [0.08, 0.17, 0.26, 0.35]
    metal_start_z = inp_layer_z[-1] + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    in_plane_pairs = [
        (0.0, 0.0, 0.25, 0.25),
        (0.5, 0.5, 0.75, 0.75),
    ]
    atoms: list[BasisAtomSpec] = []
    for layer_index, z_value in enumerate(inp_layer_z, start=1):
        for pair_index, (in_x, in_y, p_x, p_y) in enumerate(in_plane_pairs, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"InPIn{layer_index}_{pair_index}",
                    element="In",
                    fractional=[in_x, in_y, z_value],
                )
            )
            atoms.append(
                BasisAtomSpec(
                    id=f"InPP{layer_index}_{pair_index}",
                    element="P",
                    fractional=[p_x, p_y, z_value],
                )
            )
    metal_positions = [(0.0, 0.0), (0.5, 0.5), (0.25, 0.25), (0.75, 0.75)]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(metal_positions, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_positions) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_thickness = (inp_layer_z[-1] - inp_layer_z[0]) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "zinc blende InP metal semiconductor schottky contact scaffold",
        "material": f"{metal}/InP",
        "materials": ["InP", metal],
        "stack_sequence": ["InP", metal],
        "interface": f"{metal}/InP",
        "interface_orientation": f"{metal} contact / InP(001)",
        "interface_axis": "c",
        "substrate": "InP",
        "surface_axis": "c",
        "surface_orientation": "InP(001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": lattice_a,
        "inp_reference_lattice_angstrom": lattice_a,
        "coherent_strain_model": "matched_to_inp_001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "InP",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": INP_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": INP_BAND_GAP_EV,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            "In": "InP",
            "P": "InP",
            "In;P": "InP",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "nl_template": INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "inp_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic matched-cell scaffold for live visualization and diagnostics.",
            "Relax the interface and review In/P termination before quantitative Schottky or device conclusions.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=INP_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(a=lattice_a, b=lattice_a, c=cell_c, alpha=90.0, beta=90.0, gamma=90.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 520,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    "Metal/InP(001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This is an unrelaxed deterministic interface scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_inas_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _material_alias_present(text, "InAs") and "indium arsenide" not in text and "\u7837\u5316\u94df" not in text:
        return None

    metal = _match_inas_contact_metal(text) or "Au"
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The InAs Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _inas_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic pre-relaxation metal/InAs(001) Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact geometry diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_inas_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    inas = r"(?:inas|indium\s+arsenide|\u7837\u5316\u94df)"
    patterns = [
        rf"\b{metal}\s*/\s*{inas}\b",
        rf"\b{metal}\s*[- ]\s*{inas}\b",
        rf"\b{inas}\s*/\s*{metal}\b",
        rf"\b{inas}\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+{inas}\b",
        rf"\b{metal}\s+{inas}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{inas}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?{inas}\b",
        rf"{metal}\s*/\s*(?:InAs|inas|\u7837\u5316\u94df)",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:InAs|inas|\u7837\u5316\u94df)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _inas_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    cell_c = 32.0
    lattice_a = 6.0583
    interface_gap = 2.5
    metal_thickness = 2.56
    inas_layer_z = [0.08, 0.17, 0.26, 0.35]
    metal_start_z = inas_layer_z[-1] + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    in_plane_pairs = [
        (0.0, 0.0, 0.25, 0.25),
        (0.5, 0.5, 0.75, 0.75),
    ]
    atoms: list[BasisAtomSpec] = []
    for layer_index, z_value in enumerate(inas_layer_z, start=1):
        for pair_index, (in_x, in_y, as_x, as_y) in enumerate(in_plane_pairs, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"InAsIn{layer_index}_{pair_index}",
                    element="In",
                    fractional=[in_x, in_y, z_value],
                )
            )
            atoms.append(
                BasisAtomSpec(
                    id=f"InAsAs{layer_index}_{pair_index}",
                    element="As",
                    fractional=[as_x, as_y, z_value],
                )
            )
    metal_positions = [(0.0, 0.0), (0.5, 0.5), (0.25, 0.25), (0.75, 0.75)]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(metal_positions, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_positions) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_thickness = (inas_layer_z[-1] - inas_layer_z[0]) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "zinc blende InAs metal semiconductor schottky contact scaffold",
        "material": f"{metal}/InAs",
        "materials": ["InAs", metal],
        "stack_sequence": ["InAs", metal],
        "interface": f"{metal}/InAs",
        "interface_orientation": f"{metal} contact / InAs(001)",
        "interface_axis": "c",
        "substrate": "InAs",
        "surface_axis": "c",
        "surface_orientation": "InAs(001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": lattice_a,
        "inas_reference_lattice_angstrom": lattice_a,
        "coherent_strain_model": "matched_to_inas_001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "InAs",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": INAS_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": INAS_BAND_GAP_EV,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            "In": "InAs",
            "As": "InAs",
            "As;In": "InAs",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "nl_template": INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "inas_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic matched-cell scaffold for live visualization and diagnostics.",
            "Relax the interface and review In/As termination before quantitative Schottky or device conclusions.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=INAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(a=lattice_a, b=lattice_a, c=cell_c, alpha=90.0, beta=90.0, gamma=90.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 520,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    "Metal/InAs(001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This is an unrelaxed deterministic interface scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_alas_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    if not _material_alias_present(text, "AlAs") and "aluminum arsenide" not in text and "\u7837\u5316\u94dd" not in text:
        return None

    metal = _match_alas_contact_metal(text) or "Au"
    if metal == "Al":
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                "Al/AlAs Schottky contact scaffold is ambiguous because Al is both the requested metal and the semiconductor cation.",
                "Use Au, Pt, Ti, Ni, Cu, Mo, W, Pd, or Ag for the local AlAs contact scaffold, or provide a reviewed ModelSpec with region-tagged metal atoms.",
            ],
        )
    if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                f"The AlAs Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                "Use one of Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
            ],
        )

    chosen_project_id = project_id or _project_id(ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID, user_request)
    model_spec = _alas_schottky_contact_spec(
        metal=metal,
        user_request=user_request,
        project_id=chosen_project_id,
    )
    notes = [
        "Generated a deterministic pre-relaxation metal/AlAs(001) Schottky contact scaffold.",
        "The scaffold is for same-window visualization, contact geometry diagnostics, and metadata preflight before reviewed relaxation.",
    ]
    confidence = 0.84
    composite = _apply_new_crystal_composite_operations(user_request, model_spec)
    if isinstance(composite, NaturalLanguagePlan):
        return composite
    if composite is not None:
        model_spec, diff = composite
        metadata = {
            **dict(model_spec.metadata or {}),
            "nl_composite_operations": diff,
        }
        model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
        notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
        confidence = 0.82

    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=confidence,
        template_id=ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        notes=notes,
    )


def _match_alas_contact_metal(text: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    alas = r"(?:alas|aluminum\s+arsenide|aluminium\s+arsenide|\u7837\u5316\u94dd)"
    patterns = [
        rf"\b{metal}\s*/\s*{alas}\b",
        rf"\b{metal}\s*[- ]\s*{alas}\b",
        rf"\b{alas}\s*/\s*{metal}\b",
        rf"\b{alas}\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+{alas}\b",
        rf"\b{metal}\s+{alas}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{alas}\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?{alas}\b",
        rf"{metal}\s*/\s*(?:AlAs|alas|\u7837\u5316\u94dd)",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:AlAs|alas|\u7837\u5316\u94dd)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _alas_schottky_contact_spec(*, metal: str, user_request: str, project_id: str) -> ModelSpec:
    cell_c = 32.0
    lattice_a = 5.6611
    interface_gap = 2.5
    metal_thickness = 2.56
    alas_layer_z = [0.08, 0.17, 0.26, 0.35]
    metal_start_z = alas_layer_z[-1] + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    in_plane_pairs = [
        (0.0, 0.0, 0.25, 0.25),
        (0.5, 0.5, 0.75, 0.75),
    ]
    atoms: list[BasisAtomSpec] = []
    for layer_index, z_value in enumerate(alas_layer_z, start=1):
        for pair_index, (al_x, al_y, as_x, as_y) in enumerate(in_plane_pairs, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"AlAsAl{layer_index}_{pair_index}",
                    element="Al",
                    fractional=[al_x, al_y, z_value],
                )
            )
            atoms.append(
                BasisAtomSpec(
                    id=f"AlAsAs{layer_index}_{pair_index}",
                    element="As",
                    fractional=[as_x, as_y, z_value],
                )
            )
    metal_positions = [(0.0, 0.0), (0.5, 0.5), (0.25, 0.25), (0.75, 0.75)]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(metal_positions, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_positions) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_thickness = (alas_layer_z[-1] - alas_layer_z[0]) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": "zinc blende AlAs metal semiconductor schottky contact scaffold",
        "material": f"{metal}/AlAs",
        "materials": ["AlAs", metal],
        "stack_sequence": ["AlAs", metal],
        "interface": f"{metal}/AlAs",
        "interface_orientation": f"{metal} contact / AlAs(001)",
        "interface_axis": "c",
        "substrate": "AlAs",
        "surface_axis": "c",
        "surface_orientation": "AlAs(001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": lattice_a,
        "alas_reference_lattice_angstrom": lattice_a,
        "coherent_strain_model": "matched_to_alas_001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": "AlAs",
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": ALAS_ELECTRON_AFFINITY_EV,
        "semiconductor_band_gap_ev": ALAS_BAND_GAP_EV,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            "Al": "AlAs",
            "As": "AlAs",
            "Al;As": "AlAs",
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "same_element_metal_semiconductor_contact_supported": False,
        "nl_template": ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_virtual_template": ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
        "nl_source": "alas_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Deterministic matched-cell scaffold for live visualization and diagnostics.",
            "Relax the interface and review Al/As termination before quantitative Schottky or device conclusions.",
            "Al metal on AlAs is not generated by this local template because atom element labels alone cannot distinguish metal Al from semiconductor Al.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=ALAS_SCHOTTKY_CONTACT_VIRTUAL_TEMPLATE_ID,
                lattice=LatticeSpec(a=lattice_a, b=lattice_a, c=cell_c, alpha=90.0, beta=90.0, gamma=90.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 520,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    "Metal/AlAs(001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This is an unrelaxed deterministic interface scaffold for visual review and preflight diagnostics, not a production interface.",
                    "Al/AlAs same-element contact requires reviewed region-tagged structure data and is intentionally not generated here.",
                ],
            },
            "metadata": metadata,
        }
    )


def _infer_zincblende_schottky_contact_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    if not _looks_like_metal_semiconductor_contact_text(text):
        return None
    for profile in GENERIC_ZINCBLENDE_SCHOTTKY_CONTACT_PROFILES:
        if not _zincblende_profile_material_present(text, profile):
            continue
        metal = _match_zincblende_profile_contact_metal(text, profile) or "Au"
        if metal in set(profile.excluded_metals):
            return NaturalLanguagePlan(
                kind="unsupported",
                payload=None,
                confidence=0.0,
                template_id=None,
                notes=[
                    f"{metal}/{profile.material} Schottky contact scaffold is ambiguous or excluded for the local profile.",
                    "Provide a reviewed ModelSpec with region-tagged metal atoms, or choose a supported contact metal.",
                ],
            )
        if metal not in CONTACT_METAL_WORK_FUNCTION_EV:
            return NaturalLanguagePlan(
                kind="unsupported",
                payload=None,
                confidence=0.0,
                template_id=None,
                notes=[
                    f"The {profile.material} Schottky contact scaffold does not have a reviewed work-function preset for {metal}.",
                    "Use one of Al, Ti, Ni, Cu, Mo, W, Pd, Ag, Pt, or Au, or provide a reviewed ModelSpec with explicit metadata.",
                ],
            )

        chosen_project_id = project_id or _project_id(profile.template_id, user_request)
        model_spec = _zincblende_schottky_contact_spec(
            profile=profile,
            metal=metal,
            user_request=user_request,
            project_id=chosen_project_id,
        )
        notes = [
            f"Generated a deterministic pre-relaxation metal/{profile.material}(001) Schottky contact scaffold.",
            "The scaffold is for same-window visualization, contact geometry diagnostics, and metadata preflight before reviewed relaxation.",
        ]
        confidence = 0.83
        composite = _apply_new_crystal_composite_operations(user_request, model_spec)
        if isinstance(composite, NaturalLanguagePlan):
            return composite
        if composite is not None:
            model_spec, diff = composite
            metadata = {
                **dict(model_spec.metadata or {}),
                "nl_composite_operations": diff,
            }
            model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
            notes.append("Applied deterministic contact patch operations during new-structure planning: " + ", ".join(diff) + ".")
            confidence = 0.81

        return NaturalLanguagePlan(
            kind="spec",
            payload=model_spec.model_dump(mode="json"),
            confidence=confidence,
            template_id=profile.template_id,
            notes=notes,
        )
    return None


def _zincblende_profile_material_present(text: str, profile: ZincblendeSchottkyContactProfile) -> bool:
    return _material_alias_present(text, profile.material) or any(_contains_term(text, term) for term in profile.material_terms)


def _match_zincblende_profile_contact_metal(text: str, profile: ZincblendeSchottkyContactProfile) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    material = _zincblende_profile_material_pattern(profile)
    patterns = [
        rf"\b{metal}\s*/\s*(?:{material})\b",
        rf"\b{metal}\s*[- ]\s*(?:{material})\b",
        rf"\b(?:{material})\s*/\s*{metal}\b",
        rf"\b(?:{material})\s*[- ]\s*{metal}\b",
        rf"\b{metal}\s+(?:on|over)\s+(?:{material})\b",
        rf"\b{metal}\s+(?:{material})\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b(?:{material})\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode).{{0,40}}?\b(?:with|using|use|as)\s+{metal}\b",
        rf"\b(?:use|make|set)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode).{{0,40}}?(?:{material})\b",
        rf"{metal}\s*/\s*(?:{material})",
        rf"(?:\u4f7f\u7528|\u91c7\u7528|\u7528|\u4ee5)\s*{metal}\s*(?:\u4f5c\u4e3a)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781).{{0,20}}?(?:{material})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        normalized = _normalize_element(match.group("metal"))
        if normalized is not None:
            return normalized
    return None


def _zincblende_profile_material_pattern(profile: ZincblendeSchottkyContactProfile) -> str:
    terms = set(profile.material_terms)
    terms.update(_material_text_aliases(profile.material))
    return "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True) if term)


def _zincblende_schottky_contact_spec(
    *,
    profile: ZincblendeSchottkyContactProfile,
    metal: str,
    user_request: str,
    project_id: str,
) -> ModelSpec:
    cell_c = 32.0
    interface_gap = 2.5
    metal_thickness = 2.56
    semiconductor_layer_z = [0.08, 0.17, 0.26, 0.35]
    metal_start_z = semiconductor_layer_z[-1] + interface_gap / cell_c
    metal_layer_z = [metal_start_z, metal_start_z + metal_thickness / cell_c]
    in_plane_pairs = [
        (0.0, 0.0, 0.25, 0.25),
        (0.5, 0.5, 0.75, 0.75),
    ]
    atoms: list[BasisAtomSpec] = []
    for layer_index, z_value in enumerate(semiconductor_layer_z, start=1):
        for pair_index, (cation_x, cation_y, anion_x, anion_y) in enumerate(in_plane_pairs, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{profile.material}{profile.cation}{layer_index}_{pair_index}",
                    element=profile.cation,
                    fractional=[cation_x, cation_y, z_value],
                )
            )
            atoms.append(
                BasisAtomSpec(
                    id=f"{profile.material}{profile.anion}{layer_index}_{pair_index}",
                    element=profile.anion,
                    fractional=[anion_x, anion_y, z_value],
                )
            )
    metal_positions = [(0.0, 0.0), (0.5, 0.5), (0.25, 0.25), (0.75, 0.75)]
    for layer_index, z_value in enumerate(metal_layer_z, start=1):
        for site_index, (x_value, y_value) in enumerate(metal_positions, start=1):
            atoms.append(
                BasisAtomSpec(
                    id=f"{metal}Contact{(layer_index - 1) * len(metal_positions) + site_index}",
                    element=metal,
                    fractional=[x_value, y_value, _round_fractional(z_value)],
                )
            )

    semiconductor_marker = ";".join(sorted([profile.cation, profile.anion]))
    semiconductor_thickness = (semiconductor_layer_z[-1] - semiconductor_layer_z[0]) * cell_c
    metal_work_function = CONTACT_METAL_WORK_FUNCTION_EV[metal]
    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": f"zinc blende {profile.material} metal semiconductor schottky contact scaffold",
        "material": f"{metal}/{profile.material}",
        "materials": [profile.material, metal],
        "stack_sequence": [profile.material, metal],
        "interface": f"{metal}/{profile.material}",
        "interface_orientation": f"{metal} contact / {profile.material}(001)",
        "interface_axis": "c",
        "substrate": profile.material,
        "surface_axis": "c",
        "surface_orientation": f"{profile.material}(001)",
        "vacuum_angstrom": round(cell_c - (metal_layer_z[-1] * cell_c), 6),
        "in_plane_lattice_angstrom": profile.lattice_a,
        f"{profile.material.lower()}_reference_lattice_angstrom": profile.lattice_a,
        "coherent_strain_model": f"matched_to_{profile.material.lower()}_001_pre_relaxation_scaffold",
        "metal_semiconductor_interface": True,
        "schottky_contact": True,
        "contact_type": "schottky",
        "metal_contact_material": metal,
        "semiconductor_channel_material": profile.material,
        "schottky_barrier_model": "ideal_schottky_mott_metadata_reference",
        "schottky_barrier_reference": "template_estimate_for_preflight_only",
        "metal_work_function_ev": metal_work_function,
        "semiconductor_electron_affinity_ev": profile.electron_affinity_ev,
        "semiconductor_band_gap_ev": profile.band_gap_ev,
        "interface_gap_angstrom": interface_gap,
        "semiconductor_channel_thickness_angstrom": round(semiconductor_thickness, 6),
        "metal_contact_thickness_angstrom": metal_thickness,
        "material_marker_map": {
            profile.cation: profile.material,
            profile.anion: profile.material,
            semiconductor_marker: profile.material,
            metal: metal,
        },
        "layer_profile_tolerance_fractional": 0.0001,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "nl_template": profile.template_id,
        "nl_virtual_template": profile.template_id,
        "nl_source": "generic_zincblende_schottky_contact_scaffold_template",
        "nl_user_request": user_request,
        "scaffold_notes": [
            "Profile-driven matched-cell scaffold for live visualization and diagnostics.",
            f"Relax the interface and review {profile.cation}/{profile.anion} termination before quantitative Schottky or device conclusions.",
        ],
    }
    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=profile.template_id,
                lattice=LatticeSpec(
                    a=profile.lattice_a,
                    b=profile.lattice_a,
                    c=cell_c,
                    alpha=90.0,
                    beta=90.0,
                    gamma=90.0,
                ),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": profile.cutoff_energy_ev,
                "kpoint_separation": 0.04,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    f"Metal/{profile.material}(001) Schottky contact scaffold; explicit execute materializes CIF for GUI hot-loading.",
                    "This is an unrelaxed deterministic interface scaffold for visual review and preflight diagnostics, not a production interface.",
                ],
            },
            "metadata": metadata,
        }
    )


def _is_semiconductor_heterostructure_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:heterostructure|interface|superlattice|quantum\s+well|mqw|hemt)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(r"(?<![A-Za-z0-9])(?:hemt|2deg)(?![A-Za-z0-9])", text, flags=re.IGNORECASE)
        or bool(
            re.search(
                r"\b(?:2deg|two[-\s]+dimensional\s+electron\s+gas|high\s+electron\s+mobility\s+transistor)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        or any(
            term in text
            for term in (
                "\u5f02\u8d28\u7ed3",
                "\u5f02\u8d28\u7ed3\u6784",
                "\u754c\u9762",
                "\u8d85\u6676\u683c",
                "\u91cf\u5b50\u9631",
                "\u4e8c\u7ef4\u7535\u5b50\u6c14",
                "\u9ad8\u7535\u5b50\u8fc1\u79fb\u7387\u6676\u4f53\u7ba1",
            )
        )
    )


def _infer_template(text: str, *, user_request: str, project_id: str | None) -> NaturalLanguagePlan | None:
    tmd_heterobilayer_plan = _infer_commensurate_tmd_heterobilayer_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if tmd_heterobilayer_plan is not None:
        return tmd_heterobilayer_plan

    sic_6h_contact_plan = _infer_sic_6h_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if sic_6h_contact_plan is not None:
        return sic_6h_contact_plan

    sic_6h_surface_plan = _infer_sic_6h_si_face_slab_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if sic_6h_surface_plan is not None:
        return sic_6h_surface_plan

    unsupported_sic_6h_plan = _infer_unsupported_sic_6h_derived_structure_request(text)
    if unsupported_sic_6h_plan is not None:
        return unsupported_sic_6h_plan

    gaas_contact_plan = _infer_gaas_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if gaas_contact_plan is not None:
        return gaas_contact_plan

    gan_contact_plan = _infer_gan_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if gan_contact_plan is not None:
        return gan_contact_plan

    zno_contact_plan = _infer_zno_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if zno_contact_plan is not None:
        return zno_contact_plan

    beta_ga2o3_contact_plan = _infer_beta_ga2o3_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if beta_ga2o3_contact_plan is not None:
        return beta_ga2o3_contact_plan

    sic_4h_contact_plan = _infer_sic_4h_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if sic_4h_contact_plan is not None:
        return sic_4h_contact_plan

    inp_contact_plan = _infer_inp_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if inp_contact_plan is not None:
        return inp_contact_plan

    inas_contact_plan = _infer_inas_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if inas_contact_plan is not None:
        return inas_contact_plan

    alas_contact_plan = _infer_alas_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if alas_contact_plan is not None:
        return alas_contact_plan

    zincblende_contact_plan = _infer_zincblende_schottky_contact_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if zincblende_contact_plan is not None:
        return zincblende_contact_plan

    unsupported_contact_plan = _infer_unsupported_metal_semiconductor_contact_request(text)
    if unsupported_contact_plan is not None:
        return unsupported_contact_plan

    sapphire_interface_plan = _infer_sapphire_interface_scaffold_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if sapphire_interface_plan is not None:
        return sapphire_interface_plan

    sapphire_epitaxy_plan = _infer_sapphire_epitaxy_preflight_template(
        text,
        user_request=user_request,
        project_id=project_id,
    )
    if sapphire_epitaxy_plan is not None:
        return sapphire_epitaxy_plan

    if not _is_semiconductor_heterostructure_request(text):
        formula_alloy_plan = _infer_formula_alloy_template(text, user_request=user_request, project_id=project_id)
        if formula_alloy_plan is not None:
            return formula_alloy_plan

    substituted_benzene_plan = _infer_substituted_benzene_template(text, user_request=user_request, project_id=project_id)
    if substituted_benzene_plan is not None:
        return substituted_benzene_plan

    forced_template_id = _match_explicit_template_id(text)
    for template in TEMPLATE_SPECS:
        template_id = str(template["template_id"])
        if forced_template_id is not None and template_id != forced_template_id:
            continue
        if forced_template_id == template_id or _template_matches_text(template, text):
            spec = _load_example(str(template["example"]))
            chosen_project_id = project_id or _project_id(template_id, user_request)
            metadata = {
                **dict(spec.get("metadata") or {}),
                "nl_template": template["template_id"],
                "nl_source": "local_template",
                "nl_user_request": user_request,
            }
            spec = {**spec, "project_id": chosen_project_id, "revision": 0, "metadata": metadata}
            model_spec = ModelSpec.model_validate(spec)
            customization_diff: list[str] = []
            alloy_customized = _apply_iii_nitride_heterostructure_formula_request(
                user_request,
                model_spec,
                template_id=str(template["template_id"]),
            )
            if alloy_customized is not None:
                model_spec, alloy_diff = alloy_customized
                customization_diff.extend(alloy_diff)
            try:
                customized = _apply_quantum_well_layer_request(user_request, model_spec)
            except ValueError as exc:
                return NaturalLanguagePlan(
                    kind="unsupported",
                    payload=None,
                    confidence=0.0,
                    template_id=None,
                    notes=[
                        "A semiconductor heterostructure template matched, but the requested quantum-well layer counts could not be applied safely.",
                        str(exc),
                        "Use even layer counts whose total is a multiple of four, such as '8 GaAs layers and 4 AlAs layers' or '6 well layers and 6 barrier layers'.",
                    ],
                )
            if customized is not None:
                model_spec, customization_diff = customized
            if template_id == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure":
                try:
                    p_gan_customized = _apply_p_gan_gate_cap_request(user_request, model_spec)
                except ValueError as exc:
                    return NaturalLanguagePlan(
                        kind="unsupported",
                        payload=None,
                        confidence=0.0,
                        template_id=None,
                        notes=[
                            "The AlGaN/GaN HEMT template matched, but the requested p-GaN gate/cap layer could not be applied safely.",
                            str(exc),
                            "Provide a reviewed ModelSpec/SemanticPatch if you need a different p-GaN gate geometry.",
                        ],
                    )
                if p_gan_customized is not None:
                    model_spec, p_gan_diff = p_gan_customized
                    customization_diff.extend(p_gan_diff)
            composite = _apply_new_crystal_composite_operations(
                user_request,
                model_spec,
                skip_alloy=bool(customization_diff),
                skip_dopant=bool((model_spec.metadata or {}).get("p_gan_gate_cap")),
            )
            confidence = 0.85
            notes = [str(template["notes"]), "Generated from a local deterministic template."]
            if isinstance(composite, NaturalLanguagePlan):
                return composite
            if composite is not None:
                model_spec, composite_diff = composite
                diff = [*customization_diff, *composite_diff]
                metadata = {
                    **dict(model_spec.metadata or {}),
                    "nl_composite_operations": diff,
                }
                model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
                spec = model_spec.model_dump(mode="json")
                notes.append("Applied deterministic semiconductor patch operations during new-structure planning: " + ", ".join(diff) + ".")
                confidence = 0.82
            elif customization_diff:
                metadata = {
                    **dict(model_spec.metadata or {}),
                    "nl_composite_operations": customization_diff,
                }
                model_spec = model_spec.model_copy(update={"revision": 0, "metadata": metadata})
                spec = model_spec.model_dump(mode="json")
                notes.append(
                    "Applied deterministic semiconductor layer customization during new-structure planning: "
                    + ", ".join(customization_diff)
                    + "."
                )
                confidence = 0.83
            return NaturalLanguagePlan(
                kind="spec",
                payload=spec,
                confidence=confidence,
                template_id=template_id,
                notes=notes,
            )
    return None


def _infer_sapphire_epitaxy_preflight_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    target = _match_sapphire_epitaxy_target(text)
    if target is None:
        return None
    if not _mentions_sapphire_substrate(text):
        return None
    if not _looks_like_sapphire_epitaxy_context(text):
        return None

    template_id = "alpha_alumina_sapphire_substrate"
    virtual_template_id = (
        "gallium_nitride_on_sapphire_epitaxy_preflight"
        if target == "GaN"
        else "aluminum_nitride_on_sapphire_epitaxy_preflight"
    )
    spec = _load_example("alpha_alumina_sapphire_substrate_spec.json")
    chosen_project_id = project_id or _project_id(virtual_template_id, user_request)
    metadata = {
        **dict(spec.get("metadata") or {}),
        "nl_template": template_id,
        "nl_virtual_template": virtual_template_id,
        "nl_source": "sapphire_epitaxy_preflight_template",
        "nl_user_request": user_request,
        "nl_epitaxy_preflight": True,
        "nl_epitaxy_target": target,
        "nl_epitaxy_substrate": "Al2O3",
        "nl_epitaxy_next_action": "choose_epitaxy_target_then_build_reviewed_interface_spec",
    }
    model = dict(spec.get("model") or {})
    model["name"] = virtual_template_id
    spec = {
        **spec,
        "project_id": chosen_project_id,
        "revision": 0,
        "model": model,
        "metadata": metadata,
    }
    model_spec = ModelSpec.model_validate(spec)
    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=0.86,
        template_id=template_id,
        notes=[
            f"Mapped {target}-on-sapphire request to the sapphire substrate epitaxy preflight template.",
            "This creates the Al2O3 substrate precursor and exports target lattice/domain-match diagnostics; it does not claim an atomistic film/substrate interface yet.",
            "Use a reviewed interface ModelSpec after choosing the epitaxy target and domain match.",
        ],
    )


def _infer_sapphire_interface_scaffold_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    target = _match_sapphire_epitaxy_target(text)
    if target is None:
        return None
    if not _mentions_sapphire_substrate(text):
        return None
    if not _looks_like_sapphire_interface_scaffold_request(text):
        return None

    virtual_template_id = (
        "gallium_nitride_on_sapphire_interface_scaffold"
        if target == "GaN"
        else "aluminum_nitride_on_sapphire_interface_scaffold"
    )
    chosen_project_id = project_id or _project_id(virtual_template_id, user_request)
    model_spec = _build_sapphire_iii_nitride_interface_scaffold(
        target,
        project_id=chosen_project_id,
        user_request=user_request,
        virtual_template_id=virtual_template_id,
    )
    return NaturalLanguagePlan(
        kind="spec",
        payload=model_spec.model_dump(mode="json"),
        confidence=0.84,
        template_id="alpha_alumina_sapphire_substrate",
        notes=[
            f"Generated a deterministic {target}-on-sapphire pre-relaxation interface scaffold.",
            "The scaffold uses a 2x2 sapphire in-plane domain matched to a 3x3 wurtzite nitride film domain for visualization and diagnostics.",
            "Treat this as an unrelaxed interface starting point; run relaxation only after reviewing termination, polarity, and interface spacing.",
        ],
    )


def _looks_like_sapphire_interface_scaffold_request(text: str) -> bool:
    if _looks_like_sapphire_preflight_only_request(text):
        return False
    return bool(
        re.search(
            r"\b(?:interface\s+model|interface\s+scaffold|atomic\s+interface|heterointerface|hot[-\s]?load|live\s+gui|real[-\s]?time)\b",
            text,
            flags=re.IGNORECASE,
        )
        or any(
            term in text
            for term in (
                "\u754c\u9762\u6a21\u578b",
                "\u754c\u9762\u811a\u624b\u67b6",
                "\u539f\u5b50\u754c\u9762",
                "\u70ed\u52a0\u8f7d",
                "\u5b9e\u65f6\u70ed\u52a0\u8f7d",
                "\u5b9e\u65f6",
            )
        )
    )


def _looks_like_sapphire_preflight_only_request(text: str) -> bool:
    return any(
        term in text
        for term in (
            "preflight",
            "diagnostic",
            "diagnostics",
            "lattice mismatch",
            "mismatch only",
            "\u9884\u68c0",
            "\u8bca\u65ad",
            "\u53c2\u6570",
            "\u6676\u683c\u5931\u914d",
        )
    ) and not any(
        term in text
        for term in (
            "hot-load",
            "hot load",
            "interface model",
            "interface scaffold",
            "atomic interface",
            "\u70ed\u52a0\u8f7d",
            "\u754c\u9762\u6a21\u578b",
            "\u754c\u9762\u811a\u624b\u67b6",
            "\u539f\u5b50\u754c\u9762",
        )
    )


def _build_sapphire_iii_nitride_interface_scaffold(
    target: str,
    *,
    project_id: str,
    user_request: str,
    virtual_template_id: str,
) -> ModelSpec:
    sapphire_spec = ModelSpec.model_validate(_load_example("alpha_alumina_sapphire_substrate_spec.json"))
    film_example = "gallium_nitride_wurtzite_spec.json" if target == "GaN" else "aluminum_nitride_wurtzite_spec.json"
    film_spec = ModelSpec.model_validate(_load_example(film_example))
    if not isinstance(sapphire_spec.model, CrystalSpec) or not isinstance(film_spec.model, CrystalSpec):
        raise ValueError("Sapphire interface scaffold requires crystal example templates.")

    sapphire = sapphire_spec.model
    film = film_spec.model
    substrate_repeats = 2
    film_repeats = 3
    bottom_vacuum = 8.0
    top_vacuum = 8.0
    interface_gap = 3.0
    substrate_c = sapphire.lattice.c
    film_c = film.lattice.c
    vacuum = bottom_vacuum + top_vacuum
    cell_c = bottom_vacuum + substrate_c + interface_gap + film_c + top_vacuum
    common_a = sapphire.lattice.a * substrate_repeats

    atoms: list[BasisAtomSpec] = []
    for atom in sapphire.basis_atoms:
        fx, fy, fz = atom.fractional.as_tuple()
        for ix in range(substrate_repeats):
            for iy in range(substrate_repeats):
                atoms.append(
                    BasisAtomSpec(
                        id=f"{atom.element}S{len(atoms) + 1:03d}",
                        element=atom.element,
                        fractional=[
                            (fx + ix) / substrate_repeats,
                            (fy + iy) / substrate_repeats,
                            (bottom_vacuum + fz * substrate_c) / cell_c,
                        ],
                    )
                )

    film_start = bottom_vacuum + substrate_c + interface_gap
    for atom in film.basis_atoms:
        fx, fy, fz = atom.fractional.as_tuple()
        for ix in range(film_repeats):
            for iy in range(film_repeats):
                atoms.append(
                    BasisAtomSpec(
                        id=f"{atom.element}F{len(atoms) + 1:03d}",
                        element=atom.element,
                        fractional=[
                            (fx + ix) / film_repeats,
                            (fy + iy) / film_repeats,
                            (film_start + fz * film_c) / cell_c,
                        ],
                    )
                )

    preflight_targets = list((sapphire_spec.metadata or {}).get("epitaxy_targets") or [])
    selected_preflight = next(
        (
            item
            for item in preflight_targets
            if isinstance(item, dict) and str(item.get("material") or "").lower() == target.lower()
        ),
        {},
    )
    selected_domain = selected_preflight.get("domain_match") if isinstance(selected_preflight, dict) else {}
    if not isinstance(selected_domain, dict):
        selected_domain = {}
    film_strain = 100.0 * ((common_a / film_repeats) - film.lattice.a) / film.lattice.a

    metadata = {
        "source": "local_dynamic_template",
        "domain": "semiconductor",
        "structure_family": f"wurtzite {target} on c-plane sapphire interface scaffold",
        "material": f"{target}/Al2O3",
        "materials": ["Al2O3", target],
        "substrate": "Al2O3",
        "substrate_material": "Al2O3",
        "film_material": target,
        "interface": f"Al2O3/{target}",
        "interface_orientation": f"Al2O3(0001)//{target}(0001)",
        "interface_axis": "c",
        "surface_axis": "c",
        "substrate_orientation": "Al2O3(0001) c-plane",
        "nl_template": "alpha_alumina_sapphire_substrate",
        "nl_virtual_template": virtual_template_id,
        "nl_source": "sapphire_interface_scaffold_template",
        "nl_user_request": user_request,
        "nl_epitaxy_preflight": False,
        "nl_epitaxy_target": target,
        "interface_scaffold": True,
        "pre_relaxation_scaffold": True,
        "unrelaxed_interface": True,
        "requires_geometry_relaxation": True,
        "substrate_supercell": [2, 2, 1],
        "film_supercell": [3, 3, 1],
        "domain_match": {
            "film_repeats": film_repeats,
            "substrate_repeats": substrate_repeats,
            "film_period_angstrom": selected_domain.get("film_period_angstrom") or round(film.lattice.a * film_repeats, 6),
            "substrate_period_angstrom": selected_domain.get("substrate_period_angstrom") or round(common_a, 6),
            "mismatch_percent": selected_domain.get("mismatch_percent"),
        },
        "common_in_plane_lattice_angstrom": round(common_a, 6),
        "strained_film_in_plane_lattice_angstrom": round(common_a / film_repeats, 6),
        "film_in_plane_strain_percent": round(film_strain, 6),
        "interface_gap_angstrom": interface_gap,
        "vacuum_angstrom": vacuum,
        "top_vacuum_angstrom": top_vacuum,
        "bottom_vacuum_angstrom": bottom_vacuum,
        "slab_thickness_angstrom": round(substrate_c + interface_gap + film_c, 6),
        "substrate_thickness_angstrom": round(substrate_c, 6),
        "film_thickness_angstrom": round(film_c, 6),
        "epitaxy_targets": preflight_targets,
        "interface_scaffold_notes": [
            "Pre-relaxation scaffold for same-window visualization and diagnostics.",
            "The in-plane film lattice is strained to the 2x2 sapphire domain; review termination and relax before quantitative calculations.",
        ],
    }
    if target == "GaN":
        metadata["gan_reference_lattice_angstrom"] = film.lattice.a
    else:
        metadata["aln_reference_lattice_angstrom"] = film.lattice.a

    return ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": CrystalSpec(
                name=virtual_template_id,
                lattice=LatticeSpec(a=common_a, b=common_a, c=cell_c, alpha=90.0, beta=90.0, gamma=120.0),
                basis_atoms=atoms,
                operations=[],
            ).model_dump(mode="json"),
            "simulation": {
                "module": "CASTEP",
                "task": "GeometryOptimization",
                "functional": "PBE",
                "quality": "Medium",
                "cutoff_energy_ev": 600 if target == "GaN" else 560,
                "kpoint_separation": 0.05,
            },
            "outputs": {},
            "acceptance": {
                "max_warnings": 8,
                "require_convergence": False,
                "notes": [
                    "Pre-relaxation interface scaffold; preview and hot-load are allowed, but quantitative calculations require explicit relaxation review.",
                    "MaterialsScript lattice construction is preview-only; explicit execute materializes CIF for GUI hot-loading.",
                ],
            },
            "metadata": metadata,
        }
    )


def _match_sapphire_epitaxy_target(text: str) -> str | None:
    if (
        _material_alias_present(text, "GaN")
        or "gallium nitride" in text
        or "\u6c2e\u5316\u9553" in text
    ):
        return "GaN"
    if (
        _material_alias_present(text, "AlN")
        or "aluminum nitride" in text
        or "aluminium nitride" in text
        or "\u6c2e\u5316\u94dd" in text
    ):
        return "AlN"
    return None


def _mentions_sapphire_substrate(text: str) -> bool:
    return any(
        term in text
        for term in (
            "sapphire",
            "al2o3",
            "alpha alumina",
            "alpha-alumina",
            "alpha aluminum oxide",
            "alpha aluminium oxide",
            "\u84dd\u5b9d\u77f3",
            "\u6c27\u5316\u94dd",
            "\u521a\u7389",
        )
    )


def _looks_like_sapphire_epitaxy_context(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:on|onto|over|grown\s+on|epitaxy|epitaxial|substrate|interface|hetero(?:structure|junction))\b",
            text,
            flags=re.IGNORECASE,
        )
        or any(
            term in text
            for term in (
                "\u5916\u5ef6",
                "\u5916\u5ef6\u751f\u957f",
                "\u886c\u5e95",
                "\u57fa\u5e95",
                "\u886c\u5e95\u4e0a",
                "\u57fa\u5e95\u4e0a",
                "\u754c\u9762",
                "\u5f02\u8d28\u7ed3",
                "\u5f02\u8d28\u7ed3\u6784",
            )
        )
    )


def _looks_like_p_gan_gate_request(text: str) -> bool:
    """Return True when a request explicitly asks for a p-GaN gate/cap HEMT layer."""

    p_gan = bool(
        re.search(r"(?<![A-Za-z0-9])p\s*[-_ ]?\s*gan(?![A-Za-z0-9])", text, flags=re.IGNORECASE)
        or re.search(r"(?<![A-Za-z0-9])p\s*[-_ ]?\s*type\s+gan(?![A-Za-z0-9])", text, flags=re.IGNORECASE)
        or re.search(r"(?<![A-Za-z0-9])p\s*[-_ ]?\s*gallium\s+nitride(?![A-Za-z0-9])", text, flags=re.IGNORECASE)
        or re.search(r"p\s*[\u578b]\s*(?:gan|\u6c2e\u5316\u9553)", text, flags=re.IGNORECASE)
    )
    if not p_gan:
        return False
    return bool(
        re.search(r"\b(?:gate|cap|capping|gate\s+cap|cap\s+layer|gate\s+layer)\b", text, flags=re.IGNORECASE)
        or any(term in text for term in ("\u6805", "\u6805\u6781", "\u5e3d\u5c42", "\u76d6\u5e3d\u5c42", "\u6805\u5e3d"))
    )


def _apply_p_gan_gate_cap_request(text: str, spec: ModelSpec) -> tuple[ModelSpec, list[str]] | None:
    """Append a deterministic Mg-marked p-GaN cap to the AlGaN/GaN HEMT preflight template."""

    if not _looks_like_p_gan_gate_request(text):
        return None
    if not isinstance(spec.model, CrystalSpec):
        raise ValueError("p-GaN gate/cap construction requires a crystal model.")

    metadata = dict(spec.metadata or {})
    materials = _metadata_materials(metadata)
    if "GaN" not in materials or not any("Al" in _material_elements(material) and "Ga" in _material_elements(material) and "N" in _material_elements(material) for material in materials):
        raise ValueError("p-GaN gate/cap construction currently requires the AlGaN/GaN HEMT template.")
    if _superlattice_period_axis(metadata) != "c":
        raise ValueError("p-GaN gate/cap construction currently requires interface_axis='c'.")

    gan_templates = _p_gan_gate_cap_layer_templates(spec.model)
    if not gan_templates:
        raise ValueError("Could not identify deterministic GaN layer templates for the p-GaN cap.")
    layer_spacing = _quantum_well_layer_spacing(metadata, "GaN", gan_templates, spec.model.lattice.c)
    cap_layer_count, requested_thickness, actual_thickness = _p_gan_gate_cap_layer_count(
        text,
        layer_spacing=layer_spacing,
        motif_length=len(gan_templates),
    )

    old_c = float(spec.model.lattice.c)
    new_c = old_c + actual_thickness
    if new_c <= old_c:
        raise ValueError("p-GaN gate/cap construction produced a non-positive cap thickness.")

    atoms: list[BasisAtomSpec] = []
    for atom in spec.model.basis_atoms:
        atoms.append(
            BasisAtomSpec(
                id=atom.id,
                element=atom.element,
                fractional=[
                    _round_fractional(atom.fractional.x),
                    _round_fractional(atom.fractional.y),
                    _round_fractional(float(atom.fractional.z) * old_c / new_c),
                ],
            )
        )

    dopant_record: dict[str, Any] | None = None
    cap_layer_records: list[dict[str, Any]] = []
    for layer_offset in range(cap_layer_count):
        template_layer = gan_templates[layer_offset % len(gan_templates)]
        z_fractional = (old_c + layer_offset * layer_spacing) / new_c
        layer_atom_ids: list[str] = []
        for atom_index, template_atom in enumerate(template_layer, start=1):
            site_element = template_atom.element
            element = site_element
            prefix = site_element
            if dopant_record is None and site_element == "Ga":
                element = "Mg"
                prefix = "Mg"
            atom_id = f"{prefix}PGaN{layer_offset + 1}_{atom_index}"
            atoms.append(
                BasisAtomSpec(
                    id=atom_id,
                    element=element,
                    fractional=[
                        _round_fractional(template_atom.fractional.x),
                        _round_fractional(template_atom.fractional.y),
                        _round_fractional(z_fractional),
                    ],
                )
            )
            layer_atom_ids.append(atom_id)
            if element == "Mg" and dopant_record is None:
                dopant_record = _dopant_site_record(
                    atom_id=atom_id,
                    site_element="Ga",
                    dopant_element="Mg",
                    fractional=[
                        _round_fractional(template_atom.fractional.x),
                        _round_fractional(template_atom.fractional.y),
                        _round_fractional(z_fractional),
                    ],
                    auto_selected=True,
                    source="natural_language_p_gan_gate_cap",
                )
        cap_layer_records.append(
            {
                "layer_index": layer_offset + 1,
                "template_layer_index": (layer_offset % len(gan_templates)) + 1,
                "fractional_center": _round_fractional(z_fractional),
                "atom_ids": layer_atom_ids,
            }
        )

    if dopant_record is None:
        raise ValueError("Could not place an Mg acceptor marker in the p-GaN cap.")

    lattice = spec.model.lattice.model_copy(update={"c": _round_float(new_c)})
    p_gan_material = "p-GaN"
    updated_materials = [*materials]
    if p_gan_material not in updated_materials:
        updated_materials.append(p_gan_material)
    marker_map = dict(metadata.get("material_marker_map") or {})
    marker_map.update(
        {
            "Ga": "GaN",
            "Al;Ga": next((material for material in materials if "Al" in _material_elements(material) and "Ga" in _material_elements(material)), "Al0.25Ga0.75N"),
            "Ga;Mg": p_gan_material,
            "Mg;Ga": p_gan_material,
            "Mg": p_gan_material,
        }
    )
    electronic = dict(metadata.get("material_electronic_properties") or {})
    electronic.setdefault(p_gan_material, {"electron_affinity_ev": 4.1, "band_gap_ev": 3.4})
    dopant_sites = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", [])
        if isinstance(item, dict)
    ]
    dopant_sites.append(dopant_record)
    cap_record = {
        "material": p_gan_material,
        "role": "gate_cap",
        "axis": "c",
        "source": "natural_language_p_gan_gate_cap",
        "requested_thickness_angstrom": _round_float(requested_thickness) if requested_thickness is not None else None,
        "actual_thickness_angstrom": _round_float(actual_thickness),
        "thickness_error_angstrom": _round_float(actual_thickness - requested_thickness) if requested_thickness is not None else None,
        "layer_count": cap_layer_count,
        "layer_spacing_angstrom": _round_float(layer_spacing),
        "dopant_element": "Mg",
        "dopant_site_element": "Ga",
        "dopant_atom_id": dopant_record["atom_id"],
        "dopant_fraction_of_cap_cations": _round_float(1.0 / max(sum(1 for atom in atoms if atom.id.startswith(("GaPGaN", "MgPGaN"))), 1)),
        "layers": cap_layer_records,
        "notes": [
            "Preflight p-GaN cap generated by extending the deterministic AlGaN/GaN wurtzite stack along c.",
            "Mg marks p-type acceptor character; this is not a calibrated device doping concentration.",
        ],
    }
    updated_metadata = {
        **metadata,
        "materials": updated_materials,
        "material_marker_map": marker_map,
        "interface": "GaN/Al0.25Ga0.75N/p-GaN",
        "structure_family": "wurtzite p-GaN gate HEMT heterostructure",
        "p_gan_gate_cap": cap_record,
        "last_p_gan_gate_cap": cap_record,
        "p_gan_gate": True,
        "material_electronic_properties": electronic,
        "pgan_reference_lattice_angstrom": metadata.get("gan_reference_lattice_angstrom", 3.189),
        "polarization_2deg_barrier_materials": [
            material for material in materials if "Al" in _material_elements(material) and "Ga" in _material_elements(material) and "N" in _material_elements(material)
        ],
        "semiconductor_dopant_sites": dopant_sites,
        "last_semiconductor_dopant_site": dopant_record,
    }
    model = CrystalSpec(
        name=f"{spec.model.name}_p_gan_gate",
        lattice=LatticeSpec(
            a=lattice.a,
            b=lattice.b,
            c=lattice.c,
            alpha=lattice.alpha,
            beta=lattice.beta,
            gamma=lattice.gamma,
        ),
        basis_atoms=atoms,
        operations=spec.model.operations,
    )
    updated = spec.model_copy(update={"model": model, "metadata": updated_metadata})
    return ModelSpec.model_validate(updated.model_dump(mode="json")), [
        f"add_p_gan_gate_cap {cap_layer_count} layers {actual_thickness:g}A Mg:{dopant_record['atom_id']}"
    ]


def _p_gan_gate_cap_layer_count(text: str, *, layer_spacing: float, motif_length: int) -> tuple[int, float | None, float]:
    requested = _match_p_gan_gate_cap_thickness(text)
    motif = max(1, int(motif_length))
    if requested is None:
        layer_count = max(motif, 4)
        if layer_count % motif:
            layer_count += motif - (layer_count % motif)
        return layer_count, None, layer_count * layer_spacing
    if requested <= 0:
        raise ValueError("p-GaN gate/cap thickness must be positive.")
    candidates = []
    for count in range(motif, 241, motif):
        actual = count * layer_spacing
        candidates.append((abs(actual - requested), count, actual))
    if not candidates:
        raise ValueError("No motif-compatible p-GaN cap layer count is available.")
    _, layer_count, actual_thickness = min(candidates, key=lambda item: (item[0], item[1]))
    return layer_count, requested, actual_thickness


def _p_gan_gate_cap_layer_templates(model: CrystalSpec) -> list[list[BasisAtomSpec]]:
    layers = _sorted_crystal_layers(model)
    gan_elements = {"Ga", "N"}
    templates: list[list[BasisAtomSpec]] = []
    for layer in layers:
        elements = {atom.element for atom in layer}
        if elements and elements <= gan_elements:
            templates.append(layer)
            continue
        if templates:
            break
    return templates


def _match_p_gan_gate_cap_thickness(text: str) -> float | None:
    unit = r"(?P<unit>nm|nanometers?|angstroms?|ang|a|\u00e5|\u212b|\u7eb3\u7c73|\u57c3)"
    value = r"(?P<value>\d+(?:\.\d+)?)"
    patterns = [
        rf"(?:p\s*[-_ ]?\s*gan|p\s*[-_ ]?\s*type\s+gan|p\s*[-_ ]?\s*gallium\s+nitride)\s*(?:gate|cap|capping|cap\s+layer|gate\s+layer)?\s*(?:thickness|layer\s+thickness)?\s*(?:to|=|:|of|with)?\s*{value}\s*{unit}",
        rf"(?:gate|cap|capping|cap\s+layer|gate\s+layer)\s*(?:thickness|layer\s+thickness)?\s*(?:to|=|:|of|with)?\s*{value}\s*{unit}\s*(?:p\s*[-_ ]?\s*gan|p\s*[-_ ]?\s*type\s+gan)",
        rf"(?:p\s*[-_ ]?\s*gan|p\s*[\u578b]\s*(?:gan|\u6c2e\u5316\u9553))\s*(?:\u6805|\u6805\u6781|\u5e3d\u5c42|\u76d6\u5e3d\u5c42)?\s*(?:\u539a\u5ea6|\u539a)?\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value}\s*{unit}",
        rf"(?:\u6805|\u6805\u6781|\u5e3d\u5c42|\u76d6\u5e3d\u5c42)\s*(?:\u539a\u5ea6|\u539a)?\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value}\s*{unit}\s*(?:p\s*[-_ ]?\s*gan|p\s*[\u578b]\s*(?:gan|\u6c2e\u5316\u9553))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return _thickness_value_to_angstrom(float(match.group("value")), match.group("unit"))
    return None


def _p_gan_gate_cap_thickness_operation(text: str, current_spec: ModelSpec) -> dict[str, Any] | None:
    if not _is_p_gan_gate_cap_spec(current_spec):
        return None
    thickness = _match_p_gan_gate_cap_thickness(text)
    if thickness is None:
        thickness = _match_current_p_gan_gate_cap_thickness(text)
    if thickness is None:
        return None
    return {"type": "set_p_gan_gate_cap_thickness", "thickness_angstrom": thickness}


def _is_p_gan_gate_cap_spec(spec: ModelSpec) -> bool:
    return isinstance(spec.model, CrystalSpec) and isinstance((spec.metadata or {}).get("p_gan_gate_cap"), dict)


def _match_current_p_gan_gate_cap_thickness(text: str) -> float | None:
    if "thickness" not in text and "\u539a" not in text:
        return None
    value_unit = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>nm|nanometers?|angstroms?|ang|a|\u00e5|\u212b|\u7eb3\u7c73|\u57c3)?"
    target = r"gate\s+cap|cap\s+layer|gate\s+layer|gate|cap|capping"
    patterns = [
        rf"\b(?:set|make|change|adjust|update|use)\s+(?:the\s+)?(?:p\s*[-_ ]?\s*gan\s+)?(?:{target})\s+(?:layer\s+)?thickness\s*(?:to|=|:|of|with)?\s*{value_unit}\b",
        rf"\b(?:p\s*[-_ ]?\s*gan\s+)?(?:{target})\s+(?:layer\s+)?thickness\s*(?:to|=|:|of|with)?\s*{value_unit}\b",
        rf"(?:\u628a|\u5c06)?\s*(?:p\s*[-_ ]?\s*gan|p\s*[\u578b]\s*(?:gan|\u6c2e\u5316\u9553))?\s*(?:\u6805|\u6805\u6781|\u5e3d\u5c42|\u76d6\u5e3d\u5c42|\u6805\u5e3d)\s*(?:\u5c42)?\s*(?:\u539a\u5ea6|\u539a)\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value_unit}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return _thickness_value_to_angstrom(float(match.group("value")), match.groupdict().get("unit") or "angstrom")
    return None


def _quantum_well_thickness_operation(text: str, current_spec: ModelSpec) -> dict[str, Any] | None:
    if not _is_current_quantum_well_spec(current_spec):
        return None
    match = _match_current_quantum_well_thickness(text)
    if match is None:
        return None
    target_layer, thickness = match
    return {
        "type": "set_quantum_well_thickness",
        "target_layer": target_layer,
        "thickness_angstrom": thickness,
    }


def _is_current_quantum_well_spec(spec: ModelSpec) -> bool:
    if not isinstance(spec.model, CrystalSpec):
        return False
    metadata = dict(spec.metadata or {})
    if metadata.get("metal_gate_stack") or metadata.get("gate_stack"):
        return False
    materials = _metadata_materials(metadata)
    family = str(metadata.get("structure_family") or "").lower()
    return bool(
        metadata.get("interface")
        and len(materials) >= 2
        and (
            "heterostructure" in family
            or "quantum" in family
            or "superlattice" in family
            or "hemt" in family
            or "2deg" in family
        )
    )


def _match_current_quantum_well_thickness(text: str) -> tuple[str, float] | None:
    if "thickness" not in text and "\u539a" not in text:
        return None
    value_unit = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>nm|nanometers?|angstroms?|ang|a|\u00e5|\u212b|\u7eb3\u7c73|\u57c3)?"
    english_target = r"barrier|well|quantum\s+well|qw"
    patterns = [
        rf"\b(?:set|make|change|adjust|update|use)\s+(?:the\s+)?(?P<target>{english_target})\s+(?:layer\s+)?thickness\s*(?:to|=|:)?\s*{value_unit}\b",
        rf"\b(?P<target>{english_target})\s+(?:layer\s+)?thickness\s*(?:to|=|:)?\s*{value_unit}\b",
        rf"(?:\u628a|\u5c06)?\s*(?P<target>\u52bf\u5792|\u52bf\u5792\u5c42|\u5792\u5c42|\u963b\u6321\u5c42|\u91cf\u5b50\u9631|\u9631\u5c42|\u9631)\s*(?:\u539a\u5ea6|\u539a)?\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value_unit}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        target = _current_quantum_well_target_layer(match.group("target"))
        if target is None:
            continue
        thickness = _thickness_value_to_angstrom(float(match.group("value")), match.groupdict().get("unit") or "angstrom")
        if 0.0 < thickness <= 5000.0:
            return target, thickness
    return None


def _current_quantum_well_target_layer(target: str) -> str | None:
    compact = re.sub(r"\s+", "", target.strip().lower())
    if compact in {"barrier", "\u52bf\u5792", "\u52bf\u5792\u5c42", "\u5792\u5c42", "\u963b\u6321\u5c42"}:
        return "barrier"
    if compact in {"well", "quantumwell", "qw", "\u91cf\u5b50\u9631", "\u9631\u5c42", "\u9631"}:
        return "well"
    return None


_CJK_SEMICONDUCTOR_TEMPLATE_ALIASES: tuple[tuple[tuple[str, ...], str, str | None], ...] = (
    (
        (
            "\u7837\u5316\u9553/\u7837\u5316\u94dd",
            "\u7837\u5316\u9553-\u7837\u5316\u94dd",
            "\u7837\u5316\u9553\u7837\u5316\u94dd",
            "\u7837\u5316\u9553/\u7837\u5316\u94dd\u5f02\u8d28\u7ed3",
            "\u7837\u5316\u9553\u7837\u5316\u94dd\u5f02\u8d28\u7ed3",
            "\u7837\u5316\u9553/\u7837\u5316\u94dd\u8d85\u6676\u683c",
            "\u7837\u5316\u9553\u7837\u5316\u94dd\u8d85\u6676\u683c",
            "\u7837\u5316\u9553/\u7837\u5316\u94dd\u91cf\u5b50\u9631",
            "\u7837\u5316\u9553\u7837\u5316\u94dd\u91cf\u5b50\u9631",
            "\u94dd\u7837\u5316\u9553/\u7837\u5316\u9553",
            "\u94dd\u7837\u5316\u9553\u7837\u5316\u9553",
            "\u94dd\u9553\u7837/\u7837\u5316\u9553",
            "\u94dd\u9553\u7837\u7837\u5316\u9553",
        ),
        "gallium_arsenide_aluminum_arsenide_001_heterostructure",
        None,
    ),
    (
        (
            "\u7845\u9517",
            "\u9517\u7845",
            "\u7845/\u9517",
            "\u7845-\u9517",
            "\u7845\u9517\u5f02\u8d28\u7ed3",
            "\u7845\u9517\u5f02\u8d28\u7ed3\u6784",
            "\u7845\u9517\u754c\u9762",
            "\u7845\u9517\u8d85\u6676\u683c",
            "\u7845\u9517\u91cf\u5b50\u9631",
            "\u7845/\u9517\u5f02\u8d28\u7ed3",
            "\u7845/\u9517\u754c\u9762",
            "\u7845-\u9517\u8d85\u6676\u683c",
        ),
        "silicon_germanium_001_heterostructure",
        None,
    ),
    (
        (
            "\u94dd\u9553\u6c2e/\u6c2e\u5316\u9553",
            "\u94dd\u9553\u6c2e\u6c2e\u5316\u9553",
            "\u6c2e\u5316\u94dd\u9553/\u6c2e\u5316\u9553",
            "\u6c2e\u5316\u94dd\u9553\u6c2e\u5316\u9553",
        ),
        "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure",
        None,
    ),
    (
        ("\u6c2e\u5316\u94dd/\u6c2e\u5316\u9553", "\u6c2e\u5316\u94dd\u6c2e\u5316\u9553"),
        "aluminum_nitride_gallium_nitride_0001_heterostructure",
        None,
    ),
    (
        (
            "\u94df\u9553\u6c2e/\u6c2e\u5316\u9553",
            "\u94df\u9553\u6c2e\u6c2e\u5316\u9553",
            "\u6c2e\u5316\u94df\u9553/\u6c2e\u5316\u9553",
            "\u6c2e\u5316\u94df\u9553\u6c2e\u5316\u9553",
        ),
        "indium_gallium_nitride_gallium_nitride_0001_heterostructure",
        None,
    ),
    (
        (
            "InGaAs/InP",
            "InGaAs-InP",
            "InGaAs InP",
            "\u94df\u9553\u7837/\u78f7\u5316\u94df",
            "\u94df\u9553\u7837\u78f7\u5316\u94df",
            "\u94df\u9553\u7837/\u78f7\u5316\u94df\u91cf\u5b50\u9631",
            "\u94df\u9553\u7837\u78f7\u5316\u94df\u91cf\u5b50\u9631",
            "\u94df\u9553\u7837/\u78f7\u5316\u94df\u5f02\u8d28\u7ed3",
        ),
        "indium_gallium_arsenide_indium_phosphide_001_heterostructure",
        None,
    ),
    (
        (
            "InAs/GaSb",
            "InAs-GaSb",
            "InAs GaSb",
            "\u7837\u5316\u94df/\u9511\u5316\u9553",
            "\u7837\u5316\u94df-\u9511\u5316\u9553",
            "\u7837\u5316\u94df\u9511\u5316\u9553",
            "\u7837\u5316\u94df/\u9511\u5316\u9553\u91cf\u5b50\u9631",
            "\u7837\u5316\u94df\u9511\u5316\u9553\u91cf\u5b50\u9631",
            "\u7837\u5316\u94df/\u9511\u5316\u9553\u5f02\u8d28\u7ed3",
            "\u7837\u5316\u94df\u9511\u5316\u9553\u5f02\u8d28\u7ed3",
            "\u7837\u5316\u94df/\u9511\u5316\u9553\u8d85\u6676\u683c",
            "\u7837\u5316\u94df\u9511\u5316\u9553\u8d85\u6676\u683c",
            "\u7834\u7f3a\u5e26\u9699InAs/GaSb",
            "\u7834\u7f3a\u5e26\u9699\u7837\u5316\u94df\u9511\u5316\u9553",
        ),
        "indium_arsenide_gallium_antimonide_001_heterostructure",
        None,
    ),
    (("\u7837\u5316\u9553",), "gallium_arsenide_zincblende", "gallium_arsenide_001_slab"),
    (("\u78f7\u5316\u9553",), "gallium_phosphide_zincblende", None),
    (("\u9511\u5316\u9553",), "gallium_antimonide_zincblende", None),
    (("\u7837\u5316\u94dd",), "aluminum_arsenide_zincblende", None),
    (("\u78f7\u5316\u94dd",), "aluminum_phosphide_zincblende", None),
    (("\u9511\u5316\u94dd",), "aluminum_antimonide_zincblende", None),
    (("\u78f7\u5316\u94df",), "indium_phosphide_zincblende", None),
    (("\u7837\u5316\u94df",), "indium_arsenide_zincblende", None),
    (("\u9511\u5316\u94df",), "indium_antimonide_zincblende", None),
    (("\u786b\u5316\u9549",), "cadmium_sulfide_zincblende", None),
    (("\u7852\u5316\u9549",), "cadmium_selenide_zincblende", None),
    (("\u78b2\u5316\u9549",), "cadmium_telluride_zincblende", None),
    (("\u786b\u5316\u950c",), "zinc_sulfide_zincblende", None),
    (("\u7852\u5316\u950c",), "zinc_selenide_zincblende", None),
    (("\u78b2\u5316\u950c",), "zinc_telluride_zincblende", None),
    (
        ("\u6c27\u5316\u9553", "\u03b2-\u6c27\u5316\u9553", "\u03b2\u6c27\u5316\u9553", "ga2o3", "\u03b2-ga2o3"),
        "beta_gallium_oxide_monoclinic",
        "beta_gallium_oxide_010_slab",
    ),
    (
        ("\u84dd\u5b9d\u77f3", "\u84dd\u5b9d\u77f3\u886c\u5e95", "\u84dd\u5b9d\u77f3\u57fa\u5e95", "\u6c27\u5316\u94dd", "\u521a\u7389", "al2o3"),
        "alpha_alumina_sapphire_substrate",
        None,
    ),
    (("\u4e8c\u786b\u5316\u94bc",), "molybdenum_disulfide_2d_mos2_monolayer", None),
    (("\u4e8c\u786b\u5316\u94a8",), "tungsten_disulfide_2d_ws2_monolayer", None),
    (("\u4e8c\u7852\u5316\u94bc",), "molybdenum_diselenide_2d_mose2_monolayer", None),
    (("\u4e8c\u7852\u5316\u94a8",), "tungsten_diselenide_2d_wse2_monolayer", None),
    (
        (
            "\u516d\u65b9\u6c2e\u5316\u787c",
            "\u516d\u65b9\u6c2e\u5316\u787c\u5355\u5c42",
            "\u5355\u5c42\u6c2e\u5316\u787c",
            "\u4e8c\u7ef4\u6c2e\u5316\u787c",
        ),
        "hexagonal_boron_nitride_2d_hbn_monolayer",
        None,
    ),
    (
        (
            "\u9ed1\u78f7",
            "\u9ed1\u78f7\u5355\u5c42",
            "\u5355\u5c42\u9ed1\u78f7",
            "\u78f7\u70ef",
            "\u78f7\u70ef\u5355\u5c42",
            "\u5355\u5c42\u78f7\u70ef",
            "\u4e8c\u7ef4\u9ed1\u78f7",
            "\u4e8c\u7ef4\u78f7\u70ef",
        ),
        "black_phosphorus_2d_phosphorene_monolayer",
        None,
    ),
    (
        (
            "\u9499\u949b\u77ff",
            "\u9499\u949b\u77ff\u5438\u6536\u5c42",
            "\u9499\u949b\u77ff\u5149\u4f0f",
            "\u94c5\u7898\u9499\u949b\u77ff",
            "\u7532\u80fa\u94c5\u7898",
            "\u7532\u57fa\u94f5\u94c5\u7898",
            "\u7532\u80fa\u94c5\u7898\u9499\u949b\u77ff",
            "mapbi3",
            "ch3nh3pbi3",
        ),
        "methylammonium_lead_iodide_mapbi3_perovskite",
        None,
    ),
    (("\u9517\u6676\u4f53", "\u9517\u534a\u5bfc\u4f53", "\u9517\u8d85\u80de"), "germanium_diamond", None),
    (("\u91d1\u521a\u77f3", "\u91d1\u521a\u77f3\u6676\u4f53", "\u91d1\u521a\u77f3\u534a\u5bfc\u4f53"), "diamond_cubic", None),
)


_CJK_SEMICONDUCTOR_DISCOVERY_ALIASES: tuple[dict[str, Any], ...] = (
    {
        "terms": (
            "\u7845\u6676\u4f53",
            "\u7845\u534a\u5bfc\u4f53",
            "\u7845\u8d85\u80de",
            "\u6784\u5efa\u7845\u8868\u9762",
            "\u6784\u5efa\u7845\u6676\u4f53\u8868\u9762",
            "\u6784\u5efa\u7845\u534a\u5bfc\u4f53\u8868\u9762",
            "\u7845\u8868\u9762",
            "\u7845\u6676\u4f53\u8868\u9762",
            "\u7845\u534a\u5bfc\u4f53\u8868\u9762",
            "\u7845\u91d1\u521a\u77f3",
            "\u7845\u91d1\u521a\u77f3\u6676\u4f53",
            "\u91d1\u521a\u77f3\u7845",
            "\u91d1\u521a\u77f3\u7ed3\u6784\u7845",
            "\u91d1\u521a\u77f3\u7acb\u65b9\u7845",
            "n\u578b\u7845",
            "p\u578b\u7845",
        ),
        "template_id": "silicon_diamond",
        "surface_template_id": "silicon_100_slab",
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS),
        "notes": "Chinese discovery aliases for silicon bulk and Si(100) surface starts.",
    },
    {
        "terms": ("\u7845pn\u7ed3", "\u7845 pn \u7ed3", "\u7845p-n\u7ed3", "\u7845 p-n \u7ed3", "pn\u7ed3\u7845"),
        "template_id": "silicon_pn_junction",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for the deterministic silicon p-n junction start.",
    },
    {
        "terms": ("\u91d1\u521a\u77f3", "\u91d1\u521a\u77f3\u6676\u4f53", "\u91d1\u521a\u77f3\u534a\u5bfc\u4f53"),
        "template_id": "diamond_cubic",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for diamond-cubic carbon wide-bandgap semiconductor starts.",
    },
    {
        "terms": ("6h-sic", "6h sic", "6h\u78b3\u5316\u7845", "\u78b3\u5316\u78456h", "\u78b3\u5316\u7845 6h"),
        "template_id": "silicon_carbide_6h_hexagonal",
        "surface_template_id": SIC_6H_SI_FACE_SLAB_VIRTUAL_TEMPLATE_ID,
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS),
        "notes": "Chinese discovery aliases for 6H-SiC P63mc bulk and the explicit (0001) Si-face slab.",
    },
    {
        "terms": ("3c-sic", "3c sic", "3c\u78b3\u5316\u7845", "\u78b3\u5316\u7845", "\u78b3\u5316\u7845\u6676\u4f53", "\u78b3\u5316\u7845\u534a\u5bfc\u4f53", "\u7acb\u65b9\u78b3\u5316\u7845"),
        "template_id": "silicon_carbide_3c_zincblende",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Generic Chinese silicon-carbide requests route to the 3C-SiC zinc-blende start unless 4H is explicit.",
    },
    {
        "terms": ("4h-sic", "4h sic", "4h\u78b3\u5316\u7845", "\u516d\u65b9\u78b3\u5316\u7845"),
        "template_id": "silicon_carbide_4h_hexagonal",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for the explicit 4H-SiC hexagonal start.",
    },
    {
        "terms": (
            "\u516d\u65b9\u6c2e\u5316\u787c",
            "\u516d\u65b9\u6c2e\u5316\u787c\u5355\u5c42",
            "\u5355\u5c42\u6c2e\u5316\u787c",
            "\u4e8c\u7ef4\u6c2e\u5316\u787c",
            "hbn",
            "h-bn",
        ),
        "template_id": "hexagonal_boron_nitride_2d_hbn_monolayer",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for hexagonal boron nitride monolayer starts.",
    },
    {
        "terms": (
            "\u9ed1\u78f7",
            "\u9ed1\u78f7\u5355\u5c42",
            "\u5355\u5c42\u9ed1\u78f7",
            "\u78f7\u70ef",
            "\u78f7\u70ef\u5355\u5c42",
            "\u5355\u5c42\u78f7\u70ef",
            "\u4e8c\u7ef4\u9ed1\u78f7",
            "\u4e8c\u7ef4\u78f7\u70ef",
            "phosphorene",
        ),
        "template_id": "black_phosphorus_2d_phosphorene_monolayer",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for puckered black-phosphorus phosphorene monolayer starts.",
    },
    {
        "terms": (
            "\u9499\u949b\u77ff",
            "\u9499\u949b\u77ff\u5438\u6536\u5c42",
            "\u9499\u949b\u77ff\u5149\u4f0f",
            "\u94c5\u7898\u9499\u949b\u77ff",
            "\u7532\u80fa\u94c5\u7898",
            "\u7532\u57fa\u94f5\u94c5\u7898",
            "\u7532\u80fa\u94c5\u7898\u9499\u949b\u77ff",
            "mapbi3",
            "ch3nh3pbi3",
        ),
        "template_id": "methylammonium_lead_iodide_mapbi3_perovskite",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for the MAPbI3 hybrid halide perovskite solar-absorber start.",
    },
    {
        "terms": ("\u6c2e\u5316\u787c", "\u7acb\u65b9\u6c2e\u5316\u787c", "c-bn", "cbn"),
        "template_id": "boron_nitride_zincblende",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for cubic boron nitride.",
    },
    {
        "terms": ("\u6c27\u5316\u950c", "\u6c27\u5316\u950c\u6676\u4f53", "\u6c27\u5316\u950c\u534a\u5bfc\u4f53"),
        "template_id": "zinc_oxide_wurtzite",
        "surface_template_id": "zinc_oxide_0001_slab",
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS),
        "notes": "Chinese discovery aliases for ZnO bulk and ZnO(0001) surface starts.",
    },
    {
        "terms": (
            "\u6c27\u5316\u9553",
            "\u6c27\u5316\u9553\u6676\u4f53",
            "\u6c27\u5316\u9553\u534a\u5bfc\u4f53",
            "\u03b2-\u6c27\u5316\u9553",
            "\u03b2\u6c27\u5316\u9553",
            "ga2o3",
            "\u03b2-ga2o3",
        ),
        "template_id": "beta_gallium_oxide_monoclinic",
        "surface_template_id": "beta_gallium_oxide_010_slab",
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS) + ["(010)", "010"],
        "notes": "Chinese discovery aliases for monoclinic beta-Ga2O3 bulk and beta-Ga2O3(010) surface starts.",
    },
    {
        "terms": (
            "\u6c27\u5316\u9553\u8868\u9762",
            "\u6c27\u5316\u9553slab",
            "\u6c27\u5316\u9553 slab",
            "\u6c27\u5316\u9553(010)",
            "\u6c27\u5316\u9553 (010)",
            "\u03b2-\u6c27\u5316\u9553\u8868\u9762",
            "\u03b2\u6c27\u5316\u9553\u8868\u9762",
            "\u03b2-\u6c27\u5316\u9553(010)",
            "\u03b2\u6c27\u5316\u9553(010)",
            "\u03b2-ga2o3\u8868\u9762",
            "\u03b2-ga2o3(010)",
            "ga2o3 surface",
            "ga2o3 slab",
            "ga2o3 (010)",
            "beta-ga2o3 surface",
            "beta-ga2o3 (010)",
        ),
        "template_id": "beta_gallium_oxide_010_slab",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Direct aliases for the deterministic beta-Ga2O3(010) slab surface template.",
    },
    {
        "terms": (
            "\u84dd\u5b9d\u77f3",
            "\u84dd\u5b9d\u77f3\u886c\u5e95",
            "\u84dd\u5b9d\u77f3\u57fa\u5e95",
            "\u6c27\u5316\u94dd",
            "\u6c27\u5316\u94dd\u886c\u5e95",
            "\u03b1-\u6c27\u5316\u94dd",
            "\u03b1\u6c27\u5316\u94dd",
            "\u521a\u7389",
            "\u521a\u7389\u6c27\u5316\u94dd",
            "al2o3",
            "\u03b1-al2o3",
        ),
        "template_id": "alpha_alumina_sapphire_substrate",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for alpha-Al2O3 sapphire substrate starts in semiconductor workflows.",
    },
    {
        "terms": ("\u6c2e\u5316\u9553", "\u6c2e\u5316\u9553\u6676\u4f53", "\u6c2e\u5316\u9553\u534a\u5bfc\u4f53"),
        "template_id": "gallium_nitride_wurtzite",
        "surface_template_id": "gallium_nitride_0001_slab",
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS),
        "notes": "Chinese discovery aliases for GaN bulk and GaN(0001) surface starts.",
    },
    {
        "terms": ("\u6c2e\u5316\u94dd", "\u6c2e\u5316\u94dd\u6676\u4f53", "\u6c2e\u5316\u94dd\u534a\u5bfc\u4f53"),
        "template_id": "aluminum_nitride_wurtzite",
        "surface_template_id": "aluminum_nitride_0001_slab",
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS),
        "notes": "Chinese discovery aliases for AlN bulk and AlN(0001) surface starts.",
    },
    {
        "terms": ("\u6c2e\u5316\u94df", "\u6c2e\u5316\u94df\u6676\u4f53", "\u6c2e\u5316\u94df\u534a\u5bfc\u4f53"),
        "template_id": "indium_nitride_wurtzite",
        "surface_template_id": "indium_nitride_0001_slab",
        "surface_intent_terms": list(_CJK_SURFACE_INTENT_TERMS),
        "notes": "Chinese discovery aliases for InN bulk and InN(0001) surface starts.",
    },
    {
        "terms": ("\u7845\u6c27\u754c\u9762", "\u7845\u6c27\u5316\u5c42", "\u7845/\u4e8c\u6c27\u5316\u7845", "\u7845-\u4e8c\u6c27\u5316\u7845", "\u6805\u6c27\u754c\u9762"),
        "template_id": "silicon_silicon_dioxide_100_interface",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for the deterministic Si/SiO2 interface start.",
    },
    {
        "terms": ("sic mos", "4h-sic mos", "\u78b3\u5316\u7845mos", "\u78b3\u5316\u7845 mos", "\u78b3\u5316\u7845\u6805\u6c27", "\u94dd/\u4e8c\u6c27\u5316\u7845/\u78b3\u5316\u7845", "\u94dd-\u4e8c\u6c27\u5316\u7845-\u78b3\u5316\u7845"),
        "template_id": "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "intent_terms": ("mos", "\u91d1\u6c27\u534a", "\u7535\u5bb9", "\u6805\u6c27", "\u6805\u4ecb\u8d28"),
        "notes": "Chinese discovery aliases for the Al/SiO2/4H-SiC MOS capacitor start.",
    },
    {
        "terms": ("\u6c27\u5316\u94ea", "\u4e8c\u6c27\u5316\u94ea", "\u9ad8k", "\u9ad8-k", "\u9ad8\u4ecb\u7535", "\u9ad8\u4ecb\u7535\u5e38\u6570"),
        "template_id": "titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "intent_terms": ("\u6805", "mos", "\u91d1\u6c27\u534a", "\u7535\u5bb9", "\u4ecb\u8d28"),
        "notes": "Chinese discovery aliases for the TiN/HfO2/Si high-k MOS capacitor start.",
    },
    {
        "terms": (
            "\u6805\u5806",
            "\u6805\u6781\u5806\u53e0",
            "\u91d1\u6c27\u534a\u7535\u5bb9",
            "\u7845mos\u7535\u5bb9",
            "\u7845 mos \u7535\u5bb9",
            "mos\u7535\u5bb9",
        ),
        "template_id": "aluminum_silicon_dioxide_silicon_mos_capacitor",
        "surface_template_id": None,
        "surface_intent_terms": [],
        "notes": "Chinese discovery aliases for the Al/SiO2/Si MOS capacitor start.",
    },
)


def _surface_template_selection_text(text: str) -> str:
    """Remove plane labels that belong to view diagnostics, not model orientation."""

    diagnostic_before = (
        "export",
        "view",
        "look",
        "normal",
        "project",
        "observe",
        "\u5bfc\u51fa",
        "\u89c6\u56fe",
        "\u89c6\u89d2",
        "\u6cd5\u5411",
        "\u6295\u5f71",
        "\u89c2\u5bdf",
        "\u67e5\u770b",
        "\u6cbf",
        "\u4ece",
    )
    diagnostic_after = (
        "crystal plane",
        "crystallographic plane",
        "plane normal",
        "normal view",
        "view parameter",
        "projection",
        "\u6676\u9762",
        "\u6cd5\u5411",
        "\u89c6\u56fe",
        "\u89c6\u89d2",
        "\u6295\u5f71",
    )

    def replace_label(match: re.Match[str]) -> str:
        before = text[max(0, match.start() - 48) : match.start()].lower()
        after = text[match.end() : min(len(text), match.end() + 48)].lower()
        if any(marker in before for marker in diagnostic_before) or any(
            marker in after for marker in diagnostic_after
        ):
            return " "
        return match.group(0)

    selection_text = re.sub(r"\(\s*[-0-9,\s]{3,12}\s*\)", replace_label, text)
    selection_text = re.sub(r"\bsurface[-\s]+normal\b", " normal ", selection_text, flags=re.IGNORECASE)
    selection_text = selection_text.replace("\u8868\u9762\u6cd5\u5411", "\u6cd5\u5411").replace("\u8868\u9762\u89c6\u56fe", "\u89c6\u56fe")
    return selection_text


def _match_explicit_template_id(text: str) -> str | None:
    """Return a deterministic template for requests where generic terms would be ambiguous."""

    if not text:
        return None
    selection_text = _surface_template_selection_text(text)
    lowered = selection_text.lower()
    compact = re.sub(r"\s+", "", lowered)
    if _mentions_sic_6h(selection_text):
        return "silicon_carbide_6h_hexagonal"
    beta_ga2o3_terms = (
        "ga2o3",
        "beta ga2o3",
        "beta-ga2o3",
        "\u03b2 ga2o3",
        "\u03b2-ga2o3",
        "gallium oxide",
        "beta gallium oxide",
        "\u6c27\u5316\u9553",
        "\u03b2-\u6c27\u5316\u9553",
        "\u03b2\u6c27\u5316\u9553",
    )
    beta_surface_terms = (
        "surface",
        "slab",
        "(010)",
        "\u8868\u9762",
    )
    if any(term in lowered or term in compact for term in beta_ga2o3_terms) and any(
        term in lowered or term in compact for term in beta_surface_terms
    ):
        return "beta_gallium_oxide_010_slab"
    return _match_cjk_template_id(text)


def _match_cjk_template_id(text: str) -> str | None:
    """Return deterministic templates for key Chinese semiconductor requests."""

    if not text:
        return None
    surface_selection_text = _surface_template_selection_text(text)
    compact_text = re.sub(r"\s+", "", text)
    has_surface_intent = any(term in surface_selection_text for term in _CJK_SURFACE_INTENT_TERMS)
    if _looks_like_semiconductor_pn_junction_text(text) and _silicon_pn_junction_template_context_ok(text):
        return "silicon_pn_junction"
    sic_mos_intent = any(
        term in text for term in ("mos", "\u91d1\u6c27\u534a", "\u7535\u5bb9", "\u6805\u6c27", "\u6805\u4ecb\u8d28")
    )
    if not sic_mos_intent and _mentions_sic_6h(text):
        return "silicon_carbide_6h_hexagonal"
    if not sic_mos_intent and (
        any(term in compact_text for term in ("4h\u78b3\u5316\u7845", "4h-sic", "4hsic"))
        or any(term in text for term in ("\u516d\u65b9\u78b3\u5316\u7845",))
    ):
        return "silicon_carbide_4h_hexagonal"
    if any(
        term in text
        for term in (
            "\u6c27\u5316\u94ea",
            "\u4e8c\u6c27\u5316\u94ea",
            "\u9ad8k",
            "\u9ad8-k",
            "\u9ad8\u4ecb\u7535",
            "\u9ad8\u4ecb\u7535\u5e38\u6570",
        )
    ) and any(term in text for term in ("\u6805", "mos", "\u91d1\u6c27\u534a", "\u7535\u5bb9", "\u4ecb\u8d28")):
        return "titanium_nitride_hafnium_dioxide_silicon_high_k_mos_capacitor"
    if any(
        term in text
        for term in (
            "\u7845\u6c27\u754c\u9762",
            "\u7845\u6c27\u5316\u5c42",
            "\u7845\u6c27\u5316\u5c42\u754c\u9762",
            "\u7845\u6c27\u5316\u7269\u754c\u9762",
            "\u7845\u4e8c\u6c27\u5316\u7845",
            "\u7845/\u4e8c\u6c27\u5316\u7845",
            "\u7845-\u4e8c\u6c27\u5316\u7845",
            "\u7845\u6805\u6c27",
            "\u7845\u6805\u6c27\u5c42",
            "\u7845\u6805\u4ecb\u8d28",
            "\u6805\u6c27\u754c\u9762",
        )
    ):
        return "silicon_silicon_dioxide_100_interface"
    if any(term in text for term in ("4h-sic", "4h sic", "sic", "\u78b3\u5316\u7845")) and any(
        term in text for term in ("mos", "\u91d1\u6c27\u534a", "\u7535\u5bb9", "\u6805\u6c27", "\u6805\u4ecb\u8d28")
    ):
        return "aluminum_silicon_dioxide_silicon_carbide_4h_mos_capacitor"
    if any(
        term in compact_text
        for term in (
            "\u7845mos\u7535\u5bb9",
            "\u7845mos\u6805",
            "\u7845\u91d1\u6c27\u534a\u7535\u5bb9",
            "mos\u7535\u5bb9",
        )
    ) or any(term in text for term in ("\u6805\u5806", "\u6805\u6781\u5806\u53e0", "\u91d1\u6c27\u534a\u7535\u5bb9")):
        return "aluminum_silicon_dioxide_silicon_mos_capacitor"
    if any(
        term in text
        for term in (
            "\u7845\u91d1\u521a\u77f3",
            "\u7845\u91d1\u521a\u77f3\u6676\u4f53",
            "\u91d1\u521a\u77f3\u7845",
            "\u91d1\u521a\u77f3\u7ed3\u6784\u7845",
            "\u91d1\u521a\u77f3\u7acb\u65b9\u7845",
        )
    ):
        return "silicon_100_slab" if has_surface_intent else "silicon_diamond"
    for terms, template_id, surface_template_id in _CJK_SEMICONDUCTOR_TEMPLATE_ALIASES:
        if any(term in text for term in terms):
            return surface_template_id if has_surface_intent and surface_template_id else template_id
    if any(term in text for term in ("\u6c34\u5206\u5b50", "\u6c34")):
        return "water"
    if "\u82ef" in text:
        return "benzene"
    if any(
        term in surface_selection_text
        for term in ("\u7845(100)", "\u7845 100", "\u7845\u8868\u9762", "\u7845\u8868\u9762slab", "si(100)\u8868\u9762")
    ):
        return "silicon_100_slab"
    if any(term in text for term in ("\u78b3\u5316\u7845", "\u93cb\u52eb\u7f13\u7eb0\u51b2\u5bf2\u7ead")) or "纰冲寲纭" in text:
        return "silicon_carbide_3c_zincblende"
    if any(
        term in text
        for term in ("\u516d\u65b9\u6c2e\u5316\u787c", "\u5355\u5c42\u6c2e\u5316\u787c", "\u4e8c\u7ef4\u6c2e\u5316\u787c")
    ):
        return "hexagonal_boron_nitride_2d_hbn_monolayer"
    if any(term in text for term in ("\u6c2e\u5316\u787c", "\u7acb\u65b9\u6c2e\u5316\u787c")) or "绔嬫柟姘" in text:
        return "boron_nitride_zincblende"
    if any(term in text for term in ("\u78b2\u5316\u9549", "\u78b2\u5316\u954d")) or "纰插寲闀" in text:
        return "cadmium_telluride_zincblende"
    if "\u6c27\u5316\u950c" in text or "姘у寲閿" in text:
        return "zinc_oxide_0001_slab" if has_surface_intent else "zinc_oxide_wurtzite"
    if any(term in text for term in ("hemt", "\u4e8c\u7ef4\u7535\u5b50\u6c14", "\u9ad8\u7535\u5b50\u8fc1\u79fb\u7387\u6676\u4f53\u7ba1")) and any(
        term in text for term in ("gan", "\u6c2e\u5316\u9553")
    ):
        if any(term in text for term in ("\u6c2e\u5316\u94dd", "aln")):
            return "aluminum_nitride_gallium_nitride_0001_heterostructure"
        if any(term in text for term in ("\u6c2e\u5316\u94df\u9553", "\u94df\u9553\u6c2e", "ingan")):
            return "indium_gallium_nitride_gallium_nitride_0001_heterostructure"
        return "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure"
    if "\u6c2e\u5316\u94dd" in text or "姘寲閾" in text:
        return "aluminum_nitride_0001_slab" if has_surface_intent else "aluminum_nitride_wurtzite"
    if "\u6c2e\u5316\u9553" in text or "姘寲闀" in text:
        return "gallium_nitride_0001_slab" if has_surface_intent else "gallium_nitride_wurtzite"
    if "\u6c2e\u5316\u94df" in text:
        return "indium_nitride_0001_slab" if has_surface_intent else "indium_nitride_wurtzite"
    if any(
        term in text
        for term in (
            "\u7845\u6676\u4f53",
            "\u7845\u8d85\u80de",
            "\u7845\u534a\u5bfc\u4f53",
            "\u7845\u91d1\u521a\u77f3",
            "\u7845\u91d1\u521a\u77f3\u6676\u4f53",
            "\u91d1\u521a\u77f3\u7845",
            "\u91d1\u521a\u77f3\u7ed3\u6784\u7845",
            "\u91d1\u521a\u77f3\u7acb\u65b9\u7845",
            "n\u578b\u7845",
            "p\u578b\u7845",
        )
    ):
        return "silicon_diamond"
    if any(term in text for term in ("\u91d1\u521a\u77f3", "\u91d1\u521a\u77f3\u6676\u4f53", "\u91d1\u521a\u77f3\u534a\u5bfc\u4f53")):
        return "diamond_cubic"
    return None


def _infer_formula_alloy_template(text: str, *, user_request: str, project_id: str | None) -> NaturalLanguagePlan | None:
    match = _match_formula_alloy_request(text)
    if match is None:
        return None
    spec = _load_example(str(match["example"]))
    template_id = str(match["template_id"])
    chosen_project_id = project_id or _project_id(template_id, user_request)
    metadata = {
        **dict(spec.get("metadata") or {}),
        "nl_template": template_id,
        "nl_source": "formula_alloy_template",
        "nl_user_request": user_request,
        "formula_alloy_request": {
            "formula": match["formula"],
            "host_element": match["host_element"],
            "alloy_element": match["alloy_element"],
            "requested_fraction": match["fraction"],
            "source": "natural_language_formula_alloy",
        },
    }
    model_spec = ModelSpec.model_validate({**spec, "project_id": chosen_project_id, "revision": 0, "metadata": metadata})
    applied: list[str] = []
    supercell_match = _match_make_supercell(text)
    if supercell_match is not None:
        try:
            model_spec, applied = apply_semantic_patch(
                model_spec,
                SemanticPatch(
                    project_id=model_spec.project_id,
                    base_revision=model_spec.revision,
                    operations=[{"type": "make_supercell", "matrix": list(supercell_match)}],
                ),
            )
        except ValueError as exc:
            return NaturalLanguagePlan(
                kind="unsupported",
                payload=None,
                confidence=0.0,
                template_id=None,
                notes=[
                    "A semiconductor formula-alloy request matched, but the requested supercell could not be applied safely.",
                    str(exc),
                ],
            )
    try:
        patched, diff = apply_semantic_patch(
            model_spec,
            SemanticPatch(
                project_id=model_spec.project_id,
                base_revision=model_spec.revision,
                operations=_crystal_alloy_operations(
                    model_spec,
                    str(match["host_element"]),
                    str(match["alloy_element"]),
                    float(match["fraction"]),
                ),
            ),
        )
        composite = _apply_new_crystal_composite_operations(
            user_request,
            patched,
            skip_supercell=True,
            skip_alloy=True,
        )
        if isinstance(composite, NaturalLanguagePlan):
            return composite
        if composite is not None:
            patched, composite_diff = composite
            diff.extend(composite_diff)
    except ValueError as exc:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                "A semiconductor formula-alloy request matched, but it could not be applied safely.",
                str(exc),
                "Use an explicit supercell if the requested composition needs enough host sites.",
            ],
        )

    metadata = {
        **dict(patched.metadata or {}),
        "nl_composite_operations": [*applied, *diff],
    }
    patched = patched.model_copy(update={"revision": 0, "metadata": metadata})
    return NaturalLanguagePlan(
        kind="spec",
        payload=patched.model_dump(mode="json"),
        confidence=0.83,
        template_id=template_id,
        notes=[
            f"Generated deterministic semiconductor alloy from formula {match['formula']}.",
            "Applied formula-derived alloy fraction using the existing structured alloy patch path.",
        ],
    )


_FORMULA_FRACTION_PATTERN = r"0?\.\d+|1(?:\.0+)?"
_FORMULA_ASCII_START = r"(?<![A-Za-z0-9])"
_FORMULA_ASCII_END = r"(?![A-Za-z0-9])"


def _match_formula_alloy_request(text: str) -> dict[str, Any] | None:
    halide_perovskite = _match_halide_perovskite_formula_alloy(text)
    if halide_perovskite is not None:
        return halide_perovskite
    sige = _match_sige_formula_alloy(text)
    if sige is not None:
        return sige
    cjk_named = _match_cjk_named_formula_alloy(text)
    if cjk_named is not None:
        return cjk_named
    algaas = _match_algaas_formula_alloy(text)
    if algaas is not None:
        return algaas
    ingaas = _match_ingaas_formula_alloy(text)
    if ingaas is not None:
        return ingaas
    ii_vi = _match_ii_vi_formula_alloy(text)
    if ii_vi is not None:
        return ii_vi
    algan = _match_algan_formula_alloy(text)
    if algan is not None:
        return algan
    ingan = _match_ingan_formula_alloy(text)
    if ingan is not None:
        return ingan
    return None


def _match_halide_perovskite_formula_alloy(text: str) -> dict[str, Any] | None:
    halide = r"I|Br|Cl|F"
    formula_prefix = r"(?:ma\s*pb|mapb|ch3\s*nh3\s*pb|ch3nh3pb)"

    replacement_match = _match_halide_perovskite_replacement_alloy(text)
    if replacement_match is not None:
        return replacement_match

    x_match = re.search(
        rf"{_FORMULA_ASCII_START}{formula_prefix}\s*\(?\s*"
        rf"(?P<host>{halide})\s*(?:1\s*-\s*x|1-x)\s*"
        rf"(?P<alloy>{halide})\s*x\s*\)?\s*3?"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        host = _normalize_formula_element(x_match.group("host"))
        alloy = _normalize_formula_element(x_match.group("alloy"))
        fraction = _optional_formula_fraction(x_match.group("x"))
        if host == "I" and alloy in {"F", "Cl", "Br"} and fraction is not None and 0.0 < fraction < 1.0:
            return {
                "template_id": "crystal_formula_alloy",
                "example": "methylammonium_lead_iodide_mapbi3_perovskite_spec.json",
                "formula": f"MAPb(I{1.0 - fraction:g}{alloy}{fraction:g})3",
                "host_element": "I",
                "alloy_element": alloy,
                "fraction": fraction,
            }

    fraction_pattern = re.compile(
        rf"{_FORMULA_ASCII_START}{formula_prefix}\s*\(\s*"
        rf"(?P<first>{halide})\s*(?P<first_fraction>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"(?P<second>{halide})\s*(?P<second_fraction>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"\)\s*3{_FORMULA_ASCII_END}",
        flags=re.IGNORECASE,
    )
    match = fraction_pattern.search(text)
    if match is None:
        return None
    first = _normalize_formula_element(match.group("first"))
    second = _normalize_formula_element(match.group("second"))
    first_fraction = _optional_formula_fraction(match.group("first_fraction"))
    second_fraction = _optional_formula_fraction(match.group("second_fraction"))
    if (
        first is None
        or second is None
        or first == second
        or first_fraction is None
        or second_fraction is None
        or abs((first_fraction + second_fraction) - 1.0) > 0.02
    ):
        return None
    fractions = {first: first_fraction, second: second_fraction}
    alloy_candidates = [element for element in (first, second) if element in {"F", "Cl", "Br"}]
    if "I" not in fractions or len(alloy_candidates) != 1:
        return None
    alloy = alloy_candidates[0]
    fraction = fractions[alloy]
    if not 0.0 < fraction < 1.0:
        return None
    return {
        "template_id": "crystal_formula_alloy",
        "example": "methylammonium_lead_iodide_mapbi3_perovskite_spec.json",
        "formula": f"MAPb(I{fractions['I']:g}{alloy}{fraction:g})3",
        "host_element": "I",
        "alloy_element": alloy,
        "fraction": fraction,
    }


def _match_halide_perovskite_replacement_alloy(text: str) -> dict[str, Any] | None:
    if not re.search(
        r"(?:ma\s*pb\s*i\s*3|mapb\s*i\s*3|ch3\s*nh3\s*pb\s*i\s*3|ch3nh3pb\s*i\s*3)",
        text,
        flags=re.IGNORECASE,
    ):
        return None
    match = _match_crystal_alloy_fraction(text)
    if match is None:
        return None
    host, alloy, fraction = match
    if host != "I" or alloy not in {"F", "Cl", "Br"} or not 0.0 < fraction < 1.0:
        return None
    return {
        "template_id": "crystal_formula_alloy",
        "example": "methylammonium_lead_iodide_mapbi3_perovskite_spec.json",
        "formula": f"MAPb(I{1.0 - fraction:g}{alloy}{fraction:g})3",
        "host_element": "I",
        "alloy_element": alloy,
        "fraction": fraction,
    }


def _match_sige_formula_alloy(text: str) -> dict[str, Any] | None:
    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?:si\s*(?:1\s*-\s*x|1-x)\s*ge\s*x|sige){_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        ge_fraction = _optional_formula_fraction(x_match.group("x"))
        if ge_fraction is not None and 0.0 < ge_fraction < 1.0:
            si_fraction = 1.0 - ge_fraction
            if si_fraction >= ge_fraction:
                host, alloy, fraction, example = "Si", "Ge", ge_fraction, "silicon_diamond_spec.json"
            else:
                host, alloy, fraction, example = "Ge", "Si", si_fraction, "germanium_diamond_spec.json"
            return {
                "template_id": "crystal_formula_alloy",
                "example": example,
                "formula": f"Si{si_fraction:g}Ge{ge_fraction:g}",
                "host_element": host,
                "alloy_element": alloy,
                "fraction": fraction,
            }

    pattern = re.compile(
        rf"{_FORMULA_ASCII_START}si\s*(?P<si>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"ge\s*(?P<ge>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}|"
        rf"{_FORMULA_ASCII_START}ge\s*(?P<ge_first>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"si\s*(?P<si_second>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        flags=re.IGNORECASE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    si_fraction = _optional_formula_fraction(match.group("si") or match.group("si_second"))
    ge_fraction = _optional_formula_fraction(match.group("ge") or match.group("ge_first"))
    if si_fraction is None or ge_fraction is None or abs((si_fraction + ge_fraction) - 1.0) > 0.02:
        return None
    if si_fraction >= ge_fraction:
        host, alloy, fraction, example = "Si", "Ge", ge_fraction, "silicon_diamond_spec.json"
    else:
        host, alloy, fraction, example = "Ge", "Si", si_fraction, "germanium_diamond_spec.json"
    if not 0.0 < fraction < 1.0:
        return None
    return {
        "template_id": "crystal_formula_alloy",
        "example": example,
        "formula": f"Si{si_fraction:g}Ge{ge_fraction:g}",
        "host_element": host,
        "alloy_element": alloy,
        "fraction": fraction,
    }


def _match_cjk_named_formula_alloy(text: str) -> dict[str, Any] | None:
    named_matchers = (
        (
            ("\u7845\u9517", "\u9517\u7845"),
            ("\u9517", "ge"),
            _sige_formula_result,
        ),
        (
            ("\u94dd\u9553\u7837",),
            ("\u94dd", "al"),
            _algaas_formula_result,
        ),
        (
            ("\u94df\u9553\u7837",),
            ("\u94df", "in"),
            _ingaas_formula_result,
        ),
        (
            ("\u94dd\u9553\u6c2e",),
            ("\u94dd", "al"),
            _algan_formula_result,
        ),
        (
            ("\u94df\u9553\u6c2e",),
            ("\u94df", "in"),
            _ingan_formula_result,
        ),
        (
            ("\u9549\u950c\u78b2",),
            ("\u9549", "cd"),
            _cdznte_cation_formula_result,
        ),
    )
    for material_terms, fraction_terms, result_factory in named_matchers:
        if not any(_contains_formula_alloy_term(text, term) for term in material_terms):
            continue
        fraction = _match_cjk_named_formula_fraction(text, fraction_terms)
        if fraction is None or not 0.0 < fraction < 1.0:
            continue
        return result_factory(fraction)
    return None


def _contains_formula_alloy_term(text: str, term: str) -> bool:
    if re.search(r"[A-Za-z0-9]", term):
        return bool(re.search(rf"{_FORMULA_ASCII_START}{re.escape(term)}{_FORMULA_ASCII_END}", text, flags=re.IGNORECASE))
    return term in text


def _match_cjk_named_formula_fraction(text: str, element_terms: tuple[str, ...]) -> float | None:
    separators = r"(?:=|:|\uff1d|\uff1a|\u4e3a)?"
    labels = r"(?:\u542b\u91cf|\u7ec4\u5206|\u6bd4\u4f8b|fraction|content)?"
    for term in element_terms:
        term_pattern = re.escape(term)
        match = re.search(
            rf"{term_pattern}\s*{labels}\s*{separators}\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
            text,
            flags=re.IGNORECASE,
        )
        if match is not None:
            return _optional_formula_fraction(match.group("x"))
    x_match = re.search(
        rf"{_FORMULA_ASCII_START}x\s*{separators}\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        return _optional_formula_fraction(x_match.group("x"))
    return None


def _match_formula_content_fraction(text: str, element_terms: tuple[str, ...]) -> float | None:
    labels = (
        "content",
        "composition",
        "fraction",
        "mole fraction",
        "molar fraction",
        "ratio",
        "\u542b\u91cf",
        "\u7ec4\u5206",
        "\u6bd4\u4f8b",
        "\u6469\u5c14\u5206\u6570",
    )
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    separators = r"(?:=|:|\uff1d|\uff1a|\u4e3a)?"
    percent_pattern = r"(?P<percent>\d+(?:\.\d+)?)\s*[%\uff05]"
    fraction_pattern = rf"(?P<fraction>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}"
    for term in element_terms:
        term_pattern = re.escape(term)
        patterns = (
            rf"{term_pattern}\s*(?:{label_pattern})\s*{separators}\s*(?:{fraction_pattern}|{percent_pattern})",
            rf"(?:{label_pattern})\s*(?:of\s+)?{term_pattern}\s*{separators}\s*(?:{fraction_pattern}|{percent_pattern})",
            rf"{_FORMULA_ASCII_START}x\s*[_-]?\s*{term_pattern}\s*{separators}\s*(?:{fraction_pattern}|{percent_pattern})",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            if match.groupdict().get("fraction") is not None:
                fraction = _optional_formula_fraction(match.group("fraction"))
            else:
                fraction = _optional_formula_fraction(str(float(match.group("percent")) / 100.0))
            if fraction is not None and 0.0 < fraction < 1.0:
                return fraction
    return None


def _formula_context_present(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_formula_alloy_term(text, term) if re.search(r"[A-Za-z0-9]", term) else term in text for term in terms)


def _sige_formula_result(ge_fraction: float) -> dict[str, Any] | None:
    si_fraction = 1.0 - ge_fraction
    if si_fraction >= ge_fraction:
        host, alloy, fraction, example = "Si", "Ge", ge_fraction, "silicon_diamond_spec.json"
    else:
        host, alloy, fraction, example = "Ge", "Si", si_fraction, "germanium_diamond_spec.json"
    if not 0.0 < fraction < 1.0:
        return None
    return {
        "template_id": "crystal_formula_alloy",
        "example": example,
        "formula": f"Si{si_fraction:g}Ge{ge_fraction:g}",
        "host_element": host,
        "alloy_element": alloy,
        "fraction": fraction,
    }


def _algaas_formula_result(al_fraction: float) -> dict[str, Any] | None:
    ga_fraction = 1.0 - al_fraction
    return {
        "template_id": "crystal_formula_alloy",
        "example": "gallium_arsenide_zincblende_spec.json",
        "formula": f"Al{al_fraction:g}Ga{ga_fraction:g}As",
        "host_element": "Ga",
        "alloy_element": "Al",
        "fraction": al_fraction,
    }


def _ingaas_formula_result(in_fraction: float) -> dict[str, Any] | None:
    ga_fraction = 1.0 - in_fraction
    return {
        "template_id": "crystal_formula_alloy",
        "example": "gallium_arsenide_zincblende_spec.json",
        "formula": f"In{in_fraction:g}Ga{ga_fraction:g}As",
        "host_element": "Ga",
        "alloy_element": "In",
        "fraction": in_fraction,
    }


def _algan_formula_result(al_fraction: float) -> dict[str, Any] | None:
    ga_fraction = 1.0 - al_fraction
    return {
        "template_id": "crystal_formula_alloy",
        "example": "gallium_nitride_wurtzite_spec.json",
        "formula": f"Al{al_fraction:g}Ga{ga_fraction:g}N",
        "host_element": "Ga",
        "alloy_element": "Al",
        "fraction": al_fraction,
    }


def _ingan_formula_result(in_fraction: float) -> dict[str, Any] | None:
    ga_fraction = 1.0 - in_fraction
    return {
        "template_id": "crystal_formula_alloy",
        "example": "gallium_nitride_wurtzite_spec.json",
        "formula": f"In{in_fraction:g}Ga{ga_fraction:g}N",
        "host_element": "Ga",
        "alloy_element": "In",
        "fraction": in_fraction,
    }


def _cdznte_cation_formula_result(cd_fraction: float) -> dict[str, Any] | None:
    return _ii_vi_cation_formula_result(cd_fraction, 1.0 - cd_fraction, "Te")


def _match_algaas_formula_alloy(text: str) -> dict[str, Any] | None:
    content_fraction = (
        _match_formula_content_fraction(text, ("al", "aluminum", "\u94dd"))
        if _formula_context_present(text, ("algaas", "gaas", "arsenide", "\u7837"))
        else None
    )
    if content_fraction is not None:
        return _algaas_formula_result(content_fraction)

    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?:al\s*x\s*ga\s*(?:1\s*-\s*x|1-x)\s*as|algaas){_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        al_fraction = _optional_formula_fraction(x_match.group("x"))
        if al_fraction is not None and 0.0 < al_fraction < 1.0:
            return _algaas_formula_result(al_fraction)

    al_first = re.search(
        rf"{_FORMULA_ASCII_START}al\s*(?P<al>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*as{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    ga_first = re.search(
        rf"{_FORMULA_ASCII_START}ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"al\s*(?P<al>{_FORMULA_FRACTION_PATTERN})\s*as{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    match = al_first or ga_first
    if match is None:
        return None
    al_fraction = _optional_formula_fraction(match.group("al"))
    ga_fraction = _optional_formula_fraction(match.group("ga"))
    if al_fraction is None or ga_fraction is None or abs((al_fraction + ga_fraction) - 1.0) > 0.02:
        return None
    if not 0.0 < al_fraction < 1.0:
        return None
    return _algaas_formula_result(al_fraction)


def _match_ingaas_formula_alloy(text: str) -> dict[str, Any] | None:
    content_fraction = (
        _match_formula_content_fraction(text, ("in", "indium", "\u94df"))
        if _formula_context_present(text, ("ingaas", "gaas", "arsenide", "\u7837"))
        else None
    )
    if content_fraction is not None:
        return _ingaas_formula_result(content_fraction)

    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?:in\s*x\s*ga\s*(?:1\s*-\s*x|1-x)\s*as|ingaas){_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        in_fraction = _optional_formula_fraction(x_match.group("x"))
        if in_fraction is not None and 0.0 < in_fraction < 1.0:
            return _ingaas_formula_result(in_fraction)

    in_first = re.search(
        rf"{_FORMULA_ASCII_START}in\s*(?P<in>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*as{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    ga_first = re.search(
        rf"{_FORMULA_ASCII_START}ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"in\s*(?P<in>{_FORMULA_FRACTION_PATTERN})\s*as{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    match = in_first or ga_first
    if match is None:
        return None
    in_fraction = _optional_formula_fraction(match.group("in"))
    ga_fraction = _optional_formula_fraction(match.group("ga"))
    if in_fraction is None or ga_fraction is None or abs((in_fraction + ga_fraction) - 1.0) > 0.02:
        return None
    if not 0.0 < in_fraction < 1.0:
        return None
    return _ingaas_formula_result(in_fraction)


def _match_ii_vi_formula_alloy(text: str) -> dict[str, Any] | None:
    cation_alloy = _match_ii_vi_cation_formula_alloy(text)
    if cation_alloy is not None:
        return cation_alloy
    return _match_ii_vi_anion_formula_alloy(text)


def _match_ii_vi_cation_formula_alloy(text: str) -> dict[str, Any] | None:
    anion_pattern = r"te|se|s"
    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?:cd\s*(?:1\s*-\s*x|1-x)\s*zn\s*x\s*(?P<anion>{anion_pattern})|"
        rf"cdzn(?P<compact_anion>{anion_pattern})){_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        zn_fraction = _optional_formula_fraction(x_match.group("x"))
        anion = _normalize_formula_element(x_match.group("anion") or x_match.group("compact_anion"))
        if zn_fraction is not None and anion is not None and 0.0 < zn_fraction < 1.0:
            cd_fraction = 1.0 - zn_fraction
            return _ii_vi_cation_formula_result(cd_fraction, zn_fraction, anion)

    cd_first = re.search(
        rf"{_FORMULA_ASCII_START}cd\s*(?P<cd>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"zn\s*(?P<zn>{_FORMULA_FRACTION_PATTERN})\s*(?P<anion>{anion_pattern}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    zn_first = re.search(
        rf"{_FORMULA_ASCII_START}zn\s*(?P<zn>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"cd\s*(?P<cd>{_FORMULA_FRACTION_PATTERN})\s*(?P<anion>{anion_pattern}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    match = cd_first or zn_first
    if match is None:
        return None
    cd_fraction = _optional_formula_fraction(match.group("cd"))
    zn_fraction = _optional_formula_fraction(match.group("zn"))
    anion = _normalize_formula_element(match.group("anion"))
    if (
        cd_fraction is None
        or zn_fraction is None
        or anion is None
        or abs((cd_fraction + zn_fraction) - 1.0) > 0.02
        or not 0.0 < cd_fraction < 1.0
        or not 0.0 < zn_fraction < 1.0
    ):
        return None
    return _ii_vi_cation_formula_result(cd_fraction, zn_fraction, anion)


def _ii_vi_cation_formula_result(cd_fraction: float, zn_fraction: float, anion: str) -> dict[str, Any] | None:
    formula = f"Cd{cd_fraction:g}Zn{zn_fraction:g}{anion}"
    preferred = ("Cd", cd_fraction) if cd_fraction >= zn_fraction else ("Zn", zn_fraction)
    fallback = ("Zn", zn_fraction) if preferred[0] == "Cd" else ("Cd", cd_fraction)
    for host_element, _host_fraction in (preferred, fallback):
        example = _ii_vi_zincblende_example(host_element, anion)
        if example is None:
            continue
        alloy_element = "Zn" if host_element == "Cd" else "Cd"
        alloy_fraction = zn_fraction if alloy_element == "Zn" else cd_fraction
        if not 0.0 < alloy_fraction < 1.0:
            return None
        return {
            "template_id": "crystal_formula_alloy",
            "example": example,
            "formula": formula,
            "host_element": host_element,
            "alloy_element": alloy_element,
            "fraction": alloy_fraction,
        }
    return None


def _match_ii_vi_anion_formula_alloy(text: str) -> dict[str, Any] | None:
    cation_pattern = r"zn|cd"
    anion_pattern = r"te|se|s"
    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?P<cation>{cation_pattern})\s*(?P<host>{anion_pattern})\s*"
        rf"(?:1\s*-\s*x|1-x)\s*(?P<alloy>{anion_pattern})\s*x{_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        cation = _normalize_formula_element(x_match.group("cation"))
        host_anion = _normalize_formula_element(x_match.group("host"))
        alloy_anion = _normalize_formula_element(x_match.group("alloy"))
        alloy_fraction = _optional_formula_fraction(x_match.group("x"))
        if (
            cation is not None
            and host_anion is not None
            and alloy_anion is not None
            and host_anion != alloy_anion
            and alloy_fraction is not None
            and 0.0 < alloy_fraction < 1.0
        ):
            host_fraction = 1.0 - alloy_fraction
            fractions = {host_anion: host_fraction, alloy_anion: alloy_fraction}
            return _ii_vi_anion_formula_result(cation, fractions)

    direct = re.search(
        rf"{_FORMULA_ASCII_START}(?P<cation>{cation_pattern})\s*(?P<a1>{anion_pattern})\s*"
        rf"(?P<f1>{_FORMULA_FRACTION_PATTERN})\s*(?P<a2>{anion_pattern})\s*"
        rf"(?P<f2>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if direct is None:
        return None
    cation = _normalize_formula_element(direct.group("cation"))
    first_anion = _normalize_formula_element(direct.group("a1"))
    second_anion = _normalize_formula_element(direct.group("a2"))
    first_fraction = _optional_formula_fraction(direct.group("f1"))
    second_fraction = _optional_formula_fraction(direct.group("f2"))
    if (
        cation is None
        or first_anion is None
        or second_anion is None
        or first_anion == second_anion
        or first_fraction is None
        or second_fraction is None
        or abs((first_fraction + second_fraction) - 1.0) > 0.02
        or not 0.0 < first_fraction < 1.0
        or not 0.0 < second_fraction < 1.0
    ):
        return None
    return _ii_vi_anion_formula_result(cation, {first_anion: first_fraction, second_anion: second_fraction})


def _ii_vi_anion_formula_result(cation: str, fractions: dict[str, float]) -> dict[str, Any] | None:
    ordered_anions = [anion for anion in ("S", "Se", "Te") if anion in fractions]
    if len(ordered_anions) != 2:
        return None
    formula = cation + "".join(f"{anion}{fractions[anion]:g}" for anion in ordered_anions)
    preferred = max(ordered_anions, key=lambda anion: (fractions[anion], -ordered_anions.index(anion)))
    fallback = next(anion for anion in ordered_anions if anion != preferred)
    for host_anion in (preferred, fallback):
        example = _ii_vi_zincblende_example(cation, host_anion)
        if example is None:
            continue
        alloy_anion = next(anion for anion in ordered_anions if anion != host_anion)
        alloy_fraction = fractions[alloy_anion]
        if not 0.0 < alloy_fraction < 1.0:
            return None
        return {
            "template_id": "crystal_formula_alloy",
            "example": example,
            "formula": formula,
            "host_element": host_anion,
            "alloy_element": alloy_anion,
            "fraction": alloy_fraction,
        }
    return None


def _ii_vi_zincblende_example(cation: str, anion: str) -> str | None:
    return {
        ("Zn", "S"): "zinc_sulfide_zincblende_spec.json",
        ("Zn", "Se"): "zinc_selenide_zincblende_spec.json",
        ("Zn", "Te"): "zinc_telluride_zincblende_spec.json",
        ("Cd", "S"): "cadmium_sulfide_zincblende_spec.json",
        ("Cd", "Se"): "cadmium_selenide_zincblende_spec.json",
        ("Cd", "Te"): "cadmium_telluride_zincblende_spec.json",
    }.get((cation, anion))


def _normalize_formula_element(value: str | None) -> str | None:
    if value is None:
        return None
    symbol = value[:1].upper() + value[1:].lower()
    return symbol if symbol in ELEMENTS else None


def _match_algan_formula_alloy(text: str) -> dict[str, Any] | None:
    content_fraction = (
        _match_formula_content_fraction(text, ("al", "aluminum", "\u94dd"))
        if _formula_context_present(text, ("algan", "gan", "nitride", "hemt", "2deg", "\u6c2e"))
        else None
    )
    if content_fraction is not None:
        return _algan_formula_result(content_fraction)

    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?:al\s*x\s*ga\s*(?:1\s*-\s*x|1-x)\s*n|algan){_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        al_fraction = _optional_formula_fraction(x_match.group("x"))
        if al_fraction is not None and 0.0 < al_fraction < 1.0:
            return _algan_formula_result(al_fraction)

    al_first = re.search(
        rf"{_FORMULA_ASCII_START}al\s*(?P<al>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*n{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    ga_first = re.search(
        rf"{_FORMULA_ASCII_START}ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"al\s*(?P<al>{_FORMULA_FRACTION_PATTERN})\s*n{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    match = al_first or ga_first
    if match is None:
        return None
    al_fraction = _optional_formula_fraction(match.group("al"))
    ga_fraction = _optional_formula_fraction(match.group("ga"))
    if al_fraction is None or ga_fraction is None or abs((al_fraction + ga_fraction) - 1.0) > 0.02:
        return None
    if not 0.0 < al_fraction < 1.0:
        return None
    return _algan_formula_result(al_fraction)


def _match_ingan_formula_alloy(text: str) -> dict[str, Any] | None:
    content_fraction = (
        _match_formula_content_fraction(text, ("in", "indium", "\u94df"))
        if _formula_context_present(text, ("ingan", "gan", "nitride", "\u6c2e"))
        else None
    )
    if content_fraction is not None:
        return _ingan_formula_result(content_fraction)

    x_match = re.search(
        rf"{_FORMULA_ASCII_START}(?:in\s*x\s*ga\s*(?:1\s*-\s*x|1-x)\s*n|ingan){_FORMULA_ASCII_END}"
        rf".*?{_FORMULA_ASCII_START}x\s*=\s*(?P<x>{_FORMULA_FRACTION_PATTERN}){_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    if x_match is not None:
        in_fraction = _optional_formula_fraction(x_match.group("x"))
        if in_fraction is not None and 0.0 < in_fraction < 1.0:
            return _ingan_formula_result(in_fraction)

    in_first = re.search(
        rf"{_FORMULA_ASCII_START}in\s*(?P<in>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*n{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    ga_first = re.search(
        rf"{_FORMULA_ASCII_START}ga\s*(?P<ga>{_FORMULA_FRACTION_PATTERN})\s*"
        rf"in\s*(?P<in>{_FORMULA_FRACTION_PATTERN})\s*n{_FORMULA_ASCII_END}",
        text,
        flags=re.IGNORECASE,
    )
    match = in_first or ga_first
    if match is None:
        return None
    in_fraction = _optional_formula_fraction(match.group("in"))
    ga_fraction = _optional_formula_fraction(match.group("ga"))
    if in_fraction is None or ga_fraction is None or abs((in_fraction + ga_fraction) - 1.0) > 0.02:
        return None
    if not 0.0 < in_fraction < 1.0:
        return None
    return _ingan_formula_result(in_fraction)


def _optional_formula_fraction(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        fraction = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= fraction <= 1.0:
        return None
    return fraction


def _apply_iii_nitride_heterostructure_formula_request(
    text: str,
    spec: ModelSpec,
    *,
    template_id: str,
) -> tuple[ModelSpec, list[str]] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    if template_id == "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure":
        match = _match_algan_formula_alloy(text)
        alloy_element = "Al"
        host_element = "Ga"
        marker = "Al;Ga"
        region = "upper"
        source = "natural_language_iii_nitride_heterostructure_formula_alloy"
    elif template_id == "indium_gallium_nitride_gallium_nitride_0001_heterostructure":
        match = _match_ingan_formula_alloy(text)
        alloy_element = "In"
        host_element = "Ga"
        marker = "Ga;In"
        region = "upper"
        source = "natural_language_iii_nitride_heterostructure_formula_alloy"
    elif template_id == "indium_gallium_arsenide_indium_phosphide_001_heterostructure":
        match = _match_ingaas_formula_alloy(text)
        alloy_element = "In"
        host_element = "Ga"
        marker = "Ga;In"
        region = "lower_zincblende"
        source = "natural_language_iii_v_heterostructure_formula_alloy"
    else:
        return None
    if match is None:
        return None

    requested_fraction = float(match["fraction"])
    formula = str(match["formula"])
    working = spec
    all_diff: list[str] = []
    reset_candidates = _heterostructure_alloy_cation_sites(
        working.model,
        host_element=host_element,
        alloy_element=alloy_element,
        region=region,
    )
    reset_ops = [
        {"type": "substitute_atom", "atom_id": atom.id, "new_element": host_element}
        for atom in reset_candidates
        if atom.element == alloy_element
    ]
    if reset_ops:
        working, diff = apply_semantic_patch(
            working,
            SemanticPatch(project_id=working.project_id, base_revision=working.revision, operations=reset_ops),
        )
        all_diff.extend(diff)

    base_candidates = _heterostructure_alloy_cation_sites(
        working.model,
        host_element=host_element,
        alloy_element=alloy_element,
        region=region,
    )
    if not base_candidates:
        raise ValueError("Could not find heterostructure alloy cation sites for formula-alloy customization.")
    nx, ny, target_count, actual_fraction = _iii_nitride_alloy_supercell_plan(len(base_candidates), requested_fraction)
    if nx > 1 or ny > 1:
        working, diff = apply_semantic_patch(
            working,
            SemanticPatch(
                project_id=working.project_id,
                base_revision=working.revision,
                operations=[{"type": "make_supercell", "matrix": [nx, ny, 1]}],
            ),
        )
        all_diff.extend(diff)

    candidates = _heterostructure_alloy_cation_sites(
        working.model,
        host_element=host_element,
        alloy_element=alloy_element,
        region=region,
    )
    selected = _select_layer_balanced_sites(candidates, target_count)
    substitute_ops = [
        {"type": "substitute_atom", "atom_id": atom.id, "new_element": alloy_element}
        for atom in selected
    ]
    actual_fraction = len(selected) / len(candidates)
    record = {
        "host_element": host_element,
        "alloy_element": alloy_element,
        "requested_fraction": round(requested_fraction, 6),
        "requested_percent": round(100.0 * requested_fraction, 6),
        "actual_fraction": round(actual_fraction, 6),
        "actual_percent": round(100.0 * actual_fraction, 6),
        "candidate_site_count": len(candidates),
        "substituted_site_count": len(selected),
        "selected_atom_ids": [atom.id for atom in selected],
        "rounding_error_fraction": round(actual_fraction - requested_fraction, 6),
        "source": source,
    }
    metadata = dict(working.metadata or {})
    material_marker_map = dict(metadata.get("material_marker_map") or {})
    material_marker_map[marker] = formula
    material_marker_map[f"{alloy_element}-{host_element}"] = formula
    formula_key = re.sub(r"[^a-z0-9]+", "", formula.lower()) + "_reference_lattice_angstrom"
    if template_id == "indium_gallium_arsenide_indium_phosphide_001_heterostructure":
        material_marker_map["In"] = "InP"
        metadata_updates = {
            "materials": [formula, "InP"],
            "material_marker_map": material_marker_map,
            "interface": f"{formula}/InP",
            "substrate": formula,
            "coherent_strain_model": f"pseudomorphic_{formula.lower()}_on_inp",
            formula_key: _ingaas_reference_lattice_angstrom(requested_fraction),
            "applied_alloy": [record],
            "last_applied_alloy": record,
            "formula_alloy_request": {
                "formula": formula,
                "host_element": host_element,
                "alloy_element": alloy_element,
                "requested_fraction": round(requested_fraction, 6),
                "source": source,
            },
        }
    else:
        metadata_updates = {
            "materials": ["GaN", formula],
            "material_marker_map": material_marker_map,
            "interface": f"GaN/{formula}",
            "substrate": "GaN",
            "coherent_strain_model": f"pseudomorphic_{formula.lower()}_on_gan",
            formula_key: _iii_nitride_reference_lattice_angstrom(alloy_element, requested_fraction),
            "applied_alloy": [record],
            "last_applied_alloy": record,
            "formula_alloy_request": {
                "formula": formula,
                "host_element": host_element,
                "alloy_element": alloy_element,
                "requested_fraction": round(requested_fraction, 6),
                "source": source,
            },
        }
    working, diff = apply_semantic_patch(
        working,
        SemanticPatch(
            project_id=working.project_id,
            base_revision=working.revision,
            operations=[*substitute_ops, {"type": "set_metadata", "metadata_updates": metadata_updates}],
        ),
    )
    all_diff.extend(diff)
    return working, all_diff


def _iii_nitride_barrier_cation_sites(
    model: CrystalSpec,
    *,
    host_element: str,
    alloy_element: str,
) -> list[BasisAtomSpec]:
    return _heterostructure_alloy_cation_sites(
        model,
        host_element=host_element,
        alloy_element=alloy_element,
        region="upper",
    )


def _heterostructure_alloy_cation_sites(
    model: CrystalSpec,
    *,
    host_element: str,
    alloy_element: str,
    region: str,
) -> list[BasisAtomSpec]:
    if region == "lower_zincblende":
        in_region = lambda value: value < 0.49
    elif region == "lower":
        in_region = lambda value: value < 0.5
    elif region == "upper":
        in_region = lambda value: value >= 0.5
    else:
        in_region = lambda value: True
    return sorted(
        [
            atom
            for atom in model.basis_atoms
            if atom.element in {host_element, alloy_element} and in_region(float(atom.fractional.z))
        ],
        key=lambda atom: (round(float(atom.fractional.z), 6), round(float(atom.fractional.x), 6), round(float(atom.fractional.y), 6), atom.id),
    )


def _iii_nitride_alloy_supercell_plan(base_candidate_count: int, requested_fraction: float) -> tuple[int, int, int, float]:
    plans = [(1, 1), (2, 1), (2, 2), (3, 2), (3, 3), (4, 4)]
    ranked: list[tuple[float, int, int, int, int, float]] = []
    for nx, ny in plans:
        candidate_count = base_candidate_count * nx * ny
        target_count = max(1, min(candidate_count, int(math.floor(candidate_count * requested_fraction + 0.5))))
        actual_fraction = target_count / candidate_count
        layer_penalty = 0 if target_count >= 2 else 1
        ranked.append((abs(actual_fraction - requested_fraction), layer_penalty, candidate_count, nx, ny, actual_fraction))
    acceptable = [item for item in ranked if item[0] <= 0.03 and item[1] == 0]
    if acceptable:
        _, _, candidate_count, nx, ny, actual_fraction = min(acceptable, key=lambda item: (item[2], item[0]))
    else:
        _, _, candidate_count, nx, ny, actual_fraction = min(ranked, key=lambda item: (item[0], item[1], item[2]))
    target_count = max(1, min(candidate_count, int(math.floor(candidate_count * requested_fraction + 0.5))))
    return nx, ny, target_count, actual_fraction


def _select_layer_balanced_sites(candidates: list[BasisAtomSpec], target_count: int) -> list[BasisAtomSpec]:
    grouped: dict[float, list[BasisAtomSpec]] = {}
    for atom in candidates:
        grouped.setdefault(round(float(atom.fractional.z), 6), []).append(atom)
    groups = [
        sorted(group, key=lambda atom: (round(float(atom.fractional.x), 6), round(float(atom.fractional.y), 6), atom.id))
        for _, group in sorted(grouped.items())
    ]
    selected: list[BasisAtomSpec] = []
    index = 0
    while len(selected) < target_count and any(index < len(group) for group in groups):
        for group in groups:
            if index < len(group):
                selected.append(group[index])
                if len(selected) >= target_count:
                    break
        index += 1
    return selected


def _iii_nitride_reference_lattice_angstrom(alloy_element: str, fraction: float) -> float:
    endpoint = {"Al": 3.112, "In": 3.545}.get(alloy_element, 3.189)
    return round((1.0 - fraction) * 3.189 + fraction * endpoint, 6)


def _ingaas_reference_lattice_angstrom(in_fraction: float) -> float:
    return round((1.0 - in_fraction) * 5.6533 + in_fraction * 6.0583, 6)


def _apply_quantum_well_layer_request(
    text: str,
    spec: ModelSpec,
) -> tuple[ModelSpec, list[str]] | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    metadata = dict(spec.metadata or {})
    materials = _metadata_materials(metadata)
    if len(materials) != 2 or not metadata.get("interface"):
        return None
    match = _match_quantum_well_layer_counts(text, materials, str(metadata.get("substrate") or materials[0]))
    if match is None:
        return None
    axis = _superlattice_period_axis(metadata)
    if axis != "c":
        raise ValueError("custom quantum-well layer counts currently require interface_axis='c'.")

    well_material = str(match["well_material"])
    barrier_material = str(match["barrier_material"])
    layer_templates = _crystal_layer_templates_by_material(spec.model, materials)
    missing = [material for material in (well_material, barrier_material) if material not in layer_templates]
    if missing:
        raise ValueError(f"custom quantum-well layer counts are unsupported for material template(s): {', '.join(missing)}.")
    motif_length = max(len(layer_templates[well_material]), len(layer_templates[barrier_material]), 1)
    spacings = {
        material: _quantum_well_layer_spacing(metadata, material, layer_templates[material], spec.model.lattice.c)
        for material in materials
    }
    conversion: dict[str, Any] = {}
    thickness_request = "well_thickness_angstrom" in match or "barrier_thickness_angstrom" in match
    if "well_layers" in match and "barrier_layers" in match:
        well_layers = int(match["well_layers"])
        barrier_layers = int(match["barrier_layers"])
    elif "well_thickness_angstrom" in match and "barrier_thickness_angstrom" in match:
        well_thickness = float(match["well_thickness_angstrom"])
        barrier_thickness = float(match["barrier_thickness_angstrom"])
        well_layers, barrier_layers, conversion = _quantum_well_thicknesses_to_layer_counts(
            well_material=well_material,
            barrier_material=barrier_material,
            well_thickness_angstrom=well_thickness,
            barrier_thickness_angstrom=barrier_thickness,
            spacings=spacings,
            motif_length=motif_length,
        )
    else:
        well_layers, barrier_layers, conversion = _quantum_well_single_thickness_to_layer_counts(
            well_material=well_material,
            barrier_material=barrier_material,
            well_thickness_angstrom=match.get("well_thickness_angstrom"),
            barrier_thickness_angstrom=match.get("barrier_thickness_angstrom"),
            default_well_layers=len(layer_templates[well_material]),
            default_barrier_layers=len(layer_templates[barrier_material]),
            spacings=spacings,
            motif_length=motif_length,
        )
    max_layer_count = 240 if thickness_request else 40
    _validate_quantum_well_layer_count(well_layers, label="well", max_count=max_layer_count)
    _validate_quantum_well_layer_count(barrier_layers, label="barrier", max_count=max_layer_count)
    if (well_layers + barrier_layers) % motif_length != 0:
        raise ValueError(
            "custom quantum-well layer counts must have a total layer count that is a multiple "
            f"of the {motif_length}-layer periodic motif."
        )
    sequence = [
        *[(well_material, index) for index in range(well_layers)],
        *[(barrier_material, index) for index in range(barrier_layers)],
    ]
    c_length = sum(spacings[material] for material, _ in sequence)
    if c_length <= 0:
        raise ValueError("custom quantum-well layer count produced an invalid c-axis length.")

    atoms: list[BasisAtomSpec] = []
    z_position = 0.0
    material_layer_counters: dict[str, int] = {material: 0 for material in materials}
    for global_layer_index, (material, _) in enumerate(sequence, start=1):
        templates = layer_templates[material]
        template_atoms = templates[(global_layer_index - 1) % len(templates)]
        material_layer_counters[material] += 1
        role = "W" if material == well_material else "B"
        for atom_index, atom in enumerate(template_atoms, start=1):
            fractional = atom.fractional
            atoms.append(
                BasisAtomSpec(
                    id=f"{atom.element}{role}{material_layer_counters[material]}_{atom_index}",
                    element=atom.element,
                    fractional=[fractional.x, fractional.y, z_position / c_length],
                )
            )
        z_position += spacings[material]

    lattice = spec.model.lattice.model_copy(update={"c": c_length})
    request_record = {
        "well_material": well_material,
        "barrier_material": barrier_material,
        "well_layer_count": well_layers,
        "barrier_layer_count": barrier_layers,
        "axis": axis,
        "source": match.get("source") or "natural_language_quantum_well_layers",
        **conversion,
    }
    updated_metadata = {
        **metadata,
        "structure_family": f"{metadata.get('structure_family') or spec.model.name} custom quantum well",
        "quantum_well_layer_request": request_record,
        "last_quantum_well_layer_request": request_record,
    }
    model = CrystalSpec(
        name=spec.model.name,
        lattice=LatticeSpec(
            a=lattice.a,
            b=lattice.b,
            c=lattice.c,
            alpha=lattice.alpha,
            beta=lattice.beta,
            gamma=lattice.gamma,
        ),
        basis_atoms=atoms,
        operations=spec.model.operations,
    )
    updated = spec.model_copy(update={"model": model, "metadata": updated_metadata})
    return ModelSpec.model_validate(updated.model_dump(mode="json")), [
        f"set_quantum_well_layers {well_material}:{well_layers} {barrier_material}:{barrier_layers}"
    ]


def _metadata_materials(metadata: dict[str, Any]) -> list[str]:
    materials = metadata.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    return [str(material) for material in materials if str(material)]


def _well_barrier_materials(materials: list[str], substrate: str) -> tuple[str, str]:
    """Choose deterministic well/barrier roles from a two-material heterostructure."""

    if len(materials) != 2:
        raise ValueError("quantum-well layer parsing requires exactly two materials.")
    substrate_value = str(substrate)
    if substrate_value in materials:
        well = substrate_value
        barrier = next(material for material in materials if material != well)
        return well, barrier
    return materials[0], materials[1]



def _match_quantum_well_layer_counts(
    text: str,
    materials: list[str],
    substrate: str,
) -> dict[str, Any] | None:
    if not re.search(r"\b(?:quantum\s+well|mqw|superlattice|heterostructure|hemt|2deg)\b", text, flags=re.IGNORECASE) and not any(
        term in text for term in ("\u91cf\u5b50\u9631", "\u591a\u91cf\u5b50\u9631", "\u8d85\u6676\u683c", "\u5f02\u8d28\u7ed3\u6784", "\u4e8c\u7ef4\u7535\u5b50\u6c14", "\u9ad8\u7535\u5b50\u8fc1\u79fb\u7387\u6676\u4f53\u7ba1")
    ):
        return None
    if not re.search(
        r"(?:\b(?:layers?|monolayers?)\b|\d{1,2}\s*ml\b|\bml\b|\d+(?:\.\d+)?\s*(?:nm|nanometers?|angstroms?|ang)\b|\b(?:nm|nanometers?|angstroms?|ang)\b|\u5c42|\u5355\u5c42|\u7eb3\u7c73|\u57c3)",
        text,
        flags=re.IGNORECASE,
    ):
        return None
    well, barrier = _well_barrier_materials(materials, substrate)
    counts = _match_named_layer_counts(text, well=well, barrier=barrier)
    if counts is None:
        counts = _match_slash_layer_counts(text, materials=materials)
    if counts is None:
        thickness = _match_layer_thicknesses(text, well=well, barrier=barrier, materials=materials)
        if thickness is None:
            return None
        return {"well_material": well, "barrier_material": barrier, **thickness}
    return {"well_material": well, "barrier_material": barrier, **counts}


def _match_named_layer_counts(text: str, *, well: str, barrier: str) -> dict[str, Any] | None:
    material_counts = _explicit_material_layer_counts(text, [well, barrier])
    if well in material_counts and barrier in material_counts:
        return {
            "well_layers": material_counts[well],
            "barrier_layers": material_counts[barrier],
            "source": "explicit_material_layers",
        }
    role_counts = _explicit_role_layer_counts(text)
    if "well" in role_counts and "barrier" in role_counts:
        return {
            "well_layers": role_counts["well"],
            "barrier_layers": role_counts["barrier"],
            "source": "explicit_role_layers",
        }
    return None


def _match_slash_layer_counts(text: str, *, materials: list[str]) -> dict[str, Any] | None:
    if len(materials) != 2 or "/" not in text:
        return None
    counts = _explicit_material_layer_counts(text, materials)
    if materials[0] in counts and materials[1] in counts:
        return {
            "well_layers": counts[materials[0]],
            "barrier_layers": counts[materials[1]],
            "source": "slash_material_layers",
        }
    return None


def _match_layer_thicknesses(text: str, *, well: str, barrier: str, materials: list[str]) -> dict[str, Any] | None:
    thicknesses = _explicit_material_thicknesses(text, [well, barrier])
    if well in thicknesses and barrier in thicknesses:
        return {
            "well_thickness_angstrom": thicknesses[well],
            "barrier_thickness_angstrom": thicknesses[barrier],
            "source": "explicit_material_thicknesses",
        }
    if well in thicknesses or barrier in thicknesses:
        result: dict[str, Any] = {"source": "explicit_material_thicknesses"}
        if well in thicknesses:
            result["well_thickness_angstrom"] = thicknesses[well]
        if barrier in thicknesses:
            result["barrier_thickness_angstrom"] = thicknesses[barrier]
        return result
    if len(materials) == 2:
        slash_thicknesses = _explicit_material_thicknesses(text, materials)
        if materials[0] in slash_thicknesses and materials[1] in slash_thicknesses:
            return {
                "well_thickness_angstrom": slash_thicknesses[materials[0]],
                "barrier_thickness_angstrom": slash_thicknesses[materials[1]],
                "source": "slash_material_thicknesses",
            }
        if materials[0] in slash_thicknesses or materials[1] in slash_thicknesses:
            result = {"source": "slash_material_thicknesses"}
            if materials[0] in slash_thicknesses:
                result["well_thickness_angstrom"] = slash_thicknesses[materials[0]]
            if materials[1] in slash_thicknesses:
                result["barrier_thickness_angstrom"] = slash_thicknesses[materials[1]]
            return result
    role_thicknesses = _explicit_role_thicknesses(text)
    if "well" in role_thicknesses and "barrier" in role_thicknesses:
        return {
            "well_thickness_angstrom": role_thicknesses["well"],
            "barrier_thickness_angstrom": role_thicknesses["barrier"],
            "source": "explicit_role_thicknesses",
        }
    if "well" in role_thicknesses or "barrier" in role_thicknesses:
        result = {"source": "explicit_role_thicknesses"}
        if "well" in role_thicknesses:
            result["well_thickness_angstrom"] = role_thicknesses["well"]
        if "barrier" in role_thicknesses:
            result["barrier_thickness_angstrom"] = role_thicknesses["barrier"]
        return result
    return None



def _explicit_material_layer_counts(text: str, materials: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for material in materials:
        for alias in _material_text_aliases(material):
            escaped = re.escape(alias)
            patterns = [
                rf"\b{escaped}\b\s*[\(\[]?\s*(?P<count>\d{{1,2}})\s*(?:ml|monolayers?|layers?)\s*[\)\]]?",
                rf"\b{escaped}\s*(?:well|barrier)?\s*(?:with\s+)?(?P<count>\d{{1,2}})\s*(?:ml|monolayers?|layers?)\b",
                rf"\b(?P<count>\d{{1,2}})\s*(?:ml|monolayers?|layers?)\s*(?:of\s+)?{escaped}\b",
                rf"\b(?P<count>\d{{1,2}})\s*{escaped}\s*(?:ml|monolayers?|layers?)\b",
                rf"\b{escaped}\b\s*(?:\u9631\u5c42|\u52bf\u5792\u5c42)?\s*(?P<count>\d{{1,2}})\s*(?:\u5c42|\u5355\u5c42)",
                rf"(?<![A-Za-z0-9])(?P<count>\d{{1,2}})\s*(?:\u5c42|\u5355\u5c42)\s*{escaped}(?![A-Za-z0-9])",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match is not None:
                    counts[material] = int(match.group("count"))
                    break
            if material in counts:
                break
    return counts



def _explicit_material_thicknesses(text: str, materials: list[str]) -> dict[str, float]:
    thicknesses: dict[str, float] = {}
    unit = r"(?P<unit>nm|nanometers?|angstroms?|ang|\u7eb3\u7c73|\u57c3)"
    value = r"(?P<value>\d+(?:\.\d+)?)"
    for material in materials:
        for alias in _material_text_aliases(material):
            escaped = re.escape(alias)
            patterns = [
                rf"\b{escaped}\b\s*[\(\[]?\s*{value}\s*{unit}\s*[\)\]]?",
                rf"\b{escaped}\s*(?:well|barrier)?\s*(?:with\s+|of\s+|=|:)?\s*{value}\s*{unit}\b",
                rf"\b{value}\s*{unit}\s*(?:of\s+)?{escaped}\b",
                rf"\b{escaped}\b\s*(?:\u9631\u5c42|\u52bf\u5792\u5c42)?\s*{value}\s*{unit}",
                rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])\s*(?:\u9631\u5c42|\u52bf\u5792\u5c42)?\s*{value}\s*{unit}",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match is not None:
                    thicknesses[material] = _thickness_value_to_angstrom(float(match.group("value")), match.group("unit"))
                    break
            if material in thicknesses:
                break
    return thicknesses



def _explicit_role_thicknesses(text: str) -> dict[str, float]:
    thicknesses: dict[str, float] = {}
    unit = r"(?P<unit>nm|nanometers?|angstroms?|ang|\u7eb3\u7c73|\u57c3)"
    value = r"(?P<value>\d+(?:\.\d+)?)"
    for role in ("well", "barrier"):
        chinese_roles = (
            ("\u9631\u5c42", "\u91cf\u5b50\u9631", "\u9631\u533a")
            if role == "well"
            else ("\u52bf\u5792\u5c42", "\u52bf\u5792", "\u969c\u58c1\u5c42", "\u969c\u58c1")
        )
        chinese_role_pattern = "(?:" + "|".join(re.escape(term) for term in chinese_roles) + ")"
        patterns = [
            rf"\b{value}\s*{unit}\s+(?:\w+\s+)?{role}\b",
            rf"\b{role}\s*(?:region|layer|layers|thickness|layer\s+thickness)?\s*(?:with\s+|of\s+|=|:)?\s*{value}\s*{unit}\b",
            rf"\b{role}\s*(?:region|layer|layers)?\s+thickness\s*(?:with\s+|of\s+|=|:)?\s*{value}\s*{unit}\b",
            rf"{chinese_role_pattern}\s*(?:\u539a\u5ea6|\u539a)?\s*(?:=|:|\u4e3a|\u5230)?\s*{value}\s*{unit}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is not None:
                thicknesses[role] = _thickness_value_to_angstrom(float(match.group("value")), match.group("unit"))
                break
    return thicknesses


def _thickness_value_to_angstrom(value: float, unit: str) -> float:
    unit_lower = unit.lower()
    if unit_lower.startswith("nm") or unit_lower.startswith("nanometer") or unit == "\u7eb3\u7c73":
        return value * 10.0
    return value



def _explicit_role_layer_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for role in ("well", "barrier"):
        chinese_role = "\u9631\u5c42" if role == "well" else "\u52bf\u5792\u5c42"
        patterns = [
            rf"\b(?P<count>\d{{1,2}})\s*(?:ml|monolayers?|layers?)\s+(?:\w+\s+)?{role}\b",
            rf"\b(?P<count>\d{{1,2}})\s*{role}\s*(?:ml|monolayers?|layers?)\b",
            rf"\b{role}\s*(?:region|layer|layers)?\s*(?:with\s+|of\s+|=|:)?\s*(?P<count>\d{{1,2}})\s*(?:ml|monolayers?|layers?)\b",
            rf"{chinese_role}\s*(?:=|:)?\s*(?P<count>\d{{1,2}})\s*(?:\u5c42|\u5355\u5c42)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is not None:
                counts[role] = int(match.group("count"))
                break
    return counts


def _material_text_aliases(material: str) -> list[str]:
    aliases = {material}
    lowered_material = material.lower()
    if lowered_material not in CASE_SENSITIVE_MATERIAL_FORMULA_LOWERCASES:
        aliases.add(lowered_material)
    compact = re.sub(r"[^A-Za-z0-9]+", "", material)
    if compact:
        aliases.add(compact)
        lowered_compact = compact.lower()
        if lowered_compact not in CASE_SENSITIVE_MATERIAL_FORMULA_LOWERCASES:
            aliases.add(lowered_compact)
    element_compact = "".join(re.findall(r"[A-Z][a-z]?", material))
    if element_compact:
        aliases.add(element_compact)
        lowered_element_compact = element_compact.lower()
        if lowered_element_compact not in CASE_SENSITIVE_MATERIAL_FORMULA_LOWERCASES:
            aliases.add(lowered_element_compact)
    spaced = " ".join(re.findall(r"[A-Z][a-z]?", material))
    if spaced:
        aliases.add(spaced)
        aliases.add(spaced.lower())
    return sorted(aliases, key=len, reverse=True)


def _validate_quantum_well_layer_count(count: int, *, label: str, max_count: int = 40) -> None:
    if count < 2 or count > max_count:
        raise ValueError(f"{label} layer count must be between 2 and {max_count}.")
    if count % 2 != 0:
        raise ValueError(f"{label} layer count must be even to preserve the local cation/anion or diamond-layer motif.")


def _quantum_well_thicknesses_to_layer_counts(
    *,
    well_material: str,
    barrier_material: str,
    well_thickness_angstrom: float,
    barrier_thickness_angstrom: float,
    spacings: dict[str, float],
    motif_length: int,
) -> tuple[int, int, dict[str, Any]]:
    well_spacing = float(spacings[well_material])
    barrier_spacing = float(spacings[barrier_material])
    candidates: list[tuple[float, int, int, float, float]] = []
    for well_layers in range(2, 41, 2):
        for barrier_layers in range(2, 41, 2):
            if (well_layers + barrier_layers) % motif_length != 0:
                continue
            actual_well = well_layers * well_spacing
            actual_barrier = barrier_layers * barrier_spacing
            error = abs(actual_well - well_thickness_angstrom) + abs(actual_barrier - barrier_thickness_angstrom)
            candidates.append((error, well_layers, barrier_layers, actual_well, actual_barrier))
    if not candidates:
        raise ValueError("No motif-compatible layer counts are available for the requested quantum-well thicknesses.")
    _, well_layers, barrier_layers, actual_well, actual_barrier = min(candidates, key=lambda item: (item[0], item[1] + item[2], item[1], item[2]))
    return (
        well_layers,
        barrier_layers,
        {
            "requested_well_thickness_angstrom": _round_float(well_thickness_angstrom),
            "requested_barrier_thickness_angstrom": _round_float(barrier_thickness_angstrom),
            "actual_well_thickness_angstrom": _round_float(actual_well),
            "actual_barrier_thickness_angstrom": _round_float(actual_barrier),
            "well_thickness_error_angstrom": _round_float(actual_well - well_thickness_angstrom),
            "barrier_thickness_error_angstrom": _round_float(actual_barrier - barrier_thickness_angstrom),
        },
    )


def _quantum_well_single_thickness_to_layer_counts(
    *,
    well_material: str,
    barrier_material: str,
    well_thickness_angstrom: Any | None,
    barrier_thickness_angstrom: Any | None,
    default_well_layers: int,
    default_barrier_layers: int,
    spacings: dict[str, float],
    motif_length: int,
) -> tuple[int, int, dict[str, Any]]:
    if well_thickness_angstrom is None and barrier_thickness_angstrom is None:
        raise ValueError("No quantum-well thickness was requested.")
    well_spacing = float(spacings[well_material])
    barrier_spacing = float(spacings[barrier_material])
    fixed_well_layers = _nearest_even_layer_count(default_well_layers)
    fixed_barrier_layers = _nearest_even_layer_count(default_barrier_layers)
    max_layers = 240
    candidates: list[tuple[float, int, int, float, float]] = []
    if well_thickness_angstrom is not None:
        requested = float(well_thickness_angstrom)
        for well_layers in range(2, max_layers + 1, 2):
            if (well_layers + fixed_barrier_layers) % motif_length != 0:
                continue
            actual_well = well_layers * well_spacing
            actual_barrier = fixed_barrier_layers * barrier_spacing
            candidates.append((abs(actual_well - requested), well_layers, fixed_barrier_layers, actual_well, actual_barrier))
    else:
        requested = float(barrier_thickness_angstrom)
        for barrier_layers in range(2, max_layers + 1, 2):
            if (fixed_well_layers + barrier_layers) % motif_length != 0:
                continue
            actual_well = fixed_well_layers * well_spacing
            actual_barrier = barrier_layers * barrier_spacing
            candidates.append((abs(actual_barrier - requested), fixed_well_layers, barrier_layers, actual_well, actual_barrier))
    if not candidates:
        raise ValueError("No motif-compatible layer count is available for the requested quantum-well thickness.")
    _, well_layers, barrier_layers, actual_well, actual_barrier = min(candidates, key=lambda item: (item[0], item[1] + item[2], item[1], item[2]))
    conversion: dict[str, Any] = {
        "actual_well_thickness_angstrom": _round_float(actual_well),
        "actual_barrier_thickness_angstrom": _round_float(actual_barrier),
    }
    if well_thickness_angstrom is not None:
        requested_well = float(well_thickness_angstrom)
        conversion["requested_well_thickness_angstrom"] = _round_float(requested_well)
        conversion["well_thickness_error_angstrom"] = _round_float(actual_well - requested_well)
    if barrier_thickness_angstrom is not None:
        requested_barrier = float(barrier_thickness_angstrom)
        conversion["requested_barrier_thickness_angstrom"] = _round_float(requested_barrier)
        conversion["barrier_thickness_error_angstrom"] = _round_float(actual_barrier - requested_barrier)
    return well_layers, barrier_layers, conversion


def _nearest_even_layer_count(value: int) -> int:
    count = max(2, int(value))
    if count % 2:
        count += 1
    return count


def _round_float(value: float) -> float:
    return round(float(value), 6)


def _crystal_layer_templates_by_material(model: CrystalSpec, materials: list[str]) -> dict[str, list[list[BasisAtomSpec]]]:
    layers = _sorted_crystal_layers(model)
    contiguous = _contiguous_material_layer_templates(layers, materials)
    if contiguous is not None:
        return contiguous
    result: dict[str, list[list[BasisAtomSpec]]] = {material: [] for material in materials}
    material_elements = {material: _material_elements(material) for material in materials}
    for layer in layers:
        elements = {atom.element for atom in layer}
        for material, elements_for_material in material_elements.items():
            if elements and elements <= elements_for_material:
                result[material].append(layer)
                break
    return {
        material: layer_templates
        for material, layer_templates in result.items()
        if layer_templates
    }


def _contiguous_material_layer_templates(
    layers: list[list[BasisAtomSpec]],
    materials: list[str],
) -> dict[str, list[list[BasisAtomSpec]]] | None:
    if len(materials) < 2 or len(layers) % len(materials) != 0:
        return None
    layers_per_material = len(layers) // len(materials)
    if layers_per_material < 1:
        return None
    result: dict[str, list[list[BasisAtomSpec]]] = {}
    for index, material in enumerate(materials):
        start = index * layers_per_material
        end = start + layers_per_material
        material_layers = layers[start:end]
        material_elements = _material_elements(material)
        layer_elements = {atom.element for layer in material_layers for atom in layer}
        if not layer_elements or not layer_elements <= material_elements:
            return None
        result[material] = material_layers
    return result


def _sorted_crystal_layers(model: CrystalSpec) -> list[list[BasisAtomSpec]]:
    sorted_atoms = sorted(
        model.basis_atoms,
        key=lambda atom: (atom.fractional.z, atom.fractional.x, atom.fractional.y, atom.id),
    )
    layers: list[list[BasisAtomSpec]] = []
    centers: list[float] = []
    tolerance = 1e-5
    for atom in sorted_atoms:
        z_value = float(atom.fractional.z)
        if not layers or abs(z_value - centers[-1]) > tolerance:
            layers.append([atom])
            centers.append(z_value)
        else:
            layers[-1].append(atom)
            centers[-1] = sum(float(item.fractional.z) for item in layers[-1]) / len(layers[-1])
    return [sorted(layer, key=lambda atom: (atom.fractional.x, atom.fractional.y, atom.id)) for layer in layers]


def _material_elements(material: str) -> set[str]:
    return {
        symbol
        for symbol in re.findall(r"[A-Z][a-z]?", material)
        if symbol in ELEMENTS
    }


def _quantum_well_layer_spacing(
    metadata: dict[str, Any],
    material: str,
    templates: list[list[BasisAtomSpec]],
    fallback_c_length: float,
) -> float:
    key = re.sub(r"[^a-z0-9]+", "", material.lower()) + "_reference_lattice_angstrom"
    value = metadata.get(key)
    reference = None
    try:
        reference = float(value)
    except (TypeError, ValueError):
        reference = None
    if reference is not None and templates:
        return reference / len(templates)
    return fallback_c_length / max(sum(1 for _ in templates), 1)


def _apply_new_crystal_composite_operations(
    text: str,
    spec: ModelSpec,
    *,
    skip_supercell: bool = False,
    skip_alloy: bool = False,
    skip_dopant: bool = False,
) -> tuple[ModelSpec, list[str]] | NaturalLanguagePlan | None:
    if not isinstance(spec.model, CrystalSpec):
        return None
    if (spec.metadata or {}).get("domain") != "semiconductor":
        return None

    working = spec
    applied: list[str] = []
    vacancy_applied = False
    dopant_fraction_applied = False

    def apply_operations(operations: list[dict[str, Any]]) -> bool:
        nonlocal working, applied
        if not operations:
            return False
        try:
            patched, diff = apply_semantic_patch(
                working,
                SemanticPatch(project_id=working.project_id, base_revision=working.revision, operations=operations),
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        working = patched
        applied.extend(diff)
        return True

    try:
        commensurate_heterobilayer_match = _match_commensurate_tmd_heterobilayer(text)
        if commensurate_heterobilayer_match is not None:
            apply_operations(
                _commensurate_tmd_heterobilayer_operations(
                    working,
                    commensurate_heterobilayer_match,
                )
            )
        else:
            commensurate_twist_match = _match_commensurate_tmd_twisted_bilayer(text)
            if commensurate_twist_match is not None:
                apply_operations(
                    _commensurate_tmd_twisted_bilayer_operations(working, commensurate_twist_match)
                )

        supercell_match = None if skip_supercell else _match_make_supercell(text)
        if supercell_match is not None:
            apply_operations([{"type": "make_supercell", "matrix": list(supercell_match)}])
        elif not skip_supercell:
            period_match = _match_superlattice_period(text)
            if period_match is not None:
                apply_operations(_superlattice_period_operations(working, period_match))

        pn_junction_match = _match_semiconductor_pn_junction(text)
        if pn_junction_match is not None:
            if supercell_match is None and _semiconductor_pn_junction_needs_default_supercell(working):
                apply_operations([{"type": "make_supercell", "matrix": [2, 1, 1]}])
            apply_operations(_semiconductor_pn_junction_operations(working, pn_junction_match))

        contact_metal = _contact_metal_replacement_operations(text, working)
        if contact_metal is not None:
            apply_operations(contact_metal)

        contact_gap = _contact_gap_geometry_operations(text, working)
        if contact_gap is not None:
            apply_operations(contact_gap)

        contact_thickness = _contact_metal_thickness_geometry_operations(text, working)
        if contact_thickness is not None:
            apply_operations(contact_thickness)

        if contact_gap is None or _contact_text_has_electronic_parameter(text):
            contact_parameters = _contact_parameter_operations(text, working)
            if contact_parameters is not None:
                apply_operations(contact_parameters)

        set_vacuum_match = _match_set_vacuum(text)
        if set_vacuum_match is not None:
            axis, thickness = set_vacuum_match
            apply_operations([{"type": "set_vacuum", "axis": axis, "thickness_angstrom": thickness}])

        vacuum_match = _match_add_vacuum(text)
        if vacuum_match is not None:
            axis, thickness = vacuum_match
            apply_operations([{"type": "add_vacuum", "axis": axis, "thickness_angstrom": thickness}])

        center_slab_axis = _match_center_slab(text)
        if center_slab_axis is not None:
            apply_operations([{"type": "center_slab", "axis": center_slab_axis}])

        if not (working.metadata or {}).get("p_gan_gate_cap"):
            for target_layer, thickness in _match_gate_stack_thicknesses(text):
                apply_operations([{"type": "set_gate_stack_thickness", "target_layer": target_layer, "thickness_angstrom": thickness}])

        lattice_parameter_match = _match_crystal_lattice_parameters(text)
        if lattice_parameter_match is not None:
            apply_operations(_crystal_lattice_parameter_operations(working, lattice_parameter_match))

        layer_translation_match = _match_crystal_layer_translation(text)
        if layer_translation_match is not None:
            apply_operations(_crystal_layer_translation_operations(working, layer_translation_match))

        layer_rotation_match = _match_crystal_layer_rotation(text)
        if layer_rotation_match is not None:
            apply_operations(_crystal_layer_rotation_operations(working, layer_rotation_match))

        strain_match = _match_crystal_strain(text)
        if strain_match is not None:
            axes, percent, mode = strain_match
            apply_operations(_crystal_strain_operations(working, axes, percent, mode))

        vacancy_match = _match_crystal_vacancy(text)
        if vacancy_match is not None:
            apply_operations(_crystal_vacancy_operations(working, vacancy_match))
            vacancy_applied = True
        else:
            auto_vacancy_match = _match_crystal_auto_vacancy(text)
            if auto_vacancy_match is not None:
                requested_element = auto_vacancy_match[0]
                atom = _auto_select_crystal_site(
                    working,
                    requested_site_element=requested_element,
                    operation="vacancy",
                )
                apply_operations(_crystal_vacancy_operations(working, atom.id, auto_selected=True))
                vacancy_applied = True

        antisite_match = _match_crystal_antisite(text)
        if antisite_match is not None:
            atom_id, element = antisite_match
            apply_operations(_crystal_antisite_operations(working, atom_id, element))

        if not skip_dopant:
            dopant_fraction_match = _match_crystal_dopant_fraction(text)
            if dopant_fraction_match is not None:
                host_element, dopant_element, fraction = dopant_fraction_match
                operations = _crystal_dopant_fraction_operations(working, host_element, dopant_element, fraction)
                apply_operations(_with_optional_carrier_intent(working, text, operations, dopant_element, fraction))
                dopant_fraction_applied = True
            else:
                sublattice_dopant_match = _match_crystal_sublattice_dopant(text)
                if sublattice_dopant_match is not None:
                    element, requested_site_element = sublattice_dopant_match
                    atom = _auto_select_crystal_site(
                        working,
                        requested_site_element=requested_site_element,
                        replacing_with=element,
                        operation="sublattice dopant",
                    )
                    operations = _crystal_dopant_operations(
                        working,
                        atom.id,
                        element,
                        auto_selected=True,
                        source="natural_language_crystal_sublattice_dopant",
                    )
                    apply_operations(_with_optional_carrier_intent(working, text, operations, element))
                else:
                    dopant_match = _match_crystal_dopant(text)
                    if dopant_match is not None:
                        atom_id, element = dopant_match
                        operations = _crystal_dopant_operations(working, atom_id, element)
                        apply_operations(_with_optional_carrier_intent(working, text, operations, element))
                    else:
                        auto_dopant_match = _match_crystal_auto_dopant(text)
                        if auto_dopant_match is not None:
                            element, requested_site_element = auto_dopant_match
                            atom = _auto_select_crystal_site(
                                working,
                                requested_site_element=requested_site_element,
                                replacing_with=element,
                                operation="dopant",
                            )
                            operations = _crystal_dopant_operations(working, atom.id, element, auto_selected=True)
                            apply_operations(_with_optional_carrier_intent(working, text, operations, element))
                        else:
                            carrier_type_match = _match_semiconductor_carrier_type(text)
                            if carrier_type_match is not None:
                                carrier_type, fraction = carrier_type_match
                                if _should_record_defect_carrier_intent(text, vacancy_applied):
                                    apply_operations([_defect_carrier_type_metadata_operation(working, carrier_type)])
                                else:
                                    apply_operations(_semiconductor_carrier_type_dopant_operations(working, carrier_type, fraction))

        if not skip_alloy and not dopant_fraction_applied:
            alloy_match = _match_crystal_alloy_fraction(text)
            if alloy_match is not None:
                host_element, alloy_element, fraction = alloy_match
                apply_operations(_crystal_alloy_operations(working, host_element, alloy_element, fraction))

        interstitial_match = _match_crystal_interstitial_fractional(text)
        if interstitial_match is not None:
            atom_id, element, fractional = interstitial_match
            apply_operations(_crystal_interstitial_operations(working, atom_id, element, fractional))

        add_fractional_match = None if interstitial_match is not None else _match_crystal_add_atom_fractional(text)
        if add_fractional_match is not None:
            atom_id, element, fractional = add_fractional_match
            operation: dict[str, Any] = {"type": "add_atom", "element": element, "fractional": fractional}
            if atom_id:
                operation["id"] = atom_id
            apply_operations([operation])

        set_fractional_match = _match_crystal_set_atom_fractional(text)
        if set_fractional_match is not None:
            atom_id, fractional = set_fractional_match
            apply_operations([{"type": "set_atom_position", "atom_id": atom_id, "fractional": fractional}])

        passivation_match = _match_crystal_hydrogen_passivation(text, working)
        if passivation_match is not None:
            operations, _ = passivation_match
            apply_operations(operations)

        castep_match = _match_castep_settings(text, working)
        if castep_match is not None:
            apply_operations([castep_match])
    except ValueError as exc:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id=None,
            notes=[
                "A semiconductor template matched, but one requested inline modification could not be applied safely.",
                str(exc),
                "Use explicit post-supercell atom IDs such as Si1_000 when modifying a supercell created in the same request.",
            ],
        )

    if not applied:
        return None
    return working.model_copy(update={"revision": 0}), applied


def _apply_inline_castep_settings(text: str, spec: ModelSpec) -> tuple[ModelSpec, list[str]] | None:
    operation = _match_castep_settings(text, spec)
    if operation is None:
        return None
    return apply_semantic_patch(
        spec,
        SemanticPatch(project_id=spec.project_id, base_revision=spec.revision, operations=[operation]),
    )


def _infer_substituted_benzene_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    group, _ = _extract_functional_group_request(text)
    for template in SUBSTITUTED_BENZENE_TEMPLATES:
        template_id = str(template["template_id"])
        template_group = str(template["group"])
        term_match = any(_contains_term(text, str(term)) for term in template["terms"])
        benzene_group_match = group == template_group and ("benzene" in text or "苯" in text)
        if not term_match and not benzene_group_match:
            continue
        spec = _substituted_benzene_spec(
            group=template_group,
            template_id=template_id,
            user_request=user_request,
            project_id=project_id,
        )
        if spec is None:
            return None
        return NaturalLanguagePlan(
            kind="spec",
            payload=spec,
            confidence=0.86,
            template_id=template_id,
            notes=[str(template["notes"]), "Generated from benzene plus a deterministic functional-group patch."],
        )
    return None


def _substituted_benzene_spec(
    *,
    group: str,
    template_id: str,
    user_request: str,
    project_id: str | None,
) -> dict[str, Any] | None:
    spec = _load_example("benzene_spec.json")
    chosen_project_id = project_id or _project_id(template_id, user_request)
    metadata = {
        **dict(spec.get("metadata") or {}),
        "nl_template": template_id,
        "nl_source": "local_substituted_benzene_template",
        "nl_base_template": "benzene",
        "nl_functional_group": group,
        "nl_user_request": user_request,
    }
    base_spec = ModelSpec.model_validate({**spec, "project_id": chosen_project_id, "revision": 0, "metadata": metadata})
    if not isinstance(base_spec.model, MoleculeSpec):
        return None
    target = _resolve_functional_group_target(base_spec.model, None)
    if target is None:
        return None
    anchor_id, leaving_id, direction = target
    operations = _functional_group_operations(base_spec.model, group, anchor_id, leaving_id, direction)
    if operations is None:
        return None
    patched_spec, _ = apply_semantic_patch(
        base_spec,
        SemanticPatch(project_id=chosen_project_id, base_revision=0, operations=operations),
    )
    assert isinstance(patched_spec.model, MoleculeSpec)
    molecule = patched_spec.model.model_copy(update={"name": template_id})
    patched_spec = patched_spec.model_copy(update={"revision": 0, "model": molecule, "metadata": metadata})
    return ModelSpec.model_validate(patched_spec.model_dump(mode="json")).model_dump(mode="json")


def _infer_crystal_surface_preparation_patch(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    if not _is_slab_like_crystal_spec(current_spec):
        return None

    working = current_spec
    operations: list[dict[str, Any]] = []
    labels: list[str] = []
    vacancy_applied = False
    dopant_fraction_applied = False

    def apply_group(group_operations: list[dict[str, Any]], label: str) -> None:
        nonlocal working
        patched, _ = apply_semantic_patch(
            working,
            SemanticPatch(project_id=working.project_id, base_revision=working.revision, operations=group_operations),
        )
        working = patched
        operations.extend(group_operations)
        labels.append(label)

    try:
        set_vacuum_match = _match_set_vacuum(text)
        if set_vacuum_match is not None:
            axis, thickness = set_vacuum_match
            apply_group([{"type": "set_vacuum", "axis": axis, "thickness_angstrom": thickness}], f"set vacuum to {thickness:g} A")
        else:
            add_vacuum_match = _match_add_vacuum(text)
            if add_vacuum_match is not None:
                axis, thickness = add_vacuum_match
                apply_group([{"type": "add_vacuum", "axis": axis, "thickness_angstrom": thickness}], f"add {thickness:g} A vacuum")

        center_slab_axis = _match_center_slab(text)
        if center_slab_axis is not None:
            apply_group([{"type": "center_slab", "axis": center_slab_axis}], "center slab")

        layer_translation_match = _match_crystal_layer_translation(text)
        if layer_translation_match is not None:
            translation_operations = _crystal_layer_translation_operations(working, layer_translation_match)
            translation_record = translation_operations[-1]["metadata_updates"]["last_crystal_layer_translation"]
            apply_group(
                translation_operations,
                (
                    f"translate layer {translation_record['layer_index']} by "
                    f"{translation_record['distance_angstrom']:g} Angstrom along "
                    f"{translation_record['translation_axis']}"
                ),
            )

        layer_rotation_match = _match_crystal_layer_rotation(text)
        if layer_rotation_match is not None:
            rotation_operations = _crystal_layer_rotation_operations(working, layer_rotation_match)
            rotation_record = rotation_operations[-1]["metadata_updates"]["last_crystal_layer_rotation"]
            apply_group(
                rotation_operations,
                (
                    f"rotate layer {rotation_record['layer_index']} by "
                    f"{rotation_record['angle_degrees']:g} degrees around "
                    f"{rotation_record['rotation_axis']} as a twist scaffold"
                ),
            )

        vacancy_match = _match_crystal_vacancy(text)
        if vacancy_match is not None:
            apply_group(_crystal_vacancy_operations(working, vacancy_match), f"create vacancy at {vacancy_match}")
            vacancy_applied = True
        else:
            auto_vacancy_match = _match_crystal_auto_vacancy(text)
            if auto_vacancy_match is not None:
                requested_element = auto_vacancy_match[0]
                atom = _auto_select_crystal_site(
                    working,
                    requested_site_element=requested_element,
                    operation="vacancy",
                )
                apply_group(
                    _crystal_vacancy_operations(working, atom.id, auto_selected=True),
                    f"create vacancy at auto-selected {atom.id}",
                )
                vacancy_applied = True

        metadata = dict(current_spec.metadata or {})
        passivation_metadata = metadata.get("passivation") if isinstance(metadata.get("passivation"), dict) else {}
        already_fully_passivated_both = (
            metadata.get("termination") == "fully_hydrogen_passivated_both"
            or (
                passivation_metadata.get("full_passivation_requested") is True
                and set(passivation_metadata.get("surfaces") or []) == {"top", "bottom"}
            )
        )
        passivation_requested = _match_crystal_hydrogen_passivation_request(text) is not None
        passivation_match = (
            None if already_fully_passivated_both and passivation_requested
            else _match_crystal_hydrogen_passivation(text, working)
        )
        if passivation_match is not None:
            passivation_operations, surfaces = passivation_match
            surface_note = " and ".join(surfaces)
            apply_group(passivation_operations, f"hydrogen-passivate {surface_note} surface")
        elif labels and passivation_requested:
            labels.append("confirm hydrogen-passivated slab surface")

        antisite_match = _match_crystal_antisite(text)
        if antisite_match is not None:
            atom_id, element = antisite_match
            apply_group(_crystal_antisite_operations(working, atom_id, element), f"create {element} antisite at {atom_id}")

        dopant_fraction_match = _match_crystal_dopant_fraction(text)
        if dopant_fraction_match is not None:
            host_element, dopant_element, fraction = dopant_fraction_match
            dopant_operations = _crystal_dopant_fraction_operations(working, host_element, dopant_element, fraction)
            apply_group(
                _with_optional_carrier_intent(working, text, dopant_operations, dopant_element, fraction),
                f"create {fraction:.3f} {dopant_element} dopant fraction",
            )
            dopant_fraction_applied = True
        else:
            sublattice_dopant_match = _match_crystal_sublattice_dopant(text)
            if sublattice_dopant_match is not None:
                element, requested_site_element = sublattice_dopant_match
                atom = _auto_select_crystal_site(
                    working,
                    requested_site_element=requested_site_element,
                    replacing_with=element,
                    operation="sublattice dopant",
                )
                dopant_operations = _crystal_dopant_operations(
                    working,
                    atom.id,
                    element,
                    auto_selected=True,
                    source="natural_language_crystal_sublattice_dopant",
                )
                apply_group(
                    _with_optional_carrier_intent(working, text, dopant_operations, element),
                    f"substitute auto-selected {requested_site_element} sublattice atom {atom.id} with {element}",
                )
            else:
                dopant_match = _match_crystal_dopant(text)
                if dopant_match is not None:
                    atom_id, element = dopant_match
                    dopant_operations = _crystal_dopant_operations(working, atom_id, element)
                    apply_group(
                        _with_optional_carrier_intent(working, text, dopant_operations, element),
                        f"substitute {atom_id} with dopant {element}",
                    )
                else:
                    auto_dopant_match = _match_crystal_auto_dopant(text)
                    if auto_dopant_match is not None:
                        element, requested_site_element = auto_dopant_match
                        atom = _auto_select_crystal_site(
                            working,
                            requested_site_element=requested_site_element,
                            replacing_with=element,
                            operation="dopant",
                        )
                        dopant_operations = _crystal_dopant_operations(working, atom.id, element, auto_selected=True)
                        apply_group(
                            _with_optional_carrier_intent(working, text, dopant_operations, element),
                            f"substitute auto-selected crystal atom {atom.id} with dopant {element}",
                        )
                    else:
                        carrier_type_match = _match_semiconductor_carrier_type(text)
                        if carrier_type_match is not None:
                            carrier_type, fraction = carrier_type_match
                            if _should_record_defect_carrier_intent(text, vacancy_applied):
                                apply_group(
                                    [_defect_carrier_type_metadata_operation(working, carrier_type)],
                                    f"record {carrier_type.replace('_', '-')} defect carrier intent",
                                )
                            else:
                                apply_group(
                                    _semiconductor_carrier_type_dopant_operations(working, carrier_type, fraction),
                                    f"create {carrier_type.replace('_', '-')} dopant intent",
                                )

        alloy_match = None if dopant_fraction_applied else _match_crystal_alloy_fraction(text)
        if alloy_match is not None:
            host_element, alloy_element, fraction = alloy_match
            apply_group(
                _crystal_alloy_operations(working, host_element, alloy_element, fraction),
                f"create {fraction:.3f} {alloy_element} alloy fraction",
            )

        castep_match = _match_castep_settings(text, working)
        if castep_match is not None:
            apply_group([castep_match], "set CASTEP calculation settings")
    except ValueError as exc:
        if labels:
            return NaturalLanguagePlan(
                kind="unsupported",
                payload=None,
                confidence=0.0,
                template_id="crystal_surface_preparation",
                notes=[
                    "A slab surface-preparation combination matched, but one requested operation could not be applied safely.",
                    str(exc),
                ],
            )
        return None

    if len(labels) < 2:
        return None
    return _patch_plan(
        operations,
        "crystal_surface_preparation",
        "Prepare slab surface by " + ", ".join(labels) + ".",
    )


def _match_crystal_dopant_dilution(text: str) -> dict[str, Any] | None:
    matrix = _match_make_supercell(text)
    if matrix is None:
        return None

    lower = text.lower()
    english_intent = bool(
        re.search(r"\b(?:dilute|reduce|lower|decrease)\b.{0,60}\b(?:dopant|doping|concentration)\b", lower)
        or re.search(r"\b(?:keep|retain|leave)\s+(?:only\s+)?(?:one|1)\b.{0,40}\b(?:dopant|dopants|doping)\b", lower)
        or re.search(r"\b(?:single|one|1)\s+(?:[A-Za-z]{1,2}\s+)?(?:dopant|doping)\b", lower)
    )
    cjk_intent = (
        "\u63ba\u6742" in text
        and any(
            term in text
            for term in (
                "\u7a00\u91ca",
                "\u964d\u4f4e",
                "\u51cf\u5c11",
                "\u51cf\u5c0f",
                "\u4f4e\u6d53\u5ea6",
                "\u53ea\u4fdd\u7559",
                "\u4ec5\u4fdd\u7559",
                "\u4fdd\u7559\u4e00\u4e2a",
                "\u4fdd\u75591\u4e2a",
            )
        )
    )
    if not english_intent and not cjk_intent:
        return None

    requested_dopant: str | None = None
    auto_dopant = _match_crystal_auto_dopant(text)
    if auto_dopant is not None:
        requested_dopant = auto_dopant[0]
    return {"matrix": list(matrix), "dopant_element": requested_dopant}


def _current_dopant_restore_candidates(
    current_spec: ModelSpec,
    requested_dopant: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        return []
    atoms_by_id = {atom.id: atom for atom in current_spec.model.basis_atoms}
    metadata = dict(current_spec.metadata or {})
    raw_records = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_records:
        raw_records.append(dict(latest))

    candidates: list[dict[str, Any]] = []
    seen_atom_ids: set[str] = set()
    for raw in raw_records:
        atom_id = str(raw.get("atom_id") or raw.get("site_id") or "")
        if not atom_id or atom_id in seen_atom_ids:
            continue
        site_element = _normalize_element(str(raw.get("site_element") or ""))
        dopant_element = _normalize_element(str(raw.get("dopant_element") or raw.get("new_element") or ""))
        atom = atoms_by_id.get(atom_id)
        if atom is None or site_element is None or dopant_element is None:
            continue
        if requested_dopant is not None and dopant_element != requested_dopant:
            continue
        if atom.element != dopant_element or site_element == dopant_element:
            continue
        candidates.append(
            {
                "atom_id": atom_id,
                "site_element": site_element,
                "dopant_element": dopant_element,
            }
        )
        seen_atom_ids.add(atom_id)

    if candidates or requested_dopant is None:
        return candidates

    fallback_site = _preferred_dopant_site_element(current_spec.model, requested_dopant)
    if fallback_site is None:
        return []
    for atom in current_spec.model.basis_atoms:
        if atom.element == requested_dopant and atom.id not in seen_atom_ids:
            candidates.append(
                {
                    "atom_id": atom.id,
                    "site_element": fallback_site,
                    "dopant_element": requested_dopant,
                }
            )
            seen_atom_ids.add(atom.id)
    return candidates


def _crystal_dopant_dilution_operations(
    current_spec: ModelSpec,
    matrix: Sequence[int],
    requested_dopant: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("dopant dilution requires a crystal model.")
    if len(matrix) != 3 or any(int(value) <= 1 for value in matrix):
        raise ValueError("dopant dilution requires an explicit supercell larger than 1x1x1.")

    candidates = _current_dopant_restore_candidates(current_spec, requested_dopant)
    if not candidates:
        raise ValueError("No existing semiconductor dopant site could be identified for dilution.")
    dopant_elements = sorted({str(item["dopant_element"]) for item in candidates})
    if requested_dopant is None and len(dopant_elements) != 1:
        raise ValueError("Multiple dopant elements are present; specify which dopant to dilute.")
    dopant_element = requested_dopant or dopant_elements[0]
    selected_candidates = [item for item in candidates if item["dopant_element"] == dopant_element]
    host_elements = sorted({str(item["site_element"]) for item in selected_candidates})
    if len(host_elements) != 1:
        raise ValueError("Dopant dilution requires one unambiguous host sublattice.")
    host_element = host_elements[0]

    restore_operations = [
        {"type": "substitute_atom", "atom_id": str(item["atom_id"]), "new_element": host_element}
        for item in selected_candidates
    ]
    working = current_spec
    if restore_operations:
        working, _ = apply_semantic_patch(
            working,
            SemanticPatch(project_id=working.project_id, base_revision=working.revision, operations=restore_operations),
        )

    nx, ny, nz = (int(matrix[0]), int(matrix[1]), int(matrix[2]))
    supercell_operation = {"type": "make_supercell", "matrix": [nx, ny, nz]}
    working, _ = apply_semantic_patch(
        working,
        SemanticPatch(project_id=working.project_id, base_revision=working.revision, operations=[supercell_operation]),
    )

    host_candidates = [atom for atom in working.model.basis_atoms if atom.element == host_element]
    if not host_candidates:
        raise ValueError(f"No {host_element} sites are available after dopant dilution supercell generation.")
    selected_atom = sorted(host_candidates, key=lambda atom: _crystal_atom_sort_key(atom.id))[0]
    dopant_operation = {"type": "substitute_atom", "atom_id": selected_atom.id, "new_element": dopant_element}
    actual_fraction = 1.0 / len(host_candidates)
    dopant_site = _dopant_site_record(
        atom_id=selected_atom.id,
        site_element=host_element,
        dopant_element=dopant_element,
        fractional=[
            _round_fractional(selected_atom.fractional.x),
            _round_fractional(selected_atom.fractional.y),
            _round_fractional(selected_atom.fractional.z),
        ],
        auto_selected=True,
        source="natural_language_crystal_dopant_dilution",
    )
    auto_site = {
        "operation": "dopant_dilution",
        "atom_id": selected_atom.id,
        "site_element": host_element,
        "auto_selected_site": True,
        "selection_rule": "first_matching_semiconductor_site_after_restore_supercell",
        "source": "natural_language_auto_site",
        "new_element": dopant_element,
    }
    fraction_record = {
        "host_element": host_element,
        "dopant_element": dopant_element,
        "requested_fraction": round(actual_fraction, 6),
        "requested_percent": round(100.0 * actual_fraction, 6),
        "actual_fraction": round(actual_fraction, 6),
        "actual_percent": round(100.0 * actual_fraction, 6),
        "candidate_site_count": len(host_candidates),
        "substituted_site_count": 1,
        "selected_atom_ids": [selected_atom.id],
        "rounding_error_fraction": 0.0,
        "source": "natural_language_crystal_dopant_dilution",
    }
    dilution_record = {
        "host_element": host_element,
        "dopant_element": dopant_element,
        "restored_atom_ids": [str(item["atom_id"]) for item in selected_candidates],
        "restored_site_count": len(selected_candidates),
        "supercell_matrix": [nx, ny, nz],
        "selected_atom_id": selected_atom.id,
        "actual_fraction": round(actual_fraction, 6),
        "actual_percent": round(100.0 * actual_fraction, 6),
        "source": "natural_language_crystal_dopant_dilution",
    }
    previous_dilutions = [
        dict(item)
        for item in (current_spec.metadata or {}).get("applied_dopant_dilution", []) or []
        if isinstance(item, dict)
    ]
    previous_dilutions.append(dilution_record)

    return [
        *restore_operations,
        supercell_operation,
        dopant_operation,
        {
            "type": "set_metadata",
            "metadata_updates": {
                "applied_dopant_dilution": previous_dilutions,
                "applied_dopant_fraction": [fraction_record],
                "last_applied_dopant_dilution": dilution_record,
                "last_applied_dopant_fraction": fraction_record,
                "last_semiconductor_dopant_site": dopant_site,
                "nl_auto_selected_sites": [auto_site],
                "semiconductor_dopant_sites": [dopant_site],
            },
        },
    ]


def _infer_current_crystal_composite_patch(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    if not isinstance(current_spec.model, CrystalSpec):
        return None
    if (current_spec.metadata or {}).get("domain") != "semiconductor":
        return None

    working = current_spec
    operations: list[dict[str, Any]] = []
    labels: list[str] = []
    vacancy_applied = False
    dopant_fraction_applied = False

    def apply_group(group_operations: list[dict[str, Any]], label: str) -> None:
        nonlocal working
        patched, _ = apply_semantic_patch(
            working,
            SemanticPatch(project_id=working.project_id, base_revision=working.revision, operations=group_operations),
        )
        working = patched
        operations.extend(group_operations)
        labels.append(label)

    def unsupported(exc: ValueError) -> NaturalLanguagePlan:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id="crystal_composite_edit",
            notes=[
                "A current-crystal edit combination matched, but one requested operation could not be applied safely.",
                str(exc),
                "Use explicit post-supercell atom IDs such as Si1_000 when modifying a supercell created in the same request.",
            ],
        )

    dilution_match = _match_crystal_dopant_dilution(text)
    if dilution_match is not None:
        try:
            dilution_operations = _crystal_dopant_dilution_operations(
                current_spec,
                dilution_match["matrix"],
                dilution_match.get("dopant_element"),
            )
            apply_semantic_patch(
                current_spec,
                SemanticPatch(
                    project_id=current_spec.project_id,
                    base_revision=current_spec.revision,
                    operations=dilution_operations,
                ),
            )
        except ValueError as exc:
            return unsupported(exc)
        dopant_label = dilution_match.get("dopant_element") or "existing"
        matrix_label = "x".join(str(value) for value in dilution_match["matrix"])
        return _patch_plan(
            dilution_operations,
            "crystal_dopant_dilution",
            f"Dilute {dopant_label} dopant into a {matrix_label} supercell while keeping one dopant.",
        )

    try:
        commensurate_heterobilayer_match = _match_commensurate_tmd_heterobilayer(text)
        if commensurate_heterobilayer_match is not None:
            heterobilayer_operations = _commensurate_tmd_heterobilayer_operations(
                working,
                commensurate_heterobilayer_match,
            )
            preview_heterobilayer, _ = apply_semantic_patch(
                working,
                SemanticPatch(
                    project_id=working.project_id,
                    base_revision=working.revision,
                    operations=heterobilayer_operations,
                ),
            )
            heterobilayer_record = preview_heterobilayer.metadata["last_commensurate_heterobilayer"]
            apply_group(
                heterobilayer_operations,
                (
                    f"build {heterobilayer_record['bottom_material']}/"
                    f"{heterobilayer_record['top_material']} m={heterobilayer_record['commensurate_m']}, "
                    f"n={heterobilayer_record['commensurate_n']} commensurate TMD heterobilayer at "
                    f"{heterobilayer_record['twist_angle_degrees']:g} degrees with "
                    f"{heterobilayer_record['max_abs_biaxial_strain_percent']:g}% maximum strain"
                ),
            )
        else:
            commensurate_twist_match = _match_commensurate_tmd_twisted_bilayer(text)
            if commensurate_twist_match is not None:
                twist_operations = _commensurate_tmd_twisted_bilayer_operations(
                    working,
                    commensurate_twist_match,
                )
                preview_twist, _ = apply_semantic_patch(
                    working,
                    SemanticPatch(
                        project_id=working.project_id,
                        base_revision=working.revision,
                        operations=twist_operations,
                    ),
                )
                twist_record = preview_twist.metadata["last_commensurate_twist"]
                apply_group(
                    twist_operations,
                    (
                        f"build m={twist_record['commensurate_m']}, n={twist_record['commensurate_n']} "
                        f"commensurate TMD twisted bilayer at "
                        f"{twist_record['twist_angle_degrees']:g} degrees"
                    ),
                )

        supercell_match = _match_make_supercell(text)
        if supercell_match is not None:
            nx, ny, nz = supercell_match
            apply_group([{"type": "make_supercell", "matrix": [nx, ny, nz]}], f"make supercell {nx}x{ny}x{nz}")
        else:
            period_match = _match_superlattice_period(text)
            if period_match is not None:
                apply_group(_superlattice_period_operations(working, period_match), f"make {period_match}-period superlattice")

        p_gan_thickness = _p_gan_gate_cap_thickness_operation(text, working)
        if p_gan_thickness is not None:
            apply_group(
                [p_gan_thickness],
                f"set p-GaN gate/cap thickness to {p_gan_thickness['thickness_angstrom']:g} Angstrom",
            )

        quantum_well_thickness = _quantum_well_thickness_operation(text, working)
        if quantum_well_thickness is not None:
            apply_group(
                [quantum_well_thickness],
                (
                    f"set quantum-well {quantum_well_thickness['target_layer']} thickness "
                    f"to {quantum_well_thickness['thickness_angstrom']:g} Angstrom"
                ),
            )

        lattice_parameter_match = _match_crystal_lattice_parameters(text)
        if lattice_parameter_match is not None:
            changed_fields = ", ".join(lattice_parameter_match)
            apply_group(
                _crystal_lattice_parameter_operations(working, lattice_parameter_match),
                f"set lattice parameters {changed_fields}",
            )

        layer_translation_match = _match_crystal_layer_translation(text)
        if layer_translation_match is not None:
            translation_operations = _crystal_layer_translation_operations(working, layer_translation_match)
            translation_record = translation_operations[-1]["metadata_updates"]["last_crystal_layer_translation"]
            apply_group(
                translation_operations,
                (
                    f"translate layer {translation_record['layer_index']} by "
                    f"{translation_record['distance_angstrom']:g} Angstrom along "
                    f"{translation_record['translation_axis']}"
                ),
            )

        layer_rotation_match = _match_crystal_layer_rotation(text)
        if layer_rotation_match is not None:
            rotation_operations = _crystal_layer_rotation_operations(working, layer_rotation_match)
            rotation_record = rotation_operations[-1]["metadata_updates"]["last_crystal_layer_rotation"]
            apply_group(
                rotation_operations,
                (
                    f"rotate layer {rotation_record['layer_index']} by "
                    f"{rotation_record['angle_degrees']:g} degrees around "
                    f"{rotation_record['rotation_axis']} as a twist scaffold"
                ),
            )

        vacancy_match = _match_crystal_vacancy(text)
        if vacancy_match is not None:
            apply_group(_crystal_vacancy_operations(working, vacancy_match), f"create vacancy at {vacancy_match}")
            vacancy_applied = True
        else:
            auto_vacancy_match = _match_crystal_auto_vacancy(text)
            if auto_vacancy_match is not None:
                requested_element = auto_vacancy_match[0]
                atom = _auto_select_crystal_site(
                    working,
                    requested_site_element=requested_element,
                    operation="vacancy",
                )
                apply_group(
                    _crystal_vacancy_operations(working, atom.id, auto_selected=True),
                    f"create vacancy at auto-selected {atom.id}",
                )
                vacancy_applied = True

        antisite_match = _match_crystal_antisite(text)
        if antisite_match is not None:
            atom_id, element = antisite_match
            apply_group(_crystal_antisite_operations(working, atom_id, element), f"create {element} antisite at {atom_id}")

        dopant_fraction_match = _match_crystal_dopant_fraction(text)
        if dopant_fraction_match is not None:
            host_element, dopant_element, fraction = dopant_fraction_match
            dopant_operations = _crystal_dopant_fraction_operations(working, host_element, dopant_element, fraction)
            apply_group(
                _with_optional_carrier_intent(working, text, dopant_operations, dopant_element, fraction),
                f"create {fraction:.3f} {dopant_element} dopant fraction",
            )
            dopant_fraction_applied = True
        else:
            sublattice_dopant_match = _match_crystal_sublattice_dopant(text)
            if sublattice_dopant_match is not None:
                element, requested_site_element = sublattice_dopant_match
                atom = _auto_select_crystal_site(
                    working,
                    requested_site_element=requested_site_element,
                    replacing_with=element,
                    operation="sublattice dopant",
                )
                dopant_operations = _crystal_dopant_operations(
                    working,
                    atom.id,
                    element,
                    auto_selected=True,
                    source="natural_language_crystal_sublattice_dopant",
                )
                apply_group(
                    _with_optional_carrier_intent(working, text, dopant_operations, element),
                    f"substitute auto-selected {requested_site_element} sublattice atom {atom.id} with {element}",
                )
            else:
                dopant_match = _match_crystal_dopant(text)
                if dopant_match is not None:
                    atom_id, element = dopant_match
                    dopant_operations = _crystal_dopant_operations(working, atom_id, element)
                    apply_group(
                        _with_optional_carrier_intent(working, text, dopant_operations, element),
                        f"substitute {atom_id} with dopant {element}",
                    )
                else:
                    auto_dopant_match = _match_crystal_auto_dopant(text)
                    if auto_dopant_match is not None:
                        element, requested_site_element = auto_dopant_match
                        atom = _auto_select_crystal_site(
                            working,
                            requested_site_element=requested_site_element,
                            replacing_with=element,
                            operation="dopant",
                        )
                        dopant_operations = _crystal_dopant_operations(working, atom.id, element, auto_selected=True)
                        apply_group(
                            _with_optional_carrier_intent(working, text, dopant_operations, element),
                            f"substitute auto-selected crystal atom {atom.id} with dopant {element}",
                        )
                    else:
                        carrier_type_match = _match_semiconductor_carrier_type(text)
                        if carrier_type_match is not None:
                            carrier_type, fraction = carrier_type_match
                            if _should_record_defect_carrier_intent(text, vacancy_applied):
                                apply_group(
                                    [_defect_carrier_type_metadata_operation(working, carrier_type)],
                                    f"record {carrier_type.replace('_', '-')} defect carrier intent",
                                )
                            else:
                                apply_group(
                                    _semiconductor_carrier_type_dopant_operations(working, carrier_type, fraction),
                                    f"create {carrier_type.replace('_', '-')} dopant intent",
                                )

        alloy_match = None if dopant_fraction_applied else _match_crystal_alloy_fraction(text)
        if alloy_match is not None:
            host_element, alloy_element, fraction = alloy_match
            apply_group(
                _crystal_alloy_operations(working, host_element, alloy_element, fraction),
                f"create {fraction:.3f} {alloy_element} alloy fraction",
            )

        castep_match = _match_castep_settings(text, working)
        if castep_match is not None:
            apply_group([castep_match], "set CASTEP calculation settings")
    except ValueError as exc:
        lattice_and_castep = (
            _match_crystal_lattice_parameters(text) is not None
            and _match_castep_settings(text, current_spec) is not None
        )
        if labels or lattice_and_castep:
            return unsupported(exc)
        return None

    if len(labels) < 2:
        return None
    try:
        apply_semantic_patch(
            current_spec,
            SemanticPatch(project_id=current_spec.project_id, base_revision=current_spec.revision, operations=operations),
        )
    except ValueError as exc:
        return unsupported(exc)
    return _patch_plan(
        operations,
        "crystal_composite_edit",
        "Apply current crystal edits: " + ", ".join(labels) + ".",
    )


def _is_slab_like_crystal_spec(current_spec: ModelSpec) -> bool:
    if not isinstance(current_spec.model, CrystalSpec):
        return False
    metadata = dict(current_spec.metadata or {})
    model_name = current_spec.model.name.lower()
    return "surface_orientation" in metadata or "slab" in model_name or "surface" in model_name


def _infer_patch(text: str, current_spec: ModelSpec) -> NaturalLanguagePlan | None:
    if _looks_like_calculation_readiness_request(text):
        return None

    if isinstance(current_spec.model, CrystalSpec):
        dopant_metadata_reconcile = _infer_dopant_metadata_reconcile_patch(text, current_spec)
        if dopant_metadata_reconcile is not None:
            return dopant_metadata_reconcile

        surface_preparation = _infer_crystal_surface_preparation_patch(text, current_spec)
        if surface_preparation is not None:
            return surface_preparation

        composite_patch = _infer_current_crystal_composite_patch(text, current_spec)
        if composite_patch is not None:
            return composite_patch

        quantum_well_thickness = _quantum_well_thickness_operation(text, current_spec)
        if quantum_well_thickness is not None:
            thickness = float(quantum_well_thickness["thickness_angstrom"])
            target = str(quantum_well_thickness["target_layer"])
            return _patch_plan(
                [quantum_well_thickness],
                "quantum_well_thickness",
                f"Set quantum-well {target} thickness to {thickness:g} Angstrom.",
            )

        p_gan_thickness = _p_gan_gate_cap_thickness_operation(text, current_spec)
        if p_gan_thickness is not None:
            thickness = float(p_gan_thickness["thickness_angstrom"])
            return _patch_plan(
                [p_gan_thickness],
                "p_gan_gate_cap_thickness",
                f"Set p-GaN gate/cap thickness to {thickness:g} Angstrom.",
            )

        contact_metal = _contact_metal_replacement_operations(text, current_spec)
        if contact_metal is not None:
            update = contact_metal[-1].get("metadata_updates", {})
            old_metal = (update.get("last_contact_metal_replacement") or {}).get("old_metal")
            new_metal = (update.get("last_contact_metal_replacement") or {}).get("new_metal")
            return _patch_plan(
                contact_metal,
                "metal_semiconductor_contact_metal",
                f"Replace metal/semiconductor contact layer {old_metal} with {new_metal}.",
            )

        contact_gap = _contact_gap_geometry_operations(text, current_spec)
        if contact_gap is not None:
            update = contact_gap[-1].get("metadata_updates", {})
            gap_record = update.get("last_contact_gap_adjustment") or {}
            return _patch_plan(
                contact_gap,
                "metal_semiconductor_contact_gap",
                f"Set metal/semiconductor contact gap to {float(gap_record.get('target_gap_angstrom', update.get('interface_gap_angstrom'))):g} Angstrom.",
            )

        interface_gap = _interface_scaffold_gap_operation(text, current_spec)
        if interface_gap is not None:
            return _patch_plan(
                [interface_gap],
                "interface_scaffold_gap",
                f"Set semiconductor interface scaffold gap to {float(interface_gap['thickness_angstrom']):g} Angstrom.",
            )

        contact_thickness = _contact_metal_thickness_geometry_operations(text, current_spec)
        if contact_thickness is not None:
            update = contact_thickness[-1].get("metadata_updates", {})
            thickness_record = update.get("last_contact_thickness_adjustment") or {}
            return _patch_plan(
                contact_thickness,
                "metal_semiconductor_contact_thickness",
                f"Set metal contact thickness to {float(thickness_record.get('target_thickness_angstrom', update.get('metal_contact_thickness_angstrom'))):g} Angstrom.",
            )

        contact_parameters = _contact_parameter_operations(text, current_spec)
        if contact_parameters is not None:
            changed = sorted(str(key) for key in contact_parameters[0]["metadata_updates"] if key != "last_contact_parameter_update")
            return _patch_plan(
                contact_parameters,
                "metal_semiconductor_contact_parameters",
                "Update metal/semiconductor contact preflight parameters: " + ", ".join(changed) + ".",
            )

    castep_match = _match_castep_settings(text, current_spec)
    if castep_match is not None:
        return _patch_plan(
            [castep_match],
            "castep_settings",
            "Update CASTEP calculation settings without rebuilding geometry.",
        )

    if isinstance(current_spec.model, CrystalSpec):
        passivation_match = _match_crystal_hydrogen_passivation(text, current_spec)
        if passivation_match is not None:
            operations, surfaces = passivation_match
            surface_note = " and ".join(surfaces)
            return _patch_plan(
                operations,
                "crystal_hydrogen_passivation",
                f"Hydrogen-passivate {surface_note} slab surface.",
            )

        interstitial_match = _match_crystal_interstitial_fractional(text)
        if interstitial_match is not None:
            atom_id, element, fractional = interstitial_match
            return _patch_plan(
                _crystal_interstitial_operations(current_spec, atom_id, element, fractional),
                "crystal_interstitial_fractional",
                f"Add {element} interstitial at fractional coordinates.",
            )

        add_fractional_match = _match_crystal_add_atom_fractional(text)
        if add_fractional_match is not None:
            atom_id, element, fractional = add_fractional_match
            payload = {"type": "add_atom", "element": element, "fractional": fractional}
            if atom_id:
                payload["id"] = atom_id
            return _patch_plan(
                [payload],
                "crystal_add_atom_fractional",
                f"Add crystal atom {atom_id or element} at fractional coordinates.",
            )

        set_fractional_match = _match_crystal_set_atom_fractional(text)
        if set_fractional_match is not None:
            atom_id, fractional = set_fractional_match
            return _patch_plan(
                [{"type": "set_atom_position", "atom_id": atom_id, "fractional": fractional}],
                "crystal_set_atom_fractional",
                f"Move crystal atom {atom_id} to fractional coordinates.",
            )

        supercell_match = _match_make_supercell(text)
        if supercell_match is not None:
            nx, ny, nz = supercell_match
            return _patch_plan(
                [{"type": "make_supercell", "matrix": [nx, ny, nz]}],
                "crystal_supercell",
                f"Make crystal supercell {nx}x{ny}x{nz}.",
            )

        period_match = _match_superlattice_period(text)
        if period_match is not None:
            return _patch_plan(
                _superlattice_period_operations(current_spec, period_match),
                "crystal_superlattice_period",
                f"Repeat semiconductor superlattice to {period_match} periods.",
            )

        set_vacuum_match = _match_set_vacuum(text)
        if set_vacuum_match is not None:
            axis, thickness = set_vacuum_match
            return _patch_plan(
                [{"type": "set_vacuum", "axis": axis, "thickness_angstrom": thickness}],
                "crystal_vacuum",
                f"Set vacuum along {axis} to {thickness:g} Angstrom.",
            )

        vacuum_match = _match_add_vacuum(text)
        if vacuum_match is not None:
            axis, thickness = vacuum_match
            return _patch_plan(
                [{"type": "add_vacuum", "axis": axis, "thickness_angstrom": thickness}],
                "crystal_vacuum",
                f"Add {thickness:g} Angstrom vacuum along {axis}.",
            )

        center_slab_axis = _match_center_slab(text)
        if center_slab_axis is not None:
            return _patch_plan(
                [{"type": "center_slab", "axis": center_slab_axis}],
                "crystal_center_slab",
                f"Center slab along {center_slab_axis} in the vacuum region.",
            )

        gate_stack_thickness_matches = _match_gate_stack_thicknesses(text)
        if gate_stack_thickness_matches and _is_gate_stack_spec(current_spec):
            operations = [
                {"type": "set_gate_stack_thickness", "target_layer": target_layer, "thickness_angstrom": thickness}
                for target_layer, thickness in gate_stack_thickness_matches
            ]
            description = "; ".join(
                f"set {target_layer} gate-stack layer thickness to {thickness:g} Angstrom"
                for target_layer, thickness in gate_stack_thickness_matches
            )
            return _patch_plan(
                operations,
                "gate_stack_thickness",
                description[0].upper() + description[1:] + ".",
            )

        layer_translation_match = _match_crystal_layer_translation(text)
        if layer_translation_match is not None:
            try:
                operations = _crystal_layer_translation_operations(current_spec, layer_translation_match)
            except ValueError as exc:
                return NaturalLanguagePlan(
                    kind="unsupported",
                    payload=None,
                    confidence=0.0,
                    template_id="crystal_layer_translation",
                    notes=[
                        "An explicit crystal-layer translation matched but could not be applied safely.",
                        str(exc),
                    ],
                )
            record = operations[-1]["metadata_updates"]["last_crystal_layer_translation"]
            return _patch_plan(
                operations,
                "crystal_layer_translation",
                (
                    f"Translate crystal layer {record['layer_index']} by "
                    f"{record['distance_angstrom']:g} Angstrom along {record['translation_axis']} "
                    "with periodic wrapping."
                ),
            )

        layer_rotation_match = _match_crystal_layer_rotation(text)
        if layer_rotation_match is not None:
            try:
                operations = _crystal_layer_rotation_operations(current_spec, layer_rotation_match)
            except ValueError as exc:
                return NaturalLanguagePlan(
                    kind="unsupported",
                    payload=None,
                    confidence=0.0,
                    template_id="crystal_layer_rotation",
                    notes=[
                        "An explicit crystal-layer rotation matched but could not be applied safely.",
                        str(exc),
                    ],
                )
            record = operations[-1]["metadata_updates"]["last_crystal_layer_rotation"]
            return _patch_plan(
                operations,
                "crystal_layer_rotation",
                (
                    f"Rotate crystal layer {record['layer_index']} by {record['angle_degrees']:g} degrees "
                    f"around {record['rotation_axis']} as a non-commensurate visual-review scaffold; "
                    "build a commensurate supercell and relax before calculation."
                ),
            )

        commensurate_heterobilayer_match = _match_commensurate_tmd_heterobilayer(text)
        if commensurate_heterobilayer_match is not None:
            try:
                operations = _commensurate_tmd_heterobilayer_operations(
                    current_spec,
                    commensurate_heterobilayer_match,
                )
                preview_heterobilayer, _ = apply_semantic_patch(
                    current_spec,
                    SemanticPatch(
                        project_id=current_spec.project_id,
                        base_revision=current_spec.revision,
                        operations=operations,
                    ),
                )
            except ValueError as exc:
                return NaturalLanguagePlan(
                    kind="unsupported",
                    payload=None,
                    confidence=0.0,
                    template_id="commensurate_tmd_heterobilayer",
                    notes=[
                        "A commensurate TMD heterobilayer request matched but could not be applied safely.",
                        str(exc),
                    ],
                )
            record = preview_heterobilayer.metadata["last_commensurate_heterobilayer"]
            return _patch_plan(
                operations,
                "commensurate_tmd_heterobilayer",
                (
                    f"Build {record['bottom_material']}/{record['top_material']} exact integer "
                    f"coincidence heterobilayer with m={record['commensurate_m']}, "
                    f"n={record['commensurate_n']}, twist={record['twist_angle_degrees']:g} degrees, "
                    f"and max biaxial strain={record['max_abs_biaxial_strain_percent']:g}%; "
                    "geometry relaxation remains required."
                ),
            )

        commensurate_twist_match = _match_commensurate_tmd_twisted_bilayer(text)
        if commensurate_twist_match is not None:
            try:
                operations = _commensurate_tmd_twisted_bilayer_operations(
                    current_spec,
                    commensurate_twist_match,
                )
                preview_twist, _ = apply_semantic_patch(
                    current_spec,
                    SemanticPatch(
                        project_id=current_spec.project_id,
                        base_revision=current_spec.revision,
                        operations=operations,
                    ),
                )
            except ValueError as exc:
                return NaturalLanguagePlan(
                    kind="unsupported",
                    payload=None,
                    confidence=0.0,
                    template_id="commensurate_tmd_twisted_bilayer",
                    notes=[
                        "A commensurate TMD twisted-bilayer request matched but could not be applied safely.",
                        str(exc),
                    ],
                )
            record = preview_twist.metadata["last_commensurate_twist"]
            return _patch_plan(
                operations,
                "commensurate_tmd_twisted_bilayer",
                (
                    f"Build exact m={record['commensurate_m']}, n={record['commensurate_n']} "
                    f"commensurate TMD twisted bilayer at {record['twist_angle_degrees']:g} degrees "
                    f"with {record['atom_count']} atoms; geometry relaxation remains required."
                ),
            )

        lattice_parameter_match = _match_crystal_lattice_parameters(text)
        if lattice_parameter_match is not None:
            try:
                operations = _crystal_lattice_parameter_operations(current_spec, lattice_parameter_match)
            except ValueError as exc:
                return NaturalLanguagePlan(
                    kind="unsupported",
                    payload=None,
                    confidence=0.0,
                    template_id="crystal_lattice_parameters",
                    notes=[
                        "An explicit lattice-parameter edit matched but could not be applied safely.",
                        str(exc),
                    ],
                )
            changed_fields = ", ".join(lattice_parameter_match)
            return _patch_plan(
                operations,
                "crystal_lattice_parameters",
                f"Set explicit crystal lattice parameters {changed_fields} while preserving fractional coordinates.",
            )

        strain_match = _match_crystal_strain(text)
        if strain_match is not None:
            axes, percent, mode = strain_match
            return _patch_plan(
                _crystal_strain_operations(current_spec, axes, percent, mode),
                "crystal_strain",
                f"Apply {percent:g}% {mode} strain to lattice axes {', '.join(axes)}.",
            )

        vacancy_match = _match_crystal_vacancy(text)
        if vacancy_match is not None:
            return _patch_plan(
                _crystal_vacancy_operations(current_spec, vacancy_match),
                "crystal_vacancy",
                f"Create vacancy at crystal atom {vacancy_match}.",
            )
        auto_vacancy_match = _match_crystal_auto_vacancy(text)
        if auto_vacancy_match is not None:
            requested_element = auto_vacancy_match[0]
            atom = _auto_select_crystal_site(
                current_spec,
                requested_site_element=requested_element,
                operation="vacancy",
            )
            return _patch_plan(
                _crystal_vacancy_operations(current_spec, atom.id, auto_selected=True),
                "crystal_auto_vacancy",
                f"Create vacancy at auto-selected crystal atom {atom.id}.",
            )

        antisite_match = _match_crystal_antisite(text)
        if antisite_match is not None:
            atom_id, element = antisite_match
            return _patch_plan(
                _crystal_antisite_operations(current_spec, atom_id, element),
                "crystal_antisite",
                f"Create {element} antisite at crystal atom {atom_id}.",
            )

        dopant_fraction_match = _match_crystal_dopant_fraction(text)
        if dopant_fraction_match is not None:
            host_element, dopant_element, fraction = dopant_fraction_match
            operations = _crystal_dopant_fraction_operations(current_spec, host_element, dopant_element, fraction)
            return _patch_plan(
                _with_optional_carrier_intent(current_spec, text, operations, dopant_element, fraction),
                "crystal_dopant_fraction",
                f"Create deterministic {fraction:.3f} {dopant_element} dopant fraction.",
            )

        dopant_match = _match_crystal_dopant(text)
        if dopant_match is not None:
            atom_id, element = dopant_match
            operations = _crystal_dopant_operations(current_spec, atom_id, element)
            return _patch_plan(
                _with_optional_carrier_intent(current_spec, text, operations, element),
                "crystal_dopant",
                f"Substitute crystal atom {atom_id} with dopant {element}.",
            )
        sublattice_dopant_match = _match_crystal_sublattice_dopant(text)
        if sublattice_dopant_match is not None:
            element, requested_site_element = sublattice_dopant_match
            atom = _auto_select_crystal_site(
                current_spec,
                requested_site_element=requested_site_element,
                replacing_with=element,
                operation="sublattice dopant",
            )
            operations = _crystal_dopant_operations(
                current_spec,
                atom.id,
                element,
                auto_selected=True,
                source="natural_language_crystal_sublattice_dopant",
            )
            return _patch_plan(
                _with_optional_carrier_intent(current_spec, text, operations, element),
                "crystal_sublattice_dopant",
                f"Substitute auto-selected {requested_site_element} sublattice atom {atom.id} with dopant {element}.",
            )
        auto_dopant_match = _match_crystal_auto_dopant(text)
        if auto_dopant_match is not None:
            element, requested_site_element = auto_dopant_match
            atom = _auto_select_crystal_site(
                current_spec,
                requested_site_element=requested_site_element,
                replacing_with=element,
                operation="dopant",
            )
            operations = _crystal_dopant_operations(current_spec, atom.id, element, auto_selected=True)
            return _patch_plan(
                _with_optional_carrier_intent(current_spec, text, operations, element),
                "crystal_auto_dopant",
                f"Substitute auto-selected crystal atom {atom.id} with dopant {element}.",
            )

        carrier_type_match = _match_semiconductor_carrier_type(text)
        if carrier_type_match is not None:
            carrier_type, fraction = carrier_type_match
            return _patch_plan(
                _semiconductor_carrier_type_dopant_operations(current_spec, carrier_type, fraction),
                "semiconductor_carrier_type",
                f"Create conservative {carrier_type.replace('_', '-')} semiconductor dopant intent.",
            )

        pn_junction_match = _match_semiconductor_pn_junction(text)
        if pn_junction_match is not None:
            return _patch_plan(
                _semiconductor_pn_junction_patch_operations(current_spec, pn_junction_match),
                "semiconductor_pn_junction",
                "Create deterministic semiconductor p-n junction region dopants.",
            )

        alloy_match = _match_crystal_alloy_fraction(text)
        if alloy_match is not None:
            host_element, alloy_element, fraction = alloy_match
            return _patch_plan(
                _crystal_alloy_operations(current_spec, host_element, alloy_element, fraction),
                "crystal_alloy_fraction",
                f"Create deterministic {fraction:.3f} {alloy_element} alloy fraction.",
            )

    delete_bond_match = _match_delete_bond(text)
    if delete_bond_match is not None:
        atom1, atom2 = delete_bond_match
        return _patch_plan(
            [{"type": "delete_bond", "atom1": atom1, "atom2": atom2}],
            "delete_bond",
            f"Delete bond {atom1}-{atom2}.",
        )

    add_bond_match = _match_add_bond(text)
    if add_bond_match is not None:
        atom1, atom2, bond_type = add_bond_match
        return _patch_plan(
            [{"type": "add_bond", "atom1": atom1, "atom2": atom2, "bond_type": bond_type}],
            "add_bond",
            f"Add {bond_type} bond {atom1}-{atom2}.",
        )

    set_bond_match = _match_set_bond_type(text)
    if set_bond_match is not None:
        atom1, atom2, bond_type = set_bond_match
        return _patch_plan(
            [{"type": "set_bond_type", "atom1": atom1, "atom2": atom2, "bond_type": bond_type}],
            "set_bond_type",
            f"Set bond {atom1}-{atom2} to {bond_type}.",
        )

    functional_group_match = _match_functional_group(text, current_spec)
    if functional_group_match is not None:
        group, operations, target_note = functional_group_match
        return _patch_plan(
            operations,
            f"functional_group_{group}",
            f"Attach {group} group at {target_note}.",
        )

    add_atom_match = _match_add_atom(text, current_spec)
    if add_atom_match is not None:
        atom_id, element, coords, bonded_to, bond_type = add_atom_match
        operations = [{"type": "add_atom", "id": atom_id, "element": element, "xyz_angstrom": coords}]
        if bonded_to:
            operations.append({"type": "add_bond", "atom1": bonded_to, "atom2": atom_id, "bond_type": bond_type})
        return _patch_plan(
            operations,
            "add_atom",
            f"Add atom {atom_id} ({element}) at explicit Cartesian coordinates.",
        )

    delete_match = re.search(r"\b(?:delete|remove)\s+([A-Za-z][A-Za-z0-9_-]*)\b", text, flags=re.IGNORECASE)
    if delete_match is None:
        delete_match = re.search(r"(?:\u5220\u9664|\u79fb\u9664)\s*([A-Za-z][A-Za-z0-9_-]*)", text)
    if delete_match is not None:
        atom_id = delete_match.group(1)
        return _patch_plan(
            [{"type": "delete_atom", "atom_id": atom_id}],
            "delete_atom",
            f"Delete atom {atom_id}.",
        )

    restore_match = re.search(
        r"\b(?:replace|substitute|change)\s+([A-Za-z][A-Za-z0-9_-]*)\s+"
        r"from\s+[A-Za-z]{1,2}\s+(?:back\s+)?(?:to|with|as)\s+([A-Za-z][A-Za-z]?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if restore_match is None:
        restore_match = re.search(
            r"(?:\u628a|\u5c06)?\s*([A-Za-z][A-Za-z0-9_-]*)\s*"
            r"(?:\u4ece\s*[A-Za-z]{1,2}\s*)?"
            r"(?:\u6362\u56de|\u6539\u56de|\u6062\u590d\u4e3a|\u8fd8\u539f\u4e3a)\s*"
            r"([A-Za-z]{1,2})",
            text,
        )
    if restore_match is not None:
        atom_id = restore_match.group(1)
        element = _normalize_element(restore_match.group(2))
        if element is not None:
            return _patch_plan(
                [{"type": "substitute_atom", "atom_id": atom_id, "new_element": element}],
                "substitute_atom",
                f"Restore atom {atom_id} to {element}.",
            )

    substitute_match = re.search(
        r"\b(?:replace|substitute|change)\s+([A-Za-z][A-Za-z0-9_-]*)\s+(?:with|to|as)\s+([A-Za-z][A-Za-z]?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if substitute_match is None:
        substitute_match = re.search(
            r"(?:\u628a|\u5c06)?\s*([A-Za-z][A-Za-z0-9_-]*)\s*"
            r"(?:\u66ff\u6362\u4e3a|\u66ff\u6362\u6210|\u6362\u6210|\u6539\u6210|\u6539\u4e3a|\u53d8\u6210)\s*"
            r"([A-Za-z]{1,2})",
            text,
        )
    if substitute_match is not None:
        atom_id = substitute_match.group(1)
        element = _normalize_element(substitute_match.group(2))
        if element is not None:
            return _patch_plan(
                [{"type": "substitute_atom", "atom_id": atom_id, "new_element": element}],
                "substitute_atom",
                f"Substitute atom {atom_id} with {element}.",
            )

    move_match = re.search(
        r"\b(?:move|set|place)\s+([A-Za-z][A-Za-z0-9_-]*)\s+(?:to|at)\s*\(?\s*"
        r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if move_match is None:
        move_match = re.search(
            r"(?:\u5c06|\u628a)?\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:\u79fb\u52a8\u5230|\u79fb\u5230|\u653e\u5230)\s*"
            r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
    if move_match is not None:
        atom_id = move_match.group(1)
        coords = [float(move_match.group(index)) for index in (2, 3, 4)]
        return _patch_plan(
            [{"type": "set_atom_position", "atom_id": atom_id, "xyz_angstrom": coords}],
            "set_atom_position",
            f"Move atom {atom_id} to explicit Cartesian coordinates.",
        )

    return None


def _match_functional_group(text: str, current_spec: ModelSpec) -> tuple[str, list[dict[str, Any]], str] | None:
    if not isinstance(current_spec.model, MoleculeSpec):
        return None
    group, target_id = _extract_functional_group_request(text)
    if group is None:
        return None
    target = _resolve_functional_group_target(current_spec.model, target_id)
    if target is None:
        return None
    anchor_id, leaving_id, direction = target
    operations = _functional_group_operations(current_spec.model, group, anchor_id, leaving_id, direction)
    if operations is None:
        return None
    target_note = f"{anchor_id}" + (f" replacing {leaving_id}" if leaving_id else "")
    return group, operations, target_note


def _extract_functional_group_request(text: str) -> tuple[str | None, str | None]:
    group_terms = "|".join(sorted((re.escape(term) for term in FUNCTIONAL_GROUP_ALIASES), key=len, reverse=True))
    patterns = [
        rf"\b(?:replace|substitute|change)\s+([A-Za-z][A-Za-z0-9_-]*)\s+(?:with|to|as)\s+(?:a\s+|an\s+)?({group_terms})\b",
        rf"\b(?:add|attach|put)\s+(?:a\s+|an\s+)?({group_terms})\s+(?:group\s+)?(?:to|at|on)\s+([A-Za-z][A-Za-z0-9_-]*)\b",
    ]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            if index == 0:
                return _normalize_functional_group(match.group(2)), match.group(1)
            return _normalize_functional_group(match.group(1)), match.group(2)

    conversion = re.search(rf"\b(?:turn|convert|make)\s+(?:it|this|benzene|molecule)?\s*(?:into|to|as)?\s*(?:a\s+|an\s+)?({group_terms})\b", text, flags=re.IGNORECASE)
    if conversion is not None:
        return _normalize_functional_group(conversion.group(1)), None
    for term in sorted(FUNCTIONAL_GROUP_ALIASES, key=len, reverse=True):
        if _contains_term(text, term):
            return FUNCTIONAL_GROUP_ALIASES[term], None
    return None, None


def _contains_term(text: str, term: str) -> bool:
    """Return True when a natural-language term appears in text."""

    if any(ord(char) > 127 for char in term):
        return term in text
    flags = 0 if _case_sensitive_material_formula(term) else re.IGNORECASE
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, flags=flags) is not None


def _case_sensitive_material_formula(term: str) -> bool:
    """Return True for short formulas whose lowercase spelling is an ordinary word."""

    return bool(term) and term != term.lower() and term.lower() in CASE_SENSITIVE_MATERIAL_FORMULA_LOWERCASES


def _resolve_functional_group_target(
    molecule: MoleculeSpec,
    target_id: str | None,
) -> tuple[str, str | None, tuple[float, float, float]] | None:
    atoms = {atom.id: atom for atom in molecule.atoms}
    bonds_by_atom: dict[str, list[str]] = {atom.id: [] for atom in molecule.atoms}
    for bond in molecule.bonds:
        bonds_by_atom.setdefault(bond.atom1, []).append(bond.atom2)
        bonds_by_atom.setdefault(bond.atom2, []).append(bond.atom1)

    if target_id and target_id in atoms:
        target = atoms[target_id]
        if target.element == "H":
            neighbors = [item for item in bonds_by_atom.get(target_id, []) if atoms.get(item) and atoms[item].element != "H"]
            if len(neighbors) != 1:
                return None
            anchor_id = neighbors[0]
            direction = _direction_between(atoms[anchor_id].xyz_angstrom.as_tuple(), target.xyz_angstrom.as_tuple())
            return anchor_id, target_id, direction
        leaving_id = _first_bonded_hydrogen(target_id, atoms, bonds_by_atom)
        if leaving_id is None:
            return None
        direction = _direction_between(target.xyz_angstrom.as_tuple(), atoms[leaving_id].xyz_angstrom.as_tuple())
        return target_id, leaving_id, direction

    for atom in molecule.atoms:
        if atom.element != "H":
            continue
        neighbors = [item for item in bonds_by_atom.get(atom.id, []) if atoms.get(item) and atoms[item].element != "H"]
        if len(neighbors) == 1:
            anchor_id = neighbors[0]
            direction = _direction_between(atoms[anchor_id].xyz_angstrom.as_tuple(), atom.xyz_angstrom.as_tuple())
            return anchor_id, atom.id, direction
    return None


def _functional_group_operations(
    molecule: MoleculeSpec,
    group: str,
    anchor_id: str,
    leaving_id: str | None,
    direction: tuple[float, float, float],
) -> list[dict[str, Any]] | None:
    atoms = {atom.id: atom for atom in molecule.atoms}
    anchor = atoms.get(anchor_id)
    if anchor is None:
        return None
    used = set(atoms)
    anchor_xyz = anchor.xyz_angstrom.as_tuple()
    perp1, _ = _perpendicular_basis(direction)
    operations: list[dict[str, Any]] = []
    if leaving_id:
        operations.append({"type": "delete_atom", "atom_id": leaving_id})

    def new_atom(element: str, xyz: tuple[float, float, float]) -> str:
        atom_id = _next_atom_id_from_used(element, used)
        used.add(atom_id)
        operations.append({"type": "add_atom", "id": atom_id, "element": element, "xyz_angstrom": _round_xyz(xyz)})
        return atom_id

    if group == "nitro":
        n_id = new_atom("N", _add(anchor_xyz, _scale(direction, 1.47)))
        n_xyz = _add(anchor_xyz, _scale(direction, 1.47))
        o1_id = new_atom("O", _add(_add(n_xyz, _scale(direction, 0.35)), _scale(perp1, 1.10)))
        o2_id = new_atom("O", _add(_add(n_xyz, _scale(direction, 0.35)), _scale(perp1, -1.10)))
        operations.extend(
            [
                {"type": "add_bond", "atom1": anchor_id, "atom2": n_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": n_id, "atom2": o1_id, "bond_type": "Partial double"},
                {"type": "add_bond", "atom1": n_id, "atom2": o2_id, "bond_type": "Partial double"},
            ]
        )
        return operations

    if group == "hydroxyl":
        o_xyz = _add(anchor_xyz, _scale(direction, 1.36))
        o_id = new_atom("O", o_xyz)
        h_id = new_atom("H", _add(o_xyz, _scale(direction, 0.96)))
        operations.extend(
            [
                {"type": "add_bond", "atom1": anchor_id, "atom2": o_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": o_id, "atom2": h_id, "bond_type": "Single"},
            ]
        )
        return operations

    if group == "amino":
        n_xyz = _add(anchor_xyz, _scale(direction, 1.40))
        n_id = new_atom("N", n_xyz)
        h1_id = new_atom("H", _add(_add(n_xyz, _scale(direction, 0.45)), _scale(perp1, 0.90)))
        h2_id = new_atom("H", _add(_add(n_xyz, _scale(direction, 0.45)), _scale(perp1, -0.90)))
        operations.extend(
            [
                {"type": "add_bond", "atom1": anchor_id, "atom2": n_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": n_id, "atom2": h1_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": n_id, "atom2": h2_id, "bond_type": "Single"},
            ]
        )
        return operations

    if group == "methyl":
        c_xyz = _add(anchor_xyz, _scale(direction, 1.50))
        c_id = new_atom("C", c_xyz)
        h1_id = new_atom("H", _add(c_xyz, _scale(direction, 1.09)))
        h2_id = new_atom("H", _add(_add(c_xyz, _scale(direction, -0.36)), _scale(perp1, 0.96)))
        h3_id = new_atom("H", _add(_add(c_xyz, _scale(direction, -0.36)), _scale(perp1, -0.96)))
        operations.extend(
            [
                {"type": "add_bond", "atom1": anchor_id, "atom2": c_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": c_id, "atom2": h1_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": c_id, "atom2": h2_id, "bond_type": "Single"},
                {"type": "add_bond", "atom1": c_id, "atom2": h3_id, "bond_type": "Single"},
            ]
        )
        return operations

    return None



def _match_delete_bond(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(?:delete|remove)\s+bond\s+(?:between\s+)?([A-Za-z][A-Za-z0-9_-]*)\s*(?:-|and|to)\s*([A-Za-z][A-Za-z0-9_-]*)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(r"(?:\u5220\u9664|\u79fb\u9664)\s*([A-Za-z][A-Za-z0-9_-]*)\s*[-\u2013]\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:\u952e)?", text)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _match_add_bond(text: str) -> tuple[str, str, str] | None:
    match = re.search(
        r"\b(?:add|create)\s+(?:(single|double|triple|aromatic|partial double)\s+)?bond\s+"
        r"(?:between\s+)?([A-Za-z][A-Za-z0-9_-]*)\s*(?:-|and|to)\s*([A-Za-z][A-Za-z0-9_-]*)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    bond_type = _normalize_bond_type(match.group(1) or "single")
    if bond_type is None:
        return None
    return match.group(2), match.group(3), bond_type


def _match_set_bond_type(text: str) -> tuple[str, str, str] | None:
    patterns = [
        r"\b(?:set|change|make)\s+bond\s+([A-Za-z][A-Za-z0-9_-]*)\s*(?:-|and|to)\s*([A-Za-z][A-Za-z0-9_-]*)\s+(?:to|as)?\s*(single|double|triple|aromatic|partial double)\b",
        r"\b(?:set|change|make)\s+([A-Za-z][A-Za-z0-9_-]*)\s*(?:-|and|to)\s*([A-Za-z][A-Za-z0-9_-]*)\s+bond\s+(?:to|as)?\s*(single|double|triple|aromatic|partial double)\b",
        r"\b(?:set|change|make)\s+([A-Za-z][A-Za-z0-9_-]*)\s*(?:-|and|to)\s*([A-Za-z][A-Za-z0-9_-]*)\s+(?:to|as)\s+(single|double|triple|aromatic|partial double)\s+bond\b",
        r"(?:\u628a|\u5c06)?\s*([A-Za-z][A-Za-z0-9_-]*)\s*[-\u2013]\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:\u6539\u6210|\u6539\u4e3a|\u8bbe\u4e3a|\u53d8\u6210)\s*(\u5355\u952e|\u53cc\u952e|\u4e09\u952e|\u82b3\u9999\u952e)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            bond_type = _normalize_bond_type(match.group(3))
            if bond_type is not None:
                return match.group(1), match.group(2), bond_type
    return None



def _match_castep_settings(text: str, current_spec: ModelSpec) -> dict[str, Any] | None:
    lowered = text.lower()
    has_trigger = bool(
        re.search(
            r"\b(?:castep|cutoff|k[- ]?point|kpoints?|band\s*structure|band[- ]?gap|electronic\s+bands?|density\s+of\s+states|projected\s+density\s+of\s+states|dos|pdos|optical|optics|phonons?|elastic(?:ity| constants?)?|geometry\s+optimi[sz]ation|geom\s*opt|relaxation|scf|self[- ]?consistent)\b",
            lowered,
        )
    ) or any(
        token in text
        for token in (
            "\u8ba1\u7b97\u5e26\u9699",
            "\u5e26\u9699",
            "\u80fd\u5e26",
            "\u622a\u65ad\u80fd",
            "\u622a\u65ad\u80fd\u91cf",
            "\u5e73\u9762\u6ce2\u622a\u65ad",
            "k\u70b9",
            "k \u70b9",
            "k\u70b9\u7f51\u683c",
            "\u6001\u5bc6\u5ea6",
            "\u6295\u5f71\u6001\u5bc6\u5ea6",
            "\u5149\u5b66\u6027\u8d28",
            "\u58f0\u5b50",
            "\u5f39\u6027\u5e38\u6570",
            "\u51e0\u4f55\u4f18\u5316",
            "\u7ed3\u6784\u4f18\u5316",
            "\u5f1b\u8c6b",
            "\u5355\u70b9\u80fd",
            "\u81ea\u6d3d",
        )
    )
    if not has_trigger:
        return None

    task = _match_castep_task(text)
    cutoff = _match_castep_cutoff(text)
    kpoints = _match_castep_kpoint_grid(text)
    kpoint_separation = None if kpoints is not None else _match_castep_kpoint_separation(text)
    if task is None and cutoff is None and kpoints is None and kpoint_separation is None:
        return None

    simulation = current_spec.simulation
    current_task = getattr(simulation, "task", None) or CastepTask.ENERGY
    operation: dict[str, Any] = {
        "type": "set_castep_energy",
        "task": task or normalize_castep_task(current_task).value,
        "functional": str(getattr(simulation, "functional", None) or "PBE"),
        "quality": str(getattr(simulation, "quality", None) or "Medium"),
    }

    cutoff_value = cutoff if cutoff is not None else getattr(simulation, "cutoff_energy_ev", None)
    if cutoff_value is not None:
        operation["cutoff_energy_ev"] = int(cutoff_value)

    if kpoints is not None:
        operation["kpoints"] = list(kpoints)
    elif kpoint_separation is not None:
        operation["kpoint_separation"] = kpoint_separation
    else:
        existing_kpoints = getattr(simulation, "kpoints", None)
        existing_kpoint_separation = getattr(simulation, "kpoint_separation", None)
        if existing_kpoints is not None:
            operation["kpoints"] = list(existing_kpoints)
        elif existing_kpoint_separation is not None:
            operation["kpoint_separation"] = float(existing_kpoint_separation)

    return operation



def _match_castep_task(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(?:band\s*structure|bandstructure|bands|band[- ]?gap|electronic\s+bands?)\b", lowered) or any(
        token in text for token in ("\u5e26\u9699", "\u80fd\u5e26")
    ):
        return "BandStructure"
    if re.search(
        r"\b(?:projected\s+density\s+of\s+states|projected\s+dos|pdos)\b",
        lowered,
    ) or "\u6295\u5f71\u6001\u5bc6\u5ea6" in text:
        return "ProjectedDensityOfStates"
    if re.search(r"\b(?:density\s+of\s+states|dos)\b", lowered) or "\u6001\u5bc6\u5ea6" in text:
        return "DensityOfStates"
    if re.search(r"\b(?:optical(?:\s+properties)?|optics)\b", lowered):
        return "Optics"
    if any(token in text for token in ("\u5149\u5b66", "\u5149\u5b66\u6027\u8d28")):
        return "Optics"
    if re.search(r"\bphonons?\b", lowered):
        return "Phonon"
    if "\u58f0\u5b50" in text:
        return "Phonon"
    if re.search(r"\belastic(?:ity| constants?)?\b", lowered):
        return "ElasticConstants"
    if any(token in text for token in ("\u5f39\u6027", "\u5f39\u6027\u5e38\u6570")):
        return "ElasticConstants"
    if re.search(r"\b(?:geometry\s+optimi[sz]ation|geom\s*opt|relax(?:ation)?)\b", lowered):
        return "GeometryOptimization"
    if any(token in text for token in ("\u51e0\u4f55\u4f18\u5316", "\u7ed3\u6784\u4f18\u5316", "\u4f18\u5316\u7ed3\u6784", "\u5f1b\u8c6b")):
        return "GeometryOptimization"
    if re.search(r"\b(?:single[- ]point\s+energy|static\s+energy|energy\s+calculation|scf|self[- ]?consistent(?:\s+field)?)\b", lowered):
        return "Energy"
    if any(token in text for token in ("\u5355\u70b9\u80fd", "\u9759\u6001\u80fd\u91cf", "\u80fd\u91cf\u8ba1\u7b97", "\u81ea\u6d3d")):
        return "Energy"
    return None


def _infer_dopant_metadata_reconcile_patch(
    text: str,
    current_spec: ModelSpec,
) -> NaturalLanguagePlan | None:
    """Infer an explicit request to repair stale concrete dopant-site metadata."""

    if not isinstance(current_spec.model, CrystalSpec):
        return None
    normalized = " ".join(text.lower().split())
    action_terms = (
        "reconcile",
        "repair",
        "fix",
        "re-audit",
        "reaudit",
        "recheck",
        "clean up",
        "cleanup",
        "clean stale",
        "remove stale",
        "prune stale",
        "resolve stale",
        "synchronize",
        "sync",
        "align",
        "\u4fee\u590d",
        "\u6e05\u9664",
        "\u8c03\u548c",
        "\u6e05\u7406",
        "\u540c\u6b65",
        "\u5bf9\u9f50",
        "\u91cd\u65b0\u5ba1\u67e5",
        "\u91cd\u65b0\u6838\u67e5",
    )
    target_terms = (
        "dopant metadata",
        "doping metadata",
        "dopant-site metadata",
        "dopant site metadata",
        "dopant sites",
        "dopant record",
        "doping record",
        "stale dopant",
        "stale dopant metadata",
        "current model",
        "current structure",
        "current revision",
        "current project",
        "current crystal",
        "\u63ba\u6742\u5143\u6570\u636e",
        "\u63ba\u6742\u4f4d\u70b9\u5143\u6570\u636e",
        "\u63ba\u6742\u8bb0\u5f55",
        "\u5931\u6548\u63ba\u6742",
        "\u8fc7\u671f\u63ba\u6742",
        "\u5f53\u524d\u6a21\u578b",
        "\u5f53\u524d\u7ed3\u6784",
        "\u5f53\u524d\u4fee\u8ba2",
        "\u5f53\u524d\u9879\u76ee",
    )
    if not any(term in normalized for term in action_terms):
        return None
    if not any(term in normalized for term in target_terms):
        return None
    return NaturalLanguagePlan(
        kind="patch",
        payload={"operations": [{"type": "reconcile_dopant_metadata"}]},
        confidence=0.94,
        template_id="reconcile_dopant_metadata",
        notes=[
            "Reconcile concrete dopant-site metadata with the current crystal atom table.",
            "The operation changes metadata only and creates a non-destructive revision when repair is needed.",
        ],
    )



def _match_castep_cutoff(text: str) -> int | None:
    match = re.search(
        r"(?:cutoff(?:\s+energy)?|plane\s*wave\s*cutoff)\s*(?:to|=|:)?\s*(\d{2,6})\s*(?:ev)?",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(r"(\d{2,6})\s*(?:ev)\s*(?:cutoff|plane\s*wave)", text, flags=re.IGNORECASE)
    if match is None:
        match = re.search(
            r"(?:\u622a\u65ad\u80fd(?:\u91cf)?|\u5e73\u9762\u6ce2(?:\u80fd\u91cf)?\u622a\u65ad)\s*(?:to|=|:|\u4e3a|\u5230|\u8bbe\u7f6e\u4e3a)?\s*(\d{2,6})\s*(?:ev)?",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        match = re.search(
            r"(\d{2,6})\s*(?:ev)\s*(?:\u7684)?\s*(?:\u622a\u65ad\u80fd(?:\u91cf)?|\u5e73\u9762\u6ce2(?:\u80fd\u91cf)?\u622a\u65ad)",
            text,
            flags=re.IGNORECASE,
        )
    return int(match.group(1)) if match is not None else None



def _match_castep_kpoint_grid(text: str) -> tuple[int, int, int] | None:
    match = re.search(
        r"(?:k[- ]?points?|k[- ]?point\s*grid).*?(\d+)\s*[xX\u00d7\uff0a*]\s*(\d+)\s*[xX\u00d7\uff0a*]\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"(?:k\s*\u70b9\s*(?:\u7f51\u683c|\u683c\u70b9)?|\u5012\u7a7a\u95f4\s*k\s*\u70b9).*?(\d+)\s*[xX\u00d7\uff0a*]\s*(\d+)\s*[xX\u00d7\uff0a*]\s*(\d+)",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))



def _match_castep_kpoint_separation(text: str) -> float | None:
    match = re.search(
        r"(?:k[- ]?point\s*(?:separation|spacing)|kpoint\s*(?:separation|spacing))\s*(?:to|=|:)?\s*(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"(?:k\s*\u70b9\s*(?:\u95f4\u8ddd|\u95f4\u9694)?)\s*(?:to|=|:|\u4e3a|\u8bbe\u7f6e\u4e3a)?\s*(\d+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
    return float(match.group(1)) if match is not None else None


def _match_make_supercell(text: str) -> tuple[int, int, int] | None:
    patterns = [
        r"\b(?:make|create|build|generate|set)\s+(?:a\s+)?(?P<x>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<y>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<z>\d+)\s+supercell\b",
        r"\b(?:as|into|to|with)\s+(?:a\s+)?(?P<x>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<y>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<z>\d+)\s+supercell\b",
        r"\bsupercell\s+(?P<x>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<y>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<z>\d+)\b",
        r"(?P<x>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<y>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<z>\d+)\s*(?:n\s*\u578b|p\s*\u578b)?\s*(?:\u7845)?\s*\u8d85\u80de",
        r"(?P<x>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<y>\d+)\s*[xX\u00d7\uff0a*]\s*(?P<z>\d+)\s*(?:[A-Za-z0-9/()._-]{1,32}\s*)+supercell",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        matrix = tuple(int(match.group(axis)) for axis in ("x", "y", "z"))
        if all(1 <= value <= 20 for value in matrix):
            return matrix
    return None


def _match_superlattice_period(text: str) -> int | None:
    patterns = [
        r"\b(?P<count>\d+)\s*[- ]?\s*periods?\b.*\b(?:superlattice|heterostructure|quantum\s+well|mqw)\b",
        r"\b(?P<count>\d+)\s*[- ]?\s*periods?\s+(?:superlattice|heterostructure|quantum\s+well|mqw)\b",
        r"\b(?:superlattice|heterostructure|quantum\s+well|mqw)\s+(?:with\s+)?(?P<count>\d+)\s*[- ]?\s*periods?\b",
        r"\b(?:make|create|build|generate|set)\s+(?:a\s+)?(?P<count>\d+)\s*[- ]?\s*periods?\s+(?:superlattice|heterostructure|quantum\s+well|mqw)\b",
        r"(?P<count>\d+)\s*(?:\u4e2a)?\s*\u5468\u671f.*?(?:\u91cf\u5b50\u9631|\u591a\u91cf\u5b50\u9631|\u8d85\u6676\u683c|\u5f02\u8d28\u7ed3\u6784)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        count = int(match.group("count"))
        if 1 <= count <= 20:
            return count
    return None


def _superlattice_period_operations(current_spec: ModelSpec, period_count: int) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("superlattice period requires a crystal model.")
    metadata = dict(current_spec.metadata or {})
    structure_family = str(metadata.get("structure_family") or current_spec.model.name or "").lower()
    if not metadata.get("interface") and "heterostructure" not in structure_family and "superlattice" not in structure_family:
        raise ValueError("superlattice period requires a heterostructure or superlattice current model.")

    axis = _superlattice_period_axis(metadata)
    matrix = {"a": [period_count, 1, 1], "b": [1, period_count, 1], "c": [1, 1, period_count]}[axis]
    previous = [
        dict(item)
        for item in metadata.get("applied_superlattice_period", [])
        if isinstance(item, dict)
    ]
    previous_total = _previous_superlattice_period_count(metadata)
    estimated_total = previous_total * period_count
    record = {
        "requested_period_count": period_count,
        "period_multiplier": period_count,
        "previous_period_count_estimate": previous_total,
        "estimated_total_period_count": estimated_total,
        "axis": axis,
        "supercell_matrix": matrix,
        "interface": metadata.get("interface"),
        "materials": metadata.get("materials"),
        "source": "natural_language_superlattice_period",
    }
    previous.append(record)
    return [
        {"type": "make_supercell", "matrix": matrix},
        {
            "type": "set_metadata",
            "metadata_updates": {
                "applied_superlattice_period": previous,
                "last_applied_superlattice_period": record,
                "superlattice_period_count": estimated_total,
            },
        },
    ]


def _superlattice_period_axis(metadata: dict[str, Any]) -> str:
    raw_axis = metadata.get("interface_axis") or "c"
    axis = str(raw_axis).strip().lower()
    return {"x": "a", "y": "b", "z": "c"}.get(axis, axis) if axis in {"a", "b", "c", "x", "y", "z"} else "c"


def _previous_superlattice_period_count(metadata: dict[str, Any]) -> int:
    value = metadata.get("superlattice_period_count")
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 1
    return max(count, 1)


def _match_set_vacuum(text: str) -> tuple[str, float] | None:
    unit = r"(?P<unit>nanometers?|nm|angstroms?|ang|a|\u00e5|\u212b|\u57c3|\u7eb3\u7c73)?"
    value = r"(?P<thickness>\d+(?:\.\d+)?)"
    patterns = [
        rf"\b(?:set|make|change|adjust|update|use)\s+(?:the\s+)?vacuum(?:\s+(?:layer|spacing|gap))?(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\s*(?:to|=|:)?\s*{value}\s*{unit}\b",
        rf"\bvacuum(?:\s+(?:layer|spacing|gap))?(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\s*(?:to|=|:)?\s*{value}\s*{unit}\b",
        rf"\b(?:set|make|change|adjust|update|use)\s+{value}\s*{unit}\s+vacuum(?:\s+(?:layer|spacing|gap))?(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\b",
        rf"(?:\u628a|\u5c06)?\s*\u771f\u7a7a\u5c42\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value}\s*{unit}(?:\s*(?:\u6cbf|\u5728)\s*(?P<axis>[abcxyz]))?",
        rf"(?:\u8bbe\u7f6e|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574|\u8c03\u5230)\s*(?:\u6cbf|\u5728)?\s*(?P<axis>[abcxyz])?\s*(?:\u65b9\u5411)?\s*\u771f\u7a7a\u5c42\s*(?:\u5230|\u4e3a|=|:)?\s*{value}\s*{unit}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        thickness = _thickness_value_to_angstrom(float(match.group("thickness")), match.groupdict().get("unit") or "angstrom")
        if 0.0 < thickness <= 5000.0:
            return (match.groupdict().get("axis") or "z").lower(), thickness
    return None


def _match_center_slab(text: str) -> str | None:
    patterns = [
        r"\b(?:center|centre|recenter|re-centre|re-center)\s+(?:the\s+)?(?:slab|surface|film|layer)(?:\s+(?:in|inside|within)\s+(?:the\s+)?vacuum)?(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\b",
        r"\b(?:make|set|adjust)\s+(?:the\s+)?(?:vacuum|slab\s+vacuum)\s+(?:symmetric|symmetrical|two[- ]?sided|balanced)(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\b",
        r"\b(?:symmetric|symmetrical|two[- ]?sided|balanced)\s+vacuum\s+(?:around|for)\s+(?:the\s+)?(?:slab|surface|film|layer)(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\b",
        r"(?:\u628a|\u5c06)?\s*(?:slab|surface|film|layer|\u8584\u819c|\u8868\u9762|\u677f\u5c42)?\s*(?:\u5728|\u653e\u5230)?\s*(?:\u771f\u7a7a\u5c42|\u771f\u7a7a\u533a|\u771f\u7a7a)?\s*(?:\u4e2d)?\s*(?:\u5c45\u4e2d|\u91cd\u65b0\u5c45\u4e2d)",
        r"(?:\u4e24\u4fa7|\u53cc\u4fa7|\u4e0a\u4e0b)\s*\u771f\u7a7a\s*(?:\u5bf9\u79f0|\u5e73\u8861)|\u771f\u7a7a\s*(?:\u4e24\u4fa7|\u53cc\u4fa7|\u4e0a\u4e0b)\s*(?:\u5bf9\u79f0|\u5e73\u8861)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        return (match.groupdict().get("axis") or "z").lower()
    return None


def _match_add_vacuum(text: str) -> tuple[str, float] | None:
    patterns = [
        r"\b(?:add|increase|insert|create)\s+(?P<thickness>\d+(?:\.\d+)?)\s*(?:angstroms?|ang)\s+vacuum(?:\s+(?:layer|spacing))?(?:\s+(?:along|on|in)\s+(?P<axis>[abcxyz]))?\b",
        r"\b(?:add|increase|insert|create)\s+vacuum(?:\s+(?:layer|spacing))?\s+(?:along|on|in)\s+(?P<axis>[abcxyz])\s+(?P<thickness>\d+(?:\.\d+)?)\s*(?:angstroms?|ang)?\b",
        r"(?:\u6dfb\u52a0|\u589e\u52a0|\u52a0\u5165)\s*(?P<thickness>\d+(?:\.\d+)?)\s*(?:\u57c3|angstroms?|ang)?\s*\u771f\u7a7a\u5c42(?:\s*(?:\u6cbf|\u5728)\s*(?P<axis>[abcxyz]))?",
        r"(?:\u6cbf|\u5728)\s*(?P<axis>[abcxyz])\s*(?:\u65b9\u5411)?\s*(?:\u6dfb\u52a0|\u589e\u52a0|\u52a0\u5165)?\s*(?P<thickness>\d+(?:\.\d+)?)\s*(?:\u57c3|angstroms?|ang)?\s*\u771f\u7a7a\u5c42",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        thickness = float(match.group("thickness"))
        if 0.0 < thickness <= 500.0:
            return (match.groupdict().get("axis") or "z").lower(), thickness
    return None


def _match_gate_stack_thickness(text: str) -> tuple[str, float] | None:
    matches = _match_gate_stack_thicknesses(text)
    return matches[0] if matches else None


def _match_gate_stack_thicknesses(text: str) -> list[tuple[str, float]]:
    if "thickness" not in text and "\u539a\u5ea6" not in text:
        return []
    target_pattern = (
        r"gate\s+oxide|metal\s+gate|high-?k|gate|oxide|hfo2|sio2|al2o3|tin|ti\s*n|aluminum|al|"
        r"channel|silicon|si|\u6805\u6c27\u5c42|\u6805\u6c27|\u6c27\u5316\u5c42|\u6c27\u5316\u7269|"
        r"\u9ad8-?k|\u9ad8\u4ecb\u7535|\u6805\u6781|\u91d1\u5c5e\u6805|\u91d1\u5c5e|\u6c9f\u9053|\u7845"
    )
    value_unit = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>nanometers?|nm|angstroms?|ang|a|\u00e5|\u212b|\u57c3|\u7eb3\u7c73)?"
    patterns = [
        rf"\b(?:set|make|change|adjust|update|use)\s+(?:the\s+)?(?P<target>{target_pattern})\s+(?:layer\s+)?thickness\s*(?:to|=|:)?\s*{value_unit}\b",
        rf"\b(?P<target>{target_pattern})\s+(?:layer\s+)?thickness\s*(?:to|=|:)?\s*{value_unit}\b",
        rf"(?:\u628a|\u5c06)?\s*(?:\u8bbe\u7f6e|\u8c03\u6574|\u6539|\u6539\u6210|\u6539\u4e3a|\u8bbe\u4e3a)?\s*(?P<target>{target_pattern})\s*(?:\u5c42)?\s*\u539a\u5ea6\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value_unit}",
    ]
    candidates: list[tuple[int, int, str, float]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            target_layer = _gate_stack_target_layer_from_text(match.group("target"))
            if target_layer is None:
                continue
            thickness = float(match.group("value"))
            unit = (match.groupdict().get("unit") or "").lower()
            if unit in {"nm", "nanometer", "nanometers", "\u7eb3\u7c73"}:
                thickness *= 10.0
            if 0.0 < thickness <= 200.0:
                candidates.append((match.start(), match.end(), target_layer, thickness))
    accepted: list[tuple[int, int, str, float]] = []
    for start, end, target_layer, thickness in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < previous_end and end > previous_start for previous_start, previous_end, _, _ in accepted):
            continue
        accepted.append((start, end, target_layer, thickness))
    return [(target_layer, thickness) for _, _, target_layer, thickness in accepted]


def _gate_stack_target_layer_from_text(target: str) -> str | None:
    compact = re.sub(r"\s+", "", target.strip().lower())
    if compact in {
        "gateoxide",
        "oxide",
        "highk",
        "hfo2",
        "sio2",
        "al2o3",
        "\u6805\u6c27\u5c42",
        "\u6805\u6c27",
        "\u6c27\u5316\u5c42",
        "\u6c27\u5316\u7269",
        "\u9ad8k",
        "\u9ad8-k",
        "\u9ad8\u4ecb\u7535",
    }:
        return "oxide"
    if compact in {"metalgate", "gate", "tin", "aluminum", "al", "\u6805\u6781", "\u91d1\u5c5e\u6805", "\u91d1\u5c5e"}:
        return "gate"
    if compact in {"channel", "silicon", "si", "\u6c9f\u9053", "\u7845"}:
        return "channel"
    return None


def _is_gate_stack_spec(spec: ModelSpec) -> bool:
    if not isinstance(spec.model, CrystalSpec):
        return False
    metadata = dict(spec.metadata or {})
    family = str(metadata.get("structure_family") or "").lower()
    return bool(
        metadata.get("metal_gate_stack")
        or metadata.get("gate_stack")
        or metadata.get("gate_material")
        or metadata.get("gate_oxide_material")
        or "gate stack" in family
        or "mos capacitor" in family
    )


def _is_metal_semiconductor_contact_spec(spec: ModelSpec) -> bool:
    if not isinstance(spec.model, CrystalSpec):
        return False
    metadata = dict(spec.metadata or {})
    family = str(metadata.get("structure_family") or "").lower()
    contact_type = str(metadata.get("contact_type") or "").lower()
    return bool(
        metadata.get("metal_semiconductor_interface")
        or metadata.get("schottky_contact")
        or contact_type in {"schottky", "ohmic", "metal_semiconductor"}
        or "schottky" in family
        or "metal semiconductor" in family
        or "metal-semiconductor" in family
    )


def _contact_metal_replacement_operations(text: str, current_spec: ModelSpec) -> list[dict[str, Any]] | None:
    if not _is_metal_semiconductor_contact_spec(current_spec):
        return None
    if not isinstance(current_spec.model, CrystalSpec):
        return None
    metadata = dict(current_spec.metadata or {})
    old_metal = str(metadata.get("metal_contact_material") or metadata.get("electrode_material") or "").strip()
    if not old_metal:
        return None
    new_metal = _match_contact_metal_replacement(text, old_metal)
    if new_metal is None or new_metal == old_metal:
        return None
    atom_ids = [atom.id for atom in current_spec.model.basis_atoms if atom.element == old_metal]
    if not atom_ids:
        return None

    metadata_updates = _contact_metal_metadata_updates(metadata, old_metal, new_metal, atom_ids)
    return [
        *[
            {"type": "substitute_atom", "atom_id": atom_id, "new_element": new_metal}
            for atom_id in atom_ids
        ],
        {"type": "set_metadata", "metadata_updates": metadata_updates},
    ]


def _match_contact_metal_replacement(text: str, old_metal: str) -> str | None:
    metal = rf"(?P<metal>{ELEMENT_TERM_PATTERN})"
    old = re.escape(old_metal)
    patterns = [
        rf"\b(?:build|create|generate|make)\s+(?:an?\s+)?{metal}\s*/\s*(?:si|silicon)\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b(?:build|create|generate|make)\s+(?:an?\s+)?{metal}\s*[- ]\s*(?:si|silicon)\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{metal}\s*/\s*(?:si|silicon)\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b{metal}\s*[- ]\s*(?:si|silicon)\s+(?:schottky|metal[-\s]?semiconductor)?\s*(?:contact|diode)\b",
        rf"\b(?:build|create|generate|make)\s+(?:an?\s+)?{metal}\s+(?:metal[-\s]?semiconductor|schottky)\s+(?:schottky\s+)?(?:contact|diode)\b",
        rf"\b(?:build|create|generate|make)\s+(?:an?\s+)?{metal}\s+(?:metal[-\s]?semiconductor|schottky)\s+contact\b",
        rf"\b(?:change|switch|replace|set|make|use)\s+(?:the\s+)?(?:metal\s+)?(?:contact|electrode|metal|schottky\s+contact)(?:\s+metal)?\s+(?:to|with|as)\s+{metal}\b",
        rf"\b(?:change|switch|replace)\s+{old}\s+(?:metal\s+)?(?:contact|electrode|layer)?\s+(?:to|with)\s+{metal}\b",
        rf"\b(?:use|make)\s+{metal}\s+(?:as\s+)?(?:the\s+)?(?:metal\s+)?(?:contact|electrode|schottky\s+contact)\b",
        rf"(?:\u6784\u5efa|\u521b\u5efa|\u751f\u6210|\u5236\u4f5c)?\s*{metal}\s*/\s*(?:Si|silicon|\u7845)\s*(?:\u8096\u7279\u57fa|\u91d1\u5c5e[-/\s]?\u534a\u5bfc\u4f53)?\s*(?:\u63a5\u89e6|\u4e8c\u6781\u7ba1)",
        rf"(?:\u628a|\u5c06)?\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u91d1\u5c5e\u5c42|\u7535\u6781|\u91d1\u5c5e)\s*(?:\u6362\u6210|\u6362\u4e3a|\u6539\u6210|\u6539\u4e3a|\u66ff\u6362\u4e3a|\u8bbe\u4e3a|\u8bbe\u7f6e\u4e3a)\s*{metal}",
        rf"(?:\u6362\u6210|\u6362\u4e3a|\u6539\u6210|\u6539\u4e3a|\u4f7f\u7528)\s*{metal}\s*(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u7535\u6781)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        return _normalize_element(match.group("metal"))
    return None


def _contact_metal_metadata_updates(
    metadata: dict[str, Any],
    old_metal: str,
    new_metal: str,
    atom_ids: Sequence[str],
) -> dict[str, Any]:
    semiconductor = str(metadata.get("semiconductor_channel_material") or metadata.get("substrate") or "").strip()
    materials = _replace_material_name(metadata.get("materials"), old_metal, new_metal)
    stack_sequence = _replace_material_name(metadata.get("stack_sequence"), old_metal, new_metal)
    marker_map = {
        str(key): str(value)
        for key, value in dict(metadata.get("material_marker_map") or {}).items()
    }
    marker_map = {
        (new_metal if key == old_metal else key): (new_metal if value == old_metal else value)
        for key, value in marker_map.items()
    }
    marker_map[new_metal] = new_metal
    if old_metal in marker_map and old_metal != new_metal:
        marker_map.pop(old_metal, None)

    updates: dict[str, Any] = {
        "metal_contact_material": new_metal,
        "materials": materials,
        "stack_sequence": stack_sequence,
        "material_marker_map": marker_map,
        "last_contact_metal_replacement": {
            "source": "natural_language_metal_semiconductor_contact_metal",
            "old_metal": old_metal,
            "new_metal": new_metal,
            "replaced_atom_ids": list(atom_ids),
            "replaced_atom_count": len(list(atom_ids)),
            "old_metal_work_function_ev": _contact_metadata_float(metadata.get("metal_work_function_ev")),
            "new_metal_work_function_ev": CONTACT_METAL_WORK_FUNCTION_EV.get(new_metal),
        },
    }
    if CONTACT_METAL_WORK_FUNCTION_EV.get(new_metal) is not None:
        updates["metal_work_function_ev"] = CONTACT_METAL_WORK_FUNCTION_EV[new_metal]
    if semiconductor:
        updates["interface"] = f"{new_metal}/{semiconductor}"
        orientation = metadata.get("interface_orientation")
        if isinstance(orientation, str):
            updates["interface_orientation"] = orientation.replace(old_metal, new_metal)
    updates["last_contact_parameter_update"] = {
        "source": "natural_language_metal_semiconductor_contact_metal",
        "updated_keys": sorted(
            key
            for key in [
                "interface",
                "interface_orientation" if "interface_orientation" in updates else "",
                "material_marker_map",
                "materials",
                "metal_contact_material",
                "metal_work_function_ev" if "metal_work_function_ev" in updates else "",
                "stack_sequence",
            ]
            if key
        ),
    }
    return updates


def _replace_material_name(raw_materials: Any, old_metal: str, new_metal: str) -> list[str]:
    if isinstance(raw_materials, str):
        materials = [raw_materials]
    elif isinstance(raw_materials, Sequence):
        materials = [str(item) for item in raw_materials if str(item)]
    else:
        materials = []
    if not materials:
        return [new_metal]
    replaced = [new_metal if material == old_metal else material for material in materials]
    if new_metal not in replaced:
        replaced.append(new_metal)
    return replaced


def _contact_gap_geometry_operations(text: str, current_spec: ModelSpec) -> list[dict[str, Any]] | None:
    if not _is_metal_semiconductor_contact_spec(current_spec):
        return None
    if not isinstance(current_spec.model, CrystalSpec):
        return None
    if _contact_text_has_electronic_parameter(text):
        return None
    target_gap = _match_contact_length_value(
        text,
        [
            r"(?:interface|contact)\s+(?:gap|spacing|distance)",
            r"(?:metal[-\s]?semiconductor|schottky)\s+(?:gap|spacing|distance)",
            r"(?:\u754c\u9762|\u63a5\u89e6)\s*(?:\u95f4\u8ddd|\u8ddd\u79bb|\u7a7a\u9699)",
        ],
    )
    if target_gap is None:
        return None

    metadata = dict(current_spec.metadata or {})
    metal = str(metadata.get("metal_contact_material") or metadata.get("electrode_material") or "").strip()
    semiconductor = str(metadata.get("semiconductor_channel_material") or metadata.get("substrate") or "").strip()
    if not metal or not semiconductor:
        return None
    axis = _normalize_lattice_axis(str(metadata.get("interface_axis") or metadata.get("surface_axis") or "c"))
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    axis_length = _contact_lattice_axis_length(current_spec.model, axis)
    if axis_index is None or axis_length is None or axis_length <= 0:
        return None

    metal_atoms = _contact_atoms_for_material(current_spec.model, metadata, metal)
    semiconductor_atoms = _contact_atoms_for_material(current_spec.model, metadata, semiconductor)
    if not metal_atoms or not semiconductor_atoms:
        return None
    metal_coords = [_basis_atom_fractional_tuple(atom)[axis_index] for atom in metal_atoms]
    semiconductor_coords = [_basis_atom_fractional_tuple(atom)[axis_index] for atom in semiconductor_atoms]
    metal_min = min(metal_coords)
    semiconductor_top_candidates = [coord for coord in semiconductor_coords if coord <= metal_min + 1e-9]
    if not semiconductor_top_candidates:
        return None
    semiconductor_top = max(semiconductor_top_candidates)
    current_gap = (metal_min - semiconductor_top) * axis_length
    delta_fractional = (target_gap - current_gap) / axis_length

    operations: list[dict[str, Any]] = []
    moved_records: list[dict[str, Any]] = []
    for atom in metal_atoms:
        fractional = list(_basis_atom_fractional_tuple(atom))
        old_value = fractional[axis_index]
        new_value = old_value + delta_fractional
        if new_value < -1e-9 or new_value > 1.0 + 1e-9:
            return None
        fractional[axis_index] = _round_fractional(min(max(new_value, 0.0), 1.0))
        operations.append(
            {
                "type": "set_atom_position",
                "atom_id": atom.id,
                "fractional": fractional,
            }
        )
        moved_records.append(
            {
                "atom_id": atom.id,
                "old_fractional": _round_fractional(old_value),
                "new_fractional": fractional[axis_index],
            }
        )

    adjustment_record = {
        "source": "natural_language_metal_semiconductor_contact_gap",
        "axis": axis,
        "target_gap_angstrom": round(float(target_gap), 6),
        "previous_gap_angstrom": round(float(current_gap), 6),
        "delta_angstrom": round(float(target_gap - current_gap), 6),
        "delta_fractional": round(float(delta_fractional), 6),
        "metal": metal,
        "semiconductor": semiconductor,
        "moved_atom_count": len(moved_records),
        "moved_atoms": moved_records,
    }
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "interface_gap_angstrom": round(float(target_gap), 6),
                "last_contact_gap_adjustment": adjustment_record,
                "last_contact_parameter_update": {
                    "source": "natural_language_metal_semiconductor_contact_gap",
                    "updated_keys": ["interface_gap_angstrom", "last_contact_gap_adjustment"],
                },
            },
        }
    )
    return operations


def _interface_scaffold_gap_operation(text: str, current_spec: ModelSpec) -> dict[str, Any] | None:
    if not isinstance(current_spec.model, CrystalSpec):
        return None
    metadata = dict(current_spec.metadata or {})
    if not metadata.get("interface_scaffold"):
        return None
    target_gap = _match_contact_length_value(
        text,
        [
            r"(?:semiconductor\s+)?interface\s+scaffold\s+(?:gap|spacing|distance)",
            r"semiconductor\s+interface\s+(?:gap|spacing|distance)",
            r"(?:interface|film[-\s]?substrate|nitride[-\s]?sapphire|sapphire[-\s]?nitride)\s+(?:gap|spacing|distance)",
            r"(?:\u754c\u9762|\u8584\u819c\s*\u886c\u5e95|\u84dd\u5b9d\u77f3\s*\u754c\u9762)\s*(?:\u95f4\u8ddd|\u8ddd\u79bb|\u7a7a\u9699)",
        ],
    )
    if target_gap is None:
        return None
    axis = _normalize_lattice_axis(str(metadata.get("interface_axis") or metadata.get("surface_axis") or "c"))
    return {"type": "set_interface_gap", "axis": axis, "thickness_angstrom": target_gap}


def _contact_metal_thickness_geometry_operations(text: str, current_spec: ModelSpec) -> list[dict[str, Any]] | None:
    if not _is_metal_semiconductor_contact_spec(current_spec):
        return None
    if not isinstance(current_spec.model, CrystalSpec):
        return None
    target_thickness = _match_contact_length_value(
        text,
        [
            r"(?:metal\s+)?(?:contact|electrode|metal)\s+(?:layer\s+)?thickness",
            r"(?:schottky|metal[-\s]?semiconductor)\s+(?:metal\s+)?(?:contact\s+)?thickness",
            r"(?:\u91d1\u5c5e\u63a5\u89e6|\u63a5\u89e6\u91d1\u5c5e|\u91d1\u5c5e\u5c42|\u7535\u6781)\s*(?:\u5c42)?\s*\u539a\u5ea6",
        ],
    )
    if target_thickness is None:
        return None

    metadata = dict(current_spec.metadata or {})
    metal = str(metadata.get("metal_contact_material") or metadata.get("electrode_material") or "").strip()
    if not metal:
        return None
    axis = _normalize_lattice_axis(str(metadata.get("interface_axis") or metadata.get("surface_axis") or "c"))
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    axis_length = _contact_lattice_axis_length(current_spec.model, axis)
    if axis_index is None or axis_length is None or axis_length <= 0:
        return None
    metal_atoms = [atom for atom in current_spec.model.basis_atoms if atom.element == metal]
    if len(metal_atoms) < 2:
        return None
    metal_coords = [_basis_atom_fractional_tuple(atom)[axis_index] for atom in metal_atoms]
    metal_min = min(metal_coords)
    metal_max = max(metal_coords)
    current_thickness = (metal_max - metal_min) * axis_length
    if current_thickness <= 1e-9:
        return None
    target_fractional_span = target_thickness / axis_length
    operations: list[dict[str, Any]] = []
    moved_records: list[dict[str, Any]] = []
    for atom in metal_atoms:
        fractional = list(_basis_atom_fractional_tuple(atom))
        old_value = fractional[axis_index]
        relative = (old_value - metal_min) / (metal_max - metal_min)
        new_value = metal_min + relative * target_fractional_span
        if new_value < -1e-9 or new_value > 1.0 + 1e-9:
            return None
        fractional[axis_index] = _round_fractional(min(max(new_value, 0.0), 1.0))
        operations.append(
            {
                "type": "set_atom_position",
                "atom_id": atom.id,
                "fractional": fractional,
            }
        )
        moved_records.append(
            {
                "atom_id": atom.id,
                "old_fractional": _round_fractional(old_value),
                "new_fractional": fractional[axis_index],
            }
        )
    adjustment_record = {
        "source": "natural_language_metal_semiconductor_contact_thickness",
        "axis": axis,
        "target_thickness_angstrom": round(float(target_thickness), 6),
        "previous_thickness_angstrom": round(float(current_thickness), 6),
        "delta_angstrom": round(float(target_thickness - current_thickness), 6),
        "metal": metal,
        "anchored_fractional_min": _round_fractional(metal_min),
        "moved_atom_count": len(moved_records),
        "moved_atoms": moved_records,
    }
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "metal_contact_thickness_angstrom": round(float(target_thickness), 6),
                "last_contact_thickness_adjustment": adjustment_record,
                "last_contact_parameter_update": {
                    "source": "natural_language_metal_semiconductor_contact_thickness",
                    "updated_keys": ["metal_contact_thickness_angstrom", "last_contact_thickness_adjustment"],
                },
            },
        }
    )
    return operations


def _contact_text_has_electronic_parameter(text: str) -> bool:
    return bool(
        re.search(
            r"work\s+function|electron\s+affinity|band[-\s]?gap|barrier|\u529f\u51fd\u6570|\u7535\u5b50\u4eb2\u548c|\u5e26\u9699|\u7981\u5e26|\u52bf\u5792",
            text,
            flags=re.IGNORECASE,
        )
    )


def _contact_lattice_axis_length(crystal: CrystalSpec, axis: str) -> float | None:
    if axis == "a":
        return float(crystal.lattice.a)
    if axis == "b":
        return float(crystal.lattice.b)
    if axis == "c":
        return float(crystal.lattice.c)
    return None


def _contact_atoms_for_material(crystal: CrystalSpec, metadata: dict[str, Any], material: str) -> list[BasisAtomSpec]:
    material = str(material or "").strip()
    if not material:
        return []
    marker_map = {
        str(key): str(value)
        for key, value in dict(metadata.get("material_marker_map") or {}).items()
        if key is not None and value is not None
    }
    material_elements = _material_elements(material)
    atoms: list[BasisAtomSpec] = []
    for atom in crystal.basis_atoms:
        if atom.element == material:
            atoms.append(atom)
            continue
        if marker_map.get(atom.element) == material:
            atoms.append(atom)
            continue
        if not marker_map and atom.element in material_elements:
            atoms.append(atom)
    return atoms


def _basis_atom_fractional_tuple(atom: BasisAtomSpec) -> tuple[float, float, float]:
    return (float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z))


def _contact_parameter_operations(text: str, current_spec: ModelSpec) -> list[dict[str, Any]] | None:
    if not _is_metal_semiconductor_contact_spec(current_spec):
        return None

    metadata = dict(current_spec.metadata or {})
    metal_material = str(metadata.get("metal_contact_material") or metadata.get("electrode_material") or "").strip()
    semiconductor_material = str(
        metadata.get("semiconductor_channel_material") or metadata.get("substrate") or ""
    ).strip()
    updates: dict[str, Any] = {}

    work_function = _match_contact_ev_value(
        text,
        [
            *_contact_material_terms(metal_material, "work function"),
            r"(?:metal|electrode|contact)\s+work\s+function",
            r"work\s+function",
            r"(?:\u91d1\u5c5e|\u7535\u6781|\u63a5\u89e6)?\s*\u529f\u51fd\u6570",
        ],
    )
    if work_function is not None:
        updates["metal_work_function_ev"] = work_function

    electron_affinity = _match_contact_ev_value(
        text,
        [
            *_contact_material_terms(semiconductor_material, "electron affinity"),
            r"(?:semiconductor|channel)\s+electron\s+affinity",
            r"electron\s+affinity",
            r"(?:\u534a\u5bfc\u4f53|\u6c9f\u9053|\u7845)?\s*\u7535\u5b50\u4eb2\u548c(?:\u52bf|\u80fd)?",
        ],
    )
    if electron_affinity is not None:
        updates["semiconductor_electron_affinity_ev"] = electron_affinity

    band_gap = _match_contact_ev_value(
        text,
        [
            *_contact_material_terms(semiconductor_material, "band gap"),
            *_contact_material_terms(semiconductor_material, "bandgap"),
            r"(?:semiconductor|channel)\s+band[-\s]?gap",
            r"band[-\s]?gap",
            r"(?:\u534a\u5bfc\u4f53|\u6c9f\u9053|\u7845)?\s*(?:\u5e26\u9699|\u7981\u5e26\u5bbd\u5ea6)",
        ],
    )
    if band_gap is not None:
        updates["semiconductor_band_gap_ev"] = band_gap

    interface_gap = _match_contact_length_value(
        text,
        [
            r"(?:interface|contact)\s+(?:gap|spacing|distance)",
            r"(?:metal[-\s]?semiconductor|schottky)\s+(?:gap|spacing|distance)",
            r"(?:\u754c\u9762|\u63a5\u89e6)\s*(?:\u95f4\u8ddd|\u8ddd\u79bb|\u7a7a\u9699)",
        ],
    )
    if interface_gap is not None:
        updates["interface_gap_angstrom"] = interface_gap

    target_barrier = _match_schottky_barrier_target(text)
    if target_barrier is not None:
        carrier_type, target_barrier_ev = target_barrier
        electron_affinity_value = _contact_metadata_float(
            updates.get("semiconductor_electron_affinity_ev", metadata.get("semiconductor_electron_affinity_ev"))
        )
        band_gap_value = _contact_metadata_float(
            updates.get("semiconductor_band_gap_ev", metadata.get("semiconductor_band_gap_ev"))
        )
        derived_work_function: float | None = None
        if carrier_type == "n_type" and electron_affinity_value is not None:
            derived_work_function = electron_affinity_value + target_barrier_ev
        elif carrier_type == "p_type" and electron_affinity_value is not None and band_gap_value is not None:
            derived_work_function = electron_affinity_value + band_gap_value - target_barrier_ev
        if derived_work_function is not None and 0.0 <= derived_work_function <= 20.0:
            updates["metal_work_function_ev"] = round(derived_work_function, 6)
            updates["target_schottky_barrier"] = {
                "carrier_type": carrier_type,
                "target_barrier_ev": target_barrier_ev,
                "derived_metal_work_function_ev": round(derived_work_function, 6),
                "semiconductor_electron_affinity_ev": electron_affinity_value,
                "semiconductor_band_gap_ev": band_gap_value,
                "source": "natural_language_schottky_barrier_target",
            }

    if not updates:
        return None

    updates["last_contact_parameter_update"] = {
        "source": "natural_language_metal_semiconductor_contact_parameters",
        "updated_keys": sorted(str(key) for key in updates),
    }
    return [{"type": "set_metadata", "metadata_updates": updates}]


def _contact_metadata_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _contact_material_terms(material: str, property_name: str) -> list[str]:
    if not material:
        return []
    flexible_material = r"\s*".join(re.escape(part.lower()) for part in re.split(r"\s+", material.strip()) if part)
    if not flexible_material:
        return []
    flexible_property = property_name.replace(" ", r"\s+")
    return [rf"{flexible_material}\s+{flexible_property}"]


def _match_schottky_barrier_target(text: str) -> tuple[str, float] | None:
    if not re.search(r"\b(?:schottky|contact|barrier)\b|\u8096\u7279\u57fa|\u52bf\u5792|\u63a5\u89e6", text, flags=re.IGNORECASE):
        return None
    value = r"(?P<value>[+-]?\d+(?:\.\d+)?)"
    unit = r"(?:\s*(?:e\s*v|electron[-\s]?volts?))?"
    carrier_patterns = [
        ("n_type", r"n[-\s]?type|n\s*-\s*type|electron|n\s*\u578b|n\u578b|\u7535\u5b50"),
        ("p_type", r"p[-\s]?type|p\s*-\s*type|hole|p\s*\u578b|p\u578b|\u7a7a\u7a74"),
    ]
    barrier_terms = r"(?:schottky\s+)?(?:contact\s+)?barrier(?:\s+height)?|\u8096\u7279\u57fa\s*\u52bf\u5792|\u63a5\u89e6\s*\u52bf\u5792|\u52bf\u5792"
    for carrier_type, carrier_pattern in carrier_patterns:
        patterns = [
            rf"(?:{carrier_pattern}).{{0,40}}?(?:{barrier_terms}).{{0,20}}?(?:to|=|:|at|as|is|are)?\s*{value}{unit}",
            rf"(?:{barrier_terms}).{{0,40}}?(?:{carrier_pattern}).{{0,20}}?(?:to|=|:|at|as|is|are)?\s*{value}{unit}",
            rf"(?:\u628a|\u5c06)?\s*(?:{carrier_pattern}).{{0,40}}?(?:{barrier_terms}).{{0,20}}?(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value}{unit}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            target = float(match.group("value"))
            if 0.0 <= target <= 10.0:
                return carrier_type, target
    return None


def _match_contact_ev_value(text: str, term_patterns: Sequence[str]) -> float | None:
    value = r"(?P<value>[+-]?\d+(?:\.\d+)?)"
    unit = r"(?:\s*(?:e\s*v|electron[-\s]?volts?))?"
    for term in term_patterns:
        patterns = [
            rf"\b(?:set|make|change|adjust|update|use)\s+(?:the\s+)?(?:{term})\s*(?:to|=|:|at|as)?\s*{value}{unit}",
            rf"(?:{term})\s*(?:is|are|to|=|:|at|as)?\s*{value}{unit}",
            rf"(?:\u628a|\u5c06)?\s*(?:{term})\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value}{unit}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            parsed = float(match.group("value"))
            if 0.0 <= parsed <= 20.0:
                return parsed
    return None


def _match_contact_length_value(text: str, term_patterns: Sequence[str]) -> float | None:
    value = r"(?P<value>\d+(?:\.\d+)?)"
    unit = r"(?P<unit>nanometers?|nm|angstroms?|ang|a|\u00e5|\u212b|\u57c3|\u7eb3\u7c73)?"
    for term in term_patterns:
        patterns = [
            rf"\b(?:set|make|change|adjust|update|use)\s+(?:the\s+)?(?:{term})\s*(?:to|=|:|at|as)?\s*{value}\s*{unit}\b",
            rf"(?:{term})\s*(?:is|are|to|=|:|at|as)?\s*{value}\s*{unit}\b",
            rf"(?:\u628a|\u5c06)?\s*(?:{term})\s*(?:\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u8c03\u6574\u5230|\u8c03\u5230|\u5230|\u4e3a|=|:)?\s*{value}\s*{unit}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            thickness = _thickness_value_to_angstrom(float(match.group("value")), match.groupdict().get("unit") or "angstrom")
            if 0.0 < thickness <= 200.0:
                return thickness
    return None


def _infer_commensurate_tmd_heterobilayer_template(
    text: str,
    *,
    user_request: str,
    project_id: str | None,
) -> NaturalLanguagePlan | None:
    request = _match_commensurate_tmd_heterobilayer(text)
    if request is None:
        return None
    bottom_material = request.get("bottom_material")
    top_material = request.get("top_material")
    if bottom_material is None or top_material is None:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id="commensurate_tmd_heterobilayer",
            notes=[
                "A new commensurate TMD heterobilayer request must name both bottom and top materials.",
                "Supported materials are MoS2, WS2, MoSe2, and WSe2.",
                "Put the bottom material first, for example 'build MoS2/WS2 commensurate twisted heterobilayer with m=2, n=1'.",
            ],
        )
    example_name = TMD_EXAMPLE_BY_MATERIAL.get(str(bottom_material))
    if example_name is None:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id="commensurate_tmd_heterobilayer",
            notes=["The requested bottom TMD material has no reviewed local monolayer template."],
        )

    raw_spec = _load_example(example_name)
    chosen_project_id = project_id or _project_id("commensurate_tmd_heterobilayer", user_request)
    raw_spec = {
        **raw_spec,
        "project_id": chosen_project_id,
        "revision": 0,
        "metadata": {
            **dict(raw_spec.get("metadata") or {}),
            "nl_template": "commensurate_tmd_heterobilayer",
            "nl_source": "local_template_plus_semantic_patch",
            "nl_user_request": user_request,
        },
    }
    base = ModelSpec.model_validate(raw_spec)
    try:
        operations = _commensurate_tmd_heterobilayer_operations(base, request)
        built, diff = apply_semantic_patch(
            base,
            SemanticPatch(
                project_id=base.project_id,
                base_revision=base.revision,
                operations=operations,
            ),
        )
    except ValueError as exc:
        return NaturalLanguagePlan(
            kind="unsupported",
            payload=None,
            confidence=0.0,
            template_id="commensurate_tmd_heterobilayer",
            notes=[
                "A commensurate TMD heterobilayer request matched but could not be constructed safely.",
                str(exc),
            ],
        )
    built = built.model_copy(
        update={
            "revision": 0,
            "metadata": {
                **dict(built.metadata or {}),
                "nl_composite_operations": diff,
            },
        }
    )
    receipt = built.metadata["last_commensurate_heterobilayer"]
    return NaturalLanguagePlan(
        kind="spec",
        payload=built.model_dump(mode="json"),
        confidence=0.9,
        template_id="commensurate_tmd_heterobilayer",
        notes=[
            (
                f"Built {bottom_material}/{top_material} exact integer coincidence heterobilayer "
                f"with m={receipt['commensurate_m']}, n={receipt['commensurate_n']}, "
                f"twist={receipt['twist_angle_degrees']:g} degrees, and "
                f"max biaxial strain={receipt['max_abs_biaxial_strain_percent']:g}%."
            ),
            "The cell is periodic after the recorded strain partition and remains a pre-relaxation scaffold.",
            "Same-window hot-load is supported; production calculations remain blocked until reviewed geometry relaxation.",
        ],
    )


def _match_crystal_layer_translation(text: str) -> dict[str, Any] | None:
    if re.search(
        r"\b(?:shift|translate|slide|displace|move)\b|(?:平移|横移|移动)",
        text,
        flags=re.IGNORECASE,
    ) is None:
        return None

    target: dict[str, Any] | None = None
    for pattern in (
        r"\blayer\s*#?\s*(?P<index>\d+)\b",
        r"第\s*(?P<index>\d+)\s*层",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            target = {"kind": "index", "layer_index": int(match.group("index"))}
            break
    if target is None:
        edge_match = re.search(
            r"\b(?P<edge>top(?:most)?|bottom(?:most)?)\s+layer\b|(?P<cjk_edge>最上层|顶层|最下层|底层)",
            text,
            flags=re.IGNORECASE,
        )
        if edge_match is not None:
            raw_edge = str(edge_match.group("edge") or edge_match.group("cjk_edge") or "").lower()
            edge = "top" if raw_edge.startswith("top") or raw_edge in {"最上层", "顶层"} else "bottom"
            target = {"kind": "edge", "edge": edge}
    if target is None:
        return None

    axis_match = re.search(
        r"\b(?:along|in)\s+(?:the\s+)?(?P<axis>[abcxyz])(?:\s*[- ]?axis|\s+direction)?\b|"
        r"(?:沿|沿着)\s*(?P<cjk_axis>[abcxyz])\s*(?:轴|方向)?|"
        r"(?P<plain_axis>[abcxyz])\s*(?:轴|方向)",
        text,
        flags=re.IGNORECASE,
    )
    if axis_match is None:
        return None
    raw_axis = axis_match.group("axis") or axis_match.group("cjk_axis") or axis_match.group("plain_axis")

    number = r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    unit = r"(?P<unit>nanometers?|nm|angstroms?|ang|a|å|Å|Å|埃)"
    distance_match = None
    for pattern in (
        rf"\b(?:by|through)\s*{number}\s*{unit}\b",
        rf"(?:平移|横移|移动)\s*{number}\s*{unit}",
        rf"{number}\s*{unit}",
    ):
        distance_match = re.search(pattern, text, flags=re.IGNORECASE)
        if distance_match is not None:
            break
    if distance_match is None:
        return None

    distance = _thickness_value_to_angstrom(
        float(distance_match.group("value")),
        distance_match.group("unit"),
    )
    return {
        **target,
        "axis": _normalize_lattice_axis(str(raw_axis)),
        "distance_angstrom": round(float(distance), 6),
    }


def _match_crystal_layer_rotation(text: str) -> dict[str, Any] | None:
    if re.search(
        r"\b(?:rotate|twist)\b|(?:\u65cb\u8f6c|\u626d\u8f6c|\u626d\u89d2)",
        text,
        flags=re.IGNORECASE,
    ) is None:
        return None

    target: dict[str, Any] | None = None
    for pattern in (
        r"\blayer\s*#?\s*(?P<index>\d+)\b",
        r"\u7b2c\s*(?P<index>\d+)\s*\u5c42",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            target = {"kind": "index", "layer_index": int(match.group("index"))}
            break
    if target is None:
        edge_match = re.search(
            r"\b(?P<edge>top(?:most)?|bottom(?:most)?)\s+layer\b|"
            r"(?P<cjk_edge>\u6700\u4e0a\u5c42|\u9876\u5c42|\u6700\u4e0b\u5c42|\u5e95\u5c42)",
            text,
            flags=re.IGNORECASE,
        )
        if edge_match is not None:
            raw_edge = str(edge_match.group("edge") or edge_match.group("cjk_edge") or "").lower()
            edge = "top" if raw_edge.startswith("top") or raw_edge in {"\u6700\u4e0a\u5c42", "\u9876\u5c42"} else "bottom"
            target = {"kind": "edge", "edge": edge}
    if target is None:
        return None

    value = r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    angle_match = None
    for pattern in (
        rf"\b(?:by|through|through an angle of|at an angle of)\s*{value}\s*(?:degrees?|deg|\u00b0)\b",
        rf"\b(?:twist|rotation)\s+angle\s*(?:of|=|:)?\s*{value}\s*(?:degrees?|deg|\u00b0)\b",
        rf"(?:\u65cb\u8f6c|\u626d\u8f6c|\u626d\u89d2)\s*{value}\s*(?:\u5ea6|\u00b0)",
        rf"{value}\s*(?:\u5ea6|\u00b0)",
    ):
        angle_match = re.search(pattern, text, flags=re.IGNORECASE)
        if angle_match is not None:
            break
    if angle_match is None:
        return None

    axis_match = re.search(
        r"\b(?:around|about)\s+(?:the\s+)?(?P<axis>[abcxyz])(?:\s*[- ]?axis)?\b|"
        r"(?:\u7ed5|\u7ed5\u7740)\s*(?P<cjk_axis>[abcxyz])\s*(?:\u8f74)?",
        text,
        flags=re.IGNORECASE,
    )
    raw_axis = (
        axis_match.group("axis") or axis_match.group("cjk_axis")
        if axis_match is not None
        else None
    )
    return {
        **target,
        "axis": _normalize_lattice_axis(str(raw_axis)) if raw_axis else None,
        "angle_degrees": round(float(angle_match.group("value")), 6),
    }


def _match_commensurate_tmd_twist_request(text: str) -> dict[str, Any] | None:
    if re.search(r"\bcommensurate\b|\u5171\u683c", text, flags=re.IGNORECASE) is None:
        return None
    if re.search(
        r"\b(?:twist(?:ed)?|moire|moir[e\u00e9]|bilayer)\b|"
        r"(?:\u626d\u8f6c|\u626d\u89d2|\u83ab\u5c14|\u53cc\u5c42)",
        text,
        flags=re.IGNORECASE,
    ) is None:
        return None

    indices: tuple[int, int] | None = None
    for pattern in (
        r"\bm\s*=\s*(?P<m>\d+)\s*[,;/\s]+\s*n\s*=\s*(?P<n>\d+)\b",
        r"\bn\s*=\s*(?P<n>\d+)\s*[,;/\s]+\s*m\s*=\s*(?P<m>\d+)\b",
        r"\(\s*m\s*,\s*n\s*\)\s*=\s*\(\s*(?P<m>\d+)\s*,\s*(?P<n>\d+)\s*\)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            indices = (int(match.group("m")), int(match.group("n")))
            break

    value = r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    angle: float | None = None
    for pattern in (
        rf"\b(?:twist(?:ed)?|rotation)\s*(?:angle)?\s*(?:of|=|:)?\s*{value}\s*(?:degrees?|deg|\u00b0)",
        rf"{value}\s*(?:degrees?|deg|\u00b0)\s*(?:commensurate\s+)?(?:twist(?:ed)?|rotation)",
        rf"(?:\u626d\u89d2|\u626d\u8f6c\u89d2|\u65cb\u8f6c\u89d2)\s*(?:=|:)?\s*{value}\s*(?:\u5ea6|\u00b0)",
        rf"{value}\s*(?:\u5ea6|\u00b0)\s*(?:\u5171\u683c)?(?:\u626d\u8f6c|\u626d\u89d2)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            angle = round(float(match.group("value")), 9)
            break

    interlayer_distance: float | None = None
    for pattern in (
        rf"\binterlayer\s+(?:distance|spacing|separation)\s*(?:of|=|:)?\s*{value}\s*(?P<unit>angstroms?|ang|a|nm|nanometers?)\b",
        rf"(?:\u5c42\u95f4\u8ddd|\u5c42\u95f4\u8ddd\u79bb|\u5c42\u95f4\u95f4\u8ddd)\s*(?:=|:|\u4e3a|\u8bbe\u4e3a)?\s*{value}\s*(?P<unit>\u57c3|\u00c5|a|nm|\u7eb3\u7c73)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            interlayer_distance = float(match.group("value"))
            unit = str(match.group("unit") or "").lower()
            if unit in {"nm", "nanometer", "nanometers", "\u7eb3\u7c73"}:
                interlayer_distance *= 10.0
            interlayer_distance = round(interlayer_distance, 9)
            break

    orientation = None
    if re.search(r"\bcounter[-\s]?clockwise\b|\banticlockwise\b|\u9006\u65f6\u9488", text, flags=re.IGNORECASE):
        orientation = "counterclockwise"
    elif re.search(r"\bclockwise\b|\u987a\u65f6\u9488", text, flags=re.IGNORECASE):
        orientation = "clockwise"
    elif angle is not None:
        orientation = "counterclockwise" if angle > 0 else "clockwise"

    return {
        "indices": indices,
        "requested_angle_degrees": angle,
        "interlayer_distance_angstrom": interlayer_distance,
        "twist_orientation": orientation,
    }


def _match_commensurate_tmd_twisted_bilayer(text: str) -> dict[str, Any] | None:
    request = _match_commensurate_tmd_twist_request(text)
    if request is None:
        return None
    if len(_mentioned_tmd_materials(text)) >= 2 or _looks_like_tmd_heterobilayer_text(text):
        return None
    return request


def _match_commensurate_tmd_heterobilayer(text: str) -> dict[str, Any] | None:
    request = _match_commensurate_tmd_twist_request(text)
    if request is None:
        return None
    materials = _mentioned_tmd_materials(text)
    hetero_intent = _looks_like_tmd_heterobilayer_text(text)
    if len(materials) < 2 and not (hetero_intent and len(materials) == 1):
        return None
    if len(materials) > 2:
        return {
            **request,
            "bottom_material": materials[0],
            "top_material": materials[1],
            "extra_materials": materials[2:],
        }

    strain_policy = "balanced"
    if re.search(
        r"\b(?:bottom|lower)\s+(?:layer\s+)?(?:fixed|unstrained)\b|"
        r"\b(?:fix|keep)\s+(?:the\s+)?(?:bottom|lower)\s+layer\b|"
        r"(?:\u56fa\u5b9a\u5e95\u5c42|\u5e95\u5c42\u4e0d\u5e94\u53d8)",
        text,
        flags=re.IGNORECASE,
    ):
        strain_policy = "bottom_fixed"
    elif re.search(
        r"\b(?:top|upper)\s+(?:layer\s+)?(?:fixed|unstrained)\b|"
        r"\b(?:fix|keep)\s+(?:the\s+)?(?:top|upper)\s+layer\b|"
        r"(?:\u56fa\u5b9a\u9876\u5c42|\u9876\u5c42\u4e0d\u5e94\u53d8)",
        text,
        flags=re.IGNORECASE,
    ):
        strain_policy = "top_fixed"

    max_strain_percent = None
    max_strain_match = re.search(
        r"\bmax(?:imum)?\s+(?:biaxial\s+)?strain\s*(?:of|=|:)?\s*"
        r"(?P<value>\d+(?:\.\d+)?)\s*%|"
        r"(?:\u6700\u5927|\u4e0a\u9650)(?:\u53cc\u8f74)?\u5e94\u53d8\s*(?:=|:|\u4e3a)?\s*"
        r"(?P<cjk_value>\d+(?:\.\d+)?)\s*(?:%|\uff05)",
        text,
        flags=re.IGNORECASE,
    )
    if max_strain_match is not None:
        max_strain_percent = float(
            max_strain_match.group("value") or max_strain_match.group("cjk_value")
        )

    return {
        **request,
        "bottom_material": materials[0] if len(materials) >= 2 else None,
        "top_material": materials[1] if len(materials) >= 2 else materials[0],
        "strain_policy": strain_policy,
        "max_strain_percent": max_strain_percent,
        "extra_materials": [],
    }


def _looks_like_tmd_heterobilayer_text(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:heterobilayer|hetero[-\s]?bilayer|van\s+der\s+waals\s+hetero(?:structure|bilayer)|"
            r"tmd\s+hetero(?:structure|bilayer))\b|"
            r"(?:\u5f02\u8d28\u53cc\u5c42|\u5f02\u8d28\u4e8c\u7ef4\u53cc\u5c42|\u8303\u5fb7\u534e\u5f02\u8d28\u7ed3)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _mentioned_tmd_materials(text: str) -> list[str]:
    patterns = {
        "MoS2": (
            r"(?<![a-z0-9])mos2(?![a-z0-9])",
            r"\bmolybdenum\s+disul(?:fide|phide)\b",
            r"\u4e8c\u786b\u5316\u94bc",
        ),
        "WS2": (
            r"(?<![a-z0-9])ws2(?![a-z0-9])",
            r"\btungsten\s+disul(?:fide|phide)\b",
            r"\u4e8c\u786b\u5316\u94a8",
        ),
        "MoSe2": (
            r"(?<![a-z0-9])mose2(?![a-z0-9])",
            r"\bmolybdenum\s+diselenide\b",
            r"\u4e8c\u7852\u5316\u94bc",
        ),
        "WSe2": (
            r"(?<![a-z0-9])wse2(?![a-z0-9])",
            r"\btungsten\s+diselenide\b",
            r"\u4e8c\u7852\u5316\u94a8",
        ),
    }
    mentions: list[tuple[int, str]] = []
    for material, material_patterns in patterns.items():
        positions = [
            match.start()
            for pattern in material_patterns
            for match in re.finditer(pattern, text, flags=re.IGNORECASE)
        ]
        if positions:
            mentions.append((min(positions), material))
    return [material for _position, material in sorted(mentions)]


def _commensurate_tmd_twisted_bilayer_operations(
    current_spec: ModelSpec,
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("commensurate twisted bilayer requires a crystal model.")
    metadata = dict(current_spec.metadata or {})
    family = str(metadata.get("structure_family") or "").lower()
    if metadata.get("domain") != "semiconductor" or "tmd" not in family or "monolayer" not in family:
        raise ValueError("commensurate twisted bilayer requires a pristine semiconductor TMD monolayer.")

    max_atoms = COMMENSURATE_TWIST_DEFAULT_MAX_ATOMS
    indices = request.get("indices")
    requested_angle = request.get("requested_angle_degrees")
    if indices is None:
        if requested_angle is None:
            raise ValueError(
                "commensurate twisted bilayer requires either explicit coprime m,n indices "
                "or a twist angle that maps within 0.1 degrees under the atom-count limit."
            )
        m, n, _actual_angle = _select_commensurate_twist_indices(
            float(requested_angle),
            max_atoms=max_atoms,
        )
    else:
        m, n = (int(value) for value in indices)
    if m <= n or n < 0 or math.gcd(m, n) != 1:
        raise ValueError("commensurate twist indices must be coprime and satisfy m > n >= 0.")

    actual_angle = commensurate_twist_angle_degrees(m, n)
    orientation = request.get("twist_orientation")
    if orientation is None:
        orientation = "counterclockwise"
    signed_actual_angle = actual_angle if orientation == "counterclockwise" else -actual_angle
    if requested_angle is not None:
        requested_angle = float(requested_angle)
        requested_orientation = "counterclockwise" if requested_angle > 0 else "clockwise"
        if requested_orientation != orientation:
            raise ValueError("requested twist-angle sign conflicts with the requested orientation.")
        error = abs(abs(requested_angle) - actual_angle)
        if error > COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES + 1e-12:
            raise ValueError(
                f"indices m={m}, n={n} produce {actual_angle:.9f} degrees, "
                f"not requested {abs(requested_angle):.9f} degrees "
                f"(error {error:.9f} > {COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES:g})."
            )

    interlayer_distance = request.get("interlayer_distance_angstrom")
    if interlayer_distance is None:
        material_key = re.sub(r"[^a-z0-9]+", "", str(metadata.get("material") or "").lower())
        interlayer_distance = TMD_COMMENSURATE_TWIST_DEFAULT_INTERLAYER_ANGSTROM.get(material_key)
        if interlayer_distance is None:
            raise ValueError(
                "no reviewed default interlayer distance is available for this TMD; "
                "provide interlayer distance explicitly."
            )

    operation: dict[str, Any] = {
        "type": "make_commensurate_twisted_bilayer",
        "commensurate_m": m,
        "commensurate_n": n,
        "interlayer_distance_angstrom": round(float(interlayer_distance), 9),
        "twist_orientation": orientation,
        "max_atoms": max_atoms,
    }
    if requested_angle is not None:
        operation["angle_degrees"] = round(float(requested_angle), 9)
    return [operation]


def _commensurate_tmd_heterobilayer_operations(
    current_spec: ModelSpec,
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("commensurate TMD heterobilayer requires a crystal model.")
    metadata = dict(current_spec.metadata or {})
    family = str(metadata.get("structure_family") or "").lower()
    if metadata.get("domain") != "semiconductor" or "tmd" not in family or "monolayer" not in family:
        raise ValueError("commensurate TMD heterobilayer requires a pristine semiconductor TMD monolayer.")

    if request.get("extra_materials"):
        raise ValueError(
            "commensurate TMD heterobilayer accepts exactly two materials; extra materials: "
            + ", ".join(str(item) for item in request["extra_materials"])
        )
    current_material = _canonical_tmd_material(metadata.get("material"))
    if current_material is None:
        current_material = _tmd_material_from_crystal_atoms(current_spec.model)
    requested_bottom = request.get("bottom_material")
    if requested_bottom is not None and requested_bottom != current_material:
        raise ValueError(
            f"request names {requested_bottom} as the bottom layer, but the current monolayer is "
            f"{current_material}; start a new {requested_bottom}/{request.get('top_material')} model "
            "or reverse the material order"
        )
    top_material = _canonical_tmd_material(request.get("top_material"))
    if top_material is None:
        raise ValueError("top TMD material must be one of MoS2, WS2, MoSe2, or WSe2.")
    if top_material == current_material:
        raise ValueError(
            "commensurate TMD heterobilayer requires two different materials; "
            "use the homobilayer command for identical layers."
        )

    max_atoms = COMMENSURATE_TWIST_DEFAULT_MAX_ATOMS
    indices = request.get("indices")
    requested_angle = request.get("requested_angle_degrees")
    if indices is None:
        if requested_angle is None:
            raise ValueError(
                "commensurate TMD heterobilayer requires either explicit coprime m,n indices "
                "or a twist angle that maps within 0.1 degrees under the atom-count limit."
            )
        m, n, _actual_angle = _select_commensurate_twist_indices(
            float(requested_angle),
            max_atoms=max_atoms,
        )
    else:
        m, n = (int(value) for value in indices)
    if m <= n or n < 0 or math.gcd(m, n) != 1:
        raise ValueError("commensurate heterobilayer indices must be coprime and satisfy m > n >= 0.")

    actual_angle = commensurate_twist_angle_degrees(m, n)
    orientation = request.get("twist_orientation") or "counterclockwise"
    if requested_angle is not None:
        requested_angle = float(requested_angle)
        requested_orientation = "counterclockwise" if requested_angle > 0 else "clockwise"
        if requested_orientation != orientation:
            raise ValueError("requested heterobilayer twist-angle sign conflicts with the requested orientation.")
        error = abs(abs(requested_angle) - actual_angle)
        if error > COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES + 1e-12:
            raise ValueError(
                f"indices m={m}, n={n} produce {actual_angle:.9f} degrees, "
                f"not requested {abs(requested_angle):.9f} degrees "
                f"(error {error:.9f} > {COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES:g})."
            )

    interlayer_distance = request.get("interlayer_distance_angstrom")
    if interlayer_distance is None:
        bottom_default = TMD_COMMENSURATE_TWIST_DEFAULT_INTERLAYER_ANGSTROM[
            re.sub(r"[^a-z0-9]+", "", current_material.lower())
        ]
        top_default = TMD_COMMENSURATE_TWIST_DEFAULT_INTERLAYER_ANGSTROM[
            re.sub(r"[^a-z0-9]+", "", top_material.lower())
        ]
        interlayer_distance = 0.5 * (bottom_default + top_default)

    max_strain_percent = request.get("max_strain_percent")
    if max_strain_percent is None:
        max_strain_percent = COMMENSURATE_HETEROBILAYER_DEFAULT_MAX_STRAIN_PERCENT
    operation: dict[str, Any] = {
        "type": "make_commensurate_tmd_heterobilayer",
        "top_layer_material": top_material,
        "commensurate_m": m,
        "commensurate_n": n,
        "interlayer_distance_angstrom": round(float(interlayer_distance), 9),
        "twist_orientation": orientation,
        "strain_policy": request.get("strain_policy") or "balanced",
        "max_strain_percent": round(float(max_strain_percent), 9),
        "max_atoms": max_atoms,
    }
    if requested_angle is not None:
        operation["angle_degrees"] = round(float(requested_angle), 9)
    return [operation]


def _canonical_tmd_material(value: Any) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    for material in TMD_TEMPLATE_BY_MATERIAL:
        if normalized == re.sub(r"[^a-z0-9]+", "", material.lower()):
            return material
    return None


def _tmd_material_from_crystal_atoms(crystal: CrystalSpec) -> str | None:
    metals = sorted({atom.element for atom in crystal.basis_atoms if atom.element in TMD_METALS})
    chalcogens = sorted({atom.element for atom in crystal.basis_atoms if atom.element in TMD_CHALCOGENS})
    if len(metals) != 1 or len(chalcogens) != 1:
        return None
    return _canonical_tmd_material(f"{metals[0]}{chalcogens[0]}2")


def _select_commensurate_twist_indices(
    requested_angle_degrees: float,
    *,
    max_atoms: int,
) -> tuple[int, int, float]:
    magnitude = abs(float(requested_angle_degrees))
    if not math.isfinite(magnitude) or magnitude <= 1e-12 or magnitude > 60.0 + 1e-12:
        raise ValueError("commensurate twist angle must be in (0, 60] degrees.")
    candidates: list[tuple[float, int, int, int, float]] = []
    for m in range(1, 101):
        for n in range(0, m):
            if math.gcd(m, n) != 1:
                continue
            index = m * m + m * n + n * n
            atom_count = 6 * index
            if atom_count > max_atoms:
                continue
            actual = commensurate_twist_angle_degrees(m, n)
            candidates.append((abs(actual - magnitude), atom_count, m, n, actual))
    if not candidates:
        raise ValueError(f"no commensurate TMD twist candidate fits max_atoms={max_atoms}.")
    error, _atom_count, m, n, actual = min(candidates)
    if error > COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES + 1e-12:
        raise ValueError(
            f"no commensurate TMD twist matches {magnitude:.9f} degrees within "
            f"{COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES:g} degrees and max_atoms={max_atoms}; "
            f"nearest is m={m}, n={n}, angle={actual:.9f}, error={error:.9f}."
        )
    return m, n, actual


def _crystal_layer_translation_operations(
    current_spec: ModelSpec,
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("crystal layer translation requires a crystal model.")
    metadata = dict(current_spec.metadata or {})
    if metadata.get("domain") != "semiconductor":
        raise ValueError("crystal layer translation is enabled only for semiconductor models.")

    profile_axis = _normalize_lattice_axis(
        str(metadata.get("interface_axis") or metadata.get("surface_axis") or "c")
    )
    translation_axis = _normalize_lattice_axis(str(request.get("axis") or ""))
    if translation_axis not in {"a", "b", "c"}:
        raise ValueError("crystal layer translation requires lattice axis a, b, or c.")
    if translation_axis == profile_axis:
        raise ValueError(
            f"lateral layer translation must use an in-plane axis, not profile axis {profile_axis}; "
            "use an interface-gap or layer-thickness command for normal-axis changes."
        )
    distance = float(request.get("distance_angstrom") or 0.0)
    if abs(distance) <= 1e-12 or abs(distance) > 200.0:
        raise ValueError("crystal layer translation distance must be non-zero and no more than 200 Angstrom.")

    tolerance = _metadata_float_value(metadata.get("layer_profile_tolerance_fractional"), 1e-4)
    layers = _profile_crystal_layers(current_spec.model, profile_axis, tolerance)
    if not layers:
        raise ValueError("no crystal layers could be resolved from the current model.")
    if request.get("kind") == "edge":
        edge = str(request.get("edge") or "")
        layer_index = len(layers) if edge == "top" else 1
        selector = f"{edge}_layer"
    else:
        layer_index = int(request.get("layer_index") or 0)
        selector = f"layer_{layer_index}"
    if layer_index < 1 or layer_index > len(layers):
        raise ValueError(f"layer index {layer_index} is outside the available range 1..{len(layers)}.")

    target_atoms = layers[layer_index - 1]
    atom_ids = sorted(atom.id for atom in target_atoms)
    axis_index = {"a": 0, "b": 1, "c": 2}[translation_axis]
    axis_length = float(getattr(current_spec.model.lattice, translation_axis))
    delta_fractional = distance / axis_length
    wrapped_atom_ids = sorted(
        atom.id
        for atom in target_atoms
        if (_basis_atom_fractional_tuple(atom)[axis_index] + delta_fractional) < 0.0
        or (_basis_atom_fractional_tuple(atom)[axis_index] + delta_fractional) >= 1.0
    )
    profile_index = {"a": 0, "b": 1, "c": 2}[profile_axis]
    profile_center = sum(
        _basis_atom_fractional_tuple(atom)[profile_index] for atom in target_atoms
    ) / len(target_atoms)
    record = {
        "source": "natural_language_crystal_layer_translation",
        "target_selector": selector,
        "layer_index": layer_index,
        "layer_count": len(layers),
        "profile_axis": profile_axis,
        "profile_fractional_center": round(profile_center, 6),
        "translation_axis": translation_axis,
        "distance_angstrom": round(distance, 6),
        "delta_fractional": round(delta_fractional, 9),
        "atom_count": len(atom_ids),
        "atom_ids": atom_ids,
        "periodic_wrap": True,
        "wrapped_atom_count": len(wrapped_atom_ids),
        "wrapped_atom_ids": wrapped_atom_ids,
        "layer_profile_tolerance_fractional": round(tolerance, 9),
        "in_plane_translation": True,
    }
    history = [
        dict(item)
        for item in metadata.get("crystal_layer_translations", []) or []
        if isinstance(item, dict)
    ]
    history.append(record)
    return [
        {
            "type": "translate_crystal_atoms",
            "atom_ids": atom_ids,
            "axis": translation_axis,
            "distance_angstrom": round(distance, 6),
            "wrap_fractional": True,
        },
        {
            "type": "set_metadata",
            "metadata_updates": {
                "crystal_layer_translations": history,
                "last_crystal_layer_translation": record,
            },
        },
    ]


def _crystal_layer_rotation_operations(
    current_spec: ModelSpec,
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("crystal layer rotation requires a crystal model.")
    metadata = dict(current_spec.metadata or {})
    if metadata.get("domain") != "semiconductor":
        raise ValueError("crystal layer rotation is enabled only for semiconductor models.")

    profile_axis = _normalize_lattice_axis(
        str(metadata.get("interface_axis") or metadata.get("surface_axis") or "c")
    )
    requested_axis = request.get("axis")
    rotation_axis = _normalize_lattice_axis(str(requested_axis)) if requested_axis else profile_axis
    if rotation_axis != profile_axis:
        raise ValueError(
            f"layer twist rotation must use profile axis {profile_axis}; requested axis {rotation_axis} "
            "would tilt the layer instead of rotating it in plane."
        )
    angle = float(request.get("angle_degrees") or 0.0)
    if abs(angle) <= 1e-12 or abs(angle) >= 360.0 - 1e-12:
        raise ValueError("crystal layer rotation angle must produce a non-identity rotation.")

    orthogonality = _lattice_axis_orthogonality_max_abs_cosine(current_spec.model, rotation_axis)
    if orthogonality is None or orthogonality > 1e-6:
        raise ValueError(
            f"profile axis {profile_axis} is not orthogonal to both in-plane lattice vectors; "
            "automatic in-plane layer rotation is unsafe for this cell."
        )

    tolerance = _metadata_float_value(metadata.get("layer_profile_tolerance_fractional"), 1e-4)
    layers = _profile_crystal_layers(current_spec.model, profile_axis, tolerance)
    if not layers:
        raise ValueError("no crystal layers could be resolved from the current model.")
    if request.get("kind") == "edge":
        edge = str(request.get("edge") or "")
        layer_index = len(layers) if edge == "top" else 1
        selector = f"{edge}_layer"
    else:
        layer_index = int(request.get("layer_index") or 0)
        selector = f"layer_{layer_index}"
    if layer_index < 1 or layer_index > len(layers):
        raise ValueError(f"layer index {layer_index} is outside the available range 1..{len(layers)}.")

    target_atoms = layers[layer_index - 1]
    atom_ids = sorted(atom.id for atom in target_atoms)
    rotated_atoms, rotation_receipt = rotate_crystal_atom_set(
        current_spec.model.lattice,
        current_spec.model.basis_atoms,
        atom_ids=atom_ids,
        axis=rotation_axis,
        angle_degrees=angle,
        wrap_fractional=True,
    )
    profile_index = {"a": 0, "b": 1, "c": 2}[profile_axis]
    profile_center = sum(
        _basis_atom_fractional_tuple(atom)[profile_index] for atom in target_atoms
    ) / len(target_atoms)
    record = {
        "source": "natural_language_crystal_layer_rotation",
        "target_selector": selector,
        "layer_index": layer_index,
        "layer_count": len(layers),
        "profile_axis": profile_axis,
        "profile_fractional_center": round(profile_center, 6),
        "rotation_axis": rotation_axis,
        "rotation_axis_source": "explicit" if requested_axis else "profile_axis_default",
        "angle_degrees": round(angle, 6),
        "pivot_fractional": list(rotation_receipt["pivot_fractional"]),
        "atom_count": len(atom_ids),
        "atom_ids": atom_ids,
        "periodic_wrap": True,
        "wrapped_atom_count": rotation_receipt["wrapped_atom_count"],
        "wrapped_atom_ids": list(rotation_receipt["wrapped_atom_ids"]),
        "layer_profile_tolerance_fractional": round(tolerance, 9),
        "axis_orthogonality_max_abs_cosine": round(orthogonality, 12),
        "rotation_axis_orthogonal_to_in_plane_vectors": True,
        "in_plane_rotation": True,
        "pre_rotation_atom_coordinate_sha256": _crystal_atom_coordinate_sha256(
            current_spec.model.basis_atoms,
            atom_ids,
        ),
        "post_rotation_atom_coordinate_sha256": _crystal_atom_coordinate_sha256(
            rotated_atoms,
            atom_ids,
        ),
        "scaffold_only": True,
        "visual_review_only": True,
        "visual_hotload_ready": True,
        "commensurability_verified": False,
        "requires_commensurate_supercell": True,
        "requires_geometry_relaxation": True,
        "calculation_ready": False,
        "calculation_blocking_reason": "layer_rotation_commensurability_unverified",
    }
    history = [
        dict(item)
        for item in metadata.get("crystal_layer_rotations", []) or []
        if isinstance(item, dict)
    ]
    history.append(record)
    return [
        {
            "type": "rotate_crystal_atoms",
            "atom_ids": atom_ids,
            "axis": rotation_axis,
            "angle_degrees": round(angle, 6),
            "pivot_fractional": list(rotation_receipt["pivot_fractional"]),
            "wrap_fractional": True,
        },
        {
            "type": "set_metadata",
            "metadata_updates": {
                "crystal_layer_rotations": history,
                "last_crystal_layer_rotation": record,
            },
        },
    ]


def _lattice_axis_orthogonality_max_abs_cosine(model: CrystalSpec, axis: str) -> float | None:
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    if axis_index is None:
        return None
    vectors = _lattice_vectors(model)
    axis_vector = vectors[axis_index]
    axis_norm = math.sqrt(sum(value * value for value in axis_vector))
    if axis_norm <= 1e-12:
        return None
    cosines: list[float] = []
    for index, vector in enumerate(vectors):
        if index == axis_index:
            continue
        vector_norm = math.sqrt(sum(value * value for value in vector))
        if vector_norm <= 1e-12:
            return None
        cosines.append(
            abs(sum(axis_vector[item] * vector[item] for item in range(3)))
            / (axis_norm * vector_norm)
        )
    return max(cosines, default=0.0)


def _crystal_atom_coordinate_sha256(atoms: Sequence[BasisAtomSpec], atom_ids: Sequence[str]) -> str:
    atoms_by_id = {atom.id: atom for atom in atoms}
    payload = [
        {
            "id": atom_id,
            "fractional": [
                round(float(atoms_by_id[atom_id].fractional.x), 12),
                round(float(atoms_by_id[atom_id].fractional.y), 12),
                round(float(atoms_by_id[atom_id].fractional.z), 12),
            ],
        }
        for atom_id in sorted(atom_ids)
        if atom_id in atoms_by_id
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _profile_crystal_layers(
    model: CrystalSpec,
    axis: str,
    tolerance: float,
) -> list[list[BasisAtomSpec]]:
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis)
    if axis_index is None:
        return []
    sorted_atoms = sorted(
        model.basis_atoms,
        key=lambda atom: (_basis_atom_fractional_tuple(atom)[axis_index], atom.id),
    )
    layers: list[list[BasisAtomSpec]] = []
    for atom in sorted_atoms:
        value = _basis_atom_fractional_tuple(atom)[axis_index]
        if not layers:
            layers.append([atom])
            continue
        center = sum(_basis_atom_fractional_tuple(item)[axis_index] for item in layers[-1]) / len(layers[-1])
        if abs(value - center) <= tolerance:
            layers[-1].append(atom)
        else:
            layers.append([atom])
    return [sorted(layer, key=lambda atom: atom.id) for layer in layers]


def _metadata_float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _match_crystal_lattice_parameters(text: str) -> dict[str, float] | None:
    gate = re.search(
        r"\blattice\s+(?:constant|parameter)s?\b|\b(?:unit\s+)?cell\s+parameters?\b|"
        r"(?:\u6676\u683c\u5e38\u6570|\u6676\u683c\u53c2\u6570|\u6676\u80de\u53c2\u6570|\u6676\u80de\u5e38\u6570)",
        text,
        flags=re.IGNORECASE,
    )
    if gate is None:
        return None

    normalized = (
        text.replace("\u03b1", "alpha")
        .replace("\u03b2", "beta")
        .replace("\u03b3", "gamma")
    )
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    connector = (
        r"(?:=|:|\bto\b|\bas\b|\bis\b|\bat\b|\bof\b|"
        r"\u8bbe\u7f6e\u4e3a|\u8bbe\u4e3a|\u6539\u4e3a|\u6539\u6210|"
        r"\u8c03\u6574\u5230|\u8c03\u5230|\u4e3a)"
    )
    unit = (
        r"angstroms?|ang|nm|degrees?|deg|\u212b|\u00c5|\u00e5|"
        r"\u57c3|\u5ea6|\u00b0"
    )
    edits: dict[str, float] = {}
    conflict = False

    def add_value(field: str, raw_value: str, raw_unit: str | None) -> None:
        nonlocal conflict
        field = field.lower()
        unit_value = (raw_unit or "").lower()
        value = float(raw_value)
        if field in {"a", "b", "c"}:
            if unit_value in {"degree", "degrees", "deg", "\u5ea6", "\u00b0"}:
                conflict = True
                return
            if unit_value == "nm":
                value *= 10.0
        elif unit_value in {
            "angstrom",
            "angstroms",
            "ang",
            "nm",
            "\u212b",
            "\u00c5",
            "\u00e5",
            "\u57c3",
        }:
            conflict = True
            return
        value = round(value, 6)
        if field in edits and abs(edits[field] - value) > 1e-9:
            conflict = True
            return
        edits[field] = value

    axis_group_patterns = (
        rf"(?P<axes>(?<![A-Za-z0-9_])[abc](?![A-Za-z0-9_])"
        rf"(?:\s*(?:,|/|&|\band\b|\u548c|\u4e0e)\s*(?<![A-Za-z0-9_])[abc](?![A-Za-z0-9_]))+)"
        rf"\s*{connector}\s*(?P<value>{number})(?:\s*(?P<unit>{unit}))?",
        rf"(?P<axes>(?<![A-Za-z0-9_])[abc](?![A-Za-z0-9_])"
        rf"(?:\s*=\s*(?<![A-Za-z0-9_])[abc](?![A-Za-z0-9_]))+)"
        rf"\s*=\s*(?P<value>{number})(?:\s*(?P<unit>{unit}))?",
    )
    for pattern in axis_group_patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            axes = re.findall(
                r"(?<![A-Za-z0-9_])[abc](?![A-Za-z0-9_])",
                match.group("axes"),
                flags=re.IGNORECASE,
            )
            for axis in axes:
                add_value(axis, match.group("value"), match.groupdict().get("unit"))

    individual_pattern = (
        rf"(?<![A-Za-z0-9_])(?P<field>alpha|beta|gamma|a|b|c)(?![A-Za-z0-9_])"
        rf"\s*{connector}\s*(?P<value>{number})(?:\s*(?P<unit>{unit}))?"
    )
    for match in re.finditer(individual_pattern, normalized, flags=re.IGNORECASE):
        add_value(match.group("field"), match.group("value"), match.groupdict().get("unit"))

    if conflict or not edits:
        return None
    field_order = ("a", "b", "c", "alpha", "beta", "gamma")
    return {field: edits[field] for field in field_order if field in edits}


def _crystal_lattice_parameter_operations(
    current_spec: ModelSpec,
    edits: dict[str, float],
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("explicit lattice-parameter edits require a crystal model.")
    reference_lattice = current_spec.model.lattice.model_dump(mode="json")
    requested = {field: float(value) for field, value in edits.items()}
    new_lattice = LatticeSpec.model_validate({**reference_lattice, **requested})
    lattice_payload = new_lattice.model_dump(mode="json")
    changed_fields = [
        field
        for field in ("a", "b", "c", "alpha", "beta", "gamma")
        if field in requested and abs(float(reference_lattice[field]) - float(lattice_payload[field])) > 1e-9
    ]
    if not changed_fields:
        raise ValueError("the requested lattice parameters already match the current crystal lattice.")

    record = {
        "changed_fields": changed_fields,
        "requested_parameters": requested,
        "reference_lattice": reference_lattice,
        "lattice": lattice_payload,
        "fractional_coordinates_preserved": True,
        "length_unit": "angstrom",
        "angle_unit": "degree",
        "source": "natural_language_crystal_lattice_parameters",
    }
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("lattice_parameter_edits", [])
        if isinstance(item, dict)
    ]
    previous.append(record)
    metadata_updates: dict[str, Any] = {
        "lattice_parameter_edits": previous,
        "last_lattice_parameter_edit": record,
    }
    if (
        ("a" in changed_fields or "b" in changed_fields)
        and abs(new_lattice.a - new_lattice.b) <= 1e-6
    ):
        metadata_updates["in_plane_lattice_angstrom"] = new_lattice.a
    return [
        {"type": "set_lattice", "lattice": lattice_payload},
        {"type": "set_metadata", "metadata_updates": metadata_updates},
    ]


def _match_crystal_strain(text: str) -> tuple[list[str], float, str] | None:
    if "strain" not in text and "\u5e94\u53d8" not in text:
        return None
    percent_match = re.search(r"(?P<percent>[+-]?\d+(?:\.\d+)?)\s*[%\uff05]", text)
    if percent_match is None:
        return None
    percent = float(percent_match.group("percent"))
    if not -50.0 <= percent <= 100.0:
        return None
    mode = "tensile" if percent >= 0 else "compressive"
    if re.search(r"\bcompressive\b|\u538b\u7f29", text, flags=re.IGNORECASE):
        percent = -abs(percent)
        mode = "compressive"
    elif re.search(r"\btensile\b|\u62c9\u4f38", text, flags=re.IGNORECASE):
        percent = abs(percent)
        mode = "tensile"

    axes: list[str] | None = None
    if re.search(r"\b(?:biaxial|in[- ]?plane)\b|\u53cc\u8f74|\u9762\u5185", text, flags=re.IGNORECASE):
        axes = ["a", "b"]
        mode = f"biaxial_{mode}"
    elif re.search(r"\b(?:isotropic|hydrostatic|uniform)\b|\u5404\u5411\u540c\u6027|\u5747\u5300", text, flags=re.IGNORECASE):
        axes = ["a", "b", "c"]
        mode = f"isotropic_{mode}"
    else:
        axis_match = re.search(r"\b(?:along|on|in|to|axis)\s+(?P<axis>[abcxyz])\b", text, flags=re.IGNORECASE)
        if axis_match is None:
            axis_match = re.search(
                r"(?:\u6cbf|\u6cbf\u7740|\u5728|\u5bf9|\u7ed9)\s*(?P<axis>[abcxyz])\s*(?:\u8f74|\u65b9\u5411)?",
                text,
                flags=re.IGNORECASE,
            )
        if axis_match is None:
            axis_match = re.search(r"(?P<axis>[abcxyz])\s*(?:\u8f74|\u65b9\u5411)", text, flags=re.IGNORECASE)
        if axis_match is not None:
            axes = [_normalize_lattice_axis(axis_match.group("axis"))]
            mode = f"uniaxial_{mode}"

    if axes is None:
        axes = ["a", "b", "c"]
        mode = f"isotropic_default_{mode}"
    return axes, percent, mode


def _crystal_strain_operations(current_spec: ModelSpec, axes: list[str], percent: float, mode: str) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("crystal strain requires a crystal model.")
    lattice = current_spec.model.lattice
    factor = 1.0 + percent / 100.0
    if factor <= 0:
        raise ValueError("strain would produce non-positive lattice lengths.")
    updates = {axis: round(getattr(lattice, axis) * factor, 6) for axis in axes}
    new_lattice = lattice.model_copy(update=updates)
    record = {
        "axes": axes,
        "percent": round(percent, 6),
        "mode": mode,
        "scale_factor": round(factor, 8),
        "reference_lattice": lattice.model_dump(mode="json"),
        "lattice": new_lattice.model_dump(mode="json"),
        "source": "natural_language_crystal_strain",
    }
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("applied_strain", [])
        if isinstance(item, dict)
    ]
    previous.append(record)
    metadata_updates: dict[str, Any] = {
        "applied_strain": previous,
        "last_applied_strain": record,
    }
    if "a" in axes and "b" in axes and abs(new_lattice.a - new_lattice.b) <= 1e-6:
        metadata_updates["in_plane_lattice_angstrom"] = new_lattice.a
    return [
        {"type": "set_lattice", "lattice": new_lattice.model_dump(mode="json")},
        {"type": "set_metadata", "metadata_updates": metadata_updates},
    ]


def _normalize_lattice_axis(axis: str) -> str:
    return {"x": "a", "y": "b", "z": "c"}.get(axis.lower(), axis.lower())


def _match_crystal_vacancy(text: str) -> str | None:
    patterns = [
        r"\b(?:create|make|add|introduce)\s+(?:a\s+)?vacancy\s+(?:at|on)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\b",
        r"\b(?P<atom_id>[A-Za-z]{1,3}\d[A-Za-z0-9_-]*)\s+vacancy\b",
        r"(?:\u521b\u5efa|\u751f\u6210|\u6dfb\u52a0|\u5f15\u5165)\s*(?P<atom_id>[A-Za-z]{1,3}\d[A-Za-z0-9_-]*)\s*\u7a7a\u4f4d",
        r"(?P<atom_id>[A-Za-z]{1,3}\d[A-Za-z0-9_-]*)\s*(?:\u53d8\u6210|\u53d8\u4e3a|\u8bbe\u4e3a|\u6539\u6210|\u6539\u4e3a|\u6210\u4e3a)\s*\u7a7a\u4f4d",
        r"(?P<atom_id>[A-Za-z]{1,3}\d[A-Za-z0-9_-]*)\s*\u7a7a\u4f4d",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return match.group("atom_id")
    return None



def _match_vacancy_notation_element(text: str) -> str | None:
    """Match common semiconductor vacancy notation such as V_O, V-Ga, or VO defect."""

    if _has_explicit_substitution_intent(text):
        return None
    separated = re.search(
        rf"(?<![A-Za-z0-9])V\s*[_-]\s*(?P<element>{ELEMENT_TERM_PATTERN})(?![A-Za-z0-9])",
        text,
        flags=re.IGNORECASE,
    )
    if separated is not None:
        element = _normalize_element(separated.group("element"))
        if element is not None:
            return element
    compact = re.search(
        r"(?<![A-Za-z0-9])V(?P<element>[A-Z][a-z]?)(?![A-Za-z0-9])",
        text,
        flags=re.IGNORECASE,
    )
    if compact is not None and _has_vacancy_defect_context(text):
        element = _normalize_element(compact.group("element"))
        if element is not None:
            return element
    return None


def _has_vacancy_defect_context(text: str) -> bool:
    return bool(re.search(r"\b(?:vacancy|defect)\b", text, flags=re.IGNORECASE) or "\u7a7a\u4f4d" in text or "\u7f3a\u9677" in text)


def _has_explicit_substitution_intent(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:dope|doped|doping|dopant|substitute|substitution|substituting|replace|replacing)\b",
            text,
            flags=re.IGNORECASE,
        )
        or any(term in text for term in ("\u63ba\u6742", "\u63ba\u5165", "\u66ff\u4ee3", "\u53d6\u4ee3", "\u66ff\u6362"))
    )


def _is_vacancy_notation_sublattice_match(
    text: str,
    matched_text: str,
    element: str | None,
    site_element: str | None,
) -> bool:
    if element != "V" or site_element is None or _has_explicit_substitution_intent(text):
        return False
    return bool(
        re.fullmatch(rf"\s*V\s*[_-]\s*{re.escape(site_element)}\s*", matched_text, flags=re.IGNORECASE)
        or (_has_vacancy_defect_context(text) and re.fullmatch(rf"\s*V{re.escape(site_element)}\s*", matched_text))
    )


def _match_crystal_auto_vacancy(text: str) -> tuple[str | None] | None:
    notation_element = _match_vacancy_notation_element(text)
    if notation_element is not None:
        return (notation_element,)
    patterns = [
        rf"\b(?:create|make|add|introduce)\s+(?:a\s+)?(?:(?P<element>{ELEMENT_TERM_PATTERN})\s+)?vacancy(?:\s+defect)?\b",
        rf"\b(?P<element>{ELEMENT_TERM_PATTERN})\s+vacancy(?:\s+defect)?\b",
        rf"(?:\u521b\u5efa|\u751f\u6210|\u6dfb\u52a0|\u5f15\u5165)\s*(?:(?P<element>{ELEMENT_TERM_PATTERN}|\u7845)\s*)?\u7a7a\u4f4d",
        rf"(?P<element>{ELEMENT_TERM_PATTERN}|\u7845)\s*\u7a7a\u4f4d",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        raw_element = match.groupdict().get("element")
        if not raw_element:
            return (None,)
        element = _normalize_element(raw_element)
        if element is not None:
            return (element,)
    return None


def _crystal_vacancy_operations(current_spec: ModelSpec, atom_id: str, *, auto_selected: bool = False) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = [{"type": "delete_atom", "atom_id": atom_id}]
    if not isinstance(current_spec.model, CrystalSpec):
        return operations
    atom = next((item for item in current_spec.model.basis_atoms if item.id == atom_id), None)
    if atom is None:
        return operations
    metadata = dict(current_spec.metadata or {})
    defects = [dict(item) for item in metadata.get("defects", []) if isinstance(item, dict)]
    vacancy = {
        "type": "vacancy",
        "site_id": atom.id,
        "site_element": atom.element,
        "fractional": [
            _round_fractional(atom.fractional.x),
            _round_fractional(atom.fractional.y),
            _round_fractional(atom.fractional.z),
        ],
        "source": "natural_language_crystal_vacancy",
    }
    if auto_selected:
        vacancy["auto_selected_site"] = True
    defects.append(vacancy)
    auto_sites = _auto_selected_sites_metadata(
        current_spec,
        operation="vacancy",
        atom_id=atom.id,
        site_element=atom.element,
        new_element=None,
    )
    defect_types = sorted({str(item.get("type")) for item in defects if item.get("type")})
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "defects": defects,
                "defect_count": len(defects),
                "defect_types": defect_types,
                **({"nl_auto_selected_sites": auto_sites} if auto_selected else {}),
            },
        }
    )
    return operations


def _match_crystal_dopant(text: str) -> tuple[str, str] | None:
    patterns = [
        r"\b(?:dope|dopant|doping)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+(?:with|to|as)\s+(?P<element>[A-Za-z]{1,2})\b",
        r"\b(?:put|place|set|add)\s+(?P<element>[A-Za-z]{1,2})\s+(?:dopant\s+)?(?:at|on|to)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\b",
        r"\b(?P<element>[A-Za-z]{1,2})\s+dopant\s+(?:at|on)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\b",
        r"(?:\u7528|\u4ee5)?\s*(?P<element>[A-Za-z]{1,2})\s*\u63ba\u6742\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)",
        r"(?:\u5c06|\u628a)?\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s*\u63ba\u6742(?:\u4e3a|\u6210)?\s*(?P<element>[A-Za-z]{1,2})",
        r"\u5728\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s*(?:\u4f4d\u70b9|\u4f4d\u7f6e|site)?\s*\u63ba\u6742\s*(?P<element>[A-Za-z]{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        atom_id = match.group("atom_id")
        if not _looks_like_explicit_atom_id(atom_id):
            continue
        element = _normalize_element(match.group("element"))
        if element is not None:
            return atom_id, element
    return None


def _match_crystal_auto_dopant(text: str) -> tuple[str, str | None] | None:
    patterns = [
        rf"\b(?:dope|doping)\s+(?P<site_element>{ELEMENT_TERM_PATTERN})\s+(?:with|using)\s+(?P<element>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?:dope|dopant|doping)\s+(?:with\s+)?(?P<element>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?:doped|doping)\s+(?:with|using)\s+(?P<element>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?P<element>{ELEMENT_TERM_PATTERN})\s+(?:dopant|doped|doping)\b",
        rf"(?:\u63ba\u6742|\u5f15\u5165\u63ba\u6742|\u52a0\u5165\u63ba\u6742)\s*(?P<element>{ELEMENT_TERM_PATTERN})",
        rf"(?:\u7528|\u4ee5)?\s*(?P<element>{ELEMENT_TERM_PATTERN})\s*(?:\u63ba\u6742|\u63ba\u5165)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        if _matched_element_is_embedded_token(text, match.span("element")):
            continue
        element = _normalize_element(match.group("element"))
        if element is None:
            continue
        raw_site_element = match.groupdict().get("site_element")
        site_element = _normalize_element(raw_site_element) if raw_site_element else None
        return element, site_element
    return None


def _matched_element_is_embedded_token(text: str, span: tuple[int, int]) -> bool:
    """Return true when an element regex matched only part of a formula/name token."""

    start, end = span
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return bool(
        (before and re.match(r"[A-Za-z0-9_]", before))
        or (after and re.match(r"[A-Za-z0-9_]", after))
    )


def _match_crystal_sublattice_dopant(text: str) -> tuple[str, str] | None:
    patterns = [
        r"\b(?P<element>[A-Z][a-z]?)\s*[_@]\s*(?P<site_element>[A-Z][a-z]?)\b",
        rf"\b(?P<element>{ELEMENT_TERM_PATTERN})\s+(?:on|at|onto)\s+(?P<site_element>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)\b",
        rf"\b(?P<element>{ELEMENT_TERM_PATTERN})\s+(?:substituting|replacing)\s+(?P<site_element>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)?\b",
        rf"\b(?:dope|doping|dopant)\s+(?:the\s+)?(?P<site_element>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)\s+(?:with|using)\s+(?P<element>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?:replace|substitute)\s+(?:the\s+)?(?P<site_element>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)\s+(?:with|by|to)\s+(?P<element>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?P<site_element>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)\s+(?:doped|substituted)\s+(?:with|by)\s+(?P<element>{ELEMENT_TERM_PATTERN})\b",
        rf"(?P<element>{ELEMENT_TERM_PATTERN})\s*(?:\u63ba\u6742|\u63ba\u5165)\s*(?P<site_element>{ELEMENT_TERM_PATTERN})\s*(?:\u4f4d\u70b9|\u4f4d|\u6676\u4f4d|\u5b50\u6676\u683c)",
        rf"(?:\u5728|\u5230)?\s*(?P<site_element>{ELEMENT_TERM_PATTERN})\s*(?:\u4f4d\u70b9|\u4f4d|\u6676\u4f4d|\u5b50\u6676\u683c)\s*(?:\u63ba\u6742|\u63ba\u5165|\u63ba)\s*(?P<element>{ELEMENT_TERM_PATTERN})",
        rf"(?:\u7528|\u4ee5)?\s*(?P<element>{ELEMENT_TERM_PATTERN})\s*(?:\u66ff\u4ee3|\u53d6\u4ee3|\u66ff\u6362)\s*(?P<site_element>{ELEMENT_TERM_PATTERN})\s*(?:\u4f4d\u70b9|\u4f4d|\u6676\u4f4d|\u5b50\u6676\u683c)?",
        rf"(?:\u628a|\u5c06)?\s*(?P<site_element>{ELEMENT_TERM_PATTERN})\s*(?:\u4f4d\u70b9|\u4f4d|\u6676\u4f4d|\u5b50\u6676\u683c)\s*(?:\u66ff\u4ee3|\u53d6\u4ee3|\u66ff\u6362)(?:\u4e3a|\u6210)?\s*(?P<element>{ELEMENT_TERM_PATTERN})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        element = _normalize_element(match.group("element"))
        site_element = _normalize_element(match.group("site_element"))
        if _is_vacancy_notation_sublattice_match(text, match.group(0), element, site_element):
            continue
        if element is not None and site_element is not None and element != site_element:
            return element, site_element
    return None



def _match_semiconductor_carrier_type(text: str) -> tuple[str, float | None] | None:
    """Match conservative n-type/p-type semiconductor intent."""

    n_type = bool(
        re.search(
            r"(?<![A-Za-z0-9])n\s*[- ]?\s*type(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])n\s*[- ]?\s*(?:doped|doping)(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])electron\s*[- ]?\s*(?:doped|doping|type|carrier|conducting)(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])donor\s*[- ]?\s*(?:doped|doping|dopant|type)(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])donor\s*[- ]?\s*(?:defect|vacancy|center)(?![A-Za-z0-9])"
            r"|n\s*\u578b|\u7535\u5b50\u578b|\u7535\u5b50\s*\u63ba\u6742|\u65bd\u4e3b\s*\u63ba\u6742"
            r"|\u65bd\u4e3b\s*(?:\u7f3a\u9677|\u7a7a\u4f4d|\u4e2d\u5fc3)"
            r"|\u4f9b\u4f53\s*(?:\u7f3a\u9677|\u7a7a\u4f4d|\u4e2d\u5fc3)",
            text,
            flags=re.IGNORECASE,
        )
    )
    p_type = bool(
        re.search(
            r"(?<![A-Za-z0-9])p\s*[- ]?\s*type(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])p\s*[- ]?\s*(?:doped|doping)(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])hole\s*[- ]?\s*(?:doped|doping|type|carrier|conducting)(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])acceptor\s*[- ]?\s*(?:doped|doping|dopant|type)(?![A-Za-z0-9])"
            r"|(?<![A-Za-z0-9])acceptor\s*[- ]?\s*(?:defect|vacancy|center)(?![A-Za-z0-9])"
            r"|p\s*\u578b|\u7a7a\u7a74\u578b|\u7a7a\u7a74\s*\u63ba\u6742|\u53d7\u4e3b\s*\u63ba\u6742"
            r"|\u53d7\u4e3b\s*(?:\u7f3a\u9677|\u7a7a\u4f4d|\u4e2d\u5fc3)",
            text,
            flags=re.IGNORECASE,
        )
    )
    if n_type == p_type:
        return None
    carrier_type = "n_type" if n_type else "p_type"
    percent_match = re.search(r"(?P<percent>\d+(?:\.\d+)?)\s*%", text)
    fraction = float(percent_match.group("percent")) / 100.0 if percent_match is not None else None
    if fraction is not None and not 0.0 < fraction <= 1.0:
        return None
    return carrier_type, fraction


def _match_semiconductor_pn_junction(text: str) -> dict[str, Any] | None:
    """Match a conservative semiconductor p-n junction start."""

    if _looks_like_semiconductor_pn_junction_text(text):
        pass
    elif not (
        re.search(r"\bp\s*[- ]?\s*n\s+(?:junction|interface|diode)\b", text, flags=re.IGNORECASE)
        or re.search(r"\bpn\s+(?:junction|interface|diode)\b", text, flags=re.IGNORECASE)
        or any(term in text for term in ("pn结", "pn 结", "p-n结", "p-n 结"))
    ):
        return None
    return {
        "junction_type": "pn_junction",
        "axis": "a",
        "source": "natural_language_semiconductor_pn_junction",
    }


def _looks_like_semiconductor_pn_junction_text(text: str) -> bool:
    """Return True for English or Chinese p-n junction intent."""

    if not text:
        return False
    lowered = " ".join(text.lower().split())
    if (
        re.search(r"\bp\s*[-/]?\s*n\s+(?:junction|interface|diode)\b", lowered, flags=re.IGNORECASE)
        or re.search(r"\bpn\s+(?:junction|interface|diode)\b", lowered, flags=re.IGNORECASE)
        or re.search(r"\bp\s+type\s+n\s+type\s+(?:junction|interface|diode)\b", lowered, flags=re.IGNORECASE)
    ):
        return True
    compact = re.sub(r"\s+", "", text.lower())
    return any(
        term in compact
        for term in (
            "pn\u7ed3",
            "p-n\u7ed3",
            "p/n\u7ed3",
            "pn\u754c\u9762",
            "p-n\u754c\u9762",
            "p/n\u754c\u9762",
            "pn\u4e8c\u6781\u7ba1",
            "p-n\u4e8c\u6781\u7ba1",
            "p/n\u4e8c\u6781\u7ba1",
            "p\u578bn\u578b\u7ed3",
            "p\u578bn\u578b\u754c\u9762",
        )
    )


def _text_mentions_silicon(text: str) -> bool:
    return bool(
        re.search(r"(?<![A-Za-z0-9])(?:si|silicon)(?![A-Za-z0-9])", text, flags=re.IGNORECASE)
        or "\u7845" in text
    )


def _semiconductor_pn_junction_needs_default_supercell(current_spec: ModelSpec) -> bool:
    if not isinstance(current_spec.model, CrystalSpec):
        return False
    return len(current_spec.model.basis_atoms) < 16


def _semiconductor_pn_junction_operations(
    current_spec: ModelSpec,
    match: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create a deterministic p-n junction by region-separated dopants."""

    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("semiconductor p-n junction requires a crystal model.")
    axis = str(match.get("axis") or "a").lower()
    p_target = _carrier_type_dopant_target(current_spec, "p_type")
    n_target = _carrier_type_dopant_target(current_spec, "n_type")
    p_dopant = str(match.get("p_dopant_element") or p_target["dopant_element"])
    n_dopant = str(match.get("n_dopant_element") or n_target["dopant_element"])
    p_site_element = str(match.get("p_site_element") or p_target.get("site_element") or "")
    n_site_element = str(match.get("n_site_element") or n_target.get("site_element") or "")
    if not p_site_element or not n_site_element:
        raise ValueError("Could not determine p-side and n-side dopant site elements for this semiconductor.")

    p_candidates = [
        atom
        for atom in current_spec.model.basis_atoms
        if atom.element == p_site_element and atom.element != p_dopant
    ]
    n_candidates = [
        atom
        for atom in current_spec.model.basis_atoms
        if atom.element == n_site_element and atom.element != n_dopant
    ]
    if len(p_candidates) < 1 or len(n_candidates) < 1:
        raise ValueError("At least one p-side and one n-side dopant site are required for a p-n junction start.")
    p_atom = _select_junction_site(p_candidates, axis, region="p")
    n_atom = _select_junction_site(n_candidates, axis, region="n")
    if p_atom.id == n_atom.id:
        raise ValueError("Could not select distinct p-side and n-side dopant sites.")
    host_element = p_site_element if p_site_element == n_site_element else None

    p_record = _dopant_site_record(
        atom_id=p_atom.id,
        site_element=p_site_element,
        dopant_element=p_dopant,
        fractional=_fractional_list(p_atom),
        auto_selected=True,
        source="natural_language_semiconductor_pn_junction",
    )
    n_record = _dopant_site_record(
        atom_id=n_atom.id,
        site_element=n_site_element,
        dopant_element=n_dopant,
        fractional=_fractional_list(n_atom),
        auto_selected=True,
        source="natural_language_semiconductor_pn_junction",
    )
    base_family = str((current_spec.metadata or {}).get("structure_family") or current_spec.model.name)
    junction_record = {
        "junction_type": "pn_junction",
        "host_element": host_element,
        "axis": axis,
        "p_region": {
            "carrier_type": "p_type",
            "dopant_element": p_dopant,
            "site_element": p_site_element,
            "site_ids": [p_atom.id],
            "fractional_range": [0.0, 0.5],
        },
        "n_region": {
            "carrier_type": "n_type",
            "dopant_element": n_dopant,
            "site_element": n_site_element,
            "site_ids": [n_atom.id],
            "fractional_range": [0.5, 1.0],
        },
        "source": "natural_language_semiconductor_pn_junction",
    }
    dopant_sites = [
        dict(item)
        for item in (current_spec.metadata or {}).get("semiconductor_dopant_sites", [])
        if isinstance(item, dict)
    ]
    dopant_sites.extend([p_record, n_record])
    junctions = [
        dict(item)
        for item in (current_spec.metadata or {}).get("semiconductor_junctions", [])
        if isinstance(item, dict)
    ]
    junctions.append(junction_record)
    auto_selected_sites = [
        dict(item)
        for item in (current_spec.metadata or {}).get("nl_auto_selected_sites", [])
        if isinstance(item, dict)
    ]
    auto_selected_sites.extend(
        [
            {
                "operation": "p-n junction",
                "atom_id": p_atom.id,
                "site_element": p_site_element,
                "new_element": p_dopant,
                "auto_selected_site": True,
                "selection_rule": "lower_fractional_a_region",
                "source": "natural_language_semiconductor_pn_junction",
            },
            {
                "operation": "p-n junction",
                "atom_id": n_atom.id,
                "site_element": n_site_element,
                "new_element": n_dopant,
                "auto_selected_site": True,
                "selection_rule": "upper_fractional_a_region",
                "source": "natural_language_semiconductor_pn_junction",
            },
        ]
    )
    metadata = {
        "structure_family": f"{base_family} p-n junction",
        "semiconductor_junctions": junctions,
        "last_semiconductor_junction": junction_record,
        "semiconductor_dopant_sites": dopant_sites,
        "last_semiconductor_dopant_site": n_record,
        "nl_auto_selected_sites": auto_selected_sites,
    }
    return [
        {"type": "substitute_atom", "atom_id": p_atom.id, "new_element": p_dopant},
        {"type": "substitute_atom", "atom_id": n_atom.id, "new_element": n_dopant},
        {"type": "set_metadata", "metadata_updates": metadata},
    ]


def _semiconductor_pn_junction_patch_operations(
    current_spec: ModelSpec,
    match: dict[str, Any],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    working = current_spec
    if _semiconductor_pn_junction_needs_default_supercell(working):
        supercell_operation = {"type": "make_supercell", "matrix": [2, 1, 1]}
        working, _ = apply_semantic_patch(
            working,
            SemanticPatch(
                project_id=working.project_id,
                base_revision=working.revision,
                operations=[supercell_operation],
            ),
        )
        operations.append(supercell_operation)
    operations.extend(_semiconductor_pn_junction_operations(working, match))
    return operations


def _split_sites_for_junction(
    atoms: list[BasisAtomSpec],
    axis: str,
) -> tuple[list[BasisAtomSpec], list[BasisAtomSpec]]:
    lower = [atom for atom in atoms if _fractional_axis_value(atom, axis) < 0.5]
    upper = [atom for atom in atoms if _fractional_axis_value(atom, axis) >= 0.5]
    if lower and upper:
        return lower, upper
    ordered = sorted(atoms, key=lambda atom: _junction_site_sort_key(atom, axis))
    midpoint = len(ordered) // 2
    return ordered[:midpoint], ordered[midpoint:]


def _select_junction_site(
    atoms: list[BasisAtomSpec],
    axis: str,
    *,
    region: str,
) -> BasisAtomSpec:
    lower, upper = _split_sites_for_junction(atoms, axis)
    if region == "p":
        candidates = lower or atoms
        return sorted(candidates, key=lambda atom: _junction_site_sort_key(atom, axis))[0]
    candidates = upper or atoms
    return sorted(candidates, key=lambda atom: _junction_site_sort_key(atom, axis), reverse=True)[0]


def _junction_site_sort_key(atom: BasisAtomSpec, axis: str) -> tuple[float, float, float, str]:
    values = {
        "a": float(atom.fractional.x),
        "b": float(atom.fractional.y),
        "c": float(atom.fractional.z),
        "x": float(atom.fractional.x),
        "y": float(atom.fractional.y),
        "z": float(atom.fractional.z),
    }
    primary = values.get(axis, values["a"])
    return (primary, float(atom.fractional.y), float(atom.fractional.z), atom.id)


def _fractional_axis_value(atom: BasisAtomSpec, axis: str) -> float:
    if axis in {"b", "y"}:
        return float(atom.fractional.y)
    if axis in {"c", "z"}:
        return float(atom.fractional.z)
    return float(atom.fractional.x)


def _fractional_list(atom: BasisAtomSpec) -> list[float]:
    return [
        _round_fractional(atom.fractional.x),
        _round_fractional(atom.fractional.y),
        _round_fractional(atom.fractional.z),
    ]


def _semiconductor_carrier_type_dopant_operations(
    current_spec: ModelSpec,
    carrier_type: str,
    fraction: float | None,
) -> list[dict[str, Any]]:
    """Map n-type/p-type carrier intent to deterministic dopant operations."""

    target = _carrier_type_dopant_target(current_spec, carrier_type)
    dopant_element = target["dopant_element"]
    site_element = target.get("site_element")
    if fraction is not None:
        operations = _crystal_dopant_fraction_operations(current_spec, site_element, dopant_element, fraction)
    else:
        atom = _auto_select_crystal_site(
            current_spec,
            requested_site_element=site_element,
            replacing_with=dopant_element,
            operation=f"{carrier_type} dopant",
        )
        operations = _crystal_dopant_operations(
            current_spec,
            atom.id,
            dopant_element,
            auto_selected=True,
            source="natural_language_semiconductor_carrier_type",
        )
    operations.append(
        _carrier_type_metadata_operation(
            current_spec,
            carrier_type,
            dopant_element,
            fraction,
            site_element=site_element,
            mapping_rule=target.get("mapping_rule"),
        )
    )
    return operations


def _carrier_type_dopant_target(current_spec: ModelSpec, carrier_type: str) -> dict[str, str | None]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("semiconductor carrier-type intent requires a crystal model.")
    elements = {atom.element for atom in current_spec.model.basis_atoms}
    group_ii = {"Mg", "Zn", "Cd"}
    group_iii = {"B", "Al", "Ga", "In"}
    group_iv = {"C", "Si", "Ge", "Sn"}
    group_v = {"N", "P", "As", "Sb"}
    group_vi = {"O", "S", "Se", "Te"}
    host_elements = sorted(elements & group_iv)

    def target(dopant: str, site: str | None, rule: str) -> dict[str, str | None]:
        return {"dopant_element": dopant, "site_element": site, "mapping_rule": rule}

    metadata = current_spec.metadata or {}
    if metadata.get("oxide_semiconductor") and "O" in elements:
        oxide_cations = sorted(elements - {"O", "H"})
        site = _majority_site_element(current_spec.model, oxide_cations)
        if site is not None:
            if carrier_type == "n_type":
                return target("Sn", site, "oxide_semiconductor_cation_site")
            if carrier_type == "p_type":
                return target("N", "O", "oxide_semiconductor_anion_site")

    if len(host_elements) == 1 and not any(element not in group_iv for element in elements):
        if carrier_type == "n_type":
            return target("P", host_elements[0], "single_group_iv")
        if carrier_type == "p_type":
            return target("B", host_elements[0], "single_group_iv")

    if elements == {"C", "Si"}:
        if carrier_type == "n_type":
            return target("N", "C", "silicon_carbide")
        if carrier_type == "p_type":
            return target("Al", "Si", "silicon_carbide")

    iii_sites = elements & group_iii
    v_sites = elements & group_v
    if _is_hbn_layered_spec(current_spec):
        if carrier_type == "n_type":
            return target("Si", "B", "hbn_layered_boron_site")
        if carrier_type == "p_type":
            return target("C", "N", "hbn_layered_nitrogen_site")
    if iii_sites and v_sites:
        site = _majority_site_element(current_spec.model, sorted(iii_sites))
        if "N" in v_sites:
            if carrier_type == "n_type":
                return target("Si", site, "iii_nitride_cation_site")
            if carrier_type == "p_type":
                return target("Mg", site, "iii_nitride_cation_site")
        if carrier_type == "n_type":
            return target("Si", site, "iii_v_cation_site")
        if carrier_type == "p_type":
            return target("Zn", site, "iii_v_cation_site")

    ii_sites = elements & group_ii
    vi_sites = elements & group_vi
    if ii_sites and vi_sites:
        if carrier_type == "n_type":
            site = _majority_site_element(current_spec.model, sorted(ii_sites))
            return target("Al", site, "ii_vi_cation_site")
        if carrier_type == "p_type":
            site = _majority_site_element(current_spec.model, sorted(vi_sites))
            return target("N", site, "ii_vi_anion_site")

    tmd_metals = elements & TMD_METALS
    tmd_chalcogens = elements & TMD_CHALCOGENS
    if tmd_metals and tmd_chalcogens:
        if carrier_type == "n_type":
            site = _majority_site_element(current_spec.model, sorted(tmd_chalcogens))
            return target("Cl", site, "tmd_chalcogen_site")
        if carrier_type == "p_type":
            site = _majority_site_element(current_spec.model, sorted(tmd_metals))
            return target("Nb", site, "tmd_metal_site")

    raise ValueError(f"Unsupported semiconductor carrier type: {carrier_type}")


def _is_hbn_layered_spec(spec: ModelSpec) -> bool:
    if not isinstance(spec.model, CrystalSpec):
        return False
    elements = {atom.element for atom in spec.model.basis_atoms}
    if not {"B", "N"} <= elements:
        return False
    metadata = spec.metadata or {}
    family = str(metadata.get("structure_family") or "").lower()
    material = str(metadata.get("material") or "").lower()
    return (
        "hbn" in family
        or "h-bn" in material
        or bool(metadata.get("layered_insulator"))
    ) and ("monolayer" in family or metadata.get("vacuum_angstrom") is not None)


def _carrier_type_metadata_operation(
    current_spec: ModelSpec,
    carrier_type: str,
    dopant_element: str,
    fraction: float | None,
    *,
    site_element: str | None = None,
    mapping_rule: str | None = None,
) -> dict[str, Any]:
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("semiconductor_carrier_intents", [])
        if isinstance(item, dict)
    ]
    record: dict[str, Any] = {
        "carrier_type": carrier_type,
        "dopant_element": dopant_element,
        "source": "natural_language_semiconductor_carrier_type",
    }
    if site_element is not None:
        record["site_element"] = site_element
    if mapping_rule is not None:
        record["mapping_rule"] = mapping_rule
    if fraction is not None:
        record["requested_fraction"] = round(fraction, 6)
        record["requested_percent"] = round(100.0 * fraction, 6)
    previous.append(record)
    return {
        "type": "set_metadata",
        "metadata_updates": {
            "semiconductor_carrier_intents": previous,
            "last_semiconductor_carrier_intent": record,
        },
    }


def _should_record_defect_carrier_intent(text: str, vacancy_applied: bool) -> bool:
    return bool(vacancy_applied and _has_vacancy_defect_context(text) and not _has_explicit_substitution_intent(text))


def _defect_carrier_type_metadata_operation(current_spec: ModelSpec, carrier_type: str) -> dict[str, Any]:
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("semiconductor_carrier_intents", [])
        if isinstance(item, dict)
    ]
    defects = [
        dict(item)
        for item in (current_spec.metadata or {}).get("defects", [])
        if isinstance(item, dict)
    ]
    latest_defect = defects[-1] if defects else {}
    record: dict[str, Any] = {
        "carrier_type": carrier_type,
        "carrier_mechanism": "defect",
        "defect_type": latest_defect.get("type") or "vacancy",
        "source": "natural_language_semiconductor_defect_carrier_type",
    }
    if latest_defect.get("site_element"):
        record["site_element"] = latest_defect.get("site_element")
    if latest_defect.get("site_id"):
        record["site_id"] = latest_defect.get("site_id")
    previous.append(record)
    return {
        "type": "set_metadata",
        "metadata_updates": {
            "semiconductor_carrier_intents": previous,
            "last_semiconductor_carrier_intent": record,
        },
    }


def _with_optional_carrier_intent(
    current_spec: ModelSpec,
    text: str,
    operations: list[dict[str, Any]],
    dopant_element: str,
    fraction: float | None = None,
) -> list[dict[str, Any]]:
    carrier_type_match = _match_semiconductor_carrier_type(text)
    if carrier_type_match is None:
        return operations
    carrier_type, carrier_fraction = carrier_type_match
    return [
        *operations,
        _carrier_type_metadata_operation(
            current_spec,
            carrier_type,
            dopant_element,
            fraction if fraction is not None else carrier_fraction,
        ),
    ]


def _crystal_dopant_operations(
    current_spec: ModelSpec,
    atom_id: str,
    element: str,
    *,
    auto_selected: bool = False,
    source: str = "natural_language_crystal_dopant",
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = [{"type": "substitute_atom", "atom_id": atom_id, "new_element": element}]
    if not isinstance(current_spec.model, CrystalSpec):
        return operations
    atom = next((item for item in current_spec.model.basis_atoms if item.id == atom_id), None)
    if atom is None:
        return operations
    record = _dopant_site_record(
        atom_id=atom.id,
        site_element=atom.element,
        dopant_element=element,
        fractional=[
            _round_fractional(atom.fractional.x),
            _round_fractional(atom.fractional.y),
            _round_fractional(atom.fractional.z),
        ],
        auto_selected=auto_selected,
        source=source,
    )
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("semiconductor_dopant_sites", [])
        if isinstance(item, dict)
    ]
    previous.append(record)
    metadata_updates: dict[str, Any] = {
        "semiconductor_dopant_sites": previous,
        "last_semiconductor_dopant_site": record,
    }
    if auto_selected:
        metadata_updates["nl_auto_selected_sites"] = _auto_selected_sites_metadata(
            current_spec,
            operation="dopant",
            atom_id=atom.id,
            site_element=atom.element,
            new_element=element,
        )
    operations.append({"type": "set_metadata", "metadata_updates": metadata_updates})
    return operations


def _dopant_site_record(
    *,
    atom_id: str,
    site_element: str,
    dopant_element: str,
    fractional: list[float],
    auto_selected: bool,
    source: str,
) -> dict[str, Any]:
    return {
        "site_id": atom_id,
        "atom_id": atom_id,
        "site_element": site_element,
        "dopant_element": dopant_element,
        "new_element": dopant_element,
        "fractional": fractional,
        "auto_selected_site": auto_selected,
        "source": source,
    }


def _match_crystal_dopant_fraction(text: str) -> tuple[str | None, str, float] | None:
    percent = r"(?P<percent>\d+(?:\.\d+)?)\s*[%\uff05]"
    patterns = [
        rf"\b(?:replace|substitute)\s+{percent}\s+(?:of\s+)?(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:with|by|to)\s+(?P<dopant>{ELEMENT_TERM_PATTERN})\s*(?:dopants?|doping)?\b",
        rf"\b(?:dope|doping)\s+(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:with|using)\s+{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?:dope|doping|dopant)\s+(?:with\s+)?{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})(?:\s+(?:dopants?|doped|doping))?\b",
        rf"\b(?:make|create|build|set|form|generate)\b.*?{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})\s+(?:doped|dopant|doping)\b(?:\s+(?P<host>{ELEMENT_TERM_PATTERN}))?",
        rf"\b(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:with\s+)?{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})\s+(?:doped|doping|dopant)\b",
        rf"\b{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})\s+(?:doped|doping|dopant)\s+(?P<host>{ELEMENT_TERM_PATTERN})\b",
        rf"(?:\u63ba\u6742|\u63ba\u5165|\u5f15\u5165\u63ba\u6742|\u52a0\u5165\u63ba\u6742)\s*(?:with\s+)?{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})",
        rf"{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})\s*(?:\u63ba\u6742|\u63ba\u5165|\u66ff\u4f4d\u63ba\u6742)",
        rf"(?P<host>{ELEMENT_TERM_PATTERN})\s*(?:\u4e2d|\u6676\u4f53\u4e2d)?\s*(?:\u63ba\u6742|\u63ba\u5165|\u5f15\u5165)\s*{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})",
        rf"(?:\u63ba\u6742\u6d53\u5ea6|\u63ba\u6742\u6bd4\u4f8b|\u63ba\u6742\u542b\u91cf)\s*(?:\u4e3a|=|:|\u5230|\u8bbe\u7f6e\u4e3a)?\s*{percent}\s*(?P<dopant>{ELEMENT_TERM_PATTERN})",
        rf"(?P<dopant>{ELEMENT_TERM_PATTERN})\s*(?:\u7684)?\s*(?:\u63ba\u6742\u6d53\u5ea6|\u63ba\u6742\u6bd4\u4f8b|\u63ba\u6742\u542b\u91cf)\s*(?:\u4e3a|=|:|\u5230)?\s*{percent}",
        rf"(?P<dopant>{ELEMENT_TERM_PATTERN})\s*(?:\u63ba\u6742|\u63ba\u5165)\s*(?:\u6d53\u5ea6|\u6bd4\u4f8b|\u542b\u91cf)?\s*(?:\u4e3a|=|:|\u5230)?\s*{percent}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        if _fraction_replacement_should_be_alloy(text, match.group(0)):
            continue
        dopant = _normalize_element(match.group("dopant"))
        host_raw = match.groupdict().get("host")
        host = _normalize_element(host_raw) if host_raw else None
        fraction = float(match.group("percent")) / 100.0
        if dopant is None or not 0.0 < fraction <= 1.0:
            continue
        if host == dopant:
            continue
        return host, dopant, fraction
    fraction_match = re.search(
        rf"\b(?:dope|doping|dopant)\b.*?\bx\s*=\s*(?P<fraction>0?\.\d+|1(?:\.0+)?)\s*(?P<dopant>{ELEMENT_TERM_PATTERN})\b",
        text,
        flags=re.IGNORECASE,
    )
    if fraction_match is not None:
        dopant = _normalize_element(fraction_match.group("dopant"))
        fraction = float(fraction_match.group("fraction"))
        if dopant is not None and 0.0 < fraction <= 1.0:
            return None, dopant, fraction
    return None


def _fraction_replacement_should_be_alloy(text: str, matched_text: str) -> bool:
    """Prefer alloy/composition semantics for percent replacement unless doping intent is explicit."""

    if not re.search(
        r"\b(?:replace|substitute|replacing|substituting)\b|\u66ff\u6362|\u53d6\u4ee3",
        matched_text,
        flags=re.IGNORECASE,
    ):
        return False
    return not _text_explicitly_requests_dopant_fraction(text)


def _text_explicitly_requests_dopant_fraction(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:dope|doped|doping|dopant|n\s*[- ]?\s*type|p\s*[- ]?\s*type|donor|acceptor)\b",
            text,
            flags=re.IGNORECASE,
        )
        or any(term in text for term in ("\u63ba\u6742", "\u63ba\u5165", "\u66ff\u4f4d\u63ba\u6742", "n\u578b", "p\u578b", "\u65bd\u4e3b", "\u53d7\u4e3b"))
    )


def _crystal_dopant_fraction_operations(
    current_spec: ModelSpec,
    host_element: str | None,
    dopant_element: str,
    fraction: float,
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("crystal dopant fraction requires a crystal model.")
    crystal = current_spec.model
    host = host_element or _preferred_dopant_site_element(crystal, dopant_element)
    if host is None:
        raise ValueError("Could not choose a deterministic host sublattice for doping; provide the host element explicitly.")
    candidates = [atom for atom in crystal.basis_atoms if atom.element == host]
    if not candidates:
        raise ValueError(f"No {host} sites are available for doping.")
    target_count = max(1, int(math.floor(len(candidates) * fraction + 0.5)))
    target_count = min(target_count, len(candidates))
    selected = sorted(candidates, key=lambda atom: _crystal_atom_sort_key(atom.id))[:target_count]
    operations: list[dict[str, Any]] = [
        {"type": "substitute_atom", "atom_id": atom.id, "new_element": dopant_element}
        for atom in selected
    ]
    actual_fraction = target_count / len(candidates)
    record = {
        "host_element": host,
        "dopant_element": dopant_element,
        "requested_fraction": round(fraction, 6),
        "requested_percent": round(100.0 * fraction, 6),
        "actual_fraction": round(actual_fraction, 6),
        "actual_percent": round(100.0 * actual_fraction, 6),
        "candidate_site_count": len(candidates),
        "substituted_site_count": target_count,
        "selected_atom_ids": [atom.id for atom in selected],
        "rounding_error_fraction": round(actual_fraction - fraction, 6),
        "source": "natural_language_crystal_dopant_fraction",
    }
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("applied_dopant_fraction", [])
        if isinstance(item, dict)
    ]
    previous.append(record)
    dopant_sites = [
        dict(item)
        for item in (current_spec.metadata or {}).get("semiconductor_dopant_sites", [])
        if isinstance(item, dict)
    ]
    for atom in selected:
        dopant_sites.append(
            _dopant_site_record(
                atom_id=atom.id,
                site_element=host,
                dopant_element=dopant_element,
                fractional=[
                    _round_fractional(atom.fractional.x),
                    _round_fractional(atom.fractional.y),
                    _round_fractional(atom.fractional.z),
                ],
                auto_selected=True,
                source="natural_language_crystal_dopant_fraction",
            )
        )
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "applied_dopant_fraction": previous,
                "last_applied_dopant_fraction": record,
                "semiconductor_dopant_sites": dopant_sites,
                "last_semiconductor_dopant_site": dopant_sites[-1],
            },
        }
    )
    return operations


def _match_crystal_alloy_fraction(text: str) -> tuple[str | None, str, float] | None:
    percent = r"(?P<percent>\d+(?:\.\d+)?)\s*[%\uff05]"
    patterns = [
        rf"\b(?:replace|substitute)\s+{percent}\s+(?:of\s+)?(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:with|by|to)\s+(?P<alloy>{ELEMENT_TERM_PATTERN})\b",
        rf"\b{percent}\s+(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:replaced|substituted)\s+(?:with|by|to)\s+(?P<alloy>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?:make|create|build|set|form|generate)\s+(?:an?\s+)?(?:alloy|alloyed\s+structure)\s+(?:with\s+)?{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})\b",
        rf"\b(?:make|create|build|set|form|generate)?\s*{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})\s+(?:alloy|alloyed|composition)\b",
        rf"(?:make|create|build|set|form|generate|\u6539\u6210|\u53d8\u6210|\u5236\u6210)?\s*{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})\s*(?:\u5408\u91d1|\u5408\u91d1\u5316|\u7ec4\u5206|\u542b\u91cf)",
        rf"\balloy\s+(?:with\s+)?{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})\b",
        rf"(?:\u66ff\u6362|\u53d6\u4ee3|\u6362\u6210|\u6362\u4e3a|\u6539\u6210|\u6539\u4e3a)\s*{percent}\s*(?P<host>{ELEMENT_TERM_PATTERN})\s*(?:\u4e3a|\u6210|\u7528|with|to)?\s*(?P<alloy>{ELEMENT_TERM_PATTERN})",
        rf"{percent}\s*(?P<host>{ELEMENT_TERM_PATTERN})\s*(?:\u88ab)?(?:\u66ff\u6362|\u53d6\u4ee3|\u6362\u6210|\u6362\u4e3a|\u6539\u6210|\u6539\u4e3a)(?:\u4e3a|\u6210|\u7528|with|to)?\s*(?P<alloy>{ELEMENT_TERM_PATTERN})",
        rf"(?:\u5408\u91d1\u5316|\u5408\u91d1)\s*(?:\u4e3a|\u5230|with)?\s*{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})",
        rf"(?:\u5408\u91d1\u6bd4\u4f8b|\u5408\u91d1\u7ec4\u5206|\u5408\u91d1\u542b\u91cf|\u7ec4\u5206|\u542b\u91cf)\s*(?:\u4e3a|=|:|\u5230|\u8bbe\u7f6e\u4e3a)?\s*{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})",
        rf"(?P<alloy>{ELEMENT_TERM_PATTERN})\s*(?:\u7684)?\s*(?:\u5408\u91d1\u6bd4\u4f8b|\u5408\u91d1\u7ec4\u5206|\u5408\u91d1\u542b\u91cf|\u7ec4\u5206|\u542b\u91cf)\s*(?:\u4e3a|=|:|\u5230)?\s*{percent}",
        rf"(?P<host>{ELEMENT_TERM_PATTERN})\s*(?:\u4e2d|\u6676\u4f53\u4e2d)?\s*(?:\u52a0\u5165|\u6dfb\u52a0|\u5f15\u5165|\u6df7\u5165|\u5408\u91d1\u5316)\s*{percent}\s*(?P<alloy>{ELEMENT_TERM_PATTERN})(?:\s*(?:\u5f62\u6210|\u5236\u6210)?\s*\u5408\u91d1)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        alloy = _normalize_element(match.group("alloy"))
        host_raw = match.groupdict().get("host")
        host = _normalize_element(host_raw) if host_raw else None
        fraction = float(match.group("percent")) / 100.0
        if alloy is None or not 0.0 < fraction <= 1.0:
            continue
        if host == alloy:
            continue
        return host, alloy, fraction
    fraction_patterns = [
        rf"\b(?P<alloy>{ELEMENT_TERM_PATTERN})\s+(?:fraction|content|composition)\s*(?:x\s*)?(?:=|:|is|to)?\s*(?P<fraction>0?\.\d+|1(?:\.0+)?)\b.*?\b(?:on|at|in)\s+(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)\b",
        rf"\b(?:set|make|create|build|use)?\s*(?P<alloy>{ELEMENT_TERM_PATTERN})\s+(?:fraction|content|composition)\s*x\s*=\s*(?P<fraction>0?\.\d+|1(?:\.0+)?)\b.*?\b(?P<host>{ELEMENT_TERM_PATTERN})\s+(?:site|sites|sublattice)\b",
        rf"(?P<alloy>{ELEMENT_TERM_PATTERN})\s*(?:\u7ec4\u5206|\u542b\u91cf|\u6bd4\u4f8b)\s*(?:x\s*)?(?:=|:|\uff1d|\uff1a|\u4e3a)?\s*(?P<fraction>0?\.\d+|1(?:\.0+)?)\b.*?(?P<host>{ELEMENT_TERM_PATTERN})\s*(?:\u4f4d\u70b9|\u4f4d|\u6676\u4f4d|\u5b50\u6676\u683c)",
    ]
    for pattern in fraction_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        alloy = _normalize_element(match.group("alloy"))
        host = _normalize_element(match.group("host"))
        fraction = float(match.group("fraction"))
        if alloy is not None and host is not None and alloy != host and 0.0 < fraction <= 1.0:
            return host, alloy, fraction
    fraction_match = re.search(
        rf"\balloy\b.*?\bx\s*=\s*(?P<fraction>0?\.\d+|1(?:\.0+)?)\s*(?P<alloy>{ELEMENT_TERM_PATTERN})\b",
        text,
        flags=re.IGNORECASE,
    )
    if fraction_match is not None:
        raw_alloy = fraction_match.group("alloy")
        if raw_alloy.lower() == "as" and re.match(r"\s+(?:a|an|the)\b", text[fraction_match.end() :], flags=re.IGNORECASE):
            return None
        alloy = _normalize_element(raw_alloy)
        fraction = float(fraction_match.group("fraction"))
        if alloy is not None and 0.0 < fraction <= 1.0:
            return None, alloy, fraction
    return None


def _crystal_alloy_operations(
    current_spec: ModelSpec,
    host_element: str | None,
    alloy_element: str,
    fraction: float,
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        raise ValueError("crystal alloy fraction requires a crystal model.")
    crystal = current_spec.model
    host = host_element or _preferred_alloy_site_element(crystal, alloy_element)
    if host is None:
        raise ValueError("Could not choose a deterministic host sublattice for alloying; provide the host element explicitly.")
    candidates = [atom for atom in crystal.basis_atoms if atom.element == host]
    if not candidates:
        raise ValueError(f"No {host} sites are available for alloying.")
    target_count = max(1, int(math.floor(len(candidates) * fraction + 0.5)))
    target_count = min(target_count, len(candidates))
    selected = sorted(candidates, key=lambda atom: _crystal_atom_sort_key(atom.id))[:target_count]
    operations: list[dict[str, Any]] = [
        {"type": "substitute_atom", "atom_id": atom.id, "new_element": alloy_element}
        for atom in selected
    ]
    actual_fraction = target_count / len(candidates)
    record = {
        "host_element": host,
        "alloy_element": alloy_element,
        "requested_fraction": round(fraction, 6),
        "requested_percent": round(100.0 * fraction, 6),
        "actual_fraction": round(actual_fraction, 6),
        "actual_percent": round(100.0 * actual_fraction, 6),
        "candidate_site_count": len(candidates),
        "substituted_site_count": target_count,
        "selected_atom_ids": [atom.id for atom in selected],
        "rounding_error_fraction": round(actual_fraction - fraction, 6),
        "source": "natural_language_crystal_alloy_fraction",
    }
    previous = [
        dict(item)
        for item in (current_spec.metadata or {}).get("applied_alloy", [])
        if isinstance(item, dict)
    ]
    previous.append(record)
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "applied_alloy": previous,
                "last_applied_alloy": record,
            },
        }
    )
    return operations


def _preferred_alloy_site_element(crystal: CrystalSpec, alloy_element: str) -> str | None:
    elements = sorted({atom.element for atom in crystal.basis_atoms if atom.element != alloy_element})
    if not elements:
        return None
    group_iii = {"B", "Al", "Ga", "In"}
    group_iv = {"C", "Si", "Ge", "Sn"}
    group_v = {"N", "P", "As", "Sb"}
    group_ii = {"Mg", "Zn", "Cd"}
    group_vi = {"O", "S", "Se", "Te"}
    has_tmd = bool(set(elements) & TMD_METALS and set(elements) & TMD_CHALCOGENS)
    if has_tmd and alloy_element in TMD_METALS:
        candidates = [element for element in elements if element in TMD_METALS]
    elif has_tmd and alloy_element in TMD_CHALCOGENS:
        candidates = [element for element in elements if element in TMD_CHALCOGENS]
    elif alloy_element in group_iv:
        candidates = [element for element in elements if element in group_iv]
    elif alloy_element in group_ii:
        candidates = [element for element in elements if element in group_ii]
    elif alloy_element in group_iii:
        candidates = [element for element in elements if element in group_iii]
    elif alloy_element in group_v:
        candidates = [element for element in elements if element in group_v]
    elif alloy_element in group_vi:
        candidates = [element for element in elements if element in group_vi]
    else:
        candidates = elements
    if not candidates:
        candidates = elements
    counts = {element: sum(1 for atom in crystal.basis_atoms if atom.element == element) for element in candidates}
    max_count = max(counts.values())
    return sorted(element for element, count in counts.items() if count == max_count)[0]


def _crystal_atom_sort_key(atom_id: str) -> tuple[str, int, str]:
    match = re.match(r"([A-Za-z]+)(\d+)(.*)", atom_id)
    if match is None:
        return atom_id, 0, ""
    return match.group(1), int(match.group(2)), match.group(3)


def _match_crystal_antisite(text: str) -> tuple[str, str] | None:
    patterns = [
        r"\b(?:create|make|add|introduce)\s+(?P<element>[A-Za-z]{1,2})\s+antisite(?:\s+defect)?\s+(?:at|on)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\b",
        r"\b(?:replace|substitute)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+(?:with|to|as)\s+(?P<element>[A-Za-z]{1,2})\s+antisite\b",
        r"\b(?P<element>[A-Za-z]{1,2})[_ -](?:on|at)[_ -](?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+antisite\b",
        r"(?:\u521b\u5efa|\u751f\u6210|\u6dfb\u52a0|\u5f15\u5165)\s*(?P<element>[A-Za-z]{1,2})\s*\u53cd\u4f4d(?:\u7f3a\u9677)?\s*(?:\u4e8e|\u5728|\u5230|\u81f3)?\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)",
        r"(?:\u5728)?\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s*(?:\u4f4d\u70b9|\u4f4d\u7f6e|site)?\s*(?:\u521b\u5efa|\u751f\u6210|\u6dfb\u52a0|\u5f15\u5165)\s*(?P<element>[A-Za-z]{1,2})\s*\u53cd\u4f4d(?:\u7f3a\u9677)?",
        r"(?:\u628a|\u5c06)?\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s*(?:\u66ff\u6362\u4e3a|\u66ff\u6362\u6210|\u6362\u6210|\u6539\u6210|\u6539\u4e3a|\u53d8\u6210|\u53d8\u4e3a|\u8bbe\u4e3a)\s*(?P<element>[A-Za-z]{1,2})\s*\u53cd\u4f4d(?:\u7f3a\u9677)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        element = _normalize_element(match.group("element"))
        if element is not None:
            return match.group("atom_id"), element
    return None


def _crystal_antisite_operations(current_spec: ModelSpec, atom_id: str, element: str) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = [{"type": "substitute_atom", "atom_id": atom_id, "new_element": element}]
    if not isinstance(current_spec.model, CrystalSpec):
        return operations
    atom = next((item for item in current_spec.model.basis_atoms if item.id == atom_id), None)
    if atom is None:
        return operations
    metadata = dict(current_spec.metadata or {})
    defects = [dict(item) for item in metadata.get("defects", []) if isinstance(item, dict)]
    defects.append(
        {
            "type": "antisite",
            "site_id": atom.id,
            "atom_id": atom.id,
            "site_element": atom.element,
            "original_element": atom.element,
            "element": element,
            "new_element": element,
            "fractional": [
                _round_fractional(atom.fractional.x),
                _round_fractional(atom.fractional.y),
                _round_fractional(atom.fractional.z),
            ],
            "source": "natural_language_crystal_antisite",
        }
    )
    defect_types = sorted({str(item.get("type")) for item in defects if item.get("type")})
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "defects": defects,
                "defect_count": len(defects),
                "defect_types": defect_types,
            },
        }
    )
    return operations



def _match_crystal_interstitial_fractional(text: str) -> tuple[str | None, str, list[float]] | None:
    element_terms = ELEMENT_TERM_PATTERN
    fractional_word = _fractional_word_pattern()
    coord = r"\(?\s*(?P<x>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<y>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<z>-?\d+(?:\.\d+)?)"
    patterns = [
        rf"\b(?:add|create|place|insert|introduce)\s+(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>{element_terms})\s+(?:interstitial|interstitial\s+atom)\s+(?:at|to|on)\s+{fractional_word}\s*{coord}",
        rf"\b(?:add|create|place|insert|introduce)\s+(?:an?\s+)?(?:interstitial|interstitial\s+atom)\s+(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>{element_terms})\s+(?:at|to|on)\s+{fractional_word}\s*{coord}",
        rf"(?:\u6dfb\u52a0|\u521b\u5efa|\u653e\u7f6e|\u63d2\u5165)\s+(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>{element_terms})\s*(?:\u95f4\u9699\u539f\u5b50|\u95f4\u9699)\s*(?:\u5230|\u5728)\s*{fractional_word}\s*{coord}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        element = _normalize_element(match.group("element"))
        if element is None:
            continue
        atom_id = match.groupdict().get("atom_id")
        if atom_id is not None and _normalize_element(atom_id) == element:
            atom_id = None
        return atom_id, element, _fractional_coords_from_match(match)
    return None


def _crystal_interstitial_operations(
    current_spec: ModelSpec,
    atom_id: str | None,
    element: str,
    fractional: list[float],
) -> list[dict[str, Any]]:
    if not isinstance(current_spec.model, CrystalSpec):
        operation: dict[str, Any] = {"type": "add_atom", "element": element, "fractional": fractional}
        if atom_id:
            operation["id"] = atom_id
        return [operation]
    used_ids = {atom.id for atom in current_spec.model.basis_atoms}
    chosen_id = atom_id or _next_crystal_atom_id_from_used(element, used_ids)
    defects = [dict(item) for item in (current_spec.metadata or {}).get("defects", []) if isinstance(item, dict)]
    defects.append(
        {
            "type": "interstitial",
            "atom_id": chosen_id,
            "site_id": chosen_id,
            "element": element,
            "site_element": element,
            "fractional": [_round_fractional(value) for value in fractional],
            "source": "natural_language_crystal_interstitial",
        }
    )
    defect_types = sorted({str(item.get("type")) for item in defects if item.get("type")})
    return [
        {"type": "add_atom", "id": chosen_id, "element": element, "fractional": fractional},
        {
            "type": "set_metadata",
            "metadata_updates": {
                "defects": defects,
                "defect_count": len(defects),
                "defect_types": defect_types,
            },
        },
    ]



def _match_crystal_hydrogen_passivation_request(text: str) -> str | None:
    if re.search(
        r"\b(?:h[- ]?passivat(?:e|ed|ion)?|hydrogen[- ]?passivat(?:e|ed|ion)?|passivat(?:e|ed|ion)|hydrogenat(?:e|ed|ion)|hydrogen[- ]?terminat(?:e|ed|ion))\b",
        text,
        flags=re.IGNORECASE,
    ):
        return "hydrogen"
    if any(
        term in text
        for term in (
            "\u6c22\u949d\u5316",
            "\u52a0\u6c22",
            "\u6c22\u5316",
            "\u6c22\u9971\u548c",
            "\u8868\u9762\u949d\u5316",
            "\u9971\u548c\u60ac\u6302\u952e",
            "\u949d\u5316\u60ac\u6302\u952e",
            "\u60ac\u6302\u952e\u949d\u5316",
            "\u949d\u5316\u6240\u6709\u60ac\u6302\u952e",
            "\u949d\u5316\u5168\u90e8\u60ac\u6302\u952e",
            "\u9971\u548c\u6240\u6709\u60ac\u6302\u952e",
            "\u9971\u548c\u5168\u90e8\u60ac\u6302\u952e",
        )
    ):
        return "hydrogen"
    if "\u949d\u5316" in text and "\u60ac\u6302\u952e" in text:
        return "hydrogen"
    return None


def _match_crystal_hydrogen_passivation(text: str, current_spec: ModelSpec) -> tuple[list[dict[str, Any]], list[str]] | None:
    if _match_crystal_hydrogen_passivation_request(text) is None:
        return None
    if not isinstance(current_spec.model, CrystalSpec):
        return None
    metadata = dict(current_spec.metadata or {})
    model_name = current_spec.model.name.lower()
    if "surface_orientation" not in metadata and "slab" not in model_name and "surface" not in model_name:
        return None

    surface_axis = str(metadata.get("surface_axis") or "c").lower()
    axis_key = {"x": "a", "y": "b", "z": "c"}.get(surface_axis, surface_axis)
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_key)
    axis_length = {
        "a": current_spec.model.lattice.a,
        "b": current_spec.model.lattice.b,
        "c": current_spec.model.lattice.c,
    }.get(axis_key)
    if axis_index is None or axis_length is None:
        return None

    surfaces = _requested_passivation_surfaces(text)
    full_passivation = _requested_full_hydrogen_passivation(text)
    operations: list[dict[str, Any]] = []
    used_ids = {atom.id for atom in current_spec.model.basis_atoms}
    for surface in surfaces:
        atoms = _surface_atoms(current_spec.model, axis_index=axis_index, surface=surface)
        sign = 1.0 if surface == "top" else -1.0
        for atom in atoms:
            bond_length = _hydrogen_passivation_bond_length(atom.element)
            count = _hydrogen_count_for_surface_atom(current_spec.model, atom, full_passivation=full_passivation)
            for fractional in _hydrogen_passivation_fractionals(
                current_spec.model,
                atom,
                axis_index=axis_index,
                sign=sign,
                bond_length=bond_length,
                count=count,
            ):
                if _crystal_atom_exists_near(current_spec.model, "H", fractional):
                    continue
                atom_id = _next_crystal_atom_id_from_used(f"H{surface}", used_ids)
                used_ids.add(atom_id)
                operations.append({"type": "add_atom", "id": atom_id, "element": "H", "fractional": fractional})

    if not operations:
        return None

    termination_prefix = "fully_hydrogen_passivated" if full_passivation else "hydrogen_passivated"
    termination = termination_prefix + "_" + ("both" if set(surfaces) == {"top", "bottom"} else surfaces[0])
    operations.append(
        {
            "type": "set_metadata",
            "metadata_updates": {
                "termination": termination,
                "passivation": {
                    "element": "H",
                    "surfaces": surfaces,
                    "surface_axis": axis_key,
                    "added_atom_count": len([operation for operation in operations if operation["type"] == "add_atom"]),
                    "previous_termination": metadata.get("termination"),
                    "method": "deterministic_fractional_surface_offset",
                    "full_passivation_requested": full_passivation,
                },
            },
        }
    )
    return operations, surfaces



def _requested_passivation_surfaces(text: str) -> list[str]:
    if re.search(r"\b(?:both|all)\s+(?:surfaces|sides)\b|\btop\s+and\s+bottom\b|\bbottom\s+and\s+top\b", text, flags=re.IGNORECASE):
        return ["top", "bottom"]
    if any(term in text for term in ("\u4e0a\u4e0b", "\u53cc\u9762", "\u4e24\u9762", "\u53cc\u4fa7", "\u4e24\u4fa7")):
        return ["top", "bottom"]
    if re.search(r"\b(?:bottom|lower|backside)\b", text, flags=re.IGNORECASE) or any(term in text for term in ("\u5e95\u90e8", "\u4e0b\u8868\u9762", "\u80cc\u9762")):
        return ["bottom"]
    if re.search(r"\b(?:top|upper|frontside)\b", text, flags=re.IGNORECASE) or any(term in text for term in ("\u9876\u90e8", "\u4e0a\u8868\u9762", "\u6b63\u9762")):
        return ["top"]
    if re.search(r"\ball\s+dangling\s+bonds?\b", text, flags=re.IGNORECASE) or any(
        term in text for term in ("\u6240\u6709\u60ac\u6302\u952e", "\u5168\u90e8\u60ac\u6302\u952e")
    ):
        return ["top", "bottom"]
    return ["top"]



def _requested_full_hydrogen_passivation(text: str) -> bool:
    if re.search(
        r"\b(?:fully|complete(?:ly)?|saturat(?:e|ed|ion)|all\s+dangling\s+bonds?|every\s+dangling\s+bond)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return any(
        term in text
        for term in (
            "\u5b8c\u5168\u949d\u5316",
            "\u5b8c\u5168\u6c22\u949d\u5316",
            "\u5168\u90e8\u949d\u5316",
            "\u5168\u90e8\u6c22\u949d\u5316",
            "\u5b8c\u5168\u6c22\u5316",
            "\u5168\u90e8\u6c22\u5316",
            "\u6240\u6709\u60ac\u6302\u952e",
            "\u5168\u90e8\u60ac\u6302\u952e",
            "\u9971\u548c\u60ac\u6302\u952e",
        )
    )


def _surface_atoms(crystal: CrystalSpec, *, axis_index: int, surface: str) -> list[Any]:
    values = [
        (atom.fractional.x, atom.fractional.y, atom.fractional.z)[axis_index]
        for atom in crystal.basis_atoms
    ]
    target = max(values) if surface == "top" else min(values)
    tolerance = 1e-5
    atoms = [
        atom
        for atom in crystal.basis_atoms
        if abs((atom.fractional.x, atom.fractional.y, atom.fractional.z)[axis_index] - target) <= tolerance
    ]
    return sorted(atoms, key=lambda atom: (atom.fractional.x, atom.fractional.y, atom.fractional.z, atom.id))


def _hydrogen_count_for_surface_atom(crystal: CrystalSpec, atom: Any, *, full_passivation: bool) -> int:
    if not full_passivation:
        return 1
    expected = _expected_crystal_coordination(atom.element)
    if expected is None:
        return 1
    return max(expected - _crystal_neighbor_count(crystal, atom), 1)


def _expected_crystal_coordination(element: str) -> int | None:
    if element in {"C", "Si", "Ge", "Sn", "B", "Al", "Ga", "In", "N", "P", "As", "Sb"}:
        return 4
    return None


def _crystal_neighbor_count(crystal: CrystalSpec, target_atom: Any) -> int:
    target = (target_atom.fractional.x, target_atom.fractional.y, target_atom.fractional.z)
    count = 0
    for atom in crystal.basis_atoms:
        if atom.id == target_atom.id:
            continue
        other = (atom.fractional.x, atom.fractional.y, atom.fractional.z)
        distance = _minimum_image_fractional_distance(crystal, target, other)
        if distance <= _crystal_neighbor_threshold(target_atom.element, atom.element):
            count += 1
    return count


def _hydrogen_passivation_fractionals(
    crystal: CrystalSpec,
    atom: Any,
    *,
    axis_index: int,
    sign: float,
    bond_length: float,
    count: int,
) -> list[list[float]]:
    base = [atom.fractional.x, atom.fractional.y, atom.fractional.z]
    normal_component = bond_length
    lateral_offsets = _passivation_lateral_offsets(count, axis_index=axis_index)
    result = []
    for lateral_axis, lateral_angstrom in lateral_offsets:
        fractional = list(base)
        fractional[axis_index] = _wrap_fractional(fractional[axis_index] + sign * normal_component / _axis_length(crystal, axis_index))
        if lateral_axis is not None and lateral_axis != axis_index:
            fractional[lateral_axis] = _wrap_fractional(fractional[lateral_axis] + lateral_angstrom / _axis_length(crystal, lateral_axis))
        result.append([_round_fractional(value) for value in fractional])
    return result


def _passivation_lateral_offsets(count: int, *, axis_index: int) -> list[tuple[int | None, float]]:
    if count <= 1:
        return [(None, 0.0)]
    lateral_axes = [index for index in (0, 1, 2) if index != axis_index]
    primary_axis = lateral_axes[0]
    secondary_axis = lateral_axes[1]
    if count == 2:
        return [(primary_axis, -0.45), (primary_axis, 0.45)]
    if count == 3:
        return [(primary_axis, -0.52), (primary_axis, 0.52), (secondary_axis, 0.52)]
    return [
        (primary_axis, -0.52),
        (primary_axis, 0.52),
        (secondary_axis, -0.52),
        (secondary_axis, 0.52),
    ][:count]


def _axis_length(crystal: CrystalSpec, axis_index: int) -> float:
    return (crystal.lattice.a, crystal.lattice.b, crystal.lattice.c)[axis_index]


def _minimum_image_fractional_distance(
    crystal: CrystalSpec,
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    vectors = _lattice_vectors(crystal)
    best: float | None = None
    for da in (-1, 0, 1):
        for db in (-1, 0, 1):
            for dc in (-1, 0, 1):
                diff = (right[0] + da - left[0], right[1] + db - left[1], right[2] + dc - left[2])
                cart = tuple(
                    diff[0] * vectors[0][index] + diff[1] * vectors[1][index] + diff[2] * vectors[2][index]
                    for index in range(3)
                )
                distance = math.sqrt(sum(value * value for value in cart))
                if best is None or distance < best:
                    best = distance
    return float(best or 0.0)


def _lattice_vectors(crystal: CrystalSpec) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    alpha = math.radians(crystal.lattice.alpha)
    beta = math.radians(crystal.lattice.beta)
    gamma = math.radians(crystal.lattice.gamma)
    a_vec = (crystal.lattice.a, 0.0, 0.0)
    b_vec = (crystal.lattice.b * math.cos(gamma), crystal.lattice.b * math.sin(gamma), 0.0)
    cx = crystal.lattice.c * math.cos(beta)
    cy = crystal.lattice.c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / max(math.sin(gamma), 1e-12)
    cz2 = max(crystal.lattice.c * crystal.lattice.c - cx * cx - cy * cy, 0.0)
    return a_vec, b_vec, (cx, cy, math.sqrt(cz2))


def _crystal_neighbor_threshold(element1: str, element2: str) -> float:
    radii = {
        "H": 0.31,
        "C": 0.76,
        "N": 0.71,
        "Mg": 1.41,
        "P": 1.07,
        "Si": 1.11,
        "Ge": 1.20,
        "Al": 1.21,
        "Ga": 1.22,
        "As": 1.19,
        "Sb": 1.39,
        "In": 1.42,
        "Sn": 1.39,
    }
    return max(0.8, 1.25 * (radii.get(element1, 0.9) + radii.get(element2, 0.9)))


def _looks_like_explicit_atom_id(value: str) -> bool:
    return bool(re.search(r"\d", value))


def _auto_select_crystal_site(
    spec: ModelSpec,
    *,
    requested_site_element: str | None = None,
    replacing_with: str | None = None,
    operation: str,
) -> Any:
    if not isinstance(spec.model, CrystalSpec):
        raise ValueError(f"{operation} auto-site selection requires a crystal model.")
    atoms = list(spec.model.basis_atoms)
    if requested_site_element is None and replacing_with is not None:
        requested_site_element = _preferred_dopant_site_element(spec.model, replacing_with)
    if requested_site_element is not None:
        atoms = [atom for atom in atoms if atom.element == requested_site_element]
    if replacing_with is not None:
        atoms = [atom for atom in atoms if atom.element != replacing_with]
    if not atoms:
        site_note = f" for {requested_site_element}" if requested_site_element else ""
        raise ValueError(f"No suitable crystal site{site_note} is available for {operation}.")
    return atoms[0]


def _preferred_dopant_site_element(crystal: CrystalSpec, dopant_element: str) -> str | None:
    elements = {atom.element for atom in crystal.basis_atoms}
    group_ii = {"Mg", "Zn", "Cd"}
    group_iii = {"B", "Al", "Ga", "In"}
    group_iv = {"C", "Si", "Ge", "Sn"}
    group_v = {"N", "P", "As", "Sb"}
    group_vi = {"O", "S", "Se", "Te"}
    halogens = {"F", "Cl", "Br", "I"}
    tmd_metals = elements & TMD_METALS
    tmd_chalcogens = elements & TMD_CHALCOGENS
    if tmd_metals and tmd_chalcogens:
        if dopant_element in TMD_CHALCOGEN_SITE_DOPANTS:
            candidates = sorted(tmd_chalcogens - {dopant_element})
            return _majority_site_element(crystal, candidates)
        if dopant_element in TMD_METAL_SITE_DOPANTS:
            candidates = sorted(tmd_metals - {dopant_element})
            return _majority_site_element(crystal, candidates)
    if elements == {"B", "N"}:
        if dopant_element in group_ii or dopant_element in group_iii:
            return _majority_site_element(crystal, ["B"])
        if dopant_element in group_v or dopant_element in group_vi or dopant_element in halogens:
            return _majority_site_element(crystal, ["N"])
        if dopant_element in group_iv:
            return _majority_site_element(crystal, ["B"])
    if elements & group_iii and elements & group_v:
        if dopant_element in group_ii:
            candidates = sorted(elements & group_iii)
            return _majority_site_element(crystal, candidates)
        if dopant_element in group_v:
            candidates = sorted((elements & group_v) - {dopant_element})
            return _majority_site_element(crystal, candidates)
        if dopant_element in group_iii:
            candidates = sorted((elements & group_iii) - {dopant_element})
            return _majority_site_element(crystal, candidates)
        if dopant_element in group_iv:
            candidates = sorted(elements & group_iii)
            return _majority_site_element(crystal, candidates)
    if elements & group_ii and elements & group_vi:
        if dopant_element in group_vi:
            candidates = sorted((elements & group_vi) - {dopant_element})
            return _majority_site_element(crystal, candidates)
        if dopant_element in group_ii:
            candidates = sorted((elements & group_ii) - {dopant_element})
            return _majority_site_element(crystal, candidates)
        dopant_valence = {
            "B": 3,
            "Mg": 2,
            "Al": 3,
            "Ga": 3,
            "In": 3,
            "C": 4,
            "Si": 4,
            "Ge": 4,
            "Sn": 4,
            "N": 5,
            "P": 5,
            "As": 5,
            "Sb": 5,
            "F": 7,
            "Cl": 7,
            "Br": 7,
            "I": 7,
        }.get(dopant_element)
        if dopant_valence is not None:
            candidates = sorted(elements & (group_ii if dopant_valence <= 4 else group_vi))
            return _majority_site_element(crystal, candidates)
    if elements & group_iv and not elements & (group_ii | group_iii | group_v | group_vi):
        candidates = sorted((elements & group_iv) - {dopant_element})
        return _majority_site_element(crystal, candidates)
    host_candidates = sorted(elements - {dopant_element})
    return host_candidates[0] if len(host_candidates) == 1 else None


def _majority_site_element(crystal: CrystalSpec, candidates: Sequence[str]) -> str | None:
    available = [element for element in candidates if element]
    if not available:
        return None
    counts = {element: sum(1 for atom in crystal.basis_atoms if atom.element == element) for element in available}
    max_count = max(counts.values())
    return sorted(element for element, count in counts.items() if count == max_count)[0]


def _auto_selected_sites_metadata(
    spec: ModelSpec,
    *,
    operation: str,
    atom_id: str,
    site_element: str,
    new_element: str | None,
) -> list[dict[str, Any]]:
    entries = [
        dict(item)
        for item in (spec.metadata or {}).get("nl_auto_selected_sites", [])
        if isinstance(item, dict)
    ]
    entry: dict[str, Any] = {
        "operation": operation,
        "atom_id": atom_id,
        "site_element": site_element,
        "auto_selected_site": True,
        "selection_rule": "first_matching_semiconductor_site",
        "source": "natural_language_auto_site",
    }
    if new_element is not None:
        entry["new_element"] = new_element
    entries.append(entry)
    return entries


def _hydrogen_passivation_bond_length(element: str) -> float:
    return {
        "Si": 1.48,
        "Ge": 1.53,
        "Ga": 1.56,
        "As": 1.52,
        "N": 1.02,
        "P": 1.42,
        "C": 1.09,
    }.get(element, 1.20)


def _wrap_fractional(value: float) -> float:
    wrapped = value % 1.0
    if abs(wrapped - 1.0) <= 1e-12:
        return 0.0
    return wrapped


def _round_fractional(value: float) -> float:
    return round(float(value), 6)


def _crystal_atom_exists_near(crystal: CrystalSpec, element: str, fractional: list[float], *, tolerance: float = 1e-5) -> bool:
    for atom in crystal.basis_atoms:
        if atom.element != element:
            continue
        current = (atom.fractional.x, atom.fractional.y, atom.fractional.z)
        if all(abs(current[index] - fractional[index]) <= tolerance for index in range(3)):
            return True
    return False


def _next_crystal_atom_id_from_used(prefix: str, used: set[str]) -> str:
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"



def _match_crystal_add_atom_fractional(text: str) -> tuple[str | None, str, list[float]] | None:
    element_terms = ELEMENT_TERM_PATTERN
    fractional_word = _fractional_word_pattern()
    coord = r"\(?\s*(?P<x>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<y>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<z>-?\d+(?:\.\d+)?)"
    patterns = [
        rf"\b(?:add|create|place)\s+(?:atom\s+)?(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>{element_terms})\s+(?:atom\s+)?(?:at|to|on)\s+{fractional_word}\s*{coord}",
        rf"\b(?:add|create|place)\s+(?:atom\s+)?(?P<element>{element_terms})\s+(?:named|as)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+(?:at|to|on)\s+{fractional_word}\s*{coord}",
        rf"(?:\u5728|\u5230)?\s*{fractional_word}\s*{coord}\s*(?:\u6dfb\u52a0|\u521b\u5efa|\u653e\u7f6e)\s+(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>{element_terms})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        element = _normalize_element(match.group("element"))
        if element is None:
            continue
        atom_id = match.groupdict().get("atom_id")
        if atom_id is not None and _normalize_element(atom_id) == element:
            atom_id = None
        return atom_id, element, _fractional_coords_from_match(match)
    return None



def _match_crystal_set_atom_fractional(text: str) -> tuple[str, list[float]] | None:
    fractional_word = _fractional_word_pattern()
    coord = r"\(?\s*(?P<x>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<y>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<z>-?\d+(?:\.\d+)?)"
    patterns = [
        rf"\b(?:move|set|place)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+(?:to|at|on)\s+{fractional_word}\s*{coord}",
        rf"\b(?:move|set|place)\s+(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+{fractional_word}\s*{coord}",
        rf"(?:\u5c06|\u628a)?\s*(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s*(?:\u79fb\u52a8\u5230|\u79fb\u5230|\u8bbe\u4e3a|\u653e\u5230)\s*{fractional_word}\s*{coord}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return match.group("atom_id"), _fractional_coords_from_match(match)
    return None


def _fractional_coords_from_match(match: re.Match[str]) -> list[float]:
    return [float(match.group(name)) for name in ("x", "y", "z")]



def _fractional_word_pattern() -> str:
    return r"(?:fractional|fractional\s+coordinates?|\u5206\u6570\u5750\u6807)"


def _match_add_atom(text: str, current_spec: ModelSpec) -> tuple[str, str, list[float], str | None, str] | None:
    if not isinstance(current_spec.model, MoleculeSpec):
        return None
    match = re.search(
        r"\b(?:add|create)\s+(?:atom\s+)?"
        r"(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>[A-Za-z]{1,2}|hydrogen|carbon|nitrogen|oxygen|fluorine|chlorine|sulfur|phosphorus)\s+"
        r"(?:atom\s+)?(?:at|to)\s*\(?\s*"
        r"(?P<x>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<y>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<z>-?\d+(?:\.\d+)?)"
        r"(?:\s*\)?\s*(?:bonded\s+to|bond\s+to|connected\s+to)\s+(?P<bonded_to>[A-Za-z][A-Za-z0-9_-]*))?"
        r"(?:\s+(?:as|with)\s+(?P<bond_type>single|double|triple|aromatic|partial double)\s+bond)?",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        match = re.search(
            r"(?:\u5728|\u5230)?\s*\(?\s*(?P<x>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<y>-?\d+(?:\.\d+)?)\s*[, ]\s*(?P<z>-?\d+(?:\.\d+)?)\s*\)?\s*"
            r"(?:\u6dfb\u52a0|\u521b\u5efa)\s+(?:(?P<atom_id>[A-Za-z][A-Za-z0-9_-]*)\s+)?(?P<element>[A-Za-z]{1,2}|hydrogen|carbon|nitrogen|oxygen|fluorine|chlorine|sulfur|phosphorus)"
            r"(?:\s*(?:\u5e76)?\s*(?:\u8fde\u63a5\u5230|\u8fde\u5230|\u4e0e)\s*(?P<bonded_to>[A-Za-z][A-Za-z0-9_-]*))?",
            text,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    element = _normalize_element(match.group("element"))
    if element is None:
        return None
    atom_id = match.group("atom_id")
    if atom_id is None or _normalize_element(atom_id) == element:
        atom_id = _next_atom_id_for_spec(current_spec, element)
    coords = [float(match.group(name)) for name in ("x", "y", "z")]
    bond_type = _normalize_bond_type(match.groupdict().get("bond_type") or "single")
    if bond_type is None:
        return None
    return atom_id, element, coords, match.group("bonded_to"), bond_type


def _normalize_functional_group(raw: str) -> str | None:
    value = " ".join(raw.strip().lower().split())
    return FUNCTIONAL_GROUP_ALIASES.get(value)


def _first_bonded_hydrogen(atom_id: str, atoms: dict[str, Any], bonds_by_atom: dict[str, list[str]]) -> str | None:
    for neighbor_id in bonds_by_atom.get(atom_id, []):
        neighbor = atoms.get(neighbor_id)
        if neighbor is not None and neighbor.element == "H":
            return neighbor_id
    return None


def _direction_between(start: tuple[float, float, float], end: tuple[float, float, float]) -> tuple[float, float, float]:
    return _normalize((end[0] - start[0], end[1] - start[1], end[2] - start[2]))


def _perpendicular_basis(direction: tuple[float, float, float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    reference = (0.0, 0.0, 1.0)
    if abs(_dot(direction, reference)) > 0.9:
        reference = (0.0, 1.0, 0.0)
    first = _normalize(_cross(direction, reference))
    second = _normalize(_cross(direction, first))
    return first, second


def _next_atom_id_from_used(element: str, used: set[str]) -> str:
    index = 1
    while f"{element}{index}" in used:
        index += 1
    return f"{element}{index}"


def _add(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _scale(vector: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(vector, vector))
    if length <= 1e-12:
        return (1.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _round_xyz(vector: tuple[float, float, float]) -> list[float]:
    return [round(float(item), 6) for item in vector]


def _patch_plan(operations: list[dict[str, Any]], template_id: str, note: str) -> NaturalLanguagePlan:
    return NaturalLanguagePlan(
        kind="patch",
        payload={"operations": operations},
        confidence=0.78,
        template_id=template_id,
        notes=[note, "Generated from a precise atom-level local command."],
    )


def _normalize_element(raw: str) -> str | None:
    value = raw.strip()
    if value in ELEMENT_ALIASES:
        return ELEMENT_ALIASES[value]
    lowered = value.lower()
    if lowered in ELEMENT_ALIASES:
        return ELEMENT_ALIASES[lowered]
    symbol = value[:1].upper() + value[1:].lower()
    return symbol if symbol in ELEMENTS else None


def _normalize_bond_type(raw: str) -> str | None:
    value = " ".join(raw.strip().lower().split())
    return BOND_TYPE_ALIASES.get(value)


def _next_atom_id_for_spec(spec: ModelSpec, element: str) -> str:
    if not isinstance(spec.model, MoleculeSpec):
        return f"{element}1"
    used = {atom.id for atom in spec.model.atoms}
    index = 1
    while f"{element}{index}" in used:
        index += 1
    return f"{element}{index}"


def _load_example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


def _project_id(template_id: str, user_request: str) -> str:
    digest = hashlib.sha256(user_request.encode("utf-8")).hexdigest()[:8]
    safe_template = re.sub(r"[^A-Za-z0-9_-]+", "_", template_id).strip("_") or "model"
    safe_template = safe_template[:24].rstrip("_-") or "model"
    return f"nl_{safe_template}_{digest}"
