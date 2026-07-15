from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from material_studio_mcp_server.state import store as store_module
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.state import (
    ProjectRevisionAllocationConflictError,
    ProjectRevisionConflictError,
    ProjectStateBusyError,
    ProjectStore,
    RevisionInfo,
)


def load_benzene() -> ModelSpec:
    path = Path("src/material_studio_mcp_server/examples/benzene_spec.json")
    return ModelSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_create_save_history_and_rollback(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene()

    first = store.create_project(spec, user_text="create benzene", generated_script="# script")
    assert first.revision == 0
    assert first.spec_path.exists()
    assert first.current_path.exists()

    current = store.load_current(spec.project_id)
    updated = current.model_copy(update={"metadata": {"changed": True}})
    second = store.save_revision(spec.project_id, updated, user_text="change", action="metadata")
    assert second.revision == 1

    rollback = store.rollback(
        spec.project_id,
        0,
        user_text="rollback",
        expected_revision=1,
        expected_new_revision=2,
    )
    assert rollback.revision == 2
    assert store.load_current(spec.project_id).revision == 2
    assert len(store.list_history(spec.project_id)) == 3


def test_list_projects_and_latest_project(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "latest_project_proj"})

    store.create_project(spec, user_text="create benzene", generated_script="# script")

    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0]["project_id"] == "latest_project_proj"
    assert projects[0]["revision"] == 0
    assert Path(projects[0]["current_path"]).exists()
    assert store.latest_project()["project_id"] == "latest_project_proj"


def test_invalid_current_pointer_recovers_latest_revision_without_mutating_pointer(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "recover_invalid_current"})
    created = store.create_project(spec, user_text="create", generated_script="# script")
    original_revision = created.spec_path.read_bytes()
    invalid_pointer = b'{"project_id":"recover_invalid_current","spec":'
    created.current_path.write_bytes(invalid_pointer)

    recovered, resolution = store.resolve_current(spec.project_id)

    assert recovered.revision == 0
    assert recovered.project_id == spec.project_id
    assert resolution["status"] == "recovered_invalid_current_pointer"
    assert resolution["read_source"] == "latest_valid_revision"
    assert resolution["valid"] is False
    assert resolution["recovery_used"] is True
    assert resolution["recovery_is_read_only"] is True
    assert resolution["repair_required"] is True
    assert resolution["safe_to_continue_read_only"] is True
    assert resolution["next_successful_revision_write_repairs_pointer"] is True
    assert resolution["error_type"] == "JSONDecodeError"
    assert Path(resolution["revision_path"]).read_bytes() == original_revision
    assert created.current_path.read_bytes() == invalid_pointer

    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0]["project_id"] == spec.project_id
    assert projects[0]["revision"] == 0
    assert projects[0]["current_pointer_status"] == "recovered_invalid_current_pointer"
    assert projects[0]["current_pointer_valid"] is False
    assert projects[0]["current_pointer_recovery_used"] is True
    assert store.latest_project()["project_id"] == spec.project_id
    assert created.current_path.read_bytes() == invalid_pointer


def test_missing_current_pointer_recovers_revision_read_only(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "recover_missing_current"})
    created = store.create_project(spec, user_text="create")
    created.current_path.unlink()

    recovered, resolution = store.resolve_current(spec.project_id)

    assert recovered.revision == 0
    assert resolution["status"] == "recovered_missing_current_pointer"
    assert resolution["exists"] is False
    assert resolution["error"] == "current.json is missing"
    assert created.current_path.exists() is False
    assert store.latest_project()["current_pointer_recovery_used"] is True


def test_valid_json_current_pointer_with_tampered_spec_recovers_immutable_revision(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "recover_tampered_current"})
    created = store.create_project(spec, user_text="create")
    pointer_payload = json.loads(created.current_path.read_text(encoding="utf-8"))
    pointer_payload["spec"]["metadata"] = {"tampered": True}
    tampered_pointer = json.dumps(pointer_payload, ensure_ascii=False).encode("utf-8")
    created.current_path.write_bytes(tampered_pointer)

    recovered, resolution = store.resolve_current(spec.project_id)

    assert recovered.revision == 0
    assert recovered.metadata != {"tampered": True}
    assert resolution["status"] == "recovered_invalid_current_pointer"
    assert resolution["error_type"] == "ValueError"
    assert "does not match immutable revision file" in resolution["error"]
    assert created.current_path.read_bytes() == tampered_pointer


def test_save_after_recovery_skips_corrupt_orphan_and_repairs_current_pointer(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "repair_on_next_write"})
    created = store.create_project(spec, user_text="create", generated_script="# r0")
    orphan = created.project_dir / "revisions" / "r001_model_spec.json"
    orphan_bytes = b'{"corrupt":"orphan"'
    orphan.write_bytes(orphan_bytes)
    created.current_path.write_bytes(b'{"broken":')

    recovered = store.load_current(spec.project_id)
    updated = recovered.model_copy(update={"metadata": {"recovered": True}})
    saved = store.save_revision(
        spec.project_id,
        updated,
        user_text="continue after pointer recovery",
        action="metadata",
        generated_script="# r2",
    )

    assert saved.revision == 2
    assert orphan.read_bytes() == orphan_bytes
    assert (created.project_dir / "revisions" / "r002_model_spec.json").exists()
    current_payload = json.loads(saved.current_path.read_text(encoding="utf-8"))
    assert current_payload["revision"] == 2
    assert current_payload["spec"]["revision"] == 2
    current, resolution = store.resolve_current(spec.project_id)
    assert current.revision == 2
    assert resolution["status"] == "valid"
    assert resolution["recovery_used"] is False
    assert [event["revision"] for event in store.list_history(spec.project_id)] == [0, 2]
    previous, skipped = store.previous_valid_revision(spec.project_id, current.revision)
    assert previous is not None
    assert previous.revision == 0
    assert [item["revision"] for item in skipped] == [1]
    assert not list(created.project_dir.rglob("*.tmp"))


def test_atomic_current_replace_failure_preserves_previous_committed_pointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "atomic_current_pointer"})
    created = store.create_project(spec, user_text="create")
    committed_pointer = created.current_path.read_bytes()
    current = store.load_current(spec.project_id)
    updated = current.model_copy(update={"metadata": {"attempted": True}})
    real_replace = store_module.os.replace

    def fail_current_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination).resolve() == created.current_path.resolve():
            raise OSError("simulated current pointer commit interruption")
        real_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", fail_current_replace)
    with pytest.raises(OSError, match="simulated current pointer commit interruption"):
        store.save_revision(spec.project_id, updated, user_text="interrupted", action="metadata")

    assert created.current_path.read_bytes() == committed_pointer
    resolved, resolution = store.resolve_current(spec.project_id)
    assert resolved.revision == 0
    assert resolution["status"] == "valid"
    assert (created.project_dir / "revisions" / "r001_model_spec.json").exists()
    assert not list(created.project_dir.rglob("*.tmp"))


def test_concurrent_expected_revision_writes_allow_exactly_one_commit(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "concurrent_revision"})
    store.create_project(spec, user_text="create")
    base = store.load_current(spec.project_id)

    def write(label: str) -> RevisionInfo | ProjectRevisionConflictError:
        worker_store = ProjectStore(tmp_path)
        candidate = base.model_copy(update={"metadata": {"writer": label}})
        try:
            return worker_store.save_revision(
                spec.project_id,
                candidate,
                user_text=label,
                action="concurrent_test",
                expected_revision=0,
            )
        except ProjectRevisionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ("first", "second")))

    committed = [item for item in results if not isinstance(item, Exception)]
    conflicts = [
        item for item in results if isinstance(item, ProjectRevisionConflictError)
    ]
    assert len(committed) == 1
    assert len(conflicts) == 1
    assert committed[0].revision == 1
    assert conflicts[0].expected_revision == 0
    assert conflicts[0].current_revision == 1
    transaction = committed[0].state_write_transaction
    assert transaction is not None
    assert transaction["scope"] == "project"
    assert transaction["domain"] == "project_state"
    assert {
        "save_revision",
        "revision_write",
        "history_publish",
        "current_pointer_publish",
    } <= set(transaction["coverage"])
    assert store.load_current(spec.project_id).revision == 1
    assert [event["revision"] for event in store.list_history(spec.project_id)] == [
        0,
        1,
    ]
    assert not (store.project_dir(spec.project_id) / "revisions" / "r002_model_spec.json").exists()


def test_expected_new_revision_rejects_orphan_allocation_drift(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "orphan_allocation"})
    created = store.create_project(spec, user_text="create")
    current_before = created.current_path.read_bytes()
    history_path = created.project_dir / "history.jsonl"
    history_before = history_path.read_bytes()
    orphan_path = created.project_dir / "revisions" / "r001_model_spec.json"
    orphan_bytes = b'{"incomplete":'
    orphan_path.write_bytes(orphan_bytes)
    candidate = store.load_current(spec.project_id).model_copy(
        update={"metadata": {"prepared_for": 1}}
    )

    with pytest.raises(ProjectRevisionAllocationConflictError) as raised:
        store.save_revision(
            spec.project_id,
            candidate,
            user_text="prepared for r1",
            action="metadata",
            generated_script="# prepared r1",
            expected_revision=0,
            expected_new_revision=1,
        )

    assert raised.value.project_id == spec.project_id
    assert raised.value.expected_new_revision == 1
    assert raised.value.allocated_revision == 2
    assert raised.value.current_revision == 0
    assert orphan_path.read_bytes() == orphan_bytes
    assert created.current_path.read_bytes() == current_before
    assert history_path.read_bytes() == history_before
    assert not (
        created.project_dir / "revisions" / "r002_model_spec.json"
    ).exists()
    assert not (created.project_dir / "scripts" / "r002_build.pl").exists()


def test_project_state_lock_timeout_preserves_current_history_and_revisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "state_lock_timeout"})
    created = store.create_project(spec, user_text="create")
    committed_current = created.current_path.read_bytes()
    history_path = created.project_dir / "history.jsonl"
    committed_history = history_path.read_bytes()
    candidate = store.load_current(spec.project_id).model_copy(
        update={"metadata": {"blocked": True}}
    )
    monkeypatch.setattr(store_module, "PROJECT_STATE_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(store_module, "PROJECT_STATE_LOCK_POLL_SECONDS", 0.005)
    lock_path = created.project_dir / "project_state.lock"

    with store_module._project_state_advisory_write_lock(
        lock_path,
        project_id=spec.project_id,
        workspace_root=tmp_path,
        timeout_seconds=1.0,
        poll_seconds=0.01,
    ):
        with pytest.raises(ProjectStateBusyError):
            store.save_revision(
                spec.project_id,
                candidate,
                user_text="blocked",
                action="metadata",
                expected_revision=0,
            )

    assert created.current_path.read_bytes() == committed_current
    assert history_path.read_bytes() == committed_history
    assert not (created.project_dir / "revisions" / "r001_model_spec.json").exists()


def test_atomic_history_replace_failure_preserves_committed_history_and_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "atomic_history"})
    created = store.create_project(spec, user_text="create")
    history_path = created.project_dir / "history.jsonl"
    committed_history = history_path.read_bytes()
    committed_current = created.current_path.read_bytes()
    candidate = store.load_current(spec.project_id).model_copy(
        update={"metadata": {"attempted": True}}
    )
    real_replace = store_module.os.replace

    def fail_history_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination).resolve() == history_path.resolve():
            raise OSError("simulated history commit interruption")
        real_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", fail_history_replace)
    with pytest.raises(OSError, match="simulated history commit interruption"):
        store.save_revision(
            spec.project_id,
            candidate,
            user_text="interrupted",
            action="metadata",
            expected_revision=0,
        )

    assert history_path.read_bytes() == committed_history
    assert created.current_path.read_bytes() == committed_current
    assert store.load_current(spec.project_id).revision == 0
    assert (created.project_dir / "revisions" / "r001_model_spec.json").exists()
    assert not list(created.project_dir.rglob("*.tmp"))


def test_project_state_transaction_reuses_same_project_and_rejects_nested_other(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    spec = load_benzene().model_copy(update={"project_id": "state_reentrant"})
    store.create_project(spec, user_text="create")
    candidate = store.load_current(spec.project_id).model_copy(
        update={"metadata": {"nested": True}}
    )

    with store.project_state_transaction(
        spec.project_id,
        coverage="outer_test",
    ) as transaction:
        committed = store.save_revision(
            spec.project_id,
            candidate,
            user_text="nested",
            action="metadata",
            expected_revision=0,
        )
        with pytest.raises(RuntimeError, match="different project"):
            with store.project_state_transaction(
                "other_project",
                coverage="must_fail",
            ):
                pass

    assert committed.state_write_transaction is transaction
    assert transaction["nested_call_count"] == 1
    assert "outer_test" in transaction["coverage"]
    assert "save_revision" in transaction["coverage"]


def test_project_id_path_traversal_rejected(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("..\\escape")
