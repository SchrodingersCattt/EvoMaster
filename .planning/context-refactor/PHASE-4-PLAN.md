# Phase 4 Cleanup And v0 Compatibility Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 DESIGN.md v3.3 Phase 4 主线：删除 context refactor 过渡期 shim，完成 `AgentRuntimeSpec.context_builder` 到 `system_prompt_builder` 的一次性 rename，迁移并删除 legacy manifest / current input / context import 路径，退役 v0 checkpoint marker 与 legacy restore 分支。Phase 4 完成后，provider-facing context 的生产、压缩、恢复与测试入口只保留 `matmaster.context.*` 和 `src.services.model_history_restore_service.ModelHistoryRestoreService`。

**Architecture:** Phase 1-3 已把真实实现迁到 `matmaster.context`，但当前代码仍保留若干兼容门面：`matmaster/core/context_builder.py`、`matmaster/core/context_compactor.py`、`matmaster/manifests/*`、`matmaster/types/current_input.py`、`src/services/history_restore_service.py`，并且当前仓库仍把 `PlaygroundContext` / `WorkspaceArchivalConfig` 真实定义留在 `matmaster/types/context.py`。Phase 4 分两条线推进：先把所有 runtime / tests 调用点切到最终路径并删除 shim，再在产品 gate 满足后退役 v0 restore / v0 checkpoint marker。Task 4 同时承接 DESIGN.md §14 Phase 0.5 里尚未实际落地的 `PlaygroundContext` 归位工作；PR 描述必须显式声明该范围合并。`Oversized Input` 与 fallback 删除仍是独立设计，不混进本 cleanup PR。

**Tech Stack:** Python 3.11+ / uv / pytest / pytest-asyncio / Pydantic / dataclasses / `matmaster.context` / `ModelHistoryRestorer` / `TurnInput` / `SystemPromptBuilder`

**Spec 来源:** `.planning/context-refactor/DESIGN.md` §2 #4、§3.3、§4.1-4.2、§5.1-5.3、§7.3、§10.3、§11.1-11.5、§13、§14 Phase 4、§15、§16、§17、附录 B「Phase 4 改动」；`.planning/context-refactor/PHASE-3-PLAN.md` 的 Phase 4 边界；`.planning/context-refactor/FOLLOWUPS.md` 的 Phase 3 resolution。

---

## 全局约束

1. **Phase 4 主线只做 cleanup + v0 compatibility retirement。** 不实现 `user_turn_context.transform="oversized_summary"`，不设计 `InputSummaryConfig`，不删除 compaction fallback。Oversized Input 需要单独 spec；fallback 删除依赖 Phase 3 埋点数据。
2. **执行 v0 退役前必须确认产品 gate。** Task 8 开始前必须满足 DESIGN.md §14 Phase 4b 的条件之一：所有线上 session 的最新 `user_turn_context` / v1 checkpoint 已超过 30 天，或产品确认不再恢复 Phase 1 之前的 session。没有该确认时，只能完成 Task 1-7 和 Task 9 的非 v0 退役部分。
3. **删除 shim 前先迁移所有 import。** 每个删除任务都先加静态 guard，再更新生产与测试 import，最后删除文件。删除文件后 `rg` 不应出现旧 import 路径。
4. **`matmaster/context/system_prompt.py` 最终暴露 `SystemPromptBuilder`。** `ContextBuilder` 名字不再作为 public API 存在；`AgentRuntimeSpec` 字段同步改为 `system_prompt_builder`。
5. **`PlaygroundContext` / `WorkspaceArchivalConfig` 最终归属 `matmaster.core.playground`。** 当前代码还没有真正完成 Phase 0.5 归位；Phase 4 必须先把定义移动过去，再删除 `matmaster/types/context.py`。这是承接未完成的 Phase 0.5 mechanical move，不是新增 Phase 4 业务范围。
6. **`TurnInput` 是当前轮输入的唯一 typed carrier。** `CurrentInputContext` 类、`build_current_instruction_block` helper 与 `matmaster/types/current_input.py` 文件删除。为了 API / Worker 分离部署安全，Worker 解析 payload 时可以兼容老 key `current_input_context`，但类型与 import 必须使用 `TurnInput`。
7. **v1 restore 不再消费 raw `User/query` 作为 model-visible user message。** Phase 4 删除 hybrid 分支后，`User/query` 只服务 frontend display restore 与审计；backend model restore 只消费 `user_turn_context`、assistant / tool events 与 v1 checkpoint。
8. **`history_checkpoint_codec.py` 只接受 `<compacted_history>` marker。** `<previous_session_summary>` 和 `COMPAT:v0-checkpoint-marker` 注释一起删除。
9. **`src/services/history_restore_service.py` re-export shim 删除。** 所有调用者改为显式 import `ModelHistoryRestoreService`。
10. **测试目录迁移。** `tests/matmaster/manifests/` 删除；仍有价值的行为覆盖迁到 `tests/matmaster/context/` 或已有 source/session tests。
11. **保留 raw transcript 相关代码。** `src/services/chat_history.py::ChatHistoryConverter.events_to_dialog_messages` 仍服务前端 display / repair tests，不因 backend v0 restore 退役而删除。
12. **不把 service object 塞进 `run_meta`。** Phase 4 可以重命名 passive metadata key，但不得把 port、factory、assembler、sink、callback 放进 `run_meta`。
13. **所有 Python 命令使用 uv 环境。** 使用 `uv run python` / `uv run pytest`，不使用系统 `python` 或 `pip`。
14. **一个 Task 一个 commit。** Task 1 与 Task 9 是 read-only / verification，无 commit；Task 2-8 各一个 commit。
15. **当前工作树可能 dirty。** 开始每个 Task 前先检查将要编辑的文件 diff，不能恢复、格式化或改写用户已有改动。
16. **Task 里的 `git add` 清单是 seed list，不是完整真理。** 每个 commit 前必须先跑该 Task 的 `rg` guard 与 `git status --short`，把 repo-wide import 迁移实际触及的文件一起 stage；不得让验证依赖未 staged 的工作区改动。

---

## File Structure

新建文件：

- Create: `tests/matmaster/context/test_phase4_static_boundaries.py` — Phase 4 静态边界 guard，验证旧 shim 文件、旧 import、v0 marker 兼容分支已消失。
- Create or Move: `tests/matmaster/core/test_playground_context.py` — 从 `tests/matmaster/types/test_context.py` 迁移，覆盖 `PlaygroundContext` / `WorkspaceArchivalConfig` 的最终归属。

修改文件：

- Modify: `matmaster/context/system_prompt.py` — `ContextBuilder` rename 为 `SystemPromptBuilder`，删除 user request / compact bundle legacy helper。
- Modify: `matmaster/types/runtime.py` — `AgentRuntimeSpec.context_builder` rename 为 `system_prompt_builder`，validator 同步。
- Modify: `matmaster/core/exp.py` — 使用 `SystemPromptBuilder`，构造 / `model_copy` 字段同步 rename。
- Modify: `matmaster/core/__init__.py` — 删除 `ContextBuilder` export。
- Modify: `matmaster/core/playground.py` — 将 `PlaygroundContext` / `WorkspaceArchivalConfig` 定义归位到此文件。
- Modify: `matmaster/types/__init__.py` — 删除 `PlaygroundContext` 的 types 层 re-export 或改到最终路径；不得继续相对 import `.context`。
- Modify: `matmaster/context/sources/turn_input.py` — 增加 `TurnInput.from_values` / `from_payload` / `to_payload` / convenience properties，替代 `CurrentInputContext`。
- Modify: `matmaster/context/compaction.py` — `current_input_context` 参数 rename 为 `turn_input`，内部不再依赖 legacy carrier。
- Modify: `matmaster/core/agent.py` / `matmaster/core/agent_compaction.py` / `matmaster/core/exp.py` — runtime metadata 从 legacy current input carrier 切到 `TurnInput`。
- Modify: `src/services/stream_service.py` / `src/worker/agent_worker.py` / `src/services/agent_run_service.py` — API / Worker / service 链路使用 `TurnInput`，Worker 兼容读取老 payload key。
- Modify: `src/services/model_history_restore_service.py` — 删除 legacy restore fallback wiring，始终委托 v1 restorer。
- Modify: `matmaster/context/history_restore.py` — 删除 `has_user_turn_context` / `legacy_restore` / hybrid raw `User/query` 消费分支。
- Modify: `src/services/history_checkpoint_codec.py` — 只接受 v1 marker。
- Modify: `src/dao/chat_events_table.py` — 删除仅供 legacy restore 探测的 `has_user_turn_context` DAO 方法（若 `rg` 确认没有其它生产调用）。
- Modify: tests under `tests/matmaster/`, `tests/services/`, `tests/test_chat_stream_direct.py`, `tests/test_chat_events_history_checkpoint.py` — 跟随 import 与行为变化更新。

删除文件 / 目录：

- Delete: `matmaster/core/context_builder.py`
- Delete: `matmaster/core/context_compactor.py`
- Delete: `matmaster/manifests/`
- Delete: `matmaster/types/context.py`
- Delete: `matmaster/types/current_input.py`
- Delete: `src/services/history_restore_service.py`
- Delete: `tests/matmaster/manifests/`
- Delete or Move: `tests/matmaster/types/test_context.py`
- Delete or Move: `tests/matmaster/types/test_current_input.py`
- Delete: shim-only tests such as `tests/matmaster/context/test_system_prompt.py::test_core_context_builder_shim_reexports_context_implementation` and `tests/matmaster/core/test_context_compactor.py::test_core_context_compactor_shim_reexports_new_implementation`

不变文件：

- `matmaster/context/sections.py`
- `matmaster/context/rendering.py`
- `matmaster/context/turn_context.py`
- `matmaster/context/compositions.py`
- `matmaster/context/assembly.py`
- `matmaster/context/session.py`
- `src/services/chat_history.py`

---

## Task 1: Baseline And Retirement Gate Inventory

**Files:** read-only

**Spec 依据:** DESIGN.md §14 Phase 4、§17「v0 兼容性退役」；PHASE-3-PLAN.md「全局约束」。

- [ ] **Step 1: Confirm uv environment and clean/known dirty worktree**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -V && git status --short
```

Expected:

```text
Python 3.11+ or Python 3.13.x
git status --short prints no output or only known user changes
```

If any Phase 4 target file is dirty, inspect it before editing:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git diff -- \
  matmaster/context/system_prompt.py \
  matmaster/types/runtime.py \
  matmaster/core/exp.py \
  matmaster/core/playground.py \
  matmaster/context/sources/turn_input.py \
  matmaster/context/compaction.py \
  matmaster/core/agent.py \
  matmaster/core/agent_compaction.py \
  src/services/stream_service.py \
  src/worker/agent_worker.py \
  src/services/agent_run_service.py \
  src/services/model_history_restore_service.py \
  matmaster/context/history_restore.py \
  src/services/history_checkpoint_codec.py
```

Expected: either empty output or user changes that can be preserved by applying Phase 4 edits around them. Do not revert unrelated changes.

- [ ] **Step 2: Confirm Phase 3 artifacts exist**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && \
  test -f matmaster/context/compaction.py && \
  test -f matmaster/context/system_prompt.py && \
  test -f matmaster/core/context_compactor.py && \
  test -f matmaster/core/context_builder.py && \
  test -f .planning/context-refactor/PHASE-3-PROMPT-AB.md && \
  rg -n "schema_version.*history_checkpoint\\.v1|<compacted_history>" \
    matmaster src tests
```

Expected: command exits `0`; `matmaster/context/compaction.py` is the real implementation; the two `matmaster/core/context_*` files are shims that Phase 4 will remove.

- [ ] **Step 3: Inventory legacy boundaries before editing**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.manifests|matmaster\\.core\\.context_builder|matmaster\\.core\\.context_compactor|matmaster\\.types\\.context|matmaster\\.types\\.current_input|src\\.services\\.history_restore_service|COMPAT:v0|MARKERS_V0|previous_session_summary|legacy_restore|has_user_turn_context|context_builder" \
  matmaster src tests
```

Expected before Phase 4:

```text
legacy imports and shim files are present
history_checkpoint_codec.py accepts MARKERS_V0
model_history_restore_service.py wires legacy_restore and has_user_turn_context
AgentRuntimeSpec still uses context_builder
tests/matmaster/manifests exists
```

Expected after Phase 4 is described in Task 9.

- [ ] **Step 4: Run focused baseline tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/core/test_context_builder.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/types/test_runtime.py \
  tests/matmaster/types/test_context.py \
  tests/matmaster/types/test_current_input.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/manifests \
  -q
```

Expected: all pass. If baseline fails, fix the baseline before starting Phase 4 edits.

- [ ] **Step 5: Record v0 retirement gate**

Before Task 8, get one concrete decision and paste it into the PR description:

```text
v0 restore/checkpoint compatibility retirement gate:
- decision source: product confirmation or ops query
- decision date: YYYY-MM-DD
- decision: old raw-only sessions no longer need backend model restore

scope note:
- this PR also completes the previously-unfinished Phase 0.5 move of PlaygroundContext / WorkspaceArchivalConfig into matmaster.core.playground before deleting the types.context shim
- HistoryRestoreService re-export shim removal is independent of the v0 retirement gate and may land before Task 8
```

Expected: Task 8 does not start without this gate. Task 1 has no commit.

---

## Task 2: Rename ContextBuilder To SystemPromptBuilder And Rename Runtime Spec Field

**Files:**
- Modify: `matmaster/context/system_prompt.py`
- Modify: `matmaster/types/runtime.py`
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/core/__init__.py`
- Modify Test: `tests/matmaster/context/test_system_prompt.py`
- Move/Modify Test: `tests/matmaster/core/test_context_builder.py` → `tests/matmaster/context/test_system_prompt_builder.py`
- Modify Test: `tests/matmaster/types/test_runtime.py`
- Modify Test: `tests/matmaster/test_runtime_spec.py`
- Modify Test: `tests/matmaster/services/test_history_checkpoint_service.py`
- Modify Test: `tests/matmaster/core/agent_kernel_test_helpers.py`
- Modify Test: tests that instantiate `AgentRuntimeSpec(context_builder=...)`
- Delete: `matmaster/core/context_builder.py`

**Spec 依据:** DESIGN.md §13「命名清理表」、§14 Phase 4a、§15。

- [ ] **Step 1: Add failing tests for final runtime field**

Create or update `tests/matmaster/context/test_system_prompt.py` so it imports the final class directly:

```python
from matmaster.context.system_prompt import SystemPromptBuilder


def test_system_prompt_builder_builds_base_prompt(ctx):
    builder = SystemPromptBuilder()

    result = builder.build_system_prompt(
        ctx,
        system_prompt="Base persona.",
        identity="Identity text.",
    )

    assert "Base persona." in result
    assert "Identity text." in result
```

Update `tests/matmaster/types/test_runtime.py` with a final-field smoke test:

```python
from matmaster.context.system_prompt import SystemPromptBuilder
from matmaster.types.runtime import AgentRuntimeSpec


def test_agent_runtime_spec_requires_system_prompt_builder() -> None:
    spec = AgentRuntimeSpec(system_prompt_builder=SystemPromptBuilder())

    assert isinstance(spec.system_prompt_builder, SystemPromptBuilder)


def test_agent_runtime_spec_rejects_context_builder_keyword() -> None:
    with pytest.raises(ValueError):
        AgentRuntimeSpec(context_builder=SystemPromptBuilder())
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_system_prompt.py \
  tests/matmaster/types/test_runtime.py::test_agent_runtime_spec_requires_system_prompt_builder \
  tests/matmaster/types/test_runtime.py::test_agent_runtime_spec_rejects_context_builder_keyword \
  -q
```

Expected: fail because `SystemPromptBuilder` and `system_prompt_builder` are not implemented yet.

- [ ] **Step 2: Rename builder class and remove compact-bundle helpers**

In `matmaster/context/system_prompt.py`, rename the class and keep only system prompt assembly responsibility:

```python
class SystemPromptBuilder:
    """Sectioned system prompt assembler."""

    SEPARATOR = "\n\n---\n\n"
    SYSTEM_SECTION_ORDER = (
        "system_prompt",
        "identity",
        "skills",
        "tools",
        "memory",
        "task",
    )

    def build_system_prompt(
        self,
        ctx: PlaygroundContext,
        tool_registry: Any = None,
        *,
        system_prompt: str = "",
        identity: str = "",
        skill_registry: Any = None,
        memory_context: str | None = None,
        task_context: str | None = None,
        disabled_sections: set[str] | None = None,
    ) -> str:
        ...
```

Remove these members from `system_prompt.py`:

```text
ContextBuilder
BUNDLE_SECTION_ORDER
DEFAULT_COMPACT_HEADER
DEFAULT_CONTINUATION_INSTRUCTION
build_user_request
build_compact_bundle
_tag if it is only used by build_compact_bundle
```

Keep private helpers that are used by `build_system_prompt`, such as `_build_section`, `_build_system_prompt`, `_build_identity`, `_build_skills`, `_build_tools`, `_build_memory`, and `_build_task`.

Also update the module docstring and comments from the old mixed-responsibility name:

```text
ContextBuilder -- sectioned system prompt assembler
→ SystemPromptBuilder -- sectioned system prompt assembler
```

After this step, `matmaster/context/system_prompt.py` docstrings and comments should not mention `ContextBuilder`, `context_builder`, `build_user_request`, `build_compact_bundle`, or `<previous_session_summary>`. Other v0 marker references are retired later in Task 8.

- [ ] **Step 3: Rename AgentRuntimeSpec field**

In `matmaster/types/runtime.py`, use:

```python
from matmaster.context.system_prompt import SystemPromptBuilder

...

class AgentRuntimeSpec(BaseModel):
    ...
    system_prompt_builder: SystemPromptBuilder
    ...

    @model_validator(mode="after")
    def _check_v2_field_types(self) -> AgentRuntimeSpec:
        ...
        if not isinstance(self.system_prompt_builder, SystemPromptBuilder):
            raise ValueError(
                "system_prompt_builder must be SystemPromptBuilder, "
                f"got {type(self.system_prompt_builder).__name__}"
            )
```

Do not keep a `context_builder` alias field. This is the Phase 4 break point.

- [ ] **Step 4: Update Exp runtime construction**

In `matmaster/core/exp.py`, replace the import and field usage:

```python
from matmaster.context.system_prompt import SystemPromptBuilder
```

Use `system_prompt_builder` in assemble:

```python
return AgentRuntimeSpec(
    system_prompt_builder=SystemPromptBuilder(),
    llm_provider=ctx.llm_provider,
    max_turns=self._config.max_turns,
    compaction=self._config.compaction,
    meta={},
)
```

Use the renamed local in `build_runtime`:

```python
system_prompt_builder = SystemPromptBuilder()
system_prompt = system_prompt_builder.build_system_prompt(
    ctx,
    registry,
    system_prompt=self._config.system_prompt,
    identity=self._config.developer_instructions,
    skill_registry=self._skill_registry,
)
```

Update `model_copy`:

```python
spec = spec.model_copy(
    update={
        ...
        "system_prompt_builder": system_prompt_builder,
        ...
    }
)
```

- [ ] **Step 5: Update tests and helper factories**

Apply this mechanical replacement in tests:

```text
from matmaster.core.context_builder import ContextBuilder
→ from matmaster.context.system_prompt import SystemPromptBuilder

ContextBuilder()
→ SystemPromptBuilder()

context_builder=
→ system_prompt_builder=

spec.context_builder
→ spec.system_prompt_builder
```

Move `tests/matmaster/core/test_context_builder.py` to `tests/matmaster/context/test_system_prompt_builder.py` and remove tests for `build_user_request` / `build_compact_bundle`, because those methods belonged to the old mixed-responsibility builder and are not part of Phase 4 final API.

Update `tests/matmaster/services/test_history_checkpoint_service.py` so checkpoint helper tests no longer depend on the removed compact-bundle method. Replace:

```python
from matmaster.core.context_builder import ContextBuilder

...

[UserMessage(content=ContextBuilder().build_compact_bundle(summary=summary))]
```

with:

```python
def _compact_user_message(summary: str) -> UserMessage:
    return UserMessage(
        content=f"<compacted_history>\n{summary}\n</compacted_history>"
    )

...

[_compact_user_message(summary)]
```

- [ ] **Step 6: Delete legacy core shim and export**

Delete:

```text
matmaster/core/context_builder.py
```

In `matmaster/core/__init__.py`, remove:

```python
from .context_builder import ContextBuilder
```

and remove `"ContextBuilder"` from `__all__`.

- [ ] **Step 7: Verify no legacy builder path remains**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.core\\.context_builder|\\bContextBuilder\\b|\\bcontext_builder\\b|build_user_request|build_compact_bundle" \
  matmaster src tests
```

Expected: no legacy builder matches. `SessionContextBuilder` is allowed and must not be removed. If a docstring/comment match remains for `ContextBuilder` or `context_builder`, update that text too. v0 marker text such as `previous_session_summary` is not part of this Task 2 scan; it is retired in Task 8 and enforced by Task 9.

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_system_prompt.py \
  tests/matmaster/context/test_system_prompt_builder.py \
  tests/matmaster/types/test_runtime.py \
  tests/matmaster/test_runtime_spec.py \
  tests/matmaster/core/test_exp.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/core/test_agent_kernel_stream.py \
  -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/system_prompt.py \
  matmaster/types/runtime.py \
  matmaster/core/exp.py \
  matmaster/core/__init__.py \
  tests/matmaster/context/test_system_prompt.py \
  tests/matmaster/context/test_system_prompt_builder.py \
  tests/matmaster/types/test_runtime.py \
  tests/matmaster/test_runtime_spec.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/core/agent_kernel_test_helpers.py \
  tests/matmaster/core/test_exp.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/core/test_agent_kernel_stream.py && \
  git add -u matmaster/core/context_builder.py tests/matmaster/core/test_context_builder.py && \
  git commit -m "refactor: rename system prompt builder"
```

Expected: one commit with no trailer lines.

---

## Task 3: Remove core context_compactor Shim

**Files:**
- Modify Test: `tests/matmaster/core/test_context_compactor.py`
- Modify Test: `tests/matmaster/core/test_agent_kernel_compaction.py`
- Modify Test: `tests/matmaster/core/test_hook_wiring.py`
- Modify Test: any test importing `matmaster.core.context_compactor`
- Delete: `matmaster/core/context_compactor.py`

**Spec 依据:** DESIGN.md §5.3、§9.3、§14 Phase 4a、附录 B「Phase 4 改动」。

- [ ] **Step 1: Add static guard for deleted compactor shim**

Create `tests/matmaster/context/test_phase4_static_boundaries.py` with:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_core_context_compactor_shim_is_removed() -> None:
    assert not (ROOT / "matmaster/core/context_compactor.py").exists()
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py::test_core_context_compactor_shim_is_removed \
  -q
```

Expected: fail because the shim still exists.

- [ ] **Step 2: Update compactor imports to final path**

Replace test imports:

```text
from matmaster.core.context_compactor import CompactionPlan
from matmaster.core.context_compactor import CompactionResult
from matmaster.core.context_compactor import ContextCompactor

→

from matmaster.context.compaction import CompactionPlan
from matmaster.context.compaction import CompactionResult
from matmaster.context.compaction import ContextCompactor
```

Remove the shim identity test from `tests/matmaster/core/test_context_compactor.py`:

```python
def test_core_context_compactor_shim_reexports_new_implementation() -> None:
    ...
```

- [ ] **Step 3: Delete shim file**

Delete:

```text
matmaster/core/context_compactor.py
```

- [ ] **Step 4: Verify no shim import remains**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.core\\.context_compactor|core/context_compactor\\.py|context_compactor" \
  matmaster src tests
```

Expected: no production import of `matmaster.core.context_compactor`. Matches such as event source value `"context_compactor"` are allowed because they are persisted event source labels, not import paths.

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_hook_wiring.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_hook_wiring.py && \
  git add -u matmaster/core/context_compactor.py && \
  git commit -m "refactor: remove legacy context compactor shim"
```

Expected: one commit with no trailer lines.

---

## Task 4: Move PlaygroundContext To core.playground And Delete types.context

**Files:**
- Modify: `matmaster/core/playground.py`
- Modify: `matmaster/devshell/runner.py`
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/core/runtime_context_assembly.py`
- Modify: `matmaster/core/path_access.py`
- Modify: `matmaster/context/system_prompt.py`
- Modify: `matmaster/types/__init__.py`
- Modify: `src/services/agent_run_bohrium_stage.py`
- Modify Test: tests importing `matmaster.types.context`
- Move Test: `tests/matmaster/types/test_context.py` → `tests/matmaster/core/test_playground_context.py`
- Delete: `matmaster/types/context.py`

**Spec 依据:** DESIGN.md §5.2、§5.3、§14 Phase 0.5、§14 Phase 4a。

- [ ] **Step 1: Add failing static guard**

Append to `tests/matmaster/context/test_phase4_static_boundaries.py`:

```python
def test_types_context_shim_is_removed() -> None:
    assert not (ROOT / "matmaster/types/context.py").exists()
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py::test_types_context_shim_is_removed \
  -q
```

Expected: fail because `matmaster/types/context.py` still exists.

- [ ] **Step 2: Move context models into core.playground**

Move the exact definitions of `WorkspaceArchivalConfig` and `PlaygroundContext` from `matmaster/types/context.py` into `matmaster/core/playground.py`, above class `Playground`.

The top of `matmaster/core/playground.py` should include these imports:

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator

from matmaster.types.runtime_ports import PlaygroundRuntimePorts
from matmaster.types.session import Session, SSHSessionConfig
```

Remove this import from `matmaster/core/playground.py`:

```python
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig
```

Keep the class bodies behavior-equivalent to the current `matmaster/types/context.py` definitions, including `with_execution`, `with_bohrium`, `with_runtime_ports`, and `with_run_meta`.

- [ ] **Step 3: Update all imports**

Apply this as a repo-wide migration, not just a fixed file list. Use `rg -l 'matmaster\.types\.context' matmaster src tests` to find every direct import and inspect each match manually, including function-local lazy imports inside tests and helper functions. Also update relative imports from `matmaster/types/__init__.py`.

Replace:

```text
from matmaster.types.context import PlaygroundContext
from matmaster.types.context import WorkspaceArchivalConfig
from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig
```

with:

```text
from matmaster.core.playground import PlaygroundContext
from matmaster.core.playground import WorkspaceArchivalConfig
from matmaster.core.playground import PlaygroundContext, WorkspaceArchivalConfig
```

Apply this to production and tests. The current known production files include:

```text
matmaster/devshell/runner.py
matmaster/core/exp.py
matmaster/core/runtime_context_assembly.py
matmaster/core/path_access.py
matmaster/context/system_prompt.py
matmaster/types/__init__.py
src/services/agent_run_bohrium_stage.py
```

In `matmaster/types/__init__.py`, remove the types-layer context export entirely unless a real production importer still requires `from matmaster.types import PlaygroundContext`. If such an importer exists, update that importer to `matmaster.core.playground` instead; do not keep `from .context import PlaygroundContext` after `matmaster/types/context.py` is deleted.

- [ ] **Step 4: Move tests**

Move:

```text
tests/matmaster/types/test_context.py
→ tests/matmaster/core/test_playground_context.py
```

Update imports in the moved file to use `matmaster.core.playground`.

- [ ] **Step 5: Delete old module**

Delete:

```text
matmaster/types/context.py
```

- [ ] **Step 6: Verify import direction and cycles**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.types\\.context|types/context\\.py|from \\.context import (PlaygroundContext|WorkspaceArchivalConfig)" \
  matmaster src tests
```

Expected: no matches.

Run import smoke tests:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python - <<'PY'
from matmaster.core.playground import Playground, PlaygroundContext, WorkspaceArchivalConfig
from matmaster.core.exp import Exp
from matmaster.context.system_prompt import SystemPromptBuilder

print(Playground.__name__, PlaygroundContext.__name__, WorkspaceArchivalConfig.__name__)
print(Exp.__name__, SystemPromptBuilder.__name__)
PY
```

Expected:

```text
Playground PlaygroundContext WorkspaceArchivalConfig
Exp SystemPromptBuilder
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/core/test_playground_context.py \
  tests/matmaster/core/test_playground.py \
  tests/matmaster/core/test_playground_manager.py \
  tests/matmaster/core/test_exp.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/context/test_system_prompt.py \
  tests/matmaster/integration/test_e2e_minimal.py \
  -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/core/playground.py \
  matmaster/devshell/runner.py \
  matmaster/core/exp.py \
  matmaster/core/runtime_context_assembly.py \
  matmaster/core/path_access.py \
  matmaster/context/system_prompt.py \
  matmaster/types/__init__.py \
  src/services/agent_run_bohrium_stage.py \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/core/test_playground_context.py \
  tests/matmaster/core/test_playground.py \
  tests/matmaster/core/test_playground_manager.py \
  tests/matmaster/core/test_exp.py \
  tests/matmaster/core/test_exp_runtime_v2.py && \
  git add -u matmaster/types/context.py tests/matmaster/types/test_context.py && \
  git commit -m "refactor: move playground context into core"
```

Expected: one commit with no trailer lines.

---

## Task 5: Replace CurrentInputContext With TurnInput And Delete types.current_input

**Files:**
- Modify: `matmaster/context/sources/turn_input.py`
- Modify: `matmaster/context/compaction.py`
- Modify: `matmaster/core/agent.py`
- Modify: `matmaster/core/agent_compaction.py`
- Modify: `matmaster/core/exp.py`
- Modify: `src/services/stream_service.py`
- Modify: `src/worker/agent_worker.py`
- Modify: `src/services/agent_run_service.py`
- Modify Test: tests importing `matmaster.types.current_input`
- Move/Delete Test: `tests/matmaster/types/test_current_input.py`
- Delete: `matmaster/types/current_input.py`

**Spec 依据:** DESIGN.md §3.3、§5.3、§7.3、§10.3、§13、§14 Phase 4a；AGENTS.md API / Worker 分离约束。

- [ ] **Step 1: Add failing static guard**

Append to `tests/matmaster/context/test_phase4_static_boundaries.py`:

```python
def test_types_current_input_shim_is_removed() -> None:
    assert not (ROOT / "matmaster/types/current_input.py").exists()
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py::test_types_current_input_shim_is_removed \
  -q
```

Expected: fail because the shim still exists.

- [ ] **Step 2: Extend TurnInput with serialization helpers**

Add these helpers to `matmaster/context/sources/turn_input.py`:

```python
from typing import Any


def _clean_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        text for value in values if isinstance(value, str) and (text := value.strip())
    )


@dataclass(frozen=True)
class TurnInput:
    instruction: TurnInstructionSource = field(default_factory=TurnInstructionSource)
    attachments: TurnAttachmentsSource = field(default_factory=TurnAttachmentsSource)
    pre_turn_history_event_id: int = 0

    @classmethod
    def from_values(
        cls,
        *,
        user_text: str | None = None,
        files: Any = None,
        images: Any = None,
        workspace_paths: Any = None,
        pre_turn_history_event_id: int | None = 0,
    ) -> "TurnInput":
        return cls(
            instruction=TurnInstructionSource(user_text=(user_text or "").strip()),
            attachments=TurnAttachmentsSource(
                files=_clean_tuple(files),
                images=_clean_tuple(images),
                workspace_paths=_clean_tuple(workspace_paths),
            ),
            pre_turn_history_event_id=int(pre_turn_history_event_id or 0),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> "TurnInput | None":
        if not isinstance(payload, dict):
            return None
        raw_boundary = payload.get(
            "pre_turn_history_event_id",
            payload.get("pre_query_scope_event_id", 0),
        )
        try:
            boundary = int(raw_boundary or 0)
        except (TypeError, ValueError):
            boundary = 0
        return cls.from_values(
            user_text=payload.get("user_text"),
            files=payload.get("files"),
            images=payload.get("images"),
            workspace_paths=payload.get("workspace_paths"),
            pre_turn_history_event_id=boundary,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "files": list(self.files),
            "images": list(self.images),
            "workspace_paths": list(self.workspace_paths),
            "pre_turn_history_event_id": self.pre_turn_history_event_id,
        }

    @property
    def user_text(self) -> str:
        return self.instruction.user_text

    @property
    def files(self) -> tuple[str, ...]:
        return self.attachments.files

    @property
    def images(self) -> tuple[str, ...]:
        return self.attachments.images

    @property
    def workspace_paths(self) -> tuple[str, ...]:
        return self.attachments.workspace_paths
```

Update existing `__post_init__`, `to_sections`, `has_effective_input`, and `_merged_current_instruction_text` methods to remain inside the same `TurnInput` class. There must be only one `TurnInput` class definition after the edit.

- [ ] **Step 3: Move current-input tests into turn_input tests**

Move assertions from `tests/matmaster/types/test_current_input.py` into `tests/matmaster/context/sources/test_turn_input.py` and rewrite them for `TurnInput`:

```python
def test_turn_input_round_trips_payload() -> None:
    turn_input = TurnInput.from_values(
        user_text="analyze current",
        files=["https://oss.example.com/new.cif"],
        images=["https://oss.example.com/image.png"],
        workspace_paths=["/share/current/POSCAR"],
        pre_turn_history_event_id=42,
    )

    assert TurnInput.from_payload(turn_input.to_payload()) == turn_input


def test_turn_input_reads_legacy_payload_boundary_name() -> None:
    turn_input = TurnInput.from_payload(
        {
            "user_text": "legacy",
            "files": ["a.cif"],
            "pre_query_scope_event_id": 7,
        }
    )

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 7
    assert turn_input.files == ("a.cif",)
```

Add explicit coverage for the Phase 4 boundary semantic change from nullable legacy boundary to integer boundary:

```python
def test_turn_input_missing_boundary_defaults_to_zero() -> None:
    turn_input = TurnInput.from_payload({"user_text": "hi"})

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 0


def test_turn_input_invalid_boundary_defaults_to_zero() -> None:
    turn_input = TurnInput.from_payload(
        {"user_text": "hi", "pre_turn_history_event_id": "not-an-int"}
    )

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 0
```

In `tests/matmaster/context/test_compaction.py`, add or update a preflight case showing boundary `0` is a valid known boundary, not a missing-boundary sentinel:

```python
async def test_preflight_compaction_with_boundary_zero_uses_turn_input() -> None:
    turn_input = TurnInput.from_payload({"user_text": "first turn"})

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 0
    # Execute the existing preflight compaction helper / compactor fixture and
    # assert it reaches apply_compaction_plan instead of skipping due to a
    # missing boundary.
```

This is an intentional semantic tightening from legacy `CurrentInputContext.pre_query_scope_event_id: int | None` to `TurnInput.pre_turn_history_event_id: int`. Missing, null, or invalid boundary payloads now fold to `0`, where `0` means session start.

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/sources/test_turn_input.py \
  -q
```

Expected: pass after Step 2.

- [ ] **Step 4: Update API and Worker queue payload handling**

In `src/services/stream_service.py`, replace import:

```python
from matmaster.context.sources.turn_input import TurnInput
```

Update `SendStreamContext`:

```python
turn_input: TurnInput | None = None
```

When preparing send message, build `TurnInput`:

```python
pre_turn_history_event_id = self._get_pre_query_scope_event_id(sid) or 0
turn_input = TurnInput.from_values(
    user_text=req.content,
    files=req.files,
    images=req.images,
    workspace_paths=req.workspace_paths,
    pre_turn_history_event_id=pre_turn_history_event_id,
)
```

When enqueueing the Worker job, write the new key and a legacy key for rolling API/Worker deployments. The legacy key must include `pre_query_scope_event_id` because old Workers parse only that boundary name:

```python
turn_input_payload = ctx.turn_input.to_payload() if ctx.turn_input is not None else None
legacy_current_input_payload = None
if ctx.turn_input is not None and turn_input_payload is not None:
    legacy_current_input_payload = {
        **turn_input_payload,
        "pre_query_scope_event_id": ctx.turn_input.pre_turn_history_event_id,
    }

...

"turn_input": turn_input_payload,
"current_input_context": legacy_current_input_payload,
```

The legacy key is a queue compatibility alias, not a Python shim. It can be removed in a later deployment cleanup after all workers run Phase 4+ code.

Add a rolling-deploy compatibility test that builds the new API payload and verifies the legacy payload still contains `pre_query_scope_event_id` with the same numeric value. If the test still imports `CurrentInputContext` before Step 6 deletes the module, keep that import local to the test and remove or rewrite it before the final Step 7 grep:

```python
def test_worker_payload_legacy_current_input_keeps_boundary_name() -> None:
    turn_input = TurnInput.from_values(
        user_text="hello",
        pre_turn_history_event_id=42,
    )
    legacy_payload = {
        **turn_input.to_payload(),
        "pre_query_scope_event_id": turn_input.pre_turn_history_event_id,
    }

    assert legacy_payload["pre_query_scope_event_id"] == 42
```

In `src/worker/agent_worker.py`, parse new first, old second:

```python
from matmaster.context.sources.turn_input import TurnInput

...

turn_input = TurnInput.from_payload(
    payload.get("turn_input") or payload.get("current_input_context")
)
```

Pass `turn_input=turn_input` to `AgentRunService.run_agent(...)`.

Apply this import migration repo-wide, including function-local lazy imports in tests and helper factories; use `rg -l 'matmaster\.types\.current_input|CurrentInputContext' matmaster src tests` before and after the edit.

- [ ] **Step 5: Update service and kernel signatures**

In `src/services/agent_run_service.py`, replace `current_input_context` parameter with:

```python
turn_input: TurnInput | None = None
```

Where Stage 5b currently derives `pre_turn_history_event_id` from `current_input_context`, use:

```python
pre_turn_history_event_id = (
    turn_input.pre_turn_history_event_id if turn_input is not None else 0
)
turn_input = turn_input or TurnInput.from_values(
    user_text=content,
    files=files,
    images=current_user_images_payload,
    workspace_paths=workspace_paths,
    pre_turn_history_event_id=pre_turn_history_event_id,
)
```

In `matmaster/core/exp.py`, change kernel meta key:

```python
turn_input = run_meta.get("turn_input")
if turn_input is not None:
    meta["turn_input"] = turn_input
```

In `matmaster/core/agent.py`, read:

```python
raw_turn_input = spec.meta.get("turn_input")
turn_input = (
    raw_turn_input
    if isinstance(raw_turn_input, TurnInput)
    else TurnInput.from_payload(raw_turn_input)
)
```

In `matmaster/core/agent_compaction.py`, rename parameters from `current_input_context` to `turn_input` and pass that to compactor.

In `matmaster/context/compaction.py`, replace `_turn_input_from_current_context` with direct use of the provided `TurnInput`. The preflight missing-boundary check becomes unnecessary because `TurnInput.pre_turn_history_event_id` is always an int; `0` represents session start.

This is intentional: Phase 4 retires the old `pre_query_scope_event_id is None` sentinel. If the queue payload omits the boundary, `TurnInput.from_payload(...)` converts it to `0`, and preflight code treats that as a known session-start boundary rather than skipping compaction.

After renaming the parameter and removing `_turn_input_from_current_context`, also remove the module-level import:

```python
from matmaster.types.current_input import CurrentInputContext
```

from every production file that currently imports it:

```text
matmaster/context/compaction.py
matmaster/core/agent.py
matmaster/core/agent_compaction.py
src/services/agent_run_service.py
src/services/stream_service.py
src/worker/agent_worker.py
```

- [ ] **Step 6: Delete legacy current input module**

Delete:

```text
matmaster/types/current_input.py
tests/matmaster/types/test_current_input.py
```

- [ ] **Step 7: Verify no legacy import remains**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.types\\.current_input|CurrentInputContext|build_current_instruction_block|pre_query_scope_event_id" \
  matmaster src tests
```

Expected: no `matmaster.types.current_input`, no `CurrentInputContext`, no `build_current_instruction_block`. The string `pre_query_scope_event_id` may appear only inside `TurnInput.from_payload(...)` legacy queue payload parsing test and implementation.

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_reply_events.py \
  tests/matmaster/context/sources/test_turn_input.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/sources/turn_input.py \
  matmaster/context/compaction.py \
  matmaster/core/agent.py \
  matmaster/core/agent_compaction.py \
  matmaster/core/exp.py \
  src/services/stream_service.py \
  src/worker/agent_worker.py \
  src/services/agent_run_service.py \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/context/sources/test_turn_input.py \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_reply_events.py \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py && \
  git add -u matmaster/types/current_input.py tests/matmaster/types/test_current_input.py && \
  git commit -m "refactor: replace current input shim with turn input"
```

Expected: one commit with no trailer lines.

---

## Task 6: Delete matmaster.manifests Package And Move Remaining Tests To context

**Files:**
- Modify: tests importing `matmaster.manifests.*`
- Modify: `tests/services/test_attachment_manifest_service.py`
- Modify: `tests/matmaster/services/test_active_mcp_replay.py`
- Modify: `tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py`
- Delete: `matmaster/manifests/`
- Delete: `tests/matmaster/manifests/`
- Delete or Rewrite: `tests/matmaster/context/test_manifests_equivalence.py`

**Spec 依据:** DESIGN.md §5.1、§5.3、§13、§14 Phase 4a、§16。

- [ ] **Step 1: Add failing static guard**

Append to `tests/matmaster/context/test_phase4_static_boundaries.py`:

```python
def test_manifests_package_is_removed() -> None:
    assert not (ROOT / "matmaster/manifests").exists()
    assert not (ROOT / "tests/matmaster/manifests").exists()
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py::test_manifests_package_is_removed \
  -q
```

Expected: fail because both directories still exist.

- [ ] **Step 2: Replace attachment manifest tests with source tests**

In `tests/services/test_attachment_manifest_service.py`, replace:

```python
from matmaster.manifests.attachment import (
    build_available_attachments,
    filter_entries_after_event_id,
    filter_entries_in_event_range,
    format_available_attachments,
)
```

with:

```python
from matmaster.context.sources.attachments import (
    filter_entries_after_event_id,
    filter_entries_in_event_range,
    format_entries_text,
    scan_legacy_attachment_entries,
)
```

Replace helper calls:

```text
build_available_attachments(events)
→ list(scan_legacy_attachment_entries(events))

format_available_attachments(entries)
→ format_entries_text(entries)
```

If the file name no longer matches its content, move it:

```text
tests/services/test_attachment_manifest_service.py
→ tests/matmaster/context/sources/test_attachment_source_legacy_scan.py
```

- [ ] **Step 3: Replace mcp / skill manifest imports in tests**

Replace:

```python
from matmaster.manifests.mcp import resolve_runnable_servers
from matmaster.manifests.skill import resolve_active_skills
```

with:

```python
from matmaster.context.sources.tools import resolve_runnable_servers
from matmaster.context.sources.skills import resolve_active_skills
```

Replace scanner imports:

```python
from matmaster.manifests.scanner import SkillHitRecord, scan_skill_hits
```

with:

```python
from matmaster.context.scanner import SkillHitRecord, scan_skill_hits
```

The known tests to update are:

```text
tests/matmaster/services/test_active_mcp_replay.py
tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py
tests/matmaster/manifests/test_mcp.py
tests/matmaster/manifests/test_skill.py
tests/matmaster/manifests/test_scanner.py
```

Move valuable tests from `tests/matmaster/manifests/` into existing context files:

```text
tests/matmaster/manifests/test_mcp.py       → tests/matmaster/context/sources/test_tools.py
tests/matmaster/manifests/test_skill.py     → tests/matmaster/context/sources/test_skills.py
tests/matmaster/manifests/test_scanner.py   → tests/matmaster/context/test_scanner.py
tests/matmaster/manifests/test_rehydrator.py → delete after confirming tests/matmaster/context/test_session.py and test_compaction.py cover the behavior
```

- [ ] **Step 4: Delete manifest equivalence test**

Delete:

```text
tests/matmaster/context/test_manifests_equivalence.py
```

Reason: Phase 2B needed legacy-vs-new equivalence while shims existed. After Phase 4 removes the legacy package, equivalence against deleted code is no longer a valid test.

More specifically, this harness imports both `matmaster.manifests.*` and `matmaster.context.sources.*`. After Step 5 deletes `matmaster.manifests`, the legacy side is gone and the test would fail at import time, not at assertion time. There is no useful "new vs new" version of this coverage. The Phase 2B equivalence guarantee is preserved by git history and by the context/source fixture tests that remain green; Phase 4 explicitly retires the live legacy comparison.

- [ ] **Step 5: Delete manifests package**

Delete:

```text
matmaster/manifests/
tests/matmaster/manifests/
```

- [ ] **Step 6: Verify final imports**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.manifests|tests/matmaster/manifests|manifests/" \
  matmaster src tests
```

Expected: no matches.

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/context/test_scanner.py \
  tests/matmaster/context/test_session.py \
  tests/matmaster/context/sources/test_attachments.py \
  tests/matmaster/context/sources/test_skills.py \
  tests/matmaster/context/sources/test_tools.py \
  tests/matmaster/services/test_active_mcp_replay.py \
  tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py \
  -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/context/test_scanner.py \
  tests/matmaster/context/test_session.py \
  tests/matmaster/context/sources/test_attachments.py \
  tests/matmaster/context/sources/test_skills.py \
  tests/matmaster/context/sources/test_tools.py \
  tests/matmaster/services/test_active_mcp_replay.py \
  tests/matmaster/services/test_agent_run_stream_runtime_boundaries.py && \
  git add -u matmaster/manifests tests/matmaster/manifests tests/matmaster/context/test_manifests_equivalence.py tests/services/test_attachment_manifest_service.py && \
  git commit -m "refactor: remove legacy manifests package"
```

Expected: one commit with no trailer lines.

---

## Task 7: Delete HistoryRestoreService Re-export Shim

**Files:**
- Modify Test: `tests/matmaster/services/test_history_restore_service.py`
- Modify Test: `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- Modify Test: `tests/matmaster/integration/test_history_checkpoint_recovery_tail.py`
- Modify any remaining importers
- Delete: `src/services/history_restore_service.py`

**Spec 依据:** DESIGN.md §5.3、§13、§14 Phase 4a。

- [ ] **Step 1: Add failing static guard**

Append to `tests/matmaster/context/test_phase4_static_boundaries.py`:

```python
def test_history_restore_service_shim_is_removed() -> None:
    assert not (ROOT / "src/services/history_restore_service.py").exists()
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py::test_history_restore_service_shim_is_removed \
  -q
```

Expected: fail because the shim still exists.

- [ ] **Step 2: Update imports**

Replace:

```python
from src.services.history_restore_service import HistoryRestoreService
```

with:

```python
from src.services.model_history_restore_service import ModelHistoryRestoreService
```

Replace instantiations:

```text
HistoryRestoreService(events_table)
→ ModelHistoryRestoreService(events_table)
```

Rename test file if it still exists:

```text
tests/matmaster/services/test_history_restore_service.py
→ tests/matmaster/services/test_model_history_restore_service_tail.py
```

If the renamed file duplicates coverage already in `test_model_history_restore_service.py`, merge the unique tests into `test_model_history_restore_service.py` and delete the old file.

- [ ] **Step 3: Delete shim**

Delete:

```text
src/services/history_restore_service.py
```

- [ ] **Step 4: Verify**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "history_restore_service|HistoryRestoreService" \
  src matmaster tests
```

Expected: no matches. `ModelHistoryRestoreService` matches are expected if the query includes that name; this command should not.

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/integration/test_history_checkpoint_recovery_tail.py \
  -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/integration/test_history_checkpoint_recovery_tail.py && \
  git add -u src/services/history_restore_service.py tests/matmaster/services/test_history_restore_service.py && \
  git commit -m "refactor: remove history restore service shim"
```

Expected: one commit with no trailer lines.

---

## Task 8: Retire v0 Checkpoint Marker And Legacy Backend Restore

> **Status:** 未完成，等待 v0 restore/checkpoint compatibility retirement gate。当前已合并 Task 1-7；本任务需在产品/ops 确认旧 raw-only sessions 不再需要 backend model restore 后再执行。

**Files:**
- Modify: `src/services/history_checkpoint_codec.py`
- Modify: `matmaster/context/history_restore.py`
- Modify: `src/services/model_history_restore_service.py`
- Modify: `src/dao/chat_events_table.py` if `has_user_turn_context` becomes unused
- Modify Test: `tests/matmaster/services/test_history_checkpoint_codec.py`
- Modify Test: `tests/matmaster/context/test_history_restore.py`
- Modify Test: `tests/matmaster/services/test_model_history_restore_service.py`
- Modify Test: `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- Modify Test: `tests/test_chat_events_history_checkpoint.py`

**Spec 依据:** DESIGN.md §2 #4、§11.1、§11.2.1、§11.5、§14 Phase 4b、§17。

- [ ] **Step 1: Confirm retirement gate**

Before editing, ensure Task 1 Step 5 has a concrete decision. Add the decision text to the PR description and keep it in the task log.

Expected: this task starts only after the v0 retirement gate is satisfied.

- [ ] **Step 2: Add failing codec test for v0 marker rejection**

In `tests/matmaster/services/test_history_checkpoint_codec.py`, replace the old v0-acceptance test with:

```python
def test_validate_base_messages_rejects_v0_marker() -> None:
    legacy_marker = "previous" + "_session_summary"
    msg = UserMessage(
        content=(
            f"<{legacy_marker}>\n"
            "legacy summary\n"
            f"</{legacy_marker}>"
        )
    )

    with pytest.raises(ValueError, match="compacted_history"):
        validate_base_messages([msg])
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_history_checkpoint_codec.py::test_validate_base_messages_rejects_v0_marker \
  -q
```

Expected: fail because v0 marker is still accepted.

- [ ] **Step 3: Make checkpoint codec v1-only**

In `src/services/history_checkpoint_codec.py`, replace marker constants with:

```python
COMPACTED_HISTORY_MARKER = "<compacted_history>"
```

Replace `_has_acceptable_marker` with:

```python
def _has_compacted_history_marker(content: str) -> bool:
    return COMPACTED_HISTORY_MARKER in content
```

Update validation:

```python
if not _has_compacted_history_marker(first_content):
    raise ValueError(
        "checkpoint base_messages[0] must contain <compacted_history> marker"
    )
```

Remove `MARKERS_V0`, `MARKERS_V1`, and the `COMPAT:v0-checkpoint-marker` comment.

- [ ] **Step 4: Simplify core ModelHistoryRestorer to v1-only**

In `matmaster/context/history_restore.py`, remove constructor parameters and fields:

```text
has_user_turn_context
legacy_restore
```

The constructor should start like:

```python
class ModelHistoryRestorer:
    def __init__(
        self,
        *,
        get_latest_checkpoint: GetLatestCheckpoint,
        get_events_after: GetEventsAfter,
        deserialize_base_messages: DeserializeBaseMessages,
        events_to_messages: EventsToMessages,
        normalize_tool_result_event: NormalizeToolResultEvent,
        validate_history: ValidateHistory | None = None,
    ) -> None:
        self._get_latest_checkpoint = get_latest_checkpoint
        self._get_events_after = get_events_after
        self._deserialize_base_messages = deserialize_base_messages
        self._events_to_messages = events_to_messages
        self._normalize_tool_result_event = normalize_tool_result_event
        self._validate_history = validate_history
```

Replace `restore` with v1-only flow:

```python
def restore(
    self,
    session_id: str,
    *,
    spawn_id: str | None = None,
) -> list[Message]:
    checkpoint = self._get_latest_checkpoint(session_id, spawn_id)
    if self._is_v1_checkpoint(checkpoint):
        assert checkpoint is not None
        content = checkpoint["content"]
        covered = content.get("covered_until_event_id")
        if covered is None:
            logger.warning(
                "history_checkpoint.v1 has null covered_until_event_id; "
                "ignoring checkpoint and restoring from v1 event stream"
            )
            checkpoint = None
    else:
        checkpoint = None

    return self._restore_v1(
        session_id=session_id,
        spawn_id=spawn_id,
        checkpoint=checkpoint,
    )
```

Update `_restore_v1`:

```python
events = self._get_events_after(session_id, after, spawn_id)
if checkpoint is None:
    # v0 retirement: without a valid v1 checkpoint, raw-only legacy sessions
    # must not restore assistant-only history. Start replay at the first
    # user_turn_context; if none exists, restore an empty backend history.
    first_utc_idx = next(
        (
            idx
            for idx, event in enumerate(events)
            if event.get("type") == "user_turn_context"
        ),
        None,
    )
    if first_utc_idx is None:
        return []
    events = events[first_utc_idx:]
compatible_tail_events = [
    compatible
    for event in events
    if (compatible := self._event_to_v1_compatible_event(event)) is not None
]
```

Update `_event_to_v1_compatible_event` signature:

```python
def _event_to_v1_compatible_event(
    self,
    event: dict[str, Any],
) -> dict[str, Any] | None:
```

For raw user query, always skip:

```python
if source == "User" and etype == "query":
    return None
```

Remove `hybrid_mode`, `covered_invocations`, and all legacy raw `User/query` model restore logic.

The `checkpoint is None` branch is intentionally stricter than the Phase 1-3 hybrid path: it does not consume old `User/query`, and it also drops leading assistant/tool events before the first `user_turn_context` so a raw-only legacy session cannot become assistant-only model history.

- [ ] **Step 5: Simplify service wrapper**

In `src/services/model_history_restore_service.py`:

Remove `_restore_legacy_untrimmed`.

Remove `_session_has_user_turn_context`.

Remove local functions `has_user_turn_context` and `legacy_restore` from `_delegate_v1_restore`.

Construct the restorer with the reduced signature:

```python
restorer = ModelHistoryRestorer(
    get_latest_checkpoint=get_latest_checkpoint,
    get_events_after=get_events_after,
    deserialize_base_messages=deserialize_checkpoint_base_messages,
    events_to_messages=ChatHistoryConverter.events_to_messages,
    normalize_tool_result_event=self._normalize_tool_result_event,
    validate_history=validate_base_messages,
)
```

Update `restore_history` so all-invalid v1 checkpoints restore from the v1 event stream instead of falling back to legacy. Preserve the existing loop that tries each candidate v1 checkpoint and returns on the first successful restore; only after every v1 checkpoint candidate fails should the fallback branch below run. Do not delete the multi-checkpoint retry loop.

```python
if v1_checkpoints:
    logger.warning(
        "model_history_restore: all v1 checkpoints failed; restoring from v1 event stream "
        "session_id=%s spawn_id=%s",
        session_id,
        spawn_id,
    )

messages = self._delegate_v1_restore(
    session_id=session_id,
    spawn_id=spawn_id,
    task_id=task_id,
    raw_limit=raw_limit,
    checkpoint=None,
)
return trim_history_images(messages)
```

- [ ] **Step 6: Remove DAO probe if unused**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "has_user_turn_context" src matmaster tests
```

If the only remaining references are `src/dao/chat_events_table.py` and tests for that DAO method, delete:

```text
src/dao/chat_events_table.py::has_user_turn_context
tests/test_chat_events_history_checkpoint.py tests for has_user_turn_context
```

If another production feature still uses it for monitoring, keep the DAO method and remove only backend restore coupling.

- [ ] **Step 7: Update restore tests**

In `tests/matmaster/context/test_history_restore.py`, replace v0 / hybrid expectations with v1-only expectations:

```python
def test_no_checkpoint_restores_user_turn_context_events_only() -> None:
    restorer = make_restorer(
        checkpoint=None,
        events=[
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": {"content": "raw legacy"},
            },
            {
                "id": 2,
                "source": "matmaster",
                "type": "user_turn_context",
                "content": {
                    "message": {
                        "role": "user",
                        "content": "<current_instruction>\nvisible\n</current_instruction>",
                        "images": [],
                    }
                },
            },
        ],
    )

    messages = restorer.restore("sess-1")

    assert [m.content for m in messages] == [
        "<current_instruction>\nvisible\n</current_instruction>"
    ]
```

Add a test that raw-only legacy sessions produce no user messages:

```python
def test_raw_user_query_is_not_backend_restored_after_v0_retirement() -> None:
    restorer = make_restorer(
        checkpoint=None,
        events=[
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": {"content": "raw legacy"},
            }
        ],
    )

    assert restorer.restore("sess-1") == []
```

Add a second raw-only test with an assistant response to prevent assistant-only backend history:

```python
def test_raw_legacy_query_and_response_restore_empty_after_v0_retirement() -> None:
    restorer = make_restorer(
        checkpoint=None,
        events=[
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": {"content": "raw legacy"},
            },
            {
                "id": 2,
                "source": "MatMaster",
                "type": "response",
                "content": {"content": "legacy answer"},
            },
        ],
    )

    assert restorer.restore("sess-1") == []
```

In `tests/matmaster/services/test_model_history_restore_service.py`, delete tests named like:

```text
test_no_checkpoint_without_user_turn_context_uses_legacy_restore
```

Replace them with service-level v1-only no-checkpoint tests.

- [ ] **Step 8: Verify v0 compatibility text is gone**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "COMPAT:v0|MARKERS_V0|previous_session_summary|legacy_restore|hybrid_mode|covered_invocations|has_user_turn_context|events_to_dialog_messages\\(" \
  matmaster src tests
```

Expected:

```text
No COMPAT:v0, MARKERS_V0, previous_session_summary, legacy_restore, hybrid_mode, covered_invocations, has_user_turn_context
events_to_dialog_messages may remain only in src/services/chat_history.py and chat_history display/repair tests
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/context/test_history_restore.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/integration/test_history_checkpoint_recovery_tail.py \
  tests/test_chat_events_history_checkpoint.py \
  tests/test_chat_history_repair.py \
  -q
```

Expected: all pass.

- [ ] **Step 9: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/history_checkpoint_codec.py \
  matmaster/context/history_restore.py \
  src/services/model_history_restore_service.py \
  src/dao/chat_events_table.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/context/test_history_restore.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/integration/test_history_checkpoint_recovery_tail.py \
  tests/test_chat_events_history_checkpoint.py \
  tests/test_chat_history_repair.py && \
  git commit -m "refactor: retire v0 model history compatibility"
```

Expected: one commit with no trailer lines.

## Task 9: Final Static Verification, Test Sweep, And Follow-up Notes

> **Status:** 未完成，等待 Task 8 的 v0 退役 gate 完成后再执行最终静态验证与测试扫尾。当前只完成并合并 Task 1-7。

**Files:** read-only unless `.planning/context-refactor/FOLLOWUPS.md` needs a small append

**Spec 依据:** DESIGN.md §14 Phase 4、§18。

- [ ] **Step 1: Run final legacy-boundary scan**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "matmaster\\.manifests|matmaster\\.core\\.context_builder|matmaster\\.core\\.context_compactor|matmaster\\.types\\.context|matmaster\\.types\\.current_input|src\\.services\\.history_restore_service|\\bContextBuilder\\b|\\bcontext_builder\\b|CurrentInputContext|build_current_instruction_block|COMPAT:v0|MARKERS_V0|previous_session_summary|legacy_restore|hybrid_mode|covered_invocations|has_user_turn_context|_apply_user_instructions_to_initial_user_query|COMPAT:legacy-runtime-injection-helper" \
  matmaster src tests
```

Expected: no matches, except:

```text
pre_query_scope_event_id may appear only in TurnInput.from_payload legacy queue compatibility code and its test
events_to_dialog_messages may still appear in ChatHistoryConverter and display/repair tests
context_compactor may appear only as persisted event source string "context_compactor"
SessionContextBuilder is allowed and should not be removed by the ContextBuilder scan
```

- [ ] **Step 2: Run static boundary tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_phase4_static_boundaries.py \
  -q
```

Expected: pass.

- [ ] **Step 3: Run focused context and restore suites**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/integration/test_history_checkpoint_recovery_tail.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  -q
```

Expected: all pass.

- [ ] **Step 4: Run broader regression slice**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_reply_events.py \
  tests/test_chat_history_repair.py \
  tests/test_chat_events_history_checkpoint.py \
  tests/matmaster/core \
  tests/matmaster/services \
  tests/matmaster/types \
  tests/services \
  -q
```

Expected: all pass.

- [ ] **Step 5: Run full test suite if time budget allows**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest -q
```

Expected: all pass. If full suite is too slow for local iteration, record the focused suites that passed and the reason full suite was not run.

- [ ] **Step 6: Record follow-up boundaries**

If `.planning/context-refactor/FOLLOWUPS.md` does not already contain separate entries for these future specs, append a concise section:

```markdown
## Phase 4 Follow-up Boundaries

- Oversized Input remains a separate spec. It must design `InputSummaryConfig`, original-input disk persistence, path safety, failure behavior, and `user_turn_context.transform="oversized_summary"` end-to-end before implementation.
- Compaction fallback deletion remains a separate PR. It depends on Phase 3 fallback hit-rate and success-rate data; Phase 4 cleanup does not delete `sliding_window` / `tool_truncation`.
- Queue payload compatibility: Phase 4 Worker reads both `turn_input` and legacy `current_input_context` payload keys. A later deployment cleanup may remove the legacy key after all queued jobs and workers are known to be Phase 4+.
- Boundary semantics: `CurrentInputContext.pre_query_scope_event_id is None` is retired. Future oversized-input design must use `TurnInput.pre_turn_history_event_id: int`, where `0` means session start and not an unknown boundary.
```

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "Oversized Input|fallback deletion|turn_input.*current_input_context|pre_turn_history_event_id: int|session start" \
  .planning/context-refactor/FOLLOWUPS.md
```

Expected: the three follow-up boundaries are discoverable.

If FOLLOWUPS.md changed, commit:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  .planning/context-refactor/FOLLOWUPS.md && \
  git commit -m "chore: record context refactor follow-up boundaries"
```

Expected: commit only if the file changed.

---

## Notes For Oversized Input Spec

Phase 4 cleanup deliberately does **not** implement oversized input. The separate spec should start from these already-available extension points:

- `user_turn_context.transform="oversized_summary"` is reserved in DESIGN.md §3.2.
- `ContextCompactor.apply_compaction_plan(summary_override, session_attachments_override)` exists as the planned bypass.
- `TurnInput` now owns current user text, files, images, workspace paths, and `pre_turn_history_event_id`; the oversized design should decide what is written to durable storage and what remains in the provider-facing summary.
- The `CurrentInputContext.pre_query_scope_event_id is None` sentinel is retired. Oversized Input must use `TurnInput.pre_turn_history_event_id: int = 0` semantics; unknown boundary must not be reintroduced via `None`.
- The spec must define original text/file persistence, path safety, token budget thresholds, failure semantics, and restore behavior before any code changes.

## Notes For Fallback Deletion

Phase 4 cleanup deliberately keeps compaction fallback. A later PR may delete fallback only after Phase 3 telemetry answers:

- How often summary compaction fails.
- Whether fallback paths produce usable user-visible outcomes.
- Whether no-checkpoint behavior after fallback is acceptable for long tool loops.

Until that data exists, `sliding_window` / `tool_truncation` remain runtime fallback paths.
