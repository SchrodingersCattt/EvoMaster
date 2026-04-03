# Phase 33: ToolRunner 完整实现 + ToolScheduler - Research

**Researched:** 2026-04-02
**Domain:** asyncio 并发调度、jsonschema 校验、工具执行链编排
**Confidence:** HIGH

## Summary

Phase 33 在 Phase 32 已交付的类型体系（ToolSpec / ToolBinding / ToolInstance / ResourceClaim / ToolDecision）和基础设施（ToolCatalog / InlineToolRunner / GuardPipeline）之上，构建三个新模块（StructuralValidation、CapabilityPolicy、ToolScheduler）并实现完整 ToolRunner 执行链。

核心技术挑战集中在三个方面：(1) ToolScheduler 的读写锁 + 信号量并发控制，使用纯 asyncio 原语（Lock + Condition + 计数器 + Semaphore）实现，无第三方依赖；(2) StructuralValidation 的 jsonschema 校验集成，项目已有 jsonschema 4.26 作为依赖；(3) 完整 ToolRunner 执行链的七步串行编排与 InlineToolRunner 的替换/共存策略。

所有实现均受 `docs/specs/2026-04-02-tool-runtime-v2.md` spec 约束，Phase 33 对应 spec 的 Phase 1 调度策略（保守模式），不涉及约束迁移（Phase 2/spec Phase 2）。

**Primary recommendation:** 按 spec section 14 的文件布局创建三个新模块，然后实现完整 ToolRunner 并在 Exp.build_runtime() 中注入，InlineToolRunner 作为回退保留。

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 完整 ToolRunner 不调用 pre_hook/post_hook。Hook 系统之后完全重做，Phase 33 不考虑任何 Hook 兼容性。这是独立开发项目，无外部消费者依赖。
- **D-02:** RWLock 用 asyncio.Lock + asyncio.Condition + 读者计数器组合实现经典读写锁。counted 模式直接用 asyncio.Semaphore。不引入第三方锁库。
- **D-03:** Scheduler acquire 默认超时 60 秒。超时返回 ToolResult(status="error", meta={"layer": "scheduler"})。
- **D-04:** args_schema 校验使用 jsonschema 库（已是项目依赖，v4.26）完整校验。校验错误信息直接作为 ToolDecision.reason 返回给 LLM。
- **D-05:** 完整 ToolRunner 执行链严格遵循 spec section 9.1：Catalog 查找 -> StructuralValidation -> RunStateGuard -> CapabilityPolicy -> fast path -> Scheduler -> executor -> 释放。
- **D-06:** 错误处理严格遵循 spec section 9.3：每层统一产出 ToolResult，meta["layer"] 标记失败来源。
- **D-07:** CapabilityPolicy Phase 1 仅处理 effect_level 约束和 plane/capability 匹配，不迁移工具内部安全检查。
- **D-08:** Fast path 条件：effect_level="none" + claims 全 shared_read + fast_path_eligible=True -> 跳过 Scheduler，不跳过 CapabilityPolicy。
- **D-09:** 内建工具 ResourceClaim 按 spec section 8.2 表格声明。
- **D-10:** 激活路径：Exp.build_runtime() 构造完整 ToolRunner（含 ToolCatalog + StructuralValidation + GuardPipeline + CapabilityPolicy + ToolScheduler），通过 AgentRuntimeSpec.tool_runner 注入 Kernel。

### Claude's Discretion
- Scheduler 内部 RWLock 的具体实现细节（公平性策略、饥饿防护）
- StructuralValidation 的路径规范化具体实现
- CapabilityPolicy 的具体拒绝规则实现（在 spec 约束范围内）

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TRUN-03 | 实现完整 ToolRunner，执行链为 ToolCatalog 查找 -> StructuralValidation -> RunStateGuard -> CapabilityPolicy -> fast path 判定 -> ToolScheduler -> executor -> 释放 | spec section 9.1 完整链路定义 + section 9.3 错误处理表 + 现有 ToolRunner Protocol 和 ToolExecutionContext |
| TRUN-04 | 实现 ToolScheduler，基于 ResourceClaim 调度（exclusive 互斥 / shared_read 并发 / counted 信号量），支持 fast path 跳过 | spec section 8 调度模型 + D-02 asyncio 原语选择 + D-03 超时策略 |
| TCON-01 | 实现 StructuralValidation（无状态），负责 args_schema 校验 / 路径规范化 / plane 启用检查 / session_capabilities 匹配 | spec section 6.9 Layer A + D-04 jsonschema 库 + jsonschema 4.26 验证 |
| TCON-03 | 实现 CapabilityPolicy Protocol，Phase 1 处理 effect_level 约束和 plane/capability 匹配 | spec section 6.9 Layer C + D-07 scope 约束 |

</phase_requirements>

## Spec vs Code Mismatches (CRITICAL)

研究过程中发现 Phase 32 实现的类型与 spec 之间存在若干字段命名和默认值差异。Phase 33 实现必须处理这些差异：

| 维度 | Spec 定义 | Phase 32 代码 | Phase 33 影响 |
|------|-----------|--------------|---------------|
| **ResourceClaim.resource_id** | `resource: str` | `resource_id: str` | Scheduler 消费 `resource_id` 字段名，与 spec 不同但代码已定型 |
| **ResourceClaim.limit** | `max_concurrent: int = 1` | `limit: int \| None = None` | Scheduler 读 `limit` 字段，counted 模式下 None 需要有合理默认值 |
| **ToolSpec.effect_level** | `Literal["none", "local_mutation", "external_effect"]` | `str = "local_mutation"` (注释写 "pure_read") | StructuralValidation 和 CapabilityPolicy 需使用 spec 的三值枚举 "none" 而非注释中的 "pure_read" |
| **ToolSpec.fast_path_eligible** | `bool = True` | `bool = False` | Fast path 默认行为不同；当前默认 False 更保守，Phase 33 应保持当前代码默认值，需要按 spec section 10 对内建工具显式设置 |
| **ToolSpec.usage_hint** | 存在 | 不存在 | Phase 33 不需要此字段，无影响 |

**处理策略：** Phase 33 应以当前代码定义为准（resource_id、limit、effect_level string type、fast_path_eligible=False），因为 Phase 32 的类型已经通过测试稳定。Scheduler/Validation/Policy 实现应消费现有字段名。

## Standard Stack

### Core (Phase 33 无新增外部依赖)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| jsonschema | 4.26.0 | args_schema 校验 | 项目已有依赖，D-04 锁定使用 |
| asyncio (stdlib) | Python 3.10+ | Lock/Condition/Semaphore 并发原语 | D-02 锁定纯 asyncio 实现 |
| pydantic | 项目现有 | ToolDecision frozen model | 已有模式，StructuralValidation/CapabilityPolicy 返回值 |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| asyncio.Lock+Condition RWLock | aiorwlock 第三方库 | D-02 明确排除第三方锁库 |
| jsonschema | pydantic validation | jsonschema 直接消费 OpenAI tool schema 格式；pydantic 需要额外转换层 |

## Architecture Patterns

### Recommended File Layout (per spec section 14)

```
matmaster/core/
  structural_validation.py   # NEW: Layer A -- 无状态参数/拓扑校验
  capability_policy.py       # NEW: Layer C -- effect_level/capability 策略
  tool_scheduler.py          # NEW: ResourceClaim 调度器
  tool_runner.py             # EXTEND: 新增完整 ToolRunner 类
```

### Pattern 1: 完整 ToolRunner 执行链 (spec section 9.1)

**What:** 七步串行流水线，每步产出 ToolResult 或继续

**When to use:** 所有经 Exp.build_runtime() 构造的 AgentRuntime

**Example:**
```python
# Source: spec section 9.1 + 9.3 error handling table
class FullToolRunner:
    """完整 ToolRunner: Catalog -> Validation -> Guard -> Policy -> Scheduler -> Execute -> Release."""

    def __init__(
        self,
        catalog: ToolCatalog,
        structural_validation: StructuralValidation,
        guard_pipeline: GuardPipeline,
        capability_policy: CapabilityPolicy,
        scheduler: ToolScheduler,
        topology: RuntimeTopology,
    ) -> None:
        ...

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[..., Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        results = []
        for tc in tool_calls:
            # 1. Cancel check
            if ctx.stop_event and ctx.stop_event.is_set():
                tr = ToolResult(status="cancelled", content="Run cancelled.")
                ...
                continue

            # 2. Catalog lookup
            instance = self._catalog.get_tool(tc.name)
            if instance is None:
                tr = ToolResult(status="error", content=f"Unknown tool: {tc.name}")
                ...
                continue

            # 3. StructuralValidation (Layer A)
            decision = self._validation.validate(self._topology, instance, tc.arguments)
            if decision.decision == "deny":
                tr = ToolResult(status="error", content=decision.reason,
                               meta={"layer": "structural"})
                ...
                continue

            # 4. RunStateGuard (Layer B)
            guard_result = self._guard_pipeline.evaluate(tc, ctx.turn, ctx.max_turns)
            if not guard_result.allowed:
                tr = ToolResult(status="error", content=guard_result.reason or "Guard denied",
                               meta={"layer": "guard"})
                ...
                continue

            # 5. CapabilityPolicy (Layer C)
            decision = self._policy.evaluate(self._topology, instance, tc.arguments)
            if decision.decision == "deny":
                tr = ToolResult(status="error", content=decision.reason,
                               meta={"layer": "policy", "guidance": decision.guidance})
                ...
                continue

            # 6. Fast path check
            claims = instance.tool_binding.resource_claims
            is_fast = (
                instance.tool_spec.effect_level == "none"
                and all(c.mode == "shared_read" for c in claims)
                and instance.tool_spec.fast_path_eligible
            )

            # 7. Scheduler acquire (skip for fast path)
            ticket = None
            if not is_fast:
                ticket = await self._scheduler.acquire(claims, timeout=60.0)
                if ticket is None:
                    tr = ToolResult(status="error", content="Scheduling timeout",
                                   meta={"layer": "scheduler"})
                    ...
                    continue

            # 8. Execute
            try:
                tr = await instance.tool_executor(tc.arguments)
            except Exception as e:
                tr = ToolResult.from_error(tc.name, e)
            finally:
                if ticket is not None:
                    self._scheduler.release(ticket)

            results.append((tc, tr))
            if on_result:
                await on_result(tc, tr)

        return results
```

### Pattern 2: ToolScheduler RWLock (D-02)

**What:** per-resource 读写锁 + 信号量

**When to use:** ToolScheduler 内部，管理 ResourceClaim 的并发控制

**Example:**
```python
# Source: 经典读写锁 + asyncio 原语
class _RWLock:
    """经典读写锁：shared_read 可并发，exclusive 互斥。"""
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self._readers: int = 0
        self._writer: bool = False

    async def acquire_read(self, timeout: float) -> bool:
        async with self._lock:
            deadline = asyncio.get_event_loop().time() + timeout
            while self._writer:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return False
            self._readers += 1
            return True

    def release_read(self) -> None:
        # Must be called under lock or careful sequencing
        ...

    async def acquire_write(self, timeout: float) -> bool:
        async with self._lock:
            deadline = asyncio.get_event_loop().time() + timeout
            while self._writer or self._readers > 0:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return False
            self._writer = True
            return True

    def release_write(self) -> None:
        ...
```

### Pattern 3: StructuralValidation jsonschema 集成

**What:** 无状态校验器，消费 ToolSpec.args_schema 和 RuntimeTopology

**Example:**
```python
# Source: jsonschema 4.26 API + spec section 6.9 Layer A
import jsonschema

class StructuralValidation:
    def validate(
        self,
        topology: RuntimeTopology,
        instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision:
        # 1. args_schema validation
        schema = instance.tool_spec.args_schema
        if schema:
            try:
                jsonschema.validate(tool_args, schema)
            except jsonschema.ValidationError as e:
                return ToolDecision(
                    decision="deny",
                    reason=f"Invalid arguments: {e.message}",
                )

        # 2. Plane enabled check
        plane = instance.tool_binding.plane
        if plane not in topology.active_planes:
            return ToolDecision(
                decision="deny",
                reason=f"Tool plane '{plane.value}' is not active in current topology",
            )

        # 3. Session capabilities match
        caps = topology.session_capabilities
        if caps is not None:
            # Example: shell tools require shell capability
            ...

        return ToolDecision(decision="allow")
```

### Anti-Patterns to Avoid

- **在 StructuralValidation 中引入状态：** Layer A 必须是纯函数式的，不持有 per-run 状态。运行态约束属于 Layer B (GuardPipeline)。
- **CapabilityPolicy 依赖运行态数据：** Policy 只接收 (topology, instance, args) 三元组，不读 recent_calls 或 turn 计数器。
- **ToolScheduler 按 plane 调度：** spec 明确说调度基于 resource_claims 而非 plane 分类（D-02 设计决策）。
- **在完整 ToolRunner 中调用 Hook：** D-01 明确排除 Hook。完整 ToolRunner 不调用 pre_hook/post_hook。
- **并发执行 execute_batch 中的多个 tool_call：** 完整 ToolRunner 对每个 tool_call 串行走完整链路（catalog -> validation -> guard -> policy -> scheduler -> execute -> release），Scheduler 负责真正的并发控制。InlineToolRunner 的 gather 并行模式不适用于完整 ToolRunner。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON Schema 校验 | 手写 args_schema 字段验证 | jsonschema.validate() | JSON Schema 规范复杂（allOf/anyOf/patternProperties），手写会遗漏 edge case |
| 读写锁 | 无（stdlib 没有 async RWLock） | 自实现（D-02 锁定） | asyncio 没有内建 RWLock，但 40-60 行经典实现即可覆盖 |
| 信号量 | 自写计数器 | asyncio.Semaphore | stdlib 提供完整实现，counted 模式直接使用 |

**Key insight:** 这个 phase 的核心复杂度不在单个组件（每个组件都相对简单），而在组件间的串行编排和错误传播的一致性（spec section 9.3）。

## Common Pitfalls

### Pitfall 1: RWLock 的 Condition.wait() 超时处理

**What goes wrong:** asyncio.Condition.wait() 需要在持有底层 Lock 的状态下调用，超时后仍然持有 Lock，必须正确处理。
**Why it happens:** asyncio.Condition 的 wait() 在等待前释放锁、被唤醒后重新获取锁。如果 wait_for 超时，asyncio.TimeoutError 是在重新获取锁之后抛出的（Python 3.11+ 行为）。
**How to avoid:** 使用 `async with self._lock:` 上下文管理器，在其内部做 wait_for，timeout 后自然退出上下文释放锁。
**Warning signs:** 测试中出现死锁或 Lock 未释放警告。

### Pitfall 2: ResourceClaim.limit 为 None 的 counted 模式

**What goes wrong:** 代码中 ResourceClaim.limit 类型是 `int | None`，但 counted 模式需要一个正整数创建 Semaphore。
**Why it happens:** Phase 32 定义 limit 默认为 None（仅 counted 模式有意义），但如果声明 counted 模式却忘记设 limit，Semaphore(None) 会失败。
**How to avoid:** Scheduler 在创建 Semaphore 时对 limit 做 `limit or 1` 防御性处理，并记录 WARNING 日志。
**Warning signs:** asyncio.Semaphore 构造时 ValueError。

### Pitfall 3: GuardPipeline.evaluate() 的签名与完整 ToolRunner 的集成

**What goes wrong:** GuardPipeline.evaluate() 接收 `(tool_call: ToolCallData, current_turn: int, max_turns: int)` 并内部构造 GuardContext。完整 ToolRunner 需要确保传递正确的 turn/max_turns。
**Why it happens:** GuardPipeline 维护自己的 recent_calls deque，这是有状态的。完整 ToolRunner 必须使用与 InlineToolRunner 相同的 GuardPipeline 实例才能正确检测循环。
**How to avoid:** 完整 ToolRunner 接收构造好的 GuardPipeline 实例（而非 guards 列表），在 Exp.build_runtime() 中统一构造。
**Warning signs:** 循环检测 Guard 不触发。

### Pitfall 4: ToolCatalog.get_tool() 返回的 ToolInstance 缺少正确的 ResourceClaim

**What goes wrong:** 当前 ToolCatalog.get_tool() 在 Phase 32 实现中，对 ToolBinding 硬编码 `resource_claims=()`（空元组）和 `plane=CONTROL_PLANE`。Scheduler 无法正确调度。
**Why it happens:** Phase 32 的 ToolCatalog 是 facade，将 ToolRegistry 的 Tool 包装为 ToolInstance，但无法从旧 Tool Protocol 推导 ResourceClaim。
**How to avoid:** Phase 33 需要扩展 ToolCatalog.get_tool()，或在 build_runtime() 阶段为每个内建工具声明正确的 ToolSpec + ToolBinding（含 resource_claims），建立真正的 base 层。这是 D-09 的核心要求。
**Warning signs:** 所有工具走 fast path 或所有工具走 exclusive 默认。

### Pitfall 5: effect_level 字符串值不匹配

**What goes wrong:** ToolSpec 代码注释写 "pure_read" 但 spec 用 "none"，CapabilityPolicy 和 fast path 判断使用哪个值不一致。
**Why it happens:** Phase 32 的 ToolSpec.effect_level 是 str 类型（非 Literal），注释与 spec 不完全一致。
**How to avoid:** Phase 33 使用 spec 定义的三值："none" / "local_mutation" / "external_effect"。在为内建工具设置 ToolSpec 时，使用 spec section 10 的表格值。
**Warning signs:** fast path 条件永远不满足。

### Pitfall 6: Exp.build_runtime() 中完整 ToolRunner 的构造时机

**What goes wrong:** 完整 ToolRunner 需要 ToolCatalog、StructuralValidation、GuardPipeline、CapabilityPolicy、ToolScheduler 五个依赖。如果在 assemble() 阶段构造会过早（tools 还没注册）；必须在 build_runtime() 中 registry 完成注册后才能构造。
**Why it happens:** 当前 build_runtime() 先注册 tools，再构造 spec.model_copy(update={...})。ToolRunner 构造必须在 tools 注册完成后、spec 最终更新时。
**How to avoid:** 在 build_runtime() 末尾、构造最终 spec 之前创建完整 ToolRunner，注入 spec.tool_runner。
**Warning signs:** ToolRunner 的 catalog 为空或缺少工具。

## Code Examples

### StructuralValidation 完整实现框架

```python
# Source: spec section 6.9 Layer A + D-04
from __future__ import annotations
from typing import Any
import jsonschema
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology, ToolPlane

class StructuralValidation:
    """Layer A: 无状态参数/拓扑校验。"""

    def validate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision:
        # 1. args_schema 校验
        schema = tool_instance.tool_spec.args_schema
        if schema:
            try:
                jsonschema.validate(tool_args, schema)
            except jsonschema.ValidationError as e:
                return ToolDecision(decision="deny", reason=f"Invalid arguments: {e.message}")

        # 2. plane 启用检查
        plane = tool_instance.tool_binding.plane
        if plane not in runtime_topology.active_planes:
            return ToolDecision(
                decision="deny",
                reason=f"Plane '{plane.value}' is not active",
            )

        # 3. session_capabilities 匹配
        caps = runtime_topology.session_capabilities
        if caps is not None:
            binding = tool_instance.tool_binding
            spec = tool_instance.tool_spec
            # SESSION_SHELL 工具需要 shell 能力
            if binding.plane == ToolPlane.SESSION_SHELL and not caps.shell_input:
                # shell_input=False 不阻止执行（stateless shell 也能 exec_bash）
                # 但如果 shell_persistence 需要 persistent 而当前是 stateless，则阻止
                pass  # Phase 1: 不做细粒度 capability check

        return ToolDecision(decision="allow")
```

### CapabilityPolicy Phase 1 实现框架

```python
# Source: spec section 6.9 Layer C + D-07
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ToolInstance
from matmaster.types.topology import RuntimeTopology

@runtime_checkable
class CapabilityPolicy(Protocol):
    def evaluate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision: ...

class DefaultCapabilityPolicy:
    """Phase 1 CapabilityPolicy: effect_level + plane/capability matching."""

    def evaluate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision:
        spec = tool_instance.tool_spec

        # effect_level 约束: external_effect 工具可能需要拓扑级限制
        # Phase 1 placeholder: 仅检查 plane/capability 匹配
        if spec.capabilities:
            # 检查 topology.session_capabilities 是否满足工具要求的 capabilities
            caps = runtime_topology.session_capabilities
            if caps is not None:
                # Example: upload_support 检查
                if "artifact.download" in spec.capabilities and not caps.upload_support:
                    return ToolDecision(
                        decision="deny",
                        reason="Session does not support artifact upload",
                        guidance="This tool requires upload support. Check session configuration.",
                    )

        return ToolDecision(decision="allow")
```

### ToolScheduler 核心结构

```python
# Source: spec section 8 + D-02 + D-03
from __future__ import annotations
import asyncio
from dataclasses import dataclass

@dataclass
class SchedulerTicket:
    """Scheduler 返回的资源持有凭证，release 时需要。"""
    resource_locks: list[tuple[str, str]]  # [(resource_id, mode), ...]

class ToolScheduler:
    """ResourceClaim-based tool scheduling."""

    def __init__(self, default_timeout: float = 60.0) -> None:
        self._default_timeout = default_timeout
        self._rw_locks: dict[str, _RWLock] = {}         # per resource_id
        self._semaphores: dict[str, asyncio.Semaphore] = {}  # per resource_id (counted)

    async def acquire(
        self,
        claims: tuple[ResourceClaim, ...],
        timeout: float | None = None,
    ) -> SchedulerTicket | None:
        """Acquire all claimed resources. Returns None on timeout."""
        ...

    def release(self, ticket: SchedulerTicket) -> None:
        """Release all resources held by ticket."""
        ...
```

### 内建工具 ResourceClaim 声明 (spec section 8.2 + 10)

```python
# Source: spec section 8.2 内建工具 ResourceClaim 表
# Phase 33 在 ToolCatalog 或 build_runtime 阶段声明

BUILTIN_CLAIMS: dict[str, tuple[ResourceClaim, ...]] = {
    "execute_bash": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "list_dir": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "glob": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "grep": (ResourceClaim(resource_id="session", mode="exclusive"),),
    "read_file": (ResourceClaim(resource_id="workspace", mode="shared_read"),),
    "write_file": (ResourceClaim(resource_id="workspace", mode="exclusive"),),
    "edit_file": (ResourceClaim(resource_id="workspace", mode="exclusive"),),
    "task_create": (ResourceClaim(resource_id="task-store", mode="exclusive"),),
    "task_get": (ResourceClaim(resource_id="task-store", mode="shared_read"),),
    "task_list": (ResourceClaim(resource_id="task-store", mode="shared_read"),),
    "task_update": (ResourceClaim(resource_id="task-store", mode="exclusive"),),
    "task_complete": (ResourceClaim(resource_id="task-store", mode="exclusive"),),
    "web_search": (ResourceClaim(resource_id="web", mode="counted", limit=3),),
    "web_fetch": (ResourceClaim(resource_id="web", mode="counted", limit=3),),
    "spawn": (ResourceClaim(resource_id="spawn", mode="counted", limit=2),),
    "monitor_job": (
        ResourceClaim(resource_id="workspace", mode="exclusive"),
        ResourceClaim(resource_id="artifact-sync", mode="exclusive"),
    ),
}

BUILTIN_SPECS: dict[str, dict] = {
    # effect_level 按 spec section 10 表格
    "execute_bash": {"effect_level": "local_mutation", "capabilities": frozenset({"shell.execute"}), "plane": ToolPlane.SESSION_SHELL},
    "list_dir": {"effect_level": "none", "capabilities": frozenset({"workspace.list"}), "plane": ToolPlane.SESSION_SHELL},
    "read_file": {"effect_level": "none", "capabilities": frozenset({"workspace.read"}), "plane": ToolPlane.SESSION_FS, "fast_path_eligible": True},
    "task_get": {"effect_level": "none", "capabilities": frozenset({"task.read"}), "plane": ToolPlane.CONTROL_PLANE, "fast_path_eligible": True},
    "task_list": {"effect_level": "none", "capabilities": frozenset({"task.read"}), "plane": ToolPlane.CONTROL_PLANE, "fast_path_eligible": True},
    "web_search": {"effect_level": "external_effect", "capabilities": frozenset({"web.search"}), "plane": ToolPlane.CONTROL_PLANE},
    # ... etc per spec section 10
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Kernel 内联 guard -> gather -> post-hook | ToolRunner Protocol 委托 (Phase 32) | 2026-04-02 | Kernel 不再直接管理工具执行 |
| asyncio.gather() 全并发 | ResourceClaim 调度 (Phase 33) | 2026-04-02 | 按资源声明控制并发 |
| 安全检查散落在工具内部 | 三层约束模型 (Phase 33 Layer A+C) | 2026-04-02 | 统一的 deny 决策格式 |

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio (asyncio_mode=auto) |
| Config file | pytest.ini |
| Quick run command | `uv run pytest tests/matmaster/core/ -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TRUN-03 | 完整 ToolRunner 七步执行链端到端 | unit | `uv run pytest tests/matmaster/core/test_full_tool_runner.py -x` | -- Wave 0 |
| TRUN-03 | Catalog 未找到工具返回 error ToolResult | unit | `uv run pytest tests/matmaster/core/test_full_tool_runner.py::TestCatalogMiss -x` | -- Wave 0 |
| TRUN-03 | Executor 异常返回 error ToolResult | unit | `uv run pytest tests/matmaster/core/test_full_tool_runner.py::TestExecutorException -x` | -- Wave 0 |
| TRUN-03 | Cancel event 跳过剩余 tool_calls | unit | `uv run pytest tests/matmaster/core/test_full_tool_runner.py::TestCancelSemantics -x` | -- Wave 0 |
| TRUN-04 | Scheduler exclusive 互斥 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py::TestExclusive -x` | -- Wave 0 |
| TRUN-04 | Scheduler shared_read 并发 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py::TestSharedRead -x` | -- Wave 0 |
| TRUN-04 | Scheduler counted 信号量限制 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py::TestCounted -x` | -- Wave 0 |
| TRUN-04 | Scheduler 超时返回 None | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py::TestTimeout -x` | -- Wave 0 |
| TRUN-04 | Fast path 跳过 Scheduler | unit | `uv run pytest tests/matmaster/core/test_full_tool_runner.py::TestFastPath -x` | -- Wave 0 |
| TCON-01 | args_schema 校验失败返回 deny | unit | `uv run pytest tests/matmaster/core/test_structural_validation.py::TestArgsSchema -x` | -- Wave 0 |
| TCON-01 | plane 未启用返回 deny | unit | `uv run pytest tests/matmaster/core/test_structural_validation.py::TestPlaneCheck -x` | -- Wave 0 |
| TCON-01 | session_capabilities 不匹配返回 deny | unit | `uv run pytest tests/matmaster/core/test_structural_validation.py::TestCapabilities -x` | -- Wave 0 |
| TCON-03 | effect_level 约束返回 deny + guidance | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py::TestEffectLevel -x` | -- Wave 0 |
| TCON-03 | capability 不匹配返回 deny + guidance | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py::TestCapabilityMatch -x` | -- Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/matmaster/core/ -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps

- [ ] `tests/matmaster/core/test_structural_validation.py` -- covers TCON-01
- [ ] `tests/matmaster/core/test_capability_policy.py` -- covers TCON-03
- [ ] `tests/matmaster/core/test_tool_scheduler.py` -- covers TRUN-04
- [ ] `tests/matmaster/core/test_full_tool_runner.py` -- covers TRUN-03

## Open Questions

1. **ToolCatalog.get_tool() 需要增强以返回正确的 ToolBinding/ResourceClaim**
   - What we know: 当前 get_tool() 硬编码 plane=CONTROL_PLANE, resource_claims=() 空元组
   - What's unclear: 是在 ToolCatalog 层面通过查表增强 get_tool()，还是在 build_runtime() 层面为每个工具注册完整的 ToolInstance（含正确 binding）
   - Recommendation: 在 build_runtime() 层面创建完整 ToolInstance 注册到 catalog base 层，因为 ResourceClaim 和 plane 信息来自 spec section 8.2/10 的静态声明表

2. **FullToolRunner 内 tool_call 是串行还是可以部分并行**
   - What we know: InlineToolRunner 用 gather 并发执行 approved tools，但完整 ToolRunner 的 Scheduler 本身就控制并发
   - What's unclear: 是否应该对 execute_batch 内多个 tool_call 使用 asyncio.gather 让 Scheduler 自然排队
   - Recommendation: execute_batch 内对多个 tool_call 使用 asyncio.gather（或 TaskGroup），让 Scheduler 的 acquire/release 自然控制并发。串行处理会浪费独立资源（如 web_search 和 read_file 可以并行）。但需要注意 GuardPipeline 是有状态的（recent_calls），所以 guard 评估部分必须串行，只有通过所有约束层的 tool_call 才进入 gather 并发执行。

3. **effect_level 字符串与 spec Literal 的对齐**
   - What we know: ToolSpec.effect_level 是 str 类型（Phase 32 决策），spec 定义三个值 "none" / "local_mutation" / "external_effect"
   - What's unclear: Phase 33 是否应该将 str 收窄为 Literal
   - Recommendation: Phase 33 不改 ToolSpec 类型定义（避免改动 Phase 32 产出），但 StructuralValidation/CapabilityPolicy/fast path 判断中使用 spec 的三值字符串常量。

## Project Constraints (from CLAUDE.md)

- 始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 按 标准库 -> 第三方 -> 本地 分组，全部放文件顶部
- 单文件超过 1000 行必须重构
- DAO 层不吞异常；service 层按需降级
- 新增工具必须实现 Tool Protocol 并返回 ToolResult
- Protocol 接口使用 @runtime_checkable 装饰器

## Sources

### Primary (HIGH confidence)
- `docs/specs/2026-04-02-tool-runtime-v2.md` -- Tool Runtime v2 完整架构 spec，section 6.9 / 8 / 9 / 10 / 13 / 14 直接定义 Phase 33 范围
- `matmaster/core/tool_runner.py` -- ToolRunner Protocol + InlineToolRunner 当前实现
- `matmaster/tools/tool_catalog.py` -- ToolCatalog Phase 1 facade 当前实现
- `matmaster/types/tool_spec.py` -- ToolSpec / ResourceClaim / ToolBinding / ToolInstance 定义
- `matmaster/types/tool_decision.py` -- ToolDecision 定义
- `matmaster/types/topology.py` -- RuntimeTopology / SessionCapabilities / ToolPlane 定义
- `matmaster/core/guard_pipeline.py` -- GuardPipeline.evaluate() 接口和 LoopDetectionGuard
- `matmaster/types/guards.py` -- Guard Protocol / GuardContext / GuardResult

### Secondary (MEDIUM confidence)
- `matmaster/core/agent.py` L274-420 -- _run_items() 中 tool_runner 的调用方式
- `matmaster/core/exp.py` L148-244 -- build_runtime() 当前构造流程
- `matmaster/types/runtime.py` -- AgentRuntimeSpec 字段定义（含 Phase 32 v2 字段）

### Tertiary (LOW confidence)
- jsonschema 4.26 API -- 基于项目中已安装版本验证，e.message 属性格式化

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 项目已有依赖，D-02/D-04 锁定选型
- Architecture: HIGH -- spec 完整定义了文件布局和执行链
- Pitfalls: HIGH -- 基于直接阅读现有代码发现的具体问题
- Spec-Code mismatches: HIGH -- 通过逐字段对比 spec 和 Phase 32 代码发现

**Research date:** 2026-04-02
**Valid until:** 2026-04-30 (stable -- spec 已冻结，代码基础已通过 Phase 32 测试)
