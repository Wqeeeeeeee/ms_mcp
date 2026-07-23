"""历史事件助手。

此模块提供了创建历史事件的函数。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def make_history_event(
    *,
    project_id: str,
    revision: int,
    action: str,
    user_text: str | None,
    diff: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建历史事件。

    参数:
        project_id: 项目 ID
        revision: 修订版本号
        action: 操作
        user_text: 用户文本
        diff: 差异列表
        extra: 额外数据

    返回:
        历史事件字典
    """
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "revision": revision,
        "action": action,
        "user_text": user_text,
        "diff": diff or [],
        **(extra or {}),
    }
