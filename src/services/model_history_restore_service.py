from __future__ import annotations

import json
import logging
from typing import Any

from matmaster.types.messages import Message, UserMessage
from matmaster.utils.event_source import normalize_event_source
from src.services.chat_history import ChatHistoryConverter
from src.services.history_checkpoint_codec import (
    deserialize_base_messages,
    validate_base_messages,
)
from src.services.image_input_service import trim_history_images

logger = logging.getLogger(__name__)


class ModelHistoryRestoreService:
    def __init__(self, events_table: Any) -> None:
        self.events_table = events_table

    def restore_history(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        task_id: str | None,
        raw_limit: int | None = None,
    ) -> list[Message]:
        checkpoints = self.events_table.get_history_checkpoints(
            session_id, spawn_id, limit=5
        )
        v1_checkpoints = self._v1_checkpoints(checkpoints)

        for v1_checkpoint in v1_checkpoints:
            content = v1_checkpoint.get("content")
            if not isinstance(content, dict):
                logger.warning(
                    "model_history_restore: v1 checkpoint content is not a dict "
                    "session_id=%s spawn_id=%s checkpoint_id=%s",
                    session_id,
                    spawn_id,
                    v1_checkpoint.get("id"),
                )
                continue

            covered_until = content.get("covered_until_event_id")
            if covered_until is None:
                logger.warning(
                    "model_history_restore: v1 checkpoint has null "
                    "covered_until_event_id; falling back to legacy restore "
                    "session_id=%s spawn_id=%s checkpoint_id=%s",
                    session_id,
                    spawn_id,
                    v1_checkpoint.get("id"),
                )
                return self._restore_legacy(session_id, spawn_id, task_id, raw_limit)

            try:
                after_id = int(covered_until)
                return self._restore_v1(
                    session_id=session_id,
                    spawn_id=spawn_id,
                    task_id=task_id,
                    checkpoint=v1_checkpoint,
                    after_id=after_id,
                    hybrid_mode=False,
                )
            except Exception as exc:
                logger.warning(
                    "model_history_restore: v1 checkpoint restore failed; trying older "
                    "checkpoint session_id=%s spawn_id=%s checkpoint_id=%s err=%s: %s",
                    session_id,
                    spawn_id,
                    v1_checkpoint.get("id"),
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                continue

        if v1_checkpoints:
            return self._restore_legacy(session_id, spawn_id, task_id, raw_limit)

        if self._session_has_user_turn_context(session_id, spawn_id):
            return self._restore_v1(
                session_id=session_id,
                spawn_id=spawn_id,
                task_id=task_id,
                checkpoint=None,
                after_id=None,
                hybrid_mode=True,
            )

        # COMPAT:v0-restore -- old sessions without user_turn_context or v1
        # checkpoint still restore through ChatHistoryConverter until Phase 4.
        return self._restore_legacy(session_id, spawn_id, task_id, raw_limit)

    def _restore_legacy(
        self,
        session_id: str,
        spawn_id: str | None,
        task_id: str | None,
        raw_limit: int | None,
    ) -> list[Message]:
        raw_events = self.events_table.get_session_events(
            session_id,
            limit=raw_limit,
            include_spawn=spawn_id is not None,
        )
        if spawn_id is None:
            raw_events = ChatHistoryConverter.exclude_spawn_events(raw_events)
        else:
            raw_events = [
                event for event in raw_events if event.get("spawn_id") == spawn_id
            ]
        raw_events = ChatHistoryConverter.exclude_task_events(raw_events, task_id)
        return trim_history_images(ChatHistoryConverter.events_to_messages(raw_events))

    def _restore_v1(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        task_id: str | None,
        checkpoint: dict[str, Any] | None,
        after_id: int | None,
        hybrid_mode: bool,
    ) -> list[Message]:
        base_messages: list[Message] = []
        if checkpoint is not None:
            content = checkpoint.get("content")
            if not isinstance(content, dict):
                raise ValueError("checkpoint content must be a dict")
            base_messages = deserialize_base_messages(content["base_messages"])
            validate_base_messages(base_messages)

        scope_events = self.events_table.get_scope_events_after_id(
            session_id,
            spawn_id,
            after_id,
        )
        scope_events = ChatHistoryConverter.exclude_task_events(scope_events, task_id)

        covered_invocation_ids: set[str] = set()
        if hybrid_mode:
            covered_invocation_ids = {
                str(event.get("invocation_id"))
                for event in scope_events
                if (event.get("type") or "").strip() == "user_turn_context"
                and event.get("invocation_id")
            }

        compatible_tail_events: list[dict[str, Any]] = []
        for event in scope_events:
            compatible_event = self._event_to_v1_compatible_event(
                event,
                hybrid_mode=hybrid_mode,
                covered_invocation_ids=covered_invocation_ids,
            )
            if compatible_event is not None:
                compatible_tail_events.append(compatible_event)

        tail_messages = ChatHistoryConverter.events_to_messages(compatible_tail_events)

        history = [*base_messages, *tail_messages]
        if base_messages:
            validate_base_messages(history)
        return trim_history_images(history)

    def _event_to_v1_compatible_event(
        self,
        event: dict[str, Any],
        *,
        hybrid_mode: bool,
        covered_invocation_ids: set[str],
    ) -> dict[str, Any] | None:
        source = normalize_event_source(event.get("source"))
        event_type = (event.get("type") or "").strip()
        content = event.get("content")

        if event_type == "user_turn_context":
            if not isinstance(content, dict):
                return None
            raw_message = content.get("message")
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

        if source == "User" and event_type == "query":
            if not hybrid_mode:
                return None

            invocation_id = event.get("invocation_id")
            if invocation_id and str(invocation_id) in covered_invocation_ids:
                return None

            # COMPAT:hybrid-restore -- pre-Phase-1 raw User/query rows without a
            # covering user_turn_context are preserved until active histories are
            # rewritten.
            return event

        if event_type == "tool_result":
            return self._normalize_tool_result_event(event)

        if event_type in {
            "assistant_state",
            "response",
            "run_result",
            "finish",
            "tool_call",
        }:
            return event

        if event_type in {
            "thought",
            "skill_hit",
            "compaction",
            "history_checkpoint",
            "context_compaction",
        }:
            return None

        return None

    def _session_has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        return bool(self.events_table.has_user_turn_context(session_id, spawn_id))

    @staticmethod
    def _v1_checkpoints(checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
        v1_checkpoints: list[dict[str, Any]] = []
        for checkpoint in checkpoints:
            content = checkpoint.get("content")
            if (
                isinstance(content, dict)
                and content.get("schema_version") == "history_checkpoint.v1"
            ):
                v1_checkpoints.append(checkpoint)
        return v1_checkpoints

    @staticmethod
    def _normalize_tool_result_event(event: dict[str, Any]) -> dict[str, Any]:
        content = event.get("content")
        if not isinstance(content, dict):
            return event

        normalized = dict(content)
        if "id" not in normalized and content.get("call_id"):
            normalized["id"] = content.get("call_id")
        if "name" not in normalized and content.get("tool_name"):
            normalized["name"] = content.get("tool_name")
        result = normalized.get("result")
        if result is not None and not isinstance(result, str):
            normalized["result"] = json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        return {**event, "content": normalized}
