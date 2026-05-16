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
