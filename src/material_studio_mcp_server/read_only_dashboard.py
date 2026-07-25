"""Read-only workspace snapshots and a loopback-only HTTP dashboard.

The service intentionally does not instantiate :class:`ProjectStore`: that
class creates its workspace directory during construction.  Every operation in
this module is a bounded read of an already-existing workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit


_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_SAFE_ARTIFACT_TYPES: Mapping[str, str] = {
    ".json": "application/json; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".cif": "chemical/x-cif; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_TEXT_ARTIFACT_TYPES = frozenset({".json", ".jsonl", ".csv", ".txt", ".log", ".cif"})

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Materials Studio MCP workspace</title>
  <link rel="stylesheet" href="/app.css">
  <script src="/app.js" defer></script>
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">READ-ONLY LOCAL VIEW</p>
      <h1>Materials Studio MCP workspace</h1>
      <p id="status">Loading the current workspace snapshot...</p>
    </header>
    <section id="projects" aria-live="polite"></section>
    <section id="artifact-viewer" hidden>
      <div class="viewer-heading">
        <h2 id="artifact-title">Artifact</h2>
        <button id="artifact-close" type="button">Close</button>
      </div>
      <pre id="artifact-content"></pre>
    </section>
  </main>
</body>
</html>
""".encode("utf-8")

_DASHBOARD_CSS = b"""*{box-sizing:border-box}body{margin:0;background:#f5f2eb;color:#162119;
font:15px/1.5 system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:48px 24px}
header{border-bottom:1px solid #c8c2b5;margin-bottom:28px}.eyebrow{font-size:12px;
letter-spacing:.14em;color:#496252}h1{font-size:clamp(30px,5vw,54px);margin:.15em 0}
#status{color:#58625a}.project{background:#fff;border:1px solid #d7d2c7;border-radius:12px;
padding:20px;margin:16px 0;box-shadow:0 8px 30px #17251b0a}.project h2{margin:0}
.meta{color:#657067}.artifacts{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
gap:8px;padding:0;list-style:none}.artifact{width:100%;text-align:left;border:1px solid #d9ded8;
background:#f8faf7;padding:10px;border-radius:7px;cursor:pointer}.artifact:hover{border-color:#47765a}
#artifact-viewer{position:fixed;inset:5vh 5vw;background:#101713;color:#dce8df;padding:20px;
border-radius:12px;overflow:auto;box-shadow:0 20px 90px #0008}.viewer-heading{display:flex;
justify-content:space-between;align-items:center;position:sticky;top:0;background:#101713}
#artifact-close{padding:8px 14px}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warning{color:#8b4d25}
"""

_DASHBOARD_JS = """'use strict';
const statusNode=document.getElementById('status');
const projectsNode=document.getElementById('projects');
const viewer=document.getElementById('artifact-viewer');
const viewerTitle=document.getElementById('artifact-title');
const viewerContent=document.getElementById('artifact-content');
document.getElementById('artifact-close').addEventListener('click',()=>{viewer.hidden=true;});
function node(tag,text,className){const value=document.createElement(tag);
  if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;}
async function showArtifact(projectId,revision,item){
  const query=new URLSearchParams({project_id:projectId,revision:String(revision),path:item.path});
  viewerTitle.textContent=item.path;viewerContent.textContent='Loading...';viewer.hidden=false;
  try{const response=await fetch('/api/artifact?'+query.toString(),{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    if(item.content_type.startsWith('image/')){
      viewerContent.textContent='Raster image artifact. Open the API URL directly to inspect it.';
    }else{viewerContent.textContent=await response.text();}
  }catch(error){viewerContent.textContent='Unable to read artifact: '+error.message;}}
function renderProject(project){
  const card=node('article',undefined,'project');card.append(node('h2',project.project_id));
  const revision=project.revision===null?'no current revision':'revision r'+String(project.revision).padStart(3,'0');
  card.append(node('p',[revision,project.model_type,project.model_name].filter(Boolean).join(' / '),'meta'));
  if(project.status!=='ready')card.append(node('p',project.error||project.status,'warning'));
  const list=node('ul',undefined,'artifacts');
  for(const item of project.artifact_index.items||[]){const row=node('li');
    const button=node('button',item.path+' / '+item.size_bytes+' bytes','artifact');
    button.type='button';button.disabled=!item.readable;
    button.addEventListener('click',()=>showArtifact(project.project_id,project.revision,item));
    row.append(button);list.append(row);}card.append(list);return card;}
async function load(){try{const response=await fetch('/api/snapshot',{cache:'no-store'});
  if(!response.ok)throw new Error('HTTP '+response.status);const snapshot=await response.json();
  statusNode.textContent=snapshot.project_count+' project(s) / generated '+snapshot.generated_at;
  projectsNode.replaceChildren(...snapshot.projects.map(renderProject));
}catch(error){statusNode.textContent='Unable to load snapshot: '+error.message;statusNode.className='warning';}}
load();
""".encode("utf-8")


class DashboardError(RuntimeError):
    """A bounded dashboard request could not be completed safely."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class DashboardLimits:
    """Hard limits for workspace traversal and response materialization."""

    max_projects: int = 100
    max_json_bytes: int = 512 * 1024
    max_artifact_bytes: int = 2 * 1024 * 1024
    max_artifacts_per_revision: int = 200
    max_directory_entries: int = 2_000
    max_directory_depth: int = 8
    max_http_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for field_name, value in self.__dict__.items():
            if not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer.")
        if self.max_projects > 1_000:
            raise ValueError("max_projects may not exceed 1000.")
        if self.max_directory_depth > 32:
            raise ValueError("max_directory_depth may not exceed 32.")


@dataclass(frozen=True)
class ArtifactContent:
    """A verified artifact payload returned by ``read_artifact``."""

    project_id: str
    revision: int
    relative_path: str
    content_type: str
    payload: bytes
    content_sha256: str
    mtime_ns: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_link_like_stat(details: os.stat_result) -> bool:
    if stat.S_ISLNK(details.st_mode):
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _is_link_like(path: Path) -> bool:
    try:
        return _is_link_like_stat(path.lstat())
    except FileNotFoundError:
        return False


def _assert_no_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for candidate in list(reversed(absolute.parents)) + [absolute]:
        if os.path.lexists(candidate) and _is_link_like(candidate):
            raise DashboardError(
                "Workspace path contains a symlink or reparse point.",
                status=403,
                code="link_path_forbidden",
            )


def _safe_string(value: Any, *, max_characters: int = 256) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    rendered = "".join(
        character if ord(character) >= 32 or character in "\t" else " "
        for character in rendered
    ).strip()
    if not rendered:
        return None
    if len(rendered) > max_characters:
        return rendered[: max_characters - 1] + "\u2026"
    return rendered


def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or _PROJECT_ID_RE.fullmatch(project_id) is None:
        raise DashboardError(
            "project_id must contain only letters, digits, underscores, or hyphens.",
            code="invalid_project_id",
        )
    return project_id


def _validate_revision(revision: int | str) -> int:
    if isinstance(revision, bool):
        raise DashboardError("revision must be a non-negative integer.", code="invalid_revision")
    if isinstance(revision, str):
        if not revision.isascii() or not revision.isdigit():
            raise DashboardError(
                "revision must be a non-negative integer.",
                code="invalid_revision",
            )
        parsed = int(revision, 10)
    elif isinstance(revision, int):
        parsed = revision
    else:
        raise DashboardError("revision must be a non-negative integer.", code="invalid_revision")
    if parsed < 0 or parsed > 999_999_999:
        raise DashboardError("revision is outside the supported range.", code="invalid_revision")
    return parsed


def _validate_relative_artifact_path(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path or len(relative_path) > 1024:
        raise DashboardError("Artifact path is empty or too long.", code="invalid_artifact_path")
    if "\\" in relative_path or "\x00" in relative_path or ":" in relative_path:
        raise DashboardError(
            "Artifact path contains a forbidden character.",
            code="invalid_artifact_path",
        )
    raw_parts = relative_path.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise DashboardError(
            "Artifact path traversal is forbidden.",
            code="invalid_artifact_path",
        )
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute():
        raise DashboardError("Absolute artifact paths are forbidden.", code="invalid_artifact_path")
    parts = parsed.parts
    if not parts:
        raise DashboardError(
            "Artifact path traversal is forbidden.",
            code="invalid_artifact_path",
        )
    if len(parts) > 32:
        raise DashboardError("Artifact path is too deep.", code="invalid_artifact_path")
    return parts


def _bounded_read(path: Path, *, max_bytes: int) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise DashboardError("Requested file was not found.", status=404, code="not_found") from exc
    if _is_link_like_stat(before):
        raise DashboardError(
            "Symlink and reparse-point reads are forbidden.",
            status=403,
            code="link_path_forbidden",
        )
    if not stat.S_ISREG(before.st_mode):
        raise DashboardError("Requested path is not a file.", status=404, code="not_found")
    if before.st_size > max_bytes:
        raise DashboardError(
            "Requested file exceeds the configured byte limit.",
            status=413,
            code="file_too_large",
        )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DashboardError(
            "Requested file could not be opened safely.",
            status=403,
            code="unsafe_file",
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise DashboardError("Requested path is not a regular file.", status=404, code="not_found")
        if (
            getattr(before, "st_dev", None),
            getattr(before, "st_ino", None),
        ) != (
            getattr(opened, "st_dev", None),
            getattr(opened, "st_ino", None),
        ):
            raise DashboardError(
                "Requested file changed during the safe-open check.",
                status=409,
                code="file_changed",
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise DashboardError(
                    "Requested file exceeds the configured byte limit.",
                    status=413,
                    code="file_too_large",
                )
        return b"".join(chunks), opened
    finally:
        os.close(descriptor)


def _decode_json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8-sig", errors="strict")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DashboardError(
            f"{label} is not valid UTF-8 JSON.",
            status=422,
            code="invalid_json",
        ) from exc
    if not isinstance(value, dict):
        raise DashboardError(
            f"{label} must contain a JSON object.",
            status=422,
            code="invalid_json",
        )
    return value


class WorkspaceSnapshotService:
    """Bounded read-only access to an existing Materials Studio MCP workspace."""

    def __init__(
        self,
        workspace_root: str | os.PathLike[str],
        *,
        limits: DashboardLimits | None = None,
    ) -> None:
        self.workspace_root = Path(os.path.abspath(os.fspath(workspace_root)))
        self.limits = limits or DashboardLimits()
        _assert_no_link_components(self.workspace_root)
        try:
            details = self.workspace_root.stat()
        except FileNotFoundError as exc:
            raise DashboardError(
                "Workspace does not exist; the read-only dashboard will not create it.",
                status=404,
                code="workspace_not_found",
            ) from exc
        if not stat.S_ISDIR(details.st_mode):
            raise DashboardError(
                "Workspace root is not a directory.",
                status=400,
                code="invalid_workspace",
            )

    def _project_directory(self, project_id: str) -> Path:
        validated = _validate_project_id(project_id)
        project_directory = self.workspace_root / validated
        if not os.path.lexists(project_directory):
            raise DashboardError("Project was not found.", status=404, code="project_not_found")
        if _is_link_like(project_directory) or not project_directory.is_dir():
            raise DashboardError(
                "Project directory is link-like or invalid.",
                status=403,
                code="unsafe_project",
            )
        return project_directory

    def _output_directory(
        self,
        project_id: str,
        revision: int | str,
        *,
        required: bool,
    ) -> Path | None:
        project_directory = self._project_directory(project_id)
        parsed_revision = _validate_revision(revision)
        outputs_directory = project_directory / "outputs"
        revision_directory = outputs_directory / f"r{parsed_revision:03d}"
        current = project_directory
        for path in (outputs_directory, revision_directory):
            if not os.path.lexists(path):
                if required:
                    raise DashboardError(
                        "Revision output directory was not found.",
                        status=404,
                        code="output_not_found",
                    )
                return None
            if _is_link_like(path) or not path.is_dir():
                raise DashboardError(
                    "Revision output path is link-like or invalid.",
                    status=403,
                    code="unsafe_output_path",
                )
            current = path
        return current

    def _read_current(self, project_directory: Path) -> tuple[dict[str, Any], os.stat_result]:
        current_path = project_directory / "current.json"
        payload, details = _bounded_read(
            current_path,
            max_bytes=self.limits.max_json_bytes,
        )
        return _decode_json_object(payload, label="current.json"), details

    def _summarize_current(
        self,
        directory_project_id: str,
        current: Mapping[str, Any],
        details: os.stat_result,
    ) -> dict[str, Any]:
        current_project_id = current.get("project_id")
        revision_value = current.get("revision")
        if current_project_id != directory_project_id:
            raise DashboardError(
                "current.json project identity does not match its directory.",
                status=409,
                code="current_identity_mismatch",
            )
        revision = _validate_revision(revision_value)
        spec = current.get("spec")
        if not isinstance(spec, dict):
            raise DashboardError(
                "current.json has no embedded model spec.",
                status=422,
                code="invalid_current",
            )
        embedded_project_id = spec.get("project_id")
        try:
            embedded_revision = _validate_revision(spec.get("revision"))
        except DashboardError as exc:
            raise DashboardError(
                "current.json embedded spec revision does not match its pointer.",
                status=409,
                code="current_identity_mismatch",
            ) from exc
        if (
            embedded_project_id != directory_project_id
            or embedded_project_id != current_project_id
            or embedded_revision != revision
        ):
            raise DashboardError(
                "current.json embedded spec identity does not match its pointer.",
                status=409,
                code="current_identity_mismatch",
            )
        model = spec.get("model") if isinstance(spec.get("model"), dict) else {}
        simulation = (
            spec.get("simulation") if isinstance(spec.get("simulation"), dict) else {}
        )
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
        return {
            "project_id": directory_project_id,
            "status": "ready",
            "error": None,
            "revision": revision,
            "model_type": _safe_string(spec.get("model_type")),
            "model_name": _safe_string(
                model.get("name") or spec.get("name") or metadata.get("name")
            ),
            "software": _safe_string(spec.get("software")),
            "simulation_module": _safe_string(simulation.get("module")),
            "simulation_task": _safe_string(simulation.get("task")),
            "current_mtime_ns": details.st_mtime_ns,
        }

    def list_projects(self) -> dict[str, Any]:
        projects: list[dict[str, Any]] = []
        scanned_entries = 0
        truncated = False
        try:
            iterator = os.scandir(self.workspace_root)
        except OSError as exc:
            raise DashboardError(
                "Workspace directory could not be read.",
                status=500,
                code="workspace_read_failed",
            ) from exc
        with iterator:
            for entry in iterator:
                scanned_entries += 1
                if scanned_entries > self.limits.max_directory_entries:
                    truncated = True
                    break
                if len(projects) >= self.limits.max_projects:
                    truncated = True
                    break
                if _PROJECT_ID_RE.fullmatch(entry.name) is None:
                    continue
                try:
                    entry_details = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link_like_stat(entry_details) or not stat.S_ISDIR(entry_details.st_mode):
                    continue
                projects.append({"project_id": entry.name})
        projects.sort(key=lambda item: item["project_id"].lower())
        return {
            "items": projects,
            "truncated": truncated,
            "scanned_entries": scanned_entries,
        }

    def list_artifacts(
        self,
        project_id: str,
        revision: int | str,
    ) -> dict[str, Any]:
        validated_project_id = _validate_project_id(project_id)
        parsed_revision = _validate_revision(revision)
        output_directory = self._output_directory(
            validated_project_id,
            parsed_revision,
            required=False,
        )
        if output_directory is None:
            return {
                "project_id": validated_project_id,
                "revision": parsed_revision,
                "status": "not_materialized",
                "items": [],
                "artifact_count": 0,
                "truncated": False,
                "rejected_link_count": 0,
                "scanned_entries": 0,
            }

        items: list[dict[str, Any]] = []
        stack: list[tuple[Path, tuple[str, ...], int]] = [
            (output_directory, (), 0)
        ]
        scanned_entries = 0
        rejected_link_count = 0
        truncated = False

        while stack and not truncated:
            directory, relative_parts, depth = stack.pop()
            try:
                iterator = os.scandir(directory)
            except OSError:
                continue
            subdirectories: list[tuple[Path, tuple[str, ...], int]] = []
            with iterator:
                for entry in iterator:
                    scanned_entries += 1
                    if scanned_entries > self.limits.max_directory_entries:
                        truncated = True
                        break
                    try:
                        details = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _is_link_like_stat(details):
                        rejected_link_count += 1
                        continue
                    next_parts = relative_parts + (entry.name,)
                    try:
                        _validate_relative_artifact_path(
                            PurePosixPath(*next_parts).as_posix()
                        )
                    except DashboardError:
                        continue
                    if stat.S_ISDIR(details.st_mode):
                        if depth >= self.limits.max_directory_depth:
                            truncated = True
                            continue
                        subdirectories.append((Path(entry.path), next_parts, depth + 1))
                        continue
                    if not stat.S_ISREG(details.st_mode):
                        continue
                    suffix = Path(entry.name).suffix.lower()
                    if suffix not in _SAFE_ARTIFACT_TYPES:
                        continue
                    if len(items) >= self.limits.max_artifacts_per_revision:
                        truncated = True
                        break
                    items.append(
                        {
                            "path": PurePosixPath(*next_parts).as_posix(),
                            "size_bytes": details.st_size,
                            "mtime_ns": details.st_mtime_ns,
                            "content_type": _SAFE_ARTIFACT_TYPES[suffix].split(";", 1)[0],
                            "readable": details.st_size <= self.limits.max_artifact_bytes
                            and (
                                suffix != ".json"
                                or details.st_size <= self.limits.max_json_bytes
                            ),
                        }
                    )
            stack.extend(reversed(sorted(subdirectories, key=lambda item: item[1])))

        items.sort(key=lambda item: item["path"].lower())
        return {
            "project_id": validated_project_id,
            "revision": parsed_revision,
            "status": "ready",
            "items": items,
            "artifact_count": len(items),
            "truncated": truncated,
            "rejected_link_count": rejected_link_count,
            "scanned_entries": scanned_entries,
        }

    def snapshot(self) -> dict[str, Any]:
        project_listing = self.list_projects()
        projects: list[dict[str, Any]] = []
        for entry in project_listing["items"]:
            project_id = entry["project_id"]
            project_directory = self.workspace_root / project_id
            try:
                current, details = self._read_current(project_directory)
                project = self._summarize_current(project_id, current, details)
                try:
                    project["artifact_index"] = self.list_artifacts(
                        project_id,
                        project["revision"],
                    )
                except DashboardError as exc:
                    project["artifact_index"] = {
                        "status": "unavailable",
                        "items": [],
                        "artifact_count": 0,
                        "truncated": False,
                        "error": exc.code,
                    }
            except DashboardError as exc:
                project = {
                    "project_id": project_id,
                    "status": "unavailable",
                    "error": exc.code,
                    "revision": None,
                    "model_type": None,
                    "model_name": None,
                    "software": None,
                    "simulation_module": None,
                    "simulation_task": None,
                    "current_mtime_ns": None,
                    "artifact_index": {
                        "status": "unavailable",
                        "items": [],
                        "artifact_count": 0,
                        "truncated": False,
                    },
                }
            projects.append(project)
        return {
            "schema_version": "materials-studio-workspace-snapshot/v1",
            "read_only": True,
            "generated_at": _utc_now(),
            "workspace_root": str(self.workspace_root),
            "project_count": len(projects),
            "projects_truncated": project_listing["truncated"],
            "projects": projects,
        }

    def _safe_artifact_path(
        self,
        project_id: str,
        revision: int | str,
        relative_path: str,
    ) -> tuple[str, int, str, Path]:
        validated_project_id = _validate_project_id(project_id)
        parsed_revision = _validate_revision(revision)
        parts = _validate_relative_artifact_path(relative_path)
        output_directory = self._output_directory(
            validated_project_id,
            parsed_revision,
            required=True,
        )
        assert output_directory is not None
        candidate = output_directory.joinpath(*parts)
        current = output_directory
        for part in parts:
            current = current / part
            if not os.path.lexists(current):
                raise DashboardError(
                    "Artifact was not found.",
                    status=404,
                    code="artifact_not_found",
                )
            if _is_link_like(current):
                raise DashboardError(
                    "Symlink and reparse-point artifacts are forbidden.",
                    status=403,
                    code="link_path_forbidden",
                )

        try:
            resolved_output = output_directory.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            common = os.path.commonpath(
                (os.fspath(resolved_output), os.fspath(resolved_candidate))
            )
        except (OSError, ValueError) as exc:
            raise DashboardError(
                "Artifact path could not be resolved safely.",
                status=403,
                code="unsafe_artifact_path",
            ) from exc
        if os.path.normcase(common) != os.path.normcase(os.fspath(resolved_output)):
            raise DashboardError(
                "Artifact path escaped its revision output directory.",
                status=403,
                code="path_escape",
            )
        return (
            validated_project_id,
            parsed_revision,
            PurePosixPath(*parts).as_posix(),
            candidate,
        )

    def read_artifact(
        self,
        project_id: str,
        revision: int | str,
        relative_path: str,
    ) -> ArtifactContent:
        (
            validated_project_id,
            parsed_revision,
            normalized_relative_path,
            candidate,
        ) = self._safe_artifact_path(project_id, revision, relative_path)
        suffix = candidate.suffix.lower()
        if suffix not in _SAFE_ARTIFACT_TYPES:
            raise DashboardError(
                "Artifact type is not in the read-only allowlist.",
                status=415,
                code="unsupported_artifact_type",
            )
        byte_limit = self.limits.max_artifact_bytes
        if suffix == ".json":
            byte_limit = min(byte_limit, self.limits.max_json_bytes)
        payload, details = _bounded_read(candidate, max_bytes=byte_limit)
        self._validate_artifact_payload(payload, suffix=suffix)
        return ArtifactContent(
            project_id=validated_project_id,
            revision=parsed_revision,
            relative_path=normalized_relative_path,
            content_type=_SAFE_ARTIFACT_TYPES[suffix],
            payload=payload,
            content_sha256=hashlib.sha256(payload).hexdigest(),
            mtime_ns=details.st_mtime_ns,
        )

    @staticmethod
    def _validate_artifact_payload(payload: bytes, *, suffix: str) -> None:
        if suffix in _TEXT_ARTIFACT_TYPES:
            try:
                decoded = payload.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError as exc:
                raise DashboardError(
                    "Text artifact is not valid UTF-8.",
                    status=422,
                    code="invalid_text_artifact",
                ) from exc
            if "\x00" in decoded:
                raise DashboardError(
                    "Text artifact contains NUL bytes.",
                    status=422,
                    code="invalid_text_artifact",
                )
            if suffix == ".json":
                try:
                    json.loads(decoded)
                except json.JSONDecodeError as exc:
                    raise DashboardError(
                        "JSON artifact is invalid.",
                        status=422,
                        code="invalid_json",
                    ) from exc
            elif suffix == ".jsonl":
                try:
                    for line in decoded.splitlines():
                        if line.strip():
                            json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DashboardError(
                        "JSONL artifact contains an invalid record.",
                        status=422,
                        code="invalid_jsonl",
                    ) from exc
            return
        signatures = {
            ".png": payload.startswith(b"\x89PNG\r\n\x1a\n"),
            ".jpg": payload.startswith(b"\xff\xd8\xff"),
            ".jpeg": payload.startswith(b"\xff\xd8\xff"),
            ".webp": len(payload) >= 12
            and payload.startswith(b"RIFF")
            and payload[8:12] == b"WEBP",
        }
        if not signatures.get(suffix, False):
            raise DashboardError(
                "Raster artifact signature does not match its extension.",
                status=422,
                code="invalid_raster_artifact",
            )


def _json_response_bytes(value: Any, *, limit: int) -> bytes:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    if len(payload) > limit:
        raise DashboardError(
            "Dashboard JSON response exceeds its configured limit.",
            status=413,
            code="response_too_large",
        )
    return payload


def _host_header_is_safe(host_header: str | None, *, expected_port: int) -> bool:
    if (
        host_header is None
        or not host_header
        or len(host_header) > 255
        or "," in host_header
        or any(ord(character) < 32 or ord(character) == 127 for character in host_header)
    ):
        return False
    try:
        parsed = urlsplit("//" + host_header)
        port = parsed.port
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None or parsed.hostname is None:
        return False
    if parsed.path or parsed.query or parsed.fragment or host_header.endswith(":"):
        return False
    hostname = parsed.hostname.rstrip(".").lower()
    if port is not None and port != expected_port:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _single_query_value(
    parameters: Mapping[str, Sequence[str]],
    name: str,
) -> str:
    values = parameters.get(name)
    if not values or len(values) != 1 or values[0] == "":
        raise DashboardError(
            f"Query parameter {name!r} must appear exactly once.",
            code="invalid_query",
        )
    return values[0]


def _handler_for(service: WorkspaceSnapshotService):
    class ReadOnlyDashboardHandler(BaseHTTPRequestHandler):
        server_version = "MaterialsStudioReadOnlyDashboard/1"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            # A library embedding the server may add its own non-workspace logger.
            return

        def do_GET(self) -> None:
            self._dispatch(head_only=False)

        def do_HEAD(self) -> None:
            self._dispatch(head_only=True)

        def do_POST(self) -> None:
            self._method_not_allowed()

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:
            self._method_not_allowed()

        def do_TRACE(self) -> None:
            self._method_not_allowed()

        def do_CONNECT(self) -> None:
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            head_only = self.command == "HEAD"
            self._send_error(
                DashboardError(
                    "Only GET and HEAD are supported.",
                    status=405,
                    code="method_not_allowed",
                ),
                head_only=head_only,
                extra_headers={"Allow": "GET, HEAD"},
            )

        def _dispatch(self, *, head_only: bool) -> None:
            if not _host_header_is_safe(
                self.headers.get("Host"),
                expected_port=self.server.server_port,
            ):
                self._send_error(
                    DashboardError(
                        "Host header is not loopback-bound.",
                        status=421,
                        code="unsafe_host_header",
                    ),
                    head_only=head_only,
                )
                return
            if len(self.path) > 8192:
                self._send_error(
                    DashboardError(
                        "Request target is too long.",
                        status=414,
                        code="request_target_too_long",
                    ),
                    head_only=head_only,
                )
                return
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                self._send_error(
                    DashboardError(
                        "Only origin-form request targets are accepted.",
                        code="invalid_request_target",
                    ),
                    head_only=head_only,
                )
                return
            try:
                if parsed.path == "/":
                    self._require_no_query(parsed.query)
                    self._send_bytes(
                        200,
                        _DASHBOARD_HTML,
                        "text/html; charset=utf-8",
                        head_only=head_only,
                    )
                    return
                if parsed.path == "/app.css":
                    self._require_no_query(parsed.query)
                    self._send_bytes(
                        200,
                        _DASHBOARD_CSS,
                        "text/css; charset=utf-8",
                        head_only=head_only,
                    )
                    return
                if parsed.path == "/app.js":
                    self._require_no_query(parsed.query)
                    self._send_bytes(
                        200,
                        _DASHBOARD_JS,
                        "text/javascript; charset=utf-8",
                        head_only=head_only,
                    )
                    return
                if parsed.path == "/healthz":
                    self._require_no_query(parsed.query)
                    payload = _json_response_bytes(
                        {"ok": True, "read_only": True},
                        limit=service.limits.max_http_response_bytes,
                    )
                    self._send_bytes(
                        200,
                        payload,
                        "application/json; charset=utf-8",
                        head_only=head_only,
                    )
                    return
                if parsed.path == "/api/snapshot":
                    self._require_no_query(parsed.query)
                    payload = _json_response_bytes(
                        service.snapshot(),
                        limit=service.limits.max_http_response_bytes,
                    )
                    self._send_bytes(
                        200,
                        payload,
                        "application/json; charset=utf-8",
                        head_only=head_only,
                    )
                    return

                parameters = parse_qs(
                    parsed.query,
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=8,
                )
                if parsed.path == "/api/artifacts":
                    self._require_exact_query_keys(
                        parameters,
                        {"project_id", "revision"},
                    )
                    result = service.list_artifacts(
                        _single_query_value(parameters, "project_id"),
                        _single_query_value(parameters, "revision"),
                    )
                    payload = _json_response_bytes(
                        result,
                        limit=service.limits.max_http_response_bytes,
                    )
                    self._send_bytes(
                        200,
                        payload,
                        "application/json; charset=utf-8",
                        head_only=head_only,
                    )
                    return
                if parsed.path == "/api/artifact":
                    self._require_exact_query_keys(
                        parameters,
                        {"project_id", "revision", "path"},
                    )
                    artifact = service.read_artifact(
                        _single_query_value(parameters, "project_id"),
                        _single_query_value(parameters, "revision"),
                        _single_query_value(parameters, "path"),
                    )
                    self._send_bytes(
                        200,
                        artifact.payload,
                        artifact.content_type,
                        head_only=head_only,
                        extra_headers={
                            "ETag": f'"sha256-{artifact.content_sha256}"',
                            "X-Content-SHA256": artifact.content_sha256,
                        },
                    )
                    return
                raise DashboardError("Route was not found.", status=404, code="not_found")
            except ValueError:
                self._send_error(
                    DashboardError(
                        "Query string is malformed.",
                        code="invalid_query",
                    ),
                    head_only=head_only,
                )
            except DashboardError as exc:
                self._send_error(exc, head_only=head_only)

        @staticmethod
        def _require_no_query(query: str) -> None:
            if query:
                raise DashboardError(
                    "This route does not accept query parameters.",
                    code="invalid_query",
                )

        @staticmethod
        def _require_exact_query_keys(
            parameters: Mapping[str, Sequence[str]],
            expected: set[str],
        ) -> None:
            if set(parameters) != expected:
                raise DashboardError(
                    "Query parameters do not match the route contract.",
                    code="invalid_query",
                )

        def _send_error(
            self,
            error: DashboardError,
            *,
            head_only: bool,
            extra_headers: Mapping[str, str] | None = None,
        ) -> None:
            try:
                payload = _json_response_bytes(
                    {
                        "error": error.code,
                        "message": str(error),
                        "read_only": True,
                    },
                    limit=64 * 1024,
                )
            except DashboardError:
                payload = b'{"error":"response_error","read_only":true}\n'
            self._send_bytes(
                error.status,
                payload,
                "application/json; charset=utf-8",
                head_only=head_only,
                extra_headers=extra_headers,
            )

        def _send_bytes(
            self,
            status: int,
            payload: bytes,
            content_type: str,
            *,
            head_only: bool,
            extra_headers: Mapping[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'; object-src 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header(
                "Permissions-Policy",
                "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            )
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

    return ReadOnlyDashboardHandler


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except OSError:
            pass
        super().server_bind()


def create_dashboard_server(
    workspace_root: str | os.PathLike[str],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    limits: DashboardLimits | None = None,
) -> ThreadingHTTPServer:
    """Create, but do not start, a loopback-only read-only dashboard server."""

    try:
        bind_address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise DashboardError(
            "Dashboard host must be an explicit loopback IP address.",
            code="invalid_bind_host",
        ) from exc
    if not bind_address.is_loopback:
        raise DashboardError(
            "Dashboard may bind only to a loopback address.",
            status=403,
            code="non_loopback_bind_forbidden",
        )
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise DashboardError("Dashboard port must be between 0 and 65535.", code="invalid_port")

    service = WorkspaceSnapshotService(workspace_root, limits=limits)
    server_class: type[ThreadingHTTPServer]
    if bind_address.version == 6:
        server_class = _IPv6ThreadingHTTPServer
    else:
        server_class = ThreadingHTTPServer
    server = server_class(
        (str(bind_address), port),
        _handler_for(service),
    )
    server.daemon_threads = True
    setattr(server, "snapshot_service", service)
    return server


def serve_dashboard(
    workspace_root: str | os.PathLike[str],
    *,
    host: str = "127.0.0.1",
    port: int = 4877,
    limits: DashboardLimits | None = None,
) -> None:
    """Serve the dashboard until interrupted."""

    server = create_dashboard_server(
        workspace_root,
        host=host,
        port=port,
        limits=limits,
    )
    bound = server.server_address
    rendered_host = f"[{bound[0]}]" if ":" in str(bound[0]) else str(bound[0])
    print(f"Read-only Materials Studio dashboard: http://{rendered_host}:{bound[1]}/")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for ``python -m material_studio_mcp_server.read_only_dashboard``."""

    parser = argparse.ArgumentParser(
        description="Serve a loopback-only, read-only Materials Studio MCP dashboard."
    )
    parser.add_argument("--workspace", required=True, help="Existing MCP workspace directory")
    parser.add_argument("--host", default="127.0.0.1", help="Explicit loopback IP address")
    parser.add_argument("--port", type=int, default=4877, help="Loopback TCP port")
    arguments = parser.parse_args(argv)
    try:
        serve_dashboard(
            arguments.workspace,
            host=arguments.host,
            port=arguments.port,
        )
    except KeyboardInterrupt:
        return 0
    except DashboardError as exc:
        parser.error(str(exc))
    return 0


__all__ = [
    "ArtifactContent",
    "DashboardError",
    "DashboardLimits",
    "WorkspaceSnapshotService",
    "create_dashboard_server",
    "main",
    "serve_dashboard",
]


if __name__ == "__main__":
    raise SystemExit(main())
