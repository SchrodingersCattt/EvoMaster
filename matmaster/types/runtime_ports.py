"""Narrow runtime capability ports for context/spec boundaries.

RuntimePorts are not metadata containers. They carry callable capabilities
that core runtime components invoke directly.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict

from matmaster.types.events import BusEvent


class CompactionCheckpointPayload(TypedDict):
    durability: str
    strategy: str


class BusEventSink(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None] | None:
        ...


class PreCompactionBarrier(Protocol):
    def __call__(self) -> Awaitable[None] | None:
        ...


class CheckpointSink(Protocol):
    async def __call__(
        self,
        *,
        payload: CompactionCheckpointPayload,
        base_messages: list[dict[str, Any]],
    ) -> int | None:
        ...


class CheckpointSinkFactory(Protocol):
    def __call__(self, *, spawn_id: str | None = None) -> CheckpointSink:
        ...


class SessionEventHistoryPort(Protocol):
    def query_events(self) -> list[dict[str, Any]]:
        ...

    def all_events(self) -> list[dict[str, Any]]:
        ...

    def latest_checkpoint_covered_until_event_id(self) -> int | None:
        ...


@dataclass(frozen=True)
class EmptySessionEventHistory:
    def query_events(self) -> list[dict[str, Any]]:
        return []

    def all_events(self) -> list[dict[str, Any]]:
        return []

    def latest_checkpoint_covered_until_event_id(self) -> int | None:
        return None


@dataclass(frozen=True)
class PlaygroundCompactionPort:
    history: SessionEventHistoryPort | None = None
    checkpoint_sink_factory: CheckpointSinkFactory | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None


@dataclass(frozen=True)
class PlaygroundRuntimePorts:
    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(
        default_factory=PlaygroundCompactionPort
    )


@dataclass(frozen=True)
class KernelRuntimePorts:
    checkpoint_sink: CheckpointSink | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None
