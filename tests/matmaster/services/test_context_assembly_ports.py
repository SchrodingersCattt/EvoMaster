from __future__ import annotations

import logging

import pytest

from matmaster.context.ports import (
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)
from src.services.context_assembly_ports import (
    AppSessionEventsPort,
    AppSessionJobsPort,
    AppUserInstructionsPort,
    _freeze_json_object,
)
from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    hash_user_instructions,
)


@pytest.mark.asyncio
async def test_app_user_instructions_port_missing_file_returns_empty_bundle(
    tmp_path,
) -> None:
    result = await AppUserInstructionsPort().load_user_instructions(tmp_path)

    assert result == UserInstructions(
        text="",
        hash=hash_user_instructions(""),
        truncated=False,
    )


@pytest.mark.asyncio
async def test_app_user_instructions_port_preserves_raw_trailing_newline(
    tmp_path,
) -> None:
    agent_file = tmp_path / ".matmaster" / "AGENT.md"
    agent_file.parent.mkdir()
    agent_file.write_text("Use SI units.\n", encoding="utf-8")

    result = await AppUserInstructionsPort().load_user_instructions(tmp_path)

    assert result.text == "Use SI units.\n"
    assert result.hash == hash_user_instructions("Use SI units.\n")


@pytest.mark.asyncio
async def test_app_user_instructions_port_truncates_by_utf8_bytes(
    tmp_path,
    caplog,
) -> None:
    agent_file = tmp_path / ".matmaster" / "AGENT.md"
    agent_file.parent.mkdir()
    agent_file.write_text("a" * (USER_INSTRUCTIONS_MAX_BYTES + 10), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = await AppUserInstructionsPort().load_user_instructions(tmp_path)

    assert len(result.text.encode("utf-8")) == USER_INSTRUCTIONS_MAX_BYTES
    assert result.truncated is True
    assert result.hash == hash_user_instructions(result.text)
    assert "AGENT.md exceeds" in caplog.text


class FakeEventsTable:
    def __init__(self, rows=None) -> None:
        self.calls = []
        self.rows = rows or [
            {
                "id": 3,
                "source": "MatMaster",
                "type": "user_turn_context",
                "content": {
                    "kind": "anchor",
                    "images": ["https://example.com/a.png"],
                },
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
                "spawn_id": None,
            }
        ]

    def query_context_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


@pytest.mark.asyncio
async def test_app_session_events_port_maps_rows_to_typed_events() -> None:
    table = FakeEventsTable()
    port = AppSessionEventsPort(table)

    events = await port.load_events(
        SessionEventQuery(
            session_id="sess-1",
            spawn_id=None,
            until_event_id=9,
            event_types=("user_turn_context", "history_checkpoint"),
            limit=50,
            order="desc",
        )
    )

    assert table.calls == [
        {
            "session_id": "sess-1",
            "spawn_id": None,
            "until_event_id": 9,
            "event_types": ("user_turn_context", "history_checkpoint"),
            "limit": 50,
            "order": "desc",
        }
    ]
    assert events[0].id == 3
    assert events[0].event_type == "user_turn_context"
    assert events[0].content["images"] == ("https://example.com/a.png",)
    assert events[0].invocation_id == "inv-1"


@pytest.mark.asyncio
async def test_app_session_events_port_preserves_raw_user_query_payload() -> None:
    table = FakeEventsTable(
        rows=[
            {
                "id": 4,
                "source": "User",
                "type": "query",
                "content": {
                    "content": "Explain FeO.",
                    "files": ["https://oss.example.com/input.cif"],
                    "images": ["https://oss.example.com/image.png"],
                    "workspace_paths": ["/share/result.xyz"],
                },
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
                "spawn_id": None,
            }
        ]
    )

    events = await AppSessionEventsPort(table).load_events(
        SessionEventQuery(session_id="sess-1", spawn_id=None)
    )

    assert events[0].event_type == "query"
    assert events[0].source == "User"
    assert events[0].content["content"] == "Explain FeO."
    assert events[0].content["files"] == ("https://oss.example.com/input.cif",)
    assert events[0].content["images"] == ("https://oss.example.com/image.png",)
    assert events[0].content["workspace_paths"] == ("/share/result.xyz",)


@pytest.mark.asyncio
async def test_app_session_events_port_preserves_falsy_raw_content() -> None:
    table = FakeEventsTable(
        rows=[
            {
                "id": 5,
                "source": "System",
                "type": "raw_string",
                "content": "",
                "session_id": "sess-1",
                "task_id": None,
                "invocation_id": None,
                "spawn_id": None,
            }
        ]
    )

    events = await AppSessionEventsPort(table).load_events(
        SessionEventQuery(session_id="sess-1", spawn_id=None)
    )

    assert events[0].content == {"value": ""}


def test_freeze_json_object_rejects_non_json_schema_drift() -> None:
    with pytest.raises(TypeError, match="Unsupported JSON value type"):
        _freeze_json_object({"bad": object()})


@pytest.mark.asyncio
async def test_app_session_jobs_port_is_empty_placeholder() -> None:
    result = await AppSessionJobsPort().load_session_jobs(
        query=SessionJobsQuery(session_id="sess-1")
    )

    assert result == SessionJobs.empty()
