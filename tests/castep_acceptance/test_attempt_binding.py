from __future__ import annotations

import json
from pathlib import Path

from material_studio_mcp_server.castep_acceptance.verification import (
    verify_castep_acceptance_execution,
)
from material_studio_mcp_server.ms_roundtrip.gui_inventory import (
    capture_gui_inventory,
)
from material_studio_mcp_server.state.store import ProjectStore

from ._helpers import run_fake_acceptance


def _reverify(result, gui):
    observation = capture_gui_inventory(gui)
    return verify_castep_acceptance_execution(
        plan=result.plan,
        source_spec=result.source_spec,
        store=ProjectStore(result.workspace_root),
        public_preview=result.public_preview,
        public_execute=result.public_execute,
        preview_side_effect_free=True,
        public_tool_reused=True,
        runner_identity_valid=False,
        real_environment=False,
        execute_invocation_count=1,
        gui_before=observation,
        gui_after=observation,
    )


def test_attempt_verifier_rejects_journal_and_metadata_tampering(
    monkeypatch,
    tmp_path,
) -> None:
    result, _runner, gui = run_fake_acceptance(monkeypatch, tmp_path)
    events_path = Path(result.public_execute["execution_attempt_events_path"])
    original_events = events_path.read_bytes()
    events_path.write_bytes(original_events + b"\n")
    journal_tamper = _reverify(result, gui)
    assert journal_tamper.status == "FAIL"
    assert "execution_attempt_history_invalid" in journal_tamper.failure_codes
    assert journal_tamper.backend_execution_count == 0
    events_path.write_bytes(original_events)

    metadata_path = Path(result.public_execute["planned_outputs"]["result_metadata"])
    original_metadata = metadata_path.read_bytes()
    metadata = json.loads(original_metadata)
    metadata["execution_attempt"]["attempt_id"] = "0" * 32
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    metadata_tamper = _reverify(result, gui)
    assert metadata_tamper.status == "FAIL"
    assert "execution_attempt_binding_invalid" in metadata_tamper.failure_codes
    metadata_path.write_bytes(original_metadata)
    assert result.workspace_root.is_dir()
