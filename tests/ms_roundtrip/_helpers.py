from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from material_studio_mcp_server.config import MaterialStudioConfig
from material_studio_mcp_server.domains.surface import build, plan
from material_studio_mcp_server.gui import ProcessInfo, WindowInfo
from material_studio_mcp_server.runner import ScriptRunResult
from material_studio_mcp_server.runtime import (
    BuildOutputKind,
    ModelKind,
    ModelingIntent,
    ReferenceAccess,
    ReferenceAccessMode,
    RUNTIME_CONTRACT_VERSION,
    SemanticParameter,
)
from material_studio_mcp_server.translators import write_crystal_cif


def build_candidate(path: Path, *, project_id: str = "sic_roundtrip_test"):
    intent = ModelingIntent(
        contract_version=RUNTIME_CONTRACT_VERSION,
        request_id="ms-roundtrip-test-candidate",
        material="3C-SiC",
        scenario="surface_slab",
        operation="create_si_face_slab",
        model_kind=ModelKind.CRYSTAL,
        requires_current_model=False,
        output_kind=BuildOutputKind.MODEL_SPEC,
        parameters=(SemanticParameter(name="project_id", value=project_id),),
        semantic_requirements=(),
        declared_assumptions=(),
        reference_access=ReferenceAccess(
            mode=ReferenceAccessMode.TASK_ONLY,
            source_ids=("cod-1010995",),
            raw_structure_access=False,
            final_coordinate_access=False,
            hidden_holdout_access=False,
        ),
    )
    model = build(plan(intent, None))
    write_crystal_cif(model.model, path)
    return model


def _perl_literal(script: str, variable: str) -> str:
    match = re.search(
        rf"my \${re.escape(variable)} = '((?:\\.|[^'])*)';",
        script,
    )
    if match is None:
        raise AssertionError(f"missing Perl variable {variable}")
    raw = match.group(1)
    result: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] == "\\" and index + 1 < len(raw):
            result.append(raw[index + 1])
            index += 2
        else:
            result.append(raw[index])
            index += 1
    return "".join(result)


class FakeGuiBackend:
    supported = True
    unavailable_reason = None
    file_open_may_launch_new_instance = False
    startup_dialog_open_supported = False

    def __init__(self, *, minimized: bool = False) -> None:
        self.processes = [ProcessInfo(name="MatStudio.exe", pid=4242)]
        self.windows = [
            WindowInfo(
                handle=8181,
                title="Current Project - Materials Studio",
                pid=4242,
                rect=(0, 0, 1200, 800),
                class_name="Afx:MaterialsStudio",
                is_visible=True,
                is_minimized=minimized,
                is_foreground=False,
            )
        ]
        self.list_process_calls = 0
        self.list_window_calls = 0
        self.prohibited_calls: list[str] = []

    def list_processes(self) -> list[ProcessInfo]:
        self.list_process_calls += 1
        return list(self.processes)

    def list_windows(self, pid: int | None = None) -> list[WindowInfo]:
        self.list_window_calls += 1
        return [window for window in self.windows if pid is None or window.pid == pid]

    def _prohibited(self, name: str):
        self.prohibited_calls.append(name)
        raise AssertionError(f"GUI method {name} must not be called")

    def activate_window(self, window: WindowInfo) -> bool:
        return self._prohibited("activate_window")

    def capture_window(self, window: WindowInfo, output_path: Path) -> Path:
        return self._prohibited("capture_window")

    def open_file(self, path: Path):
        return self._prohibited("open_file")

    def open_file_in_existing_window(self, window: WindowInfo, path: Path):
        return self._prohibited("open_file_in_existing_window")

    def launch_app(self):
        return self._prohibited("launch_app")

    def add_second_window(self) -> None:
        self.windows.append(
            WindowInfo(
                handle=9191,
                title="Unexpected - Materials Studio",
                pid=4242,
                rect=(0, 0, 900, 600),
                class_name="Afx:MaterialsStudio",
                is_visible=True,
                is_minimized=False,
                is_foreground=False,
            )
        )

    def replace_identity(self) -> None:
        self.processes = [ProcessInfo(name="MatStudio.exe", pid=5252)]
        self.windows = [
            WindowInfo(
                handle=9292,
                title="Replacement - Materials Studio",
                pid=5252,
                rect=(0, 0, 1200, 800),
                class_name="Afx:MaterialsStudio",
                is_visible=True,
                is_minimized=False,
                is_foreground=False,
            )
        ]


class FakeRunner:
    def __init__(
        self,
        runner_path: Path,
        *,
        output_transform: Callable[[bytes], bytes] | None = None,
        after_run: Callable[[], None] | None = None,
        mutate_input: bool = False,
        tamper_script: bool = False,
        success: bool = True,
    ) -> None:
        self.config = MaterialStudioConfig(
            runner=runner_path,
            workspace_root=runner_path.parent,
            default_timeout_seconds=30,
            install_home=runner_path.parent,
            runner_source="offline_fake",
            extra_runner_args=(),
        )
        self.output_transform = output_transform or (lambda payload: payload)
        self.after_run = after_run
        self.mutate_input = mutate_input
        self.tamper_script = tamper_script
        self.success = success
        self.run_calls = 0

    def run_script(
        self,
        script: str,
        *,
        args: list[str] | None = None,
        working_dir: str | Path | None = None,
        timeout_seconds: int | None = None,
        job_prefix: str = "msjob",
        keep_script_name: str = "script.pl",
    ) -> ScriptRunResult:
        self.run_calls += 1
        source = Path(_perl_literal(script, "source"))
        output = Path(_perl_literal(script, "output"))
        root = Path(working_dir or self.config.workspace_root)
        job_dir = root / ".material-studio-mcp" / "jobs" / "fake-job"
        job_dir.mkdir(parents=True, exist_ok=False)
        script_path = job_dir / keep_script_name
        script_path.write_text(
            script + ("# tampered\n" if self.tamper_script else ""),
            encoding="utf-8",
        )
        payload = source.read_bytes()
        output.write_bytes(self.output_transform(payload))
        if self.mutate_input:
            source.write_bytes(payload + b"\n# input mutation\n")
        parsed = {
            "source": str(source),
            "output": str(output),
            "document_name": source.name,
        }
        output_log = job_dir / f"{script_path.name}.out"
        output_log.write_text(json.dumps(parsed, sort_keys=True), encoding="utf-8")
        if self.after_run is not None:
            self.after_run()
        started = time.monotonic()
        return ScriptRunResult(
            command=[str(self.config.runner), script_path.stem],
            job_id=job_dir.name,
            job_dir=job_dir,
            script_path=script_path,
            return_code=0 if self.success else 1,
            stdout="",
            stderr="" if self.success else "offline failure",
            output_file=output_log,
            log_file=None,
            materials_output=json.dumps(parsed, sort_keys=True),
            materials_log="",
            success=self.success,
            timed_out=False,
            parsed_json=parsed,
            created_files=[script_path, output_log],
            duration_seconds=time.monotonic() - started,
        )


def write_model_spec(path: Path, model) -> None:
    path.write_text(
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )


def copy_roundtrip_output(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
