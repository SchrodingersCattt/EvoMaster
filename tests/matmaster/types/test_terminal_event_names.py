from __future__ import annotations

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
