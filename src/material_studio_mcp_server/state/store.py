"""结构化建模工作流的持久化项目状态存储。

此模块提供了基于文件的项目存储，支持仅追加历史。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from material_studio_mcp_server.specs.project import ModelSpec

from .diff import diff_specs
from .history import make_history_event


# 项目 ID 正则表达式
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
REVISION_FILE_RE = re.compile(r"^r(?P<revision>\d+)_model_spec\.json$")
MAX_CURRENT_POINTER_ERROR_LENGTH = 1000


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace one UTF-8 text file in its destination directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _bounded_error(exc: Exception) -> str:
    """Return a stable bounded error string for status payloads."""

    message = str(exc).strip() or exc.__class__.__name__
    return message[:MAX_CURRENT_POINTER_ERROR_LENGTH]


@dataclass(frozen=True)
class RevisionInfo:
    """修订版本信息。

    属性:
        project_id: 项目 ID
        revision: 修订版本号
        project_dir: 项目目录
        spec_path: 规格文件路径
        current_path: 当前文件路径
        script_path: 脚本文件路径
    """

    project_id: str
    revision: int
    project_dir: Path
    spec_path: Path
    current_path: Path
    script_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回字典表示。"""
        return {
            "project_id": self.project_id,
            "revision": self.revision,
            "project_dir": str(self.project_dir),
            "spec_path": str(self.spec_path),
            "current_path": str(self.current_path),
            "script_path": str(self.script_path) if self.script_path else None,
        }


def default_workspace_root() -> Path:
    """返回默认的工作区根目录。

    返回:
        工作区根目录路径
    """
    configured = os.environ.get("MATERIAL_STUDIO_MCP_WORKSPACE")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return (Path(base) / "materials_studio_mcp" / "workspace").resolve()
    return (Path.home() / ".local" / "share" / "materials_studio_mcp" / "workspace").resolve()


def sanitize_project_id(project_id: str) -> str:
    """清理项目 ID。

    参数:
        project_id: 原始项目 ID

    返回:
        清理后的项目 ID

    异常:
        ValueError: 如果项目 ID 包含无效字符
    """
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("project_id 只能包含字母、数字、下划线和破折号")
    return project_id


class ProjectStore:
    """基于文件的项目存储，支持仅追加历史。"""

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        """初始化项目存储。

        参数:
            workspace_root: 工作区根目录
        """
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else default_workspace_root()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        """获取项目目录。

        参数:
            project_id: 项目 ID

        返回:
            项目目录路径

        异常:
            ValueError: 如果路径逃逸工作区根目录
        """
        safe_id = sanitize_project_id(project_id)
        path = (self.workspace_root / safe_id).resolve()
        if self.workspace_root not in path.parents and path != self.workspace_root:
            raise ValueError("项目路径逃逸工作区根目录")
        return path

    def _revision_candidates(self, project_id: str) -> list[tuple[int, Path]]:
        """Return revision files ordered from newest revision to oldest."""

        revisions_dir = self.project_dir(project_id) / "revisions"
        if not revisions_dir.exists():
            return []
        candidates: list[tuple[int, Path]] = []
        for path in revisions_dir.iterdir():
            if not path.is_file():
                continue
            match = REVISION_FILE_RE.fullmatch(path.name)
            if match is None:
                continue
            candidates.append((int(match.group("revision")), path.resolve()))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates

    def _latest_valid_revision(
        self,
        project_id: str,
    ) -> tuple[ModelSpec, Path, list[dict[str, Any]]] | None:
        """Return the newest valid immutable revision without modifying state."""

        invalid_revisions: list[dict[str, Any]] = []
        for revision, path in self._revision_candidates(project_id):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                spec = ModelSpec.model_validate(payload)
                if spec.project_id != project_id:
                    raise ValueError(
                        f"revision project_id {spec.project_id!r} does not match {project_id!r}"
                    )
                if spec.revision != revision:
                    raise ValueError(
                        f"revision payload r{spec.revision:03d} does not match filename r{revision:03d}"
                    )
                return spec, path, invalid_revisions
            except Exception as exc:
                invalid_revisions.append(
                    {
                        "revision": revision,
                        "path": str(path),
                        "error_type": exc.__class__.__name__,
                        "error": _bounded_error(exc),
                    }
                )
        return None

    def previous_valid_revision(
        self,
        project_id: str,
        before_revision: int,
    ) -> tuple[ModelSpec | None, list[dict[str, Any]]]:
        """Return the newest valid immutable revision older than a revision."""

        invalid_revisions: list[dict[str, Any]] = []
        for revision, path in self._revision_candidates(project_id):
            if revision >= before_revision:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                spec = ModelSpec.model_validate(payload)
                if spec.project_id != project_id:
                    raise ValueError(
                        f"revision project_id {spec.project_id!r} does not match {project_id!r}"
                    )
                if spec.revision != revision:
                    raise ValueError(
                        f"revision payload r{spec.revision:03d} does not match filename r{revision:03d}"
                    )
                return spec, invalid_revisions
            except Exception as exc:
                invalid_revisions.append(
                    {
                        "revision": revision,
                        "path": str(path),
                        "error_type": exc.__class__.__name__,
                        "error": _bounded_error(exc),
                    }
                )
        return None, invalid_revisions

    def _next_revision_number(self, project_id: str) -> int:
        """Choose an append-only revision number without overwriting orphan files."""

        candidates = self._revision_candidates(project_id)
        return (candidates[0][0] + 1) if candidates else 0

    def next_revision_number(self, project_id: str) -> int:
        """Return the revision number an explicit next write must use."""

        current = self.load_current(project_id)
        return max(current.revision + 1, self._next_revision_number(project_id))

    def resolve_current(self, project_id: str) -> tuple[ModelSpec, dict[str, Any]]:
        """Resolve current state, falling back read-only to the newest valid revision."""

        project_dir = self.project_dir(project_id)
        current_path = project_dir / "current.json"
        current_exists = current_path.exists()
        current_mtime_ns = current_path.stat().st_mtime_ns if current_exists else None
        pointer_error: Exception | None = None
        if current_exists:
            try:
                payload = json.loads(current_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("current.json must contain a JSON object")
                spec = ModelSpec.model_validate(payload["spec"])
                pointer_project_id = str(payload.get("project_id") or "")
                pointer_revision = int(payload["revision"])
                if pointer_project_id != project_id or spec.project_id != project_id:
                    raise ValueError("current.json project_id does not match its project directory")
                if pointer_revision != spec.revision:
                    raise ValueError("current.json revision does not match embedded spec revision")
                revision_path = self._revision_path(project_id, spec.revision)
                if revision_path.exists():
                    revision_spec = ModelSpec.model_validate(
                        json.loads(revision_path.read_text(encoding="utf-8"))
                    )
                    if revision_spec.project_id != project_id or revision_spec.revision != spec.revision:
                        raise ValueError(
                            "current.json revision identity does not match immutable revision file"
                        )
                    if revision_spec.model_dump(mode="json") != spec.model_dump(mode="json"):
                        raise ValueError(
                            "current.json embedded spec does not match immutable revision file"
                        )
                return spec, {
                    "status": "valid",
                    "read_source": "current_json",
                    "path": str(current_path.resolve()),
                    "exists": True,
                    "valid": True,
                    "revision": spec.revision,
                    "revision_path": str(revision_path.resolve()),
                    "revision_path_exists": revision_path.exists(),
                    "recovery_used": False,
                    "recovery_is_read_only": True,
                    "repair_required": False,
                    "safe_to_continue_read_only": True,
                    "next_successful_revision_write_repairs_pointer": False,
                    "current_mtime_ns": current_mtime_ns,
                    "invalid_revision_files": [],
                }
            except Exception as exc:
                pointer_error = exc

        fallback = self._latest_valid_revision(project_id)
        if fallback is None:
            if not current_exists:
                raise ValueError(f"项目不存在: {project_id}")
            assert pointer_error is not None
            raise ValueError(
                f"current.json is invalid and no valid revision can recover project {project_id}: "
                f"{_bounded_error(pointer_error)}"
            ) from pointer_error

        spec, revision_path, invalid_revisions = fallback
        revision_mtime_ns = revision_path.stat().st_mtime_ns
        status = "recovered_invalid_current_pointer" if current_exists else "recovered_missing_current_pointer"
        return spec, {
            "status": status,
            "read_source": "latest_valid_revision",
            "path": str(current_path.resolve()),
            "exists": current_exists,
            "valid": False,
            "revision": spec.revision,
            "revision_path": str(revision_path),
            "revision_path_exists": True,
            "recovery_used": True,
            "recovery_is_read_only": True,
            "repair_required": True,
            "safe_to_continue_read_only": True,
            "next_successful_revision_write_repairs_pointer": True,
            "current_mtime_ns": max(
                value for value in (current_mtime_ns, revision_mtime_ns) if value is not None
            ),
            "error_type": pointer_error.__class__.__name__ if pointer_error is not None else None,
            "error": _bounded_error(pointer_error) if pointer_error is not None else "current.json is missing",
            "invalid_revision_files": invalid_revisions,
        }

    def create_project(
        self,
        spec: ModelSpec,
        *,
        user_text: str | None = None,
        generated_script: str | None = None,
        diff: list[str] | None = None,
    ) -> RevisionInfo:
        """创建项目。

        参数:
            spec: 模型规格
            user_text: 用户文本
            generated_script: 生成的脚本
            diff: 差异列表

        返回:
            RevisionInfo 实例

        异常:
            ValueError: 如果项目已存在
        """
        project_dir = self.project_dir(spec.project_id)
        if (project_dir / "current.json").exists() or self._revision_candidates(spec.project_id):
            raise ValueError(f"项目已存在: {spec.project_id}")
        spec = spec.model_copy(update={"revision": 0})
        return self._write_revision(
            spec,
            user_text=user_text,
            action="create",
            generated_script=generated_script,
            diff=diff or [],
        )

    def load_current(self, project_id: str) -> ModelSpec:
        """加载当前项目。

        参数:
            project_id: 项目 ID

        返回:
            ModelSpec 实例

        异常:
            ValueError: 如果项目不存在
        """
        spec, _ = self.resolve_current(project_id)
        return spec

    def save_revision(
        self,
        project_id: str,
        spec: ModelSpec,
        *,
        user_text: str | None = None,
        action: str,
        generated_script: str | None = None,
        diff: list[str] | None = None,
    ) -> RevisionInfo:
        """保存修订版本。

        参数:
            project_id: 项目 ID
            spec: 模型规格
            user_text: 用户文本
            action: 操作
            generated_script: 生成的脚本
            diff: 差异列表

        返回:
            RevisionInfo 实例
        """
        current = self.load_current(project_id)
        revision = self.next_revision_number(project_id)
        spec = spec.model_copy(update={"project_id": project_id, "revision": revision})
        return self._write_revision(
            spec,
            user_text=user_text,
            action=action,
            generated_script=generated_script,
            diff=diff if diff is not None else diff_specs(current, spec),
        )

    def list_history(self, project_id: str) -> list[dict[str, Any]]:
        """列出项目历史。

        参数:
            project_id: 项目 ID

        返回:
            历史事件列表
        """
        history_path = self.project_dir(project_id) / "history.jsonl"
        if not history_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def list_projects(self) -> list[dict[str, Any]]:
        """Return structured projects that have a current revision."""

        projects: list[dict[str, Any]] = []
        for candidate in self.workspace_root.iterdir():
            if not candidate.is_dir():
                continue
            try:
                project_id = sanitize_project_id(candidate.name)
            except ValueError:
                continue
            current_path = candidate / "current.json"
            try:
                spec, current_resolution = self.resolve_current(project_id)
            except Exception:
                continue
            projects.append(
                {
                    "project_id": project_id,
                    "revision": spec.revision,
                    "project_dir": str(candidate.resolve()),
                    "current_path": str(current_path.resolve()),
                    "current_mtime_ns": current_resolution.get("current_mtime_ns", 0),
                    "current_pointer_status": current_resolution.get("status"),
                    "current_pointer_valid": current_resolution.get("valid"),
                    "current_pointer_recovery_used": current_resolution.get("recovery_used"),
                    "current_resolution": current_resolution,
                }
            )
        projects.sort(key=lambda item: (-int(item["current_mtime_ns"]), str(item["project_id"])))
        return projects

    def latest_project(self) -> dict[str, Any] | None:
        """Return the most recently updated structured project, if any."""

        projects = self.list_projects()
        return projects[0] if projects else None

    def get_revision(self, project_id: str, revision: int) -> ModelSpec:
        """获取修订版本。

        参数:
            project_id: 项目 ID
            revision: 修订版本号

        返回:
            ModelSpec 实例

        异常:
            ValueError: 如果修订版本不存在
        """
        path = self._revision_path(project_id, revision)
        if not path.exists():
            raise ValueError(f"修订版本不存在: r{revision:03d}")
        return ModelSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def rollback(
        self,
        project_id: str,
        target_revision: int,
        *,
        user_text: str | None = None,
        generated_script: str | None = None,
    ) -> RevisionInfo:
        """回滚到指定修订版本。

        参数:
            project_id: 项目 ID
            target_revision: 目标修订版本号
            user_text: 用户文本
            generated_script: 生成的脚本

        返回:
            RevisionInfo 实例
        """
        target = self.get_revision(project_id, target_revision)
        current = self.load_current(project_id)
        new_spec = target.model_copy(
            update={
                "revision": self.next_revision_number(project_id)
            }
        )
        return self._write_revision(
            new_spec,
            user_text=user_text,
            action=f"rollback:r{target_revision:03d}",
            generated_script=generated_script,
            diff=diff_specs(current, new_spec),
            extra={"target_revision": target_revision},
        )

    def outputs_dir(self, project_id: str, revision: int) -> Path:
        """获取输出目录。

        参数:
            project_id: 项目 ID
            revision: 修订版本号

        返回:
            输出目录路径
        """
        path = self.project_dir(project_id) / "outputs" / f"r{revision:03d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_result_metadata(self, project_id: str, revision: int, result: dict[str, Any]) -> Path:
        """写入结果元数据。

        参数:
            project_id: 项目 ID
            revision: 修订版本号
            result: 结果字典

        返回:
            结果元数据文件路径
        """
        path = self.outputs_dir(project_id, revision) / "result_metadata.json"
        atomic_write_text(path, json.dumps(result, indent=2, ensure_ascii=False))
        return path

    def _revision_path(self, project_id: str, revision: int) -> Path:
        """获取修订版本路径。"""
        return self.project_dir(project_id) / "revisions" / f"r{revision:03d}_model_spec.json"

    def _script_path(self, project_id: str, revision: int) -> Path:
        """获取脚本路径。"""
        return self.project_dir(project_id) / "scripts" / f"r{revision:03d}_build.pl"

    def _write_revision(
        self,
        spec: ModelSpec,
        *,
        user_text: str | None,
        action: str,
        generated_script: str | None,
        diff: list[str],
        extra: dict[str, Any] | None = None,
    ) -> RevisionInfo:
        """写入修订版本。"""
        project_dir = self.project_dir(spec.project_id)
        revisions_dir = project_dir / "revisions"
        scripts_dir = project_dir / "scripts"
        outputs_dir = project_dir / "outputs" / f"r{spec.revision:03d}"
        revisions_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir.mkdir(parents=True, exist_ok=True)

        spec_path = self._revision_path(spec.project_id, spec.revision)
        script_path = (
            self._script_path(spec.project_id, spec.revision)
            if generated_script is not None
            else None
        )
        if spec_path.exists():
            raise ValueError(
                f"修订版本已存在，拒绝覆盖仅追加历史: r{spec.revision:03d}"
            )
        if script_path is not None and script_path.exists():
            raise ValueError(
                f"修订脚本已存在，拒绝覆盖仅追加历史: r{spec.revision:03d}"
            )
        atomic_write_text(
            spec_path,
            json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
        if script_path is not None:
            assert generated_script is not None
            atomic_write_text(script_path, generated_script)

        event = make_history_event(
            project_id=spec.project_id,
            revision=spec.revision,
            action=action,
            user_text=user_text,
            diff=diff,
            extra=extra,
        )
        with (project_dir / "history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        current_path = project_dir / "current.json"
        atomic_write_text(
            current_path,
            json.dumps(
                {
                    "project_id": spec.project_id,
                    "revision": spec.revision,
                    "spec_path": str(spec_path),
                    "script_path": str(script_path) if script_path else None,
                    "spec": spec.model_dump(mode="json"),
                },
                indent=2,
                ensure_ascii=False,
            ),
        )

        return RevisionInfo(
            project_id=spec.project_id,
            revision=spec.revision,
            project_dir=project_dir,
            spec_path=spec_path,
            current_path=current_path,
            script_path=script_path,
        )
