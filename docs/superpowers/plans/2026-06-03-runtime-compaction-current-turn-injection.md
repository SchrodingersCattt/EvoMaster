# Runtime Compaction Current Turn Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 runtime compaction 在摘要完成后，把当前轮用户文本指令逐字重新注入压缩后的 runtime query，同时保持 preflight、checkpoint、covered_until 和附件处理语义不变。

**Architecture:** 本次只解耦 `ContextCompactor.apply_summary()` 里传给 assembler 的 `turn_input`。preflight 继续在可切分时注入完整 `TurnInput`，runtime 在有当轮输入时注入 `instruction_only()` 纯文本变体；runtime 的摘要输入、intent、provider 边界和 durable checkpoint 视图保持原样。

**Tech Stack:** Python 3.11+, uv, pytest, dataclass, Pydantic message models, `AgentKernel`, `ContextCompactor`, MatMaster context assembly.

---

## File Structure

- Modify `matmaster/context/sources/turn_input.py`: add `TurnInput.instruction_only()` so callers can keep only the current instruction text and history boundary while clearing files, workspace paths, images, and image detail.
- Modify `tests/matmaster/context/sources/test_turn_input.py`: add focused unit coverage for `instruction_only()`.
- Modify `matmaster/context/compaction.py`: add `_resolve_injected_turn_input()` and replace only the `turn_input=turn_input if current_split else None` expression in `apply_summary()`.
- Modify `tests/matmaster/context/test_compaction.py`: add runtime current-instruction injection coverage, checkpoint cleanliness coverage, no image part coverage, preflight full-attachment regression coverage, and the preflight-without-current-split corner case.
- Modify `matmaster/core/agent_compaction.py`: let `run_runtime_compaction_if_needed()` accept `turn_input` and forward it into `run_compaction_plan()`; update docstrings that currently say runtime never receives `turn_input`.
- Modify `tests/matmaster/core/test_agent_compaction.py`: prove the runtime compaction helper forwards `turn_input` into the shared compaction runner.
- Modify `matmaster/core/agent.py`: pass the current `turn_input` local into `run_runtime_compaction_if_needed()`.
- Modify `tests/matmaster/core/test_agent_kernel_compaction.py`: prove `AgentKernel` passes the raw current turn input through the runtime compaction path.
- Modify `tests/matmaster/core/test_kernel_runtime_surface.py`: include `turn_input` in the expected parameter surface for `run_runtime_compaction_if_needed()`.

---

### Task 1: Add Instruction-Only TurnInput Variant

**Files:**
- Modify: `matmaster/context/sources/turn_input.py`
- Modify: `tests/matmaster/context/sources/test_turn_input.py`

- [ ] **Step 1: Write the failing `instruction_only()` test**

Add this test after `test_turn_input_default_merges_attachments_into_current_instruction()` in `tests/matmaster/context/sources/test_turn_input.py`:

```python
def test_turn_input_instruction_only_keeps_text_and_drops_attachments() -> None:
    turn_input = TurnInput(
        instruction=TurnInstructionSource(
            user_text=" Analyze current structure. ",
            deferred=True,
        ),
        attachments=TurnAttachmentsSource(
            files=("https://oss.example.com/current.cif",),
            images=("https://oss.example.com/current.png",),
            image_detail="high",
            workspace_paths=("/share/current/POSCAR",),
        ),
        pre_turn_history_event_id=42,
    )

    stripped = turn_input.instruction_only()

    assert stripped.user_text == " Analyze current structure. "
    assert stripped.instruction.deferred is True
    assert stripped.attachments == TurnAttachmentsSource()
    assert stripped.pre_turn_history_event_id == 42
    assert stripped.to_sections()[0].content == "Analyze current structure."
    assert stripped.attachments.images_as_parts() == ()
```

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_turn_input.py::test_turn_input_instruction_only_keeps_text_and_drops_attachments -q
```

Expected: FAIL with `AttributeError: 'TurnInput' object has no attribute 'instruction_only'`.

- [ ] **Step 3: Implement the pure-text variant**

Add this method to `TurnInput` in `matmaster/context/sources/turn_input.py`, directly after `with_deferred_instruction()`:

```python
    def instruction_only(self) -> TurnInput:
        return dataclasses.replace(self, attachments=TurnAttachmentsSource())
```

This deliberately keeps `instruction` and `pre_turn_history_event_id` unchanged and clears every attachment-related carrier in one dataclass replacement.

- [ ] **Step 4: Run the focused test and verify green**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_turn_input.py::test_turn_input_instruction_only_keeps_text_and_drops_attachments -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/sources/turn_input.py tests/matmaster/context/sources/test_turn_input.py
git commit -m "feat: add instruction-only turn input variant"
```

---

### Task 2: Inject Current Text During Runtime Apply Summary

**Files:**
- Modify: `matmaster/context/compaction.py`
- Modify: `tests/matmaster/context/test_compaction.py`

- [ ] **Step 1: Write runtime injection and checkpoint-cleanliness tests**

Add these tests after `test_runtime_compaction_uses_high_water_and_compacted_history_marker()` in `tests/matmaster/context/test_compaction.py`:

```python
@pytest.mark.asyncio
async def test_runtime_compaction_reinjects_current_instruction_text() -> None:
    compactor = make_compactor()
    turn_input = TurnInput.from_values(
        user_text="Run exact fitting with alpha=0.37.",
        files=["https://oss.example.com/current.cif"],
        images=["https://oss.example.com/current.png"],
        image_detail="high",
        workspace_paths=["/share/current/POSCAR"],
        pre_turn_history_event_id=3,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="Run exact fitting with alpha=0.37."),
        AssistantMessage(content="working"),
    ]

    result = await compactor.apply_summary(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
        "Summary only mentions previous context.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert "<compacted_history>" in runtime_content
    assert "<current_instruction>\nRun exact fitting with alpha=0.37.\n</current_instruction>" in runtime_content
    assert "current.cif" not in runtime_content
    assert "current.png" not in runtime_content
    assert "/share/current/POSCAR" not in runtime_content
    assert messages[1].images == []
    assert result.base_messages is not None
    assert "<current_instruction>" not in result.base_messages[0]["content"]
    assert result.checkpoint_covered_until_event_id == 9


@pytest.mark.asyncio
async def test_runtime_compaction_keeps_omitted_current_request_authoritative() -> None:
    compactor = make_compactor()
    turn_input = TurnInput.from_values(
        user_text="Do not relax the cell; only compute static energy.",
        pre_turn_history_event_id=3,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="Do not relax the cell; only compute static energy."),
        AssistantMessage(content="starting calculation"),
    ]

    await compactor.apply_summary(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
        "Previous context says the user asked about FeO.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert "Previous context says the user asked about FeO." in runtime_content
    assert "Do not relax the cell; only compute static energy." in runtime_content
```

- [ ] **Step 2: Write the preflight no-split corner-case test**

Add this test after the two runtime tests:

```python
@pytest.mark.asyncio
async def test_preflight_plan_without_current_split_keeps_runtime_boundary() -> None:
    compactor = make_compactor(boundary=lambda: 33)
    turn_input = TurnInput.from_values(
        user_text="current query",
        pre_turn_history_event_id=7,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="current query"),
    ]

    result = await compactor.apply_summary(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="preflight",
            trigger_tokens=999,
            turn=0,
        ),
        messages,
        "Summary text.",
        turn_input=turn_input,
    )

    runtime_content = messages[1].content or ""
    assert "<current_instruction>" not in runtime_content
    assert result.checkpoint_covered_until_event_id == 33
```

This locks the spec invariant that `intent` is still derived from `current_split`, not directly from `plan.phase`.

- [ ] **Step 3: Strengthen the existing preflight attachment regression**

In `test_preflight_compaction_uses_raw_current_input_without_double_wrap()`, add this assertion after `assert "Use current file." in runtime_content`:

```python
    assert "file_1 current.cif https://oss/current.cif" in runtime_content
```

This proves preflight still injects the complete `turn_input`, including file attachment lines.

- [ ] **Step 4: Run the compaction tests and verify red**

Run:

```bash
uv run pytest \
  tests/matmaster/context/test_compaction.py::test_runtime_compaction_reinjects_current_instruction_text \
  tests/matmaster/context/test_compaction.py::test_runtime_compaction_keeps_omitted_current_request_authoritative \
  tests/matmaster/context/test_compaction.py::test_preflight_plan_without_current_split_keeps_runtime_boundary \
  tests/matmaster/context/test_compaction.py::test_preflight_compaction_uses_raw_current_input_without_double_wrap \
  -q
```

Expected:
- The two runtime tests fail because `runtime_content` lacks `<current_instruction>`.
- The preflight attachment assertion passes because `https://oss/current.cif` renders as `file_1 current.cif https://oss/current.cif`.
- The preflight no-split test passes before implementation and must keep passing after implementation.

- [ ] **Step 5: Add injected turn-input resolver**

Add this helper in `matmaster/context/compaction.py` immediately after `_should_split_current_input_for_preflight()`:

```python
def _resolve_injected_turn_input(
    *,
    phase: Literal["preflight", "runtime"],
    current_split: bool,
    turn_input: TurnInput | None,
) -> TurnInput | None:
    if current_split:
        return turn_input
    if (
        phase == "runtime"
        and turn_input is not None
        and turn_input.has_effective_input()
    ):
        return turn_input.instruction_only()
    return None
```

- [ ] **Step 6: Replace only the assembler turn_input expression**

In `ContextCompactor.apply_summary()`, replace this field:

```python
                turn_input=turn_input if current_split else None,
```

with this field:

```python
                turn_input=_resolve_injected_turn_input(
                    phase=plan.phase,
                    current_split=current_split,
                    turn_input=turn_input,
                ),
```

Do not change:
- `_should_split_current_input_for_preflight()`
- `_summary_base_messages()`
- `intent = PREFLIGHT_COMPACTION if current_split else RUNTIME_COMPACTION`
- `covered_until_event_id` handling
- `apply_fallback()`
- `CURRENT_INPUT_CONTINUATION_INSTRUCTION`

- [ ] **Step 7: Run the focused tests and verify green**

Run:

```bash
uv run pytest \
  tests/matmaster/context/sources/test_turn_input.py::test_turn_input_instruction_only_keeps_text_and_drops_attachments \
  tests/matmaster/context/test_compaction.py::test_runtime_compaction_reinjects_current_instruction_text \
  tests/matmaster/context/test_compaction.py::test_runtime_compaction_keeps_omitted_current_request_authoritative \
  tests/matmaster/context/test_compaction.py::test_preflight_plan_without_current_split_keeps_runtime_boundary \
  tests/matmaster/context/test_compaction.py::test_preflight_compaction_uses_raw_current_input_without_double_wrap \
  -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_compaction.py
git commit -m "feat: inject runtime current instruction after compaction"
```

---

### Task 3: Thread TurnInput Through Runtime Compaction Helper

**Files:**
- Modify: `matmaster/core/agent_compaction.py`
- Modify: `tests/matmaster/core/test_agent_compaction.py`
- Modify: `tests/matmaster/core/test_kernel_runtime_surface.py`

- [ ] **Step 1: Write helper-level forwarding test**

Modify the imports at the top of `tests/matmaster/core/test_agent_compaction.py`:

```python
from matmaster.context.compaction import CompactionPlan
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.agent_compaction import (
    run_compaction_plan,
    run_runtime_compaction_if_needed,
)
```

Add this test after `_plan()`:

```python
@pytest.mark.asyncio
async def test_runtime_compaction_runner_forwards_turn_input(monkeypatch) -> None:
    class RuntimePlanningCompactor(Compactor):
        async def plan_runtime_compaction(
            self,
            messages: list[object],
            turn_usage: dict[str, int],
            *,
            turn: int,
        ) -> CompactionPlan:
            return CompactionPlan(
                compaction_id="root:1",
                compaction_count=1,
                phase="runtime",
                trigger_tokens=123,
                turn=turn,
            )

    captured: dict[str, object] = {}

    async def fake_run_compaction_plan(**kwargs):
        captured.update(kwargs)
        if False:
            yield None

    monkeypatch.setattr(
        "matmaster.core.agent_compaction.run_compaction_plan",
        fake_run_compaction_plan,
    )
    turn_input = TurnInput.from_values(user_text="current request")
    state = _KernelState(
        messages=[SystemMessage(content="sys"), UserMessage(content="old")],
        turn=2,
    )

    items = [
        item
        async for item in run_runtime_compaction_if_needed(
            kernel_spec=_kernel_spec(),
            kernel_resources=_kernel_resources(
                Provider("unused"),
                RuntimePlanningCompactor(),
            ),
            state=state,
            checkpoint_sink=None,
            turn_input=turn_input,
            tool_definitions=None,
        )
    ]

    assert items == []
    assert captured["turn_input"] is turn_input
```

- [ ] **Step 2: Update the runtime helper surface test**

In `tests/matmaster/core/test_kernel_runtime_surface.py`, update `test_run_runtime_compaction_splits_spec_and_resources()`:

```python
def test_run_runtime_compaction_splits_spec_and_resources() -> None:
    params = _params(run_runtime_compaction_if_needed)
    assert "kernel_spec" in params
    assert "kernel_resources" in params
    assert "turn_input" in params
    assert "kernel_runtime" not in params
    assert "spec" not in params
```

- [ ] **Step 3: Run helper tests and verify red**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_compaction.py::test_runtime_compaction_runner_forwards_turn_input \
  tests/matmaster/core/test_kernel_runtime_surface.py::test_run_runtime_compaction_splits_spec_and_resources \
  -q
```

Expected:
- `test_runtime_compaction_runner_forwards_turn_input` fails with `TypeError: run_runtime_compaction_if_needed() got an unexpected keyword argument 'turn_input'`.
- The surface test fails because `turn_input` is not in the function signature.

- [ ] **Step 4: Add the runtime helper parameter**

In `matmaster/core/agent_compaction.py`, update the signature:

```python
async def run_runtime_compaction_if_needed(
    *,
    kernel_spec: AgentKernelSpec,
    kernel_resources: AgentKernelResources,
    state: _KernelState,
    checkpoint_sink: Any,
    turn_input: TurnInput | None = None,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> AsyncIterator[_KernelItem]:
```

Update its docstring to:

```python
    """Plan + execute a runtime compaction between LLM turns when budget exceeded.

    ``turn_input`` is forwarded so summary application can re-inject the
    current instruction text after runtime compaction.
    """
```

When calling `run_compaction_plan()`, add:

```python
                turn_input=turn_input,
```

- [ ] **Step 5: Update the shared runner docstring**

In `run_compaction_plan()`, replace the existing `turn_input` paragraph with:

```python
    ``turn_input`` is used by preflight plans to reattach the full current
    input and by runtime plans to reattach the current instruction text.
```

- [ ] **Step 6: Run helper tests and verify green**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_agent_compaction.py::test_runtime_compaction_runner_forwards_turn_input \
  tests/matmaster/core/test_kernel_runtime_surface.py::test_run_runtime_compaction_splits_spec_and_resources \
  -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add matmaster/core/agent_compaction.py tests/matmaster/core/test_agent_compaction.py tests/matmaster/core/test_kernel_runtime_surface.py
git commit -m "feat: pass turn input to runtime compaction runner"
```

---

### Task 4: Wire AgentKernel Runtime Calls

**Files:**
- Modify: `matmaster/core/agent.py`
- Modify: `tests/matmaster/core/test_agent_kernel_compaction.py`

- [ ] **Step 1: Add a runtime recording compactor test double**

Add this class after `_RecordingTurnInputCompactor` in `tests/matmaster/core/test_agent_kernel_compaction.py`:

```python
class _RecordingRuntimeTurnInputCompactor(_LifecycleCompactor):
    def __init__(self) -> None:
        super().__init__("runtime summary")
        self.seen_turn_inputs: list[Any] = []

    async def apply_summary(
        self,
        plan,
        messages: list[Any],
        summary: str,
        *,
        turn_input=None,
    ):
        self.seen_turn_inputs.append(turn_input)
        return await super().apply_summary(
            plan,
            messages,
            summary,
            turn_input=turn_input,
        )
```

- [ ] **Step 2: Write the kernel runtime forwarding test**

Add this test after `test_kernel_passes_raw_turn_input_to_preflight_compactor()`:

```python
@pytest.mark.asyncio
async def test_kernel_passes_raw_turn_input_to_runtime_compactor():
    from matmaster.core.agent import AgentKernel

    compactor = _RecordingRuntimeTurnInputCompactor()

    async def checkpoint_sink(**kwargs):
        return 42

    turn_input = TurnInput.from_values(
        user_text="runtime original request",
        files=["https://oss.example.com/chat/current.cif"],
        pre_turn_history_event_id=42,
    )
    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        compactor=compactor,
        runtime_ports=KernelRuntimePorts(checkpoint_sink=checkpoint_sink),
    )

    [
        event
        async for event in AgentKernel().run_stream(
            kernel_runtime,
            make_kernel_turn("effective task text", turn_input=turn_input),
            history=[
                UserMessage(content="old question"),
                AssistantMessage(content="old answer"),
            ],
        )
    ]

    assert compactor.seen_turn_inputs
    assert compactor.seen_turn_inputs[0] is turn_input
    assert compactor.seen_turn_inputs[0].user_text == "runtime original request"
    assert compactor.seen_turn_inputs[0].files == (
        "https://oss.example.com/chat/current.cif",
    )
    assert compactor.seen_turn_inputs[0].pre_turn_history_event_id == 42
```

- [ ] **Step 3: Run the kernel forwarding test and verify red**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py::test_kernel_passes_raw_turn_input_to_runtime_compactor -q
```

Expected: FAIL because `compactor.seen_turn_inputs[0]` is `None`.

- [ ] **Step 4: Pass the local turn_input into runtime compaction**

In `matmaster/core/agent.py`, update the `run_runtime_compaction_if_needed()` call inside `_run_items()`:

```python
            async for item in run_runtime_compaction_if_needed(
                kernel_spec=kernel_spec,
                kernel_resources=kernel_resources,
                state=state,
                checkpoint_sink=checkpoint_sink,
                turn_input=turn_input,
                tool_definitions=tool_definitions,
            ):
                yield item
```

- [ ] **Step 5: Run the kernel forwarding test and verify green**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py::test_kernel_passes_raw_turn_input_to_runtime_compactor -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_compaction.py
git commit -m "feat: wire runtime compaction turn input from kernel"
```

---

### Task 5: Run Focused Regression Suite and Hooks

**Files:**
- Verify only; no new source edits expected.

- [ ] **Step 1: Run all tests named by the spec**

Run:

```bash
uv run pytest \
  tests/matmaster/context/sources/test_turn_input.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_kernel_runtime_surface.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run pre-commit on changed files**

Run:

```bash
uv run pre-commit run --files \
  matmaster/context/sources/turn_input.py \
  matmaster/context/compaction.py \
  matmaster/core/agent_compaction.py \
  matmaster/core/agent.py \
  tests/matmaster/context/sources/test_turn_input.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_agent_compaction.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_kernel_runtime_surface.py
```

Expected: PASS for all hooks. If a formatter rewrites files, inspect the diff, then rerun the same command.

- [ ] **Step 3: Inspect the final diff**

Run:

```bash
git diff --stat
git diff -- matmaster/context/sources/turn_input.py matmaster/context/compaction.py matmaster/core/agent_compaction.py matmaster/core/agent.py
```

Expected:
- `TurnInput.instruction_only()` is the only new public method.
- `ContextCompactor.apply_summary()` still derives `intent` from `current_split`.
- Runtime `covered_until_event_id` still comes from `_runtime_covered_until_provider`.
- `apply_fallback()` is unchanged.
- `CURRENT_INPUT_CONTINUATION_INSTRUCTION` remains unconnected.

- [ ] **Step 4: Commit verification-only adjustments if hooks changed formatting**

If Step 2 changed formatting, run:

```bash
git add matmaster/context/sources/turn_input.py matmaster/context/compaction.py matmaster/core/agent_compaction.py matmaster/core/agent.py tests/matmaster/context/sources/test_turn_input.py tests/matmaster/context/test_compaction.py tests/matmaster/core/test_agent_compaction.py tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/core/test_kernel_runtime_surface.py
git commit -m "test: verify runtime compaction current turn injection"
```

If Step 2 did not change formatting, skip this commit.

---

## Self-Review

**Spec coverage**

- Runtime current user text is structurally re-injected: Task 2 adds `_resolve_injected_turn_input()` and runtime tests that use a summary omitting the active request.
- Preflight behavior is unchanged: Task 2 strengthens the existing preflight test to prove full attachment injection still happens and adds the no-current-split corner case.
- Assembly layer is unchanged: no task edits `matmaster/context/assembly.py` or `matmaster/context/compositions.py`.
- Checkpoint durable base stays clean: Task 2 asserts `<current_instruction>` is absent from `result.base_messages[0]["content"]`.
- Runtime attachments and images are not duplicated: Task 1 clears attachments; Task 2 asserts attachment paths and image parts are absent from the runtime injected message.
- Runtime helper and kernel call sites are wired: Tasks 3 and 4 prove `turn_input` reaches the compactor through both helper and kernel paths.
- Fallback path remains unchanged: Task 5 diff inspection explicitly checks `apply_fallback()`.

**Placeholder scan**

This plan contains no open-ended implementation placeholders. Every source edit has a concrete target file, concrete code block, command, and expected result.

**Type consistency**

- `TurnInput.instruction_only() -> TurnInput` is used only after Task 1 defines it.
- `_resolve_injected_turn_input()` uses the existing `Literal["preflight", "runtime"]` type already imported in `matmaster/context/compaction.py`.
- `run_runtime_compaction_if_needed()` receives `turn_input: TurnInput | None = None`, matching `run_compaction_plan()` and `ContextCompactor.apply_summary()`.
- Tests use existing `TurnInput.from_values()`, `CompactionPlan`, `_KernelState`, `SystemMessage`, `UserMessage`, and `AssistantMessage` helpers already present in the same files.
