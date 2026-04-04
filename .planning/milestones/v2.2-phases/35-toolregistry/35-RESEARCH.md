# Phase 35: 约束迁移 + ToolRegistry 降级 - Research

**Researched:** 2026-04-03
**Domain:** 工具安全约束迁移、ToolRegistry 降级、三层约束模型 Phase 2 扩展
**Confidence:** HIGH

## Summary

Phase 35 是 v2.2 Wave C 的核心任务，目标是将分散在工具内部的安全检查逻辑统一迁入三层约束模型（Layer B: RunStateGuard, Layer C: CapabilityPolicy），同时将 ToolRegistry 降级为纯存储层，使 ToolCatalog 成为唯一上层消费接口。

本阶段的所有基础设施（FullToolRunner 七步链、GuardPipeline、CapabilityPolicy Protocol、ToolScheduler、ToolCatalog、ToolCompiler）在 Phase 32-34 中已完整实现并验证。Phase 35 的工作本质上是迁移（将检查逻辑从工具移到约束层）和清理（删除冗余方法、字段、代码路径），不涉及新的架构创建。

**Primary recommendation:** 按四步串行推进：(1) read-before-modify 迁入 RunStateGuard，(2) bash 安全检查迁入 CapabilityPolicy，(3) state_mode/stop_mode 枚举纠正 + Scheduler 消费，(4) ToolRegistry 降级 + ContextBuilder 改造。每步独立可验证。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** read-before-modify 检查完全从 WriteTool/EditTool 内部删除。ReadTracker 仅通过 GuardContext 注入 RunStateGuard。工具变纯粹——WriteTool 只写，EditTool 只编辑。符合 spec 设计意图：安全约束统一在三层模型，不在工具内部。
- **D-02:** ReadTracker 实例在 Exp.build_runtime() 中创建，注入 GuardPipeline 的 RunStateGuard。ReadTool 仍持有 tracker 引用调用 mark_read()——它是状态产生者不是检查者。Guard 层作为状态消费者检查 has_been_read()。符合 spec 6.10 "read_tracker 注入 GuardContext"。
- **D-03:** bash 危险命令检查 (is_dangerous_bash_command) 和 Python 内容检查 (is_dangerous_python_content) 全部从 bash_tool.py 迁入 CapabilityPolicy。bash_tool.py 变为纯执行层，零安全策略。CapabilityPolicy 根据 tool_name 分发不同检查逻辑。
- **D-04:** 激进删除。ToolRegistry 删除 execute()、get_tool_definitions()、get_tools_by_source() 方法。只保留 register()、all_tools、__contains__、__len__ 等纯存储接口。断点干净。
- **D-05:** AgentRuntimeSpec.tool_registry 字段删除。Kernel 仅通过 spec.tool_runner + spec.tool_catalog 消费工具。Exp.build_runtime() 不再把 registry 注入 spec。Kernel 中的 registry.execute() legacy fallback 路径同步删除。
- **D-06:** Kernel 中所有直接调用 spec.tool_registry.execute() 和 spec.tool_registry.get_tool_definitions() 的代码路径（agent.py L323, L500, L688, L856）全部删除。_resolve_tool_definitions() 不再有 registry fallback——必须走 tool_catalog。
- **D-07:** 按 spec 6.9 建议，移除 _build_tools() 中的工具逐行枚举，改为通用说明（如"使用 function calling 中声明的工具"）。消除 system prompt 与 tool_definitions 的不一致风险，尤其是 MCP overlay 动态注入后。ContextBuilder 不再依赖 tool_registry。
- **D-08:** 按 spec 6.6 纠正枚举值。state_mode 从 `str = "stateless"` 改为 `Literal["stateless", "persistent"] = "stateless"`。stop_mode 从 `str = "immediate"` 改为 `Literal["cancellable", "best_effort", "non_cancellable"] = "cancellable"`。当前代码中的 turn_scoped/session_scoped/immediate/graceful/detached 值未被任何代码消费，纠正代价为零。
- **D-09:** Phase 35 一步到位完成 CMIG-03：(1) ToolCompiler 根据工具元数据填充 state_mode/stop_mode，(2) Scheduler 根据 stop_mode 调整取消策略。当前 Scheduler 已有 stop_event 路径，改动量可控。

### Claude's Discretion
- RunStateGuard 的具体实现结构（是新增独立 Guard 类还是扩展 LoopDetectionGuard）
- CapabilityPolicy 中 bash/python 检查的具体分发逻辑
- ToolCompiler 中各内建工具的 state_mode/stop_mode 具体取值映射
- Scheduler 对 best_effort/non_cancellable 的具体行为差异实现
- Exp.run()/run_stream() 中 stop_event 注入路径的调整（不再走 tool_registry.all_tools）
- 测试文件的具体组织方式

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CMIG-01 | 扩展 GuardContext 增加 ReadTracker，将 read-before-modify 检查从 WriteTool/EditTool 迁入 RunStateGuard | GuardContext 是 dataclass，新增 read_tracker 字段即可。GuardPipeline.evaluate() 已有 ctx 构造逻辑。新增 RunStateGuard 类遵循 Guard Protocol |
| CMIG-02 | 将 bash_tool 的 _is_dangerous_command 迁入 CapabilityPolicy | DefaultCapabilityPolicy.evaluate() 已有 tool_instance 参数（含 tool_name），可直接根据 tool_name 分发检查。bash_tool.py 的正则模式集合可直接搬入 |
| CMIG-03 | ToolBinding 的 state_mode/stop_mode 字段启用，Scheduler 根据 SessionCapabilities 调整策略 | ToolBinding 当前用 str 类型，需改为 Literal。ToolCompiler 在 compile() 中构造 ToolBinding 时填充。ToolScheduler 已有 stop_event 传播路径 |
| CMIG-04 | ToolRegistry 降级为纯存储层，ToolCatalog 接管所有上层消费接口 | agent.py 4 处 legacy 路径需删除。exp.py 2 处 stop_event 注入走 tool_registry.all_tools 需改走 catalog。InlineToolRunner 走 spec.tool_registry.execute() 需删除或改走 catalog |
| CMIG-05 | ContextBuilder 工具来源迁移或移除 system prompt 工具枚举段 | ContextBuilder._build_tools() 当前 7 行，删除改为通用说明。build() 签名中 tool_registry 参数可改为可选或移除 |
</phase_requirements>

## Architecture Patterns

### 当前三层约束模型执行链

FullToolRunner 七步链中，Phase 35 改动集中在 Step 4 (Layer B) 和 Step 5 (Layer C)：

```
1. Cancel check (stop_event)
2. Catalog lookup (ToolCatalog.get_tool)
3. StructuralValidation (Layer A) --- 不改动
   3b. input_validator (tool-specific)
4. RunStateGuard (Layer B) --- CMIG-01: 扩展承接 read-before-modify
5. CapabilityPolicy (Layer C) --- CMIG-02: 扩展承接 bash/python 安全检查
6. Fast path check
7. Scheduler acquire --- CMIG-03: 消费 stop_mode
8. Execute + Release
9. Normalize + Truncate
```

### Pattern 1: RunStateGuard 实现（推荐独立 Guard 类）

**What:** 新增 `ReadBeforeModifyGuard` 类，实现 Guard Protocol，持有 ReadTracker 引用

**Why independent class:** LoopDetectionGuard 和 ReadBeforeModifyGuard 的关注点完全不同（循环检测 vs 文件读写跟踪），合并会违反单一职责。Guard Protocol 设计的初衷就是支持多个独立 Guard 的管线组合。

**Implementation:**

```python
# matmaster/core/guard_pipeline.py (新增)
class ReadBeforeModifyGuard:
    """Enforces read-before-modify for write/edit tools.

    Checks GuardContext.read_tracker to verify that target files
    have been read before write/edit operations.
    """

    _MODIFY_TOOLS = frozenset({"write_file", "edit_file"})

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        if ctx.tool_name not in self._MODIFY_TOOLS:
            return GuardResult(allowed=True)

        tracker = ctx.read_tracker
        if tracker is None:
            return GuardResult(allowed=True)  # No tracker = no enforcement

        file_path = ctx.tool_args.get("file_path", "")
        if not file_path:
            return GuardResult(allowed=True)  # No path = structural validation's job

        import posixpath
        normalized = posixpath.normpath(file_path)

        # write_file: only check existing files (new files are always OK)
        # Note: we cannot check path_exists here (no session access).
        # For write_file, the read-tracker check is sufficient:
        # if the file was never read, it's either new (OK) or existing (blocked).
        # The original WriteTool checked session.path_exists + tracker.
        # After migration, the Guard only checks tracker. This means:
        # - If file was read -> has_been_read=True -> allowed
        # - If file was not read and is new -> has_been_read=False -> denied
        # This is MORE restrictive than the original (which allowed new files).
        # Solution: for write_file, check if the tool is creating a new file
        # by looking at tool_args or delegating to a different path.
        #
        # CRITICAL DESIGN POINT: See Common Pitfalls section for resolution.

        if not tracker.has_been_read(normalized):
            return GuardResult(
                allowed=False,
                reason=f"File '{file_path}' must be read before modify",
                guidance="Read the file first using read_file before writing or editing.",
            )
        return GuardResult(allowed=True)
```

### Pattern 2: CapabilityPolicy bash/python 检查分发

**What:** DefaultCapabilityPolicy.evaluate() 根据 tool_instance.tool_spec.tool_name 分发到不同安全检查器

**Implementation:**

```python
# matmaster/core/capability_policy.py (扩展)
class DefaultCapabilityPolicy:
    def evaluate(self, runtime_topology, tool_instance, tool_args):
        spec = tool_instance.tool_spec

        # 1. effect_level constraint (existing)
        ...

        # 2. Fine-grained capability matching (existing)
        ...

        # 3. Phase 2: tool-specific safety checks
        if spec.tool_name == "execute_bash":
            return self._check_bash_safety(tool_args)

        return ToolDecision(decision="allow")

    def _check_bash_safety(self, tool_args):
        command = tool_args.get("command", "").strip()
        is_dangerous, reason = is_dangerous_bash_command(command)
        if is_dangerous:
            return ToolDecision(
                decision="deny",
                reason=f"Blocked: {reason}",
                guidance="Use a safer command or alternative approach.",
            )
        # Python content check applies when bash runs python scripts
        # (the original code didn't check this in bash_tool directly,
        # but the functions exist -- leave for future if needed)
        return ToolDecision(decision="allow")
```

### Pattern 3: ToolBinding 枚举纠正 + ToolCompiler 填充

**What:** ToolBinding state_mode/stop_mode 从 str 改为 Literal，ToolCompiler 在 compile() 时根据工具元数据填充

**Recommended state_mode/stop_mode mapping for builtins:**

| Tool | state_mode | stop_mode | Rationale |
|------|-----------|-----------|-----------|
| execute_bash | stateless | cancellable | 每次独立进程，可被 kill |
| read_file | stateless | cancellable | 无状态读 |
| write_file | stateless | cancellable | 原子写 |
| edit_file | stateless | cancellable | 原子替换 |
| list_dir / glob / grep | stateless | cancellable | 只读子进程 |
| task_* | stateless | cancellable | 本地 JSON 操作 |
| mm_web_search / web_fetch | stateless | best_effort | HTTP 请求中断可能留下半完成连接 |
| spawn | persistent | non_cancellable | 子 agent 一旦启动不可安全中断 |
| monitor_job | persistent | best_effort | HPC 轮询可中断但需尝试清理 |

### Pattern 4: ContextBuilder 通用说明替代工具枚举

**What:** 删除 `_build_tools()` 中的工具逐行列举，改为固定通用说明

```python
@staticmethod
def _build_tools(tool_registry=None) -> str:
    """Build the available tools section -- generic guidance only."""
    return (
        "# Tools\n\n"
        "Use the tools declared in function calling. "
        "Each tool's name, description, and parameter schema are "
        "provided in the function definitions."
    )
```

### Anti-Patterns to Avoid

- **在 Guard 层访问 Session:** Guard 层无法获得 session 引用（也不应该获得），不能在 Guard 中调用 session.path_exists()。write_file 的新文件检测必须用其他方式解决（见 Common Pitfalls）。
- **同时删除工具内检查和约束层检查:** 迁移过程中，同一个检查逻辑不应在两处同时存在。先在约束层实现新检查，验证等价后再删除工具内旧检查。
- **在 CapabilityPolicy 中持有状态:** CapabilityPolicy 是确定性无状态的（per-call evaluation），不应持有运行时状态。运行态约束（如 read-before-modify）属于 Layer B。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Guard 链组合 | 自定义 if-else 链 | GuardPipeline 的 external_guards list | 已有短路求值、调用记录、窗口管理 |
| 工具查找 + 执行 | 直接调 registry.execute() | FullToolRunner.execute_batch() | 七步链已包含所有约束层 |
| OpenAI tool_definitions | 手动构造 JSON | ToolCatalog.build_definitions() | 自动合并 base + overlay |
| 资源调度 | 手动 asyncio.Lock | ToolScheduler.acquire/release | RWLock + Semaphore 已实现 |

## Common Pitfalls

### Pitfall 1: write_file 新文件检测在 Guard 层缺失

**What goes wrong:** 原 WriteTool 的 read-before-modify 检查逻辑是 `session.path_exists(file_path) AND NOT tracker.has_been_read(path)` -- 只对已存在的文件检查是否读过。Guard 层没有 session 引用，无法调用 path_exists()。如果直接用 `NOT tracker.has_been_read(path)` 替代，会误拦新文件创建。

**Why it happens:** Guard Protocol 的 GuardContext 只包含工具名、参数、turn 信息，不包含 session（设计上 Guard 不应依赖 session）。

**How to avoid:** 两个方案：

- **方案 A（推荐）:** 在 FullToolRunner 执行链中，Step 3b (input_validator) 保留 WriteTool 的 path_exists 检查作为 input_validator。ReadBeforeModifyGuard 只检查 edit_file（edit_file 的文件必定存在，不需要 path_exists 判断）。write_file 的 read-before-modify 通过 input_validator 路径处理。
  - 优点：不改变 Guard 的纯数据契约，利用已有的 input_validator 机制
  - 缺点：write_file 的安全检查还在工具侧（虽然是通过 validator 而非直接在 _execute 中）

- **方案 B:** 扩展 GuardContext 增加 session_query 回调 `path_exists: Callable[[str], bool] | None`。Guard 可选地调用它。
  - 优点：完全迁出工具
  - 缺点：Guard 依赖了 session 能力，违背 Guard 纯数据原则

- **方案 C:** write_file 也强制要求 read-before-modify（即使新文件也要先 read）。这改变了现有行为但更安全。
  - 优点：最简单，Guard 实现最纯粹
  - 缺点：改变用户行为——创建新文件前需要先调 read_file 确认不存在

**Recommendation:** 方案 A。write_file 的 path_exists 检查留在 input_validator，edit_file 的 read-before-modify 完全迁入 Guard。这符合分层原则：input_validator 是工具特有的语义校验，Guard 是跨工具的运行态约束。实际上 edit_file 永远操作已存在文件（str_replace 需要原内容），所以只有 edit_file 的 read-before-modify 适合纯 Guard 层检查。write_file 的逻辑涉及"文件是否存在"这一工具特有语义，适合 input_validator。

### Pitfall 2: CapabilityPolicy 中 is_dangerous_python_content 的处置

**What goes wrong:** bash_tool.py 中有两个安全检查函数：is_dangerous_bash_command（检查命令本身）和 is_dangerous_python_content（检查 Python 脚本内容）。但当前 bash_tool.py 的 _execute/_execute_async 中只调用了 is_dangerous_bash_command，is_dangerous_python_content 定义了但未在 BashTool 中使用。

**Why it matters:** 如果只迁移 is_dangerous_bash_command 到 CapabilityPolicy，is_dangerous_python_content 的正则模式集合是否需要迁移？留在 bash_tool.py 中的死代码是否应该清理？

**How to avoid:** 审计 is_dangerous_python_content 的实际调用者。如果只有 bash_tool.py 定义而无调用，按 D-03 的"全部从 bash_tool.py 迁入"精神，连模式定义一起迁到 CapabilityPolicy（作为备用检查能力），同时从 bash_tool.py 删除。CapabilityPolicy 中可以选择性启用 python content 检查（例如当检测到 `python -c` 或 `python3 script.py` 命令时）。

### Pitfall 3: agent.py _run_loop() legacy 路径中的 registry 引用

**What goes wrong:** agent.py 有两条路径：`_run_loop()`（run() 调用的旧路径）和 `_run_items()`（run_stream() 调用的新路径）。两条路径都有 registry.execute() 和 registry.get_tool_definitions() 的调用。删除 tool_registry 字段后，两条路径都需要修改。

**Why it happens:** _run_loop() 中直接调用 `spec.tool_registry.execute()`（L323），_run_items() 中有 registry fallback（L500, L688）。_call_llm() 中也有 registry.get_tool_definitions() 调用（L856）。

**How to avoid:**

- _run_loop() 中的工具执行必须改走 FullToolRunner（或直接删除 _run_loop，因为 run() 可以通过 _run_items() + 收集模式实现）
- _run_items() 中的 elif registry fallback（L496-500）删除，只保留 catalog 路径
- _call_llm() 中的 tool_defs 解析（L855-860）改走 catalog.build_definitions()

**Critical:** _run_loop() 是 `run()` 的实现路径，`run()` 是现有全部 50+ 测试的入口。删除 _run_loop() 或改变其工具执行路径需要确保所有测试通过。由于 Phase 34 后 build_runtime() 已注入 tool_runner，run() 路径的 spec 中 tool_runner is not None，所以 _run_items() 中会走 FullToolRunner 路径（L585-626）而非 legacy 路径（L627-745）。但 _run_loop() 是独立路径，仍然直接调用 registry.execute()。

**Resolution:** _run_loop() 中的工具执行也需要改走 FullToolRunner。最安全的做法是让 _run_loop() 也委托到 _run_items() 上（类似 run() -> _run_items() 收集模式），但这改动较大。更简单的做法是在 _run_loop() 中也加 `if spec.tool_runner` 分支（类似 _run_items() L585 的模式），并删除 else 分支中的 registry 调用。

### Pitfall 4: Exp.run()/run_stream() 的 stop_event 注入路径

**What goes wrong:** Exp.run() L344-347 和 Exp.run_stream() L387-390 都通过 `spec.tool_registry.all_tools` 遍历工具注入 stop_event。删除 tool_registry 字段后这条路径断裂。

**Why it happens:** 工具（特别是 BashTool）持有 `_stop_event` 属性，用于在执行过程中检查取消信号。当前注入路径是 `for tool in tool_registry.all_tools: tool._stop_event = stop_event`。

**How to avoid:** 两个选择：

- **方案 A:** 改走 ToolCatalog。但 ToolCatalog 存储的是 ToolInstance（frozen dataclass），不直接持有工具对象引用。需要在 ToolCatalog 上新增方法获取原始 Tool 引用。
- **方案 B（推荐）:** ToolCatalog 保留对内部 registry 的引用。通过 `catalog.registry._tools.values()` 获取工具对象。但这暴露了内部实现。
- **方案 C（最佳）:** 在 ToolCatalog 上新增 `inject_stop_event(stop_event)` 方法，内部遍历 registry 工具注入。封装了实现细节。
- **方案 D:** 不在 Exp 层注入 stop_event，改为通过 ToolExecutionContext.stop_event 在每次 execute 时传递。但这需要工具实现接受 ToolExecutionContext，当前工具的 execute() 签名不接受它。

**Recommendation:** 方案 C 或退一步方案 B。因为 ToolCatalog 已经持有 registry 引用（`self._registry`），只需暴露一个遍历方法。

### Pitfall 5: InlineToolRunner 的 registry.execute() 调用

**What goes wrong:** InlineToolRunner（tool_runner.py L150）直接调用 `self._spec.tool_registry.execute()`。删除 tool_registry 字段后 InlineToolRunner 坏掉。

**Why it matters:** InlineToolRunner 是 Phase 1 过渡用的。Phase 34 后 build_runtime() 已注入 FullToolRunner 作为 spec.tool_runner。但 InlineToolRunner 可能仍有测试引用。

**How to avoid:** InlineToolRunner 要么改走 catalog，要么标记为 deprecated 并确保不再被任何生产路径使用。检查后确认 Phase 34 后 Exp.build_runtime() 注入的是 FullToolRunner，InlineToolRunner 只在其自身测试中使用。可以保留 InlineToolRunner 但让它接受 catalog 而非通过 spec.tool_registry。

### Pitfall 6: 预存在的测试失败

**What goes wrong:** test_capability_policy.py 有 2 个预存在的失败测试（TestEffectLevel::test_deny_external_write_without_external_plane 和 TestEffectLevelWithRealBuiltinMeta::test_builtin_meta_web_tools_have_external_write）。这些不是 Phase 35 引入的，但需要在 Phase 35 的能力策略扩展中一并修复或记录。

**Root cause:** 经检查，DefaultCapabilityPolicy 的 active_planes 检查在 effect_level="external_write" 时应该 deny，但实际返回 allow。可能是 RuntimeTopology.active_planes 默认值包含了 EXTERNAL_SERVICE。

**How to avoid:** 在 Phase 35 扩展 CapabilityPolicy 时，先修复这些预存在的失败，确保基线测试全绿。

## Code Examples

### GuardContext 扩展（spec 6.10 Phase 2 目标态）

```python
# matmaster/types/guards.py
@dataclass
class GuardContext:
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    current_turn: int
    max_turns: int
    recent_calls: list[RecentCall] = field(default_factory=list)
    read_tracker: Any | None = None  # ReadTracker | None, avoid circular import
```

### GuardPipeline 注入 read_tracker

```python
# matmaster/core/guard_pipeline.py
class GuardPipeline:
    def __init__(
        self,
        external_guards: list[Guard] | None = None,
        read_tracker: Any | None = None,  # ReadTracker
    ) -> None:
        self._loop_guard = LoopDetectionGuard()
        self._guards: list[Guard] = [self._loop_guard]
        if external_guards:
            self._guards.extend(external_guards)
        self._recent_calls: deque[RecentCall] = deque(maxlen=LOOP_WINDOW)
        self._read_tracker = read_tracker  # Injected into GuardContext

    def evaluate(self, tool_call, current_turn, max_turns):
        ctx = GuardContext(
            tool_name=tool_call.name,
            tool_args=tool_call.arguments,
            tool_call_id=tool_call.id,
            current_turn=current_turn,
            max_turns=max_turns,
            recent_calls=list(self._recent_calls),
            read_tracker=self._read_tracker,  # Phase 2: inject tracker
        )
        ...
```

### ToolBinding 枚举纠正

```python
# matmaster/types/tool_spec.py
class ToolBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_key: str
    plane: ToolPlane
    resource_claims: tuple[ResourceClaim, ...] = ()
    state_mode: Literal["stateless", "persistent"] = "stateless"
    stop_mode: Literal["cancellable", "best_effort", "non_cancellable"] = "cancellable"
```

### ToolCompiler state_mode/stop_mode 填充

```python
# matmaster/tools/tool_compiler.py (扩展 BUILTIN_META)
# 新增 state_mode, stop_mode 到 BUILTIN_META 元组
# 或新增独立映射表：
BUILTIN_STOP_MODES: dict[str, tuple[str, str]] = {
    # tool_name: (state_mode, stop_mode)
    "execute_bash": ("stateless", "cancellable"),
    "read_file": ("stateless", "cancellable"),
    "write_file": ("stateless", "cancellable"),
    "edit_file": ("stateless", "cancellable"),
    "list_dir": ("stateless", "cancellable"),
    "glob": ("stateless", "cancellable"),
    "grep": ("stateless", "cancellable"),
    "task_create": ("stateless", "cancellable"),
    "task_get": ("stateless", "cancellable"),
    "task_list": ("stateless", "cancellable"),
    "task_update": ("stateless", "cancellable"),
    "task_complete": ("stateless", "cancellable"),
    "mm_web_search": ("stateless", "best_effort"),
    "web_fetch": ("stateless", "best_effort"),
    "spawn": ("persistent", "non_cancellable"),
    "monitor_job": ("persistent", "best_effort"),
}
```

### Scheduler stop_mode 消费

```python
# matmaster/core/tool_scheduler.py 或 tool_runner.py
# FullToolRunner execute_batch 中 Step 1 cancel check 扩展：
if ctx.stop_event is not None and ctx.stop_event.is_set():
    stop_mode = instance.tool_binding.stop_mode
    if stop_mode == "cancellable":
        tr = ToolResult(status="cancelled", content="Run cancelled.")
    elif stop_mode == "best_effort":
        # Still execute but set a flag for cleanup
        tr = ToolResult(status="cancelled", content="Cancellation requested (best-effort).")
    elif stop_mode == "non_cancellable":
        # Do not cancel, let it run
        pass  # Skip cancel check for this tool
```

### ToolRegistry 降级后的签名

```python
# matmaster/tools/tool_registry.py (降级后)
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: dict[str, str] = {}

    def register(self, tool: Tool, *, source: str = "unknown") -> None: ...

    @property
    def all_tools(self) -> list[Tool]: ...

    def __len__(self) -> int: ...
    def __contains__(self, name: str) -> bool: ...

    # DELETED: execute(), get_tool_definitions(), get_tools_by_source()
```

## 受影响文件全景（详细 diff 范围）

### 约束迁移（CMIG-01, CMIG-02）
| File | Change |
|------|--------|
| `matmaster/types/guards.py` | GuardContext 新增 read_tracker 字段 |
| `matmaster/core/guard_pipeline.py` | 新增 ReadBeforeModifyGuard；GuardPipeline.__init__ 接受 read_tracker |
| `matmaster/tools/builtin/write_tool.py` | 删除 _execute() 中 L83-89 的 read-before-modify 检查（保留 input_validator 中的 path-outside-workspace 检查）；改造为 input_validator 处理 path_exists + read check |
| `matmaster/tools/builtin/edit_tool.py` | 删除 _execute() 中 L107-111 的 read-before-modify 检查 |
| `matmaster/tools/builtin/bash_tool.py` | 删除 L21-84 所有安全检查函数和常量。_execute/_execute_async 中的 is_dangerous_bash_command 调用删除 |
| `matmaster/core/capability_policy.py` | 新增 _check_bash_safety() 和 _check_python_safety()，迁入正则模式集合 |
| `matmaster/core/exp.py` | build_runtime() 中 ReadTracker 创建位置调整：注入 GuardPipeline（ReadBeforeModifyGuard） |

### state_mode / stop_mode（CMIG-03）
| File | Change |
|------|--------|
| `matmaster/types/tool_spec.py` | ToolBinding.state_mode/stop_mode 改为 Literal 类型 |
| `matmaster/tools/tool_compiler.py` | compile() 填充 state_mode/stop_mode 从 BUILTIN_STOP_MODES 表 |
| `matmaster/core/tool_runner.py` | FullToolRunner cancel check 根据 stop_mode 调整行为 |
| `matmaster/core/tool_scheduler.py` | 可选：Scheduler 根据 stop_mode 调整 timeout 策略 |

### ToolRegistry 降级 + ContextBuilder（CMIG-04, CMIG-05）
| File | Change |
|------|--------|
| `matmaster/tools/tool_registry.py` | 删除 execute(), get_tool_definitions(), get_tools_by_source() |
| `matmaster/types/runtime.py` | 删除 AgentRuntimeSpec.tool_registry 字段 |
| `matmaster/core/agent.py` | 删除 _run_loop() L323 registry.execute，_run_items() L496-500 registry fallback，_run_items() L685-714 legacy path，_call_llm() L855-860 registry.get_tool_definitions |
| `matmaster/core/tool_runner.py` | InlineToolRunner 清理或标记 deprecated |
| `matmaster/core/exp.py` | build_runtime() 不再注入 registry 到 spec；run()/run_stream() stop_event 注入路径改走 catalog |
| `matmaster/core/context_builder.py` | _build_tools() 改为通用说明；build() 签名调整（tool_registry 参数可选或移除） |
| `matmaster/tools/tool_catalog.py` | build_definitions() 不再委托 registry.get_tool_definitions()，改为自行构造 |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (via uv run) |
| Config file | pyproject.toml |
| Quick run command | `uv run pytest tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py tests/matmaster/tools/test_bash_tool.py tests/matmaster/core/test_guard_pipeline.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_context_builder.py -q` |
| Full suite command | `uv run pytest tests/matmaster/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CMIG-01 | ReadBeforeModifyGuard deny on unread edit_file | unit | `uv run pytest tests/matmaster/core/test_guard_pipeline.py -x -k "read_before_modify"` | Wave 0 |
| CMIG-01 | WriteTool 内部 read-before-modify 检查已删除 | unit | `uv run pytest tests/matmaster/tools/test_write_tool.py -x` | Exists (needs update) |
| CMIG-01 | EditTool 内部 read-before-modify 检查已删除 | unit | `uv run pytest tests/matmaster/tools/test_edit_tool.py -x` | Exists (needs update) |
| CMIG-02 | CapabilityPolicy deny on dangerous bash command | unit | `uv run pytest tests/matmaster/core/test_capability_policy.py -x -k "bash_safety"` | Wave 0 |
| CMIG-02 | BashTool 内部安全检查已删除 | unit | `uv run pytest tests/matmaster/tools/test_bash_tool.py -x` | Exists (needs update) |
| CMIG-03 | ToolBinding Literal types enforced | unit | `uv run pytest tests/matmaster/tools/test_tool_compiler.py -x -k "stop_mode"` | Wave 0 |
| CMIG-03 | Scheduler 根据 stop_mode 调整行为 | unit | `uv run pytest tests/matmaster/core/test_tool_scheduler.py -x -k "stop_mode"` | Wave 0 |
| CMIG-04 | ToolRegistry 无 execute/get_tool_definitions | unit | `uv run pytest tests/matmaster/tools/ -x -k "registry"` | Exists (needs update) |
| CMIG-04 | agent.py legacy 路径已删除 | integration | `uv run pytest tests/matmaster/core/test_tool_runner.py -x` | Exists |
| CMIG-05 | ContextBuilder 不枚举工具 | unit | `uv run pytest tests/matmaster/core/test_context_builder.py -x` | Exists (needs update) |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/test_write_tool.py tests/matmaster/tools/test_edit_tool.py tests/matmaster/tools/test_bash_tool.py tests/matmaster/core/test_guard_pipeline.py tests/matmaster/core/test_capability_policy.py tests/matmaster/core/test_context_builder.py -q`
- **Per wave merge:** `uv run pytest tests/matmaster/ -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/matmaster/core/test_guard_pipeline.py` -- 新增 ReadBeforeModifyGuard 测试用例
- [ ] `tests/matmaster/core/test_capability_policy.py` -- 新增 bash_safety 测试用例 + 修复 2 个预存在失败
- [ ] `tests/matmaster/tools/test_tool_compiler.py` -- 新增 state_mode/stop_mode 填充验证
- [ ] `tests/matmaster/core/test_tool_scheduler.py` -- 新增 stop_mode 消费测试
- [ ] 现有工具测试（write_tool/edit_tool/bash_tool）需要更新，反映安全检查已迁出

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| 工具内部分散安全检查 | 三层约束模型统一管控 | Phase 35 | 安全策略集中管理 |
| ToolRegistry 作为上层消费接口 | ToolCatalog 成为唯一接口 | Phase 35 | ToolRegistry 降为存储层 |
| system prompt 逐行枚举工具 | function calling schema 承载工具信息 | Phase 35 | 消除 prompt/definitions 不一致 |
| ToolBinding state_mode/stop_mode 未类型化 | Literal 枚举 + Scheduler 消费 | Phase 35 | 工具取消语义精确化 |

## Open Questions

1. **write_file 的 path_exists 检查最终归属**
   - What we know: Guard 层无法访问 session，path_exists 是 session 操作
   - What's unclear: 是留在 input_validator（方案 A），还是扩展 GuardContext（方案 B），还是改变行为强制 read-before-write（方案 C）
   - Recommendation: 方案 A（input_validator），符合分层原则且改动最小

2. **_run_loop() 是否应该完全移除**
   - What we know: _run_loop() 是 run() 的实现，run() 被 50+ 测试使用。Phase 34 后 run() 仍走 _run_loop()
   - What's unclear: 是改造 _run_loop() 走 tool_runner，还是让 run() 也委托到 _run_items() 收集模式
   - Recommendation: 改造 _run_loop() 中的工具执行部分走 tool_runner，不整体重写。_run_loop() 中增加 `if spec.tool_runner` 分支，与 _run_items() 同构

3. **InlineToolRunner 的处置**
   - What we know: Phase 34 后不再作为生产默认路径
   - What's unclear: 是否仍有测试依赖
   - Recommendation: 保留但标记 deprecated，清理其对 spec.tool_registry 的依赖

## Project Constraints (from CLAUDE.md)

- 始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 按标准库 -> 第三方 -> 本地分组
- 单文件超过 1000 行必须重构
- 新增工具必须实现 Tool Protocol 并返回 ToolResult
- 层间传递使用 frozen Pydantic model，不可变
- Tool / LLMProvider / Hook / Guard 全部基于 @runtime_checkable Protocol 定义

## Sources

### Primary (HIGH confidence)
- 项目源代码直接审计 -- 所有受影响文件逐行阅读
- `docs/specs/2026-04-02-tool-runtime-v2.md` 6.6, 6.9, 6.10, 8.2 -- 设计规范
- `docs/plans/2026-04-02-v2.2-phase2-advancement.md` Task 4 -- 推进计划
- `.planning/phases/35-toolregistry/35-CONTEXT.md` -- 用户决策

### Secondary (MEDIUM confidence)
- 现有测试文件审计 -- 测试基线验证（test_capability_policy 有 2 个预存在失败）
- Phase 32-34 已实现代码 -- 基础设施验证

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 全部是项目内部代码改造，无外部依赖引入
- Architecture: HIGH -- 三层约束模型和 ToolCatalog 已在 Phase 32-34 实现并验证
- Pitfalls: HIGH -- 通过源代码审计发现 6 个具体风险点（write_file path_exists、python content、_run_loop legacy、stop_event 注入、InlineToolRunner、预存在测试失败）

**Research date:** 2026-04-03
**Valid until:** 2026-05-03 (项目内部代码，30 天有效)
