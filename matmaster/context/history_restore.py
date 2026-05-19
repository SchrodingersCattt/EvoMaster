from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from matmaster.types.messages import Message, UserMessage

logger = logging.getLogger(__name__)

_V1_SCHEMA = "history_checkpoint.v1"

GetLatestCheckpoint = Callable[[str, str | None], dict[str, Any] | None]
GetEventsAfter = Callable[[str, int | None, str | None], list[dict[str, Any]]]
HasUserTurnContext = Callable[[str, str | None], bool]
LegacyRestore = Callable[[str, str | None], list[Message]]
DeserializeBaseMessages = Callable[[list[dict[str, Any]]], list[Message]]
EventsToMessages = Callable[[list[dict[str, Any]]], list[Message]]
NormalizeToolResultEvent = Callable[[dict[str, Any]], dict[str, Any]]
ValidateHistory = Callable[[list[Message]], None]


class ModelHistoryRestorer:
    """Rebuild backend-visible LLM history from session events."""

    def __init__(
        self,
        *,
        get_latest_checkpoint: GetLatestCheckpoint,
        get_events_after: GetEventsAfter,
        has_user_turn_context: HasUserTurnContext,
        legacy_restore: LegacyRestore,
        deserialize_base_messages: DeserializeBaseMessages,
        events_to_messages: EventsToMessages,
        normalize_tool_result_event: NormalizeToolResultEvent,
        validate_history: ValidateHistory | None = None,
    ) -> None:
        self._get_latest_checkpoint = get_latest_checkpoint
        self._get_events_after = get_events_after
        self._has_user_turn_context = has_user_turn_context
        self._legacy_restore = legacy_restore
        self._deserialize_base_messages = deserialize_base_messages
        self._events_to_messages = events_to_messages
        self._normalize_tool_result_event = normalize_tool_result_event
        self._validate_history = validate_history

    def restore(
        self,
        session_id: str,
        *,
        spawn_id: str | None = None,
    ) -> list[Message]:
        checkpoint = self._get_latest_checkpoint(session_id, spawn_id)
        schema_v1 = self._is_v1_checkpoint(checkpoint)

        if not schema_v1:
            if not self._has_user_turn_context(session_id, spawn_id):
                return self._legacy_restore(session_id, spawn_id)
            return self._restore_v1(
                session_id=session_id,
                spawn_id=spawn_id,
                checkpoint=None,
            )

        assert checkpoint is not None
        content = checkpoint["content"]
        covered = content.get("covered_until_event_id")
        if covered is None:
            logger.warning(
                "history_checkpoint.v1 has null covered_until_event_id; "
                "falling back to legacy restore"
            )
            return self._legacy_restore(session_id, spawn_id)

        return self._restore_v1(
            session_id=session_id,
            spawn_id=spawn_id,
            checkpoint=checkpoint,
        )

    @staticmethod
    def _is_v1_checkpoint(checkpoint: dict[str, Any] | None) -> bool:
        if checkpoint is None:
            return False
        content = checkpoint.get("content")
        if not isinstance(content, dict):
            return False
        return content.get("schema_version") == _V1_SCHEMA

    def _restore_v1(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[Message]:
        if checkpoint is not None:
            content = checkpoint["content"]
            after = int(content["covered_until_event_id"])
            base_messages = self._deserialize_base_messages(
                content.get("base_messages") or []
            )
            hybrid_mode = False
        else:
            base_messages = []
            after = None
            hybrid_mode = True

        events = self._get_events_after(session_id, after, spawn_id)

        covered_invocations: set[str] = set()
        if hybrid_mode:
            for event in events:
                if event.get("type") == "user_turn_context":
                    invocation_id = event.get("invocation_id")
                    if invocation_id:
                        covered_invocations.add(str(invocation_id))

        compatible_tail_events: list[dict[str, Any]] = []
        for event in events:
            compatible = self._event_to_v1_compatible_event(
                event,
                hybrid_mode=hybrid_mode,
                covered_invocations=covered_invocations,
            )
            if compatible is not None:
                compatible_tail_events.append(compatible)

        tail_messages = self._events_to_messages(compatible_tail_events)
        history = [*base_messages, *tail_messages]
        if base_messages and self._validate_history is not None:
            self._validate_history(history)
        return history

    def _event_to_v1_compatible_event(
        self,
        event: dict[str, Any],
        *,
        hybrid_mode: bool,
        covered_invocations: set[str],
    ) -> dict[str, Any] | None:
        etype = str(event.get("type") or "").strip()
        source = str(event.get("source") or "").strip()
        payload = event.get("content")

        if etype == "user_turn_context":
            if not isinstance(payload, dict):
                return None
            raw_message = payload.get("message")
            if not isinstance(raw_message, dict):
                return None
            message = UserMessage.model_validate(raw_message)
            image_urls = [image.url for image in message.images if image.url]
            return {
                **event,
                "source": "User",
                "type": "query",
                "content": {
                    "content": message.content or "",
                    "images": image_urls,
                },
                "images": image_urls,
            }

        if source == "User" and etype == "query":
            if not hybrid_mode:
                return None
            invocation_id = event.get("invocation_id")
            if invocation_id and str(invocation_id) in covered_invocations:
                return None
            return event

        if etype == "tool_result":
            return self._normalize_tool_result_event(event)

        if etype in {
            "assistant_state",
            "response",
            "run_result",
            "finish",
            "tool_call",
        }:
            return event

        if etype in {
            "thought",
            "skill_hit",
            "compaction",
            "history_checkpoint",
            "context_compaction",
        }:
            return None

        return None
