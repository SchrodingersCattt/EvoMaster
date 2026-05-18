# Phase 0 文件拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 3 个超大文件（`matmaster/core/agent.py` 975 行 / `src/services/agent_run_service.py` 930 行 / `src/services/stream_service.py` 960 行）按 DESIGN.md §14 Phase 0 描述的职责边界抽出 helper 模块，每个文件落到 < 800 行，零行为变化，所有现有测试不动一行（除 import 路径必要更新）。

**Architecture:** 纯 mechanical refactor。每个 helper 抽出走「baseline test → 抽出代码到新 module → 原文件用 import/调用替代 → 跑测试验证等价 → commit」5 步循环。对已被测试直接 import 的私有 helper（`_apply_user_instructions_to_initial_user_query` 等）保留 `agent_run_service.py` 内的 re-export，让现有测试 import 路径不破。对 `AgentKernel._run_compaction_plan` 这种 instance method，保留 thin wrapper 委托到独立模块的自由函数，避免破坏可能存在的 method-mock 测试。提交策略：每个 helper 抽出独立 commit，行数验证作为独立 commit-less 步骤，便于回滚和 review。

**Tech Stack:** Python 3.10+ / pytest / ruff / mypy / uv（仓库内 `uv` 环境）

**对应 ROADMAP**: v3.0 Phase 1 (Prerequisites)，requirements SPLIT-01 / SPLIT-02 / SPLIT-03。

**Spec 来源**: `.planning/context-refactor/DESIGN.md` §14 Phase 0、附录 B「Phase 0 改动」。

---

## 全局约束（每个 Task 必读）

1. **零行为变化**：抽出函数的签名、参数、返回值、副作用必须 100% 等价。函数体逐字搬运，**不允许**借机重构、改名、合并、加 type hint。
2. **现有测试是 ground truth**：不引入新测试。每个 Task 末尾必须跑相关测试目录，全部通过才能 commit。
3. **commit 粒度**：每个 helper 抽出后立即 commit；行数验证（Task 4 / 8 / 11）不 commit。
4. **不动 docs/**：CLAUDE.md 明确说 `docs/` 不能 git 提交。本 phase 不产生 docs。
5. **不动 test 分支**：CLAUDE.md 明确说 test 分支不合并到任何其他分支。本 phase 在当前 `refactor/context` 分支工作。
6. **优先使用 uv 环境**：跑测试用 `uv run pytest ...`；如仓库根 `pyproject.toml` 没声明 uv，再降级到 `python -m pytest`。Task 1 会确认。

---

## Task 1: Setup baseline

**Files:** 无（read-only 探查）

- [ ] **Step 1: 确认测试运行命令**

Run:
```bash
ls /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/pyproject.toml /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/uv.lock 2>&1 | head -5
```

如果有 `uv.lock`，后续所有 `pytest` 命令前缀使用 `uv run`。否则改用 `python -m pytest`（仓库内 Python 环境）。本 plan 后续命令统一写 `uv run pytest`，执行者按实际环境替换。

- [ ] **Step 2: 跑全量测试，记录 baseline**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/ -x -q 2>&1 | tail -30
```

Expected: 全部通过（PASSED）或仅有已知 skip。**如果有 failure，先停下来报告给用户**——baseline 不绿就不要开拆。

- [ ] **Step 3: 记录三个文件初始行数**

Run:
```bash
wc -l /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_service.py /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_service.py
```

Expected (大致)：
```
     975 .../matmaster/core/agent.py
     930 .../src/services/agent_run_service.py
     960 .../src/services/stream_service.py
    2865 total
```

记录这三个数字（实际值可能因细小修改有 ±1 偏差）。Task 4 / 8 / 11 会拿这个 baseline 对比。

- [ ] **Step 4: 确认 git 工作树干净**

Run:
```bash
git status --porcelain
```

Expected: 仅 `.planning/REQUIREMENTS.md` 和 `.planning/context-refactor/DESIGN.md` 这两个 modified（已存在的草稿改动）。如其他文件有未提交改动，先 commit 或 stash。

本 Task 不 commit。

---

## Task 2: 从 agent.py 抽出 `matmaster/core/agent_compaction.py`

**Spec 依据**: DESIGN.md §14 Phase 0a「snapshot/checkpoint sink wiring」+「preflight compaction 装配」。把 `AgentKernel._run_compaction_plan`（89 行）+ kernel 内 preflight/runtime compaction 调度（共约 50 行）抽到独立模块。

**Files:**
- Create: `matmaster/core/agent_compaction.py`
- Modify: `matmaster/core/agent.py:90-178`（删除 `_run_compaction_plan`，留 thin wrapper）
- Modify: `matmaster/core/agent.py:351-409`（preflight + runtime dispatch 替换为 helper 调用）
- Test: `tests/matmaster/core/test_agent_kernel_compaction.py`、`tests/test_chat_events_history_checkpoint.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py tests/test_chat_events_history_checkpoint.py -v 2>&1 | tail -20
```

Expected: PASSED. 这是 mechanical refactor 的等价性基准。

### Step 2: 创建 `matmaster/core/agent_compaction.py`

新文件骨架（请把 `_run_compaction_plan` 的函数体**逐字**从 `agent.py:90-178` 粘贴进 `run_compaction_plan` 函数体；preflight / runtime dispatch 的函数体**逐字**从 `agent.py:351-378` / `agent.py:388-409` 粘贴进相应函数体）：

```python
"""Compaction dispatch helpers extracted from AgentKernel.

Phase 0 refactor: these were inline methods on AgentKernel
(``_run_compaction_plan``, plus the inline preflight/runtime dispatch
blocks in ``_run_items``). They live here as free async generators so
that ``agent.py`` stays under the 800-line target and ``matmaster/context/``
work in later phases has room to grow.

Zero behavior change vs the pre-Phase-0 code paths.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.hooks import CompactionContext, HookEvent
from matmaster.core.kernel_items import _KernelItem, _KernelState
from matmaster.types.current_input import CurrentInputContext
from matmaster.types.events import CompactionEvent

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec

logger = logging.getLogger(__name__)


async def run_compaction_plan(
    *,
    spec: "AgentRuntimeSpec",
    state: _KernelState,
    plan: Any,
    checkpoint_sink: Any,
    current_input_context: CurrentInputContext | None = None,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of AgentKernel._run_compaction_plan (was agent.py:90-178).

    Logic identical to pre-Phase-0 implementation; ``self`` is removed because
    the original body only referenced ``spec`` / ``state`` / ``plan`` /
    ``checkpoint_sink`` / ``current_input_context`` and the module-level
    ``logger``.
    """
    # ── PASTE agent.py lines 99-178 verbatim here ──
    # The body starts with `yield _KernelItem(event=CompactionEvent(...))` and
    # ends with the final `yield _KernelItem(event=CompactionEvent(... covered_until_event_id=covered_until_event_id))`.
    # Do not modify a single line of logic; only the outer signature changed.
    ...


async def run_preflight_compaction_if_needed(
    *,
    spec: "AgentRuntimeSpec",
    state: _KernelState,
    history: list | None,
    current_input_context: CurrentInputContext | None,
    checkpoint_sink: Any,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of the inline preflight dispatch (was agent.py:351-378).

    Original code (inside ``_run_items``):
        if spec.compactor:
            spec.compactor.update_message_count(len(state.messages))
            preflight_planner = getattr(spec.compactor, "plan_preflight_compaction", None)
            if callable(preflight_planner):
                skip_preflight_for_empty_history = (
                    current_input_context is not None
                    and current_input_context.has_effective_input()
                    and not history
                )
                plan = None if skip_preflight_for_empty_history else preflight_planner(state.messages)
                if plan is not None:
                    async for item in self._run_compaction_plan(
                        spec=spec, state=state, plan=plan,
                        checkpoint_sink=checkpoint_sink,
                        current_input_context=current_input_context,
                    ):
                        yield item
            else:
                await spec.compactor.preflight_if_needed(state.messages)
    """
    if not spec.compactor:
        return
    spec.compactor.update_message_count(len(state.messages))
    preflight_planner = getattr(spec.compactor, "plan_preflight_compaction", None)
    if callable(preflight_planner):
        skip_preflight_for_empty_history = (
            current_input_context is not None
            and current_input_context.has_effective_input()
            and not history
        )
        plan = (
            None
            if skip_preflight_for_empty_history
            else preflight_planner(state.messages)
        )
        if plan is not None:
            async for item in run_compaction_plan(
                spec=spec,
                state=state,
                plan=plan,
                checkpoint_sink=checkpoint_sink,
                current_input_context=current_input_context,
            ):
                yield item
    else:
        await spec.compactor.preflight_if_needed(state.messages)


async def run_runtime_compaction_if_needed(
    *,
    spec: "AgentRuntimeSpec",
    state: _KernelState,
    turn_usage: dict,
    checkpoint_sink: Any,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of the inline runtime-compaction dispatch (was agent.py:388-409).

    Original code (inside ``_run_items``):
        if spec.compactor:
            runtime_planner = getattr(spec.compactor, "plan_runtime_compaction", None)
            if callable(runtime_planner):
                plan = await runtime_planner(state.messages, turn_usage, turn=state.turn)
                if plan is not None:
                    async for item in self._run_compaction_plan(
                        spec=spec, state=state, plan=plan, checkpoint_sink=checkpoint_sink,
                    ):
                        yield item
            else:
                await spec.compactor.compact_if_needed(state.messages, turn_usage, state.turn)
    """
    if not spec.compactor:
        return
    runtime_planner = getattr(spec.compactor, "plan_runtime_compaction", None)
    if callable(runtime_planner):
        plan = await runtime_planner(state.messages, turn_usage, turn=state.turn)
        if plan is not None:
            async for item in run_compaction_plan(
                spec=spec,
                state=state,
                plan=plan,
                checkpoint_sink=checkpoint_sink,
            ):
                yield item
    else:
        await spec.compactor.compact_if_needed(
            state.messages, turn_usage, state.turn
        )
```

**关键**：`run_compaction_plan` 函数体必须是 `agent.py:99-178` 的逐字粘贴，包括 `messages_before = len(state.messages)` 那一行起的全部 80 行。不要错过 `_run_compaction_plan` 内部的任何 `await` / `yield` / 异常处理。

### Step 3: 改写 `matmaster/core/agent.py`

应用如下修改：

**3a. 在 `agent.py` 顶部 import 区（第 22-23 行附近）添加**：

```python
from matmaster.core.agent_compaction import (
    run_compaction_plan,
    run_preflight_compaction_if_needed,
    run_runtime_compaction_if_needed,
)
```

**3b. 替换 `_run_compaction_plan` 方法**（当前 `agent.py:90-178`）为 thin wrapper：

```python
    async def _run_compaction_plan(
        self,
        *,
        spec: "AgentRuntimeSpec",
        state: _KernelState,
        plan: Any,
        checkpoint_sink: Any,
        current_input_context: CurrentInputContext | None = None,
    ) -> AsyncIterator[_KernelItem]:
        """Thin wrapper preserved for back-compat with tests that mock the method.

        Logic lives in matmaster.core.agent_compaction.run_compaction_plan.
        """
        async for item in run_compaction_plan(
            spec=spec,
            state=state,
            plan=plan,
            checkpoint_sink=checkpoint_sink,
            current_input_context=current_input_context,
        ):
            yield item
```

**3c. 替换 `_run_items` 内的 preflight 段**（当前 `agent.py:351-378`）：

原 28 行代码块替换为：

```python
        async for item in run_preflight_compaction_if_needed(
            spec=spec,
            state=state,
            history=history,
            current_input_context=effective_current_input_context,
            checkpoint_sink=checkpoint_sink,
        ):
            yield item
```

注意：原代码块的 `checkpoint_sink = spec.runtime_ports.checkpoint_sink` 这一行（`agent.py:349`）应保留在 `_run_items` 内，作为 helper 调用前的局部变量。

**3d. 替换 `_run_items` 内的 runtime compaction 段**（当前 `agent.py:388-409`）：

原 22 行代码块替换为：

```python
            async for item in run_runtime_compaction_if_needed(
                spec=spec,
                state=state,
                turn_usage=turn_usage,
                checkpoint_sink=checkpoint_sink,
            ):
                yield item
```

**3e. 清理 agent.py 顶部不再使用的 import**：

抽出 `_run_compaction_plan` 后，`agent.py` 顶部以下 import 应**保留**（仍被 `_run_items` 等其它代码使用）：
- `import inspect` — 仍用于其他位置？grep 确认；如果没有再删
- `from matmaster.core.hooks import ...` — 保留（`run_stream` 还会用 `UserPromptContext`、`RunContext`）
- `from matmaster.types.events import ..., CompactionEvent, ...` — 保留若 `_run_items` / `run_stream` 仍用其他 event；`CompactionEvent` 可以删

Run:
```bash
grep -n "inspect\." /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py
grep -n "CompactionEvent\|CompactionContext" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py
```

如果 grep 显示 `inspect.` / `CompactionEvent` / `CompactionContext` 在抽出后已无 reference，从 `agent.py` 的 import 区删除对应行。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py tests/test_chat_events_history_checkpoint.py tests/matmaster/core/test_agent_kernel_stream.py -v 2>&1 | tail -25
```

Expected: 全部 PASSED. 如有 failure，先比对原代码 vs 新 helper：函数体差异、参数命名、调用方式。**严禁修改测试**——破的是抽出，不是测试。

### Step 5: 跑 ruff/mypy 静态检查

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check matmaster/core/agent.py matmaster/core/agent_compaction.py 2>&1 | tail -10
```

Expected: 无 error。若有 unused import 警告，删除对应 import 行后重跑。

### Step 6: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/core/agent.py matmaster/core/agent_compaction.py && git commit -m "$(cat <<'EOF'
refactor(agent): extract compaction dispatch to agent_compaction.py

Phase 0 file split (DESIGN.md §14): move _run_compaction_plan and the
inline preflight/runtime compaction dispatch out of AgentKernel so
agent.py stays under the 800-line ceiling. Zero behavior change.

- New: matmaster/core/agent_compaction.py with run_compaction_plan +
  run_preflight_compaction_if_needed + run_runtime_compaction_if_needed
- AgentKernel._run_compaction_plan kept as a thin wrapper for back-compat
  with potential method-mock tests
- _run_items now delegates preflight/runtime compaction to the helpers

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-01
EOF
)"
```

---

## Task 3: 从 agent.py 抽出 `matmaster/core/agent_tool_dispatch.py`

**Spec 依据**: DESIGN.md §14 Phase 0a「tool 调度辅助」。把 `_run_items` 内部的 tool execute_batch + ToolMessage 装配 + ToolResultEvent / SkillHitEvent 发射（53 行），以及配套的 `_validate_tool_call_ids` / `_accumulate_usage` 两个 static helper（19 行），共约 72 行抽到独立模块。

**Files:**
- Create: `matmaster/core/agent_tool_dispatch.py`
- Modify: `matmaster/core/agent.py:547-599`（tool dispatch 段替换）
- Modify: `matmaster/core/agent.py:956-976`（删除 `_validate_tool_call_ids` / `_accumulate_usage`）
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`、`tests/matmaster/core/test_full_tool_runner.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_full_tool_runner_normalize.py -v 2>&1 | tail -20
```

Expected: PASSED.

### Step 2: 创建 `matmaster/core/agent_tool_dispatch.py`

```python
"""Tool dispatch helpers extracted from AgentKernel.

Phase 0 refactor: the inline tool-call loop in ``AgentKernel._run_items``
(execute_batch + ToolMessage append + ToolResultEvent emit + SkillHitEvent
emit) and two adjacent static helpers (``_validate_tool_call_ids``,
``_accumulate_usage``) move here so ``agent.py`` stays under 800 lines.

Zero behavior change vs the pre-Phase-0 code paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from matmaster.core.kernel_items import _KernelItem
from matmaster.core.tool_runner import ToolExecutionContext
from matmaster.types.cancellation import CancellationToken
from matmaster.types.errors import LLMError
from matmaster.types.events import SkillHitEvent, ToolResultEvent
from matmaster.types.messages import ToolCallData, ToolMessage

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec
    from matmaster.core.kernel_items import _KernelState


def validate_tool_call_ids(tool_calls: list[ToolCallData]) -> None:
    """Verbatim move of AgentKernel._validate_tool_call_ids (agent.py:956-969)."""
    # ── PASTE agent.py:957-969 body verbatim ──
    ...


def accumulate_usage(total: dict, delta: dict | None) -> None:
    """Verbatim move of AgentKernel._accumulate_usage (agent.py:972-976)."""
    # ── PASTE agent.py:973-976 body verbatim ──
    ...


async def dispatch_tool_calls(
    *,
    spec: "AgentRuntimeSpec",
    state: "_KernelState",
    tool_calls: Sequence[ToolCallData],
    turn_usage: dict,
    turn_index: int,
    cancel_token: CancellationToken | None,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of the inline tool-dispatch loop (was agent.py:557-599).

    Original code (inside ``_run_items`` after assistant message append):
        if spec.tool_runner is None:
            raise RuntimeError("No tool_runner in AgentRuntimeSpec")
        exec_ctx = ToolExecutionContext(turn=state.turn, max_turns=spec.max_turns, cancel_token=cancel_token)
        runner_results = await spec.tool_runner.execute_batch(response.tool_calls, exec_ctx)
        for tc, tool_result in runner_results:
            state.messages.append(ToolMessage(tool_call_id=tc.id, tool_name=tc.name, content=tool_result.content))
            yield _KernelItem(event=ToolResultEvent(source="agent", call_id=tc.id, ..., total_usage=dict(state.total_usage)))
            if tc.name == "Skill":
                skill_name = tc.arguments.get("skill")
                if isinstance(skill_name, str) and skill_name:
                    yield _KernelItem(event=SkillHitEvent(source="agent", skill_name=skill_name))
    """
    if spec.tool_runner is None:
        raise RuntimeError("No tool_runner in AgentRuntimeSpec")

    exec_ctx = ToolExecutionContext(
        turn=state.turn,
        max_turns=spec.max_turns,
        cancel_token=cancel_token,
    )
    runner_results = await spec.tool_runner.execute_batch(tool_calls, exec_ctx)

    for tc, tool_result in runner_results:
        state.messages.append(
            ToolMessage(
                tool_call_id=tc.id,
                tool_name=tc.name,
                content=tool_result.content,
            )
        )
        yield _KernelItem(
            event=ToolResultEvent(
                source="agent",
                call_id=tc.id,
                tool_name=tc.name,
                result=tool_result.content,
                status=tool_result.status,
                payload=tool_result.payload,
                turn_index=turn_index,
                turn_usage=dict(turn_usage),
                total_usage=dict(state.total_usage),
            )
        )
        if tc.name == "Skill":
            skill_name = tc.arguments.get("skill")
            if isinstance(skill_name, str) and skill_name:
                yield _KernelItem(
                    event=SkillHitEvent(
                        source="agent",
                        skill_name=skill_name,
                    )
                )
```

**关键**：上面 `dispatch_tool_calls` 函数体的代码块已是 `agent.py:557-599` 的逐字内容（只重命名了 `response.tool_calls` 入参 → `tool_calls`、提到 `total_usage`/`turn_usage`/`turn_index` 改由参数传入）。`validate_tool_call_ids` 和 `accumulate_usage` 的 body 请从 `agent.py:957-969` / `agent.py:973-976` 逐字搬运，不要重写。

### Step 3: 改写 `matmaster/core/agent.py`

**3a. 顶部 import 添加**：

```python
from matmaster.core.agent_tool_dispatch import (
    accumulate_usage,
    dispatch_tool_calls,
    validate_tool_call_ids,
)
```

**3b. 删除 `_run_items` 内 line 557-599 的 tool dispatch 段**，替换为：

```python
            async for item in dispatch_tool_calls(
                spec=spec,
                state=state,
                tool_calls=response.tool_calls,
                turn_usage=turn_usage,
                turn_index=turn_index,
                cancel_token=cancel_token,
            ):
                yield item
```

**3c. 把 `_run_items` 内调用 `self._validate_tool_call_ids(response.tool_calls)`（约 line 496）改为**：

```python
            if response.tool_calls:
                validate_tool_call_ids(response.tool_calls)
```

**3d. 把 `_run_items` 内调用 `self._accumulate_usage(state.total_usage, response.usage)`（约 line 466）改为**：

```python
            accumulate_usage(state.total_usage, response.usage)
```

**3e. 删除 AgentKernel 类内的 `_validate_tool_call_ids` static method（agent.py:956-969）和 `_accumulate_usage` static method（agent.py:972-976）**。这两个是 `@staticmethod`，没有测试直接通过 class 名访问的迹象——但保险起见，先 grep：

Run:
```bash
grep -rn "_validate_tool_call_ids\|_accumulate_usage" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/tests /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src
```

Expected: 仅出现在 `matmaster/core/agent.py` 自身。如果出现在 tests/ 下，把对应 test import 路径改为 `from matmaster.core.agent_tool_dispatch import validate_tool_call_ids, accumulate_usage`。如果出现在其他生产代码，**停下来报告**——可能有未发现的耦合。

**3f. 清理已不被引用的 import**：

Run:
```bash
grep -n "ToolMessage\|ToolResultEvent\|SkillHitEvent\|ToolExecutionContext" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py
```

抽出后 `agent.py` 内部不再 yield 这些 event 也不再 append `ToolMessage`，对应 import 可从顶部删除。`from matmaster.core.tool_runner import ToolExecutionContext` 已移到新文件，原 inline `from matmaster.core.tool_runner import ToolExecutionContext`（line 305）可整行删除。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/core/test_full_tool_runner.py tests/matmaster/core/test_full_tool_runner_normalize.py tests/matmaster/core/test_tool_runner_error_wrap.py -v 2>&1 | tail -25
```

Expected: PASSED.

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check matmaster/core/agent.py matmaster/core/agent_tool_dispatch.py 2>&1 | tail -10
```

Expected: 无 error.

### Step 6: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/core/agent.py matmaster/core/agent_tool_dispatch.py && git commit -m "$(cat <<'EOF'
refactor(agent): extract tool dispatch loop to agent_tool_dispatch.py

Phase 0 file split (DESIGN.md §14): move the inline tool-call execute
batch + ToolMessage append + ToolResultEvent/SkillHitEvent emit (was
agent.py:547-599) and the two adjacent static helpers
_validate_tool_call_ids / _accumulate_usage out of AgentKernel.

- New: matmaster/core/agent_tool_dispatch.py with dispatch_tool_calls +
  validate_tool_call_ids + accumulate_usage
- _run_items now delegates the tool loop and uses the free-function
  validators

Zero behavior change; existing kernel + tool runner tests cover the
move.

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-01
EOF
)"
```

---

## Task 4: 从 agent.py 抽出 `matmaster/core/agent_llm_stream.py`

**Spec 依据**: DESIGN.md §14 Phase 0 目标「行数 < 800/文件，预留 Phase 1-3 扩展空间」。Task 2+3 抽完后 agent.py ≈ 820 行（实测 tool dispatch 是 `agent.py:557-599` 共 43 行而非 53；wrapper 与 import 也吃部分收益），仍 > 800。本 Task **必做**：把 LLM streaming 四件套 — `_call_llm_streaming` / `_stream_llm_items` / `_sleep_backoff_with_cancel` / `_response_item` — 一并抽到 `agent_llm_stream.py`。

**为什么要抽四个而不是两个**：`_call_llm_streaming`（`agent.py:602-696`）调 `self._stream_llm_items` 和 `self._sleep_backoff_with_cancel`；`_stream_llm_items`（`agent.py:712-941`）多处调 `self._response_item`（line 784/791/845/849/888/891/897）。抽 2 个留 2 个会跨模块持有 self 引用，反而更乱。一次性搬走四个让 helper 模块自洽，原 kernel 只保留 thin wrapper。

**Files:**
- Create: `matmaster/core/agent_llm_stream.py`
- Modify: `matmaster/core/agent.py:602-696`（删 `_call_llm_streaming`，留 thin wrapper）
- Modify: `matmaster/core/agent.py:699-711`（删 `_sleep_backoff_with_cancel`）
- Modify: `matmaster/core/agent.py:712-941`（删 `_stream_llm_items`）
- Modify: `matmaster/core/agent.py:942-953`（删 `_response_item`）
- Modify: `matmaster/core/agent.py:445-447`（`_run_items` 调用更新）
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/core/test_agent_kernel_finish_diagnostics.py tests/matmaster/core/test_agent_kernel_empty_response_sentinels.py -v 2>&1 | tail -25
```

Expected: PASSED.

### Step 2: 创建 `matmaster/core/agent_llm_stream.py`

新文件包含四个互相调用的自由函数。原 `_call_llm_streaming` / `_stream_llm_items` 都签名复杂、依赖宽广，import 必须完整覆盖原 agent.py 顶部所有相关 symbol：

```python
"""LLM streaming + retry + chunk aggregation extracted from AgentKernel.

Phase 0 refactor (DESIGN.md §14): the LLM call path is the largest single
chunk of agent.py (~340 lines across four methods that call each other via
``self``). Moving them out as free functions brings agent.py under the
800-line target and gives Phase 2+ kernel rewrites room to grow.

Zero behavior change vs the pre-Phase-0 code paths.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.finish_diagnostics import (
    build_finish_detail,
    is_incomplete_response,
)
from matmaster.core.kernel_items import _KernelItem, _KernelStopRequested
from matmaster.response_text import (
    is_empty_response_sentinel_prefix,
    is_trivial_response_text,
    normalize_visible_response_text,
)
from matmaster.types.cancellation import CancellationToken
from matmaster.types.errors import LLMError
from matmaster.types.events import (
    FinishDetail,
    ResponseEvent,
    ThoughtEvent,
)
from matmaster.types.messages import LLMResponse

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec

logger = logging.getLogger(__name__)

# Constants kept verbatim from agent.py:82-84
_STOP_CHECK_EVERY_N_STREAM_CHUNKS = 8
_STOP_RETRY_SLEEP_SLICE_SEC = 0.25


def _response_item(
    content: str,
    stream_id: str,
    stream_state: str | None,
) -> _KernelItem:
    """Verbatim move of AgentKernel._response_item (agent.py:942-953)."""
    return _KernelItem(
        event=ResponseEvent(
            source="agent",
            content=content,
            stream_state=stream_state,
            stream_id=stream_id,
        )
    )


async def _sleep_backoff_with_cancel(
    seconds: float,
    cancel_token: CancellationToken | None,
) -> None:
    """Verbatim move of AgentKernel._sleep_backoff_with_cancel (agent.py:699-711)."""
    # ── PASTE agent.py:704-711 body verbatim ──
    ...


async def stream_llm_items(
    spec: "AgentRuntimeSpec",
    api_messages: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]] | None,
    *,
    timeout: float | None = None,
    cancel_token: CancellationToken | None = None,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of AgentKernel._stream_llm_items (agent.py:712-941).

    The body references ``self._response_item`` at 7 locations; replace each
    with the module-level ``_response_item`` defined above.
    """
    # ── PASTE agent.py:721-940 body verbatim, with the following substitutions ──
    #   self._response_item(...) → _response_item(...)
    # No other changes.
    ...


async def call_llm_streaming(
    spec: "AgentRuntimeSpec",
    api_messages: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]] | None,
    *,
    cancel_token: CancellationToken | None = None,
) -> AsyncIterator[_KernelItem]:
    """Verbatim move of AgentKernel._call_llm_streaming (agent.py:602-696).

    The body references ``self._stream_llm_items`` and
    ``self._sleep_backoff_with_cancel`` — replace each with the module-level
    ``stream_llm_items`` / ``_sleep_backoff_with_cancel`` defined above.
    """
    # ── PASTE agent.py:611-696 body verbatim, with the following substitutions ──
    #   self._stream_llm_items(...) → stream_llm_items(...)
    #   self._sleep_backoff_with_cancel(...) → _sleep_backoff_with_cancel(...)
    # No other changes.
    ...
```

**关键**：
- 四个函数 body 全部 verbatim 搬运；只把 `self.<helper>` 改成模块级名称
- 上面 import 列表已对照 `agent.py:14-77` 顶部所有 dependency 整理过，覆盖 LLM streaming 所需的全部 symbol（`is_incomplete_response`、`is_empty_response_sentinel_prefix`、`normalize_visible_response_text`、`LLMError`、`FinishDetail`、`ThoughtEvent`、`LLMResponse` 等）
- `_response_item` 在新模块中保留下划线前缀（私有约定）；只在 `stream_llm_items` 内被调用，无外部 caller

### Step 3: 改写 `matmaster/core/agent.py`

**3a. 顶部 import 添加**：

```python
from matmaster.core.agent_llm_stream import (
    call_llm_streaming,
    stream_llm_items,
)
```

**3b. 把 `AgentKernel._call_llm_streaming`（line 602-696）替换为 thin wrapper**：

```python
    async def _call_llm_streaming(
        self,
        spec: "AgentRuntimeSpec",
        api_messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]] | None,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncIterator[_KernelItem]:
        """Thin wrapper preserved for back-compat with tests that mock the method."""
        async for item in call_llm_streaming(
            spec, api_messages, tool_defs, cancel_token=cancel_token,
        ):
            yield item
```

**3c. 删除 `_sleep_backoff_with_cancel`（line 699-711）整段**——本方法外部无调用（agent.py 内的调用都在已抽出的 `_call_llm_streaming` 内部）。

**3d. 删除 `_stream_llm_items`（line 712-941）整段**。

**3e. 删除 `_response_item`（line 942-953）整段**——其外部无调用。

**3f. 检查 _run_items 里的 `self._call_llm_streaming` 调用（agent.py:445）保留**，因为 `_call_llm_streaming` 现在还是 thin wrapper instance method。

**3g. 清理 agent.py 顶部不再被引用的 import**：

Run:
```bash
grep -nE "is_incomplete_response|is_empty_response_sentinel_prefix|is_trivial_response_text|normalize_visible_response_text|LLMError|FinishDetail|ThoughtEvent|LLMResponse|_STOP_CHECK_EVERY_N_STREAM_CHUNKS|_STOP_RETRY_SLEEP_SLICE_SEC" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py
```

逐项判断：如果某 symbol 在抽出后只剩 import 行（无其它引用），从顶部 import 删除。`_STOP_CHECK_EVERY_N_STREAM_CHUNKS` / `_STOP_RETRY_SLEEP_SLICE_SEC` 两个模块级常量**必删**（已移到新文件，agent.py 不再需要）。

`is_trivial_response_text` 仍被 `_run_items` 用（line 475），保留 import。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/core/ -x -q 2>&1 | tail -20
```

Expected: 全部 PASSED. LLM streaming 是 kernel 最复杂路径，如有 fail，**严格按 fail message 比对原代码 vs 新代码的差异**——通常是 `self.` 替换漏了一处。

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check matmaster/core/agent.py matmaster/core/agent_llm_stream.py 2>&1 | tail -10
```

Expected: 无 error.

### Step 6: 验证 agent.py < 800

Run:
```bash
wc -l /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py
```

Expected: ≤ 600. 估算 ~530 行（820 抽前 − 95 _call_llm_streaming − 13 _sleep_backoff − 230 _stream_llm_items − 12 _response_item + 12 thin wrapper + 5 import = 487；保守估 ≤ 600）。

**若仍 > 800**：检查 import 清理是否充分；检查抽出是否完整。**不再增加新拆分** — 此时 plan 已穷尽 DESIGN 列举的 scope，问题在执行精度上。

### Step 7: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/core/agent.py matmaster/core/agent_llm_stream.py && git commit -m "$(cat <<'EOF'
refactor(agent): extract LLM streaming path to agent_llm_stream.py

Phase 0 file split (DESIGN.md §14): the LLM call path is the largest
single chunk of agent.py — ~340 lines across four methods that call
each other through ``self``. Moving them out as free functions brings
agent.py under the 800-line target and gives Phase 2+ kernel rewrites
room to grow.

- New: matmaster/core/agent_llm_stream.py with call_llm_streaming +
  stream_llm_items + _sleep_backoff_with_cancel + _response_item
- AgentKernel._call_llm_streaming kept as thin wrapper for back-compat
  with potential method-mock tests
- The three other ex-methods (_sleep_backoff_with_cancel,
  _stream_llm_items, _response_item) have no external callers and are
  removed from AgentKernel

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-01
EOF
)"
```

---

## Task 5: 从 agent_run_service.py 抽出 `src/services/agent_run_instructions.py`

**Spec 依据**: DESIGN.md §14 Phase 0a「instructions loading」。把 user instructions 模板常量（5 个，`agent_run_service.py:69-86`）+ 4 个 helper 函数（`agent_run_service.py:143-220`，共 78 行）抽到独立模块。

**关键约束**：`tests/matmaster/services/test_user_instructions_runtime_injection.py` 直接 import `_apply_user_instructions_to_initial_user_query` 与 `_strip_user_instructions_prefix`（从 `src.services.agent_run_service`）。抽出后必须**保留 `agent_run_service.py` 内的 re-export**，让现有测试 import 路径不破。

**Files:**
- Create: `src/services/agent_run_instructions.py`
- Modify: `src/services/agent_run_service.py:69-86`（删除模板常量，改为 from import）
- Modify: `src/services/agent_run_service.py:143-220`（删除 4 个 helper，保留 re-export 行）
- Modify: `src/services/agent_run_service.py:490`（保留 `_USER_INSTRUCTIONS_PATH` 引用路径 — 通过 import 拿到）
- Modify: `src/services/agent_run_service.py:776`（保留 `_apply_user_instructions_to_initial_user_query` 调用 — 通过 import 拿到）
- Test: `tests/matmaster/services/test_user_instructions_runtime_injection.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_instructions_runtime_injection.py -v 2>&1 | tail -15
```

Expected: PASSED.

### Step 2: 创建 `src/services/agent_run_instructions.py`

```python
"""User instructions injection helpers extracted from agent_run_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a): moved out of
``agent_run_service.py`` so it stays under the 800-line target. These
helpers are the legacy runtime-injection path that Phase 1 will replace
with the AGENT.md hash anchor in user_turn_context events
(DESIGN.md §1c HASH-03). Until then, behavior is unchanged.

NOTE: ``agent_run_service.py`` re-exports the public names from this
module so existing test imports
(``from src.services.agent_run_service import _apply_user_instructions_to_initial_user_query``)
keep working through Phase 1.
"""

from __future__ import annotations

from matmaster.types.messages import Message, UserMessage

# ── PASTE agent_run_service.py:69-86 verbatim (the 5 constants) ──
_USER_INSTRUCTIONS_PATH = '/personal/.matmaster/AGENT.md'
_USER_INSTRUCTIONS_START = (
    f'<matmaster-user-instructions source="{_USER_INSTRUCTIONS_PATH}">'
)
_USER_INSTRUCTIONS_END = '</matmaster-user-instructions>'
_USER_INSTRUCTIONS_TEMPLATE = (
    f"{_USER_INSTRUCTIONS_START}\n"
    "The following content comes from the user's personal instruction file.\n"
    "\n"
    "Treat it as user-level preferences. Follow it when relevant, but do not "
    "let it override system, developer, tool, safety, data-access, or project "
    "constraints.\n"
    "\n"
    "{content}\n"
    f"{_USER_INSTRUCTIONS_END}\n"
    "\n"
    "{user_query}"
)


def _strip_user_instructions_prefix(text: str | None) -> str:
    """Verbatim move of agent_run_service.py:143-159."""
    # ── PASTE agent_run_service.py:144-159 body verbatim ──
    ...


def _find_first_user_message_index(history: list[Message]) -> int | None:
    """Verbatim move of agent_run_service.py:162-167."""
    # ── PASTE agent_run_service.py:163-167 body verbatim ──
    ...


def _render_user_instructions_block(
    *,
    user_instructions: str,
    user_query: str,
) -> str:
    """Verbatim move of agent_run_service.py:170-179."""
    return _USER_INSTRUCTIONS_TEMPLATE.format(
        content=user_instructions,
        user_query=user_query,
    )


def _apply_user_instructions_to_initial_user_query(
    *,
    user_prompt: str,
    user_instructions: str | None,
    history: list[Message],
) -> tuple[str, list[Message]]:
    """Verbatim move of agent_run_service.py:182-220."""
    # ── PASTE agent_run_service.py:194-220 body verbatim ──
    ...
```

**关键**：所有 4 个函数 body 必须逐字搬运。常量值也必须逐字保留——`_USER_INSTRUCTIONS_PATH = '/personal/.matmaster/AGENT.md'` 这个值在 `agent_run_service.py:490` 还被 `_ui_session.read_file(_USER_INSTRUCTIONS_PATH)` 直接用，要确保 import 后此引用仍能解析。

### Step 3: 改写 `src/services/agent_run_service.py`

**3a. 删除原 line 69-86 的常量块**。

**3b. 删除原 line 143-220 的 4 个函数定义**（包括它们之间的空行）。

**3c. 在 line 56-58 附近（`logger = logging.getLogger(__name__)` 之上或之下）添加 re-export import**：

```python
# Phase 0: extracted to agent_run_instructions.py; re-exported here so
# existing test imports keep working. Phase 1 will remove
# _apply_user_instructions_to_initial_user_query entirely (HASH-03).
from src.services.agent_run_instructions import (  # noqa: F401
    _USER_INSTRUCTIONS_END,
    _USER_INSTRUCTIONS_PATH,
    _USER_INSTRUCTIONS_START,
    _USER_INSTRUCTIONS_TEMPLATE,
    _apply_user_instructions_to_initial_user_query,
    _find_first_user_message_index,
    _render_user_instructions_block,
    _strip_user_instructions_prefix,
)
```

**3d. 保留 line 490 的 `_USER_INSTRUCTIONS_PATH` 引用、line 776 的 `_apply_user_instructions_to_initial_user_query` 调用**——它们现在通过 re-export 拿到，无需改源。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_instructions_runtime_injection.py tests/matmaster/services/test_agent_run_stream.py -v 2>&1 | tail -20
```

Expected: PASSED.

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check src/services/agent_run_service.py src/services/agent_run_instructions.py 2>&1 | tail -10
```

Expected: 无 error.（`# noqa: F401` 已抑制 re-export 的 unused warning）

### Step 6: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add src/services/agent_run_service.py src/services/agent_run_instructions.py && git commit -m "$(cat <<'EOF'
refactor(agent-run): extract user instructions helpers

Phase 0 file split (DESIGN.md §14): move the user instructions runtime
injection helpers (4 functions + 5 constants, ~95 lines) from
agent_run_service.py to a dedicated module so the orchestrator file
stays under the 800-line target.

- New: src/services/agent_run_instructions.py with
  _apply_user_instructions_to_initial_user_query and friends
- agent_run_service.py re-exports the public names so existing test
  imports keep working through Phase 1

Phase 1 (HASH-03) will fully retire _apply_user_instructions_to_initial_user_query
once AGENT.md hash anchor becomes the runtime path.

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-02
EOF
)"
```

---

## Task 6: 从 agent_run_service.py 抽出 `src/services/agent_run_history_wiring.py`

**Spec 依据**: DESIGN.md §14 Phase 0a「history restore wiring」。把 `run_agent` Stage 5 中关于 history restore + query events 收集 + checkpoint id 解析 + `_RunSessionEventHistory` inner class + `PlaygroundRuntimePorts` 装配（共约 95 行，`agent_run_service.py:666-758`）抽到独立模块。

**Files:**
- Create: `src/services/agent_run_history_wiring.py`
- Modify: `src/services/agent_run_service.py:666-758`（替换为 helper 调用）
- Test: `tests/matmaster/services/test_agent_run_stream.py`、`tests/matmaster/services/test_history_restore_service.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_history_restore_service.py tests/matmaster/services/test_history_checkpoint_service.py -v 2>&1 | tail -20
```

Expected: PASSED.

### Step 2: 创建 `src/services/agent_run_history_wiring.py`

```python
"""History restore + runtime ports wiring extracted from agent_run_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a): move history restore +
attachment manifest + checkpoint covered_until lookup + the inner
``_RunSessionEventHistory`` adapter + ``PlaygroundRuntimePorts`` assembly
out of ``run_agent`` so ``agent_run_service.py`` stays under the
800-line target.

Phase 1+ (RESTORE-01) will rename ``HistoryRestoreService`` to
``ModelHistoryRestoreService`` and add schema-aware dispatch; this
module is the staging area.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from matmaster.manifests import attachment as attachment_manifest
from matmaster.types.runtime_ports import (
    PlaygroundCompactionPort,
    PlaygroundRuntimePorts,
)
from src.services.history_restore_service import HistoryRestoreService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryWiringResult:
    """Bundle of history-related values produced for a single run_agent call.

    Fields kept positional in ``run_agent`` after the move:
    - history: restored model-visible history
    - attachment_text: rendered ``<available_attachments>`` text (also stored
      in run_meta later)
    - runtime_ports: assembled ``PlaygroundRuntimePorts``
    - bohrium_rebuild_events: events for Bohrium registry rebuild (may be empty)
    """
    history: list
    attachment_text: str
    runtime_ports: PlaygroundRuntimePorts
    bohrium_rebuild_events: list[dict]


def build_history_wiring(
    *,
    events_table: Any | None,
    session_id: str,
    task_id: str,
    raw_history_limit: int,
    child_event_sink: Callable,
    checkpoint_sink_factory: Callable,
    pre_compaction_barrier: Callable,
) -> HistoryWiringResult:
    """Assemble history + attachments + runtime_ports for a single run.

    Verbatim move of agent_run_service.py:666-773 logic (history restore
    + query_events collection + closures + _RunSessionEventHistory +
    PlaygroundRuntimePorts assembly + bohrium_rebuild_events fetch).
    """
    # ── 1. History restore (was agent_run_service.py:667-676) ──
    history = (
        HistoryRestoreService(events_table).restore_history(
            session_id=session_id,
            spawn_id=None,
            task_id=task_id,
            raw_limit=raw_history_limit,
        )
        if events_table is not None
        else []
    )

    # ── 2. Query events + attachments (was 677-693) ──
    query_events: list[dict] = []
    if events_table is not None:
        try:
            raw_query_events = events_table.get_session_user_query_events(session_id)
            query_events = (
                raw_query_events if isinstance(raw_query_events, list) else []
            )
        except Exception:
            logger.warning(
                "attachment manifest: get_session_user_query_events failed for session_id=%s",
                session_id,
                exc_info=True,
            )
    entries = attachment_manifest.build_available_attachments(query_events)
    attachment_text = attachment_manifest.format_available_attachments(entries)

    # ── 3. Closures used by _RunSessionEventHistory (was 695-737) ──
    def _get_query_events() -> list[dict]:
        return list(query_events)

    def _get_all_events() -> list[dict]:
        if events_table is None:
            return []
        try:
            events = events_table.get_session_events(
                session_id,
                limit=raw_history_limit,
            )
            return events if isinstance(events, list) else []
        except Exception:
            logger.warning("manifest: get_session_events failed", exc_info=True)
            return []

    def _get_latest_checkpoint_covered_until_event_id() -> int | None:
        if events_table is None:
            return None
        try:
            checkpoints = events_table.get_history_checkpoints(
                session_id, None, limit=1
            )
        except Exception:
            logger.warning(
                "manifest: get_history_checkpoints failed",
                exc_info=True,
            )
            return None
        if not isinstance(checkpoints, list):
            return None
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            content = checkpoint.get("content")
            if isinstance(content, dict):
                raw = content.get("covered_until_event_id")
                if raw is not None:
                    try:
                        return int(raw)
                    except (TypeError, ValueError):
                        return None
        return None

    class _RunSessionEventHistory:
        def query_events(self) -> list[dict[str, Any]]:
            return _get_query_events()

        def all_events(self) -> list[dict[str, Any]]:
            return _get_all_events()

        def latest_checkpoint_covered_until_event_id(self) -> int | None:
            return _get_latest_checkpoint_covered_until_event_id()

    # ── 4. Runtime ports (was 749-758) ──
    runtime_ports = PlaygroundRuntimePorts(
        child_event_forward_sink=child_event_sink,
        compaction=PlaygroundCompactionPort(
            history=_RunSessionEventHistory(),
            checkpoint_sink_factory=checkpoint_sink_factory,
            pre_compaction_barrier=pre_compaction_barrier,
        ),
    )

    # ── 5. Bohrium rebuild events (was 761-773) ──
    bohrium_rebuild_events: list[dict] = []
    try:
        if events_table is not None:
            bohrium_rebuild_events = events_table.get_bohrium_events(session_id)
    except Exception:
        logger.warning(
            'Failed to load Bohrium events for registry rebuild',
            exc_info=True,
        )

    return HistoryWiringResult(
        history=history,
        attachment_text=attachment_text,
        runtime_ports=runtime_ports,
        bohrium_rebuild_events=bohrium_rebuild_events,
    )
```

**关键**：closures `_get_query_events` / `_get_all_events` / `_get_latest_checkpoint_covered_until_event_id` 与 inner class `_RunSessionEventHistory` 必须**保留在 helper 内部**（不能上提为 module level）——它们 closure over `events_table` / `session_id` / `query_events` 等局部变量。

### Step 3: 改写 `src/services/agent_run_service.py`

**3a. 顶部 import 添加**：

```python
from src.services.agent_run_history_wiring import build_history_wiring
```

**3b. 删除原 line 26（`from matmaster.manifests import attachment as attachment_manifest`）—— 已挪到 helper。同样 line 42-45 的 `PlaygroundCompactionPort` / `PlaygroundRuntimePorts` 也已挪到 helper**，如果 `agent_run_service.py` 本身不再直接 import 它们，删掉。

Run:
```bash
grep -n "attachment_manifest\.\|PlaygroundCompactionPort\|PlaygroundRuntimePorts" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_service.py
```

如果 grep 结果为空（除新加的 history wiring import），删除对应 top-level import 行。

**3c. 替换 line 666-773 的整段为**：

```python
            # -- Stage 5: History --
            wiring = build_history_wiring(
                events_table=events_table,
                session_id=session_id,
                task_id=task_id,
                raw_history_limit=_DIALOG_HISTORY_MAX_EVENTS,
                child_event_sink=_child_event_sink,
                checkpoint_sink_factory=_checkpoint_sink_factory,
                pre_compaction_barrier=fanout.flush_persistence_barrier,
            )
            history = wiring.history
            attachment_text = wiring.attachment_text
            pg_ctx = pg_ctx.with_runtime_ports(wiring.runtime_ports)
            pg_ctx = pg_ctx.with_run_meta(attachment_manifest=attachment_text)
            if wiring.bohrium_rebuild_events:
                pg_ctx = pg_ctx.with_run_meta(
                    bohrium_rebuild_events=wiring.bohrium_rebuild_events,
                )
```

注意：`_child_event_sink` 和 `_checkpoint_sink_factory` 是 `run_agent` 中更早已定义的局部闭包/函数，从 helper 接受为参数。它们的定义保持原位不动。

### Step 3.5: 更新测试 patch path（**关键**，避免 mock 失效）

`HistoryRestoreService` 在 `agent_run_service.py` 顶部 import 已被删除，但现有测试 `mock.patch('src.services.agent_run_service.HistoryRestoreService')` 是 patch-where-it's-looked-up — 抽出后这条 patch 不再拦截 helper 内部使用的真实 `HistoryRestoreService`，会导致 mock 失效、跑到真实 service 逻辑。

定位需要更新的 patch path：

Run:
```bash
grep -rn "src.services.agent_run_service.HistoryRestoreService" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/tests/
```

Expected: 至少命中 `tests/matmaster/services/test_agent_run_stream.py:161`、`:481` 两处。

把所有命中位置的字符串 `'src.services.agent_run_service.HistoryRestoreService'` 改为 `'src.services.agent_run_history_wiring.HistoryRestoreService'`。

确认改写后仍命中目标：

Run:
```bash
grep -rn "src.services.agent_run_history_wiring.HistoryRestoreService" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/tests/
```

Expected: 与上一步的命中数量相同。

注意：`HistoryCheckpointService` 在 `test_agent_run_stream.py:485` 仍 patch 旧路径 `src.services.agent_run_service.HistoryCheckpointService`。`HistoryCheckpointService` 在本 Task 中**未被移动**（仍在 `agent_run_service.py` 顶部 import），patch 路径保持不动。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/ -x -q 2>&1 | tail -15
```

Expected: PASSED. 如有 `HistoryRestoreService.restore_history` 被真实调用的痕迹（如真连 DB 报错），说明 Step 3.5 遗漏 patch 路径，重新 grep。

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check src/services/agent_run_service.py src/services/agent_run_history_wiring.py 2>&1 | tail -10
```

Expected: 无 error.

### Step 6: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add src/services/agent_run_service.py src/services/agent_run_history_wiring.py tests/matmaster/services/test_agent_run_stream.py && git commit -m "$(cat <<'EOF'
refactor(agent-run): extract history restore + runtime ports wiring

Phase 0 file split (DESIGN.md §14): move the run_agent Stage 5 logic
(history restore + attachment manifest + checkpoint covered_until
lookup + _RunSessionEventHistory adapter + PlaygroundRuntimePorts
assembly + bohrium rebuild events fetch, ~95 lines) into a dedicated
module.

- New: src/services/agent_run_history_wiring.py with
  build_history_wiring + HistoryWiringResult dataclass
- run_agent now calls build_history_wiring once and applies the result
- Tests updated to patch HistoryRestoreService at the new lookup path
  (agent_run_history_wiring) since mock.patch resolves by symbol
  location, not import-time alias

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-02
EOF
)"
```

---

## Task 7: 从 agent_run_service.py 抽出 `src/services/agent_run_bohrium_stage.py`

**Spec 依据**: DESIGN.md §14 Phase 0a「bohrium rebuild」。把 `run_agent` Stage 3 中 Bohrium setup + workspace upload 闭包构造（共约 85 行，`agent_run_service.py:223-251` 的 workspace/figure helpers + `446-504` 的 setup 段）抽到独立模块。

**注意**：仓库里**已经存在** `src/services/agent_run_bohrium.py`，里面是 `BohriumSetupService` 类（被 Task 中 import）。新文件命名为 `agent_run_bohrium_stage.py` 区分——前者是底层服务，后者是 run_agent 阶段的装配胶水。

**Files:**
- Create: `src/services/agent_run_bohrium_stage.py`
- Modify: `src/services/agent_run_service.py:223-251`（删除 workspace + figure helpers）
- Modify: `src/services/agent_run_service.py:446-504`（替换 setup 段为 helper 调用）
- Test: `tests/matmaster/services/test_agent_run_stream.py`、`tests/matmaster/services/test_lazy_mcp_replay.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_lazy_mcp_replay.py -v 2>&1 | tail -15
```

Expected: PASSED.

### Step 2: 创建 `src/services/agent_run_bohrium_stage.py`

```python
"""Bohrium stage helpers extracted from agent_run_service.run_agent.

Phase 0 refactor (DESIGN.md §14 Phase 0a): move workspace upload
closure + figure upload config builder + Bohrium setup / context
threading out of ``run_agent`` so the orchestrator stays under the
800-line target.

The actual Bohrium credential + SSH attach logic lives in
``src/services/agent_run_bohrium.py:BohriumSetupService``; this file
hosts only the run-time wiring around that service.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from matmaster.integration.fanout import RunEventFanout
from matmaster.integration.workspace_handler import WorkspaceHandler
from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.figures import FigureUploadConfig
from src.dao.oss_io import upload_bytes_to_oss
from src.services.agent_run_bohrium import BohriumSetupService

logger = logging.getLogger(__name__)

# Path to user instructions inside the Bohrium-mounted personal volume.
# Phase 1 (HASH-02) will replace this with a port-based loader.
from src.services.agent_run_instructions import _USER_INSTRUCTIONS_PATH


@dataclass(frozen=True)
class BohriumStageResult:
    """Return value of ``run_bohrium_stage``.

    abort_result is non-None when the caller should early-return with that
    object (preserves the existing ``run_agent`` short-circuit semantics).
    """
    abort_result: Any | None
    pg_ctx: Any
    ssh_attached: bool
    user_instructions: str | None


def _build_workspace_upload_fn(
    archival_config: WorkspaceArchivalConfig | None,
) -> Callable[..., Any] | None:
    """Verbatim move of agent_run_service.py:223-241."""
    # ── PASTE agent_run_service.py:225-241 body verbatim ──
    ...


def _build_figure_upload_config(
    *, session_id: str, task_id: str,
) -> FigureUploadConfig:
    """Verbatim move of agent_run_service.py:244-251."""
    # ── PASTE agent_run_service.py:246-251 body verbatim ──
    ...


async def run_bohrium_stage(
    *,
    sessions_service: Any,
    fanout: RunEventFanout,
    dispatch_from_thread: Callable,
    session_id: str,
    task_id: str,
    playground: Any,
    pg_ctx: Any,
    run_started_at: datetime,
    bohrium_required: bool,
    remote_workdir: str | None,
) -> BohriumStageResult:
    """Verbatim move of the inline Bohrium Stage 3 (was agent_run_service.py:446-504).

    Steps:
    1. Construct BohriumSetupService bound to the event sink
    2. Invoke run_setup()
    3. If abort_result returned, propagate it unchanged
    4. Thread bohrium meta and execution session into pg_ctx
    5. Best-effort read of user instructions from the mounted volume
    6. Register WorkspaceHandler in the fanout
    """
    bohrium_svc = BohriumSetupService(
        sessions_service,
        event_sink=dispatch_from_thread,
    )
    effective_bohrium_required = bool(bohrium_required or remote_workdir)
    bohrium_result = await bohrium_svc.run_setup(
        session_id=session_id,
        playground=playground,
        run_started_at=run_started_at,
        bohrium_required=effective_bohrium_required,
        remote_workdir=remote_workdir,
    )
    ssh_attached = bohrium_result.ssh_attached
    if bohrium_result.abort_result is not None:
        return BohriumStageResult(
            abort_result=bohrium_result.abort_result,
            pg_ctx=pg_ctx,
            ssh_attached=ssh_attached,
            user_instructions=None,
        )
    bohrium_meta = (
        bohrium_result.runtime_snapshot.model_dump()
        if bohrium_result.runtime_snapshot is not None
        else {}
    )
    pg_ctx = pg_ctx.with_bohrium(bohrium_meta)
    if bohrium_result.execution_session is not None:
        execution_workdir = bohrium_result.execution_workdir or ''
        session_type = bohrium_result.session_type or 'ssh'
        pg_ctx = pg_ctx.with_execution(
            session=bohrium_result.execution_session,
            session_type=session_type,
            execution_workdir=execution_workdir,
        )

    # User instructions best-effort read (was 483-493)
    user_instructions: str | None = None
    _ui_session = (
        bohrium_result.execution_session if bohrium_result else None
    ) or pg_ctx.session
    if _ui_session is not None:
        try:
            user_instructions = (
                _ui_session.read_file(_USER_INSTRUCTIONS_PATH).strip() or None
            )
        except Exception as _ui_err:
            logger.debug('read user instructions skipped: %s', _ui_err)

    # Workspace handler registration (was 495-504)
    fanout.add_handler(
        WorkspaceHandler(
            session_id=session_id,
            task_id=task_id,
            ssh_attached=ssh_attached,
            workspace_path=pg_ctx.workdir,
            upload_fn=_build_workspace_upload_fn(pg_ctx.archival),
        )
    )

    return BohriumStageResult(
        abort_result=None,
        pg_ctx=pg_ctx,
        ssh_attached=ssh_attached,
        user_instructions=user_instructions,
    )
```

### Step 3: 改写 `src/services/agent_run_service.py`

**3a. 顶部 import 添加**：

```python
from src.services.agent_run_bohrium_stage import (
    _build_figure_upload_config,
    run_bohrium_stage,
)
```

**3b. 删除原 line 223-251**（两个 helper 函数）。

**3c. 替换 line 446-504**（整段 Stage 3）为：

```python
            # -- Stage 3: Bohrium credentials + SSH --
            loop = asyncio.get_running_loop()

            def _dispatch_from_thread(event: BusEvent) -> None:
                fanout.dispatch_from_thread(loop, event)

            stage_result = await run_bohrium_stage(
                sessions_service=self._sessions_service,
                fanout=fanout,
                dispatch_from_thread=_dispatch_from_thread,
                session_id=session_id,
                task_id=task_id,
                playground=playground,
                pg_ctx=pg_ctx,
                run_started_at=run_started_at,
                bohrium_required=bohrium_required,
                remote_workdir=remote_workdir,
            )
            if stage_result.abort_result is not None:
                return stage_result.abort_result
            pg_ctx = stage_result.pg_ctx
            ssh_attached = stage_result.ssh_attached
            user_instructions = stage_result.user_instructions
```

**3d. 清理 `agent_run_service.py` 顶部不再用的 import**：

Run:
```bash
grep -n "BohriumSetupService\|WorkspaceArchivalConfig\|WorkspaceHandler\|FigureUploadConfig\|upload_bytes_to_oss" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_service.py
```

如果 grep 显示这些只在已删除的代码段中使用，从 `agent_run_service.py` 顶部 import 区删除：
- `from matmaster.integration.workspace_handler import WorkspaceHandler`（line 25）
- `from matmaster.types.context import WorkspaceArchivalConfig`（line 29）
- `from matmaster.types.figures import FigureUploadConfig`（line 40）
- `from src.dao.oss_io import upload_bytes_to_oss`（line 47）
- `from src.services.agent_run_bohrium import BohriumSetupService`（line 49）

注意：保留 `_build_figure_upload_config` 通过 re-import 引入，因为 `run_agent` 在其他位置（在 Exp assembly 内）可能调用它。Grep 确认：

Run:
```bash
grep -n "_build_figure_upload_config" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_service.py
```

如果还有 caller，保留 `from src.services.agent_run_bohrium_stage import _build_figure_upload_config` import。

**3e. 为 `_build_workspace_upload_fn` 在 `agent_run_service.py` 加 re-export**：

测试 `tests/matmaster/integration/test_agent_run_service_workspace_upload.py:10` 直接 `from src.services.agent_run_service import _build_workspace_upload_fn`。抽出后必须保留 import 兼容：

在 `agent_run_service.py` 顶部（紧邻其他 helper 的 import）加：

```python
# Phase 0: extracted to agent_run_bohrium_stage.py; re-exported for the
# integration test that imports _build_workspace_upload_fn directly.
from src.services.agent_run_bohrium_stage import (  # noqa: F401
    _build_workspace_upload_fn,
)
```

### Step 3.5: 更新测试 patch path（**关键**，避免 mock 失效）

`BohriumSetupService` 与 `WorkspaceHandler` 从 `agent_run_service.py` 顶部 import 删除后，所有 `mock.patch('src.services.agent_run_service.BohriumSetupService')` / `mock.patch('src.services.agent_run_service.WorkspaceHandler')` 这类 patch 将失效，导致 mock 不再拦截真实 `BohriumSetupService.run_setup()`（可能触发真实 Bohrium 凭证查询）。

定位需要更新的 patch path：

Run:
```bash
grep -rn "src.services.agent_run_service.BohriumSetupService\|src.services.agent_run_service.WorkspaceHandler" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/tests/
```

Expected: 命中 `tests/matmaster/services/test_agent_run_stream.py:158`、`:159`、`tests/matmaster/integration/test_bohrium_execution_contract.py:417`、`:531`。

按映射改写：
- `'src.services.agent_run_service.BohriumSetupService'` → `'src.services.agent_run_bohrium_stage.BohriumSetupService'`
- `'src.services.agent_run_service.WorkspaceHandler'` → `'src.services.agent_run_bohrium_stage.WorkspaceHandler'`

注意：`test_bohrium_execution_contract.py` 同时 patch `'src.services.agent_run_service.get_chat_events_table'`、`'src.services.agent_run_service.get_redis_dao'`、`'src.services.agent_run_service.use_quota'` 等 — 这些**仍在 `agent_run_service.py` 顶部 import**，patch path 不动。

验证：

Run:
```bash
grep -rn "src.services.agent_run_bohrium_stage.BohriumSetupService\|src.services.agent_run_bohrium_stage.WorkspaceHandler" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/tests/
```

Expected: 命中数 = 前一步映射的总数。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/ tests/matmaster/integration/test_bohrium_execution_contract.py tests/matmaster/integration/test_agent_run_service_workspace_upload.py -x -q 2>&1 | tail -20
```

Expected: PASSED. 如有 `Bohrium credential fetch failed`、`unable to read /personal/.matmaster/AGENT.md` 这类痕迹，说明 patch 未生效，重新检查 Step 3.5 的映射。

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check src/services/agent_run_service.py src/services/agent_run_bohrium_stage.py 2>&1 | tail -10
```

Expected: 无 error.

### Step 6: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add src/services/agent_run_service.py src/services/agent_run_bohrium_stage.py tests/matmaster/services/test_agent_run_stream.py tests/matmaster/integration/test_bohrium_execution_contract.py && git commit -m "$(cat <<'EOF'
refactor(agent-run): extract Bohrium stage wiring

Phase 0 file split (DESIGN.md §14): move workspace + figure upload
helpers and the Bohrium stage assembly (credential setup + execution
context threading + user-instructions read + WorkspaceHandler register,
~85 lines) into a dedicated module.

- New: src/services/agent_run_bohrium_stage.py with run_bohrium_stage,
  _build_workspace_upload_fn, _build_figure_upload_config,
  BohriumStageResult
- run_agent Stage 3 now calls run_bohrium_stage once and propagates
  abort_result on early-return
- agent_run_service.py re-exports _build_workspace_upload_fn for the
  workspace-upload integration test
- Tests updated to patch BohriumSetupService / WorkspaceHandler at the
  new lookup path (agent_run_bohrium_stage)

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-02
EOF
)"
```

---

## Task 8: Verify agent_run_service.py < 800 行

- [ ] **Step 1: 重新计算行数**

Run:
```bash
wc -l /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_service.py
```

Expected: ≤ 800. 估算值 ~700 行（930 − 100 instructions − 95 history wiring − 85 bohrium stage + 30 调用/import = 680；保守估 ≤ 750）。

- [ ] **Step 2: 跑全量 services 测试目录**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/ -x -q 2>&1 | tail -10
```

Expected: 全部 PASSED.

本 Task 不 commit.

---

## Task 9: 从 stream_service.py 抽出 `src/services/stream_sse_filter.py`

**Spec 依据**: DESIGN.md §14 Phase 0a「SSE filter 逻辑」。把 5 个 SSE filter helper 函数（`stream_service.py:66-199`）+ `ChatStreamService._inject_elapsed_for_history` 静态方法（`stream_service.py:308-327`）抽到独立模块。

**Files:**
- Create: `src/services/stream_sse_filter.py`
- Modify: `src/services/stream_service.py:66-199`（删除 5 个函数）
- Modify: `src/services/stream_service.py:308-327`（删除 `_inject_elapsed_for_history` 静态方法）
- Modify: `src/services/stream_service.py:517-522`、`:792-797`（更新 caller 用 helper module）
- Test: `tests/test_chat_stream_direct.py`、`tests/test_chat_stream_session_directory.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/test_chat_stream_direct.py tests/test_chat_stream_session_directory.py tests/test_chat_stream_reply_events.py -v 2>&1 | tail -20
```

Expected: PASSED.

### Step 2: 创建 `src/services/stream_sse_filter.py`

```python
"""SSE event filter + normalization extracted from stream_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a): move ``_should_emit_event_to_sse``
and friends out of ``stream_service.py`` so the file stays under the
800-line target. Phase 1 (EVT-02) will extend
``should_emit_event_to_sse`` here to also hide ``user_turn_context``
events, mirroring the live ``SSEHandler._should_skip()`` policy.

The helpers were named with a leading underscore in the original
module; we keep the public API underscored too to avoid disturbing
callers that import nothing from this filter (it is internal to
stream_service).
"""

from __future__ import annotations

from matmaster.integration.event_payloads import normalize_response_sse_payload
from matmaster.utils.event_source import normalize_event_source


def _should_emit_event_to_sse(event: dict) -> bool:
    """Verbatim move of stream_service.py:66-93."""
    # ── PASTE stream_service.py:67-93 body verbatim ──
    ...


def _normalize_replayed_event(event: dict) -> dict:
    """Verbatim move of stream_service.py:96-100."""
    # ── PASTE stream_service.py:97-100 body verbatim ──
    ...


def _normalize_replayed_compaction_events(events: list[dict]) -> list[dict]:
    """Verbatim move of stream_service.py:103-148."""
    # ── PASTE stream_service.py:104-148 body verbatim ──
    ...


def _replay_terminal_dedupe_key(
    event: dict,
) -> tuple[str, str | None] | None:
    """Verbatim move of stream_service.py:151-159."""
    # ── PASTE stream_service.py:152-159 body verbatim ──
    ...


def _dedupe_replayed_terminal_events(
    events: list[dict],
) -> list[dict]:
    """Verbatim move of stream_service.py:162-199."""
    # ── PASTE stream_service.py:163-199 body verbatim ──
    # Note: the original body calls _should_emit_event_to_sse defined
    # above in this same module, so no import changes are needed inside.
    ...


def _inject_elapsed_for_history(events: list[dict]) -> list[dict]:
    """Verbatim move of ChatStreamService._inject_elapsed_for_history (stream_service.py:308-327).

    Originally a @staticmethod on ChatStreamService; here it is a module
    function. Callers update accordingly.
    """
    # ── PASTE stream_service.py:309-327 body verbatim ──
    ...
```

### Step 3: 改写 `src/services/stream_service.py`

**3a. 删除原 line 66-199 的 5 个函数定义**。

**3b. 删除原 line 308-327 的 `_inject_elapsed_for_history` 静态方法**（含 `@staticmethod` decorator 行）。

**3c. 顶部 import 添加（替代被删 helpers 的依赖）**：

```python
from src.services.stream_sse_filter import (
    _dedupe_replayed_terminal_events,
    _inject_elapsed_for_history,
    _normalize_replayed_compaction_events,
    _normalize_replayed_event,
    _should_emit_event_to_sse,
)
```

**3d. 检查 line 18-20 的两个 import**：

```python
from matmaster.integration.event_payloads import normalize_response_sse_payload
from matmaster.utils.event_source import normalize_event_source
```

抽出后这两个 import 在 `stream_service.py` 内可能不再使用。Grep 确认：

Run:
```bash
grep -n "normalize_response_sse_payload\|normalize_event_source" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_service.py
```

如果只在已删除代码中使用，删除对应 import 行。

**3e. 更新 caller**：

- Line 517-522（`generate_subscribe_stream` 内）：原来调 `_normalize_replayed_compaction_events(events)` / `_dedupe_replayed_terminal_events(events)` / `_should_emit_event_to_sse(event)` / `_normalize_replayed_event(event)`，现在通过 import 直接拿到，**无需改函数体**——只要 step 3c 的 import 添加了，原代码继续工作。
- Line 792-797（`generate_send_stream` 内）：同上。
- 原 `_inject_elapsed_for_history` 是 `ChatStreamService` 的 staticmethod，被以 `self._inject_elapsed_for_history(events)` 或 `ChatStreamService._inject_elapsed_for_history(events)` 形式调用。Grep 找出所有 call site：

Run:
```bash
grep -n "_inject_elapsed_for_history" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_service.py
```

把每个 `self._inject_elapsed_for_history(events)` 改为 `_inject_elapsed_for_history(events)`，把 `ChatStreamService._inject_elapsed_for_history(events)` 改为 `_inject_elapsed_for_history(events)`。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/test_chat_stream_direct.py tests/test_chat_stream_session_directory.py tests/test_chat_stream_reply_events.py -v 2>&1 | tail -25
```

Expected: PASSED.

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check src/services/stream_service.py src/services/stream_sse_filter.py 2>&1 | tail -10
```

Expected: 无 error.

### Step 6: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add src/services/stream_service.py src/services/stream_sse_filter.py && git commit -m "$(cat <<'EOF'
refactor(stream): extract SSE filter helpers

Phase 0 file split (DESIGN.md §14): move the 5 SSE replay filter
functions (_should_emit_event_to_sse, _normalize_replayed_event,
_normalize_replayed_compaction_events, _replay_terminal_dedupe_key,
_dedupe_replayed_terminal_events) plus ChatStreamService's
_inject_elapsed_for_history static helper (~155 lines) out of
stream_service.py.

- New: src/services/stream_sse_filter.py
- stream_service.py imports the helpers and the call sites continue to
  use the same underscored names

Phase 1 (EVT-02) will extend _should_emit_event_to_sse here to also
hide user_turn_context events.

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-03
EOF
)"
```

---

## Task 10: 抽 `RedisReplyQueue` 到 `stream_reply_queue.py` + verify stream_service.py < 800

**Spec 标注**: DESIGN.md §14 Phase 0 对 `stream_service.py` 的明示拆分项是 SSE replay filter，见附录 B「Phase 0 改动」。本 Task 抽 `RedisReplyQueue` 属于 **spec-additional mechanical split** — 不在 DESIGN 列举范围，但有两条 rationale：

1. **行数硬目标**：Task 9 抽完 SSE filter 后估算 stream_service.py ≈ 815-825 行（960 − 135 SSE helpers − 20 `_inject_elapsed_for_history` + 5 import + 5 caller 调整 ≈ 815），仍 > 800
2. **逻辑独立**：`RedisReplyQueue` 实现 `ReplyQueueLike` Protocol，与 SSE replay/streaming 管道无任何函数级耦合（只通过 Protocol 接口暴露），抽出干净

如果 Task 9 后实测 wc -l ≤ 800（不太可能但理论存在），仍执行本 Task — 现在的 ~15 行 buffer 留给 Phase 1 扩展（EVT-02 加 user_turn_context 到 hidden list、HASH-* 装配）。

**Files:**
- Create: `src/services/stream_reply_queue.py`
- Modify: `src/services/stream_service.py:22`（清理 `INTERACTION_CANCEL_VALUE` import）
- Modify: `src/services/stream_service.py:202-223`（删 `RedisReplyQueue` 类）
- Test: `tests/test_chat_stream_reply_events.py`、`tests/test_chat_stream_direct.py`

### Step 1: 跑相关 baseline 测试

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/test_chat_stream_reply_events.py tests/test_chat_stream_direct.py -v 2>&1 | tail -15
```

Expected: PASSED.

### Step 2: 创建 `src/services/stream_reply_queue.py`

```python
"""RedisReplyQueue extracted from stream_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a, spec-additional split): the
RedisReplyQueue is logically independent of the SSE replay/streaming
pipeline (it implements the ReplyQueueLike Protocol used by
interaction-reply paths). Moving it out keeps stream_service.py under
the 800-line target with buffer for Phase 1 SSE filter extensions
(EVT-02 user_turn_context filtering).
"""

from __future__ import annotations

import queue

from src.dao.redis_dao import INTERACTION_CANCEL_VALUE, get_redis_dao


class RedisReplyQueue:
    """Verbatim move of stream_service.py:202-223.

    Original body references ``queue.Empty`` (line 215) and
    ``INTERACTION_CANCEL_VALUE`` (line 213, 221) — both imports are now
    local to this module.
    """
    # ── PASTE stream_service.py:203-223 body verbatim ──
    ...
```

**关键**：`import queue` **必须**包含 — 原 `RedisReplyQueue.get()` 在 timeout 后 `raise queue.Empty`（见 `stream_service.py:215`）。

### Step 3: 改写 `src/services/stream_service.py`

**3a. 删除原 line 202-223 的 `RedisReplyQueue` 类定义**。

**3b. 顶部 import 调整**：

原 line 21-25：
```python
from src.dao.redis_dao import (
    INTERACTION_CANCEL_VALUE,
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
)
```

确认 `INTERACTION_CANCEL_VALUE` 在 stream_service.py 内的其它使用位置：

Run:
```bash
grep -n "INTERACTION_CANCEL_VALUE" /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_service.py
```

Expected: 抽出后只剩 import line（22）。若仅 import 行，把 `INTERACTION_CANCEL_VALUE,` 从该 import 块删除：

```python
from src.dao.redis_dao import (
    STREAM_CHANNEL_PREFIX,
    get_redis_dao,
)
```

**3c. 添加 `RedisReplyQueue` re-export**：

`agent_run_service.py:654` 用 `from src.services.stream_service import RedisReplyQueue`，需保持兼容。在 stream_service.py 顶部（与其他 helper import 同区域）加：

```python
# Phase 0: extracted to stream_reply_queue.py; re-exported here so
# existing callers (agent_run_service.py and chat tests) keep working.
from src.services.stream_reply_queue import RedisReplyQueue  # noqa: F401
```

注：此 re-export 让 `from src.services.stream_service import RedisReplyQueue` 这类 import 继续有效；无需修改 caller。

### Step 4: 跑 targeted tests

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/test_chat_stream_reply_events.py tests/test_chat_stream_direct.py tests/test_chat_stream_session_directory.py -v 2>&1 | tail -15
```

Expected: PASSED.

### Step 5: ruff/mypy

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check src/services/stream_service.py src/services/stream_reply_queue.py 2>&1 | tail -10
```

Expected: 无 error.

### Step 6: 验证 stream_service.py < 800

Run:
```bash
wc -l /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_service.py
```

Expected: ≤ 790. 估算 ~793 行（825 抽前 − 22 RedisReplyQueue − 1 INTERACTION_CANCEL_VALUE import + 2 re-export = 804；保守 ≤ 800）。

### Step 7: Commit

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add src/services/stream_service.py src/services/stream_reply_queue.py && git commit -m "$(cat <<'EOF'
refactor(stream): extract RedisReplyQueue (Phase 0 size buffer)

Phase 0 file split (DESIGN.md §14, spec-additional): the
RedisReplyQueue is logically independent of the SSE replay/streaming
pipeline. Moving it out keeps stream_service.py under the 800-line
target after the SSE-filter split, with buffer for Phase 1 extensions.

- New: src/services/stream_reply_queue.py
- stream_service.py re-exports RedisReplyQueue so
  ``from src.services.stream_service import RedisReplyQueue`` callers
  keep working (used by agent_run_service.py:654)
- INTERACTION_CANCEL_VALUE import removed from stream_service.py (now
  only used inside the extracted RedisReplyQueue class)

Refs: .planning/context-refactor/DESIGN.md §14 Phase 0a, SPLIT-03
EOF
)"
```

---

## Task 11: Final verification

- [ ] **Step 1: 跑全量测试套件**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/ -x -q 2>&1 | tail -30
```

Expected: 全部 PASSED. 与 Task 1 Step 2 baseline 对比，PASSED 数量必须**完全相等**或更多（如果新文件被自动测试目录拾起算入）。

- [ ] **Step 2: 所有目标文件 + 新文件最终行数确认**

Run:
```bash
wc -l \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent_compaction.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent_tool_dispatch.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/matmaster/core/agent_llm_stream.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_service.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_instructions.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_history_wiring.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/agent_run_bohrium_stage.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_service.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_sse_filter.py \
  /Users/kealdoom/Developer/dp/matmaster/matmaster-evo/src/services/stream_reply_queue.py
```

Expected:
- `agent.py` / `agent_run_service.py` / `stream_service.py` 三个原文件均 ≤ 800 行 ✅
- `agent_llm_stream.py` 约 340 行（最大的新 helper，含 LLM streaming 全套）
- 其余 helper 文件 50-250 行
- 任何新 helper 超过 400 行 → 拆分粒度可能不对，但不阻断 Phase 0 完工

- [ ] **Step 3: 检查无循环 import**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "
import matmaster.core.agent
import matmaster.core.agent_compaction
import matmaster.core.agent_tool_dispatch
import matmaster.core.agent_llm_stream
import src.services.agent_run_service
import src.services.agent_run_instructions
import src.services.agent_run_history_wiring
import src.services.agent_run_bohrium_stage
import src.services.stream_service
import src.services.stream_sse_filter
import src.services.stream_reply_queue
print('OK')
"
```

Expected: `OK`. 若出现 `ImportError: cannot import name ...` 或循环 import 错误，说明某个 helper 的 import 方向不对（typically helper import 了原文件的某个符号）。

- [ ] **Step 4: 静态检查全套**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run ruff check matmaster/core/ src/services/ 2>&1 | tail -15
```

Expected: 无 new error（与 Phase 0 之前的 ruff 状态对比；现有遗留 warning 可保留）。

- [ ] **Step 5: 烟雾测试 — `mm-devshell` import 链路**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -c "from matmaster.core.exp import Exp; from matmaster.core.agent import AgentKernel; from src.services.agent_run_service import AgentRunService; from src.services.stream_service import ChatStreamService; print('matmaster import chain OK')"
```

Expected: `matmaster import chain OK`. 此命令模拟 dev shell 启动时的 import 链路，确保拆分后核心入口可加载。

- [ ] **Step 6: git log 概览**

Run:
```bash
git log --oneline -n 10
```

Expected: 看到 6-8 个 `refactor(...)` commit（每个抽出一条；可能含 1-2 个兜底 commit）。

- [ ] **Step 7: 更新 ROADMAP / STATE（可选，由 GSD workflow 决定）**

如果项目用 GSD 管理状态，把 `.planning/STATE.md` 与 ROADMAP 中 Phase 1 状态推进。本 Task 不强制——也可留给 `/gsd-progress` 之类指令统一处理。

本 Task 不 commit.

---

## Plan 完成验收

执行完 Task 1-11 后，应满足：

1. `matmaster/core/agent.py` ≤ 800 行（预计 ~530 行，含 LLM streaming 抽出后大量瘦身）✅
2. `src/services/agent_run_service.py` ≤ 800 行（预计 ~700 行）✅
3. `src/services/stream_service.py` ≤ 800 行（预计 ~790 行）✅
4. 新增 helper 模块（全部必做）：
   - `matmaster/core/agent_compaction.py`（Task 2）
   - `matmaster/core/agent_tool_dispatch.py`（Task 3）
   - `matmaster/core/agent_llm_stream.py`（Task 4）
   - `src/services/agent_run_instructions.py`（Task 5）
   - `src/services/agent_run_history_wiring.py`（Task 6）
   - `src/services/agent_run_bohrium_stage.py`（Task 7）
   - `src/services/stream_sse_filter.py`（Task 9）
   - `src/services/stream_reply_queue.py`（Task 10，spec-additional）
5. 所有原 `tests/` 测试通过 ✅
6. 静态检查（ruff）无新增 error ✅
7. 无循环 import ✅
8. 每个抽出独立 commit（预计 8 次 `refactor(...)` commits），便于 review / 回滚 ✅
9. 测试 import 路径与 mock patch 路径全部正确：
   - re-export shim 兼容 `_apply_user_instructions_to_initial_user_query` 等私有名 import
   - patch path 更新到新 helper 模块（`HistoryRestoreService` / `BohriumSetupService` / `WorkspaceHandler`）

DESIGN.md §14 Phase 0 验收要点（"现有测试全部通过；不引入新测试"）全数满足。Task 10 的 `RedisReplyQueue` 抽出是 spec-additional，已在该 Task 顶部明确标注 rationale。

---

## 风险与回滚

| 风险 | 触发条件 | 回滚 |
|------|----------|------|
| 抽出后某测试 `mock.patch("matmaster.core.agent.AgentKernel._run_compaction_plan")` 因 wrapper 改动失效 | Task 2 Step 4 fail | 比对 `wrapper` body 是否完整 delegate；测试 mock 看 wrapper 还是 helper 都可——wrapper 必须保留实例方法签名（已设计） |
| Task 4 `_stream_llm_items` 移动后 `self._response_item` 替换漏一处 | Task 4 Step 4 fail with `AttributeError: 'NoneType' object has no attribute '_response_item'` 或类似 | grep 新文件确认 `_response_item` 出现 7 次（line 784/791/845/849/888/891/897 对应位置），且无 `self.` 前缀残留 |
| 抽出的 closure 引用了未传入的局部变量 | Task 6 / 7 Step 4 fail with `NameError` | 检查 helper 函数签名，把缺失变量加为参数 |
| 测试 import `_USER_INSTRUCTIONS_PATH` / `_build_workspace_upload_fn` 直接路径失效 | Task 5 / 7 Step 4 fail | re-export shim 已包含这些名字（Task 5 Step 3c、Task 7 Step 3e）；确认 `# noqa: F401` 没被 ruff strict 模式拒绝 |
| **mock patch path 仍指向旧 lookup 位置**（最高发风险） | Task 6 / 7 Step 4 fail，测试跑到真实 service 逻辑（如真连 Bohrium / DB） | Task 6 Step 3.5 / Task 7 Step 3.5 已显式列出 patch path 映射；如仍 fail，重新 grep `src.services.agent_run_service.<Name>` 找漏网 patch |
| 行数验证 fail（仍 > 800） | Task 4 / 8 / 10 Step 6 fail | 检查 import 清理是否充分；常数 / 注释行未被一并搬走；**不再增加新拆分** — plan 已穷尽 DESIGN scope，问题在执行精度 |
| `agent_run_service.py` 删除顶部 import 后某 line 还在引用（如 `WorkspaceArchivalConfig` 在别处 type hint） | Task 7 Step 5 ruff fail | 加回 import 行，再次 grep 找漏网引用 |
| 抽 LLM streaming 后 `_STOP_CHECK_EVERY_N_STREAM_CHUNKS` / `_STOP_RETRY_SLEEP_SLICE_SEC` 在 agent.py 还被某处引用 | Task 4 Step 4 fail with `NameError` | grep agent.py 这两个常量；如仍被引用，从新模块 re-import 回去（或两边都保留——它们值固定不会漂移） |

### 回滚

每个 helper 抽出是独立 commit（共 8 个 commit）。回滚策略按损坏范围由小到大：

1. **单个 task 失败**：保留前序 commit，回退本 task 改动。如尚未 commit，`git restore <files>` 还原；如已 commit，`git revert HEAD` 创建一个反向 commit（不重写历史）。
2. **多个连续 task 需回退**：逆序 `git revert <sha1> <sha2> ...`，git 会按顺序创建反向 commit。**不**使用 `git reset --hard` — 仓库协作规则禁止破坏性 git 命令，且本仓库 `.planning/REQUIREMENTS.md` 与 `DESIGN.md` 可能有用户未提交的草稿改动。
3. **全部回退**：在 Task 1 之前的 SHA 上 `git checkout -b backup/<date>` 备份当前分支末端，然后逆序 revert 所有 Phase 0 commit。完成后保留 backup 分支至少 1 周以备 forensic review。
4. **极端兜底**：如 revert 链遇到冲突无法干净反转，**停下来报告给用户**，由用户决定是 `git reset` 还是 `git stash` + 重新分支 — 不要擅自做破坏性操作。

---

## 与后续 Phase 的衔接

- **Phase 1**（DESIGN.md §14 / ROADMAP Phase 2）会扩展 `stream_sse_filter._should_emit_event_to_sse` 加 `user_turn_context` 到 hidden list（EVT-02）。本 Phase 0 已把这块独立出来，Phase 1 的改动只发生在新 helper 模块内，diff 干净。
- **Phase 1 HASH-03** 会删除 `_apply_user_instructions_to_initial_user_query`。本 Phase 0 的 re-export shim 让该删除一次性完成：从 `agent_run_instructions.py` 移除函数 + 从 `agent_run_service.py` 删 re-export 行，无须跨多文件清理。
- **Phase 2C** 会改写 `agent.py` kernel 入口（不再装配 turn_input）。本 Phase 0 抽出的 `agent_tool_dispatch.py` / `agent_compaction.py` 不受 kernel 入口签名变化影响，可平滑保留。

DESIGN.md 附录 B 「Phase 0 改动」清单与本 plan 全部对应。
