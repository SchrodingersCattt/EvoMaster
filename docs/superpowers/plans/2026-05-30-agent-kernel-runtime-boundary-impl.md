# Agent Kernel Runtime Boundary 实施计划

> **For agentic workers:** 本计划实现 `docs/superpowers/specs/2026-05-30-agent-kernel-runtime-boundary-design.md`。
> 强制遵守 spec 第 4 节命名规范。不保留任何旧字段兼容。完成每个阶段后运行对应单测。
> **禁止** 向 `docs/` 目录做任何 git 提交。

**Goal:** 把 `AgentRuntimeSpec`（同时混装配置 + live resource + assembly 内部对象）拆成
`AgentKernelSpec`（纯配置）+ `AgentKernelResources`（live resource）+ `AgentKernelRuntime`
（二者组合），并把 context assembly 内部对象收束进 `ContextCompactor` / `ContextAssemblyRuntime`，
让 `AgentKernel` 只通过 `kernel_resources.compactor` 间接触发上下文重组。

**Architecture:** `Exp.build_runtime(ctx)` 一次性构造 `kernel_spec` 与 `kernel_resources`，组装
`AgentKernelRuntime`，返回 `AgentRuntime(kernel, kernel_runtime, cleanup)`。
`AgentKernel.run_stream(kernel_runtime, ...)` 在入口拆出 `kernel_spec` / `kernel_resources`，
helper 按真实依赖收窄参数。删除 `Exp.assemble()`。

**Tech Stack:** Python 3.10+（实际 .venv 为 3.13）、frozen dataclass、pytest、ruff（select=E,F,I,B,UP,SIM,C4；无 ARG）。

---

## 0. 命名总表（spec §4，强制）

| 概念 | 类型名 | 局部变量名 |
|---|---|---|
| 服务层输入 | `AgentRunContext` | `agent_run_ctx` / `ctx` |
| kernel 配置 | `AgentKernelSpec` | `kernel_spec` |
| kernel 资源 | `AgentKernelResources` | `kernel_resources` |
| kernel runtime | `AgentKernelRuntime` | `kernel_runtime` |
| Exp 返回 bundle | `AgentRuntime` | `runtime` |
| context 重组能力 | `ContextAssemblyRuntime` | `context_runtime` |

禁止变量名：`runtime_spec`、`agent_runtime_spec`、`resources_runtime`、`context_resources`、
`assembly_context`、`spec_runtime`。

## 1. 新类型契约（精确代码）

### `matmaster/types/runtime.py`

保留 `CompactionConfig`、`KernelResult`。删除 `AgentRuntimeSpec`（含 pydantic 校验器，校验逻辑随之消失，
这是 spec 选用 frozen dataclass 的有意取舍）。新增：

```python
@dataclass(frozen=True)
class AgentKernelSpec:
    system_prompt: str
    max_turns: int
    compaction: CompactionConfig
    run_identity: RunIdentity
    turn_input: TurnInput | None = None


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

imports 调整：移除 `SystemPromptBuilder`、`BaseModel/ConfigDict/Field/model_validator`（不再需要）。
保留 `TurnInput`、`RunIdentity`、`LLMProvider`、`KernelRuntimePorts`、`Callable`、`dataclass/field`、`Any`。
模块 docstring 更新为描述三类 kernel runtime 类型。

### `matmaster/types/__init__.py`

- `_LAZY_IMPORTS`：删除 `"AgentRuntimeSpec"`，新增 `"AgentKernelSpec"`、`"AgentKernelResources"`、
  `"AgentKernelRuntime"`（均指向 `matmaster.types.runtime`）。
- `TYPE_CHECKING` 块：`from .runtime import AgentRuntimeSpec, CompactionConfig` →
  `from .runtime import AgentKernelResources, AgentKernelRuntime, AgentKernelSpec, CompactionConfig`。
- `_RUNTIME_EXPORTS`：改为 `frozenset({"AgentKernelSpec", "AgentKernelResources", "AgentKernelRuntime", "CompactionConfig"})`。

## 2. Context Assembly 收束

### `matmaster/core/runtime_context_assembly.py`

新增 `ContextAssemblyRuntime`，改造 `RuntimeContextAssembly`，改造 `build_runtime_context_assembly`
签名（不再吃 `AgentRuntimeSpec`，改吃显式 `llm_provider` + `compaction`）。

```python
@dataclass(frozen=True)
class ContextAssemblyRuntime:
    assembler: ContextAssembler
    ports: ContextAssemblyPorts
    user_instructions: UserInstructions
    covered_until_provider: Callable[[], int | None]


@dataclass(frozen=True)
class RuntimeContextAssembly:
    context_runtime: ContextAssemblyRuntime | None = None
    compactor: ContextCompactor | None = None


def build_runtime_context_assembly(
    *,
    llm_provider: Any,
    compaction: CompactionConfig,
    ctx: AgentRunContext,
    skill_resolver: SkillResolver,
    spawn_id: str | None,
    logger: logging.Logger,
) -> RuntimeContextAssembly:
    if llm_provider is None:
        return RuntimeContextAssembly()
    history_port = ctx.request.ports.compaction.history or EmptySessionEventHistory()
    user_instructions = ctx.request.user_instructions or UserInstructions(
        text="", hash=hash_user_instructions(""), truncated=False
    )
    assembly_ports = ContextAssemblyPorts(
        session_events=history_port,
        session_jobs=_EmptySessionJobsPort(),
    )
    context_assembler = ContextAssembler(
        ports=assembly_ports,
        session_context_factory=build_session_context_factory(skill_resolver=skill_resolver),
        render_options=ContextRenderOptions(),
    )
    context_runtime = ContextAssemblyRuntime(
        assembler=context_assembler,
        ports=assembly_ports,
        user_instructions=user_instructions,
        covered_until_provider=history_port.latest_scope_event_id,
    )
    compactor = ContextCompactor(
        config=compaction,
        context_assembler=context_runtime.assembler,
        user_instructions=context_runtime.user_instructions,
        session_id=ctx.environment.session_id,
        spawn_id=spawn_id,
        runtime_covered_until_provider=context_runtime.covered_until_provider,
        event_sink=None,
        compaction_scope=f'{ctx.environment.metadata.task_id}:{spawn_id or "root"}',
    )
    return RuntimeContextAssembly(context_runtime=context_runtime, compactor=compactor)
```

imports：新增 `from matmaster.types.runtime import CompactionConfig`；删除
`from matmaster.types.runtime import AgentRuntimeSpec`。`RuntimeContextAssembly` 顶层不再暴露
`context_assembler` / `assembly_ports`（移入 `context_runtime`）。

## 3. Exp 装配（spec §5.4 六步，一次性）

### `matmaster/core/exp.py`

- 删除 `async def assemble(self, ctx)`（public 方法整段删除）。
- `build_runtime()` 去掉 `spec = await self.assemble(ctx)` 与末尾 `spec.model_copy(update={...})`。
- 顺序：解析纯计算字段 → 构造 registry/topology/catalog/hook/runner 等 live 对象 → 渲染
  `system_prompt`（局部变量）→ `build_runtime_context_assembly(...)` → 一次性构造 `kernel_spec`、
  `kernel_resources`、`kernel_runtime` → `return AgentRuntime(kernel=kernel, kernel_runtime=kernel_runtime, cleanup=...)`。
- `build_runtime_context_assembly` 调用改为传 `llm_provider=request.llm_provider, compaction=self._config.compaction`，
  不再传 `spec=`。
- kernel_spec / kernel_resources 构造（替换原 model_copy 块）：

```python
kernel_spec = AgentKernelSpec(
    system_prompt=system_prompt,
    max_turns=self._config.max_turns,
    compaction=self._config.compaction,
    run_identity=self._build_run_identity(ctx, spawn_id=spawn_id),
    turn_input=request.turn_input,
)
kernel_resources = AgentKernelResources(
    llm_provider=request.llm_provider,
    runtime_ports=KernelRuntimePorts(
        checkpoint_sink=checkpoint_sink,
        pre_compaction_barrier=pre_compaction_barrier,
    ),
    tool_runner=full_runner,
    tool_catalog=catalog,
    runtime_topology=topology,
    hook_executor=hook_executor,
    compactor=runtime_context.compactor,
    capability_policy=capability_policy,
    structural_validation=structural_validation,
)
kernel_runtime = AgentKernelRuntime(spec=kernel_spec, resources=kernel_resources)
kernel = AgentKernel()
return AgentRuntime(kernel=kernel, kernel_runtime=kernel_runtime, cleanup=self._run_cleanup_callbacks)
```

- `runtime_scope()`：`spec = runtime.spec` → `kernel_runtime = runtime.kernel_runtime`；
  cancel-token 注入：`catalog = getattr(kernel_runtime.resources, "tool_catalog", None)`。
- `run_stream()`：`runtime.kernel.run_stream(runtime.kernel_runtime, task, history=history, cancel_token=cancel_token)`。
- imports：`from matmaster.types.runtime import AgentKernelResources, AgentKernelRuntime, AgentKernelSpec, AgentRuntime`，
  删除 `AgentRuntimeSpec`。删除 `from matmaster.context.system_prompt import SystemPromptBuilder`？仍需要（渲染 system_prompt 用 `SystemPromptBuilder()`），保留。
- 模块/类 docstring 去掉 `assemble()` 描述。

## 4. Kernel 主循环

### `matmaster/core/kernel_items.py`

`_KernelState` 新增字段（供 `dispatch_tool_calls` 在收窄签名后读取当轮 usage）：

```python
turn_usage: dict[str, int] = dc_field(default_factory=dict)
```

### `matmaster/core/agent.py`

- `ensure_tool_definitions(kernel_resources, state)`：用 `kernel_resources.tool_catalog` /
  `kernel_resources.runtime_topology`。
- `AgentKernel.run_stream(self, kernel_runtime, task, history=None, cancel_token=None)`：入口
  `kernel_spec = kernel_runtime.spec; kernel_resources = kernel_runtime.resources`。
  `async with kernel_resources.llm_provider:`；hook 用 `kernel_resources.hook_executor`，
  identity 用 `kernel_spec.run_identity`。`_consume_and_yield` 调用 `self._run_items(kernel_spec, kernel_resources, task, history, cancel_token)`。
- `_run_items(self, kernel_spec, kernel_resources, task, history, cancel_token)`：
  - hook_executor=`kernel_resources.hook_executor`，session_id=`kernel_spec.run_identity.session_id`。
  - `turn_input = kernel_spec.turn_input`；system_prompt=`kernel_spec.system_prompt`。
  - `checkpoint_sink = kernel_resources.runtime_ports.checkpoint_sink`。
  - `ensure_tool_definitions(kernel_resources, state)`。
  - `run_preflight_compaction_if_needed(kernel_spec=kernel_spec, kernel_resources=kernel_resources, state=..., history=..., turn_input=..., checkpoint_sink=..., tool_definitions=...)`。
  - 循环条件 `state.turn < kernel_spec.max_turns`。
  - `run_runtime_compaction_if_needed(kernel_spec=kernel_spec, kernel_resources=kernel_resources, state=state, turn_usage=state.turn_usage, checkpoint_sink=..., tool_definitions=...)`。
  - LLM：`self._call_llm_streaming(kernel_spec, kernel_resources, api_messages, tool_defs, cancel_token=...)`。
  - 取得 response 后：`state.turn_usage = response.usage`（替代原 local `turn_usage`）。
  - `is_root_run = kernel_spec.run_identity.spawn_id is None`。
  - `if kernel_resources.compactor: kernel_resources.compactor.update_message_count(len(state.messages))`。
  - tool 分发：`dispatch_tool_calls(tool_calls=response.tool_calls, tool_runner=kernel_resources.tool_runner, max_turns=kernel_spec.max_turns, state=state, cancel_token=cancel_token)`。
  - 最终 `yield self._terminal(state, "max_turns")`。
  - 删除原 `turn_usage: dict[str, int] = {}` local（改用 `state.turn_usage`）；`ResponseEvent`/`AssistantStateEvent`
    的 `turn_usage=` 改成 `state.turn_usage`。
- `_call_llm_streaming(self, kernel_spec, kernel_resources, api_messages, tool_defs, *, cancel_token=None)`：
  转发 `call_llm_streaming(kernel_resources, api_messages, tool_defs, cancel_token=cancel_token)`
  （见 §5 决策：llm_stream helper 只吃 `kernel_resources`）。
- imports：`if TYPE_CHECKING: from matmaster.types.runtime import AgentKernelRuntime`（用于类型注解）。

## 5. Helper 收窄（spec §8 阶段三 / §9.5）

### `matmaster/core/agent_tool_dispatch.py`

```python
async def dispatch_tool_calls(
    *,
    tool_calls: Sequence[ToolCallData],
    tool_runner: Any,
    max_turns: int,
    state: _KernelState,
    cancel_token: CancellationToken | None,
) -> AsyncIterator[_KernelItem]:
    if tool_runner is None:
        raise RuntimeError("No tool_runner in kernel resources")
    exec_ctx = ToolExecutionContext(turn=state.turn, max_turns=max_turns, cancel_token=cancel_token)
    runner_results = await tool_runner.execute_batch(tool_calls, exec_ctx)
    turn_index = state.turn - 1
    for tc, tool_result in runner_results:
        state.messages.append(ToolMessage(...))
        yield _KernelItem(event=ToolResultEvent(
            ...,
            turn_index=turn_index,
            turn_usage=state.turn_usage,
            total_usage=state.total_usage,
        ))
        # Skill -> SkillHitEvent 分支保持不变
```

- 参数集合恒等于 `{tool_calls, tool_runner, max_turns, state, cancel_token}`（§9.5 硬条件）。
- 删除 `AgentRuntimeSpec` import 与 `spec=`、`turn_usage=`、`turn_index=` 参数。

### `matmaster/core/agent_compaction.py`

`run_compaction_plan`、`run_preflight_compaction_if_needed`、`run_runtime_compaction_if_needed`
均把 `spec=` 拆成 `kernel_spec=` + `kernel_resources=`：

- `run_compaction_plan(*, kernel_spec, kernel_resources, state, plan, checkpoint_sink, turn_input=None, tool_definitions=None)`
  - `kernel_resources.runtime_ports.pre_compaction_barrier`
  - `call_summary_llm(llm_provider=kernel_resources.llm_provider, system_prompt=kernel_spec.system_prompt, ..., context_limit=kernel_spec.compaction.context_limit, reserved_summary_tokens=kernel_spec.compaction.reserved_summary_tokens)`
  - `kernel_resources.compactor.apply_summary(...)` / `apply_fallback(...)`
  - `kernel_resources.hook_executor`
- `run_preflight_compaction_if_needed(*, kernel_spec, kernel_resources, state, history, turn_input, checkpoint_sink, tool_definitions=None)`
  - `if not kernel_resources.compactor: return`；`plan_preflight_compaction` 取自 `kernel_resources.compactor`；
    调用 `run_compaction_plan(kernel_spec=kernel_spec, kernel_resources=kernel_resources, ...)`。
- `run_runtime_compaction_if_needed(*, kernel_spec, kernel_resources, state, turn_usage, checkpoint_sink, tool_definitions=None)`
  - 同上，`plan_runtime_compaction` 取自 `kernel_resources.compactor`。
- 删除 `AgentRuntimeSpec` import。

### `matmaster/core/agent_llm_stream.py`（决策：只吃 `kernel_resources`）

> **决策点（实施者注意）**：spec §8 阶段三写"接收 kernel_spec 与 kernel_resources"，但这两个
> helper 实际只需要 provider。按 spec §2/§10 的"依赖最小化"总原则，最小可用面是只吃
> `kernel_resources`。§9.5 硬条件只要求"provider 走 kernel_resources.llm_provider、不存在
> kernel_runtime 透传"，本决策满足。surface 测试断言 `kernel_resources` ∈ 参数、`kernel_runtime`/`spec` ∉ 参数。

```python
async def stream_llm_items(kernel_resources, api_messages, tool_defs, *, timeout=None, cancel_token=None): ...
async def call_llm_streaming(kernel_resources, api_messages, tool_defs, *, cancel_token=None): ...
```

- `provider = kernel_resources.llm_provider`；`spec.llm_provider.chat_stream` → `kernel_resources.llm_provider.chat_stream`。
- 删除 `AgentRuntimeSpec` import。

## 6. 非测试消费点迁移

- `matmaster/devshell/runner.py:~166`：`runtime.kernel.run_stream(runtime.spec, ...)` → `runtime.kernel.run_stream(runtime.kernel_runtime, ...)`。
- `matmaster/devshell/repl.py:~218`：`catalog = runtime.spec.tool_catalog` → `catalog = runtime.kernel_runtime.resources.tool_catalog`。
- `evaluation/core/mat_runner.py:192`：`runtime.kernel.run_stream(runtime.spec, prompt)` → `runtime.kernel.run_stream(runtime.kernel_runtime, prompt)`。
- docstring/注释顺手更新（`matmaster/types/run_metadata.py:9`、`matmaster/types/topology.py:76`、
  `matmaster/core/playground.py:141`）：把 `AgentRuntimeSpec`/`Exp.assemble()` 字样改为新命名。

## 7. 共享测试基建

### `tests/matmaster/core/agent_kernel_test_helpers.py`

把 `make_runtime_spec(...) -> AgentRuntimeSpec` 改名为 `make_kernel_runtime(...) -> AgentKernelRuntime`：
内部构造 `AgentKernelSpec` + `AgentKernelResources` + `AgentKernelRuntime`。
保留同样的关键字参数（responses/tools/max_turns/system_prompt/compaction/compactor/tool_runner/
tool_catalog/hook_executor/run_identity/turn_input/runtime_ports）。`system_prompt_builder` 参数删除。
`run_identity` 缺省时给 `RunIdentity()`；`runtime_ports` 缺省 `KernelRuntimePorts()`；
`llm_provider` 用 `StubLLMProvider(responses)`。删除 `AgentRuntimeSpec` import，改 import 新类型 +
`RunIdentity` + `KernelRuntimePorts`。

### `tests/matmaster/core/conftest.py`

`make_dummy_spec_kwargs` 注释里 `AgentRuntimeSpec` 字样更新；若其返回字段名含已删字段则同步。

## 8. 测试迁移配方（适用于所有受影响测试文件）

统一替换规则：

1. `from matmaster.types.runtime import AgentRuntimeSpec[, CompactionConfig]` →
   按需 `from matmaster.types.runtime import AgentKernelRuntime, AgentKernelSpec, AgentKernelResources, CompactionConfig`。
2. 直接构造 `AgentRuntimeSpec(...)`：
   - 纯配置字段（system_prompt/max_turns/compaction/run_identity/turn_input）→ `AgentKernelSpec(...)`。
   - live 字段（llm_provider/tool_runner/tool_catalog/runtime_topology/hook_executor/compactor/
     runtime_ports/capability_policy/structural_validation）→ `AgentKernelResources(...)`。
   - 组成 `AgentKernelRuntime(spec=..., resources=...)`。
   - 删除 `system_prompt_builder=` kwarg。
   - 测 helper 优先用 `make_kernel_runtime(...)`。
3. `kernel.run_stream(spec, ...)` → `kernel.run_stream(kernel_runtime, ...)`。
4. `runtime.spec.X` →
   - 配置字段 `X∈{system_prompt,max_turns,compaction,run_identity,turn_input}` → `runtime.kernel_runtime.spec.X`。
   - live 字段 → `runtime.kernel_runtime.resources.X`。
5. `runtime.spec = MagicMock(tool_catalog=...)` → `runtime.kernel_runtime = MagicMock(resources=MagicMock(tool_catalog=...))`
   （或构造真实 `AgentKernelRuntime`）。
6. `AgentRuntime(kernel=..., spec=..., cleanup=...)` → `AgentRuntime(kernel=..., kernel_runtime=..., cleanup=...)`。
7. `exp.assemble(ctx)` 相关 test：删除/改写为 `build_runtime` 行为测试（assemble 已不存在）。
8. context assembly 内部断言（`runtime.spec.context_assembler` / `session_events_port` / `session_jobs_port` /
   `system_prompt_builder`）→ 删除或改为行为测试（触发压缩仍能 assemble）。
9. helper 调用点（`dispatch_tool_calls`/`run_compaction_plan`/`run_*_compaction_if_needed`/
   `call_llm_streaming`/`stream_llm_items`）→ 按 §4/§5 新签名传参。

受影响测试文件清单（逐个迁移 + 运行）：

- `tests/matmaster/test_runtime_spec.py` → **替换**为 AgentKernelSpec/Resources 字段测试 + 边界测试（§9.1/§5.2）。
- `tests/matmaster/types/test_runtime.py` → 大改：AgentKernelSpec/Resources/Runtime + AgentRuntime(kernel_runtime) frozen 测试。
- `tests/matmaster/context/sources/test_turn_input_imports.py` → 子进程 import 串改 `AgentKernelSpec, CompactionConfig`。
- `tests/matmaster/core/test_runtime_context_assembly.py` → 改 `build_runtime_context_assembly(llm_provider=..., compaction=...)`，断言 `result.context_runtime` / `result.compactor`。
- `tests/matmaster/core/test_agent_compaction.py` → `_spec` 改 `_kernel_spec`+`_kernel_resources`；helper 新签名。
- `tests/matmaster/core/test_kernel_helpers.py` → `ensure_tool_definitions(kernel_resources,...)`、`dispatch_tool_calls` 新签名。
- `tests/matmaster/core/test_agent_kernel_stream.py` / `test_hook_wiring.py` / `test_agent_kernel_compaction.py` /
  `test_agent_kernel_empty_response_sentinels.py` / `test_agent_kernel_finish_diagnostics.py` /
  `test_agent_kernel_protocol_guardrails.py` / `test_agent_kernel_usage_events.py` → 用 `make_kernel_runtime` + `run_stream(kernel_runtime)`。
- `tests/matmaster/core/test_exp.py` → 删 assemble 测试；`runtime.spec.X` → `runtime.kernel_runtime.{spec,resources}.X`；MagicMock 改 `kernel_runtime`。
- `tests/matmaster/core/test_exp_runtime_v2.py` → 全量 `runtime.spec.X` 迁移；新增 §9.2 wiring + §9.6 surface + §5.2 边界测试。
- `tests/matmaster/integration/test_e2e_mat_master.py` / `test_e2e_minimal.py` / `test_pipeline_alignment.py` /
  `test_tool_protocol_guardrails.py` → `run_stream(runtime.kernel_runtime / kernel_runtime)`，`runtime.spec.tool_catalog` → `.kernel_runtime.resources.tool_catalog`。
- `tests/matmaster/devshell/test_runner.py` / `test_repl.py` / `test_devshell_mcp_skill_filter.py` →
  `runtime.spec` → `runtime.kernel_runtime`（含 MagicMock 结构 `resources.tool_catalog`）。
- `tests/matmaster/devshell/test_compaction_via_devshell.py` → `exp.assemble(ctx)` 改 `build_runtime` 取值。
- `tests/matmaster/services/agent_run_stream_fixtures.py` → `runtime.spec = spec` → `runtime.kernel_runtime = kernel_runtime`。

## 9. 新增测试（spec §9.1/§9.2/§9.5/§9.6/§5.2 边界）

- 类型边界：AgentKernelSpec 仅配置字段；AgentKernelResources 含 live 字段；AgentKernelRuntime.spec/.resources 命名稳定；不存在 `AgentRuntimeSpec`。
- Exp wiring：`build_runtime().kernel_runtime` 可用；`resources.compactor` 非空；`spec.run_identity` 来自 environment；`spec.turn_input` 来自 request.turn_input。
- 边界：`not hasattr(runtime.kernel_runtime.spec, "context_assembler"/"session_events_port"/"session_jobs_port"/"system_prompt_builder")`。
- helper surface（`inspect.signature`）：
  - `dispatch_tool_calls` 参数集合恒等于 `{tool_calls, tool_runner, max_turns, state, cancel_token}`，无 `kernel_runtime`/`spec`。
  - `run_compaction_plan` 同时含 `kernel_spec` 与 `kernel_resources`，无 `kernel_runtime`。
  - `call_llm_streaming` / `stream_llm_items` 含 `kernel_resources`，无 `kernel_runtime`/`spec`。
  - `Exp` 公共 surface 恒等于 `{build_runtime, runtime_scope, run_stream}`；`hasattr(Exp, "assemble") is False`。

## 10. 验证命令

```bash
.venv/bin/python -m pytest tests/matmaster/types/test_runtime.py tests/matmaster/test_runtime_spec.py -q
.venv/bin/python -m pytest tests/matmaster/core -q
.venv/bin/python -m pytest tests/matmaster/integration tests/matmaster/devshell tests/matmaster/services -q
.venv/bin/python -c "import matmaster.core.exp, matmaster.core.agent, matmaster.core.agent_compaction, matmaster.core.agent_tool_dispatch, matmaster.core.agent_llm_stream, matmaster.core.runtime_context_assembly, evaluation.core.mat_runner"
.venv/bin/ruff check matmaster/ tests/matmaster/ evaluation/core/mat_runner.py
grep -rn "AgentRuntimeSpec" matmaster/ src/ evaluation/ tests/   # 期望仅注释/无
```

## 11. 验收（spec §10）

- [ ] `AgentKernel.run_stream()` 不再接收 `AgentRuntimeSpec`。
- [ ] 代码库无 `AgentRuntimeSpec` 类型引用。
- [ ] `AgentRuntime` 字段名为 `kernel_runtime`。
- [ ] `AgentKernelRuntime.spec` 只含配置；`.resources` 含 provider/tool_runner/catalog/hooks/compactor/runtime_ports。
- [ ] 顶层不暴露 context assembly internals。
- [ ] 压缩 / checkpoint 行为不变（durable 写 `history_checkpoint.v1`，fallback 不写）。
- [ ] 变量名遵守 §0 命名表。
- [ ] 测试不通过旧字段验证 wiring。
- [ ] `Exp.assemble()` 删除；surface = `{build_runtime, runtime_scope, run_stream}`。
- [ ] §9 全部 surface 测试通过；阶段三 helper 不接 `kernel_runtime`。
