"""Translate CASTEP specs to MaterialsScript Perl snippets."""

from __future__ import annotations

from material_studio_mcp_server.castep_materialscript import render_castep_run_snippet
from material_studio_mcp_server.specs.castep import CastepEnergySpec


def render_castep_energy_snippet(spec: CastepEnergySpec) -> str:
    """Render a compatibility-named, task-aware CASTEP snippet."""

    return "\n" + render_castep_run_snippet(spec) + "\n"
