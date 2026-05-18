"""Tests for RuntimePorts narrow capability contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass

import pytest
from pydantic import TypeAdapter, ValidationError

from matmaster.types.events import ResponseEvent
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.runtime_ports import (
    BohriumRuntimePort,
    BohriumRuntimeSnapshot,
    BusEventSink,
    CheckpointSink,
    CheckpointSinkFactory,
    EmptySessionEventHistory,
    FigureUploadPort,
    KernelRuntimePorts,
    PlaygroundCompactionPort,
    PlaygroundRuntimePorts,
)


def test_empty_session_event_history_is_explicit_empty_reader() -> None:
    history = EmptySessionEventHistory()

    assert history.query_events() == []
    assert history.all_events() == []
    assert history.latest_checkpoint_covered_until_event_id() is None


def test_empty_session_event_history_has_no_implicit_scope_boundary() -> None:
    history = EmptySessionEventHistory()

    assert history.latest_scope_event_id() is None


@pytest.mark.asyncio
async def test_empty_session_event_history_load_events_returns_empty() -> None:
    from matmaster.context.ports import SessionEventQuery

    history = EmptySessionEventHistory()

    assert (
        await history.load_events(SessionEventQuery(session_id="sess-1", spawn_id=None))
    ) == ()


def test_playground_runtime_ports_defaults_are_narrow() -> None:
    ports = PlaygroundRuntimePorts()

    assert ports.child_event_forward_sink is None
    assert isinstance(ports.compaction, PlaygroundCompactionPort)
    assert isinstance(ports.figure_upload, FigureUploadPort)
    assert isinstance(ports.bohrium, BohriumRuntimePort)
    assert ports.figure_upload.config is None
    assert ports.bohrium.snapshot is None
    assert ports.compaction.history is None
    assert ports.compaction.checkpoint_sink_factory is None
    assert ports.compaction.pre_compaction_barrier is None
    assert not hasattr(ports, "extra")
    assert not hasattr(ports, "metadata")
    assert not hasattr(ports, "state")
    assert not hasattr(ports, "services")


def test_figure_upload_port_is_frozen_dataclass() -> None:
    cfg = FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="figures/sess-1/task-1",
        upload_bytes=lambda data, name: f"https://oss.example/{name}",
    )
    port = FigureUploadPort(config=cfg)

    assert is_dataclass(port)
    assert port.config is cfg
    with pytest.raises(FrozenInstanceError):
        port.config = None


def test_bohrium_runtime_snapshot_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BohriumRuntimeSnapshot(unknown_field="x")


def test_kernel_runtime_ports_defaults_are_narrow() -> None:
    ports = KernelRuntimePorts()

    assert ports.checkpoint_sink is None
    assert ports.pre_compaction_barrier is None
    assert not hasattr(ports, "extra")
    assert not hasattr(ports, "metadata")
    assert not hasattr(ports, "state")
    assert not hasattr(ports, "services")


@pytest.mark.asyncio
async def test_bus_event_sink_protocol_accepts_bus_event() -> None:
    seen = []

    async def sink(event) -> None:
        seen.append(event)

    typed_sink: BusEventSink = sink
    event = ResponseEvent(source="agent", content="child")

    await typed_sink(event)

    assert seen == [event]
    assert TypeAdapter(ResponseEvent).validate_python(event.model_dump())


@pytest.mark.asyncio
async def test_checkpoint_sink_protocol_signature() -> None:
    calls = []

    async def sink(*, payload, base_messages):
        calls.append((payload, base_messages))
        return 42

    typed_sink: CheckpointSink = sink
    covered = await typed_sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "covered_until_event_id": 41,
        },
        base_messages=[{"role": "user", "content": "compact"}],
    )

    assert covered == 42
    assert calls == [
        (
            {
                "durability": "durable",
                "strategy": "summary",
                "covered_until_event_id": 41,
            },
            [{"role": "user", "content": "compact"}],
        )
    ]


def test_checkpoint_sink_factory_protocol_signature() -> None:
    async def sink(*, payload, base_messages):
        return 7

    def factory(*, spawn_id: str | None = None):
        assert spawn_id == "child-1"
        return sink

    typed_factory: CheckpointSinkFactory = factory

    assert typed_factory(spawn_id="child-1") is sink
