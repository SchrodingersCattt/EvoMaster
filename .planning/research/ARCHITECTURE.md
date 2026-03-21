# Architecture Patterns

**Domain:** AI Agent Framework Refactoring (playground/exp/agent three-layer architecture)
**Researched:** 2026-03-21
**Confidence:** HIGH (based on codebase analysis + nanobot reference + established patterns)

## Recommended Architecture

三层重构的核心设计原则：每层只做一件事，层间通过类型化契约通信，能力通过依赖注入组装，事件通过 MessageBus 解耦。

```
                    ┌─────────────────────────────────────────────┐
                    │              Caller / Entry Point           │
                    │   (agent_run_service / run.py / test)       │
                    └──────────────────┬──────────────────────────┘
                                       │ creates
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │         Layer 1: Playground (Workspace)     │
                    │                                             │
                    │  Responsibility: Environment preparation    │
                    │  Output: PlaygroundContext (typed contract)  │
                    │                                             │
                    │  - Session setup (Local/Docker/SSH)          │
                    │  - Working directory creation                │
                    │  - MCP server initialization                 │
                    │  - Config loading & validation               │
                    │  - Environment variable preparation          │
                    │  - Skill file sync (SSH remote)              │
                    └──────────────────┬──────────────────────────┘
                                       │ passes PlaygroundContext
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │         Layer 2: Exp (Assembly)              │
                    │                                             │
                    │  Responsibility: Capability assembly        │
                    │  Input:  PlaygroundContext                   │
                    │  Output: AgentRuntimeSpec (typed contract)   │
                    │                                             │
                    │  - LLM provider selection & creation         │
                    │  - ToolRegistry assembly (builtin+MCP+skill) │
                    │  - ContextBuilder configuration              │
                    │  - Guard injection (business guards)         │
                    │  - Prompt template resolution                │
                    │  - Termination policy definition             │
                    │  - Solver mode routing (direct/planner)      │
                    └──────────────────┬──────────────────────────┘
                                       │ passes AgentRuntimeSpec
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │         Layer 3: Agent (Kernel)              │
                    │                                             │
                    │  Responsibility: Pure execution loop        │
                    │  Input:  AgentRuntimeSpec + task             │
                    │  Output: Events (via MessageBus)            │
                    │                                             │
                    │  - LLM call → tool execution → msg accumlate │
                    │  - Built-in guards (loop detection, max turn)│
                    │  - Context compaction (when triggered)       │
                    │  - Event emission (thought/tool_call/result) │
                    │  - Stop signal handling                      │
                    └──────────────────┬──────────────────────────┘
                                       │ emits events
                                       ▼
                    ┌─────────────────────────────────────────────┐
                    │              MessageBus                      │
                    │                                             │
                    │  - Typed event emission (AgentEvent union)   │
                    │  - Multiple subscriber support               │
                    │  - SSE bridge / persistence / logging        │
                    └─────────────────────────────────────────────┘
```

### Component Boundaries

| Component | Responsibility | Input | Output | Communicates With |
|-----------|---------------|-------|--------|-------------------|
| **Playground** | Environment preparation: session, workdir, MCP, config | Config path / overrides | `PlaygroundContext` | Caller (receives config), Exp (passes context) |
| **Exp** | Capability assembly: tools, LLM, guards, prompts | `PlaygroundContext` | `AgentRuntimeSpec` | Playground (receives context), Agent (passes spec) |
| **Agent (Kernel)** | Pure execution loop: LLM -> tool -> loop | `AgentRuntimeSpec` + `TaskInstance` | Events via `MessageBus` | Exp (receives spec), MessageBus (emits events) |
| **MessageBus** | Event routing and distribution | `AgentEvent` (from Agent) | Events to subscribers | Agent (receives events), SSE/persistence/logging subscribers |
| **ToolRegistry** | Tool storage and execution dispatch | Tool registration calls | `ToolSpec[]` for LLM, execution results | Exp (assembly), Agent (execution) |
| **LLMProvider** | LLM API abstraction | Messages + tools | `LLMResponse` | Agent (called during step) |
| **ContextBuilder** | System prompt assembly from multiple sources | Identity + skills + memory + task | System prompt string | Exp (configuration), Agent (prompt generation) |
| **GuardPipeline** | Pre/post tool call interception | Tool call info + observation | Allow/Block decision | Agent (built-in guards), Exp (injected business guards) |
| **CompatAdapter** | Bridge old playground implementations to new contracts | Old playground instance | `PlaygroundContext` + `AgentRuntimeSpec` | Old code (wraps), New pipeline (produces contracts) |

### Data Flow

**PlaygroundContext 的生命周期：**

```
Config YAML
    │
    ▼
Playground.setup()
    │
    ├── load config → validate schema
    ├── create session (Local/Docker/SSH)
    ├── setup workdir structure
    ├── init MCP servers (async event loop in thread)
    └── collect into PlaygroundContext
            │
            ▼
    PlaygroundContext {
        session: BaseSession,
        workdir: Path,
        config: ValidatedConfig,
        mcp_manager: MCPToolManager | None,
        skill_registry: SkillRegistry | None,
        env_vars: dict[str, str],
        run_metadata: RunMetadata,
    }
```

**AgentRuntimeSpec 的组装过程：**

```
PlaygroundContext
    │
    ▼
Exp.assemble(playground_ctx)
    │
    ├── select LLM provider from config → create_llm()
    ├── build ToolRegistry
    │     ├── register builtins (bash, editor, finish)
    │     ├── register MCP tools from playground_ctx.mcp_manager
    │     ├── register skill tools from playground_ctx.skill_registry
    │     └── register domain tools (memory, peek_file, web_search, ...)
    ├── configure ContextBuilder (prompt files, format kwargs)
    ├── assemble GuardPipeline
    │     ├── (built-in guards are Agent-internal, not here)
    │     └── inject business guards: manuscript gate, structure gate, ...
    ├── define TerminationPolicy (max_turns, finish_conditions)
    └── collect into AgentRuntimeSpec
            │
            ▼
    AgentRuntimeSpec {
        llm: LLMProvider,
        tools: ToolRegistry,
        context_builder: ContextBuilder,
        guards: list[Guard],
        termination: TerminationPolicy,
        compaction: CompactionConfig,
        hooks: AgentHooks,
    }
```

**Agent 执行循环中的数据流：**

```
AgentRuntimeSpec + TaskInstance
    │
    ▼
Agent.__init__(spec)      # consume spec, store components
    │
    ▼
Agent.run(task)
    │
    ├── context_builder.build_system_prompt()  → system_prompt
    ├── context_builder.build_user_prompt(task) → user_prompt
    ├── Dialog([system_msg, user_msg], tools=spec.tools.get_specs())
    │
    └── for turn in range(max_turns):
          │
          ├── context_manager.prepare_for_query(dialog) → trimmed_dialog
          ├── llm.chat(trimmed_dialog) → LLMResponse
          │     └── bus.emit(ThoughtEvent(...))
          │
          ├── if response.tool_calls:
          │     for tool_call in response.tool_calls:
          │         ├── guard_pipeline.pre_execute(tool_call) → allow/block
          │         │     └── bus.emit(ToolCallEvent(...))
          │         ├── tools.execute(tool_call) → observation
          │         │     └── bus.emit(ToolResultEvent(...))
          │         └── guard_pipeline.post_execute(tool_call, observation)
          │
          ├── if termination.should_stop(response, turn):
          │     └── bus.emit(FinishEvent(...))
          │     └── break
          │
          └── compaction_check(dialog)
                └── if triggered: compact & bus.emit(CompactionEvent)
```

## Typed Contracts: PlaygroundContext and AgentRuntimeSpec

### PlaygroundContext

Playground 层的唯一输出。包含 Agent 运行所需的环境信息，但不包含执行逻辑相关的配置。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RunMetadata(BaseModel):
    """运行级别的元信息"""
    run_id: str
    run_dir: Path
    task_id: str | None = None
    session_type: str  # "local" | "docker" | "ssh"
    playground_name: str  # "mat_master" | "minimal" | ...


class PlaygroundContext(BaseModel):
    """Playground 层输出的类型化契约

    包含 Agent 运行所需的全部环境信息。
    Exp 层消费此对象来组装 AgentRuntimeSpec。

    设计原则：
    - 只包含环境/基础设施信息，不包含执行策略
    - 所有字段都有明确类型，不使用 Dict[str, Any]
    - Immutable after creation (frozen=True in production)
    """
    session: Any  # BaseSession (避免循环导入，运行时用 TYPE_CHECKING)
    workdir: Path
    config: Any  # ValidatedConfig (Pydantic model from YAML)
    config_dir: Path
    mcp_manager: Any | None = None  # MCPToolManager
    skill_registry: Any | None = None  # SkillRegistry
    env_vars: dict[str, str] = Field(default_factory=dict)
    run_metadata: RunMetadata

    model_config = {"arbitrary_types_allowed": True}
```

**为什么用 Pydantic BaseModel 而不是 dataclass：**
- 与项目现有 Pydantic 生态一致
- 自带序列化/反序列化（调试、日志、持久化都方便）
- `model_config` 可以控制 frozen、json_encoders 等行为
- 验证逻辑可以用 `@field_validator` 声明式表达

**为什么某些字段用 Any：**
- `session`、`mcp_manager` 等对象来自 evomaster 内部，有复杂的继承关系
- 用 `TYPE_CHECKING` 做类型提示、运行时用 Any 是 Python 社区标准做法
- 未来可以为这些字段定义 Protocol 接口做更严格的约束

### AgentRuntimeSpec

Exp 层的唯一输出。Agent kernel 消费此对象，不需要知道配置从哪来、环境怎么搭。

```python
from __future__ import annotations
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field


class Guard(Protocol):
    """Guard 协议：pre/post tool execution 拦截"""
    def pre_execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[bool, str]:
        """返回 (allow, reason)。allow=False 时阻止执行。"""
        ...

    def post_execute(
        self, tool_name: str, args: dict[str, Any], observation: str
    ) -> None:
        """执行后回调，用于更新 guard 内部状态。"""
        ...


class TerminationPolicy(BaseModel):
    """终止策略"""
    max_turns: int = 100
    finish_tool_name: str = "finish"
    require_task_completed: bool = True


class AgentHooks(BaseModel):
    """Agent 生命周期钩子 (可选)"""
    on_step_start: Any | None = None  # Callable[[int], None]
    on_step_end: Any | None = None    # Callable[[int, bool], None]
    on_llm_token: Any | None = None   # Callable[[str], None]

    model_config = {"arbitrary_types_allowed": True}


class AgentRuntimeSpec(BaseModel):
    """Exp 层输出的类型化契约

    包含 Agent 执行所需的全部能力配置。
    Agent kernel 只消费此对象，不关心环境来源。

    设计原则：
    - 只包含执行所需的能力，不包含环境信息
    - Agent 不需要 workdir、session type 等环境细节
    - 所有能力通过依赖注入，Agent 不自行创建
    """
    llm: Any  # LLMProvider (BaseLLM)
    tools: Any  # ToolRegistry
    context_builder: Any  # ContextBuilder
    guards: list[Any] = Field(default_factory=list)  # list[Guard]
    termination: TerminationPolicy = Field(default_factory=TerminationPolicy)
    compaction: Any | None = None  # CompactionConfig
    hooks: AgentHooks = Field(default_factory=AgentHooks)
    # session 在这里是因为 tool 执行需要它（bash/editor 需要 session.execute）
    # 但 Agent 本身不应该直接操作 session
    session: Any = None  # BaseSession (for tool execution only)

    model_config = {"arbitrary_types_allowed": True}
```

**为什么 session 出现在 AgentRuntimeSpec 中：**
当前 tool 执行签名是 `tool.execute(session, args_json)`，session 是 tool 执行的运行时依赖。这属于 tool execution context，不是 agent 的环境信息。Agent 只是把 session 传递给 tool，自己不操作它。长期可以考虑让 ToolRegistry 持有 session 引用，从 spec 中移除。

## Dependency Injection of Capabilities

### 设计原则

不使用 DI 容器（如 python-dependency-injector），因为：
- 项目复杂度不需要容器级别的 DI
- 构造函数注入 + Protocol 接口已经足够
- 保持依赖链可读（看构造函数就知道需要什么）

采用构造函数注入模式 + Protocol 接口：

```python
# 1. 定义 Protocol（接口约束）
class LLMProviderProtocol(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...
    def chat_with_retry(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...

class ToolRegistryProtocol(Protocol):
    def get_tool(self, name: str) -> BaseTool | None: ...
    def get_tool_specs(self, names: list[str] | None = None) -> list[ToolSpec]: ...
    def execute(self, name: str, session: Any, args_json: str) -> tuple[str, dict]: ...

# 2. Agent kernel 只依赖 Protocol，不依赖具体类
class AgentKernel:
    def __init__(self, spec: AgentRuntimeSpec):
        # 所有依赖从 spec 中注入
        self.llm = spec.llm
        self.tools = spec.tools
        self.context_builder = spec.context_builder
        self.guards = spec.guards
        self.termination = spec.termination
        self.compaction = spec.compaction
        # ...

# 3. Exp 层负责组装 spec（工厂模式）
class MatMasterExp:
    def assemble(self, ctx: PlaygroundContext) -> AgentRuntimeSpec:
        llm = self._create_llm(ctx)
        tools = self._create_tool_registry(ctx)
        guards = self._create_guards(ctx)
        context_builder = self._create_context_builder(ctx)
        return AgentRuntimeSpec(
            llm=llm,
            tools=tools,
            context_builder=context_builder,
            guards=guards,
            session=ctx.session,
        )
```

### 能力注入链

```
Config YAML
    ↓ (parsed by ConfigManager)
PlaygroundContext
    ↓ (consumed by Exp)
Exp.assemble()
    ├── LLM: create_llm(LLMConfig) → BaseLLM instance
    ├── Tools: ToolRegistry()
    │     ├── .register(BashTool())
    │     ├── .register(EditorTool())
    │     ├── .register(FinishTool())
    │     ├── .register_many(mcp_tools)        # from ctx.mcp_manager
    │     ├── .register(SkillTool(ctx.skills))  # from ctx.skill_registry
    │     └── .register_many(domain_tools)      # memory, peek_file, etc.
    ├── Guards: [ManuscriptGuard(), StructureGuard(), ...]
    ├── ContextBuilder: configured with prompt paths + format kwargs
    └── AgentRuntimeSpec(llm, tools, guards, context_builder, ...)
            ↓ (consumed by Agent)
        AgentKernel(spec)
```

## Event Bus Design

### 为什么不直接沿用 callback

当前系统用 `event_callback: Callable[[str, str, Any], None]` 把事件从 Agent 传递到 Service 层。问题：
1. Agent 直接持有 callback reference，形成运行时耦合
2. callback 签名是 `(source, type, content, **extra)` —— 无类型安全
3. 无法多订阅者（只能有一个 callback）
4. Agent 内部的 StreamingMatMasterAgent 需要继承来注入 callback

### 推荐设计：Typed EventBus

参考 nanobot 的 MessageBus 但做关键增强：类型化事件 + 多订阅者 + 同步兼容。

```python
from __future__ import annotations
import asyncio
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Union


class EventType(str, Enum):
    """Agent 事件类型枚举"""
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINISH = "finish"
    ERROR = "error"
    CONTEXT_COMPACTION = "context_compaction"
    CONFIRMATION_REQUEST = "confirmation_request"


@dataclass
class AgentEvent:
    """Agent 事件基类"""
    event_type: EventType
    source: str           # agent name / "System"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ThoughtEvent(AgentEvent):
    """LLM 思考事件"""
    content: str = ""
    stream_state: str | None = None  # "start" | "streaming" | "end"
    stream_id: str | None = None
    reasoning: str | None = None     # extended thinking / reasoning content

    def __post_init__(self):
        self.event_type = EventType.THOUGHT


@dataclass
class ToolCallEvent(AgentEvent):
    """工具调用事件"""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""

    def __post_init__(self):
        self.event_type = EventType.TOOL_CALL


@dataclass
class ToolResultEvent(AgentEvent):
    """工具结果事件"""
    tool_name: str = ""
    observation: str = ""
    call_id: str = ""
    info: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.event_type = EventType.TOOL_RESULT


@dataclass
class FinishEvent(AgentEvent):
    """执行完成事件"""
    status: str = "completed"  # "completed" | "failed" | "cancelled"
    reason: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.event_type = EventType.FINISH


# Union type for type checking
AgentEventUnion = Union[
    ThoughtEvent, ToolCallEvent, ToolResultEvent, FinishEvent
]


class EventBus:
    """同步事件总线

    设计选择：同步而非异步。
    原因：Agent 执行循环当前在 ThreadPoolExecutor 中同步运行（非 asyncio），
    改为异步需要同时重构 agent_run_service 的线程模型，超出本次范围。
    EventBus 用 threading.Lock + queue.Queue 保证线程安全。

    未来如果 Agent 执行切换到 asyncio，EventBus 可以添加 async API
    而保持同步 API 向后兼容。
    """

    def __init__(self):
        self._subscribers: list[Callable[[AgentEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        """注册事件订阅者"""
        with self._lock:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        """移除事件订阅者"""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not handler]

    def emit(self, event: AgentEvent) -> None:
        """发射事件到所有订阅者（同步调用）"""
        with self._lock:
            subscribers = list(self._subscribers)
        for handler in subscribers:
            try:
                handler(event)
            except Exception:
                pass  # subscriber failure should not break agent loop


class QueueBridge:
    """将 EventBus 事件桥接到 queue.Queue（供 SSE 消费）

    替代当前 agent_run_service 中直接传入的 event_callback。
    agent_run_service 创建 QueueBridge，订阅到 EventBus，
    然后从 bridge.queue 消费事件转发到 SSE。
    """

    def __init__(self, bus: EventBus):
        self.queue: queue.Queue[AgentEvent] = queue.Queue()
        self._bus = bus
        self._bus.subscribe(self._on_event)

    def _on_event(self, event: AgentEvent) -> None:
        self.queue.put(event)

    def close(self) -> None:
        self._bus.unsubscribe(self._on_event)
```

### EventBus 与现有系统的集成

```
                    Agent Kernel
                         │
                    bus.emit(ThoughtEvent(...))
                    bus.emit(ToolCallEvent(...))
                         │
                         ▼
                    ┌─ EventBus ─┐
                    │            │
         ┌──────────┤            ├──────────┐
         │          │            │          │
         ▼          ▼            ▼          ▼
    QueueBridge  PersistSub   LogSub   MetricsSub
         │          │            │          │
         ▼          ▼            ▼          ▼
    SSE Stream  EventsTable  Logger   (future)
    (existing)  (existing)   (existing)
```

### 为什么选同步 EventBus 而非 asyncio.Queue

1. **现状约束**：Agent 在 `ThreadPoolExecutor.submit()` 中同步运行，不在 asyncio event loop 中
2. **最小改动**：同步 EventBus 可以直接替换现有 callback，不需要重构 agent_run_service 的线程模型
3. **渐进迁移**：未来可以添加 `async_emit()` 方法，新旧共存
4. **nanobot 的 MessageBus 是 asyncio.Queue**：因为 nanobot 全链路 async。matmaster 还不是

## Guard Injection Patterns

### Guard 系统拆分策略

当前 `ToolGuard` 类承载了 6 个独立关注点在一个类里，且硬编码在 `MatMasterAgent.__init__` 中。重构后拆分为两层：

**层 1：Agent 内置 Guard（kernel 安全机制）**
- 循环检测（loop detection）：连续相同 tool call 阻断
- 最大轮次限制（max turns）：防止无限循环
- 这些是所有 Agent 共享的安全机制，不可配置、不可移除

**层 2：业务 Guard（Exp 注入的可配置策略）**
- 手稿完成门控（manuscript gate）
- 结构检索门控（structure retrieval gate）
- 认证失败门控（auth failure gate）
- 危险脚本门控（dangerous script gate）
- 准备阶段门控（prepare gate）
- 这些是业务领域特定的，由 Exp 层根据 playground 类型注入

### Guard Protocol 设计

```python
from typing import Protocol, Any


class Guard(Protocol):
    """Guard 协议

    Guard 是可组合的 tool 执行拦截器。
    每个 Guard 实现一个关注点，通过 GuardPipeline 串联执行。
    """

    @property
    def name(self) -> str:
        """Guard 标识名"""
        ...

    def pre_execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[bool, str]:
        """tool 执行前检查

        Returns:
            (allow, message)
            allow=True: 允许执行
            allow=False: 阻止执行，message 作为 tool observation 返回给 Agent
        """
        ...

    def post_execute(
        self, tool_name: str, args: dict[str, Any], observation: str
    ) -> None:
        """tool 执行后回调

        用于更新 Guard 内部状态（如 auth failure 计数器）。
        不能阻止已完成的执行。
        """
        ...


class GuardPipeline:
    """Guard 管道：串联执行多个 Guard

    执行顺序：
    1. pre_execute 按注册顺序执行，任一返回 allow=False 则阻止
    2. tool 执行
    3. post_execute 按注册顺序执行（全部执行，不短路）
    """

    def __init__(self):
        self._guards: list[Guard] = []

    def add(self, guard: Guard) -> None:
        self._guards.append(guard)

    def pre_execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[bool, str]:
        for guard in self._guards:
            allow, message = guard.pre_execute(tool_name, args)
            if not allow:
                return False, f"[{guard.name}] {message}"
        return True, ""

    def post_execute(
        self, tool_name: str, args: dict[str, Any], observation: str
    ) -> None:
        for guard in self._guards:
            guard.post_execute(tool_name, args, observation)
```

### Guard 注入流程

```python
# Exp 层组装 Guard
class MatMasterExp:
    def _create_guards(self, ctx: PlaygroundContext) -> list[Guard]:
        guards = []

        # 业务 Guard 根据配置注入
        config = ctx.config
        mat_config = config.get("mat_master", {})

        # 手稿门控：mat_master 特有
        if mat_config.get("manuscript_gate_enabled", True):
            guards.append(ManuscriptGuard(
                fail_markers=MANUSCRIPT_FAIL_MARKERS,
            ))

        # 认证失败门控
        guards.append(AuthFailureGuard(
            threshold=mat_config.get("auth_failure_threshold", 3),
        ))

        # 危险脚本门控
        guards.append(DangerousScriptGuard())

        return guards
```

**Agent 内置 Guard 不通过 Pipeline，直接在 step loop 中硬编码：**

```python
class AgentKernel:
    def _step(self) -> bool:
        # 内置 Guard：循环检测（不可移除）
        if self._loop_detector.is_looping(tool_call):
            self._handle_loop_detected(tool_call)
            return False

        # 业务 Guard：通过 Pipeline 执行（可配置）
        allow, reason = self._guard_pipeline.pre_execute(
            tool_call.name, tool_call.args
        )
        if not allow:
            self._add_tool_response(tool_call.id, reason)
            return False

        # 执行 tool
        observation = self.tools.execute(...)

        # 业务 Guard：post-execute
        self._guard_pipeline.post_execute(
            tool_call.name, tool_call.args, observation
        )
```

## Compatibility Adapter Strategy

### Strangler Fig 模式应用

采用 Strangler Fig 模式进行增量迁移：新三层骨架与旧实现并存，通过适配层桥接，逐步将流量从旧路径切换到新路径。

```
                    agent_run_service
                         │
                    ┌────┴────┐
                    │ Router  │ (playground name based)
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         CompatAdapter  NewPipeline  NewPipeline
         (wraps old     (mat_master) (minimal)
          x_master)
              │
              ▼
         Old x_master
         Playground
```

### CompatAdapter 设计

```python
class PlaygroundCompatAdapter:
    """兼容适配器：将旧 Playground 实现桥接到新三层契约

    用于 x_master 等暂不迁移的 playground 类型。
    适配器包装旧 playground 实例，从中提取 PlaygroundContext，
    然后用默认 Exp 组装 AgentRuntimeSpec。

    迁移完成后，此适配器可以被移除。
    """

    def __init__(self, legacy_playground):
        """
        Args:
            legacy_playground: 旧 BasePlayground 子类实例
        """
        self._legacy = legacy_playground

    def extract_playground_context(self) -> PlaygroundContext:
        """从旧 playground 提取 PlaygroundContext

        将旧 playground 的隐式状态映射到新的类型化契约。
        """
        pg = self._legacy
        return PlaygroundContext(
            session=pg.session,
            workdir=Path(pg.run_dir) if pg.run_dir else Path.cwd(),
            config=pg.config,
            config_dir=pg.config_dir,
            mcp_manager=getattr(pg, 'mcp_manager', None),
            skill_registry=getattr(pg, '_skill_registry', None),
            env_vars={},
            run_metadata=RunMetadata(
                run_id=getattr(pg, 'task_id', 'legacy'),
                run_dir=Path(pg.run_dir) if pg.run_dir else Path.cwd(),
                session_type=self._detect_session_type(pg),
                playground_name=self._detect_playground_name(pg),
            ),
        )

    def _detect_session_type(self, pg) -> str:
        from evomaster.agent.session import DockerSession, SSHSession
        if isinstance(pg.session, SSHSession):
            return "ssh"
        elif isinstance(pg.session, DockerSession):
            return "docker"
        return "local"

    def _detect_playground_name(self, pg) -> str:
        class_name = pg.__class__.__name__.lower()
        if 'mat_master' in class_name or 'matmaster' in class_name:
            return 'mat_master'
        elif 'minimal' in class_name:
            return 'minimal'
        elif 'x_master' in class_name:
            return 'x_master'
        return 'unknown'
```

### 迁移路径

| 阶段 | mat_master | minimal | x_master |
|------|-----------|---------|----------|
| Phase 1: Kernel | 旧路径 | 旧路径 | 旧路径 |
| Phase 2: Contracts | 旧路径 + 新 contracts 定义 | 旧路径 | 旧路径 |
| Phase 3: Exp Assembly | 新路径（Exp 组装） | 旧路径 | 旧路径 |
| Phase 4: mat_master Migration | 新三层 | 旧路径 | 旧路径 |
| Phase 5: minimal Migration | 新三层 | 新三层 | CompatAdapter |
| Phase 6: Cleanup | 新三层 | 新三层 | CompatAdapter (或 migrate) |

### agent_run_service 适配

```python
# agent_run_service.py 中的路由逻辑
def _get_pipeline(self, playground_name: str):
    """根据 playground 类型选择执行管线

    新迁移的 playground 走新三层管线；
    未迁移的走 CompatAdapter 包装的旧路径。
    """
    if playground_name in self._new_pipelines:
        return self._new_pipelines[playground_name]

    # 降级到旧路径 + CompatAdapter
    legacy_pg = self._get_or_create_playground(session_id)
    adapter = PlaygroundCompatAdapter(legacy_pg)
    ctx = adapter.extract_playground_context()
    # 用通用 Exp 组装 spec
    exp = GenericExp()
    spec = exp.assemble(ctx)
    return NewPipeline(spec)
```

## Patterns to Follow

### Pattern 1: Spec-First Agent Construction

**What:** Agent 不通过继承扩展行为，而是通过 AgentRuntimeSpec 注入能力。

**When:** 所有 Agent 创建场景。

**Why:** 消除 MatMasterAgent -> Agent -> BaseAgent 的继承链。当前 Agent 行为差异通过继承 + override 实现（如 `_on_tool_call_start`、`_handle_finish_tool_call`），导致子类必须理解父类内部状态。Spec 模式把差异表达为数据（不同的 guards、不同的 termination policy），而非代码继承。

```python
# Before (inheritance-based):
class MatMasterAgent(Agent):
    def __init__(self, *args, config_dict=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tool_guard = ToolGuard(self.logger, config_dict)  # hardcoded
        # ... 20+ lines of config extraction

# After (spec-based):
class AgentKernel:
    def __init__(self, spec: AgentRuntimeSpec, bus: EventBus):
        self.llm = spec.llm
        self.tools = spec.tools
        self.guards = GuardPipeline(spec.guards)
        self.bus = bus
        # No config extraction, no inheritance
```

### Pattern 2: Protocol-Based Boundaries

**What:** 组件间依赖通过 Protocol（structural subtyping）声明，不通过 ABC 继承。

**When:** 跨层通信（Exp -> Agent, Agent -> ToolRegistry）。

**Why:** Protocol 不要求被实现类显式继承，减少耦合。现有 BaseTool ABC 要求所有 tool 继承自同一基类，但 MCP tool 和 builtin tool 实际上有不同的生命周期。

```python
# Protocol 不要求继承
class ToolExecutor(Protocol):
    def execute(self, name: str, session: Any, args: str) -> tuple[str, dict]: ...

# 任何有 execute 方法的对象都满足此 Protocol
# 不需要 class MyTool(BaseTool): ...
```

### Pattern 3: Layered Configuration Validation

**What:** 配置在每层入口做一次验证，层内使用已验证的类型。

**When:** Config YAML -> PlaygroundContext -> AgentRuntimeSpec 的每个转换点。

**Why:** 消除 `Dict[str, Any]` 在层间传递。当前 `config_dict` 是 dict，任何 key 拼写错误都要运行时才暴露。

```python
# Playground 层：YAML -> PlaygroundConfig
class PlaygroundConfig(BaseModel):
    session: SessionConfig
    agents: dict[str, AgentConfig]
    skills: SkillsConfig
    mcp: MCPConfig | None = None
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

# Exp 层使用已验证的 PlaygroundConfig，不再碰原始 dict
class MatMasterExp:
    def assemble(self, ctx: PlaygroundContext) -> AgentRuntimeSpec:
        agent_config = ctx.config.agents["general"]  # 类型安全
        llm_config = agent_config.llm  # 类型安全，IDE 补全
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Layer Bypass

**What:** 跳过中间层直接通信（如 Playground 直接配置 Agent 的 guard）。

**Why bad:** 破坏层间契约，导致职责混乱。当前 `MatMasterPlayground.setup()` 既搭环境又注册 skills 到 agent 的 tool registry，playground 层穿透到了 agent 层。

**Instead:** Playground 只产出 PlaygroundContext（包含 skill_registry），由 Exp 层决定如何把 skills 注册到 ToolRegistry。

### Anti-Pattern 2: God Constructor

**What:** 一个构造函数接收 10+ 参数，内部做大量初始化逻辑。

**Why bad:** 当前 `MatMasterAgent.__init__` 接收 `config_dict`、`mode_profile`、`direct_max_workers` 等，内部从 config_dict 中提取 20+ 配置项。这让测试极其困难（需要构造完整的 config dict）。

**Instead:** 用 AgentRuntimeSpec 封装所有依赖。测试时直接构造 spec（可以 mock 每个组件），不需要完整 config。

### Anti-Pattern 3: Callback Inheritance

**What:** 通过继承添加 callback 能力（如 StreamingMatMasterAgent 继承 MatMasterAgent 只为添加 event_callback）。

**Why bad:** 创建了不必要的继承层级。StreamingMatMasterAgent 只是给 MatMasterAgent 加了 `_emit()` 方法，但因为继承，它必须理解整个 MatMasterAgent 的内部状态。

**Instead:** EventBus 注入。Agent 通过 `bus.emit()` 发射事件，不需要知道谁在监听。StreamingMatMasterAgent 这个类可以消失。

### Anti-Pattern 4: Config Dict Cascading

**What:** 从一个大 dict 中层层取值，每层有不同的默认值和 fallback 逻辑。

**Why bad:** 当前 `MatMasterAgent.__init__` 中的 `(config_dict or {}).get('mat_master', {}).get('planner', {}).get('quality_gates', {}).get('finish_block_max', 3)` 链式取值不安全、不可读。

**Instead:** Pydantic model 声明式定义配置结构，让 Pydantic 处理默认值和验证。

## Suggested Build Order

基于组件依赖关系，推荐以下构建顺序（先无依赖的叶子节点，后有依赖的上层组件）：

```
Phase 1: Foundation (无外部依赖)
  ├── 1a. Typed Contracts: PlaygroundContext, AgentRuntimeSpec, Guard Protocol
  ├── 1b. EventBus + AgentEvent types
  └── 1c. TerminationPolicy, CompactionConfig (Pydantic models)

Phase 2: Kernel (依赖 Phase 1)
  ├── 2a. AgentKernel (consumes AgentRuntimeSpec, emits to EventBus)
  │     - Pure execution loop, no config extraction
  │     - Built-in guards (loop detection, max turns)
  ├── 2b. GuardPipeline (consumes Guard Protocol)
  └── 2c. ContextBuilder (from nanobot pattern, adapted for matmaster)

Phase 3: Assembly Layer (依赖 Phase 1 + 2)
  ├── 3a. Base Exp class with assemble() method
  ├── 3b. MatMasterExp (assembles AgentRuntimeSpec for mat_master)
  │     - Guard injection (manuscript, auth failure, dangerous script, etc.)
  │     - Solver routing (direct/planner as Exp-level concern)
  └── 3c. MinimalExp (assembles AgentRuntimeSpec for minimal)

Phase 4: Workspace Layer (依赖 Phase 1)
  ├── 4a. New Playground base class (only PlaygroundContext output)
  ├── 4b. MatMasterPlayground refactored (session + workdir + MCP)
  └── 4c. MinimalPlayground refactored

Phase 5: Integration (依赖 Phase 1-4)
  ├── 5a. CompatAdapter for legacy playgrounds
  ├── 5b. agent_run_service integration (QueueBridge, pipeline routing)
  └── 5c. End-to-end testing (mat_master + minimal on new skeleton)

Phase 6: Cleanup (依赖 Phase 5 稳定)
  ├── 6a. Remove StreamingMatMasterAgent (replaced by EventBus)
  ├── 6b. Remove old BaseExp, old agent inheritance chain
  └── 6c. Documentation and migration guide
```

**构建顺序的关键原则：**
- Phase 1 的 contracts 是所有后续 phase 的基础，必须先定义稳定
- Phase 2 的 Kernel 可以独立于 Playground/Exp 进行单元测试（mock spec）
- Phase 3 和 Phase 4 可以并行开发（Exp 和 Playground 只通过 PlaygroundContext 通信）
- Phase 5 是集成点，需要等 3 和 4 都就绪
- Phase 6 是清理工作，只在新路径稳定后进行

**Solver (direct/planner) 在 Exp 层的位置：**

```
MatMasterExp
    ├── assemble() → AgentRuntimeSpec  # 通用能力组装
    │
    └── run(task, mode="direct"|"planner")
          │
          ├── if mode == "direct":
          │     spec = self.assemble(ctx)
          │     agent = AgentKernel(spec, bus)
          │     return agent.run(task)
          │
          └── if mode == "planner":
                # Planner 是 Exp 组合 Agent 的高阶模式
                plan = self._generate_plan(task)  # 用 planning agent
                for step in plan:
                    spec = self.assemble(ctx)  # 每个 step 可以有不同的 spec
                    agent = AgentKernel(spec, bus)
                    agent.run(step_task)
```

## Scalability Considerations

| Concern | Current (2-4 workers) | At 20 workers | At 100 workers |
|---------|----------------------|---------------|----------------|
| EventBus | 同步 in-process | 同步 in-process (够用) | 考虑 Redis pub/sub |
| Guard state | In-memory per Agent | In-memory per Agent (够用) | 需要 guard state 隔离审计 |
| PlaygroundContext | Per request construct | Per request construct (够用) | 考虑 context pooling |
| ToolRegistry | Per Agent instance | Per Agent instance (够用) | 共享 builtin, per-agent MCP |

## Sources

- Codebase analysis: `evomaster/core/playground.py`, `evomaster/core/exp.py`, `evomaster/agent/agent.py`
- Codebase analysis: `playground/mat_master/core/playground.py`, `playground/mat_master/core/agent.py`
- Codebase analysis: `playground/mat_master/core/tool_guard.py`, `playground/mat_master/service/stream_agent.py`
- Codebase analysis: `evomaster/agent/tools/base.py` (current ToolRegistry, BaseTool)
- Codebase analysis: `src/services/agent_run_service.py` (current integration point)
- Reference architecture: nanobot kernel (`/Users/kealdoom/Desktop/github/nanobot/nanobot/`)
  - `agent/loop.py`: AgentLoop pattern
  - `bus/queue.py`: MessageBus (asyncio.Queue based)
  - `bus/events.py`: InboundMessage/OutboundMessage dataclasses
  - `providers/base.py`: LLMProvider ABC, LLMResponse, ToolCallRequest
  - `agent/tools/registry.py`: ToolRegistry with async execute
  - `agent/context.py`: ContextBuilder with multi-source prompt assembly
- [Strangler Fig Pattern - AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/modernization-decomposing-monoliths/strangler-fig.html) (adapter migration strategy)
- [Pydantic AI](https://ai.pydantic.dev/) (typed contracts, dependency injection patterns in agent frameworks)
- [Dependency Injector](https://python-dependency-injector.ets-labs.org/) (evaluated but not recommended - too heavy for this project)

---

*Architecture analysis: 2026-03-21*
