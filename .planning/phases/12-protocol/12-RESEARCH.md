# Phase 12: Protocol 层 + 测试基础设施 - Research

**Researched:** 2026-03-26
**Domain:** Python async Protocol 定义, pytest-asyncio 测试基础设施, runtime async validation
**Confidence:** HIGH

## Summary

Phase 12 的核心任务是将 6 个 Protocol 的方法签名从 sync 改为 async def，同时建立 pytest-asyncio 测试基础设施和 async validation helper。研究确认了三个关键技术事实：

1. Python 的 `@runtime_checkable` Protocol 的 `isinstance()` 无法区分 sync 和 async 实现（两者都返回 True），这意味着仅靠类型系统无法捕获 sync/async 不匹配错误。`inspect.iscoroutinefunction` 是唯一可靠的运行时检测手段。

2. pytest-asyncio 当前最新稳定版为 1.3.0（requires Python >=3.10，项目使用 3.13.2 满足要求）。项目 pytest.ini 已配置 `asyncio_mode = auto`，但 pytest-asyncio 尚未安装为依赖。1.x 版本移除了 `event_loop` fixture，默认模式为 strict，但 auto 模式仍完全支持。

3. ABC 的 `@abstractmethod` 同样不强制 async/sync 匹配——sync 实现继承 async abstract method 不会报错，Python 静默接受。这进一步证实了 validation helper 的必要性。

**Primary recommendation:** 安装 pytest-asyncio>=1.3.0 到 dev 依赖，改 6 个 Protocol 签名为 async def，实现基于 `__protocol_attrs__` + `inspect.iscoroutinefunction` 的 validation helper，创建 tests/conftest.py 提供 async mock factories。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Phase 12 只改 Protocol 定义和 BuiltinTool ABC 签名。现有实现保持 sync，不添加 async 壳。后续 Phase 13-18 逐步将实现改为真正的 async。测试用 async mock 验证新 Protocol。
- **D-02:** 改 6 个 Protocol：LLMProvider, Tool, Hook, Guard（REQUIREMENTS 定义） + EventHandler, ReplyQueueLike（Phase 15/16 需要，提前改签名省事）。WorkerRegistry 属于 src/ 层，不在 v2.0 范围，不改。
- **D-03:** Guard Protocol 的 evaluate() 保持同步不变（明确决策：纯计算无 I/O，async 增加开销无收益）。
- **D-04:** chat_with_retry() 从 LLMProvider Protocol 移除，同时从 OpenAIProvider 实现中也删除。重试逻辑已在 Kernel._call_llm() 中，Provider 级别的 retry 是冗余的。
- **D-05:** Tool Protocol 保持 execute 命名（不改为 run），REQUIREMENTS 中 PROT-02 的 run() 是笔误。Tool Protocol.execute() 和 BuiltinTool ABC 的 execute()/_execute() 全部改为 async def。
- **D-06:** Protocol 签名变更包括返回类型。LLMProvider.chat_stream() 返回类型从 Iterator[StreamChunk] 改为 AsyncIterator[StreamChunk]。这是合约定义的一部分，在 Phase 12 一并处理。
- **D-07:** 实现实例级 async validation helper：validate_async_protocol(obj, protocol_cls)。给定实例和 Protocol 类，检查实例的每个 Protocol 方法是否是 async def（通过 inspect.iscoroutinefunction）。定位：测试工具 + Exp.assemble() 组装时的早期检测。
- **D-08:** 创建 tests/conftest.py 提供 async mock factories（mock async LLMProvider, mock async Tool, mock async Hook），供后续阶段直接复用。加上 async validation helper 自身的测试。pytest.ini 已有 asyncio_mode=auto，确认生效即可。

### Claude's Discretion
- async mock factories 的具体实现细节（AsyncMock vs 手写 async def）
- validation helper 的错误消息格式
- conftest.py 中 fixture scope 选择
- Hook Protocol 各方法的返回类型是否需要调整（当前大部分返回 None）

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PROT-01 | LLMProvider Protocol 的 chat() 和 chat_stream() 改为 async def，移除 chat_with_retry() | 已验证现有 LLMProvider 有 3 个方法（chat, chat_with_retry, chat_stream），需移除 chat_with_retry 并将 chat/chat_stream 改为 async。chat_stream 返回类型需从 Iterator 改为 AsyncIterator。D-04 同时要求从 OpenAIProvider 删除 chat_with_retry 实现。 |
| PROT-02 | Tool Protocol 的 execute() 改为 async def，BuiltinTool ABC 的 execute()/_execute() 改为 async def | Tool Protocol 中 execute 是唯一需要改的方法（name/description/json_schema 是 property，保持不变）。BuiltinTool ABC 的 execute()（公共入口）和 _execute()（抽象方法）都需要改为 async def。D-05 确认不改名为 run。 |
| PROT-03 | Hook Protocol 全部 7 个方法改为 async def | 已验证 7 个方法：pre_tool_call, post_tool_call, pre_llm_call, should_continue, on_stream_chunk, on_segment_complete, on_guard_blocked。BaseHook 默认实现也需同步改为 async def。run_* helper 函数需改为 async 并 await hook 调用。 |
| PROT-04 | Guard Protocol 的 evaluate() 保持同步 | 已确认 Guard.evaluate() 为纯计算（GuardContext -> GuardResult），无 I/O。D-03 锁定保持 sync。validation helper 需正确跳过 Guard 的 sync 方法验证。 |
| PROT-05 | async Protocol runtime validation helper | 研究确认 runtime_checkable isinstance() 无法区分 sync/async（已实测验证）。`__protocol_attrs__` + `inspect.iscoroutinefunction` 是可靠的检测组合。需处理 property 跳过（Tool Protocol 的 name/description/json_schema）。 |
| TEST-01 | pytest-asyncio 基础设施配置 + async 测试可运行 | pytest-asyncio 1.3.0 可用但未安装。需添加到 pyproject.toml dev 依赖。pytest.ini 已有 asyncio_mode=auto。1.x 移除了 event_loop fixture（本项目不受影响，因为是全新安装）。需创建 tests/conftest.py。 |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest-asyncio | 1.3.0 | async test support for pytest | pytest 官方推荐的 asyncio 测试插件，auto mode 自动识别 async def test |
| pytest | 9.0.2 (已安装) | 测试框架 | 已在项目中使用 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| inspect (stdlib) | Python 3.13 builtin | iscoroutinefunction 检测 | validation helper 核心依赖 |
| typing (stdlib) | Python 3.13 builtin | AsyncIterator, Protocol | Protocol 签名返回类型 |
| unittest.mock (stdlib) | Python 3.13 builtin | AsyncMock | async mock factory 构建 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pytest-asyncio | anyio + pytest-anyio | REQUIREMENTS 明确排除 anyio/trio，坚持 asyncio 标准库 |
| AsyncMock | 手写 async def mock | AsyncMock 更简洁但对 spec 支持有限；手写 async def 更明确更可控 |

**Installation:**
```bash
uv add --group dev pytest-asyncio>=1.3.0
```

**Version verification:** pytest-asyncio 1.3.0 是 PyPI 上的最新稳定版，requires Python >=3.10（项目 Python 3.13.2 满足）。uv dry-run 确认将安装 1.3.0。

## Architecture Patterns

### Protocol 签名变更映射

当前状态和目标状态的完整映射：

```
LLMProvider Protocol:
  chat()         : sync -> async def     (返回 LLMResponse)
  chat_with_retry: sync -> REMOVED       (从 Protocol + OpenAIProvider 删除)
  chat_stream()  : sync -> async def     (返回 AsyncIterator[StreamChunk])

Tool Protocol:
  name           : property -> 不变
  description    : property -> 不变
  json_schema    : property -> 不变
  execute()      : sync -> async def     (返回 str | ToolResult | None)

BuiltinTool ABC:
  execute()      : sync -> async def     (公共入口，异常处理)
  _execute()     : sync -> async def     (抽象方法，子类实现)

Hook Protocol (全部 7 个方法):
  pre_tool_call()       : sync -> async def  (返回 HookAction)
  post_tool_call()      : sync -> async def  (返回 None)
  pre_llm_call()        : sync -> async def  (返回 None)
  should_continue()     : sync -> async def  (返回 bool)
  on_stream_chunk()     : sync -> async def  (返回 None)
  on_segment_complete() : sync -> async def  (返回 None)
  on_guard_blocked()    : sync -> async def  (返回 None)

Guard Protocol:
  evaluate()     : sync -> sync (不变)   (D-03 锁定)

EventHandler Protocol:
  handle()       : sync -> async def     (返回 None)

ReplyQueueLike Protocol:
  put_content()  : sync -> async def     (返回 None)
  put_cancel()   : sync -> async def     (返回 None)
  get()          : sync -> async def     (返回 str | None)
```

### BaseHook + run_* helper 的联动改造

```python
# BaseHook 默认实现改为 async
class BaseHook:
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        pass  # async no-op

    # ... 其余 5 个方法同理

# run_* helper 改为 async，内部 await
async def run_pre_tool_call(hooks: list[Hook], tool_call: ToolCallData) -> HookAction:
    for hook in hooks:
        action = await hook.pre_tool_call(tool_call)
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE
```

### Validation Helper 算法

```python
# 文件位置：matmaster/validation.py 或 matmaster/utils/async_validation.py
import inspect
from typing import Protocol

def validate_async_protocol(obj: object, protocol_cls: type) -> list[str]:
    """检查 obj 的方法签名是否匹配 protocol_cls 的 async/sync 定义。

    返回不匹配的错误消息列表。空列表表示全部通过。
    """
    errors = []
    for attr_name in protocol_cls.__protocol_attrs__:
        # 跳过 property（如 Tool.name, Tool.description）
        proto_static = inspect.getattr_static(protocol_cls, attr_name)
        if isinstance(proto_static, property):
            continue

        proto_method = getattr(protocol_cls, attr_name, None)
        impl_method = getattr(obj, attr_name, None)

        if impl_method is None:
            errors.append(f"Missing method: {attr_name}")
            continue

        expected_async = inspect.iscoroutinefunction(proto_method)
        actual_async = inspect.iscoroutinefunction(impl_method)

        if expected_async and not actual_async:
            errors.append(
                f"{type(obj).__name__}.{attr_name}() must be async def "
                f"(required by {protocol_cls.__name__})"
            )
        elif not expected_async and actual_async:
            errors.append(
                f"{type(obj).__name__}.{attr_name}() must be sync def "
                f"(required by {protocol_cls.__name__})"
            )

    return errors
```

### Async Mock Factory 设计

两种实现路径的对比和推荐：

```python
# 方案 A: 手写 async def（推荐 -- 更明确，签名完全匹配 Protocol）
class MockAsyncLLMProvider:
    """满足 async LLMProvider Protocol 的 mock 实现。"""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="mock response", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")
        # 注意：async generator 自动满足 AsyncIterator


# 方案 B: unittest.mock.AsyncMock（更灵活但签名不受控）
from unittest.mock import AsyncMock
mock_provider = AsyncMock()
mock_provider.chat.return_value = LLMResponse(content="mock", finish_reason="stop")
```

**推荐方案 A**：手写 async def mock class。原因：
1. 签名与 Protocol 完全一致，validation helper 可以正确检测
2. 测试意图更清晰，不依赖 mock 框架的内部行为
3. 后续阶段（Phase 13-18）可直接复用这些 mock 作为测试基础
4. AsyncMock 的 `inspect.iscoroutinefunction` 返回 True，但方法签名不受 Protocol 约束

### ToolRegistry.execute 过渡期注意

ToolRegistry.execute() 目前直接调用 `tool.execute(arguments)`。Phase 12 改 Tool Protocol 后：
- Protocol 定义 `execute` 为 async def
- 现有 12 个 BuiltinTool 实现仍然是 sync（Phase 14 改造）
- ToolRegistry.execute() 在 Phase 17（Kernel 改造）前仍然是 sync 调用

**过渡期策略**：Phase 12 只改 Protocol/ABC 定义。ToolRegistry.execute() 暂不改为 async（那是 Phase 17 的事）。在过渡期间，sync 实现对 async Protocol 的 isinstance() 检查仍然通过（这是 runtime_checkable 的已知行为），但 validation helper 可以检测出不匹配。

### 项目结构变更

```
matmaster/
├── types/
│   ├── llm_provider.py    # [改] chat/chat_stream async, 删 chat_with_retry
│   └── guards.py          # [不变] evaluate 保持 sync
├── tools/
│   ├── tool_registry.py   # [改] Tool Protocol execute -> async def
│   └── builtin/
│       └── base.py        # [改] BuiltinTool ABC execute/_execute -> async def
├── core/
│   └── hooks.py           # [改] Hook Protocol 7方法 async, BaseHook async, run_* async
├── integration/
│   └── event_router.py    # [改] EventHandler.handle -> async def
├── hooks/
│   └── confirmation.py    # [改] ReplyQueueLike 3方法 -> async def
├── providers/
│   └── openai_provider.py # [改] 删除 chat_with_retry 方法
└── validation.py          # [新] validate_async_protocol helper

tests/
├── conftest.py            # [新] async mock factories + pytest-asyncio 配置
└── matmaster/
    └── types/
        └── test_llm_provider.py  # [改] 适配新 Protocol（删 chat_with_retry 测试）
```

### Anti-Patterns to Avoid

- **async 壳包 sync 实现**：Phase 12 不要在现有 sync 实现外面包 `async def` 壳。D-01 明确禁止这种做法。Phase 13-18 逐步改为真正的 async 实现。
- **Protocol 双版本并存**：不要创建 SyncLLMProvider + AsyncLLMProvider 两个 Protocol。PROJECT.md 决策明确排除 dual Protocol pattern（维护成本高，API 不一致）。
- **validation helper 用类装饰器**：D-07 锁定为实例级函数 `validate_async_protocol(obj, protocol_cls)`，不要做成 `@validate_async` 装饰器。
- **跳过 BuiltinTool ABC 改造**：Phase 12 必须同时改 BuiltinTool ABC（execute + _execute 都改 async），否则 Phase 14 改 12 个子类时 ABC 约束不对。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| async/sync 方法签名检测 | 手写 AST 分析或 type annotation 解析 | `inspect.iscoroutinefunction` | 标准库提供，已验证可靠，处理了 bound method、staticmethod、from __future__ annotations 等边案 |
| async test 自动发现 | 手写 pytest plugin 或 conftest hook | pytest-asyncio auto mode | 成熟插件，asyncio_mode=auto 自动识别 async def test_* |
| Protocol 方法枚举 | 手写 dir() 过滤 | `Protocol.__protocol_attrs__` | typing 模块内置属性，准确列出 Protocol 定义的方法/property |
| property vs method 区分 | hasattr 或 callable 判断 | `inspect.getattr_static` + `isinstance(_, property)` | getattr_static 不触发 descriptor protocol，准确识别 property 定义 |

**Key insight:** Python 标准库 `inspect` 模块为 async 检测提供了完整的工具链。不需要引入第三方库或自己实现检测逻辑。

## Common Pitfalls

### Pitfall 1: runtime_checkable 不检查 async/sync

**What goes wrong:** 将 Protocol 方法改为 `async def` 后，sync 实现仍然通过 `isinstance(obj, Protocol)` 检查，导致运行时 `await sync_function()` 抛出 `TypeError: object ... can't be used in 'await' expression`。
**Why it happens:** `@runtime_checkable` 只检查方法名是否存在，不检查方法是否为 coroutine function。
**How to avoid:** 使用 validation helper 在组装时（Exp.assemble）或测试中主动检测。
**Warning signs:** 测试全绿但集成运行时 TypeError。

### Pitfall 2: async generator 返回类型标注

**What goes wrong:** `async def chat_stream(...) -> AsyncIterator[StreamChunk]` 的函数体里用 `yield`，但 mypy 可能报类型不匹配。
**Why it happens:** async generator 的实际返回类型是 `AsyncGenerator[StreamChunk, None]`，而 `AsyncIterator[StreamChunk]` 是其超类型。标注 AsyncIterator 是合法的（协变），但某些 mypy 版本可能有误报。
**How to avoid:** 返回类型标注用 `AsyncIterator[StreamChunk]`（Protocol 层面），实现层面 mypy 会正确推断 AsyncGenerator。
**Warning signs:** mypy 报错 Incompatible return type。

### Pitfall 3: BaseHook async 改造遗漏 run_* helper

**What goes wrong:** 改了 Hook Protocol 和 BaseHook 为 async，但忘了改 `run_pre_tool_call`, `run_should_continue` 等 helper 函数。后续 Kernel await 这些 helper 时报 TypeError。
**Why it happens:** run_* helper 函数定义在 hooks.py 但不属于任何 class，容易被遗漏。
**How to avoid:** 同一个 PR/task 内一并改造。hooks.py 中有 7 个 run_* helper，全部需要改为 async def 并 await hook 方法调用。
**Warning signs:** grep `def run_` 确认全部改为 `async def run_`。

### Pitfall 4: EventEmitterHook 继承 BaseHook 的过渡问题

**What goes wrong:** BaseHook 改为 async 后，EventEmitterHook（继承 BaseHook）的方法也需要变成 async。但 EventEmitterHook 在 Phase 15 才真正改造实现。Phase 12 如果改了 BaseHook，EventEmitterHook 的 sync 方法 override 会导致 validation helper 报错。
**Why it happens:** EventEmitterHook 直接继承 BaseHook 并 override 了多个方法。如果父类方法变 async 而子类仍然 sync，Python 不报错但 validation 不通过。
**How to avoid:** Phase 12 只改 Protocol 和 BaseHook，不改 EventEmitterHook（D-01）。EventEmitterHook 的测试在 Phase 12 暂时跳过 async validation；Phase 15 改造时一并处理。但必须注意：如果改了 BaseHook 为 async，EventEmitterHook 的 sync override 在 Python 层面是合法的（不会报错），只是 `isinstance(hook, Hook)` 仍然返回 True（但 validation helper 会检测出不匹配）。
**Warning signs:** 现有 test_hooks.py 中涉及 EventEmitterHook 的测试如果不做调整会 pass（因为 sync override 在 Python 层面不报错），但 validation helper 测试会正确检测出问题。

### Pitfall 5: chat_with_retry 删除后的测试影响

**What goes wrong:** 删除 chat_with_retry 后，tests/matmaster/types/test_llm_provider.py 中 4 个相关测试会失败（TestChatWithRetryProtocol 类），tests/matmaster/core/conftest.py 中的 MockLLMProvider 也需要删除该方法。
**Why it happens:** 现有测试明确测试 chat_with_retry 在 Protocol 中的存在性。
**How to avoid:** 同一 task 内更新测试文件。删除/重写 TestChatWithRetryProtocol，更新 MockLLMProvider，更新 CompleteLLMProvider。
**Warning signs:** `pytest --co` 仍然收集到旧测试 -> 运行时 AttributeError。

### Pitfall 6: pytest-asyncio 1.x 的 event_loop fixture 移除

**What goes wrong:** 如果某些测试（包括第三方 conftest）使用了 `event_loop` fixture，pytest-asyncio 1.x 安装后会报 fixture 未找到。
**Why it happens:** pytest-asyncio 1.0 移除了 `event_loop` fixture。
**How to avoid:** 本项目是全新安装 pytest-asyncio（之前完全没有），不存在遗留 event_loop fixture 使用。但需要确认现有 1072 个测试中没有任何使用 event_loop 的地方。
**Warning signs:** `grep -r "event_loop" tests/` 检查。

## Code Examples

### Example 1: LLMProvider Protocol 目标状态

```python
# matmaster/types/llm_provider.py -- Phase 12 后
from __future__ import annotations
from typing import Any, AsyncIterator, Protocol, runtime_checkable
from matmaster.types.messages import LLMResponse, StreamChunk

@runtime_checkable
class LLMProvider(Protocol):
    """LLM backend interface for the agent kernel.

    chat() for non-streaming, chat_stream() for streaming.
    chat_with_retry removed -- retry logic lives in Kernel._call_llm().
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]: ...
```

### Example 2: BuiltinTool ABC 目标状态

```python
# matmaster/tools/builtin/base.py -- Phase 12 后
class BuiltinTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    json_schema: ClassVar[dict[str, Any]]

    def __init__(self, *, session: Any | None = None, workdir: Path | None = None) -> None:
        self._session = session
        self._workdir = workdir
        self.logger = logging.getLogger(self.__class__.__name__)

    async def execute(self, arguments: dict[str, Any]) -> str:
        """Tool Protocol entry point. Delegates to _execute."""
        try:
            return await self._execute(arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"

    @abstractmethod
    async def _execute(self, arguments: dict[str, Any]) -> str:
        """Subclass implementation. Raise on error, return string on success."""
        ...
```

### Example 3: Validation Helper

```python
# matmaster/validation.py
from __future__ import annotations
import inspect


def validate_async_protocol(obj: object, protocol_cls: type) -> list[str]:
    """Validate that obj's methods match protocol_cls async/sync signatures.

    Skips properties (e.g. Tool.name). Returns list of mismatch error
    messages. Empty list means all checks passed.
    """
    errors: list[str] = []
    proto_attrs = getattr(protocol_cls, "__protocol_attrs__", set())

    for attr_name in proto_attrs:
        proto_static = inspect.getattr_static(protocol_cls, attr_name)
        if isinstance(proto_static, property):
            continue

        proto_method = getattr(protocol_cls, attr_name, None)
        impl_method = getattr(obj, attr_name, None)

        if impl_method is None:
            errors.append(
                f"{type(obj).__name__} missing method '{attr_name}' "
                f"required by {protocol_cls.__name__}"
            )
            continue

        if not callable(impl_method):
            continue  # property on impl side, skip async check

        expected_async = inspect.iscoroutinefunction(proto_method)
        actual_async = inspect.iscoroutinefunction(impl_method)

        if expected_async != actual_async:
            expected = "async def" if expected_async else "def"
            actual = "async def" if actual_async else "def"
            errors.append(
                f"{type(obj).__name__}.{attr_name}() is {actual}, "
                f"expected {expected} per {protocol_cls.__name__}"
            )

    return errors
```

### Example 4: Async Mock Factory (tests/conftest.py)

```python
# tests/conftest.py
from __future__ import annotations
from typing import Any, AsyncIterator
import pytest
from matmaster.types.messages import LLMResponse, StreamChunk, ToolCallData
from matmaster.tools.tool_result import ToolResult
from matmaster.core.hooks import HookAction


class MockAsyncLLMProvider:
    """Async mock satisfying LLMProvider Protocol for testing."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="mock response", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class MockAsyncTool:
    """Async mock satisfying Tool Protocol for testing."""

    def __init__(self, name: str = "test_tool", result: str = "ok") -> None:
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        return self._result


class MockAsyncHook:
    """Async mock satisfying Hook Protocol for testing."""

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        pass

    async def pre_llm_call(self, messages: list, turn: int) -> None:
        pass

    async def should_continue(self, messages: list, turn: int) -> bool:
        return True

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        pass

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        pass

    async def on_guard_blocked(
        self, tool_call: ToolCallData, result: Any
    ) -> None:
        pass
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pytest-asyncio 0.21 event_loop fixture | 1.x loop_scope 参数 | 2024 (0.23+) | 本项目不受影响（全新安装） |
| asyncio_mode 默认 strict | 仍然默认 strict | 一直如此 | 项目已配置 auto，无需担心 |
| typing.runtime_checkable 检测 async | 仍然无法检测 | Python 设计如此 | 必须用 inspect.iscoroutinefunction |

**Deprecated/outdated:**
- pytest-asyncio event_loop fixture: 在 1.0 中移除，用 asyncio.get_running_loop() 替代
- asyncio_default_fixture_loop_scope 配置: 0.x 时代的过渡选项，1.x 中用 loop_scope 参数

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 (需安装) |
| Config file | `pytest.ini` (已有 asyncio_mode=auto) |
| Quick run command | `uv run pytest tests/matmaster/types/ tests/matmaster/core/test_hooks.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROT-01 | LLMProvider chat/chat_stream 为 async, chat_with_retry 已移除 | unit | `uv run pytest tests/matmaster/types/test_llm_provider.py -x` | YES (需修改) |
| PROT-02 | Tool Protocol execute 为 async, BuiltinTool ABC execute/_execute 为 async | unit | `uv run pytest tests/matmaster/tools/test_tool_registry.py tests/matmaster/tools/test_builtin_base.py -x` | YES (需修改) |
| PROT-03 | Hook Protocol 7 方法为 async, BaseHook async, run_* async | unit | `uv run pytest tests/matmaster/core/test_hooks.py -x` | YES (需修改) |
| PROT-04 | Guard evaluate 保持 sync | unit | `uv run pytest tests/matmaster/types/test_guards.py -x` | YES (验证不变) |
| PROT-05 | validation helper 检测 sync/async 不匹配 | unit | `uv run pytest tests/matmaster/test_validation.py -x` | NO (Wave 0) |
| TEST-01 | pytest-asyncio auto mode 生效, async def test 可运行 | smoke | `uv run pytest --co` + `uv run pytest tests/matmaster/test_validation.py -x` | NO (Wave 0) |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/types/ tests/matmaster/core/test_hooks.py tests/matmaster/tools/test_tool_registry.py -x`
- **Per wave merge:** `uv run pytest`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/test_validation.py` -- validation helper 单元测试（covers PROT-05, TEST-01）
- [ ] `tests/conftest.py` -- async mock factories（MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook）
- [ ] pytest-asyncio 安装: `uv add --group dev pytest-asyncio>=1.3.0`

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | all | YES | 3.13.2 | -- |
| pytest | testing | YES | 9.0.2 | -- |
| pytest-asyncio | async tests | NO (not installed) | -- | Must install: `uv add --group dev pytest-asyncio>=1.3.0` |
| uv | package management | YES | (project uses uv) | -- |

**Missing dependencies with no fallback:**
- pytest-asyncio: 必须安装，否则 asyncio_mode=auto 配置无效且 async def test 无法运行

**Missing dependencies with fallback:**
- None

## Open Questions

1. **Hook Protocol 返回类型是否需要调整**
   - What we know: 当前 5 个 observation hooks 返回 None，2 个 intercepting hooks 返回 HookAction/bool。改为 async 后返回类型不变（async def 返回 Coroutine[..., None] 包装的原始类型）。
   - What's unclear: 是否有必要把返回类型从隐式 None 改为显式 `-> None` 标注。
   - Recommendation: 保持现有返回类型标注不变（CONTEXT 未指定需要变更）。Claude's discretion 列出了此项，但无强需求。

2. **现有测试在 Protocol 签名变更后的兼容性**
   - What we know: 1072 个现有测试，大部分使用 sync mock。Phase 12 改 Protocol 定义后，这些 sync mock 仍然通过 isinstance 检查（runtime_checkable 不区分 async/sync）。
   - What's unclear: 是否有测试直接调用 Protocol 方法并依赖同步返回值（这些测试在 Protocol 改为 async 后 mock 仍然 sync，调用不会 await，所以不报错）。
   - Recommendation: Phase 12 只更新直接测试 Protocol 定义的测试文件（test_llm_provider.py 等），其余测试在 Phase 13-18 随实现同步迁移（D-01, TEST-02）。

## Sources

### Primary (HIGH confidence)
- Python 3.13.2 stdlib `inspect.iscoroutinefunction` -- 本地实测验证 sync/async 检测行为
- Python 3.13.2 stdlib `typing.Protocol.__protocol_attrs__` -- 本地实测验证方法枚举
- Python 3.13.2 stdlib ABC abstractmethod -- 本地实测验证不强制 async/sync 匹配
- PyPI pytest-asyncio 1.3.0 metadata -- version, requires_python>=3.10
- 项目代码 matmaster/ -- 6 个 Protocol 当前定义，所有方法均为 sync

### Secondary (MEDIUM confidence)
- pytest-asyncio 官方文档 auto mode 说明 -- auto mode 自动识别 async def test
- pytest-asyncio 1.0 migration guide -- event_loop fixture 移除，loop_scope 替代

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pytest-asyncio 1.3.0 版本号和安装命令已通过 uv dry-run 验证
- Architecture: HIGH - 所有 Protocol 方法枚举、async 检测行为均通过本地 Python 脚本实测验证
- Pitfalls: HIGH - runtime_checkable 不检查 async/sync 行为已实测确认；ABC 不强制 async/sync 匹配已实测确认

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable domain, Python typing/inspect behavior unlikely to change)
