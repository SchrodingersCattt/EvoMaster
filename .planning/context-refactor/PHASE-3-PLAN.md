# Phase 3 Compaction 接入与 Prompt 形态决策 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 DESIGN.md v3.3 Phase 3：把 preflight / runtime compaction 主路径从 `matmaster.core.context_compactor.ContextBuilder + CompactionRehydrator` 切到 `matmaster.context.assembly.ContextAssembler.assemble_compaction(...)`，让 durable checkpoint 写出 `history_checkpoint.v1` + `<compacted_history>` marker，并完成 `<turn_attachments>` 拆分 prompt 形态的 offline A/B 决策。Phase 3 完成后，普通 user turn 与 compaction 两条 provider-facing user message 生产路径都由 `matmaster.context` 内核统一负责。

**Architecture:** Phase 2C 已经让普通 user turn 通过 `ContextAssembler.assemble_turn(...)` 渲染，并在 `AgentRuntimeSpec` 上预留了 `context_assembler` / `user_instructions_port` / `session_events_port` / `session_jobs_port` 四个字段；但 `Exp.build_runtime()` 仍然为 compactor 构造旧 `CompactionRehydrator`，`ContextCompactor.apply_compaction_plan(...)` 仍手写 `build_compact_bundle()` 与 `build_current_instruction_block()`。Phase 3 的核心是把 `ContextCompactor` 迁到 `matmaster/context/compaction.py` 并改成只负责 token 估算、summary LLM、fallback 和 compaction 生命周期，真正的 provider-facing message 装配交给 `ContextAssembler.assemble_compaction(...)`。`matmaster/core/context_compactor.py` 与 `matmaster/core/context_builder.py` 在本阶段变成 re-export shim；`matmaster/manifests/*` 继续保留到 Phase 4。

**Tech Stack:** Python 3.11+ / uv / pytest / pytest-asyncio / dataclasses / Pydantic `UserMessage` / `ContextAssembler` / `HistoryCheckpointService` / `RunEventFanout` / MatMaster runtime ports

**Spec 来源:** `.planning/context-refactor/DESIGN.md` §2、§3.3-3.6、§4.1-4.2、§5.1-5.3、§6.5、§7bis.3、§9、§10、§12 Case 2/4、§13、§14 Phase 3、§15、§16、§17、附录 B「Phase 3 改动」；`.planning/context-refactor/PHASE-2C-PLAN.md`「Notes For Phase 3」；`.planning/context-refactor/FOLLOWUPS.md`「议题 3」。

---

## 全局约束

1. **Phase 3 只迁移 compaction 主路径与 prompt 形态决策。** 不删除 `matmaster/manifests/*`，不退役 `COMPAT:v0-restore`，不退役 `COMPAT:v0-checkpoint-marker`，不做 oversized input。
2. **`ContextCompactor` 不再 import `ContextBuilder` 或 `CompactionRehydrator`。** 迁移后真实代码在 `matmaster/context/compaction.py`，旧路径 `matmaster/core/context_compactor.py` 只 re-export。
3. **checkpoint 写入端切到 v1 必须与 v1 marker 同步。** Durable compaction 的 `base_messages[0].content` 必须包含 `<compacted_history>` 后，`agent_compaction.py` 才能写 `schema_version="history_checkpoint.v1"`；不得出现 `schema_version="history_checkpoint.v1"` 但 base message 仍是 `<previous_session_summary>` 的中间状态。`HistoryCheckpointService.build_checkpoint_sink()` 已接收并透传 `schema_version="history_checkpoint.v1"`、`render_version="user_context_render.v1"`、`user_instructions_text`、`user_instructions_hash`，Phase 3 只切 compaction 写入端。
4. **codec 仍接受 v0 + v1 双 marker。** `src/services/history_checkpoint_codec.py` 的 `COMPAT:v0-checkpoint-marker` 保留到 Phase 4。
5. **runtime compaction 必须有确定 high-water event id。** `ContextAssembler.assemble_compaction(RUNTIME_COMPACTION)` 不允许隐式派生边界。Phase 3 通过 compaction history port 暴露 `latest_scope_event_id()`，在 `pre_compaction_barrier` flush 后读取。若 runtime high-water 缺失，不能用 `covered_until=0` 装配贫瘠 anchor；必须走 runtime fallback（`sliding_window` / `tool_truncation`），结果为 `ephemeral` 且不写 checkpoint。
6. **preflight compaction 的 covered_until 来自本轮写入前边界。** `CurrentInputContext.pre_query_scope_event_id` 为 `N` 时，`TurnInput.pre_turn_history_event_id=N`；`0` 表示首轮前无事件。不能用 `None` 写 v1 checkpoint。
7. **修复 Phase 2C followup 的双层 `<current_instruction>` wrap。** `AgentKernel._run_items()` 不再把已渲染的 provider-facing `task` 写回 `CurrentInputContext.user_text` 后交给 compactor；compactor 使用 service 注入的 raw current input。
8. **fallback 保留。** Summary 成功走 durable v1 checkpoint；summary 失败时 runtime compaction 继续使用 `sliding_window` / `tool_truncation` fallback，结果为 ephemeral，不写 checkpoint。Preflight summary 失败继续 raise，不 silent fallback。
9. **fallback 埋点用现有 compaction lifecycle event + structured log。** 不新增全局 metrics service；Phase 3 只保证 `CompactionEvent.strategy/durability/failure_reason` 与日志能区分 summary 成功、sliding_window、tool_truncation、boundary missing。
10. **prompt shape A/B 是决策门。** `TurnInput` 已支持 `split_attachments=True`；Phase 3 增加 `ContextRenderOptions(split_turn_attachments=...)` 并接到 assembler。默认是否切换取决于 Task 9 的 offline eval 结果；若没有通过记录，生产默认保持 Phase 2C 合并形态。
11. **`run_meta` 只传 passive metadata。** Phase 3 可以在 `run_meta` 中携带 `user_instructions_text/hash/truncated` 这类值，但不得塞入 port、factory、assembler、service object 或 callback。
12. **service 与 compactor 共享同一 factory 模块。** `src/services/context_assembly_factory.py` 扩展出 DAO 侧 `build_context_assembler(...)` 与 runtime history 侧 adapter。`agent_run_service.py` 使用 DAO 侧 factory；`Exp.build_runtime()` 使用 runtime history adapter。两条路径可以有不同输入能力，但 assembler/session builder/render options 的构造规则必须集中在同一个 factory 模块中，避免漂移。
13. **`AgentRuntimeSpec` 的 Phase 2C 预留字段在 Phase 3 变成真实注入。** `context_assembler` / `session_events_port` / `session_jobs_port` 在 `Exp.build_runtime()` 写入 spec，供 compactor 与后续验证读取；kernel 本身不直接用这些字段。当前 jobs 仍为空实现，但字段应注入一个返回 `SessionJobs.empty()` 的 port，而不是把测试期望与实现留成 `None` 分歧。
14. **`matmaster/types/current_input.py` 仍保留 shim。** Phase 3 可以减少 runtime 对 `build_current_instruction_block` 的依赖，但 `CurrentInputContext` 与 shim 删除属于 Phase 4。
15. **所有 Python 命令使用 `uv run python` 或 `uv run pytest`。**
16. **一个 Task 一个 commit。** Task 1 与 Task 11 是 read-only / verification，无 commit；其他 Task 按计划中的 commit message 提交。
17. **当前工作树可能 dirty。** 开始每个 Task 前先检查将要编辑的文件 diff，不能恢复、格式化或改写用户已有改动。

---

## File Structure

新建文件：

- Create: `matmaster/context/compaction.py` — `ContextCompactor` 真实实现迁入这里，改为依赖 `ContextAssembler`。
- Create: `matmaster/context/system_prompt.py` — 承接旧 `ContextBuilder` 的 system prompt 组装职责，Phase 3 保留 `ContextBuilder` 名字兼容。
- Create: `tests/matmaster/context/test_compaction.py` — 新 compaction 单元测试，覆盖 assembler 接入、v1 marker、preflight/runtime covered_until、fallback。
- Create: `tests/matmaster/context/test_system_prompt.py` — system prompt shim 等价测试。
- Create: `.planning/context-refactor/PHASE-3-PROMPT-AB.md` — Task 9 生成的 A/B 评估记录与默认策略结论。

修改文件：

- Modify: `matmaster/core/context_compactor.py` — 改为 thin shim，re-export `matmaster.context.compaction`。
- Modify: `matmaster/core/context_builder.py` — 改为 thin shim，re-export `matmaster.context.system_prompt.ContextBuilder`。
- Modify: `matmaster/context/assembly.py` — 新增 `ContextRenderOptions`，让 assembler 控制 `split_turn_attachments`。
- Modify: `matmaster/context/compositions.py` — `ContextCompositionInputs` 增加 `split_turn_attachments`，`_step_turn_input` 透传给 `TurnInput.to_sections(...)`。
- Modify: `src/services/context_assembly_factory.py` — 新增 DAO 侧 `build_context_assembler(...)` helper 与 runtime history adapter，统一 assembler/session builder/render options 的构造规则。
- Modify: `src/services/agent_run_service.py` — Stage 5b 改用 `build_context_assembler(...)`；把 user instructions hash/truncated 作为 passive metadata 写入 `run_meta`。
- Modify: `matmaster/core/exp.py` — 不再构造 `CompactionRehydrator`；构造 `ContextAssembler`、`UserInstructions`、新 `ContextCompactor`，并把 assembler/ports 注入 `AgentRuntimeSpec`。
- Modify: `matmaster/core/agent.py` — preflight compaction 传 raw `CurrentInputContext`，不再把 provider-facing `task` 覆盖回 `user_text`。
- Modify: `matmaster/core/agent_compaction.py` — checkpoint payload 写 v1 metadata 与 `covered_until_event_id`。
- Modify: `matmaster/types/runtime_ports.py` — compaction history port 增加 `latest_scope_event_id()`；checkpoint payload TypedDict 增加 v1 metadata 字段。
- Modify: `src/services/agent_run_history_wiring.py` — `_RunSessionEventHistory` 实现 `latest_scope_event_id()` 与 raw `query_context_events(...)`，供 runtime compactor assembly 使用。
- Modify: `tests/matmaster/core/test_context_compactor.py` / `tests/matmaster/devshell/test_compaction_via_devshell.py` — import 路径与断言更新到 v1 marker。
- Modify: `tests/matmaster/core/test_agent_kernel_compaction.py` — checkpoint payload v1 metadata、raw current input、boundary override 测试。
- Modify: `tests/matmaster/core/test_exp_runtime_v2.py` — 断言 Exp 注入 assembler/ports，不再断言 `_rehydrator`。
- Modify: `tests/matmaster/integration/test_history_checkpoint_recovery.py` — compactor 构造与 v1 marker 断言更新。

不变文件：

- `matmaster/manifests/*` — Phase 3 不动。
- `src/services/model_history_restore_service.py` / `matmaster/context/history_restore.py` — Phase 2B 已落地，Phase 3 只通过新增 v1 checkpoint 测试验证。
- `src/services/history_checkpoint_codec.py` — 通常不需要改；若测试发现 marker 常量已有缺口，只补测试或错误信息，不删除 v0 marker。
- `src/services/stream_sse_filter.py` / `matmaster/integration/sse_handler.py` — Phase 1 已隐藏 internal events。

---

## Task 1: Baseline And Phase Boundary Inventory

**Files:** read-only

**Spec 依据:** DESIGN.md §14 Phase 3、附录 B「Phase 3 改动」、PHASE-2C-PLAN.md「Notes For Phase 3」。

- [ ] **Step 1: Confirm uv environment and dirty files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -V && git status --short
```

Expected:

```text
Python 3.11+ or Python 3.13.x
git status --short prints current dirty files
```

If any Phase 3 target file is dirty, inspect it before editing:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git diff -- \
  matmaster/core/context_compactor.py \
  matmaster/core/context_builder.py \
  matmaster/context/assembly.py \
  matmaster/context/compositions.py \
  src/services/context_assembly_factory.py \
  src/services/agent_run_service.py \
  matmaster/core/exp.py \
  matmaster/core/agent.py \
  matmaster/core/agent_compaction.py \
  matmaster/types/runtime_ports.py \
  src/services/agent_run_history_wiring.py
```

Expected: either empty output or user changes that can be preserved by applying Phase 3 edits around them. Do not revert unrelated changes.

- [ ] **Step 2: Confirm Phase 2C artifacts are present**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && \
  test -f matmaster/context/assembly.py && \
  test -f matmaster/context/compositions.py && \
  test -f matmaster/context/sources/turn_input.py && \
  test -f matmaster/context/session.py && \
  test -f src/services/context_assembly_factory.py && \
  test -f src/services/context_assembly_ports.py && \
  test -f src/services/context_turn_intent.py && \
  test -f src/services/user_turn_context_service.py && \
  rg -n "def hash_user_instructions" src/services/user_turn_context_service.py && \
  test -f tests/services/test_context_assembly_factory.py && \
  test -f tests/matmaster/services/test_agent_run_stream_context_cutover.py
```

Expected: command exits `0` and prints the `hash_user_instructions` definition line. If a Phase 2C artifact or helper symbol is missing, stop and restore Phase 2C baseline before starting Phase 3.

- [ ] **Step 3: Snapshot current compaction imports**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "ContextCompactor|CompactionRehydrator|build_compact_bundle|build_current_instruction_block|previous_session_summary|compacted_history" \
  matmaster src tests .planning/context-refactor/FOLLOWUPS.md
```

Expected before Phase 3:

```text
matmaster/core/context_compactor.py imports ContextBuilder, CompactionRehydrator, build_current_instruction_block
matmaster/core/exp.py constructs CompactionRehydrator
tests assert <previous_session_summary> in compaction outputs
FOLLOWUPS.md contains the double <current_instruction> preflight issue
```

Expected after Phase 3 is described in Task 11.

- [ ] **Step 4: Run focused baseline tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/services/test_context_assembly_factory.py \
  -q
```

Expected: all pass. If baseline fails, stop and fix baseline first; Phase 3 must start from a known-good Phase 2C state.

This Task has no commit.

---

## Task 2: Add Render Options For Prompt Shape A/B

**Files:**
- Modify: `matmaster/context/compositions.py`
- Modify: `matmaster/context/assembly.py`
- Modify Test: `tests/matmaster/context/test_compositions.py`
- Modify Test: `tests/matmaster/context/test_assembly.py`

**Spec 依据:** DESIGN.md §6.5、§6bis.6、§14 Phase 3c。

- [ ] **Step 1: Write failing composition test for split attachments**

Append to `tests/matmaster/context/test_compositions.py`:

```python
def test_compaction_inputs_can_split_turn_attachments() -> None:
    from matmaster.context.compositions import (
        COMPACTED_COMPOSITION,
        ContextCompositionInputs,
    )
    from matmaster.context.sections import ContextView
    from matmaster.context.sources.turn_input import (
        TurnAttachmentsSource,
        TurnInput,
        TurnInstructionSource,
    )

    result = COMPACTED_COMPOSITION.apply(
        ContextCompositionInputs(
            compacted_history_summary="Earlier summary.",
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Use current file."),
                attachments=TurnAttachmentsSource(files=("https://oss/a.cif",)),
                pre_turn_history_event_id=5,
            ),
            split_turn_attachments=True,
        )
    )

    runtime = result.render(ContextView.RUNTIME)
    checkpoint = result.render(ContextView.CHECKPOINT)
    assert "<current_instruction>" in runtime
    assert "<turn_attachments>" in runtime
    assert "<turn_attachments>" not in checkpoint
```

- [ ] **Step 2: Write failing assembler test for default-preserving render options**

Append to `tests/matmaster/context/test_assembly.py`:

```python
@pytest.mark.asyncio
async def test_assembler_render_options_default_to_merged_turn_attachments() -> None:
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=EventsPort()))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Use current file."),
                attachments=TurnAttachmentsSource(files=("https://oss/a.cif",)),
                pre_turn_history_event_id=5,
            ),
            user_instructions=_instructions(),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<current_instruction>" in runtime
    assert "[Current attachments]" in runtime
    assert "<turn_attachments>" not in runtime


@pytest.mark.asyncio
async def test_assembler_render_options_can_split_turn_attachments() -> None:
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        render_options=ContextRenderOptions(split_turn_attachments=True),
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Use current file."),
                attachments=TurnAttachmentsSource(files=("https://oss/a.cif",)),
                pre_turn_history_event_id=5,
            ),
            user_instructions=_instructions(),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<current_instruction>" in runtime
    assert "<turn_attachments>" in runtime
    assert "[Current attachments]" not in runtime
```

- [ ] **Step 3: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_compositions.py::test_compaction_inputs_can_split_turn_attachments \
  tests/matmaster/context/test_assembly.py::test_assembler_render_options_default_to_merged_turn_attachments \
  tests/matmaster/context/test_assembly.py::test_assembler_render_options_can_split_turn_attachments \
  -q
```

Expected: failures for missing `split_turn_attachments` / `ContextRenderOptions`.

- [ ] **Step 4: Implement `ContextCompositionInputs.split_turn_attachments`**

In `matmaster/context/compositions.py`, update `ContextCompositionInputs`:

```python
@dataclass(frozen=True)
class ContextCompositionInputs:
    user_instructions_text: str = ""
    compacted_history_summary: str = ""
    turn_input: TurnInput | None = None
    session_sections: tuple[ContextSection, ...] = ()
    session_jobs: SessionJobs = field(default_factory=SessionJobs.empty)
    session_attachments_override: SectionSource | None = None
    defer_turn_instruction: bool = False
    split_turn_attachments: bool = False
```

Update `_step_turn_input`:

```python
def _step_turn_input(inputs: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    if inputs.turn_input is None:
        return ()
    turn_input = inputs.turn_input
    if inputs.defer_turn_instruction:
        turn_input = dataclasses.replace(
            turn_input,
            instruction=dataclasses.replace(
                turn_input.instruction,
                deferred=True,
            ),
        )
    return turn_input.to_sections(
        split_attachments=inputs.split_turn_attachments,
    )
```

- [ ] **Step 5: Implement `ContextRenderOptions` in assembler**

In `matmaster/context/assembly.py`, add near request dataclasses:

```python
@dataclass(frozen=True)
class ContextRenderOptions:
    split_turn_attachments: bool = False
```

Update `ContextAssembler.__init__`:

```python
    def __init__(
        self,
        ports: ContextAssemblyPorts,
        *,
        session_context_factory: SessionContextFactory | None = None,
        render_options: ContextRenderOptions | None = None,
        _session_section_builder_for_tests: SessionSectionBuilder | None = None,
    ) -> None:
        self._ports = ports
        self._render_options = render_options or ContextRenderOptions()
        ...
```

When constructing `ContextCompositionInputs` in both `assemble_turn` and `assemble_compaction`, pass:

```python
split_turn_attachments=self._render_options.split_turn_attachments,
```

- [ ] **Step 6: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_compositions.py \
  tests/matmaster/context/test_assembly.py \
  tests/matmaster/context/sources/test_turn_input.py \
  -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add matmaster/context/compositions.py matmaster/context/assembly.py \
  tests/matmaster/context/test_compositions.py tests/matmaster/context/test_assembly.py
git commit -m "feat: add context render options"
```

---

## Task 3: Centralize ContextAssembler Construction

**Files:**
- Modify: `src/services/context_assembly_factory.py`
- Modify: `src/services/agent_run_service.py`
- Modify Test: `tests/services/test_context_assembly_factory.py`
- Modify Test: `tests/matmaster/services/test_agent_run_stream_context_cutover.py`

**Spec 依据:** DESIGN.md §7bis.6、§10.3、PHASE-2C-PLAN.md「Notes For Phase 3」#2。

- [ ] **Step 1: Add failing factory tests**

Append to `tests/services/test_context_assembly_factory.py`:

```python
def test_build_context_assembler_wires_ports_and_render_options() -> None:
    from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
    from src.services.context_assembly_factory import build_context_assembler

    class EventsTable:
        def query_context_events(self, **kwargs):
            return []

    assembler, ports = build_context_assembler(
        events_table=EventsTable(),
        skill_registry=None,
        legal_mcp_servers=None,
        schemas_by_server=None,
        split_turn_attachments=True,
    )

    assert isinstance(assembler, ContextAssembler)
    assert ports.session_jobs is not None
    assert assembler._render_options == ContextRenderOptions(
        split_turn_attachments=True
    )
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/services/test_context_assembly_factory.py::test_build_context_assembler_wires_ports_and_render_options \
  -q
```

Expected: import failure for missing `build_context_assembler`.

- [ ] **Step 3: Implement factory helper**

In `src/services/context_assembly_factory.py`, add:

```python
from collections.abc import Mapping
from typing import Any

from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
from matmaster.context.ports import ContextAssemblyPorts
from src.services.context_assembly_ports import AppSessionEventsPort, AppSessionJobsPort


def build_context_assembler(
    *,
    events_table: object,
    skill_registry: Any | None,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
    split_turn_attachments: bool = False,
):
    ports = ContextAssemblyPorts(
        session_events=AppSessionEventsPort(events_table=events_table),
        session_jobs=AppSessionJobsPort(),
    )
    assembler = ContextAssembler(
        ports=ports,
        session_context_factory=build_session_context_factory(
            skill_registry=skill_registry,
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        ),
        render_options=ContextRenderOptions(
            split_turn_attachments=split_turn_attachments,
        ),
    )
    return assembler, ports
```

Keep `build_session_context_factory(...)` unchanged; existing callers and tests still use it.

- [ ] **Step 4: Switch AgentRunService Stage 5b to the factory**

In `src/services/agent_run_service.py`, replace direct imports:

```python
from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import ContextAssemblyPorts, UserInstructions
from src.services.context_assembly_factory import build_session_context_factory
from src.services.context_assembly_ports import AppSessionEventsPort, AppSessionJobsPort
```

with:

```python
from matmaster.context.assembly import ContextAssemblyIntent, TurnAssemblyRequest
from matmaster.context.ports import UserInstructions
from src.services.context_assembly_factory import build_context_assembler
```

Replace Stage 5b assembler setup with:

```python
context_assembler, assembly_ports = build_context_assembler(
    events_table=events_table,
    skill_registry=self._build_skill_registry(exp_config, session=pg_ctx.session),
    legal_mcp_servers=(pg_ctx.run_meta or {}).get("legal_mcp_servers"),
    schemas_by_server=(pg_ctx.run_meta or {}).get("schemas_by_server"),
    split_turn_attachments=bool(
        (pg_ctx.run_meta or {}).get("split_turn_attachments", False)
    ),
)
session_events_port = assembly_ports.session_events
```

- [ ] **Step 5: Add passive user instructions metadata for Exp**

In the existing `pg_ctx = pg_ctx.with_run_meta(...)` block that writes `figure_upload_config` and `user_instructions`, include:

```python
pg_ctx = pg_ctx.with_run_meta(
    figure_upload_config=figure_upload_config,
    user_instructions=user_instructions.text,
    user_instructions_hash=user_instructions.hash,
    user_instructions_truncated=user_instructions.truncated,
)
```

These are passive metadata values. Do not add ports or assembler objects to `run_meta`.
`with_run_meta(...)` is merge-style in this codebase: it adds/replaces the named keys while preserving existing run metadata. If implementation inspection shows replace-style behavior instead, update the call to explicitly carry forward the previous `pg_ctx.run_meta` keys rather than dropping them.

- [ ] **Step 6: Verify focused service tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/services/test_context_assembly_factory.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  tests/matmaster/services/test_agent_run_stream.py \
  -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/services/context_assembly_factory.py src/services/agent_run_service.py \
  tests/services/test_context_assembly_factory.py tests/matmaster/services/test_agent_run_stream_context_cutover.py
git commit -m "refactor: centralize context assembler wiring"
```

---

## Task 4: Extend Compaction Boundary And Checkpoint Payload Contracts

**Files:**
- Modify: `matmaster/types/runtime_ports.py`
- Modify: `src/services/agent_run_history_wiring.py`
- Modify: `matmaster/core/context_compactor.py`
- Modify Test: `tests/matmaster/types/test_runtime_ports.py`
- Modify Test: `tests/matmaster/services/test_agent_run_stream.py`
- Modify Test: `tests/matmaster/services/test_history_checkpoint_service.py`

**Spec 依据:** DESIGN.md §3.3、§7bis.3、§9.2、§14 Phase 3b。

- [ ] **Step 1: Add failing tests for high-water history port**

In `tests/matmaster/types/test_runtime_ports.py`, extend the empty history test:

```python
def test_empty_session_event_history_latest_scope_event_id_is_zero() -> None:
    history = EmptySessionEventHistory()

    assert history.latest_scope_event_id() == 0
```

In `tests/matmaster/services/test_agent_run_stream.py`, update the fake events table used by history wiring tests so it exposes a latest event id:

```python
events_table.get_latest_scope_event_id.return_value = 25
...
history = runtime_ports.compaction.history
assert history is not None
assert history.latest_scope_event_id() == 25
history.query_context_events(spawn_id=None, until_event_id=10, event_types=("query",))
events_table.get_latest_scope_event_id.assert_called_with("sess-1", None)
events_table.query_context_events.assert_called_with(
    session_id="sess-1",
    spawn_id=None,
    until_event_id=10,
    event_types=("query",),
    limit=None,
    order="asc",
)
```

Expected red: `SessionEventHistoryPort` / `EmptySessionEventHistory` / `_RunSessionEventHistory` do not expose `latest_scope_event_id()` / `query_context_events(...)` yet.

- [ ] **Step 2: Extend runtime port and checkpoint payload types**

In `matmaster/types/runtime_ports.py`, update `CompactionCheckpointPayload`:

```python
class CompactionCheckpointPayload(TypedDict):
    durability: str
    strategy: str
    covered_until_event_id: NotRequired[int]
    schema_version: NotRequired[str]
    render_version: NotRequired[str]
    user_instructions_text: NotRequired[str]
    user_instructions_hash: NotRequired[str]
```

Update `SessionEventHistoryPort`:

```python
class SessionEventHistoryPort(Protocol):
    def query_events(self) -> list[dict[str, Any]]: ...

    def all_events(self) -> list[dict[str, Any]]: ...

    def query_context_events(
        self,
        *,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = "asc",
    ) -> list[dict[str, Any]]: ...

    def latest_checkpoint_covered_until_event_id(self) -> int | None: ...

    def latest_scope_event_id(self) -> int | None: ...
```

Update `EmptySessionEventHistory`:

```python
    def latest_scope_event_id(self) -> int | None:
        return 0

    def query_context_events(
        self,
        *,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        return []
```

- [ ] **Step 3: Implement high-water history adapter**

In `src/services/agent_run_history_wiring.py`, add:

```python
    def _get_latest_scope_event_id() -> int | None:
        if events_table is None:
            return 0
        try:
            raw = events_table.get_latest_scope_event_id(session_id, None)
        except Exception:
            logger.warning("manifest: get_latest_scope_event_id failed", exc_info=True)
            return None
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return None

    def _query_context_events(
        *,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        if events_table is None:
            return []
        try:
            events = events_table.query_context_events(
                session_id=session_id,
                spawn_id=spawn_id,
                until_event_id=until_event_id,
                event_types=event_types,
                limit=limit,
                order=order,
            )
            return events if isinstance(events, list) else []
        except Exception:
            logger.warning("manifest: query_context_events failed", exc_info=True)
            return []
```

Then add method to `_RunSessionEventHistory`:

```python
        def query_context_events(
            self,
            *,
            spawn_id: str | None,
            until_event_id: int | None = None,
            event_types: tuple[str, ...] | None = None,
            limit: int | None = None,
            order: str = "asc",
        ) -> list[dict[str, Any]]:
            return _query_context_events(
                spawn_id=spawn_id,
                until_event_id=until_event_id,
                event_types=event_types,
                limit=limit,
                order=order,
            )

        def latest_scope_event_id(self) -> int | None:
            return _get_latest_scope_event_id()
```

- [ ] **Step 4: Extend `CompactionResult` with v1 metadata fields**

In the existing `CompactionResult` dataclass in `matmaster/core/context_compactor.py`, add defaults:

```python
    user_instructions_text: str = ""
    user_instructions_hash: str = (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
```

This is a compatibility contract only. **Do not update `agent_compaction.py` to emit `schema_version="history_checkpoint.v1"` in Task 4.** Emission moves to Task 6 after the real compactor emits `<compacted_history>`, so no commit can create a v1 checkpoint payload around a v0 `<previous_session_summary>` base message.

Task 5 moves this dataclass to `matmaster/context/compaction.py`.

- [ ] **Step 5: Verify focused tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/types/test_runtime_ports.py \
  -q
```

Expected: all pass. `history_checkpoint_codec.py` still accepts both markers, and `agent_compaction.py` still emits only the old minimal payload until Task 6 switches the base message marker.

- [ ] **Step 6: Commit**

```bash
git add matmaster/types/runtime_ports.py src/services/agent_run_history_wiring.py \
  matmaster/core/context_compactor.py tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_history_checkpoint_service.py tests/matmaster/types/test_runtime_ports.py
git commit -m "feat: extend compaction checkpoint metadata"
```

---

## Task 5: Move ContextCompactor To `matmaster.context.compaction`

**Files:**
- Create: `matmaster/context/compaction.py`
- Modify: `matmaster/core/context_compactor.py`
- Modify tests importing compactor helpers

**Spec 依据:** DESIGN.md §5.1、§5.3、§9.3、§13、§14 Phase 3a。

- [ ] **Step 1: Move implementation mechanically**

Create `matmaster/context/compaction.py` with the current contents of `matmaster/core/context_compactor.py`.

Then replace `matmaster/core/context_compactor.py` with:

```python
"""Compatibility shim for the Phase 3 context compaction move.

The real implementation lives in `matmaster.context.compaction`.
This shim stays until Phase 4 removes legacy core import paths.
"""

from matmaster.context.compaction import (  # noqa: F401
    CURRENT_INPUT_CONTINUATION_INSTRUCTION,
    SUMMARY_SYSTEM_PROMPT,
    CompactionPlan,
    CompactionResult,
    ContextCompactor,
    estimate_tokens,
    parse_turns,
)
```

- [ ] **Step 2: Update tests to prefer new import path**

In `tests/matmaster/core/test_context_compactor.py` and `tests/matmaster/devshell/test_compaction_via_devshell.py`, update top-level imports from:

```python
from matmaster.core.context_compactor import (
    ContextCompactor,
    estimate_tokens,
    parse_turns,
)
```

to:

```python
from matmaster.context.compaction import (
    ContextCompactor,
    estimate_tokens,
    parse_turns,
)
```

Keep some shim coverage by adding one tiny test to `tests/matmaster/core/test_context_compactor.py`:

```python
def test_core_context_compactor_shim_reexports_new_implementation() -> None:
    from matmaster.context.compaction import ContextCompactor as NewContextCompactor
    from matmaster.core.context_compactor import ContextCompactor as ShimContextCompactor

    assert ShimContextCompactor is NewContextCompactor
```

- [ ] **Step 3: Verify mechanical move**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  -q
```

Expected: all pass; behavior still uses old rehydrator/builder until Task 6.

- [ ] **Step 4: Commit**

```bash
git add matmaster/context/compaction.py matmaster/core/context_compactor.py \
  tests/matmaster/core/test_context_compactor.py tests/matmaster/devshell/test_compaction_via_devshell.py
git commit -m "refactor: move context compactor into context package"
```

---

## Task 6: Refactor ContextCompactor To Use ContextAssembler

**Files:**
- Modify: `matmaster/context/compaction.py`
- Modify: `matmaster/core/agent_compaction.py`
- Modify Test: `tests/matmaster/context/test_compaction.py`
- Modify Test: `tests/matmaster/core/test_context_compactor.py`
- Modify Test: `tests/matmaster/core/test_agent_kernel_compaction.py`
- Modify Test: `tests/matmaster/devshell/test_compaction_via_devshell.py`

**Spec 依据:** DESIGN.md §7bis.3、§9.1-9.3、§12 Case 2/4、§14 Phase 3a-3b、FOLLOWUPS.md 议题 3。

- [ ] **Step 1: Add new compaction tests for v1 assembly**

Create `tests/matmaster/context/test_compaction.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.assembly import ContextAssembler
from matmaster.context.compaction import CompactionPlan, ContextCompactor
from matmaster.context.ports import ContextAssemblyPorts, SessionEvent, UserInstructions
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.types.current_input import CurrentInputContext
from matmaster.types.messages import AssistantMessage, LLMResponse, SystemMessage, UserMessage
from matmaster.types.runtime import CompactionConfig


class EventsPort:
    def __init__(self) -> None:
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return (
            SessionEvent(
                id=1,
                event_type="query",
                source="User",
                content={"content": "old question"},
            ),
        )


def session_sections(events, until_event_id, include_attachments):
    sections = []
    if include_attachments:
        sections.append(
            ContextSection(
                key="session_attachments",
                tag="session_attachments",
                content="file_1 old.cif https://oss/old.cif",
                order=SectionOrder.SESSION_ATTACHMENTS,
                views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
            )
        )
    return tuple(sections)


class Provider:
    def __init__(self, summary: str = "Summary text.") -> None:
        self.summary = summary
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self.summary, finish_reason="stop")


def make_compactor(*, provider=None, boundary=lambda: 9) -> ContextCompactor:
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        _session_section_builder_for_tests=session_sections,
    )
    return ContextCompactor(
        config=CompactionConfig(context_limit=1000, trigger_ratio=0.9),
        summary_provider=provider or Provider(),
        context_assembler=assembler,
        user_instructions=UserInstructions(text="Use SI units.", hash="sha256:abc"),
        session_id="sess-1",
        spawn_id=None,
        runtime_covered_until_provider=boundary,
    )


@pytest.mark.asyncio
async def test_runtime_compaction_uses_high_water_and_compacted_history_marker() -> None:
    compactor = make_compactor()
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
    ]

    result = await compactor.apply_compaction_plan(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
    )

    assert result.base_snapshot is not None
    assert "<compacted_history>" in result.base_snapshot[0]["content"]
    assert "<previous_session_summary>" not in result.base_snapshot[0]["content"]
    assert result.checkpoint_covered_until_event_id == 9
    assert result.user_instructions_text == "Use SI units."
    assert result.user_instructions_hash == "sha256:abc"


@pytest.mark.asyncio
async def test_runtime_compaction_missing_boundary_uses_fallback() -> None:
    compactor = make_compactor(boundary=lambda: None)
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
    ]

    result = await compactor.apply_compaction_plan(
        CompactionPlan(
            compaction_id="root:1",
            compaction_count=1,
            phase="runtime",
            trigger_tokens=999,
            turn=3,
        ),
        messages,
    )

    assert result.durability == "ephemeral"
    assert result.base_snapshot is None
    assert result.failure_reason == "runtime_current_event_boundary_missing"


@pytest.mark.asyncio
async def test_preflight_compaction_uses_raw_current_input_without_double_wrap() -> None:
    compactor = make_compactor()
    ctx = CurrentInputContext.from_values(
        user_text="Use current file.",
        files=["https://oss/current.cif"],
        pre_query_scope_event_id=7,
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
        UserMessage(content="<user_instructions>wrapped</user_instructions>"),
    ]

    result = await compactor.apply_compaction_plan(
        compactor.plan_preflight_compaction(messages),
        messages,
        current_input_context=ctx,
    )

    runtime_content = messages[1].content or ""
    assert runtime_content.count("<current_instruction>") == 1
    assert "Use current file." in runtime_content
    assert "wrapped" not in runtime_content
    assert result.checkpoint_covered_until_event_id == 7
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_compaction.py \
  -q
```

Expected: constructor signature and marker assertions fail.

- [ ] **Step 3: Update `ContextCompactor.__init__` signature**

In `matmaster/context/compaction.py`, replace old imports:

```python
from matmaster.core.context_builder import ContextBuilder
from matmaster.manifests.rehydrator import CompactionRehydrator
from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)
```

with:

```python
from matmaster.context.assembly import (
    CompactionAssemblyRequest,
    ContextAssembler,
    ContextAssemblyIntent,
)
from matmaster.context.sections import ContextView
from matmaster.context.ports import UserInstructions
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from matmaster.types.current_input import CurrentInputContext
```

Replace constructor parameters:

```python
        *,
        context_assembler: ContextAssembler,
        user_instructions: UserInstructions,
        session_id: str,
        spawn_id: str | None,
        runtime_covered_until_provider: Callable[[], int | None] | None = None,
        event_sink: Callable[[Any], Awaitable[None]] | None = None,
        compaction_scope: str = "root",
```

Store them:

```python
        self._context_assembler = context_assembler
        self._user_instructions = user_instructions
        self._session_id = session_id
        self._spawn_id = spawn_id
        self._runtime_covered_until_provider = runtime_covered_until_provider
```

- [ ] **Step 4: Add helper to convert `CurrentInputContext` to `TurnInput`**

Add private method:

```python
    @staticmethod
    def _turn_input_from_current_context(
        current_input_context: CurrentInputContext,
    ) -> TurnInput:
        boundary = current_input_context.pre_query_scope_event_id
        if boundary is None:
            boundary = 0
        return TurnInput(
            instruction=TurnInstructionSource(
                user_text=current_input_context.user_text,
            ),
            attachments=TurnAttachmentsSource(
                files=tuple(current_input_context.files),
                images=tuple(current_input_context.images),
                workspace_paths=tuple(current_input_context.workspace_paths),
            ),
            pre_turn_history_event_id=int(boundary),
        )
```

- [ ] **Step 5: Refactor summary success path to call assembler**

Inside `apply_compaction_plan`, keep summary/fallback selection, but replace `rehydrated = ...` and `build_compact_bundle(...)` blocks with:

```python
            turn_input = (
                self._turn_input_from_current_context(current_input_context)
                if current_split and current_input_context is not None
                else None
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
                    turn_input=turn_input,
                    covered_until_event_id=covered_until_event_id,
                ),
            )
            runtime_user_msg = assembly.user_turn_context.to_message(ContextView.RUNTIME)
            checkpoint_user_msg = assembly.user_turn_context.to_message(
                ContextView.CHECKPOINT
            )
            messages[:] = [system_msg, runtime_user_msg]
            checkpoint_covered_until_event_id = assembly.covered_until_event_id
            user_instructions_text = assembly.user_instructions_text
            user_instructions_hash = assembly.user_instructions_hash
```

Ensure `base_snapshot` is only produced when `durability == "durable"` and `checkpoint_user_msg is not None`.
The `runtime_current_event_boundary_missing` exception is intentional: the existing runtime `except` branch must catch it and use `sliding_window` / `tool_truncation` fallback. Do not pass `covered_until_event_id=0` to assembler for runtime compaction; that would replace the live messages with a checkpoint-shaped user message built from empty session sections.

Also update `SUMMARY_SYSTEM_PROMPT` in `matmaster/context/compaction.py` so it no longer references `<rehydrated_context>` or "new rehydrated context". The prompt should describe the new v1 shape: previous compact context may appear as `<compacted_history>`, and current session state is supplied by session sections such as `<session_attachments>`, `<session_skills>`, and `<session_tools>`.

- [ ] **Step 6: Preserve preflight boundary missing behavior**

After deriving `turn_input`, if `current_split` and the original `current_input_context.pre_query_scope_event_id is None`, keep the compaction runtime message but mark result ephemeral:

```python
            if (
                current_split
                and current_input_context is not None
                and current_input_context.pre_query_scope_event_id is None
            ):
                durability = "ephemeral"
                failure_reason = "preflight_current_input_boundary_missing"
```

This preserves the existing safety rule: do not write a durable checkpoint when the summary covers old history but the durable boundary is unknown.

- [ ] **Step 7: Verify `CompactionResult` v1 metadata fields**

Task 4 / Task 5 already introduced these fields and moved them into `matmaster/context/compaction.py`. In this step, only verify the moved `CompactionResult` still has:

```python
    user_instructions_text: str = ""
    user_instructions_hash: str = (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
```

Do not rename `base_snapshot` in Phase 3; tests and kernel checkpoint wiring still use it. Record the naming cleanup for Phase 4 if desired.

- [ ] **Step 8: Emit v1 checkpoint payload in `agent_compaction`**

After `ContextCompactor` produces `<compacted_history>` via `ContextAssembler`, update `matmaster/core/agent_compaction.py` payload construction:

```python
            payload = {
                "durability": result.durability,
                "strategy": result.strategy,
                "schema_version": "history_checkpoint.v1",
                "render_version": "user_context_render.v1",
                "user_instructions_text": result.user_instructions_text,
                "user_instructions_hash": result.user_instructions_hash,
            }
            if result.checkpoint_covered_until_event_id is not None:
                payload["covered_until_event_id"] = (
                    result.checkpoint_covered_until_event_id
                )
```

In `tests/matmaster/core/test_agent_kernel_compaction.py`, update durable compaction test doubles to return:

```python
            base_snapshot=[
                {
                    "role": "user",
                    "content": "<compacted_history>\nsummary\n</compacted_history>",
                    "images": [],
                }
            ],
            checkpoint_covered_until_event_id=41,
            user_instructions_text="Use SI units.",
            user_instructions_hash="sha256:abc",
```

If a test double currently builds `base_snapshot` through `ContextBuilder().build_compact_bundle(...)`, replace that test-double bundle with the explicit v1 `<compacted_history>` message above. Kernel checkpoint tests should not keep a v0 base message while expecting a v1 payload.

Update the expected checkpoint sink payload:

```python
                "payload": {
                    "durability": "durable",
                    "strategy": "summary",
                    "schema_version": "history_checkpoint.v1",
                    "render_version": "user_context_render.v1",
                    "user_instructions_text": "Use SI units.",
                    "user_instructions_hash": "sha256:abc",
                    "covered_until_event_id": 41,
                },
```

This step deliberately lives after the compactor marker switch. A v1 payload must never wrap a v0 `<previous_session_summary>` base message.

- [ ] **Step 9: Update old compactor tests from v0 marker to v1 marker**

In compaction tests, replace assertions:

```python
assert "<previous_session_summary>" in content
assert "<rehydrated_context>" in content
```

with:

```python
assert "<compacted_history>" in content
assert "<previous_session_summary>" not in content
```

Where tests previously asserted old rehydrated XML strings, assert the new section tags produced by `SessionContextBuilder`, such as `<session_attachments>`, `<session_skills>`, or `<session_tools>`.

- [ ] **Step 10: Verify focused compaction tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_compaction.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  -q
```

Expected: all pass with v1 marker.

- [ ] **Step 11: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_compaction.py \
  matmaster/core/agent_compaction.py tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/devshell/test_compaction_via_devshell.py
git commit -m "feat: route compaction through context assembler"
```

---

## Task 7: Wire Exp Runtime To The New Compactor

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/core/agent.py`
- Modify Test: `tests/matmaster/core/test_exp_runtime_v2.py`
- Modify Test: `tests/matmaster/core/test_agent_kernel_compaction.py`
- Modify Test: `tests/matmaster/integration/test_history_checkpoint_recovery.py`

**Spec 依据:** DESIGN.md §10、§14 Phase 3a、PHASE-2C-PLAN.md「Notes For Phase 3」#2/#4。

- [ ] **Step 1: Add failing Exp runtime test for assembler injection**

In `tests/matmaster/core/test_exp_runtime_v2.py`, update the `RuntimeHistory` test double with the Phase 3 history-port surface:

```python
    def query_context_events(self, **kwargs):
        self.context_query_kwargs = kwargs
        return []
```

Then replace the old `_rehydrator` assertions with:

```python
    assert runtime.spec.context_assembler is runtime.spec.compactor._context_assembler
    assert runtime.spec.session_events_port is not None
    assert runtime.spec.session_jobs_port is not None
    assert runtime.spec.compactor._runtime_covered_until_provider() == 25
```

Expected red until `Exp.build_runtime()` constructs the new compactor.

- [ ] **Step 2: Add runtime-history events port helper**

`Exp.build_runtime()` does not own `events_table`; it only sees the narrow
`SessionEventHistoryPort` from runtime ports. Do not pass `events_table` through
`run_meta`. Instead, extend `src/services/context_assembly_factory.py` with an
adapter that turns the runtime history port's `query_context_events(...)` method
into a `SessionEventsPort`. Do **not** build this adapter from
`history_port.all_events()`: that method uses display/history rows from
`get_session_events(...)`, which may be limited and may flatten `User/query`
payloads. Runtime context assembly needs raw context rows with `id` and raw JSON
payload shape.

Add this test to `tests/services/test_context_assembly_factory.py`:

```python
@pytest.mark.asyncio
async def test_runtime_history_events_port_filters_existing_history_rows() -> None:
    from matmaster.context.ports import SessionEventQuery
    from src.services.context_assembly_factory import RuntimeHistorySessionEventsPort

    class History:
        def query_context_events(self, **kwargs):
            self.kwargs = kwargs
            return [
                {"id": 1, "type": "query", "source": "User", "content": {"content": "old"}},
            ]

        def all_events(self):
            raise AssertionError("runtime context assembly must not use all_events()")

        def query_events(self):
            return []

        def latest_checkpoint_covered_until_event_id(self):
            return None

        def latest_scope_event_id(self):
            return 3

    history = History()
    events = await RuntimeHistorySessionEventsPort(history).load_events(
        SessionEventQuery(
            session_id="sess-1",
            spawn_id=None,
            until_event_id=2,
            event_types=("query",),
            order="asc",
        )
    )

    assert [event.id for event in events] == [1]
    assert events[0].content == {"content": "old"}
    assert history.kwargs == {
        "spawn_id": None,
        "until_event_id": 2,
        "event_types": ("query",),
        "limit": None,
        "order": "asc",
    }
```

Implement the adapter in `src/services/context_assembly_factory.py`:

```python
from matmaster.context.ports import SessionEventQuery
from matmaster.context.scanner import coerce_session_events


class RuntimeHistorySessionEventsPort:
    def __init__(self, history_port: Any) -> None:
        self._history_port = history_port

    async def load_events(self, query: SessionEventQuery):
        rows = self._history_port.query_context_events(
            spawn_id=query.spawn_id,
            until_event_id=query.until_event_id,
            event_types=query.event_types,
            limit=query.limit,
            order=query.order,
        )
        return coerce_session_events(rows)
```

This adapter is only for runtime compactor assembly. Service Stage 5b still uses
`AppSessionEventsPort(events_table)` because it has direct DAO access.

- [ ] **Step 3: Stop constructing `CompactionRehydrator` in `Exp.build_runtime()`**

In `matmaster/core/exp.py`, replace:

```python
from matmaster.core.context_compactor import ContextCompactor
from matmaster.manifests.rehydrator import CompactionRehydrator
...
rehydrator = CompactionRehydrator(...)
compactor = ContextCompactor(
    config=spec.compaction,
    summary_provider=summary_provider,
    rehydrator=rehydrator,
    context_builder=builder,
    event_sink=None,
    compaction_scope=...,
)
```

with:

```python
from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
from matmaster.context.compaction import ContextCompactor
from matmaster.context.ports import ContextAssemblyPorts, UserInstructions
from src.services.context_assembly_factory import (
    RuntimeHistorySessionEventsPort,
    build_session_context_factory,
)
from src.services.context_assembly_ports import AppSessionJobsPort

...
instructions_text = str(run_meta.get("user_instructions") or "")
instructions_hash = run_meta.get("user_instructions_hash")
if not isinstance(instructions_hash, str) or not instructions_hash:
    from src.services.user_turn_context_service import hash_user_instructions

    instructions_hash = hash_user_instructions(instructions_text)
user_instructions = UserInstructions(
    text=instructions_text,
    hash=instructions_hash,
    truncated=bool(run_meta.get("user_instructions_truncated", False)),
)
assembly_ports = ContextAssemblyPorts(
    session_events=RuntimeHistorySessionEventsPort(history_port),
    session_jobs=AppSessionJobsPort(),
)
context_assembler = ContextAssembler(
    ports=assembly_ports,
    session_context_factory=build_session_context_factory(
        skill_registry=self._skill_registry,
        legal_mcp_servers=run_meta.get("legal_mcp_servers"),
        schemas_by_server=run_meta.get("schemas_by_server"),
    ),
    render_options=ContextRenderOptions(
        split_turn_attachments=bool(
            run_meta.get("split_turn_attachments", False)
        ),
    ),
)
```

- [ ] **Step 4: Inject assembler and ports into `AgentRuntimeSpec`**

When creating `spec = spec.model_copy(update={...})`, include:

```python
                "context_assembler": context_assembler,
                "session_events_port": assembly_ports.session_events,
                "session_jobs_port": assembly_ports.session_jobs,
```

`user_instructions_port` can stay `None` in Phase 3 runtime; service has already read a stable bundle for this turn.

- [ ] **Step 5: Fix preflight double wrap in `AgentKernel._run_items()`**

First add a kernel-level regression assertion in `tests/matmaster/core/test_agent_kernel_compaction.py`, so the fix is locked at the caller boundary and not only in `ContextCompactor` unit tests:

```python
@pytest.mark.asyncio
async def test_kernel_passes_raw_current_input_context_to_preflight_compactor() -> None:
    from matmaster.core.agent import AgentKernel

    seen_contexts = []

    class CapturingCompactor(_DurablePreflightCompactor):
        async def apply_compaction_plan(
            self,
            plan,
            messages,
            *,
            current_input_context=None,
        ):
            seen_contexts.append(current_input_context)
            return await super().apply_compaction_plan(
                plan,
                messages,
                current_input_context=current_input_context,
            )

    raw_ctx = CurrentInputContext.from_values(
        user_text="Use current file.",
        files=["https://oss/current.cif"],
        pre_query_scope_event_id=41,
    )
    spec = _make_spec(provider=ContentOnlyProvider()).model_copy(
        update={
            "compactor": CapturingCompactor(),
            "runtime_ports": KernelRuntimePorts(checkpoint_sink=lambda **kwargs: None),
            "meta": {
                "current_input_context": raw_ctx.to_payload(),
            }
        }
    )

    async for _event in AgentKernel().run_stream(
        spec,
        "<user_instructions>wrapped</user_instructions>\n\n"
        "<current_instruction>Use current file.</current_instruction>",
        history=[
            UserMessage(content="old question"),
            AssistantMessage(content="old answer"),
        ],
    ):
        pass

    assert seen_contexts
    assert seen_contexts[0].user_text == "Use current file."
    assert "user_instructions" not in seen_contexts[0].user_text
```

The assertion target is fixed: the compactor must receive the raw `CurrentInputContext.user_text`, not the provider-facing `task` string.

In `matmaster/core/agent.py`, replace:

```python
        effective_current_input_context = (
            replace(current_input_context, user_text=task)
            if current_input_context is not None
            else None
        )
```

with:

```python
        effective_current_input_context = current_input_context
```

Then remove unused `replace` import from `dataclasses`.

This is Phase 3's explicit fix for FOLLOWUPS.md 议题 3. The current LLM call still uses `UserMessage(content=task, images=...)`; only compaction's view of raw current input changes.

- [ ] **Step 6: Update test double result fields**

Any test compactor constructing `CompactionResult(...)` must add:

```python
user_instructions_text="Use SI units.",
user_instructions_hash="sha256:abc",
```

For tests where exact hash is irrelevant, use the empty-text hash constant from Task 4.

- [ ] **Step 7: Verify runtime tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/services/test_context_assembly_factory.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  -q
```

Expected: all pass, no `CompactionRehydrator` construction in `Exp.build_runtime()`.

- [ ] **Step 8: Commit**

```bash
git add src/services/context_assembly_factory.py matmaster/core/exp.py matmaster/core/agent.py \
  tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py tests/services/test_context_assembly_factory.py
git commit -m "feat: wire exp compaction through assembler"
```

---

## Task 8: Move ContextBuilder To System Prompt Shim

**Files:**
- Create: `matmaster/context/system_prompt.py`
- Modify: `matmaster/core/context_builder.py`
- Create/Modify Test: `tests/matmaster/context/test_system_prompt.py`
- Modify Test: `tests/matmaster/core/test_context_builder.py`

**Spec 依据:** DESIGN.md §5.3、§13、§15、附录 B「Phase 3 改动」。

- [ ] **Step 1: Copy existing `ContextBuilder` implementation**

Create `matmaster/context/system_prompt.py` containing the current `ContextBuilder` class from `matmaster/core/context_builder.py`. Keep method names unchanged:

- `build_system_prompt(...)`
- `build_user_request(...)`
- `build_compact_bundle(...)`

Although compactor no longer calls `build_compact_bundle(...)`, keeping it in the moved class preserves legacy tests and old imports until Phase 4.

- [ ] **Step 2: Replace core file with shim**

Replace `matmaster/core/context_builder.py` with:

```python
"""Compatibility shim for the Phase 3 system prompt move.

The real implementation lives in `matmaster.context.system_prompt`.
This shim stays until Phase 4 renames AgentRuntimeSpec.context_builder to
system_prompt_builder and removes legacy import paths.
"""

from matmaster.context.system_prompt import ContextBuilder  # noqa: F401
```

- [ ] **Step 3: Add shim equivalence test**

Create `tests/matmaster/context/test_system_prompt.py`:

```python
from __future__ import annotations


def test_core_context_builder_shim_reexports_context_implementation() -> None:
    from matmaster.context.system_prompt import ContextBuilder as NewContextBuilder
    from matmaster.core.context_builder import ContextBuilder as ShimContextBuilder

    assert ShimContextBuilder is NewContextBuilder
```

- [ ] **Step 4: Verify tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_system_prompt.py \
  tests/matmaster/core/test_context_builder.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/system_prompt.py matmaster/core/context_builder.py \
  tests/matmaster/context/test_system_prompt.py tests/matmaster/core/test_context_builder.py
git commit -m "refactor: move context builder to context package"
```

---

## Task 9: Prompt Shape A/B Decision Gate

**Files:**
- Modify: `src/services/context_assembly_factory.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `matmaster/core/exp.py`
- Create: `.planning/context-refactor/PHASE-3-PROMPT-AB.md`
- Modify Test: `tests/matmaster/services/test_agent_run_stream_context_cutover.py`
- Modify Test: `tests/matmaster/context/test_assembly.py`

**Spec 依据:** DESIGN.md §6.5、§14 Phase 3c、§17 风险 5。

- [ ] **Step 1: Generate offline A/B fixture output**

Run this script to print the two prompt shapes:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python - <<'PY'
from matmaster.context.assembly import (
    ContextAssembler,
    ContextRenderOptions,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import ContextAssemblyPorts, UserInstructions
from matmaster.context.sources.turn_input import TurnAttachmentsSource, TurnInput, TurnInstructionSource
from matmaster.context.sections import ContextView

class EventsPort:
    async def load_events(self, query):
        return ()

async def render(split):
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
        render_options=ContextRenderOptions(split_turn_attachments=split),
    )
    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="fixture",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Compare these structures."),
                attachments=TurnAttachmentsSource(
                    files=("https://oss.example.com/a.cif", "https://oss.example.com/b.cif"),
                    images=("https://oss.example.com/diff.png",),
                    workspace_paths=("/share/jobs/result.csv",),
                ),
                pre_turn_history_event_id=10,
            ),
            user_instructions=UserInstructions(text="", hash="sha256:empty"),
        ),
    )
    return result.user_turn_context.render(ContextView.RUNTIME)

import asyncio
print("=== A merged current_instruction ===")
print(asyncio.run(render(False)))
print("=== B split turn_attachments ===")
print(asyncio.run(render(True)))
PY
```

Expected:

```text
A contains one <current_instruction> with [Current attachments]
B contains <current_instruction> and separate <turn_attachments>
```

- [ ] **Step 2: Write A/B result document**

Create `.planning/context-refactor/PHASE-3-PROMPT-AB.md` with:

```markdown
# Phase 3 Prompt Shape A/B

**Date:** YYYY-MM-DD
**Decision:** keep-merged or enable-split

## Fixtures

- Single current file
- Two current files
- Current image
- Workspace path
- Mixed old session attachments plus current turn attachments

## Evaluation Criteria

- Correctly identifies current-turn attachments as current material.
- Does not treat old session attachments as current task input.
- Preserves image payload in `UserMessage.images`.
- Tool selection remains stable for file-analysis tasks.
- No nested or duplicated `<current_instruction>` tags.

## Result

`keep-merged` or `enable-split`

## Rationale

Concrete observations from the fixture outputs and any manual/automated eval run.
```

Replace `YYYY-MM-DD` with the actual date of execution. The decision string must be exactly `keep-merged` or `enable-split`.

- [ ] **Step 3: Implement production flag**

In `src/services/context_assembly_factory.py`, keep `split_turn_attachments` default `False`.

In `src/services/agent_run_service.py` and `matmaster/core/exp.py`, read:

```python
split_turn_attachments=bool(run_meta.get("split_turn_attachments", False))
```

No environment-variable fallback in Phase 3; runtime prompt shape is explicit metadata/config driven, not ambient process state.

- [ ] **Step 4: Apply decision**

Phase 3 default decision is `keep-merged`. Only set `Decision: enable-split` when offline fixture review or LLM eval data shows the split shape preserves current-turn attachment behavior and tool selection. Do not switch to `enable-split` merely because the code path exists or because it makes one assertion easier.

If `.planning/context-refactor/PHASE-3-PROMPT-AB.md` says `Decision: enable-split`, set the service-side run metadata in `AgentRunService.run_agent`:

```python
pg_ctx = pg_ctx.with_run_meta(split_turn_attachments=True)
```

If it says `Decision: keep-merged`, do not add that line. In both cases, leave `ContextRenderOptions` support in place for future controlled experiments.
`with_run_meta(...)` merges named keys with existing run metadata. If implementation inspection shows replace semantics instead, preserve existing `user_instructions`, `user_instructions_hash`, and `user_instructions_truncated` keys explicitly in the same update.

- [ ] **Step 5: Add regression assertion for chosen default**

If `keep-merged`, keep existing Phase 2C test:

```python
assert "<turn_attachments>" not in runtime.content
```

If `enable-split`, update the assertion in `tests/matmaster/services/test_agent_run_stream_context_cutover.py`:

```python
assert "<turn_attachments>" in runtime.content
assert "[Current attachments]" not in runtime.content
```

- [ ] **Step 6: Verify focused tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_assembly.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  tests/matmaster/context/test_compaction.py \
  -q
```

Expected: all pass with the documented decision.

- [ ] **Step 7: Commit**

```bash
git add src/services/context_assembly_factory.py src/services/agent_run_service.py matmaster/core/exp.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py tests/matmaster/context/test_assembly.py \
  .planning/context-refactor/PHASE-3-PROMPT-AB.md
git commit -m "feat: add prompt shape decision gate"
```

---

## Task 10: Roundtrip And Multi-Compaction Integration

**Files:**
- Modify: `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- Modify: `tests/matmaster/devshell/test_compaction_via_devshell.py`
- Modify: `tests/matmaster/core/test_agent_kernel_compaction.py`
- Modify: `.planning/context-refactor/FOLLOWUPS.md`

**Spec 依据:** DESIGN.md §11.4、§14 Phase 3d、§16 integration tests、§17 风险 2/4。

- [ ] **Step 1: Add v1 roundtrip assertion**

In `tests/matmaster/integration/test_history_checkpoint_recovery.py`, update the compactor roundtrip test to assert:

```python
    checkpoint = events_table.history_checkpoints(spawn_id=None)[0]
    content = checkpoint["content"]
    assert content["schema_version"] == "history_checkpoint.v1"
    assert content["render_version"] == "user_context_render.v1"
    assert content["user_instructions_text"] == "Use SI units."
    assert content["user_instructions_hash"] == "sha256:abc"
    assert "<compacted_history>" in content["base_messages"][0]["content"]
    assert "<previous_session_summary>" not in content["base_messages"][0]["content"]
```

- [ ] **Step 2: Add two-compaction chain assertion**

Add a test where:

1. First durable compaction writes `history_checkpoint.v1` with `<compacted_history>first summary</compacted_history>`.
2. Later assistant/user events are added.
3. Second compaction summary input includes the previous compact message as part of messages.
4. Latest checkpoint restore returns only latest `base_messages` plus tail events.

Core assertions:

```python
assert len(events_table.history_checkpoints(spawn_id=None)) == 2
latest = events_table.history_checkpoints(spawn_id=None)[0]["content"]
assert "second summary" in latest["base_messages"][0]["content"]
assert "first summary" in provider.calls[0][1]["content"]
```

- [ ] **Step 3: Add fallback telemetry assertion**

In `tests/matmaster/devshell/test_compaction_via_devshell.py`, update fallback tests to assert:

```python
assert result.strategy in {"sliding_window", "tool_truncation"}
assert result.durability == "ephemeral"
assert result.failure_reason
assert result.base_snapshot is None
```

Also assert the complete `CompactionEvent` carries the same strategy/durability in `tests/matmaster/core/test_agent_kernel_compaction.py`.

- [ ] **Step 4: Resolve FOLLOWUPS issue 3**

In `.planning/context-refactor/FOLLOWUPS.md`, update 议题 3 with a Resolution section:

```markdown
**Resolution (Phase 3)**

Phase 3 moved compaction to `ContextAssembler.assemble_compaction(...)` and stopped
overwriting `CurrentInputContext.user_text` with the provider-facing `task` in
`AgentKernel._run_items`. Preflight compaction now renders exactly one
`<current_instruction>` block from raw current input.
```

Do not delete the historical description; it is useful context.

- [ ] **Step 5: Verify integration suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_restore_service.py \
  -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  .planning/context-refactor/FOLLOWUPS.md
git commit -m "test: cover v1 compaction roundtrip"
```

---

## Task 11: Final Static Checks And Acceptance

**Files:** read-only, except follow-up notes if a new documented risk is found

**Spec 依据:** DESIGN.md §4、§14 Phase 3d、§16、§17。

- [ ] **Step 1: Static check — compactor no longer uses legacy builders**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "CompactionRehydrator|build_compact_bundle|build_current_instruction_block|previous_session_summary|rehydrated_context" \
  matmaster/context/compaction.py matmaster/core/exp.py matmaster/core/agent.py
```

Expected: empty output.

- [ ] **Step 2: Static check — legacy shims only**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && sed -n '1,120p' matmaster/core/context_compactor.py && sed -n '1,80p' matmaster/core/context_builder.py
```

Expected:

```text
context_compactor.py only imports/re-exports from matmaster.context.compaction
context_builder.py only imports/re-exports from matmaster.context.system_prompt
```

- [ ] **Step 3: Static check — v1 checkpoint writes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n \
  "history_checkpoint.v1|user_context_render.v1|user_instructions_text|user_instructions_hash|covered_until_event_id" \
  matmaster/core/agent_compaction.py src/services/history_checkpoint_service.py matmaster/context/compaction.py tests
```

Expected:

- `agent_compaction.py` includes all v1 payload fields.
- `history_checkpoint_service.py` forwards all v1 payload fields.
- `context/compaction.py` fills `CompactionResult.user_instructions_text/hash`.
- Tests assert `<compacted_history>` marker.

- [ ] **Step 4: Static check — v0 compat marker still retained**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "COMPAT:v0-checkpoint-marker|MARKERS_V0|<previous_session_summary>" src/services/history_checkpoint_codec.py tests
```

Expected: marker compatibility remains in codec tests. `<previous_session_summary>` may appear in codec compatibility tests and legacy shim tests only; it must not appear in new compaction output assertions.

- [ ] **Step 5: Static check — prompt shape decision documented**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "Decision: (keep-merged|enable-split)" .planning/context-refactor/PHASE-3-PROMPT-AB.md
```

Expected: exactly one match.

- [ ] **Step 6: Focused regression suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/core/test_context_builder.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/core/test_exp_runtime_v2.py \
  tests/matmaster/devshell/test_compaction_via_devshell.py \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_agent_run_stream_context_cutover.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/services/test_context_assembly_factory.py \
  tests/test_chat_events_history_checkpoint.py \
  -q
```

Expected: all pass.

- [ ] **Step 7: Acceptance checklist**

Confirm each item manually:

- [ ] `matmaster/context/compaction.py` is the only real `ContextCompactor` implementation.
- [ ] `matmaster/core/context_compactor.py` is a shim.
- [ ] `matmaster/core/context_builder.py` is a shim.
- [ ] `Exp.build_runtime()` no longer constructs `CompactionRehydrator`.
- [ ] Durable compaction checkpoint payload contains `schema_version="history_checkpoint.v1"`.
- [ ] Durable compaction checkpoint base message contains `<compacted_history>`.
- [ ] Runtime compaction passes an explicit high-water event boundary into assembler.
- [ ] Preflight compaction with current input no longer creates nested `<current_instruction>`.
- [ ] Runtime fallback remains available and yields ephemeral result.
- [ ] Prompt shape A/B decision is recorded in `.planning/context-refactor/PHASE-3-PROMPT-AB.md`.
- [ ] `matmaster/manifests/*` still exists and is not edited by Phase 3.
- [ ] `COMPAT:v0-restore` and `COMPAT:v0-checkpoint-marker` remain for Phase 4.
- [ ] `AgentRuntimeSpec.context_builder` still uses the old field name; rename to `system_prompt_builder` is Phase 4 work.

This Task has no commit unless a new follow-up is added to `FOLLOWUPS.md`.

---

## Notes For Phase 4

Phase 3 leaves these deliberate compatibility pieces for Phase 4:

1. Delete shim paths: `matmaster/manifests/*`, `matmaster/core/context_builder.py`, `matmaster/core/context_compactor.py`, `matmaster/types/context.py`, `matmaster/types/current_input.py`.
2. Rename `AgentRuntimeSpec.context_builder` to `system_prompt_builder`.
3. Rename or retire `CompactionResult.base_snapshot`; Phase 3 keeps it to avoid wide kernel/test churn.
4. Retire `COMPAT:v0-checkpoint-marker` after the observation window; codec should then require `<compacted_history>`.
5. Retire `COMPAT:v0-restore` after product confirms old sessions no longer need restore.
6. Decide whether fallback strategies should be removed using Phase 3 compaction lifecycle data.
7. Before deciding fallback removal, confirm `CompactionEvent.strategy` / `durability` / `failure_reason` are actually aggregated into a queryable statistics source (events table query or log aggregation). Phase 3 only emits distinguishable lifecycle data; Phase 4 owns the 30-day measurement and deletion decision.

---

## Self-Review Checklist

- [ ] DESIGN.md §14 Phase 3a is covered by Tasks 5-7.
- [ ] DESIGN.md §14 Phase 3b is covered by Tasks 4, 6, 10, 11.
- [ ] DESIGN.md §14 Phase 3c is covered by Tasks 2, 3, 9.
- [ ] DESIGN.md §14 Phase 3d is covered by Tasks 6, 10, 11.
- [ ] FOLLOWUPS.md double `<current_instruction>` issue is covered by Tasks 6, 7, 10.
- [ ] No Task emits `history_checkpoint.v1` before compactor output contains `<compacted_history>`.
- [ ] Runtime compactor assembly reads raw context rows via `query_context_events(...)`, not display-flattened `all_events()`.
- [ ] No Task asks implementers to edit sibling repositories.
- [ ] No Task uses system Python.
- [ ] No Task deletes Phase 4-owned compatibility shims.
