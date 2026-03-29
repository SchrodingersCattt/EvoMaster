# Phase 14: Tool 系统异步化 - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

将 12 个 BuiltinTool + ToolRegistry + LazyMCPTool + SkillTool 的 execute 方法全部改为 async。session-dependent 的同步 evomaster API 调用通过 asyncio.to_thread 桥接。SpawnTool 的 spawn_fn 保持 sync（延后到 Phase 18 随 Exp 一起改造）。

本阶段不改 AgentKernel、Exp 生命周期、Hook 系统。Kernel 层通过已有的 _sync_call_async 桥接调用 async ToolRegistry.execute()。

</domain>

<decisions>
## Implementation Decisions

### BashTool 执行模式
- **D-01:** BashTool 只做 to_thread 包装 session.exec_bash()，不引入 session-free 模式（asyncio.create_subprocess_exec）。当前无 session-free 使用场景，不引入新能力。Success Criteria #2 中 session-free 条件自然不触发。

### to_thread 包装粒度
- **D-02:** 在 BuiltinTool.execute() 模板方法中统一用 `await asyncio.to_thread(self._execute, arguments)` 包装。所有子类 _execute() 保持 sync def 不变，12 个子类零改动（仅需确认 _execute 签名为 sync）。
- **D-03:** BuiltinTool ABC 的 _execute() 签名从 async def 回退为 sync def（Phase 12 改的 async 签名不适用于 to_thread 包装策略）。execute() 改为 async def。
- **D-04:** 异常处理保留在 execute() 模板方法中（try/except 包裹 to_thread 调用），与现有行为一致。

### 非 BuiltinTool 适配范围
- **D-05:** LazyMCPTool 和 SkillTool 在 Phase 14 一并改为 async def execute()。内部同步调用用 asyncio.to_thread 包装。保证 ToolRegistry.execute() 可统一 await 所有 tool。
- **D-06:** ToolRegistry.execute() 改为 async def，内部 await tool.execute()。

### SpawnTool 过渡策略
- **D-07:** spawn_fn 保持 sync Callable 类型不变。SpawnTool._execute() 保持 sync def（调用 sync spawn_fn）。通过 D-02 的 to_thread 包装，整个 spawn 过程（含子 agent 执行）在线程池运行，不阻塞 event loop。
- **D-08:** TOOL-05（spawn_fn async callable）实质延后到 Phase 18，与 Exp async 化一起处理。Phase 14 只需确保 SpawnTool 满足 async Tool Protocol（通过 execute() 模板方法的 to_thread 包装自动满足）。

### Kernel 桥接
- **D-09:** Kernel 调用 ToolRegistry.execute() 的位置（agent.py:247）改为 `_sync_call_async(registry.execute(...), loop)`，复用 Phase 13 建立的桥接模式。Phase 17 Kernel async 化时移除桥接。

### Claude's Discretion
- LazyMCPTool / SkillTool 内部 to_thread 的具体包装方式
- ToolRegistry.execute() 的 normalize_tool_result 调用是否需要适配 async 返回值
- task tools（5 个）的 _execute 签名确认（操作本地文件，通过 to_thread 包装自动不阻塞）
- 测试迁移的具体范围和 async mock 策略

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- TOOL-01~TOOL-05 requirements 定义（TOOL-05 延后到 Phase 18）
- `.planning/ROADMAP.md` -- Phase 14 目标、依赖、成功标准
- `.planning/PROJECT.md` -- 核心决策（Protocol hard cut, 自底向上迁移）

### 前置阶段 Context
- `.planning/phases/12-protocol/12-CONTEXT.md` -- Protocol async 签名决策（D-05 关于 _execute async 签名，本阶段回退）
- `.planning/phases/13-llm-provider/13-CONTEXT.md` -- Provider async 实现 + Kernel 桥接模式（_sync_call_async）

### BuiltinTool 基础设施（改造核心）
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC（execute + _execute 模板方法）
- `matmaster/tools/tool_registry.py` -- Tool Protocol + ToolRegistry（execute 调度）

### 12 个 BuiltinTool 实现
- `matmaster/tools/builtin/bash_tool.py` -- BashTool（session.exec_bash）
- `matmaster/tools/builtin/read_tool.py` -- ReadTool（session.read_file / session.is_file）
- `matmaster/tools/builtin/write_tool.py` -- WriteTool（session.write_file）
- `matmaster/tools/builtin/edit_tool.py` -- EditTool（session.read_file / session.write_file）
- `matmaster/tools/builtin/glob_tool.py` -- GlobTool（session.glob）
- `matmaster/tools/builtin/grep_tool.py` -- GrepTool（session.exec_bash）
- `matmaster/tools/builtin/listdir_tool.py` -- ListDirTool
- `matmaster/tools/builtin/spawn_tool.py` -- SpawnTool（spawn_fn 保持 sync）
- `matmaster/tools/builtin/task/task_create.py` -- TaskCreateTool
- `matmaster/tools/builtin/task/task_list.py` -- TaskListTool
- `matmaster/tools/builtin/task/task_get.py` -- TaskGetTool
- `matmaster/tools/builtin/task/task_update.py` -- TaskUpdateTool
- `matmaster/tools/builtin/task/task_complete.py` -- TaskCompleteTool

### 非 BuiltinTool（一并改造）
- `matmaster/tools/lazy_mcp.py` -- LazyMCPTool + LazyMCPConnector
- `matmaster/tools/skill_tool.py` -- SkillTool

### Kernel 桥接（了解但主体不改）
- `matmaster/core/agent.py` -- AgentKernel._sync_call_async（:70）+ tool dispatch（:247）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_sync_call_async` / `_sync_iterate_async` 桥接函数已在 agent.py 中建立（Phase 13）
- Phase 12 建立的 pytest-asyncio 基础设施 + async mock factories（tests/conftest.py）
- validate_async_protocol() helper 可验证改造后的 Tool 实现

### Established Patterns
- BuiltinTool 使用 Template Method 模式：execute()（公共 + 异常处理）调用 _execute()（具体逻辑）
- Tool Protocol 使用 @runtime_checkable 装饰器
- 所有 BuiltinTool 通过 session.* 调用执行 I/O（session 是同步 evomaster API）
- ToolResult(status, content, info) 统一结果模型 + normalize_tool_result 归一化

### Integration Points
- ToolRegistry.execute() 被 AgentKernel.run() 在 tool dispatch 循环中调用（agent.py:247）
- BuiltinTool.execute() 是 Tool Protocol 的实现入口
- LazyMCPTool.execute() 通过 MCP Protocol 调用远程 tool
- SkillTool.execute() 调度 skill 操作（get_info / get_reference / run_script）

</code_context>

<specifics>
## Specific Ideas

- BuiltinTool ABC _execute 签名从 async def 回退为 sync def 是 D-02/D-03 的自然结果，不是设计冲突。Phase 12 预设了 _execute 各自 async 的方案，Phase 14 选择了更简洁的 to_thread 整体包装方案。
- TOOL-05（spawn_fn async）实质与 Phase 18 EXPL-04（SubAgent spawn async 链路）是同一件事，分开做没意义。Phase 14 只需保证 SpawnTool 满足 async Tool Protocol。
- 12 个子类 _execute 的改动量为零——这是选择 to_thread 整体包装策略的核心优势。

</specifics>

<deferred>
## Deferred Ideas

- BashTool session-free 模式（asyncio.create_subprocess_exec）-- 当前无使用场景，未来 DevShell 本地调试时可能需要
- TOOL-05 spawn_fn async callable -- 延后到 Phase 18 与 Exp async 化一起处理

</deferred>

---

*Phase: 14-tool*
*Context gathered: 2026-03-27*
