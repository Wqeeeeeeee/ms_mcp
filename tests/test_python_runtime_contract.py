from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from material_studio_mcp_server.python_runtime import (
    PYTHON_RUNTIME_CONTRACT_SCHEMA,
    PYTHON_RUNTIME_PROBE_SCHEMA,
    probe_python_runtime_contract,
    python_runtime_contract,
    python_runtime_contract_sha256,
    python_runtime_contract_summary,
    validate_python_runtime_contract,
)


def test_python_runtime_contract_is_complete_and_deterministic() -> None:
    first = python_runtime_contract()
    second = python_runtime_contract()

    assert first == second
    assert first["schema"] == PYTHON_RUNTIME_CONTRACT_SCHEMA
    assert first["status"] == "complete"
    assert first["errors"] == []
    assert validate_python_runtime_contract(first) == []
    assert first["contract_sha256"] == python_runtime_contract_sha256(first)
    assert first["python"]["executable"] == str(Path(sys.executable).resolve())
    assert first["distribution_count"] == len(first["distributions"])
    assert {"jinja2", "mcp", "packaging", "pydantic"} <= set(
        first["distributions"]
    )
    assert all(
        item["status"] == "complete"
        for item in first["distributions"].values()
    )
    assert all(item["status"] == "complete" for item in first["python_binaries"])


def test_python_runtime_probe_uses_requested_interpreter() -> None:
    root = Path(__file__).parents[1]
    result = probe_python_runtime_contract(sys.executable, root)
    direct = python_runtime_contract()

    assert result["schema"] == PYTHON_RUNTIME_PROBE_SCHEMA
    assert result["status"] == "complete"
    assert result["ok"] is True
    assert result["error"] is None
    assert result["contract_sha256"] == direct["contract_sha256"]
    assert result["contract"] == direct
    assert python_runtime_contract_summary(result["contract"])[
        "python_executable"
    ] == str(Path(sys.executable).resolve())


def test_python_runtime_probe_disconnects_child_stdin(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def capture_run(*args, **kwargs):
        observed.update(kwargs)
        raise RuntimeError("stop after capturing subprocess arguments")

    monkeypatch.setattr(subprocess, "run", capture_run)

    probe_python_runtime_contract(sys.executable, Path(__file__).parents[1])

    assert observed.get("stdin") is subprocess.DEVNULL


def test_python_runtime_contract_tamper_is_rejected() -> None:
    contract = json.loads(json.dumps(python_runtime_contract()))
    contract["python"]["version"] = "0.0.0-tampered"

    errors = validate_python_runtime_contract(contract)

    assert "python_runtime_contract_sha256_mismatch" in errors


def test_python_runtime_probe_rejects_missing_command(tmp_path: Path) -> None:
    result = probe_python_runtime_contract(
        tmp_path / "missing-python.exe",
        Path(__file__).parents[1],
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "python_command_not_found"
    assert result["contract"] is None
