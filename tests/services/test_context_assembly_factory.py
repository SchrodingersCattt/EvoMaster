from __future__ import annotations

import pytest

from matmaster.context.ports import SessionEvent
from matmaster.context.session import SessionContextBuilder
from matmaster.core.runtime_context_assembly import build_session_context_factory


@pytest.fixture
def sample_events() -> tuple[SessionEvent, ...]:
    return (
        SessionEvent(
            id=1,
            event_type="skill_hit",
            content={"skill_name": "pxrd"},
            source="System",
        ),
        SessionEvent(
            id=2,
            event_type="query",
            source="User",
            content={"content": "hello", "files": ("/tmp/a.txt",)},
            invocation_id="inv-1",
        ),
    )


def test_factory_returns_session_context_builder_with_injected_dependencies(
    sample_events: tuple[SessionEvent, ...],
) -> None:
    factory = build_session_context_factory(
        skill_registry=object(),
        legal_mcp_servers={"bohrium"},
        schemas_by_server={"bohrium": [{"name": "submit_job"}]},
    )

    builder = factory(sample_events)

    assert isinstance(builder, SessionContextBuilder)
    assert builder.events is sample_events
    assert builder.legal_mcp_servers == {"bohrium"}
    assert builder.schemas_by_server == {"bohrium": [{"name": "submit_job"}]}


def test_factory_passes_none_legal_servers_through(
    sample_events: tuple[SessionEvent, ...],
) -> None:
    factory = build_session_context_factory(
        skill_registry=object(),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    builder = factory(sample_events)

    assert builder.legal_mcp_servers is None
    assert builder.schemas_by_server is None


def test_factory_accepts_empty_events_and_returns_buildable_sections() -> None:
    factory = build_session_context_factory(
        skill_registry=None,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    builder = factory(())

    sections = builder.build_sections(until_event_id=None, include_attachments=True)
    assert sections == ()


def test_factory_rejects_non_tuple_events_via_session_builder_invariant() -> None:
    factory = build_session_context_factory(
        skill_registry=None,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    with pytest.raises(TypeError, match="must be a tuple of SessionEvent"):
        factory([])  # type: ignore[arg-type]


def test_build_context_assembler_wires_ports_and_render_options() -> None:
    from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
    from src.services.context_assembly_factory import build_context_assembler

    class EventsTable:
        def query_context_events(self, **kwargs):
            return []

    assembler, ports = build_context_assembler(
        events_table=EventsTable(),
        skill_registry=None,
        legal_mcp_servers=None,
        schemas_by_server=None,
        split_turn_attachments=True,
    )

    assert isinstance(assembler, ContextAssembler)
    assert ports.session_jobs is not None
    assert assembler._render_options == ContextRenderOptions(
        split_turn_attachments=True
    )
