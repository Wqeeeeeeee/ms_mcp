from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceRequest,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-real-castep",
        action="store_true",
        default=False,
        help="run the single opt-in real CASTEP Energy acceptance execution",
    )
    parser.addoption(
        "--real-castep-workspace",
        action="store",
        default=None,
        help="use this absent external directory for the preserved real CASTEP run",
    )
    parser.addoption(
        "--real-castep-evidence-output",
        action="store",
        default=None,
        help="write coordinate-free real CASTEP evidence to a new external file",
    )
    parser.addoption(
        "--real-castep-request-id",
        action="store",
        default=None,
        help="bind the real execution to the reviewed preview request identifier",
    )
    parser.addoption(
        "--real-castep-timeout-seconds",
        action="store",
        type=int,
        default=None,
        help="bind the real execution to the reviewed runner timeout",
    )
    parser.addoption(
        "--real-castep-expected-plan-sha256",
        action="store",
        default=None,
        help="require the real execution preview to match this reviewed plan digest",
    )


@pytest.fixture
def request_factory(tmp_path: Path) -> Callable[..., CastepAcceptanceRequest]:
    workspace = tmp_path / "fresh-castep-workspace"

    def factory(
        *,
        execution_mode: str = "preview",
        expected_plan_sha256: str | None = None,
        real_opt_in: str | None = None,
        selected_workspace: Path = workspace,
    ) -> CastepAcceptanceRequest:
        return CastepAcceptanceRequest(
            request_id="castep-acceptance-test",
            workspace_root=selected_workspace,
            execution_mode=execution_mode,
            expected_plan_sha256=expected_plan_sha256,
            real_opt_in=real_opt_in,
            timeout_seconds=30,
        )

    return factory
