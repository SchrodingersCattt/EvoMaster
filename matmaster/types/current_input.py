from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

# Phase 2C shim: `TurnInput` 的真实定义已迁到
# `matmaster.context.sources.turn_input`。本模块保留 re-export 是为了在
# Phase 4 删除旧 import 路径之前，让生产 / 测试代码渐进切换。
# `CurrentInputContext` / `build_current_instruction_block` 仍供
# core/agent.py 与旧压缩入口的兼容路径使用。
from matmaster.context.sources.turn_input import TurnInput  # noqa: F401


def _clean_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        text for value in values if isinstance(value, str) and (text := value.strip())
    )


def _display_name(value: str) -> str:
    parsed = urlparse(value)
    return PurePosixPath(parsed.path or value).name or value


@dataclass(frozen=True)
class CurrentInputContext:
    user_text: str = ""
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()
    pre_query_scope_event_id: int | None = None

    @classmethod
    def from_values(
        cls,
        *,
        user_text: str | None = None,
        files: Any = None,
        images: Any = None,
        workspace_paths: Any = None,
        pre_query_scope_event_id: int | None = None,
    ) -> CurrentInputContext:
        return cls(
            user_text=(user_text or "").strip(),
            files=_clean_tuple(files),
            images=_clean_tuple(images),
            workspace_paths=_clean_tuple(workspace_paths),
            pre_query_scope_event_id=pre_query_scope_event_id,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> CurrentInputContext | None:
        if not isinstance(payload, dict):
            return None
        raw = payload.get("pre_query_scope_event_id")
        try:
            boundary = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            boundary = None
        return cls.from_values(
            user_text=payload.get("user_text"),
            files=payload.get("files"),
            images=payload.get("images"),
            workspace_paths=payload.get("workspace_paths"),
            pre_query_scope_event_id=boundary,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "files": list(self.files),
            "images": list(self.images),
            "workspace_paths": list(self.workspace_paths),
            "pre_query_scope_event_id": self.pre_query_scope_event_id,
        }

    def has_effective_input(self) -> bool:
        return bool(
            self.user_text.strip() or self.files or self.images or self.workspace_paths
        )


def build_current_instruction_block(context: CurrentInputContext) -> str:
    if not context.has_effective_input():
        return ""
    lines: list[str] = []
    if context.user_text.strip():
        lines.append(context.user_text.strip())
    attachment_lines = [
        *(f"file_{i} {_display_name(v)} {v}" for i, v in enumerate(context.files, 1)),
        *(f"workspace_{i} {v}" for i, v in enumerate(context.workspace_paths, 1)),
        *(f"image_{i} {_display_name(v)} {v}" for i, v in enumerate(context.images, 1)),
    ]
    if attachment_lines:
        if lines:
            lines.append("")
        lines.append("[Current attachments]")
        lines.extend(attachment_lines)
    return (
        "<current_instruction>\n"
        + "\n".join(lines).strip()
        + "\n</current_instruction>"
    )
