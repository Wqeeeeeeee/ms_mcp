"""语义补丁模型和应用助手。

此模块定义了用于修改结构化模型的数据模型和函数。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .castep import CastepEnergySpec, CastepTaskValue
from .common import ExecutionMode, FractionalVector3, StrictModel, Vector3
from .crystal import BasisAtomSpec, CrystalSpec, LatticeSpec
from .forcite import ForciteConvergence, ForciteOptimizationSpec, ForciteQuality
from .molecule import AtomSpec, BondSpec, MoleculeSpec
from .project import ModelSpec


PatchOperationType = Literal[
    "add_atom",
    "delete_atom",
    "add_bond",
    "delete_bond",
    "set_bond_type",
    "substitute_atom",
    "set_atom_position",
    "translate_crystal_atoms",
    "rotate_crystal_atoms",
    "make_commensurate_twisted_bilayer",
    "make_commensurate_tmd_heterobilayer",
    "set_total_charge",
    "set_spin_multiplicity",
    "set_forcite_optimization",
    "set_castep_energy",
    "make_supercell",
    "add_vacuum",
    "set_vacuum",
    "center_slab",
    "set_lattice",
    "set_metadata",
    "reconcile_dopant_metadata",
    "set_interface_gap",
    "set_gate_stack_thickness",
    "set_p_gan_gate_cap_thickness",
    "set_quantum_well_thickness",
]


class SemanticPatchOperation(StrictModel):
    """语义补丁操作。

    属性:
        operation: 操作类型
        id: 原子 ID
        element: 元素符号
        xyz_angstrom: 笛卡尔坐标
        atom_id: 原子 ID
        atom1: 第一个原子 ID
        atom2: 第二个原子 ID
        bond_type: 键类型
        new_element: 新元素符号
        total_charge: 总电荷
        spin_multiplicity: 自旋多重度
        forcefield: 力场名称
        quality: 计算质量
        charge_assignment: 电荷分配模式
        max_iterations: 最大迭代次数
        convergence: 收敛级别
        functional: 交换关联泛函
        cutoff_energy_ev: 截断能量
        kpoint_separation: k 点间距
        kpoints: k 点网格
        matrix: 超胞矩阵
        axis: 轴
        thickness_angstrom: 厚度
        lattice: 晶格规格
        fractional: 分数坐标
        nearest_cartesian_angstrom: 最近笛卡尔坐标
    """

    operation: PatchOperationType = Field(alias="type")
    id: str | None = None
    element: str | None = None
    xyz_angstrom: Vector3 | None = None
    atom_id: str | None = None
    atom_ids: list[str] | None = Field(default=None, min_length=1)
    atom1: str | None = None
    atom2: str | None = None
    bond_type: str | None = None
    new_element: str | None = None
    total_charge: int | None = None
    spin_multiplicity: int | None = Field(default=None, ge=1)
    forcefield: str | None = None
    quality: str | None = None
    charge_assignment: str | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=1_000_000)
    convergence: str | None = None
    task: CastepTaskValue | None = None
    functional: str | None = None
    cutoff_energy_ev: int | None = Field(default=None, ge=1, le=100_000)
    kpoint_separation: float | None = Field(default=None, gt=0, le=10)
    kpoints: tuple[int, int, int] | None = None
    matrix: tuple[int, int, int] | None = None
    axis: Literal["a", "b", "c", "x", "y", "z"] | None = None
    distance_angstrom: float | None = Field(default=None, ge=-1_000, le=1_000)
    angle_degrees: float | None = Field(default=None, ge=-360, le=360)
    pivot_fractional: FractionalVector3 | None = None
    wrap_fractional: bool = True
    commensurate_m: int | None = Field(default=None, ge=1, le=100)
    commensurate_n: int | None = Field(default=None, ge=0, le=99)
    interlayer_distance_angstrom: float | None = Field(default=None, gt=0, le=100)
    twist_orientation: Literal["counterclockwise", "clockwise"] | None = None
    max_atoms: int | None = Field(default=None, ge=6, le=20_000)
    top_layer_material: str | None = Field(default=None, min_length=3, max_length=16)
    strain_policy: Literal["balanced", "bottom_fixed", "top_fixed"] | None = None
    max_strain_percent: float | None = Field(default=None, gt=0, le=10)
    thickness_angstrom: float | None = Field(default=None, gt=0)
    target_layer: Literal["gate", "oxide", "channel", "well", "barrier"] | None = None
    lattice: LatticeSpec | None = None
    fractional: FractionalVector3 | None = None
    nearest_cartesian_angstrom: Vector3 | None = None
    metadata_updates: dict[str, Any] | None = None

    @field_validator("kpoints", "matrix")
    @classmethod
    def positive_int_tuple(cls, value: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
        """验证正整数元组。"""
        if value is not None and any(item <= 0 for item in value):
            raise ValueError("元组值必须是正整数")
        return value

    @field_validator("atom_ids")
    @classmethod
    def unique_atom_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [atom_id.strip() for atom_id in value]
        if any(not atom_id for atom_id in cleaned):
            raise ValueError("atom_ids must not contain empty identifiers")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("atom_ids must contain unique identifiers")
        return cleaned

    @model_validator(mode="after")
    def validate_known_operation(self) -> "SemanticPatchOperation":
        """验证已知操作。"""
        known = {
            "add_atom",
            "delete_atom",
            "add_bond",
            "delete_bond",
            "set_bond_type",
            "substitute_atom",
            "set_atom_position",
            "translate_crystal_atoms",
            "rotate_crystal_atoms",
            "make_commensurate_twisted_bilayer",
            "make_commensurate_tmd_heterobilayer",
            "set_total_charge",
            "set_spin_multiplicity",
            "set_forcite_optimization",
            "set_castep_energy",
            "make_supercell",
            "add_vacuum",
            "set_vacuum",
            "center_slab",
            "set_lattice",
            "set_metadata",
            "reconcile_dopant_metadata",
            "set_interface_gap",
            "set_gate_stack_thickness",
            "set_p_gan_gate_cap_thickness",
            "set_quantum_well_thickness",
        }
        if self.operation not in known:
            raise ValueError(f"不支持的补丁操作: {self.operation}")
        if self.operation == "translate_crystal_atoms":
            if not self.atom_ids or self.axis is None or self.distance_angstrom is None:
                raise ValueError("translate_crystal_atoms requires atom_ids, axis, and distance_angstrom")
            if not math.isfinite(float(self.distance_angstrom)):
                raise ValueError("translate_crystal_atoms distance_angstrom must be finite")
            if abs(float(self.distance_angstrom)) <= 1e-12:
                raise ValueError("translate_crystal_atoms distance_angstrom must be non-zero")
        if self.operation == "rotate_crystal_atoms":
            if not self.atom_ids or self.axis is None or self.angle_degrees is None:
                raise ValueError("rotate_crystal_atoms requires atom_ids, axis, and angle_degrees")
            if not math.isfinite(float(self.angle_degrees)):
                raise ValueError("rotate_crystal_atoms angle_degrees must be finite")
            if abs(float(self.angle_degrees)) <= 1e-12 or abs(float(self.angle_degrees)) >= 360.0 - 1e-12:
                raise ValueError("rotate_crystal_atoms angle_degrees must produce a non-identity rotation")
        if self.operation == "make_commensurate_twisted_bilayer":
            if (
                self.commensurate_m is None
                or self.commensurate_n is None
                or self.interlayer_distance_angstrom is None
            ):
                raise ValueError(
                    "make_commensurate_twisted_bilayer requires commensurate_m, "
                    "commensurate_n, and interlayer_distance_angstrom"
                )
            if self.commensurate_m <= self.commensurate_n:
                raise ValueError("commensurate twist indices must satisfy m > n >= 0")
            if math.gcd(self.commensurate_m, self.commensurate_n) != 1:
                raise ValueError("commensurate twist indices m and n must be coprime")
            if self.angle_degrees is not None and not (
                math.isfinite(float(self.angle_degrees))
                and 1e-12 < abs(float(self.angle_degrees)) <= 60.0 + 1e-12
            ):
                raise ValueError("commensurate twisted bilayer requested angle must be in (0, 60] degrees")
        if self.operation == "make_commensurate_tmd_heterobilayer":
            if (
                self.commensurate_m is None
                or self.commensurate_n is None
                or self.interlayer_distance_angstrom is None
                or self.top_layer_material is None
            ):
                raise ValueError(
                    "make_commensurate_tmd_heterobilayer requires commensurate_m, "
                    "commensurate_n, interlayer_distance_angstrom, and top_layer_material"
                )
            if self.commensurate_m <= self.commensurate_n:
                raise ValueError("commensurate heterobilayer indices must satisfy m > n >= 0")
            if math.gcd(self.commensurate_m, self.commensurate_n) != 1:
                raise ValueError("commensurate heterobilayer indices m and n must be coprime")
            if self.angle_degrees is not None and not (
                math.isfinite(float(self.angle_degrees))
                and 1e-12 < abs(float(self.angle_degrees)) <= 60.0 + 1e-12
            ):
                raise ValueError("commensurate TMD heterobilayer requested angle must be in (0, 60] degrees")
        return self


class SemanticPatch(StrictModel):
    """语义补丁。

    属性:
        project_id: 项目 ID
        base_revision: 基础修订版本
        operations: 操作列表
        run_after_apply: 应用后是否运行
        execution_mode: 执行模式
        force: 是否强制
    """

    project_id: str
    base_revision: int = Field(ge=0)
    operations: list[SemanticPatchOperation] = Field(min_length=1)
    run_after_apply: bool = False
    execution_mode: ExecutionMode = ExecutionMode.PREVIEW
    force: bool = False


def apply_semantic_patch(base_spec: ModelSpec, patch: SemanticPatch) -> tuple[ModelSpec, list[str]]:
    """应用语义补丁而不修改原始规格。

    参数:
        base_spec: 基础模型规格
        patch: 语义补丁

    返回:
        (新模型规格, 差异列表) 元组

    异常:
        ValueError: 如果项目 ID 不匹配或修订版本不匹配
    """
    if patch.project_id != base_spec.project_id:
        raise ValueError("补丁 project_id 与基础规格不匹配")
    if patch.base_revision != base_spec.revision and not patch.force:
        raise ValueError(
            f"补丁 base_revision {patch.base_revision} 与当前修订版本 {base_spec.revision} 不匹配"
        )

    updated = base_spec.model_copy(deep=True)
    diff: list[str] = []
    if isinstance(updated.model, MoleculeSpec):
        _apply_molecule_patch(updated, patch, diff)
    elif isinstance(updated.model, CrystalSpec):
        _apply_crystal_patch(updated, patch, diff)
        reconciliation = _reconcile_crystal_dopant_metadata(updated, diff)
        if (
            any(operation.operation == "reconcile_dopant_metadata" for operation in patch.operations)
            and not reconciliation["changed"]
        ):
            raise ValueError("当前晶体没有需要调和的掺杂位点元数据")
    else:
        _apply_imported_patch(updated, patch, diff)
    updated = updated.model_copy(update={"revision": base_spec.revision + 1})
    return ModelSpec.model_validate(updated.model_dump(mode="json")), diff


def _apply_molecule_patch(spec: ModelSpec, patch: SemanticPatch, diff: list[str]) -> None:
    """应用分子补丁。"""
    molecule = spec.model
    assert isinstance(molecule, MoleculeSpec)
    atoms = [atom.model_copy(deep=True) for atom in molecule.atoms]
    bonds = [bond.model_copy(deep=True) for bond in molecule.bonds]

    for operation in patch.operations:
        op = operation.operation
        if op == "set_metadata":
            _apply_metadata_patch(spec, operation, diff)
            continue
        if op == "add_atom":
            if not operation.element or operation.xyz_angstrom is None:
                raise ValueError("add_atom 需要 element 和 xyz_angstrom")
            atom_id = operation.id or _next_atom_id(operation.element, atoms)
            atoms.append(AtomSpec(id=atom_id, element=operation.element, xyz_angstrom=operation.xyz_angstrom))
            diff.append(f"add_atom {atom_id}")
        elif op == "delete_atom":
            atom_id = _required_atom_id(operation)
            atoms = [atom for atom in atoms if atom.id != atom_id]
            bonds = [bond for bond in bonds if bond.atom1 != atom_id and bond.atom2 != atom_id]
            diff.append(f"delete_atom {atom_id}")
        elif op == "add_bond":
            if not operation.atom1 or not operation.atom2:
                raise ValueError("add_bond 需要 atom1 和 atom2")
            bonds.append(BondSpec(atom1=operation.atom1, atom2=operation.atom2, type=operation.bond_type or "Single"))
            diff.append(f"add_bond {operation.atom1}-{operation.atom2}")
        elif op == "delete_bond":
            if not operation.atom1 or not operation.atom2:
                raise ValueError("delete_bond 需要 atom1 和 atom2")
            targets = {operation.atom1, operation.atom2}
            bonds = [bond for bond in bonds if {bond.atom1, bond.atom2} != targets]
            diff.append(f"delete_bond {operation.atom1}-{operation.atom2}")
        elif op == "set_bond_type":
            if not operation.atom1 or not operation.atom2 or not operation.bond_type:
                raise ValueError("set_bond_type 需要 atom1、atom2 和 bond_type")
            targets = {operation.atom1, operation.atom2}
            changed = False
            new_bonds = []
            for bond in bonds:
                if {bond.atom1, bond.atom2} == targets:
                    new_bonds.append(bond.model_copy(update={"type": operation.bond_type}))
                    changed = True
                else:
                    new_bonds.append(bond)
            if not changed:
                raise ValueError(f"set_bond_type 未找到键 {operation.atom1}-{operation.atom2}")
            bonds = new_bonds
            diff.append(f"set_bond_type {operation.atom1}-{operation.atom2} {operation.bond_type}")
        elif op == "substitute_atom":
            atom_id = _required_atom_id(operation)
            if not operation.new_element:
                raise ValueError("substitute_atom 需要 new_element")
            atoms = [
                atom.model_copy(update={"element": operation.new_element}) if atom.id == atom_id else atom
                for atom in atoms
            ]
            diff.append(f"substitute_atom {atom_id}->{operation.new_element}")
        elif op == "set_atom_position":
            atom_id = _required_atom_id(operation)
            if operation.xyz_angstrom is None:
                raise ValueError("set_atom_position 需要 xyz_angstrom")
            atoms = [
                atom.model_copy(update={"xyz_angstrom": operation.xyz_angstrom}) if atom.id == atom_id else atom
                for atom in atoms
            ]
            diff.append(f"set_atom_position {atom_id}")
        elif op == "set_total_charge":
            molecule = molecule.model_copy(update={"total_charge": operation.total_charge})
            diff.append(f"set_total_charge {operation.total_charge}")
        elif op == "set_spin_multiplicity":
            molecule = molecule.model_copy(update={"spin_multiplicity": operation.spin_multiplicity})
            diff.append(f"set_spin_multiplicity {operation.spin_multiplicity}")
        elif op == "set_forcite_optimization":
            spec.simulation = _forcite_from_operation(operation)
            diff.append("set_forcite_optimization")
        elif op == "set_castep_energy":
            spec.simulation = _castep_from_operation(operation)
            diff.append("set_castep_energy")
        else:
            raise ValueError(f"{op} 对分子模型无效")

    spec.model = MoleculeSpec(
        name=molecule.name,
        atoms=atoms,
        bonds=bonds,
        total_charge=molecule.total_charge,
        spin_multiplicity=molecule.spin_multiplicity,
    )


def _apply_crystal_patch(spec: ModelSpec, patch: SemanticPatch, diff: list[str]) -> None:
    """应用晶体补丁。"""
    crystal = spec.model
    assert isinstance(crystal, CrystalSpec)
    lattice = crystal.lattice.model_copy(deep=True)
    atoms = [atom.model_copy(deep=True) for atom in crystal.basis_atoms]
    model_name = crystal.name

    for operation in patch.operations:
        op = operation.operation
        if op == "set_metadata":
            _apply_metadata_patch(spec, operation, diff)
            continue
        if op == "reconcile_dopant_metadata":
            continue
        if op == "make_supercell":
            if operation.matrix is None:
                raise ValueError("make_supercell 需要 matrix")
            nx, ny, nz = operation.matrix
            new_atoms: list[BasisAtomSpec] = []
            for atom in atoms:
                for ix in range(nx):
                    for iy in range(ny):
                        for iz in range(nz):
                            fractional = FractionalVector3(
                                x=(atom.fractional.x + ix) / nx,
                                y=(atom.fractional.y + iy) / ny,
                                z=(atom.fractional.z + iz) / nz,
                            )
                            new_atoms.append(
                                BasisAtomSpec(
                                    id=f"{atom.id}_{ix}{iy}{iz}",
                                    element=atom.element,
                                    fractional=fractional,
                                )
                            )
            atoms = new_atoms
            lattice = lattice.model_copy(update={"a": lattice.a * nx, "b": lattice.b * ny, "c": lattice.c * nz})
            diff.append(f"make_supercell {nx}x{ny}x{nz}")
        elif op == "add_vacuum":
            lattice, atoms = _apply_vacuum_patch(spec, lattice, atoms, operation, mode="add")
            diff.append(f"add_vacuum {operation.axis} {operation.thickness_angstrom}")
        elif op == "set_vacuum":
            lattice, atoms = _apply_vacuum_patch(spec, lattice, atoms, operation, mode="set")
            diff.append(f"set_vacuum {operation.axis} {operation.thickness_angstrom}")
        elif op == "center_slab":
            atoms = _apply_center_slab_patch(spec, lattice, atoms, operation)
            diff.append(f"center_slab {operation.axis or (spec.metadata or {}).get('surface_axis') or 'c'}")
        elif op == "set_gate_stack_thickness":
            atoms = _apply_gate_stack_thickness_patch(spec, lattice, atoms, operation, diff)
        elif op == "set_p_gan_gate_cap_thickness":
            lattice, atoms = _apply_p_gan_gate_cap_thickness_patch(spec, lattice, atoms, operation, diff)
        elif op == "set_quantum_well_thickness":
            lattice, atoms = _apply_quantum_well_thickness_patch(spec, lattice, atoms, operation, diff)
        elif op == "set_interface_gap":
            lattice, atoms = _apply_interface_gap_patch(spec, lattice, atoms, operation, diff)
        elif op == "add_atom":
            if not operation.element or operation.fractional is None:
                raise ValueError("add_atom 对晶体模型需要 element 和 fractional")
            atom_id = operation.id or _next_crystal_atom_id(operation.element, atoms)
            atoms.append(BasisAtomSpec(id=atom_id, element=operation.element, fractional=operation.fractional))
            diff.append(f"add_atom {atom_id}")
        elif op == "substitute_atom":
            atom_id = _target_crystal_atom(operation, atoms, lattice)
            if not operation.new_element:
                raise ValueError("substitute_atom 需要 new_element")
            atoms = [
                atom.model_copy(update={"element": operation.new_element}) if atom.id == atom_id else atom
                for atom in atoms
            ]
            diff.append(f"substitute_atom {atom_id}->{operation.new_element}")
        elif op == "delete_atom":
            atom_id = _target_crystal_atom(operation, atoms, lattice)
            atoms = [atom for atom in atoms if atom.id != atom_id]
            diff.append(f"delete_atom {atom_id}")
        elif op == "set_atom_position":
            atom_id = _target_crystal_atom(operation, atoms, lattice)
            if operation.fractional is None:
                raise ValueError("set_atom_position 对晶体模型需要 fractional")
            atoms = [
                atom.model_copy(update={"fractional": operation.fractional}) if atom.id == atom_id else atom
                for atom in atoms
            ]
            diff.append(f"set_atom_position {atom_id}")
        elif op == "translate_crystal_atoms":
            atoms = _apply_crystal_atom_translation_patch(lattice, atoms, operation, diff)
        elif op == "rotate_crystal_atoms":
            atoms = _apply_crystal_atom_rotation_patch(lattice, atoms, operation, diff)
        elif op == "make_commensurate_twisted_bilayer":
            lattice, atoms, model_name = _apply_commensurate_twisted_bilayer_patch(
                spec,
                lattice,
                atoms,
                operation,
                diff,
                source_model_name=model_name,
            )
        elif op == "make_commensurate_tmd_heterobilayer":
            lattice, atoms, model_name = _apply_commensurate_tmd_heterobilayer_patch(
                spec,
                lattice,
                atoms,
                operation,
                diff,
                source_model_name=model_name,
            )
        elif op == "set_lattice":
            if operation.lattice is None:
                raise ValueError("set_lattice 需要 lattice")
            lattice = operation.lattice
            diff.append("set_lattice")
        elif op == "set_forcite_optimization":
            spec.simulation = _forcite_from_operation(operation)
            diff.append("set_forcite_optimization")
        elif op == "set_castep_energy":
            spec.simulation = _castep_from_operation(operation)
            diff.append("set_castep_energy")
        else:
            raise ValueError(f"{op} 对晶体模型无效")

    spec.model = CrystalSpec(name=model_name, lattice=lattice, basis_atoms=atoms, operations=crystal.operations)


def _apply_crystal_atom_translation_patch(
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
) -> list[BasisAtomSpec]:
    """Rigidly translate an explicit crystal atom set along one lattice vector."""

    atom_ids = operation.atom_ids or []
    axis_key = {"x": "a", "y": "b", "z": "c"}.get(str(operation.axis), str(operation.axis))
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_key)
    if not atom_ids or axis_index is None or operation.distance_angstrom is None:
        raise ValueError("translate_crystal_atoms requires atom_ids, axis, and distance_angstrom")
    axis_length = float(getattr(lattice, axis_key))
    if axis_length <= 0:
        raise ValueError("translate_crystal_atoms requires a positive lattice axis length")

    atom_id_set = set(atom_ids)
    missing_ids = sorted(atom_id_set - {atom.id for atom in atoms})
    if missing_ids:
        raise ValueError("translate_crystal_atoms references missing atom IDs: " + ", ".join(missing_ids))

    distance = float(operation.distance_angstrom)
    delta_fractional = distance / axis_length
    wrapped_count = 0
    translated: list[BasisAtomSpec] = []
    for atom in atoms:
        if atom.id not in atom_id_set:
            translated.append(atom)
            continue
        fractional = [float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z)]
        shifted = fractional[axis_index] + delta_fractional
        if operation.wrap_fractional:
            if shifted < 0.0 or shifted >= 1.0:
                wrapped_count += 1
            shifted %= 1.0
        else:
            if shifted < -1e-12 or shifted > 1.0 + 1e-12:
                raise ValueError("translate_crystal_atoms would move atoms outside the unit cell")
            shifted = min(max(shifted, 0.0), 1.0)
        shifted = round(shifted, 12)
        if operation.wrap_fractional and shifted >= 1.0:
            shifted = 0.0
        fractional[axis_index] = shifted
        translated.append(
            atom.model_copy(
                update={
                    "fractional": FractionalVector3(
                        x=fractional[0],
                        y=fractional[1],
                        z=fractional[2],
                    )
                }
            )
        )

    diff.append(
        f"translate_crystal_atoms {len(atom_ids)} {axis_key} {distance:g}A wrapped {wrapped_count}"
    )
    return translated


def _apply_crystal_atom_rotation_patch(
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
) -> list[BasisAtomSpec]:
    """Rigidly rotate an explicit crystal atom set around one lattice vector."""

    rotated, receipt = rotate_crystal_atom_set(
        lattice,
        atoms,
        atom_ids=operation.atom_ids or [],
        axis=str(operation.axis or ""),
        angle_degrees=float(operation.angle_degrees or 0.0),
        pivot_fractional=operation.pivot_fractional,
        wrap_fractional=operation.wrap_fractional,
    )
    pivot = receipt["pivot_fractional"]
    diff.append(
        "rotate_crystal_atoms "
        f"{receipt['atom_count']} {receipt['axis']} {receipt['angle_degrees']:g}deg "
        f"pivot {pivot[0]:g},{pivot[1]:g},{pivot[2]:g} wrapped {receipt['wrapped_atom_count']}"
    )
    return rotated


def rotate_crystal_atom_set(
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    *,
    atom_ids: list[str],
    axis: str,
    angle_degrees: float,
    pivot_fractional: FractionalVector3 | None = None,
    wrap_fractional: bool = True,
) -> tuple[list[BasisAtomSpec], dict[str, Any]]:
    """Return a periodic-image-aware rigid rotation and a deterministic receipt."""

    axis_key = {"x": "a", "y": "b", "z": "c"}.get(axis, axis)
    axis_index = {"a": 0, "b": 1, "c": 2}.get(axis_key)
    if not atom_ids or axis_index is None:
        raise ValueError("rotate_crystal_atoms requires atom_ids and lattice axis a, b, or c")
    if len(set(atom_ids)) != len(atom_ids):
        raise ValueError("rotate_crystal_atoms atom_ids must contain unique identifiers")
    angle = float(angle_degrees)
    if not math.isfinite(angle) or abs(angle) <= 1e-12 or abs(angle) >= 360.0 - 1e-12:
        raise ValueError("rotate_crystal_atoms angle_degrees must produce a non-identity rotation")

    atom_id_set = set(atom_ids)
    atoms_by_id = {atom.id: atom for atom in atoms}
    missing_ids = sorted(atom_id_set - set(atoms_by_id))
    if missing_ids:
        raise ValueError("rotate_crystal_atoms references missing atom IDs: " + ", ".join(missing_ids))

    selected = [atoms_by_id[atom_id] for atom_id in atom_ids]
    pivot = (
        tuple(float(value) for value in pivot_fractional.as_tuple())
        if pivot_fractional is not None
        else tuple(
            _periodic_fractional_center(
                [
                    (float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z))[index]
                    for atom in selected
                ]
            )
            for index in range(3)
        )
    )
    vectors = _patch_lattice_vectors(lattice)
    axis_vector = vectors[axis_index]
    axis_norm = math.sqrt(_patch_dot(axis_vector, axis_vector))
    if axis_norm <= 1e-12:
        raise ValueError("rotate_crystal_atoms requires a non-degenerate lattice axis")
    unit_axis = tuple(value / axis_norm for value in axis_vector)
    pivot_cartesian = _patch_fractional_to_cartesian(pivot, vectors)
    radians = math.radians(angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)

    wrapped_atom_ids: list[str] = []
    rotated_by_id: dict[str, BasisAtomSpec] = {}
    for atom in selected:
        fractional = (
            float(atom.fractional.x),
            float(atom.fractional.y),
            float(atom.fractional.z),
        )
        unwrapped = tuple(
            pivot[index] + _nearest_periodic_delta(fractional[index] - pivot[index])
            for index in range(3)
        )
        cartesian = _patch_fractional_to_cartesian(unwrapped, vectors)
        relative = tuple(cartesian[index] - pivot_cartesian[index] for index in range(3))
        cross = _patch_cross(unit_axis, relative)
        axial_scale = _patch_dot(unit_axis, relative) * (1.0 - cosine)
        rotated_relative = tuple(
            relative[index] * cosine + cross[index] * sine + unit_axis[index] * axial_scale
            for index in range(3)
        )
        rotated_cartesian = tuple(
            pivot_cartesian[index] + rotated_relative[index]
            for index in range(3)
        )
        raw_fractional = _patch_cartesian_to_fractional(rotated_cartesian, vectors)
        if wrap_fractional:
            if any(value < -1e-10 or value >= 1.0 + 1e-10 for value in raw_fractional):
                wrapped_atom_ids.append(atom.id)
            normalized = tuple(_canonical_fractional(value % 1.0) for value in raw_fractional)
        else:
            if any(value < -1e-10 or value > 1.0 + 1e-10 for value in raw_fractional):
                raise ValueError("rotate_crystal_atoms would move atoms outside the unit cell")
            normalized = tuple(_canonical_fractional(min(max(value, 0.0), 1.0)) for value in raw_fractional)
        rotated_by_id[atom.id] = atom.model_copy(
            update={
                "fractional": FractionalVector3(
                    x=normalized[0],
                    y=normalized[1],
                    z=normalized[2],
                )
            }
        )

    return (
        [rotated_by_id.get(atom.id, atom) for atom in atoms],
        {
            "axis": axis_key,
            "angle_degrees": angle,
            "pivot_fractional": [_round_patch_float(value) for value in pivot],
            "atom_count": len(atom_ids),
            "atom_ids": list(atom_ids),
            "periodic_wrap": bool(wrap_fractional),
            "wrapped_atom_count": len(wrapped_atom_ids),
            "wrapped_atom_ids": sorted(wrapped_atom_ids),
        },
    )


def _periodic_fractional_center(values: list[float]) -> float:
    sine = sum(math.sin(2.0 * math.pi * value) for value in values)
    cosine = sum(math.cos(2.0 * math.pi * value) for value in values)
    if abs(sine) <= 1e-12 and abs(cosine) <= 1e-12:
        return _canonical_fractional(sum(values) / len(values))
    return _canonical_fractional((math.atan2(sine, cosine) / (2.0 * math.pi)) % 1.0)


def _nearest_periodic_delta(value: float) -> float:
    return value - math.floor(value + 0.5)


def _canonical_fractional(value: float) -> float:
    rounded = round(float(value), 12)
    if rounded >= 1.0 or abs(rounded) <= 1e-12:
        return 0.0
    return rounded


def _patch_lattice_vectors(
    lattice: LatticeSpec,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    alpha = math.radians(float(lattice.alpha))
    beta = math.radians(float(lattice.beta))
    gamma = math.radians(float(lattice.gamma))
    sin_gamma = math.sin(gamma)
    if abs(sin_gamma) <= 1e-12:
        raise ValueError("rotate_crystal_atoms requires a non-degenerate lattice gamma angle")
    a_vector = (float(lattice.a), 0.0, 0.0)
    b_vector = (float(lattice.b) * math.cos(gamma), float(lattice.b) * sin_gamma, 0.0)
    c_x = float(lattice.c) * math.cos(beta)
    c_y = float(lattice.c) * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / sin_gamma
    c_z_squared = float(lattice.c) ** 2 - c_x**2 - c_y**2
    if c_z_squared <= 1e-16:
        raise ValueError("rotate_crystal_atoms requires a non-degenerate lattice volume")
    c_vector = (c_x, c_y, math.sqrt(c_z_squared))
    return a_vector, b_vector, c_vector


def _patch_fractional_to_cartesian(
    fractional: tuple[float, float, float],
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float, float]:
    return tuple(
        fractional[0] * vectors[0][index]
        + fractional[1] * vectors[1][index]
        + fractional[2] * vectors[2][index]
        for index in range(3)
    )


def _patch_cartesian_to_fractional(
    cartesian: tuple[float, float, float],
    vectors: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float, float]:
    a_vector, b_vector, c_vector = vectors
    volume = _patch_dot(a_vector, _patch_cross(b_vector, c_vector))
    if abs(volume) <= 1e-12:
        raise ValueError("rotate_crystal_atoms requires a non-degenerate lattice volume")
    return (
        _patch_dot(cartesian, _patch_cross(b_vector, c_vector)) / volume,
        _patch_dot(cartesian, _patch_cross(c_vector, a_vector)) / volume,
        _patch_dot(cartesian, _patch_cross(a_vector, b_vector)) / volume,
    )


def _patch_dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _patch_cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


_COMMENSURATE_TWIST_DEFAULT_MAX_ATOMS = 2_000
_COMMENSURATE_TWIST_MIN_VACUUM_ANGSTROM = 5.0
_COMMENSURATE_TWIST_MIN_INTERLAYER_GAP_ANGSTROM = 1.5
_COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES = 0.1
_COMMENSURATE_HETEROBILAYER_DEFAULT_MAX_STRAIN_PERCENT = 3.0
_TMD_METALS = {"Mo", "W"}
_TMD_CHALCOGENS = {"S", "Se", "Te"}
_TMD_HETEROBILAYER_PROFILES: dict[str, dict[str, float | str]] = {
    "MoS2": {
        "metal": "Mo",
        "chalcogen": "S",
        "lattice_a_angstrom": 3.160,
        "monolayer_thickness_angstrom": 3.1196,
    },
    "WS2": {
        "metal": "W",
        "chalcogen": "S",
        "lattice_a_angstrom": 3.153,
        "monolayer_thickness_angstrom": 3.1196,
    },
    "MoSe2": {
        "metal": "Mo",
        "chalcogen": "Se",
        "lattice_a_angstrom": 3.288,
        "monolayer_thickness_angstrom": 3.3220,
    },
    "WSe2": {
        "metal": "W",
        "chalcogen": "Se",
        "lattice_a_angstrom": 3.282,
        "monolayer_thickness_angstrom": 3.3176,
    },
}


def _apply_commensurate_twisted_bilayer_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
    *,
    source_model_name: str,
) -> tuple[LatticeSpec, list[BasisAtomSpec], str]:
    """Construct an exact integer commensurate homobilayer from a periodic TMD monolayer."""

    if (
        operation.commensurate_m is None
        or operation.commensurate_n is None
        or operation.interlayer_distance_angstrom is None
    ):
        raise ValueError(
            "make_commensurate_twisted_bilayer requires commensurate_m, "
            "commensurate_n, and interlayer_distance_angstrom"
        )
    m = int(operation.commensurate_m)
    n = int(operation.commensurate_n)
    if m <= n or n < 0 or math.gcd(m, n) != 1:
        raise ValueError("commensurate twist indices must be coprime and satisfy m > n >= 0")

    metadata = dict(spec.metadata or {})
    primitive_atoms, primitive_a, source_supercell = _commensurate_tmd_primitive_basis(
        metadata,
        lattice,
        atoms,
    )
    supercell_index = m * m + m * n + n * n
    expected_atom_count = len(primitive_atoms) * 2 * supercell_index
    max_atoms = int(operation.max_atoms or _COMMENSURATE_TWIST_DEFAULT_MAX_ATOMS)
    if expected_atom_count > max_atoms:
        raise ValueError(
            "commensurate twisted bilayer would contain "
            f"{expected_atom_count} atoms, above max_atoms={max_atoms}; "
            "choose smaller coprime indices or explicitly review a larger max_atoms value"
        )

    requested_angle = float(operation.angle_degrees) if operation.angle_degrees is not None else None
    angle_magnitude = commensurate_twist_angle_degrees(m, n)
    orientation = operation.twist_orientation or (
        "clockwise" if requested_angle is not None and requested_angle < 0 else "counterclockwise"
    )
    signed_angle = angle_magnitude if orientation == "counterclockwise" else -angle_magnitude
    if requested_angle is not None:
        requested_orientation = "counterclockwise" if requested_angle > 0 else "clockwise"
        if requested_orientation != orientation:
            raise ValueError("requested twist-angle sign conflicts with twist_orientation")
        angle_error = abs(abs(requested_angle) - angle_magnitude)
        if angle_error > _COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES + 1e-12:
            raise ValueError(
                f"commensurate indices (m={m}, n={n}) produce {angle_magnitude:.9f} degrees, "
                f"which differs from requested {abs(requested_angle):.9f} degrees by "
                f"{angle_error:.9f} degrees; tolerance is "
                f"{_COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES:g} degrees"
            )
    else:
        angle_error = None
    matrix_a = ((m + n, n), (-n, m))
    matrix_b = ((m + n, m), (-m, n))
    if orientation == "counterclockwise":
        bottom_matrix, top_matrix = matrix_b, matrix_a
    else:
        bottom_matrix, top_matrix = matrix_a, matrix_b
    if _integer_matrix_determinant(bottom_matrix) != supercell_index:
        raise ValueError("bottom commensurate matrix determinant does not match the supercell index")
    if _integer_matrix_determinant(top_matrix) != supercell_index:
        raise ValueError("top commensurate matrix determinant does not match the supercell index")

    metal_atoms = [atom for atom in primitive_atoms if atom.element in _TMD_METALS]
    if len(metal_atoms) != 1:
        raise ValueError("commensurate TMD primitive basis must contain exactly one transition-metal atom")
    monolayer_center_fractional = float(metal_atoms[0].fractional.z)
    z_values = [float(atom.fractional.z) for atom in primitive_atoms]
    monolayer_thickness = (max(z_values) - min(z_values)) * float(lattice.c)
    interlayer_distance = float(operation.interlayer_distance_angstrom)
    interlayer_gap = interlayer_distance - monolayer_thickness
    if interlayer_gap < _COMMENSURATE_TWIST_MIN_INTERLAYER_GAP_ANGSTROM - 1e-9:
        raise ValueError(
            "interlayer_distance_angstrom leaves only "
            f"{interlayer_gap:.6f} angstrom between opposing chalcogen planes; "
            f"at least {_COMMENSURATE_TWIST_MIN_INTERLAYER_GAP_ANGSTROM:g} angstrom is required"
        )
    total_slab_thickness = monolayer_thickness + interlayer_distance
    vacuum_angstrom = float(lattice.c) - total_slab_thickness
    if vacuum_angstrom < _COMMENSURATE_TWIST_MIN_VACUUM_ANGSTROM - 1e-9:
        raise ValueError(
            "current c lattice leaves only "
            f"{vacuum_angstrom:.6f} angstrom vacuum around the twisted bilayer; "
            f"at least {_COMMENSURATE_TWIST_MIN_VACUUM_ANGSTROM:g} angstrom is required"
        )

    bottom_center_fractional = 0.5 - interlayer_distance / (2.0 * float(lattice.c))
    top_center_fractional = 0.5 + interlayer_distance / (2.0 * float(lattice.c))
    bottom_atoms = _commensurate_layer_atoms(
        primitive_atoms,
        bottom_matrix,
        layer_number=1,
        layer_center_fractional=bottom_center_fractional,
        source_center_fractional=monolayer_center_fractional,
    )
    top_atoms = _commensurate_layer_atoms(
        primitive_atoms,
        top_matrix,
        layer_number=2,
        layer_center_fractional=top_center_fractional,
        source_center_fractional=monolayer_center_fractional,
    )
    twisted_atoms = bottom_atoms + top_atoms
    if len(twisted_atoms) != expected_atom_count:
        raise ValueError(
            f"commensurate construction generated {len(twisted_atoms)} atoms; "
            f"expected {expected_atom_count}"
        )

    common_length = primitive_a * math.sqrt(supercell_index)
    twisted_lattice = LatticeSpec(
        a=common_length,
        b=common_length,
        c=float(lattice.c),
        alpha=90.0,
        beta=90.0,
        gamma=120.0,
    )
    structure_sha256 = _crystal_structure_sha256(twisted_lattice, twisted_atoms)
    bottom_ids = [atom.id for atom in bottom_atoms]
    top_ids = [atom.id for atom in top_atoms]
    receipt = {
        "source": "semantic_patch_make_commensurate_twisted_bilayer",
        "source_revision": int(spec.revision),
        "source_model_name": source_model_name,
        "source_template_supercell": list(source_supercell),
        "primitive_basis_atom_count": len(primitive_atoms),
        "primitive_lattice_a_angstrom": _round_patch_float(primitive_a),
        "commensurate_m": m,
        "commensurate_n": n,
        "supercell_index": supercell_index,
        "bottom_supercell_matrix": [list(row) for row in bottom_matrix],
        "top_supercell_matrix": [list(row) for row in top_matrix],
        "matrix_determinant_verified": True,
        "twist_orientation": orientation,
        "twist_angle_degrees": _round_patch_float(signed_angle),
        "twist_angle_magnitude_degrees": _round_patch_float(angle_magnitude),
        "requested_twist_angle_degrees": (
            _round_patch_float(requested_angle) if requested_angle is not None else None
        ),
        "twist_angle_error_degrees": (
            _round_patch_float(angle_error) if angle_error is not None else None
        ),
        "twist_angle_tolerance_degrees": _COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES,
        "twist_angle_formula": "acos((m^2+4mn+n^2)/(2(m^2+mn+n^2)))",
        "commensurability_verified": True,
        "common_lattice_a_angstrom": _round_patch_float(common_length),
        "common_lattice_b_angstrom": _round_patch_float(common_length),
        "common_lattice_gamma_degrees": 120.0,
        "interlayer_distance_angstrom": _round_patch_float(interlayer_distance),
        "monolayer_thickness_angstrom": _round_patch_float(monolayer_thickness),
        "interlayer_chalcogen_gap_angstrom": _round_patch_float(interlayer_gap),
        "total_slab_thickness_angstrom": _round_patch_float(total_slab_thickness),
        "vacuum_angstrom": _round_patch_float(vacuum_angstrom),
        "atom_count": len(twisted_atoms),
        "atoms_per_layer": len(bottom_atoms),
        "bottom_layer_atom_id_sha256": _atom_id_list_sha256(bottom_ids),
        "top_layer_atom_id_sha256": _atom_id_list_sha256(top_ids),
        "structure_sha256": structure_sha256,
        "pre_relaxation_scaffold": True,
        "visual_review_only": False,
        "visual_hotload_ready": True,
        "requires_geometry_relaxation": True,
        "geometry_relaxed": False,
        "calculation_ready": False,
        "calculation_blocking_reason": "commensurate_twisted_bilayer_requires_geometry_relaxation",
    }
    history = [
        dict(item)
        for item in metadata.get("commensurate_twist_history", []) or []
        if isinstance(item, dict)
    ]
    history.append(receipt)
    metadata.update(
        {
            "structure_family": "commensurate twisted 2d tmd bilayer",
            "monolayer_polytype": metadata.get("tmd_phase"),
            "bilayer_stacking_family": "twisted_R_type_from_same_orientation_monolayers",
            "surface_axis": "c",
            "layer_count": 2,
            "twisted_bilayer": True,
            "commensurate_twisted_bilayer": True,
            "template_supercell": [1, 1, 1],
            "slab_thickness_angstrom": _round_patch_float(total_slab_thickness),
            "vacuum_angstrom": _round_patch_float(vacuum_angstrom),
            "commensurate_twist_history": history,
            "last_commensurate_twist": receipt,
        }
    )
    spec.metadata = metadata
    acceptance_note = (
        "Exact integer commensurate TMD twisted-bilayer pre-relaxation structure; "
        "geometry relaxation is required before production calculation."
    )
    acceptance_notes = [
        note
        for note in spec.acceptance.notes
        if "monolayer template" not in note.lower()
        and "commensurate tmd twisted-bilayer" not in note.lower()
    ]
    acceptance_notes.append(acceptance_note)
    spec.acceptance = spec.acceptance.model_copy(update={"notes": acceptance_notes})
    diff.append(
        "make_commensurate_twisted_bilayer "
        f"m={m} n={n} angle={signed_angle:.9f}deg atoms={len(twisted_atoms)} "
        f"interlayer={interlayer_distance:g}A"
    )
    suffix = f"_commensurate_twisted_bilayer_m{m}_n{n}"
    model_name = (source_model_name + suffix)[:120]
    return twisted_lattice, twisted_atoms, model_name


def _apply_commensurate_tmd_heterobilayer_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
    *,
    source_model_name: str,
) -> tuple[LatticeSpec, list[BasisAtomSpec], str]:
    """Construct a strain-controlled periodic TMD heterobilayer coincidence cell."""

    if (
        operation.commensurate_m is None
        or operation.commensurate_n is None
        or operation.interlayer_distance_angstrom is None
        or operation.top_layer_material is None
    ):
        raise ValueError(
            "make_commensurate_tmd_heterobilayer requires commensurate_m, "
            "commensurate_n, interlayer_distance_angstrom, and top_layer_material"
        )
    m = int(operation.commensurate_m)
    n = int(operation.commensurate_n)
    if m <= n or n < 0 or math.gcd(m, n) != 1:
        raise ValueError("commensurate heterobilayer indices must be coprime and satisfy m > n >= 0")

    metadata = dict(spec.metadata or {})
    bottom_basis, bottom_primitive_a, source_supercell = _commensurate_tmd_primitive_basis(
        metadata,
        lattice,
        atoms,
    )
    bottom_material = _tmd_material_from_primitive_basis(bottom_basis)
    recorded_bottom_material = _canonical_supported_tmd_material(metadata.get("material"))
    if recorded_bottom_material is not None and recorded_bottom_material != bottom_material:
        raise ValueError(
            "current TMD atom table does not match metadata material: "
            f"atoms={bottom_material}, metadata={recorded_bottom_material}"
        )
    top_material = _canonical_supported_tmd_material(operation.top_layer_material)
    if top_material is None:
        raise ValueError(
            "top_layer_material must be one of "
            + ", ".join(sorted(_TMD_HETEROBILAYER_PROFILES))
        )
    if top_material == bottom_material:
        raise ValueError(
            "make_commensurate_tmd_heterobilayer requires two different TMD materials; "
            "use make_commensurate_twisted_bilayer for a homobilayer"
        )

    top_profile = _TMD_HETEROBILAYER_PROFILES[top_material]
    top_primitive_a = float(top_profile["lattice_a_angstrom"])
    bottom_profile = _TMD_HETEROBILAYER_PROFILES[bottom_material]
    reference_bottom_a = float(bottom_profile["lattice_a_angstrom"])
    if abs(bottom_primitive_a - reference_bottom_a) > max(reference_bottom_a * 0.03, 1e-6):
        raise ValueError(
            f"current {bottom_material} primitive lattice a={bottom_primitive_a:.6f} angstrom "
            f"differs by more than 3% from the reviewed reference {reference_bottom_a:.6f}; "
            "start from a pristine reviewed TMD monolayer or explicitly restore its lattice"
        )

    strain_policy = operation.strain_policy or "balanced"
    if strain_policy == "balanced":
        common_primitive_a = (
            2.0 * bottom_primitive_a * top_primitive_a
            / (bottom_primitive_a + top_primitive_a)
        )
    elif strain_policy == "bottom_fixed":
        common_primitive_a = bottom_primitive_a
    elif strain_policy == "top_fixed":
        common_primitive_a = top_primitive_a
    else:  # pragma: no cover - Pydantic rejects this path
        raise ValueError(f"unsupported heterobilayer strain_policy: {strain_policy}")
    bottom_strain_percent = 100.0 * (common_primitive_a / bottom_primitive_a - 1.0)
    top_strain_percent = 100.0 * (common_primitive_a / top_primitive_a - 1.0)
    max_abs_strain_percent = max(abs(bottom_strain_percent), abs(top_strain_percent))
    strain_limit_percent = float(
        operation.max_strain_percent
        or _COMMENSURATE_HETEROBILAYER_DEFAULT_MAX_STRAIN_PERCENT
    )
    if max_abs_strain_percent > strain_limit_percent + 1e-12:
        raise ValueError(
            f"{bottom_material}/{top_material} with strain_policy={strain_policy} requires "
            f"{max_abs_strain_percent:.6f}% maximum biaxial strain, above "
            f"max_strain_percent={strain_limit_percent:g}; choose balanced strain, "
            "increase the limit only after review, or provide a larger domain-matched ModelSpec"
        )

    supercell_index = m * m + m * n + n * n
    expected_atom_count = 6 * supercell_index
    max_atoms = int(operation.max_atoms or _COMMENSURATE_TWIST_DEFAULT_MAX_ATOMS)
    if expected_atom_count > max_atoms:
        raise ValueError(
            "commensurate TMD heterobilayer would contain "
            f"{expected_atom_count} atoms, above max_atoms={max_atoms}; "
            "choose smaller coprime indices or explicitly review a larger max_atoms value"
        )

    requested_angle = float(operation.angle_degrees) if operation.angle_degrees is not None else None
    angle_magnitude = commensurate_twist_angle_degrees(m, n)
    orientation = operation.twist_orientation or (
        "clockwise" if requested_angle is not None and requested_angle < 0 else "counterclockwise"
    )
    signed_angle = angle_magnitude if orientation == "counterclockwise" else -angle_magnitude
    if requested_angle is not None:
        requested_orientation = "counterclockwise" if requested_angle > 0 else "clockwise"
        if requested_orientation != orientation:
            raise ValueError("requested heterobilayer twist-angle sign conflicts with twist_orientation")
        angle_error = abs(abs(requested_angle) - angle_magnitude)
        if angle_error > _COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES + 1e-12:
            raise ValueError(
                f"commensurate indices (m={m}, n={n}) produce {angle_magnitude:.9f} degrees, "
                f"which differs from requested {abs(requested_angle):.9f} degrees by "
                f"{angle_error:.9f} degrees; tolerance is "
                f"{_COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES:g} degrees"
            )
    else:
        angle_error = None

    matrix_a = ((m + n, n), (-n, m))
    matrix_b = ((m + n, m), (-m, n))
    if orientation == "counterclockwise":
        bottom_matrix, top_matrix = matrix_b, matrix_a
    else:
        bottom_matrix, top_matrix = matrix_a, matrix_b
    if _integer_matrix_determinant(bottom_matrix) != supercell_index:
        raise ValueError("bottom heterobilayer matrix determinant does not match the supercell index")
    if _integer_matrix_determinant(top_matrix) != supercell_index:
        raise ValueError("top heterobilayer matrix determinant does not match the supercell index")

    bottom_metals = [atom for atom in bottom_basis if atom.element in _TMD_METALS]
    if len(bottom_metals) != 1:
        raise ValueError("commensurate TMD primitive basis must contain exactly one transition-metal atom")
    source_center_fractional = float(bottom_metals[0].fractional.z)
    bottom_z_values = [float(atom.fractional.z) for atom in bottom_basis]
    bottom_thickness = (max(bottom_z_values) - min(bottom_z_values)) * float(lattice.c)
    top_thickness = float(top_profile["monolayer_thickness_angstrom"])
    top_basis = _heterobilayer_top_primitive_basis(
        bottom_basis,
        top_material=top_material,
        source_center_fractional=source_center_fractional,
        c_length=float(lattice.c),
        thickness_angstrom=top_thickness,
    )

    interlayer_distance = float(operation.interlayer_distance_angstrom)
    opposing_half_thickness = 0.5 * (bottom_thickness + top_thickness)
    interlayer_gap = interlayer_distance - opposing_half_thickness
    if interlayer_gap < _COMMENSURATE_TWIST_MIN_INTERLAYER_GAP_ANGSTROM - 1e-9:
        raise ValueError(
            "interlayer_distance_angstrom leaves only "
            f"{interlayer_gap:.6f} angstrom between opposing chalcogen planes; "
            f"at least {_COMMENSURATE_TWIST_MIN_INTERLAYER_GAP_ANGSTROM:g} angstrom is required"
        )
    total_slab_thickness = interlayer_distance + opposing_half_thickness
    vacuum_angstrom = float(lattice.c) - total_slab_thickness
    if vacuum_angstrom < _COMMENSURATE_TWIST_MIN_VACUUM_ANGSTROM - 1e-9:
        raise ValueError(
            "current c lattice leaves only "
            f"{vacuum_angstrom:.6f} angstrom vacuum around the TMD heterobilayer; "
            f"at least {_COMMENSURATE_TWIST_MIN_VACUUM_ANGSTROM:g} angstrom is required"
        )

    bottom_center_fractional = 0.5 - interlayer_distance / (2.0 * float(lattice.c))
    top_center_fractional = 0.5 + interlayer_distance / (2.0 * float(lattice.c))
    bottom_atoms = _commensurate_layer_atoms(
        bottom_basis,
        bottom_matrix,
        layer_number=1,
        layer_center_fractional=bottom_center_fractional,
        source_center_fractional=source_center_fractional,
    )
    top_atoms = _commensurate_layer_atoms(
        top_basis,
        top_matrix,
        layer_number=2,
        layer_center_fractional=top_center_fractional,
        source_center_fractional=source_center_fractional,
    )
    heterobilayer_atoms = bottom_atoms + top_atoms
    if len(heterobilayer_atoms) != expected_atom_count:
        raise ValueError(
            f"commensurate heterobilayer construction generated {len(heterobilayer_atoms)} atoms; "
            f"expected {expected_atom_count}"
        )

    common_length = common_primitive_a * math.sqrt(supercell_index)
    heterobilayer_lattice = LatticeSpec(
        a=common_length,
        b=common_length,
        c=float(lattice.c),
        alpha=90.0,
        beta=90.0,
        gamma=120.0,
    )
    structure_sha256 = _crystal_structure_sha256(heterobilayer_lattice, heterobilayer_atoms)
    bottom_ids = [atom.id for atom in bottom_atoms]
    top_ids = [atom.id for atom in top_atoms]
    lattice_mismatch_percent = 100.0 * (top_primitive_a / bottom_primitive_a - 1.0)
    receipt = {
        "source": "semantic_patch_make_commensurate_tmd_heterobilayer",
        "source_revision": int(spec.revision),
        "source_model_name": source_model_name,
        "source_template_supercell": list(source_supercell),
        "bottom_material": bottom_material,
        "top_material": top_material,
        "bottom_primitive_lattice_a_angstrom": _round_patch_float(bottom_primitive_a),
        "top_primitive_lattice_a_angstrom": _round_patch_float(top_primitive_a),
        "unstrained_lattice_mismatch_percent": _round_patch_float(lattice_mismatch_percent),
        "strain_policy": strain_policy,
        "common_primitive_lattice_a_angstrom": _round_patch_float(common_primitive_a),
        "bottom_biaxial_strain_percent": _round_patch_float(bottom_strain_percent),
        "top_biaxial_strain_percent": _round_patch_float(top_strain_percent),
        "max_abs_biaxial_strain_percent": _round_patch_float(max_abs_strain_percent),
        "max_strain_percent": _round_patch_float(strain_limit_percent),
        "strain_within_limit": True,
        "commensurate_m": m,
        "commensurate_n": n,
        "supercell_index": supercell_index,
        "bottom_supercell_matrix": [list(row) for row in bottom_matrix],
        "top_supercell_matrix": [list(row) for row in top_matrix],
        "matrix_determinant_verified": True,
        "twist_orientation": orientation,
        "twist_angle_degrees": _round_patch_float(signed_angle),
        "twist_angle_magnitude_degrees": _round_patch_float(angle_magnitude),
        "requested_twist_angle_degrees": (
            _round_patch_float(requested_angle) if requested_angle is not None else None
        ),
        "twist_angle_error_degrees": (
            _round_patch_float(angle_error) if angle_error is not None else None
        ),
        "twist_angle_tolerance_degrees": _COMMENSURATE_TWIST_ANGLE_TOLERANCE_DEGREES,
        "twist_angle_formula": "acos((m^2+4mn+n^2)/(2(m^2+mn+n^2)))",
        "commensurability_model": "exact_integer_coincidence_after_explicit_biaxial_strain",
        "commensurability_verified": True,
        "common_lattice_a_angstrom": _round_patch_float(common_length),
        "common_lattice_b_angstrom": _round_patch_float(common_length),
        "common_lattice_gamma_degrees": 120.0,
        "interlayer_distance_angstrom": _round_patch_float(interlayer_distance),
        "bottom_monolayer_thickness_angstrom": _round_patch_float(bottom_thickness),
        "top_monolayer_thickness_angstrom": _round_patch_float(top_thickness),
        "interlayer_chalcogen_gap_angstrom": _round_patch_float(interlayer_gap),
        "total_slab_thickness_angstrom": _round_patch_float(total_slab_thickness),
        "vacuum_angstrom": _round_patch_float(vacuum_angstrom),
        "atom_count": len(heterobilayer_atoms),
        "atoms_per_layer": len(bottom_atoms),
        "bottom_layer_atom_count": len(bottom_atoms),
        "top_layer_atom_count": len(top_atoms),
        "bottom_layer_atom_id_sha256": _atom_id_list_sha256(bottom_ids),
        "top_layer_atom_id_sha256": _atom_id_list_sha256(top_ids),
        "structure_sha256": structure_sha256,
        "pre_relaxation_scaffold": True,
        "visual_review_only": False,
        "visual_hotload_ready": True,
        "requires_geometry_relaxation": True,
        "geometry_relaxed": False,
        "calculation_ready": False,
        "calculation_blocking_reason": "commensurate_tmd_heterobilayer_requires_geometry_relaxation",
    }
    history = [
        dict(item)
        for item in metadata.get("commensurate_heterobilayer_history", []) or []
        if isinstance(item, dict)
    ]
    history.append(receipt)
    bottom_key = re.sub(r"[^a-z0-9]+", "", bottom_material.lower())
    top_key = re.sub(r"[^a-z0-9]+", "", top_material.lower())
    metadata.update(
        {
            "structure_family": "commensurate twisted 2d tmd heterobilayer",
            "material": f"{bottom_material}/{top_material}",
            "materials": [bottom_material, top_material],
            "substrate": bottom_material,
            "interface": f"{bottom_material}/{top_material} van der Waals heterointerface",
            "heterostructure": True,
            "interface_orientation": "(0001)/(0001)",
            "interface_axis": "c",
            "surface_axis": "c",
            "layer_count": 2,
            "twisted_bilayer": True,
            "commensurate_tmd_heterobilayer": True,
            "template_supercell": [1, 1, 1],
            "in_plane_lattice_angstrom": _round_patch_float(common_primitive_a),
            f"{bottom_key}_reference_lattice_angstrom": _round_patch_float(bottom_primitive_a),
            f"{top_key}_reference_lattice_angstrom": _round_patch_float(top_primitive_a),
            "coherent_strain_model": f"{strain_policy}_biaxial_tmd_commensurate_cell",
            "slab_thickness_angstrom": _round_patch_float(total_slab_thickness),
            "vacuum_angstrom": _round_patch_float(vacuum_angstrom),
            "pre_relaxation_scaffold": True,
            "unrelaxed_interface": True,
            "requires_geometry_relaxation": True,
            "commensurate_heterobilayer_history": history,
            "last_commensurate_heterobilayer": receipt,
        }
    )
    spec.metadata = metadata
    acceptance_note = (
        "Exact integer coincidence TMD heterobilayer with explicit biaxial strain; "
        "geometry relaxation and strain review are required before production calculation."
    )
    acceptance_notes = [
        note
        for note in spec.acceptance.notes
        if "monolayer template" not in note.lower()
        and "commensurate tmd heterobilayer" not in note.lower()
        and "2d monolayer template" not in note.lower()
    ]
    acceptance_notes.append(acceptance_note)
    spec.acceptance = spec.acceptance.model_copy(update={"notes": acceptance_notes})
    diff.append(
        "make_commensurate_tmd_heterobilayer "
        f"bottom={bottom_material} top={top_material} m={m} n={n} "
        f"angle={signed_angle:.9f}deg strain_policy={strain_policy} "
        f"max_strain={max_abs_strain_percent:.6f}% atoms={len(heterobilayer_atoms)} "
        f"interlayer={interlayer_distance:g}A"
    )
    suffix = f"_{bottom_material}_{top_material}_commensurate_m{m}_n{n}"
    model_name = (source_model_name + suffix)[:120]
    return heterobilayer_lattice, heterobilayer_atoms, model_name


def _canonical_supported_tmd_material(value: Any) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    for material in _TMD_HETEROBILAYER_PROFILES:
        if normalized == re.sub(r"[^a-z0-9]+", "", material.lower()):
            return material
    return None


def _tmd_material_from_primitive_basis(primitive_atoms: list[BasisAtomSpec]) -> str:
    metals = sorted({atom.element for atom in primitive_atoms if atom.element in _TMD_METALS})
    chalcogens = sorted({atom.element for atom in primitive_atoms if atom.element in _TMD_CHALCOGENS})
    if len(metals) != 1 or len(chalcogens) != 1:
        raise ValueError("TMD primitive basis must contain one metal species and one chalcogen species")
    material = f"{metals[0]}{chalcogens[0]}2"
    if material not in _TMD_HETEROBILAYER_PROFILES:
        raise ValueError(
            "commensurate TMD heterobilayer currently supports pristine MoS2, WS2, MoSe2, and WSe2"
        )
    return material


def _heterobilayer_top_primitive_basis(
    bottom_basis: list[BasisAtomSpec],
    *,
    top_material: str,
    source_center_fractional: float,
    c_length: float,
    thickness_angstrom: float,
) -> list[BasisAtomSpec]:
    profile = _TMD_HETEROBILAYER_PROFILES[top_material]
    metal = str(profile["metal"])
    chalcogen = str(profile["chalcogen"])
    half_fractional_thickness = thickness_angstrom / (2.0 * c_length)
    top_basis: list[BasisAtomSpec] = []
    for atom in bottom_basis:
        if atom.element in _TMD_METALS:
            element = metal
            atom_id = f"{metal}1"
            z = source_center_fractional
        elif atom.element in _TMD_CHALCOGENS:
            element = chalcogen
            above = float(atom.fractional.z) > source_center_fractional
            atom_id = f"{chalcogen}{'top' if above else 'bot'}1"
            z = source_center_fractional + (half_fractional_thickness if above else -half_fractional_thickness)
        else:  # pragma: no cover - source validation rejects this path
            raise ValueError(f"unsupported atom {atom.id} in TMD primitive basis")
        top_basis.append(
            BasisAtomSpec(
                id=atom_id,
                element=element,
                fractional=FractionalVector3(
                    x=float(atom.fractional.x),
                    y=float(atom.fractional.y),
                    z=_canonical_fractional(z),
                ),
            )
        )
    if len({atom.id for atom in top_basis}) != len(top_basis):
        raise ValueError("generated TMD heterobilayer top primitive basis has duplicate atom IDs")
    return top_basis


def _commensurate_tmd_primitive_basis(
    metadata: dict[str, Any],
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
) -> tuple[list[BasisAtomSpec], float, tuple[int, int, int]]:
    family = str(metadata.get("structure_family") or "").lower()
    if "tmd" not in family or "monolayer" not in family:
        raise ValueError("commensurate twisted bilayer requires a periodic 2D TMD monolayer template")
    if str(metadata.get("surface_axis") or "c").lower() not in {"c", "z"}:
        raise ValueError("commensurate twisted bilayer currently requires surface_axis=c")
    if abs(float(lattice.alpha) - 90.0) > 1e-6 or abs(float(lattice.beta) - 90.0) > 1e-6:
        raise ValueError("commensurate twisted bilayer requires alpha=beta=90 degrees")
    if abs(float(lattice.gamma) - 120.0) > 1e-6:
        raise ValueError("commensurate twisted bilayer currently requires a 120-degree hexagonal cell")

    raw_supercell = metadata.get("template_supercell")
    if not isinstance(raw_supercell, (list, tuple)) or len(raw_supercell) != 3:
        raise ValueError("commensurate twisted bilayer requires template_supercell metadata")
    try:
        nx, ny, nz = (int(value) for value in raw_supercell)
    except (TypeError, ValueError) as exc:
        raise ValueError("template_supercell must contain three integers") from exc
    if nx <= 0 or ny <= 0 or nz != 1:
        raise ValueError("commensurate twisted bilayer requires a positive in-plane template supercell and nz=1")
    primitive_a = float(lattice.a) / nx
    primitive_b = float(lattice.b) / ny
    if abs(primitive_a - primitive_b) > max(primitive_a, primitive_b) * 1e-6:
        raise ValueError("commensurate twisted bilayer requires equal primitive in-plane lattice lengths")

    signatures: dict[tuple[str, float, float, float], list[BasisAtomSpec]] = {}
    for atom in atoms:
        px = _canonical_fractional((float(atom.fractional.x) * nx) % 1.0)
        py = _canonical_fractional((float(atom.fractional.y) * ny) % 1.0)
        pz = _canonical_fractional(float(atom.fractional.z))
        key = (atom.element, round(px, 6), round(py, 6), round(pz, 6))
        signatures.setdefault(key, []).append(atom)
    repeat_count = nx * ny
    inconsistent = [key for key, values in signatures.items() if len(values) != repeat_count]
    if inconsistent:
        raise ValueError(
            "current monolayer is not a complete periodic repetition of one primitive basis; "
            "remove defects, dopants, alloy edits, or prior coordinate changes before constructing the twist cell"
        )
    if len(atoms) != len(signatures) * repeat_count:
        raise ValueError("current monolayer atom count does not match template_supercell periodicity")

    ordered = sorted(signatures, key=lambda item: (item[0] not in _TMD_METALS, item[0], item[3], item[1], item[2]))
    primitive_atoms: list[BasisAtomSpec] = []
    element_counters: dict[str, int] = {}
    for element, x, y, z in ordered:
        element_counters[element] = element_counters.get(element, 0) + 1
        primitive_atoms.append(
            BasisAtomSpec(
                id=f"{element}{element_counters[element]}",
                element=element,
                fractional=FractionalVector3(x=x, y=y, z=z),
            )
        )
    metal_atoms = [atom for atom in primitive_atoms if atom.element in _TMD_METALS]
    chalcogen_atoms = [atom for atom in primitive_atoms if atom.element in _TMD_CHALCOGENS]
    other_atoms = [
        atom
        for atom in primitive_atoms
        if atom.element not in _TMD_METALS and atom.element not in _TMD_CHALCOGENS
    ]
    if len(primitive_atoms) != 3 or len(metal_atoms) != 1 or len(chalcogen_atoms) != 2 or other_atoms:
        raise ValueError(
            "commensurate twisted bilayer currently supports MX2 TMD primitive cells "
            "with one Mo/W atom and two S/Se/Te atoms"
        )
    if len({atom.element for atom in metal_atoms}) != 1 or len({atom.element for atom in chalcogen_atoms}) != 1:
        raise ValueError("commensurate twisted bilayer currently supports a pristine TMD homobilayer")
    return primitive_atoms, (primitive_a + primitive_b) / 2.0, (nx, ny, nz)


def commensurate_twist_angle_degrees(m: int, n: int) -> float:
    """Return the exact homobilayer twist angle for coprime hexagonal indices."""

    if m <= n or n < 0 or math.gcd(m, n) != 1:
        raise ValueError("commensurate twist indices must be coprime and satisfy m > n >= 0")
    index = m * m + m * n + n * n
    cosine = (m * m + 4 * m * n + n * n) / (2.0 * index)
    return math.degrees(math.acos(min(1.0, max(-1.0, cosine))))


def _integer_matrix_determinant(matrix: tuple[tuple[int, int], tuple[int, int]]) -> int:
    return matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]


def _commensurate_layer_atoms(
    primitive_atoms: list[BasisAtomSpec],
    matrix: tuple[tuple[int, int], tuple[int, int]],
    *,
    layer_number: int,
    layer_center_fractional: float,
    source_center_fractional: float,
) -> list[BasisAtomSpec]:
    generated: list[BasisAtomSpec] = []
    for primitive_atom in primitive_atoms:
        positions = _integer_supercell_fractional_positions(
            (float(primitive_atom.fractional.x), float(primitive_atom.fractional.y)),
            matrix,
        )
        z = layer_center_fractional + float(primitive_atom.fractional.z) - source_center_fractional
        if z < -1e-12 or z > 1.0 + 1e-12:
            raise ValueError("twisted bilayer atom would fall outside the c-axis unit cell")
        z = _canonical_fractional(min(max(z, 0.0), 1.0))
        for index, (x, y) in enumerate(positions):
            generated.append(
                BasisAtomSpec(
                    id=f"{primitive_atom.id}_L{layer_number}_{index:04d}",
                    element=primitive_atom.element,
                    fractional=FractionalVector3(x=x, y=y, z=z),
                )
            )
    return generated


def _integer_supercell_fractional_positions(
    primitive_fractional: tuple[float, float],
    matrix: tuple[tuple[int, int], tuple[int, int]],
) -> list[tuple[float, float]]:
    determinant = _integer_matrix_determinant(matrix)
    if determinant <= 0:
        raise ValueError("commensurate supercell matrix must have a positive determinant")
    a, b = matrix[0]
    c, d = matrix[1]
    corners = ((0, 0), (a, b), (c, d), (a + c, b + d))
    px, py = primitive_fractional
    min_x = math.floor(min(item[0] for item in corners) - px) - 1
    max_x = math.ceil(max(item[0] for item in corners) - px) + 1
    min_y = math.floor(min(item[1] for item in corners) - py) - 1
    max_y = math.ceil(max(item[1] for item in corners) - py) + 1
    positions: set[tuple[float, float]] = set()
    tolerance = 1e-10
    for ix in range(min_x, max_x + 1):
        for iy in range(min_y, max_y + 1):
            qx = px + ix
            qy = py + iy
            fx = (qx * d - qy * c) / determinant
            fy = (-qx * b + qy * a) / determinant
            if -tolerance <= fx < 1.0 - tolerance and -tolerance <= fy < 1.0 - tolerance:
                positions.add((_canonical_fractional(fx % 1.0), _canonical_fractional(fy % 1.0)))
    if len(positions) != determinant:
        raise ValueError(
            f"commensurate matrix generated {len(positions)} primitive translations; "
            f"expected determinant {determinant}"
        )
    return sorted(positions, key=lambda value: (value[0], value[1]))


def _atom_id_list_sha256(atom_ids: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(atom_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _crystal_structure_sha256(lattice: LatticeSpec, atoms: list[BasisAtomSpec]) -> str:
    payload = {
        "lattice": {
            key: round(float(getattr(lattice, key)), 12)
            for key in ("a", "b", "c", "alpha", "beta", "gamma")
        },
        "atoms": [
            {
                "id": atom.id,
                "element": atom.element,
                "fractional": [
                    round(float(atom.fractional.x), 12),
                    round(float(atom.fractional.y), 12),
                    round(float(atom.fractional.z), 12),
                ],
            }
            for atom in sorted(atoms, key=lambda item: item.id)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reconcile_crystal_dopant_metadata(spec: ModelSpec, diff: list[str]) -> dict[str, int | bool]:
    """Keep current-state dopant metadata aligned with the crystal atom table."""

    if not isinstance(spec.model, CrystalSpec):
        return {
            "raw_site_count": 0,
            "current_site_count": 0,
            "removed_count": 0,
            "expanded_count": 0,
            "changed": False,
        }
    metadata = dict(spec.metadata or {})
    raw_entries = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", []) or []
        if isinstance(item, dict)
    ]
    latest = metadata.get("last_semiconductor_dopant_site")
    if isinstance(latest, dict) and latest not in raw_entries:
        raw_entries.append(dict(latest))
    if not raw_entries:
        return {
            "raw_site_count": 0,
            "current_site_count": 0,
            "removed_count": 0,
            "expanded_count": 0,
            "changed": False,
        }

    atoms_by_id = {atom.id: atom for atom in spec.model.basis_atoms}
    atom_order = {atom.id: index for index, atom in enumerate(spec.model.basis_atoms)}
    reconciled: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    removed_count = 0
    expanded_count = 0
    removed_record_atom_ids: set[str] = set()
    changed = False

    for raw in raw_entries:
        atom_id = str(raw.get("atom_id") or raw.get("site_id") or "").strip()
        dopant_element = str(raw.get("dopant_element") or raw.get("new_element") or "").strip()
        if not atom_id or not dopant_element:
            removed_count += 1
            if atom_id:
                removed_record_atom_ids.add(atom_id)
            changed = True
            continue
        candidate_ids = [
            candidate_id
            for candidate_id, atom in atoms_by_id.items()
            if (candidate_id == atom_id or candidate_id.startswith(f"{atom_id}_"))
            and atom.element == dopant_element
        ]
        candidate_ids.sort(key=lambda candidate_id: atom_order[candidate_id])
        if not candidate_ids:
            removed_count += 1
            removed_record_atom_ids.add(atom_id)
            changed = True
            continue
        if candidate_ids != [atom_id]:
            changed = True
            expanded_count += max(0, len(candidate_ids) - 1)
        for candidate_id in candidate_ids:
            key = (candidate_id, dopant_element)
            if key in seen:
                changed = True
                continue
            seen.add(key)
            atom = atoms_by_id[candidate_id]
            entry = {
                **raw,
                "site_id": candidate_id,
                "atom_id": candidate_id,
                "dopant_element": dopant_element,
                "new_element": dopant_element,
                "fractional": [
                    _round_patch_float(atom.fractional.x),
                    _round_patch_float(atom.fractional.y),
                    _round_patch_float(atom.fractional.z),
                ],
            }
            reconciled.append(entry)

    if reconciled != raw_entries:
        changed = True
    if not changed:
        return {
            "raw_site_count": len(raw_entries),
            "current_site_count": len(reconciled),
            "removed_count": removed_count,
            "expanded_count": expanded_count,
            "changed": False,
        }
    if reconciled:
        metadata["semiconductor_dopant_sites"] = reconciled
        metadata["last_semiconductor_dopant_site"] = reconciled[-1]
    else:
        metadata.pop("semiconductor_dopant_sites", None)
        metadata.pop("last_semiconductor_dopant_site", None)

    if removed_record_atom_ids and isinstance(metadata.get("nl_auto_selected_sites"), list):
        filtered_auto_sites = []
        for item in metadata.get("nl_auto_selected_sites") or []:
            if not isinstance(item, dict):
                filtered_auto_sites.append(item)
                continue
            operation = str(item.get("operation") or "").lower()
            item_atom_id = str(item.get("atom_id") or item.get("site_id") or "")
            if item_atom_id in removed_record_atom_ids and ("dop" in operation or "substitut" in operation):
                continue
            filtered_auto_sites.append(item)
        if filtered_auto_sites:
            metadata["nl_auto_selected_sites"] = filtered_auto_sites
        else:
            metadata.pop("nl_auto_selected_sites", None)

    spec.metadata = metadata
    diff.append(
        "reconcile_metadata semiconductor_dopant_sites "
        f"raw={len(raw_entries)} current={len(reconciled)} removed={removed_count} expanded={expanded_count}"
    )
    return {
        "raw_site_count": len(raw_entries),
        "current_site_count": len(reconciled),
        "removed_count": removed_count,
        "expanded_count": expanded_count,
        "changed": True,
    }


def _apply_vacuum_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    *,
    mode: Literal["add", "set"],
) -> tuple[LatticeSpec, list[BasisAtomSpec]]:
    if operation.axis is None or operation.thickness_angstrom is None:
        raise ValueError(f"{mode}_vacuum requires axis and thickness_angstrom")
    key = {"x": "a", "y": "b", "z": "c"}.get(operation.axis, operation.axis)
    previous_axis_length = float(getattr(lattice, key))
    if previous_axis_length <= 0:
        raise ValueError(f"{mode}_vacuum requires a positive lattice axis length")
    target_thickness = float(operation.thickness_angstrom)
    metadata = dict(spec.metadata or {})
    previous_vacuum = _metadata_optional_float(metadata.get("vacuum_angstrom"))
    slab_thickness = _slab_thickness_for_vacuum_update(metadata, previous_axis_length, previous_vacuum)
    if mode == "add":
        vacuum_angstrom = (previous_vacuum + target_thickness) if previous_vacuum is not None else target_thickness
        new_axis_length = previous_axis_length + target_thickness
    else:
        vacuum_angstrom = target_thickness
        new_axis_length = slab_thickness + vacuum_angstrom
    if new_axis_length <= 0:
        raise ValueError(f"{mode}_vacuum produced an invalid lattice axis length")

    axis_index = {"a": 0, "b": 1, "c": 2}[key]
    max_cartesian_position = max(
        (float((atom.fractional.x, atom.fractional.y, atom.fractional.z)[axis_index]) * previous_axis_length for atom in atoms),
        default=0.0,
    )
    if max_cartesian_position > new_axis_length + 1e-9:
        raise ValueError("Requested vacuum is too small for the existing atom positions along the slab axis.")

    scale = previous_axis_length / new_axis_length
    scaled_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        fractional = [atom.fractional.x, atom.fractional.y, atom.fractional.z]
        fractional[axis_index] = fractional[axis_index] * scale
        scaled_atoms.append(
            atom.model_copy(
                update={
                    "fractional": FractionalVector3(
                        x=fractional[0],
                        y=fractional[1],
                        z=fractional[2],
                    )
                }
            )
        )

    metadata.update(
        {
            "surface_axis": key,
            "vacuum_angstrom": vacuum_angstrom,
            "slab_thickness_angstrom": slab_thickness,
        }
    )
    spec.metadata = metadata
    return lattice.model_copy(update={key: new_axis_length}), scaled_atoms


def _slab_thickness_for_vacuum_update(
    metadata: dict[str, Any],
    previous_axis_length: float,
    previous_vacuum: float | None,
) -> float:
    declared = _metadata_optional_float(metadata.get("slab_thickness_angstrom"))
    if declared is not None and declared > 0:
        return declared
    if previous_vacuum is not None:
        inferred = previous_axis_length - previous_vacuum
        if inferred > 0:
            return inferred
    return previous_axis_length


def _metadata_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_center_slab_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
) -> list[BasisAtomSpec]:
    metadata = dict(spec.metadata or {})
    raw_axis = operation.axis or metadata.get("surface_axis") or "c"
    key = {"x": "a", "y": "b", "z": "c"}.get(str(raw_axis).lower(), str(raw_axis).lower())
    axis_index = {"a": 0, "b": 1, "c": 2}.get(key)
    axis_length = float(getattr(lattice, key)) if key in {"a", "b", "c"} else None
    if axis_index is None or axis_length is None or axis_length <= 0:
        raise ValueError("center_slab requires a valid lattice axis.")
    values = [float((atom.fractional.x, atom.fractional.y, atom.fractional.z)[axis_index]) for atom in atoms]
    if not values:
        raise ValueError("center_slab requires at least one crystal atom.")
    old_min = min(values)
    old_max = max(values)
    span = old_max - old_min
    if span >= 1.0:
        raise ValueError("center_slab cannot center atoms spanning the full unit cell.")
    old_center = (old_min + old_max) / 2.0
    shift = 0.5 - old_center
    new_min = old_min + shift
    new_max = old_max + shift
    if new_min < -1e-9 or new_max > 1.0 + 1e-9:
        raise ValueError("center_slab would move atoms outside the unit cell.")

    shifted_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        fractional = [atom.fractional.x, atom.fractional.y, atom.fractional.z]
        fractional[axis_index] = min(max(fractional[axis_index] + shift, 0.0), 1.0)
        shifted_atoms.append(
            atom.model_copy(
                update={
                    "fractional": FractionalVector3(
                        x=fractional[0],
                        y=fractional[1],
                        z=fractional[2],
                    )
                }
            )
        )

    centering = {
        "axis": key,
        "old_fractional_min": round(old_min, 6),
        "old_fractional_max": round(old_max, 6),
        "new_fractional_min": round(new_min, 6),
        "new_fractional_max": round(new_max, 6),
        "shift_fractional": round(shift, 6),
        "bottom_vacuum_angstrom": round(new_min * axis_length, 6),
        "top_vacuum_angstrom": round((1.0 - new_max) * axis_length, 6),
        "source": "semantic_patch_center_slab",
    }
    metadata.update(
        {
            "surface_axis": key,
            "slab_centering": centering,
            "last_slab_centering": centering,
        }
    )
    spec.metadata = metadata
    return shifted_atoms


def _apply_interface_gap_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
) -> tuple[LatticeSpec, list[BasisAtomSpec]]:
    if operation.thickness_angstrom is None:
        raise ValueError("set_interface_gap requires thickness_angstrom")
    metadata = dict(spec.metadata or {})
    if not metadata.get("interface_scaffold"):
        raise ValueError("set_interface_gap requires an interface scaffold model")

    key = {"x": "a", "y": "b", "z": "c"}.get(str(operation.axis or metadata.get("interface_axis") or metadata.get("surface_axis") or "c").lower())
    key = key or str(operation.axis or metadata.get("interface_axis") or metadata.get("surface_axis") or "c").lower()
    axis_index = {"a": 0, "b": 1, "c": 2}.get(key)
    if axis_index is None:
        raise ValueError("set_interface_gap requires a valid lattice axis")
    old_axis_length = float(getattr(lattice, key))
    if old_axis_length <= 0:
        raise ValueError("set_interface_gap requires a positive lattice axis length")

    old_gap = _metadata_optional_float(metadata.get("interface_gap_angstrom"))
    bottom_vacuum = _metadata_optional_float(metadata.get("bottom_vacuum_angstrom"))
    top_vacuum = _metadata_optional_float(metadata.get("top_vacuum_angstrom"))
    substrate_thickness = _metadata_optional_float(metadata.get("substrate_thickness_angstrom"))
    film_thickness = _metadata_optional_float(metadata.get("film_thickness_angstrom"))
    if None in {old_gap, bottom_vacuum, top_vacuum, substrate_thickness, film_thickness}:
        raise ValueError("set_interface_gap requires scaffold thickness metadata")

    target_gap = float(operation.thickness_angstrom)
    if target_gap <= 0 or target_gap > 100.0:
        raise ValueError("set_interface_gap target must be between 0 and 100 Angstrom")

    assert old_gap is not None
    assert bottom_vacuum is not None
    assert top_vacuum is not None
    assert substrate_thickness is not None
    assert film_thickness is not None
    old_film_start = bottom_vacuum + substrate_thickness + old_gap
    new_film_start = bottom_vacuum + substrate_thickness + target_gap
    new_axis_length = bottom_vacuum + substrate_thickness + target_gap + film_thickness + top_vacuum
    if new_axis_length <= 0:
        raise ValueError("set_interface_gap produced an invalid lattice axis length")

    film_atom_ids = _interface_scaffold_film_atom_ids(atoms, axis_index, old_axis_length, old_film_start)
    if not film_atom_ids:
        raise ValueError("set_interface_gap could not identify the interface film atoms")

    updated_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        fractional = [float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z)]
        old_cartesian = fractional[axis_index] * old_axis_length
        if atom.id in film_atom_ids:
            new_cartesian = new_film_start + (old_cartesian - old_film_start)
        else:
            new_cartesian = old_cartesian
        new_fractional = new_cartesian / new_axis_length
        if new_fractional < -1e-9 or new_fractional > 1.0 + 1e-9:
            raise ValueError("set_interface_gap would move atoms outside the unit cell")
        fractional[axis_index] = min(max(new_fractional, 0.0), 1.0)
        updated_atoms.append(
            atom.model_copy(
                update={
                    "fractional": FractionalVector3(
                        x=fractional[0],
                        y=fractional[1],
                        z=fractional[2],
                    )
                }
            )
        )

    adjustment = {
        "source": "semantic_patch_set_interface_gap",
        "axis": key,
        "previous_gap_angstrom": round(old_gap, 6),
        "target_gap_angstrom": round(target_gap, 6),
        "delta_angstrom": round(target_gap - old_gap, 6),
        "old_axis_length_angstrom": round(old_axis_length, 6),
        "new_axis_length_angstrom": round(new_axis_length, 6),
        "moved_film_atom_count": len(film_atom_ids),
    }
    metadata.update(
        {
            "interface_axis": key,
            "surface_axis": key,
            "interface_gap_angstrom": round(target_gap, 6),
            "slab_thickness_angstrom": round(substrate_thickness + target_gap + film_thickness, 6),
            "vacuum_angstrom": round(bottom_vacuum + top_vacuum, 6),
            "requires_geometry_relaxation": True,
            "unrelaxed_interface": True,
            "last_interface_gap_adjustment": adjustment,
        }
    )
    spec.metadata = metadata
    diff.append(f"set_interface_gap {key} {target_gap}")
    return lattice.model_copy(update={key: new_axis_length}), updated_atoms


def _interface_scaffold_film_atom_ids(
    atoms: list[BasisAtomSpec],
    axis_index: int,
    axis_length: float,
    film_start_angstrom: float,
) -> set[str]:
    ids = {atom.id for atom in atoms if re.search(r"F\d+$", atom.id)}
    if ids:
        return ids
    threshold = film_start_angstrom - 1e-6
    return {
        atom.id
        for atom in atoms
        if [float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z)][axis_index]
        * axis_length
        >= threshold
    }


def _apply_gate_stack_thickness_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
) -> list[BasisAtomSpec]:
    """Adjust a gate-stack segment thickness along the interface axis."""

    if operation.target_layer is None or operation.thickness_angstrom is None:
        raise ValueError("set_gate_stack_thickness requires target_layer and thickness_angstrom")
    metadata = dict(spec.metadata or {})
    target_material = _gate_stack_target_material(metadata, operation.target_layer)
    axis_key = _gate_stack_axis_key(metadata, operation.axis)
    axis_index = {"a": 0, "b": 1, "c": 2}[axis_key]
    axis_length = float(getattr(lattice, axis_key))
    if axis_length <= 0:
        raise ValueError("set_gate_stack_thickness requires a positive lattice axis length")

    layers = _gate_stack_layers(atoms, axis_index, metadata)
    target_layers = [layer for layer in layers if layer["material"] == target_material]
    if not target_layers:
        raise ValueError(f"Cannot find {operation.target_layer} gate-stack layer for material {target_material}.")

    centers = sorted(float(layer["center"]) for layer in target_layers)
    old_start = centers[0]
    old_end = centers[-1]
    old_span_fractional = old_end - old_start
    if old_span_fractional <= 1e-9:
        raise ValueError("set_gate_stack_thickness requires at least two distinct layer centers.")

    requested_thickness = float(operation.thickness_angstrom)
    new_span_fractional = requested_thickness / axis_length
    new_end = old_start + new_span_fractional
    if new_end > 1.0 + 1e-12:
        raise ValueError("Requested gate-stack thickness exceeds the unit-cell extent along the interface axis.")

    scale = new_span_fractional / old_span_fractional
    shift_above = new_end - old_end
    target_ids = {atom_id for layer in target_layers for atom_id in layer["atom_ids"]}
    above_ids = {
        atom_id
        for layer in layers
        if float(layer["center"]) > old_end + 1e-9
        for atom_id in layer["atom_ids"]
    }

    updated_atoms: list[BasisAtomSpec] = []
    for atom in atoms:
        fractional = [float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z)]
        current_value = fractional[axis_index]
        if atom.id in target_ids:
            fractional[axis_index] = old_start + (current_value - old_start) * scale
        elif atom.id in above_ids:
            fractional[axis_index] = current_value + shift_above
        if fractional[axis_index] < -1e-12 or fractional[axis_index] > 1.0 + 1e-12:
            raise ValueError("Requested gate-stack thickness would move atoms outside the unit cell.")
        fractional[axis_index] = min(max(fractional[axis_index], 0.0), 1.0)
        updated_atoms.append(
            atom.model_copy(
                update={
                    "fractional": FractionalVector3(
                        x=fractional[0],
                        y=fractional[1],
                        z=fractional[2],
                    )
                }
            )
        )

    updated_layers = _gate_stack_layers(updated_atoms, axis_index, metadata)
    _validate_gate_stack_layer_spacing(updated_layers, axis_length)
    new_target_layers = [layer for layer in updated_layers if layer["material"] == target_material]
    new_centers = sorted(float(layer["center"]) for layer in new_target_layers)
    if len(new_centers) >= 2:
        measured_thickness = (new_centers[-1] - new_centers[0]) * axis_length
    else:
        measured_thickness = 0.0
    if abs(measured_thickness - requested_thickness) > 1e-6:
        raise ValueError("set_gate_stack_thickness failed to produce the requested layer-center span.")

    old_span_angstrom = old_span_fractional * axis_length
    record = {
        "target_layer": operation.target_layer,
        "target_material": target_material,
        "axis": axis_key,
        "requested_thickness_angstrom": round(requested_thickness, 6),
        "old_center_span_angstrom": round(old_span_angstrom, 6),
        "new_center_span_angstrom": round(measured_thickness, 6),
        "old_fractional_center_start": round(old_start, 6),
        "old_fractional_center_end": round(old_end, 6),
        "new_fractional_center_start": round(new_centers[0], 6),
        "new_fractional_center_end": round(new_centers[-1], 6),
        "shifted_above_atom_count": len(above_ids),
        "source": "semantic_patch_set_gate_stack_thickness",
    }
    previous_edits = [
        dict(item)
        for item in metadata.get("gate_stack_thickness_edits", [])
        if isinstance(item, dict)
    ]
    previous_edits.append(record)
    metadata["gate_stack_thickness_edits"] = previous_edits
    metadata["last_gate_stack_thickness_edit"] = record
    _update_gate_stack_thickness_metadata(metadata, operation.target_layer, target_material, requested_thickness)
    spec.metadata = metadata

    diff.append(f"set_gate_stack_thickness {operation.target_layer} {target_material} {requested_thickness:g}A")
    return updated_atoms


def _apply_p_gan_gate_cap_thickness_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
) -> tuple[LatticeSpec, list[BasisAtomSpec]]:
    """Rebuild the metadata-backed p-GaN gate/cap at a new motif-compatible thickness."""

    if operation.thickness_angstrom is None:
        raise ValueError("set_p_gan_gate_cap_thickness requires thickness_angstrom")
    metadata = dict(spec.metadata or {})
    raw_cap = metadata.get("p_gan_gate_cap")
    if not isinstance(raw_cap, dict):
        raise ValueError("set_p_gan_gate_cap_thickness requires existing p_gan_gate_cap metadata")
    axis_key = _gate_stack_axis_key(metadata, operation.axis or raw_cap.get("axis") or "c")
    if axis_key != "c":
        raise ValueError("set_p_gan_gate_cap_thickness currently supports only c-axis p-GaN caps")

    cap_layers = _p_gan_gate_cap_layers(raw_cap)
    old_cap_atom_ids = _p_gan_gate_cap_atom_ids(cap_layers)
    atom_by_id = {atom.id: atom for atom in atoms}
    missing = sorted(atom_id for atom_id in old_cap_atom_ids if atom_id not in atom_by_id)
    if missing:
        raise ValueError(f"p-GaN cap metadata references missing atoms: {', '.join(missing)}")

    old_c = float(lattice.c)
    old_thickness = _metadata_optional_float(raw_cap.get("actual_thickness_angstrom"))
    layer_spacing = _metadata_optional_float(raw_cap.get("layer_spacing_angstrom"))
    if old_thickness is None or old_thickness <= 0:
        if layer_spacing is not None and layer_spacing > 0:
            old_thickness = len(cap_layers) * layer_spacing
        else:
            raise ValueError("p-GaN cap metadata needs a positive actual_thickness_angstrom")
    if layer_spacing is None or layer_spacing <= 0:
        layer_spacing = old_thickness / max(len(cap_layers), 1)
    if layer_spacing <= 0:
        raise ValueError("p-GaN cap metadata needs a positive layer spacing")
    base_c = old_c - old_thickness
    if base_c <= 0:
        raise ValueError("p-GaN cap thickness is inconsistent with the current lattice c length")

    motif_length = _p_gan_gate_cap_motif_length(cap_layers)
    templates = _p_gan_gate_cap_templates(cap_layers, atom_by_id, motif_length, raw_cap)
    if not templates:
        raise ValueError("Could not recover p-GaN cap layer templates from the current revision")

    requested_thickness = float(operation.thickness_angstrom)
    new_layer_count, actual_thickness = _p_gan_gate_cap_requested_layer_count(
        requested_thickness,
        layer_spacing=layer_spacing,
        motif_length=len(templates),
    )
    new_c = _round_patch_float(base_c + actual_thickness)
    if new_c <= base_c:
        raise ValueError("set_p_gan_gate_cap_thickness produced an invalid lattice c length")

    updated_atoms: list[BasisAtomSpec] = []
    used_ids: set[str] = set()
    for atom in atoms:
        if atom.id in old_cap_atom_ids:
            continue
        cartesian_z = float(atom.fractional.z) * old_c
        if cartesian_z > base_c + 1e-6:
            raise ValueError("Existing non-cap atoms extend into the p-GaN cap region")
        updated_atom = atom.model_copy(
            update={
                "fractional": FractionalVector3(
                    x=_round_patch_float(atom.fractional.x),
                    y=_round_patch_float(atom.fractional.y),
                    z=_round_patch_float(cartesian_z / new_c),
                )
            }
        )
        updated_atoms.append(updated_atom)
        used_ids.add(updated_atom.id)

    dopant_element = str(raw_cap.get("dopant_element") or "Mg")
    dopant_site_element = str(raw_cap.get("dopant_site_element") or "Ga")
    dopant_record: dict[str, Any] | None = None
    cap_layer_records: list[dict[str, Any]] = []
    cap_cation_count = 0
    for layer_offset in range(new_layer_count):
        template_layer = templates[layer_offset % len(templates)]
        z_fractional = _round_patch_float((base_c + layer_offset * layer_spacing) / new_c)
        layer_atom_ids: list[str] = []
        for atom_index, template_atom in enumerate(template_layer, start=1):
            site_element = str(template_atom["element"])
            element = site_element
            prefix = site_element
            if dopant_record is None and site_element == dopant_site_element:
                element = dopant_element
                prefix = dopant_element
            if site_element == dopant_site_element:
                cap_cation_count += 1
            atom_id = f"{prefix}PGaN{layer_offset + 1}_{atom_index}"
            if atom_id in used_ids:
                raise ValueError(f"Generated p-GaN cap atom id collides with existing atom: {atom_id}")
            fractional = FractionalVector3(
                x=_round_patch_float(template_atom["x"]),
                y=_round_patch_float(template_atom["y"]),
                z=z_fractional,
            )
            updated_atoms.append(BasisAtomSpec(id=atom_id, element=element, fractional=fractional))
            used_ids.add(atom_id)
            layer_atom_ids.append(atom_id)
            if element == dopant_element and dopant_record is None:
                dopant_record = _p_gan_gate_cap_dopant_site_record(
                    atom_id=atom_id,
                    site_element=dopant_site_element,
                    dopant_element=dopant_element,
                    fractional=[fractional.x, fractional.y, fractional.z],
                )
        cap_layer_records.append(
            {
                "layer_index": layer_offset + 1,
                "template_layer_index": (layer_offset % len(templates)) + 1,
                "fractional_center": z_fractional,
                "atom_ids": layer_atom_ids,
            }
        )

    if dopant_record is None:
        raise ValueError("Could not place an Mg acceptor marker in the p-GaN cap")

    cap_record = {
        **raw_cap,
        "source": "semantic_patch_set_p_gan_gate_cap_thickness",
        "requested_thickness_angstrom": _round_patch_float(requested_thickness),
        "actual_thickness_angstrom": _round_patch_float(actual_thickness),
        "thickness_error_angstrom": _round_patch_float(actual_thickness - requested_thickness),
        "layer_count": new_layer_count,
        "layer_spacing_angstrom": _round_patch_float(layer_spacing),
        "dopant_atom_id": dopant_record["atom_id"],
        "dopant_fraction_of_cap_cations": _round_patch_float(1.0 / max(cap_cation_count, 1)),
        "layers": cap_layer_records,
    }
    edit_record = {
        "operation": "set_p_gan_gate_cap_thickness",
        "axis": "c",
        "requested_thickness_angstrom": _round_patch_float(requested_thickness),
        "old_actual_thickness_angstrom": _round_patch_float(old_thickness),
        "new_actual_thickness_angstrom": _round_patch_float(actual_thickness),
        "old_layer_count": len(cap_layers),
        "new_layer_count": new_layer_count,
        "old_lattice_c_angstrom": _round_patch_float(old_c),
        "new_lattice_c_angstrom": new_c,
        "dopant_atom_id": dopant_record["atom_id"],
        "source": "semantic_patch_set_p_gan_gate_cap_thickness",
    }
    previous_edits = [
        dict(item)
        for item in metadata.get("p_gan_gate_cap_thickness_edits", [])
        if isinstance(item, dict)
    ]
    previous_edits.append(edit_record)

    dopant_sites = [
        dict(item)
        for item in metadata.get("semiconductor_dopant_sites", [])
        if isinstance(item, dict)
        and not _is_p_gan_gate_cap_dopant_site(item, old_cap_atom_ids, raw_cap)
    ]
    dopant_sites.append(dopant_record)

    metadata["p_gan_gate_cap"] = cap_record
    metadata["last_p_gan_gate_cap"] = cap_record
    metadata["p_gan_gate"] = True
    metadata["p_gan_gate_cap_thickness_edits"] = previous_edits
    metadata["last_p_gan_gate_cap_thickness_edit"] = edit_record
    metadata["semiconductor_dopant_sites"] = dopant_sites
    metadata["last_semiconductor_dopant_site"] = dopant_record
    spec.metadata = metadata

    diff.append(
        "set_p_gan_gate_cap_thickness "
        f"{requested_thickness:g}A actual {actual_thickness:g}A layers {new_layer_count} Mg:{dopant_record['atom_id']}"
    )
    return lattice.model_copy(update={"c": new_c}), updated_atoms


def _p_gan_gate_cap_layers(raw_cap: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [
        dict(item)
        for item in raw_cap.get("layers", []) or []
        if isinstance(item, dict)
    ]
    if not layers:
        raise ValueError("p-GaN cap metadata needs layer records")
    return sorted(layers, key=lambda item: _p_gan_gate_cap_int(item.get("layer_index"), len(layers) + 1))


def _p_gan_gate_cap_atom_ids(layers: list[dict[str, Any]]) -> set[str]:
    atom_ids = {
        str(atom_id)
        for layer in layers
        for atom_id in layer.get("atom_ids", []) or []
        if str(atom_id)
    }
    if not atom_ids:
        raise ValueError("p-GaN cap metadata needs layer atom ids")
    return atom_ids


def _p_gan_gate_cap_motif_length(layers: list[dict[str, Any]]) -> int:
    motif = 0
    expected = 1
    for layer in layers:
        template_index = _p_gan_gate_cap_int(layer.get("template_layer_index"), 0)
        if template_index <= 0:
            break
        if motif and template_index == 1:
            break
        if template_index != expected:
            break
        motif = template_index
        expected += 1
    if motif <= 0:
        return min(len(layers), 4)
    return motif


def _p_gan_gate_cap_templates(
    layers: list[dict[str, Any]],
    atom_by_id: dict[str, BasisAtomSpec],
    motif_length: int,
    raw_cap: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    dopant_element = str(raw_cap.get("dopant_element") or "Mg")
    dopant_site_element = str(raw_cap.get("dopant_site_element") or "Ga")
    templates: list[list[dict[str, Any]]] = []
    for layer in layers[:motif_length]:
        template_layer: list[dict[str, Any]] = []
        for atom_id in layer.get("atom_ids", []) or []:
            atom = atom_by_id[str(atom_id)]
            element = dopant_site_element if atom.element == dopant_element else atom.element
            template_layer.append(
                {
                    "element": element,
                    "x": float(atom.fractional.x),
                    "y": float(atom.fractional.y),
                }
            )
        if template_layer:
            templates.append(template_layer)
    if not any(atom["element"] == dopant_site_element for layer in templates for atom in layer):
        raise ValueError("p-GaN cap templates do not contain a dopable Ga site")
    return templates


def _p_gan_gate_cap_requested_layer_count(
    requested_thickness: float,
    *,
    layer_spacing: float,
    motif_length: int,
) -> tuple[int, float]:
    motif = max(1, int(motif_length))
    candidates: list[tuple[float, int, float]] = []
    for count in range(motif, 241, motif):
        actual = count * layer_spacing
        candidates.append((abs(actual - requested_thickness), count, actual))
    if not candidates:
        raise ValueError("No motif-compatible p-GaN cap layer count is available")
    _, layer_count, actual_thickness = min(candidates, key=lambda item: (item[0], item[1]))
    return layer_count, actual_thickness


def _is_p_gan_gate_cap_dopant_site(
    site: dict[str, Any],
    old_cap_atom_ids: set[str],
    raw_cap: dict[str, Any],
) -> bool:
    atom_id = str(site.get("atom_id") or site.get("site_id") or "")
    source = str(site.get("source") or "")
    return bool(
        atom_id in old_cap_atom_ids
        or atom_id == str(raw_cap.get("dopant_atom_id") or "")
        or source in {"natural_language_p_gan_gate_cap", "semantic_patch_set_p_gan_gate_cap_thickness"}
    )


def _p_gan_gate_cap_dopant_site_record(
    *,
    atom_id: str,
    site_element: str,
    dopant_element: str,
    fractional: list[float],
) -> dict[str, Any]:
    return {
        "site_id": atom_id,
        "atom_id": atom_id,
        "site_element": site_element,
        "dopant_element": dopant_element,
        "new_element": dopant_element,
        "fractional": [_round_patch_float(value) for value in fractional],
        "auto_selected_site": True,
        "source": "semantic_patch_set_p_gan_gate_cap_thickness",
    }


def _p_gan_gate_cap_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _round_patch_float(value: Any) -> float:
    return round(float(value), 6)


def _apply_quantum_well_thickness_patch(
    spec: ModelSpec,
    lattice: LatticeSpec,
    atoms: list[BasisAtomSpec],
    operation: SemanticPatchOperation,
    diff: list[str],
) -> tuple[LatticeSpec, list[BasisAtomSpec]]:
    """Rebuild a metadata-backed two-material quantum well at a new layer thickness."""

    if operation.target_layer not in {"well", "barrier"} or operation.thickness_angstrom is None:
        raise ValueError("set_quantum_well_thickness requires target_layer well/barrier and thickness_angstrom")
    metadata = dict(spec.metadata or {})
    axis_key = _gate_stack_axis_key(metadata, operation.axis)
    if axis_key != "c":
        raise ValueError("set_quantum_well_thickness currently supports only c-axis heterostructures")

    well_material, barrier_material = _quantum_well_material_pair(metadata)
    raw_cap = metadata.get("p_gan_gate_cap") if isinstance(metadata.get("p_gan_gate_cap"), dict) else None
    cap_context = _quantum_well_p_gan_cap_context(raw_cap, atoms) if raw_cap is not None else None
    cap_atom_ids = set(cap_context["old_atom_ids"]) if cap_context is not None else set()
    base_atoms = [atom for atom in atoms if atom.id not in cap_atom_ids]
    if not base_atoms:
        raise ValueError("set_quantum_well_thickness requires non-cap quantum-well atoms")

    period_count = _quantum_well_period_count(metadata)
    current_counts = _quantum_well_current_layer_counts(base_atoms, metadata, period_count)
    current_well_layers = current_counts["well"]
    current_barrier_layers = current_counts["barrier"]
    assigned_layers = _quantum_well_assigned_layers(
        base_atoms,
        metadata,
        period_count=period_count,
        well_layer_count=current_well_layers,
        barrier_layer_count=current_barrier_layers,
        well_material=well_material,
        barrier_material=barrier_material,
    )
    templates = {
        well_material: _quantum_well_templates_from_assigned_layers(assigned_layers, well_material),
        barrier_material: _quantum_well_templates_from_assigned_layers(assigned_layers, barrier_material),
    }
    if not templates[well_material] or not templates[barrier_material]:
        raise ValueError("Could not recover quantum-well layer templates from the current revision")

    motif_length = max(len(templates[well_material]), len(templates[barrier_material]), 1)
    spacings = {
        material: _quantum_well_layer_spacing_from_templates(metadata, material, templates[material], float(lattice.c))
        for material in (well_material, barrier_material)
    }
    requested_thickness = float(operation.thickness_angstrom)
    if operation.target_layer == "well":
        new_well_layers = _quantum_well_nearest_layer_count(
            requested_thickness,
            spacing=spacings[well_material],
            fixed_other_layers=current_barrier_layers,
            motif_length=motif_length,
        )
        new_barrier_layers = current_barrier_layers
    else:
        new_well_layers = current_well_layers
        new_barrier_layers = _quantum_well_nearest_layer_count(
            requested_thickness,
            spacing=spacings[barrier_material],
            fixed_other_layers=current_well_layers,
            motif_length=motif_length,
        )

    actual_well = new_well_layers * spacings[well_material]
    actual_barrier = new_barrier_layers * spacings[barrier_material]
    period_thickness = actual_well + actual_barrier
    base_c = period_thickness * period_count
    if base_c <= 0:
        raise ValueError("set_quantum_well_thickness produced an invalid c-axis length")

    cap_actual = 0.0
    if cap_context is not None:
        cap_actual = float(cap_context["actual_thickness"])
    new_c = _round_patch_float(base_c + cap_actual)
    if new_c <= 0:
        raise ValueError("set_quantum_well_thickness produced an invalid total c-axis length")

    updated_atoms: list[BasisAtomSpec] = []
    material_layer_counters = {well_material: 0, barrier_material: 0}
    z_position = 0.0
    for _period_index in range(period_count):
        for material, layer_count, role_prefix in (
            (well_material, new_well_layers, "W"),
            (barrier_material, new_barrier_layers, "B"),
        ):
            material_templates = templates[material]
            for _layer_offset in range(layer_count):
                material_layer_counters[material] += 1
                template_layer = material_templates[(material_layer_counters[material] - 1) % len(material_templates)]
                for atom_index, template_atom in enumerate(template_layer, start=1):
                    updated_atoms.append(
                        BasisAtomSpec(
                            id=f"{template_atom['element']}{role_prefix}{material_layer_counters[material]}_{atom_index}",
                            element=str(template_atom["element"]),
                            fractional=FractionalVector3(
                                x=_round_patch_float(template_atom["x"]),
                                y=_round_patch_float(template_atom["y"]),
                                z=_round_patch_float(z_position / new_c),
                            ),
                        )
                    )
                z_position += spacings[material]

    cap_record = None
    dopant_record = None
    if cap_context is not None:
        cap_record, dopant_record, cap_atoms = _append_quantum_well_p_gan_cap(
            cap_context,
            base_c=base_c,
            total_c=new_c,
            source="semantic_patch_set_quantum_well_thickness",
        )
        updated_atoms.extend(cap_atoms)

    qwell_record = _quantum_well_request_record(
        well_material=well_material,
        barrier_material=barrier_material,
        well_layers=new_well_layers,
        barrier_layers=new_barrier_layers,
        requested_target=str(operation.target_layer),
        requested_thickness=requested_thickness,
        actual_well=actual_well,
        actual_barrier=actual_barrier,
    )
    edit_record = {
        "operation": "set_quantum_well_thickness",
        "target_layer": operation.target_layer,
        "well_material": well_material,
        "barrier_material": barrier_material,
        "requested_thickness_angstrom": _round_patch_float(requested_thickness),
        "old_well_layer_count": current_well_layers,
        "old_barrier_layer_count": current_barrier_layers,
        "new_well_layer_count": new_well_layers,
        "new_barrier_layer_count": new_barrier_layers,
        "period_count": period_count,
        "old_lattice_c_angstrom": _round_patch_float(lattice.c),
        "new_lattice_c_angstrom": new_c,
        "source": "semantic_patch_set_quantum_well_thickness",
    }
    previous_edits = [
        dict(item)
        for item in metadata.get("quantum_well_thickness_edits", [])
        if isinstance(item, dict)
    ]
    previous_edits.append(edit_record)

    metadata["quantum_well_layer_request"] = qwell_record
    metadata["last_quantum_well_layer_request"] = qwell_record
    metadata["quantum_well_thickness_edits"] = previous_edits
    metadata["last_quantum_well_thickness_edit"] = edit_record
    metadata["structure_family"] = f"{metadata.get('structure_family') or spec.model.name} custom quantum well"
    if cap_record is not None and dopant_record is not None:
        metadata["p_gan_gate_cap"] = cap_record
        metadata["last_p_gan_gate_cap"] = cap_record
        dopant_sites = [
            dict(item)
            for item in metadata.get("semiconductor_dopant_sites", [])
            if isinstance(item, dict)
            and not _is_p_gan_gate_cap_dopant_site(item, set(cap_context["old_atom_ids"]), cap_context["raw_cap"])
        ]
        dopant_sites.append(dopant_record)
        metadata["semiconductor_dopant_sites"] = dopant_sites
        metadata["last_semiconductor_dopant_site"] = dopant_record
    spec.metadata = metadata

    target_material = well_material if operation.target_layer == "well" else barrier_material
    target_layers = new_well_layers if operation.target_layer == "well" else new_barrier_layers
    target_actual = actual_well if operation.target_layer == "well" else actual_barrier
    diff.append(
        "set_quantum_well_thickness "
        f"{operation.target_layer} {target_material} {requested_thickness:g}A actual {target_actual:g}A layers {target_layers}"
    )
    return lattice.model_copy(update={"c": new_c}), updated_atoms


def _quantum_well_material_pair(metadata: dict[str, Any]) -> tuple[str, str]:
    materials = metadata.get("materials") or []
    if isinstance(materials, str):
        materials = [materials]
    materials = [str(material) for material in materials if str(material)]
    well_material = str(metadata.get("substrate") or (materials[0] if materials else ""))
    explicit_barriers = metadata.get("polarization_2deg_barrier_materials")
    if isinstance(explicit_barriers, str):
        barrier_material = explicit_barriers
    elif isinstance(explicit_barriers, list) and explicit_barriers:
        barrier_material = str(explicit_barriers[0])
    else:
        barrier_material = next(
            (
                material
                for material in materials
                if material != well_material and material.lower() not in {"p-gan", "pgan"}
            ),
            "",
        )
    if not well_material or not barrier_material:
        raise ValueError("set_quantum_well_thickness requires well and barrier materials in metadata")
    return well_material, barrier_material


def _quantum_well_period_count(metadata: dict[str, Any]) -> int:
    for key in ("superlattice_period_count", "period_count"):
        value = _metadata_optional_float(metadata.get(key))
        if value is not None and value >= 1:
            return max(1, int(round(value)))
    latest = metadata.get("last_applied_superlattice_period")
    if isinstance(latest, dict):
        value = _metadata_optional_float(latest.get("estimated_total_period_count"))
        if value is not None and value >= 1:
            return max(1, int(round(value)))
    return 1


def _quantum_well_current_layer_counts(
    atoms: list[BasisAtomSpec],
    metadata: dict[str, Any],
    period_count: int,
) -> dict[str, int]:
    request = metadata.get("quantum_well_layer_request")
    if isinstance(request, dict):
        well_layers = _p_gan_gate_cap_int(request.get("well_layer_count"), 0)
        barrier_layers = _p_gan_gate_cap_int(request.get("barrier_layer_count"), 0)
        if well_layers > 0 and barrier_layers > 0:
            return {"well": well_layers, "barrier": barrier_layers}
    layer_count = len(_quantum_well_sorted_layers(atoms))
    layers_per_period = layer_count // max(period_count, 1)
    if layers_per_period < 2 or layers_per_period % 2:
        raise ValueError("Cannot infer current quantum-well layer counts from the current revision")
    return {"well": layers_per_period // 2, "barrier": layers_per_period // 2}


def _quantum_well_assigned_layers(
    atoms: list[BasisAtomSpec],
    metadata: dict[str, Any],
    *,
    period_count: int,
    well_layer_count: int,
    barrier_layer_count: int,
    well_material: str,
    barrier_material: str,
) -> list[dict[str, Any]]:
    layers = _quantum_well_sorted_layers(atoms)
    expected = period_count * (well_layer_count + barrier_layer_count)
    if len(layers) != expected:
        raise ValueError(
            f"Current quantum-well layer count {len(layers)} does not match expected {expected} from metadata"
        )
    assigned: list[dict[str, Any]] = []
    pattern_length = well_layer_count + barrier_layer_count
    for index, layer in enumerate(layers):
        pattern_index = index % pattern_length
        material = well_material if pattern_index < well_layer_count else barrier_material
        assigned.append({"material": material, "layer": layer})
    return assigned


def _quantum_well_sorted_layers(atoms: list[BasisAtomSpec]) -> list[list[BasisAtomSpec]]:
    sorted_atoms = sorted(atoms, key=lambda atom: (float(atom.fractional.z), float(atom.fractional.x), float(atom.fractional.y), atom.id))
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
    return [sorted(layer, key=lambda atom: (float(atom.fractional.x), float(atom.fractional.y), atom.id)) for layer in layers]


def _quantum_well_templates_from_assigned_layers(
    assigned_layers: list[dict[str, Any]],
    material: str,
) -> list[list[dict[str, Any]]]:
    material_layers = [item["layer"] for item in assigned_layers if item.get("material") == material]
    if not material_layers:
        return []
    signatures = [_quantum_well_layer_signature(layer) for layer in material_layers]
    max_candidate = min(len(signatures), 16)
    motif = len(signatures)
    for candidate in range(1, max_candidate + 1):
        if all(signatures[index] == signatures[index % candidate] for index in range(len(signatures))):
            motif = candidate
            break
    return [
        [
            {
                "element": atom.element,
                "x": float(atom.fractional.x),
                "y": float(atom.fractional.y),
            }
            for atom in layer
        ]
        for layer in material_layers[:motif]
    ]


def _quantum_well_layer_signature(layer: list[BasisAtomSpec]) -> tuple[tuple[str, float, float], ...]:
    return tuple(
        sorted(
            (
                atom.element,
                round(float(atom.fractional.x), 6),
                round(float(atom.fractional.y), 6),
            )
            for atom in layer
        )
    )


def _quantum_well_layer_spacing_from_templates(
    metadata: dict[str, Any],
    material: str,
    templates: list[list[dict[str, Any]]],
    fallback_c_length: float,
) -> float:
    key = re.sub(r"[^a-z0-9]+", "", material.lower()) + "_reference_lattice_angstrom"
    reference = _metadata_optional_float(metadata.get(key))
    if reference is not None and reference > 0 and templates:
        return reference / len(templates)
    return fallback_c_length / max(len(templates), 1)


def _quantum_well_nearest_layer_count(
    requested_thickness: float,
    *,
    spacing: float,
    fixed_other_layers: int,
    motif_length: int,
) -> int:
    if requested_thickness <= 0 or spacing <= 0:
        raise ValueError("set_quantum_well_thickness requires positive thickness and layer spacing")
    candidates: list[tuple[float, int]] = []
    for count in range(2, 241, 2):
        if (count + fixed_other_layers) % max(motif_length, 1):
            continue
        candidates.append((abs(count * spacing - requested_thickness), count))
    if not candidates:
        raise ValueError("No motif-compatible quantum-well layer count is available")
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def _quantum_well_request_record(
    *,
    well_material: str,
    barrier_material: str,
    well_layers: int,
    barrier_layers: int,
    requested_target: str,
    requested_thickness: float,
    actual_well: float,
    actual_barrier: float,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "well_material": well_material,
        "barrier_material": barrier_material,
        "well_layer_count": well_layers,
        "barrier_layer_count": barrier_layers,
        "axis": "c",
        "source": "semantic_patch_set_quantum_well_thickness",
        "actual_well_thickness_angstrom": _round_patch_float(actual_well),
        "actual_barrier_thickness_angstrom": _round_patch_float(actual_barrier),
    }
    if requested_target == "well":
        record["requested_well_thickness_angstrom"] = _round_patch_float(requested_thickness)
        record["well_thickness_error_angstrom"] = _round_patch_float(actual_well - requested_thickness)
    else:
        record["requested_barrier_thickness_angstrom"] = _round_patch_float(requested_thickness)
        record["barrier_thickness_error_angstrom"] = _round_patch_float(actual_barrier - requested_thickness)
    return record


def _quantum_well_p_gan_cap_context(
    raw_cap: dict[str, Any] | None,
    atoms: list[BasisAtomSpec],
) -> dict[str, Any] | None:
    if not isinstance(raw_cap, dict):
        return None
    cap_layers = _p_gan_gate_cap_layers(raw_cap)
    old_atom_ids = _p_gan_gate_cap_atom_ids(cap_layers)
    atom_by_id = {atom.id: atom for atom in atoms}
    missing = sorted(atom_id for atom_id in old_atom_ids if atom_id not in atom_by_id)
    if missing:
        raise ValueError(f"p-GaN cap metadata references missing atoms: {', '.join(missing)}")
    old_thickness = _metadata_optional_float(raw_cap.get("actual_thickness_angstrom"))
    spacing = _metadata_optional_float(raw_cap.get("layer_spacing_angstrom"))
    if old_thickness is None or old_thickness <= 0:
        if spacing is None or spacing <= 0:
            raise ValueError("p-GaN cap metadata needs a positive thickness before quantum-well edits")
        old_thickness = len(cap_layers) * spacing
    if spacing is None or spacing <= 0:
        spacing = old_thickness / max(len(cap_layers), 1)
    motif_length = _p_gan_gate_cap_motif_length(cap_layers)
    templates = _p_gan_gate_cap_templates(cap_layers, atom_by_id, motif_length, raw_cap)
    return {
        "raw_cap": raw_cap,
        "layers": cap_layers,
        "old_atom_ids": old_atom_ids,
        "actual_thickness": old_thickness,
        "spacing": spacing,
        "templates": templates,
    }


def _append_quantum_well_p_gan_cap(
    cap_context: dict[str, Any],
    *,
    base_c: float,
    total_c: float,
    source: str,
) -> tuple[dict[str, Any], dict[str, Any], list[BasisAtomSpec]]:
    raw_cap = dict(cap_context["raw_cap"])
    templates = cap_context["templates"]
    layer_count = _p_gan_gate_cap_int(raw_cap.get("layer_count"), len(cap_context["layers"]))
    spacing = float(cap_context["spacing"])
    dopant_element = str(raw_cap.get("dopant_element") or "Mg")
    dopant_site_element = str(raw_cap.get("dopant_site_element") or "Ga")
    atoms: list[BasisAtomSpec] = []
    cap_layer_records: list[dict[str, Any]] = []
    dopant_record: dict[str, Any] | None = None
    cap_cation_count = 0
    for layer_offset in range(layer_count):
        template_layer = templates[layer_offset % len(templates)]
        z_fractional = _round_patch_float((base_c + layer_offset * spacing) / total_c)
        layer_atom_ids: list[str] = []
        for atom_index, template_atom in enumerate(template_layer, start=1):
            site_element = str(template_atom["element"])
            element = site_element
            prefix = site_element
            if dopant_record is None and site_element == dopant_site_element:
                element = dopant_element
                prefix = dopant_element
            if site_element == dopant_site_element:
                cap_cation_count += 1
            atom_id = f"{prefix}PGaN{layer_offset + 1}_{atom_index}"
            fractional = FractionalVector3(
                x=_round_patch_float(template_atom["x"]),
                y=_round_patch_float(template_atom["y"]),
                z=z_fractional,
            )
            atoms.append(BasisAtomSpec(id=atom_id, element=element, fractional=fractional))
            layer_atom_ids.append(atom_id)
            if element == dopant_element and dopant_record is None:
                dopant_record = _p_gan_gate_cap_dopant_site_record(
                    atom_id=atom_id,
                    site_element=dopant_site_element,
                    dopant_element=dopant_element,
                    fractional=[fractional.x, fractional.y, fractional.z],
                )
                dopant_record["source"] = source
        cap_layer_records.append(
            {
                "layer_index": layer_offset + 1,
                "template_layer_index": (layer_offset % len(templates)) + 1,
                "fractional_center": z_fractional,
                "atom_ids": layer_atom_ids,
            }
        )
    if dopant_record is None:
        raise ValueError("Could not preserve the Mg acceptor marker in the p-GaN cap")
    cap_record = {
        **raw_cap,
        "source": source,
        "actual_thickness_angstrom": _round_patch_float(cap_context["actual_thickness"]),
        "layer_spacing_angstrom": _round_patch_float(spacing),
        "layer_count": layer_count,
        "dopant_atom_id": dopant_record["atom_id"],
        "dopant_fraction_of_cap_cations": _round_patch_float(1.0 / max(cap_cation_count, 1)),
        "layers": cap_layer_records,
    }
    return cap_record, dopant_record, atoms


def _gate_stack_axis_key(metadata: dict[str, Any], axis: str | None) -> str:
    raw_axis = axis or metadata.get("interface_axis") or metadata.get("surface_axis") or "c"
    key = str(raw_axis).strip().lower()
    normalized = {"x": "a", "y": "b", "z": "c"}.get(key, key)
    if normalized not in {"a", "b", "c"}:
        raise ValueError(f"Unsupported gate-stack axis: {raw_axis}")
    return normalized


def _gate_stack_target_material(metadata: dict[str, Any], target_layer: str) -> str:
    if target_layer == "gate":
        material = metadata.get("gate_material")
    elif target_layer == "oxide":
        material = metadata.get("gate_oxide_material")
    else:
        material = metadata.get("semiconductor_channel_material") or metadata.get("substrate")
    if material:
        return str(material)

    sequence = metadata.get("stack_sequence") or metadata.get("materials") or []
    if isinstance(sequence, str):
        sequence = [sequence]
    sequence = [str(item) for item in sequence if str(item)]
    if target_layer == "gate" and sequence:
        return sequence[-1]
    if target_layer == "channel" and sequence:
        return sequence[0]
    if target_layer == "oxide":
        for material in sequence:
            lower = material.lower()
            if "oxide" in lower or lower in {"sio2", "hfo2", "al2o3"}:
                return material
    raise ValueError(f"Cannot infer {target_layer} material from gate-stack metadata.")


def _gate_stack_layers(
    atoms: list[BasisAtomSpec],
    axis_index: int,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    tolerance = _metadata_float(metadata, "layer_profile_tolerance_fractional", 1e-4)
    sorted_atoms = sorted(
        atoms,
        key=lambda atom: (_atom_fractional_value(atom, axis_index), atom.id),
    )
    clusters: list[list[BasisAtomSpec]] = []
    for atom in sorted_atoms:
        value = _atom_fractional_value(atom, axis_index)
        if not clusters:
            clusters.append([atom])
            continue
        center = sum(_atom_fractional_value(item, axis_index) for item in clusters[-1]) / len(clusters[-1])
        if abs(value - center) <= tolerance:
            clusters[-1].append(atom)
        else:
            clusters.append([atom])

    layers: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        values = [_atom_fractional_value(atom, axis_index) for atom in cluster]
        elements = sorted({atom.element for atom in cluster if atom.element and atom.element != "H"})
        marker = ";".join(elements)
        material = _gate_stack_material_for_marker(marker, metadata)
        layers.append(
            {
                "index": index,
                "center": sum(values) / len(values),
                "atom_ids": [atom.id for atom in cluster],
                "marker": marker,
                "material": material,
            }
        )
    return layers


def _gate_stack_material_for_marker(marker: str, metadata: dict[str, Any]) -> str:
    marker_map = metadata.get("material_marker_map")
    if isinstance(marker_map, dict) and marker in marker_map:
        return str(marker_map[marker])

    materials = metadata.get("materials") or metadata.get("stack_sequence") or []
    if isinstance(materials, str):
        materials = [materials]
    material_map = {str(material): str(material) for material in materials if str(material)}
    for material in list(material_map):
        elements = sorted(_material_label_elements(material))
        if elements:
            material_map[";".join(elements)] = material
    return material_map.get(marker, marker)


def _material_label_elements(material: str) -> set[str]:
    return set(re.findall(r"[A-Z][a-z]?", str(material)))


def _atom_fractional_value(atom: BasisAtomSpec, axis_index: int) -> float:
    return (float(atom.fractional.x), float(atom.fractional.y), float(atom.fractional.z))[axis_index]


def _metadata_float(metadata: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(metadata.get(key, default))
    except (TypeError, ValueError):
        return default


def _validate_gate_stack_layer_spacing(layers: list[dict[str, Any]], axis_length: float) -> None:
    centers = sorted(float(layer["center"]) for layer in layers)
    if len(centers) < 2:
        return
    min_spacing = min((centers[index] - centers[index - 1]) * axis_length for index in range(1, len(centers)))
    if min_spacing < 0.5:
        raise ValueError("Requested gate-stack thickness would create layer centers closer than 0.5 Angstrom.")


def _update_gate_stack_thickness_metadata(
    metadata: dict[str, Any],
    target_layer: str,
    target_material: str,
    thickness: float,
) -> None:
    value = round(float(thickness), 6)
    if target_layer == "oxide":
        metadata["oxide_thickness_angstrom"] = value
        metadata["gate_oxide_thickness_angstrom"] = value
    elif target_layer == "gate":
        metadata["gate_thickness_angstrom"] = value
        if target_material == "Al" or "aluminum_gate_thickness_angstrom" in metadata:
            metadata["aluminum_gate_thickness_angstrom"] = value
    elif target_layer == "channel":
        metadata["channel_thickness_angstrom"] = value
        if target_material == "Si" or "silicon_slab_thickness_angstrom" in metadata:
            metadata["silicon_slab_thickness_angstrom"] = value


def _apply_imported_patch(spec: ModelSpec, patch: SemanticPatch, diff: list[str]) -> None:
    """应用导入结构补丁。"""
    for operation in patch.operations:
        if operation.operation == "set_metadata":
            _apply_metadata_patch(spec, operation, diff)
        elif operation.operation == "set_forcite_optimization":
            spec.simulation = _forcite_from_operation(operation)
            diff.append("set_forcite_optimization")
        elif operation.operation == "set_castep_energy":
            spec.simulation = _castep_from_operation(operation)
            diff.append("set_castep_energy")
        else:
            raise ValueError(f"{operation.operation} 对导入结构不支持")


def _apply_metadata_patch(spec: ModelSpec, operation: SemanticPatchOperation, diff: list[str]) -> None:
    """Merge metadata updates into the model spec."""

    if not operation.metadata_updates:
        raise ValueError("set_metadata 需要 metadata_updates")
    spec.metadata = {**dict(spec.metadata or {}), **dict(operation.metadata_updates)}
    keys = ",".join(sorted(str(key) for key in operation.metadata_updates))
    diff.append(f"set_metadata {keys}")


def _forcite_from_operation(operation: SemanticPatchOperation) -> ForciteOptimizationSpec:
    """从操作创建 Forcite 优化规格。"""
    return ForciteOptimizationSpec(
        forcefield=operation.forcefield or "COMPASS",
        quality=ForciteQuality(operation.quality or "Medium"),
        charge_assignment=operation.charge_assignment or "Forcefield assigned",
        max_iterations=operation.max_iterations or 500,
        convergence=ForciteConvergence(operation.convergence or "Medium"),
    )


def _castep_from_operation(operation: SemanticPatchOperation) -> CastepEnergySpec:
    """从操作创建 CASTEP 能量规格。"""
    return CastepEnergySpec(
        task=operation.task or "Energy",
        functional=operation.functional or "PBE",
        quality=operation.quality or "Medium",
        cutoff_energy_ev=operation.cutoff_energy_ev,
        kpoint_separation=operation.kpoint_separation,
        kpoints=operation.kpoints,
    )


def _required_atom_id(operation: SemanticPatchOperation) -> str:
    """获取必需的原子 ID。"""
    atom_id = operation.atom_id or operation.id
    if not atom_id:
        raise ValueError(f"{operation.operation} 需要 atom_id")
    return atom_id


def _next_atom_id(element: str, atoms: list[AtomSpec]) -> str:
    """生成下一个原子 ID。"""
    used = {atom.id for atom in atoms}
    index = 1
    while f"{element}{index}" in used:
        index += 1
    return f"{element}{index}"


def _next_crystal_atom_id(element: str, atoms: list[BasisAtomSpec]) -> str:
    """生成晶体基原子的下一个 ID。"""

    used = {atom.id for atom in atoms}
    index = 1
    while f"{element}{index}" in used:
        index += 1
    return f"{element}{index}"


def _target_crystal_atom(operation: SemanticPatchOperation, atoms: list[BasisAtomSpec], lattice: LatticeSpec) -> str:
    """获取目标晶体原子 ID。"""
    if operation.atom_id or operation.id:
        atom_id = operation.atom_id or operation.id or ""
        if not any(atom.id == atom_id for atom in atoms):
            raise ValueError(f"晶体不包含目标原子: {atom_id}")
        return atom_id
    if operation.nearest_cartesian_angstrom is None:
        raise ValueError(f"{operation.operation} 需要 atom_id 或 nearest_cartesian_angstrom")
    target = operation.nearest_cartesian_angstrom
    best: tuple[float, str] | None = None
    for atom in atoms:
        dx = atom.fractional.x * lattice.a - target.x
        dy = atom.fractional.y * lattice.b - target.y
        dz = atom.fractional.z * lattice.c - target.z
        distance2 = dx * dx + dy * dy + dz * dz
        if best is None or distance2 < best[0]:
            best = (distance2, atom.id)
    if best is None:
        raise ValueError("晶体不包含原子")
    return best[1]
