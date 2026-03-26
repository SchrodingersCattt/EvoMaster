# Phase 8: BuiltinTool 基础设施与核心 Tools - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

建立 matmaster 原生 BuiltinTool 体系（基类 + 构造注入 + Exp 注册机制），交付 BashTool/ListDirTool 和 TaskCreate/TaskGet/TaskList/TaskUpdate/TaskComplete 五个 Task 工具。Agent 通过这些工具可在远程环境执行 shell 命令、浏览目录结构、追踪任务状态。

</domain>

<decisions>
## Implementation Decisions

### ToolContext 设计
- **D-01:** 采用构造注入模式。Tool Protocol `execute(arguments: dict[str, Any]) -> str` 签名保持不变。session/workdir 等依赖在 Exp assemble 阶段通过 BuiltinTool 构造函数注入。Kernel 和 ToolRegistry 不感知 session 概念。
- **D-02:** 此决策解除了 STATE.md 标记的 ToolContext blocker。不引入 ToolContext 参数类型。

### BuiltinTool 基类层次
- **D-03:** 采用统一基类设计。新增 `BuiltinTool` 抽象基类，session/workdir 通过构造函数注入（可选参数）。所有 Phase 8/9/11 的 builtin tool 继承此基类，消除构造样板重复。
- **D-04:** BuiltinTool 基类满足 Tool Protocol（name/description/json_schema/execute），子类只需实现具体逻辑。

### TaskTool 语义范围
- **D-05:** 采用 5 tool 分离设计，对齐 Claude Code 的 Task 套件：TaskCreate/TaskGet/TaskList/TaskUpdate/TaskComplete。每个 tool schema 简单，LLM 不需理解复杂 action 枚举。
- **D-06:** 状态持久化到 workspace 文件（workdir/.tasks.json）。跨 run 持久，文件可审计。Task 生命周期绑定 workspace 而非进程。
- **D-07:** TaskComplete 与 TaskUpdate 是不同语义：update 改状态/描述，complete 标记完成。

### Exp 注册切换
- **D-08:** Phase 8-9 过渡期保持 `ExpConfig.tools.builtin = ["*"]` 不变。`_init_builtin_tools()` 内部先注册 native BuiltinTool，MonitorJobTool 继续走 EvoToolAdapter 路径。
- **D-09:** source 标签区分来源：native tool 用 `"builtin"`，evo adapter tool 用 `"builtin_evo"`。Phase 9 全部 tool 到位后再切换到显式列举并清除 evo adapter 依赖。

### Claude's Discretion
- BuiltinTool 基类的具体字段设计（哪些构造参数、是否用 dataclass 还是普通 class）
- TaskTool 的 tasks.json 文件格式和 schema 细节
- BashTool/ListDirTool 的具体实现方式（通过 session 执行远程命令的机制）
- _init_builtin_tools 内部新旧 tool 注册的具体代码组织

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` -- 项目愿景、核心价值、三层架构、post-v1 变更
- `.planning/REQUIREMENTS.md` -- Phase 8 需求：TOOL-04, TOOL-07, TOOL-09
- `.planning/ROADMAP.md` -- Phase 8 目标、成功标准、依赖关系

### Phase 8 直接依赖的交付物
- `matmaster/tools/tool_registry.py` -- Tool Protocol 定义（name/description/json_schema/execute）和 ToolRegistry 扁平注册表
- `matmaster/tools/evomaster_tool_adapter.py` -- EvoToolAdapter 构造注入模式参考（MonitorJobTool 继续使用）
- `matmaster/core/exp.py` -- Exp.build_runtime() 和 _init_builtin_tools()（Phase 8 改造目标）
- `matmaster/config/exp.py` -- ExpConfig 和 ExpToolsConfig（tools.builtin: list[str]）
- `matmaster/types/context.py` -- PlaygroundContext（workdir/session/cache_area 字段）

### 先前 phase 上下文
- `.planning/phases/03-exp-assembly-layer/03-CONTEXT.md` -- ToolRegistry 设计、Tool Protocol 决策、assemble 框架
- `.planning/phases/05-integration-quality/05-CONTEXT.md` -- Service 层集成、EventRouter、Hook 实现

### 现有 evomaster builtin tools（参考/迁移对象）
- `evomaster/agent/tools/builtin/bash.py` -- BashTool（远程 shell 执行参考）
- `evomaster/agent/tools/builtin/editor.py` -- EditorTool（文件操作参考，Phase 9 使用）
- `evomaster/agent/tools/builtin/monitor_job/_tool.py` -- MonitorJobTool（保留走 EvoToolAdapter）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/tool_registry.py` Tool Protocol + ToolRegistry -- 新 BuiltinTool 必须满足此 Protocol，注册到 ToolRegistry
- `matmaster/tools/evomaster_tool_adapter.py` EvoToolAdapter -- 构造注入模式参考（`__init__(tool, session)` 绑定后 execute 不需 session 参数）
- `matmaster/core/exp.py` Exp._init_builtin_tools() -- Phase 8 改造点，当前从 evomaster 导入 3 个 tool 并用 EvoToolAdapter 包装
- `matmaster/types/context.py` PlaygroundContext -- 提供 workdir (Path)、session (Any)、cache_area (Path)

### Established Patterns
- `@runtime_checkable` Protocol 用于接口定义 -- BuiltinTool 基类应满足 Tool Protocol
- Pydantic frozen model 用于不可变契约 -- 但 BuiltinTool 是有状态的（持有 session 引用），不用 frozen model
- 同步 threading 模型 -- tool.execute() 是同步方法，在 ThreadPoolExecutor 中执行
- 构造注入依赖 -- EvoToolAdapter 已验证此模式在项目中的正确性

### Integration Points
- `matmaster/tools/builtin/` -- Phase 8 新建目录，放置 BuiltinTool 基类和各 tool 实现
- `matmaster/core/exp.py:_init_builtin_tools()` -- 改造为双源注册（native + evo adapter）
- ToolRegistry.register(tool, source="builtin") -- 新 tool 注册入口

</code_context>

<specifics>
## Specific Ideas

- Task 5 tool 对齐 Claude Code 设计：TaskCreate/TaskGet/TaskList/TaskUpdate/TaskComplete
- tasks.json 存入 workdir（而非 cache_area），因为任务追踪是 workspace 的一部分，应跟随 workspace 归档
- MonitorJobTool 是科研场景特有 tool，Phase 8 保留走 EvoToolAdapter，不迁移

</specifics>

<deferred>
## Deferred Ideas

- ExpConfig.tools.builtin 从 wildcard 切换到显式列举 -- Phase 9 全部 tool 到位后执行
- 清除 EvoToolAdapter 对 BashTool/EditorTool 的依赖 -- Phase 9 交付原生 Read/Write/Edit 后
- MonitorJobTool 原生化 -- 评估是否需要，当前保留 evo adapter 路径

</deferred>

---

*Phase: 08-builtintool-tools*
*Context gathered: 2026-03-25*
