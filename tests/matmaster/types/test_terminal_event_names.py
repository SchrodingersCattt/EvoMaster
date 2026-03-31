from __future__ import annotations

from pydantic import TypeAdapter


def test_terminal_events_use_distinct_result_and_stream_names() -> None:
    """Terminal event names should distinguish business result from stream closure."""
    from matmaster.types import events as events_module

    run_result_cls = getattr(events_module, "RunResultEvent", None)
    stream_closed_cls = getattr(events_module, "StreamClosedEvent", None)

    assert run_result_cls is not None
    assert stream_closed_cls is not None

    run_result = run_result_cls(
        source="MatMaster", status="completed", reason="natural"
    )
    stream_closed = stream_closed_cls(source="System")

    assert run_result.type == "run_result"
    assert stream_closed.type == "stream_closed"


def test_terminal_events_accept_legacy_finish_and_end_payloads() -> None:
    """Old persisted finish/end payloads should still deserialize during migration."""
    from matmaster.types import events as events_module

    bus_event_adapter = TypeAdapter(events_module.BusEvent)

    run_result = bus_event_adapter.validate_python(
        {
            "type": "finish",
            "source": "MatMaster",
            "status": "completed",
            "reason": "natural",
        }
    )
    stream_closed = bus_event_adapter.validate_python(
        {
            "type": "end",
            "source": "System",
        }
    )

    assert type(run_result).__name__ == "RunResultEvent"
    assert type(stream_closed).__name__ == "StreamClosedEvent"
