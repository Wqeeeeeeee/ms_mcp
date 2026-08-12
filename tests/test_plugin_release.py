from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

import scripts.build_plugin_release as release_builder
from scripts.build_plugin_release import (
    DOCUMENTATION_FILES,
    INSTALLER_FILES,
    ReleaseBuildError,
    build_release,
)


VERSION = "0.4.0"
BASE_SHA = "a" * 40
REFERENCE_SHA = "b" * 40
LICENSE_TEXT = "MIT License\n\nCopyright (c) 2026 Xu kaidong\n"


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _record_digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _make_wheel(
    directory: Path,
    *,
    metadata_version: str = VERSION,
    extra_members: dict[str, bytes] | None = None,
    core_metadata_version: str = "2.4",
    license_expression: str | None = "MIT",
    license_file_headers: tuple[str, ...] = ("LICENSE",),
    license_member: bytes | None = LICENSE_TEXT.encode("utf-8"),
    mcp_requirement: str | None = "mcp[cli]>=1.12.4,<2",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"materials_studio_mcp-{VERSION}-py3-none-any.whl"
    dist_info = f"materials_studio_mcp-{VERSION}.dist-info"
    license_expression_line = (
        f"License-Expression: {license_expression}\n" if license_expression is not None else ""
    )
    license_file_lines = "".join(f"License-File: {name}\n" for name in license_file_headers)
    requirement_line = f"Requires-Dist: {mcp_requirement}\n" if mcp_requirement else ""
    entries = {
        "material_studio_mcp_server/__init__.py": b"__version__ = '0.4.0'\n",
        f"{dist_info}/METADATA": (
            f"Metadata-Version: {core_metadata_version}\n"
            "Name: materials-studio-mcp\n"
            f"Version: {metadata_version}\n"
            f"{license_expression_line}"
            f"{license_file_lines}"
            f"{requirement_line}"
            "Summary: Test wheel\n\n"
        ).encode("utf-8"),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": (
            b"[console_scripts]\n"
            b"ms-mcp = material_studio_mcp_server.server:main\n"
            b"ms-mcp-config-doctor = material_studio_mcp_server.codex_config:main\n"
            b"ms-mcp-config-register = material_studio_mcp_server.codex_registration:main\n"
            b"ms-mcp-dashboard = material_studio_mcp_server.read_only_dashboard:main\n"
            b"ms-mcp-legacy = ms_mcp.server:main\n"
            b"ms-mcp-live-smoke = material_studio_mcp_server.live_smoke:main\n"
            b"ms-mcp-protocol-smoke = material_studio_mcp_server.protocol_smoke:main\n"
            b"ms-mcp-runtime-deploy = material_studio_mcp_server.runtime_deployment:main\n"
        ),
    }
    if license_member is not None:
        entries[f"{dist_info}/licenses/LICENSE"] = license_member
    entries.update(extra_members or {})
    record_path = f"{dist_info}/RECORD"
    entries[record_path] = "".join(
        f"{name},{_record_digest(data)},{len(data)}\n" for name, data in entries.items()
    ).encode("utf-8") + f"{record_path},,\n".encode("utf-8")
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in entries.items():
            _zip_write(archive, name, data)
    return wheel


def _rewrite_wheel(wheel: Path, *, remove_suffix: str | None = None, tamper_module: bool = False) -> None:
    with zipfile.ZipFile(wheel) as archive:
        entries = {
            info.filename: archive.read(info)
            for info in archive.infolist()
            if not info.is_dir() and not (remove_suffix and info.filename.endswith(remove_suffix))
        }
    record_paths = [name for name in entries if name.endswith(".dist-info/RECORD")]
    if tamper_module:
        entries["material_studio_mcp_server/__init__.py"] = b"tampered = True\n"
    if remove_suffix != ".dist-info/RECORD" and record_paths and not tamper_module:
        record_path = record_paths[0]
        entries.pop(record_path)
        entries[record_path] = "".join(
            f"{name},{_record_digest(data)},{len(data)}\n" for name, data in entries.items()
        ).encode("utf-8") + f"{record_path},,\n".encode("utf-8")
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in entries.items():
            _zip_write(archive, name, data)


def _make_source(root: Path) -> Path:
    _write(
        root,
        "pyproject.toml",
        "[project]\n"
        "name = \"materials-studio-mcp\"\n"
        "version = \"0.4.0\"\n"
        "authors = [{ name = \"Xu kaidong\" }]\n"
        "license = \"MIT\"\n"
        "license-files = [\"LICENSE\"]\n\n"
        "[project.urls]\n"
        "Repository = \"https://github.com/Wqeeeeeeee/ms_mcp\"\n",
    )
    _write(root, "LICENSE", LICENSE_TEXT)
    manifest = {
        "name": "materials-studio-mcp",
        "version": VERSION,
        "description": "Local Materials Studio MCP server and modeling workflow.",
        "author": {"name": "Xu kaidong"},
        "homepage": "https://github.com/Wqeeeeeeee/ms_mcp#readme",
        "repository": "https://github.com/Wqeeeeeeee/ms_mcp",
        "license": "MIT",
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "Materials Studio MCP",
            "shortDescription": "Safe local Materials Studio",
            "longDescription": "Versioned local Python runtime with preview-first modeling.",
            "developerName": "Xu kaidong",
            "category": "Developer Tools",
            "capabilities": ["Local MCP"],
            "websiteURL": "https://github.com/Wqeeeeeeee/ms_mcp",
            "defaultPrompt": "Check status read-only.",
            "brandColor": "#334155",
        },
    }
    _write(
        root,
        "plugins/materials-studio-mcp/.codex-plugin/plugin.json",
        json.dumps(manifest, indent=2),
    )
    _write(root, "plugins/materials-studio-mcp/LICENSE", LICENSE_TEXT)
    _write(
        root,
        "plugins/materials-studio-mcp/.mcp.json",
        json.dumps(
            {
                "materials-studio": {
                    "command": "cmd.exe",
                    "args": ["/d", "/c", "Run-MS-MCP.bat"],
                    "cwd": ".",
                    "env": {"MATERIAL_STUDIO_MCP_PLUGIN_MODE": "1"},
                    "default_tools_approval_mode": "prompt",
                    "enabled_tools": ["material_studio_get_status"],
                    "disabled_tools": ["material_studio_run_script"],
                }
            },
            indent=2,
        ),
    )
    _write(
        root,
        "src/material_studio_mcp_server/codex_config.py",
        "SAFE_ENABLED_TOOLS = ('material_studio_get_status',)\n"
        "DISABLED_TOOLS = ('material_studio_run_script',)\n",
    )
    _write(root, "plugins/materials-studio-mcp/Run-MS-MCP.bat", "@echo off\nexit /b 0\n")
    _write(
        root,
        "plugins/materials-studio-mcp/scripts/Run-MS-MCP.ps1",
        "Set-StrictMode -Version Latest\n",
    )
    _write(
        root,
        "plugins/materials-studio-mcp/skills/materials-studio-modeling/SKILL.md",
        "---\nname: materials-studio-modeling\ndescription: Preview first.\n---\n\n# Skill\n",
    )
    _write(
        root,
        ".agents/plugins/marketplace.json",
        json.dumps(
            {
                "name": "wqeeeeeeee-ms-mcp",
                "interface": {"displayName": "Materials Studio MCP"},
                "plugins": [
                    {
                        "name": "materials-studio-mcp",
                        "source": {
                            "source": "local",
                            "path": "./plugins/materials-studio-mcp",
                        },
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_INSTALL",
                        },
                        "category": "Developer Tools",
                    }
                ],
            }
        ),
    )
    _write(root, "README.md", "# Materials Studio MCP\n\nLicensed under MIT.\n")
    for relative in INSTALLER_FILES:
        if relative.endswith(".bat"):
            _write(root, relative, "@echo off\nexit /b 0\n")
        else:
            _write(root, relative, "Set-StrictMode -Version Latest\n")
    for relative in DOCUMENTATION_FILES:
        _write(root, relative, "# Release documentation\n")
    return root


def _sum_lines(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        result[name] = digest
    return result


def test_builds_deterministic_release_without_using_repository_dist(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source with spaces 中文")
    wheel = _make_wheel(tmp_path / "built wheel")
    first = build_release(
        source_root=source,
        wheel_path=wheel,
        output_dir=tmp_path / "out one",
        base_sha=BASE_SHA,
        reference_sha=REFERENCE_SHA,
    )
    second = build_release(
        source_root=source,
        wheel_path=wheel,
        output_dir=tmp_path / "out two 中文",
        base_sha=BASE_SHA,
        reference_sha=REFERENCE_SHA,
    )

    assert first.plugin_zip.read_bytes() == second.plugin_zip.read_bytes()
    assert first.manifest.read_bytes() == second.manifest.read_bytes()
    manifest = json.loads(first.manifest.read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert manifest["base_sha"] == BASE_SHA
    assert manifest["reference_sha"] == REFERENCE_SHA
    assert manifest["repository_license_status"] == "declared"
    assert manifest["repository_license_spdx"] == "MIT"
    assert manifest["repository_copyright"] == "Copyright (c) 2026 Xu kaidong"
    assert manifest["public_distribution_ready"] is True
    assert manifest["release_blockers"] == []
    assert manifest["third_party_notices"]["required"] is False
    assert manifest["wheel"]["path"] == f"materials_studio_mcp-{VERSION}-py3-none-any.whl"
    assert manifest["wheel"]["sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert manifest["real_acceptance"] == {
        "castep": "NOT_RUN",
        "materials_studio": "NOT_RUN",
    }

    bundle_root = f"materials-studio-mcp-plugin-{VERSION}-windows"
    with zipfile.ZipFile(first.plugin_zip) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert len(names) == len(set(names))
        assert all(name.startswith(f"{bundle_root}/") for name in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert f"{bundle_root}/LICENSE" in names
        assert f"{bundle_root}/plugins/materials-studio-mcp/LICENSE" in names
        assert f"{bundle_root}/plugins/materials-studio-mcp/.codex-plugin/plugin.json" in names
        assert f"{bundle_root}/materials_studio_mcp-{VERSION}-py3-none-any.whl" in names
        assert f"{bundle_root}/release-manifest.json" in names
        internal_sums = archive.read(f"{bundle_root}/SHA256SUMS.txt").decode("utf-8")
        for line in internal_sums.splitlines():
            digest, relative = line.split("  ", 1)
            assert hashlib.sha256(archive.read(f"{bundle_root}/{relative}")).hexdigest() == digest

    external = _sum_lines(first.checksums)
    assert external == {
        first.plugin_zip.name: hashlib.sha256(first.plugin_zip.read_bytes()).hexdigest(),
        first.manifest.name: hashlib.sha256(first.manifest.read_bytes()).hexdigest(),
        first.wheel.name: hashlib.sha256(first.wheel.read_bytes()).hexdigest(),
    }
    assert not (source / "dist").exists()


@pytest.mark.parametrize("field", ["skills", "mcpServers"])
def test_rejects_plugin_component_path_traversal(tmp_path: Path, field: str) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    path = source / "plugins/materials-studio-mcp/.codex-plugin/plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = "./../outside"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReleaseBuildError, match="escapes|inside"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


def test_rejects_wheel_metadata_version_mismatch(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel", metadata_version="0.3.1")
    with pytest.raises(ReleaseBuildError, match="wheel metadata name/version"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


@pytest.mark.parametrize(
    "mcp_requirement",
    [
        None,
        "mcp[cli]>=1.12.4",
        "mcp[cli]>=1.12.4,<=2",
        "mcp[cli]>=1.12.4,<3",
        "mcp>=1.12.4,<2",
        "mcp[cli]>=1.12.4,<2; python_version < '3.12'",
    ],
)
def test_rejects_wheel_without_exact_bounded_mcp_runtime_dependency(
    tmp_path: Path, mcp_requirement: str | None
) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel", mcp_requirement=mcp_requirement)
    with pytest.raises(ReleaseBuildError, match="MCP runtime dependency"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


@pytest.mark.parametrize(
    ("wheel_kwargs", "message"),
    [
        ({"core_metadata_version": "2.3"}, "Core Metadata 2.4 or newer"),
        ({"license_expression": None}, "License-Expression must be MIT"),
        ({"license_expression": "Apache-2.0"}, "License-Expression must be MIT"),
        ({"license_file_headers": ()}, "License-File must contain exactly LICENSE"),
        ({"license_file_headers": ("COPYING",)}, "License-File must contain exactly LICENSE"),
        ({"license_member": None}, "exactly one .dist-info/licenses/LICENSE"),
        ({"license_member": b"different\n"}, "wheel LICENSE does not match"),
    ],
)
def test_rejects_wheel_license_metadata_or_member_mismatch(
    tmp_path: Path, wheel_kwargs: dict[str, object], message: str
) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "claim", **wheel_kwargs)
    with pytest.raises(ReleaseBuildError, match=message):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out-claim",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


@pytest.mark.parametrize(
    ("suffix", "message"),
    [
        (".dist-info/entry_points.txt", "entry_points.txt"),
        (".dist-info/RECORD", "RECORD"),
    ],
)
def test_rejects_wheel_missing_entrypoints_or_record(
    tmp_path: Path, suffix: str, message: str
) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    _rewrite_wheel(wheel, remove_suffix=suffix)
    with pytest.raises(ReleaseBuildError, match=message):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


def test_rejects_wheel_record_integrity_mismatch(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    _rewrite_wheel(wheel, tamper_module=True)
    with pytest.raises(ReleaseBuildError, match="RECORD integrity mismatch"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )

def test_rejects_wheel_member_path_traversal(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel_directory = tmp_path / "wheel"
    wheel_directory.mkdir()
    wheel = wheel_directory / f"materials_studio_mcp-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        _zip_write(archive, "../escape.py", b"pass\n")
        _zip_write(
            archive,
            f"materials_studio_mcp-{VERSION}.dist-info/METADATA",
            b"Metadata-Version: 2.4\nName: materials-studio-mcp\nVersion: 0.4.0\n\n",
        )
    with pytest.raises(ReleaseBuildError, match="unsafe archive path"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


def test_rejects_casefold_duplicate_or_archived_symlink_in_wheel(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    duplicate_wheel = _make_wheel(
        tmp_path / "duplicate",
        extra_members={"Material_Studio_MCP_Server/__init__.py": b"duplicate\n"},
    )
    with pytest.raises(ReleaseBuildError, match="duplicate member"):
        build_release(
            source_root=source,
            wheel_path=duplicate_wheel,
            output_dir=tmp_path / "out-duplicate",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )

    symlink_wheel = _make_wheel(tmp_path / "symlink")
    with zipfile.ZipFile(symlink_wheel, "a") as archive:
        info = zipfile.ZipInfo("material_studio_mcp_server/link.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"../outside.py")
    with pytest.raises(ReleaseBuildError, match="symbolic link"):
        build_release(
            source_root=source,
            wheel_path=symlink_wheel,
            output_dir=tmp_path / "out-symlink",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )
def test_rejects_mcp_launcher_allowlist_or_denylist_drift(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    path = source / "plugins/materials-studio-mcp/.mcp.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    mutations = []
    extra_server = json.loads(json.dumps(baseline))
    extra_server["evil"] = {"command": "evil.exe"}
    mutations.append(extra_server)
    injected_args = json.loads(json.dumps(baseline))
    injected_args["materials-studio"]["args"].extend(["&", "evil.cmd"])
    mutations.append(injected_args)
    missing_mode = json.loads(json.dumps(baseline))
    missing_mode["materials-studio"]["env"] = {}
    mutations.append(missing_mode)
    weakened_approval = json.loads(json.dumps(baseline))
    weakened_approval["materials-studio"]["default_tools_approval_mode"] = "auto"
    mutations.append(weakened_approval)
    missing_allow = json.loads(json.dumps(baseline))
    missing_allow["materials-studio"]["enabled_tools"] = []
    mutations.append(missing_allow)
    missing_deny = json.loads(json.dumps(baseline))
    missing_deny["materials-studio"]["disabled_tools"] = []
    mutations.append(missing_deny)
    extra_field = json.loads(json.dumps(baseline))
    extra_field["materials-studio"]["title"] = "unreviewed"
    mutations.append(extra_field)

    for index, mutation in enumerate(mutations):
        path.write_text(json.dumps(mutation), encoding="utf-8")
        with pytest.raises(ReleaseBuildError, match=r"\.mcp\.json"):
            build_release(
                source_root=source,
                wheel_path=wheel,
                output_dir=tmp_path / f"out-{index}",
                base_sha=BASE_SHA,
                reference_sha=REFERENCE_SHA,
            )


def test_rejects_marketplace_source_policy_or_category_drift(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    path = source / ".agents/plugins/marketplace.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    mutations = []
    escaped_source = json.loads(json.dumps(baseline))
    escaped_source["plugins"][0]["source"]["path"] = "../outside"
    mutations.append(escaped_source)
    changed_policy = json.loads(json.dumps(baseline))
    changed_policy["plugins"][0]["policy"]["authentication"] = "NONE"
    mutations.append(changed_policy)
    changed_category = json.loads(json.dumps(baseline))
    changed_category["plugins"][0]["category"] = "Other"
    mutations.append(changed_category)

    for index, mutation in enumerate(mutations):
        path.write_text(json.dumps(mutation), encoding="utf-8")
        with pytest.raises(ReleaseBuildError, match="marketplace"):
            build_release(
                source_root=source,
                wheel_path=wheel,
                output_dir=tmp_path / f"out-{index}",
                base_sha=BASE_SHA,
                reference_sha=REFERENCE_SHA,
            )
def test_rejects_secret_and_concrete_developer_path(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    (source / "README.md").write_text(
        "OPENAI_API_KEY=sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n",
        encoding="utf-8",
    )
    with pytest.raises(ReleaseBuildError, match="secret"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )

    (source / "README.md").write_text(
        "Developer checkout: C:\\Users\\alice\\Documents\\ms_mcp\n",
        encoding="utf-8",
    )
    with pytest.raises(ReleaseBuildError, match="Windows user path"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


def test_rejects_plugin_symlink_or_reparse_point(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    outside = _write(tmp_path, "outside.txt", "outside\n")
    link = source / "plugins/materials-studio-mcp/leak.txt"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    with pytest.raises(ReleaseBuildError, match="links and reparse points"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


def test_rejects_simulated_reparse_directory_when_host_cannot_create_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    linked_directory = source / "plugins/materials-studio-mcp/linked-runtime"
    linked_directory.mkdir()
    original = release_builder._is_link_or_reparse

    def simulated_reparse(path: Path) -> bool:
        return path == linked_directory or original(path)

    monkeypatch.setattr(release_builder, "_is_link_or_reparse", simulated_reparse)
    with pytest.raises(ReleaseBuildError, match="links and reparse points"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )


def test_requires_exact_git_shas(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    with pytest.raises(ReleaseBuildError, match="40-character"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha="main",
            reference_sha=REFERENCE_SHA,
        )


def test_rejects_repository_plugin_or_manifest_license_mismatch(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    wheel = _make_wheel(tmp_path / "wheel")
    _write(source, "plugins/materials-studio-mcp/LICENSE", "different\n")
    with pytest.raises(ReleaseBuildError, match="plugin LICENSE must exactly match"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )

    _write(source, "plugins/materials-studio-mcp/LICENSE", LICENSE_TEXT)
    path = source / "plugins/materials-studio-mcp/.codex-plugin/plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["license"] = "Apache-2.0"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseBuildError, match="manifest license must be MIT"):
        build_release(
            source_root=source,
            wheel_path=wheel,
            output_dir=tmp_path / "out",
            base_sha=BASE_SHA,
            reference_sha=REFERENCE_SHA,
        )
