# Phase 32: Kernel Generator + Tool Runtime v2 Core Skeleton - Research

**Researched:** 2026-04-02
**Domain:** Python asyncio generator patterns / Pydantic v2 frozen model design / Agent kernel refactoring
**Confidence:** HIGH

## Summary

Phase 32 是一个大规模内部重构，涉及 25 个 requirement，跨越三个领域：(1) AgentKernel 从 `_run_loop()` 改造为 `_run_items()` AsyncGenerator 驱动的执行路径，(2) Tool Runtime v2 完整类型体系定义（8 个 frozen model + 1 个 enum），(3) ToolCatalog facade + InlineToolRunner 抽取 + AgentRuntimeSpec 扩展。核心约束是所有现有 39 个 kernel 测试零修改通过。

两份设计 spec（`kernel-generator-first.md` + `tool-runtime-v2.md`）已经过 Claude + GPT 双重评审并锁定，伪代码精确到行级。项目使用 Python 3.13.2 + Pydantic 2.12.5 + pytest-asyncio（asyncio_mode=auto）。所有新类型遵循项目已有模式：公开契约用 frozen Pydantic model，内核私有类型用 `dataclass(frozen=True)`，接口用 `@runtime_checkable Protocol`。

**Primary recommendation:** 严格按 CONTEXT.md D-01 决策分 3 个 plan 执行（Plan A: 类型体系 + ToolResult 升级, Plan B: ToolCatalog + ToolRunner + Spec 扩展, Plan C: Kernel generator 改造 + 回归验证），每个 plan 完成后运行全量 kernel 测试确保零破坏。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 25 个 requirement 分 3 个 plan 实现：
  - Plan A: 类型体系 + ToolResult 升级（TOBJ-01~08, TRES-01）
  - Plan B: ToolCatalog + ToolRunner + AgentRuntimeSpec 扩展（TCAT-01~03, TRUN-01~02, TRUN-05, SPEC-01, TDEF-01, TCON-02）
  - Plan C: Kernel generator 改造 + 回归验证（KGEN-01~05, REGR-01, REGR-03）
- **D-02:** ToolResult 一步到位替换：删除 `info` 字段，新增 `payload: dict[str, Any]` + `meta: dict[str, Any]`。影响面仅 3 处代码（`output_processor.py:45`、`hooks.py:227`、`events.py:72` 的 ToolResultEvent.info）+ 相关测试，不值得做兼容层。
- **D-03:** AgentRuntimeSpec 新增的 5 个字段直接使用具体类型注解（如 `ToolRunner | None`），不用 `Any`。循环导入通过 `TYPE_CHECKING` 解决。
- **D-04:** Phase 1 ToolCatalog 采用纯委托 facade 模式：所有操作委托给内部 ToolRegistry。
- **D-05:** 新增测试跟随源码结构放置，不建新目录。
- **D-06:** 两份 spec 的所有设计点确认锁定，可直接执行。

### Claude's Discretion
无 -- discussion 中所有设计点均已锁定。

### Deferred Ideas (OUT OF SCOPE)
无 -- discussion 未超出 phase scope。
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| KGEN-01 | `_run_items()` 私有 AsyncGenerator 作为唯一执行路径 | spec 4.1 Layer 0 设计，Python AsyncGenerator 模式已验证 |
| KGEN-02 | `run_stream()` 公开接口，yield BusEvent | spec 4.1 Layer 1 设计，复用 events.py 现有类型 |
| KGEN-03 | `run()` 委托 `_run_items()` 返回 KernelRunResult | spec 4.1 Layer 2 设计，签名完全不变 |
| KGEN-04 | `_KernelState` 局部状态管理 | spec 4.2 设计，dataclass + field(default_factory) |
| KGEN-05 | LLM 调用后 yield final completed snapshot | spec 4.7 Phase 1 策略已锁定 |
| TOBJ-01 | SessionCapabilities frozen Pydantic model | spec 6.1 定义，Session Protocol 增加 capabilities 属性 |
| TOBJ-02 | RuntimeTopology frozen Pydantic model | spec 6.2 定义，含 frozenset[ToolPlane] |
| TOBJ-03 | ToolPlane 枚举 | spec 6.3 定义，str Enum |
| TOBJ-04 | ToolSpec frozen Pydantic model | spec 6.4 定义，frozenset[str] capabilities |
| TOBJ-05 | ResourceClaim frozen Pydantic model | spec 6.5 定义，三种 mode |
| TOBJ-06 | ToolBinding frozen Pydantic model | spec 6.6 定义，含 binding_key 格式 |
| TOBJ-07 | ToolInstance frozen dataclass | spec 6.7 定义，组合 ToolSpec + ToolBinding + executor |
| TOBJ-08 | ToolDecision frozen Pydantic model | spec 6.9 Layer ToolDecision 定义 |
| TCAT-01 | ToolCatalog base + overlay 两层结构 | spec 6.8 定义，D-04 锁定 facade 模式 |
| TCAT-02 | ToolCatalog.version 递增 + Kernel 比对 | spec 5.3 / spec 6.8 version 机制 |
| TCAT-03 | Phase 1 内部持有 ToolRegistry facade | D-04 锁定，不改动注入路径 |
| TRUN-01 | ToolRunner Protocol (`@runtime_checkable`) | spec 4.3 定义，execute_batch 签名 |
| TRUN-02 | InlineToolRunner 过渡实现 | spec 4.4 定义，逻辑等价 agent.py L217-311 |
| TRUN-05 | Kernel 通过 spec.tool_runner 获取 runner | spec 5.2 Phase 1 回退逻辑 |
| TCON-02 | RunStateGuard 保持现有 GuardPipeline | Phase 1 不扩展 GuardContext |
| TRES-01 | ToolResult 升级 payload + meta | D-02 锁定一步到位 |
| SPEC-01 | AgentRuntimeSpec 新增 5 个可选字段 | D-03 锁定具体类型注解 |
| TDEF-01 | `_resolve_tool_definitions()` helper | spec 4.6 双路径设计 |
| REGR-01 | 全量 kernel 测试零修改通过 | 39 个现有测试已确认可运行 |
| REGR-03 | 工具内部安全检查保持不动 | Phase 1 不迁移约束 |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- 始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 按 标准库 / 第三方 / 本地 分组
- 单文件超过 1000 行必须重构
- 新增工具必须实现 Tool Protocol 并返回 ToolResult
- 新增 Exp 模式创建 TOML 文件
- frozen Pydantic model 用于层间契约
- `@runtime_checkable Protocol` 用于接口定义
- `TYPE_CHECKING + lazy import` 解决循环导入

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.13.2 | Runtime | 项目当前版本 |
| Pydantic | 2.12.5 | frozen model 定义 | 项目核心契约层 |
| pytest | >=9.0.2 | 测试框架 | pyproject.toml 声明 |
| pytest-asyncio | >=0.24.0 | async 测试支持 | asyncio_mode=auto 已配置 |

### Key Python Standard Library
| Module | Purpose | Usage |
|--------|---------|-------|
| `asyncio` | AsyncGenerator / gather / Event | _run_items() generator, 工具并发执行 |
| `dataclasses` | frozen=True 私有类型 | _KernelItem, _KernelState, _TerminalItem, ToolInstance |
| `typing` | Protocol, runtime_checkable, TYPE_CHECKING | ToolRunner Protocol, 循环导入隔离 |
| `enum` | str Enum | ToolPlane |
| `threading` | Event | stop_event 取消机制不变 |
| `collections.abc` | AsyncIterator, Callable, Awaitable | run_stream 返回类型, on_result 回调 |

### No New Dependencies Required
此 phase 纯内部重构，不引入任何新的外部依赖。

## Architecture Patterns

### Recommended Project Structure (New Files)
```
matmaster/
  core/
    agent.py                    # [MODIFY] _run_loop → _run_items generator; run_stream(); run() 委托
    tool_runner.py              # [NEW] ToolRunner Protocol + ToolExecutionContext + InlineToolRunner
  tools/
    tool_result.py              # [MODIFY] info → payload + meta
    tool_catalog.py             # [NEW] ToolCatalog (facade over ToolRegistry)
    tool_registry.py            # [NO CHANGE]
  types/
    runtime.py                  # [MODIFY] AgentRuntimeSpec 新增 5 字段
    events.py                   # [MODIFY] ToolResultEvent.info → payload
    session.py                  # [MODIFY] Session Protocol + capabilities 属性
    topology.py                 # [NEW] RuntimeTopology + SessionCapabilities + ToolPlane
    tool_spec.py                # [NEW] ToolSpec + ToolBinding + ResourceClaim + ToolInstance
    tool_decision.py            # [NEW] ToolDecision
  hooks/
    output_processor.py         # [MODIFY] result.info → result.payload

tests/
  matmaster/
    core/
      test_tool_runner.py              # [NEW]
      test_agent_kernel_stream.py      # [NEW]
    types/
      test_topology.py                 # [NEW]
      test_tool_spec.py                # [NEW]
      test_tool_decision.py            # [NEW]
    tools/
      test_tool_catalog.py             # [NEW]
```

### Pattern 1: Frozen Pydantic Model (层间契约)
**What:** 所有公开运行时对象使用 Pydantic `model_config = ConfigDict(frozen=True)`
**When to use:** 跨层传递的数据合约 (SessionCapabilities, RuntimeTopology, ToolSpec, ToolBinding, etc.)
**Example:**
```python
# Source: matmaster/types/runtime.py 现有模式
from pydantic import BaseModel, ConfigDict

class SessionCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    shell_persistence: Literal["stateless", "persistent"]
    shell_input: bool = False
    file_ops: Literal["native", "sftp"]
    upload_support: bool = False
    exec_cancel: bool = False
```

### Pattern 2: Frozen Dataclass (内核私有类型)
**What:** 内核内部不跨层的类型用 `@dataclass(frozen=True)`
**When to use:** KernelResult, AgentRuntime, _KernelItem, _TerminalItem, ToolInstance
**Example:**
```python
# Source: matmaster/types/runtime.py 现有模式
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ToolInstance:
    tool_spec: ToolSpec
    tool_binding: ToolBinding
    tool_executor: Callable[[dict[str, Any]], Awaitable[ToolResult]]
```

### Pattern 3: Runtime-Checkable Protocol (接口定义)
**What:** 用 `@runtime_checkable class XxxProtocol(Protocol)` 定义接口
**When to use:** ToolRunner, Guard, Hook, LLMProvider, Session -- 所有可替换实现的接口
**Example:**
```python
# Source: matmaster/core/hooks.py 现有模式
from typing import Protocol, runtime_checkable

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

### Pattern 4: TYPE_CHECKING Guard (循环导入)
**What:** 运行时不需要的类型引用放在 `if TYPE_CHECKING:` 块中
**When to use:** AgentRuntimeSpec 引用 ToolRunner / ToolCatalog / RuntimeTopology 时
**Example:**
```python
# Source: matmaster/core/agent.py 现有模式
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec, KernelRunResult
```

### Pattern 5: AsyncGenerator 三层接口
**What:** 私有 generator 产出内部类型，公开 generator 映射为事件，兼容方法收集结果
**When to use:** Kernel 的 _run_items / run_stream / run 三层
**Example:**
```python
# Spec 4.1 Layer 0 / 1 / 2
async def _run_items(...) -> AsyncIterator[_KernelItem]:
    # yield _KernelItem(event=..., messages_delta=..., terminal=...)
    ...

async def run_stream(...) -> AsyncIterator[BusEvent]:
    async for item in self._run_items(...):
        if item.event is not None:
            yield item.event
        if item.terminal is not None:
            yield RunResultEvent(...)

async def run(...) -> KernelRunResult:
    messages = [...]
    async for item in self._run_items(...):
        if item.messages_delta:
            messages.extend(item.messages_delta)
        if item.terminal:
            return KernelRunResult(result=..., messages=messages)
```

### Anti-Patterns to Avoid
- **Mutable self state in Kernel:** `_run_items()` 使用局部 `_KernelState`，不挂在 `self` 上。Kernel 是无状态/并发安全的。
- **从事件反推 transcript:** `run()` 通过 `messages_delta` 收集消息，不从 event 反向解析。
- **InlineToolRunner 检查 stop_event:** 取消是 Kernel `_run_items()` 的职责，在调用 execute_batch 前检查。
- **新建事件类型:** 复用 events.py 现有 18 种类型，不引入平行事件层。
- **直接在 ToolCatalog 中实现存储逻辑:** Phase 1 纯委托给内部 ToolRegistry。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Frozen model validation | 手写 __init__ + __setattr__ 冻结 | Pydantic `ConfigDict(frozen=True)` | Pydantic 提供自动 validation、序列化、hashable 支持 |
| AsyncGenerator 消费模式 | 手写 callback chain | `async for item in _run_items()` | Python 原生 AsyncGenerator 协议，yield/return 语义清晰 |
| Protocol 接口检查 | isinstance + abc.ABCMeta | `@runtime_checkable Protocol` + isinstance | 项目一致模式，无需 MRO 复杂度 |
| 事件联合类型分发 | if/elif 链判断类型 | Pydantic discriminated union (Field(discriminator="type")) | events.py 已有此模式 |

## Common Pitfalls

### Pitfall 1: _run_items Generator 不完整消费导致资源泄漏
**What goes wrong:** 如果 `run()` 或 `run_stream()` 在消费 `_run_items()` 时提前退出但未 `aclose()` generator，LLM provider context manager 可能不正确关闭。
**Why it happens:** AsyncGenerator 的 finally 块只在 `aclose()` 或完整消费后才执行。
**How to avoid:** `run()` 和 `run_stream()` 必须完整消费 `_run_items()` 直到 terminal item。LLM provider 的 `async with` 应在 `_run_items()` 外部管理（当前 `run()` 方法已经在外部 `async with spec.llm_provider`）。
**Warning signs:** 测试中 provider.__aexit__() 未被调用。

### Pitfall 2: ToolResult.info 删除后遗漏同步点
**What goes wrong:** 删除 `ToolResult.info` 后，忘记同步更新 `ToolResultEvent.info` 或 `OutputProcessorHook` 中的引用。
**Why it happens:** D-02 确认影响面是 3 处代码 + 测试，但可能有 grep 未覆盖的动态引用。
**How to avoid:** 删除 `info` 后立即全局搜索 `.info` 引用（已确认 5 处测试 + 3 处代码需更新），运行全量测试验证。
**Warning signs:** AttributeError: 'ToolResult' object has no attribute 'info'。
**Verified impact points:**
- `matmaster/core/hooks.py:227` (EventEmitterHook.post_tool_call)
- `matmaster/hooks/output_processor.py:45` (OutputProcessorHook.post_tool_call)
- `matmaster/types/events.py:72` (ToolResultEvent.info field)
- `tests/matmaster/tools/test_tool_result.py` (3 assertions)
- `tests/matmaster/types/test_events.py:110` (1 assertion)
- `tests/matmaster/hooks/test_output_processor.py` (3 assertions)
- `tests/matmaster/core/test_hooks.py:289` (1 assertion)

### Pitfall 3: Pydantic frozen model 中使用 frozenset
**What goes wrong:** `frozenset[str]` 在 Pydantic v2 中需要特殊处理，直接传 `set` 或 `list` 给 frozenset 字段会报 validation error。
**Why it happens:** Pydantic v2 对 frozenset 的 coercion 行为与 v1 不同。
**How to avoid:** Pydantic v2.12.5 支持自动 coercion `set -> frozenset` 和 `list -> frozenset`（已在项目 Python 3.13 + Pydantic 2.12 验证）。定义时直接用 `frozenset[str]` 类型注解，测试时可传 set 或 frozenset。RuntimeTopology 中的 `active_planes: frozenset[ToolPlane]` 同理。
**Warning signs:** ValidationError on frozenset field.

### Pitfall 4: AgentRuntimeSpec frozen model 新增字段的默认值
**What goes wrong:** AgentRuntimeSpec 是 `frozen=True`，新增的 5 个可选字段必须有 `None` 默认值，否则现有所有构造 AgentRuntimeSpec 的代码（测试中 _make_spec 等）都需要修改。
**Why it happens:** Pydantic frozen model 不允许构造后修改，且没有默认值的字段是必填的。
**How to avoid:** 所有新增字段类型为 `XxxType | None = None`（如 `tool_runner: ToolRunner | None = None`），确保现有调用方零修改。
**Warning signs:** 现有测试 `_make_spec()` 报 ValidationError。

### Pitfall 5: TYPE_CHECKING 块中的类型在运行时被引用
**What goes wrong:** D-03 要求 AgentRuntimeSpec 使用具体类型注解而非 `Any`。如果 ToolRunner 等类型仅在 `TYPE_CHECKING` 中 import，但 Pydantic model 在运行时需要解析类型注解，会报 NameError。
**Why it happens:** Pydantic v2 默认在类定义时评估注解。`from __future__ import annotations` 将注解变为字符串，延迟评估。但 Pydantic 使用 `model_rebuild()` 或 `__get_pydantic_core_schema__` 时仍可能触发。
**How to avoid:** 
1. 确保 `runtime.py` 文件顶部有 `from __future__ import annotations`
2. 使用 `model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)` -- 已在现有 AgentRuntimeSpec 中配置
3. 在实际运行时需要 isinstance 检查时，使用 lazy import
4. 对于 Pydantic v2.12+，`from __future__ import annotations` + `TYPE_CHECKING` guard 的模式已被项目广泛使用（见 agent.py L27-28）
**Warning signs:** NameError: name 'ToolRunner' is not defined at model creation time。

### Pitfall 6: ToolCatalog facade 模式下的 version 一致性
**What goes wrong:** D-04 锁定 ToolCatalog 内部持有 ToolRegistry。如果 `register_overlay()` 调用 `_registry.register()` 但忘记递增 `_version`，Kernel 不会刷新 tool_definitions。
**Why it happens:** 两层状态（ToolCatalog.version 和 ToolRegistry 内部 _tools dict）需要同步。
**How to avoid:** `register_overlay()` 方法中 `_version += 1` 必须在 `_registry.register()` 之后执行。测试应验证每次 `register_overlay()` 后 version 递增。
**Warning signs:** 新注入的 MCP 工具不出现在 LLM 的 tool_definitions 中。

## Code Examples

### Example 1: _KernelItem / _KernelState / _TerminalItem 定义
```python
# Source: docs/specs/2026-04-02-kernel-generator-first.md Section 4.1
# File: matmaster/core/agent.py (内部私有类型)

@dataclass
class _TerminalItem:
    """内核终止结果，仅 run() 消费。"""
    status: str          # completed / cancelled / failed
    reason: str          # natural / max_turns / cancelled / hook_stopped / invalid_finish
    final_content: str | None
    num_turns: int
    stop_reason: str | None
    usage: dict[str, int]

@dataclass
class _KernelItem:
    """内核私有流项。不公开。"""
    event: BusEvent | None = None
    messages_delta: list[Message] | None = None
    terminal: _TerminalItem | None = None

@dataclass
class _KernelState:
    """内核循环局部状态。每次 _run_items() 调用独立。"""
    messages: list[Message]
    turn: int = 0
    total_usage: dict[str, int] = field(default_factory=dict)
    last_stop_reason: str | None = None
    last_catalog_version: int | None = None
    cached_tool_definitions: list[dict[str, Any]] | None = None
```

### Example 2: _resolve_tool_definitions 双路径
```python
# Source: docs/specs/2026-04-02-kernel-generator-first.md Section 4.6
# File: matmaster/core/agent.py (内部 helper)

def _resolve_tool_definitions(
    spec: AgentRuntimeSpec,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    # Phase 2 路径（当 tool_catalog 非 None 时激活）
    if spec.tool_catalog is not None:
        current_version = spec.tool_catalog.version
        if current_version != state.last_catalog_version:
            state.cached_tool_definitions = spec.tool_catalog.build_definitions()
            state.last_catalog_version = current_version
        return state.cached_tool_definitions

    # Phase 1 回退
    if (
        spec.tool_registry
        and hasattr(spec.tool_registry, 'get_tool_definitions')
    ):
        return spec.tool_registry.get_tool_definitions()
    return None
```

### Example 3: ToolCatalog Phase 1 Facade
```python
# Source: docs/specs/2026-04-02-tool-runtime-v2.md Section 6.8 + D-04
# File: matmaster/tools/tool_catalog.py

class ToolCatalog:
    """Phase 1: facade over ToolRegistry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._version: int = 0

    def register_overlay(self, tool: Tool, *, source: str = "mcp") -> None:
        self._registry.register(tool, source=source)
        self._version += 1

    def get_tool(self, tool_name: str) -> ToolInstance | None:
        # 从 registry 查找并包装为 ToolInstance
        raw_tool = self._registry._tools.get(tool_name)
        if raw_tool is None:
            return None
        # Phase 1: 简单包装，ToolSpec/ToolBinding 使用默认值
        return _wrap_as_instance(raw_tool, self._registry._sources.get(tool_name, "unknown"))

    def build_definitions(self) -> list[dict[str, Any]]:
        return self._registry.get_tool_definitions()

    @property
    def version(self) -> int:
        return self._version
```

### Example 4: ToolResult 升级后的同步点
```python
# BEFORE (current):
class ToolResult(BaseModel):
    status: str = "success"
    content: str = ""
    info: dict[str, Any] = Field(default_factory=dict)

# AFTER (TRES-01):
class ToolResult(BaseModel):
    status: str = "success"
    content: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

# Sync points to update:
# 1. EventEmitterHook.post_tool_call: result.info → result.payload
# 2. OutputProcessorHook.post_tool_call: result.info → result.payload
# 3. ToolResultEvent.info → ToolResultEvent.payload
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Kernel 内联工具执行 | ToolRunner Protocol + InlineToolRunner | Phase 32 | 工具执行链可独立测试和替换 |
| _run_loop() 直接返回 | _run_items() AsyncGenerator | Phase 32 | 统一事件产出路径 |
| ToolResult.info | ToolResult.payload + meta | Phase 32 | 结构化数据与元信息分离 |
| AgentRuntimeSpec 无预留字段 | 5 个 Tool Runtime v2 字段 | Phase 32 | Exp 层可注入完整 runtime |
| ToolRegistry 直接被 Kernel 消费 | ToolCatalog facade | Phase 32 | base+overlay 分离，version 驱动刷新 |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2+ / pytest-asyncio 0.24.0+ |
| Config file | pyproject.toml `[tool.pytest.ini_options]` asyncio_mode = "auto" |
| Quick run command | `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py -x -q` |
| Full suite command | `uv run python -m pytest tests/matmaster/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| KGEN-01 | _run_items() 产出 _KernelItem 序列 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Wave 0 |
| KGEN-02 | run_stream() yield BusEvent | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Wave 0 |
| KGEN-03 | run() 行为完全一致 | regression | `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -x -q` | Existing (39 tests) |
| KGEN-04 | _KernelState 局部状态 | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Wave 0 |
| KGEN-05 | LLM 后 yield final snapshot | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Wave 0 |
| TOBJ-01 | SessionCapabilities frozen model | unit | `uv run python -m pytest tests/matmaster/types/test_topology.py -x -q` | Wave 0 |
| TOBJ-02 | RuntimeTopology frozen model | unit | `uv run python -m pytest tests/matmaster/types/test_topology.py -x -q` | Wave 0 |
| TOBJ-03 | ToolPlane enum | unit | `uv run python -m pytest tests/matmaster/types/test_topology.py -x -q` | Wave 0 |
| TOBJ-04 | ToolSpec frozen model | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | Wave 0 |
| TOBJ-05 | ResourceClaim frozen model | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | Wave 0 |
| TOBJ-06 | ToolBinding frozen model | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | Wave 0 |
| TOBJ-07 | ToolInstance frozen dataclass | unit | `uv run python -m pytest tests/matmaster/types/test_tool_spec.py -x -q` | Wave 0 |
| TOBJ-08 | ToolDecision frozen model | unit | `uv run python -m pytest tests/matmaster/types/test_tool_decision.py -x -q` | Wave 0 |
| TCAT-01 | ToolCatalog base+overlay | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_catalog.py -x -q` | Wave 0 |
| TCAT-02 | ToolCatalog.version 递增 | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_catalog.py -x -q` | Wave 0 |
| TCAT-03 | 内部 ToolRegistry facade | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_catalog.py -x -q` | Wave 0 |
| TRUN-01 | ToolRunner Protocol | unit | `uv run python -m pytest tests/matmaster/core/test_tool_runner.py -x -q` | Wave 0 |
| TRUN-02 | InlineToolRunner | unit | `uv run python -m pytest tests/matmaster/core/test_tool_runner.py -x -q` | Wave 0 |
| TRUN-05 | spec.tool_runner 获取 | integration | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Wave 0 |
| TCON-02 | GuardPipeline 接口不变 | regression | `uv run python -m pytest tests/matmaster/core/test_guard_pipeline.py -x -q` | Existing |
| TRES-01 | ToolResult payload + meta | unit | `uv run python -m pytest tests/matmaster/tools/test_tool_result.py -x -q` | Existing (needs update) |
| SPEC-01 | AgentRuntimeSpec 5 字段 | unit | `uv run python -m pytest tests/matmaster/types/test_runtime.py -x -q` | Existing (needs update) |
| TDEF-01 | _resolve_tool_definitions | unit | `uv run python -m pytest tests/matmaster/core/test_agent_kernel_stream.py -x -q` | Wave 0 |
| REGR-01 | 全量 kernel 测试通过 | regression | `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -v` | Existing (39 tests) |
| REGR-03 | 安全检查不动 | regression | `uv run python -m pytest tests/matmaster/tools/test_bash_tool.py tests/matmaster/tools/test_read_tool.py -x -q` | Existing |

### Sampling Rate
- **Per task commit:** `uv run python -m pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -x -q`
- **Per wave merge:** `uv run python -m pytest tests/matmaster/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/types/test_topology.py` -- covers TOBJ-01, TOBJ-02, TOBJ-03
- [ ] `tests/matmaster/types/test_tool_spec.py` -- covers TOBJ-04, TOBJ-05, TOBJ-06, TOBJ-07
- [ ] `tests/matmaster/types/test_tool_decision.py` -- covers TOBJ-08
- [ ] `tests/matmaster/tools/test_tool_catalog.py` -- covers TCAT-01, TCAT-02, TCAT-03
- [ ] `tests/matmaster/core/test_tool_runner.py` -- covers TRUN-01, TRUN-02
- [ ] `tests/matmaster/core/test_agent_kernel_stream.py` -- covers KGEN-01~05, TRUN-05, TDEF-01
- [ ] Update `tests/matmaster/tools/test_tool_result.py` -- sync TRES-01 (info -> payload + meta)
- [ ] Update `tests/matmaster/types/test_events.py` -- sync ToolResultEvent.info -> payload
- [ ] Update `tests/matmaster/hooks/test_output_processor.py` -- sync result.info -> result.payload
- [ ] Update `tests/matmaster/core/test_hooks.py` -- sync event.info -> event.payload

## Key Implementation Details

### ToolResult 迁移精确影响清单 (D-02)

**Source files to modify (3):**
1. `matmaster/tools/tool_result.py` -- 删除 `info` 字段，新增 `payload` + `meta`
2. `matmaster/core/hooks.py:227` -- `info=result.info` -> `payload=result.payload`
3. `matmaster/hooks/output_processor.py:45` -- `dict(result.info)` -> `dict(result.payload)`, line 55/62 `info={...}` -> `payload={...}`

**Event type to modify (1):**
4. `matmaster/types/events.py:72` -- `ToolResultEvent.info` -> `ToolResultEvent.payload`

**Test files to update (4):**
5. `tests/matmaster/tools/test_tool_result.py` -- 3 assertions (L13, L19, L40)
6. `tests/matmaster/types/test_events.py:110` -- 1 assertion
7. `tests/matmaster/hooks/test_output_processor.py` -- 3 assertions (L38, L63)
8. `tests/matmaster/core/test_hooks.py:289` -- 1 assertion

### AgentRuntimeSpec 扩展精确字段列表 (D-03 + SPEC-01)

```python
# 新增 5 个可选字段，全部 None 默认值
tool_runner: ToolRunner | None = None
tool_catalog: ToolCatalog | None = None
runtime_topology: RuntimeTopology | None = None
capability_policy: CapabilityPolicy | None = None
structural_validation: StructuralValidation | None = None
```

需要 `from __future__ import annotations` + `TYPE_CHECKING` guard 来避免循环导入。`arbitrary_types_allowed=True` 已在现有 model_config 中配置。

### Session Protocol 扩展 (TOBJ-01)

Session Protocol 需增加 `capabilities` 属性。为保持向后兼容，应在具体实现（LocalSession / SSHSession）中提供默认实现，而非修改 Protocol 为必须属性。或者在 Protocol 中声明并在 runtime check 时容忍缺失。

当前 Phase 32 scope: 仅定义 SessionCapabilities 类型和在 Session Protocol 中声明属性。具体 Session 实现（LocalSession / SSHSession）的 capabilities 填充属于 Exp 层接入（Phase 34）。

### Kernel Generator 改造关键步骤

1. 定义 `_KernelItem` / `_KernelState` / `_TerminalItem` 为 agent.py 模块级私有类型
2. 将 `_run_loop()` 改写为 `_run_items()` AsyncGenerator，逻辑等价但 return -> yield terminal
3. 所有 `self._finish(...)` 调用改为 `yield _KernelItem(terminal=_TerminalItem(...))`
4. 工具执行段委托给 `tool_runner.execute_batch()`，on_result 回调 yield ToolResultEvent
5. LLM 调用后 yield ResponseEvent / ThoughtEvent（final snapshot）
6. `run()` 消费 `_run_items()` 收集 messages_delta + terminal -> KernelRunResult
7. `run_stream()` 消费 `_run_items()` 过滤 event -> yield BusEvent
8. LLM provider context manager 保持在 `run()` / `run_stream()` 外层管理

### agent.py 文件大小控制

当前 agent.py 700 行。新增 `_KernelItem` / `_KernelState` / `_TerminalItem` / `_run_items` / `run_stream` / `_resolve_tool_definitions` 后预估增长到 ~900 行。CLAUDE.md 规定单文件不超过 1000 行。ToolRunner 已抽到独立文件 `tool_runner.py`，agent.py 应保持在限制内。如果接近 1000 行，可考虑将 `_KernelItem` / `_KernelState` / `_TerminalItem` 抽到 `matmaster/core/_kernel_types.py` 私有模块。

## Open Questions

1. **Session Protocol capabilities 属性的向后兼容性**
   - What we know: Phase 32 定义 SessionCapabilities 类型，Session Protocol 声明 capabilities 属性
   - What's unclear: 现有 LocalSession / SSHSession 未实现该属性，runtime_checkable isinstance 检查是否会失败
   - Recommendation: Phase 32 仅定义类型，不修改 Session Protocol。或者在 Protocol 中使用带默认值的方式（但 Protocol 不支持默认实现属性）。实际接入留给 Phase 34。planner 决定边界。

2. **ToolCatalog facade 中 get_tool() 的包装实现**
   - What we know: D-04 锁定 facade 模式，需要将 Tool 包装为 ToolInstance
   - What's unclear: Phase 1 包装时 ToolSpec / ToolBinding 的字段值来源（现有 Tool Protocol 只有 name/description/json_schema/execute）
   - Recommendation: Phase 1 使用默认值填充（source="unknown", capabilities=frozenset(), effect_level="local_mutation", plane=ToolPlane.CONTROL_PLANE），Phase 2 由 Exp 层真正解析。

## Sources

### Primary (HIGH confidence)
- `docs/specs/2026-04-02-kernel-generator-first.md` -- 完整 Kernel generator 设计，含行级伪代码
- `docs/specs/2026-04-02-tool-runtime-v2.md` -- 完整 Tool Runtime v2 架构设计
- `matmaster/core/agent.py` -- 当前 AgentKernel 实现 (700 行)
- `matmaster/types/runtime.py` -- 当前 AgentRuntimeSpec / KernelResult 定义
- `matmaster/tools/tool_result.py` -- 当前 ToolResult 定义
- `matmaster/tools/tool_registry.py` -- 当前 ToolRegistry 实现
- `matmaster/types/events.py` -- 18 种事件类型定义
- `matmaster/core/hooks.py` -- Hook Protocol + EventEmitterHook
- `matmaster/core/guard_pipeline.py` -- GuardPipeline 实现
- `matmaster/types/session.py` -- Session Protocol 定义
- `matmaster/types/guards.py` -- Guard Protocol + GuardContext + GuardResult

### Secondary (MEDIUM confidence)
- `tests/matmaster/core/agent_kernel_test_helpers.py` -- 测试辅助工具（MockProvider / _make_spec / etc.）
- `matmaster/hooks/output_processor.py` -- result.info 消费点
- `matmaster/integration/event_payloads.py` -- 事件 payload 转换

### Tertiary (LOW confidence)
None -- 此 phase 纯内部重构，所有信息源均为一手代码和锁定设计文档。

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 项目内部重构，技术栈已固定
- Architecture: HIGH - 两份 spec 已经过双重评审并锁定，伪代码精确到行级
- Pitfalls: HIGH - 基于对现有代码的完整阅读和 D-02 影响面分析

**Research date:** 2026-04-02
**Valid until:** 2026-05-02 (内部重构，无外部依赖变化风险)
