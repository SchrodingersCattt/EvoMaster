"""Incremental message pipeline for the agent main loop.

Replaces the per-turn full re-run of
canonicalize_messages_for_provider + normalize_and_validate_openai_messages
with a stateful pipeline that caches the processed prefix and only
re-processes the tail of state.messages between turns.
"""

from __future__ import annotations

import logging
from typing import Any

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    _merge_user_messages,
    validate_openai_messages,
    validate_openai_tool_turn_sequence,
)
from matmaster.types.messages import Message, UserMessage

logger = logging.getLogger(__name__)


def _to_normalized_api_dict(msg: Message) -> dict[str, Any]:
    """Convert one Message to API-ready dict, normalizing content=None to ""."""
    payload = msg.to_api_dict()
    if "content" not in payload or payload.get("content") is None:
        payload["content"] = ""
    return payload


class _ToolTurnValidator:
    """Stateful mirror of validate_openai_tool_turn_sequence for tail feeds."""

    def __init__(self) -> None:
        self._pending_tool_ids: set[str] = set()
        self._seen_tool_ids: set[str] = set()

    @property
    def pending_tool_ids(self) -> set[str]:
        return self._pending_tool_ids

    def reset(self) -> None:
        self._pending_tool_ids.clear()
        self._seen_tool_ids.clear()

    def feed_tail(self, new_msgs: list[dict[str, Any]]) -> None:
        """Validate a normalized tail segment without the end-pending assertion."""
        for message in new_msgs:
            role = message.get("role")

            if role == "tool":
                tool_id = str(message.get("tool_call_id") or "")
                if tool_id in self._seen_tool_ids:
                    raise LLMError(
                        f"duplicate tool_result ids for assistant turn: {tool_id}",
                        retryable=False,
                        error_category="bad_request",
                    )
                if not self._pending_tool_ids and not self._seen_tool_ids:
                    raise LLMError(
                        "orphan tool message after assistant without tool_calls",
                        retryable=False,
                        error_category="bad_request",
                    )
                if not tool_id or tool_id not in self._pending_tool_ids:
                    raise LLMError(
                        "tool_result without matching previous assistant "
                        f"tool_call: {tool_id}",
                        retryable=False,
                        error_category="bad_request",
                    )
                self._seen_tool_ids.add(tool_id)
                self._pending_tool_ids.remove(tool_id)
                continue

            if self._pending_tool_ids:
                raise LLMError(
                    "missing tool_result ids for assistant turn: "
                    f"{sorted(self._pending_tool_ids)}",
                    retryable=False,
                    error_category="bad_request",
                )

            self._seen_tool_ids.clear()

            if role != "assistant":
                continue

            raw_tool_calls = message.get("tool_calls") or []
            declared_ids: list[str] = []
            for tool_call in raw_tool_calls:
                if not isinstance(tool_call, dict):
                    raise LLMError(
                        "assistant tool_call payload must be a dict",
                        retryable=False,
                        error_category="bad_request",
                    )
                tool_id = str(tool_call.get("id") or "")
                if not tool_id:
                    raise LLMError(
                        "assistant tool_call missing id",
                        retryable=False,
                        error_category="bad_request",
                    )
                declared_ids.append(tool_id)

            if len(declared_ids) != len(set(declared_ids)):
                duplicates = sorted(
                    {
                        tool_id
                        for tool_id in declared_ids
                        if declared_ids.count(tool_id) > 1
                    }
                )
                raise LLMError(
                    f"duplicate tool_call ids in outbound assistant turn: {duplicates}",
                    retryable=False,
                    error_category="bad_request",
                )

            self._seen_tool_ids = set()
            self._pending_tool_ids = set(declared_ids)


class IncrementalMessagePipeline:
    """Stateful provider-payload builder for the agent main loop."""

    def __init__(self) -> None:
        self._canonical_cache: list[Message] = []
        self._api_cache: list[dict[str, Any]] = []
        self._source_len = 0
        self._prefix_fingerprint: tuple[int, int, int] | None = None
        self._validator = _ToolTurnValidator()

    def reset(self) -> None:
        """Drop all caches. Next feed_tail rebuilds from scratch."""
        self._canonical_cache = []
        self._api_cache = []
        self._source_len = 0
        self._prefix_fingerprint = None
        self._validator.reset()

    def feed_tail(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Process messages tail and return API-ready dicts.

        Reuses prefix cache and only processes messages[self._source_len:].
        Prefix mutation detection is best-effort only; any path that rewrites
        previously processed messages must call reset() explicitly.

        Returned values are shallow copies at the outer list and per-message
        dict levels. Callers must treat nested values as read-only.
        """
        if len(messages) < self._source_len:
            logger.warning(
                "pipeline prefix shrunk; auto-reset",
                extra={
                    "observed_len": len(messages),
                    "expected_source_len": self._source_len,
                },
            )
            self.reset()

        if self._source_len > 0 and self._prefix_fingerprint is not None:
            current = (
                self._source_len,
                id(messages[0]),
                id(messages[self._source_len - 1]),
            )
            if current != self._prefix_fingerprint:
                logger.warning(
                    "pipeline prefix mutation detected; auto-reset",
                    extra={
                        "observed": current,
                        "expected": self._prefix_fingerprint,
                    },
                )
                self.reset()

        tail = messages[self._source_len :]
        if not tail:
            return [dict(m) for m in self._api_cache]

        orig_api_len = len(self._api_cache)
        was_merged = False
        try:
            for i, msg in enumerate(tail):
                if (
                    self._canonical_cache
                    and isinstance(self._canonical_cache[-1], UserMessage)
                    and isinstance(msg, UserMessage)
                ):
                    merged = _merge_user_messages(self._canonical_cache[-1], msg)
                    self._canonical_cache[-1] = merged
                    self._api_cache[-1] = _to_normalized_api_dict(merged)
                    if i == 0:
                        was_merged = True
                    continue

                self._canonical_cache.append(msg)
                self._api_cache.append(_to_normalized_api_dict(msg))

            start = orig_api_len - 1 if was_merged else orig_api_len
            new_api_segment = self._api_cache[start:]
            validate_openai_messages(new_api_segment)
            self._validator.feed_tail(new_api_segment)

            if self._validator.pending_tool_ids:
                raise LLMError(
                    "missing tool_result ids for assistant turn: "
                    f"{sorted(self._validator.pending_tool_ids)}",
                    retryable=False,
                    error_category="bad_request",
                )

        except Exception:
            self.reset()
            raise

        self._source_len = len(messages)
        self._prefix_fingerprint = (
            self._source_len,
            id(messages[0]),
            id(messages[self._source_len - 1]),
        )
        return [dict(m) for m in self._api_cache]

    def revalidate_full(self, api_messages: list[dict[str, Any]]) -> None:
        """Run full validators on already-normalized api_messages."""
        validate_openai_messages(api_messages)
        validate_openai_tool_turn_sequence(api_messages)
