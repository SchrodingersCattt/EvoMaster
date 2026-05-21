"""Narrow runtime capability ports for context/spec boundaries.

RuntimePorts are not metadata containers. They carry callable capabilities
that core runtime components invoke directly.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, ConfigDict

from matmaster.context.ports import SessionEvent, SessionEventQuery
from matmaster.types.events import BusEvent
from matmaster.types.figures import FigureUploadConfig


class CompactionCheckpointPayload(TypedDict):
    durability: str
    strategy: str
    covered_until_event_id: NotRequired[int]
    schema_version: NotRequired[str]
    render_version: NotRequired[str]
    user_instructions_text: NotRequired[str]
    user_instructions_hash: NotRequired[str]


@runtime_checkable
class BusEventSink(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None] | None: ...


@runtime_checkable
class PreCompactionBarrier(Protocol):
    def __call__(self) -> Awaitable[None] | None: ...


@runtime_checkable
class CheckpointSink(Protocol):
    async def __call__(
        self,
        *,
        payload: CompactionCheckpointPayload,
        base_messages: list[dict[str, Any]],
    ) -> int | None: ...


@runtime_checkable
class CheckpointSinkFactory(Protocol):
    def __call__(self, *, spawn_id: str | None = None) -> CheckpointSink: ...


@runtime_checkable
class SessionEventHistoryPort(Protocol):
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]: ...

    def query_events(self) -> list[dict[str, Any]]: ...

    def all_events(self) -> list[dict[str, Any]]: ...

    def latest_checkpoint_covered_until_event_id(self) -> int | None: ...

    def latest_scope_event_id(self) -> int | None: ...


@dataclass(frozen=True)
class EmptySessionEventHistory:
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        return ()

    def query_events(self) -> list[dict[str, Any]]:
        return []

    def all_events(self) -> list[dict[str, Any]]:
        return []

    def latest_checkpoint_covered_until_event_id(self) -> int | None:
        return None

    def latest_scope_event_id(self) -> int | None:
        return None


@dataclass(frozen=True)
class PlaygroundCompactionPort:
    history: SessionEventHistoryPort | None = None
    checkpoint_sink_factory: CheckpointSinkFactory | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None


@dataclass(frozen=True)
class FigureUploadPort:
    config: FigureUploadConfig | None = None


class BohriumRuntimeSnapshot(BaseModel):
    """Narrow Bohrium runtime snapshot for path/runtime consumers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ssh_attached: bool = False
    node_id: int | None = None
    remote_project_root: str | None = None
    remote_workspace_root: str | None = None


@dataclass(frozen=True)
class BohriumRuntimePort:
    snapshot: BohriumRuntimeSnapshot | None = None


@dataclass(frozen=True)
class PlaygroundRuntimePorts:
    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(
        default_factory=PlaygroundCompactionPort
    )
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
    bohrium: BohriumRuntimePort = field(default_factory=BohriumRuntimePort)
    interrupt_checker: InterruptChecker | None = None


@runtime_checkable
class InterruptChecker(Protocol):
    """Check and wait for user interrupt at checkpoint boundaries."""

    def has_hint(self) -> bool: ...

    async def wait_for_confirm(self, timeout: float) -> bool: ...

    def cleanup(self) -> None: ...


@dataclass(frozen=True)
class KernelRuntimePorts:
    checkpoint_sink: CheckpointSink | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None
    interrupt_checker: InterruptChecker | None = None
