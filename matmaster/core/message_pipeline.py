"""Incremental canonical-message pipeline for the agent main loop.

Caches the canonicalized (merged consecutive UserMessage) Message prefix and
only re-merges the tail between turns. Wire serialization and OpenAI-shape
validation live in the transport (ChatCompletionsTransport.convert_messages),
not here.
"""

from __future__ import annotations

import logging

from matmaster.types.message_normalization import canonicalize_messages_for_provider
from matmaster.types.messages import Message

logger = logging.getLogger(__name__)


class IncrementalMessagePipeline:
    """Stateful canonical-Message builder for the agent main loop."""

    def __init__(self) -> None:
        self._canonical_cache: list[Message] = []
        self._source_len = 0
        self._prefix_fingerprint: tuple[int, int, int] | None = None

    def reset(self) -> None:
        """Drop all caches. Next feed_tail rebuilds from scratch."""
        self._canonical_cache = []
        self._source_len = 0
        self._prefix_fingerprint = None

    def feed_tail(self, messages: list[Message]) -> list[Message]:
        """Process messages tail and return canonical list[Message].

        Reuses prefix cache and only processes messages[self._source_len:].
        Prefix mutation detection is best-effort only; any path that rewrites
        previously processed messages must call reset() explicitly.
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
                    extra={"observed": current, "expected": self._prefix_fingerprint},
                )
                self.reset()

        tail = messages[self._source_len :]
        if not tail:
            return list(self._canonical_cache)

        # 合并规则单源于 canonicalize：把缓存尾元素与新尾巴一起折叠后拼回，
        # 缓存前缀不重算，增量性不变。
        self._canonical_cache[-1:] = canonicalize_messages_for_provider(
            [*self._canonical_cache[-1:], *tail]
        )

        self._source_len = len(messages)
        self._prefix_fingerprint = (
            self._source_len,
            id(messages[0]),
            id(messages[self._source_len - 1]),
        )
        return list(self._canonical_cache)
