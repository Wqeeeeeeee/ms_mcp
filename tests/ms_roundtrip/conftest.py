from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from material_studio_mcp_server.ms_roundtrip import (
    CandidateBinding,
    RoundtripRequest,
)

from ._helpers import FakeGuiBackend, FakeRunner, build_candidate


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-real-ms",
        action="store_true",
        default=False,
        help="run the opt-in Materials Studio 20.1 round-trip acceptance test",
    )


@pytest.fixture
def candidate_path(tmp_path: Path) -> Path:
    path = tmp_path / "candidate" / "structure.cif"
    build_candidate(path)
    return path


@pytest.fixture
def output_root(tmp_path: Path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture
def request_factory(
    candidate_path: Path,
    output_root: Path,
) -> Callable[..., RoundtripRequest]:
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()

    def factory(
        *,
        execution_mode: str = "preview",
        run_id: str = "roundtrip-test-001",
        expected_sha256: str = digest,
        selected_output_root: Path = output_root,
    ) -> RoundtripRequest:
        return RoundtripRequest(
            request_id="roundtrip-request-001",
            run_id=run_id,
            candidate=CandidateBinding(
                structure_path=candidate_path,
                expected_structure_sha256=expected_sha256,
            ),
            output_root=selected_output_root,
            execution_mode=execution_mode,
            timeout_seconds=30,
        )

    return factory


@pytest.fixture
def fake_gui() -> FakeGuiBackend:
    return FakeGuiBackend(minimized=True)


@pytest.fixture
def fake_runner(tmp_path: Path) -> FakeRunner:
    runner_path = tmp_path / "fake-install" / "RunMatScript.bat"
    runner_path.parent.mkdir()
    runner_path.write_bytes(b"@echo off\r\nrem offline fake runner\r\n")
    return FakeRunner(runner_path)
