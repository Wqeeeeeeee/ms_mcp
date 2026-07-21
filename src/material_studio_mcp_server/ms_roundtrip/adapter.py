"""Execute one confined, preview-first Materials Studio CIF round trip."""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from material_studio_mcp_server.runner import MaterialStudioRunner, ScriptRunResult

from .comparison import compare_roundtrip_cif_bytes
from .contracts import (
    ExternalArtifactDigest,
    GuiInventoryReceipt,
    RoundtripExecutionResult,
    RoundtripPlan,
    RoundtripReceipt,
    RoundtripRequest,
    RunArtifactDigest,
    RunnerExecutionReceipt,
    RunnerIdentityReceipt,
    TaggedSummaryReceipt,
)
from .errors import RoundtripError, RoundtripErrorCode
from .gui_inventory import (
    ReadOnlyGuiBackend,
    capture_gui_inventory,
    compare_gui_inventories,
    default_gui_backend,
)
from .planning import RECEIPT_NAME, SCRIPT_NAME, plan_digest, plan_roundtrip
from .secure_io import (
    MAX_RUNNER_ARTIFACT_BYTES,
    atomic_write_json,
    canonical_json_bytes,
    digest_run_artifact,
    ensure_inside,
    native_text_bytes,
    relative_run_path,
    resolve_existing_directory,
    sha256_bytes,
    sha256_text,
    snapshot_unchanged,
    stable_read_file,
)


class RunnerLike(Protocol):
    config: object

    def run_script(
        self,
        script: str,
        *,
        args: list[str] | None = None,
        working_dir: str | Path | None = None,
        timeout_seconds: int | None = None,
        job_prefix: str = "msjob",
        keep_script_name: str = "script.pl",
    ) -> ScriptRunResult: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _runner_path(runner: RunnerLike) -> Path:
    config = getattr(runner, "config", None)
    path = getattr(config, "runner", None)
    if not isinstance(path, Path):
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_IDENTITY_INVALID,
            "The runner does not expose a bound executable path.",
        )
    return path


def _inspect_runner(
    runner: RunnerLike,
    *,
    real_environment: bool,
):
    path = _runner_path(runner)
    snapshot = stable_read_file(
        path,
        max_bytes=MAX_RUNNER_ARTIFACT_BYTES,
        require_single_link=False,
        code=RoundtripErrorCode.RUNNER_IDENTITY_INVALID,
    )
    config = runner.config
    exact_name = snapshot.path.name.casefold() == "runmatscript.bat"
    install_match = any(
        part.casefold()
        in {"materials studio 20.1", "materials studio 20.1 x64 server"}
        for part in snapshot.path.parts
    )
    template_absent = not bool(os.environ.get("MATERIAL_STUDIO_COMMAND_TEMPLATE"))
    extra_args = getattr(config, "extra_runner_args", ())
    extra_args_absent = isinstance(extra_args, tuple) and not extra_args
    if real_environment:
        valid = (
            isinstance(runner, MaterialStudioRunner)
            and exact_name
            and install_match
            and template_absent
            and extra_args_absent
        )
        identity = "materials_studio_20.1_runmatscript.bat"
    else:
        valid = callable(getattr(runner, "run_script", None))
        identity = "offline_fake_runner"
    if not valid:
        raise RoundtripError(
            RoundtripErrorCode.RUNNER_IDENTITY_INVALID,
            "Runner identity does not satisfy the selected environment contract.",
        )
    artifact = ExternalArtifactDigest(
        role="runner_executable",
        location_sha256=sha256_text(str(snapshot.path)),
        sha256=snapshot.sha256,
        byte_count=snapshot.byte_count,
    )
    receipt = RunnerIdentityReceipt(
        runner_identity=identity,
        real_environment=real_environment,
        executable=artifact,
        exact_runmatscript_name=exact_name,
        materials_studio_20_1_install=install_match,
        command_template_override_absent=template_absent,
        extra_runner_args_absent=extra_args_absent,
        identity_valid=True,
    )
    return receipt, snapshot


def _empty_gui_inventory() -> GuiInventoryReceipt:
    return GuiInventoryReceipt(
        process_count=0,
        window_count=0,
        usable_single_window=False,
        process_identity_sha256=None,
        window_identity_sha256=None,
        window_title_sha256=None,
        window_visible=None,
        window_minimized=None,
        window_foreground=None,
    )


def _runner_artifacts(
    result: ScriptRunResult,
    *,
    run_root: Path,
    expected_script_sha256: str,
) -> tuple[tuple[RunArtifactDigest, ...], bool, bool]:
    try:
        job_dir = resolve_existing_directory(result.job_dir)
        ensure_inside(run_root, job_dir)
        paths = {path.expanduser().resolve(strict=True) for path in result.created_files}
        paths.add(result.script_path.expanduser().resolve(strict=True))
        artifacts: dict[str, RunArtifactDigest] = {}
        script_matches = False
        for path in sorted(paths, key=lambda value: str(value).casefold()):
            relative = relative_run_path(run_root, path)
            is_script = path == result.script_path.expanduser().resolve(strict=True)
            artifact = digest_run_artifact(
                run_root=run_root,
                path=path,
                role="script" if is_script else "runner_artifact",
                expected_sha256=expected_script_sha256 if is_script else None,
            )
            artifacts[relative] = artifact
            if is_script:
                script_matches = artifact.sha256 == expected_script_sha256
        return tuple(artifacts[key] for key in sorted(artifacts)), True, script_matches
    except (OSError, RoundtripError):
        return (), False, False


def _runner_execution_receipt(
    result: ScriptRunResult | None,
    *,
    artifacts: tuple[RunArtifactDigest, ...],
    all_confined: bool,
) -> RunnerExecutionReceipt:
    if result is None:
        return RunnerExecutionReceipt(
            success=False,
            timed_out=False,
            return_code=None,
            duration_seconds=0.0,
            command_sha256=None,
            stdout_sha256=None,
            stderr_sha256=None,
            materials_output_sha256=None,
            materials_log_sha256=None,
            artifacts=artifacts,
            all_artifacts_confined=all_confined,
        )
    duration = float(result.duration_seconds)
    if not math.isfinite(duration) or duration < 0.0:
        duration = 0.0
    return RunnerExecutionReceipt(
        success=bool(result.success),
        timed_out=bool(result.timed_out),
        return_code=int(result.return_code),
        duration_seconds=duration,
        command_sha256=sha256_bytes(canonical_json_bytes(result.command)),
        stdout_sha256=sha256_text(result.stdout),
        stderr_sha256=sha256_text(result.stderr),
        materials_output_sha256=sha256_text(result.materials_output),
        materials_log_sha256=sha256_text(result.materials_log),
        artifacts=artifacts,
        all_artifacts_confined=all_confined,
    )


def _tagged_summary(
    result: ScriptRunResult,
    *,
    input_path: Path,
    output_path: Path,
) -> TaggedSummaryReceipt | None:
    parsed = result.parsed_json
    if not isinstance(parsed, dict) or set(parsed) != {
        "source",
        "output",
        "document_name",
    }:
        return None
    source = parsed.get("source")
    output = parsed.get("output")
    document_name = parsed.get("document_name")
    if (
        type(source) is not str
        or type(output) is not str
        or type(document_name) is not str
        or not document_name
    ):
        return None
    source_matches = source == str(input_path)
    output_matches = output == str(output_path)
    if not source_matches or not output_matches:
        return None
    return TaggedSummaryReceipt(
        source_path_sha256=sha256_text(source),
        output_path_sha256=sha256_text(output),
        document_name_sha256=sha256_text(document_name),
        source_matches_input=True,
        output_matches_fresh_output=True,
        tagged_json_matches_input_output=True,
    )


class MaterialsStudioRoundtripAdapter:
    """Private dependency-injected adapter; no public MCP registration."""

    def __init__(
        self,
        *,
        runner: RunnerLike | None = None,
        gui_backend: ReadOnlyGuiBackend | None = None,
        real_environment: bool | None = None,
    ) -> None:
        self._runner = runner
        self._gui_backend = gui_backend
        self._real_environment = real_environment

    def run(
        self,
        request: RoundtripRequest,
    ) -> RoundtripPlan | RoundtripExecutionResult:
        plan = plan_roundtrip(request)
        if request.execution_mode == "preview":
            return plan
        if request.execution_mode != "execute":
            raise RoundtripError(
                RoundtripErrorCode.EXECUTE_MODE_REQUIRED,
                "Execution requires the exact execute literal.",
            )
        return self._execute(request, plan)

    def _execute(
        self,
        request: RoundtripRequest,
        plan: RoundtripPlan,
    ) -> RoundtripExecutionResult:
        real_environment = (
            self._real_environment
            if self._real_environment is not None
            else self._runner is None and self._gui_backend is None
        )
        if type(real_environment) is not bool:
            raise TypeError("real_environment must be bool")
        runner: RunnerLike = self._runner or MaterialStudioRunner()
        gui_backend = self._gui_backend or default_gui_backend()
        runner_identity, runner_before = _inspect_runner(
            runner,
            real_environment=real_environment,
        )
        gui_before = capture_gui_inventory(gui_backend)
        if not gui_before.receipt.usable_single_window:
            raise RoundtripError(
                RoundtripErrorCode.GUI_PRECONDITION_FAILED,
                "Execution requires one existing visible Materials Studio window.",
            )
        input_before = stable_read_file(
            request.candidate.structure_path,
            expected_sha256=request.candidate.expected_structure_sha256,
            code=RoundtripErrorCode.INPUT_IDENTITY_MISMATCH,
        )
        if input_before.sha256 != plan.input_artifact.sha256:
            raise RoundtripError(
                RoundtripErrorCode.INPUT_IDENTITY_MISMATCH,
                "The candidate changed after preview planning.",
            )
        try:
            plan.run_root.mkdir(mode=0o700, parents=False, exist_ok=False)
            run_root = resolve_existing_directory(plan.run_root)
            ensure_inside(request.output_root.expanduser().resolve(strict=True), run_root)
        except RoundtripError:
            raise
        except OSError as exc:
            raise RoundtripError(
                RoundtripErrorCode.OUTPUT_CONFINEMENT_FAILED,
                "The unique run root could not be created.",
            ) from exc

        started_at = _utc_now()
        started = time.monotonic()
        result: ScriptRunResult | None = None
        failure_codes: list[str] = []

        def fail(code: RoundtripErrorCode) -> None:
            if code.value not in failure_codes:
                failure_codes.append(code.value)

        try:
            result = runner.run_script(
                plan.script_text,
                working_dir=run_root,
                timeout_seconds=request.timeout_seconds,
                job_prefix=f"ms-roundtrip-{request.run_id}",
                keep_script_name=SCRIPT_NAME,
            )
        except Exception:
            fail(RoundtripErrorCode.RUNNER_FAILED)

        try:
            gui_after = capture_gui_inventory(gui_backend)
        except Exception:
            gui_after = type(gui_before)(
                receipt=_empty_gui_inventory(),
                process_ids=(),
                window_handles=(),
                window_pid_pairs=(),
            )
        gui_invariant = compare_gui_inventories(gui_before, gui_after)
        if not gui_invariant.invariant_passed:
            fail(RoundtripErrorCode.GUI_INVARIANT_FAILED)

        try:
            input_after = stable_read_file(
                input_before.path,
                expected_sha256=input_before.sha256,
                code=RoundtripErrorCode.INPUT_MUTATED,
            )
            input_immutable = snapshot_unchanged(input_before, input_after)
        except RoundtripError:
            input_immutable = False
        if not input_immutable:
            fail(RoundtripErrorCode.INPUT_MUTATED)

        try:
            runner_after = stable_read_file(
                runner_before.path,
                expected_sha256=runner_before.sha256,
                max_bytes=MAX_RUNNER_ARTIFACT_BYTES,
                require_single_link=False,
                code=RoundtripErrorCode.RUNNER_MUTATED,
            )
            runner_unchanged = snapshot_unchanged(runner_before, runner_after)
        except RoundtripError:
            runner_unchanged = False
        if not runner_unchanged:
            fail(RoundtripErrorCode.RUNNER_MUTATED)

        artifacts: tuple[RunArtifactDigest, ...] = ()
        all_artifacts_confined = result is not None
        script_matches = False
        if result is not None:
            artifacts, all_artifacts_confined, script_matches = _runner_artifacts(
                result,
                run_root=run_root,
                expected_script_sha256=plan.script_safety.script_artifact_sha256,
            )
            if not result.success:
                fail(RoundtripErrorCode.RUNNER_FAILED)
            if not all_artifacts_confined or not script_matches:
                fail(RoundtripErrorCode.RUNNER_ARTIFACT_INVALID)
        runner_execution = _runner_execution_receipt(
            result,
            artifacts=artifacts,
            all_confined=all_artifacts_confined,
        )

        output_snapshot = None
        output_artifact = None
        try:
            output_snapshot = stable_read_file(
                plan.output_path,
                code=RoundtripErrorCode.OUTPUT_IDENTITY_MISMATCH,
            )
            ensure_inside(run_root, output_snapshot.path)
            output_artifact = RunArtifactDigest(
                role="roundtrip_output",
                relative_path=relative_run_path(run_root, output_snapshot.path),
                sha256=output_snapshot.sha256,
                byte_count=output_snapshot.byte_count,
            )
        except RoundtripError as exc:
            fail(
                RoundtripErrorCode.OUTPUT_MISSING
                if not plan.output_path.exists()
                else exc.code
            )
        output_fresh = output_artifact is not None

        tagged = (
            _tagged_summary(
                result,
                input_path=input_before.path,
                output_path=plan.output_path,
            )
            if result is not None
            else None
        )
        if tagged is None:
            fail(RoundtripErrorCode.TAGGED_SUMMARY_INVALID)

        comparison = None
        if output_snapshot is not None and input_immutable:
            try:
                comparison = compare_roundtrip_cif_bytes(
                    input_before.payload,
                    output_snapshot.payload,
                    expected_input_sha256=input_before.sha256,
                    expected_output_sha256=output_snapshot.sha256,
                )
                if not comparison.passed:
                    fail(RoundtripErrorCode.THRESHOLD_FAILED)
            except RoundtripError:
                fail(RoundtripErrorCode.COMPARISON_FAILED)
        else:
            fail(RoundtripErrorCode.COMPARISON_FAILED)

        status = "PASS" if not failure_codes else "FAIL"
        completed_at = _utc_now()
        receipt = RoundtripReceipt(
            request_id=request.request_id,
            run_id=request.run_id,
            request_digest_sha256=plan.request_digest_sha256,
            plan_digest_sha256=plan_digest(plan),
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            real_environment=real_environment,
            real_materials_studio_status=(
                "PASS"
                if real_environment and status == "PASS"
                else "FAIL"
                if real_environment
                else "NOT_RUN"
            ),
            input_artifact=plan.input_artifact,
            input_candidate_immutable=input_immutable,
            candidate_validation=plan.candidate_validation,
            script_safety=plan.script_safety,
            runner_identity=runner_identity,
            runner_execution=runner_execution,
            runner_executable_unchanged=runner_unchanged,
            output_artifact=output_artifact,
            output_confined_and_fresh=output_fresh,
            tagged_summary=tagged,
            gui_invariant=gui_invariant,
            comparison=comparison,
            failure_codes=tuple(failure_codes),
        )
        receipt_path = run_root / RECEIPT_NAME
        receipt_snapshot = atomic_write_json(
            receipt_path,
            receipt.model_dump(mode="json"),
        )
        receipt_artifact = RunArtifactDigest(
            role="result_receipt",
            relative_path=relative_run_path(run_root, receipt_snapshot.path),
            sha256=receipt_snapshot.sha256,
            byte_count=receipt_snapshot.byte_count,
        )
        elapsed = time.monotonic() - started
        if not math.isfinite(elapsed):
            raise RoundtripError(
                RoundtripErrorCode.RECEIPT_PERSISTENCE_FAILED,
                "Execution timing became non-finite.",
            )
        return RoundtripExecutionResult(
            status=status,
            run_root=run_root,
            output_path=output_snapshot.path if output_snapshot is not None else None,
            receipt_path=receipt_snapshot.path,
            receipt_artifact=receipt_artifact,
            receipt=receipt,
        )


def run_roundtrip(
    request: RoundtripRequest,
    *,
    runner: RunnerLike | None = None,
    gui_backend: ReadOnlyGuiBackend | None = None,
    real_environment: bool | None = None,
) -> RoundtripPlan | RoundtripExecutionResult:
    return MaterialsStudioRoundtripAdapter(
        runner=runner,
        gui_backend=gui_backend,
        real_environment=real_environment,
    ).run(request)


__all__ = [
    "MaterialsStudioRoundtripAdapter",
    "RunnerLike",
    "run_roundtrip",
]
