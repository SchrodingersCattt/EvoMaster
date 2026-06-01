"""ContextCompactor -- runtime context compression for the agent kernel.

Compresses old messages via LLM summarization when the estimated prompt
tokens approach the context window limit. Falls back to sliding-window
truncation if summarization fails.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from matmaster.context.assembly import (
    CompactionAssemblyRequest,
    ContextAssembler,
    ContextAssemblyIntent,
)
from matmaster.context.ports import UserInstructions, hash_user_instructions
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import (
    TurnInput,
)
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.message_normalization import (
    canonicalize_messages_for_provider,
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from matmaster.types.runtime import CompactionConfig

logger = logging.getLogger(__name__)

_EMPTY_USER_INSTRUCTIONS_HASH = hash_user_instructions("")

SUMMARY_USER_REQUEST_TEMPLATE = """\
<compact_request>
当前会话上下文已接近上限，需要你对所有对话进行压缩。请按以下要求输出
一份结构化摘要，后续对话将以输出的摘要作为历史背景继续，使用的语言需要
同之前用户使用的语言维持一致。

需要保留：
- 关键决策及其原因
- 工具调用的结果（必须保留确切数值、路径、文件名、错误信息）
- 用户给出的约束、参数、偏好
- 当前任务状态与已完成事项
- 需要继续进行的任务

输出要求：
- 只输出摘要文本，不要寒暄、不要解释你正在做什么、不要复述本压缩请求
- 不要调用任何工具
- 不要继续推进工具调用未完成的任务，只对其结果做总结
- 不要新增没有提到的信息
</compact_request>\
"""

_SUMMARY_USER_REQUEST_TEMPLATE_EN_RESERVED = """\
<compact_request>
The conversation context above is approaching the limit. Please produce a
structured summary; subsequent dialogue will treat your summary as the
historical context. Match the language of the conversation above.

Preserve:
- Key decisions and their rationale
- Tool call results (keep exact values, paths, filenames, error messages)
- User constraints, parameters, and preferences
- Current task status and completed items

Output requirements:
- Output only the summary text. No pleasantries, meta-commentary, or
  restatement of this request.
- Do not call any tools (disabled at API level even if tools appear relevant).
- Do not continue any in-flight tool-driven reasoning; only summarize results.
- Do not add information not present above.
- If the conversation above starts with a <compacted_history> block, merge it
  with later events into a single fresh summary; do not copy the old block
  verbatim.
</compact_request>\
"""

_TRUNCATE_HEAD_CHARS = 1200
_TRUNCATE_TAIL_CHARS = 800
_TRUNCATE_MIN_CONTENT_CHARS = 500

CURRENT_INPUT_CONTINUATION_INSTRUCTION = (
    "不要向用户复述上述摘要，除非用户明确要求。"
    "当前用户指令位于下面的 <current_instruction> 块中；"
    "请基于摘要背景直接执行该指令。"
)


@functools.cache
def _get_encoder():
    """Lazy-load tiktoken encoder with fallback."""
    try:
        import tiktoken

        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        logger.warning("tiktoken unavailable, using len/4 heuristic")
        return None


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


def _truncate_tool_message_for_summary(msg: ToolMessage) -> ToolMessage:
    content = msg.content or ""
    head = content[:_TRUNCATE_HEAD_CHARS]
    tail = content[-_TRUNCATE_TAIL_CHARS:]
    marker = (
        "\n\n[tool_result truncated before summary call]\n"
        f"tool_name: {msg.tool_name}\n"
        f"tool_call_id: {msg.tool_call_id}\n"
        f"original_chars: {len(content)}\n"
        f"preserved: first {_TRUNCATE_HEAD_CHARS} chars and "
        f"last {_TRUNCATE_TAIL_CHARS} chars\n"
        "reason: summary input would exceed context window\n\n"
    )
    return ToolMessage(
        content=head + marker + tail,
        tool_call_id=msg.tool_call_id,
        tool_name=msg.tool_name,
    )


def _summary_base_messages(
    *,
    full_messages: list[Message],
    phase: Literal["preflight", "runtime"],
    turn_input: TurnInput | None,
) -> list[Message]:
    current_split = _should_split_current_input_for_preflight(
        phase=phase,
        messages=full_messages,
        turn_input=turn_input,
    )
    return full_messages[:-1] if current_split else full_messages


def _should_split_current_input_for_preflight(
    *,
    phase: Literal["preflight", "runtime"],
    messages: list[Message],
    turn_input: TurnInput | None,
) -> bool:
    return (
        phase == "preflight"
        and turn_input is not None
        and turn_input.has_effective_input()
        and len(messages) >= 3
        and isinstance(messages[-1], UserMessage)
        and bool(messages[1:-1])
    )


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
    base_messages: list[dict[str, Any]] | None
    checkpoint_covered_until_event_id: int | None = None
    user_instructions_text: str = ""
    user_instructions_hash: str = _EMPTY_USER_INSTRUCTIONS_HASH


@dataclass(frozen=True)
class SummaryInputPreparation:
    """Result of preparing messages for a summary call."""

    messages: list[Message]
    truncated_tool_call_ids: tuple[str, ...]
    original_tokens: int
    prepared_tokens: int
    request_tokens: int
    message_budget: int


def prepare_messages_for_summary_call(
    *,
    full_messages: list[Message],
    phase: Literal["preflight", "runtime"],
    turn_input: TurnInput | None,
    compact_request: UserMessage,
    context_limit: int,
    reserved_summary_tokens: int,
    safety_margin_tokens: int = 5_000,
) -> SummaryInputPreparation:
    """Prepare base messages for a cache-preserving summary call."""
    if not full_messages:
        raise ValueError("Cannot prepare summary call for empty messages")
    if not isinstance(full_messages[0], SystemMessage):
        raise TypeError(
            f"full_messages[0] must be SystemMessage, got {type(full_messages[0])}"
        )

    base_messages = _summary_base_messages(
        full_messages=full_messages,
        phase=phase,
        turn_input=turn_input,
    )
    input_budget = context_limit - reserved_summary_tokens - safety_margin_tokens
    request_tokens = estimate_tokens([compact_request], safety_margin=1.1)
    message_budget = input_budget - request_tokens
    if message_budget <= 0:
        raise ValueError("summary message budget non-positive")

    prepared_tokens = estimate_tokens(list(base_messages), safety_margin=1.0)
    if prepared_tokens <= message_budget:
        return SummaryInputPreparation(
            messages=list(base_messages),
            truncated_tool_call_ids=(),
            original_tokens=prepared_tokens,
            prepared_tokens=prepared_tokens,
            request_tokens=request_tokens,
            message_budget=message_budget,
        )

    working = list(base_messages)
    original_tokens = prepared_tokens
    candidates = [
        (idx, estimate_tokens([msg], safety_margin=1.0))
        for idx, msg in enumerate(working)
        if isinstance(msg, ToolMessage)
        and msg.content
        and len(msg.content) >= _TRUNCATE_MIN_CONTENT_CHARS
    ]
    candidates.sort(key=lambda item: item[1], reverse=True)

    truncated_ids: list[str] = []
    for idx, _tokens in candidates:
        msg = working[idx]
        if not isinstance(msg, ToolMessage):
            continue
        working[idx] = _truncate_tool_message_for_summary(msg)
        truncated_ids.append(msg.tool_call_id)
        prepared_tokens = estimate_tokens(working, safety_margin=1.0)
        if prepared_tokens <= message_budget:
            break

    if prepared_tokens > message_budget:
        raise ValueError("summary input exceeds context window after tool truncation")

    return SummaryInputPreparation(
        messages=working,
        truncated_tool_call_ids=tuple(truncated_ids),
        original_tokens=original_tokens,
        prepared_tokens=prepared_tokens,
        request_tokens=request_tokens,
        message_budget=message_budget,
    )


async def call_summary_llm(
    *,
    llm_provider: LLMProvider,
    system_prompt: str,
    full_messages: list[Message],
    phase: Literal["preflight", "runtime"],
    turn_input: TurnInput | None,
    tool_definitions: list[dict] | None,
    context_limit: int,
    reserved_summary_tokens: int,
    safety_margin_tokens: int = 5_000,
) -> str:
    """Call the main LLM to summarize conversation history."""
    if not full_messages:
        raise ValueError("Cannot summarize empty message list")
    if not isinstance(full_messages[0], SystemMessage):
        raise TypeError(
            f"full_messages[0] must be SystemMessage, got {type(full_messages[0])}"
        )
    if full_messages[0].content != system_prompt:
        logger.debug("Summary call system prompt differs from supplied system_prompt")

    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase=phase,
        turn_input=turn_input,
        compact_request=compact_request,
        context_limit=context_limit,
        reserved_summary_tokens=reserved_summary_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )
    summary_messages = [*prep.messages, compact_request]
    api_messages = normalize_and_validate_openai_messages(
        canonicalize_messages_for_provider(summary_messages)
    )
    response = await llm_provider.chat(
        api_messages,
        tools=tool_definitions,
        tool_choice="none",
    )
    if response.tool_calls:
        raise ValueError("Summary LLM attempted tool calls")
    if not response.content or not response.content.strip():
        raise ValueError("Summary LLM returned empty content")
    return response.content


class ContextCompactor:
    """Runtime context compressor, called by kernel before each LLM invocation."""

    def __init__(
        self,
        config: CompactionConfig,
        *,
        context_assembler: ContextAssembler,
        user_instructions: UserInstructions,
        session_id: str,
        spawn_id: str | None,
        runtime_covered_until_provider: Callable[[], int | None] | None = None,
        event_sink: Callable[[Any], Awaitable[None]] | None = None,
        compaction_scope: str = "root",
    ) -> None:
        self._config = config
        self._context_assembler = context_assembler
        self._user_instructions = user_instructions
        self._session_id = session_id
        self._spawn_id = spawn_id
        self._runtime_covered_until_provider = runtime_covered_until_provider
        self._event_sink = event_sink
        self._compaction_scope = compaction_scope
        self._last_llm_message_count: int = 0
        self._last_compaction_turn: int = 0
        self._compaction_count: int = 0

    def update_message_count(self, count: int) -> None:
        """Record the messages length after the last LLM call."""
        self._last_llm_message_count = count

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

    def _finalize_result_state(
        self, plan: CompactionPlan, messages: list[Message]
    ) -> None:
        self._compaction_count = plan.compaction_count
        if plan.phase == "runtime":
            self._last_compaction_turn = plan.turn or 0
        self._last_llm_message_count = len(messages)
        logger.warning(
            "Context compaction #%d triggered at turn %d: "
            "estimated_tokens=%d threshold=%d",
            self._compaction_count,
            self._last_compaction_turn,
            plan.trigger_tokens,
            self._auto_threshold(),
        )

    async def apply_summary(
        self,
        plan: CompactionPlan,
        messages: list[Message],
        summary: str,
        *,
        turn_input: TurnInput | None = None,
    ) -> CompactionResult:
        """Apply a pre-computed summary and mutate messages in place."""
        if not messages:
            raise ValueError("Cannot compact an empty message list")
        if not isinstance(messages[0], SystemMessage):
            raise TypeError(
                f"messages[0] must be SystemMessage, got {type(messages[0])}"
            )
        system_msg = messages[0]
        current_split = _should_split_current_input_for_preflight(
            phase=plan.phase,
            messages=messages,
            turn_input=turn_input,
        )
        intent = (
            ContextAssemblyIntent.PREFLIGHT_COMPACTION
            if current_split
            else ContextAssemblyIntent.RUNTIME_COMPACTION
        )
        covered_until_event_id = None
        if intent == ContextAssemblyIntent.RUNTIME_COMPACTION:
            if self._runtime_covered_until_provider is None:
                raise ValueError(
                    "runtime compaction requires runtime_covered_until_provider"
                )
            covered_until_event_id = self._runtime_covered_until_provider()
            if covered_until_event_id is None:
                raise ValueError("runtime_current_event_boundary_missing")

        assembly = await self._context_assembler.assemble_compaction(
            intent,
            CompactionAssemblyRequest(
                session_id=self._session_id,
                spawn_id=self._spawn_id,
                user_instructions=self._user_instructions,
                compacted_history_summary=summary,
                turn_input=turn_input if current_split else None,
                covered_until_event_id=covered_until_event_id,
            ),
        )
        runtime_user_msg = assembly.user_turn_context.to_message(ContextView.RUNTIME)
        checkpoint_user_msg = assembly.user_turn_context.to_message(
            ContextView.CHECKPOINT
        )
        messages[:] = [system_msg, runtime_user_msg]
        self._finalize_result_state(plan, messages)
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="summary",
            durability="durable",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=0,
            failure_reason=None,
            base_messages=[checkpoint_user_msg.model_dump(mode="json")],
            checkpoint_covered_until_event_id=assembly.covered_until_event_id,
            user_instructions_text=assembly.user_instructions_text,
            user_instructions_hash=assembly.user_instructions_hash,
        )

    async def apply_fallback(
        self,
        plan: CompactionPlan,
        messages: list[Message],
        *,
        failure_reason: str,
    ) -> CompactionResult:
        """Apply a tool-turn-safe sliding-window fallback."""
        if not messages:
            raise ValueError("Cannot compact an empty message list")
        if not isinstance(messages[0], SystemMessage):
            raise TypeError(
                f"messages[0] must be SystemMessage, got {type(messages[0])}"
            )
        system_msg = messages[0]
        tail = _select_tool_safe_tail(messages[1:], n=3)
        if not tail:
            raise ValueError("runtime fallback produced empty tail")
        messages[:] = [system_msg, *tail]
        self._finalize_result_state(plan, messages)
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="sliding_window",
            durability="ephemeral",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=len(tail),
            failure_reason=failure_reason,
            base_messages=None,
        )


def _assistant_tool_call_ids(message: AssistantMessage) -> tuple[str, ...]:
    return tuple(tc.id for tc in message.tool_calls or ())


def _select_tool_safe_tail(
    non_system_messages: list[Message],
    *,
    n: int,
) -> list[Message]:
    """Select a trailing tail without emitting orphan tool results."""
    if n <= 0 or not non_system_messages:
        return []

    selected_indices = set(
        range(max(0, len(non_system_messages) - n), len(non_system_messages))
    )

    tool_indices_by_id: dict[str, list[int]] = {}
    for idx, msg in enumerate(non_system_messages):
        if isinstance(msg, ToolMessage):
            tool_indices_by_id.setdefault(msg.tool_call_id, []).append(idx)

    def complete_turn_indices(assistant_idx: int) -> set[int] | None:
        msg = non_system_messages[assistant_idx]
        if not isinstance(msg, AssistantMessage):
            return None
        call_ids = _assistant_tool_call_ids(msg)
        if not call_ids:
            return {assistant_idx}

        turn_indices = {assistant_idx}
        for call_id in call_ids:
            result_idx = next(
                (
                    idx
                    for idx in tool_indices_by_id.get(call_id, [])
                    if idx > assistant_idx
                ),
                None,
            )
            if result_idx is None:
                return None
            turn_indices.add(result_idx)
        return turn_indices

    changed = True
    while changed:
        changed = False
        selected_tool_ids = {
            msg.tool_call_id
            for idx, msg in enumerate(non_system_messages)
            if idx in selected_indices and isinstance(msg, ToolMessage)
        }
        for idx, msg in enumerate(non_system_messages):
            if not isinstance(msg, AssistantMessage):
                continue
            call_ids = set(_assistant_tool_call_ids(msg))
            if not call_ids:
                continue
            if idx not in selected_indices and not (call_ids & selected_tool_ids):
                continue

            turn_indices = complete_turn_indices(idx)
            if turn_indices is None:
                before = len(selected_indices)
                selected_indices.discard(idx)
                selected_indices = {
                    selected_idx
                    for selected_idx in selected_indices
                    if not (
                        isinstance(
                            non_system_messages[selected_idx],
                            ToolMessage,
                        )
                        and non_system_messages[selected_idx].tool_call_id in call_ids
                    )
                }
                changed = changed or len(selected_indices) != before
                continue

            before = len(selected_indices)
            selected_indices.update(turn_indices)
            changed = changed or len(selected_indices) != before

    owner_ids: set[str] = set()
    selected_assistant_indices: set[int] = set()
    for idx in selected_indices:
        msg = non_system_messages[idx]
        if not isinstance(msg, AssistantMessage):
            continue
        call_ids = _assistant_tool_call_ids(msg)
        if not call_ids:
            selected_assistant_indices.add(idx)
            continue
        turn_indices = complete_turn_indices(idx)
        if turn_indices is not None and turn_indices <= selected_indices:
            selected_assistant_indices.add(idx)
            owner_ids.update(call_ids)

    result: list[Message] = []
    for idx, msg in enumerate(non_system_messages):
        if idx not in selected_indices:
            continue
        if isinstance(msg, ToolMessage) and msg.tool_call_id not in owner_ids:
            continue
        if isinstance(msg, AssistantMessage):
            call_ids = _assistant_tool_call_ids(msg)
            if call_ids and idx not in selected_assistant_indices:
                continue
        result.append(msg)
    return result
