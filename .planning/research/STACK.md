# Technology Stack

**Project:** MatMaster Agent Framework Refactoring (v2)
**Researched:** 2026-03-21
**Research Mode:** Ecosystem -- Stack dimension for modular agent kernel in Python

## Executive Summary

MatMaster 的重构目标是将 playground/exp/agent 三层抽象拆分为职责清晰的组件，参考 nanobot kernel 设计。核心技术决策围绕五个维度展开：层间类型化契约、工具注册系统、LLM provider 抽象、事件总线、上下文管理。

**关键结论：** 现有技术栈（Python 3.13 + Pydantic + FastAPI）已经足够支撑重构目标。重构的核心不是引入新框架，而是用 Pydantic frozen model + Protocol 接口重建层间边界。唯一需要新引入的外部依赖是事件总线（推荐自建，参考 nanobot 的 asyncio.Queue 方案），其他全部基于现有依赖完成。

## Recommended Stack

### Core Runtime (保持不变)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.13 (>=3.10) | Runtime | 已部署在生产环境，3.13 支持改进的错误提示和 typing 增强 |
| uv | latest | Package manager | 已在用，lockfile 确定性构建 |

**Confidence:** HIGH -- 直接从 pyproject.toml 和 .python-version 验证

### 层间类型化契约 (Typed Contracts)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Pydantic | >=2.12 (pin <3) | PlaygroundContext / AgentRuntimeSpec 定义 | 已在依赖中，frozen model 提供不可变性 + 运行时验证 + JSON Schema 导出，比 dataclass 多验证层 |
| typing.Protocol | stdlib | LLMProvider / ToolExecutor / EventBus 接口定义 | 结构化子类型(structural subtyping)，不强制继承，允许 nanobot 风格的轻量 Tool 类直接满足协议 |
| Literal / Discriminated Union | Pydantic v2 | 事件类型区分、配置多态 | 用 type 字段做 discriminator，避免 isinstance 链，Pydantic v2 原生高效支持 |

**Confidence:** HIGH -- Pydantic v2 discriminated union 和 frozen model 在官方文档中有明确说明，Protocol 是 Python 标准库

**设计决策：**

PlaygroundContext 和 AgentRuntimeSpec 使用 `Pydantic BaseModel(frozen=True)` 而非 `@dataclass(frozen=True)`，原因：

1. 运行时验证：Pydantic 在构造时自动验证字段类型和约束，dataclass 不做验证
2. 项目一致性：matmaster 已经全面使用 Pydantic（AgentConfig、LLMConfig 等全是 BaseModel）
3. JSON Schema 导出：Pydantic 的 model_json_schema() 可直接生成 OpenAPI 文档
4. 精细冻结控制：Pydantic 支持 Field(frozen=True) 按字段冻结，dataclass 只能全冻结或全不冻结

接口定义使用 `typing.Protocol` 而非 `ABC`，原因：

1. 不强制继承：现有 BaseTool、BaseLLM 的子类可以直接满足 Protocol 而无需改动继承链
2. 适配层友好：compatibility adapter 可以让旧类满足新 Protocol 而不修改旧代码
3. mypy/pyright 静态检查足够：不需要 runtime_checkable（避免 isinstance 性能开销）
4. nanobot 的 Tool 基类就是这种模式 -- 通过 name/description/parameters/execute 属性定义契约

```python
# 推荐的 Protocol 定义风格
from typing import Protocol, Any

class ToolExecutor(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def parameters(self) -> dict[str, Any]: ...
    async def execute(self, **kwargs: Any) -> str: ...

class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> "LLMResponse": ...

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> "LLMResponse": ...
```

```python
# 推荐的 frozen contract 风格
from pydantic import BaseModel, ConfigDict, Field

class PlaygroundContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    workdir: Path
    session_type: Literal["docker", "local", "ssh"]
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    run_meta: dict[str, Any] = Field(default_factory=dict)

class AgentRuntimeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_config: PromptConfig
    tool_registry: ToolRegistry  # 不冻结内部状态，只冻结引用
    llm_provider: LLMProvider
    termination_policy: TerminationPolicy
    hooks: list[AgentHook] = Field(default_factory=list)
    guards: list[Guard] = Field(default_factory=list)
```

### 工具注册系统 (Tool Registry)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Pydantic BaseModel | >=2.12 | 工具参数 schema 生成 | 现有 BaseToolParams 已用此方案，model_json_schema() 直接导出 OpenAI function schema |
| 自建 ToolRegistry | -- | 注册制工具管理 | 现有实现已成熟，nanobot 的 ToolRegistry 也是同一模式（dict[str, Tool]），无需引入外部库 |

**Confidence:** HIGH -- 两个代码库（matmaster 现有的和 nanobot 参考的）都验证了同一模式

**设计决策：**

保持现有的 ToolRegistry 模式（register/get/execute/get_definitions），但做以下调整：

1. Tool.execute 改为 async：matmaster 现有的 BaseTool.execute 是同步的（返回 tuple[str, dict]），nanobot 的是 async（返回 str）。重构应统一为 async，因为 MCP 工具调用本身就是 async 的
2. 去掉 session 参数穿透：现有的 execute(session, args_json) 把 session 作为参数传入每个工具，这违反了 agent kernel 不关心环境的原则。session 应在工具实例化时注入（通过构造函数或 RunContext）
3. 参数验证用 Pydantic：保持现有的 params_class 模式（BaseTool 有 params_class: ClassVar[type[BaseToolParams]]），这比 nanobot 的手写 JSON Schema 更安全
4. MCP 工具作为 adapter：MCPTool 实现 ToolExecutor Protocol，在 execute 内部调用 MCP client

**不使用的方案：**

- LangChain tool decorator：过度抽象，引入大量传递依赖，matmaster 不需要 LangChain 生态
- PydanticAI 的 @agent.tool：PydanticAI 是完整的 agent 框架，不是可组合的 tool 库，引入它意味着重写整个 agent loop

### LLM Provider 抽象

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| 自建 LLMProvider Protocol | -- | 统一接口定义 | 参考 nanobot 的 LLMProvider ABC：chat() + chat_with_retry()，但改用 Protocol |
| openai SDK | latest | OpenAI provider 实现 | 已在依赖中，直接调用 |
| anthropic SDK | latest | Anthropic provider 实现 | 已在依赖中，直接调用 |
| google-genai SDK | latest | Google provider 实现 | 已在依赖中，直接调用 |

**Confidence:** HIGH -- 基于对两个代码库的直接分析

**关键决策：不引入 LiteLLM**

matmaster 现有架构直接使用原生 SDK（openai、anthropic、google-genai），每个 provider 有独立实现类。nanobot 引入了 LiteLLM 作为统一层。对于 matmaster 重构，推荐保持原生 SDK 路线，理由：

1. 已有投入：matmaster 的 provider 实现已经处理了 reasoning_protocol、model_family、temperature_policy 等复杂逻辑，这些是 LiteLLM 无法覆盖的领域特定行为
2. 控制力：原生 SDK 对流式输出、thinking blocks、prompt caching 的控制更精细，LiteLLM 的抽象层会遮蔽这些差异
3. 依赖体积：LiteLLM 1.82.x 是一个重量级依赖（引入大量子依赖），matmaster 只用 3 个 provider
4. 调试成本：当 provider 返回异常行为时，直接看原生 SDK 的 request/response 比透过 LiteLLM 层调试更直接

但是，如果未来需要支持 10+ 个 provider（如 Azure、Bedrock、Ollama 等），LiteLLM 会成为更好的选择。当前 3 provider 规模下，原生 SDK 是正确的选择。

**推荐的 Provider 结构：**

```
evomaster/kernel/providers/
    __init__.py
    base.py          # LLMProvider Protocol + LLMResponse dataclass
    openai.py        # OpenAIProvider 实现
    anthropic.py     # AnthropicProvider 实现
    google.py        # GoogleProvider 实现
    factory.py       # create_provider(config: LLMConfig) -> LLMProvider
```

统一响应类型使用 dataclass（而非 Pydantic），因为它是内部传递对象不需要验证：

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None
```

### 事件总线系统 (Event Bus / MessageBus)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| asyncio.Queue | stdlib | 核心传输机制 | nanobot 验证了 asyncio.Queue 足以支撑 agent 事件流，零外部依赖 |
| Pydantic BaseModel | >=2.12 | 事件类型定义 | discriminated union 区分事件类型，运行时验证 |
| 自建 MessageBus | -- | 发布/订阅解耦 | 参考 nanobot 的 MessageBus 设计，但扩展为支持多消费者的 topic-based 订阅 |

**Confidence:** MEDIUM -- nanobot 的 MessageBus 验证了基础可行性，但 matmaster 的需求更复杂（流式 callback、Redis pub/sub 跨 worker）

**设计决策：自建而非使用 bubus**

评估了 bubus（browser-use 出品，Pydantic-powered event bus，v1.6.0）：

优点：production-ready，支持 async/sync handlers，Pydantic 类型安全，WAL 持久化
缺点：增加外部依赖，其功能范围（bus forwarding、WAL persistence）超出 matmaster 需求

matmaster 的 MessageBus 需求比较具体：

1. agent kernel 发射事件（thought/tool_call/tool_result/finish）到 bus
2. 调用方（Web Service 层）从 bus 消费事件进行流式推送
3. 进程内通信（同一 worker 内），跨 worker 用现有的 Redis pub/sub

这正好是 nanobot 的 asyncio.Queue 方案能覆盖的范围。自建 ~60 行代码，零外部依赖。

**推荐的事件类型定义：**

```python
from typing import Literal, Union
from pydantic import BaseModel, Field

class ThoughtEvent(BaseModel):
    type: Literal["thought"] = "thought"
    content: str
    reasoning: str | None = None

class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_name: str
    arguments: dict[str, Any]
    call_id: str

class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_name: str
    result: str
    call_id: str
    is_error: bool = False

class FinishEvent(BaseModel):
    type: Literal["finish"] = "finish"
    status: str
    final_content: str | None = None

AgentEvent = Union[ThoughtEvent, ToolCallEvent, ToolResultEvent, FinishEvent]
# Pydantic discriminated union: Field(discriminator="type")
```

```python
import asyncio
from typing import AsyncIterator

class MessageBus:
    """进程内 async 事件总线，agent kernel 发射事件，调用方消费"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def emit(self, event: AgentEvent) -> None:
        await self._queue.put(event)

    async def consume(self) -> AgentEvent:
        return await self._queue.get()

    async def stream(self) -> AsyncIterator[AgentEvent]:
        """持续消费事件直到收到 FinishEvent"""
        while True:
            event = await self._queue.get()
            yield event
            if isinstance(event, FinishEvent):
                break
```

### 上下文管理 (Context Management)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| 自建 ContextBuilder | -- | 多源组装 system prompt | 参考 nanobot 的 ContextBuilder：identity + bootstrap + memory + skills 分段组装 |
| Pydantic BaseModel | >=2.12 | PromptConfig 定义 | 结构化 prompt 配置（identity, skills, memory, task 各段） |

**Confidence:** HIGH -- nanobot 和 matmaster 都已有 ContextBuilder 实现，重构只需统一接口

**设计决策：**

现有的 ContextManager（evomaster/agent/context.py）负责 compaction 和 window 管理，与 ContextBuilder（负责 prompt 组装）是不同职责。重构应拆分为：

1. ContextBuilder：从 PromptConfig 组装 system prompt（纯函数，无状态）
2. ContextManager：管理对话窗口、触发 compaction（有状态，属于 agent kernel 内部）

### Supporting Libraries (保持不变)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| FastAPI | >=0.100.0 | Web Service 层 | 不重构，保持现状 |
| Uvicorn | >=0.22.0 | ASGI server | 不重构 |
| PyMySQL | >=1.1.2 | 持久化 | 不重构 |
| redis | >=5.0.0 | 跨 worker 协调 | 不重构 |
| PyYAML | latest | 配置解析 | 保持 |
| mcp | >=1.0 (pin <2) | MCP 协议集成 | 保持，注意 v2 在开发中（目前 pre-alpha），应 pin 到 1.x |
| python-dotenv | latest | 环境变量 | 保持 |

**Confidence:** HIGH -- 直接从 pyproject.toml 验证

### Dev Dependencies (保持 + 增强)

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| pytest | >=9.0.2 | 测试 | 已在依赖中 |
| pytest-asyncio | >=0.24 | async 测试 | 新 ToolRegistry 和 MessageBus 都是 async，必须有 |
| mypy | >=1.13 | 静态类型检查 | Protocol-based 架构需要 mypy 验证类型正确性 |
| pre-commit | >=4.5.1 | Git hooks | 已在依赖中 |

**Confidence:** MEDIUM -- pytest-asyncio 和 mypy 版本需在安装时确认最新

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| 层间契约 | Pydantic frozen model | @dataclass(frozen=True) | dataclass 不做运行时验证，项目已全面使用 Pydantic |
| 层间契约 | Pydantic frozen model | TypedDict | TypedDict 是纯类型提示无运行时验证，且不能定义方法 |
| 接口定义 | typing.Protocol | ABC | Protocol 不强制继承，适配层更灵活，nanobot 的 Tool 类本身就是结构化满足 |
| 工具注册 | 自建 ToolRegistry | LangChain tools | 引入巨大传递依赖，matmaster 只需简单注册制 |
| 工具注册 | 自建 ToolRegistry | PydanticAI toolsets | PydanticAI 是完整 agent 框架，不可拆分使用工具子系统 |
| LLM 抽象 | 原生 SDK + Protocol | LiteLLM | 3 个 provider 不值得引入重量级依赖，已有领域特定的 provider 逻辑 |
| LLM 抽象 | 原生 SDK + Protocol | aisuite | 比 LiteLLM 轻但社区较小，同样不值得为 3 provider 引入 |
| 事件总线 | 自建 asyncio.Queue | bubus | 60 行自建代码 vs 额外依赖，matmaster 需求不复杂 |
| 事件总线 | 自建 asyncio.Queue | lahja | 面向多进程 IPC，matmaster 是单进程内事件 + Redis 跨进程 |
| Context | 自建 ContextBuilder | LangChain prompt templates | 引入不必要依赖，prompt 组装逻辑是项目特定的 |
| 日志 | stdlib logging | structlog / loguru | structlog 更好但引入新依赖和迁移成本，现有 logging 配置在 src/utils/logger.py 中已稳定运行 |

## What NOT to Use

### 1. LangChain / LangGraph
虽然 LangGraph 是 2025 年 agent 编排的主流选择，但 matmaster 的目标是轻量 kernel 重构而非引入编排框架。LangGraph 的图执行模型与 matmaster 的 playground/exp/agent 三层模型不兼容。如果未来做多 agent 编排，可以考虑，但那在 Out of Scope 中。

### 2. LiteLLM
如上分析，3 个 provider 规模下原生 SDK 更合适。LiteLLM 1.82.x 引入 50+ 子依赖，且其抽象层会遮蔽 matmaster 已实现的 reasoning_protocol / temperature_policy 等精细控制。

### 3. CrewAI / AG2 / Google ADK
这些都是完整的 agent 框架，不是可组合的库。matmaster 本身就是 agent 框架，引入另一个框架没有意义。

### 4. SQLAlchemy / Alembic
数据库层（PyMySQL）在 Out of Scope 中，不做改动。

### 5. Pydantic Settings
现有的 config 加载逻辑（PyYAML + python-dotenv + 自定义 LLMConfig）已经稳定运行，pydantic-settings 的 .env 加载和 nested settings 功能在这里不会带来足够收益来证明迁移成本。

## How Existing Stack Fits Into Refactored Architecture

```
                           现有依赖 → 重构角色
                           ─────────────────────

  Pydantic              →  PlaygroundContext, AgentRuntimeSpec, 事件类型 (frozen models)
  typing.Protocol       →  LLMProvider, ToolExecutor, Guard 接口定义
  openai/anthropic/     →  LLMProvider Protocol 的具体实现（从 utils/llm.py 拆出）
    google-genai
  asyncio.Queue         →  MessageBus 核心传输
  mcp                   →  MCPTool adapter（实现 ToolExecutor Protocol）
  PyYAML                →  配置文件解析（Playground 层读取 config.yaml）
  FastAPI               →  Web Service 层消费 MessageBus 事件进行 SSE/WebSocket 推送
  redis                 →  跨 worker 停止信号、session 协调（不在重构范围内，保持现状）
  PyMySQL               →  持久化层（不在重构范围内，保持现状）
```

## Installation

```bash
# 无需新增核心依赖
# 现有 pyproject.toml 的 dependencies 已包含所有需要的库

# 新增 dev 依赖（建议添加到 [project.optional-dependencies] dev 中）
uv add --dev pytest-asyncio mypy
```

```toml
# pyproject.toml 中 dev 依赖建议更新为：
[project.optional-dependencies]
dev = [
    "pre-commit>=4.5.1",
    "pytest>=9.0.2",
    "pytest-asyncio>=0.24",
    "mypy>=1.13",
]
```

## MCP SDK Version Pinning Warning

当前 pyproject.toml 中 `mcp` 没有上限约束。MCP Python SDK v2 目前处于 pre-alpha 阶段（计划 Q1 2026 发布），v2 可能有 breaking changes。建议立即 pin 到 `mcp>=1.0,<2` 避免意外升级。

**Confidence:** MEDIUM -- v2 时间表来自 WebSearch，具体 breaking changes 范围未确认

## Sources

### Verified with Official Documentation (HIGH confidence)
- Pydantic v2 frozen models and discriminated unions: https://docs.pydantic.dev/latest/concepts/unions/
- Python typing.Protocol specification: https://typing.python.org/en/latest/spec/protocol.html
- MCP Python SDK releases: https://github.com/modelcontextprotocol/python-sdk/releases
- Pydantic PyPI (v2.12.5 stable, v2.13.0b2 beta): https://pypi.org/project/pydantic/

### Verified with Code Analysis (HIGH confidence)
- nanobot kernel architecture: /Users/kealdoom/Desktop/github/nanobot/nanobot/ (agent/loop.py, bus/queue.py, providers/base.py, agent/tools/registry.py)
- matmaster existing stack: evomaster/agent/tools/base.py, evomaster/utils/llm.py, pyproject.toml

### From Web Research (MEDIUM confidence)
- LiteLLM v1.82.4: https://pypi.org/project/litellm/
- bubus v1.6.0: https://pypi.org/project/bubus/
- structlog v25.5.0: https://www.structlog.org/
- Agent framework landscape 2025: https://medium.com/@hieutrantrung.it/the-ai-agent-framework-landscape-in-2025-what-changed-and-what-matters-3cd9b07ef2c3
- PydanticAI toolsets: https://ai.pydantic.dev/toolsets/

---

*Stack analysis: 2026-03-21 -- Ecosystem research for MatMaster agent framework refactoring*
