"""Translate strict DMol3 specs to MaterialsScript Perl."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from material_studio_mcp_server.dmol3_materialscript import (
    DMOL3_GEOMETRY_RESULT_SCHEMA,
    DMOL3_MATERIALSCRIPT_CONTRACT,
    DMOL3_REVIEWED_RESULT_KEYS,
    build_dmol3_materialscript_plan,
    render_dmol3_run_snippet,
)
from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.specs.dmol3 import (
    DMol3GeometryOptimizationSpec,
)
from material_studio_mcp_server.specs.molecule import MoleculeSpec

from .common import header, tagged_json_print


def render_dmol3_task_script(
    spec: DMol3GeometryOptimizationSpec,
    input_file: str | Path,
    *,
    project_id: str,
    revision: int,
) -> str:
    """Render a standalone calculation companion for preview/review only."""

    plan = build_dmol3_materialscript_plan(spec)
    payload = {
        "project_id": project_id,
        "revision": revision,
        "script_kind": "dmol3_task",
        "module": "DMol3",
        "task": plan.task,
        "input_structure": str(input_file),
        "dispatch": plan.summary(),
        "execution_policy": "preview_only",
    }
    return (
        header()
        + f"my $input = {perl_string(input_file)};\n"
        + "my $doc = Documents->Import($input);\n"
        + render_dmol3_run_snippet(spec)
        + "\n"
        + tagged_json_print(payload)
    )


def render_dmol3_geometry_optimization_script(
    spec: DMol3GeometryOptimizationSpec,
    input_file: str | Path,
    output_structure: str | Path,
    output_report: str | Path,
    *,
    project_id: str,
    base_revision: int,
    source_molecule: MoleculeSpec,
) -> str:
    """Render a result-bearing DMol3 geometry-optimization script.

    Temporary deterministic atom names bind the output coordinates to source
    atom IDs. The original document atom names are restored before export.
    Any atom-count, token, or element mismatch aborts the script.
    """

    plan = build_dmol3_materialscript_plan(spec)
    source_atom_ids = [atom.id for atom in source_molecule.atoms]
    source_atom_elements = [atom.element for atom in source_molecule.atoms]
    static_payload = {
        "schema_version": DMOL3_GEOMETRY_RESULT_SCHEMA,
        "project_id": project_id,
        "base_revision": base_revision,
        "script_kind": "dmol3_geometry_optimization",
        "module": "DMol3",
        "task": plan.task,
        "input_structure": str(input_file),
        "output_structure": str(output_structure),
        "output_report": str(output_report),
        "materials_studio_api_contract": DMOL3_MATERIALSCRIPT_CONTRACT,
        "result_keys": list(DMOL3_REVIEWED_RESULT_KEYS),
        "energy_evolution_charts_requested": (
            spec.create_energy_evolution_chart.value == "Yes"
        ),
    }
    payload_prefix = json.dumps(
        static_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:-1]
    perl_atom_ids = ", ".join(perl_string(value) for value in source_atom_ids)
    perl_elements = ", ".join(
        perl_string(value) for value in source_atom_elements
    )
    return (
        header()
        + _dmol3_result_helpers()
        + f"my $input = {perl_string(input_file)};\n"
        + f"my $output_structure = {perl_string(output_structure)};\n"
        + f"my $output_report = {perl_string(output_report)};\n"
        + "my $doc = Documents->Import($input);\n"
        + f"my @source_atom_ids = ({perl_atom_ids});\n"
        + f"my @source_atom_elements = ({perl_elements});\n"
        + "my @source_atom_names;\n"
        + "my $input_atoms = $doc->Atoms;\n"
        + 'die "DMol3 source atom count mismatch" '
        + "unless $input_atoms->Count == scalar(@source_atom_ids);\n"
        + "my $source_atom_index = 0;\n"
        + "foreach my $atom (@$input_atoms) {\n"
        + "    die \"DMol3 source atom element mismatch\" "
        + "unless $atom->ElementSymbol eq "
        + "$source_atom_elements[$source_atom_index];\n"
        + "    push @source_atom_names, "
        + "(defined($atom->Name) ? $atom->Name : \"\");\n"
        + "    $atom->Name = sprintf(\"MSMCPAtom%06d\", "
        + "$source_atom_index + 1);\n"
        + "    ++$source_atom_index;\n"
        + "}\n"
        + render_dmol3_run_snippet(spec)
        + "\n"
        + "my $optimized_structure = $dmol3_results->Structure;\n"
        + "my $optimized_atoms = $optimized_structure->Atoms;\n"
        + 'die "DMol3 optimized atom count mismatch" '
        + "unless $optimized_atoms->Count == scalar(@source_atom_ids);\n"
        + "my %optimized_by_token;\n"
        + "foreach my $atom (@$optimized_atoms) {\n"
        + "    my $token = $atom->Name;\n"
        + '    die "DMol3 optimized atom token missing" '
        + "unless defined($token) && length($token);\n"
        + '    die "DMol3 optimized atom token duplicated" '
        + "if exists($optimized_by_token{$token});\n"
        + "    $optimized_by_token{$token} = $atom;\n"
        + "}\n"
        + 'my $optimized_atoms_json = "[";\n'
        + "for (my $index = 0; $index < scalar(@source_atom_ids); ++$index) {\n"
        + "    my $token = sprintf(\"MSMCPAtom%06d\", $index + 1);\n"
        + '    die "DMol3 optimized atom identity mismatch" '
        + "unless exists($optimized_by_token{$token});\n"
        + "    my $atom = $optimized_by_token{$token};\n"
        + '    die "DMol3 optimized atom element mismatch" '
        + "unless $atom->ElementSymbol eq $source_atom_elements[$index];\n"
        + "    my $xyz = $atom->XYZ;\n"
        + '    $optimized_atoms_json .= "," if $index > 0;\n'
        + '    $optimized_atoms_json .= "{\\\"id\\\":\\\"" '
        + ". json_escape($source_atom_ids[$index]) "
        + '. "\\\",\\\"element\\\":\\\"" '
        + ". json_escape($source_atom_elements[$index]) "
        + '. "\\\",\\\"xyz_angstrom\\\":{\\\"x\\\":" '
        + ". (0 + $xyz->X) . \",\\\"y\\\":\" . (0 + $xyz->Y) "
        + '. ",\\\"z\\\":" . (0 + $xyz->Z) . "}}";\n'
        + "}\n"
        + '$optimized_atoms_json .= "]";\n'
        + "$optimized_structure->Export($output_structure, "
        + 'Settings(Version => "2020"));\n'
        + "for (my $index = 0; $index < scalar(@source_atom_ids); ++$index) {\n"
        + "    my $token = sprintf(\"MSMCPAtom%06d\", $index + 1);\n"
        + "    $optimized_by_token{$token}->Name = $source_atom_names[$index];\n"
        + "}\n"
        + "my $dmol3_report = $dmol3_results->Report;\n"
        + "$dmol3_report->Export($output_report);\n"
        + "my $converged_json = $dmol3_results->Converged "
        + '? "true" : "false";\n'
        + "my $total_energy_json = defined($dmol3_results->TotalEnergy) "
        + '? (0 + $dmol3_results->TotalEnergy) : "null";\n'
        + 'my $energy_chart_json = optional_document_name_json('
        + '$dmol3_results, "EnergyChart");\n'
        + 'my $convergence_chart_json = optional_document_name_json('
        + '$dmol3_results, "ConvergenceChart");\n'
        + 'print "__MS_MCP_JSON_START__\\n";\n'
        + f"print {perl_string(payload_prefix)};\n"
        + 'print ",\\\"converged\\\":" . $converged_json;\n'
        + 'print ",\\\"total_energy_kcal_per_mol\\\":" '
        + ". $total_energy_json;\n"
        + 'print ",\\\"optimized_atoms\\\":" . $optimized_atoms_json;\n'
        + 'print ",\\\"result_document_names\\\":{";\n'
        + 'print "\\\"EnergyChart\\\":" . $energy_chart_json . ",";\n'
        + 'print "\\\"ConvergenceChart\\\":" '
        + '. $convergence_chart_json . "}}\\n";\n'
        + 'print "__MS_MCP_JSON_END__\\n";\n'
    )


def dmol3_calculation_preview_metadata(
    spec: DMol3GeometryOptimizationSpec,
    input_file: str | Path,
    *,
    project_id: str | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    """Return a fail-closed non-execution receipt for a DMol3 companion."""

    plan = build_dmol3_materialscript_plan(spec)
    preview_payload = {
        key: value
        for key, value in {
            "project_id": project_id,
            "expected_revision": revision,
            "execution_mode": "preview",
            "open_in_gui": False,
            "take_snapshot": False,
            "export_view_audit": True,
            "response_mode": "compact",
        }.items()
        if value is not None
    }
    execute_payload = dict(preview_payload)
    execute_payload["execution_mode"] = "execute"
    return {
        "available": True,
        "kind": "dmol3_geometry_optimization",
        "module": "DMol3",
        "task": plan.task,
        "project_id": project_id,
        "source_revision": revision,
        "input_structure": str(input_file),
        "dispatch": plan.summary(),
        "execution_policy": "preview_only",
        "separate_execution_policy": "explicit_execute_only",
        "execution_supported_by_structured_workflow": True,
        "execution_supported_by_separate_tool": True,
        "execution_tool": "material_studio_dmol3_relax_current",
        "execute_requires_user_confirmation": True,
        "execution_handoff": {
            "status": "explicit_execution_available",
            "task": plan.task,
            "project_id": project_id,
            "source_revision": revision,
            "execution_supported": True,
            "execution_tool": "material_studio_dmol3_relax_current",
            "preview_action": {
                "recommended_tool": "material_studio_dmol3_relax_current",
                "recommended_action": "preview_current_revision_dmol3_relaxation",
                "payload_hint": preview_payload,
                "payload_hint_is_directly_callable": (
                    project_id is not None and revision is not None
                ),
                "needs_user_confirmation": False,
                "safe_to_call_without_confirmation": True,
            },
            "execute_action": {
                "recommended_tool": "material_studio_dmol3_relax_current",
                "recommended_action": (
                    "execute_current_revision_dmol3_relaxation_after_explicit_confirmation"
                ),
                "payload_hint": execute_payload,
                "payload_hint_is_directly_callable": (
                    project_id is not None and revision is not None
                ),
                "needs_user_confirmation": True,
                "safe_to_call_without_confirmation": False,
            },
            "unsupported_reason": None,
        },
        "structure_materialization_executes_calculation": False,
        "requires_explicit_separate_execution": True,
        "calculation_executed": False,
        "calculation_result_available": False,
    }


def _dmol3_result_helpers() -> str:
    return r'''sub json_escape {
    my ($value) = @_;
    $value = "" unless defined $value;
    $value =~ s/\\/\\\\/g;
    $value =~ s/"/\\"/g;
    $value =~ s/\r/\\r/g;
    $value =~ s/\n/\\n/g;
    $value =~ s/\t/\\t/g;
    return $value;
}

sub optional_document_name_json {
    my ($results, $key) = @_;
    my $document;
    my $available = eval { $document = $results->$key; 1; };
    return "null" unless $available && defined $document;
    my $name;
    my $named = eval { $name = $document->Name; 1; };
    return "null" unless $named && defined $name && length($name);
    return '"' . json_escape($name) . '"';
}

'''
