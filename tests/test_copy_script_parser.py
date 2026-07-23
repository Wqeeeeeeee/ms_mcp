from __future__ import annotations

import hashlib

import pytest

from material_studio_mcp_server.parsers.copy_script import (
    MAX_COPY_SCRIPT_CHARACTERS,
    analyze_reviewed_copy_script,
)


def test_analyze_reviewed_copy_script_accepts_inert_view_evidence() -> None:
    script = (
        "use MaterialsScript qw(:all);\n"
        'my $doc = $Documents{"model.xsd"};\n'
        "$doc->Views->ActiveView->Camera->ResetView();\n"
    )

    result = analyze_reviewed_copy_script(script)

    assert result["safe_for_view_evidence"] is True
    assert result["execution_allowed"] is False
    assert result["view_action_signal_detected"] is True
    assert result["external_effect_findings"] == []
    assert result["structure_mutation_findings"] == []
    assert result["script_sha256"] == hashlib.sha256(
        script.encode("utf-8")
    ).hexdigest()


def test_analyze_reviewed_copy_script_blocks_external_effects_and_structure_changes() -> None:
    script = (
        "use MaterialsScript qw(:all);\n"
        'system("cmd /c whoami");\n'
        'open(my $fh, ">", "changed.txt");\n'
        'exec("external-tool");\n'
        'my $doc = Documents->New("changed.xsd");\n'
        '$doc->CreateAtom("Si", Point(X => 0, Y => 0, Z => 0));\n'
        '$doc->Export("changed.cif");\n'
    )

    result = analyze_reviewed_copy_script(script)

    assert result["safe_for_view_evidence"] is False
    assert {item["code"] for item in result["external_effect_findings"]} == {
        "shell_system",
        "shell_exec",
        "perl_file_open",
        "document_import_export",
    }
    assert {item["code"] for item in result["structure_mutation_findings"]} == {
        "document_or_structure_create",
    }


@pytest.mark.parametrize(
    "script, message",
    [
        ("   ", "must not be empty"),
        ("View\x00Reset", "unsupported control characters"),
        ("V" * (MAX_COPY_SCRIPT_CHARACTERS + 1), "must be at most"),
    ],
    ids=["blank", "control-character", "too-long"],
)
def test_analyze_reviewed_copy_script_rejects_invalid_text(
    script: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        analyze_reviewed_copy_script(script)
