from __future__ import annotations

import logging
from typing import Any

from matmaster.types.messages import Message

from src.services.chat_history import ChatHistoryConverter
from src.services.history_checkpoint_codec import (
    deserialize_base_messages,
    validate_base_messages,
)

logger = logging.getLogger(__name__)


class HistoryRestoreService:
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

        for checkpoint in checkpoints:
            try:
                content = checkpoint.get('content')
                if not isinstance(content, dict):
                    raise ValueError('checkpoint content must be a dict')

                covered_until = int(content['covered_until_event_id'])
                base_messages = deserialize_base_messages(content['base_messages'])
                validate_base_messages(base_messages)

                scope_events = self.events_table.get_scope_events_after_id(
                    session_id,
                    spawn_id,
                    covered_until,
                )
                scope_events = ChatHistoryConverter.exclude_task_events(
                    scope_events, task_id
                )
                tail_messages = ChatHistoryConverter.events_to_messages(scope_events)
                history = [*base_messages, *tail_messages]
                validate_base_messages(history)
                return history
            except Exception as exc:
                logger.warning(
                    'history_restore: checkpoint restore failed, trying older checkpoint '
                    'session_id=%s spawn_id=%s checkpoint_id=%s err=%s: %s',
                    session_id,
                    spawn_id,
                    checkpoint.get('id'),
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )

        raw_events = self.events_table.get_session_events(
            session_id,
            limit=raw_limit,
            include_spawn=spawn_id is not None,
        )
        if spawn_id is None:
            raw_events = ChatHistoryConverter.exclude_spawn_events(raw_events)
        else:
            raw_events = [
                event for event in raw_events if event.get('spawn_id') == spawn_id
            ]
        raw_events = ChatHistoryConverter.exclude_task_events(raw_events, task_id)
        return ChatHistoryConverter.events_to_messages(raw_events)
