from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    UserInstructionsInfo,
    build_user_turn_context_payload,
    decide_user_turn_context_kind,
    hash_user_instructions,
    latest_anchor_user_instructions_hash,
    load_user_instructions_from_session,
    render_runtime_task_for_user_turn_context,
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


def test_latest_anchor_hash_prefers_latest_user_turn_anchor() -> None:
    events = [
        {
            "type": "user_turn_context",
            "content": {"kind": "continuation", "user_instructions_hash": None},
        },
        {
            "type": "user_turn_context",
            "content": {"kind": "anchor", "user_instructions_hash": "sha256:new"},
        },
        {
            "type": "history_checkpoint",
            "content": {"user_instructions_hash": "sha256:old"},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) == "sha256:new"


def test_latest_anchor_hash_handles_chronological_events_with_ids() -> None:
    events = [
        {
            "id": 10,
            "type": "user_turn_context",
            "content": {"kind": "anchor", "user_instructions_hash": "sha256:old"},
        },
        {
            "id": 11,
            "type": "user_turn_context",
            "content": {"kind": "continuation", "user_instructions_hash": None},
        },
        {
            "id": 12,
            "type": "user_turn_context",
            "content": {"kind": "anchor", "user_instructions_hash": "sha256:new"},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) == "sha256:new"


def test_latest_anchor_hash_uses_history_checkpoint_when_no_anchor_event() -> None:
    events = [
        {"type": "response", "content": "ignored"},
        {
            "type": "history_checkpoint",
            "content": {"user_instructions_hash": "sha256:checkpoint"},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) == "sha256:checkpoint"


def test_latest_anchor_hash_does_not_cross_chronological_checkpoint_with_ids() -> None:
    events = [
        {
            "id": 20,
            "type": "user_turn_context",
            "content": {"kind": "anchor", "user_instructions_hash": "sha256:old"},
        },
        {
            "id": 21,
            "type": "history_checkpoint",
            "content": {"user_instructions_hash": "sha256:checkpoint"},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) == "sha256:checkpoint"


def test_latest_anchor_hash_returns_none_for_chronological_checkpoint_without_hash() -> (
    None
):
    events = [
        {
            "id": 30,
            "type": "user_turn_context",
            "content": {"kind": "anchor", "user_instructions_hash": "sha256:old"},
        },
        {
            "id": 31,
            "type": "history_checkpoint",
            "content": {"covered_until_event_id": 30, "base_messages": []},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) is None


def test_latest_anchor_hash_returns_none_when_checkpoint_lacks_hash() -> None:
    events = [
        {
            "type": "history_checkpoint",
            "content": {"covered_until_event_id": 100, "base_messages": []},
        },
        {
            "type": "user_turn_context",
            "content": {
                "kind": "anchor",
                "user_instructions_hash": "sha256:same-as-current",
            },
        },
    ]

    assert latest_anchor_user_instructions_hash(events) is None


def test_latest_anchor_hash_returns_none_when_only_continuation_present() -> None:
    events = [
        {
            "type": "user_turn_context",
            "content": {"kind": "continuation", "user_instructions_hash": None},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) is None


def test_decide_kind_anchor_for_missing_or_changed_hash() -> None:
    assert decide_user_turn_context_kind("sha256:a", None) == "anchor"
    assert decide_user_turn_context_kind("sha256:a", "sha256:b") == "anchor"
    assert decide_user_turn_context_kind("sha256:a", "sha256:a") == "continuation"


def test_render_runtime_task_adds_instructions_only_for_anchor() -> None:
    info = UserInstructionsInfo(
        text="Prefer concise answers.",
        hash=hash_user_instructions("Prefer concise answers."),
    )

    anchor = render_runtime_task_for_user_turn_context(
        user_prompt="Explain FeO.",
        user_instructions=info,
        kind="anchor",
    )
    continuation = render_runtime_task_for_user_turn_context(
        user_prompt="Explain FeO.",
        user_instructions=info,
        kind="continuation",
    )

    assert anchor.startswith(
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
    )
    assert "Prefer concise answers." in anchor
    assert anchor.endswith("Explain FeO.")
    assert continuation == "Explain FeO."


def test_build_payload_freezes_user_message_and_anchor_hash() -> None:
    info = UserInstructionsInfo(text="Use SI units.", hash="sha256:abc")

    payload = build_user_turn_context_payload(
        kind="anchor",
        rendered_message_content="provider-facing content",
        images=[{"url": "https://oss.example.com/chat/current.png", "detail": "auto"}],
        user_instructions=info,
        transform="raw",
    )

    assert payload["schema_version"] == "user_turn_context.v1"
    assert payload["kind"] == "anchor"
    assert payload["message"]["role"] == "user"
    assert payload["message"]["content"] == "provider-facing content"
    assert payload["message"]["images"][0]["url"].endswith("current.png")
    assert payload["user_instructions_hash"] == "sha256:abc"
    assert payload["transform"] == "raw"
    assert payload["render_version"] == "user_context_render.v1"


def test_build_payload_omits_hash_for_continuation() -> None:
    info = UserInstructionsInfo(text="Use SI units.", hash="sha256:abc")

    payload = build_user_turn_context_payload(
        kind="continuation",
        rendered_message_content="current only",
        images=[],
        user_instructions=info,
        transform="raw",
    )

    assert payload["kind"] == "continuation"
    assert payload["user_instructions_hash"] is None


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
