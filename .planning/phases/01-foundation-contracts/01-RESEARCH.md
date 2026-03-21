# Phase 1: Foundation Contracts - Research

**Researched:** 2026-03-21
**Domain:** Pydantic frozen models / typing.Protocol / discriminated union / synchronous EventBus (stdlib queue)
**Confidence:** HIGH

## Summary

Phase 1 建立整个重构的类型基础。所有新代码放在 `matmaster/` 目录下，与 `evomaster/` 完全脱钩。核心产出是两个 Pydantic frozen model 契约（PlaygroundContext、AgentRuntimeSpec）、两组 Pydantic discriminated union 事件类型（AgentEvent、SystemEvent，合并为 BusEvent）、Guard Protocol 接口、以及基于 `queue.Queue` 的同步 MessageBus + QueueBridge。

技术栈全部来自现有依赖和 stdlib：Pydantic v2 frozen model 做边界契约，`typing.Protocol` 做组件接口，`Literal` type 做 discriminated union 判别字段，`queue.Queue` + `threading.Lock` 做同步事件总线。零新依赖。

研究的关键发现：(1) 现有系统有 18 种不同事件类型需要映射到 BusEvent union（7 种 AgentEvent + 11 种 SystemEvent），每种都需要保留当前 SSE 消费方依赖的字段语义；(2) MessageBus 必须是同步的，因为 agent 在 ThreadPoolExecutor 中运行，不在 asyncio event loop 中；(3) QueueBridge 的转换逻辑是关键 -- 它需要把 Pydantic BusEvent 对象转换为现有 SSE payload 格式 `{source, type, content, session_id, task_id, ...extra}`，让 `stream_service.py` 的 `StreamQueueManager.broadcast()` 无需任何改动。

**Primary recommendation:** 先定义所有 Pydantic model 和 Protocol，然后实现 MessageBus/QueueBridge。每个文件独立可测试，不依赖 evomaster 任何模块。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- 所有新代码放 `matmaster/` 目录下，与 evomaster 脱钩
- `matmaster/types/` 独立契约包，包含 context.py、runtime.py、events.py、guards.py
- `matmaster/bus/` 独立事件总线包，包含 queue.py、bridge.py
- 新契约完全独立于 evomaster/utils/types.py 中的现有类型，干净新起点
- 后续 Phase 2-4 的代码也全部放 matmaster/ 下
- AgentEvent 覆盖当前项目所有已有事件类型，不额外新增
- 分层 union 设计：AgentEvent（kernel 层）、SystemEvent（服务层）、BusEvent = AgentEvent | SystemEvent
- type 字段作为 Pydantic discriminated union 判别字段（Literal type）
- ThoughtEvent 单一类型，通过 stream_state 字段区分流式（start/streaming/end）和非流式
- Guard Protocol 定义 evaluate(ctx: GuardContext) -> GuardResult 接口
- GuardContext 包含：tool_name、tool_args、tool_call_id、current_turn、max_turns、recent_calls
- GuardResult 包含：allowed: bool、reason: str | None、guidance: str | None
- Guard 允许有状态，Protocol 只规定接口不限制内部实现
- TerminationPolicy 不作为独立类型，max_turns 直接作为 AgentRuntimeSpec 的字段
- 重构后去掉 finish tool，终止条件为：LLM 返回无 tool_calls（自然结束）或 max_turns 到达（强制终止）
- 使用同步 queue.Queue，适配 agent 的 ThreadPoolExecutor 同步线程模型
- 单消费者模式，QueueBridge 独占消费 MessageBus
- QueueBridge 从 MessageBus 读取 BusEvent，转换为现有 SSE payload 格式（source, type, content, extra），推入现有 SSE queue
- 现有 service 层不需要改动，Phase 5 再统一

### Claude's Discretion
- 各 Event 类型的具体字段设计（在覆盖现有事件语义的前提下）
- GuardContext 中 recent_calls 的具体记录结构
- MessageBus 和 QueueBridge 的内部实现细节
- matmaster/ 的 __init__.py 导出策略

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CONT-01 | PlaygroundContext 使用 Pydantic frozen model 定义，包含 workdir、session type、cache area、环境变量、MCP manager、skill registry | Pydantic v2 ConfigDict(frozen=True) + Any 类型占位符，现有 BasePlayground 字段分析完成 |
| CONT-02 | AgentRuntimeSpec 使用 Pydantic frozen model 定义，包含 LLM provider、tool registry、guards、termination policy、hooks、compaction config | Pydantic v2 frozen model + Protocol 引用类型，现有 MatMasterAgent.__init__ 和 AgentConfig 字段分析完成 |
| CONT-03 | AgentEvent 使用 Pydantic discriminated union 定义事件类型 | Pydantic v2 Annotated[Union[...], Field(discriminator='type')] 语法验证，现有 18 种事件类型完整映射 |
| CONT-04 | Guard Protocol 接口定义（evaluate 方法签名 + GuardResult 返回类型） | typing.Protocol + 现有 ToolGuard.evaluate() 接口分析，GuardContext/GuardResult 字段设计 |
| CONT-05 | TerminationPolicy 类型定义 | 简化为 AgentRuntimeSpec.max_turns: int 字段，去掉 finish tool，终止条件明确 |
| EBUS-01 | MessageBus 使用同步 queue 实现，适配 ThreadPoolExecutor 线程模型 | queue.Queue + threading.Lock 设计验证，nanobot MessageBus 参考分析 |
| EBUS-02 | QueueBridge 将 MessageBus 事件桥接到现有 SSE 消费路径 | 现有 event_callback 签名和 StreamQueueManager.broadcast 接口完整分析，转换映射确定 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Pydantic | >=2.12, <3 (pyproject.toml 已有) | PlaygroundContext, AgentRuntimeSpec, 所有 Event 类型 | frozen model 提供不可变性 + 运行时验证 + JSON Schema 导出，项目已全面使用 |
| typing.Protocol | stdlib (Python 3.13) | Guard Protocol 接口定义 | 结构化子类型，不强制继承，旧类可直接满足新接口 |
| typing.Literal | stdlib | discriminated union 判别字段 | Pydantic v2 原生支持 Literal 做 discriminator |
| queue.Queue | stdlib | MessageBus 核心传输 | 线程安全的同步队列，适配 ThreadPoolExecutor |
| threading.Lock | stdlib | EventBus 订阅者管理 | 保护 subscriber 列表的并发访问 |
| dataclasses | stdlib | 内部辅助结构（GuardContext 等） | 内部传递对象不需要 Pydantic 验证开销 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| datetime | stdlib | 事件时间戳 | 每个 Event 的 timestamp 字段 |
| uuid | stdlib | stream_id 生成 | ThoughtEvent 流式标识符 |
| enum.Enum | stdlib | EventType / stream_state 枚举 | 可选，Literal 字符串已足够 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Pydantic frozen model | @dataclass(frozen=True) | dataclass 无运行时验证，不能 JSON Schema 导出，与项目现有风格不一致 |
| typing.Protocol | ABC | ABC 强制继承，不适合 brownfield 适配场景 |
| queue.Queue | asyncio.Queue | agent 在 ThreadPoolExecutor 中同步运行，不在 event loop 中，asyncio.Queue 无法使用 |
| Pydantic discriminated union | isinstance 链 | isinstance 链无法利用 Pydantic 的验证和序列化能力，不可维护 |

**Installation:**
```bash
# 零新依赖 -- 所有库已在 pyproject.toml 或 stdlib 中
# 无需安装任何新包
```

## Architecture Patterns

### Recommended Project Structure
```
matmaster/
    __init__.py              # 顶层命名空间
    contracts/
        __init__.py          # 导出所有契约类型
        context.py           # PlaygroundContext (CONT-01)
        runtime.py           # AgentRuntimeSpec (CONT-02)
        events.py            # AgentEvent/SystemEvent/BusEvent (CONT-03)
        guards.py            # Guard Protocol + GuardContext + GuardResult (CONT-04)
    bus/
        __init__.py          # 导出 MessageBus, QueueBridge
        queue.py             # MessageBus 实现 (EBUS-01)
        bridge.py            # QueueBridge 实现 (EBUS-02)
```

### Pattern 1: Pydantic Frozen Model for Boundary Contracts
**What:** 使用 `model_config = ConfigDict(frozen=True)` 定义不可变边界契约
**When to use:** PlaygroundContext 和 AgentRuntimeSpec -- 层间传递的数据对象
**Example:**
```python
# Source: Pydantic v2 official docs -- https://docs.pydantic.dev/latest/concepts/config/
from typing import Any
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

class PlaygroundContext(BaseModel):
    """Layer 1 output: 环境上下文，由 Playground 构建后传递给 Exp 层。"""
    model_config = ConfigDict(frozen=True)

    workdir: Path
    session_type: str               # "docker" | "local" | "ssh"
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    mcp_manager: Any = None         # Phase 4 定义具体 Protocol
    skill_registry: Any = None      # Phase 3 定义具体 Protocol
    run_meta: dict[str, Any] = Field(default_factory=dict)
```

### Pattern 2: Pydantic Discriminated Union for Events
**What:** 使用 `Literal` type 字段 + `Annotated[Union[...], Field(discriminator='type')]` 区分事件类型
**When to use:** AgentEvent 和 SystemEvent 类型定义
**Example:**
```python
# Source: Pydantic v2 official docs -- https://docs.pydantic.dev/latest/concepts/unions/
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field
from datetime import datetime

class ThoughtEvent(BaseModel):
    type: Literal['thought'] = 'thought'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    content: str = ''
    stream_state: str | None = None       # 'start' | 'streaming' | 'end' | None
    stream_id: str | None = None
    token_count: int = 0
    reasoning_content: str | None = None

class ToolCallEvent(BaseModel):
    type: Literal['tool_call'] = 'tool_call'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_name: str
    arguments: dict[str, Any]
    call_id: str

# ... 其他事件类型

AgentEvent = Annotated[
    Union[ThoughtEvent, ToolCallEvent, ToolResultEvent, FinishEvent, ErrorEvent,
          AssistantStateEvent, SkillHitEvent],
    Field(discriminator='type')
]
```

### Pattern 3: typing.Protocol for Component Interfaces
**What:** 使用 Protocol 定义组件接口，不强制继承
**When to use:** Guard 接口、后续的 LLMProvider / ToolExecutor 接口
**Example:**
```python
# Source: Python typing.Protocol spec
from typing import Protocol
from dataclasses import dataclass

@dataclass
class GuardContext:
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    current_turn: int
    max_turns: int
    recent_calls: list[dict[str, Any]]  # 最近 N 次调用记录

@dataclass
class GuardResult:
    allowed: bool
    reason: str | None = None
    guidance: str | None = None       # 注入给 LLM 的引导提示

class Guard(Protocol):
    """Guard 接口：评估是否允许某次工具调用。
    实现可以是有状态的（如维护 deque 记录近期调用）。
    """
    def evaluate(self, ctx: GuardContext) -> GuardResult: ...
```

### Pattern 4: Synchronous MessageBus with Queue
**What:** 使用 `queue.Queue` 实现同步事件总线，`threading.Lock` 保护订阅者列表
**When to use:** Agent kernel 发射事件，QueueBridge 消费事件
**Example:**
```python
# Source: Architecture research + nanobot MessageBus reference
import queue
import threading
from typing import Callable

class MessageBus:
    """同步事件总线。Agent kernel 调用 emit() 发射事件。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[BusEvent] = queue.Queue()

    def emit(self, event: BusEvent) -> None:
        self._queue.put(event)

    def get(self, timeout: float | None = None) -> BusEvent:
        return self._queue.get(timeout=timeout)

    @property
    def pending(self) -> int:
        return self._queue.qsize()
```

### Pattern 5: QueueBridge SSE Adapter
**What:** 将 BusEvent 转换为现有 SSE payload 格式，推入现有 asyncio.Queue
**When to use:** 桥接新 MessageBus 到现有 `stream_service.py` 的 `StreamQueueManager.broadcast()`
**Example:**
```python
# Source: 基于 src/services/agent_run_service.py event_callback 签名分析
import queue
from typing import Any

class QueueBridge:
    """从 MessageBus 消费 BusEvent，转换为 SSE payload dict，推入目标 queue。"""

    def __init__(self, bus: MessageBus, target: queue.Queue) -> None:
        self._bus = bus
        self._target = target

    def _to_sse_payload(self, event: BusEvent) -> dict[str, Any]:
        """将 BusEvent 转换为现有 SSE payload 格式。
        现有格式: {source: str, type: str, content: Any, session_id, task_id, ...extra}
        """
        return {
            'source': event.source,
            'type': event.type,
            'content': self._extract_content(event),
            **self._extract_extra(event),
        }
```

### Anti-Patterns to Avoid
- **Import evomaster in matmaster/:** matmaster/ 是全新命名空间，不应 import 任何 evomaster 模块。类型兼容通过 Protocol 实现，不通过导入。
- **在 Event 中使用 evomaster.utils.types 的类型:** 不复用 ToolCall、AssistantMessage 等类型。Event 中的字段是原始值（str, dict, int），不是 Pydantic model 嵌套。
- **给 Event 类型加 __post_init__:** 使用 Pydantic BaseModel 而非 dataclass，type 字段通过 Literal 默认值设置，不需要 __post_init__。
- **在 MessageBus 中使用 asyncio.Queue:** agent 在 ThreadPoolExecutor 中同步运行，asyncio.Queue 的 put/get 是 coroutine，无法在同步线程中调用。
- **多消费者模式:** 决策明确为单消费者（QueueBridge 独占），不要实现 subscribe/unsubscribe 模式。简单队列模型足够。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 事件类型区分 | isinstance 链或自定义 dispatch | Pydantic discriminated union | 一行 `Field(discriminator='type')` 搞定验证 + 反序列化 + 类型安全 |
| 运行时验证 | 手写 assert/raise | Pydantic frozen model | 构造时自动验证所有字段类型和约束 |
| 不可变数据 | 手写 `__setattr__` 拦截 | ConfigDict(frozen=True) | Pydantic 原生支持，包括 hash、copy 等 |
| JSON Schema | 手写 schema dict | model_json_schema() | Pydantic 自动从类型定义生成，永远与代码同步 |
| 线程安全队列 | 手写 list + Lock | queue.Queue | stdlib 已处理所有边界情况（满/空/超时/GC） |

**Key insight:** Phase 1 完全是类型定义和基础设施，所有复杂性都由 Pydantic 和 stdlib 处理。人工编写的代码量极小（预计不超过 300 行纯代码），主要是类型声明和简单的转换逻辑。

## Common Pitfalls

### Pitfall 1: Event 字段遗漏导致 SSE 回退破坏
**What goes wrong:** 新 Event 类型缺少现有 SSE 消费方依赖的字段（如 ThoughtEvent 缺少 stream_state、stream_id、token_count），导致前端渲染异常。
**Why it happens:** 现有事件通过 `**extra` kwargs 传递附加字段，这些字段没有文档化，分散在 stream_agent.py 的各处 `_emit()` 调用中。
**How to avoid:** 研究已完整收集了所有 _emit() 调用点和 event_callback() 调用点的字段。对照下方事件映射表逐一验证。
**Warning signs:** QueueBridge._to_sse_payload() 输出的 dict 中缺少 stream_state/stream_id/token_count/context 等字段。

### Pitfall 2: Frozen Model 中引用可变对象
**What goes wrong:** PlaygroundContext 是 frozen 的，但其 mcp_manager、skill_registry 字段引用的对象是可变的。修改这些对象的内部状态不违反 Pydantic frozen 语义（frozen 只阻止重新赋值），但可能造成概念混淆。
**Why it happens:** Pydantic frozen 是浅冻结，不是深冻结。`ctx.env_vars['NEW_KEY'] = 'val'` 会抛异常（dict 被冻结），但 `ctx.mcp_manager.some_method()` 不会。
**How to avoid:** 文档中明确说明 frozen 的语义边界。mcp_manager 和 skill_registry 暂用 `Any` 类型占位，Phase 3/4 再为它们定义 Protocol 接口做更严格的约束。dict 类型字段（env_vars, run_meta）在 frozen model 中确实是不可修改的（Pydantic 会阻止 dict 的 __setitem__）。
**Warning signs:** 测试中尝试修改 frozen model 的 dict 字段却没有抛出异常。

### Pitfall 3: discriminated union type 字段名冲突
**What goes wrong:** AgentEvent 和 SystemEvent 都用 `type` 字段做 discriminator，当合并为 `BusEvent = AgentEvent | SystemEvent` 时，如果有两个子类型的 Literal 值相同（如 'error'），Pydantic 会抛出 discriminator 冲突。
**Why it happens:** AgentEvent 和 SystemEvent 的 type 值必须全局唯一才能组合成 BusEvent。
**How to avoid:** 确保 AgentEvent 的所有 type 值（thought, tool_call, tool_result, finish, error, assistant_state, skill_hit）与 SystemEvent 的所有 type 值（confirmation_request, confirmation_timeout, context_compaction, exp_run, cancelled, workspace_upload_error, bohrium_node, mcp_server_status, mcp_connect）没有交集。当前设计中确实没有交集。
**Warning signs:** Pydantic 在构造 TypeAdapter 或使用 discriminated union 时抛出 `ConfigError`。

### Pitfall 4: QueueBridge content 字段类型不一致
**What goes wrong:** 现有 event_callback 的 content 参数类型多样 -- str（thought 文本）、dict（tool_call payload）、model_dump() 输出（assistant_state）。QueueBridge 必须保持这些类型不变。
**Why it happens:** 现有系统的 content 没有类型约束，每种事件类型传不同的内容。
**How to avoid:** QueueBridge._to_sse_payload() 中按 event.type 分发转换逻辑，不做统一的 content 提取。ThoughtEvent -> content=str, ToolCallEvent -> content=dict, FinishEvent -> content=str|None 等。
**Warning signs:** 前端收到的 content 类型与之前不一致。

### Pitfall 5: GuardContext.recent_calls 的数据来源不明
**What goes wrong:** GuardContext 要求 recent_calls 字段，但在 Phase 1 只是类型定义，没有填充逻辑。如果设计时不考虑数据来源，Phase 2 实现 GuardPipeline 时会发现无法构造 GuardContext。
**Why it happens:** Guard 评估发生在 kernel 的 tool 执行前，recent_calls 需要 kernel 维护一个滑动窗口记录。
**How to avoid:** recent_calls 设计为 `list[RecentCall]`，其中 `RecentCall` 是一个简单的 dataclass/TypedDict 包含 tool_name、tool_args、call_id、timestamp。Phase 1 只定义结构，Phase 2 在 kernel 中维护并注入。
**Warning signs:** GuardContext 的 recent_calls 字段在测试中总是空 list。

## Code Examples

### Example 1: 完整的 PlaygroundContext 定义 (CONT-01)
```python
# matmaster/types/context.py
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlaygroundContext(BaseModel):
    """Playground 层输出的环境上下文契约。

    由 Playground.setup() 构建，传递给 Exp.assemble()。
    frozen=True 保证层间传递时不被意外修改。
    """
    model_config = ConfigDict(frozen=True)

    workdir: Path
    session_type: str                                # "docker" | "local" | "ssh"
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    mcp_manager: Any = None                          # Phase 4 定义 Protocol
    skill_registry: Any = None                       # Phase 3 定义 Protocol
    run_meta: dict[str, Any] = Field(default_factory=dict)
```

### Example 2: 完整的 AgentRuntimeSpec 定义 (CONT-02)
```python
# matmaster/types/runtime.py
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .guards import Guard


class CompactionConfig(BaseModel):
    """Context compaction 配置。"""
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    context_window_tokens: int = 128_000
    trigger_ratio: float = 0.7
    strategy: str = 'sliding_window'           # 'sliding_window' | 'summary' | 'latest_half'
    compaction_llm: str | None = None          # config.llm 中的 key


class AgentRuntimeSpec(BaseModel):
    """Exp 层输出的 agent 运行时规格契约。

    由 Exp.assemble(ctx: PlaygroundContext) 构建，
    传递给 AgentKernel.run(spec, task)。
    frozen=True 保证 kernel 运行期间规格不变。
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # LLM (Phase 2 定义 LLMProvider Protocol)
    llm_provider: Any                                # LLMProvider Protocol

    # Tools (Phase 3 定义 ToolRegistry)
    tool_registry: Any                               # ToolRegistry instance

    # Guards
    guards: list[Guard] = Field(default_factory=list)

    # Termination (CONT-05: 简化为 max_turns 字段)
    max_turns: int = 100

    # Hooks (Phase 2 定义 Hook Protocol)
    hooks: list[Any] = Field(default_factory=list)

    # Context
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ''

    # Mode
    mode: str = 'direct'                             # 'direct' | 'planner'
```

### Example 3: 事件类型分层设计 (CONT-03)
```python
# matmaster/types/events.py
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ── AgentEvent: kernel 层发射的事件 ──────────────────

class ThoughtEvent(BaseModel):
    """LLM 思考/推理事件。流式和非流式统一用 stream_state 区分。"""
    type: Literal['thought'] = 'thought'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    content: str = ''
    stream_state: str | None = None       # 'start' | 'streaming' | 'end' | None(非流式)
    stream_id: str | None = None
    token_count: int = 0
    context: str | None = None            # 'step_execution' 等
    reasoning_content: str | None = None

class ToolCallEvent(BaseModel):
    """工具调用事件。"""
    type: Literal['tool_call'] = 'tool_call'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    call_id: str
    tool_name: str
    arguments: dict[str, Any]

class ToolResultEvent(BaseModel):
    """工具执行结果事件。"""
    type: Literal['tool_result'] = 'tool_result'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    call_id: str
    tool_name: str
    result: Any                           # str | dict
    info: dict[str, Any] = Field(default_factory=dict)

class FinishEvent(BaseModel):
    """Agent 执行完成事件。"""
    type: Literal['finish'] = 'finish'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    status: str = 'completed'             # 'completed' | 'failed' | 'cancelled'
    reason: str = ''
    final_content: str | None = None

class ErrorEvent(BaseModel):
    """Agent 执行错误事件。"""
    type: Literal['error'] = 'error'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str
    traceback: str | None = None

class AssistantStateEvent(BaseModel):
    """完整的 assistant 消息状态（含 tool_calls 列表），供持久化。"""
    type: Literal['assistant_state'] = 'assistant_state'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    state: dict[str, Any]                 # AssistantMessage.model_dump() 的完整内容

class SkillHitEvent(BaseModel):
    """Skill 命中追踪事件。"""
    type: Literal['skill_hit'] = 'skill_hit'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    skill_name: str

AgentEvent = Annotated[
    Union[ThoughtEvent, ToolCallEvent, ToolResultEvent, FinishEvent,
          ErrorEvent, AssistantStateEvent, SkillHitEvent],
    Field(discriminator='type')
]


# ── SystemEvent: 服务层发射的事件 ─────────────────────

class ConfirmationRequestEvent(BaseModel):
    type: Literal['confirmation_request'] = 'confirmation_request'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    question: str
    mode: str                             # 'timeout' | 'block'
    timeout_seconds: int | None = None
    context: str | None = None
    actions: list[str] = Field(default_factory=list)
    origin: str | None = None

class ConfirmationTimeoutEvent(BaseModel):
    type: Literal['confirmation_timeout'] = 'confirmation_timeout'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    question: str
    default_reply: str | None = None

class ContextCompactionEvent(BaseModel):
    type: Literal['context_compaction'] = 'context_compaction'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any]

class ExpRunEvent(BaseModel):
    type: Literal['exp_run'] = 'exp_run'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    exp_name: str

class CancelledEvent(BaseModel):
    type: Literal['cancelled'] = 'cancelled'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    reason: str = ''

class WorkspaceUploadErrorEvent(BaseModel):
    type: Literal['workspace_upload_error'] = 'workspace_upload_error'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str

class BohriumNodeEvent(BaseModel):
    type: Literal['bohrium_node'] = 'bohrium_node'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any] = Field(default_factory=dict)

class McpServerStatusEvent(BaseModel):
    type: Literal['mcp_server_status'] = 'mcp_server_status'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    server_name: str
    transport: str | None = None
    phase: str = ''
    detail: dict[str, Any] = Field(default_factory=dict)

class McpConnectEvent(BaseModel):
    type: Literal['mcp_connect'] = 'mcp_connect'
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    phase: str = ''                       # 'start' | 'ready' | 'failed'
    message: str = ''
    elapsed_ms: int | None = None
    error: str | None = None

SystemEvent = Annotated[
    Union[ConfirmationRequestEvent, ConfirmationTimeoutEvent,
          ContextCompactionEvent, ExpRunEvent, CancelledEvent,
          WorkspaceUploadErrorEvent, BohriumNodeEvent,
          McpServerStatusEvent, McpConnectEvent],
    Field(discriminator='type')
]

# ── BusEvent: 统一类型 ───────────────────────────────

BusEvent = Annotated[
    Union[ThoughtEvent, ToolCallEvent, ToolResultEvent, FinishEvent,
          ErrorEvent, AssistantStateEvent, SkillHitEvent,
          ConfirmationRequestEvent, ConfirmationTimeoutEvent,
          ContextCompactionEvent, ExpRunEvent, CancelledEvent,
          WorkspaceUploadErrorEvent, BohriumNodeEvent,
          McpServerStatusEvent, McpConnectEvent],
    Field(discriminator='type')
]
```

### Example 4: Guard Protocol (CONT-04)
```python
# matmaster/types/guards.py
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RecentCall:
    """单次工具调用记录，用于 GuardContext.recent_calls 滑动窗口。"""
    tool_name: str
    tool_args: dict[str, Any]
    call_id: str
    timestamp: float              # time.monotonic()


@dataclass
class GuardContext:
    """Guard 评估上下文。由 kernel 在每次 tool 调用前构造。"""
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    current_turn: int
    max_turns: int
    recent_calls: list[RecentCall] = field(default_factory=list)


@dataclass
class GuardResult:
    """Guard 评估结果。"""
    allowed: bool
    reason: str | None = None
    guidance: str | None = None   # 注入给 LLM 的引导提示（如 "请停止重复调用"）


class Guard(Protocol):
    """Guard 接口协议。

    实现可以是有状态的（如用 deque 记录近期调用频率）。
    Protocol 只规定 evaluate 方法签名。
    """
    def evaluate(self, ctx: GuardContext) -> GuardResult: ...
```

### Example 5: MessageBus (EBUS-01)
```python
# matmaster/bus/queue.py
import queue
from typing import Any

from matmaster.types.events import BusEvent


class MessageBus:
    """同步事件总线。

    Agent kernel 调用 emit() 发射 BusEvent，
    QueueBridge 调用 get() 消费事件。
    单 producer（agent thread）单 consumer（bridge）模式。

    设计选择：
    - 同步 queue.Queue 而非 asyncio.Queue：agent 在 ThreadPoolExecutor 中同步运行
    - 无 subscribe 模式：单消费者通过 get() 拉取，而非多订阅者推送
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: queue.Queue[BusEvent] = queue.Queue(maxsize=maxsize)

    def emit(self, event: BusEvent) -> None:
        """发射事件（线程安全，非阻塞）。"""
        self._queue.put(event)

    def get(self, timeout: float | None = None) -> BusEvent:
        """消费下一个事件（阻塞直到有事件或超时）。"""
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> BusEvent:
        """非阻塞消费。队列为空时抛出 queue.Empty。"""
        return self._queue.get_nowait()

    @property
    def pending(self) -> int:
        """待消费事件数量（近似值）。"""
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
```

### Example 6: QueueBridge (EBUS-02)
```python
# matmaster/bus/bridge.py
from typing import Any

from matmaster.types.events import (
    BusEvent, ThoughtEvent, ToolCallEvent, ToolResultEvent,
    FinishEvent, ErrorEvent, AssistantStateEvent, SkillHitEvent,
    ConfirmationRequestEvent, ConfirmationTimeoutEvent,
    ContextCompactionEvent, ExpRunEvent, CancelledEvent,
    WorkspaceUploadErrorEvent, BohriumNodeEvent,
    McpServerStatusEvent, McpConnectEvent,
)
from .queue import MessageBus


class QueueBridge:
    """将 MessageBus 事件桥接到现有 SSE payload 格式。

    从 MessageBus 消费 BusEvent，转换为现有 event_callback 的
    payload 格式 {source, type, content, ...extra}，然后调用
    send_callback 推送。

    用法（在 agent_run_service 中）:
        bus = MessageBus()
        bridge = QueueBridge(bus)

        # agent 线程中: bus.emit(ThoughtEvent(...))
        # 消费线程/协程中:
        payload = bridge.next_payload()
        send_cb(payload)
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    def next_payload(self, timeout: float | None = None) -> dict[str, Any]:
        """从 bus 消费一个事件并转换为 SSE payload dict。"""
        event = self._bus.get(timeout=timeout)
        return self._to_sse_payload(event)

    def _to_sse_payload(self, event: BusEvent) -> dict[str, Any]:
        """将 BusEvent 转换为现有 SSE payload 格式。

        现有格式: event_callback(source, type, content, **extra)
        -> payload = {source, type, content, ...extra}
        """
        base = {
            'source': event.source,
            'type': event.type,
        }
        # 按事件类型提取 content 和 extra fields
        if isinstance(event, ThoughtEvent):
            base['content'] = event.content
            if event.stream_state is not None:
                base['stream_state'] = event.stream_state
            if event.stream_id is not None:
                base['stream_id'] = event.stream_id
            if event.token_count:
                base['token_count'] = event.token_count
            if event.context:
                base['context'] = event.context
        elif isinstance(event, ToolCallEvent):
            base['content'] = {
                'id': event.call_id,
                'name': event.tool_name,
                'args': event.arguments,
            }
        elif isinstance(event, ToolResultEvent):
            base['content'] = {
                'id': event.call_id,
                'name': event.tool_name,
                'result': event.result,
                'info': event.info,
            }
        elif isinstance(event, FinishEvent):
            base['content'] = event.final_content or event.status
        elif isinstance(event, ErrorEvent):
            base['content'] = event.message
        elif isinstance(event, AssistantStateEvent):
            base['content'] = event.state
        elif isinstance(event, SkillHitEvent):
            base['content'] = event.skill_name
        elif isinstance(event, ExpRunEvent):
            base['content'] = event.exp_name
        elif isinstance(event, CancelledEvent):
            base['content'] = event.reason or 'Task cancelled by user.'
        elif isinstance(event, WorkspaceUploadErrorEvent):
            base['content'] = event.message
        elif isinstance(event, ConfirmationRequestEvent):
            base['content'] = {
                'question': event.question,
                'mode': event.mode,
            }
            if event.timeout_seconds is not None:
                base['content']['timeout_seconds'] = event.timeout_seconds
            if event.context:
                base['content']['context'] = event.context
            if event.actions:
                base['content']['actions'] = event.actions
            if event.origin:
                base['content']['origin'] = event.origin
        elif isinstance(event, ConfirmationTimeoutEvent):
            base['content'] = {
                'question': event.question,
                'default_reply': event.default_reply,
            }
        elif isinstance(event, ContextCompactionEvent):
            base['content'] = event.payload
        elif isinstance(event, BohriumNodeEvent):
            base['content'] = event.payload
        elif isinstance(event, McpServerStatusEvent):
            base['content'] = event.detail
            base['mcp_phase'] = event.phase
            base['mcp_server'] = event.server_name
            base['mcp_transport'] = event.transport
        elif isinstance(event, McpConnectEvent):
            base['content'] = {
                'phase': event.phase,
                'message': event.message,
            }
            if event.elapsed_ms is not None:
                base['content']['elapsed_ms'] = event.elapsed_ms
            if event.error:
                base['content']['error'] = event.error
            base['mcp_phase'] = event.phase
        return base
```

## Current Event Types Complete Mapping

基于代码分析收集的所有事件类型及其字段，用于指导 Event model 设计：

### AgentEvent (kernel 层)

| type | source | content 类型 | extra kwargs | 代码位置 |
|------|--------|-------------|-------------|---------|
| `thought` (非流式) | agent_name | str (reasoning 或 assistant text) | -- | stream_agent.py:121-125 |
| `thought` (流式 start) | agent_name | '' | stream_state='start', context='step_execution', stream_id=str | stream_agent.py:65-72 |
| `thought` (流式 streaming) | agent_name | str delta | stream_state='streaming', stream_id=str | stream_agent.py:48-55 |
| `thought` (流式 end) | agent_name | '' | stream_state='end', stream_id=str, token_count=int | stream_agent.py:76-85 |
| `tool_call` | 'MatMaster' | dict {id, name, args} | -- | stream_agent.py:163-167 |
| `tool_result` | 'MatMaster' | dict {id, name, result, info} | -- | stream_agent.py:182-189 |
| `assistant_state` | 'MatMaster' | dict (model_dump) | -- | stream_agent.py:126 |
| `skill_hit` | 'MatMaster' | str (skill_name) | -- | stream_agent.py:143 |

### SystemEvent (服务层)

| type | source | content 类型 | extra kwargs | 代码位置 |
|------|--------|-------------|-------------|---------|
| `confirmation_request` | source_override 或 agent | dict {question, mode, timeout_seconds?, context?, actions?, origin?} | -- | confirm.py:106-119 |
| `confirmation_timeout` | source | dict {question, default_reply} | -- | confirm.py (timeout path) |
| `context_compaction` | 'System' | dict (payload) | -- | stream_agent.py:36 |
| `exp_run` | 'MatMaster' | str (exp_name) | -- | agent_run_service.py:669 |
| `cancelled` | 'System' | str | -- | agent_run_service.py:716 |
| `finish` | 'System' | str 'Done' | -- | agent_run_service.py:735 |
| `error` | 'System' | str (error message) | -- | agent_run_service.py:749 |
| `workspace_upload_error` | 'System' | str | -- | agent_run_service.py:310 |
| `mcp_server_status` | 'System' | dict (progress) | mcp_phase, mcp_server, mcp_transport | agent_run_service.py:169-176 |
| `mcp_connect` | 'System' | dict {phase, message, elapsed_ms?, error?} | mcp_phase | agent_run_service.py:179-214 |
| `bohrium_node` | 'System' | dict (node info) | -- | agent_run_service.py (bohrium setup) |

注意：`finish` 和 `error` 在 SystemEvent 层面可以合并到 FinishEvent/ErrorEvent 中（它们语义相同），但当前设计遵循 CONTEXT.md 的决策：AgentEvent 是 kernel 发射的，SystemEvent 是服务层发射的。服务层的 `finish`（'Done'）和 kernel 的 FinishEvent（completed/failed/cancelled）是不同的事件。

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Pydantic v1 discriminator | Pydantic v2 `Field(discriminator='field_name')` | Pydantic v2 (2023) | 更简洁的语法，性能提升 |
| Union[...] 无 discriminator | `Annotated[Union[...], Field(discriminator=...)]` | Pydantic v2 | 验证时直接按 discriminator 值选择子类型，O(1) 而非 O(n) 尝试 |
| Protocol 需要 `runtime_checkable` | Protocol 纯静态检查（mypy/pyright） | Python 3.8+ | runtime_checkable 有性能开销，静态检查已足够 |

**Deprecated/outdated:**
- Pydantic v1 的 `__root__` validator 做 union 区分 -- v2 用 discriminated union 替代
- `@dataclass` + `__post_init__` 设置 type 字段 -- Pydantic BaseModel + Literal 默认值更干净

## Open Questions

1. **PlaygroundContext 的 mcp_manager 和 skill_registry 类型**
   - What we know: Phase 1 暂用 `Any` 占位
   - What's unclear: Phase 3/4 是否会定义 Protocol 接口约束这些字段
   - Recommendation: 用 `Any` 是正确的，Phase 3/4 可以通过 TypeVar 或具体 Protocol 收窄类型

2. **Planner 特有事件是否纳入 BusEvent**
   - What we know: ResearchPlanner 发射 `phase_change`, `execution_summary`, `status_skill_produced` 等事件（通过 `_emit('Planner', ...)`），目前 CONTEXT.md 决策是"覆盖当前所有已有事件类型，不额外新增"
   - What's unclear: Planner 事件是否算"已有"事件类型。它们当前使用同一个 event_callback 机制传递
   - Recommendation: Phase 1 只定义上面映射表中的 18 种类型。Planner 事件可以在 Phase 3（Exp Assembly）中扩展 SystemEvent union。Planner 的 thought 事件已经用现有 ThoughtEvent 覆盖（source='Planner'），phase_change/execution_summary 等可以后续扩展

3. **QueueBridge 的 session_id/task_id 注入**
   - What we know: 现有 SSE payload 包含 session_id 和 task_id，这些是调用上下文而非事件本身的属性
   - What's unclear: session_id/task_id 是在 QueueBridge 层注入还是在更上层（agent_run_service）注入
   - Recommendation: QueueBridge.next_payload() 返回不含 session_id/task_id 的基础 payload，由 agent_run_service 在调用 send_cb 前注入。这保持了 bus/bridge 与 session 概念的解耦

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=9.0.2 |
| Config file | 无 pytest.ini / pyproject.toml [tool.pytest]，使用 pytest 默认配置 |
| Quick run command | `python -m pytest tests/matmaster/ -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONT-01 | PlaygroundContext frozen model 构造、验证、不可变性 | unit | `python -m pytest tests/matmaster/types/test_context.py -x` | Wave 0 |
| CONT-02 | AgentRuntimeSpec frozen model 构造、验证、不可变性 | unit | `python -m pytest tests/matmaster/types/test_runtime.py -x` | Wave 0 |
| CONT-03 | AgentEvent/SystemEvent/BusEvent discriminated union 序列化、反序列化、type 判别 | unit | `python -m pytest tests/matmaster/types/test_events.py -x` | Wave 0 |
| CONT-04 | Guard Protocol 接口满足、GuardContext/GuardResult 构造 | unit | `python -m pytest tests/matmaster/types/test_guards.py -x` | Wave 0 |
| CONT-05 | TerminationPolicy 简化为 max_turns 字段验证 | unit | `python -m pytest tests/matmaster/types/test_runtime.py::test_max_turns -x` | Wave 0 |
| EBUS-01 | MessageBus emit/get 线程安全、超时、pending | unit | `python -m pytest tests/matmaster/bus/test_queue.py -x` | Wave 0 |
| EBUS-02 | QueueBridge 事件转换为 SSE payload 格式正确性 | unit | `python -m pytest tests/matmaster/bus/test_bridge.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/matmaster/ -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/__init__.py` -- test package init
- [ ] `tests/matmaster/types/__init__.py` -- contracts test package
- [ ] `tests/matmaster/types/test_context.py` -- PlaygroundContext 测试
- [ ] `tests/matmaster/types/test_runtime.py` -- AgentRuntimeSpec + max_turns 测试
- [ ] `tests/matmaster/types/test_events.py` -- 事件 discriminated union 测试
- [ ] `tests/matmaster/types/test_guards.py` -- Guard Protocol 满足性测试
- [ ] `tests/matmaster/bus/__init__.py` -- bus test package
- [ ] `tests/matmaster/bus/test_queue.py` -- MessageBus 线程安全测试
- [ ] `tests/matmaster/bus/test_bridge.py` -- QueueBridge SSE payload 转换测试

## Sources

### Primary (HIGH confidence)
- Pydantic v2 官方文档 -- frozen models, discriminated unions: https://docs.pydantic.dev/latest/concepts/unions/ , https://docs.pydantic.dev/latest/concepts/config/
- Python typing.Protocol 规范: https://typing.python.org/en/latest/spec/protocol.html
- nanobot MessageBus 参考实现: /Users/kealdoom/Desktop/github/nanobot/nanobot/bus/queue.py
- nanobot 事件类型参考: /Users/kealdoom/Desktop/github/nanobot/nanobot/bus/events.py
- 现有 event_callback 签名分析: playground/mat_master/service/stream_agent.py, src/services/agent_run_service.py
- 现有 ToolGuard 接口分析: playground/mat_master/core/tool_guard.py
- 现有 Agent/AgentConfig 分析: evomaster/agent/agent.py
- 现有 BasePlayground 分析: evomaster/core/playground.py
- 现有 SSE 队列管理: src/services/stream_service.py (StreamQueueManager)
- 架构研究: .planning/research/ARCHITECTURE.md (EventBus 设计)
- 技术栈研究: .planning/research/STACK.md (discriminated union 验证)

### Secondary (MEDIUM confidence)
- 项目级研究摘要: .planning/research/SUMMARY.md (零新依赖策略)
- 现有 ConfirmationManager 事件契约: playground/mat_master/service/confirm.py

### Tertiary (LOW confidence)
- None -- Phase 1 使用的所有技术（Pydantic, Protocol, queue.Queue）都是成熟的标准库/框架功能

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 零新依赖，全部使用现有 Pydantic + stdlib
- Architecture: HIGH -- 目录结构和文件组织由 CONTEXT.md 锁定，事件类型映射基于代码分析
- Pitfalls: HIGH -- 5 个 pitfall 全部来自对现有代码的具体分析，每个都有明确的防范策略
- Event mapping: HIGH -- 基于对 stream_agent.py、agent_run_service.py、confirm.py 所有 _emit/event_callback 调用的逐行分析

**Research date:** 2026-03-21
**Valid until:** 2026-04-21 (stable patterns, no moving targets)
