# Inline Self-Compaction Design

- Date: 2026-05-17
- Status: Draft (awaiting user approval)
- Author: Kealdoom + Claude (brainstorming session)
- Related prior work:
  - [2026-05-09-compaction-checkpoint-context-design.md](2026-05-09-compaction-checkpoint-context-design.md)
  - [2026-05-11-preflight-current-input-compaction-design.md](2026-05-11-preflight-current-input-compaction-design.md)

## 1. Context & Motivation

### 1.1 Current behavior

`ContextCompactor._summarize` (in [matmaster/context/compaction.py](../../../matmaster/context/compaction.py))
serializes every message in history to a JSON line via
`json.dumps(msg.model_dump(mode="json"), ensure_ascii=False)`, concatenates them
into a single text blob, and sends the blob to an LLM with
`SUMMARY_SYSTEM_PROMPT` as the system instruction. The LLM provider used for
this call comes from `summary_provider`, which in production is *almost always*
identical to `spec.llm_provider` (the main LLM) because
`CompactionConfig.compaction_llm` is never configured in real deployments
(confirmed by user; the dedicated `compaction:` alias in
[config/llm_config.yaml](../../../config/llm_config.yaml) is dead config).

### 1.2 Two real problems

1. **Zero prompt-cache hit.** Even when `summary_provider` is the same instance
   as `spec.llm_provider`, the `messages` array passed to the summary call
   (`[SUMMARY_SYSTEM_PROMPT system, JSON-serialized history user]`) does **not**
   share the prefix of the main conversation's most recent LLM call
   (`[spec.system_prompt, ...real messages..., tools schema]`). The cache key
   diverges at the very first message, so the entire history is re-billed and
   re-uploaded to the provider on every compaction.
2. **Input shape diverges from pretraining distribution.** JSON-serialized
   conversation history is far from any conversational corpus the model was
   trained on. The model must "deserialize-understand" before it can summarize,
   which costs quality. Specifically, it cannot leverage its strong priors on
   the natural conversational flow `assistant -> tool_calls -> tool_result ->
   assistant`, because that structure is now flattened into JSON keys inside a
   single user message.

### 1.3 What this design changes

Instead of serializing history into a JSON blob and sending it to a separate
LLM call, the summary call now:

- Uses the **main LLM provider** (`spec.llm_provider`)
- Reuses the **main system prompt** (`spec.system_prompt`) byte-for-byte
- Sends **real conversation messages** (not JSON) as the chat history
- Appends a single `UserMessage` containing the compaction instruction at the
  end
- Carries the **same `tool_definitions`** as the main conversation, with
  `tool_choice="none"` to forbid tool calls during the summary

The downstream pipeline (`ContextAssembler.assemble_compaction`,
`COMPACTED_COMPOSITION` wrapping, `messages[:] = [system_msg, runtime_user_msg]`
in-place replacement, checkpoint persistence, event emission) is **not touched**.

### 1.4 Benefits (two layers, stacked)

**Unconditional layer (always realized):**

- **Input shape normalization.** The summary input is a real messages array,
  matching the model's pretraining distribution for conversations and tool use.
  The model uses its native priors on `tool_call -> tool_result -> assistant`
  flow at zero cost, instead of parsing JSON.
- **Model consistency.** Summary text is produced by the same model that will
  later consume it, so terminology, style, and emphasis align with downstream
  reasoning.
- **Reduced assembly complexity.** `summary_provider` field deleted, dual-provider
  lifecycle code in `agent.py` simplified, dead-code paths removed. Net code
  reduction.

**Conditional layer (prompt cache hit):**

- **Runtime compaction:** almost always hits. The main conversation just finished
  a turn, the cache is warm, and the summary call shares the entire prefix.
- **Preflight compaction:** hits when (a) the session is within provider cache
  TTL of a recent main call, (b) `spec.system_prompt` matches byte-for-byte
  across runs (no new attachments / skills since last run), (c) restored history
  matches the canonicalized form of the original main call. When these
  conditions fail, the unconditional benefits still apply.

## 2. Out of Scope

The following remain unchanged in this PR:

- `ContextAssembler.assemble_compaction` and the `COMPACTED_COMPOSITION` pipeline
- The `<compacted_history>` / `<current_instruction>` wrapping for the
  rebuilt user message
- `messages[:] = [system_msg, runtime_user_msg]` in-place replacement form
- `CompactionPlan` and `CompactionResult` dataclasses (no new fields)
- `CompactionEvent` public event schema (subscribers see no behavioral change)
- The outer `run_compaction_plan` / `run_preflight_compaction_if_needed` /
  `run_runtime_compaction_if_needed` generators' event-emission shape and
  checkpoint-sink call (only their internal try/except structure changes)
- `HistoryCheckpointService.build_checkpoint_sink` and `ModelHistoryRestorer`
  (`base_snapshot` shape unchanged, checkpoint replay path unchanged)
- `CURRENT_INPUT_CONTINUATION_INSTRUCTION` constant in
  [compaction.py:60-64](../../../matmaster/context/compaction.py) (used by the
  assembler for `<current_instruction>` blocks, unrelated to this change)

## 3. Core Algorithm

### 3.1 New module function: `call_summary_llm`

Location: bottom of [matmaster/context/compaction.py](../../../matmaster/context/compaction.py).
Stateless, no class membership, ~60 lines.

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
    """Call the main LLM to summarize conversation history.

    Stateless. Does not hold compactor or kernel references. Raises on
    empty/failed response; caller decides whether to fall back.

    Delegates summary-input preparation (current_split, budget computation,
    targeted ToolMessage truncation) to prepare_messages_for_summary_call so
    that the byte-level prefix invariant with the main conversation is
    preserved whenever possible.
    """
    assert isinstance(full_messages[0], SystemMessage)

    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)

    # Step 1: compute budget + prepare messages
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

    # Step 2: assemble summary call messages (system + prepared + request)
    summary_messages = [*prep.messages, compact_request]

    # Step 3: canonicalize + validate (reuse main-conversation pipeline)
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

Key invariants:

- **`full_messages[0]` must be a `SystemMessage`** (kernel invariant; asserted
  here)
- **`base_messages` includes the system message directly** (sliced, not
  re-assembled) so the prefix passed to the LLM matches the main conversation's
  layout byte-for-byte
- **`tools` argument carries the same `tool_definitions` as the main
  conversation**, ensuring the cache key's `tools` section is unchanged
- **`tool_choice="none"`** is the only behavioral parameter that differs from
  main calls; it does not participate in the cache key (cache hits are
  unaffected)

### 3.2 Preflight vs runtime: the only differences

| Step | Preflight (`current_split=True`) | Runtime |
|---|---|---|
| `base_messages` | `processed[:-1]` (excludes the trailing current-turn user) | `processed` (includes the trailing message, which may be a `ToolMessage`) |
| Downstream assembler intent | `PREFLIGHT_COMPACTION` (re-inserts current user via `<current_instruction>`) | `RUNTIME_COMPACTION` (no current-turn re-insertion) |
| `apply_compaction_plan` post-processing | unchanged | unchanged |

### 3.3 Summary input preparation for tool results

A `ToolMessage` returned by a parallel tool call (e.g. four concurrent
`paper_search` returning 30K each) can push a single turn from 100K into 220K
context. Without pre-processing, the summary call would itself exceed the
provider's context limit and fail. But naive truncation is also dangerous: it
can shred the `assistant.tool_calls -> tool` protocol pairing, lose critical
result content (file paths, exact values, error messages), and drift the
prompt-cache prefix on common (non-oversized) paths.

This section specifies a single preparation function that handles all four
concerns:

```python
@dataclass(frozen=True)
class SummaryInputPreparation:
    """Result of preparing messages for a summary call.

    Returned by prepare_messages_for_summary_call. Carries the messages slice
    ready to pass to the LLM (system + history, minus current-turn user when
    current_split applies), plus accounting information for diagnostics and
    tests.
    """
    messages: list[Message]                       # system + history slice (no compact_request)
    truncated_tool_call_ids: tuple[str, ...]      # tool_call_ids whose content was shortened
    original_tokens: int                          # estimate_tokens of base_messages BEFORE truncation
    prepared_tokens: int                          # estimate_tokens of returned messages AFTER truncation
    tool_schema_tokens: int                       # estimate of tool_definitions JSON
    request_tokens: int                           # estimate of the compact_request UserMessage
    message_budget: int                           # computed budget that messages must fit under


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
    """Prepare base_messages for a summary call.

    Behavior:
    - Decides current_split per phase + turn_input.
    - Computes message_budget by subtracting tool schema, compact_request, and
      safety_margin from the input budget.
    - If base_messages already fit the budget, returns them as a new list of
      the SAME message instances (no truncation, no copy of inner content).
    - Otherwise constructs a new list with oversized ToolMessage entries
      replaced by truncated copies (head + marker + tail). Original messages
      and tool_call_id / tool_name fields are preserved exactly.

    Does NOT mutate full_messages or its elements.
    """
```

**Processing order (matters for correctness):**

1. **Decide `current_split`** based on phase + turn_input, identical rule to the
   existing logic in [compaction.py:249-256](../../../matmaster/context/compaction.py).
   For preflight with effective turn_input, the trailing user message is held
   out (it will be re-injected by the assembler via `<current_instruction>`);
   for runtime, all of history participates.
   ```python
   current_split = (
       phase == "preflight"
       and turn_input is not None
       and turn_input.has_effective_input()
       and len(full_messages) >= 3
       and isinstance(full_messages[-1], UserMessage)
   )
   base_messages = full_messages[:-1] if current_split else full_messages
   ```

2. **Compute budget — count everything in the summary call's input**:
   ```python
   input_budget = context_limit - reserved_summary_tokens - safety_margin_tokens
   tool_schema_tokens = estimate_json_tokens(tool_definitions or [])
   request_tokens = estimate_tokens([compact_request], safety_margin=1.1)
   message_budget = input_budget - tool_schema_tokens - request_tokens
   ```
   `estimate_json_tokens` is a new helper that mirrors `estimate_tokens` but
   accepts an arbitrary JSON-serializable object (used for the tool schema):
   ```python
   def estimate_json_tokens(obj: Any, safety_margin: float = 1.0) -> int:
       text = json.dumps(obj, ensure_ascii=False)
       enc = _get_encoder()
       if enc is not None:
           return int(len(enc.encode(text)) * safety_margin)
       return int(max(len(text) // 4, 1) * safety_margin)
   ```
   If `message_budget <= 0` (e.g. tool schema alone consumes the budget),
   `prepare_messages_for_summary_call` raises `ValueError("summary message
   budget non-positive")`. **Do NOT drop the `tools` field to make room** —
   that breaks the cache-prefix invariant, defeating the entire design goal.
   The summary call should simply fail and fall back via the established Q4
   path.

3. **Common case — already fits**:
   ```python
   prepared_tokens = estimate_tokens(base_messages, safety_margin=1.0)
   if prepared_tokens <= message_budget:
       return SummaryInputPreparation(
           messages=list(base_messages),       # shallow copy of the list; same Message instances
           truncated_tool_call_ids=(),
           original_tokens=prepared_tokens,
           prepared_tokens=prepared_tokens,
           tool_schema_tokens=tool_schema_tokens,
           request_tokens=request_tokens,
           message_budget=message_budget,
       )
   ```
   This is the **hot path**: no per-message inspection, no content copy. The
   returned list contains the exact same `Message` instances, so when
   canonicalized for the provider they produce a byte-identical prefix to the
   main conversation's last call.

4. **Over-budget — minimize damage**:
   - Only `ToolMessage` instances are candidates for shortening. `UserMessage`,
     `AssistantMessage`, and `SystemMessage` content is preserved as-is.
   - Sort candidates by their individual `estimate_tokens([msg])` descending.
   - For each candidate (largest first), produce a truncated copy and replace
     it in the working list, then re-estimate. Stop as soon as
     `prepared_tokens <= message_budget`. **Do not over-truncate** (no
     `0.8 * budget` floor).
   - Never delete, reorder, merge, or otherwise restructure `ToolMessage`
     entries. The protocol pairing `assistant.tool_calls -> tool` must remain
     exactly intact; truncation only edits the textual `content` field.

5. **Truncation parameters**:
   - Minimum content length to consider for truncation: 500 chars (smaller
     ToolMessages are not worth processing).
   - Default preserved content: **head 1200 chars + tail 800 chars** (raised
     from the legacy 200/100 because scientific tool outputs — paper_search,
     Bohrium job logs, LAMMPS/GROMACS dumps — frequently carry critical
     numerical values and citation/path information past the first 200 chars).
   - Both parameters are module-level constants
     (`_TRUNCATE_HEAD_CHARS = 1200`, `_TRUNCATE_TAIL_CHARS = 800`,
     `_TRUNCATE_MIN_CONTENT_CHARS = 500`) so they can be tuned without
     touching the function body.

6. **Structured truncation marker** (replaces the legacy `... [truncated:
   N chars → 300 chars to fit context window] ...`):
   ```text
   [tool_result truncated before summary call]
   tool_name: paper_search
   tool_call_id: call_xxx
   original_chars: 31542
   preserved: first 1200 chars and last 800 chars
   reason: summary input would exceed context window
   ```
   The marker is structured and informative so the summarizing model can:
   - recognize the truncation as a system-imposed bound, not a tool failure
   - know which tool was called and where to find the original ID
   - quantify the information loss
   - choose to mention "this tool result was truncated; full content
     unavailable" in its summary rather than fabricate missing detail

7. **Post-truncation validation**:
   `prepare_messages_for_summary_call` does NOT itself run
   `canonicalize_messages_for_provider` or
   `normalize_and_validate_openai_messages`. That happens in
   `call_summary_llm` at step 3 (§3.1), reusing the main-conversation
   pipeline. Since truncation only edits `ToolMessage.content` and keeps all
   structural fields intact, validation should always pass; if it ever fails,
   `call_summary_llm` propagates the validation error to
   `run_compaction_plan`, which routes per phase (preflight raises, runtime
   falls back).

**Caching consequences:**

- **Common case (history fits budget)**: zero content changes, prefix is
  byte-identical to main conversation, cache hits fully across messages.
- **Over-budget case**: cache key diverges at the first truncated
  `ToolMessage`. Truncation is targeted (largest first, only as much as
  needed), so the prefix up to that point still hits. This is acceptable
  because over-budget cases imply the conversation is already so large that
  partial cache hit + ability to actually run the summary is strictly better
  than full cache hit + API rejection.

**What this design explicitly does NOT do (vs Claude Code):**

- Does not persist a stable `replacement` string per `tool_use_id` across
  compaction rounds. Each compaction recomputes truncated content from the
  current `state.messages`. Adopting a persistent `ContentReplacementState`
  (à la Claude Code's `toolResultStorage`) would require transcript /
  checkpoint schema changes, subagent inheritance, and resume-time
  rehydration — out of scope for this MVP. See §9 "Open Questions / Future
  Work".
- Does not group tool_results by API-level user message (Claude Code's
  per-API-turn budget). matmaster's `ToolMessage` stays as one item per tool
  call locally; LiteLLM/Anthropic-side merging during canonicalization is the
  provider's concern. If LiteLLM merging behavior ever causes systematic
  underestimation, revisit (also tracked in §9).

### 3.4 ContextCompactor refactor: `apply_summary` + `apply_fallback`

`ContextCompactor.apply_compaction_plan` is split into two methods, making the
two execution paths (success vs degraded) explicit:

```python
class ContextCompactor:
    # __init__ no longer takes summary_provider, llm_provider, or system_prompt.
    # No new dependencies are added. (LLM calls live in call_summary_llm.)

    async def apply_summary(
        self,
        plan: CompactionPlan,
        messages: list[Message],
        summary: str,                        # ← provided by caller
        *,
        turn_input: TurnInput | None = None,
    ) -> CompactionResult:
        """Apply a pre-computed summary; mutate messages in place; produce a
        durable checkpoint base_snapshot."""
        # (Logic from the current try-block of apply_compaction_plan:)
        # - assembler.assemble_compaction(...)
        # - messages[:] = [system_msg, runtime_user_msg]
        # - prepare checkpoint_user_msg -> base_snapshot
        # Returns CompactionResult(strategy="summary", durability="durable", ...)

    async def apply_fallback(
        self,
        plan: CompactionPlan,
        messages: list[Message],
        *,
        failure_reason: str,
    ) -> CompactionResult:
        """Apply tool-turn-safe tail fallback when the summary call failed.

        Selects the last N non-system messages while preserving
        assistant.tool_calls -> tool pairing. The naive "last 3" approach
        breaks under tool-calling tails because matmaster's validator
        (validate_openai_tool_turn_sequence) hard-rejects orphan tool
        results.

        Algorithm:
            messages[:] = [messages[0], *_select_tool_safe_tail(messages[1:], n=3)]

        Where _select_tool_safe_tail walks backward from the tail and
        expands the selection to include the assistant message that owns
        any included tool_call_ids; if a tool_call_id has no findable
        owner within the message history (orphan in the original list),
        the orphan tool message is excluded from the tail. If the resulting
        tail is empty (all candidates were orphan tools), raise the runtime
        fallback as a hard failure rather than emit invalid history.

        Returns CompactionResult(strategy="sliding_window", durability=
        "ephemeral", failure_reason=...)
        """


def _select_tool_safe_tail(
    non_system_messages: list[Message],
    *,
    n: int,
) -> list[Message]:
    """Select up to n trailing messages, expanding backward as needed to
    preserve tool_call/tool_result pairing.

    Rules:
    1. Start with the last n messages.
    2. Collect all tool_call_ids referenced by ToolMessage entries in the
       selection.
    3. Walk backward through earlier messages; for each AssistantMessage
       with tool_calls overlapping the collected ids, include it in the
       selection.
    4. Drop any ToolMessage whose tool_call_id was not found in any
       included AssistantMessage.tool_calls (truly orphan).
    5. Return the resulting messages in original order.

    Guarantees:
    - Output passes validate_openai_tool_turn_sequence (no orphan
      tool_result; no missing tool_result for an included assistant
      tool_call — included assistants must have all their tool_calls
      resolved, so if some tool_results were not in the original tail,
      the assistant is excluded).
    """
```

The fallback's `messages[:]` replacement uses `messages[0]` (kernel
invariant: SystemMessage) followed by the tool-safe tail. The default `n=3`
matches the historical sliding-window depth. If `_select_tool_safe_tail`
returns an empty list (the tail consists entirely of orphan tool messages,
which would indicate severe upstream corruption), `apply_fallback` raises
rather than mutating `messages` — runtime compaction then propagates the
failure, kernel logs it, and the agent run terminates with an
`invalid_finish` finish_detail rather than continuing with garbage history.
```

### 3.5 `run_compaction_plan` orchestration

The outer generator now explicitly orchestrates `call_summary_llm` and routing:

```python
async def run_compaction_plan(
    *,
    spec: AgentRuntimeSpec,
    state: _KernelState,
    plan: Any,
    checkpoint_sink: Any,
    turn_input: TurnInput | None = None,
    tool_definitions: list[dict] | None = None,    # ← new
) -> AsyncIterator[_KernelItem]:
    yield _KernelItem(event=CompactionEvent(status="running", ...))

    pre_compaction_barrier = spec.runtime_ports.pre_compaction_barrier
    if pre_compaction_barrier is not None:
        result = pre_compaction_barrier()
        if inspect.isawaitable(result):
            await result

    messages_before = len(state.messages)

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
            plan, state.messages, summary, turn_input=turn_input
        )
    except Exception as exc:
        if plan.phase == "preflight":
            logger.warning(
                "Preflight compaction summary failed; aborting", exc_info=True
            )
            raise
        logger.warning(
            "Compaction #%d summary failed; falling back",
            plan.compaction_count, exc_info=True,
        )
        result = await spec.compactor.apply_fallback(
            plan, state.messages, failure_reason=str(exc)
        )

    # hook emit, checkpoint sink, complete event — unchanged
    ...
```

Failure-mode summary:

| Failure type | Preflight | Runtime |
|---|---|---|
| `call_summary_llm` raises (timeout / network / empty content / context limit / etc.) | raise to kernel (task fails) | `apply_fallback` (sliding window) |

This preserves the established Q4 contract (preflight failures are not silently
recovered; runtime failures degrade gracefully).

## 4. Prompt Template

### 4.1 Sentinel wrapping and Chinese default

The compaction request is wrapped in a `<compact_request>...</compact_request>`
sentinel so the model can distinguish it from organic user input. The MVP
default is Chinese; an English placeholder is reserved in source but not used.

```python
SUMMARY_USER_REQUEST_TEMPLATE = """\
<compact_request>
当前会话上下文已接近上限，需要你对上方所有对话进行压缩。请按以下要求输出
一份结构化摘要，后续对话将以你输出的摘要作为历史背景继续。

需要保留：
- 关键决策及其原因
- 工具调用的结果（必须保留确切数值、路径、文件名、错误信息）
- 用户给出的约束、参数、偏好
- 当前任务状态与已完成事项

输出要求：
- 只输出摘要文本，不要寒暄、不要解释你正在做什么、不要复述本压缩请求
- 不要调用任何工具（已被 API 禁用，即使工具看起来相关）
- 不要继续推进上方工具调用未完成的任务，只对其结果做总结
- 不要新增上方没有提到的信息
- 如上方对话开头本身就包含 <compacted_history> 历史摘要块，请将其与后续事件
  合并重写为一份新的摘要，不要逐字保留旧块
</compact_request>\
"""

# Placeholder: kept for future language auto-detection. Not referenced in code.
# To enable, swap in a detection step that selects between ZH and EN templates.
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
```

Rationale for the Chinese default:

- matmaster's user base is predominantly Chinese-speaking
- A single template avoids language-detection complexity (YAGNI)
- The agent's reply-language convention is "match user's language"; using a
  Chinese template aligns with the expected output language for the typical
  session

If language drift becomes a problem (e.g. an English-only session goes through
compaction and the model summarizes in Chinese), the English placeholder can be
swapped in via a future language-detection step. The placeholder is named with
a leading underscore and `_RESERVED` suffix to make its inactive status obvious
in source.

### 4.2 Why these specific guardrail phrases

- **"不要调用任何工具（已被 API 禁用，即使工具看起来相关）"**: belt-and-suspenders
  with `tool_choice="none"`. On Bedrock (where `tool_choice="none"` is not
  available; see §6), this phrase is the primary defense against tool calls
  during summary.
- **"不要继续推进上方工具调用未完成的任务，只对其结果做总结"**: addresses the
  "tool result tail" case where compaction triggers immediately after a tool
  result arrives, and the model would otherwise try to continue the
  in-flight tool-driven reasoning.
- **"将旧 <compacted_history> 块合并重写"**: handles iterative compaction (a
  session that has already been compacted gets compacted again) to prevent
  nested summary blocks.

## 5. Wiring Changes

### 5.1 `ContextCompactor.__init__` signature

```python
def __init__(
    self,
    config: CompactionConfig,
    *,                                       # ← summary_provider arg deleted
    context_assembler: ContextAssembler,
    user_instructions: UserInstructions,
    session_id: str,
    spawn_id: str | None,
    runtime_covered_until_provider: Callable[[], int | None] | None = None,
    event_sink: Callable[[Any], Awaitable[None]] | None = None,
    compaction_scope: str = "root",
) -> None:
```

Changes:

- **Delete `summary_provider` parameter** (positional, was 2nd arg)
- **Delete `_summary_provider` field, `summary_provider` property, and
  `_summarize` method** from the class
- **No new dependencies added.** LLM, system prompt, and tool definitions are
  passed at call time via `call_summary_llm`, not held by the class.

### 5.2 Assembly site simplification

[matmaster/core/runtime_context_assembly.py:91-99](../../../matmaster/core/runtime_context_assembly.py)
loses its summary_provider construction:

```python
# DELETE these lines:
#     summary_provider = spec.llm_provider
#     if spec.compaction.compaction_llm:
#         llm_config = getattr(ctx, "llm_config", None)
#         if llm_config is not None:
#             from matmaster.providers.llm_factory import build_provider
#             try:
#                 summary_provider = build_provider(...)
#                 ...
```

[matmaster/core/runtime_context_assembly.py:144-154](../../../matmaster/core/runtime_context_assembly.py)
loses the `summary_provider=summary_provider` kwarg:

```python
compactor=ContextCompactor(
    config=spec.compaction,
    # summary_provider=summary_provider,     # ← DELETE
    context_assembler=context_assembler,
    user_instructions=user_instructions,
    ...
),
```

### 5.3 `LLMProvider.chat()` Protocol extension

[matmaster/types/llm_provider.py:35-39](../../../matmaster/types/llm_provider.py)
adds a keyword-only `tool_choice` parameter, defaulting to `None` for full
backward compatibility:

```python
async def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    tool_choice: str | dict | None = None,    # ← new (keyword-only, default None)
) -> LLMResponse: ...
```

Existing call sites (main conversation path) need no changes; `tool_choice`
defaults to `None`, preserving current behavior.

### 5.4 Provider implementations

**[openai_provider.py:407+](../../../matmaster/providers/openai_provider.py)** —
3-line passthrough after the `if tools:` block:

```python
if tool_choice is not None:
    kwargs["tool_choice"] = tool_choice
```

**[bedrock_provider.py:343+](../../../matmaster/providers/bedrock_provider.py)** —
adopts **strategy B**: pass tools field unchanged, do not set `toolChoice`,
rely on the prompt template's "不要调用任何工具" guardrail.

Rationale: Bedrock Converse API's `toolChoice` field offers only `auto` /
`any` / `tool` — no `none`. Strategy A (drop `tools` field) would break the
prompt cache prefix in the `tools` section. Strategy B preserves full cache
prefix consistency at the cost of weaker behavioral guarantee; if the model
emits a tool call anyway, `response.content` will be empty, `call_summary_llm`
raises `ValueError("Summary LLM returned empty content")`, and the runtime
path falls back to sliding-window. Preflight on Bedrock under this strategy
remains a hard-fail (preflight contract: any failure raises).

```python
# bedrock_provider.chat() addition:
# When tool_choice="none", we cannot translate to Bedrock's toolChoice.
# Strategy B: pass tools unchanged, rely on prompt guardrail + fallback.
if tool_choice is not None and tool_choice != "none":
    # Future: handle "auto" / dict forms if needed
    raise NotImplementedError(
        f"bedrock_provider does not yet support tool_choice={tool_choice!r}"
    )
# tool_choice="none" intentionally passes through without setting toolChoice
```

Production-path note: matmaster-evo's production traffic uses
openai_provider via LiteLLM Proxy. Bedrock is a backup path with very low
real-world traffic, so the weaker guarantee is acceptable.

### 5.5 `ensure_tool_definitions` helper and kernel call-site update

The current kernel order in
[matmaster/core/agent.py:239-285](../../../matmaster/core/agent.py) creates a
correctness gap:

1. line 239: `run_preflight_compaction_if_needed` runs
2. line 257: `run_runtime_compaction_if_needed` runs (inside `while` loop)
3. line 265-285: tool definitions resolved (version-aware caching of
   `state.cached_tool_definitions`)
4. line 287-289: main LLM call uses the resolved tool_defs

So when preflight compaction fires (case 1), `state.cached_tool_definitions`
is **always `None`** — never resolved before this point. Passing `None` as
`tool_definitions` to `call_summary_llm` would set `tools=None` in the
summary call's payload, while the immediately-following main call (case 4)
sets `tools=<resolved list>`. The `tools` section of the prompt-cache key
diverges, defeating the central design goal that the summary call shares the
same `tools` schema with the main conversation.

**Fix**: extract tool definitions resolution into a helper, and call it
**before** every compaction site as well as before the main LLM call:

```python
# matmaster/core/agent.py (or matmaster/core/kernel_helpers.py if extracted)

def ensure_tool_definitions(
    spec: AgentRuntimeSpec,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    """Resolve and cache tool definitions on the kernel state.

    Re-resolves when tool_catalog.version changes; otherwise reuses the
    cached list. Returns None when no tool_catalog is configured.

    Must be called before any LLM payload assembly (compaction or main turn)
    so the same `tool_definitions` list object is observed by both the
    summary call and the next main call, preserving prompt-cache prefix
    invariance.
    """
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
        state.cached_tool_definitions = spec.tool_catalog.build_definitions(
            desc_ctx
        )
    return state.cached_tool_definitions
```

Then the kernel calls this helper at **three sites**, in order:

```python
# Before preflight compaction (line 239):
tool_definitions = ensure_tool_definitions(spec, state)
async for item in run_preflight_compaction_if_needed(
    spec=spec,
    state=state,
    history=history,
    turn_input=turn_input,
    checkpoint_sink=checkpoint_sink,
    tool_definitions=tool_definitions,    # ← never None when tool_catalog exists
):
    yield item

# ...

# Inside the while loop, before runtime compaction (line 257):
while state.turn < spec.max_turns:
    state.turn += 1
    tool_definitions = ensure_tool_definitions(spec, state)
    async for item in run_runtime_compaction_if_needed(
        spec=spec,
        state=state,
        turn_usage=turn_usage,
        checkpoint_sink=checkpoint_sink,
        tool_definitions=tool_definitions,    # ← same object as next main call
    ):
        yield item

    # ... (the existing tool-definitions block at line 265-285 is now
    #      replaced by tool_definitions assignment from the helper above
    #      and the inline desc_ctx code is deleted)
    tool_defs = tool_definitions

    api_messages = normalize_and_validate_openai_messages(...)
    # main LLM call uses tool_defs (the same list ensure_tool_definitions
    # returned), preserving the prefix invariant
```

**Key invariant**: between any compaction site and the next main LLM call in
the same turn, no code path may invalidate or rebuild
`state.cached_tool_definitions`. Catalog version changes between turns are
expected and acceptable (both summary and main call see the new schema), but
within a turn the list object must remain stable.

`tool_definitions` flows through
`run_*_compaction_if_needed` -> `run_compaction_plan` ->
`call_summary_llm` -> `prepare_messages_for_summary_call` without being
stored or re-resolved by any intermediate layer.

## 6. Deletion & Cleanup

All cleanup is in-scope for this PR (no separate cleanup PR; combined to
minimize review overhead).

### 6.1 Code deletions

| Resource | Location | Reason |
|---|---|---|
| `CompactionConfig.compaction_llm` field | [types/runtime.py:33](../../../matmaster/types/runtime.py) | No longer used (was never set in real configs) |
| `SUMMARY_SYSTEM_PROMPT` constant | [context/compaction.py:42-58](../../../matmaster/context/compaction.py) | Replaced by `SUMMARY_USER_REQUEST_TEMPLATE` |
| `ContextCompactor._summary_provider` field, `summary_provider` property | [context/compaction.py:136, 148-151](../../../matmaster/context/compaction.py) | No longer needed |
| `ContextCompactor._summarize` method | [context/compaction.py:427-443](../../../matmaster/context/compaction.py) | Logic moves to `call_summary_llm` |
| `ContextCompactor._truncate_tool_results` method | [context/compaction.py:369-425](../../../matmaster/context/compaction.py) | Logic moves into `prepare_messages_for_summary_call` (§3.3); ownership moves to the summary caller. The legacy single-purpose function is deleted; its replacement is part of the richer preparation flow with structured markers, larger preserved windows, and tool-pairing guarantees. |
| `ContextCompactor.preflight_if_needed` legacy method | [context/compaction.py:157-163](../../../matmaster/context/compaction.py) | Dead code (no production callers; only the `else` fallback in `agent_compaction.py:156` would hit, but the `plan_*` methods always exist) |
| `ContextCompactor.compact_if_needed` legacy method | [context/compaction.py:165-173](../../../matmaster/context/compaction.py) | Dead code (same reasoning) |
| Dual-provider lifecycle block in agent.py | [core/agent.py:105-153](../../../matmaster/core/agent.py) the `_summary_provider = None; getattr(spec.compactor, "summary_provider", None); ...; async with _summary_provider:` ladder | Simplified to single-level `async with spec.llm_provider:` |
| `summary_provider` construction in runtime_context_assembly | [core/runtime_context_assembly.py:91-99](../../../matmaster/core/runtime_context_assembly.py) and the `summary_provider=` kwarg at line 146 | Replaced by main-provider direct use; no `build_provider` call |

### 6.2 Config deletions

| Resource | Location |
|---|---|
| `compaction:` model alias section | [config/llm_config.yaml:152-163](../../../config/llm_config.yaml) |

LiteLLM router cleanup (router-side `model` named `gemini-3-flash-preview` for
compaction) is out of repo scope.

### 6.3 Breaking change note

`CompactionConfig.compaction_llm` is removed. Pydantic's default `extra="ignore"`
behavior (assumed; `CompactionConfig.model_config` is `ConfigDict(frozen=True)`
without explicit `extra` setting) means historical configs containing this field
will be silently ignored, not rejected. No active deployments use this field
(confirmed by user), so this is documentation-only.

Release note text:

> `CompactionConfig.compaction_llm` and the `config.llm.compaction` alias are
> deprecated and no longer take effect. Compaction now always uses the main LLM
> provider. These entries may be removed from configs.

## 7. Testing Strategy

### 7.1 Existing tests requiring updates

| File | Change |
|---|---|
| [tests/matmaster/context/test_compaction.py:60](../../../tests/matmaster/context/test_compaction.py) | Drop `summary_provider` from ContextCompactor construction; split `apply_compaction_plan` calls into `apply_summary` / `apply_fallback` |
| [tests/matmaster/core/test_context_compactor.py:175](../../../tests/matmaster/core/test_context_compactor.py) | Same |
| [tests/matmaster/integration/test_history_checkpoint_recovery.py:68](../../../tests/matmaster/integration/test_history_checkpoint_recovery.py) | Same; mock `spec.llm_provider.chat` to return a fixed summary string for integration scenarios |
| [tests/matmaster/devshell/test_compaction_via_devshell.py:113](../../../tests/matmaster/devshell/test_compaction_via_devshell.py) | Same; devshell already uses stub provider |
| [tests/test_chat_events_history_checkpoint.py](../../../tests/test_chat_events_history_checkpoint.py) | Grep for references to old `_run_compaction_plan` name in docstrings; replace with current `run_compaction_plan` |
| [tests/test_stream_replay_skill_hit.py](../../../tests/test_stream_replay_skill_hit.py) | Grep + update similarly |
| [tests/matmaster/types/test_runtime.py](../../../tests/matmaster/types/test_runtime.py) | Remove any assertion that `CompactionConfig.compaction_llm` field exists |

### 7.2 New tests

| Test target | File (suggested) | Coverage |
|---|---|---|
| `call_summary_llm` unit tests | `tests/matmaster/context/test_summary_caller.py` (new) or extension of existing `test_compaction.py` | 1. preflight + current_split=True -> `base_messages` excludes trailing user; 2. runtime -> `base_messages` includes trailing message; 3. compact_request appended; 4. `llm_provider.chat` receives `tools=tool_definitions` and `tool_choice="none"`; 5. Empty response -> `ValueError`; 6. Calls `prepare_messages_for_summary_call` and forwards its `messages` slice |
| `prepare_messages_for_summary_call` unit tests | Same file | 1. Below budget -> returns same `Message` instances (identity check on each element, not just equality); 2. Above budget -> returns new list with only oversized `ToolMessage`s replaced; other instances are identity-preserved; 3. `tool_call_id` and `tool_name` of truncated copies match originals exactly; 4. Truncation marker contains `tool_name`, `tool_call_id`, `original_chars`, preserved range; 5. Original list and original Message instances NOT mutated; 6. `message_budget <= 0` raises `ValueError`; 7. Budget computation accounts for `tool_definitions` schema and `compact_request`; 8. Truncation stops as soon as `prepared_tokens <= message_budget` (no over-truncation) |
| `_select_tool_safe_tail` unit tests | Same file | 1. Tail with `[assistant(tool_calls=A,B), tool(A), tool(B)]` -> all 3 selected; 2. Tail with `[tool(A), tool(B), assistant(text)]` where assistant owning A,B is further back -> selection expands backward to include the owning assistant; 3. Tail with truly orphan tool message (no owner exists in history) -> orphan excluded; 4. Empty tail (all orphans) -> returns empty list |
| `ContextCompactor.apply_summary` unit tests | `tests/matmaster/context/test_compaction.py` (extend) | 1. messages replaced in-place with `[system, runtime_user_msg]`; 2. `base_snapshot` is checkpoint_user_msg serialization; 3. CompactionResult: durability=durable, strategy=summary |
| `ContextCompactor.apply_fallback` unit tests | Same | 1. Tool-tail case: `messages[:] = [system, *_select_tool_safe_tail(...)]` produces a list that passes `validate_openai_tool_turn_sequence`; 2. CompactionResult: durability=ephemeral, strategy=sliding_window, failure_reason set; 3. base_snapshot=None; 4. All-orphan tail -> raises (does NOT mutate messages) |
| `ensure_tool_definitions` unit tests | `tests/matmaster/core/test_kernel_helpers.py` (new or matching existing layout) | 1. No tool_catalog -> returns None; 2. First call resolves and caches on state; 3. Second call without version change returns same list object (identity); 4. version change invalidates cache and re-resolves |
| `run_compaction_plan` orchestration tests | `tests/matmaster/core/test_agent_compaction.py` (new, if not exists) | 1. summary success -> `apply_summary` called; 2. preflight summary failure -> raises, `apply_fallback` not called; 3. runtime summary failure -> `apply_fallback` called; 4. `tool_definitions` passed through to `call_summary_llm` |
| **Cache prefix consistency integration test (value-proof, required)** | `tests/matmaster/integration/test_summary_cache_prefix.py` (new) | (a) Mock provider records every `chat()` call's `messages` and `tools` arguments. (b) Simulate a main turn, then trigger compaction. (c) Assert that the summary call's `tools` argument is the **same list object** as the prior main call's `tools` argument (identity check, not equality). (d) Assert that the summary call's `messages` array, when serialized via `canonicalize_messages_for_provider`, shares the byte-exact prefix of the main call's `messages` array up to and including the last shared history entry; only the appended `compact_request` differs at the tail. |
| **Preflight tool-defs consistency integration test (required)** | Same file | (a) Configure a `tool_catalog` and trigger a session-cold start that fires preflight compaction. (b) Assert that the preflight summary call's `tools` argument is non-None and equal to the immediately-following main call's `tools` argument. This catches the bug where `ensure_tool_definitions` is forgotten before preflight. |
| **Concurrent oversize-truncation invariance test (required)** | Same file | (a) Construct a `state.messages` with N parallel `ToolMessage` entries that together exceed budget. (b) Call `prepare_messages_for_summary_call`. (c) Assert: only the largest M tool results are truncated (where M is the minimum needed to fit); other tool results are unchanged (identity); `tool_call_id` and `tool_name` fields match originals; `state.messages` and its elements are unchanged after the call returns. |

The cache-prefix integration test is the **value-proof test**: if a future
refactor of `canonicalize_messages_for_provider` introduces byte drift, this
test fails immediately, flagging cache regression.

## 8. Benefits Boundary & Operational Notes

### 8.1 When cache misses (and why it's still a net win)

| Scenario | Cache behavior | Mitigation / why still OK |
|---|---|---|
| Summary call's first turn after compaction | After `messages[:] = [system, runtime_user_msg]`, the next main call's prefix diverges from history. **Necessarily misses.** | Inherent cost of any real compaction. The summary itself is much shorter than original history, so input cost drops anyway. |
| Preflight on cold session (across TTL or system_prompt changed) | tools / messages / system may all diverge from any historical cache. | The unconditional benefits (input shape, model consistency) still apply. The previous design had zero cache anyway, so no regression. |
| Runtime case with parallel tool-result blowout (e.g. 4x 30K tool results in one turn) | Pre-truncation modifies oversized ToolMessages; cache key diverges starting at the first truncated message. | This is exactly the case where pre-truncation is *needed*; the trade is "lose partial cache to fit the call at all" vs "call fails entirely". Net win. |
| Bedrock provider path | `tools` passes through unchanged so cache is preserved; but model behavior is constrained only by the prompt guardrail. | Bedrock is a backup path with low real-world traffic. Failures fall back to sliding-window on runtime; preflight raises (Q4 contract). |

### 8.2 Token budget

**Compaction trigger threshold** (unchanged): `auto_threshold = 200_000 -
20_000 - 13_000 = 167_000`. When estimated tokens of `state.messages`
reach this, compaction fires.

**Summary call message budget** (new, computed inside
`prepare_messages_for_summary_call` per §3.3):

```python
input_budget    = context_limit - reserved_summary_tokens - safety_margin_tokens
                = 200_000 - 20_000 - 5_000
                = 175_000
message_budget  = input_budget - tool_schema_tokens - request_tokens
```

Where:

- `tool_schema_tokens` is the estimated JSON-serialized size of
  `tool_definitions`. For matmaster's current tool catalog (~dozens of
  tools including paper_search, Bohrium tools, MCP-loaded skills), this is
  typically in the 5-15K range and is dynamic as MCP servers connect /
  disconnect.
- `request_tokens` is the estimate of the compact_request UserMessage
  (~400-600 tokens for the current `SUMMARY_USER_REQUEST_TEMPLATE`).

A worst-case representative number: with tool_schema=15K, request=600,
`message_budget ≈ 175_000 - 15_000 - 600 = 159_400`. This is **lower than
`auto_threshold` (167_000)**, meaning a session that triggers compaction
exactly at the threshold may already have history exceeding
`message_budget`, requiring `prepare_messages_for_summary_call` to truncate
on the very first compaction. This is the expected behavior — the
truncation is targeted (largest ToolMessage first, only as much as needed)
and the design intentionally prefers "partial cache miss + summary call
succeeds" over "full cache hit + summary rejected".

**Operational note**: if the tool catalog grows substantially (e.g.
`tool_schema_tokens` reaches 30K), `message_budget` could shrink to a
point where ordinary sessions trigger truncation on every compaction. If
that happens, the right knob to turn is `reserved_summary_tokens` (raise
it to give the compactor more room) rather than the truncation parameters.
Future work: surface `tool_schema_tokens` in
`CompactionEvent.payload` for observability.

### 8.3 Truncation-induced retry loop (out of scope, acknowledged)

If `prepare_messages_for_summary_call` (or `apply_fallback`) truncates a
`ToolMessage` and the model immediately re-calls the same tool with the same
arguments (because the truncated result was insufficient context), a
degenerate loop is theoretically possible:

```
turn N: tool result 30K
turn N+1: compaction summary input over budget -> truncate that tool result
turn N+2: summary back to main loop; new turn; model sees "[tool_result truncated]" marker
turn N+3: model re-calls same tool with same args
turn N+4: another 30K tool result -> compaction -> ...
```

The structured truncation marker (§3.3 step 6) is the first line of defense:
it tells the model "this was truncated by the system, full content is no
longer recoverable" rather than implying the tool failed. The structured
field `reason: summary input would exceed context window` further signals
that retrying with the same args will not help.

Deeper mitigation is *not* in scope of this PR. It depends on:

- Tool-layer range control (e.g. `paper_search` accepting `max_results`)
- Prompt engineering hints (e.g. "if a tool result was truncated by the
  system, narrow your query rather than retrying the same call")
- Model intelligence

If this loop is observed in production, follow-up PRs can add prompt
engineering to either the system prompt or extend the truncation marker
text with stronger guidance.

## 9. Open Questions / Future Work

1. **Language auto-detection.** The current design defaults to Chinese with
   an English template reserved but not wired up. If real workloads include
   significant English-only sessions and the Chinese template causes language
   drift, add a detection step (CJK character ratio in recent UserMessages)
   and a template selection map.
2. **Bedrock `tool_choice` translation.** Strategy B (pass through unchanged,
   rely on prompt guardrail) is the MVP. If Bedrock summary failure rate
   becomes observable (logged warnings in `apply_fallback`), revisit with
   strategy A (drop tools) or strategy C (translate to `toolChoice={"any":...}`
   with stricter prompt).
3. **Cache observability.** Provider-side cache hit metrics (`cache_read_tokens`
   field in `LLMResponse.usage`) should be logged for summary calls so we can
   verify the design's cache benefit in production. The infrastructure for
   this is partly in place (see [openai_provider.py:453-455](../../../matmaster/providers/openai_provider.py)
   `_extract_cached_tokens`) but is not currently exposed in compaction event
   payloads. Consider adding to `CompactionEvent` in a follow-up PR.
4. **Iterative compaction quality.** When a session compacts twice, the second
   summary takes a prior `<compacted_history>` block plus new events as input.
   The template instructs the model to merge, but how well this works in
   practice with the main model (versus the dedicated flash model previously
   used) needs production observation.
5. **Stable tool_result replacement (Claude Code parity).** Claude Code's
   `toolResultStorage` persists a `ContentReplacementState` per
   `tool_use_id`: once a tool result is replaced (truncated / offloaded),
   subsequent rounds reuse the exact same replacement string, so the
   prompt-cache prefix remains stable across many turns of the same session.
   This MVP recomputes truncated content from `state.messages` on every
   compaction, so the prefix can drift between compaction rounds even for
   the same underlying tool result. The MVP trade-off is intentional:
   adopting persistent replacement requires changes to (a) transcript /
   checkpoint schema (replacement state must survive resume), (b) subagent
   inheritance (forked agents must observe the same replacements), (c)
   tool-result lifecycle (replacement triggers at result generation, not
   only at compaction time). If production observes (i) repeated
   compactions within a session producing significantly different summaries,
   or (ii) common cache-prefix drift across compaction rounds, consider a
   follow-up PR adopting Claude Code's approach.
6. **API-level user message budget grouping.** Claude Code groups
   tool_result budget by API-level user message (after `normalizeMessages
   ForAPI` merging), not by local `ToolMessage` count. matmaster currently
   treats each `ToolMessage` as a separate entity for budget purposes,
   which can underestimate the true API payload after LiteLLM /
   Anthropic-side merging of consecutive tool_results into one user turn.
   If production observes systematic budget under-estimation (summary
   calls still rejected for context-length despite our pre-budget
   computation), revisit by counting tool_results per LiteLLM-merged user
   group.

## 10. Implementation Order Reference (for planning phase)

The implementation plan should sequence work as follows. Each step is
intended to be atomic enough for one commit; the order minimizes
intermediate broken states.

**Additive foundations (no behavior change to existing paths):**

1. Add `estimate_json_tokens` helper to `compaction.py` (used by §3.3 budget
   computation). Unit-test it on simple JSON structures.
2. Add `_select_tool_safe_tail` helper to `compaction.py` (used by new
   `apply_fallback`). Unit-test independently against tool-pairing
   scenarios.
3. Add `prepare_messages_for_summary_call` + `SummaryInputPreparation`
   dataclass + truncation constants (`_TRUNCATE_HEAD_CHARS`,
   `_TRUNCATE_TAIL_CHARS`, `_TRUNCATE_MIN_CONTENT_CHARS`) to
   `compaction.py`. Unit-test the common-case identity return, the budget
   computation, and the over-budget targeted truncation behavior.
4. Add `call_summary_llm` module function to `compaction.py`. Unit-test
   that it delegates to `prepare_messages_for_summary_call` and forwards
   `tool_choice="none"`.
5. Extend `LLMProvider.chat()` Protocol with keyword-only `tool_choice`
   parameter (default None, backward-compatible). Update
   `openai_provider.chat()` to forward the parameter when non-None. Update
   `bedrock_provider.chat()` per §5.4 (strategy B: pass tools, do not set
   toolChoice).
6. Add `ensure_tool_definitions(spec, state)` helper to `agent.py` (or
   extract to a `kernel_helpers.py` if file size warrants). Unit-test
   identity caching across calls.

**Compactor refactor (replaces existing methods):**

7. Add `ContextCompactor.apply_summary` and `apply_fallback` methods,
   reusing logic split out of the existing `apply_compaction_plan`.
8. Update `run_compaction_plan` (and the two `run_*_compaction_if_needed`
   wrappers) to: accept `tool_definitions` parameter, call
   `call_summary_llm`, route to `apply_summary` on success or
   `apply_fallback` per phase on failure.
9. Update kernel call sites in `agent.py`: invoke `ensure_tool_definitions`
   before preflight (`line 239`), before runtime compaction (`line 257`,
   inside the `while` loop, before each `run_runtime_compaction_if_needed`
   call), and before the main LLM call (replacing the inline
   tool-definitions block at lines 265-285). Pass the resolved
   `tool_definitions` into both compaction sites.

**Cleanup (delete old paths now that new paths are live):**

10. Update `runtime_context_assembly.py` to drop the entire
    `summary_provider = spec.llm_provider; if spec.compaction.compaction_llm: ...`
    block (lines 91-99) and the `summary_provider=summary_provider` kwarg
    in the ContextCompactor construction (line 146).
11. Remove `ContextCompactor.__init__` `summary_provider` parameter, the
    `_summary_provider` field, the `summary_provider` property, the
    `_summarize` method, the `_truncate_tool_results` method, the
    `preflight_if_needed` method, the `compact_if_needed` method, the
    `SUMMARY_SYSTEM_PROMPT` constant. Add the new
    `SUMMARY_USER_REQUEST_TEMPLATE` and the inactive English placeholder.
12. Remove the dual-provider lifecycle ladder from `agent.py` (lines
    105-153 area); collapse to single-level `async with spec.llm_provider:`.
13. Remove `CompactionConfig.compaction_llm` field from
    `types/runtime.py`. Remove `config/llm_config.yaml` `compaction:`
    section.

**Test alignment:**

14. Update all existing tests touching `ContextCompactor` (per §7.1) to
    use the new constructor signature and split `apply_compaction_plan`
    calls into `apply_summary` / `apply_fallback`.
15. Add the new tests per §7.2, especially the **three required
    integration tests** (cache prefix consistency, preflight tool-defs
    consistency, concurrent oversize-truncation invariance) which together
    constitute the value-proof for this design.
