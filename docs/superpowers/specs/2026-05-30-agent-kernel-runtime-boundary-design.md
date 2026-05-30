# Agent Kernel Runtime Boundary 设计

- Date: 2026-05-30
- Status: Draft, 待审阅
- Author: Kealdoom + Codex
- 影响范围:
  - `matmaster/types/runtime.py`
  - `matmaster/core/exp.py`
  - `matmaster/core/agent.py`
  - `matmaster/core/agent_compaction.py`
  - `matmaster/core/agent_tool_dispatch.py`
  - `matmaster/core/runtime_context_assembly.py`
  - 相关 runtime spec、Exp、kernel、compaction 测试

## 1. 背景

当前运行链路已经形成两层对象：

- `AgentRunContext` 是服务层传给 `Exp` 的输入，包含 `ExecutionEnvironment`
  与 `AgentRunRequest`。
- `AgentRuntimeSpec` 是 `Exp` 构造后传给 `AgentKernel` 的运行对象。

概念上这两层边界是合理的。`AgentRunContext` 描述本轮运行从哪里来、运行环境是什么、
服务层已经解析出了哪些输入与能力端口；`AgentRuntimeSpec` 应该描述 kernel 已经可以直接
执行的运行时。

问题在于当前 `AgentRuntimeSpec` 同时承担了三类职责：

- kernel 直接需要的配置，例如 `system_prompt`、`max_turns`、`run_identity`。
- kernel 直接调用的运行资源，例如 `llm_provider`、`tool_runner`、`tool_catalog`、
  `hook_executor`、`compactor`。
- context assembly 的中间装配对象，例如 `context_assembler`、
  `session_events_port`、`session_jobs_port`、`user_instructions_port`、
  `system_prompt_builder`。

第三类对象存在是有原因的。agent 运行过程中如果触发压缩，需要重新组装上下文；
`ContextCompactor` 必须能通过 `ContextAssembler` 与 session event port 读取数据库或事件端口，
重新生成 compacted user message，并写入 `history_checkpoint`。

因此本设计不删除运行时上下文组装能力，而是调整它的归属：

```text
AgentKernel
  只知道压缩器可用

ContextCompactor
  持有上下文重组能力

ContextAssembler
  持有 session_events / session_jobs 端口
```

也就是说，压缩能力仍在，assembly 内部对象不再平铺在 kernel-facing runtime 顶层。

## 2. 目标

- 保留 `AgentRunContext` 与 kernel runtime 的双层边界，不合并二者。
- 将当前 `AgentRuntimeSpec` 改造成更清晰的 kernel-facing runtime。
- 把 context assembly 相关对象收束到 `ContextAssemblyRuntime` 与 `ContextCompactor`
  内部。
- 避免 `AgentKernel` 直接感知 `ContextAssembler`、session event port、session job port、
  user instructions loader 等装配细节。
- 明确新增类型和变量命名，避免 `spec`、`runtime`、`resources` 混用后语义漂移。
- 不保留兼容字段。项目仍处于开发阶段，采用直接迁移。

## 3. 非目标

- 不改变压缩算法本身。
- 不改变 `user_turn_context`、`history_checkpoint`、`context_compaction` 的持久化协议。
- 不重新设计 `ContextAssembler` 的 section composition 行为。
- 不让 `AgentKernel` 直接读取 DB、Redis 或服务层 DAO。
- 不在主代码里添加兼容兜底字段或旧字段 alias。
- 不把 `AgentRunContext` 直接传给 `AgentKernel`。

## 4. 命名规范

本次重构强制采用以下命名组。实现、测试、局部变量与文档必须保持一致。

| 概念 | 类型名 | 局部变量名 | 语义 |
|---|---|---|---|
| 服务层输入 | `AgentRunContext` | `agent_run_ctx` 或 `ctx` | `Exp` 的输入，不传给 kernel |
| kernel 配置 | `AgentKernelSpec` | `kernel_spec` | 纯配置与身份，不持有 live resource |
| kernel 资源 | `AgentKernelResources` | `kernel_resources` | provider、tools、hooks、compactor 等活对象 |
| kernel runtime | `AgentKernelRuntime` | `kernel_runtime` | `kernel_spec + kernel_resources` |
| Exp 返回 bundle | `AgentRuntime` | `runtime` | `kernel + kernel_runtime + cleanup` |
| context 重组能力 | `ContextAssemblyRuntime` | `context_runtime` | assembler、ports、frozen instructions、covered boundary provider |

禁止新增下列表达作为同义变量：

- `runtime_spec`
- `agent_runtime_spec`
- `resources_runtime`
- `context_resources`
- `assembly_context`
- `spec_runtime`

如果某个函数只需要配置，参数名用 `kernel_spec`。如果只需要活对象，参数名用
`kernel_resources`。如果需要二者，参数名用 `kernel_runtime`。

## 5. 目标架构

### 5.1 类型结构

`matmaster/types/runtime.py` 中新增三类 kernel runtime 类型：

```python
@dataclass(frozen=True)
class AgentKernelSpec:
    system_prompt: str
    max_turns: int
    compaction: CompactionConfig
    run_identity: RunIdentity
    turn_input: TurnInput | None = None
```

```python
@dataclass(frozen=True)
class AgentKernelResources:
    llm_provider: LLMProvider
    runtime_ports: KernelRuntimePorts
    tool_runner: Any
    tool_catalog: Any
    runtime_topology: Any
    hook_executor: Any | None = None
    compactor: Any | None = None
    capability_policy: Any | None = None
    structural_validation: Any | None = None
```

```python
@dataclass(frozen=True)
class AgentKernelRuntime:
    spec: AgentKernelSpec
    resources: AgentKernelResources
```

`AgentRuntime` 改为：

```python
@dataclass(frozen=True)
class AgentRuntime:
    kernel: Any
    kernel_runtime: AgentKernelRuntime
    cleanup: Callable[[], Any]
```

注意字段名使用 `kernel_runtime`，不使用 `spec`。这样调用点不会再写出
`runtime.spec.xxx` 这种把 runtime 与 spec 混在一起的表达。

### 5.2 Context Assembly Runtime

在 `matmaster/core/runtime_context_assembly.py` 中新增：

```python
@dataclass(frozen=True)
class ContextAssemblyRuntime:
    assembler: ContextAssembler
    ports: ContextAssemblyPorts
    user_instructions: UserInstructions
    covered_until_provider: Callable[[], int | None]
```

`RuntimeContextAssembly` 调整为：

```python
@dataclass(frozen=True)
class RuntimeContextAssembly:
    context_runtime: ContextAssemblyRuntime | None = None
    compactor: ContextCompactor | None = None
```

短期实现可以继续让 `ContextCompactor` 接收现有构造参数：

```python
ContextCompactor(
    config=kernel_spec.compaction,
    context_assembler=context_runtime.assembler,
    user_instructions=context_runtime.user_instructions,
    runtime_covered_until_provider=context_runtime.covered_until_provider,
    ...
)
```

本设计不要求第一阶段修改 `ContextCompactor` 构造签名。关键约束是：
`AgentKernelRuntime` 顶层不暴露 `context_runtime`、`assembler`、`session_events_port`、
`session_jobs_port`。

### 5.3 Kernel 依赖方向

目标依赖方向：

```text
AgentRunService
  -> AgentRunContext
  -> Exp.build_runtime(agent_run_ctx)
  -> AgentRuntime(kernel, kernel_runtime, cleanup)
  -> AgentKernel.run_stream(kernel_runtime, task, history, cancel_token)
```

运行时压缩方向：

```text
AgentKernel
  -> kernel_resources.compactor.apply_summary(...)
  -> ContextCompactor
  -> ContextAssembler.assemble_compaction(...)
  -> ContextAssemblyPorts.session_events.load_events(...)
```

`AgentKernel` 只知道 `compactor`，不知道 `ContextAssembler` 与 session event port。

## 6. 字段去向

当前 `AgentRuntimeSpec` 字段按以下规则迁移。

| 当前字段 | 新位置 | 理由 |
|---|---|---|
| `llm_provider` | `AgentKernelResources.llm_provider` | live resource |
| `max_turns` | `AgentKernelSpec.max_turns` | kernel 配置 |
| `hook_executor` | `AgentKernelResources.hook_executor` | live resource |
| `runtime_ports` | `AgentKernelResources.runtime_ports` | kernel 能力端口 |
| `compaction` | `AgentKernelSpec.compaction` | kernel 配置 |
| `system_prompt` | `AgentKernelSpec.system_prompt` | kernel 配置 |
| `compactor` | `AgentKernelResources.compactor` | live resource |
| `system_prompt_builder` | 删除，保留为 `Exp.build_runtime` 局部变量 | kernel 不需要 builder |
| `run_identity` | `AgentKernelSpec.run_identity` | kernel 身份 |
| `turn_input` | `AgentKernelSpec.turn_input` | preflight compaction 与图片输入需要 |
| `context_assembler` | 不暴露，收束到 `ContextCompactor` 内部 | assembly detail |
| `user_instructions_port` | 删除，不进入 kernel runtime | 本轮使用 frozen `UserInstructions` |
| `session_events_port` | 不暴露，收束到 `ContextAssembler` 内部 | assembly detail |
| `session_jobs_port` | 不暴露，收束到 `ContextAssembler` 内部 | assembly detail |
| `tool_runner` | `AgentKernelResources.tool_runner` | live resource |
| `tool_catalog` | `AgentKernelResources.tool_catalog` | live resource |
| `runtime_topology` | `AgentKernelResources.runtime_topology` | live resource/config hybrid |
| `capability_policy` | `AgentKernelResources.capability_policy` | live resource |
| `structural_validation` | `AgentKernelResources.structural_validation` | live resource |

## 7. 关键行为约束

### 7.1 User instructions 在单轮内冻结

`AgentRunContext.request.user_instructions` 是本轮 run 的冻结输入。运行中触发压缩时，
`ContextCompactor` 使用这个 frozen value，不重新通过 `user_instructions_port`
读取 AGENT.md。

原因：

- 同一轮执行中用户指令不能前后变化。
- 压缩 checkpoint 的 `user_instructions_hash` 必须对应本轮实际使用的指令。
- 重新读取 AGENT.md 会让同一 run 内的 prompt 约束变成时间相关状态。

### 7.2 Kernel 不展开 context assembly internals

`AgentKernel` 与 `agent_compaction` helper 不允许访问下列字段或对象：

- `context_assembler`
- `session_events_port`
- `session_jobs_port`
- `user_instructions_port`
- `ContextAssemblyRuntime`

它们只允许通过 `kernel_resources.compactor` 间接触发上下文重组。

### 7.3 Runtime 不使用旧字段兼容

本项目仍在开发阶段，本重构直接迁移调用点。禁止保留：

- `AgentRuntime.spec`
- `AgentRuntimeSpec`
- `runtime.spec.context_assembler`
- `runtime.spec.session_events_port`
- `runtime.spec.session_jobs_port`
- `runtime.spec.system_prompt_builder`

如果测试或调用点需要验证 context assembly wiring，应该验证行为，或验证
`ContextCompactor` 能完成压缩，而不是依赖顶层字段暴露。

## 8. 修改步骤

### 阶段一：引入新 runtime shape

修改 `matmaster/types/runtime.py`：

- 新增 `AgentKernelSpec`、`AgentKernelResources`、`AgentKernelRuntime`。
- 修改 `AgentRuntime` 字段为 `kernel_runtime`。
- 删除 `AgentRuntimeSpec` 定义、导出与所有引用，不保留旧类型别名。

修改 `matmaster/core/exp.py`：

- 保留 `assemble()`，返回 `AgentKernelSpec` 的基础配置。
- `build_runtime()` 构造 `kernel_spec`、`kernel_resources`、`kernel_runtime`。
- 返回 `AgentRuntime(kernel=kernel, kernel_runtime=kernel_runtime, cleanup=...)`。

### 阶段二：调整 kernel 调用

修改 `matmaster/core/agent.py`：

- `run_stream()` 参数从 `spec` 改为 `kernel_runtime`。
- 函数开头统一拆出：

```python
kernel_spec = kernel_runtime.spec
kernel_resources = kernel_runtime.resources
```

- 所有配置读取使用 `kernel_spec`。
- 所有 live resource 读取使用 `kernel_resources`。

示例：

```python
state = _KernelState(
    messages=[
        SystemMessage(content=kernel_spec.system_prompt),
        *(history or []),
        UserMessage(content=task, images=turn_images),
    ]
)
```

```python
tool_definitions = ensure_tool_definitions(kernel_resources, state)
```

### 阶段三：收窄 helper 参数

修改 `matmaster/core/agent_compaction.py`：

- `run_compaction_plan()` 接收 `kernel_spec` 与 `kernel_resources`。
- summary LLM 调用使用 `kernel_resources.llm_provider`。
- system prompt、compaction config 使用 `kernel_spec`。
- compactor 使用 `kernel_resources.compactor`。

修改 `matmaster/core/agent_tool_dispatch.py`：

- `dispatch_tool_calls()` 不再接收完整 runtime。
- 参数改为 `tool_runner` 与 `max_turns`。

修改 `matmaster/core/agent_llm_stream.py`：

- 第一轮迁移中接收 `kernel_spec` 与 `kernel_resources`。
- 需要 provider 的位置统一从 `kernel_resources.llm_provider` 读取。
- 需要 identity 的位置统一从 `kernel_spec.run_identity` 读取。

### 阶段四：收束 context assembly objects

修改 `matmaster/core/runtime_context_assembly.py`：

- 新增 `ContextAssemblyRuntime`。
- `build_runtime_context_assembly()` 构造 `context_runtime`。
- 构造 `ContextCompactor` 时继续传入 `context_runtime.assembler` 等现有参数。
- `RuntimeContextAssembly` 只对 `Exp.build_runtime()` 暴露 `compactor` 与
  `context_runtime`，但 `context_runtime` 不进入 `AgentKernelRuntime`。

### 阶段五：删除旧顶层字段测试依赖

更新测试：

- 删除对 `runtime.spec.context_assembler` 的断言。
- 删除对 `runtime.spec.session_events_port` 的断言。
- 删除对 `runtime.spec.session_jobs_port` 的断言。
- 改为行为测试：触发 preflight/runtime compaction，确认仍能通过 event port
  重组 session sections 与 checkpoint message。

新增边界测试：

```python
def test_kernel_runtime_does_not_expose_context_assembly_internals():
    runtime = ...
    assert not hasattr(runtime.kernel_runtime.spec, "context_assembler")
    assert not hasattr(runtime.kernel_runtime.spec, "session_events_port")
    assert not hasattr(runtime.kernel_runtime.spec, "session_jobs_port")
    assert not hasattr(runtime.kernel_runtime.spec, "system_prompt_builder")
```

## 9. 测试策略

需要覆盖四类风险。

### 9.1 类型边界测试

更新或替换 `tests/matmaster/test_runtime_spec.py`：

- 验证 `AgentKernelSpec` 只包含配置字段。
- 验证 `AgentKernelResources` 包含 live resource 字段。
- 验证 `AgentKernelRuntime.spec` 与 `.resources` 命名稳定。
- 验证不再存在旧 `AgentRuntimeSpec` 与旧顶层字段。

### 9.2 Exp wiring 测试

更新 `tests/matmaster/core/test_exp_runtime_v2.py`：

- `Exp.build_runtime()` 返回的 `AgentRuntime.kernel_runtime` 可被 kernel 使用。
- `kernel_runtime.resources.compactor` 非空。
- `kernel_runtime.spec.run_identity` 正确来自 `AgentRunContext.environment`。
- `kernel_runtime.spec.turn_input` 正确来自 `AgentRunContext.request.turn_input`。

### 9.3 Kernel 行为测试

更新 kernel 相关测试：

- 无工具自然完成路径不变。
- 工具调用路径仍 append assistant/tool messages。
- `ensure_tool_definitions()` 在 tool catalog version 变化时仍刷新。
- `dispatch_tool_calls()` 使用显式 `tool_runner` 与 `max_turns`。

### 9.4 Compaction 行为测试

更新 compaction 相关测试：

- preflight compaction 仍能拆分当前输入。
- runtime compaction 仍能通过 `ContextCompactor` 重新 assemble compacted context。
- durable summary 仍写入 `history_checkpoint.v1`。
- fallback sliding window 仍不写 checkpoint。

## 10. 验收标准

- `AgentKernel.run_stream()` 不再接收 `AgentRuntimeSpec`。
- 代码库中不再新增 `AgentRuntimeSpec` 类型引用。
- `AgentRuntime` 字段名为 `kernel_runtime`，不再是 `spec`。
- `AgentKernelRuntime.spec` 只包含配置字段，不包含 live resource。
- `AgentKernelRuntime.resources` 包含 provider、tool runner、catalog、hooks、
  compactor、runtime ports。
- `AgentKernelRuntime` 顶层不暴露 context assembly internals。
- 压缩行为与 checkpoint 行为保持不变。
- 所有新增变量名遵守本 spec 第 4 节命名规范。
- 测试不通过旧字段访问验证 wiring。

## 11. 备选方案

### 方案 A：只删除旧字段，不拆 `AgentRuntimeSpec`

优点：

- 改动小。
- 测试迁移少。

缺点：

- `AgentRuntimeSpec` 名字仍然不准确，因为它继续持有 live resources。
- `runtime.spec.xxx` 的表达仍然混淆 runtime 与 spec。
- 后续 helper 参数收窄不自然。

### 方案 B：直接把 `AgentRunContext` 传给 kernel

优点：

- 对象数量最少。
- 短期调用链简单。

缺点：

- kernel 会感知 `ExecutionEnvironment`、session、workdir 等服务层概念。
- `Exp.build_runtime()` 的装配边界消失。
- 后续工具、拓扑、压缩、事件端口更容易反向耦合。

本设计不采用。

### 方案 C：拆成 `AgentKernelSpec + AgentKernelResources + AgentKernelRuntime`

优点：

- 命名表达准确。
- kernel 配置与 live resources 分离。
- context assembly internals 可以自然下沉到 compactor 内部。
- 后续 helper 函数可以按真实依赖收窄参数。

缺点：

- 迁移调用点较多。
- 测试需要一次性更新旧字段访问。

本设计采用方案 C。

## 12. 后续实现原则

- 每个阶段完成后运行相关单测，避免一次性大爆炸。
- 不做旧字段兼容。
- 不加入迁移兜底逻辑。
- 不顺手重写压缩算法。
- 不把 `ContextAssemblyRuntime` 泄漏到 kernel-facing runtime 顶层。
- 所有新变量名按第 4 节命名表执行。
