from __future__ import annotations

import sys
from pathlib import Path

from material_studio_mcp_server.config import MaterialStudioConfig
from material_studio_mcp_server.runner import MaterialStudioRunner, extract_tagged_json


def test_extract_new_tagged_json() -> None:
    text = '__MS_MCP_JSON_START__\n{"ok": true}\n__MS_MCP_JSON_END__\n'
    assert extract_tagged_json(text) == {"ok": True}


def test_runner_reports_created_files_and_duration(monkeypatch, tmp_path: Path) -> None:
    config = MaterialStudioConfig(
        runner=Path(sys.executable),
        workspace_root=tmp_path,
        default_timeout_seconds=10,
        install_home=None,
        runner_source="test",
        extra_runner_args=(),
    )
    monkeypatch.setenv("MATERIAL_STUDIO_COMMAND_TEMPLATE", '"{runner}" "{script_path}"')
    script = (
        "from pathlib import Path\n"
        "Path('created.txt').write_text('ok', encoding='utf-8')\n"
        "Path('script.pl.out').write_text('__MS_MCP_JSON_START__\\n{\"ok\": true}\\n__MS_MCP_JSON_END__\\n', encoding='utf-8')\n"
    )

    result = MaterialStudioRunner(config).run_script(script)
    data = result.to_dict()

    assert data["success"] is True
    assert data["parsed_json"] == {"ok": True}
    assert any(path.endswith("created.txt") for path in data["created_files"])
    assert data["duration_seconds"] >= 0
