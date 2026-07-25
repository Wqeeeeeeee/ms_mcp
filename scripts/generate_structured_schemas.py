"""Regenerate the checked-in JSON Schemas from the strict Pydantic models."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from material_studio_mcp_server.specs.castep import CastepEnergySpec
from material_studio_mcp_server.specs.crystal import CrystalSpec
from material_studio_mcp_server.specs.dmol3 import DMol3GeometryOptimizationSpec
from material_studio_mcp_server.specs.forcite import (
    ForciteDynamicsSpec,
    ForciteOptimizationSpec,
)
from material_studio_mcp_server.specs.molecule import MoleculeSpec
from material_studio_mcp_server.specs.patch import SemanticPatch
from material_studio_mcp_server.specs.project import ModelSpec


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _with_dialect(schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "$schema": SCHEMA_DIALECT}


def generated_schemas() -> dict[str, dict[str, Any]]:
    return {
        "model_spec.schema.json": _with_dialect(ModelSpec.model_json_schema()),
        "molecule_spec.schema.json": _with_dialect(
            MoleculeSpec.model_json_schema()
        ),
        "crystal_spec.schema.json": _with_dialect(
            CrystalSpec.model_json_schema()
        ),
        "forcite_spec.schema.json": _with_dialect(
            TypeAdapter(
                ForciteOptimizationSpec | ForciteDynamicsSpec
            ).json_schema()
        ),
        "castep_spec.schema.json": _with_dialect(
            CastepEnergySpec.model_json_schema()
        ),
        "dmol3_spec.schema.json": _with_dialect(
            DMol3GeometryOptimizationSpec.model_json_schema()
        ),
        "patch_spec.schema.json": _with_dialect(
            SemanticPatch.model_json_schema()
        ),
    }


def main() -> int:
    output_directory = (
        REPOSITORY_ROOT / "src" / "material_studio_mcp_server" / "schemas"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, schema in generated_schemas().items():
        (output_directory / filename).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
