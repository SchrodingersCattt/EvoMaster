# Phase 3: Exp Assembly Layer - Research

**Researched:** 2026-03-22
**Domain:** Python assembly/composition layer design -- Exp base class, ToolRegistry, ContextBuilder, Guard injection, WorkerRegistry Protocol
**Confidence:** HIGH

## Summary

Phase 3 构建 matmaster 三层架构的中间装配层。核心任务是实现 Exp base class 的 assemble() 方法，消费 Phase 1 定义的 PlaygroundContext，输出 Phase 1 定义的 AgentRuntimeSpec，供 Phase 2 的 AgentKernel.run() 消费。这是一个纯 Python 层间适配问题，不涉及外部库引入。

技术域分为五个子系统：(1) Exp base class + DirectExp 子类定义装配框架；(2) ToolRegistry 统一 builtin/MCP/skill 三类工具的扁平注册；(3) ContextBuilder 按固定段序组装 system prompt；(4) 业务 Guard 通过 assemble() 注入到 AgentRuntimeSpec.guards；(5) WorkerRegistry Protocol 定义（仅接口，不迁移业务代码）。所有这些子系统的输入输出契约已由 Phase 1/2 完整定义，Phase 3 只需在 `matmaster/assembly/` 目录下实现填充逻辑。

**Primary recommendation:** 按 nanobot 的 Tool ABC + ToolRegistry 模式实现，但适配 matmaster 的同步 threading 模型（Tool.execute 为同步方法），所有新代码放在 `matmaster/assembly/` 包下。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- ToolRegistry 统一注册：扁平注册表模型，所有工具统一注册到同一命名空间，通过 source 标签区分来源（builtin/mcp/skill）
- 同名工具冲突处理：后注册覆盖前者，assemble() 按顺序注册 builtin -> MCP -> skill，日志警告覆盖事件
- MCP 工具集成方式：装配时拉取——assemble() 时连接 MCP server，拉取 tool list，包装成 Tool 对象注册
- Tool 统一接口：定义 Tool Protocol（name + json_schema + execute），参考 nanobot 设计
- ContextBuilder 组装：分段 Builder 模式，拆分为多个段（identity/mode_contract/skills/tools/memory/task），每段独立生成，最后拼接
- 固定段顺序：identity -> mode_contract -> skills -> tools -> memory -> task
- direct/planner 模式差异：通过 mode_contract 段切换
- skills 段生成：ContextBuilder 接收 SkillRegistry 引用自己遍历生成 skills 段
- Solver 就是 Exp 子类：不同子类通过不同的 assemble() 策略适配不同任务类型
- Phase 3 交付范围：Exp base class + assemble() 框架 + 一个可工作的 DirectExp 子类
- assemble() 设计为可重复调用：每次用不同参数产生不同 spec
- PlannerExp 可完全 override run()
- run_interrupted 检测保留在 Service 层
- Bohrium 凭证加载保留在 Service 层，通过 PlaygroundContext.run_meta 传递
- Bohrium 凭证绑定到 session 在 Exp 层
- session_run_owner 管理：定义 WorkerRegistry Protocol（set/refresh/delete），Service 层提供 Redis 实现
- Phase 3 只建接口不迁移业务代码，Phase 5 统一迁移

### Claude's Discretion
- Tool Protocol 中 execute() 的具体签名（同步 vs 异步，参数类型）
- ContextBuilder 各段的具体文本格式和分隔符
- DirectExp 的具体 assemble() 实现细节
- ToolRegistry 内部存储结构

### Deferred Ideas (OUT OF SCOPE)
- PlannerExp 完全重构 -- 后续迭代，Phase 3 只建 base + DirectExp
- WorkerRegistry/Bohrium 业务逻辑实际迁移 -- Phase 5
- 工具并行执行 -- Phase 2 已延迟，继续延迟
- Context compaction 集成 -- CompactionConfig 已在 spec 中，具体策略留后续
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ASBL-01 | Exp base class 定义 assemble() 方法，消费 PlaygroundContext 输出 AgentRuntimeSpec | Exp base class 设计（Pattern 1）+ DirectExp 子类（Pattern 2）+ nanobot AgentLoop 参考 |
| ASBL-02 | ToolRegistry 统一 builtin/MCP/skill tools 的注册路径 | Tool Protocol + ToolRegistry 设计（Pattern 3）+ nanobot Tool/ToolRegistry 实现参考 |
| ASBL-03 | 业务 Guard 通过 AgentRuntimeSpec.guards 注入 | Guard 注入模式（Pattern 4）+ 现有 ToolGuard 6 关注点拆分策略 |
| ASBL-04 | Solver 模式收入 exp 层作为高阶装配模式 | Solver 作为 Exp 子类设计（Pattern 5）+ PlannerExp 预留点分析 |
| ASBL-05 | ContextBuilder 从 identity/skills/memory/task 多源组装 system prompt | ContextBuilder 分段组装（Pattern 6）+ nanobot ContextBuilder 参考 |
| ASBL-06 | WorkerRegistry Protocol 定义（仅接口，不迁移业务代码） | WorkerRegistry Protocol 设计（Pattern 7）+ 现有 worker_registry_service.py 接口分析 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | v2 (existing) | frozen model 定义 AgentRuntimeSpec 等契约 | 项目已用 Pydantic v2，Phase 1/2 建立的模式 |
| typing | stdlib | Protocol, runtime_checkable, ABC | 项目 Protocol 模式已确立（Guard, Hook, LLMProvider） |
| logging | stdlib | 日志 | 项目标准日志方式 |
| dataclasses | stdlib | 轻量内部数据结构 | 项目对非契约数据用 dataclass |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| abc | stdlib | Tool ABC base class | 当需要强制子类实现特定方法时 |
| json | stdlib | Tool arguments 解析、JSON Schema | Tool 定义和参数处理 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Protocol + ABC 混合 | 纯 Protocol | ABC 给出更清晰的"必须实现"信号，适合 Tool 这种需要大量子类的场景 |
| 手写 Builder | Pydantic model 做 builder | ContextBuilder 需要段级控制（启用/禁用/重排），Builder 模式更灵活 |
| dict 存储 ToolRegistry | 自定义数据结构 | dict 足够——工具数量 O(100)，查找 O(1)，无需复杂数据结构 |

**Installation:**
```bash
# 零新依赖。所有所需库已在项目依赖中。
```

## Architecture Patterns

### Recommended Project Structure
```
matmaster/
├── assembly/               # Phase 3 新代码位置
│   ├── __init__.py         # 导出 Exp, DirectExp, ToolRegistry, ContextBuilder 等
│   ├── exp.py              # Exp base class（assemble + run 框架）
│   ├── direct_exp.py       # DirectExp 子类（直接模式装配）
│   ├── tool_registry.py    # ToolRegistry + Tool Protocol/ABC
│   ├── context_builder.py  # ContextBuilder 分段组装器
│   ├── guards.py           # 业务 Guard 适配（ManuscriptGateGuard 等壳）
│   └── worker_registry.py  # WorkerRegistry Protocol（仅接口定义）
├── types/
│   ├── runtime.py          # AgentRuntimeSpec（需更新 tool_registry 字段类型）
│   ├── context.py          # PlaygroundContext（skill_registry 字段类型更新）
│   └── ...
├── engine/
│   └── ...                 # Phase 2 交付物，不修改
├── bus/
│   └── ...                 # Phase 1 交付物，不修改
└── providers/
    └── ...                 # Phase 2 交付物，不修改
```

### Pattern 1: Exp Base Class
**What:** 定义 assemble() 抽象方法和 run() 默认流程的基类
**When to use:** 所有 Exp 子类必须继承
**Example:**
```python
# Source: 项目 CONTEXT.md 决策 + nanobot AgentLoop 参考
import logging
from abc import ABC, abstractmethod
from typing import Any

from matmaster.types.context import PlaygroundContext
from matmaster.types.runtime import AgentRuntimeSpec
from matmaster.engine.agent import AgentKernel
from matmaster.types.events import FinishEvent

class Exp(ABC):
    """能力装配层基类。

    assemble() 消费 PlaygroundContext 输出 AgentRuntimeSpec，
    run() 默认流程：assemble -> kernel.run -> return。
    子类通过 override assemble() 定制装配策略，
    高级子类可 override run() 实现多步状态机。
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def exp_name(self) -> str:
        """Exp 名称，自动从类名推断。"""
        name = self.__class__.__name__
        return name[:-3] if name.endswith("Exp") else name

    @abstractmethod
    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        """装配 AgentRuntimeSpec。

        可重复调用：每次用不同 kwargs 产生不同 spec。
        PlannerExp 在每个 step 前可以重新 assemble。
        """
        ...

    def run(
        self,
        ctx: PlaygroundContext,
        task: str,
        *,
        stop_event: threading.Event | None = None,
        **assemble_kwargs: Any,
    ) -> FinishEvent:
        """默认执行流程：assemble -> kernel.run。"""
        spec = self.assemble(ctx, **assemble_kwargs)
        kernel = AgentKernel()
        return kernel.run(spec, task, stop_event=stop_event)
```

### Pattern 2: DirectExp 子类
**What:** 最简单的 Exp 实现，直接模式（非 planner）的完整装配
**When to use:** mat_master 和 minimal 的直接执行模式
**Example:**
```python
# DirectExp 装配流程
class DirectExp(Exp):
    """Direct 模式装配：builtin + MCP + skill tools，直接 system prompt。"""

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        builtin_tools: list[Tool] | None = None,
        guards: list[Guard] | None = None,
        max_turns: int = 100,
    ) -> None:
        super().__init__()
        self._llm_provider = llm_provider
        self._builtin_tools = builtin_tools or []
        self._guards = guards or []
        self._max_turns = max_turns

    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        # 1. 构建 ToolRegistry
        registry = ToolRegistry()
        for tool in self._builtin_tools:
            registry.register(tool, source="builtin")
        # MCP tools: 从 ctx.mcp_manager 拉取（Phase 4 提供）
        # Skill tools: 从 ctx.skill_registry 拉取
        # 2. 构建 system prompt
        builder = ContextBuilder()
        system_prompt = builder.build(ctx, registry, mode="direct")
        # 3. 构建 hooks (MessageBus + EventEmitterHook)
        bus = MessageBus()
        emitter_hook = EventEmitterHook(bus, source=self.exp_name)
        # 4. 组装 AgentRuntimeSpec
        return AgentRuntimeSpec(
            llm_provider=self._llm_provider,
            tool_registry=registry,
            guards=self._guards,
            max_turns=self._max_turns,
            hooks=[emitter_hook],
            system_prompt=system_prompt,
            mode="direct",
        )
```

### Pattern 3: Tool Protocol/ABC + ToolRegistry
**What:** 统一工具接口和扁平注册表
**When to use:** 所有工具（builtin/MCP/skill）必须实现 Tool 接口
**Key design decisions:**
```python
# Source: nanobot Tool ABC + 项目 CONTEXT.md 决策

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class Tool(Protocol):
    """工具统一接口。Kernel 只看到这个 Protocol。"""

    @property
    def name(self) -> str: ...

    @property
    def json_schema(self) -> dict[str, Any]: ...

    def execute(self, arguments: dict[str, Any]) -> str: ...
    # 同步方法：matmaster 在 ThreadPoolExecutor 中运行

class ToolRegistry:
    """扁平注册表：所有工具统一命名空间，source 标签区分来源。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: dict[str, str] = {}  # name -> source tag

    def register(self, tool: Tool, *, source: str = "unknown") -> None:
        if tool.name in self._tools:
            logger.warning(
                "Tool '%s' (source=%s) overridden by source=%s",
                tool.name, self._sources.get(tool.name), source,
            )
        self._tools[tool.name] = tool
        self._sources[tool.name] = source

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self._tools)}"
        return tool.execute(arguments)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """返回 OpenAI function calling 格式的工具定义列表。"""
        return [
            {"type": "function", "function": {"name": t.name, "description": ..., "parameters": t.json_schema}}
            for t in self._tools.values()
        ]

    def get_tools_by_source(self, source: str) -> list[Tool]:
        return [t for n, t in self._tools.items() if self._sources.get(n) == source]
```

### Pattern 4: Guard 注入
**What:** 业务 Guard 在 Exp.assemble() 中创建并注入到 AgentRuntimeSpec.guards
**When to use:** 需要业务语义门控（manuscript gate, auth failure gate 等）
**Key insight:** Kernel 的 GuardPipeline 已经支持 external_guards 注入（Phase 2 完成），Exp 层只需将业务 Guard 实例放入 spec.guards 列表
```python
# 现有 Guard Protocol 直接复用
# matmaster/types/guards.py 已定义：
#   Guard Protocol: evaluate(ctx: GuardContext) -> GuardResult
# 业务 Guard 只需实现这个 Protocol

class ManuscriptGateGuard:
    """Manuscript 完成门控 Guard（从 ToolGuard 拆分）。

    阻止 finish 调用当 manuscript sections 未验证。
    实现 Guard Protocol。
    """
    def evaluate(self, ctx: GuardContext) -> GuardResult:
        # 迁移自 ToolGuard.can_finish_manuscript() 逻辑
        ...

class AuthFailureGateGuard:
    """Auth failure 门控 Guard（从 ToolGuard 拆分）。

    连续认证失败后阻止进一步工具调用。
    """
    def evaluate(self, ctx: GuardContext) -> GuardResult:
        ...
```

### Pattern 5: Solver 作为 Exp 子类
**What:** Solver 模式（如 ResearchPlanner）作为 Exp 子类，通过不同的 assemble() 策略实现
**When to use:** 需要多步执行、每步重新装配的高级模式
**Phase 3 scope:** 只预留扩展点，不实际实现 PlannerExp
```python
# PlannerExp 预留设计（Phase 3 不实现，仅展示扩展点）
class PlannerExp(Exp):
    """Planner 模式：多步状态机，每步 assemble -> kernel.run。"""

    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        step = kwargs.get("step")
        mode = kwargs.get("mode", "planner")
        # 每步可用不同 prompt/tools/guards
        ...

    def run(self, ctx, task, *, stop_event=None, **kw) -> FinishEvent:
        # Override run() 实现多步状态机
        # Phase 3 不实现，留给后续迭代
        ...
```

### Pattern 6: ContextBuilder 分段组装
**What:** 按固定段序组装 system prompt
**When to use:** 所有 Exp.assemble() 构建 system prompt 时
**Example:**
```python
# Source: 项目 CONTEXT.md 决策 + nanobot ContextBuilder 参考

class ContextBuilder:
    """分段 system prompt 组装器。

    段序固定：identity -> mode_contract -> skills -> tools -> memory -> task
    每段独立生成、可选启用/禁用。
    """

    SEPARATOR = "\n\n---\n\n"  # 段间分隔符（参考 nanobot）

    def build(
        self,
        ctx: PlaygroundContext,
        tool_registry: ToolRegistry,
        *,
        mode: str = "direct",
        identity: str | None = None,
        skill_registry: Any = None,
        memory_context: str | None = None,
        task_context: str | None = None,
        disabled_sections: set[str] | None = None,
    ) -> str:
        disabled = disabled_sections or set()
        sections: list[str] = []

        if "identity" not in disabled:
            sections.append(self._build_identity(identity or self._default_identity()))
        if "mode_contract" not in disabled:
            sections.append(self._build_mode_contract(mode))
        if "skills" not in disabled and skill_registry:
            sections.append(self._build_skills(skill_registry))
        if "tools" not in disabled:
            sections.append(self._build_tools(tool_registry))
        if "memory" not in disabled and memory_context:
            sections.append(f"# Memory\n\n{memory_context}")
        if "task" not in disabled and task_context:
            sections.append(f"# Task Context\n\n{task_context}")

        return self.SEPARATOR.join(sections)
```

### Pattern 7: WorkerRegistry Protocol
**What:** 定义 session_run_owner 管理接口
**When to use:** Phase 3 仅定义 Protocol，Phase 5 提供 Redis 实现
```python
# Source: src/services/worker_registry_service.py 现有接口分析

@runtime_checkable
class WorkerRegistry(Protocol):
    """session_run_owner 管理接口。

    Phase 3 定义接口，Phase 5 提供 Redis 实现。
    Service 层依赖注入到 Exp。
    """

    def set_session_run_owner(self, session_id: str, worker_id: str) -> bool: ...
    def refresh_session_run_owner(self, session_id: str, worker_id: str) -> bool: ...
    def delete_session_run_owner(self, session_id: str) -> bool: ...
    def get_session_run_owner(self, session_id: str) -> str | None: ...
```

### Anti-Patterns to Avoid
- **Exp 内创建 LLMProvider:** LLMProvider 应由外部创建并传入 Exp 构造函数，Exp 不负责 API key 等配置
- **ToolRegistry 按来源分表:** 决策已锁定扁平模型，不要为 builtin/MCP/skill 建三个 dict
- **ContextBuilder 内硬编码 prompt 模板:** prompt 模板应作为参数传入或从文件加载，ContextBuilder 只负责组装逻辑
- **在 Tool.execute() 中做异步:** matmaster 运行在 ThreadPoolExecutor 同步线程中，Tool.execute() 必须是同步方法
- **Exp.run() 内持有过多状态:** run() 每次调用应从 assemble() 获取新鲜 spec，不要在 Exp 实例上累积可变状态

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Tool 参数验证 | 自己写 JSON Schema 验证器 | nanobot 的 validate_params + cast_params 模式 | nanobot 已有完整的 schema-driven 参数校验，包含类型转换和嵌套验证 |
| Tool 定义格式 | 自己拼 OpenAI function schema | to_schema() 方法（参考 nanobot Tool.to_schema） | 工具定义格式标准化，to_schema() 保证一致 |
| Guard 管道执行 | 自己在 Exp 层实现 guard 链 | Phase 2 的 GuardPipeline | GuardPipeline 已实现串联执行 + 短路逻辑 + 内置 LoopDetectionGuard |
| 事件桥接 | 自己在 Exp 层写事件转发 | Phase 2 的 EventEmitterHook + MessageBus | Exp 只需创建 MessageBus + EventEmitterHook，注入到 spec.hooks |

**Key insight:** Phase 1/2 已交付 Guard Protocol、GuardPipeline、Hook Protocol、EventEmitterHook、MessageBus。Phase 3 不需要重建这些机制，只需在 assemble() 中创建实例并注入到 AgentRuntimeSpec。

## Common Pitfalls

### Pitfall 1: AgentRuntimeSpec.tool_registry 类型更新
**What goes wrong:** 当前 `tool_registry: Any`，如果不更新为 ToolRegistry Protocol 或具体类型，kernel 的 `spec.tool_registry.execute()` 和 `spec.tool_registry.get_tool_definitions()` 调用依赖鸭子类型
**Why it happens:** Phase 1 留了 `Any` 占位符
**How to avoid:** Phase 3 定义好 Tool Protocol 和 ToolRegistry 后，更新 AgentRuntimeSpec 的 tool_registry 字段类型。但因为 `frozen=True` 和 `arbitrary_types_allowed=True` 已设置，Protocol 类型可以直接使用
**Warning signs:** mypy/type checker 在 kernel 代码中对 tool_registry 属性访问无法推断类型

### Pitfall 2: Tool.execute() 同步 vs 异步选择
**What goes wrong:** nanobot 的 Tool.execute() 是 async，直接照搬会导致 matmaster 的 ThreadPoolExecutor 同步模型里需要 event loop
**Why it happens:** nanobot 是异步架构，matmaster 是同步 threading 架构
**How to avoid:** Tool.execute() 必须是同步方法（返回 str），签名为 `execute(self, arguments: dict[str, Any]) -> str`。MCP 工具如果底层是 async，在包装类中用 `asyncio.run()` 或事先获取结果
**Warning signs:** 在 tool.execute() 中看到 await 或 async def

### Pitfall 3: ContextBuilder 段序与 Prompt Cache 冲突
**What goes wrong:** 如果段序不固定或动态排列，LLM 的 prompt caching（prefix caching）失效，导致每次调用都计算完整 prompt
**Why it happens:** LLM 对 prompt 开头的 token 做 KV cache，前缀变化 = cache miss
**How to avoid:** 段序锁定为 identity -> mode_contract -> skills -> tools -> memory -> task。变化频率高的段放后面（task/memory），稳定的放前面（identity/mode_contract）
**Warning signs:** 每次 LLM 调用的 prompt_tokens 计费异常高

### Pitfall 4: ToolRegistry 注册顺序与覆盖
**What goes wrong:** 如果 MCP tool 和 builtin tool 同名（如 execute_bash），注册顺序错误会导致错误的实现被使用
**Why it happens:** 扁平注册模型下同名覆盖
**How to avoid:** 严格按 builtin -> MCP -> skill 顺序注册（决策已锁定），每次覆盖打 warning 日志。在测试中验证注册顺序
**Warning signs:** 日志中频繁出现工具覆盖警告但行为不符预期

### Pitfall 5: Exp.assemble() 返回 frozen 对象后的修改企图
**What goes wrong:** AgentRuntimeSpec 是 `frozen=True`，assemble() 返回后不能修改。如果 run() 需要微调 spec，必须重新 assemble()
**Why it happens:** frozen model 的设计初衷就是防止运行时意外修改
**How to avoid:** assemble() 设计为接受 **kwargs 参数，每次调用可传不同参数产生不同 spec。不要试图 `spec.model_copy(update={...})`（虽然 Pydantic v2 支持，但违背 frozen 语义）
**Warning signs:** `ValidationError: Instance is frozen` 异常

### Pitfall 6: 循环导入（assembly <-> types）
**What goes wrong:** `matmaster/assembly/` 导入 `matmaster/types/runtime.py`，而后者可能导入 assembly 的类型
**Why it happens:** Phase 1/2 已有 TYPE_CHECKING guard 处理类似问题（kernel.py）
**How to avoid:** 使用 `if TYPE_CHECKING:` 延迟导入。AgentRuntimeSpec 的 tool_registry 类型可用 Protocol（定义在 assembly 或独立 types 文件中），运行时不导入
**Warning signs:** ImportError at module load time

## Code Examples

### Kernel 对 tool_registry 的调用方式（Phase 2 已确定）
```python
# Source: matmaster/engine/agent.py line 145 & 166-167
# Kernel 使用两个方法：
result = spec.tool_registry.execute(tc.name, tc.arguments)  # line 145
tool_defs = spec.tool_registry.get_tool_definitions()         # line 166
# ToolRegistry 必须提供这两个方法
```

### nanobot Tool ABC 参考（同步化适配）
```python
# Source: /Users/kealdoom/Desktop/github/nanobot/nanobot/agent/tools/base.py
# nanobot 的 Tool 有: name, description, parameters (JSON Schema), execute()
# 以及 to_schema() 产生 OpenAI function calling 格式
# matmaster 需要适配：execute 改为同步，添加 source 标签支持

# nanobot Tool.to_schema() 输出格式:
{
    "type": "function",
    "function": {
        "name": "tool_name",
        "description": "...",
        "parameters": {"type": "object", "properties": {...}, "required": [...]},
    },
}
```

### nanobot ToolRegistry 参考（同步化适配）
```python
# Source: /Users/kealdoom/Desktop/github/nanobot/nanobot/agent/tools/registry.py
# nanobot ToolRegistry:
#   register(tool) -> 存入 dict[str, Tool]
#   execute(name, params) -> 校验 + 执行 + 错误处理
#   get_definitions() -> list[dict] (OpenAI format)
# matmaster 增加: source 标签、覆盖警告、get_tools_by_source()
```

### nanobot ContextBuilder 段组装参考
```python
# Source: /Users/kealdoom/Desktop/github/nanobot/nanobot/agent/context.py
# nanobot 段序: identity -> bootstrap files -> memory -> skills
# 段间分隔: "\n\n---\n\n"
# matmaster 增加: mode_contract, tools, task 段
# matmaster 段序: identity -> mode_contract -> skills -> tools -> memory -> task
```

### 现有 Guard Protocol 复用
```python
# Source: matmaster/types/guards.py
# Guard Protocol 已定义：
#   evaluate(ctx: GuardContext) -> GuardResult
# GuardPipeline 已实现：
#   内置 LoopDetectionGuard + external_guards 注入
# Phase 3 只需：创建业务 Guard 实例 -> 放入 spec.guards 列表
```

### EventEmitterHook 注入模式
```python
# Source: matmaster/engine/hooks.py line 134-180
# Exp.assemble() 中创建:
bus = MessageBus()
emitter = EventEmitterHook(bus, source="direct_exp")
# 注入到:
spec = AgentRuntimeSpec(hooks=[emitter], ...)
# bus 对象需要暴露给外层（Phase 4 的 Playground 需要消费事件）
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| BaseExp 持有 agent + config，run() 直接调用 agent.run() | Exp.assemble() 输出 AgentRuntimeSpec，与 kernel 解耦 | Phase 3 (本次) | Exp 不再持有 agent 引用，只输出 spec |
| ToolGuard 6 关注点合一 | 拆分为独立 Guard，通过 spec.guards 注入 | Phase 3 (本次) | kernel 无需感知业务语义 |
| compose_mat_master_system_prompt() 硬编码模板替换 | ContextBuilder 分段组装，模板可配置 | Phase 3 (本次) | 段级可控，支持 direct/planner 切换 |
| AsyncToolRegistry 分类 MCP 工具 | ToolRegistry 扁平注册所有工具 | Phase 3 (本次) | 统一工具接口，kernel 不区分来源 |

**Deprecated/outdated:**
- `evomaster/core/exp.py` BaseExp: 被新 Exp base class 替代，Phase 5 通过 compat adapter 桥接
- `playground/mat_master/core/tool_guard.py` ToolGuard: 拆分为独立 Guard 实例，通过 spec.guards 注入

## Open Questions

1. **MCP 工具的同步包装策略**
   - What we know: MCP 客户端通常是 async，matmaster 需要同步 execute()
   - What's unclear: Phase 3 是否需要实现 MCP 工具包装，还是留给 Phase 4/5
   - Recommendation: Phase 3 定义 Tool Protocol 和 ToolRegistry，提供 MCP 工具包装的接口预留点（如 McpToolWrapper 类骨架），但不实际连接 MCP server。Phase 4 的 Playground 负责 MCP manager 初始化和工具拉取

2. **skill_registry 字段类型化**
   - What we know: PlaygroundContext.skill_registry 当前是 `Any`，需要更新
   - What's unclear: skill_registry 的 Protocol 接口应包含哪些方法
   - Recommendation: Phase 3 定义 SkillRegistry Protocol（get_all_skills, get_meta_info_context），Phase 4 的 MatMasterSkillRegistry 实现它

3. **Exp 持有 MessageBus 引用的生命周期**
   - What we know: assemble() 中创建 MessageBus + EventEmitterHook，但 MessageBus 需要被 Phase 4 的 Playground/Service 层消费
   - What's unclear: MessageBus 应该由谁创建——Exp 还是外部注入
   - Recommendation: MessageBus 由外部（Phase 4 的 Playground/Service）创建并传入 Exp。Exp.assemble() 接收 MessageBus 作为参数，用它创建 EventEmitterHook

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing, configured in pytest.ini) |
| Config file | `pytest.ini` (pythonpath=., testpaths=tests, asyncio_mode=auto) |
| Quick run command | `python -m pytest tests/matmaster/assembly/ -x -q` |
| Full suite command | `python -m pytest tests/matmaster/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ASBL-01 | Exp.assemble() 接收 PlaygroundContext 输出 AgentRuntimeSpec | unit | `python -m pytest tests/matmaster/assembly/test_exp.py -x` | Wave 0 |
| ASBL-01 | DirectExp.run() 完整流程（assemble -> kernel.run） | integration | `python -m pytest tests/matmaster/assembly/test_direct_exp.py -x` | Wave 0 |
| ASBL-02 | ToolRegistry 注册/执行/定义导出 | unit | `python -m pytest tests/matmaster/assembly/test_tool_registry.py -x` | Wave 0 |
| ASBL-02 | 同名工具覆盖 + source 标签 | unit | `python -m pytest tests/matmaster/assembly/test_tool_registry.py::test_override_warning -x` | Wave 0 |
| ASBL-03 | 业务 Guard 通过 spec.guards 注入后被 GuardPipeline 执行 | integration | `python -m pytest tests/matmaster/assembly/test_guard_injection.py -x` | Wave 0 |
| ASBL-04 | Exp 子类化验证（DirectExp 继承 Exp） | unit | `python -m pytest tests/matmaster/assembly/test_exp.py::test_subclass -x` | Wave 0 |
| ASBL-05 | ContextBuilder 分段组装 + 段序固定 | unit | `python -m pytest tests/matmaster/assembly/test_context_builder.py -x` | Wave 0 |
| ASBL-05 | ContextBuilder 段启用/禁用 | unit | `python -m pytest tests/matmaster/assembly/test_context_builder.py::test_disable_sections -x` | Wave 0 |
| ASBL-06 | WorkerRegistry Protocol 定义 + isinstance 检查 | unit | `python -m pytest tests/matmaster/assembly/test_worker_registry.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/matmaster/assembly/ -x -q`
- **Per wave merge:** `python -m pytest tests/matmaster/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/assembly/__init__.py` -- package init
- [ ] `tests/matmaster/assembly/conftest.py` -- shared fixtures (MockTool, MockSkillRegistry, mock PlaygroundContext builder)
- [ ] `tests/matmaster/assembly/test_tool_registry.py` -- covers ASBL-02
- [ ] `tests/matmaster/assembly/test_context_builder.py` -- covers ASBL-05
- [ ] `tests/matmaster/assembly/test_exp.py` -- covers ASBL-01, ASBL-04
- [ ] `tests/matmaster/assembly/test_direct_exp.py` -- covers ASBL-01 integration
- [ ] `tests/matmaster/assembly/test_guard_injection.py` -- covers ASBL-03
- [ ] `tests/matmaster/assembly/test_worker_registry.py` -- covers ASBL-06
- [ ] 更新 `matmaster/types/runtime.py` 中 tool_registry 字段类型

## Sources

### Primary (HIGH confidence)
- `matmaster/types/runtime.py` -- AgentRuntimeSpec 当前定义，tool_registry: Any 需更新
- `matmaster/types/context.py` -- PlaygroundContext 定义，skill_registry: Any 需更新
- `matmaster/types/guards.py` -- Guard Protocol 定义，业务 Guard 直接实现此 Protocol
- `matmaster/engine/agent.py` -- AgentKernel.run()，Phase 3 输出的 spec 的消费者，行 145 和 166 确定 tool_registry 需要的方法
- `matmaster/engine/hooks.py` -- EventEmitterHook 实现，Exp 层创建并注入到 spec
- `matmaster/engine/guard_pipeline.py` -- GuardPipeline 实现，接受 external_guards
- `matmaster/bus/queue.py` -- MessageBus 同步事件总线
- nanobot `/Users/kealdoom/Desktop/github/nanobot/nanobot/agent/tools/base.py` -- Tool ABC 参考
- nanobot `/Users/kealdoom/Desktop/github/nanobot/nanobot/agent/tools/registry.py` -- ToolRegistry 参考
- nanobot `/Users/kealdoom/Desktop/github/nanobot/nanobot/agent/context.py` -- ContextBuilder 参考
- nanobot `/Users/kealdoom/Desktop/github/nanobot/nanobot/agent/skills.py` -- SkillsLoader 参考

### Secondary (MEDIUM confidence)
- `evomaster/core/exp.py` -- 现有 BaseExp，理解旧 Exp 模型以设计新 Exp
- `playground/mat_master/core/registry.py` -- MatMasterSkillRegistry 4 层优先级，理解 skill 数据模型
- `playground/mat_master/prompts/build_prompt.py` -- 现有 prompt 组装逻辑，理解段内容
- `playground/mat_master/core/tool_guard.py` -- 现有 ToolGuard 6 关注点，理解业务 Guard 需求
- `playground/mat_master/core/solvers/research_planner.py` -- ResearchPlanner 状态机，理解 PlannerExp 预留需求
- `src/services/worker_registry_service.py` -- WorkerRegistry 当前 Redis 实现，理解接口契约

### Tertiary (LOW confidence)
- 无

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 零新依赖，全部使用项目已有的 stdlib + pydantic
- Architecture: HIGH - 所有输入输出契约由 Phase 1/2 明确定义，设计空间受限于 CONTEXT.md 锁定决策
- Pitfalls: HIGH - 基于对现有代码的深入阅读（kernel 使用 tool_registry 的方式、frozen model 限制、同步 threading 模型等）
- Validation: HIGH - 现有 tests/matmaster/ 目录结构和测试模式已确立（conftest.py mock patterns）

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable domain, pure Python architecture, no external dependency changes)
