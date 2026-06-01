# Run Meta Refactor: Typed Ports + Typed Metadata Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 [PlaygroundContext.run_meta](matmaster/core/playground.py:69) 这个 `dict[str, Any]` 中转袋彻底拆掉。能力对象（callable / sink / config-with-callable）迁到 [PlaygroundRuntimePorts](matmaster/types/runtime_ports.py:97)，被动元数据收敛成 typed `RunMetadata` BaseModel。同时清理 [_build_kernel_meta](matmaster/core/exp.py:317) 出口的 `AgentRuntimeSpec.meta` dict-bag。结束后核心路径不再有任何 `(ctx.run_meta or {}).get(...)` 形式的防御性 dict 访问。

**Architecture:** 仓库已有一个隐式契约 —— callback / sink / barrier 走 `PlaygroundRuntimePorts`（典型例子是 `child_event_forward_sink`、`checkpoint_sink_factory`、`pre_compaction_barrier`），且有 [test_run_agent_does_not_store_callback_ports_in_run_meta](tests/matmaster/services/test_agent_run_stream.py:392) 防止回流。本计划把这条契约扩展为 `port vs metadata` 二分原则：

- 含 callable / 表征运行时服务能力的对象 → `PlaygroundRuntimePorts` 子字段
- 描述本次 run 的被动数据（id、token、配置快照、文本 / hash / 集合）→ `RunMetadata` 字段
- 任何东西都不应该再以 `Any` 形式塞进 `run_meta` dict

**Tech Stack:** Python 3.11+ via `uv run`, Pydantic v2 BaseModel + `model_config(frozen=True)`, dataclasses for ports, pytest. 不引入新依赖。

**Prerequisite:** R6 (active skills boundary) 已 merge。本 plan 假定 `SkillResolver` 已通过显式参数链路传递，不再依赖 `run_meta`。

---

## Scope And Non-Negotiables

- 本计划只处理 `run_meta` 与 `AgentRuntimeSpec.meta` 两个 dict-bag。其他 `meta` 字段（[ToolResult.meta](matmaster/tools/tool_result.py:21)、[LazyMCPTool.meta](matmaster/tools/lazy_mcp.py:114)、[ExpSubagentMeta](matmaster/config/exp.py:50)、`SkillMetaInfo`、provider metadata 等）**不在 scope**。它们要么已是 typed model，要么属于工具协议扩展点，性质不同。
- 三个 phase 按 P0 → P1 → P2 顺序，每个 phase 独立 PR。P0 必须先合，P2 严禁与 P0 / P1 合在一个 PR。
- 任何阶段结束时，新写的字段名都**不得通过 `run_meta` 流通**。boundary 测试用 `assert <field> not in pg_ctx.run_meta` 形式固化契约。
- 删除幽灵读取项（`skill_config`、`legal_mcp_servers`、`schemas_by_server`、`split_turn_attachments`）前必须先全仓 grep 确认无第三方写入。
- `PlaygroundContext` 保持 `frozen=True`。新增字段一律走 `model_copy(update=...)` 或新的 `with_*` 方法。
- 不动 [evaluation/core/mat_runner.py](evaluation/core/mat_runner.py:172) 与 [matmaster/devshell/runner.py](matmaster/devshell/runner.py:66) 的 `run_meta={"source": ..., ...}` 构造形式，仅在 P2 把它们改成构造 `RunMetadata`。devshell / evaluation 与生产路径必须共用同一份 `RunMetadata` 类型。
- 测试改动量大，每个 phase 必须先跑 `tests/matmaster/core/test_playground_context.py`、`tests/matmaster/services/test_agent_run_stream.py`、`tests/matmaster/core/test_exp_runtime_v2.py` 三个核心套件全绿，再扩散到 integration / devshell。

## Architecture Decisions Locked

| 决策 | 选择 | 理由 |
|---|---|---|
| `figure_upload_config` 归属 | `PlaygroundRuntimePorts.figure_upload: FigureUploadPort` | [FigureUploadConfig.upload_bytes](matmaster/types/figures.py:47) 是 `Callable`，属能力对象，与既有 sink/barrier 同类 |
| `bohrium` 归属 | `PlaygroundRuntimePorts.bohrium: BohriumRuntimePort(snapshot: BohriumRuntimeSnapshot \| None)`（P2） | [path_access.py:52](matmaster/core/path_access.py:52) 用它做运行时路径权限派生，是能力侧而非被动 metadata。**`BohriumRuntimeSnapshot` 是窄 typed model，仅含 `remote_project_root`/`remote_workspace_root`/`ssh_attached`/`node_id` 等消费者真实用到的字段**；禁止用 `dict[str, Any]` 兜底（违反 [AGENTS.md:87](AGENTS.md:87)） |
| `session_id` 归属 | `PlaygroundContext.session_id: str = ""` 顶层显式字段，且 `Playground.prepare(*, session_id, run_dir, task_id, ...)` 改为显式关键字参数签名。P1 只剥离 `session_id`；`run_dir` / `task_id` 在 P2 引入 `RunMetadata` 前仍暂存于 `run_meta` | 身份维度，与 `workdir`/`session_type` 同级；**不允许通过 `run_meta` dict 流通**，boundary test 必须断言 `"session_id" not in pg_ctx.run_meta`。`run_dir` / `task_id` 是 P2 前的临时 metadata，不属于本行约束 |
| `current_user_images` 归属 | 并入 `TurnInput.images`，agent 改读 `turn_input.attachments.images_as_parts()`（P1 先从 `spec.meta["turn_input"]` 取，P2 再从 `spec.turn_input` 取），最后删除 [exp.py:586-595](matmaster/core/exp.py:586) 的 hack 分支。**强制顺序：先写 e2e 集成测试覆盖图片消费链路 → 迁移 → 删除 hack**，禁止反序 | 现状不是死代码：[exp.py:586](matmaster/core/exp.py:586) 是绕过 `_build_kernel_meta` 的特例分支，把 `run_meta["current_user_images"]` 塞回 `spec.meta`，被 [agent.py:240-243](matmaster/core/agent.py:240) 消费；[test_current_user_images_are_sent_as_content_parts](tests/matmaster/core/test_agent_kernel_stream.py:487) 固化此语义。误删会回归图像输入 |
| ports 写入策略 | 任何 port 字段更新必须走 `dataclasses.replace(ctx.runtime_ports, <field>=...)`，并通过 `PlaygroundContext.with_runtime_port(<field>=...)` helper 暴露。**禁止用全新 `PlaygroundRuntimePorts(...)` 构造替换**；`build_history_wiring()` 改为接收 base ports 并返回合并后的实例 | [agent_run_history_wiring.py:186-193](src/services/agent_run_history_wiring.py:186) 当前 `PlaygroundRuntimePorts(...)` 只填 `child_event_forward_sink` + `compaction`，service 层 [agent_run_service.py:515](src/services/agent_run_service.py:515) 的 `with_runtime_ports(wiring.runtime_ports)` 会**覆盖**之前 P0 注入的 `figure_upload` 与 P2 注入的 `bohrium` |
| `RunMetadata` 形态 | Pydantic `BaseModel(frozen=True, extra="forbid")`，与 `PlaygroundContext` 同风格 | 复用既有 `model_copy(update=...)` 模式；`extra="forbid"` 防止幽灵字段卷土重来 |
| `user_instructions` 在 `RunMetadata` 里 | `user_instructions: UserInstructions \| None`，service 直接 `with_metadata(user_instructions=instructions_bundle)`，**runtime assembly 改读 `ctx.metadata.user_instructions`，禁止重算 hash** | service 已在 [agent_run_service.py:521-526](src/services/agent_run_service.py:521) 构造 `instructions_bundle`，当前却把它拆成 3 个字段塞 run_meta，[runtime_context_assembly.py:85-92](matmaster/core/runtime_context_assembly.py:85) 再重组。boundary test 断言 `ctx.metadata.user_instructions.hash == instructions_bundle.hash`（无 rehash），并断言 `truncated` 标记一路透传到 compactor payload |
| `AgentRuntimeSpec.meta` 处置 | 拆成 `run_identity: RunIdentity` + `turn_input: TurnInput \| None` 两个字段，删除 dict。**`RunIdentity` 唯一定义点在 `matmaster/types/run_metadata.py`**，`AgentRuntimeSpec` import 复用 | 同样的 dict-bag 问题在 spec 层重演；与 ctx 改造同套路 |
| 兼容入口 | **不保留任何 dict 兼容路径**。`Playground.prepare()` P1 改显式关键字签名，P2 改为强制接收 `RunMetadata` 实例 | 全仓 caller 数量可控（service + devshell + evaluation + 测试 fixture），一次改完比留 dict 兼容入口更安全；短期兼容入口会导致 P2 之后第三方调用方持续传 dict 永远迁不完 |
| 幽灵字段处置 | 直接删除读取与写入，不留 deprecated warning | 它们当前是恒空读取，留 deprecated 比删除更危险（让人误以为还在用） |

## File Structure

- Modify: [matmaster/types/runtime_ports.py](matmaster/types/runtime_ports.py)
  P0 新增 `FigureUploadPort`；P2 新增 `BohriumRuntimePort` + `BohriumRuntimeSnapshot`（窄 typed model），扩展 `PlaygroundRuntimePorts`。
- Modify: [matmaster/core/playground.py](matmaster/core/playground.py)
  P0 新增 `with_runtime_port(<field>=...)` helper（基于 `dataclasses.replace`），用于单字段合并；P1 加 `session_id` 顶层字段并改写 `prepare()` 签名为显式关键字参数；P2 加 `metadata: RunMetadata` 字段，删 `run_meta` 与 `with_run_meta`。`with_bohrium` 保留，但签名收紧为 typed `BohriumRuntimeSnapshot`，不再接受 dict。
- Modify: [src/services/agent_run_history_wiring.py](src/services/agent_run_history_wiring.py)
  P0 起 `build_history_wiring()` 改为接收 base `PlaygroundRuntimePorts` 并返回合并后的实例，禁止内部独立构造覆盖。
- Create: `matmaster/types/run_metadata.py`
  P2 引入 `RunMetadata`、`RunIdentity` 两个 frozen BaseModel。**`RunIdentity` 唯一定义点**，下游一律 import 复用。
- Modify: [matmaster/types/runtime.py](matmaster/types/runtime.py)
  P2 把 `AgentRuntimeSpec.meta: dict` 拆成 `run_identity: RunIdentity` + `turn_input: TurnInput | None` 两个 typed 字段。`RunIdentity` 从 `run_metadata.py` import，**禁止重新定义**。
- Modify: [matmaster/core/exp.py](matmaster/core/exp.py)
  各 phase 替换 `ctx.run_meta.get(...)` 调用点；P2 重写 `_build_kernel_meta` → `_build_run_identity`，并删除 [exp.py:586-595](matmaster/core/exp.py:586) `current_user_images` 的 hack 分支（必须在 Task 6 e2e 测试与 TurnInput.images 迁移完成后才能删）。
- Modify: [matmaster/core/runtime_context_assembly.py](matmaster/core/runtime_context_assembly.py)
  P1 删幽灵字段读取；P2 改读 `ctx.metadata.user_instructions`（禁止重算 hash）。
- Modify: [matmaster/core/agent.py](matmaster/core/agent.py)
  P1 迁移图片消费代码（[agent.py:240-243](matmaster/core/agent.py:240) 不是死代码），改读 `turn_input.attachments.images_as_parts()`；P2 改读 `spec.run_identity` / `spec.turn_input`。
- Modify: [matmaster/core/path_access.py](matmaster/core/path_access.py)
  P2 改读 `ctx.runtime_ports.bohrium.snapshot.remote_project_root` 等 typed 字段。
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
  各 phase 同步替换写入端，且必须使用 `with_runtime_port` helper，**禁止整包替换 `runtime_ports`**。
- Modify: [matmaster/devshell/runner.py](matmaster/devshell/runner.py)、[evaluation/core/mat_runner.py](evaluation/core/mat_runner.py)
  P2 改成构造 `RunMetadata`。
- Modify tests:
  - [tests/matmaster/core/test_playground_context.py](tests/matmaster/core/test_playground_context.py)
  - [tests/matmaster/services/test_agent_run_stream.py](tests/matmaster/services/test_agent_run_stream.py)
  - [tests/matmaster/services/agent_run_stream_fixtures.py](tests/matmaster/services/agent_run_stream_fixtures.py)
  - [tests/matmaster/core/test_exp_runtime_v2.py](tests/matmaster/core/test_exp_runtime_v2.py)
  - [tests/matmaster/core/test_exp_skill_replay.py](tests/matmaster/core/test_exp_skill_replay.py)
  - [tests/matmaster/core/test_hook_wiring.py](tests/matmaster/core/test_hook_wiring.py)
  - [tests/matmaster/integration/test_bohrium_execution_contract.py](tests/matmaster/integration/test_bohrium_execution_contract.py)
  - [tests/matmaster/integration/test_lazy_mcp_integration.py](tests/matmaster/integration/test_lazy_mcp_integration.py)
  - 全部用 `rg -n "run_meta=" tests` 找出的 fixture
- Create: `tests/matmaster/services/test_figure_upload_port_boundary.py`
- Create: `tests/matmaster/core/test_run_metadata_model.py`（P2）

---

# Phase P0: figure_upload_config 迁出 run_meta

**目标 PR 边界：** 此 phase 单独成 PR，diff 限定在 ports 定义、service 注入、exp 取出、boundary 测试四块。不顺手碰其他 `run_meta` 字段。

### Task 1: 引入 `FigureUploadPort`、`with_runtime_port` helper、history wiring 合并

**Files:**
- Modify: [matmaster/types/runtime_ports.py](matmaster/types/runtime_ports.py)
- Modify: [matmaster/core/playground.py](matmaster/core/playground.py)
- Modify: [src/services/agent_run_history_wiring.py](src/services/agent_run_history_wiring.py)
- Test: [tests/matmaster/types/test_runtime_ports.py](tests/matmaster/types/test_runtime_ports.py)
- Test: [tests/matmaster/core/test_playground_context.py](tests/matmaster/core/test_playground_context.py)

- [ ] **Step 1: 写失败测试（三组）**

A. `tests/matmaster/types/test_runtime_ports.py` 新增：断言 `PlaygroundRuntimePorts.figure_upload` 默认为 `FigureUploadPort(config=None)`，且 `FigureUploadPort` 是 frozen dataclass。

B. `tests/matmaster/core/test_playground_context.py` 新增 `test_with_runtime_port_merges_single_field_only`：构造 `pg_ctx` 同时含 `child_event_forward_sink` 与 `compaction.history`，调用 `pg_ctx.with_runtime_port(figure_upload=FigureUploadPort(config=cfg))`，断言返回的新实例 `.figure_upload.config is cfg` **且** `.child_event_forward_sink` 与 `.compaction.history` 都未丢失。

C. [tests/matmaster/services/test_agent_run_history_wiring.py](tests/matmaster/services/test_agent_run_history_wiring.py) 新增 `test_build_history_wiring_merges_into_existing_runtime_ports`：传入 base ports 含 `figure_upload=FigureUploadPort(config=cfg)`，调用 `build_history_wiring(..., base_runtime_ports=base)`，断言返回的 `wiring.runtime_ports.figure_upload.config is cfg`（即 history wiring 没有覆盖 figure_upload）。

- [ ] **Step 2: 验证失败**

`uv run pytest tests/matmaster/types/test_runtime_ports.py tests/matmaster/core/test_playground_context.py tests/matmaster/services/test_agent_run_history_wiring.py -q`

- [ ] **Step 3: 实现 `FigureUploadPort` 与 ports 字段扩展**

[runtime_ports.py](matmaster/types/runtime_ports.py) 加：

```python
from matmaster.types.figures import FigureUploadConfig

@dataclass(frozen=True)
class FigureUploadPort:
    config: FigureUploadConfig | None = None

@dataclass(frozen=True)
class PlaygroundRuntimePorts:
    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(default_factory=PlaygroundCompactionPort)
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
```

确认无循环 import（`FigureUploadConfig` 已依赖 `pydantic`，与 `runtime_ports.py` 现有依赖无冲突）。

- [ ] **Step 4: 实现 `PlaygroundContext.with_runtime_port` helper**

[playground.py](matmaster/core/playground.py) 加：

```python
from dataclasses import replace

def with_runtime_port(self, **fields: Any) -> "PlaygroundContext":
    """Return a new frozen instance with selected runtime port fields merged.

    Uses ``dataclasses.replace`` to preserve all sibling port fields.
    """
    if not fields:
        return self
    new_ports = replace(self.runtime_ports, **fields)
    return self.model_copy(update={"runtime_ports": new_ports})
```

**保留 `with_runtime_ports(runtime_ports)` 旧方法**，但所有新代码必须用 `with_runtime_port`（单数）。Task 2 起 service 层全部迁到新 helper；旧方法在 P2 收尾时再删。

- [ ] **Step 5: 改写 `build_history_wiring` 合并语义**

[agent_run_history_wiring.py:186-193](src/services/agent_run_history_wiring.py:186) 当前：

```python
runtime_ports = PlaygroundRuntimePorts(
    child_event_forward_sink=child_event_sink,
    compaction=PlaygroundCompactionPort(...),
)
```

改为：

```python
def build_history_wiring(
    *,
    base_runtime_ports: PlaygroundRuntimePorts,
    ...
) -> HistoryWiringResult:
    ...
    runtime_ports = dataclasses.replace(
        base_runtime_ports,
        child_event_forward_sink=child_event_sink,
        compaction=PlaygroundCompactionPort(...),
    )
```

caller [agent_run_service.py](src/services/agent_run_service.py)（Task 2 Step 4 同步改）：

```python
wiring = build_history_wiring(
    base_runtime_ports=pg_ctx.runtime_ports,  # 此时已含 figure_upload
    ...
)
pg_ctx = pg_ctx.with_runtime_ports(wiring.runtime_ports)
```

- [ ] **Step 6: 验证通过**

`uv run pytest tests/matmaster/types/test_runtime_ports.py tests/matmaster/core/test_playground_context.py tests/matmaster/services/test_agent_run_history_wiring.py -q`

### Task 2: service 层迁到 `with_runtime_port` 注入 `figure_upload`

**Files:**
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
- Test: [tests/matmaster/services/test_agent_run_stream.py](tests/matmaster/services/test_agent_run_stream.py)

- [ ] **Step 1: 写失败测试**

新增 `test_run_agent_injects_figure_upload_via_runtime_ports`：

```python
ctx = svc._test_fake_exp.last_ctx
assert ctx.runtime_ports.figure_upload.config is not None
assert isinstance(ctx.runtime_ports.figure_upload.config, FigureUploadConfig)
assert "figure_upload_config" not in ctx.run_meta
# 关键：figure_upload 注入早于 history wiring，必须能存活到 last_ctx
assert ctx.runtime_ports.child_event_forward_sink is not None
assert ctx.runtime_ports.compaction.history is not None
```

注意现有 [test_run_agent_injects_figure_upload_config_into_pg_ctx_run_meta](tests/matmaster/services/test_agent_run_stream.py:107) 测的是旧路径，本任务 Step 5 一并删除。

- [ ] **Step 2: 验证失败**

`uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_run_agent_injects_figure_upload_via_runtime_ports -q`

- [ ] **Step 3: 实现 service 层注入**

定位 [agent_run_service.py:484-489](src/services/agent_run_service.py:484) 当前代码：

```python
pg_ctx = pg_ctx.with_run_meta(
    figure_upload_config=figure_upload_config,
    user_instructions=...,
    ...
)
```

拆成两段：

```python
pg_ctx = pg_ctx.with_runtime_port(
    figure_upload=FigureUploadPort(config=figure_upload_config),
)
pg_ctx = pg_ctx.with_run_meta(
    user_instructions=user_instructions.text,
    user_instructions_hash=user_instructions.hash,
    user_instructions_truncated=user_instructions.truncated,
)
```

注意 `with_runtime_port` 是 Task 1 Step 4 引入的新 helper，单数形式，**禁止用 `with_runtime_ports(PlaygroundRuntimePorts(...))` 全包替换**——后者会丢失之前注入的字段。

- [ ] **Step 4: history wiring caller 同步改**

[agent_run_service.py:505-515](src/services/agent_run_service.py:505) 调用 `build_history_wiring()` 时传入 `base_runtime_ports=pg_ctx.runtime_ports`（此时已含 `figure_upload`）：

```python
wiring = build_history_wiring(
    base_runtime_ports=pg_ctx.runtime_ports,
    events_table=events_table,
    session_id=session_id,
    task_id=task_id,
    raw_history_limit=_DIALOG_HISTORY_MAX_EVENTS,
    child_event_sink=_child_event_sink,
    checkpoint_sink_factory=_checkpoint_sink_factory,
    pre_compaction_barrier=fanout.flush_persistence_barrier,
)
pg_ctx = pg_ctx.with_runtime_ports(wiring.runtime_ports)
```

- [ ] **Step 5: 改 exp 层读取**

修改 [matmaster/core/exp.py:495](matmaster/core/exp.py:495)：

```python
figure_upload_config = ctx.runtime_ports.figure_upload.config
if figure_upload_config is not None:
    runner_state.set("figure_upload_config", figure_upload_config)
```

- [ ] **Step 6: 删除旧测试与 fixture 残留**

删除 [test_run_agent_injects_figure_upload_config_into_pg_ctx_run_meta](tests/matmaster/services/test_agent_run_stream.py:107)。`rg -n "figure_upload_config" tests/` 应该不再出现在 `run_meta` 上下文。

- [ ] **Step 7: 验证通过**

`uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/core/test_exp_runtime_v2.py -q`

### Task 3: 把契约写入 boundary 测试

**Files:**
- Modify: [tests/matmaster/services/test_agent_run_stream.py](tests/matmaster/services/test_agent_run_stream.py)
- Create: `tests/matmaster/services/test_figure_upload_port_boundary.py`

- [ ] **Step 1: 扩展 forbidden 集合**

在 [test_run_agent_does_not_store_callback_ports_in_run_meta:392](tests/matmaster/services/test_agent_run_stream.py:392) 的 `forbidden` 集合里增加：

```python
forbidden = {
    'child_event_forward_sink',
    'checkpoint_sink_factory',
    'pre_compaction_barrier',
    'figure_upload_config',   # P0 新加
}
```

- [ ] **Step 2: 新增独立 boundary 测试（含 merge 保留断言）**

`tests/matmaster/services/test_figure_upload_port_boundary.py` 内：

```python
def test_figure_upload_port_is_set_and_survives_history_wiring(...):
    ctx = svc._test_fake_exp.last_ctx

    # 1. 新路径写入
    assert ctx.runtime_ports.figure_upload.config is not None
    assert isinstance(ctx.runtime_ports.figure_upload.config, FigureUploadConfig)

    # 2. 旧路径不回流
    assert ctx.run_meta.get('figure_upload_config') is None

    # 3. 关键：history wiring 不覆盖 figure_upload
    assert ctx.runtime_ports.child_event_forward_sink is not None
    assert ctx.runtime_ports.compaction.history is not None
    assert ctx.runtime_ports.compaction.checkpoint_sink_factory is not None
```

这条测试与 forbidden 集合双保险：前者保证写入新路径并能存活到 last_ctx（merge 语义对），后者保证不回流旧路径。

- [ ] **Step 3: 验证通过**

`uv run pytest tests/matmaster/services/test_agent_run_stream.py tests/matmaster/services/test_figure_upload_port_boundary.py -q`

### Task 4: P0 最终验证

- [ ] **Step 1: 全量回归**

`uv run pytest tests/ -q -x`

- [ ] **Step 2: grep 残留**

`rg -n "figure_upload_config" src/ matmaster/`

预期：只剩 [exp.py:495](matmaster/core/exp.py:495) 取出与 runner_state 使用一处、[runtime_ports.py](matmaster/types/runtime_ports.py) 类型定义，没有任何 `run_meta` 上下文残留。

- [ ] **Step 3: ports merge 全局合规**

`rg -n "PlaygroundRuntimePorts\(" src/ matmaster/`

预期：运行时代码里只有 [runtime_ports.py](matmaster/types/runtime_ports.py) 的 dataclass 定义会出现 `PlaygroundRuntimePorts(`；`history_wiring` 使用 `dataclasses.replace(base_runtime_ports, ...)`，不再直接构造 `PlaygroundRuntimePorts(...)`。测试代码可继续直接构造 fixtures。

- [ ] **Step 4: diff 检查**

`git diff --stat`

预期：动到 ~6 个文件（runtime_ports.py、playground.py、agent_run_service.py、agent_run_history_wiring.py、exp.py、测试文件）。

---

# Phase P1: session_id 显式化 + 图片链路 / 幽灵字段清理

**目标 PR 边界：** 本 phase 独立 PR。包含 session_id 字段化、`current_user_images` → `TurnInput` 图片链路迁移、`skill_config` / `legal_mcp_servers` 等幽灵读取删除。不动 `run_meta` 整体形态（P2 才动）。

### Task 5: 给 `PlaygroundContext` 加显式 `session_id` 字段并从 `run_meta` 剥离

**Files:**
- Modify: [matmaster/core/playground.py](matmaster/core/playground.py)
- Modify: [matmaster/core/exp.py](matmaster/core/exp.py)
- Modify: [matmaster/core/runtime_context_assembly.py](matmaster/core/runtime_context_assembly.py)
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
- Test: [tests/matmaster/core/test_playground_context.py](tests/matmaster/core/test_playground_context.py)
- Test: [tests/matmaster/services/test_agent_run_stream.py](tests/matmaster/services/test_agent_run_stream.py)

- [ ] **Step 1: 写失败测试（含剥离断言）**

A. `test_playground_context_carries_explicit_session_id`：service prepare 后 `pg_ctx.session_id == session_id`，并断言 `_build_kernel_meta` 输出的 `spec.meta["session_id"]` 等于 `ctx.session_id`。

B. `test_playground_prepare_does_not_leak_session_id_into_run_meta`（**关键 boundary**）：

```python
pg_ctx = playground.prepare(
    run_dir="...",
    task_id="t1",
    session_id="s1",
)
assert pg_ctx.session_id == "s1"
assert "session_id" not in pg_ctx.run_meta  # 严禁双轨
```

C. `test_run_agent_passes_session_id_to_playground_prepare`：service 入口实际调用 `prepare(..., session_id=session_id)` 而非 dict 形式。

- [ ] **Step 2: 验证失败**

`uv run pytest tests/matmaster/core/test_playground_context.py tests/matmaster/services/test_agent_run_stream.py -q`

- [ ] **Step 3: `PlaygroundContext` 加字段**

[playground.py:48-82](matmaster/core/playground.py:48) 加 `session_id: str = ""` 字段（紧跟 `session_type`）。

- [ ] **Step 4: 重写 `Playground.prepare()` 为显式关键字签名**

[playground.py:175-227](matmaster/core/playground.py:175) 当前：

```python
def prepare(self, run_meta: dict[str, Any]) -> PlaygroundContext:
    ...
    run_meta_copy = dict(run_meta)
    return PlaygroundContext(..., run_meta=run_meta_copy, ...)
```

改为显式关键字参数 + 只把 `session_id` 从 `run_meta` 中剥离。注意：`run_dir` / `task_id` 在 P2 引入 `RunMetadata` 前仍保留在 `run_meta`，因为当前 `_build_kernel_meta` 与 `runtime_context_assembly` 仍会消费它们：

```python
def prepare(
    self,
    *,
    run_dir: str | Path | None = None,
    task_id: str = "",
    session_id: str = "",
    session_override: Any = None,
    **extra_run_meta: Any,  # 临时口子：turn_input / bohrium 等 P2 才迁
) -> PlaygroundContext:
    workspace_path = self._resolve_workspace_path_explicit(run_dir, task_id)
    workspace_path.mkdir(parents=True, exist_ok=True)
    ...
    self._setup_logging_explicit(run_dir, task_id)

    run_meta = {
        "run_dir": str(run_dir or ""),
        "task_id": task_id,
        **extra_run_meta,
    }
    # P1 只剥离 session_id；run_dir/task_id 到 P2 才迁入 RunMetadata。
    assert "session_id" not in run_meta

    return PlaygroundContext(
        workdir=workspace_path,
        session_type=self._session_type,
        session_id=session_id,
        cache_area=cache_area,
        execution_workdir=str(workspace_path),
        env_vars=self._collect_env_vars(),
        archival=self._archival,
        run_meta=run_meta,
        session=self.session,
    )
```

`_resolve_workspace_path_explicit(run_dir, task_id)` 与 `_setup_logging_explicit(run_dir, task_id)` 是把现有 `_resolve_workspace_path(run_meta)` / `_setup_logging(run_meta)` 的入参改成显式两参数。

- [ ] **Step 5: service caller 同步**

[agent_run_service.py:273-278](src/services/agent_run_service.py:273) 改为：

```python
pg_ctx = playground.prepare(
    run_dir=run_dir,
    task_id=task_id,
    session_id=session_id,
)
```

devshell / evaluation 路径（[devshell/runner.py:58-67](matmaster/devshell/runner.py:58) 和 [evaluation/core/mat_runner.py:165-173](evaluation/core/mat_runner.py:165)）当前是直接构造 `PlaygroundContext(..., run_meta={"source": ..., "task_id": ..., ...})`。本任务不改它们：`PlaygroundContext.session_id` 有默认空字符串，且这些路径的 `run_meta` 到 P2 再统一迁成 `RunMetadata`，符合 Scope And Non-Negotiables。

- [ ] **Step 6: 下游改读 ctx.session_id**

- [exp.py:241](matmaster/core/exp.py:241)：`parent_session_id = ctx.session_id`
- [exp.py:327](matmaster/core/exp.py:327)：`_build_kernel_meta` 改成从 `ctx.session_id` 取，**不再从 run_meta 读**
- [runtime_context_assembly.py:115/119](matmaster/core/runtime_context_assembly.py:115)：改读 `ctx.session_id`

- [ ] **Step 7: 验证通过**

`uv run pytest tests/matmaster/ -q -x`

特别注意 `test_playground_prepare_does_not_leak_session_id_into_run_meta` 必须绿。

### Task 6: `current_user_images` 统一迁入 `TurnInput.images` 并删除 hack 分支

**Files:**
- Modify: [matmaster/context/sources/turn_input.py](matmaster/context/sources/turn_input.py)（若 `TurnInput.images` 尚未含 detail/mime_type 等字段）
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
- Modify: [matmaster/core/exp.py](matmaster/core/exp.py)
- Modify: [matmaster/core/agent.py](matmaster/core/agent.py)
- Test: 新增 `tests/matmaster/integration/test_image_input_e2e.py`
- Test: [tests/matmaster/core/test_agent_kernel_stream.py](tests/matmaster/core/test_agent_kernel_stream.py)
- Test: [tests/matmaster/context/sources/test_turn_input.py](tests/matmaster/context/sources/test_turn_input.py)

**关键纠正：** [exp.py:586-595](matmaster/core/exp.py:586) **不是死代码**。它在 `build_runtime` 之后用 `spec.model_copy(update={"meta": {**spec.meta, "current_user_images": ...}})` 把图片绕过 `_build_kernel_meta` 塞回 `spec.meta`，被 [agent.py:240-243](matmaster/core/agent.py:240) 消费，[test_current_user_images_are_sent_as_content_parts](tests/matmaster/core/test_agent_kernel_stream.py:487) 固化此链路。**误删会回归图像输入**。

**强制顺序：先建端到端测试 → 把数据源迁到 `TurnInput.images` → 改 agent 读取 → 最后删 hack 分支。**

- [ ] **Step 1: 写 e2e 集成测试覆盖当前真实链路**

`tests/matmaster/integration/test_image_input_e2e.py` 新建：

```python
async def test_images_flow_from_service_to_kernel_user_message():
    """端到端：service 注入 images → kernel 构造 UserMessage 时 message.images 非空。"""
    svc = ...  # AgentRunService with image_input
    images = ["https://oss.example.com/a.png"]
    await svc.run_agent(..., images=images)

    # 通过 FakeLLMProvider 捕获最后一次发给 LLM 的 messages
    provider = svc._test_fake_exp.provider
    last_user_msg = next(m for m in reversed(provider.seen_messages[-1]) if m["role"] == "user")

    # 关键断言：图片真的到了 LLM
    assert any("image_url" in part for part in last_user_msg["content"]) \
        or last_user_msg.get("images")
```

此测试在 Step 1 应**通过**（当前链路工作），用于在后续步骤中防回归。

- [ ] **Step 2: 验证 e2e 测试在当前实现下通过**

`uv run pytest tests/matmaster/integration/test_image_input_e2e.py -q`

预期：绿。如果红，说明当前生产链路已经有图像输入 bug，先单独修，不在本 plan scope。

- [ ] **Step 3: 扩展 `TurnInput` 的图片 detail 表达**

当前生产链路会把 `selected_profile.vision_detail` 写入 `current_user_images` 中的每张图片 dict；迁入 `TurnInput` 时不能悄悄丢掉 detail。由于现状所有图片共享同一个 `vision_detail`，本任务采用最小扩展：`TurnAttachmentsSource.images` 仍保持 `tuple[str, ...]`，新增 `image_detail` 字段，并让 `images_as_parts()` 带上 detail。

[turn_input.py:55-84](matmaster/context/sources/turn_input.py:55) 改为：

```python
from typing import Literal

@dataclass(frozen=True)
class TurnAttachmentsSource:
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    image_detail: Literal["low", "high", "auto"] | None = None
    workspace_paths: tuple[str, ...] = ()

    def images_as_parts(self) -> tuple[ImageContentPart, ...]:
        return tuple(
            ImageContentPart(url=url, detail=self.image_detail)
            for url in self.images
        )
```

同步更新 `TurnInput.from_values(..., image_detail=...)`、`from_payload()`、`to_payload()` 和 [tests/matmaster/context/sources/test_turn_input.py](tests/matmaster/context/sources/test_turn_input.py)，新增断言：

```python
turn_input = TurnInput.from_values(
    user_text="看图",
    images=["https://oss.example.com/a.png"],
    image_detail="high",
)
assert turn_input.attachments.images_as_parts() == (
    ImageContentPart(url="https://oss.example.com/a.png", detail="high"),
)
```

- [ ] **Step 4: 把 `current_user_images` 统一迁入 `TurnInput.images`**

[agent_run_service.py:357-371](src/services/agent_run_service.py:357) 当前先构造 `image_parts: list[dict]` 写入 `pg_ctx.with_run_meta(current_user_images=image_parts)`，[agent_run_service.py:560-572](src/services/agent_run_service.py:560) 又把 `current_user_images` 同时塞进 `TurnInput.images` —— 这是双轨。改成单一来源：

```python
image_urls = tuple(current_images)

# 不再写 pg_ctx.with_run_meta(current_user_images=...)

turn_input = turn_input or TurnInput.from_values(
    user_text=user_prompt,
    files=(),
    images=image_urls,
    image_detail=selected_profile.vision_detail if current_images else None,
    workspace_paths=(),
    pre_turn_history_event_id=pre_turn_history_event_id,
)
```

如果调用方已经显式传入 `turn_input`，不要覆盖它；此时 `images` 参数应在进入 `AgentRunService.run_agent()` 前已经被上游组装进 `turn_input`。若现状还没有这个上游路径，补一个 service 层测试固定预期：显式 `turn_input` 优先，裸 `images=` 只用于构造默认 `TurnInput`。

- [ ] **Step 5: agent 改读 `turn_input.attachments.images_as_parts()`**

[agent.py:240-243](matmaster/core/agent.py:240) 改为：

```python
# P1 临时通过 spec.meta["turn_input"] 取；P2 改成 spec.turn_input。
raw_turn_input = spec.meta.get("turn_input")
turn_input = (
    raw_turn_input
    if isinstance(raw_turn_input, TurnInput)
    else TurnInput.from_payload(raw_turn_input)
)
current_user_images = (
    list(turn_input.attachments.images_as_parts())
    if turn_input is not None
    else []
)
```

`spec.meta["current_user_images"]` 读取**保留**到 e2e 测试与单测全绿后再删，避免单步崩盘。

- [ ] **Step 6: 跑 e2e 测试、TurnInput 测试与现有 kernel 测试**

`uv run pytest tests/matmaster/integration/test_image_input_e2e.py tests/matmaster/context/sources/test_turn_input.py tests/matmaster/core/test_agent_kernel_stream.py -q`

预期：全绿。`test_current_user_images_are_sent_as_content_parts` 此时还在用 `spec.meta`，不冲突。

- [ ] **Step 7: 删除 hack 分支与旧读取**

确认 Step 6 全绿后：

- 删 [exp.py:586-595](matmaster/core/exp.py:586) 的 `if current_user_images: spec = spec.model_copy(...)` 整块（指**该行块**，不是文件）
- 删 [agent.py:240-243](matmaster/core/agent.py:240) 通过 `spec.meta.get("current_user_images")` 的读取（指**该行块**）
- 重写 [test_current_user_images_are_sent_as_content_parts](tests/matmaster/core/test_agent_kernel_stream.py:487)：spec 构造改为 `spec.model_copy(update={"meta": {"turn_input": TurnInput.from_values(user_text="看图", images=["https://oss.example.com/chat/a.png"], image_detail="high")}})`，断言 agent 从 `turn_input.attachments.images_as_parts()` 消费。P2 再把此测试里的 `meta.turn_input` 改成 `spec.turn_input`

- [ ] **Step 8: 全量回归 + 残留 grep**

`uv run pytest tests/ -q -x`
`rg -n 'current_user_images' src/ matmaster/`

预期：除了 service 层早期构造 `image_parts` 的命名外（可顺手改名 `image_urls`），核心路径无其他出现；e2e 测试常驻保护。

### Task 7: 删除 `skill_config` 幽灵读取

**Files:**
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
- Modify: [matmaster/core/exp.py](matmaster/core/exp.py)
- Test: [tests/matmaster/services/test_agent_run_stream.py](tests/matmaster/services/test_agent_run_stream.py)

- [ ] **Step 1: 全仓搜索确认无写入**

`rg -n '"skill_config"|with_run_meta.*skill_config|run_meta\[.skill_config.\]' src/ matmaster/ tests/`

预期：除了 [agent_run_service.py:642](src/services/agent_run_service.py:642) 的读取，无任何写入。

- [ ] **Step 2: 检查 `exp.run_stream(skills=...)` 参数链路**

在 [exp.py:561](matmaster/core/exp.py:561) 起的 `run_stream` 签名里看 `skills` 参数是否真有其他用途。如果只是给 `_init_skill_tools` 用、且 service 层永远传 None，可考虑把整个参数链路一并清掉。本任务保守只删读取点。

- [ ] **Step 3: 删除读取**

[agent_run_service.py:642](src/services/agent_run_service.py:642) 改为：

```python
exp.run_stream(
    pg_ctx,
    user_prompt,
    history=history,
    cancel_token=cancel_token,
    skill_resolver=skill_resolver,
)
```

直接去掉 `skills=` 一行。

- [ ] **Step 4: 测试通过**

`uv run pytest tests/matmaster/services/ tests/matmaster/core/test_exp.py -q`

### Task 8: 删除 `legal_mcp_servers` / `schemas_by_server` / `split_turn_attachments` 残留

**Files:**
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
- Modify: [matmaster/core/runtime_context_assembly.py](matmaster/core/runtime_context_assembly.py)
- Modify: [src/services/context_assembly_factory.py](src/services/context_assembly_factory.py)（可能涉及）

- [ ] **Step 1: 三字段写入端确认**

`rg -n '"legal_mcp_servers"|"schemas_by_server"|"split_turn_attachments"' src/ matmaster/ tests/`

预期：只在三处读取点出现，无任何 `with_run_meta(legal_mcp_servers=...)` 写入。

- [ ] **Step 2: 追溯历史确认这些字段已被 skill_resolver 替代**

查 git blame：

`git log -S 'legal_mcp_servers' -- src/services/agent_run_service.py matmaster/core/runtime_context_assembly.py`

确认 R6 active skills boundary 重构后这些字段已通过 `SkillResolver` 闭包传递。

- [ ] **Step 3: 删除读取**

- [agent_run_service.py:535-538](src/services/agent_run_service.py:535)：把 `build_context_assembler` 调用里的三个 `run_meta.get(...)` 参数全部删掉，改成 None 或默认值
- [runtime_context_assembly.py:102-106](matmaster/core/runtime_context_assembly.py:102)：同上，`session_context_factory` 内部参数与 `ContextRenderOptions` 改成默认值
- 如有上游 helper（如 [context_assembly_factory.py](src/services/context_assembly_factory.py)）暴露这些参数，签名一并清理

- [ ] **Step 4: 测试通过**

`uv run pytest tests/matmaster/ tests/services/ -q -x`

### Task 9: P1 最终验证

- [ ] **Step 1: 全量回归**

`uv run pytest tests/ -q -x`

- [ ] **Step 2: 确认 run_meta key 集合收敛到 P2 预期范围**

`rg -n 'run_meta\.get\(|run_meta\[' src/ matmaster/ | grep -v '^tests'`

预期剩下的 key 只有：`task_id`、`run_dir`、`turn_input`、`user_instructions*`（hash/truncated）、`bohrium_rebuild_events`、`active_skills`、`bohrium`、`source`。

如果列表里出现以下任一，说明本 phase 未清干净，立即返工：

- `session_id`（Task 5 应已剥离）
- `current_user_images`（Task 6 应已迁入 TurnInput）
- `figure_upload_config`（P0 应已迁 ports）
- `skill_config` / `legal_mcp_servers` / `schemas_by_server` / `split_turn_attachments`（Task 7-8 应已删除）

- [ ] **Step 3: e2e 图片测试常驻保护**

`uv run pytest tests/matmaster/integration/test_image_input_e2e.py -q`

预期：绿。这条测试在 P1 之后必须长期常驻 CI，防止后续 P2 改 spec.meta 时图片输入再次回归。

---

# Phase P2: run_meta → typed RunMetadata + AgentRuntimeSpec.meta 拆解

**目标 PR 边界：** 本 phase 单独 PR，文件改动量最大。前置：P0 + P1 必须 merge。

### Task 10: 引入 `RunMetadata` 与 `RunIdentity` typed model

**Files:**
- Create: `matmaster/types/run_metadata.py`
- Create: `tests/matmaster/types/test_run_metadata.py`

- [ ] **Step 1: 写失败测试**

测：

- `RunMetadata` frozen 行为、默认值、`model_copy(update=...)` 不可变更新语义
- `RunMetadata` 在 `extra="forbid"` 下传入未知字段会抛 `ValidationError`（防止幽灵字段卷土重来）
- `RunMetadata.user_instructions` 是 `UserInstructions` 对象时，`.hash` 与 `.truncated` 字段在 `model_copy` 后保持引用相等（**不重算 hash**）
- `RunIdentity` 同样测 frozen + 默认值

- [ ] **Step 2: 验证失败**

`uv run pytest tests/matmaster/types/test_run_metadata.py -q`

- [ ] **Step 3: 实现 `RunMetadata` 与 `RunIdentity`**

```python
# matmaster/types/run_metadata.py
from pydantic import BaseModel, ConfigDict, Field
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.ports import UserInstructions

class RunIdentity(BaseModel):
    """运行身份。AgentRuntimeSpec.run_identity 也直接复用此模型。"""
    model_config = ConfigDict(frozen=True)

    task_id: str = ""
    session_id: str = ""
    spawn_id: str | None = None


class RunMetadata(BaseModel):
    """PlaygroundContext.metadata 的 typed 形态，替代 run_meta dict。

    严格 typed：extra='forbid' 阻止任意 key 偷渡进来。
    """
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    run_dir: str = ""
    task_id: str = ""
    source: str = ""  # devshell / evaluation / web，仅用于日志诊断
    turn_input: TurnInput | None = None
    user_instructions: UserInstructions | None = None  # 复用 service 层 instructions_bundle，禁止重算 hash
    active_skills: frozenset[str] = Field(default_factory=frozenset)
    bohrium_rebuild_events: tuple = Field(default_factory=tuple)
```

**重要约束：**

- `RunIdentity` 在此唯一定义。`matmaster/types/runtime.py` 与 `matmaster/core/agent.py` 必须 `from matmaster.types.run_metadata import RunIdentity`，**严禁重复定义**。
- 不提供 `from_legacy_dict()` classmethod —— 与 Architecture Decisions 的"不保留任何 dict 兼容路径"一致。所有 caller 一次性迁移到 `RunMetadata(...)` 构造。
- `session_id` **不在** `RunMetadata` 里（P1 已落 `PlaygroundContext.session_id`）。`RunIdentity.session_id` 是给 spec 层用的（kernel hook 需要），与 ctx.session_id 在 `_build_run_identity` 处映射。
- `current_user_images` **不在** `RunMetadata` 里（P1 Task 6 已迁入 `TurnInput.images`）。
- `figure_upload_config` **不在** `RunMetadata` 里（P0 已迁 ports）。
- `bohrium` **不在** `RunMetadata` 里（Task 11 迁 ports）。

- [ ] **Step 4: 验证通过**

`uv run pytest tests/matmaster/types/test_run_metadata.py -q`

### Task 11: `bohrium` 迁到 `PlaygroundRuntimePorts.bohrium`（窄 typed snapshot）

**Files:**
- Modify: [matmaster/types/runtime_ports.py](matmaster/types/runtime_ports.py)
- Modify: [matmaster/core/playground.py](matmaster/core/playground.py)
- Modify: [matmaster/core/path_access.py](matmaster/core/path_access.py)
- Modify: [src/services/agent_run_bohrium.py](src/services/agent_run_bohrium.py) 与 [src/services/agent_run_bohrium_stage.py](src/services/agent_run_bohrium_stage.py)（caller 端）
- Test: [tests/matmaster/test_bohrium_setup_injection.py](tests/matmaster/test_bohrium_setup_injection.py)
- Test: [tests/matmaster/integration/test_bohrium_execution_contract.py](tests/matmaster/integration/test_bohrium_execution_contract.py)
- Test: [tests/matmaster/core/test_path_access.py](tests/matmaster/core/test_path_access.py)（若不存在则新建）

**关键约束：** [AGENTS.md:87](AGENTS.md:87) 明确禁止 RuntimePorts 子端口含 `extra` / `metadata` / `state` / `dict[str, Any]` 兜底字段。`BohriumRuntimePort.snapshot` 必须是窄 typed model，仅含真实消费者用到的字段。

- [ ] **Step 1: 调查真实消费者字段集**

`rg -n 'bohrium' src/ matmaster/ | grep -v test`

确认消费者：

- [path_access.py:52-60](matmaster/core/path_access.py:52)：读 `bohrium["remote_project_root"]`、`bohrium["remote_workspace_root"]`
- [test_bohrium_setup_injection.py:339](tests/matmaster/test_bohrium_setup_injection.py:339)：写 `bohrium["node_id"]`（现有值为 int）
- [test_playground_context.py:231](tests/matmaster/core/test_playground_context.py:231)：写 `{"ssh_attached": True, "node_id": "abc"}`，迁移时需改成真实 int，例如 `node_id=9`
- [test_bohrium_execution_contract.py:584](tests/matmaster/integration/test_bohrium_execution_contract.py:584)：读 `bmeta = pg_passed.run_meta.get('bohrium', {})`

汇总出真实字段集（不超过 5 个）。

- [ ] **Step 2: 定义 `BohriumRuntimeSnapshot` 窄 typed model 与 `BohriumRuntimePort`**

```python
# matmaster/types/runtime_ports.py
from pydantic import BaseModel, ConfigDict

class BohriumRuntimeSnapshot(BaseModel):
    """Bohrium runtime snapshot, narrow typed model. AGENTS.md:87 forbids dict[str, Any]."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    ssh_attached: bool = False
    node_id: int | None = None
    remote_project_root: str | None = None
    remote_workspace_root: str | None = None
    # 若 Step 1 发现其他真实字段，按需追加（每加一个字段都要有消费者引用为据）

@dataclass(frozen=True)
class BohriumRuntimePort:
    snapshot: BohriumRuntimeSnapshot | None = None


@dataclass(frozen=True)
class PlaygroundRuntimePorts:
    child_event_forward_sink: BusEventSink | None = None
    compaction: PlaygroundCompactionPort = field(default_factory=PlaygroundCompactionPort)
    figure_upload: FigureUploadPort = field(default_factory=FigureUploadPort)
    bohrium: BohriumRuntimePort = field(default_factory=BohriumRuntimePort)
```

- [ ] **Step 3: 写失败测试**

A. `test_bohrium_runtime_snapshot_rejects_unknown_fields`：传入 `BohriumRuntimeSnapshot(unknown_field="x")` 抛 `ValidationError`（验 `extra="forbid"`）。

B. `test_playground_with_bohrium_uses_typed_snapshot`：`pg_ctx.with_bohrium(BohriumRuntimeSnapshot(node_id=9))` 后 `pg_ctx.runtime_ports.bohrium.snapshot.node_id == 9`，且 `pg_ctx.run_meta` 不含 `bohrium` key。

C. `test_path_access_reads_typed_bohrium_snapshot`：path_access 在 typed snapshot 下能正确返回 path roots。

- [ ] **Step 4: 改 `Playground.with_bohrium()`**

[playground.py:109-112](matmaster/core/playground.py:109)：

```python
from dataclasses import replace

def with_bohrium(self, snapshot: BohriumRuntimeSnapshot) -> "PlaygroundContext":
    """Return a new frozen instance with typed Bohrium snapshot in runtime_ports."""
    new_ports = replace(self.runtime_ports, bohrium=BohriumRuntimePort(snapshot=snapshot))
    return self.model_copy(update={"runtime_ports": new_ports})
```

注意签名从 `snapshot: dict[str, Any]` 改成 `snapshot: BohriumRuntimeSnapshot`，所有 caller（service 层 bohrium 阶段）必须先构造 typed model 再传。

- [ ] **Step 5: `path_access.py` 改读 typed 字段**

[path_access.py:51-60](matmaster/core/path_access.py:51)：

```python
snapshot = ctx.runtime_ports.bohrium.snapshot
if snapshot is not None:
    _add(snapshot.remote_project_root, "runtime")
    if snapshot.remote_workspace_root:
        _add(
            posixpath.join(snapshot.remote_workspace_root, ".matmaster"),
            "project_runtime",
        )
```

注意：异常语义保持现状——`snapshot is None` 时跳过所有 root 注入；字段为 `None` 时 `_add` 已有 `isinstance(raw_root, str)` 防御。

- [ ] **Step 6: caller 端构造 typed snapshot**

`rg -n 'with_bohrium\(' src/ matmaster/` 找出所有 caller。当前应该都是 dict 形式：

```python
pg_ctx.with_bohrium({"ssh_attached": True, "node_id": x, ...})
```

改为：

```python
pg_ctx.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True, node_id=x, ...))
```

- [ ] **Step 7: 测试通过**

`uv run pytest tests/matmaster/test_bohrium_setup_injection.py tests/matmaster/integration/test_bohrium_execution_contract.py tests/matmaster/core/test_path_access.py tests/matmaster/types/test_runtime_ports.py -q`

- [ ] **Step 8: AGENTS 合规检查**

`rg -n 'snapshot: dict|BohriumRuntimePort.*dict|bohrium: dict' matmaster/types/runtime_ports.py`

预期：零。`runtime_ports.py` 里已有 protocol / TypedDict 可能合法使用 `dict[str, Any]`，本检查只锁定 Bohrium 子端口不得回落到 dict 形态。

### Task 12: `PlaygroundContext.run_meta` → `PlaygroundContext.metadata`

**Files:**
- Modify: [matmaster/core/playground.py](matmaster/core/playground.py)
- Modify: [matmaster/core/exp.py](matmaster/core/exp.py)
- Modify: [matmaster/core/runtime_context_assembly.py](matmaster/core/runtime_context_assembly.py)
- Modify: [src/services/agent_run_service.py](src/services/agent_run_service.py)
- Modify: [matmaster/devshell/runner.py](matmaster/devshell/runner.py)
- Modify: [evaluation/core/mat_runner.py](evaluation/core/mat_runner.py)
- Modify: 全部 `rg -n "run_meta" tests/` 找到的 fixture
- Test: [tests/matmaster/core/test_playground_context.py](tests/matmaster/core/test_playground_context.py)

- [ ] **Step 1: 写新的契约测试**

`test_playground_context_metadata_is_typed_runmetadata`：断言 `pg_ctx.metadata` 是 `RunMetadata` 实例；断言 `with_metadata(turn_input=...)` 返回新 frozen 实例。

- [ ] **Step 2: 修改 `PlaygroundContext` 与 `Playground.prepare()`（无 dict 兼容入口）**

[playground.py:48-126](matmaster/core/playground.py:48)：

```python
from matmaster.types.run_metadata import RunMetadata

class PlaygroundContext(BaseModel):
    ...
    metadata: RunMetadata = Field(default_factory=RunMetadata)
    # 删除 run_meta 字段
    # 删除 with_run_meta 方法
    # 保留 typed with_bohrium(snapshot: BohriumRuntimeSnapshot)，只删除旧 dict 语义

    def with_metadata(self, **fields: Any) -> "PlaygroundContext":
        """Return a new frozen instance with typed RunMetadata fields merged."""
        if not fields:
            return self
        unknown = set(fields) - set(RunMetadata.model_fields)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown RunMetadata field(s): {names}")
        data = {
            name: getattr(self.metadata, name)
            for name in RunMetadata.model_fields
        }
        data.update(fields)
        new_meta = RunMetadata.model_validate(data)
        return self.model_copy(update={"metadata": new_meta})
```

注意：不要直接用 `self.metadata.model_copy(update=fields)`。Pydantic v2 的 `model_copy(update=...)` 不会验证 update，未知字段会进入实例 `__dict__`，从而绕过 `extra="forbid"`。本任务必须新增 boundary test：

```python
def test_with_metadata_rejects_unknown_fields():
    with pytest.raises(ValueError, match="Unknown RunMetadata field"):
        pg_ctx.with_metadata(ghost_field="x")
```

`Playground.prepare()` 强制接收 `RunMetadata` 实例（**禁止 dict 兼容入口**，与 Architecture Decisions 一致）：

```python
def prepare(
    self,
    metadata: RunMetadata,
    *,
    session_id: str = "",
    session_override: Any = None,
) -> PlaygroundContext:
    """Create workspace, session, logging and return a frozen context.

    Args:
        metadata: Typed run metadata. Required.
        session_id: Run-scoped session identity (P1 explicit field).
        session_override: Caller-owned session to reuse (test/eval path).
    """
    if not isinstance(metadata, RunMetadata):
        raise TypeError(
            f"prepare() requires RunMetadata, got {type(metadata).__name__}. "
            "dict input was removed in run-meta-refactor P2."
        )
    workspace_path = self._resolve_workspace_path_explicit(
        metadata.run_dir, metadata.task_id,
    )
    ...
    return PlaygroundContext(
        ...
        session_id=session_id,
        metadata=metadata,
    )
```

注意 P1 Task 5 已经把 `run_dir`/`task_id`/`session_id` 改成显式关键字参数；P2 这里把 `run_dir`/`task_id` 收回 `metadata` 内部，`session_id` 继续走 prepare 的显式关键字（因为它在 `PlaygroundContext.session_id` 顶层而非 metadata）。所有 P1 caller 必须同步重写。

- [ ] **Step 3: 替换全部 caller（service / devshell / evaluation）**

机械替换表：

| 旧 | 新 |
|---|---|
| `ctx.run_meta.get("task_id", "")` | `ctx.metadata.task_id` |
| `ctx.run_meta.get("turn_input")` | `ctx.metadata.turn_input` |
| `ctx.run_meta.get("user_instructions")` 等三字段 | `ctx.metadata.user_instructions.text` / `.hash` / `.truncated` |
| `pg_ctx.with_run_meta(turn_input=...)` | `pg_ctx.with_metadata(turn_input=...)` |
| `pg_ctx.with_run_meta(user_instructions=..., user_instructions_hash=..., ...)` | `pg_ctx.with_metadata(user_instructions=instructions_bundle)` |
| `ctx.run_meta.get("active_skills") or ()` | `ctx.metadata.active_skills` |
| `(ctx.run_meta or {}).get("bohrium_rebuild_events")` | `ctx.metadata.bohrium_rebuild_events` |
| `playground.prepare(run_dir=..., task_id=..., session_id=...)` | `playground.prepare(RunMetadata(run_dir=..., task_id=...), session_id=...)` |

涉及位置：[exp.py:241/473/492/836/923](matmaster/core/exp.py:241)、[runtime_context_assembly.py:80-119](matmaster/core/runtime_context_assembly.py:80)、[agent_run_service.py:273/280/484/517/632](src/services/agent_run_service.py:273)、[mat_runner.py:165-173](evaluation/core/mat_runner.py:165)、[devshell/runner.py:58-67](matmaster/devshell/runner.py:58)。

devshell / evaluation 当前是直接 `PlaygroundContext(..., run_meta={"source": "devshell"})`，改为：

```python
PlaygroundContext(
    ...
    session_id="",
    metadata=RunMetadata(source="devshell"),
)
```

- [ ] **Step 4: runtime_context_assembly 改读 typed user_instructions**

[runtime_context_assembly.py:85-92](matmaster/core/runtime_context_assembly.py:85) 当前：

```python
instructions_text = str(run_meta.get("user_instructions") or "")
instructions_hash = run_meta.get("user_instructions_hash")
if not isinstance(instructions_hash, str) or not instructions_hash:
    instructions_hash = _hash_user_instructions(instructions_text)
user_instructions = UserInstructions(
    text=instructions_text, hash=instructions_hash,
    truncated=bool(run_meta.get("user_instructions_truncated", False)),
)
```

改为：

```python
user_instructions = ctx.metadata.user_instructions or UserInstructions(
    text="", hash=_hash_user_instructions(""), truncated=False,
)
```

**禁止重算 hash**：service 层已构造的 `instructions_bundle` 必须原样穿透。boundary test 断言：

```python
def test_user_instructions_hash_is_not_recomputed_in_assembly():
    bundle = UserInstructions(text="...", hash="sha256:abc", truncated=False)
    ctx = pg_ctx.with_metadata(user_instructions=bundle)
    assembly = build_runtime_context_assembly(spec=..., ctx=ctx, ...)
    # compactor 持有的 user_instructions 必须是同一个对象
    assert assembly.compactor._user_instructions is bundle
```

- [ ] **Step 5: 测试 fixture 批量改**

`rg -n "run_meta" tests/` 找出全部测试 fixture。所有 `run_meta={"x": "y"}` 必须改为 `metadata=RunMetadata(x="y")`（**禁止用 dict 形式**，因为 prepare 已不接受 dict）。

`rg -n "with_run_meta\(" tests/` 全部改为 `with_metadata(...)`。

- [ ] **Step 6: 验证通过**

`uv run pytest tests/ -q -x`

特别注意 user_instructions hash 不重算的 boundary test 必须绿。

### Task 13: `AgentRuntimeSpec.meta` 拆成 typed 字段

**Files:**
- Modify: [matmaster/types/runtime.py](matmaster/types/runtime.py)
- Modify: [matmaster/core/exp.py](matmaster/core/exp.py)
- Modify: [matmaster/core/agent.py](matmaster/core/agent.py)
- Test: [tests/matmaster/core/test_agent_kernel_compaction.py](tests/matmaster/core/test_agent_kernel_compaction.py)
- Test: [tests/matmaster/core/test_hook_wiring.py](tests/matmaster/core/test_hook_wiring.py)

- [ ] **Step 1: 写失败测试**

测 `AgentRuntimeSpec.run_identity` 字段、`turn_input` 字段；断言 `meta: dict` 字段已删除。

- [ ] **Step 2: 改 `AgentRuntimeSpec`（import 复用 `RunIdentity`）**

[runtime.py:46-75](matmaster/types/runtime.py:46) **不重新定义 `RunIdentity`**，从 `run_metadata.py` import 复用：

```python
from matmaster.types.run_metadata import RunIdentity
from matmaster.context.sources.turn_input import TurnInput

class AgentRuntimeSpec(BaseModel):
    ...
    run_identity: RunIdentity = Field(default_factory=RunIdentity)
    turn_input: TurnInput | None = None
    # 删除 meta: dict[str, Any]
```

boundary test：

```python
def test_run_identity_is_single_sourced():
    from matmaster.types.run_metadata import RunIdentity as Canonical
    from matmaster.types.runtime import AgentRuntimeSpec
    # AgentRuntimeSpec.run_identity 字段类型必须是 canonical RunIdentity
    field = AgentRuntimeSpec.model_fields["run_identity"]
    assert field.annotation is Canonical
```

- [ ] **Step 3: 重写 `_build_kernel_meta` → `_build_run_identity`**

[exp.py:317-333](matmaster/core/exp.py:317)：

```python
@staticmethod
def _build_run_identity(ctx: PlaygroundContext, *, spawn_id: str | None) -> RunIdentity:
    return RunIdentity(
        task_id=ctx.metadata.task_id,
        session_id=ctx.session_id,   # P1 已落 PlaygroundContext 顶层
        spawn_id=spawn_id,
    )
```

[exp.py:541-547](matmaster/core/exp.py:541) 的 `spec.model_copy(update={...})` 改：

```python
update={
    ...
    "run_identity": self._build_run_identity(ctx, spawn_id=spawn_id),
    "turn_input": ctx.metadata.turn_input,
    # 删除 "meta": ...
}
```

- [ ] **Step 4: kernel 改读 typed 字段**

[agent.py:158-180](matmaster/core/agent.py:158)：

```python
RunContext(
    task_id=spec.run_identity.task_id,
    session_id=spec.run_identity.session_id,
    reason="startup",
)
```

- [agent.py:220](matmaster/core/agent.py:220)：`session_id = spec.run_identity.session_id`
- [agent.py:234](matmaster/core/agent.py:234)：`raw_turn_input = spec.turn_input`
- [agent.py:240-243](matmaster/core/agent.py:240)：P1 Task 6 已迁到 `turn_input.attachments.images_as_parts()`，此处仅把 `raw_turn_input = spec.turn_input` 接上
- [agent.py:318](matmaster/core/agent.py:318)：`is_root_run = spec.run_identity.spawn_id is None`

- [ ] **Step 5: 测试通过**

`uv run pytest tests/matmaster/core/ -q -x`

### Task 14: P2 最终验证

- [ ] **Step 1: 全量回归**

`uv run pytest tests/ -q -x`

- [ ] **Step 2: dict-bag 灭绝验证**

`rg -n 'run_meta|spec\.meta\b' src/ matmaster/ evaluation/`

预期：零结果（除非 docstring / 注释里历史描述）。

- [ ] **Step 3: 类型契约固化**

新增 boundary 测试：`PlaygroundContext` 不再有 `run_meta` 字段；`AgentRuntimeSpec` 不再有 `meta` 字段。用 Pydantic 反射：

```python
def test_playground_context_has_no_dict_bag():
    assert "run_meta" not in PlaygroundContext.model_fields

def test_agent_runtime_spec_has_no_dict_bag():
    assert "meta" not in AgentRuntimeSpec.model_fields
```

- [ ] **Step 4: diff 检查与 PR 描述**

`git diff --stat`

PR 描述模板应包含：

```
P2 完成 dict-bag → typed model 收敛。配合 P0 (figure_upload → ports) 与 P1 (session_id 字段化 + 幽灵字段清理 + current_user_images 并入 TurnInput) 已 merge。

变更点：
- PlaygroundContext.run_meta (dict) → PlaygroundContext.metadata (RunMetadata)
- PlaygroundContext.with_run_meta() 已删除；with_bohrium() 保留为 typed snapshot helper，不再接受 dict
- AgentRuntimeSpec.meta (dict) → AgentRuntimeSpec.run_identity (RunIdentity) + .turn_input (TurnInput | None)
- PlaygroundRuntimePorts.bohrium 新增（BohriumRuntimeSnapshot 窄 typed model）
- Playground.prepare() 强制接收 RunMetadata 实例，dict 入参抛 TypeError

向后不兼容（破坏性，与 Architecture Decisions 一致）：
- 第三方调用 ctx.run_meta 的代码必须改为 ctx.metadata.<field>
- 第三方调用 spec.meta["x"] 的代码必须改为 spec.run_identity.x 或 spec.turn_input.x
- 第三方调用 Playground.prepare({...}) 或 Playground.prepare(run_dir=..., task_id=...) 的代码必须改为 Playground.prepare(RunMetadata(...))
- 第三方调用 with_bohrium(dict) 的代码必须改为 with_bohrium(BohriumRuntimeSnapshot(...))
```

---

## Verification Strategy（全 phase 共用）

每个 phase 完成后必须跑：

1. `uv run pytest tests/matmaster/core/test_playground_context.py -q` — 核心契约
2. `uv run pytest tests/matmaster/services/test_agent_run_stream.py -q` — service 集成
3. `uv run pytest tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_hook_wiring.py -q` — runtime 装配
4. `uv run pytest tests/matmaster/integration/ -q` — bohrium / lazy MCP 端到端
5. `uv run pytest tests/ -q -x` — 全量

外加全 phase 共用的"残留 grep"：

```
rg -n 'run_meta' src/ matmaster/ evaluation/  # P2 后应为零
rg -n 'spec\.meta\b' src/ matmaster/ evaluation/  # P2 后应为零
```

## Out Of Scope

- [ToolResult.meta](matmaster/tools/tool_result.py:21) 不动。其字段（`layer`、`mark_read`、`encoding_used`、`truncated`、`full_result_path`）已经是工具协议的扩展点，由 [tool_runner.py:128-275](matmaster/core/tool_runner.py:128) 集中使用，不属于跨类隐式传参问题。
- [LazyMCPTool.meta](matmaster/tools/lazy_mcp.py:114) 不动。该 dict 是 MCP server 元数据，所有字段已在构造时 typed-cast 过。
- `ExpSubagentMeta`、`SkillMetaInfo`、Bedrock provider metadata、`AskQuestionEvent.metadata`、评测 `_eval_task_meta.json` 不动。它们是领域实体或外部协议 metadata，与本计划处理的"dict-bag 跨类传参"是两类问题。
- evaluation / devshell 路径的 `meta = json.loads(...)`、`meta = KNOWN_MODELS[...]` 等局部变量不动。
- 不引入新的 deprecated warning 框架。要么改干净，要么不动。
