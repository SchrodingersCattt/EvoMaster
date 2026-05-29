from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    hash_user_instructions,
    load_user_instructions_from_session,
    make_user_instructions_info,
    write_user_turn_context_event,
)


def test_hash_user_instructions_uses_sha256_prefix() -> None:
    assert hash_user_instructions("").startswith("sha256:")
    assert hash_user_instructions("abc") == hash_user_instructions("abc")
    assert hash_user_instructions("abc") != hash_user_instructions("abcd")


def test_hash_user_instructions_does_not_strip_whitespace() -> None:
    assert hash_user_instructions("Use SI units.") != hash_user_instructions(
        "Use SI units.\n"
    )
    assert hash_user_instructions(" abc ") != hash_user_instructions("abc")


def test_make_user_instructions_info_uses_empty_string_for_none() -> None:
    info = make_user_instructions_info(None)

    assert info.text == ""
    assert info.hash == hash_user_instructions("")
    assert info.truncated is False


def test_make_user_instructions_info_preserves_truncated_flag() -> None:
    info = make_user_instructions_info("abc", truncated=True)

    assert info.text == "abc"
    assert info.hash == hash_user_instructions("abc")
    assert info.truncated is True


def test_load_user_instructions_missing_file_returns_empty_hash() -> None:
    session = Mock()
    session.read_file.side_effect = FileNotFoundError("missing")

    info = load_user_instructions_from_session(session)

    assert info.text == ""
    assert info.hash == hash_user_instructions("")
    assert info.truncated is False


def test_load_user_instructions_truncates_by_utf8_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = Mock()
    session.read_file.return_value = "a" * (USER_INSTRUCTIONS_MAX_BYTES + 10)

    with caplog.at_level(logging.WARNING):
        info = load_user_instructions_from_session(session)

    assert len(info.text.encode("utf-8")) == USER_INSTRUCTIONS_MAX_BYTES
    assert info.truncated is True
    assert info.hash == hash_user_instructions(info.text)
    assert "AGENT.md exceeds" in caplog.text


def test_load_user_instructions_preserves_trailing_newline() -> None:
    session = Mock()
    session.read_file.return_value = "Use SI units.\n"

    info = load_user_instructions_from_session(session)

    assert info.text == "Use SI units.\n"
    assert info.hash == hash_user_instructions("Use SI units.\n")


@pytest.mark.asyncio
async def test_write_user_turn_context_raises_when_invocation_id_missing() -> None:
    events_table = Mock()
    events_table.add_event = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(return_value=None)

    with pytest.raises(RuntimeError, match="requires invocation_id"):
        await write_user_turn_context_event(
            events_table=events_table,
            session_id="s1",
            task_id="t1",
            invocation_id=None,
            spawn_id=None,
            payload={"schema_version": "user_turn_context.v1"},
        )

    events_table.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_write_user_turn_context_idempotent_skip_on_duplicate() -> None:
    payload = {"schema_version": "user_turn_context.v1"}
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(
        return_value={"id": 42, "type": "user_turn_context", "content": payload}
    )
    events_table.add_event = Mock()

    status = await write_user_turn_context_event(
        events_table=events_table,
        session_id="s1",
        task_id="t1",
        invocation_id="inv-1",
        spawn_id=None,
        payload=payload,
    )

    events_table.query_user_turn_context_by_invocation.assert_called_once_with(
        "s1", "inv-1", None
    )
    assert status == "duplicate"
    events_table.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_write_user_turn_context_raises_when_duplicate_payload_differs() -> None:
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(
        return_value={
            "id": 42,
            "type": "user_turn_context",
            "content": {"schema_version": "user_turn_context.v1", "message": "old"},
        }
    )
    events_table.add_event = Mock()

    with pytest.raises(RuntimeError, match="payload differs"):
        await write_user_turn_context_event(
            events_table=events_table,
            session_id="s1",
            task_id="t1",
            invocation_id="inv-1",
            spawn_id=None,
            payload={"schema_version": "user_turn_context.v1", "message": "new"},
        )

    events_table.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_write_user_turn_context_writes_when_no_duplicate() -> None:
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(return_value=None)
    events_table.add_event = Mock(return_value=True)

    status = await write_user_turn_context_event(
        events_table=events_table,
        session_id="s1",
        task_id="t1",
        invocation_id="inv-1",
        spawn_id=None,
        payload={"schema_version": "user_turn_context.v1"},
    )

    assert status == "written"
    events_table.add_event.assert_called_once()


@pytest.mark.asyncio
async def test_write_user_turn_context_raises_when_add_event_returns_false() -> None:
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(return_value=None)
    events_table.add_event = Mock(return_value=False)

    with pytest.raises(RuntimeError, match="returned false"):
        await write_user_turn_context_event(
            events_table=events_table,
            session_id="s1",
            task_id="t1",
            invocation_id="inv-1",
            spawn_id=None,
            payload={"schema_version": "user_turn_context.v1"},
        )
