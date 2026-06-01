"""Internal kernel item dataclasses used by AgentKernel."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from matmaster.core.message_pipeline import IncrementalMessagePipeline
from matmaster.types.events import FinishDetail
from matmaster.types.messages import LLMResponse


@dataclass
class _TerminalItem:
    reason: str
    final_content: str | None = None
    num_turns: int = 0
    usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    messages: list[Any] = dc_field(default_factory=list)
    finish_detail: FinishDetail | None = None
    model: str | None = None
    model_profile: str | None = None
    model_route: str | None = None


@dataclass
class _KernelItem:
    event: Any = None
    llm_response: LLMResponse | None = None
    terminal: _TerminalItem | None = None


@dataclass
class _KernelState:
    messages: list[Any]
    turn: int = 0
    total_usage: dict[str, int] = dc_field(default_factory=dict)
    turn_usage: dict[str, int] = dc_field(default_factory=dict)
    usage_vendor_by_turn: list[dict[str, Any]] = dc_field(default_factory=list)
    llm_model: str | None = None
    llm_model_profile: str | None = None
    llm_model_route: str | None = None
    cached_tool_definitions: list[dict[str, Any]] | None = None
    last_catalog_version: int = -1
    pipeline: IncrementalMessagePipeline = dc_field(
        default_factory=IncrementalMessagePipeline
    )
    last_emitted_content: str | None = None


class _KernelStopRequested(Exception):
    pass
