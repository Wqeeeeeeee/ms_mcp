"""Static analysis for reviewed Materials Studio Copy Script evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Pattern


MAX_COPY_SCRIPT_CHARACTERS = 100_000


_EXTERNAL_EFFECT_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("shell_system", re.compile(r"\bsystem\s*(?:\(|\s)", re.IGNORECASE)),
    ("shell_exec", re.compile(r"\bexec\s*(?:\(|\s)", re.IGNORECASE)),
    ("shell_backticks", re.compile(r"`[^`\r\n]+`")),
    ("shell_qx", re.compile(r"\bqx\s*[/({\[]", re.IGNORECASE)),
    ("process_fork", re.compile(r"\bfork\s*(?:\(|;|\s)", re.IGNORECASE)),
    ("perl_file_open", re.compile(r"\bopen\s*(?:\(|\s)", re.IGNORECASE)),
    ("filesystem_delete", re.compile(r"\b(?:unlink|rmdir)\b", re.IGNORECASE)),
    ("filesystem_tree_api", re.compile(r"\bFile::Path\b", re.IGNORECASE)),
    (
        "network_or_process_api",
        re.compile(
            r"\b(?:LWP::|HTTP::|IO::Socket|Net::|Win32::Process|RunMatScript|RunMatServer)",
            re.IGNORECASE,
        ),
    ),
    (
        "document_import_export",
        re.compile(
            r"\bDocuments\s*->\s*(?:Import|Export)\b|->\s*Export\s*\(",
            re.IGNORECASE,
        ),
    ),
    (
        "calculation_or_module_run",
        re.compile(r"\bModules\s*->|->\s*(?:Run|GeometryOptimization|Dynamics|Energy)\s*\(", re.IGNORECASE),
    ),
)


_STRUCTURE_MUTATION_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    (
        "document_or_structure_create",
        re.compile(
            r"\bDocuments\s*->\s*New\b|->\s*(?:CreateAtom|CreateBond|CreateFragment)\s*\(",
            re.IGNORECASE,
        ),
    ),
    (
        "object_delete_or_insert",
        re.compile(r"->\s*(?:Delete|Remove|Insert)\s*\(", re.IGNORECASE),
    ),
    (
        "atomic_coordinate_or_element_assignment",
        re.compile(
            r"->\s*(?:X|Y|Z|XYZ|FractionalXYZ|ElementSymbol|ChemicalElement)\s*=",
            re.IGNORECASE,
        ),
    ),
    (
        "lattice_assignment",
        re.compile(
            r"->\s*(?:Lattice3D|LengthA|LengthB|LengthC|AngleAlpha|AngleBeta|AngleGamma|SpaceGroup|SymmetrySystem)\s*=",
            re.IGNORECASE,
        ),
    ),
    (
        "cell_or_crystal_build",
        re.compile(r"->\s*(?:ChangeCell|BuildCrystal|SetCell)\s*\(", re.IGNORECASE),
    ),
)


_VIEW_ACTION_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("native_view_command", re.compile(r"\bcmdViewer3D[A-Za-z0-9_]+\b")),
    (
        "view_api_term",
        re.compile(
            r"\b(?:Camera|Viewer|View|Orientation|Projection|ResetView|Recenter|FitToView|ViewOnto|ViewAcross|Rotate)\b",
            re.IGNORECASE,
        ),
    ),
)


def _pattern_findings(
    script: str,
    patterns: tuple[tuple[str, Pattern[str]], ...],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for code, pattern in patterns:
        matches = list(pattern.finditer(script))
        if not matches:
            continue
        line_numbers = sorted(
            {
                script.count("\n", 0, match.start()) + 1
                for match in matches
            }
        )
        findings.append(
            {
                "code": code,
                "count": len(matches),
                "line_numbers": line_numbers,
            }
        )
    return findings


def analyze_reviewed_copy_script(script: str) -> dict[str, object]:
    """Analyze inert Copy Script text without executing or rewriting it."""

    if not isinstance(script, str):
        raise ValueError("copy script text must be a string")
    if not script.strip():
        raise ValueError("copy script text must not be empty")
    if len(script) > MAX_COPY_SCRIPT_CHARACTERS:
        raise ValueError(
            f"copy script text must be at most {MAX_COPY_SCRIPT_CHARACTERS} characters"
        )
    invalid_control_codes = sorted(
        {
            ord(char)
            for char in script
            if ord(char) < 32 and char not in "\t\r\n"
        }
    )
    if invalid_control_codes:
        rendered = ", ".join(f"0x{code:02x}" for code in invalid_control_codes)
        raise ValueError(f"copy script contains unsupported control characters: {rendered}")

    encoded = script.encode("utf-8")
    external_effect_findings = _pattern_findings(script, _EXTERNAL_EFFECT_PATTERNS)
    structure_mutation_findings = _pattern_findings(
        script,
        _STRUCTURE_MUTATION_PATTERNS,
    )
    view_action_signals = _pattern_findings(script, _VIEW_ACTION_PATTERNS)
    safe_for_view_evidence = not (
        external_effect_findings or structure_mutation_findings
    )
    return {
        "schema_version": 1,
        "script_sha256": hashlib.sha256(encoded).hexdigest(),
        "character_count": len(script),
        "byte_count_utf8": len(encoded),
        "line_count": script.count("\n") + 1,
        "external_effect_findings": external_effect_findings,
        "structure_mutation_findings": structure_mutation_findings,
        "view_action_signals": view_action_signals,
        "view_action_signal_detected": bool(view_action_signals),
        "safe_for_view_evidence": safe_for_view_evidence,
        "execution_allowed": False,
        "analysis_scope": "static_evidence_only_not_semantic_camera_proof",
    }
