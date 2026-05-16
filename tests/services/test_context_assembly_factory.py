from __future__ import annotations

import pytest

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import coerce_session_events
from matmaster.context.session import SessionContextBuilder
from src.services.context_assembly_factory import build_session_context_factory


@pytest.fixture
def sample_events() -> tuple[SessionEvent, ...]:
    return coerce_session_events(
        [
            {
                "id": 1,
                "type": "skill_hit",
                "content": {"skill_name": "pxrd"},
                "source": "System",
            },
            {
                "id": 2,
                "type": "query",
                "source": "User",
                "content": {"content": "hello", "files": ["/tmp/a.txt"]},
                "invocation_id": "inv-1",
            },
        ]
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


@pytest.mark.asyncio
async def test_runtime_history_events_port_filters_existing_history_rows() -> None:
    from matmaster.context.ports import SessionEventQuery
    from src.services.context_assembly_factory import RuntimeHistorySessionEventsPort

    class History:
        def query_context_events(self, **kwargs):
            self.kwargs = kwargs
            return [
                {
                    "id": 1,
                    "type": "query",
                    "source": "User",
                    "content": {"content": "old"},
                },
            ]

        def all_events(self):
            raise AssertionError("runtime context assembly must not use all_events()")

        def query_events(self):
            return []

        def latest_checkpoint_covered_until_event_id(self):
            return None

        def latest_scope_event_id(self):
            return 3

    history = History()
    events = await RuntimeHistorySessionEventsPort(history).load_events(
        SessionEventQuery(
            session_id="sess-1",
            spawn_id=None,
            until_event_id=2,
            event_types=("query",),
            order="asc",
        )
    )

    assert [event.id for event in events] == [1]
    assert events[0].content == {"content": "old"}
    assert history.kwargs == {
        "spawn_id": None,
        "until_event_id": 2,
        "event_types": ("query",),
        "limit": None,
        "order": "asc",
    }
