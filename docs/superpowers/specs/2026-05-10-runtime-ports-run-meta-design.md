# RuntimePorts 与 run_meta 边界重构设计

## 背景

当前 MatMaster Agent 运行链路中，`PlaygroundContext.run_meta` 同时承载了三类信息：

1. 被动运行 metadata，例如 `task_id`、`session_id`、`active_skills`、`current_user_images`。
2. 服务层注入的运行时能力，例如 `event_sink`、`checkpoint_sink_factory`、`get_query_events`、`get_all_events`、`pre_compaction_barrier`。
3. 运行时配置或状态，例如 `figure_upload_config`、`bohrium_rebuild_events`、`attachment_manifest`。

这种混合用法使 `run_meta` 从 metadata 字典变成了隐式服务能力总线。代码表面上没有让 `matmaster/core` 直接 import `src/services`，但核心层仍然通过字符串 key 知道了服务层注入了哪些能力。这类写法短期灵活，长期会带来几个问题：

- 字符串 key 缺少类型检查，重构时容易漏改。
- 运行时对象会混进 metadata，`model_dump()`、测试 fake、跨层序列化的语义变模糊。
- 核心层依赖的是隐式约定，开发者需要全文搜索才能知道每个 key 的生产者和消费者。
- `AgentRuntimeSpec.meta` 也开始承载 callback，例如 `checkpoint_sink`、`pre_compaction_barrier`，把同一个问题继续传到了 kernel 边界。

本项目已经存在一套 `HookExecutor`，位于 `matmaster/core/hooks.py`。它提供 `observe`、`intercept`、`rewrite` 三类事件扩展能力，并已被 `Exp`、`FullToolRunner`、`AgentKernel` 接入。它目前缺少生产路径业务 handler 注册，所以看起来像未启用，但其语义已经明确：它是运行过程事件系统，而不是服务依赖注入系统。

因此，本设计的目标不是新增第二套 hook 系统，而是把原先计划中的 `runtime_hooks` 重命名并收敛为 `RuntimePorts`：`HookExecutor` 继续表示事件扩展点，`RuntimePorts` 表示核心层所需的外部能力端口。

## 目标

- 将 `run_meta` 中的运行时 callback 迁移到显式、typed、不可序列化的 `RuntimePorts` 字段。
- 保留并复用现有 `HookExecutor`，避免项目内出现两套都叫 hook 的体系。
- 明确 `run_meta`、`AgentRuntimeSpec.meta`、`RuntimePorts`、`HookExecutor` 的职责边界。
- 保持服务层到核心层的单向依赖关系：服务层构造端口实现，核心层只依赖中立类型合同。
- 采用分阶段迁移，先保留旧 `run_meta` key fallback，降低对测试、devshell、evaluation 的冲击。

## 非目标

- 不在本阶段全量 typed 化所有 `run_meta` 字段。
- 不改变现有 `HookExecutor` 的 `observe`、`intercept`、`rewrite` 语义。
- 不把 history reader、checkpoint sink、barrier 等必要能力塞进 `HookExecutor`。
- 不重构 `figure_upload_config`、`bohrium_rebuild_events`、`attachment_manifest` 的全部流转；这些字段可在后续阶段继续收敛。
- 不改变 API/Worker 队列模式、Redis 协调、SSE fanout 或 checkpoint 持久化策略。

## 术语与边界

### run_meta

`run_meta` 保留为被动运行 metadata 字典。它可以承载当前仍未 typed 化的轻量数据，例如：

- `task_id`
- `session_id`
- `spawn_id`
- `active_skills`
- `current_user_images`
- `attachment_manifest`
- `bohrium_rebuild_events`
- `figure_upload_config`

这些字段可以继续逐步收敛，但本阶段的强约束是：新的服务能力 callback 不再加入 `run_meta`。

### RuntimePorts

`RuntimePorts` 是核心层需要的外部能力端口，属于依赖倒置的 typed contract。它承载 callback、factory、sink、barrier 等不可序列化运行时对象。

它的语义是：核心层需要这些能力才能完成某个功能，但核心层不关心这些能力来自数据库、Redis、SSE、fanout、checkpoint service，还是测试 fake。

### HookExecutor

`HookExecutor` 是已有事件扩展系统。它适合表达：

- 观察运行事件，例如 `RUN_START`、`RUN_END`。
- 拦截工具调用，例如 `PRE_TOOL_CALL` 返回 `BLOCK`。
- 改写用户输入或工具结果，例如 `USER_PROMPT_SUBMIT`、`POST_TOOL_CALL`。

它不适合承载必须返回业务数据或必须保证顺序屏障的服务端口。

### AgentRuntimeSpec.meta

`AgentRuntimeSpec.meta` 继续承载 kernel 需要的被动 metadata。它不应承载 callback。`checkpoint_sink` 与 `pre_compaction_barrier` 应迁移到 `AgentRuntimeSpec.runtime_ports`。

## 推荐架构

新增模块：

```text
matmaster/types/runtime_ports.py
```

该模块只依赖标准库和 typing/dataclasses，不依赖 `src.services`、Redis、数据库、FastAPI、SSE handler 或具体 service。它属于中立边界合同层。

核心类型：

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


EventSink = Callable[[Any], Awaitable[None] | None]
PreCompactionBarrier = Callable[[], Awaitable[None] | None]
CheckpointSink = Callable[..., Awaitable[int | None] | int | None]
CheckpointSinkFactory = Callable[..., CheckpointSink]


def _empty_events() -> list[dict[str, Any]]:
    return []


def _no_checkpoint() -> int | None:
    return None


@dataclass(frozen=True)
class CompactionHistoryPort:
    get_query_events: Callable[[], list[dict[str, Any]]] = _empty_events
    get_all_events: Callable[[], list[dict[str, Any]]] = _empty_events
    get_latest_checkpoint_covered_until_event_id: Callable[
        [], int | None
    ] = _no_checkpoint


@dataclass(frozen=True)
class PlaygroundRuntimePorts:
    event_sink: EventSink | None = None
    checkpoint_sink_factory: CheckpointSinkFactory | None = None
    compaction_history: CompactionHistoryPort = field(
        default_factory=CompactionHistoryPort
    )
    pre_compaction_barrier: PreCompactionBarrier | None = None


@dataclass(frozen=True)
class KernelRuntimePorts:
    checkpoint_sink: CheckpointSink | None = None
    pre_compaction_barrier: PreCompactionBarrier | None = None
```

`PlaygroundRuntimePorts` 面向 `Exp`，保留 `checkpoint_sink_factory`，因为 `Exp.build_runtime(..., spawn_id=...)` 才知道当前 runtime 对应 root run 还是 child run。

`KernelRuntimePorts` 面向 `AgentKernel`，只放已经解析好的 `checkpoint_sink` 和 `pre_compaction_barrier`，kernel 不需要知道 factory 或服务层状态。

## 数据流

目标流转如下：

```text
src/services/agent_run_service.py
  构造 PlaygroundRuntimePorts
  注入 PlaygroundContext.runtime_ports

        |
        v

matmaster/core/exp.py
  从 ctx.runtime_ports 读取 compaction history、event sink、checkpoint factory、barrier
  创建现有 HookExecutor
  解析 checkpoint_sink_factory(spawn_id=...)
  构造 KernelRuntimePorts

        |
        v

matmaster/types/runtime.py
  AgentRuntimeSpec.runtime_ports = KernelRuntimePorts(...)
  AgentRuntimeSpec.hook_executor = HookExecutor(...)

        |
        v

matmaster/core/agent.py
  从 spec.runtime_ports 读取 checkpoint_sink 和 pre_compaction_barrier
  从 spec.hook_executor 发射 RUN_START、RUN_END、USER_PROMPT_SUBMIT、CONTEXT_COMPACTION 等事件
```

这样，服务层能力注入和事件扩展系统在 `Exp.build_runtime()` 汇合，但不会混为一个抽象。

## 组件设计

### PlaygroundContext

在 `matmaster/types/context.py` 中新增字段：

```python
runtime_ports: PlaygroundRuntimePorts = Field(
    default_factory=PlaygroundRuntimePorts,
    repr=False,
    exclude=True,
)
```

并新增不可变更新方法：

```python
def with_runtime_ports(
    self,
    runtime_ports: PlaygroundRuntimePorts,
) -> "PlaygroundContext":
    return self.model_copy(update={"runtime_ports": runtime_ports})
```

`exclude=True` 是必要约束，因为 `runtime_ports` 承载 callback，不应进入 `model_dump()` 或 JSON dump。

### AgentRunService

`AgentRunService` 仍然负责构造实际服务能力。当前写入 `run_meta` 的这些 callback：

- `event_sink`
- `checkpoint_sink_factory`
- `get_query_events`
- `get_all_events`
- `get_latest_checkpoint_covered_until_event_id`
- `pre_compaction_barrier`

应改为构造 `PlaygroundRuntimePorts`：

```python
runtime_ports = PlaygroundRuntimePorts(
    event_sink=_child_event_sink,
    checkpoint_sink_factory=_checkpoint_sink_factory,
    compaction_history=CompactionHistoryPort(
        get_query_events=_get_query_events,
        get_all_events=_get_all_events,
        get_latest_checkpoint_covered_until_event_id=(
            _get_latest_checkpoint_covered_until_event_id
        ),
    ),
    pre_compaction_barrier=fanout.flush_persistence_barrier,
)
pg_ctx = pg_ctx.with_runtime_ports(runtime_ports)
```

第一阶段保留旧 `run_meta` 写入或 fallback，以便测试和 devshell 分步迁移。完成全量迁移后删除旧 key。

### Exp

`Exp.build_runtime()` 负责将 `PlaygroundRuntimePorts` 转换为 `KernelRuntimePorts`。

Compaction rehydrator 优先使用：

```python
history_port = ctx.runtime_ports.compaction_history
```

旧 `run_meta["get_query_events"]` 等字段作为兼容 fallback。fallback 只存在于迁移期，后续删除。

`Exp._make_spawn_fn()` 当前从 `ctx.run_meta["event_sink"]` 读取 child event sink。迁移后优先使用：

```python
event_sink = ctx.runtime_ports.event_sink
```

`Exp.build_runtime()` 当前把 `checkpoint_sink` 和 `pre_compaction_barrier` 塞进 `spec.meta`。迁移后应构造：

```python
checkpoint_sink = None
factory = ctx.runtime_ports.checkpoint_sink_factory
if factory is not None:
    checkpoint_sink = factory(spawn_id=spawn_id)

kernel_ports = KernelRuntimePorts(
    checkpoint_sink=checkpoint_sink,
    pre_compaction_barrier=ctx.runtime_ports.pre_compaction_barrier,
)
```

然后写入 `AgentRuntimeSpec.runtime_ports`。

### AgentRuntimeSpec

在 `matmaster/types/runtime.py` 中新增字段：

```python
runtime_ports: KernelRuntimePorts = Field(
    default_factory=KernelRuntimePorts,
    repr=False,
    exclude=True,
)
```

`meta` 保持用于被动 metadata：

- `task_id`
- `session_id`
- `spawn_id`
- `attachment_manifest`
- `current_user_images`

`meta` 不再作为 callback 传递通道。

### AgentKernel

`AgentKernel` 改为从 `spec.runtime_ports` 读取必要端口：

```python
pre_compaction_barrier = spec.runtime_ports.pre_compaction_barrier
checkpoint_sink = spec.runtime_ports.checkpoint_sink
```

`spec.hook_executor` 保持现有事件 hook 语义。

### HookExecutor bridge

本设计不要求第一阶段实现 service-level hook 注册桥，但预留扩展方向：

```python
HookRegistrar = Callable[[HookExecutor], None]
```

未来可以在 `PlaygroundRuntimePorts` 中加入：

```python
hook_registrars: tuple[HookRegistrar, ...] = ()
```

然后 `Exp.build_runtime()` 创建 `HookExecutor()` 后调用：

```python
for register_hooks in ctx.runtime_ports.hook_registrars:
    register_hooks(hook_executor)
```

这允许服务层启用已有 `HookExecutor`，例如注册 metrics、audit、policy、prompt rewrite handler。但它不改变 `RuntimePorts` 对必要能力端口的职责。

## 为什么不直接复用 HookExecutor 承载所有能力

`HookExecutor` 是事件系统，适合可选扩展。`RuntimePorts` 是端口系统，适合必要能力。二者失败语义不同。

例如：

- `HookExecutor.emit()` 会吞掉 observer 异常并记录 warning，这适合 telemetry，不适合 `pre_compaction_barrier`。
- `get_all_events()` 需要同步返回历史事件列表，而 `emit()` 是 push-based 通知。
- `checkpoint_sink()` 需要返回 `covered_until_event_id`，且失败会影响 compaction durability，不应被 observer 语义吞掉。
- `event_sink` 转发的是 `BusEvent` 流，不是 `HookEvent` 上的 typed context。

因此，正确复用方式是保留 `HookExecutor` 作为事件扩展系统，并通过可选 registrar bridge 让服务层注册 handler，而不是把服务端口伪装成 hook handler。

## 迁移步骤

### 阶段 1：新增 RuntimePorts 类型

创建 `matmaster/types/runtime_ports.py`，定义：

- `CompactionHistoryPort`
- `PlaygroundRuntimePorts`
- `KernelRuntimePorts`
- `EventSink`
- `CheckpointSink`
- `CheckpointSinkFactory`
- `PreCompactionBarrier`

新增类型测试，验证默认端口可调用且返回空数据或 `None`。

### 阶段 2：扩展 PlaygroundContext

在 `PlaygroundContext` 增加 `runtime_ports` 字段和 `with_runtime_ports()` 方法。

测试要求：

- 默认构造时 `runtime_ports` 存在。
- `model_dump()` 不包含 `runtime_ports`。
- `with_runtime_ports()` 返回新实例，不修改原实例。

### 阶段 3：服务层注入 RuntimePorts

在 `AgentRunService.run_agent_sync` 中构造 `PlaygroundRuntimePorts`，把当前 callback 注入 `pg_ctx.runtime_ports`。

迁移期可以保留旧 `run_meta` callback key，但新增测试应优先断言 `runtime_ports`。

### 阶段 4：Exp 优先消费 RuntimePorts

修改 `Exp._make_spawn_fn()`：

- 优先读 `ctx.runtime_ports.event_sink`。
- 迁移期 fallback 到 `ctx.run_meta.get("event_sink")`。

修改 `Exp.build_runtime()`：

- compaction history 优先来自 `ctx.runtime_ports.compaction_history`。
- checkpoint sink 优先来自 `ctx.runtime_ports.checkpoint_sink_factory`。
- pre compaction barrier 优先来自 `ctx.runtime_ports.pre_compaction_barrier`。

旧 `run_meta` callback 仅作为 fallback。

### 阶段 5：AgentRuntimeSpec 增加 KernelRuntimePorts

在 `AgentRuntimeSpec` 增加 `runtime_ports` 字段。

`Exp.build_runtime()` 解析 root/child `checkpoint_sink` 后，写入 `spec.runtime_ports`。

迁移期可以继续把 `checkpoint_sink`、`pre_compaction_barrier` 写入 `spec.meta`，但新增测试应断言 `runtime_ports`。

### 阶段 6：AgentKernel 消费 KernelRuntimePorts

修改 `AgentKernel`：

- `pre_compaction_barrier` 从 `spec.runtime_ports.pre_compaction_barrier` 读取。
- `checkpoint_sink` 从 `spec.runtime_ports.checkpoint_sink` 读取。

迁移期 fallback 到 `spec.meta`，后续删除。

### 阶段 7：迁移 devshell 与测试 helper

`matmaster/devshell/runner.py` 当前直接修改 `self._pg_ctx.run_meta["event_sink"]`。迁移后应使用 `with_runtime_ports()` 更新 context，避免 frozen model 内部可变 dict 绕过不可变语义。

相关测试从断言 `run_meta[...]` callback 改为断言 `runtime_ports`。

### 阶段 8：删除旧 callback key

确认所有生产路径、测试、devshell 都使用 `RuntimePorts` 后，删除以下旧 key 的读取和写入：

- `event_sink`
- `checkpoint_sink_factory`
- `get_query_events`
- `get_all_events`
- `get_latest_checkpoint_covered_until_event_id`
- `pre_compaction_barrier`
- `checkpoint_sink`

删除前使用：

```bash
rg -n "event_sink|checkpoint_sink_factory|get_query_events|get_all_events|get_latest_checkpoint_covered_until_event_id|pre_compaction_barrier|checkpoint_sink" matmaster src tests
```

确认剩余引用均为 `RuntimePorts` 或测试名称中的描述。

## 测试计划

### 类型合同测试

文件：

```text
tests/matmaster/types/test_runtime_ports.py
tests/matmaster/types/test_context.py
tests/matmaster/types/test_runtime.py
```

覆盖：

- 默认 `CompactionHistoryPort` 返回空事件和 `None` checkpoint。
- `PlaygroundContext.runtime_ports` 默认存在。
- `PlaygroundContext.model_dump()` 不包含 `runtime_ports`。
- `AgentRuntimeSpec.runtime_ports` 默认存在。
- `AgentRuntimeSpec.model_dump()` 不包含 `runtime_ports`。

### Exp wiring 测试

文件：

```text
tests/matmaster/core/test_hook_wiring.py
tests/matmaster/core/test_agent_kernel_compaction.py
tests/matmaster/core/test_exp_runtime_v2.py
```

覆盖：

- `Exp.build_runtime()` 将 `ctx.runtime_ports.checkpoint_sink_factory(spawn_id=...)` 解析为 `spec.runtime_ports.checkpoint_sink`。
- root run 和 child run 使用不同 `spawn_id` 时能拿到不同 sink。
- compaction rehydrator 使用 `ctx.runtime_ports.compaction_history`。
- `AgentKernel` 调用 `spec.runtime_ports.pre_compaction_barrier` 后再执行 compaction。

### 服务层测试

文件：

```text
tests/matmaster/services/test_agent_run_stream.py
tests/matmaster/services/test_agent_run_stream_response_figures.py
```

覆盖：

- `AgentRunService` 注入 `PlaygroundRuntimePorts`。
- child event sink 仍能转发 child tool result 和 response event。
- response figures 累积逻辑不因 event sink 迁移而丢失。
- attachment manifest 仍在 `run_meta` 或 `spec.meta` 的被动 metadata 流中可用。

### 兼容性测试

迁移期保留旧 `run_meta` fallback 时，增加测试证明：

- 旧式 `run_meta["get_all_events"]` 仍可被 `Exp` 使用。
- 新式 `ctx.runtime_ports.compaction_history.get_all_events` 优先级高于旧 key。

删除 fallback 的提交中，同步删除这些兼容性测试。

## 错误处理语义

`RuntimePorts` 中的必要能力不应默认吞掉异常。具体规则：

- `pre_compaction_barrier` 抛出的异常应向上冒泡，避免 compaction 在持久化未完成时继续执行。
- `checkpoint_sink` 抛出的异常保持当前 kernel 语义：记录 warning，并把 compaction result 标记为 checkpoint 失败。
- `CompactionHistoryPort` 的具体实现是否降级由注入方决定；如果服务层选择捕获异常并返回空列表，需要在注入函数旁写明原因。
- `HookExecutor` 继续保持 observer 异常只记录 warning 的语义。

这与项目级约定一致：DAO 和服务层默认不吞异常，确有降级需求时在调用处或本层写明原因。

## 兼容性与风险

### 风险：命名混乱

如果新增字段命名为 `runtime_hooks`，会与已有 `HookExecutor` 形成双 hook 体系。设计明确使用 `RuntimePorts` 避免这个问题。

### 风险：Pydantic dump 序列化运行时对象

`runtime_ports` 必须使用 `exclude=True`。测试必须覆盖 `model_dump()` 不包含 callback 对象。

### 风险：迁移期双写导致来源不一致

迁移期若同时写 `run_meta` 和 `runtime_ports`，`Exp` 必须规定优先级：`runtime_ports` 优先，旧 `run_meta` fallback 仅用于兼容。

### 风险：checkpoint / barrier 失败语义被误改

不能把 `pre_compaction_barrier` 改成 `HookExecutor.emit()`，因为 `emit()` 会吞 observer 异常。barrier 是顺序屏障，必须保持 await 和异常传播。

### 风险：devshell 直接改 frozen model 内部 dict

devshell 当前直接写 `run_meta["event_sink"]`。迁移时应改成 `with_runtime_ports()`，顺便减少 frozen model 内部可变对象带来的语义漏洞。

## AGENTS.md 约定更新

实现本设计时，应补充项目约定：

```text
run_meta 只承载被动运行 metadata；服务能力 callback、sink、factory、barrier 等不可序列化运行时对象必须放入显式 RuntimePorts 字段。

HookExecutor 专指事件扩展系统，用于 observe/intercept/rewrite 运行过程事件；RuntimePorts 专指核心层依赖的外部能力端口。不得把需要返回业务数据或承担顺序屏障语义的服务端口伪装成 HookExecutor handler。
```

## 验收标准

- `PlaygroundContext` 有显式 `runtime_ports` 字段，且 dump 时排除。
- `AgentRuntimeSpec` 有显式 `runtime_ports` 字段，且 dump 时排除。
- `AgentRunService` 不再需要通过 `run_meta` 注入新的 callback 能力。
- `Exp` 优先消费 `ctx.runtime_ports`，旧 `run_meta` callback 只在迁移期作为 fallback。
- `AgentKernel` 优先消费 `spec.runtime_ports`，不再依赖 `spec.meta` 中的 callback。
- `HookExecutor` 保持现有事件扩展职责，不承载 history reader、checkpoint sink 或 barrier。
- 相关测试通过：

```bash
uv run pytest tests/matmaster/types/test_runtime_ports.py \
  tests/matmaster/types/test_context.py \
  tests/matmaster/types/test_runtime.py \
  tests/matmaster/core/test_hook_wiring.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/services/test_agent_run_stream.py
```

## 后续实施顺序建议

1. 先实现 `RuntimePorts` 类型和 context/spec 字段。
2. 再迁移 `AgentRunService` 和 `Exp` 的生产/消费逻辑。
3. 再迁移 `AgentKernel` 对 `checkpoint_sink` 与 `pre_compaction_barrier` 的读取。
4. 再迁移 devshell 和测试。
5. 最后删除旧 `run_meta` callback fallback，并更新 `AGENTS.md`。

这个顺序能保证每一步都有可测试的中间状态，避免一次性改动过大。
