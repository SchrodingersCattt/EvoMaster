"""ContextCompactor -- runtime context compression for the agent kernel.

Compresses old messages via LLM summarization when the estimated prompt
tokens approach the context window limit. Falls back to sliding-window
truncation if summarization fails.
"""

from __future__ import annotations

import json
import logging

from matmaster.core.bus import MessageBus
from matmaster.types.events import ContextCompactionEvent
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from matmaster.types.runtime import CompactionConfig

logger = logging.getLogger(__name__)

_encoder = None

SUMMARY_SYSTEM_PROMPT = """\
You are a conversation summarizer. Summarize the following conversation history \
into a concise structured summary. Preserve:
- Key decisions made and their rationale
- Tool call results and their outcomes (include exact values, paths, filenames)
- Error messages and how they were resolved
- User constraints, parameters, and preferences stated during the conversation
- Current status and what has been accomplished

Do NOT add new information. Do NOT include pleasantries. Be factual and precise.\
"""


def _get_encoder():
    """Lazy-load tiktoken encoder with fallback."""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken

        _encoder = tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        logger.warning("tiktoken unavailable, using len/4 heuristic")
        _encoder = None
    return _encoder


def estimate_tokens(messages: list[Message], safety_margin: float = 1.0) -> int:
    """Estimate token count for a list of messages."""
    total = 0
    enc = _get_encoder()
    for msg in messages:
        text = json.dumps(msg.to_api_dict(), ensure_ascii=False)
        if enc is not None:
            total += len(enc.encode(text))
        else:
            total += max(len(text) // 4, 1)
        total += 4
    return int(total * safety_margin)


def _find_initial_task_index(messages: list[Message]) -> int:
    """Find the index of the initial task UserMessage."""
    first_assistant = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, AssistantMessage):
            first_assistant = i
            break
    if first_assistant == -1:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], UserMessage):
                return i
        return -1
    for i in range(first_assistant - 1, -1, -1):
        if isinstance(messages[i], UserMessage):
            return i
    return -1


def parse_turns(messages: list[Message]) -> list[list[Message]]:
    """Parse mutable messages into complete turns."""
    task_idx = _find_initial_task_index(messages)
    if task_idx == -1:
        return []
    start = task_idx + 1
    if start >= len(messages):
        return []

    turns: list[list[Message]] = []
    current_turn: list[Message] = []
    current_has_assistant = False

    for msg in messages[start:]:
        if isinstance(msg, AssistantMessage):
            if current_turn and current_has_assistant:
                turns.append(current_turn)
                current_turn = []
            current_turn.append(msg)
            current_has_assistant = True
            continue

        if isinstance(msg, UserMessage):
            if current_turn and current_has_assistant:
                turns.append(current_turn)
                current_turn = []
                current_has_assistant = False
            current_turn.append(msg)
            continue

        if isinstance(msg, ToolMessage):
            current_turn.append(msg)
            continue

        current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return turns


class ContextCompactor:
    """Runtime context compressor, called by kernel before each LLM invocation."""

    def __init__(
        self,
        config: CompactionConfig,
        summary_provider: LLMProvider,
        bus: MessageBus | None = None,
    ) -> None:
        self._config = config
        self._summary_provider = summary_provider
        self._bus = bus
        self._last_llm_message_count: int = 0
        self._last_compaction_turn: int = 0
        self._compaction_count: int = 0

    def update_message_count(self, count: int) -> None:
        """Record the messages length after the last LLM call."""
        self._last_llm_message_count = count

    async def compact_if_needed(
        self, messages: list[Message], last_usage: dict[str, int], turn: int
    ) -> None:
        """Check threshold and compact messages in place when needed."""
        if not self._config.enabled:
            return

        if turn <= self._last_compaction_turn + 1:
            return

        base_tokens = last_usage.get("prompt_tokens", 0)
        delta_messages = messages[self._last_llm_message_count :]
        delta_tokens = estimate_tokens(delta_messages, safety_margin=1.1)
        estimated = base_tokens + delta_tokens
        threshold = self._config.context_window_tokens * self._config.trigger_ratio
        if estimated < threshold:
            return

        if not messages:
            return
        if not isinstance(messages[0], SystemMessage):
            raise TypeError(
                f"messages[0] must be SystemMessage, got {type(messages[0])}"
            )
        system_msg = messages[0]
        task_idx = _find_initial_task_index(messages)
        if task_idx <= 0:
            raise ValueError("Initial UserMessage(task) not found")
        initial_task_msg = messages[task_idx]

        turns = parse_turns(messages)
        if not turns:
            return

        recent_turns, kept_count = self._select_recent_turns(turns)
        compressible_start = task_idx + 1
        compressible_end = len(messages) - sum(len(t) for t in recent_turns)
        if compressible_end <= compressible_start:
            # No old turns to compress -- all turns are retained.
            # Fall back to truncating large tool results within retained
            # turns to prevent context overflow on the next LLM call.
            truncated = self._truncate_tool_results(messages, estimated, threshold)
            if truncated > 0:
                self._compaction_count += 1
                self._last_compaction_turn = turn
                self._last_llm_message_count = len(messages)
                logger.warning(
                    "Context compaction #%d (tool_truncation) at turn %d: "
                    "estimated=%d threshold=%d truncated_messages=%d",
                    self._compaction_count,
                    turn,
                    estimated,
                    int(threshold),
                    truncated,
                )
                if self._bus is not None:
                    await self._bus.emit(
                        ContextCompactionEvent(
                            source="context_compactor",
                            payload={
                                "compaction_count": self._compaction_count,
                                "trigger_tokens": estimated,
                                "strategy": "tool_truncation",
                                "retained_turns": kept_count,
                            },
                        )
                    )
            return

        old_messages = messages[compressible_start:compressible_end]
        self._compaction_count += 1
        strategy = "summary"
        flat_recent = [m for turn_messages in recent_turns for m in turn_messages]

        try:
            summary = await self._summarize(old_messages)
            compact_msg = SystemMessage(content=f"[Compacted Context]\n{summary}")
            messages[:] = [system_msg, compact_msg, initial_task_msg, *flat_recent]
        except Exception:
            logger.warning(
                "Compaction #%d summary failed, falling back to sliding_window",
                self._compaction_count,
                exc_info=True,
            )
            strategy = "sliding_window"
            messages[:] = [system_msg, initial_task_msg, *flat_recent]

        self._last_compaction_turn = turn
        self._last_llm_message_count = len(messages)

        logger.warning(
            "Context compaction #%d triggered at turn %d: "
            "estimated_tokens=%d threshold=%d strategy=%s retained_turns=%d",
            self._compaction_count,
            turn,
            estimated,
            int(threshold),
            strategy,
            kept_count,
        )

        if self._bus is not None:
            await self._bus.emit(
                ContextCompactionEvent(
                    source="context_compactor",
                    payload={
                        "compaction_count": self._compaction_count,
                        "trigger_tokens": estimated,
                        "strategy": strategy,
                        "retained_turns": kept_count,
                    },
                )
            )

    def _select_recent_turns(
        self, turns: list[list[Message]]
    ) -> tuple[list[list[Message]], int]:
        """Select recent turns to retain based on the retention rule."""
        window = self._config.context_window_tokens
        budget_20 = int(window * 0.2)
        budget_40 = int(window * 0.4)

        selected: list[list[Message]] = []
        total_tokens = 0

        for turn in reversed(turns):
            turn_tokens = estimate_tokens(turn, safety_margin=1.1)
            if len(selected) >= 3 and total_tokens + turn_tokens > budget_40:
                break
            selected.append(turn)
            total_tokens += turn_tokens
            if len(selected) >= 3 and total_tokens >= budget_20:
                break

        while len(selected) < min(3, len(turns)):
            selected.append(turns[-(len(selected) + 1)])

        selected.reverse()
        return selected, len(selected)

    @staticmethod
    def _truncate_tool_results(
        messages: list[Message],
        estimated_tokens: int,
        threshold: float,
    ) -> int:
        """Truncate large ToolMessage content in place when no old turns to compress.

        Iterates tool messages from oldest to newest. For each oversized message,
        keeps a head + tail preview with a truncation marker. Stops once estimated
        tokens drop below threshold.

        Returns the number of messages truncated.
        """
        # Collect (index, token_count) for ToolMessages, largest first
        tool_indices: list[tuple[int, int]] = []
        for i, msg in enumerate(messages):
            if isinstance(msg, ToolMessage) and msg.content:
                toks = estimate_tokens([msg])
                tool_indices.append((i, toks))

        if not tool_indices:
            return 0

        # Sort by token count descending -- truncate the biggest first
        tool_indices.sort(key=lambda x: x[1], reverse=True)

        truncated = 0
        tokens_to_shed = int(
            estimated_tokens - threshold * 0.8
        )  # target 80% of threshold

        for idx, toks in tool_indices:
            if tokens_to_shed <= 0:
                break
            msg = messages[idx]
            assert isinstance(msg, ToolMessage)
            content = msg.content or ""
            if len(content) < 500:
                continue

            # Keep first 200 chars + last 100 chars, insert marker
            head = content[:200]
            tail = content[-100:]
            marker = (
                f"\n\n... [truncated: {len(content)} chars → 300 chars "
                f"to fit context window] ...\n\n"
            )
            new_content = head + marker + tail
            messages[idx] = ToolMessage(
                content=new_content,
                tool_call_id=msg.tool_call_id,
                tool_name=msg.tool_name,
            )
            saved = toks - estimate_tokens([messages[idx]])
            tokens_to_shed -= saved
            truncated += 1

        return truncated

    async def _summarize(self, old_messages: list[Message]) -> str:
        """Use the summary provider to condense old conversation messages."""
        conversation_text = "\n".join(
            f"[{msg.role.value}]: {msg.content or ''}" for msg in old_messages
        )
        api_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Summarize this conversation:\n\n{conversation_text}",
            },
        ]
        response = await self._summary_provider.chat(api_messages)
        if not response.content:
            raise ValueError("Summary LLM returned empty content")
        return response.content
