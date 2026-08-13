from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from material_studio_mcp_server.gui_loop import GuiLoopError, GuiLoopManager


SECRET = b"0123456789abcdef0123456789abcdef"


def _binding(revision: int = 0) -> dict:
    return {
        "pid": 12345,
        "window_handle": 67890,
        "project_id": "silicon_live",
        "revision": revision,
        "initial_document_name": "model_r000_unique",
    }


def _manager(tmp_path: Path, **kwargs) -> GuiLoopManager:
    return GuiLoopManager(tmp_path, secret=SECRET, poll_seconds=0.005, **kwargs)


def _paths(manager: GuiLoopManager, binding: dict | None = None) -> dict[str, Path]:
    normalized = manager._normalize_binding(
        binding or _binding(), require_base_revision=False
    )
    return manager._paths(normalized)


def _publish_live_heartbeat(manager: GuiLoopManager, revision: int = 0) -> None:
    paths = _paths(manager)
    paths["lock"].write_text("test-loop\n", encoding="utf-8")
    manager._write_signed_envelope(
        paths["heartbeat"],
        {
            "kind": "heartbeat",
            "protocol": "materials-studio-gui-loop-v1",
            "binding": manager._session_binding(_binding()),
            "loop_id": "test-loop",
            "status": "running",
            "current_revision": revision,
            "active_document_name": (
                "model_r000_unique" if revision == 0 else f"si_r{revision:03d}.cif"
            ),
            "heartbeat_at_epoch": time.time(),
        },
    )


def _wait_for_pending(manager: GuiLoopManager) -> tuple[Path, dict]:
    paths = _paths(manager)
    deadline = time.time() + 2
    while time.time() < deadline:
        matches = list(paths["pending"].glob("*.json"))
        if matches:
            return matches[0], manager._read_signed_envelope(matches[0], "job")
        time.sleep(0.005)
    raise AssertionError("timed out waiting for a pending GUI-loop job")


def _publish_terminal(
    manager: GuiLoopManager,
    *,
    terminal_state: str,
    result_overrides: dict | None = None,
) -> dict:
    paths = _paths(manager)
    pending, job = _wait_for_pending(manager)
    running = paths["running"] / pending.name
    os.replace(pending, running)
    if terminal_state == "done":
        state = {
            "kind": "current_state",
            "protocol": "materials-studio-gui-loop-v1",
            "binding": manager._session_binding(_binding()),
            "current_revision": job["target_revision"],
            "current_document_name": job["document_name"],
            "last_job_id": job["job_id"],
            "structure_sha256": job["structure_sha256"],
            "updated_at_epoch": time.time(),
        }
        manager._write_signed_envelope(paths["state"], state)
        _publish_live_heartbeat(manager, int(job["target_revision"]))
    destination = paths[terminal_state] / running.name
    os.replace(running, destination)
    result = {
        "kind": "job_result",
        "protocol": "materials-studio-gui-loop-v1",
        "binding": manager._session_binding(_binding()),
        "job_id": job["job_id"],
        "status": terminal_state,
        "detail": (
            "import_structure completed"
            if terminal_state == "done"
            else "test import failure"
        ),
        "current_revision": (
            job["target_revision"] if terminal_state == "done" else job["expected_revision"]
        ),
        "current_document_name": (
            job["document_name"] if terminal_state == "done" else "model_r000_unique"
        ),
        "structure_sha256": (
            job["structure_sha256"] if terminal_state == "done" else ""
        ),
        "completed_at_epoch": time.time(),
    }
    result.update(result_overrides or {})
    manager._write_signed_envelope(
        paths[terminal_state] / f"{job['job_id']}.result.json",
        result,
    )
    return job


def test_prepare_creates_stable_session_queue_and_fixed_script(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.prepare(_binding(0))
    second = manager.prepare(_binding(1))
    root = Path(first["queue_root"])
    script_path = Path(first["loop_script_path"])

    assert first["status"] == "prepared"
    assert first["queue_root"] == second["queue_root"]
    assert second["current_revision"] == 0
    assert first["operation_allowlist"] == ["import_structure"]
    assert first["arbitrary_script_supported"] is False
    assert "secret" not in json.dumps(first).lower().replace("secret_exposed", "")
    assert all(
        (root / name).is_dir()
        for name in ("staging", "pending", "running", "done", "failed", "control")
    )

    script = script_path.read_text(encoding="utf-8")
    assert "Documents->Import($structure_path)" in script
    assert "Only import_structure is permitted" in script
    assert "hmac_sha256_hex" in script
    assert "O_EXCL" in script
    assert "Revision compare-and-swap failed" in script
    assert "require_active_document_binding" in script
    assert "do $" not in script
    assert "eval $" not in script
    assert SECRET.decode("ascii") not in script


def test_generated_loop_has_continuous_and_post_commit_heartbeat_contract(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    prepared = manager.prepare(_binding())
    script = Path(prepared["loop_script_path"]).read_text(encoding="utf-8")

    recurring = "if ($now - $last_heartbeat >= $HEARTBEAT_SECONDS)"
    assert "my $HEARTBEAT_SECONDS = 2.0;" in script
    assert recurring in script
    assert script.index(recurring) < script.index("opendir(my $dh, $PENDING)")
    recurring_start = script.index(recurring)
    recurring_heartbeat = script.index('heartbeat("running");', recurring_start)
    recurring_state_update = script.index("$last_heartbeat = $now;", recurring_start)
    assert recurring_start < recurring_heartbeat < recurring_state_update
    state_publish = "write_signed_atomic($STATE_PATH, $state);"
    success_heartbeat = "heartbeat(\"running\");"
    done_publish = "rename($running_path, $done_path)"
    assert script.index(state_publish) < script.index(success_heartbeat, script.index(state_publish))
    assert script.index(success_heartbeat, script.index(state_publish)) < script.index(done_publish)


def test_default_manager_key_survives_controller_restart(tmp_path: Path) -> None:
    first = GuiLoopManager(tmp_path)
    prepared = first.prepare(_binding())
    second = GuiLoopManager(tmp_path)
    resumed = second.prepare(_binding(1))

    assert resumed["queue_root"] == prepared["queue_root"]
    assert resumed["binding"]["base_revision"] == 0
    assert resumed["current_revision"] == 0


def test_unprepared_status_does_not_create_keys_or_queue_directories(tmp_path: Path) -> None:
    manager = GuiLoopManager(tmp_path)

    status = manager.status(_binding())

    assert status["status"] == "not_prepared"
    assert status["loop_ready"] is False
    assert not (tmp_path / "gui_loop").exists()


def test_status_requires_fresh_signed_heartbeat_and_exact_document(tmp_path: Path) -> None:
    manager = _manager(tmp_path, heartbeat_max_age_seconds=2.0)
    manager.prepare(_binding())
    _publish_live_heartbeat(manager)

    running = manager.status(_binding())
    assert running["status"] == "running"
    assert running["loop_ready"] is True
    assert running["heartbeat_signature_valid"] is True
    assert running["heartbeat_document_matches_state"] is True

    paths = _paths(manager)
    manager._write_signed_envelope(
        paths["heartbeat"],
        {
            "kind": "heartbeat",
            "protocol": "materials-studio-gui-loop-v1",
            "binding": manager._session_binding(_binding()),
            "loop_id": "test-loop",
            "status": "running",
            "current_revision": 0,
            "active_document_name": "different_window_document",
            "heartbeat_at_epoch": time.time(),
        },
    )
    wrong_document = manager.status(_binding())
    assert wrong_document["loop_ready"] is False
    assert wrong_document["heartbeat_document_matches_state"] is False


def test_enqueue_uses_signed_fixed_envelope_and_accepts_exact_terminal_receipt(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.prepare(_binding())
    _publish_live_heartbeat(manager)
    structure = tmp_path / "models" / "si_r001.cif"
    structure.parent.mkdir()
    structure.write_text("data_si\n_cell_length_a 5.43\n", encoding="utf-8")

    worker = threading.Thread(
        target=_publish_terminal,
        kwargs={"manager": manager, "terminal_state": "done"},
    )
    worker.start()
    result = manager.enqueue_and_wait(
        structure,
        _binding(),
        1,
        timeout_seconds=2,
        document_name=structure.name,
    )
    worker.join(timeout=2)

    assert result["status"] == "done"
    assert result["expected_revision"] == 0
    assert result["target_revision"] == 1
    assert result["result"]["current_revision"] == 1
    assert result["imported_document_name"] == structure.name


def test_enqueue_accepts_v1_loop_result_without_sha_when_signed_state_matches(
    tmp_path: Path,
) -> None:
    """A running pre-upgrade v1 loop can survive the Python runtime upgrade."""

    manager = _manager(tmp_path)
    manager.prepare(_binding())
    _publish_live_heartbeat(manager)
    structure = tmp_path / "si_r001.cif"
    structure.write_text("data_si\n", encoding="utf-8")
    worker = threading.Thread(
        target=_publish_terminal,
        kwargs={
            "manager": manager,
            "terminal_state": "done",
            "result_overrides": {"structure_sha256": None},
        },
    )
    worker.start()

    result = manager.enqueue_and_wait(
        structure,
        _binding(),
        1,
        timeout_seconds=2,
        document_name=structure.name,
    )
    worker.join(timeout=2)

    assert result["status"] == "done"
    assert result["terminal_structure_sha256_source"] == "current_state"
    assert result["post_commit_status"]["loop_ready"] is True


@pytest.mark.parametrize(
    "result_overrides",
    (
        {"job_id": "f" * 32},
        {"current_revision": 9},
        {"current_document_name": "wrong_document.cif"},
        {"structure_sha256": "0" * 64},
    ),
    ids=("job", "revision", "document", "structure_sha256"),
)
def test_done_terminal_receipt_rejects_every_job_binding_mismatch(
    tmp_path: Path,
    result_overrides: dict,
) -> None:
    manager = _manager(tmp_path)
    manager.prepare(_binding())
    _publish_live_heartbeat(manager)
    structure = tmp_path / "si_r001.cif"
    structure.write_text("data_si\n", encoding="utf-8")
    worker = threading.Thread(
        target=_publish_terminal,
        kwargs={
            "manager": manager,
            "terminal_state": "done",
            "result_overrides": result_overrides,
        },
    )
    worker.start()

    with pytest.raises(GuiLoopError) as mismatch:
        manager.enqueue_and_wait(
            structure,
            _binding(),
            1,
            timeout_seconds=2,
            document_name=structure.name,
        )
    worker.join(timeout=2)

    assert mismatch.value.receipt["status"] == "terminal_receipt_binding_mismatch"
    assert mismatch.value.receipt["side_effect_may_have_occurred"] is True
    assert mismatch.value.receipt["automatic_dialog_fallback_allowed"] is False


def test_failed_terminal_receipt_preserves_side_effect_uncertainty(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.prepare(_binding())
    _publish_live_heartbeat(manager)
    structure = tmp_path / "si_r001.cif"
    structure.write_text("data_si\n", encoding="utf-8")
    worker = threading.Thread(
        target=_publish_terminal,
        kwargs={"manager": manager, "terminal_state": "failed"},
    )
    worker.start()

    with pytest.raises(GuiLoopError) as failed:
        manager.enqueue_and_wait(
            structure,
            _binding(),
            1,
            timeout_seconds=2,
            document_name=structure.name,
        )
    worker.join(timeout=2)

    assert failed.value.receipt["status"] == "failed"
    assert failed.value.receipt["job"]["result"]["status"] == "failed"
    assert failed.value.receipt["side_effect_may_have_occurred"] is True
    assert failed.value.receipt["automatic_dialog_fallback_allowed"] is False


def test_enqueue_rejects_unready_loop_workspace_escape_and_stale_revision(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.prepare(_binding())
    inside = tmp_path / "model.cif"
    inside.write_text("data_model\n", encoding="utf-8")
    with pytest.raises(GuiLoopError) as unready:
        manager.enqueue_and_wait(inside, _binding(), 1, timeout_seconds=0)
    assert unready.value.receipt["status"] == "loop_not_ready"

    _publish_live_heartbeat(manager)
    outside = tmp_path.parent / "outside.cif"
    outside.write_text("data_outside\n", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        manager.enqueue_and_wait(outside, _binding(), 1, timeout_seconds=0)

    with pytest.raises(GuiLoopError) as conflict:
        manager.enqueue_and_wait(inside, _binding(9), 10, timeout_seconds=0)
    assert conflict.value.receipt["status"] == "revision_conflict"

    unsupported = tmp_path / "model.pl"
    unsupported.write_text("print 'no';\n", encoding="utf-8")
    with pytest.raises(GuiLoopError) as extension:
        manager.enqueue_and_wait(unsupported, _binding(), 1, timeout_seconds=0)
    assert extension.value.receipt["status"] == "structure_extension_not_allowed"


def test_request_stop_is_signed_bound_and_idempotent(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.prepare(_binding())
    receipt = manager.request_stop(_binding())
    stop = manager._read_signed_envelope(_paths(manager)["stop"], "stop")

    assert receipt["status"] == "stop_requested"
    assert stop["binding"] == manager._session_binding(_binding())
    assert stop["request_id"] == receipt["request_id"]
    assert SECRET.decode("ascii") not in json.dumps(receipt)
    repeated = manager.request_stop(_binding())
    assert repeated["request_id"] == receipt["request_id"]
    assert repeated["idempotent"] is True


def test_enqueue_timeout_returns_side_effect_uncertainty(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.prepare(_binding())
    _publish_live_heartbeat(manager)
    structure = tmp_path / "model.cif"
    structure.write_text("data_model\n", encoding="utf-8")

    with pytest.raises(GuiLoopError) as timeout:
        manager.enqueue_and_wait(structure, _binding(), 1, timeout_seconds=0.01)
    assert timeout.value.receipt["status"] == "timeout"
    assert timeout.value.receipt["side_effect_may_have_occurred"] is True
    assert timeout.value.receipt["automatic_dialog_fallback_allowed"] is False
    assert len(list(_paths(manager)["pending"].glob("*.json"))) == 1
