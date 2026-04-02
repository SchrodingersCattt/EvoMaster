# Technology Stack: v2.2 AgentKernel Generator-First 全链路改造

**Project:** matmaster-evo v2.2 Kernel Generator-First Transformation
**Researched:** 2026-04-02
**Overall confidence:** HIGH

## Executive Summary

v2.2 的 generator-first 改造**不需要引入任何新的外部依赖**。Python 3.13 标准库已提供全部所需原语：`collections.abc.AsyncGenerator` / `AsyncIterator` 类型注解、`contextlib.aclosing` 安全清理、`dataclasses` 私有内部类型。现有 Pydantic v2、pytest 9.x + pytest-asyncio 1.3.0 栈完全覆盖验证需求。

本次改造的核心是架构模式的变更（observer-push 转 generator-pull），而非技术栈变更。以下文档聚焦于需要使用的标准库 API、类型注解最佳实践、以及明确不该引入的东西。

## Recommended Stack

### Core Runtime (不变)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.13.2 | Runtime | 已有。3.13 的 `collections.abc` 原生支持泛型下标 `AsyncGenerator[Y, S]`，无需 `typing` 模块 |
| Pydantic | v2.12.5 | 契约模型 (AgentRuntimeSpec, events) | 已有。`frozen=True` + `arbitrary_types_allowed=True` 支持 `Any` 类型预留字段 |
| asyncio | stdlib | 事件循环 + 并发原语 | 已有。`asyncio.gather` 用于 InlineToolRunner 并行工具执行 |

### 新增使用的 stdlib API (零新依赖)

| API | 来源 | Purpose | Why |
|-----|------|---------|-----|
| `collections.abc.AsyncIterator` | stdlib | `run_stream()` 返回类型 | 公开接口返回 `AsyncIterator[BusEvent]`——消费者只需 `async for`，不需要 `asend()`/`athrow()` 能力 |
| `collections.abc.AsyncGenerator` | stdlib | `_run_items()` 内部返回类型 | 私有 generator 返回 `AsyncGenerator[_KernelItem, None]`——精确标注 yield 语义 |
| `dataclasses.dataclass` | stdlib | `_KernelItem`, `_KernelState`, `_TerminalItem`, `ToolExecutionContext` | 内核私有类型用 dataclass 而非 Pydantic——无需验证开销，实例化快 5-15x |
| `dataclasses.field` | stdlib | `_KernelState.total_usage` 等 mutable default | `field(default_factory=dict)` 避免可变默认值陷阱 |
| `contextlib.aclosing` | stdlib (3.10+) | Phase 2 消费者安全关闭 generator | Phase 1 不需要（`run()` 完全消费 generator）。Phase 2 Service 层用 `async with aclosing(kernel.run_stream(...)) as events:` 确保异常退出时 generator 被正确 `aclose()` |
| `typing.Protocol` | stdlib | `ToolRunner` Protocol | 已有模式——项目全面使用 `@runtime_checkable Protocol` 定义接口 |
| `typing.runtime_checkable` | stdlib | `ToolRunner` isinstance 检查 | 已有模式——与 `Hook`, `LLMProvider`, `Guard` 一致 |

### Testing (不变)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pytest | 9.0.2 | Test runner | 已有 |
| pytest-asyncio | 1.3.0 | Async test support | 已有。`asyncio_mode = "auto"` 配置在 `pyproject.toml`，async test 自动识别 |

### Supporting Libraries (不变，已在 stack 中)

| Library | Version | Purpose | Relation to v2.2 |
|---------|---------|---------|-------------------|
| openai | (current) | LLM provider SDK | 不变。`chat_stream()` 已返回 `AsyncStream`，与 generator 消费兼容 |
| tiktoken | >=0.7.0 | Token counting | 不变。ContextCompactor 继续使用 |

## Type Annotation Strategy

### 公开接口: 使用 `AsyncIterator`

```python
from collections.abc import AsyncIterator

async def run_stream(self, ...) -> AsyncIterator[BusEvent]:
    ...
```

**Why `AsyncIterator` 而非 `AsyncGenerator`:**
- 消费者只做 `async for event in kernel.run_stream(...):`，不使用 `asend()` 或 `athrow()`
- `AsyncIterator` 是更窄的接口承诺——符合 Liskov 替换原则
- mypy 对 `AsyncIterator` 返回类型的 async generator 函数检查通过
- 实际的 async generator 对象同时满足 `AsyncIterator` 和 `AsyncGenerator`

**Confidence:** HIGH (Python 官方文档 + cpython issue #112866 确认此用法)

### 私有 generator: 使用 `AsyncGenerator`

```python
from collections.abc import AsyncGenerator

async def _run_items(self, ...) -> AsyncGenerator[_KernelItem, None]:
    ...
```

**Why `AsyncGenerator[Y, None]`:**
- 私有方法，精确标注 yield 类型和 send 类型
- `None` send type 表明不使用 `asend()` 注入值
- 内部测试可能需要 `aclose()` 能力（`AsyncGenerator` 保证有此方法，`AsyncIterator` 不保证）

### 不使用 `typing.AsyncGenerator` (deprecated)

Python 3.9+ 后 `typing.AsyncGenerator` 已废弃，`collections.abc.AsyncGenerator` 是标准替代。项目已有 `from __future__ import annotations` 启用延迟解析，`collections.abc` 版本在所有语境下可直接使用。

## Internal Types: dataclass, NOT Pydantic

### 决策: `_KernelItem` / `_KernelState` / `_TerminalItem` / `ToolExecutionContext` 用 `@dataclass`

| 考量 | dataclass | Pydantic BaseModel |
|------|-----------|-------------------|
| 实例化性能 | 5-15x faster | 有 Rust 加速但仍更慢 |
| 验证需求 | 无——内核私有类型，数据已在边界校验 | 过度——这些类型不接收外部输入 |
| 可变性 | `_KernelState` 需要可变（turn++, messages append） | Pydantic frozen=True 是默认——需要额外配置可变 |
| 序列化 | 不需要——纯内核内部流转 | 过度——不会 JSON 序列化这些对象 |
| 项目惯例 | `_ToolOutcome` 已用 `NamedTuple`，`KernelResult` 已用 `dataclass(frozen=True)` | `AgentRuntimeSpec` 等跨层契约用 Pydantic |

**规则:**
- 跨层契约（`AgentRuntimeSpec`, `BusEvent`）: Pydantic `BaseModel(frozen=True)` 保证类型安全 + 序列化
- 内核私有类型（`_KernelItem`, `_KernelState`）: stdlib `@dataclass` 极简开销
- 工具执行上下文（`ToolExecutionContext`）: `@dataclass(frozen=True)` 保证不可变，无 Pydantic 开销

**Confidence:** HIGH (与现有 `KernelResult` 用 `dataclass(frozen=True)` 一致)

## ToolRunner Protocol: 设计选择

### 使用 `@runtime_checkable Protocol` 而非 ABC

```python
@runtime_checkable
class ToolRunner(Protocol):
    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        ...
```

**Why Protocol 而非 ABC:**
- 项目已有 4 个 `@runtime_checkable Protocol`（`Hook`, `LLMProvider`, `Guard`, `EventHandler`）——保持一致
- Protocol 支持结构性子类化（duck typing），`InlineToolRunner` 不需要显式继承
- `isinstance(runner, ToolRunner)` 在运行时可用（Phase 2 Exp 注入时可做 assertion）

**Performance caveat:** Python 3.12/3.13 的 `@runtime_checkable` Protocol `isinstance` 检查比普通类型检查慢。但 ToolRunner 实例化发生在 `_run_items()` 入口（每次 run 一次），不在热循环中。性能影响可忽略。

**Confidence:** HIGH (与现有架构一致，已验证 `isinstance` 在 Python 3.13.2 正常工作)

## AsyncGenerator Cleanup: Phase-Specific 策略

### Phase 1: 无需 `aclose()` / `aclosing()`

Phase 1 的两个消费路径都完全消费 generator：
- `run()`: `async for item in self._run_items(...)` 完整迭代到 terminal
- `run_stream()`: `async for item in self._run_items(...)` 完整迭代，过滤 yield events

没有提前退出的场景。Generator 自然结束，GC 无需介入。

### Phase 2: 引入 `contextlib.aclosing`

Service 层消费 `run_stream()` 时可能因异常或超时提前退出。此时需要确保 generator 被正确关闭：

```python
from contextlib import aclosing

async def run_agent_stream(self, ...):
    async with aclosing(kernel.run_stream(spec, task)) as events:
        async for event in events:
            await bus.emit(event)  # Phase 2 过渡：仍喂入 Bus
```

**Why `aclosing` 而非手动 `aclose()`:**
- PEP 525 规定 async generator 需要事件循环才能 `aclose()`——`aclosing()` 自动在 `__aexit__` 中调用
- 异常安全——无论正常退出还是异常退出都能清理
- Python 3.10+ stdlib 自带，不需要第三方库

### Phase 3 (如果去总线化): Generator fanout 不需要第三方库

Phase 3 评估去总线化时，如果需要多消费者 fanout（SSE + 持久化 + workspace），**不要引入 aiostream 或 aioreactive**。应该用 asyncio.Queue 实现 pump 模式：

```python
# 概念：producer → queue → consumers
async def _pump(gen, queues):
    async for item in gen:
        for q in queues:
            q.put_nowait(item)
    for q in queues:
        q.put_nowait(SENTINEL)
```

**Why 不用第三方:**
- 单 producer 多 consumer 场景，asyncio.Queue 已足够
- aiostream/aioreactive 引入响应式编程范式（Observable/Operator），学习成本高，与项目 async/await 原生模式不一致
- asyncio.Queue 已在 MessageBus 中使用，团队已熟悉

**Confidence:** HIGH

## AgentRuntimeSpec 扩展: `Any` 类型预留

### Phase 1 预留字段

```python
class AgentRuntimeSpec(BaseModel):
    # ... 现有字段 ...
    tool_runner: Any | None = None
    tool_catalog: Any | None = None
    runtime_topology: Any | None = None
    capability_policy: Any | None = None
    structural_validation: Any | None = None
```

**Why `Any` 而非 forward reference:**
- `ToolCatalog`, `RuntimeTopology` 等类型在 Tool Runtime v2 中定义，Phase 1 尚未创建
- Pydantic v2 的 `arbitrary_types_allowed=True` 已启用，`Any` 兼容
- Forward reference（如 `'ToolCatalog'`）需要类型存在于同一或可导入模块——违反 Phase 分离
- Phase 2 引入具体类型后直接替换为精确注解

**Confidence:** HIGH (spec 设计决策 D-07 已论证)

## What NOT to Add

| Category | Rejected | Why |
|----------|----------|-----|
| Reactive library | aiostream, aioreactive, RxPY | 引入 Observable 范式。项目用 native async/await，不需要响应式编程 |
| Event bus framework | pyventus, blinker | 正在移除间接 Bus，不应引入新的 |
| Streaming framework | streamz, faust | 面向分布式流处理，与单进程 agent 循环不匹配 |
| Type narrowing library | beartype, typeguard | `@runtime_checkable Protocol` 已满足运行时类型检查需求 |
| Async testing enhancement | anyio, trio | 项目绑定 asyncio，不需要异步运行时抽象层 |
| pytest plugin | pytest-timeout | 已有 stop_event 取消机制。测试超时用 pytest 内置 `-x --timeout` |
| Data class alternative | attrs, msgspec | dataclass 足够。attrs 是 superset（功能过剩），msgspec 面向序列化（不需要） |
| Generator utility | more-itertools, aioitertools | 标准 `async for` + `asyncio.gather` 覆盖全部需求 |

## Installation

```bash
# 无变化。不需要安装新依赖。
uv sync --extra dev
```

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| AsyncGenerator 返回类型 | `AsyncIterator[BusEvent]` (公开) | `AsyncGenerator[BusEvent, None]` | `AsyncIterator` 是更窄的接口承诺，消费者不需要 send/throw |
| 内核私有类型 | `@dataclass` | `Pydantic BaseModel` | 无需验证/序列化开销。`_KernelState` 需要可变性，Pydantic frozen 不适配 |
| ToolRunner 定义 | `@runtime_checkable Protocol` | `ABC` (abstract base class) | Protocol 支持结构性子类化，与项目 4 个现有 Protocol 一致 |
| ToolExecutionContext | `@dataclass(frozen=True)` | `NamedTuple` | dataclass 支持可选字段默认值 + Phase 2 扩展字段，NamedTuple 扩展性差 |
| Generator fanout (Phase 3) | `asyncio.Queue` pump | aiostream multicast | 零新依赖，复用现有 MessageBus 模式 |
| Async cleanup | `contextlib.aclosing` (stdlib) | 手动 try/finally + aclose() | aclosing 是标准模式，异常安全，PEP 525 推荐 |

## Integration Points

### 与现有 EventEmitterHook 的并存 (Phase 1)

Phase 1 中 `_run_items()` yield 事件与 EventEmitterHook emit 到 Bus 并存。两条路径**不产生重复消费**，因为 Phase 1 没有 `run_stream()` 的真实消费者——现有 service 层仍走 `run()` -> Hook -> Bus -> EventRouter。

### 与 FastAPI SSE 的对接 (Phase 2)

FastAPI 0.135.0+ 原生支持 `EventSourceResponse` 接收 async generator。Phase 2 Service 层消费 `run_stream()` 时可直接对接：

```python
from starlette.responses import StreamingResponse

async def sse_endpoint(request):
    async def event_generator():
        async with aclosing(kernel.run_stream(spec, task)) as events:
            async for event in events:
                yield f"data: {event.model_dump_json()}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

现有项目使用 `sse-starlette` 的 `EventSourceResponse`。Phase 2 对接时需评估是直接用 `StreamingResponse` 还是继续用 `EventSourceResponse`，但这不影响 Phase 1。

### 与 ToolRegistry 的关系

`InlineToolRunner` 持有 `ToolRegistry` 引用，调用 `registry.execute(name, args)`。这与当前 agent.py 内联逻辑完全一致。Phase 2 的真实 `ToolRunner` 将使用 `ToolCatalog` 替代 `ToolRegistry`，但 Kernel 通过 `spec.tool_runner` 注入，不直接依赖具体实现。

## Confidence Assessment

| Area | Confidence | Reason |
|------|------------|--------|
| AsyncGenerator stdlib API | HIGH | Python 3.13 验证过，`collections.abc.AsyncGenerator` + `contextlib.aclosing` 均可用 |
| dataclass for internal types | HIGH | 与 `KernelResult` 现有模式一致，性能优势明确 |
| ToolRunner Protocol 定义 | HIGH | 与现有 4 个 Protocol 一致，spec 已详细设计 |
| Pydantic Any 预留字段 | HIGH | `arbitrary_types_allowed=True` 已启用，spec D-07 已论证 |
| Phase 2 aclosing 清理 | HIGH | stdlib 3.10+ 自带，PEP 525 推荐模式 |
| Phase 3 Queue fanout | MEDIUM | 概念验证充分，但多消费者排序和背压处理需要 Phase 3 具体验证 |
| 无需新依赖判断 | HIGH | 全部需求由 stdlib + 现有依赖覆盖 |

## Sources

- [PEP 525 -- Asynchronous Generators](https://peps.python.org/pep-0525/) — async generator finalization, aclose() 语义, GeneratorExit 约束
- [Python collections.abc docs (3.13)](https://docs.python.org/3.13/library/collections.abc.html) — AsyncGenerator vs AsyncIterator 类型层级
- [cpython issue #112866](https://github.com/python/cpython/issues/112866) — AsyncIterator vs AsyncGenerator 类型注解使用指导
- [Python contextlib docs](https://docs.python.org/3/library/contextlib.html) — aclosing() 可用性和语义
- [cpython issue #102936](https://github.com/python/cpython/issues/102936) — runtime_checkable Protocol 性能特征
- [Pydantic v2 docs -- Dataclasses](https://docs.pydantic.dev/latest/concepts/dataclasses/) — Pydantic vs dataclass 选型
- 本地验证：Python 3.13.2, pydantic 2.12.5, pytest 9.0.2, pytest-asyncio 1.3.0 全部通过 `uv run` 确认
