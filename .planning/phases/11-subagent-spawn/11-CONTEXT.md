# Phase 11: SubAgent Spawn 机制 - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

实现 SubAgent spawn 机制：父 agent 通过 SubAgentTool 的 tool_call 触发子 agent 执行特定任务。子 agent 通过独立 ExpConfig（TOML 定义）配置独立的 tool 集和 system prompt，共享父 agent 的 PlaygroundContext（workspace/session）。支持递归深度保护（depth=1）、stop_event 级联取消、事件通过父 MessageBus 路由到前端。

</domain>

<decisions>
## Implementation Decisions

### SubAgent Tool 参数设计
- **D-01:** 动态 exp 选择方案。SubAgentTool schema 为 `execute({"exp_name": "explore", "task": "..."})` 。LLM 通过 exp_name 参数指定子 agent 类型（如 explore/research），task 参数传递任务描述。不引入额外 context 参数，父 agent 将上下文写入 task 文本。后续对齐 Claude Code 的多种内置 subagent 类型（Agent tool with subagent_type）。

### 子 agent 事件路由模式
- **D-02:** 共享父 MessageBus + source 前缀区分来源。子 agent 的 EventEmitterHook 构造时传入带前缀的 source（如 `MatMaster:explore`），直接 emit 到父 MessageBus。`normalize_event_source` 扩展规则以保留 `MatMaster:*` 前缀格式。`chat_history.py` 中 source 判断需同步更新以兼容前缀格式。前端可通过 source 前缀区分父/子 agent 事件渲染。

### 子 exp 配置策略
- **D-03:** 独立 TOML 文件。每个子 exp 类型一个 TOML（如 `matmaster/exps/explore.toml`、`matmaster/exps/research.toml`），独立定义 tools.builtin 列表和 developer_instructions（PRMT-03）。通过现有 `load_exp_config(name)` 加载，零改动加载链路。子 exp 的 system prompt 针对子任务场景设计，写入 TOML 的 developer_instructions 字段，沿用 ContextBuilder 的 identity section。

### 递归保护
- **D-04:** 双层保护机制。Schema 层：子 exp TOML 的 `tools.builtin` 列表不包含 `sub_agent`，LLM 完全不可见 SubAgentTool。运行时层：SubAgentTool 构造注入 `spawn_fn: Callable | None`，子 agent 构建时传入 `spawn_fn=None`，execute 时检查后返回错误字符串作为兜底。与 `BuiltinTool._require_session()` 守卫模式一致。

### Claude's Discretion
- spawn_fn 闭包的具体签名和返回值设计（在 spawn_fn 注入解耦原则下自由实现）
- SubAgentTool 的 description/json_schema 精细化（遵循 Phase 10 确立的 Claude Code 质量标准）
- stop_event 级联传播的具体实现（共享 Event 对象 vs 子 Event 联动）
- `normalize_event_source` 的前缀解析规则细节
- `chat_history.py` source 判断的兼容改法
- 子 exp TOML 的 max_turns 和 guards 配置值
- Phase 11 交付哪些子 exp TOML 文件（最少一个用于验证机制）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` -- 项目愿景、核心价值、三层架构、post-v1 变更、v1.1 milestone 目标
- `.planning/REQUIREMENTS.md` -- Phase 11 需求：SUBA-01~06, PRMT-03
- `.planning/ROADMAP.md` -- Phase 11 目标、成功标准、依赖关系

### 直接前驱 phase 上下文
- `.planning/phases/08-builtintool-tools/08-CONTEXT.md` -- BuiltinTool 基类设计、构造注入决策、source 标签、Exp 注册机制
- `.planning/phases/09-tools/09-CONTEXT.md` -- 文件操作 tool 设计、ReadTracker 共享状态注入模式、ExpConfig 显式列举切换
- `.planning/phases/10-tool-description-system-prompt/10-CONTEXT.md` -- Claude Code 质量级别 description 标准、developer_instructions 5 维度设计

### Phase 11 直接依赖的代码
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC（ClassVar + _execute + _require_session 守卫模式）
- `matmaster/core/exp.py` -- Exp.build_runtime() + _init_builtin_tools()（SubAgentTool 注册点 + spawn_fn 闭包创建点）
- `matmaster/core/agent.py` -- AgentKernel.run(spec, task, history, stop_event)（子 agent 执行入口）
- `matmaster/core/bus.py` -- MessageBus（共享 bus 传递给子 agent 的 EventEmitterHook）
- `matmaster/types/runtime.py` -- AgentRuntimeSpec, AgentRuntime, KernelResult, KernelRunResult
- `matmaster/types/events.py` -- BusEvent, RunResultEvent（source 字段）
- `matmaster/config/exp.py` -- ExpConfig + ExpToolsConfig（tools.builtin 白名单）
- `matmaster/config/loader.py` -- load_exp_config(name)（TOML 加载链路）
- `matmaster/exps/direct.toml` -- 父 exp 定义参考（子 exp TOML 结构参照）
- `matmaster/core/hooks.py` -- EventEmitterHook（source 参数传递）
- `matmaster/integration/event_payloads.py` -- normalize_event_source（前缀规则扩展点）
- `src/services/chat_history.py` -- source == 'MatMaster' 判断（兼容改造点）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/builtin/base.py` BuiltinTool ABC -- SubAgentTool 继承此基类，spawn_fn 通过构造注入
- `matmaster/core/exp.py` Exp.build_runtime() -- spawn_fn 闭包在此创建，捕获 ctx/bus/config 创建子 Exp 实例
- `matmaster/config/loader.py` load_exp_config(name) -- 子 exp TOML 加载零改动可用
- `matmaster/core/hooks.py` EventEmitterHook -- 子 agent 构造时传入父 bus + source 前缀
- `matmaster/core/agent.py` AgentKernel -- 子 agent 复用同一 Kernel 类

### Established Patterns
- BuiltinTool 构造注入: session/workdir/tracker 在 Exp assemble 时注入，Kernel 不感知
- Tool Protocol: execute(arguments: dict[str, Any]) -> str，同步执行
- _require_session() 守卫模式: None 检查 + RuntimeError，SubAgentTool 的 spawn_fn=None 守卫可复用此模式
- ExpConfig TOML 加载: load_exp_config(name) → tomllib → ExpConfig.model_validate
- EventEmitterHook(bus, source=exp_name): source 字段标识事件来源
- source 标签: "builtin" / "builtin_evo" / "mcp" / "skill" 区分 tool 来源

### Integration Points
- `matmaster/tools/builtin/sub_agent_tool.py` -- 新增 SubAgentTool 类
- `matmaster/exps/` -- 新增子 exp TOML 文件
- `matmaster/core/exp.py:_init_builtin_tools()` -- 新增 SubAgentTool 注册 + spawn_fn 闭包创建
- `matmaster/core/exp.py:build_runtime()` -- spawn_fn 需要访问 ctx/bus 以构建子 agent 运行时
- `matmaster/integration/event_payloads.py:normalize_event_source()` -- 扩展前缀解析规则
- `src/services/chat_history.py` -- source 判断兼容 `MatMaster:*` 前缀
- `matmaster/exps/direct.toml` -- tools.builtin 列表新增 `sub_agent`

</code_context>

<specifics>
## Specific Ideas

- SubAgentTool 的 exp_name 参数对齐 Claude Code 的 Agent tool subagent_type 设计理念：LLM 选择子 agent 类型来执行特定任务
- 后续子 exp 规划：explore（代码探索）、research（文献调研）等，Phase 11 至少交付一个子 exp TOML 用于验证完整机制
- spawn_fn 闭包注入解耦 SubAgentTool 与 Exp 层：tool 不直接依赖 Exp 类，通过闭包间接调用
- 子 agent 同步执行：spawn 在父 agent 的 tool execution 线程中同步完成，结果作为 tool result 字符串返回
- source 前缀方案 `MatMaster:explore` 保留了前端渲染区分能力，且与现有 SSE 传输兼容

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 11-subagent-spawn*
*Context gathered: 2026-03-25*
