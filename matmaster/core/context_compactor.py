"""ContextCompactor -- runtime context compression for the agent kernel.

Compresses old messages via LLM summarization when the estimated prompt
tokens approach the context window limit. Falls back to sliding-window
truncation if summarization fails.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from matmaster.core.context_builder import ContextBuilder
from matmaster.manifests.rehydrator import CompactionRehydrator
from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)
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

Do NOT add new information. Do NOT include pleasantries. Be factual and precise.

If the input starts with a previous compact context bundle, merge it with later
events and produce a fresh conversation summary. Older <rehydrated_context>
blocks are historical state snapshots; do not copy them verbatim. Current state
is supplied separately by the new rehydrated context.\
"""

CURRENT_INPUT_CONTINUATION_INSTRUCTION = (
    "不要向用户复述上述摘要，除非用户明确要求。"
    "当前用户指令位于下面的 <current_instruction> 块中；"
    "请基于摘要背景直接执行该指令。"
)


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


@dataclass(frozen=True)
class CompactionPlan:
    compaction_id: str
    compaction_count: int
    phase: Literal["preflight", "runtime"]
    trigger_tokens: int
    strategy: Literal["summary", "sliding_window", "tool_truncation"] | None = None
    turn: int | None = None


@dataclass(frozen=True)
class CompactionResult:
    compaction_id: str
    compaction_count: int
    phase: Literal["preflight", "runtime"]
    strategy: Literal["summary", "sliding_window", "tool_truncation"]
    durability: Literal["durable", "ephemeral"]
    trigger_tokens: int
    retained_turns: int
    failure_reason: str | None
    base_snapshot: list[dict[str, Any]] | None
    checkpoint_covered_until_event_id: int | None = None


class ContextCompactor:
    """Runtime context compressor, called by kernel before each LLM invocation."""

    def __init__(
        self,
        config: CompactionConfig,
        summary_provider: LLMProvider,
        *,
        rehydrator: CompactionRehydrator,
        context_builder: ContextBuilder,
        event_sink: Callable[[Any], Awaitable[None]] | None = None,
        compaction_scope: str = "root",
    ) -> None:
        self._config = config
        self._summary_provider = summary_provider
        self._rehydrator = rehydrator
        self._context_builder = context_builder
        self._event_sink = event_sink
        self._compaction_scope = compaction_scope
        self._last_llm_message_count: int = 0
        self._last_compaction_turn: int = 0
        self._compaction_count: int = 0

    def update_message_count(self, count: int) -> None:
        """Record the messages length after the last LLM call."""
        self._last_llm_message_count = count

    async def preflight_if_needed(self, messages: list[Message]) -> None:
        """Compact eagerly before the next turn when history is already too large."""
        plan = self.plan_preflight_compaction(messages)
        if plan is None:
            return

        await self.apply_compaction_plan(plan, messages)

    async def compact_if_needed(
        self, messages: list[Message], last_usage: dict[str, int], turn: int
    ) -> None:
        """Check threshold and compact messages in place when needed."""
        plan = await self.plan_runtime_compaction(messages, last_usage, turn=turn)
        if plan is None:
            return

        await self.apply_compaction_plan(plan, messages)

    def _next_compaction_id(self) -> tuple[int, str]:
        next_count = self._compaction_count + 1
        return next_count, f"{self._compaction_scope}:{next_count}"

    def _auto_threshold(self) -> int:
        threshold = getattr(self._config, "auto_threshold", None)
        if isinstance(threshold, int):
            return threshold
        return int(self._config.context_limit * self._config.trigger_ratio)

    def _plan_preflight_compaction(
        self,
        messages: list[Message],
    ) -> CompactionPlan | None:
        estimated_tokens = estimate_tokens(messages, safety_margin=1.1)
        threshold = self._auto_threshold()
        if estimated_tokens < threshold:
            return None
        count, compaction_id = self._next_compaction_id()
        return CompactionPlan(
            compaction_id=compaction_id,
            compaction_count=count,
            phase="preflight",
            trigger_tokens=estimated_tokens,
            turn=0,
        )

    def plan_preflight_compaction(
        self, messages: list[Message]
    ) -> CompactionPlan | None:
        return self._plan_preflight_compaction(messages)

    async def plan_runtime_compaction(
        self,
        messages: list[Message],
        turn_usage: dict[str, int],
        *,
        turn: int,
    ) -> CompactionPlan | None:
        if turn <= self._last_compaction_turn + 1:
            return None

        base_tokens = int(turn_usage.get("prompt_tokens") or 0)
        delta_messages = messages[self._last_llm_message_count :]
        delta_tokens = estimate_tokens(delta_messages, safety_margin=1.1)
        estimated_tokens = base_tokens + delta_tokens
        threshold = self._auto_threshold()
        if estimated_tokens < threshold:
            return None

        count, compaction_id = self._next_compaction_id()
        return CompactionPlan(
            compaction_id=compaction_id,
            compaction_count=count,
            phase="runtime",
            trigger_tokens=estimated_tokens,
            turn=turn,
        )

    async def apply_compaction_plan(
        self,
        plan: CompactionPlan,
        messages: list[Message],
        *,
        current_input_context: CurrentInputContext | None = None,
    ) -> CompactionResult:
        """Apply a previously planned compaction and mutate messages in place."""
        if not messages:
            raise ValueError("Cannot compact an empty message list")
        if not isinstance(messages[0], SystemMessage):
            raise TypeError(
                f"messages[0] must be SystemMessage, got {type(messages[0])}"
            )
        system_msg = messages[0]
        current_split = (
            plan.phase == "preflight"
            and current_input_context is not None
            and current_input_context.has_effective_input()
            and len(messages) >= 3
            and isinstance(messages[-1], UserMessage)
            and bool(messages[1:-1])
        )
        if current_split:
            summary_input = list(messages[1:-1])
        else:
            summary_input = [
                message
                for message in messages
                if not isinstance(message, SystemMessage)
            ]
        if not summary_input:
            raise ValueError(
                "Cannot compact messages without user or assistant history"
            )

        strategy = "summary"
        durability = "durable"
        failure_reason: str | None = None
        retained_turns = 0
        checkpoint_covered_until_event_id: int | None = None
        checkpoint_user_msg: UserMessage | None = None

        try:
            summary = await self._summarize(summary_input)
            until_event_id = (
                current_input_context.pre_query_scope_event_id
                if current_split and current_input_context is not None
                else None
            )
            rehydrated = await self._rehydrator.build(until_event_id=until_event_id)
            if current_split and current_input_context is not None:
                runtime_bundle = self._context_builder.build_compact_bundle(
                    summary=summary,
                    rehydrated_context=rehydrated,
                    continuation_instruction=CURRENT_INPUT_CONTINUATION_INSTRUCTION,
                )
                checkpoint_bundle = self._context_builder.build_compact_bundle(
                    summary=summary,
                    rehydrated_context=rehydrated,
                )
                current_user_message = messages[-1]
                instruction = build_current_instruction_block(current_input_context)
                runtime_user_msg = UserMessage(
                    content=(
                        f"{runtime_bundle}\n\n{instruction}"
                        if instruction
                        else runtime_bundle
                    ),
                    images=list(current_user_message.images),
                )
                checkpoint_user_msg = UserMessage(content=checkpoint_bundle)
                messages[:] = [system_msg, runtime_user_msg]
                if current_input_context.pre_query_scope_event_id is None:
                    durability = "ephemeral"
                    failure_reason = "preflight_current_input_boundary_missing"
                else:
                    checkpoint_covered_until_event_id = (
                        current_input_context.pre_query_scope_event_id
                    )
            else:
                bundle = self._context_builder.build_compact_bundle(
                    summary=summary,
                    rehydrated_context=rehydrated,
                )
                checkpoint_user_msg = UserMessage(content=bundle)
                messages[:] = [system_msg, checkpoint_user_msg]
        except Exception as exc:
            if plan.phase == "preflight":
                logger.warning(
                    "Preflight compaction summary failed; aborting without fallback",
                    exc_info=True,
                )
                raise
            logger.warning(
                "Compaction #%d summary failed, falling back to sliding_window",
                plan.compaction_count,
                exc_info=True,
            )
            strategy = "sliding_window"
            durability = "ephemeral"
            failure_reason = str(exc)
            truncated = self._truncate_tool_results(
                messages,
                plan.trigger_tokens,
                self._auto_threshold(),
            )
            if truncated == 0:
                messages[:] = [system_msg, *summary_input[-3:]]

        self._compaction_count = plan.compaction_count
        if plan.phase == "runtime":
            self._last_compaction_turn = plan.turn or 0
        self._last_llm_message_count = len(messages)

        logger.warning(
            "Context compaction #%d triggered at turn %d: "
            "estimated_tokens=%d threshold=%d strategy=%s retained_turns=%d",
            self._compaction_count,
            self._last_compaction_turn,
            plan.trigger_tokens,
            self._auto_threshold(),
            strategy,
            retained_turns,
        )
        base_snapshot = None
        if durability == "durable" and checkpoint_user_msg is not None:
            base_snapshot = [checkpoint_user_msg.model_dump(mode="json")]
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy=strategy,
            durability=durability,
            trigger_tokens=plan.trigger_tokens,
            retained_turns=retained_turns,
            failure_reason=failure_reason,
            base_snapshot=base_snapshot,
            checkpoint_covered_until_event_id=checkpoint_covered_until_event_id,
        )

    def _select_recent_turns(
        self, turns: list[list[Message]]
    ) -> tuple[list[list[Message]], int]:
        """Select recent turns to retain based on the retention rule."""
        window = self._config.context_limit
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
            json.dumps(msg.model_dump(mode="json"), ensure_ascii=False)
            for msg in old_messages
        )
        api_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Summarize this conversation:\n\n{conversation_text}",
            },
        ]
        response = await self._summary_provider.chat(api_messages)
        if not response.content or not response.content.strip():
            raise ValueError("Summary LLM returned empty content")
        return response.content
