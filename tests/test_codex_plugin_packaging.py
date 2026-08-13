from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from material_studio_mcp_server.codex_config import DISABLED_TOOLS, SAFE_ENABLED_TOOLS


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "materials-studio-mcp"
MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
MCP_PATH = PLUGIN_ROOT / ".mcp.json"
MARKETPLACE_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
SKILL_PATH = PLUGIN_ROOT / "skills" / "materials-studio-modeling" / "SKILL.md"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def _assert_plugin_relative_path(plugin_root: Path, value: str) -> Path:
    assert value.startswith("./")
    assert "\\" not in value
    relative = Path(value[2:])
    assert not relative.is_absolute()
    assert ".." not in relative.parts
    resolved = (plugin_root / relative).resolve()
    assert resolved == plugin_root.resolve() or plugin_root.resolve() in resolved.parents
    assert resolved.exists()
    return resolved


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n")
    assert normalized.startswith("---\n")
    _, block, body = normalized.split("---", 2)
    fields: dict[str, str] = {}
    for line in block.strip().splitlines():
        key, separator, value = line.partition(":")
        assert separator
        fields[key.strip()] = value.strip()
    return fields, body


def test_plugin_manifest_has_current_repository_metadata() -> None:
    manifest = _json(MANIFEST_PATH)
    interface = manifest["interface"]

    assert manifest["name"] == PLUGIN_ROOT.name == "materials-studio-mcp"
    assert manifest["version"] == _project_version() == "0.4.0"
    assert manifest["author"] == {
        "name": "Xu kaidong",
        "url": "https://github.com/Wqeeeeeeee",
    }
    assert manifest["homepage"].startswith("https://github.com/Wqeeeeeeee/ms_mcp")
    assert manifest["repository"] == "https://github.com/Wqeeeeeeee/ms_mcp"
    assert manifest["license"] == "MIT"
    assert "internal preview" not in manifest["description"].lower()
    assert "not for public redistribution" not in interface["longDescription"].lower()
    assert (PLUGIN_ROOT / "LICENSE").read_text(encoding="utf-8").replace("\r\n", "\n") == (
        REPO_ROOT / "LICENSE"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")

    required_interface = {
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "websiteURL",
        "defaultPrompt",
        "brandColor",
    }
    assert required_interface <= interface.keys()
    assert interface["developerName"] == "Xu kaidong"
    assert len(interface["shortDescription"]) <= 30
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", interface["brandColor"])
    assert 1 <= len(interface["capabilities"]) <= 20
    assert all(item.strip() and "\n" not in item and len(item) <= 120 for item in interface["capabilities"])

    forbidden_brand_fields = {"composerIcon", "logo", "logoDark", "screenshots"}
    assert forbidden_brand_fields.isdisjoint(interface)


def test_manifest_component_paths_are_relative_and_confined() -> None:
    manifest = _json(MANIFEST_PATH)
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    _assert_plugin_relative_path(PLUGIN_ROOT, manifest["skills"])
    _assert_plugin_relative_path(PLUGIN_ROOT, manifest["mcpServers"])

    for value in manifest.values():
        if isinstance(value, str) and (value.startswith(".") or "\\" in value):
            assert value.startswith("./")


def test_starter_prompts_cover_all_requested_safe_intents() -> None:
    prompts = _json(MANIFEST_PATH)["interface"]["defaultPrompt"]
    assert isinstance(prompts, list)
    assert 1 <= len(prompts) <= 3
    assert len(set(prompts)) == len(prompts)
    assert all(prompt.strip() and "\n" not in prompt and len(prompt) <= 128 for prompt in prompts)

    joined = " ".join(prompts).lower()
    assert "mcp" in joined and "runner status" in joined
    assert "preview creating a semiconductor structure" in joined
    assert "current model" in joined and "multi-view diagnostics" in joined
    assert "patch" in joined and "current model" in joined
    assert "castep energy" in joined and ("execute neither" in joined or "without execution" in joined)


def test_bundled_mcp_uses_current_direct_stdio_map() -> None:
    document = _json(MCP_PATH)
    assert "mcpServers" not in document
    assert "mcp_servers" not in document
    assert set(document) == {"materials-studio"}

    server = document["materials-studio"]
    assert set(server) == {
        "command",
        "args",
        "cwd",
        "env",
        "default_tools_approval_mode",
        "enabled_tools",
        "disabled_tools",
    }
    assert server["command"] == "cmd.exe"
    assert server["args"] == ["/d", "/c", "Run-MS-MCP.bat"]
    assert server["cwd"] == "."
    assert server["env"] == {"MATERIAL_STUDIO_MCP_PLUGIN_MODE": "1"}
    assert server["default_tools_approval_mode"] == "prompt"
    assert tuple(server["enabled_tools"]) == SAFE_ENABLED_TOOLS
    assert tuple(server["disabled_tools"]) == DISABLED_TOOLS
    assert not Path(server["command"]).is_absolute()
    assert all(not Path(argument).is_absolute() for argument in server["args"])
    assert (PLUGIN_ROOT / server["args"][-1]).is_file()


def test_manual_fallback_docs_preserve_exact_safe_tool_policy() -> None:
    marker = "```toml\n[mcp_servers.materials_studio]\n"
    for relative in ("docs/INSTALLATION.en.md", "docs/INSTALLATION.zh-CN.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
        start = text.index(marker) + len("```toml\n")
        end = text.index("\n```", start)
        document = tomllib.loads(text[start:end])
        server = document["mcp_servers"]["materials_studio"]
        assert tuple(server["enabled_tools"]) == SAFE_ENABLED_TOOLS
        assert tuple(server["disabled_tools"]) == DISABLED_TOOLS


def test_install_guides_cover_python_install_and_english_troubleshooting() -> None:
    for relative in ("docs/INSTALLATION.en.md", "docs/INSTALLATION.zh-CN.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "https://www.python.org/downloads/windows/" in text
        assert "py -3 --version" in text
        assert "sys.version_info >= (3, 10)" in text
        assert "-PythonCommand" in text

    english = (REPO_ROOT / "docs/INSTALLATION.en.md").read_text(encoding="utf-8").casefold()
    required_topics = (
        "runmatscript.bat",
        "plugin cache path",
        "multiple materials studio windows",
        "background/minimized target",
        "mcp_server_restart_required",
        "workspace provenance mismatch",
        "tool allowlist drift",
        "stdout contains",
        "spaces or chinese characters",
        "windows long paths",
    )
    for topic in required_topics:
        assert topic in english


def test_release_documentation_has_no_bundle_relative_broken_links() -> None:
    bundled = {
        REPO_ROOT / "README.md",
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "docs/INSTALLATION.zh-CN.md",
        REPO_ROOT / "docs/INSTALLATION.en.md",
        REPO_ROOT / "docs/CODEX_PLUGIN.zh-CN.md",
        REPO_ROOT / "docs/REAL_MS_ACCEPTANCE.zh-CN.md",
        REPO_ROOT / "docs/TROUBLESHOOTING.zh-CN.md",
        REPO_ROOT / "docs/packaging/codex_plugin_packaging_audit.md",
    }
    markdown = [path for path in bundled if path.suffix == ".md"]
    for source in markdown:
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            relative = target.split("#", 1)[0]
            if not relative or "://" in relative:
                continue
            resolved = (source.parent / relative).resolve()
            assert resolved in {path.resolve() for path in bundled}, (
                f"{source.relative_to(REPO_ROOT)} links to a file absent from the release bundle: "
                f"{target}"
            )


def test_batch_launcher_is_cache_relative_and_uses_formal_runtime_launcher() -> None:
    text = (PLUGIN_ROOT / "Run-MS-MCP.bat").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "%~dp0scripts\\run-ms-mcp.ps1" in lowered
    assert "powershell.exe" in lowered
    assert "-noprofile" in lowered and "-noninteractive" in lowered
    assert "ms_mcp.server" not in lowered
    assert "material_studio.exe" not in lowered
    assert "runmatscript" not in lowered
    assert "pip install" not in lowered
    assert "1>&2" in text
    assert not re.search(r"(?i)(?:^|[\s\"'])[a-z]:\\", text)


def test_marketplace_entry_matches_plugin_and_policy() -> None:
    marketplace = _json(MARKETPLACE_PATH)
    assert marketplace["name"] == "wqeeeeeeee-ms-mcp"
    assert marketplace["interface"]["displayName"]
    assert len(marketplace["plugins"]) == 1

    entry = marketplace["plugins"][0]
    manifest = _json(MANIFEST_PATH)
    assert entry["name"] == manifest["name"]
    assert entry["source"] == {
        "source": "local",
        "path": "./plugins/materials-studio-mcp",
    }
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == manifest["interface"]["category"]

    source = entry["source"]["path"]
    assert source.startswith("./") and ".." not in Path(source[2:]).parts
    assert (REPO_ROOT / source[2:]).resolve() == PLUGIN_ROOT.resolve()


def test_modeling_skill_frontmatter_and_safety_contract() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    fields, body = _frontmatter(text)
    lowered = body.lower()

    assert set(fields) == {"name", "description"}
    assert fields["name"] == SKILL_PATH.parent.name == "materials-studio-modeling"
    assert "materials studio" in fields["description"].lower()
    assert "status or preflight" in fields["description"].lower()
    assert "todo" not in text.lower()

    required_phrases = (
        "material_studio_live_session_preflight",
        "material_studio_live_modeling_request",
        "execution_mode=preview",
        "explicit user confirmation",
        "project/revision",
        "material_studio_run_script",
        "manually reviewed",
        "structure valid",
        "model normal",
        "live gui normal",
        "calculation ready",
        "scientifically verified",
        "gui screenshot",
        "do not automatically",
        "fail closed",
    )
    for phrase in required_phrases:
        assert phrase in lowered
    assert "nearest available template" in lowered


def test_plugin_copy_resolves_only_from_cache_root(tmp_path: Path, monkeypatch) -> None:
    cache_root = tmp_path / "Codex Cache 路径 with spaces" / "market" / "materials-studio-mcp" / "local"
    shutil.copytree(PLUGIN_ROOT, cache_root)
    unrelated = tmp_path / "renamed-source-unavailable"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    manifest = _json(cache_root / ".codex-plugin" / "plugin.json")
    skill_dir = _assert_plugin_relative_path(cache_root, manifest["skills"])
    mcp_path = _assert_plugin_relative_path(cache_root, manifest["mcpServers"])
    server = _json(mcp_path)["materials-studio"]

    assert server["cwd"] == "."
    assert (cache_root / server["args"][-1]).is_file()
    assert (skill_dir / "materials-studio-modeling" / "SKILL.md").is_file()


def test_plugin_files_contain_no_developer_absolute_path_or_secret() -> None:
    candidates = [path for path in PLUGIN_ROOT.rglob("*") if path.is_file()]
    assert candidates
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="strict")
        assert "C:\\Users\\" not in text
        assert "ms_MCP-worktrees" not in text
        assert not re.search(r"(?i)\b(?:sk-proj-|api[_-]?key\s*[:=])", text)
