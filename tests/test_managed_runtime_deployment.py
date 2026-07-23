from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from material_studio_mcp_server import runtime_deployment as deployment_module

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from material_studio_mcp_server.codex_config import (
    build_codex_config_snippet,
    diagnose_codex_config,
)
from material_studio_mcp_server.managed_runtime import (
    MANAGED_RUNTIME_MANIFEST,
    MANAGED_RUNTIME_SCHEMA,
    RUNTIME_MANIFEST_ARGUMENT,
    RUNTIME_MANIFEST_ENV,
    consume_runtime_manifest_argument,
    filesystem_io_path,
    managed_runtime_launch_cwd,
    managed_runtime_status,
    manifest_bytes,
    process_launch_path,
    require_managed_runtime_launcher_binding,
    runtime_content_snapshot,
    sha256_bytes,
)
from material_studio_mcp_server.runtime_deployment import (
    _DeploymentError,
    _archive_content_snapshot,
    _extract_archive_safely,
    apply_runtime_deployment,
    main,
    plan_runtime_deployment,
)
from material_studio_mcp_server.runtime_provenance import (
    RuntimeProvenanceTracker,
    runtime_deployment_status,
)
from material_studio_mcp_server.python_runtime import (
    python_runtime_contract,
    python_runtime_contract_sha256,
)


_CURRENT_PYTHON_RUNTIME_CONTRACT = python_runtime_contract()


def _write_minimal_runtime(root: Path) -> str:
    package = root / "src" / "material_studio_mcp_server"
    package.mkdir(parents=True)
    (root / "run_server.py").write_text("print('server')\n", encoding="utf-8")
    (root / "register_codex.py").write_text("print('register')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='materials-studio-mcp'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_runtime_module = (
        Path(__file__).parents[1]
        / "src"
        / "material_studio_mcp_server"
        / "python_runtime.py"
    )
    (package / "python_runtime.py").write_bytes(
        source_runtime_module.read_bytes()
    )
    (package / "server.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "schema.json").write_text('{"version": 1}\n', encoding="utf-8")
    snapshot = runtime_content_snapshot(root)
    payload = {
        "schema": MANAGED_RUNTIME_SCHEMA,
        "runtime_root": str(root.resolve()),
        "source": {
            "remote": "https://github.com/example/ms_mcp.git",
            "branch": "main",
            "upstream_ref": "origin/main",
            "commit": "a" * 40,
            "tree": "b" * 40,
            "commit_time": "2026-07-23T00:00:00+00:00",
        },
        "archive_sha256": "c" * 64,
        "content_snapshot": snapshot,
        "python_runtime_contract": _CURRENT_PYTHON_RUNTIME_CONTRACT,
        "entrypoints": {
            "mcp_server": "run_server.py",
            "codex_registration": "register_codex.py",
        },
        "immutability": {
            "path_is_commit_addressed": True,
            "existing_runtime_never_overwritten": True,
            "old_runtimes_never_deleted": True,
        },
    }
    encoded = manifest_bytes(payload)
    (root / MANAGED_RUNTIME_MANIFEST).write_bytes(encoded)
    return sha256_bytes(encoded)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _pushed_repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "source"
    remote = tmp_path / "remote.git"
    package = repository / "src" / "material_studio_mcp_server"
    package.mkdir(parents=True)
    (repository / "run_server.py").write_text("print('server')\n", encoding="utf-8")
    (repository / "register_codex.py").write_text(
        "print('register')\n",
        encoding="utf-8",
    )
    (repository / "pyproject.toml").write_text(
        "[project]\nname='materials-studio-mcp'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    source_runtime_module = (
        Path(__file__).parents[1]
        / "src"
        / "material_studio_mcp_server"
        / "python_runtime.py"
    )
    (package / "python_runtime.py").write_bytes(
        source_runtime_module.read_bytes()
    )
    (package / "server.py").write_text("VALUE = 1\n", encoding="utf-8")
    long_example = (
        package
        / "examples"
        / "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json"
    )
    long_example.parent.mkdir()
    long_example.write_text('{"kind": "long-path-fixture"}\n', encoding="utf-8")
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Runtime Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "runtime source")
    _git(repository.parent, "init", "--bare", str(remote))
    _git(repository, "branch", "-M", "main")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "main")
    return repository, remote


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_managed_runtime_verifies_manifest_binding_and_all_files(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    manifest_hash = _write_minimal_runtime(runtime)

    verified = managed_runtime_status(
        runtime,
        expected_manifest_sha256=manifest_hash,
    )
    (runtime / "src" / "material_studio_mcp_server" / "schema.json").write_text(
        '{"version": 2}\n',
        encoding="utf-8",
    )
    tampered = managed_runtime_status(
        runtime,
        expected_manifest_sha256=manifest_hash,
    )

    assert verified["status"] == "verified"
    assert verified["integrity_verified"] is True
    assert verified["manifest_binding_matches"] is True
    assert verified["source_commit"] == "a" * 40
    assert tampered["status"] == "integrity_failed"
    assert tampered["integrity_verified"] is False
    assert "managed_runtime_content_sha256_mismatch" in tampered["errors"]


def test_manifest_host_binding_rejects_resigned_content(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    original_manifest_hash = _write_minimal_runtime(runtime)
    manifest_path = runtime / MANAGED_RUNTIME_MANIFEST
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    (runtime / "run_server.py").write_text("print('changed')\n", encoding="utf-8")
    payload["content_snapshot"] = runtime_content_snapshot(runtime)
    resigned_content = manifest_bytes(payload)
    manifest_path.write_bytes(resigned_content)

    self_consistent = managed_runtime_status(runtime)
    host_bound = managed_runtime_status(
        runtime,
        expected_manifest_sha256=original_manifest_hash,
    )

    assert sha256_bytes(resigned_content) != original_manifest_hash
    assert self_consistent["integrity_verified"] is True
    assert host_bound["integrity_verified"] is False
    assert "manifest_sha256_binding_mismatch" in host_bound["errors"]


def test_runtime_snapshot_hashes_bytecode_and_rejects_links(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    baseline = runtime_content_snapshot(runtime)
    cache = runtime / "__pycache__"
    cache.mkdir()
    (cache / "source.pyc").write_bytes(b"cache")
    after_cache = runtime_content_snapshot(runtime)

    assert after_cache["status"] == "complete"
    assert after_cache["sha256"] != baseline["sha256"]
    assert after_cache["file_count"] == baseline["file_count"] + 1
    nested_manifest = runtime / "nested" / MANAGED_RUNTIME_MANIFEST
    nested_manifest.parent.mkdir()
    nested_manifest.write_text("{}\n", encoding="utf-8")
    after_nested_manifest = runtime_content_snapshot(runtime)
    assert after_nested_manifest["file_count"] == after_cache["file_count"] + 1
    (runtime / MANAGED_RUNTIME_MANIFEST).write_text("{}\n", encoding="utf-8")
    assert runtime_content_snapshot(runtime) == after_nested_manifest
    if hasattr(os, "symlink"):
        link = runtime / "linked.py"
        try:
            link.symlink_to(runtime / "source.py")
        except OSError:
            pytest.skip("symlink creation is not permitted on this host")
        linked = runtime_content_snapshot(runtime)
        assert linked["status"] == "incomplete"
        assert linked["unexpected_links"] == ["linked.py"]


def test_launcher_consumes_exact_manifest_binding(tmp_path: Path) -> None:
    digest = "a" * 64
    argv = ["run_server.py", RUNTIME_MANIFEST_ARGUMENT, digest, "--other"]
    environ: dict[str, str] = {}

    result = consume_runtime_manifest_argument(argv, environ)

    assert result == digest
    assert argv == ["run_server.py", "--other"]
    assert environ[RUNTIME_MANIFEST_ENV] == digest
    with pytest.raises(RuntimeError, match="requires a SHA-256"):
        consume_runtime_manifest_argument(
            ["run_server.py", RUNTIME_MANIFEST_ARGUMENT],
            {},
        )
    with pytest.raises(RuntimeError, match="conflicts"):
        consume_runtime_manifest_argument(
            ["run_server.py", RUNTIME_MANIFEST_ARGUMENT, digest],
            {RUNTIME_MANIFEST_ENV: "b" * 64},
        )

    runtime = tmp_path / "runtime"
    manifest_hash = _write_minimal_runtime(runtime)
    with pytest.raises(RuntimeError, match="requires --runtime-manifest"):
        require_managed_runtime_launcher_binding(
            runtime,
            None,
        )
    require_managed_runtime_launcher_binding(runtime, manifest_hash)
    if os.name == "nt":
        require_managed_runtime_launcher_binding(
            Path("\\\\?\\" + str(runtime.resolve())),
            manifest_hash,
        )
    (runtime / "run_server.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="integrity verification failed"):
        require_managed_runtime_launcher_binding(runtime, manifest_hash)


def test_managed_runtime_config_binds_manifest_argument_and_detects_drift(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    python = Path(sys.executable)
    manifest_hash = _write_minimal_runtime(runtime)
    config = tmp_path / "config.toml"
    snippet = build_codex_config_snippet(runtime, python_command=python)
    config.write_text(snippet, encoding="utf-8")

    payload = tomllib.loads(snippet)
    args = payload["mcp_servers"]["materials_studio"]["args"]
    ready = diagnose_codex_config(
        config_path=config,
        repo_root=runtime,
        python_command=python,
        include_snippet=False,
    )
    config.write_text(
        snippet.replace(
            f', "{RUNTIME_MANIFEST_ARGUMENT}", "{manifest_hash}"',
            "",
            1,
        ),
        encoding="utf-8",
    )
    drift = diagnose_codex_config(
        config_path=config,
        repo_root=runtime,
        python_command=python,
        include_snippet=False,
    )

    assert args == [
        str((runtime / "run_server.py").resolve()),
        RUNTIME_MANIFEST_ARGUMENT,
        manifest_hash,
    ]
    assert ready["status"] == "ready"
    assert ready["config_ready"] is True
    assert ready["managed_runtime"]["integrity_verified"] is True
    assert drift["status"] == "entrypoint_drift"
    assert drift["args_match"] is False


def test_config_registration_rejects_tampered_managed_runtime(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    python = Path(sys.executable)
    _write_minimal_runtime(runtime)
    (runtime / "run_server.py").write_text("print('tampered')\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")

    result = diagnose_codex_config(
        config_path=config,
        repo_root=runtime,
        python_command=python,
    )

    assert result["ok"] is False
    assert result["status"] == "managed_runtime_integrity_failed"
    assert result["active_config_modified"] is False


def test_python_runtime_drift_blocks_launch_and_registration(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    _write_minimal_runtime(runtime)
    manifest_path = runtime / MANAGED_RUNTIME_MANIFEST
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract = payload["python_runtime_contract"]
    contract["python"]["version"] = "0.0.0-drifted"
    contract["contract_sha256"] = python_runtime_contract_sha256(contract)
    payload["python_runtime_contract"] = contract
    manifest_content = manifest_bytes(payload)
    manifest_path.write_bytes(manifest_content)
    manifest_hash = sha256_bytes(manifest_content)

    structural = managed_runtime_status(
        runtime,
        expected_manifest_sha256=manifest_hash,
    )
    runtime_verified = managed_runtime_status(
        runtime,
        expected_manifest_sha256=manifest_hash,
        verify_python_runtime=True,
    )
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    registration = diagnose_codex_config(
        config_path=config,
        repo_root=runtime,
        python_command=sys.executable,
    )

    assert structural["integrity_verified"] is True
    assert structural["python_runtime_verified"] is None
    assert runtime_verified["integrity_verified"] is False
    assert runtime_verified["python_runtime_verified"] is False
    assert "python_runtime_contract_mismatch" in runtime_verified["errors"]
    with pytest.raises(RuntimeError, match="integrity verification failed"):
        require_managed_runtime_launcher_binding(runtime, manifest_hash)
    assert registration["status"] == "managed_runtime_python_environment_mismatch"
    assert registration["managed_runtime"]["python_runtime_command_matches"] is True
    assert (
        registration["managed_runtime"]["python_runtime_environment_matches"]
        is False
    )


def test_config_registration_rejects_different_python_command(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    _write_minimal_runtime(runtime)
    other_python = tmp_path / "python.exe"
    other_python.write_bytes(b"not-the-bound-python")
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")

    result = diagnose_codex_config(
        config_path=config,
        repo_root=runtime,
        python_command=other_python,
    )

    assert result["ok"] is False
    assert result["status"] == "managed_runtime_python_command_mismatch"
    assert result["active_config_modified"] is False


def test_runtime_provenance_uses_managed_commit_and_blocks_non_python_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    manifest_hash = _write_minimal_runtime(runtime)
    package = runtime / "src" / "material_studio_mcp_server"
    monkeypatch.setenv(RUNTIME_MANIFEST_ENV, manifest_hash)

    deployment = runtime_deployment_status(
        package,
        entrypoint=runtime / "run_server.py",
        process_cwd=runtime,
    )
    tracker = RuntimeProvenanceTracker.capture(package, process_id=1234)
    current = tracker.status()
    (package / "schema.json").write_text('{"tampered": true}\n', encoding="utf-8")
    changed = tracker.status()

    assert deployment["status"] == "managed_immutable_runtime"
    assert deployment["git"]["status"] == "managed_runtime_manifest"
    assert deployment["git"]["head_commit"] == "a" * 40
    assert current["source_current"] is True
    assert current["managed_runtime"]["integrity_verified"] is True
    assert changed["status"] == "managed_runtime_integrity_failed"
    assert changed["source_current"] is False
    assert changed["restart_required"] is True


def test_runtime_deployment_apply_is_immutable_and_returns_registration_plan(
    tmp_path: Path,
) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / ("runtimes_" + ("x" * 100))
    config = tmp_path / "config.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    config_before = _sha256(config)
    plan = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
        config_path=config,
    )

    result = apply_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
        config_path=config,
        expected_plan_id=plan["runtime_deployment_plan_id"],
        validate_protocol=False,
    )
    target = Path(result["target_runtime_path"])
    first_manifest = filesystem_io_path(
        target / MANAGED_RUNTIME_MANIFEST
    ).read_bytes()
    reused_plan = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
        config_path=config,
    )
    reused = apply_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
        config_path=config,
        expected_plan_id=reused_plan["runtime_deployment_plan_id"],
        validate_protocol=False,
    )

    assert plan["status"] == "runtime_deployment_ready"
    assert plan["source_pushed_to_upstream"] is True
    assert plan["python_runtime_probe"]["status"] == "complete"
    assert plan["python_runtime_contract"]["status"] == "complete"
    assert len(plan["python_runtime_contract_sha256"]) == 64
    assert Path(plan["target_runtime_path"]).parent.name == plan["source_commit"]
    assert (
        Path(plan["target_runtime_path"]).name
        == plan["python_runtime_contract_sha256"]
    )
    assert result["ok"] is True, json.dumps(result, indent=2)
    assert result["status"] == "runtime_deployed_registration_ready"
    assert result["runtime_written"] is True
    assert result["runtime_integrity"]["integrity_verified"] is True
    assert result["registration_plan"]["status"] == "registration_ready"
    assert result["registration_handoff"]["apply_command"] is not None
    assert result["registration_handoff"]["runtime_manifest_sha256"] == (
        result["runtime_integrity"]["manifest_sha256"]
    )
    assert result["registration_handoff"][
        "python_runtime_contract_sha256"
    ] == plan["python_runtime_contract_sha256"]
    assert result["registration_handoff"]["apply_command"][2:4] == [
        RUNTIME_MANIFEST_ARGUMENT,
        result["runtime_integrity"]["manifest_sha256"],
    ]
    if os.name == "nt":
        registered = result["registration_plan"]["recommended_entrypoint"]
        assert len(str(target)) >= 260
        assert registered["args"][0].startswith("\\\\?\\")
        assert len(registered["cwd"]) < 240
        assert Path(registered["cwd"]).is_dir()
        assert result["registration_handoff"]["apply_command"][1].startswith(
            "\\\\?\\"
        )
    long_deployed_example = (
        target
        / "src"
        / "material_studio_mcp_server"
        / "examples"
        / "aluminum_gallium_nitride_gallium_nitride_0001_heterostructure_spec.json"
    )
    assert filesystem_io_path(long_deployed_example).read_text(
        encoding="utf-8"
    ) == '{"kind": "long-path-fixture"}\n'
    deployed_manifest = json.loads(
        filesystem_io_path(target / MANAGED_RUNTIME_MANIFEST).read_text(
            encoding="utf-8"
        )
    )
    assert deployed_manifest["python_runtime_contract"][
        "contract_sha256"
    ] == plan["python_runtime_contract_sha256"]
    assert _sha256(config) == config_before
    assert reused_plan["status"] == "runtime_already_deployed"
    assert reused["status"] == "runtime_reused_registration_ready"
    assert reused["runtime_written"] is False
    assert filesystem_io_path(
        target / MANAGED_RUNTIME_MANIFEST
    ).read_bytes() == first_manifest


def test_windows_long_runtime_selects_safe_process_launch_paths(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        return
    runtime = (
        tmp_path
        / ("runtime_" + ("x" * 100))
        / ("a" * 40)
        / ("b" * 64)
    )
    filesystem_io_path(runtime).mkdir(parents=True)

    launch_script = process_launch_path(runtime / "run_server.py")
    launch_cwd = managed_runtime_launch_cwd(runtime)

    assert len(str(runtime)) >= 260
    assert str(launch_script).startswith("\\\\?\\")
    assert not str(launch_cwd).startswith("\\\\?\\")
    assert len(str(launch_cwd)) < 240
    assert launch_cwd.is_dir()


def test_runtime_manifest_is_stable_across_branches_at_same_commit(
    tmp_path: Path,
) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / "runtimes"
    main_plan = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )
    _git(repository, "checkout", "-b", "review")
    _git(repository, "push", "-u", "origin", "review")
    review_plan = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )

    assert main_plan["source_commit"] == review_plan["source_commit"]
    assert main_plan["source_upstream"] == "origin/main"
    assert review_plan["source_upstream"] == "origin/review"
    assert main_plan["target_runtime_path"] == review_plan["target_runtime_path"]
    assert main_plan["manifest_sha256"] == review_plan["manifest_sha256"]
    assert (
        main_plan["runtime_deployment_plan_id"]
        != review_plan["runtime_deployment_plan_id"]
    )


def test_runtime_path_changes_with_python_environment_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / "runtimes"
    first = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )
    changed_contract = json.loads(
        json.dumps(_CURRENT_PYTHON_RUNTIME_CONTRACT)
    )
    changed_contract["python"]["machine"] = "different-runtime-machine"
    changed_contract["contract_sha256"] = python_runtime_contract_sha256(
        changed_contract
    )
    monkeypatch.setattr(
        deployment_module,
        "probe_python_runtime_contract",
        lambda *args, **kwargs: {
            "status": "complete",
            "ok": True,
            "error": None,
            "stderr_tail": None,
            "contract": changed_contract,
            "contract_sha256": changed_contract["contract_sha256"],
        },
    )
    changed = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )

    assert first["source_commit"] == changed["source_commit"]
    assert first["commit_runtime_root"] == changed["commit_runtime_root"]
    assert first["target_runtime_path"] != changed["target_runtime_path"]
    assert Path(changed["target_runtime_path"]).name == changed_contract[
        "contract_sha256"
    ]


def test_deployment_plan_rejects_dirty_unpushed_and_conflicting_runtime(
    tmp_path: Path,
) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / "runtimes"
    clean = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )
    target = Path(clean["target_runtime_path"])
    target.mkdir(parents=True)
    (target / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    conflict = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )
    (repository / "run_server.py").write_text("dirty\n", encoding="utf-8")
    dirty = plan_runtime_deployment(
        source_root=repository,
        runtime_root=tmp_path / "other-runtimes",
        python_command=sys.executable,
    )

    assert conflict["status"] == "runtime_destination_conflict"
    assert conflict["apply_ready"] is False
    assert dirty["status"] == "source_not_deployable"
    assert "tracked_source_changes_present" in dirty["blocking_reasons"]

    unpushed = tmp_path / "unpushed"
    unpushed.mkdir()
    (unpushed / "run_server.py").write_text("server\n", encoding="utf-8")
    _git(unpushed, "init")
    _git(unpushed, "config", "user.email", "test@example.com")
    _git(unpushed, "config", "user.name", "Runtime Test")
    _git(unpushed, "add", ".")
    _git(unpushed, "commit", "-m", "local only")
    unpushed_result = plan_runtime_deployment(
        source_root=unpushed,
        runtime_root=tmp_path / "third-runtimes",
        python_command=sys.executable,
    )
    assert unpushed_result["status"] == "source_not_deployable"
    assert "source_upstream_not_configured" in unpushed_result["blocking_reasons"]


def test_deployment_plan_rejects_unusable_python_runtime(
    tmp_path: Path,
) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / "runtimes"
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"not-an-executable")

    result = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=fake_python,
    )

    assert result["ok"] is False
    assert result["status"] == "python_runtime_not_deployable"
    assert result["apply_ready"] is False
    assert result["python_runtime_probe"]["ok"] is False
    assert not runtime_root.exists()


def test_stale_runtime_plan_never_writes_destination(tmp_path: Path) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / "runtimes"
    plan = plan_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
    )
    (repository / "run_server.py").write_text("dirty\n", encoding="utf-8")

    result = apply_runtime_deployment(
        source_root=repository,
        runtime_root=runtime_root,
        python_command=sys.executable,
        expected_plan_id=plan["runtime_deployment_plan_id"],
        validate_protocol=False,
    )

    assert result["status"] == "runtime_deployment_plan_mismatch"
    assert not runtime_root.exists()


def test_archive_parser_rejects_traversal_and_links(tmp_path: Path) -> None:
    traversal_buffer = io.BytesIO()
    with tarfile.open(fileobj=traversal_buffer, mode="w") as archive:
        info = tarfile.TarInfo("../outside.txt")
        content = b"outside"
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    traversal = traversal_buffer.getvalue()

    with pytest.raises(_DeploymentError, match="unsafe archive path"):
        _archive_content_snapshot(traversal)
    with pytest.raises(_DeploymentError, match="unsafe archive path"):
        _extract_archive_safely(traversal, tmp_path / "extract")
    assert not (tmp_path / "outside.txt").exists()

    windows_traversal_buffer = io.BytesIO()
    with tarfile.open(fileobj=windows_traversal_buffer, mode="w") as archive:
        info = tarfile.TarInfo("..\\outside.txt")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    windows_traversal = windows_traversal_buffer.getvalue()
    with pytest.raises(_DeploymentError, match="unsafe archive path"):
        _archive_content_snapshot(windows_traversal)
    with pytest.raises(_DeploymentError, match="unsafe archive path"):
        _extract_archive_safely(
            windows_traversal,
            tmp_path / "windows-extract",
        )

    link_buffer = io.BytesIO()
    with tarfile.open(fileobj=link_buffer, mode="w") as archive:
        info = tarfile.TarInfo("linked")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)
    with pytest.raises(_DeploymentError, match="unsupported archive member"):
        _archive_content_snapshot(link_buffer.getvalue())


def test_runtime_deployment_cli_defaults_to_read_only_preview(
    tmp_path: Path,
    capsys,
) -> None:
    repository, _ = _pushed_repository(tmp_path)
    runtime_root = tmp_path / "runtimes"

    exit_code = main(
        [
            "--source",
            str(repository),
            "--runtime-root",
            str(runtime_root),
            "--python",
            sys.executable,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"operation": "plan_runtime_deployment"' in output
    assert '"status": "runtime_deployment_ready"' in output
    assert not runtime_root.exists()
