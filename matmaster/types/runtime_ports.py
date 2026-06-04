"""Narrow runtime capability ports for context/spec boundaries.

RuntimePorts are not metadata containers. They carry callable capabilities
that core runtime components invoke directly.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.context.ports import (
    BohriumJobLedgerPort,
    SessionEvent,
    SessionEventQuery,
    SessionJobsPort,
)
from matmaster.types.events import BusEvent
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.messages import Message

__all__ = [
    "AgentRunPorts",
    "BohriumRuntimePort",
    "BohriumRuntimeSnapshot",
    "BusEventSink",
    "CheckpointSink",
    "CheckpointSinkFactory",
    "CompactionCheckpointPayload",
    "EmptySessionEventHistory",
    "FigureUploadPort",
    "InterruptChecker",
    "KernelRuntimePorts",
    "PlaygroundCompactionPort",
    "PreCompactionBarrier",
    "SessionEventHistoryPort",
    "UserTurnContextWriteRequest",
    "UserTurnContextWriter",
]


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


@dataclass(frozen=True)
class BohriumRuntimePort:
    snapshot: BohriumRuntimeSnapshot | None = None


@runtime_checkable
class InterruptChecker(Protocol):
    """Check and wait for user interrupt at checkpoint boundaries."""

    def has_hint(self) -> bool: ...

    async def wait_for_confirm(self, timeout: float) -> bool: ...

    def cleanup(self) -> None: ...


@dataclass(frozen=True)
class UserTurnContextWriteRequest:
    session_id: str
    task_id: str | None
    invocation_id: str | None
    spawn_id: str | None
    kind: Literal["anchor", "continuation"]
    message: Message
    user_instructions_hash: str | None
    transform: str
    render_version: str
    schema_version: str


@runtime_checkable
class UserTurnContextWriter(Protocol):
    async def __call__(self, request: UserTurnContextWriteRequest) -> None: ...


@dataclass(frozen=True)
class AgentRunPorts:
    """Narrow runtime capability ports carried by AgentRunRequest.

    The service layer injects these per run. It intentionally does *not* carry
    the Bohrium snapshot: that is physical execution info and lives on
    ``ExecutionEnvironment.bohrium`` instead. Stays a narrow capability
    contract -- only callable / sink / barrier / capability, never a metadata
    or ``dict[str, Any]`` bag.
    """

    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(
        default_factory=PlaygroundCompactionPort
    )
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
    interrupt_checker: InterruptChecker | None = None
    user_turn_context_writer: UserTurnContextWriter | None = None
    bohrium_job_ledger: BohriumJobLedgerPort | None = None
    session_jobs: SessionJobsPort | None = None


@dataclass(frozen=True)
class KernelRuntimePorts:
    checkpoint_sink: CheckpointSink | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None
    interrupt_checker: InterruptChecker | None = None
