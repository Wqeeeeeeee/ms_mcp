from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import inspect
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import material_studio_mcp_server.remote_handoff as remote_handoff_module
from material_studio_mcp_server.remote_handoff import (
    REMOTE_HANDOFF_EVENT_SCHEMA,
    RemoteHandoffBindingError,
    RemoteHandoffBusyError,
    RemoteHandoffHistoryError,
    _remote_job_write_lock,
    prepare_remote_castep_bundle,
    read_remote_job_status,
    record_remote_status,
    record_remote_submission,
)
from material_studio_mcp_server.specs.project import ModelSpec
from material_studio_mcp_server.specs.remote_job import (
    RemoteSubmissionRecordRequest,
)
from material_studio_mcp_server.state import ProjectStore
from material_studio_mcp_server.translators.project_to_perl import (
    render_model_to_perl,
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_event(
    *,
    sequence: int,
    event_type: str,
    bundle_id: str,
    manifest_sha256: str,
    previous_event_sha256: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "schema": REMOTE_HANDOFF_EVENT_SCHEMA,
        "sequence": sequence,
        "event_type": event_type,
        "recorded_at": f"2026-07-24T10:00:0{sequence}Z",
        "bundle_id": bundle_id,
        "manifest_sha256": manifest_sha256,
        "previous_event_sha256": previous_event_sha256,
        "payload": payload,
    }
    return {
        **unsigned,
        "event_sha256": _sha256(_canonical_json_bytes(unsigned)),
    }


def _grow_file_during_descriptor_read(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    *,
    suffix: bytes = b" ",
) -> None:
    original_read = remote_handoff_module.os.read
    mutated = False

    def growing_read(file_descriptor: int, byte_count: int) -> bytes:
        nonlocal mutated
        content = original_read(file_descriptor, byte_count)
        if content and not mutated:
            try:
                target_details = path.stat()
                descriptor_details = os.fstat(file_descriptor)
                same_file = os.path.samestat(target_details, descriptor_details)
            except (FileNotFoundError, OSError):
                same_file = False
            if same_file:
                with path.open("ab") as stream:
                    stream.write(suffix)
                    stream.flush()
                    os.fsync(stream.fileno())
                mutated = True
        return content

    monkeypatch.setattr(remote_handoff_module.os, "read", growing_read)


def _filesystem_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if path.is_file():
            content = path.read_bytes()
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = (
                len(content),
                stat.st_mtime_ns,
                _sha256(content),
            )
    return snapshot


@dataclass(frozen=True)
class HandoffFixture:
    workspace_root: Path
    project_id: str
    revision: int
    spec_path: Path
    script_path: Path
    input_path: Path
    request: dict[str, Any]


@pytest.fixture
def handoff_fixture(tmp_path: Path) -> HandoffFixture:
    project_id = "remote_handoff_demo"
    spec = ModelSpec.model_validate(
        {
            "project_id": project_id,
            "revision": 0,
            "software": "Materials Studio",
            "model_type": "crystal",
            "model": {
                "name": "silicon",
                "lattice": {
                    "a": 5.431,
                    "b": 5.431,
                    "c": 5.431,
                    "alpha": 90.0,
                    "beta": 90.0,
                    "gamma": 90.0,
                },
                "basis_atoms": [
                    {
                        "id": "Si1",
                        "element": "Si",
                        "fractional": [0.0, 0.0, 0.0],
                    }
                ],
                "operations": [],
            },
            "simulation": {
                "module": "CASTEP",
                "task": "Energy",
                "functional": "PBE",
                "quality": "Medium",
            },
            "outputs": {},
            "acceptance": {},
            "metadata": {"test": "remote handoff"},
        }
    )
    planned_output_dir = (
        tmp_path / project_id / "outputs" / "r000"
    )
    calculation_script = render_model_to_perl(
        spec,
        planned_output_dir,
    ).calculation_preview_script
    assert calculation_script is not None
    store = ProjectStore(tmp_path)
    revision_info = store.create_project(
        spec,
        calculation_preview_script=calculation_script,
    )
    assert revision_info.calculation_preview_script_path is not None
    input_path = (
        revision_info.project_dir
        / "outputs"
        / "r000"
        / "structure_r000.cif"
    )
    input_path.write_text(
        "data_silicon\n_cell_length_a 5.431\n",
        encoding="utf-8",
    )
    request = {
        "workspace_root": str(tmp_path.resolve()),
        "project_id": project_id,
        "expected_revision": 0,
        "calculation_name": "energy_baseline",
        "task": "Energy",
        "spec_path": str(revision_info.spec_path),
        "script_path": str(revision_info.calculation_preview_script_path),
        "input_path": str(input_path),
        "expected_spec_sha256": _sha256(revision_info.spec_path.read_bytes()),
        "expected_script_sha256": _sha256(
            revision_info.calculation_preview_script_path.read_bytes()
        ),
        "expected_input_sha256": _sha256(input_path.read_bytes()),
        "requested_cores": 32,
        "execution_mode": "preview",
        "lock_timeout_seconds": 0.25,
    }
    return HandoffFixture(
        workspace_root=tmp_path.resolve(),
        project_id=project_id,
        revision=0,
        spec_path=revision_info.spec_path,
        script_path=revision_info.calculation_preview_script_path,
        input_path=input_path,
        request=request,
    )


def _prepare(
    fixture: HandoffFixture,
    *,
    execution_mode: str = "execute",
) -> dict[str, Any]:
    request = dict(fixture.request)
    if execution_mode == "execute":
        preview = prepare_remote_castep_bundle(request)
        request["expected_preview_manifest_sha256"] = preview["manifest_sha256"]
    request["execution_mode"] = execution_mode
    return prepare_remote_castep_bundle(request)


def _identity(job_id: str = "73421") -> dict[str, str]:
    return {
        "scheduler_kind": "slurm",
        "scheduler_id": "cluster-alpha",
        "job_id": job_id,
    }


def _submission_request(
    fixture: HandoffFixture,
    prepared: dict[str, Any],
    *,
    job_id: str = "73421",
) -> dict[str, Any]:
    return {
        "workspace_root": str(fixture.workspace_root),
        "project_id": fixture.project_id,
        "bundle_id": prepared["bundle_id"],
        "expected_manifest_sha256": prepared["manifest_sha256"],
        "identity": _identity(job_id),
        "submitted_at": "2026-07-24T10:00:00+08:00",
        "channel": "manual_scheduler_submission",
        "note": "Submitted outside this module.",
        "lock_timeout_seconds": 0.25,
    }


def _status_request(
    fixture: HandoffFixture,
    prepared: dict[str, Any],
    *,
    state: str = "running",
    observed_at: str = "2026-07-24T10:01:00+08:00",
    job_id: str = "73421",
) -> dict[str, Any]:
    return {
        "workspace_root": str(fixture.workspace_root),
        "project_id": fixture.project_id,
        "bundle_id": prepared["bundle_id"],
        "expected_manifest_sha256": prepared["manifest_sha256"],
        "identity": _identity(job_id),
        "observed_at": observed_at,
        "state": state,
        "detail": "Observed by an external scheduler adapter.",
        "scheduler_message_id": "poll-0001",
        "lock_timeout_seconds": 0.25,
    }


def _query_request(
    fixture: HandoffFixture,
    prepared: dict[str, Any],
    *,
    job_id: str = "73421",
) -> dict[str, Any]:
    return {
        "workspace_root": str(fixture.workspace_root),
        "project_id": fixture.project_id,
        "bundle_id": prepared["bundle_id"],
        "expected_manifest_sha256": prepared["manifest_sha256"],
        "identity": _identity(job_id),
    }


def _journal_events(prepared: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(prepared["events_path"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_prepare_preview_is_read_only(
    handoff_fixture: HandoffFixture,
) -> None:
    project_dir = handoff_fixture.workspace_root / handoff_fixture.project_id
    before = _filesystem_snapshot(project_dir)

    preview = _prepare(handoff_fixture, execution_mode="preview")

    assert preview["status"] == "preview"
    assert preview["write_performed"] is False
    assert preview["publication"] == []
    assert preview["shell_execution_performed"] is False
    assert preview["ssh_execution_performed"] is False
    assert preview["scheduler_execution_performed"] is False
    assert not (project_dir / "remote_handoffs").exists()
    assert _filesystem_snapshot(project_dir) == before


def test_manifest_json_read_rejects_growth_during_descriptor_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b'{"schema":"test"}\n')
    _grow_file_during_descriptor_read(monkeypatch, manifest_path)

    with pytest.raises(RemoteHandoffBindingError, match="changed while it was being read"):
        remote_handoff_module._read_json_object(
            manifest_path,
            label="remote handoff manifest",
            max_bytes=1024,
        )


def test_manifest_artifact_read_rejects_growth_during_descriptor_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_dir = tmp_path / "bundle"
    artifacts_dir = bundle_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    artifact_payloads = {
        "model_spec": ("artifacts/model_spec.json", b"{}\n"),
        "castep_script": ("artifacts/castep_script.pl", b"# script\n"),
        "input_structure": ("artifacts/structure.cif", b"data_test\n"),
    }
    artifacts: list[dict[str, Any]] = []
    for role, (relative_path, payload) in artifact_payloads.items():
        artifact_path = bundle_dir / relative_path
        artifact_path.write_bytes(payload)
        artifacts.append(
            {
                "role": role,
                "bundled_relative_path": relative_path,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
    growing_path = bundle_dir / "artifacts" / "model_spec.json"
    _grow_file_during_descriptor_read(monkeypatch, growing_path)

    with pytest.raises(RemoteHandoffBindingError, match="changed while it was being read"):
        remote_handoff_module._verify_manifest_artifacts(
            bundle_dir=bundle_dir,
            manifest={"artifacts": artifacts},
        )


def test_event_journal_read_rejects_growth_during_descriptor_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_id = "bounded-read"
    manifest_sha256 = "a" * 64
    prepared = _signed_event(
        sequence=1,
        event_type="prepared",
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
        previous_event_sha256=None,
        payload={},
    )
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(_canonical_json_bytes(prepared) + b"\n")
    _grow_file_during_descriptor_read(monkeypatch, events_path)

    with pytest.raises(RemoteHandoffHistoryError, match="changed while it was being read"):
        remote_handoff_module._read_event_journal(
            events_path,
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
        )


def test_event_journal_has_explicit_byte_and_event_count_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_id = "bounded-events"
    manifest_sha256 = "b" * 64
    first = _signed_event(
        sequence=1,
        event_type="prepared",
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
        previous_event_sha256=None,
        payload={},
    )
    second = _signed_event(
        sequence=2,
        event_type="submitted",
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
        previous_event_sha256=first["event_sha256"],
        payload={"identity": {"scheduler": "test", "job_id": "job-1"}},
    )
    content = b"".join(
        _canonical_json_bytes(event) + b"\n"
        for event in (first, second)
    )
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(content)

    monkeypatch.setattr(
        remote_handoff_module,
        "MAX_EVENT_JOURNAL_BYTES",
        len(content) - 1,
    )
    with pytest.raises(RemoteHandoffHistoryError, match="byte read limit"):
        remote_handoff_module._read_event_journal(
            events_path,
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
        )

    monkeypatch.setattr(
        remote_handoff_module,
        "MAX_EVENT_JOURNAL_BYTES",
        len(content),
    )
    monkeypatch.setattr(remote_handoff_module, "MAX_EVENT_COUNT", 1)
    with pytest.raises(RemoteHandoffHistoryError, match="event-count limit"):
        remote_handoff_module._parse_event_journal(
            content,
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
        )


def test_execute_requires_the_exact_preview_manifest_and_rejects_input_drift(
    handoff_fixture: HandoffFixture,
) -> None:
    project_dir = handoff_fixture.workspace_root / handoff_fixture.project_id
    before = _filesystem_snapshot(project_dir)
    preview = prepare_remote_castep_bundle(handoff_fixture.request)
    execute_request = {
        **handoff_fixture.request,
        "execution_mode": "execute",
    }

    with pytest.raises(
        ValidationError,
        match="expected_preview_manifest_sha256",
    ):
        prepare_remote_castep_bundle(execute_request)

    with pytest.raises(
        RemoteHandoffBindingError,
        match="does not match the exact preview",
    ):
        prepare_remote_castep_bundle(
            {
                **execute_request,
                "expected_preview_manifest_sha256": "0" * 64,
            }
        )

    handoff_fixture.input_path.write_text(
        "data_silicon\n_cell_length_a 5.500\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RemoteHandoffBindingError,
        match="does not match the exact preview",
    ):
        prepare_remote_castep_bundle(
            {
                **execute_request,
                "expected_input_sha256": _sha256(
                    handoff_fixture.input_path.read_bytes()
                ),
                "expected_preview_manifest_sha256": preview["manifest_sha256"],
            }
        )

    assert not (project_dir / "remote_handoffs").exists()
    expected_after_source_change = dict(before)
    input_relative = handoff_fixture.input_path.relative_to(project_dir).as_posix()
    changed_content = handoff_fixture.input_path.read_bytes()
    changed_stat = handoff_fixture.input_path.stat()
    expected_after_source_change[input_relative] = (
        len(changed_content),
        changed_stat.st_mtime_ns,
        _sha256(changed_content),
    )
    assert _filesystem_snapshot(project_dir) == expected_after_source_change


def test_prepare_rejects_link_like_handoff_ancestor(
    handoff_fixture: HandoffFixture,
) -> None:
    preview = prepare_remote_castep_bundle(handoff_fixture.request)
    project_dir = handoff_fixture.workspace_root / handoff_fixture.project_id
    handoff_root = project_dir / "remote_handoffs"
    outside = handoff_fixture.workspace_root / "outside_handoffs"
    outside.mkdir()
    try:
        handoff_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(
        RemoteHandoffBindingError,
        match="symbolic link, junction, or reparse point",
    ):
        prepare_remote_castep_bundle(
            {
                **handoff_fixture.request,
                "execution_mode": "execute",
                "expected_preview_manifest_sha256": preview["manifest_sha256"],
            }
        )

    assert list(outside.iterdir()) == []


def test_prepare_rejects_simulated_reparse_ancestor(
    handoff_fixture: HandoffFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = prepare_remote_castep_bundle(handoff_fixture.request)
    project_dir = handoff_fixture.workspace_root / handoff_fixture.project_id
    handoff_root = project_dir / "remote_handoffs"
    handoff_root.mkdir()
    original_is_link_like = remote_handoff_module._is_link_like

    def simulated_reparse(path: Path) -> bool:
        return path == handoff_root or original_is_link_like(path)

    monkeypatch.setattr(
        remote_handoff_module,
        "_is_link_like",
        simulated_reparse,
    )
    with pytest.raises(
        RemoteHandoffBindingError,
        match="symbolic link, junction, or reparse point",
    ):
        prepare_remote_castep_bundle(
            {
                **handoff_fixture.request,
                "execution_mode": "execute",
                "expected_preview_manifest_sha256": preview["manifest_sha256"],
            }
        )

    assert list(handoff_root.iterdir()) == []


def test_immutable_publication_never_exposes_a_partial_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "bundle" / "manifest.json"
    payload = b'{"complete":true}\n'
    original_write_all = remote_handoff_module._write_all

    def fail_after_partial_write(file_descriptor: int, content: bytes) -> None:
        os.write(file_descriptor, content[:5])
        raise OSError("injected write interruption")

    monkeypatch.setattr(
        remote_handoff_module,
        "_write_all",
        fail_after_partial_write,
    )
    with pytest.raises(OSError, match="injected write interruption"):
        remote_handoff_module._publish_immutable_file(
            destination,
            payload,
            allowed_root=tmp_path,
        )

    assert not os.path.lexists(destination)
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []

    monkeypatch.setattr(
        remote_handoff_module,
        "_write_all",
        original_write_all,
    )
    original_link = os.link
    claim_attempted = False

    def fail_atomic_claim(source: str | Path, target: str | Path) -> None:
        nonlocal claim_attempted
        claim_attempted = True
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        assert source_path.read_bytes() == payload
        assert not os.path.lexists(target_path)
        raise OSError("injected atomic claim interruption")

    monkeypatch.setattr(remote_handoff_module.os, "link", fail_atomic_claim)
    with pytest.raises(OSError, match="injected atomic claim interruption"):
        remote_handoff_module._publish_immutable_file(
            destination,
            payload,
            allowed_root=tmp_path,
        )
    assert claim_attempted is True
    assert not os.path.lexists(destination)
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []

    monkeypatch.setattr(remote_handoff_module.os, "link", original_link)
    assert (
        remote_handoff_module._publish_immutable_file(
            destination,
            payload,
            allowed_root=tmp_path,
        )
        == "published"
    )
    assert destination.read_bytes() == payload
    assert (
        remote_handoff_module._publish_immutable_file(
            destination,
            payload,
            allowed_root=tmp_path,
        )
        == "verified_existing"
    )


def test_remote_handoff_has_no_transport_or_process_execution_path() -> None:
    tree = ast.parse(inspect.getsource(remote_handoff_module))
    forbidden_import_roots = {
        "asyncssh",
        "fabric",
        "ftplib",
        "http",
        "paramiko",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    imported_roots: set[str] = set()
    forbidden_os_calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.partition(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and (
                node.func.attr in {"popen", "startfile", "system"}
                or node.func.attr.startswith(("exec", "spawn"))
            )
        ):
            forbidden_os_calls.add(node.func.attr)

    assert not imported_roots.intersection(forbidden_import_roots)
    assert forbidden_os_calls == set()


def test_prepare_execute_publishes_immutable_hash_bound_bundle(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)

    assert prepared["status"] == "prepared"
    assert prepared["write_performed"] is True
    assert prepared["artifact_integrity_status"] == "verified"
    assert prepared["event_count"] == 1
    assert len(prepared["bundle_id"]) <= 200
    manifest_path = Path(prepared["manifest_path"])
    manifest_bytes = manifest_path.read_bytes()
    assert _sha256(manifest_bytes) == prepared["manifest_sha256"]
    manifest = json.loads(manifest_bytes)
    binding = manifest["revision_binding"]
    assert binding == {
        "expected_revision": handoff_fixture.revision,
        "current_pointer_sha256": _sha256(
            (
                handoff_fixture.workspace_root
                / handoff_fixture.project_id
                / "current.json"
            ).read_bytes()
        ),
        "spec_sha256": handoff_fixture.request["expected_spec_sha256"],
        "script_sha256": handoff_fixture.request["expected_script_sha256"],
        "input_sha256": handoff_fixture.request["expected_input_sha256"],
        "deterministic_script_verified": True,
    }
    assert manifest["calculation"] == {
        "module": "CASTEP",
        "task": "Energy",
        "calculation_name": "energy_baseline",
        "requested_cores": 32,
    }
    first_bundle_snapshot = _filesystem_snapshot(Path(prepared["bundle_dir"]))

    repeated = _prepare(handoff_fixture)

    assert repeated["manifest_sha256"] == prepared["manifest_sha256"]
    assert repeated["prepared_event_status"] == "verified_existing"
    assert repeated["write_performed"] is False
    assert repeated["event_count"] == 1
    assert all(
        item["status"] == "verified_existing"
        for item in repeated["publication"]
    )
    assert _filesystem_snapshot(Path(prepared["bundle_dir"])) == first_bundle_snapshot

    bundled_input = next(
        Path(item["path"])
        for item in prepared["verified_artifacts"]
        if item["role"] == "input_structure"
    )
    bundled_input_before = bundled_input.read_bytes()
    handoff_fixture.input_path.write_text("tampered source", encoding="utf-8")
    with pytest.raises(
        RemoteHandoffBindingError,
        match="input SHA-256",
    ):
        _prepare(handoff_fixture)
    assert bundled_input.read_bytes() == bundled_input_before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("expected_revision", 99, "expected current revision"),
        ("expected_spec_sha256", "0" * 64, "spec SHA-256"),
        ("expected_script_sha256", "0" * 64, "script SHA-256"),
        ("expected_input_sha256", "0" * 64, "input SHA-256"),
        ("task", "GeometryOptimization", "task does not match"),
    ],
)
def test_prepare_rejects_revision_or_artifact_binding_mismatch_without_writes(
    handoff_fixture: HandoffFixture,
    field: str,
    value: Any,
    message: str,
) -> None:
    request = dict(handoff_fixture.request)
    request[field] = value
    request["execution_mode"] = "execute"
    request["expected_preview_manifest_sha256"] = "0" * 64
    project_dir = handoff_fixture.workspace_root / handoff_fixture.project_id
    before = _filesystem_snapshot(project_dir)

    with pytest.raises(RemoteHandoffBindingError, match=message):
        prepare_remote_castep_bundle(request)

    assert not (project_dir / "remote_handoffs").exists()
    assert _filesystem_snapshot(project_dir) == before


def test_prepare_rejects_a_rehashed_but_non_deterministic_script(
    handoff_fixture: HandoffFixture,
) -> None:
    handoff_fixture.script_path.write_text(
        handoff_fixture.script_path.read_text(encoding="utf-8")
        + "\n# caller-supplied drift\n",
        encoding="utf-8",
        newline="",
    )
    request = {
        **handoff_fixture.request,
        "expected_script_sha256": _sha256(
            handoff_fixture.script_path.read_bytes()
        ),
        "expected_preview_manifest_sha256": "0" * 64,
        "execution_mode": "execute",
    }
    project_dir = handoff_fixture.workspace_root / handoff_fixture.project_id
    before = _filesystem_snapshot(project_dir)

    with pytest.raises(
        RemoteHandoffBindingError,
        match="differs from deterministic translator output",
    ):
        prepare_remote_castep_bundle(request)

    assert not (project_dir / "remote_handoffs").exists()
    assert _filesystem_snapshot(project_dir) == before


def test_record_submission_requires_exact_identity_and_manifest(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    request = _submission_request(handoff_fixture, prepared)
    missing_scheduler_id = {
        **request,
        "identity": {
            "scheduler_kind": "slurm",
            "job_id": "73421",
        },
    }
    missing_job_id = {
        **request,
        "identity": {
            "scheduler_kind": "slurm",
            "scheduler_id": "cluster-alpha",
        },
    }
    with pytest.raises(ValidationError):
        RemoteSubmissionRecordRequest.model_validate(missing_scheduler_id)
    with pytest.raises(ValidationError):
        RemoteSubmissionRecordRequest.model_validate(missing_job_id)

    journal_before = Path(prepared["events_path"]).read_bytes()
    with pytest.raises(
        RemoteHandoffBindingError,
        match="manifest SHA-256",
    ):
        record_remote_submission(
            {
                **request,
                "expected_manifest_sha256": "0" * 64,
            }
        )
    assert Path(prepared["events_path"]).read_bytes() == journal_before

    submitted = record_remote_submission(request)

    assert submitted["status"] == "submitted"
    assert submitted["identity"] == _identity()
    assert submitted["submission_performed_by_this_module"] is False
    assert submitted["shell_execution_performed"] is False
    assert [event["event_type"] for event in _journal_events(prepared)] == [
        "prepared",
        "submitted",
    ]


def test_conflicting_submission_is_rejected(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    request = _submission_request(handoff_fixture, prepared)
    first = record_remote_submission(request)
    journal_after_first = Path(prepared["events_path"]).read_bytes()

    repeated = record_remote_submission(request)

    assert first["event_status"] == "appended"
    assert first["write_performed"] is True
    assert repeated["event_status"] == "verified_existing"
    assert repeated["write_performed"] is False
    assert repeated["event_count"] == 2
    assert Path(prepared["events_path"]).read_bytes() == journal_after_first

    with pytest.raises(
        RemoteHandoffBindingError,
        match="different submission identity",
    ):
        record_remote_submission(
            _submission_request(
                handoff_fixture,
                prepared,
                job_id="different-job",
            )
        )
    assert Path(prepared["events_path"]).read_bytes() == journal_after_first


def test_status_is_local_read_only_and_identity_bound(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    record_remote_submission(_submission_request(handoff_fixture, prepared))
    recorded = record_remote_status(_status_request(handoff_fixture, prepared))
    bundle_dir = Path(prepared["bundle_dir"])
    before = _filesystem_snapshot(bundle_dir)

    status = read_remote_job_status(
        _query_request(handoff_fixture, prepared)
    )

    assert status["status"] == "running"
    assert status["source"] == "local_append_only_event_journal"
    assert status["write_performed"] is False
    assert status["filesystem_write_performed"] is False
    assert status["remote_query_performed"] is False
    assert status["shell_execution_performed"] is False
    assert status["ssh_execution_performed"] is False
    assert status["scheduler_execution_performed"] is False
    assert status["latest_status_event"]["event_sha256"] == recorded["event"][
        "event_sha256"
    ]
    assert _filesystem_snapshot(bundle_dir) == before

    with pytest.raises(
        RemoteHandoffBindingError,
        match="identity does not match",
    ):
        read_remote_job_status(
            _query_request(
                handoff_fixture,
                prepared,
                job_id="wrong-job",
            )
        )
    assert _filesystem_snapshot(bundle_dir) == before


def test_events_are_append_only_and_hash_linked(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    record_remote_submission(_submission_request(handoff_fixture, prepared))
    record_remote_status(_status_request(handoff_fixture, prepared))
    events = _journal_events(prepared)

    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [event["event_type"] for event in events] == [
        "prepared",
        "submitted",
        "status",
    ]
    previous: str | None = None
    for event in events:
        assert event["schema"] == REMOTE_HANDOFF_EVENT_SCHEMA
        assert event["manifest_sha256"] == prepared["manifest_sha256"]
        assert event["previous_event_sha256"] == previous
        unsigned = dict(event)
        declared = unsigned.pop("event_sha256")
        assert declared == _sha256(_canonical_json_bytes(unsigned))
        previous = declared


def test_event_count_limit_rejects_append_without_modifying_journal(
    handoff_fixture: HandoffFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare(handoff_fixture)
    events_path = Path(prepared["events_path"])
    before = events_path.read_bytes()
    monkeypatch.setattr(remote_handoff_module, "MAX_EVENT_COUNT", 1)

    with pytest.raises(RemoteHandoffHistoryError, match="event-count limit"):
        record_remote_submission(
            _submission_request(handoff_fixture, prepared)
        )

    assert events_path.read_bytes() == before


def test_status_rejects_tampered_event_chain(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    record_remote_submission(_submission_request(handoff_fixture, prepared))
    events_path = Path(prepared["events_path"])
    original = events_path.read_bytes()
    tampered = original.replace(
        b'"channel":"manual_scheduler_submission"',
        b'"channel":"external_orchestrator"',
    )
    assert tampered != original
    events_path.write_bytes(tampered)

    with pytest.raises(
        RemoteHandoffHistoryError,
        match="SHA-256 mismatch",
    ):
        read_remote_job_status(_query_request(handoff_fixture, prepared))


def test_status_rejects_a_rehashed_status_for_another_job(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    record_remote_submission(_submission_request(handoff_fixture, prepared))
    record_remote_status(_status_request(handoff_fixture, prepared))
    events = _journal_events(prepared)
    events[-1]["payload"]["identity"]["job_id"] = "forged-job"
    unsigned = dict(events[-1])
    unsigned.pop("event_sha256")
    events[-1]["event_sha256"] = _sha256(_canonical_json_bytes(unsigned))
    Path(prepared["events_path"]).write_bytes(
        b"".join(_canonical_json_bytes(event) + b"\n" for event in events)
    )

    with pytest.raises(
        RemoteHandoffHistoryError,
        match="not bound to the submission identity",
    ):
        read_remote_job_status(_query_request(handoff_fixture, prepared))


def test_terminal_status_cannot_be_rewritten(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    record_remote_submission(_submission_request(handoff_fixture, prepared))
    record_remote_status(_status_request(handoff_fixture, prepared))
    record_remote_status(
        _status_request(
            handoff_fixture,
            prepared,
            state="succeeded",
            observed_at="2026-07-24T10:02:00+08:00",
        )
    )
    journal_before = Path(prepared["events_path"]).read_bytes()

    with pytest.raises(
        RemoteHandoffBindingError,
        match="terminal remote state succeeded",
    ):
        record_remote_status(
            _status_request(
                handoff_fixture,
                prepared,
                state="failed",
                observed_at="2026-07-24T10:03:00+08:00",
            )
        )
    assert Path(prepared["events_path"]).read_bytes() == journal_before


def test_per_job_advisory_lock_blocks_a_second_writer(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    request = _submission_request(handoff_fixture, prepared)
    request["lock_timeout_seconds"] = 0.0
    lock_path = Path(prepared["lock_path"])
    journal_before = Path(prepared["events_path"]).read_bytes()

    with _remote_job_write_lock(lock_path, timeout_seconds=0.0):
        with pytest.raises(RemoteHandoffBusyError, match="busy"):
            record_remote_submission(request)

    assert Path(prepared["events_path"]).read_bytes() == journal_before


def test_read_only_status_waits_for_the_existing_writer_lock(
    handoff_fixture: HandoffFixture,
) -> None:
    prepared = _prepare(handoff_fixture)
    record_remote_submission(_submission_request(handoff_fixture, prepared))
    query = _query_request(handoff_fixture, prepared)
    query["lock_timeout_seconds"] = 1.0
    lock_path = Path(prepared["lock_path"])
    bundle_dir = Path(prepared["bundle_dir"])
    before = _filesystem_snapshot(bundle_dir)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        with _remote_job_write_lock(lock_path, timeout_seconds=0.0):
            future = executor.submit(read_remote_job_status, query)
            time.sleep(0.05)
            assert not future.done()
        status = future.result(timeout=1.0)

    assert status["status"] == "queued"
    assert status["read_transaction"]["access"] == "read"
    assert status["read_transaction"]["filesystem_write_performed"] is False
    assert _filesystem_snapshot(bundle_dir) == before
