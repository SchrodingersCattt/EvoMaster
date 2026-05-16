from __future__ import annotations

from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    UserInstructions,
)


def test_user_instructions_is_typed_data_carrier() -> None:
    instructions = UserInstructions(
        text="Use SI units.\n",
        hash="sha256:abc",
        truncated=True,
    )

    assert instructions.text == "Use SI units.\n"
    assert instructions.hash == "sha256:abc"
    assert instructions.truncated is True


def test_session_event_preserves_typed_envelope() -> None:
    event = SessionEvent(
        id=7,
        event_type="user_turn_context",
        source="MatMaster",
        content={"kind": "anchor", "images": ()},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    assert event.id == 7
    assert event.event_type == "user_turn_context"
    assert event.content["kind"] == "anchor"
    assert event.invocation_id == "inv-1"


def test_session_event_query_defaults_are_scope_safe() -> None:
    query = SessionEventQuery(session_id="sess-1", spawn_id=None)

    assert query.until_event_id is None
    assert query.event_types is None
    assert query.limit is None
    assert query.order == "asc"


def test_session_jobs_empty_returns_no_active_jobs() -> None:
    assert SessionJobs.empty().active_jobs == ()


def test_context_assembly_ports_optional_jobs_port_defaults_none() -> None:
    class EventsPort:
        async def load_events(self, query):
            return ()

    ports = ContextAssemblyPorts(session_events=EventsPort())

    assert ports.session_jobs is None
    assert not hasattr(ports, "extra")
    assert not hasattr(ports, "metadata")
    assert not hasattr(ports, "state")
    assert not hasattr(ports, "services")
