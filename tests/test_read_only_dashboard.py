from __future__ import annotations

import hashlib
import http.client
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

import pytest

import material_studio_mcp_server.read_only_dashboard as dashboard_module
from material_studio_mcp_server.read_only_dashboard import (
    DashboardError,
    DashboardLimits,
    WorkspaceSnapshotService,
    create_dashboard_server,
)


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    project = workspace / "alpha_project"
    output = project / "outputs" / "r001"
    nested = output / "diagnostics"
    nested.mkdir(parents=True)
    current = {
        "project_id": "alpha_project",
        "revision": 1,
        "spec_path": str(project / "revisions" / "r001_model_spec.json"),
        "script_path": str(project / "scripts" / "r001_build.pl"),
        "spec": {
            "project_id": "alpha_project",
            "revision": 1,
            "software": "BIOVIA Materials Studio",
            "model_type": "crystal",
            "model": {"name": "Silicon"},
            "simulation": {"module": "CASTEP", "task": "Energy"},
        },
    }
    (project / "current.json").write_text(
        json.dumps(current, indent=2),
        encoding="utf-8",
    )
    (output / "report.json").write_text(
        json.dumps({"status": "ready", "energy_eV": -10.25}),
        encoding="utf-8",
    )
    (nested / "metrics.csv").write_text(
        "metric,value\nenergy,-10.25\n",
        encoding="utf-8",
    )
    (output / "notes.txt").write_text("read-only artifact\n", encoding="utf-8")
    (output / "not_served.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    return workspace, output


def _workspace_inventory(root: Path) -> dict[str, tuple[Any, ...]]:
    inventory: dict[str, tuple[Any, ...]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        details = path.lstat()
        if path.is_symlink():
            inventory[relative] = ("link", os.readlink(path), details.st_mtime_ns)
        elif path.is_file():
            payload = path.read_bytes()
            inventory[relative] = (
                "file",
                len(payload),
                details.st_mtime_ns,
                hashlib.sha256(payload).hexdigest(),
            )
        elif path.is_dir():
            inventory[relative] = ("directory", details.st_mtime_ns)
        else:
            inventory[relative] = ("other", details.st_mode, details.st_mtime_ns)
    return inventory


@contextmanager
def _running_server(workspace: Path, *, limits: DashboardLimits | None = None) -> Iterator[Any]:
    server = create_dashboard_server(
        workspace,
        host="127.0.0.1",
        port=0,
        limits=limits,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    server: Any,
    method: str,
    target: str,
    *,
    host_header: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    if host_header is None:
        connection.request(method, target)
    else:
        connection.putrequest(method, target, skip_host=True)
        connection.putheader("Host", host_header)
        connection.endheaders()
    response = connection.getresponse()
    payload = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, payload


def test_missing_workspace_is_not_created(tmp_path: Path) -> None:
    workspace = tmp_path / "does-not-exist"

    with pytest.raises(DashboardError, match="will not create"):
        WorkspaceSnapshotService(workspace)

    assert not workspace.exists()


def test_snapshot_is_bounded_summary_and_does_not_write(tmp_path: Path) -> None:
    workspace, _ = _make_workspace(tmp_path)
    before = _workspace_inventory(workspace)

    snapshot = WorkspaceSnapshotService(workspace).snapshot()

    assert snapshot["read_only"] is True
    assert snapshot["project_count"] == 1
    project = snapshot["projects"][0]
    assert project["project_id"] == "alpha_project"
    assert project["revision"] == 1
    assert project["model_type"] == "crystal"
    assert project["model_name"] == "Silicon"
    assert project["simulation_module"] == "CASTEP"
    assert [item["path"] for item in project["artifact_index"]["items"]] == [
        "diagnostics/metrics.csv",
        "notes.txt",
        "report.json",
    ]
    assert _workspace_inventory(workspace) == before


@pytest.mark.parametrize(
    ("embedded_field", "corrupt_value"),
    [
        ("project_id", "different_project"),
        ("revision", 2),
    ],
)
def test_snapshot_rejects_corrupt_embedded_current_identity(
    tmp_path: Path,
    embedded_field: str,
    corrupt_value: Any,
) -> None:
    workspace, _ = _make_workspace(tmp_path)
    current_path = workspace / "alpha_project" / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["spec"][embedded_field] = corrupt_value
    current_path.write_text(json.dumps(current), encoding="utf-8")
    before = _workspace_inventory(workspace)

    snapshot = WorkspaceSnapshotService(workspace).snapshot()

    project = snapshot["projects"][0]
    assert project["status"] == "unavailable"
    assert project["error"] == "current_identity_mismatch"
    assert project["revision"] is None
    assert project["artifact_index"]["status"] == "unavailable"
    assert _workspace_inventory(workspace) == before


def test_oversized_current_json_is_reported_without_unbounded_read(tmp_path: Path) -> None:
    workspace, _ = _make_workspace(tmp_path)
    current_path = workspace / "alpha_project" / "current.json"
    current_path.write_text('{"padding":"' + ("x" * 2_000) + '"}', encoding="utf-8")
    service = WorkspaceSnapshotService(
        workspace,
        limits=DashboardLimits(max_json_bytes=256),
    )

    snapshot = service.snapshot()

    assert snapshot["projects"][0]["status"] == "unavailable"
    assert snapshot["projects"][0]["error"] == "file_too_large"


def test_artifact_index_and_read_are_bounded(tmp_path: Path) -> None:
    workspace, output = _make_workspace(tmp_path)
    (output / "large.txt").write_bytes(b"x" * 101)
    service = WorkspaceSnapshotService(
        workspace,
        limits=DashboardLimits(max_artifact_bytes=100),
    )

    index = service.list_artifacts("alpha_project", 1)
    indexed = {item["path"]: item for item in index["items"]}
    assert indexed["large.txt"]["readable"] is False
    assert "not_served.html" not in indexed

    artifact = service.read_artifact("alpha_project", "1", "report.json")
    assert artifact.project_id == "alpha_project"
    assert artifact.revision == 1
    assert artifact.content_type.startswith("application/json")
    assert json.loads(artifact.payload) == {"status": "ready", "energy_eV": -10.25}
    assert artifact.content_sha256 == hashlib.sha256(artifact.payload).hexdigest()

    with pytest.raises(DashboardError) as error:
        service.read_artifact("alpha_project", 1, "large.txt")
    assert error.value.status == 413

    with pytest.raises(DashboardError) as error:
        service.read_artifact("alpha_project", 1, "not_served.html")
    assert error.value.status == 415


@pytest.mark.parametrize(
    "relative_path",
    [
        "../current.json",
        "diagnostics/../../current.json",
        "/absolute/report.json",
        "C:/Windows/win.ini",
        "diagnostics\\metrics.csv",
        "diagnostics/./metrics.csv",
    ],
)
def test_artifact_path_traversal_is_rejected(
    tmp_path: Path,
    relative_path: str,
) -> None:
    workspace, _ = _make_workspace(tmp_path)
    service = WorkspaceSnapshotService(workspace)

    with pytest.raises(DashboardError):
        service.read_artifact("alpha_project", 1, relative_path)


def test_symlink_artifact_and_directory_are_rejected(tmp_path: Path) -> None:
    workspace, output = _make_workspace(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":true}', encoding="utf-8")
    file_link = output / "linked.json"
    directory_link = output / "linked-directory"
    try:
        file_link.symlink_to(outside)
        directory_link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")

    service = WorkspaceSnapshotService(workspace)
    index = service.list_artifacts("alpha_project", 1)

    assert index["rejected_link_count"] == 2
    assert "linked.json" not in {item["path"] for item in index["items"]}
    with pytest.raises(DashboardError) as error:
        service.read_artifact("alpha_project", 1, "linked.json")
    assert error.value.status == 403
    with pytest.raises(DashboardError) as error:
        service.read_artifact("alpha_project", 1, "linked-directory/outside.json")
    assert error.value.status == 403


def test_link_like_project_is_not_listed(tmp_path: Path) -> None:
    workspace, _ = _make_workspace(tmp_path)
    linked_project = workspace / "linked_project"
    try:
        linked_project.symlink_to(workspace / "alpha_project", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")

    listing = WorkspaceSnapshotService(workspace).list_projects()

    assert [item["project_id"] for item in listing["items"]] == ["alpha_project"]


def test_link_like_artifact_check_is_enforced_without_platform_symlink_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _ = _make_workspace(tmp_path)
    service = WorkspaceSnapshotService(workspace)
    original_check = dashboard_module._is_link_like

    def classify_report_as_link(path: Path) -> bool:
        return path.name == "report.json" or original_check(path)

    monkeypatch.setattr(dashboard_module, "_is_link_like", classify_report_as_link)

    with pytest.raises(DashboardError) as error:
        service.read_artifact("alpha_project", 1, "report.json")
    assert error.value.status == 403
    assert error.value.code == "link_path_forbidden"


def test_invalid_json_jsonl_and_raster_payloads_are_rejected(tmp_path: Path) -> None:
    workspace, output = _make_workspace(tmp_path)
    (output / "invalid.json").write_text("{", encoding="utf-8")
    (output / "invalid.jsonl").write_text('{"ok":true}\nnot-json\n', encoding="utf-8")
    (output / "fake.png").write_text("<html>not a png</html>", encoding="utf-8")
    service = WorkspaceSnapshotService(workspace)

    for path in ("invalid.json", "invalid.jsonl", "fake.png"):
        with pytest.raises(DashboardError) as error:
            service.read_artifact("alpha_project", 1, path)
        assert error.value.status == 422


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.4", "::", "localhost"])
def test_dashboard_refuses_nonliteral_or_nonloopback_bind(
    tmp_path: Path,
    host: str,
) -> None:
    workspace, _ = _make_workspace(tmp_path)

    with pytest.raises(DashboardError):
        create_dashboard_server(workspace, host=host, port=0)


def test_dashboard_get_head_and_method_policy_with_security_headers(tmp_path: Path) -> None:
    workspace, _ = _make_workspace(tmp_path)
    before = _workspace_inventory(workspace)

    with _running_server(workspace) as server:
        status, headers, body = _request(server, "GET", "/")
        assert status == 200
        assert b"READ-ONLY LOCAL VIEW" in body
        assert headers["cache-control"].startswith("no-store")
        assert "default-src 'none'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"

        status, headers, body = _request(server, "GET", "/api/snapshot")
        assert status == 200
        snapshot = json.loads(body)
        assert snapshot["project_count"] == 1
        assert headers["content-type"].startswith("application/json")

        artifact_query = urlencode(
            {
                "project_id": "alpha_project",
                "revision": "1",
                "path": "report.json",
            }
        )
        status, headers, body = _request(
            server,
            "HEAD",
            "/api/artifact?" + artifact_query,
        )
        assert status == 200
        assert body == b""
        assert int(headers["content-length"]) > 0
        assert headers["etag"].startswith('"sha256-')

        status, headers, body = _request(server, "POST", "/api/snapshot")
        assert status == 405
        assert headers["allow"] == "GET, HEAD"
        assert json.loads(body)["error"] == "method_not_allowed"
        assert headers["cache-control"].startswith("no-store")

    assert _workspace_inventory(workspace) == before


def test_dashboard_rejects_dns_rebinding_host_header(tmp_path: Path) -> None:
    workspace, _ = _make_workspace(tmp_path)

    with _running_server(workspace) as server:
        status, headers, payload = _request(
            server,
            "GET",
            "/api/snapshot",
            host_header="attacker.example",
        )

    assert status == 421
    assert json.loads(payload)["error"] == "unsafe_host_header"
    assert headers["cache-control"].startswith("no-store")
    assert "default-src 'none'" in headers["content-security-policy"]


def test_dashboard_rejects_encoded_traversal_and_extra_query_fields(tmp_path: Path) -> None:
    workspace, _ = _make_workspace(tmp_path)

    with _running_server(workspace) as server:
        status, _, payload = _request(
            server,
            "GET",
            "/api/artifact?project_id=alpha_project&revision=1&path=%2E%2E%2Fcurrent.json",
        )
        assert status == 400
        assert json.loads(payload)["error"] == "invalid_artifact_path"

        status, _, payload = _request(
            server,
            "GET",
            "/api/snapshot?write=true",
        )
        assert status == 400
        assert json.loads(payload)["error"] == "invalid_query"


def test_dashboard_http_artifact_limit_and_no_workspace_write(tmp_path: Path) -> None:
    workspace, output = _make_workspace(tmp_path)
    (output / "large.log").write_bytes(b"x" * 65)
    limits = DashboardLimits(max_artifact_bytes=64)
    before = _workspace_inventory(workspace)

    with _running_server(workspace, limits=limits) as server:
        query = urlencode(
            {
                "project_id": "alpha_project",
                "revision": "1",
                "path": "large.log",
            }
        )
        status, _, payload = _request(server, "GET", "/api/artifact?" + query)

    assert status == 413
    assert json.loads(payload)["error"] == "file_too_large"
    assert _workspace_inventory(workspace) == before
