"""Build the deterministic Windows Codex plugin release bundle.

This builder is intentionally narrow.  It packages only the reviewed plugin,
installer, documentation, and wheel paths.  It does not publish anything and
it rejects inconsistent license metadata, links/reparse points, unsafe archive
names, concrete developer paths, and common secret material.
"""

from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


PACKAGE_NAME = "materials-studio-mcp"
PLUGIN_NAME = "materials-studio-mcp"
REPOSITORY_URL = "https://github.com/Wqeeeeeeee/ms_mcp"
REFERENCE_REPOSITORY_URL = "https://github.com/DrYe1109/MS-MCP"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MAX_PAYLOAD_FILE_BYTES = 256 * 1024 * 1024
MAX_WHEEL_EXPANDED_BYTES = 512 * 1024 * 1024

PLUGIN_DIRECTORY = "plugins/materials-studio-mcp"
MARKETPLACE_FILE = ".agents/plugins/marketplace.json"
README_FILE = "README.md"
LICENSE_FILE = "LICENSE"
LICENSE_SPDX = "MIT"
COPYRIGHT_LINE = "Copyright (c) 2026 Xu kaidong"
OPTIONAL_NOTICE_FILES = ("THIRD_PARTY_NOTICES.md",)

MCP_SERVER_DEFINITION_BASE = {
    "command": "cmd.exe",
    "args": ["/d", "/c", "Run-MS-MCP.bat"],
    "cwd": ".",
    "env": {"MATERIAL_STUDIO_MCP_PLUGIN_MODE": "1"},
    "startup_timeout_sec": 120,
    "default_tools_approval_mode": "prompt",
}
EXPECTED_CONSOLE_SCRIPTS = {
    "ms-mcp": "material_studio_mcp_server.server:main",
    "ms-mcp-config-doctor": "material_studio_mcp_server.codex_config:main",
    "ms-mcp-config-register": "material_studio_mcp_server.codex_registration:main",
    "ms-mcp-dashboard": "material_studio_mcp_server.read_only_dashboard:main",
    "ms-mcp-legacy": "ms_mcp.server:main",
    "ms-mcp-live-smoke": "material_studio_mcp_server.live_smoke:main",
    "ms-mcp-protocol-smoke": "material_studio_mcp_server.protocol_smoke:main",
    "ms-mcp-runtime-deploy": "material_studio_mcp_server.runtime_deployment:main",
}
REQUIRED_WHEEL_RUNTIME_MEMBERS = frozenset(
    {
        "material_studio_mcp_server/gui_fit_probe.py",
    }
)
WHEEL_SOURCE_PACKAGE_DIRECTORIES = (
    "material_studio_mcp_server",
    "ms_mcp",
)

INSTALLER_FILES = (
    "Configure-MS-MCP.bat",
    "Install-MS-MCP.bat",
    "Test-MS-MCP.bat",
    "Uninstall-MS-MCP.bat",
    "scripts/windows/WindowsInstaller.Common.ps1",
    "scripts/windows/Configure-MS-MCP.ps1",
    "scripts/windows/Install-MS-MCP.ps1",
    "scripts/windows/Test-MS-MCP.ps1",
    "scripts/windows/Uninstall-MS-MCP.ps1",
)

DOCUMENTATION_FILES = (
    "docs/gui_loop.md",
    "docs/gui_control.md",
    "docs/INSTALLATION.zh-CN.md",
    "docs/INSTALLATION.en.md",
    "docs/CODEX_PLUGIN.zh-CN.md",
    "docs/REAL_MS_ACCEPTANCE.zh-CN.md",
    "docs/TROUBLESHOOTING.zh-CN.md",
    "docs/packaging/codex_plugin_packaging_audit.md",
)

FORBIDDEN_PATH_COMPONENTS = frozenset(
    {
        ".git",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
        "probe",
        "temp",
        "tmp",
        "workspace",
    }
)
FORBIDDEN_SUFFIXES = (".bak", ".orig", ".pyc", ".pyo", ".rej", ".temp", ".tmp", "~")
LICENSE_FILENAMES = frozenset(
    {"copying", "copying.md", "copying.txt", "license", "license.md", "license.txt"}
)
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".bat",
        ".cfg",
        ".cmd",
        ".ini",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".rst",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
CONCRETE_WINDOWS_HOME_RE = re.compile(
    r"(?i)[a-z]:[\\/](?:users|documents and settings)[\\/](?![%<])[^\\/\s\"']+"
)
SECRET_PATTERNS = (
    re.compile(r"(?i)\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"']?"
        r"(?![%<$])[A-Za-z0-9+/=_-]{16,}"
    ),
)


class ReleaseBuildError(ValueError):
    """Raised when a release input fails closed."""


@dataclass(frozen=True)
class PayloadFile:
    archive_path: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class ReleaseArtifacts:
    wheel: Path
    plugin_zip: Path
    checksums: Path
    manifest: Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _normalized_utf8_text(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise ReleaseBuildError(f"{label} must be UTF-8 text") from exc


def _validate_sha(value: str, label: str) -> str:
    if not HEX_SHA_RE.fullmatch(value):
        raise ReleaseBuildError(f"{label} must be an exact 40-character Git SHA")
    return value.lower()


def _validate_archive_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ReleaseBuildError(f"unsafe archive path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseBuildError(f"unsafe archive path: {value!r}")
    if re.match(r"^[A-Za-z]:", path.parts[0]):
        raise ReleaseBuildError(f"drive-qualified archive path: {value!r}")
    folded_parts = {part.casefold() for part in path.parts}
    forbidden = folded_parts.intersection(FORBIDDEN_PATH_COMPONENTS)
    if forbidden:
        raise ReleaseBuildError(
            f"forbidden release path component {sorted(forbidden)!r}: {value!r}"
        )
    if path.name.casefold().endswith(FORBIDDEN_SUFFIXES):
        raise ReleaseBuildError(f"temporary or generated file is not releasable: {value!r}")
    return path.as_posix()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseBuildError(f"cannot inspect release input {path}: {exc}") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _assert_safe_source_path(path: Path, source_root: Path, *, expect_directory: bool = False) -> Path:
    if not path.exists():
        raise ReleaseBuildError(f"required release input is missing: {path}")

    root = source_root.resolve(strict=True)
    try:
        relative = path.relative_to(source_root)
    except ValueError as exc:
        raise ReleaseBuildError(f"release input is outside source root: {path}") from exc

    cursor = source_root
    for part in relative.parts:
        cursor = cursor / part
        if _is_link_or_reparse(cursor):
            raise ReleaseBuildError(f"links and reparse points are forbidden: {cursor}")

    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleaseBuildError(f"release input escapes source root: {path}") from exc

    if expect_directory:
        if not resolved.is_dir():
            raise ReleaseBuildError(f"expected release directory: {path}")
    elif not resolved.is_file():
        raise ReleaseBuildError(f"expected regular release file: {path}")
    return resolved


def _source_path_tokens(source_root: Path) -> tuple[str, ...]:
    resolved = str(source_root.resolve(strict=True))
    variants = {
        resolved,
        resolved.replace("\\", "/"),
        resolved.replace("/", "\\"),
    }
    return tuple(sorted((item for item in variants if item), key=len, reverse=True))


def _scan_payload_text(archive_path: str, data: bytes, source_tokens: Iterable[str]) -> None:
    if len(data) > MAX_PAYLOAD_FILE_BYTES:
        raise ReleaseBuildError(f"release input is unexpectedly large: {archive_path}")

    lowered = data.lower()
    for token in source_tokens:
        encoded = token.encode("utf-8", errors="ignore")
        if encoded and encoded.lower() in lowered:
            raise ReleaseBuildError(f"developer source path leaked into {archive_path}")
        utf16 = token.encode("utf-16-le", errors="ignore")
        if utf16 and utf16.lower() in lowered:
            raise ReleaseBuildError(f"developer source path leaked into {archive_path}")

    suffix = PurePosixPath(archive_path).suffix.casefold()
    if suffix not in TEXT_SUFFIXES:
        return
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReleaseBuildError(f"text release input is not UTF-8: {archive_path}") from exc
    if CONCRETE_WINDOWS_HOME_RE.search(text):
        raise ReleaseBuildError(f"concrete Windows user path leaked into {archive_path}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ReleaseBuildError(f"possible secret material found in {archive_path}")


def _load_expected_wheel_package_members(source_root: Path) -> dict[str, bytes]:
    """Bind every shipped package file to the exact current source-tree bytes."""

    source_directory = _assert_safe_source_path(
        source_root / "src",
        source_root,
        expect_directory=True,
    )
    expected: dict[str, bytes] = {}
    collision_keys: set[str] = set()
    for package_name in WHEEL_SOURCE_PACKAGE_DIRECTORIES:
        package_root = _assert_safe_source_path(
            source_directory / package_name,
            source_root,
            expect_directory=True,
        )
        for current, dirnames, filenames in os.walk(package_root, followlinks=False):
            current_path = Path(current)
            for dirname in list(dirnames):
                directory = current_path / dirname
                if dirname.casefold() == "__pycache__":
                    dirnames.remove(dirname)
                    continue
                if _is_link_or_reparse(directory):
                    raise ReleaseBuildError(
                        f"links and reparse points are forbidden in wheel source: {directory}"
                    )
            for filename in filenames:
                path = current_path / filename
                if path.suffix.casefold() in {".pyc", ".pyo"}:
                    continue
                resolved = _assert_safe_source_path(path, source_root)
                archive_path = _validate_archive_path(
                    resolved.relative_to(source_directory).as_posix()
                )
                collision_key = archive_path.casefold()
                if collision_key in collision_keys:
                    raise ReleaseBuildError(
                        f"wheel source contains a case-folded duplicate path: {archive_path}"
                    )
                collision_keys.add(collision_key)
                expected[archive_path] = resolved.read_bytes()
    if not expected:
        raise ReleaseBuildError("wheel source packages contain no regular files")
    return expected


def _load_project_metadata(source_root: Path) -> tuple[str, str]:
    project_file = _assert_safe_source_path(source_root / "pyproject.toml", source_root)
    with project_file.open("rb") as stream:
        document = tomllib.load(stream)
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseBuildError("pyproject.toml has no [project] table")
    name = project.get("name")
    version = project.get("version")
    if name != PACKAGE_NAME or not isinstance(version, str) or not version:
        raise ReleaseBuildError("pyproject package identity is not materials-studio-mcp with a version")
    if project.get("license") != LICENSE_SPDX:
        raise ReleaseBuildError("pyproject.toml project.license must be the SPDX expression MIT")
    if project.get("license-files") != [LICENSE_FILE]:
        raise ReleaseBuildError("pyproject.toml project.license-files must contain exactly LICENSE")
    authors = project.get("authors")
    if not isinstance(authors, list) or not any(
        isinstance(author, dict) and author.get("name") == "Xu kaidong" for author in authors
    ):
        raise ReleaseBuildError("pyproject.toml authors must identify Xu kaidong")
    urls = project.get("urls")
    if not isinstance(urls, dict) or urls.get("Repository") != REPOSITORY_URL:
        raise ReleaseBuildError("pyproject.toml project.urls.Repository must identify Wqeeeeeeee/ms_mcp")
    return name, version


def _load_repository_license(source_root: Path) -> bytes:
    license_path = _assert_safe_source_path(source_root / LICENSE_FILE, source_root)
    data = license_path.read_bytes()
    normalized = _normalized_utf8_text(data, "LICENSE")
    if not normalized.startswith("MIT License\n") or COPYRIGHT_LINE not in normalized:
        raise ReleaseBuildError(
            "LICENSE must be the repository MIT text with Copyright (c) 2026 Xu kaidong"
        )
    return data


def _resolve_plugin_component(plugin_root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith("./") or "\\" in value:
        raise ReleaseBuildError(f"plugin {label} must be a ./ relative path")
    relative = PurePosixPath(value[2:])
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ReleaseBuildError(f"plugin {label} escapes the plugin root")
    target = plugin_root.joinpath(*relative.parts)
    resolved_root = plugin_root.resolve(strict=True)
    try:
        target.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ReleaseBuildError(f"plugin {label} does not resolve inside the plugin root") from exc
    return target


def _load_codex_tool_policy(source_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    policy_path = _assert_safe_source_path(
        source_root / "src/material_studio_mcp_server/codex_config.py",
        source_root,
    )
    try:
        tree = ast.parse(policy_path.read_text(encoding="utf-8"), filename=str(policy_path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ReleaseBuildError(f"could not parse Codex tool policy: {exc}") from exc

    values: dict[str, object] = {}
    wanted = {"SAFE_ENABLED_TOOLS", "DISABLED_TOOLS"}
    for node in tree.body:
        name: str | None = None
        value_node: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value_node = node.value
        if name in wanted and value_node is not None:
            try:
                values[name] = ast.literal_eval(value_node)
            except (ValueError, SyntaxError) as exc:
                raise ReleaseBuildError(f"Codex tool policy {name} must be a literal sequence") from exc

    result: dict[str, tuple[str, ...]] = {}
    for name in sorted(wanted):
        value = values.get(name)
        if not isinstance(value, (tuple, list)) or not value or not all(
            isinstance(item, str) and item.startswith("material_studio_") for item in value
        ):
            raise ReleaseBuildError(f"Codex tool policy {name} is missing or invalid")
        normalized = tuple(value)
        if len(set(normalized)) != len(normalized):
            raise ReleaseBuildError(f"Codex tool policy {name} contains duplicates")
        result[name] = normalized

    enabled = result["SAFE_ENABLED_TOOLS"]
    disabled = result["DISABLED_TOOLS"]
    if disabled != ("material_studio_run_script",):
        raise ReleaseBuildError("Codex disabled tool policy must explicitly deny only material_studio_run_script")
    if set(enabled).intersection(disabled):
        raise ReleaseBuildError("Codex enabled and disabled tool policies overlap")
    return enabled, disabled


def _validate_plugin(source_root: Path, version: str, repository_license: bytes) -> None:
    plugin_root = _assert_safe_source_path(
        source_root / PLUGIN_DIRECTORY, source_root, expect_directory=True
    )
    manifest_path = _assert_safe_source_path(
        plugin_root / ".codex-plugin" / "plugin.json", source_root
    )
    plugin_license_path = _assert_safe_source_path(plugin_root / LICENSE_FILE, source_root)
    if _normalized_utf8_text(plugin_license_path.read_bytes(), "plugin LICENSE") != (
        _normalized_utf8_text(repository_license, "repository LICENSE")
    ):
        raise ReleaseBuildError("plugin LICENSE must exactly match the repository LICENSE")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"invalid plugin manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ReleaseBuildError("plugin manifest must be a JSON object")
    if manifest.get("name") != PLUGIN_NAME or manifest.get("version") != version:
        raise ReleaseBuildError("plugin name/version does not match pyproject.toml")
    if manifest.get("license") != LICENSE_SPDX:
        raise ReleaseBuildError("plugin manifest license must be MIT")
    homepage = manifest.get("homepage")
    if manifest.get("repository") != REPOSITORY_URL or homepage not in {
        REPOSITORY_URL,
        f"{REPOSITORY_URL}#readme",
    }:
        raise ReleaseBuildError("plugin repository/homepage must identify Wqeeeeeeee/ms_mcp")
    if not manifest.get("author"):
        raise ReleaseBuildError("plugin author metadata is required")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise ReleaseBuildError("plugin interface metadata is required")
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
    missing_interface = sorted(required_interface.difference(interface))
    if missing_interface:
        raise ReleaseBuildError(f"plugin interface metadata is incomplete: {missing_interface}")
    marker_text = " ".join(
        str(value)
        for value in (manifest.get("description"), interface.get("shortDescription"), interface.get("longDescription"))
    ).casefold()
    if "internal preview" in marker_text or "not for public redistribution" in marker_text:
        raise ReleaseBuildError("plugin metadata still contains obsolete internal-preview restrictions")

    skills_path = _resolve_plugin_component(plugin_root, manifest.get("skills"), "skills")
    mcp_path = _resolve_plugin_component(plugin_root, manifest.get("mcpServers"), "mcpServers")
    _assert_safe_source_path(skills_path, source_root, expect_directory=True)
    _assert_safe_source_path(mcp_path, source_root)

    try:
        mcp_document = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"invalid .mcp.json: {exc}") from exc
    if not isinstance(mcp_document, dict) or set(mcp_document) != {"materials-studio"}:
        raise ReleaseBuildError(
            ".mcp.json must contain exactly the direct materials-studio server map"
        )
    server = mcp_document["materials-studio"]
    enabled_tools, disabled_tools = _load_codex_tool_policy(source_root)
    expected_server = {
        **MCP_SERVER_DEFINITION_BASE,
        "enabled_tools": list(enabled_tools),
        "disabled_tools": list(disabled_tools),
    }
    if server != expected_server:
        raise ReleaseBuildError(
            ".mcp.json materials-studio definition must exactly preserve the cache-relative "
            "cmd /d /c launcher, plugin-mode environment, 120-second startup budget, "
            "prompt-by-default approval policy, safe enabled-tool allowlist, and "
            "arbitrary-script denylist"
        )

    marketplace_path = _assert_safe_source_path(source_root / MARKETPLACE_FILE, source_root)
    try:
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"invalid marketplace.json: {exc}") from exc
    if not isinstance(marketplace, dict) or set(marketplace) != {
        "name",
        "interface",
        "plugins",
    }:
        raise ReleaseBuildError("marketplace.json must use the reviewed local marketplace shape")
    if marketplace.get("name") != "wqeeeeeeee-ms-mcp":
        raise ReleaseBuildError("marketplace name must be wqeeeeeeee-ms-mcp")
    marketplace_interface = marketplace.get("interface")
    if not isinstance(marketplace_interface, dict) or set(marketplace_interface) != {"displayName"}:
        raise ReleaseBuildError("marketplace interface must contain only displayName")
    if marketplace_interface.get("displayName") != "Materials Studio MCP":
        raise ReleaseBuildError("marketplace displayName must be Materials Studio MCP")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        raise ReleaseBuildError("marketplace must contain exactly one plugin entry")
    marketplace_plugin = plugins[0]
    if set(marketplace_plugin) != {"name", "source", "policy", "category"}:
        raise ReleaseBuildError("marketplace plugin entry contains unreviewed fields")
    if marketplace_plugin != {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./{PLUGIN_DIRECTORY}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": interface["category"],
    }:
        raise ReleaseBuildError(
            "marketplace plugin source, policy, or category does not match the plugin manifest"
        )


def _validate_wheel(
    wheel_path: Path,
    version: str,
    source_root: Path,
    source_tokens: Iterable[str],
    repository_license: bytes,
) -> bytes:
    if not wheel_path.exists() or not wheel_path.is_file() or _is_link_or_reparse(wheel_path):
        raise ReleaseBuildError(f"wheel is missing, not regular, or link-backed: {wheel_path}")
    expected_name = f"materials_studio_mcp-{version}-py3-none-any.whl"
    if wheel_path.name != expected_name:
        raise ReleaseBuildError(f"wheel filename must be {expected_name}")
    wheel_bytes = wheel_path.read_bytes()
    if len(wheel_bytes) > MAX_PAYLOAD_FILE_BYTES:
        raise ReleaseBuildError("wheel exceeds the release size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(wheel_bytes))
    except zipfile.BadZipFile as exc:
        raise ReleaseBuildError("wheel is not a valid ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        if not infos:
            raise ReleaseBuildError("wheel is empty")
        if sum(item.file_size for item in infos) > MAX_WHEEL_EXPANDED_BYTES:
            raise ReleaseBuildError("wheel expanded size exceeds the safety limit")
        metadata_candidates: list[tuple[str, bytes]] = []
        wheel_metadata_candidates: list[tuple[str, bytes]] = []
        entrypoint_candidates: list[tuple[str, bytes]] = []
        record_candidates: list[tuple[str, bytes]] = []
        license_candidates: list[tuple[str, bytes]] = []
        member_data: dict[str, bytes] = {}
        seen: set[str] = set()
        for info in infos:
            name = info.filename
            collision_key = name.casefold()
            if collision_key in seen:
                raise ReleaseBuildError(f"wheel contains duplicate member: {name}")
            seen.add(collision_key)
            normalized = _validate_archive_path(name.rstrip("/")) if name.endswith("/") else _validate_archive_path(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ReleaseBuildError(f"wheel contains a symbolic link: {name}")
            if info.is_dir():
                continue
            data = archive.read(info)
            member_data[normalized] = data
            _scan_payload_text(f"wheel:{normalized}", data, source_tokens)
            parts = PurePosixPath(normalized).parts
            basename = PurePosixPath(normalized).name.casefold()
            is_dist_info_license = (
                len(parts) >= 3
                and parts[-3].casefold().endswith(".dist-info")
                and parts[-2].casefold() == "licenses"
                and parts[-1].casefold() == "license"
            )
            if is_dist_info_license:
                license_candidates.append((normalized, data))
            elif basename in LICENSE_FILENAMES or any(
                part.casefold() == "licenses" for part in parts
            ):
                raise ReleaseBuildError(f"wheel contains an unexpected license member {normalized}")
            if normalized.endswith(".dist-info/METADATA"):
                metadata_candidates.append((normalized, data))
            elif normalized.endswith(".dist-info/WHEEL"):
                wheel_metadata_candidates.append((normalized, data))
            elif normalized.endswith(".dist-info/entry_points.txt"):
                entrypoint_candidates.append((normalized, data))
            elif normalized.endswith(".dist-info/RECORD"):
                record_candidates.append((normalized, data))
        missing_runtime_members = sorted(
            REQUIRED_WHEEL_RUNTIME_MEMBERS.difference(member_data)
        )
        if missing_runtime_members:
            raise ReleaseBuildError(
                "wheel is missing required runtime member(s): "
                + ", ".join(missing_runtime_members)
            )
        if len(metadata_candidates) != 1:
            raise ReleaseBuildError("wheel must contain exactly one .dist-info/METADATA file")
        if len(wheel_metadata_candidates) != 1:
            raise ReleaseBuildError("wheel must contain exactly one .dist-info/WHEEL file")
        if len(entrypoint_candidates) != 1:
            raise ReleaseBuildError("wheel must contain exactly one .dist-info/entry_points.txt file")
        if len(record_candidates) != 1:
            raise ReleaseBuildError("wheel must contain exactly one .dist-info/RECORD file")
        if len(license_candidates) != 1:
            raise ReleaseBuildError("wheel must contain exactly one .dist-info/licenses/LICENSE file")
        if _normalized_utf8_text(license_candidates[0][1], "wheel LICENSE") != (
            _normalized_utf8_text(repository_license, "repository LICENSE")
        ):
            raise ReleaseBuildError("wheel LICENSE does not match the repository LICENSE")

        metadata_path, metadata_bytes = metadata_candidates[0]
        metadata = BytesParser().parsebytes(metadata_bytes)
        if metadata.get("Name") != PACKAGE_NAME or metadata.get("Version") != version:
            raise ReleaseBuildError("wheel metadata name/version does not match pyproject.toml")
        try:
            core_metadata_version = Version(str(metadata.get("Metadata-Version")))
        except InvalidVersion as exc:
            raise ReleaseBuildError("wheel Core Metadata version is invalid") from exc
        if core_metadata_version < Version("2.4"):
            raise ReleaseBuildError("wheel must use Core Metadata 2.4 or newer for PEP 639")
        if metadata.get("License") is not None or any(
            str(value).casefold().startswith("license ::")
            for value in metadata.get_all("Classifier", [])
        ):
            raise ReleaseBuildError("wheel must use PEP 639 license metadata, not legacy license fields")
        if metadata.get_all("License-Expression", []) != [LICENSE_SPDX]:
            raise ReleaseBuildError("wheel License-Expression must be MIT")
        if metadata.get_all("License-File", []) != [LICENSE_FILE]:
            raise ReleaseBuildError("wheel License-File must contain exactly LICENSE")

        parsed_requirements: list[Requirement] = []
        for value in metadata.get_all("Requires-Dist", []):
            try:
                parsed_requirements.append(Requirement(str(value)))
            except InvalidRequirement as exc:
                raise ReleaseBuildError(f"wheel METADATA contains invalid Requires-Dist: {value}") from exc
        mcp_requirements = [
            requirement
            for requirement in parsed_requirements
            if canonicalize_name(requirement.name) == "mcp"
        ]
        if len(mcp_requirements) != 1:
            raise ReleaseBuildError(
                "wheel METADATA must contain exactly one MCP runtime dependency"
            )
        mcp_requirement = mcp_requirements[0]
        observed_mcp_specifiers = {
            (specifier.operator, specifier.version) for specifier in mcp_requirement.specifier
        }
        if (
            mcp_requirement.extras != {"cli"}
            or mcp_requirement.marker is not None
            or observed_mcp_specifiers != {(">=", "1.12.4"), ("<", "2")}
        ):
            raise ReleaseBuildError(
                "wheel METADATA MCP runtime dependency must be exactly mcp[cli]>=1.12.4,<2"
            )
        expected_windows_uia_dependencies = {
            "comtypes": "1.4.16",
            "pywinauto": "0.6.9",
        }
        for dependency_name, expected_version in expected_windows_uia_dependencies.items():
            matches = [
                requirement
                for requirement in parsed_requirements
                if canonicalize_name(requirement.name) == dependency_name
            ]
            if len(matches) != 1:
                raise ReleaseBuildError(
                    f"wheel METADATA must contain exactly one {dependency_name} Windows UI dependency"
                )
            requirement = matches[0]
            observed_specifiers = {
                (specifier.operator, specifier.version)
                for specifier in requirement.specifier
            }
            if (
                requirement.extras
                or str(requirement.marker) != 'sys_platform == "win32"'
                or observed_specifiers != {("==", expected_version)}
            ):
                raise ReleaseBuildError(
                    "wheel METADATA Windows UI dependencies must be exactly "
                    "comtypes==1.4.16 and pywinauto==0.6.9 with the win32 marker"
                )

        wheel_metadata = BytesParser().parsebytes(wheel_metadata_candidates[0][1])
        if wheel_metadata.get("Root-Is-Purelib", "").casefold() != "true":
            raise ReleaseBuildError("wheel must be a pure-Python wheel")
        if wheel_metadata.get_all("Tag", []) != ["py3-none-any"]:
            raise ReleaseBuildError("wheel tag must be exactly py3-none-any")

        entrypoints = configparser.ConfigParser(interpolation=None)
        entrypoints.optionxform = str
        try:
            entrypoints.read_string(entrypoint_candidates[0][1].decode("utf-8-sig"))
        except (UnicodeDecodeError, configparser.Error) as exc:
            raise ReleaseBuildError(f"wheel entry_points.txt is invalid: {exc}") from exc
        if entrypoints.sections() != ["console_scripts"]:
            raise ReleaseBuildError("wheel entry_points.txt must contain only [console_scripts]")
        observed_scripts = {
            name.strip(): value.strip() for name, value in entrypoints.items("console_scripts")
        }
        if observed_scripts != EXPECTED_CONSOLE_SCRIPTS:
            raise ReleaseBuildError(
                "wheel console entrypoints do not exactly match the reviewed pyproject contract"
            )

        record_path, record_bytes = record_candidates[0]
        try:
            record_rows = list(csv.reader(io.StringIO(record_bytes.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ReleaseBuildError(f"wheel RECORD is invalid: {exc}") from exc
        record_by_path: dict[str, tuple[str, str]] = {}
        record_collision_keys: set[str] = set()
        for row in record_rows:
            if len(row) != 3:
                raise ReleaseBuildError("wheel RECORD rows must contain exactly three columns")
            path, digest, size = row
            path = _validate_archive_path(path)
            collision_key = path.casefold()
            if collision_key in record_collision_keys:
                raise ReleaseBuildError(f"wheel RECORD contains duplicate path: {path}")
            record_collision_keys.add(collision_key)
            record_by_path[path] = (digest, size)
        if set(record_by_path) != set(member_data):
            raise ReleaseBuildError("wheel RECORD member set does not match wheel contents")
        for path, data in member_data.items():
            digest, size = record_by_path[path]
            if path == record_path:
                if digest or size:
                    raise ReleaseBuildError("wheel RECORD must leave its own hash and size empty")
                continue
            expected_digest = "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(data).digest()
            ).rstrip(b"=").decode("ascii")
            if digest != expected_digest or size != str(len(data)):
                raise ReleaseBuildError(f"wheel RECORD integrity mismatch: {path}")

        expected_dist_info = f"materials_studio_mcp-{version}.dist-info/"
        for path in (metadata_path, wheel_metadata_candidates[0][0], entrypoint_candidates[0][0], record_path):
            if not path.startswith(expected_dist_info):
                raise ReleaseBuildError("wheel dist-info directory does not match package version")

        expected_package_members = _load_expected_wheel_package_members(source_root)
        package_names_casefold = {
            package_name.casefold()
            for package_name in WHEEL_SOURCE_PACKAGE_DIRECTORIES
        }
        observed_package_members = {
            path: data
            for path, data in member_data.items()
            if PurePosixPath(path).parts
            and PurePosixPath(path).parts[0].casefold() in package_names_casefold
        }
        missing_package_members = sorted(
            set(expected_package_members).difference(observed_package_members)
        )
        extra_package_members = sorted(
            set(observed_package_members).difference(expected_package_members)
        )
        if missing_package_members or extra_package_members:
            details: list[str] = []
            if missing_package_members:
                details.append("missing: " + ", ".join(missing_package_members))
            if extra_package_members:
                details.append("extra: " + ", ".join(extra_package_members))
            raise ReleaseBuildError(
                "wheel package member set does not match the current source tree ("
                + "; ".join(details)
                + ")"
            )
        mismatched_package_members = sorted(
            path
            for path, expected_data in expected_package_members.items()
            if observed_package_members[path] != expected_data
        )
        if mismatched_package_members:
            raise ReleaseBuildError(
                "wheel package bytes do not match the current source tree: "
                + ", ".join(mismatched_package_members)
            )
    return wheel_bytes


def _walk_plugin_files(source_root: Path) -> list[Path]:
    plugin_root = _assert_safe_source_path(
        source_root / PLUGIN_DIRECTORY, source_root, expect_directory=True
    )
    paths: list[Path] = []
    for current, dirnames, filenames in os.walk(plugin_root, followlinks=False):
        current_path = Path(current)
        for dirname in list(dirnames):
            directory = current_path / dirname
            _validate_archive_path(directory.relative_to(source_root).as_posix())
            if _is_link_or_reparse(directory):
                raise ReleaseBuildError(f"links and reparse points are forbidden: {directory}")
        for filename in filenames:
            path = current_path / filename
            _validate_archive_path(path.relative_to(source_root).as_posix())
            paths.append(_assert_safe_source_path(path, source_root))
    if not paths:
        raise ReleaseBuildError("plugin directory contains no files")
    return sorted(paths, key=lambda item: item.relative_to(source_root).as_posix())


def _collect_payload(
    source_root: Path,
    wheel_bytes: bytes,
    wheel_name: str,
    source_tokens: Iterable[str],
) -> list[PayloadFile]:
    required = (
        README_FILE,
        LICENSE_FILE,
        MARKETPLACE_FILE,
        *INSTALLER_FILES,
        *DOCUMENTATION_FILES,
    )
    source_files = _walk_plugin_files(source_root)
    source_files.extend(
        _assert_safe_source_path(source_root / PurePosixPath(relative), source_root)
        for relative in required
    )
    source_files.extend(
        _assert_safe_source_path(source_root / PurePosixPath(relative), source_root)
        for relative in OPTIONAL_NOTICE_FILES
        if (source_root / PurePosixPath(relative)).exists()
    )

    payload: list[PayloadFile] = []
    seen: set[str] = set()
    for path in source_files:
        archive_path = _validate_archive_path(path.relative_to(source_root).as_posix())
        collision_key = archive_path.casefold()
        if collision_key in seen:
            raise ReleaseBuildError(f"duplicate release payload path: {archive_path}")
        data = path.read_bytes()
        _scan_payload_text(archive_path, data, source_tokens)
        payload.append(PayloadFile(archive_path=archive_path, data=data))
        seen.add(collision_key)

    wheel_archive_path = _validate_archive_path(wheel_name)
    if wheel_archive_path.casefold() in seen:
        raise ReleaseBuildError(f"duplicate wheel payload path: {wheel_archive_path}")
    payload.append(PayloadFile(archive_path=wheel_archive_path, data=wheel_bytes))
    return sorted(payload, key=lambda item: item.archive_path)


def _release_manifest(
    *,
    version: str,
    base_sha: str,
    reference_sha: str,
    bundle_root: str,
    zip_name: str,
    payload: Iterable[PayloadFile],
) -> Mapping[str, object]:
    payload = tuple(payload)
    wheel_name = f"materials_studio_mcp-{version}-py3-none-any.whl"
    try:
        wheel_payload = next(item for item in payload if item.archive_path == wheel_name)
    except StopIteration as exc:  # defensive: collection always adds the wheel
        raise ReleaseBuildError("release payload is missing its wheel") from exc
    return {
        "artifact_kind": "codex_plugin_windows_release",
        "base_sha": base_sha,
        "bundle_root": bundle_root,
        "distribution_artifacts": {
            "plugin_zip": zip_name,
            "wheel": wheel_name,
        },
        "package_name": PACKAGE_NAME,
        "plugin_name": PLUGIN_NAME,
        "public_distribution_ready": True,
        "real_acceptance": {
            "castep": "NOT_RUN",
            "materials_studio": "NOT_RUN",
        },
        "reference_repository": REFERENCE_REPOSITORY_URL,
        "reference_sha": reference_sha,
        "release_blockers": [],
        "repository": REPOSITORY_URL,
        "repository_license_status": "declared",
        "repository_license_spdx": LICENSE_SPDX,
        "repository_copyright": COPYRIGHT_LINE,
        "redistribution_policy": "MIT",
        "third_party_notices": {
            "required": False,
            "path": None,
            "audit_reference": "docs/packaging/codex_plugin_packaging_audit.md",
        },
        "schema_version": 1,
        "version": version,
        "wheel": {
            "path": wheel_payload.archive_path,
            "required_runtime_members": sorted(REQUIRED_WHEEL_RUNTIME_MEMBERS),
            "sha256": wheel_payload.sha256,
            "size": len(wheel_payload.data),
        },
        "files": [
            {
                "path": item.archive_path,
                "sha256": item.sha256,
                "size": len(item.data),
            }
            for item in payload
        ],
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.flag_bits |= 0x800
    return info


def _build_zip(entries: Iterable[PayloadFile]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        seen: set[str] = set()
        for entry in sorted(entries, key=lambda item: item.archive_path):
            name = _validate_archive_path(entry.archive_path)
            collision_key = name.casefold()
            if collision_key in seen:
                raise ReleaseBuildError(f"duplicate ZIP path: {name}")
            seen.add(collision_key)
            archive.writestr(_zip_info(name), entry.data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_release(
    *,
    source_root: Path,
    wheel_path: Path,
    output_dir: Path,
    base_sha: str,
    reference_sha: str,
) -> ReleaseArtifacts:
    """Validate inputs and build the deterministic release artifacts."""

    source_root = source_root.resolve(strict=True)
    if _is_link_or_reparse(source_root):
        raise ReleaseBuildError("source root must not be a link or reparse point")
    base_sha = _validate_sha(base_sha, "base_sha")
    reference_sha = _validate_sha(reference_sha, "reference_sha")
    _, version = _load_project_metadata(source_root)
    repository_license = _load_repository_license(source_root)
    _validate_plugin(source_root, version, repository_license)

    source_tokens = _source_path_tokens(source_root)
    wheel_path = wheel_path.resolve(strict=True)
    wheel_bytes = _validate_wheel(
        wheel_path,
        version,
        source_root,
        source_tokens,
        repository_license,
    )
    payload = _collect_payload(source_root, wheel_bytes, wheel_path.name, source_tokens)

    bundle_root = f"materials-studio-mcp-plugin-{version}-windows"
    zip_name = f"{bundle_root}.zip"
    manifest_document = _release_manifest(
        version=version,
        base_sha=base_sha,
        reference_sha=reference_sha,
        bundle_root=bundle_root,
        zip_name=zip_name,
        payload=payload,
    )
    manifest_bytes = _json_bytes(manifest_document)
    internal_sums = b"".join(
        f"{item.sha256}  {item.archive_path}\n".encode("utf-8")
        for item in [*payload, PayloadFile("release-manifest.json", manifest_bytes)]
    )

    zip_entries = [
        PayloadFile(f"{bundle_root}/{item.archive_path}", item.data) for item in payload
    ]
    zip_entries.extend(
        (
            PayloadFile(f"{bundle_root}/release-manifest.json", manifest_bytes),
            PayloadFile(f"{bundle_root}/SHA256SUMS.txt", internal_sums),
        )
    )
    zip_bytes = _build_zip(zip_entries)

    output_dir = output_dir.resolve()
    output_wheel = output_dir / wheel_path.name
    output_zip = output_dir / zip_name
    output_manifest = output_dir / "release-manifest.json"
    output_checksums = output_dir / "SHA256SUMS.txt"
    _atomic_write(output_wheel, wheel_bytes)
    _atomic_write(output_zip, zip_bytes)
    _atomic_write(output_manifest, manifest_bytes)
    external_sums = "".join(
        f"{_sha256(data)}  {name}\n"
        for name, data in sorted(
            (
                (output_wheel.name, wheel_bytes),
                (output_zip.name, zip_bytes),
                (output_manifest.name, manifest_bytes),
            )
        )
    ).encode("utf-8")
    _atomic_write(output_checksums, external_sums)

    return ReleaseArtifacts(
        wheel=output_wheel,
        plugin_zip=output_zip,
        checksums=output_checksums,
        manifest=output_manifest,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the deterministic Materials Studio MCP Windows release bundle."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of this script directory)",
    )
    parser.add_argument("--wheel", type=Path, required=True, help="already-built wheel")
    parser.add_argument("--output-dir", type=Path, help="artifact directory (default: SOURCE/dist)")
    parser.add_argument("--base-sha", required=True, help="exact 40-character origin/main base SHA")
    parser.add_argument(
        "--reference-sha",
        required=True,
        help="exact 40-character audited DrYe1109/MS-MCP reference SHA",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = args.output_dir or (args.source_root / "dist")
    try:
        artifacts = build_release(
            source_root=args.source_root,
            wheel_path=args.wheel,
            output_dir=output_dir,
            base_sha=args.base_sha,
            reference_sha=args.reference_sha,
        )
    except (OSError, ReleaseBuildError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "built_release",
                "public_distribution_ready": True,
                "release_blockers": [],
                "wheel": str(artifacts.wheel),
                "plugin_zip": str(artifacts.plugin_zip),
                "checksums": str(artifacts.checksums),
                "manifest": str(artifacts.manifest),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
