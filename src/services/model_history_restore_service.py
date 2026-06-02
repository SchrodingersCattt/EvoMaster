from __future__ import annotations

import json
import logging
from typing import Any

from matmaster.context.history_restore import (
    HistoryCheckpointCorruptedError,
    HistoryRestoreFailedError,
    ModelHistoryRestorer,
)
from matmaster.types.messages import Message
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

            try:
                messages = self._delegate_v1_restore(
                    session_id=session_id,
                    spawn_id=spawn_id,
                    task_id=task_id,
                    checkpoint=v1_checkpoint,
                )
                return trim_history_images(messages)
            except HistoryCheckpointCorruptedError:
                logger.warning(
                    "model_history_restore: v1 checkpoint has null boundary; aborting "
                    "session_id=%s spawn_id=%s checkpoint_id=%s",
                    session_id,
                    spawn_id,
                    v1_checkpoint.get("id"),
                    exc_info=True,
                )
                raise
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
            raise HistoryRestoreFailedError(
                "no usable history_checkpoint.v1 could be restored "
                f"session_id={session_id} spawn_id={spawn_id}"
            )

        messages = self._delegate_v1_restore(
            session_id=session_id,
            spawn_id=spawn_id,
            task_id=task_id,
            checkpoint=None,
        )
        return trim_history_images(messages)

    def _delegate_v1_restore(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        task_id: str | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[Message]:
        def get_latest_checkpoint(
            _session_id: str,
            _spawn_id: str | None,
        ) -> dict[str, Any] | None:
            return checkpoint

        def get_events_after(
            _session_id: str,
            after_event_id: int | None,
            _spawn_id: str | None,
        ) -> list[dict[str, Any]]:
            events = self.events_table.get_scope_events_after_id(
                session_id,
                spawn_id,
                after_event_id,
            )
            events = ChatHistoryConverter.exclude_task_events(events, task_id)
            return [self._coerce_to_restorer_dict(event) for event in events]

        def has_user_turn_context(_session_id: str, _spawn_id: str | None) -> bool:
            return self._session_has_user_turn_context(session_id, spawn_id)

        def deserialize_checkpoint_base_messages(
            raw: list[dict[str, Any]],
        ) -> list[Message]:
            messages = deserialize_base_messages(raw)
            validate_base_messages(messages)
            return messages

        restorer = ModelHistoryRestorer(
            get_latest_checkpoint=get_latest_checkpoint,
            get_events_after=get_events_after,
            has_user_turn_context=has_user_turn_context,
            deserialize_base_messages=deserialize_checkpoint_base_messages,
            events_to_messages=ChatHistoryConverter.events_to_messages,
            normalize_tool_result_event=self._normalize_tool_result_event,
            validate_history=validate_base_messages,
        )
        return restorer.restore(session_id, spawn_id=spawn_id)

    @staticmethod
    def _coerce_to_restorer_dict(event: dict[str, Any]) -> dict[str, Any]:
        if isinstance(event, dict):
            return dict(event)
        return {}

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
