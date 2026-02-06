"""运行事件定义与发射（解耦 UI 与核心逻辑）

前端通过 event_sink 接收结构化事件，用于展示当前阶段、成功/失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# 阶段标识：与前端展示模块一一对应
PHASE_CONFIG = "config"
PHASE_SESSION = "session"
PHASE_TOOLS = "tools"
PHASE_AGENT = "agent"
PHASE_EXP_START = "exp_start"
PHASE_EXP_STEP = "exp_step"
PHASE_EXP_END = "exp_end"
PHASE_ERROR = "error"

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


@dataclass
class RunEvent:
    """单条运行事件（供 UI 展示）"""
    phase: str
    message: str
    status: str  # running | success | failed
    detail: Any = None  # 可选详情，如 result、error 信息

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "message": self.message,
            "status": self.status,
            "detail": self.detail,
        }


def emit(sink: Any, event: RunEvent) -> None:
    """若 sink 存在则推送事件（不依赖 UI 库，解耦）"""
    if sink is None:
        return
    try:
        if hasattr(sink, "put"):
            sink.put(event.to_dict())
        elif callable(sink):
            sink(event.to_dict())
        else:
            sink.append(event.to_dict())
    except Exception:
        pass
