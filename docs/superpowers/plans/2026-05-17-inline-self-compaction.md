# Inline Self-Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSON-blob compaction with inline self-compaction that reuses the main LLM provider, main system prompt, real conversation messages, and the same tool schema while preserving the existing compaction assembly and checkpoint pipeline.

**Architecture:** The compactor remains responsible for planning and applying already-computed summaries, while the LLM summary call moves into a stateless module function that receives provider, system prompt, message history, tool definitions, and token budget at call time. The kernel resolves tool definitions before every compaction and main LLM call so summary calls and main calls share the same cached tool schema. Runtime summary failures degrade through a tool-turn-safe sliding-window fallback; preflight failures still raise.

**Tech Stack:** Python 3.11+, uv, pytest, Pydantic message models, `AgentKernel`, `ContextCompactor`, OpenAI-compatible providers, Bedrock Converse provider, MatMaster context assembly and checkpoint services.

---

## File Structure

- Modify `matmaster/context/compaction.py`: add `SUMMARY_USER_REQUEST_TEMPLATE`, `estimate_json_tokens`, `SummaryInputPreparation`, `prepare_messages_for_summary_call`, `call_summary_llm`, `_select_tool_safe_tail`; split `ContextCompactor.apply_compaction_plan` into `apply_summary` and `apply_fallback`; delete legacy summary provider ownership and tool-result truncation fallback.
- Modify `matmaster/core/agent_compaction.py`: orchestrate `call_summary_llm`, route preflight/runtime failures correctly, and pass through `tool_definitions`.
- Modify `matmaster/core/agent.py`: add or import `ensure_tool_definitions`, remove dual-provider lifecycle, resolve tool definitions before preflight compaction, runtime compaction, and main LLM payload assembly.
- Modify `matmaster/types/llm_provider.py`: extend `LLMProvider.chat()` with keyword-only `tool_choice`.
- Modify `matmaster/providers/openai_provider.py`: forward `tool_choice` to `chat.completions.create()` when provided.
- Modify `matmaster/providers/bedrock_provider.py`: accept `tool_choice`; for `tool_choice="none"` preserve `tools` and omit Bedrock `toolChoice`; reject unsupported non-`none` choices for now.
- Modify `matmaster/core/runtime_context_assembly.py`: remove summary-provider construction and stop passing `summary_provider` into `ContextCompactor`.
- Modify `matmaster/types/runtime.py`: remove `CompactionConfig.compaction_llm`.
- Modify `config/llm_config.yaml`: remove the dead `compaction:` model alias.
- Modify existing tests that construct `ContextCompactor` or assert legacy `summary_provider` behavior.
- Create `tests/matmaster/context/test_summary_caller.py`: unit tests for summary-call preparation, truncation, and `call_summary_llm`.
- Create `tests/matmaster/core/test_kernel_helpers.py`: unit tests for `ensure_tool_definitions`.
- Create `tests/matmaster/core/test_agent_compaction.py`: orchestration tests for success/failure routing.
- Create `tests/matmaster/integration/test_summary_cache_prefix.py`: value-proof integration tests for cache-prefix, preflight tool definitions, and oversized tool-result invariants.

---

### Task 1: Add Token Accounting and Tool-Safe Tail Helpers

**Files:**
- Modify: `matmaster/context/compaction.py`
- Create: `tests/matmaster/context/test_summary_caller.py`

- [ ] **Step 1: Write failing tests for JSON token accounting and tool-safe tail selection**

Create `tests/matmaster/context/test_summary_caller.py` with the imports and helper below:

```python
from __future__ import annotations

import pytest

from matmaster.context.compaction import (
    _select_tool_safe_tail,
    estimate_json_tokens,
)
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _assistant(*ids: str) -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCallData(id=tool_id, name="tool", arguments={"value": tool_id})
            for tool_id in ids
        ],
    )


def _tool(tool_id: str, content: str = "result") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_id, tool_name="tool")
```

Add these tests:

```python
def test_estimate_json_tokens_counts_serialized_schema() -> None:
    schema = [
        {
            "type": "function",
            "function": {
                "name": "paper_search",
                "description": "Search literature",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ]

    assert estimate_json_tokens(schema) > estimate_json_tokens([])
    assert estimate_json_tokens({"中文": "保留非 ASCII"}, safety_margin=1.5) >= (
        estimate_json_tokens({"中文": "保留非 ASCII"})
    )


def test_select_tool_safe_tail_keeps_complete_assistant_tool_pair() -> None:
    messages = [
        UserMessage(content="run"),
        _assistant("a", "b"),
        _tool("a"),
        _tool("b"),
    ]

    selected = _select_tool_safe_tail(messages, n=3)

    assert selected == messages[1:]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_select_tool_safe_tail_expands_backward_to_owner() -> None:
    messages = [
        UserMessage(content="old"),
        _assistant("a", "b"),
        _tool("a"),
        _tool("b"),
        AssistantMessage(content="done"),
    ]

    selected = _select_tool_safe_tail(messages, n=3)

    assert selected == messages[1:]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_select_tool_safe_tail_excludes_orphan_tool_messages() -> None:
    messages = [
        UserMessage(content="old"),
        _tool("missing-owner"),
        AssistantMessage(content="safe"),
    ]

    selected = _select_tool_safe_tail(messages, n=2)

    assert selected == [AssistantMessage(content="safe")]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_select_tool_safe_tail_returns_empty_for_all_orphans() -> None:
    assert _select_tool_safe_tail([_tool("a"), _tool("b")], n=2) == []
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py -q
```

Expected: import errors for `estimate_json_tokens` and `_select_tool_safe_tail`.

- [ ] **Step 3: Add helpers**

Modify `matmaster/context/compaction.py` imports so `AssistantMessage` is available:

```python
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
```

Add this helper near `estimate_tokens`:

```python
def estimate_json_tokens(obj: Any, safety_margin: float = 1.0) -> int:
    """Estimate token count for an arbitrary JSON-serializable object."""
    text = json.dumps(obj, ensure_ascii=False)
    enc = _get_encoder()
    if enc is not None:
        return int(len(enc.encode(text)) * safety_margin)
    return int(max(len(text) // 4, 1) * safety_margin)
```

Add these helpers near the bottom of `matmaster/context/compaction.py`:

```python
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

    selected_indices = set(range(max(0, len(non_system_messages) - n), len(non_system_messages)))
    selected_tool_ids = {
        msg.tool_call_id
        for idx, msg in enumerate(non_system_messages)
        if idx in selected_indices and isinstance(msg, ToolMessage)
    }

    owner_ids: set[str] = set()
    for idx in range(len(non_system_messages) - 1, -1, -1):
        msg = non_system_messages[idx]
        if not isinstance(msg, AssistantMessage):
            continue
        call_ids = set(_assistant_tool_call_ids(msg))
        if call_ids and call_ids & selected_tool_ids:
            if call_ids <= selected_tool_ids:
                selected_indices.add(idx)
                owner_ids.update(call_ids)
            else:
                selected_indices.discard(idx)

    result: list[Message] = []
    for idx, msg in enumerate(non_system_messages):
        if idx not in selected_indices:
            continue
        if isinstance(msg, ToolMessage) and msg.tool_call_id not in owner_ids:
            continue
        if isinstance(msg, AssistantMessage):
            call_ids = set(_assistant_tool_call_ids(msg))
            if call_ids and not call_ids <= selected_tool_ids:
                continue
        result.append(msg)
    return result
```

- [ ] **Step 4: Run tests to verify green**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py -q
```

Expected: all tests in the new file pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_summary_caller.py
git commit -m "test: cover compaction token helpers"
```

---

### Task 2: Add Summary Input Preparation

**Files:**
- Modify: `matmaster/context/compaction.py`
- Modify: `tests/matmaster/context/test_summary_caller.py`

- [ ] **Step 1: Write failing tests for summary input preparation**

Append these imports in `tests/matmaster/context/test_summary_caller.py`:

```python
from matmaster.context.compaction import (
    SUMMARY_USER_REQUEST_TEMPLATE,
    prepare_messages_for_summary_call,
)
from matmaster.context.sources.turn_input import TurnInput
```

Append these tests:

```python
def test_prepare_messages_common_case_preserves_message_identity() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old"),
        AssistantMessage(content="answer"),
    ]
    tool_definitions = [{"type": "function", "function": {"name": "tool"}}]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        tool_definitions=tool_definitions,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == full_messages
    assert all(left is right for left, right in zip(prep.messages, full_messages))
    assert prep.truncated_tool_call_ids == ()
    assert prep.original_tokens == prep.prepared_tokens
    assert prep.tool_schema_tokens > 0
    assert prep.request_tokens > 0
    assert prep.message_budget > prep.prepared_tokens


def test_prepare_messages_preflight_excludes_current_user_when_split_applies() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    current = TurnInput.from_values(user_text="new request")
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old"),
        AssistantMessage(content="answer"),
        UserMessage(content="new request"),
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="preflight",
        turn_input=current,
        compact_request=compact_request,
        tool_definitions=None,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == full_messages[:-1]
    assert full_messages[-1] not in prep.messages


def test_prepare_messages_runtime_includes_trailing_tool_message() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="run"),
        _assistant("a"),
        _tool("a", "large output"),
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        tool_definitions=None,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages[-1] is full_messages[-1]


def test_prepare_messages_non_positive_budget_raises() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [SystemMessage(content="sys"), UserMessage(content="old")]

    with pytest.raises(ValueError, match="summary message budget non-positive"):
        prepare_messages_for_summary_call(
            full_messages=full_messages,
            phase="runtime",
            turn_input=None,
            compact_request=compact_request,
            tool_definitions=[{"schema": "x" * 20_000}],
            context_limit=1_000,
            reserved_summary_tokens=900,
        )


def test_prepare_messages_truncates_only_largest_tool_results_needed_to_fit() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    small = _tool("small", "S" * 600)
    medium = _tool("medium", "M" * 4_000)
    large = _tool("large", "L" * 16_000)
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="run"),
        _assistant("small", "medium", "large"),
        small,
        medium,
        large,
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        tool_definitions=None,
        context_limit=7_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=500,
    )

    assert prep.prepared_tokens <= prep.message_budget
    assert "large" in prep.truncated_tool_call_ids
    assert prep.messages[3] is small
    assert prep.messages[4] is medium or prep.messages[4].tool_call_id == "medium"
    assert prep.messages[5] is not large
    assert prep.messages[5].tool_call_id == large.tool_call_id
    assert prep.messages[5].tool_name == large.tool_name
    assert "[tool_result truncated before summary call]" in (prep.messages[5].content or "")
    assert "tool_name: tool" in (prep.messages[5].content or "")
    assert "tool_call_id: large" in (prep.messages[5].content or "")
    assert "original_chars: 16000" in (prep.messages[5].content or "")
    assert full_messages[5] is large
    assert full_messages[5].content == "L" * 16_000
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py -q
```

Expected: import errors for `SUMMARY_USER_REQUEST_TEMPLATE` and `prepare_messages_for_summary_call`.

- [ ] **Step 3: Add summary request template, truncation constants, dataclass, and preparation function**

Replace `SUMMARY_SYSTEM_PROMPT` in `matmaster/context/compaction.py` with:

```python
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
```

Add this dataclass after `CompactionResult`:

```python
@dataclass(frozen=True)
class SummaryInputPreparation:
    """Result of preparing messages for a summary call."""

    messages: list[Message]
    truncated_tool_call_ids: tuple[str, ...]
    original_tokens: int
    prepared_tokens: int
    tool_schema_tokens: int
    request_tokens: int
    message_budget: int
```

Add these helpers near `estimate_json_tokens`:

```python
def _truncate_tool_message_for_summary(msg: ToolMessage) -> ToolMessage:
    content = msg.content or ""
    head = content[:_TRUNCATE_HEAD_CHARS]
    tail = content[-_TRUNCATE_TAIL_CHARS:]
    marker = (
        "\n\n[tool_result truncated before summary call]\n"
        f"tool_name: {msg.tool_name}\n"
        f"tool_call_id: {msg.tool_call_id}\n"
        f"original_chars: {len(content)}\n"
        f"preserved: first {_TRUNCATE_HEAD_CHARS} chars and last {_TRUNCATE_TAIL_CHARS} chars\n"
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
    current_split = (
        phase == "preflight"
        and turn_input is not None
        and turn_input.has_effective_input()
        and len(full_messages) >= 3
        and isinstance(full_messages[-1], UserMessage)
        and bool(full_messages[1:-1])
    )
    return full_messages[:-1] if current_split else full_messages
```

Add the main preparation function:

```python
def prepare_messages_for_summary_call(
    *,
    full_messages: list[Message],
    phase: Literal["preflight", "runtime"],
    turn_input: TurnInput | None,
    compact_request: UserMessage,
    tool_definitions: list[dict] | None,
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
    tool_schema_tokens = estimate_json_tokens(tool_definitions or [])
    request_tokens = estimate_tokens([compact_request], safety_margin=1.1)
    message_budget = input_budget - tool_schema_tokens - request_tokens
    if message_budget <= 0:
        raise ValueError("summary message budget non-positive")

    prepared_tokens = estimate_tokens(list(base_messages), safety_margin=1.0)
    if prepared_tokens <= message_budget:
        return SummaryInputPreparation(
            messages=list(base_messages),
            truncated_tool_call_ids=(),
            original_tokens=prepared_tokens,
            prepared_tokens=prepared_tokens,
            tool_schema_tokens=tool_schema_tokens,
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
        tool_schema_tokens=tool_schema_tokens,
        request_tokens=request_tokens,
        message_budget=message_budget,
    )
```

- [ ] **Step 4: Run tests to verify green**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_summary_caller.py
git commit -m "feat: prepare inline compaction summary input"
```

---

### Task 3: Add Stateless Summary LLM Caller

**Files:**
- Modify: `matmaster/context/compaction.py`
- Modify: `tests/matmaster/context/test_summary_caller.py`

- [ ] **Step 1: Write failing tests for `call_summary_llm`**

Append these imports:

```python
from matmaster.context.compaction import call_summary_llm
from matmaster.types.messages import LLMResponse
```

Append this fake provider and tests:

```python
class RecordingProvider:
    def __init__(self, content: str | None = "summary") -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return LLMResponse(content=self.content, finish_reason="stop")


@pytest.mark.asyncio
async def test_call_summary_llm_uses_real_messages_tools_and_tool_choice_none() -> None:
    provider = RecordingProvider(content="structured summary")
    full_messages = [
        SystemMessage(content="main system"),
        UserMessage(content="old request"),
        AssistantMessage(content="old answer"),
    ]
    tools = [{"type": "function", "function": {"name": "paper_search"}}]

    summary = await call_summary_llm(
        llm_provider=provider,
        system_prompt="main system",
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        tool_definitions=tools,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert summary == "structured summary"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["tools"] is tools
    assert call["tool_choice"] == "none"
    roles = [msg["role"] for msg in call["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert call["messages"][0]["content"] == "main system"
    assert "<compact_request>" in call["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_call_summary_llm_raises_on_empty_response() -> None:
    provider = RecordingProvider(content="  ")
    full_messages = [SystemMessage(content="sys"), UserMessage(content="old")]

    with pytest.raises(ValueError, match="Summary LLM returned empty content"):
        await call_summary_llm(
            llm_provider=provider,
            system_prompt="sys",
            full_messages=full_messages,
            phase="runtime",
            turn_input=None,
            tool_definitions=None,
            context_limit=20_000,
            reserved_summary_tokens=1_000,
        )
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py -q
```

Expected: import error for `call_summary_llm`.

- [ ] **Step 3: Implement `call_summary_llm`**

Add imports:

```python
from matmaster.types.message_normalization import (
    canonicalize_messages_for_provider,
    normalize_and_validate_openai_messages,
)
```

Add this function near the bottom of `matmaster/context/compaction.py`:

```python
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
        tool_definitions=tool_definitions,
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
    if not response.content or not response.content.strip():
        raise ValueError("Summary LLM returned empty content")
    return response.content
```

- [ ] **Step 4: Run tests to verify green**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_summary_caller.py
git commit -m "feat: call main llm for compaction summaries"
```

---

### Task 4: Extend Provider `chat()` Tool Choice

**Files:**
- Modify: `matmaster/types/llm_provider.py`
- Modify: `matmaster/providers/openai_provider.py`
- Modify: `matmaster/providers/bedrock_provider.py`
- Modify: `tests/matmaster/providers/test_openai_provider.py`
- Modify: `tests/matmaster/providers/test_bedrock_provider.py`

- [ ] **Step 1: Write failing provider protocol tests**

Append this test to `tests/matmaster/providers/test_openai_provider.py::TestChatContent`:

```python
    async def test_chat_forwards_tool_choice(self) -> None:
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            content="summary"
        )
        provider._client = mock_client

        result = await provider.chat(
            [{"role": "user", "content": "Summarize"}],
            tools=[{"type": "function", "function": {"name": "paper_search"}}],
            tool_choice="none",
        )

        assert result.content == "summary"
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["tool_choice"] == "none"
        assert kwargs["tools"] == [
            {"type": "function", "function": {"name": "paper_search"}}
        ]
```

Append these imports and tests to `tests/matmaster/providers/test_bedrock_provider.py`:

```python
from unittest.mock import MagicMock

from matmaster.providers.bedrock_provider import BedrockProvider
```

```python
async def test_bedrock_chat_rejects_unsupported_tool_choice() -> None:
    provider = BedrockProvider(model_id="m1", region="us-west-2")

    with pytest.raises(NotImplementedError, match="tool_choice='auto'"):
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            tools=[],
            tool_choice="auto",
        )


async def test_bedrock_chat_none_tool_choice_preserves_tools(monkeypatch) -> None:
    provider = BedrockProvider(model_id="m1", region="us-west-2")
    captured = {}

    def fake_converse(**kwargs):
        captured.update(kwargs)
        return {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
        }

    client = MagicMock()
    client.converse.side_effect = fake_converse
    provider._client = client

    result = await provider.chat(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "paper_search",
                    "description": "Search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice="none",
    )

    assert result.content == "ok"
    assert "toolConfig" in captured
    assert "toolChoice" not in captured.get("toolConfig", {})
```

- [ ] **Step 2: Run targeted tests to verify red**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_openai_provider.py::TestChatContent::test_chat_forwards_tool_choice \
  tests/matmaster/providers/test_bedrock_provider.py::test_bedrock_chat_rejects_unsupported_tool_choice \
  tests/matmaster/providers/test_bedrock_provider.py::test_bedrock_chat_none_tool_choice_preserves_tools \
  -q
```

Expected: failures from provider `chat()` methods not accepting `tool_choice` yet.

- [ ] **Step 3: Update protocol and providers**

Modify `matmaster/types/llm_provider.py`:

```python
async def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    tool_choice: str | dict | None = None,
) -> LLMResponse: ...
```

Modify `matmaster/providers/openai_provider.py::OpenAIProvider.chat` signature:

```python
async def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    tool_choice: str | dict | None = None,
) -> LLMResponse:
```

Add after the existing `if tools:` block:

```python
if tool_choice is not None:
    kwargs["tool_choice"] = tool_choice
```

Modify `matmaster/providers/bedrock_provider.py::BedrockProvider.chat` signature:

```python
async def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    tool_choice: str | dict | None = None,
) -> LLMResponse:
```

Add before `_converse_kwargs` is called:

```python
if tool_choice is not None and tool_choice != "none":
    raise NotImplementedError(
        f"bedrock_provider does not yet support tool_choice={tool_choice!r}"
    )
```

Do not alter `_converse_kwargs(messages, tools)`: `tool_choice="none"` intentionally preserves the `tools` field and does not set Bedrock `toolChoice`.

- [ ] **Step 4: Run provider-related tests**

Run:

```bash
uv run pytest tests/matmaster/context/test_summary_caller.py tests/matmaster/providers -q
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/llm_provider.py matmaster/providers/openai_provider.py matmaster/providers/bedrock_provider.py tests
git commit -m "feat: support tool choice for summary calls"
```

---

### Task 5: Split `ContextCompactor` Application Paths

**Files:**
- Modify: `matmaster/context/compaction.py`
- Modify: `tests/matmaster/context/test_compaction.py`
- Modify: `tests/matmaster/core/test_context_compactor.py`

- [ ] **Step 1: Write failing tests for `apply_summary` and `apply_fallback`**

In `tests/matmaster/context/test_compaction.py`, update the test provider helpers so `ContextCompactor` construction no longer passes a provider. Add tests equivalent to:

```python
@pytest.mark.asyncio
async def test_apply_summary_replaces_messages_and_returns_durable_snapshot() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
    ]
    plan = CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase="runtime",
        trigger_tokens=999,
        turn=3,
    )

    result = await compactor.apply_summary(plan, messages, "Summary text.")

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], UserMessage)
    assert "<compacted_history>" in (messages[1].content or "")
    assert result.strategy == "summary"
    assert result.durability == "durable"
    assert result.base_snapshot is not None
    assert result.checkpoint_covered_until_event_id == 9


@pytest.mark.asyncio
async def test_apply_fallback_selects_tool_safe_tail() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="run"),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="a", name="tool", arguments={}),
                ToolCallData(id="b", name="tool", arguments={}),
            ],
        ),
        ToolMessage(content="A", tool_call_id="a", tool_name="tool"),
        ToolMessage(content="B", tool_call_id="b", tool_name="tool"),
        AssistantMessage(content="done"),
    ]
    plan = CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase="runtime",
        trigger_tokens=999,
        turn=3,
    )

    result = await compactor.apply_fallback(
        plan,
        messages,
        failure_reason="summary failed",
    )

    assert result.strategy == "sliding_window"
    assert result.durability == "ephemeral"
    assert result.failure_reason == "summary failed"
    assert result.base_snapshot is None
    normalize_and_validate_openai_messages(messages)
```

Add an all-orphan fallback test:

```python
@pytest.mark.asyncio
async def test_apply_fallback_raises_and_does_not_mutate_all_orphan_tail() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        ToolMessage(content="orphan", tool_call_id="missing", tool_name="tool"),
    ]
    original = list(messages)
    plan = CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase="runtime",
        trigger_tokens=999,
        turn=3,
    )

    with pytest.raises(ValueError, match="runtime fallback produced empty tail"):
        await compactor.apply_fallback(plan, messages, failure_reason="summary failed")

    assert messages == original
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run pytest tests/matmaster/context/test_compaction.py tests/matmaster/core/test_context_compactor.py -q
```

Expected: failures because `apply_summary` and `apply_fallback` do not exist and constructor still requires `summary_provider`.

- [ ] **Step 3: Refactor `ContextCompactor.__init__`**

Change the constructor signature in `matmaster/context/compaction.py` to:

```python
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
```

Delete:

```python
self._summary_provider = summary_provider
```

Delete the `summary_provider` property.

- [ ] **Step 4: Add shared result finalization helper**

Add this private helper method inside `ContextCompactor`:

```python
def _finalize_result_state(self, plan: CompactionPlan, messages: list[Message]) -> None:
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
```

- [ ] **Step 5: Implement `apply_summary`**

Replace the success path from `apply_compaction_plan` with:

```python
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
    current_split = (
        plan.phase == "preflight"
        and turn_input is not None
        and turn_input.has_effective_input()
        and len(messages) >= 3
        and isinstance(messages[-1], UserMessage)
        and bool(messages[1:-1])
    )
    intent = (
        ContextAssemblyIntent.PREFLIGHT_COMPACTION
        if current_split
        else ContextAssemblyIntent.RUNTIME_COMPACTION
    )
    covered_until_event_id = None
    if intent == ContextAssemblyIntent.RUNTIME_COMPACTION:
        if self._runtime_covered_until_provider is None:
            raise ValueError("runtime compaction requires runtime_covered_until_provider")
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
    checkpoint_user_msg = assembly.user_turn_context.to_message(ContextView.CHECKPOINT)
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
        base_snapshot=[checkpoint_user_msg.model_dump(mode="json")],
        checkpoint_covered_until_event_id=assembly.covered_until_event_id,
        user_instructions_text=assembly.user_instructions_text,
        user_instructions_hash=assembly.user_instructions_hash,
    )
```

- [ ] **Step 6: Implement `apply_fallback`**

Add:

```python
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
        base_snapshot=None,
    )
```

- [ ] **Step 7: Delete legacy methods**

Delete these methods from `ContextCompactor`:

```python
preflight_if_needed
compact_if_needed
apply_compaction_plan
_truncate_tool_results
_summarize
```

The public planning methods `plan_preflight_compaction` and `plan_runtime_compaction` remain.

- [ ] **Step 8: Update tests and run**

Update all `ContextCompactor(...)` construction sites in tests to remove `summary_provider=...`. Update old `apply_compaction_plan(...)` success assertions to call `apply_summary(..., "Summary text.")`. Update old runtime-failure tests to call `apply_fallback(...)` directly or move orchestration failure routing to Task 6 tests.

Run:

```bash
uv run pytest tests/matmaster/context/test_compaction.py tests/matmaster/core/test_context_compactor.py tests/matmaster/context/test_summary_caller.py -q
```

Expected: tests pass or fail only in orchestration sites not yet updated.

- [ ] **Step 9: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_compaction.py tests/matmaster/core/test_context_compactor.py tests/matmaster/context/test_summary_caller.py
git commit -m "refactor: split compaction summary and fallback paths"
```

---

### Task 6: Orchestrate Inline Summary Calls in Compaction Dispatch

**Files:**
- Modify: `matmaster/core/agent_compaction.py`
- Create: `tests/matmaster/core/test_agent_compaction.py`
- Modify: tests that use `run_preflight_compaction_if_needed` or `run_runtime_compaction_if_needed`

- [ ] **Step 1: Write failing orchestration tests**

Create `tests/matmaster/core/test_agent_compaction.py`:

```python
from __future__ import annotations

import pytest

from matmaster.core.agent_compaction import run_compaction_plan
from matmaster.core.kernel_items import _KernelState
from matmaster.context.compaction import CompactionPlan
from matmaster.types.events import CompactionEvent
from matmaster.types.messages import SystemMessage, UserMessage
from matmaster.types.runtime import AgentRuntimeSpec, CompactionConfig
from matmaster.types.runtime_ports import KernelRuntimePorts


class Provider:
    def __init__(self, content: str | Exception = "summary") -> None:
        self.content = content
        self.calls = []

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.calls.append((messages, tools, tool_choice))
        if isinstance(self.content, Exception):
            raise self.content
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content=self.content, finish_reason="stop")


class Compactor:
    def __init__(self) -> None:
        self.summary_calls = []
        self.fallback_calls = []

    async def apply_summary(self, plan, messages, summary, *, turn_input=None):
        self.summary_calls.append((plan, list(messages), summary, turn_input))
        from matmaster.context.compaction import CompactionResult

        messages[:] = [messages[0], UserMessage(content="<compacted_history>summary</compacted_history>")]
        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="summary",
            durability="durable",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=0,
            failure_reason=None,
            base_snapshot=[messages[1].model_dump(mode="json")],
        )

    async def apply_fallback(self, plan, messages, *, failure_reason):
        self.fallback_calls.append((plan, failure_reason))
        from matmaster.context.compaction import CompactionResult

        return CompactionResult(
            compaction_id=plan.compaction_id,
            compaction_count=plan.compaction_count,
            phase=plan.phase,
            strategy="sliding_window",
            durability="ephemeral",
            trigger_tokens=plan.trigger_tokens,
            retained_turns=len(messages) - 1,
            failure_reason=failure_reason,
            base_snapshot=None,
        )


def _spec(provider: Provider, compactor: Compactor) -> AgentRuntimeSpec:
    return AgentRuntimeSpec.model_construct(
        llm_provider=provider,
        max_turns=10,
        runtime_ports=KernelRuntimePorts(),
        compaction=CompactionConfig(context_limit=20_000, reserved_summary_tokens=1_000),
        system_prompt="sys",
        compactor=compactor,
        system_prompt_builder=object(),
        meta={},
    )


def _plan(phase: str) -> CompactionPlan:
    return CompactionPlan(
        compaction_id="root:1",
        compaction_count=1,
        phase=phase,
        trigger_tokens=123,
        turn=2,
    )


@pytest.mark.asyncio
async def test_run_compaction_plan_summary_success_calls_apply_summary() -> None:
    provider = Provider("summary text")
    compactor = Compactor()
    state = _KernelState(messages=[SystemMessage(content="sys"), UserMessage(content="old")])
    tools = [{"type": "function", "function": {"name": "tool"}}]

    events = [
        item.event
        async for item in run_compaction_plan(
            spec=_spec(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=tools,
        )
    ]

    assert [event.status for event in events if isinstance(event, CompactionEvent)] == [
        "running",
        "complete",
    ]
    assert provider.calls[0][1] is tools
    assert provider.calls[0][2] == "none"
    assert compactor.summary_calls[0][2] == "summary text"
    assert compactor.fallback_calls == []


@pytest.mark.asyncio
async def test_run_compaction_plan_preflight_summary_failure_raises() -> None:
    provider = Provider(RuntimeError("network down"))
    compactor = Compactor()
    state = _KernelState(messages=[SystemMessage(content="sys"), UserMessage(content="old")])

    with pytest.raises(RuntimeError, match="network down"):
        async for _item in run_compaction_plan(
            spec=_spec(provider, compactor),
            state=state,
            plan=_plan("preflight"),
            checkpoint_sink=None,
            tool_definitions=None,
        ):
            pass

    assert compactor.fallback_calls == []


@pytest.mark.asyncio
async def test_run_compaction_plan_runtime_summary_failure_uses_fallback() -> None:
    provider = Provider(RuntimeError("network down"))
    compactor = Compactor()
    state = _KernelState(messages=[SystemMessage(content="sys"), UserMessage(content="old")])

    events = [
        item.event
        async for item in run_compaction_plan(
            spec=_spec(provider, compactor),
            state=state,
            plan=_plan("runtime"),
            checkpoint_sink=None,
            tool_definitions=None,
        )
    ]

    assert compactor.summary_calls == []
    assert compactor.fallback_calls[0][1] == "network down"
    assert events[-1].strategy == "sliding_window"
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_compaction.py -q
```

Expected: `run_compaction_plan` does not accept `tool_definitions` and still calls `apply_compaction_plan`.

- [ ] **Step 3: Update compaction dispatch**

Modify imports in `matmaster/core/agent_compaction.py`:

```python
from matmaster.context.compaction import call_summary_llm
```

Change `run_compaction_plan` signature:

```python
async def run_compaction_plan(
    *,
    spec: AgentRuntimeSpec,
    state: _KernelState,
    plan: Any,
    checkpoint_sink: Any,
    turn_input: TurnInput | None = None,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> AsyncIterator[_KernelItem]:
```

Replace the `result = await spec.compactor.apply_compaction_plan(...)` block with:

```python
try:
    summary = await call_summary_llm(
        llm_provider=spec.llm_provider,
        system_prompt=spec.system_prompt,
        full_messages=state.messages,
        phase=plan.phase,
        turn_input=turn_input,
        tool_definitions=tool_definitions,
        context_limit=spec.compaction.context_limit,
        reserved_summary_tokens=spec.compaction.reserved_summary_tokens,
    )
    result = await spec.compactor.apply_summary(
        plan,
        state.messages,
        summary,
        turn_input=turn_input,
    )
except Exception as exc:
    if plan.phase == "preflight":
        logger.warning("Preflight compaction summary failed; aborting", exc_info=True)
        raise
    logger.warning(
        "Compaction #%d summary failed; falling back",
        plan.compaction_count,
        exc_info=True,
    )
    result = await spec.compactor.apply_fallback(
        plan,
        state.messages,
        failure_reason=str(exc),
    )
```

Update `run_preflight_compaction_if_needed` signature:

```python
tool_definitions: list[dict[str, Any]] | None = None,
```

and pass it into `run_compaction_plan`.

Update `run_runtime_compaction_if_needed` signature the same way and pass it into `run_compaction_plan`.

Delete the legacy `else` branches that call `preflight_if_needed` and `compact_if_needed`; the real `ContextCompactor` now always exposes plan methods, and test doubles should be updated to the modern path.

- [ ] **Step 4: Run orchestration tests**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_compaction.py tests/matmaster/context/test_compaction.py tests/matmaster/core/test_context_compactor.py -q
```

Expected: tests pass after updating legacy test doubles.

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/agent_compaction.py tests/matmaster/core/test_agent_compaction.py tests/matmaster/context/test_compaction.py tests/matmaster/core/test_context_compactor.py
git commit -m "refactor: orchestrate inline compaction summaries"
```

---

### Task 7: Resolve Tool Definitions Before Compaction and Main Calls

**Files:**
- Modify: `matmaster/core/agent.py`
- Create: `tests/matmaster/core/test_kernel_helpers.py`
- Modify: `tests/matmaster/core/test_agent_kernel_stream.py`

- [ ] **Step 1: Write failing tests for `ensure_tool_definitions`**

Create `tests/matmaster/core/test_kernel_helpers.py`:

```python
from __future__ import annotations

from matmaster.core.agent import ensure_tool_definitions
from matmaster.core.kernel_items import _KernelState
from matmaster.types.messages import SystemMessage
from matmaster.types.runtime import AgentRuntimeSpec


class Catalog:
    def __init__(self) -> None:
        self.version = 1
        self.calls = []

    def build_definitions(self, desc_ctx):
        self.calls.append(desc_ctx)
        return [{"type": "function", "function": {"name": f"tool_v{self.version}"}}]


def _spec(catalog=None, topology=None):
    return AgentRuntimeSpec.model_construct(
        tool_catalog=catalog,
        runtime_topology=topology,
        system_prompt_builder=object(),
    )


def test_ensure_tool_definitions_returns_none_without_catalog() -> None:
    state = _KernelState(messages=[SystemMessage(content="sys")])

    assert ensure_tool_definitions(_spec(), state) is None
    assert state.cached_tool_definitions is None


def test_ensure_tool_definitions_caches_same_list_object() -> None:
    catalog = Catalog()
    state = _KernelState(messages=[SystemMessage(content="sys")])
    spec = _spec(catalog)

    first = ensure_tool_definitions(spec, state)
    second = ensure_tool_definitions(spec, state)

    assert first is second
    assert catalog.calls == [None]


def test_ensure_tool_definitions_rebuilds_on_catalog_version_change() -> None:
    catalog = Catalog()
    state = _KernelState(messages=[SystemMessage(content="sys")])
    spec = _spec(catalog)

    first = ensure_tool_definitions(spec, state)
    catalog.version = 2
    second = ensure_tool_definitions(spec, state)

    assert first is not second
    assert second == [{"type": "function", "function": {"name": "tool_v2"}}]
    assert len(catalog.calls) == 2
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
uv run pytest tests/matmaster/core/test_kernel_helpers.py -q
```

Expected: import error for `ensure_tool_definitions`.

- [ ] **Step 3: Add helper**

Add this module-level function to `matmaster/core/agent.py`, near `_TERMINAL_REASON_TO_STATUS`:

```python
def ensure_tool_definitions(
    spec: AgentRuntimeSpec,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    """Resolve and cache tool definitions on kernel state."""
    if spec.tool_catalog is None:
        return None

    if spec.tool_catalog.version != state.last_catalog_version:
        state.cached_tool_definitions = None
        state.last_catalog_version = spec.tool_catalog.version

    if state.cached_tool_definitions is None:
        from matmaster.types.tool_desc_ctx import ToolDescriptionContext

        desc_ctx = None
        if spec.runtime_topology is not None:
            desc_ctx = ToolDescriptionContext(
                session_kind=spec.runtime_topology.session_kind,
                workspace_root=spec.runtime_topology.workspace_root,
                topology=spec.runtime_topology,
            )
        state.cached_tool_definitions = spec.tool_catalog.build_definitions(desc_ctx)
    return state.cached_tool_definitions
```

- [ ] **Step 4: Wire helper into `_run_items`**

Before preflight compaction:

```python
tool_definitions = ensure_tool_definitions(spec, state)
async for item in run_preflight_compaction_if_needed(
    spec=spec,
    state=state,
    history=history,
    turn_input=turn_input,
    checkpoint_sink=checkpoint_sink,
    tool_definitions=tool_definitions,
):
    yield item
```

Inside the `while state.turn < spec.max_turns` loop, immediately after `state.turn += 1`:

```python
tool_definitions = ensure_tool_definitions(spec, state)
async for item in run_runtime_compaction_if_needed(
    spec=spec,
    state=state,
    turn_usage=turn_usage,
    checkpoint_sink=checkpoint_sink,
    tool_definitions=tool_definitions,
):
    yield item
```

Delete the inline tool-definition resolution block and replace:

```python
tool_defs = state.cached_tool_definitions
```

with:

```python
tool_defs = tool_definitions
```

- [ ] **Step 5: Run helper and existing kernel cache tests**

Run:

```bash
uv run pytest tests/matmaster/core/test_kernel_helpers.py tests/matmaster/core/test_agent_kernel_stream.py::TestAgentKernelStream::test_catalog_version_invalidates_tool_definitions -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_kernel_helpers.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "refactor: resolve tools before compaction"
```

---

### Task 8: Simplify Runtime Assembly and Provider Lifecycle

**Files:**
- Modify: `matmaster/core/runtime_context_assembly.py`
- Modify: `matmaster/core/agent.py`
- Modify: `matmaster/types/runtime.py`
- Modify: `config/llm_config.yaml`
- Modify: `tests/matmaster/core/test_exp.py`
- Modify: `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- Modify: tests that reference `compaction_llm` or `summary_provider`

- [ ] **Step 1: Write or update failing cleanup tests**

Update any tests that assert `runtime.spec.compactor._summary_provider is runtime.spec.llm_provider`. Replace them with:

```python
assert not hasattr(runtime.spec.compactor, "_summary_provider")
```

Add a runtime config test if `tests/matmaster/types/test_runtime.py` exists:

```python
def test_compaction_config_ignores_removed_compaction_llm_field() -> None:
    config = CompactionConfig.model_validate({"compaction_llm": "compaction"})

    assert not hasattr(config, "compaction_llm")
```

Run:

```bash
uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/types/test_runtime.py -q
```

Expected: failures until code cleanup is complete.

- [ ] **Step 2: Remove runtime assembly summary provider construction**

In `matmaster/core/runtime_context_assembly.py`, delete:

```python
summary_provider = spec.llm_provider
if spec.compaction.compaction_llm:
    ...
```

Remove this kwarg from `ContextCompactor(...)`:

```python
summary_provider=summary_provider,
```

- [ ] **Step 3: Collapse `AgentKernel.run_stream` provider lifecycle**

In `matmaster/core/agent.py`, replace the nested `_summary_provider` logic with a single provider context:

```python
async with spec.llm_provider:
    last_reason: str | None = None

    async def _consume_and_yield():
        nonlocal last_reason
        async for item in self._run_items(spec, task, history, cancel_token):
            ...

    if spec.hook_executor is not None:
        await spec.hook_executor.emit(...)

    try:
        async for event in _consume_and_yield():
            yield event
    except BaseException as exc:
        ...
    finally:
        ...
```

Keep the existing hook emission and terminal-event logic unchanged; only remove `_summary_provider` lookup and nested context manager.

- [ ] **Step 4: Remove config field and alias**

In `matmaster/types/runtime.py`, delete:

```python
compaction_llm: str | None = None
```

In `config/llm_config.yaml`, remove the top-level `compaction:` alias section. Do not alter unrelated model aliases.

- [ ] **Step 5: Update tests and run**

Find old references:

```bash
rg -n "summary_provider|compaction_llm|SUMMARY_SYSTEM_PROMPT|apply_compaction_plan|compact_if_needed|preflight_if_needed|_truncate_tool_results|_summarize" tests matmaster config
```

Update tests so:

- `ContextCompactor(...)` no longer receives `summary_provider`.
- Summary success paths use `apply_summary`.
- Summary failure routing is tested through `run_compaction_plan`.
- Legacy direct methods `compact_if_needed` and `preflight_if_needed` are not used.
- Old docstrings mentioning `_run_compaction_plan` are corrected to `run_compaction_plan`.

Run:

```bash
uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/integration/test_history_checkpoint_recovery.py tests/matmaster/context/test_compaction.py tests/matmaster/core/test_context_compactor.py -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/runtime_context_assembly.py matmaster/core/agent.py matmaster/types/runtime.py config/llm_config.yaml tests
git commit -m "refactor: remove dedicated compaction provider"
```

---

### Task 9: Add Cache-Prefix Value-Proof Integration Tests

**Files:**
- Create: `tests/matmaster/integration/test_summary_cache_prefix.py`
- Modify: `matmaster/core/agent.py` or compaction orchestration only if tests expose object-identity drift

- [ ] **Step 1: Add integration test scaffolding**

Create `tests/matmaster/integration/test_summary_cache_prefix.py`:

```python
from __future__ import annotations

import json

import pytest

from matmaster.context.compaction import (
    SUMMARY_USER_REQUEST_TEMPLATE,
    prepare_messages_for_summary_call,
)
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.message_normalization import canonicalize_messages_for_provider
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _api_bytes(messages) -> bytes:
    payload = [m.to_api_dict() for m in canonicalize_messages_for_provider(messages)]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
```

- [ ] **Step 2: Add cache-prefix identity test**

Append:

```python
def test_summary_common_case_shares_main_conversation_prefix() -> None:
    history = [
        SystemMessage(content="sys"),
        UserMessage(content="question"),
        AssistantMessage(content="answer"),
    ]
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    tools = [{"type": "function", "function": {"name": "paper_search"}}]

    prep = prepare_messages_for_summary_call(
        full_messages=history,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        tool_definitions=tools,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == history
    assert all(left is right for left, right in zip(prep.messages, history))
    main_prefix = _api_bytes(history)
    summary_prefix = _api_bytes(prep.messages)
    assert summary_prefix == main_prefix
```

- [ ] **Step 3: Add preflight current-input split test**

Append:

```python
def test_preflight_summary_prefix_excludes_current_instruction_tail() -> None:
    turn_input = TurnInput.from_values(user_text="new instruction")
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
        UserMessage(content="new instruction"),
    ]
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)

    prep = prepare_messages_for_summary_call(
        full_messages=messages,
        phase="preflight",
        turn_input=turn_input,
        compact_request=compact_request,
        tool_definitions=None,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == messages[:-1]
    assert _api_bytes(prep.messages) == _api_bytes(messages[:-1])
```

- [ ] **Step 4: Add oversized parallel tool-result invariance test**

Append:

```python
def test_parallel_oversized_tool_results_truncate_minimum_needed_without_mutation() -> None:
    tool_a = ToolMessage(content="A" * 14_000, tool_call_id="a", tool_name="paper_search")
    tool_b = ToolMessage(content="B" * 10_000, tool_call_id="b", tool_name="paper_search")
    tool_c = ToolMessage(content="C" * 1_000, tool_call_id="c", tool_name="paper_search")
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="search"),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="a", name="paper_search", arguments={}),
                ToolCallData(id="b", name="paper_search", arguments={}),
                ToolCallData(id="c", name="paper_search", arguments={}),
            ],
        ),
        tool_a,
        tool_b,
        tool_c,
    ]
    original_contents = [tool_a.content, tool_b.content, tool_c.content]
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)

    prep = prepare_messages_for_summary_call(
        full_messages=messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        tool_definitions=None,
        context_limit=9_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=500,
    )

    assert prep.prepared_tokens <= prep.message_budget
    assert prep.truncated_tool_call_ids[0] == "a"
    assert prep.messages[3].tool_call_id == "a"
    assert prep.messages[3].tool_name == "paper_search"
    assert prep.messages[5] is tool_c
    assert [tool_a.content, tool_b.content, tool_c.content] == original_contents
```

- [ ] **Step 5: Run integration tests**

Run:

```bash
uv run pytest tests/matmaster/integration/test_summary_cache_prefix.py -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/matmaster/integration/test_summary_cache_prefix.py
git commit -m "test: prove compaction cache prefix stability"
```

---

### Task 10: Align Remaining Tests and Documentation References

**Files:**
- Modify: `tests/matmaster/devshell/test_compaction_via_devshell.py`
- Modify: `tests/test_chat_events_history_checkpoint.py`
- Modify: `tests/test_stream_replay_skill_hit.py`
- Modify: `tests/matmaster/services/test_model_history_restore_service.py` only if imports drift
- Modify: docs or comments that mention the old summary provider behavior

- [ ] **Step 1: Search for stale references**

Run:

```bash
rg -n "summary_provider|compaction_llm|SUMMARY_SYSTEM_PROMPT|apply_compaction_plan|compact_if_needed|preflight_if_needed|_truncate_tool_results|_summarize|_run_compaction_plan" .
```

Expected remaining references should either be in this plan/spec document or actively edited in this task.

- [ ] **Step 2: Update devshell compaction tests**

In `tests/matmaster/devshell/test_compaction_via_devshell.py`:

- Remove `summary_provider=` from `ContextCompactor` construction.
- Replace tests named like `test_summary_provider_receives_old_messages` with assertions that the main provider receives a summary call through `run_compaction_plan`.
- Update fake provider `chat` signatures to accept:

```python
async def chat(self, messages, tools=None, *, tool_choice=None):
```

and record `tools` / `tool_choice` when tests assert summary-call behavior.

- [ ] **Step 3: Update history checkpoint tests**

In `tests/test_chat_events_history_checkpoint.py` and `tests/matmaster/integration/test_history_checkpoint_recovery.py`:

- Replace docstring mentions of `_run_compaction_plan` with `run_compaction_plan`.
- If tests trigger compaction through the kernel, assert that checkpoint payload behavior remains unchanged.
- If tests directly instantiate `ContextCompactor`, use the new constructor and `apply_summary`.

- [ ] **Step 4: Run broad compaction-related tests**

Run:

```bash
uv run pytest \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/context/test_summary_caller.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/core/test_kernel_helpers.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/integration/test_summary_cache_prefix.py \
  tests/test_chat_events_history_checkpoint.py \
  tests/test_stream_replay_skill_hit.py \
  -q
```

Expected: all listed tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests docs matmaster
git commit -m "test: align compaction suites with inline summaries"
```

---

### Task 11: Full Verification and Cleanup

**Files:**
- Review all modified files
- No new source files expected beyond tests listed above

- [ ] **Step 1: Run final stale-reference scan**

Run:

```bash
rg -n "summary_provider|compaction_llm|SUMMARY_SYSTEM_PROMPT|apply_compaction_plan|compact_if_needed|preflight_if_needed|_truncate_tool_results|_summarize" matmaster tests config
```

Expected: no references, except local variable names inside migration comments if a test explicitly documents deleted behavior. Prefer deleting or rewriting stale comments rather than keeping exceptions.

- [ ] **Step 2: Run formatting and targeted tests**

Run:

```bash
uv run pre-commit run --all-files
```

Expected: all hooks pass. If hooks modify files, inspect the diff and rerun the same command.

Run:

```bash
uv run pytest \
  tests/matmaster/context/test_summary_caller.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/core/test_kernel_helpers.py \
  tests/matmaster/integration/test_summary_cache_prefix.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 3: Run a broader safety slice**

Run:

```bash
uv run pytest tests/matmaster/core tests/matmaster/context tests/matmaster/integration/test_history_checkpoint_recovery.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Inspect diff for scope control**

Run:

```bash
git diff --stat
git diff -- matmaster/context/compaction.py matmaster/core/agent.py matmaster/core/agent_compaction.py
```

Confirm:

- `ContextAssembler.assemble_compaction` call shape is unchanged except that summary text is supplied by caller.
- `CompactionPlan` and `CompactionResult` fields are unchanged.
- `CompactionEvent` schema is unchanged.
- Checkpoint sink payload remains the existing `history_checkpoint.v1` payload.
- `run_meta` has no new callbacks, service objects, or capability ports.
- `RuntimePorts` has no new broad `dict[str, Any]` or `extra`-style field.
- Only this repository is modified.

- [ ] **Step 5: Commit final fixes if needed**

If Step 2 or Step 3 caused formatting/test-only edits:

```bash
git add matmaster tests config
git commit -m "chore: finalize inline compaction cleanup"
```

If no files changed after previous commits, do not create an empty commit.

---

## Self-Review

**Spec coverage:** This plan maps every spec section to an executable task:

- Core algorithm: Tasks 2 and 3 add `prepare_messages_for_summary_call` and `call_summary_llm`.
- Tool-result preprocessing: Tasks 1, 2, and 9 cover targeted truncation, structured markers, budget accounting, and mutation invariants.
- `ContextCompactor` refactor: Task 5 splits summary and fallback application and deletes legacy methods.
- `run_compaction_plan` orchestration: Task 6 moves summary-call ownership into dispatch and preserves preflight/runtime failure semantics.
- Prompt template: Task 2 replaces `SUMMARY_SYSTEM_PROMPT` with the Chinese sentinel-wrapped request and reserved English placeholder.
- Provider wiring: Task 4 extends `LLMProvider.chat`, OpenAI provider, and Bedrock provider.
- Tool schema cache invariant: Task 7 resolves tool definitions before compaction and main calls; Task 9 proves prefix identity.
- Deletion and cleanup: Tasks 8, 10, and 11 remove dead config, stale tests, stale comments, and old code paths.
- Out-of-scope items are not implemented: no checkpoint schema change, no event schema change, no persistent tool-result replacement state, no language auto-detection.

**Placeholder scan:** No implementation step leaves unresolved work for the executor to invent. Each task includes concrete paths, commands, expected outcomes, and code shape for the main changes.

**Type consistency:** The plan consistently uses `SummaryInputPreparation`, `prepare_messages_for_summary_call`, `call_summary_llm`, `apply_summary`, `apply_fallback`, `ensure_tool_definitions`, `tool_choice`, and existing message classes from `matmaster.types.messages`.
