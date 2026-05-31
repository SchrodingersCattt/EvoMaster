# Anthropic Cache Point Allocation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current automatic "system + last two users" prompt-cache behavior with a deterministic four-slot Anthropic cache-point allocation strategy: system prompt, latest user message, latest completed tool-result group tail, and one flexible high-value point.

**Architecture:** Keep prompt-cache mutation at the OpenAI provider request boundary. Extend config and factory objects only enough to carry strategy options, then move selection into small provider-local helpers that compute cache targets before `_prepare_messages()` injects `cache_control` into a deep copy.

**Tech Stack:** Python 3.13, Pydantic config models, OpenAI-compatible chat message payloads, pytest, existing `uv run pytest` test workflow.

---

## File Structure

- Modify `matmaster/config/llm.py`
  - Extend `PromptCacheConfig` with explicit cache-point strategy fields.
  - Keep `cache_control()` as the single source of Anthropic payload shape.
  - Do not reintroduce request-level `extra_body.cache_control`.

- Modify `matmaster/providers/llm_factory.py`
  - Pass the new `PromptCacheConfig` fields into `AnthropicPromptCacheOptions`.
  - Preserve the current OpenAI-only behavior; BedrockProvider still does not receive prompt-cache options through this factory path.

- Modify `matmaster/providers/openai_provider.py`
  - Extend `AnthropicPromptCacheOptions`.
  - Add provider-local helper functions for target selection and injection.
  - Refactor `_prepare_messages()` so selection and mutation are separate.

- Modify `config/llm_config.yaml`
  - Enable the new strategy for both `opus` and `opus_global`.
  - Preserve current `ttl: "5m"` unless product requirements change.

- Modify `tests/matmaster/config/test_llm.py`
  - Cover new config defaults and parsing.

- Modify `tests/matmaster/providers/test_llm_factory.py`
  - Cover factory propagation for `opus` and `opus_global`.

- Modify `tests/matmaster/providers/test_openai_provider.py`
  - Replace tests that assert "last two user messages" with tests for the approved four-slot allocation.

- Optionally modify `tests/matmaster/config/test_loader.py`
  - If it already asserts project config prompt-cache behavior, update it to assert the new strategy is enabled for `opus` and `opus_global`.

---

## Design Contract

The selected allocation order is:

1. `system_prompt`
2. `latest user`
3. `latest completed tool result group tail`
4. `flexible high-value point`

Rules:

- Never exceed `max_breakpoints`, default `4`.
- Preserve input messages; `_prepare_messages()` must deep-copy before mutation.
- Fixed slots win over the flexible slot.
- The latest completed tool-result group tail means:
  - Find the latest assistant message with `tool_calls`.
  - Collect the expected `tool_call_id` values from that assistant message.
  - Walk following messages until all expected `role="tool"` results are present.
  - If all are present, the group tail is the last matching tool message in that completed group.
  - If not all are present, skip the tool-result breakpoint for that request.
- For the tool-result breakpoint, inject `cache_control` at the tool message top level:

```python
{
    "role": "tool",
    "tool_call_id": "call_...",
    "content": "...",
    "cache_control": {"type": "ephemeral"},
}
```

- For system/user text, preserve the existing content-block injection behavior:

```python
{
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": "...",
            "cache_control": {"type": "ephemeral"},
        }
    ],
}
```

- The flexible slot should select the largest remaining valuable candidate from unmarked user/tool messages, using simple character thresholds first. Do not add tokenization dependencies.

---

## Task 1: Extend Prompt Cache Config

**Files:**
- Modify: `matmaster/config/llm.py`
- Test: `tests/matmaster/config/test_llm.py`

- [ ] **Step 1: Write failing config tests**

Add tests under `TestPromptCacheConfig` and update `test_prompt_cache_field_from_dict`.

```python
def test_strategy_defaults(self) -> None:
    cfg = PromptCacheConfig()

    assert cfg.system_prompt_breakpoint is False
    assert cfg.automatic is False
    assert cfg.latest_user_breakpoint is True
    assert cfg.tool_result_breakpoint is False
    assert cfg.flexible_breakpoint is False
    assert cfg.max_breakpoints == 4
    assert cfg.min_flexible_chars == 1000


def test_strategy_fields_from_dict(self) -> None:
    cfg = PromptCacheConfig(
        system_prompt_breakpoint=True,
        automatic=True,
        latest_user_breakpoint=True,
        tool_result_breakpoint=True,
        flexible_breakpoint=True,
        max_breakpoints=4,
        min_flexible_chars=1200,
        ttl="5m",
    )

    assert cfg.latest_user_breakpoint is True
    assert cfg.tool_result_breakpoint is True
    assert cfg.flexible_breakpoint is True
    assert cfg.max_breakpoints == 4
    assert cfg.min_flexible_chars == 1200
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py -q
```

Expected: fails because `PromptCacheConfig` does not have the new fields.

- [ ] **Step 3: Implement config fields**

In `PromptCacheConfig`, add:

```python
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = Field(default=4, ge=1, le=4)
    min_flexible_chars: int = Field(default=1000, ge=1)
```

Keep existing fields:

```python
    provider: Literal["anthropic"] = "anthropic"
    system_prompt_breakpoint: bool = False
    automatic: bool = False
    ttl: Literal["5m", "1h"] = "5m"
```

Do not change `cache_control()` except to keep existing `ttl` behavior.

- [ ] **Step 4: Run config tests and verify pass**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py -q
```

Expected: pass.

---

## Task 2: Propagate Strategy Options Through Factory

**Files:**
- Modify: `matmaster/providers/openai_provider.py`
- Modify: `matmaster/providers/llm_factory.py`
- Test: `tests/matmaster/providers/test_llm_factory.py`

- [ ] **Step 1: Write failing factory tests**

Update `test_prompt_cache_options_passed_for_opus` and `test_prompt_cache_options_passed_for_opus_global` expected options:

```python
assert provider._prompt_cache_options == AnthropicPromptCacheOptions(
    system_prompt_breakpoint=True,
    cache_control={"type": "ephemeral"},
    automatic=True,
    latest_user_breakpoint=True,
    tool_result_breakpoint=True,
    flexible_breakpoint=True,
    max_breakpoints=4,
    min_flexible_chars=1000,
)
```

Update fixture prompt-cache config to include:

```python
"latest_user_breakpoint": True,
"tool_result_breakpoint": True,
"flexible_breakpoint": True,
"max_breakpoints": 4,
"min_flexible_chars": 1000,
```

- [ ] **Step 2: Run factory tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/providers/test_llm_factory.py -q
```

Expected: fails because `AnthropicPromptCacheOptions` and factory propagation do not include the new fields.

- [ ] **Step 3: Extend `AnthropicPromptCacheOptions`**

In `matmaster/providers/openai_provider.py`, update:

```python
@dataclass(frozen=True)
class AnthropicPromptCacheOptions:
    """Provider-local Anthropic prompt cache controls."""

    system_prompt_breakpoint: bool
    cache_control: dict[str, str]
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = 4
    min_flexible_chars: int = 1000
```

- [ ] **Step 4: Pass config fields in factory**

In `_build_anthropic_prompt_cache_options()`, construct:

```python
return AnthropicPromptCacheOptions(
    system_prompt_breakpoint=prompt_cache.system_prompt_breakpoint,
    cache_control=prompt_cache.cache_control(),
    automatic=prompt_cache.automatic,
    latest_user_breakpoint=prompt_cache.latest_user_breakpoint,
    tool_result_breakpoint=prompt_cache.tool_result_breakpoint,
    flexible_breakpoint=prompt_cache.flexible_breakpoint,
    max_breakpoints=prompt_cache.max_breakpoints,
    min_flexible_chars=prompt_cache.min_flexible_chars,
)
```

Keep the current guard:

```python
if prompt_cache is None or not prompt_cache.system_prompt_breakpoint:
    return None
```

- [ ] **Step 5: Run factory tests and verify pass**

Run:

```bash
uv run pytest tests/matmaster/providers/test_llm_factory.py -q
```

Expected: pass.

---

## Task 3: Refactor Provider Cache Target Selection

**Files:**
- Modify: `matmaster/providers/openai_provider.py`
- Test: `tests/matmaster/providers/test_openai_provider.py`

- [ ] **Step 1: Write failing provider tests for fixed slots**

Replace `test_chat_applies_automatic_user_cache_breakpoints` with a test that asserts:

- system gets cache control.
- only the latest user gets the user slot.
- a completed parallel tool-result group gets one top-level cache control on the last tool message.

Suggested test:

```python
async def test_chat_applies_semantic_cache_points(self) -> None:
    provider = self._provider(
        automatic=True,
        latest_user_breakpoint=True,
        tool_result_breakpoint=True,
        flexible_breakpoint=False,
    )
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = _make_mock_completion()
    provider._client = mock_client

    await provider.chat(
        [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "old"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "a", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "b", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
            {"role": "tool", "tool_call_id": "call_b", "content": "result b"},
            {"role": "user", "content": "current"},
        ]
    )

    sent = mock_client.chat.completions.create.await_args.kwargs["messages"]
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[1] == {"role": "user", "content": "old"}
    assert "cache_control" not in sent[3]
    assert sent[4]["cache_control"] == {"type": "ephemeral"}
    assert sent[5]["content"][0]["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Step 2: Write failing test for incomplete tool group**

```python
async def test_prompt_cache_skips_incomplete_tool_group(self) -> None:
    provider = self._provider(
        automatic=True,
        latest_user_breakpoint=True,
        tool_result_breakpoint=True,
        flexible_breakpoint=False,
    )
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = _make_mock_completion()
    provider._client = mock_client

    await provider.chat(
        [
            {"role": "system", "content": "system prompt"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "a", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "b", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "result a"},
            {"role": "user", "content": "current"},
        ]
    )

    sent = mock_client.chat.completions.create.await_args.kwargs["messages"]
    assert "cache_control" not in sent[2]
    assert sent[3]["content"][0]["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Step 3: Write failing test for max breakpoint cap**

Update `test_prompt_cache_respects_max_breakpoints` so `max_breakpoints=2` yields only system + latest user, with no tool result marked.

```python
async def test_prompt_cache_respects_max_breakpoints(self) -> None:
    provider = self._provider(
        automatic=True,
        latest_user_breakpoint=True,
        tool_result_breakpoint=True,
        flexible_breakpoint=True,
        max_breakpoints=2,
    )
    ...
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sent[3]
```

- [ ] **Step 4: Write failing test for flexible slot**

```python
async def test_prompt_cache_flexible_marks_largest_remaining_value(self) -> None:
    provider = self._provider(
        automatic=True,
        latest_user_breakpoint=True,
        tool_result_breakpoint=False,
        flexible_breakpoint=True,
        min_flexible_chars=10,
    )
    ...
    assert sent[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
```

Use a message list where `sent[1]` is an older long user message and the latest user is also marked by the fixed latest-user slot.

- [ ] **Step 5: Update local `_provider()` helper**

In `TestAnthropicPromptCacheRequestPayload._provider`, accept the new keyword args:

```python
def _provider(
    self,
    *,
    automatic: bool = False,
    latest_user_breakpoint: bool = True,
    tool_result_breakpoint: bool = False,
    flexible_breakpoint: bool = False,
    max_breakpoints: int = 4,
    min_flexible_chars: int = 1000,
) -> OpenAIProvider:
```

Pass them into `AnthropicPromptCacheOptions`.

- [ ] **Step 6: Run provider tests and verify failure**

Run:

```bash
uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAnthropicPromptCacheRequestPayload -q
```

Expected: fails because provider still marks the last two user messages and does not mark tool messages.

- [ ] **Step 7: Add small target helper types**

In `matmaster/providers/openai_provider.py`, add near prompt-cache helpers:

```python
@dataclass(frozen=True)
class _CacheTarget:
    message_index: int
    placement: str  # "text_content" | "tool_message"
    priority: int
```

Keep this private to the provider. Do not export it.

- [ ] **Step 8: Split injection helpers**

Replace the current nested `add_cache_control()` with two helpers:

```python
def _add_text_content_cache_control(
    message: dict[str, Any],
    cache_control: dict[str, str],
) -> bool:
    ...


def _add_tool_message_cache_control(
    message: dict[str, Any],
    cache_control: dict[str, str],
) -> bool:
    if message.get("role") != "tool":
        return False
    content = message.get("content")
    if isinstance(content, str) and not content.strip():
        return False
    if isinstance(content, list) and not content:
        return False
    message["cache_control"] = dict(cache_control)
    return True
```

Use the existing text conversion behavior for `_add_text_content_cache_control()`.

- [ ] **Step 9: Add tool-call extraction helpers**

Add:

```python
def _tool_call_ids(message: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("id"):
            ids.append(str(call["id"]))
    return ids
```

Add:

```python
def _latest_completed_tool_group_tail(messages: list[dict[str, Any]]) -> int | None:
    for assistant_idx in range(len(messages) - 1, -1, -1):
        assistant = messages[assistant_idx]
        if assistant.get("role") != "assistant":
            continue
        expected = set(_tool_call_ids(assistant))
        if not expected:
            continue

        seen: set[str] = set()
        tail_idx: int | None = None
        for idx in range(assistant_idx + 1, len(messages)):
            message = messages[idx]
            if message.get("role") == "assistant" and _tool_call_ids(message):
                break
            if message.get("role") != "tool":
                continue
            tool_call_id = message.get("tool_call_id")
            if tool_call_id in expected:
                seen.add(str(tool_call_id))
                tail_idx = idx
                if seen == expected:
                    return tail_idx
        return None
    return None
```

This intentionally returns only the latest assistant tool-call group. If that latest group is incomplete, skip the tool-result breakpoint rather than falling back to an older group.

- [ ] **Step 10: Add cache target selector**

Add:

```python
def _select_anthropic_cache_targets(
    messages: list[dict[str, Any]],
    options: AnthropicPromptCacheOptions,
) -> list[_CacheTarget]:
    targets: list[_CacheTarget] = []
    used: set[int] = set()

    def append(target: _CacheTarget) -> None:
        if len(targets) >= options.max_breakpoints:
            return
        if target.message_index in used:
            return
        targets.append(target)
        used.add(target.message_index)

    if options.system_prompt_breakpoint:
        system_idx = next(
            (idx for idx, m in enumerate(messages) if m.get("role") == "system"),
            None,
        )
        if system_idx is not None:
            append(_CacheTarget(system_idx, "text_content", 0))

    if options.automatic and options.latest_user_breakpoint:
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                append(_CacheTarget(idx, "text_content", 1))
                break

    if options.automatic and options.tool_result_breakpoint:
        tool_tail_idx = _latest_completed_tool_group_tail(messages)
        if tool_tail_idx is not None:
            append(_CacheTarget(tool_tail_idx, "tool_message", 2))

    if options.automatic and options.flexible_breakpoint:
        flexible = _select_flexible_cache_target(messages, used, options)
        if flexible is not None:
            append(flexible)

    return targets
```

- [ ] **Step 11: Add flexible target selector**

Add:

```python
def _message_text_size(message: dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content.strip())
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                total += len(part["text"].strip())
        return total
    return 0
```

Add:

```python
def _select_flexible_cache_target(
    messages: list[dict[str, Any]],
    used: set[int],
    options: AnthropicPromptCacheOptions,
) -> _CacheTarget | None:
    candidates: list[tuple[int, int, str]] = []
    for idx, message in enumerate(messages):
        if idx in used:
            continue
        role = message.get("role")
        if role not in {"user", "tool"}:
            continue
        size = _message_text_size(message)
        if size < options.min_flexible_chars:
            continue
        placement = "tool_message" if role == "tool" else "text_content"
        candidates.append((size, idx, placement))
    if not candidates:
        return None
    _, idx, placement = max(candidates)
    return _CacheTarget(idx, placement, 3)
```

- [ ] **Step 12: Refactor `_prepare_messages()`**

Update the early return:

```python
if options is None or (
    not options.system_prompt_breakpoint and not options.automatic
):
    return messages
```

Keep it, but selection now determines what actually gets marked.

Flow:

```python
prepared = copy.deepcopy(messages)
targets = _select_anthropic_cache_targets(prepared, options)
for target in targets:
    message = prepared[target.message_index]
    if target.placement == "tool_message":
        ok = _add_tool_message_cache_control(message, options.cache_control)
    else:
        ok = _add_text_content_cache_control(message, options.cache_control)
    if not ok and target.placement == "text_content" and message.get("role") == "system":
        raise LLMError(...)
```

Preserve existing validation for missing system messages when `system_prompt_breakpoint=True`:

```python
if options.system_prompt_breakpoint and not any(
    message.get("role") == "system" for message in prepared
):
    raise LLMError(...)
```

Preserve validation for an empty system message selected for cache control.

- [ ] **Step 13: Run provider prompt-cache tests**

Run:

```bash
uv run pytest tests/matmaster/providers/test_openai_provider.py::TestAnthropicPromptCacheRequestPayload -q
```

Expected: pass.

---

## Task 4: Enable New Strategy in Project Config

**Files:**
- Modify: `config/llm_config.yaml`
- Test: `tests/matmaster/config/test_loader.py` if needed

- [ ] **Step 1: Update `opus` profile config**

Change:

```yaml
    prompt_cache:
      provider: "anthropic"
      system_prompt_breakpoint: true
      automatic: true
      ttl: "5m"
```

To:

```yaml
    prompt_cache:
      provider: "anthropic"
      system_prompt_breakpoint: true
      automatic: true
      latest_user_breakpoint: true
      tool_result_breakpoint: true
      flexible_breakpoint: true
      max_breakpoints: 4
      min_flexible_chars: 1000
      ttl: "5m"
```

- [ ] **Step 2: Update `opus_global` profile config**

Make the same prompt-cache change for the `opus_global` profile.

- [ ] **Step 3: Update loader regression test if present**

If `tests/matmaster/config/test_loader.py` has `test_project_opus_routes_enable_prompt_cache_control`, extend it to assert:

```python
assert profile.prompt_cache.latest_user_breakpoint is True
assert profile.prompt_cache.tool_result_breakpoint is True
assert profile.prompt_cache.flexible_breakpoint is True
assert profile.prompt_cache.max_breakpoints == 4
```

- [ ] **Step 4: Run loader/config tests**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py -q
```

Expected: pass.

---

## Task 5: Full Verification

**Files:**
- No additional source files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
uv run pytest \
  tests/matmaster/config/test_llm.py \
  tests/matmaster/config/test_loader.py \
  tests/matmaster/providers/test_llm_factory.py \
  tests/matmaster/providers/test_openai_provider.py::TestAnthropicPromptCacheRequestPayload \
  -q
```

Expected: pass.

- [ ] **Step 2: Run linter/format checks if project standard requires**

Run the existing project lint command if documented. If unknown, run focused import/style checks only if already configured in the repo. Do not introduce a new formatter configuration.

- [ ] **Step 3: Optional real LiteLLM smoke test**

Use the already validated proxy probe shape, but run it through the provider after implementation:

- `system` message exists.
- latest `user` message exists.
- assistant has two parallel `tool_calls`.
- both matching `tool` messages exist.
- final user asks for a deterministic short response.

Expected request payload behavior:

- system text block has `cache_control`.
- latest user text block has `cache_control`.
- only the last tool message in the completed parallel group has top-level `cache_control`.
- total cache markers <= 4.

Expected usage behavior on repeated/growing-prefix call:

- first call has `cache_creation_input_tokens > 0`.
- second call has `cache_read_input_tokens > 0`.

---

## Implementation Notes

- Do not add provider-agnostic prompt-cache abstractions yet. This is Anthropic-specific behavior and belongs in `OpenAIProvider` because it is transforming OpenAI-compatible message payloads for LiteLLM Anthropic routes.
- Do not annotate every tool result in a parallel group. The approved semantics are "group tail marks the full completed tool-result prefix."
- Do not make user-message marking conditional on length. The latest user message is a semantic boundary, not a pure size optimization.
- Keep the flexible slot simple and deterministic. Largest remaining user/tool content above `min_flexible_chars` is enough for this iteration.
- If a message already has `cache_control`, do not overwrite it unless the existing codebase already assumes provider-owned mutation. Prefer leaving existing explicit markers intact if encountered during implementation.

