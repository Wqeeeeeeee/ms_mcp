"""Pure preview planning for the fixed CIF import/export transaction."""

from __future__ import annotations

import os

from material_studio_mcp_server.runner import perl_string
from material_studio_mcp_server.scripts import (
    import_export_script,
    validate_materialscript,
)

from .comparison import validate_fixed_candidate_cif
from .contracts import (
    ExternalArtifactDigest,
    RoundtripPlan,
    RoundtripRequest,
    ScriptSafetyReceipt,
)
from .errors import RoundtripError, RoundtripErrorCode
from .secure_io import (
    canonical_json_bytes,
    ensure_inside,
    native_text_bytes,
    resolve_existing_directory,
    sha256_bytes,
    sha256_text,
    stable_read_file,
)


OUTPUT_NAME = "roundtrip_output.cif"
RECEIPT_NAME = "result_receipt.json"
SCRIPT_NAME = "roundtrip.pl"

_FORBIDDEN_SCRIPT_MARKERS = (
    "system(",
    "exec(",
    "qx(",
    "`",
    "unlink(",
    "rmdir(",
    "removeitem",
    "http://",
    "https://",
    "ftp://",
    "runmatscript",
    "runmatserver",
    "matstudio.exe",
    "modules->castep",
    "modules->forcite",
    "documents->new",
    "documents->delete",
)


def _request_digest(request: RoundtripRequest) -> str:
    return sha256_bytes(canonical_json_bytes(request.model_dump(mode="json")))


def _validate_paths(request: RoundtripRequest):
    input_snapshot = stable_read_file(
        request.candidate.structure_path,
        expected_sha256=request.candidate.expected_structure_sha256,
        code=RoundtripErrorCode.INPUT_IDENTITY_MISMATCH,
    )
    if input_snapshot.path.suffix.casefold() != ".cif":
        raise RoundtripError(
            RoundtripErrorCode.UNSUPPORTED_CANDIDATE,
            "The fixed round-trip adapter accepts CIF input only.",
        )
    output_root = resolve_existing_directory(request.output_root)
    run_root = output_root / request.run_id
    if os.path.lexists(run_root):
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_ALREADY_EXISTS,
            "The unique run root already exists.",
        )
    output_path = run_root / OUTPUT_NAME
    ensure_inside(output_root, run_root)
    ensure_inside(run_root, output_path)
    try:
        input_snapshot.path.relative_to(run_root)
    except ValueError:
        pass
    else:
        raise RoundtripError(
            RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
            "The immutable input cannot be inside the fresh run root.",
        )
    return input_snapshot, output_root, run_root, output_path


def _script_safety(source, output, script: str) -> ScriptSafetyReceipt:
    repeated = import_export_script(source, output)
    validation = validate_materialscript(script)
    source_literal = perl_string(source)
    output_literal = perl_string(output)
    source_once = script.count(source_literal) == 1
    output_once = script.count(output_literal) == 1
    forbidden_absent = not any(
        marker in script.casefold() for marker in _FORBIDDEN_SCRIPT_MARKERS
    )
    exact_template = script == repeated
    if not (
        exact_template
        and validation.get("valid") is True
        and source_once
        and output_once
        and forbidden_absent
    ):
        raise RoundtripError(
            RoundtripErrorCode.SCRIPT_SAFETY_FAILED,
            "The deterministic import/export script failed safety validation.",
        )
    source_bytes = script.encode("utf-8")
    artifact_bytes = native_text_bytes(script)
    return ScriptSafetyReceipt(
        deterministic=True,
        exact_reviewed_template=True,
        materialscript_validation_passed=True,
        source_bound_once=True,
        output_bound_once=True,
        forbidden_operations_absent=True,
        script_source_sha256=sha256_bytes(source_bytes),
        script_artifact_sha256=sha256_bytes(artifact_bytes),
    )


def plan_roundtrip(request: RoundtripRequest) -> RoundtripPlan:
    """Validate and plan without writing, running, or probing the GUI."""

    if not isinstance(request, RoundtripRequest):
        raise TypeError("request must be RoundtripRequest")
    input_snapshot, _output_root, run_root, output_path = _validate_paths(request)
    candidate_validation = validate_fixed_candidate_cif(
        input_snapshot.payload,
        expected_sha256=input_snapshot.sha256,
    )
    script = import_export_script(input_snapshot.path, output_path)
    script_safety = _script_safety(input_snapshot.path, output_path, script)
    input_artifact = ExternalArtifactDigest(
        role="input_cif",
        location_sha256=sha256_text(str(input_snapshot.path)),
        sha256=input_snapshot.sha256,
        byte_count=input_snapshot.byte_count,
    )
    return RoundtripPlan(
        execution_mode=request.execution_mode,
        request_digest_sha256=_request_digest(request),
        input_artifact=input_artifact,
        candidate_validation=candidate_validation,
        run_root=run_root,
        output_path=output_path,
        output_confined=True,
        output_absent=True,
        script_text=script,
        script_safety=script_safety,
    )


def plan_digest(plan: RoundtripPlan) -> str:
    if not isinstance(plan, RoundtripPlan):
        raise TypeError("plan must be RoundtripPlan")
    return sha256_bytes(canonical_json_bytes(plan.model_dump(mode="json")))


__all__ = [
    "OUTPUT_NAME",
    "RECEIPT_NAME",
    "SCRIPT_NAME",
    "plan_digest",
    "plan_roundtrip",
]
