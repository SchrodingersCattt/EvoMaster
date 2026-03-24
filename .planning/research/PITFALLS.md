# Domain Pitfalls

**Domain:** AI Agent Framework v1.1 -- Builtin Tool Suite, SubAgent Spawn, Prompt/Description System
**Researched:** 2026-03-24
**Confidence:** HIGH (based on codebase analysis of existing matmaster/ + evomaster/ patterns, existing SubAgent implementation in playground/, Claude Code tool design patterns)

## Critical Pitfalls

Mistakes that cause rewrites or major issues.

### Pitfall 1: Session-Dependent Tool 的生命周期与 Tool Protocol 不匹配

**What goes wrong:**
当前 matmaster Tool Protocol (`tool_registry.py`) 定义了 `execute(arguments: dict) -> str` -- 一个无状态的纯函数签名。但 session-dependent tool (bash, editor, file ops) 本质上是有状态的：EditorTool 维护 `_file_history` 做 undo，BashTool 依赖 session 的 persistent shell state，两者都通过 EvoToolAdapter 绑定 session 引用。

问题出在：如果新的 matmaster 原生 builtin tool 直接实现 Tool Protocol，它需要在某个地方持有 session 引用。两种常见错误路径：

1. **在 `__init__` 时绑定 session** -- 导致 tool 实例与特定 session 耦合。当 session 重连或切换时（DockerSession 的容器可能被回收重建），tool 持有的是过期引用。当前 EvoToolAdapter 就有这个问题，但 v1 中 session 生命周期恰好覆盖整个 run，所以不显现。SubAgent spawn 会打破这个假设：子 agent 可能需要共享父的 session 但用不同的 tool 状态（比如独立的 undo history）。

2. **在 `execute()` 时从全局获取 session** -- 引入隐式依赖，破坏可测试性。Tool Protocol 的设计意图是 execute 只接收 arguments，不依赖外部状态。

**Why it happens:**
EvoToolAdapter 的设计用 `__init__(tool, session)` 把 session 绑死在适配器上，在单 agent 单 run 的场景下完美工作。但 v1.1 引入 SubAgent 后，一个 Exp.run() 内会有多个 agent 执行，共享同一个 workspace 但可能需要独立的 tool 状态。

**Consequences:**
- SubAgent 的 EditorTool undo 历史污染父 agent 的历史
- Session 重连后 tool 执行静默失败（持有已关闭的 session 引用）
- 无法在不创建真实 session 的情况下单元测试 builtin tool

**Prevention:**
1. 引入 `ToolContext` 层：`execute(arguments, context: ToolContext) -> str`。ToolContext 包含 session handle、workdir、tool-scoped state store。每次 agent run 创建新的 ToolContext，SubAgent 创建独立的 ToolContext 但共享底层 session。
2. 或者保持 Protocol 签名不变，但让 tool 内部通过 closure/factory 模式绑定 session。关键是 tool 实例必须区分 session 引用（共享）和 tool 状态（独立）。
3. EditorTool 的 `_file_history` 必须按 agent-run 隔离，不能是 tool 实例级别的状态。用 ToolContext 的 state store 或者每次 run 创建新的 tool 实例。

**Detection:**
- SubAgent 执行 undo_edit 撤销了父 agent 的编辑
- 测试中需要 mock 整个 session 才能测试一个简单的 tool

**Phase to address:** Builtin Tool 设计阶段 -- Tool Protocol 是否需要 ToolContext 参数，必须在实现任何 builtin tool 之前决定。

---

### Pitfall 2: SubAgent Spawn 阻塞父 Agent 的执行循环

**What goes wrong:**
SubAgent 作为 tool_call 的结果返回，意味着在 AgentKernel 的执行循环中，`spec.tool_registry.execute("spawn_agent", args)` 会同步执行整个子 agent 的生命周期（assemble -> build_runtime -> kernel.run -> cleanup）。当前 kernel 的 tool 执行是串行的（agent.py 第 137-182 行 `for tc in response.tool_calls`），子 agent 可能运行数十个 turn，每个 turn 都有 LLM 调用。

后果：
1. 父 agent 的 stop_event 检查只在每个 turn 开始时执行（第 89-90 行），子 agent 运行期间父无法响应取消。
2. SSE 流对前端来说会出现长时间无事件的"空白期"（子 agent 的事件通过哪个 bus 发送？）
3. 如果子 agent 耗尽 max_turns 或出错，异常传播路径不清晰 -- tool execute 返回 error string 还是抛异常？当前 kernel 的异常处理（第 168-171 行）会捕获所有 Exception 并转为 error string，但子 agent 的"正常完成但结果不满意"不应该是 error。

**Why it happens:**
现有的 `step_sub_agent.py` 中的 SubAgent 模式（playground/mat_master 中）是在 solver 层实现的，绕过了 kernel 的 tool 执行流程。v1.1 把 SubAgent spawn 做成 tool_call 触发，复用了 kernel 的执行路径，但 kernel 的 tool 执行模型假设 tool 是"快速返回"的操作（bash 命令、文件读写），不是"嵌套的 agent 运行"。

**Consequences:**
- 用户点击"停止"后子 agent 继续运行直到自然结束
- 前端超时断开 SSE 连接（子 agent 运行期间无心跳）
- Worker 的 session_run_owner TTL（7200s）可能在长子 agent 运行期间过期

**Prevention:**
1. SubAgent spawn tool 必须转发父的 stop_event 到子 kernel。子 kernel 在每个 turn 检查同一个 stop_event，实现级联取消。
2. 子 agent 的事件必须通过父的 MessageBus 发送，使用 source 标识区分。不能创建独立的 bus -- 否则前端收不到子 agent 的流式输出。
3. 考虑子 agent 是否需要流式输出到前端。如果需要，spawn tool 不能阻塞等待完整结果，需要 yield 中间状态。如果不需要（子 agent 只返回最终结果），那 spawn tool 的 execute 就是同步阻塞的，但必须有超时保护和取消传播。
4. Spawn tool 返回值格式设计：成功时返回子 agent 的 final_content；失败时返回结构化的错误信息（reason + partial result），不应该让 kernel 的 generic 异常处理把子 agent 的运行结果吞掉。

**Detection:**
- 用户取消后子 agent 仍在消耗 LLM token
- 前端 SSE 流出现 30 秒以上的空白（无 heartbeat）
- 子 agent 的 tool 调用事件在父 agent 的事件流中缺失

**Phase to address:** SubAgent Spawn 机制设计阶段 -- 必须在 spawn tool 实现之前解决取消传播和事件路由问题。

---

### Pitfall 3: Tool Description 设计不当导致 LLM 误用工具

**What goes wrong:**
当前 EvoToolAdapter 的 description 来自 `tool.params_class.__doc__`（evomaster_tool_adapter.py 第 44 行），也就是 Pydantic model 的 docstring。BashToolParams 的 docstring 有 20+ 行详细说明（bash.py 第 31-52 行），包含 Markdown 格式的使用指南。EditorToolParams 更长，有 25+ 行包含 CRITICAL REQUIREMENTS。

三个常见错误：

1. **Description 过长** -- 每个 tool 的 description 都出现在每次 LLM 请求的 tools 参数中。10 个 tool 每个 500 token 的 description = 5000 token 的固定开销。对于 200 turn 的 agent run，这是 1M token 的纯 description 开销。当前系统只有 3 个 builtin tool，v1.1 扩展到 10+ 个时 token 成本显著上升。

2. **Description 和 system prompt 内容重复** -- ContextBuilder 的 `_build_tools` section（context_builder.py 第 155-164 行）已经列出了每个 tool 的 name + description。如果 tool description 里写了使用指导，system prompt 里也写了使用指导，LLM 收到重复信息，浪费 context window 并可能产生矛盾指令。

3. **Schema 设计歧义导致 LLM 生成错误参数** -- BashToolParams 的 `is_input` 是 `Literal['true', 'false']` 字符串类型而不是 bool（bash.py 第 59-62 行）。这种反直觉的类型选择会导致 LLM 生成 `{"is_input": true}`（JSON boolean）而不是 `{"is_input": "true"}`（JSON string），kernel 的 `_parse_arguments` 能解析但 Pydantic validation 会失败。

**Why it happens:**
EvoMaster 的 tool description 设计来自人写 prompt 的时代，为了给 LLM 足够上下文而写得很详细。但在 function calling 范式下，tool description 是 API schema 的一部分，每次请求都发送，需要精简。

**Consequences:**
- Token 成本随 tool 数量线性增长，200 turn run 可能多消耗 1M+ token
- LLM 看到矛盾的使用指导（description vs system prompt），行为不可预测
- 参数类型不符合 JSON 直觉（string "true" vs boolean true），触发 validation error

**Prevention:**
1. Tool description 控制在 2-3 句话以内（<100 token），只描述 tool 做什么和什么时候用。详细的使用指导放在 system prompt 的 tools section 中（ContextBuilder 已有 tools section）。
2. 建立 description 层级：`json_schema.description`（简短，每次发送）vs `system_prompt_guide`（详细，prompt 层面）。在 Tool Protocol 上新增 `usage_guide` 可选属性，ContextBuilder 读取它来构建 prompt 中的 tools section。
3. 修正 schema 类型：`is_input` 应该是 `bool` 而不是 `Literal['true', 'false']`。所有新 builtin tool 的参数类型必须与 JSON 原生类型对齐。在 `json_schema` 属性中使用 `model_json_schema()` 后要删除 `title`、`default` 等 LLM 不需要的字段（当前 `_remove_unused_schema_info` 已做部分清理）。
4. 建立 tool description review checklist：token count < 100、无 Markdown 格式（function description 不渲染 Markdown）、参数 description 精确描述类型和约束、必选 vs 可选标注清晰。

**Detection:**
- 单次 LLM 请求的 tools 参数超过 3000 token
- LLM 频繁生成错误类型的参数（看 kernel 的 `_parse_arguments` warning 日志）
- Agent 在 tool 选择上犹豫不决（多次调用错误的 tool 再纠正）

**Phase to address:** Tool Description/Schema 设计阶段 -- 在实现任何新 builtin tool 之前制定 description 规范。

---

### Pitfall 4: SubAgent 与父 Agent 的 Workspace 共享导致文件冲突

**What goes wrong:**
v1.1 的 SubAgent 设计要求子 agent 共享父的 workspace（PROJECT.md: "共享 workspace"）。这意味着父子 agent 操作同一目录下的文件。虽然当前设计是串行的（子 agent 在父的 tool_call 中同步运行），但以下场景仍然危险：

1. **子 agent 修改了父 agent 依赖的文件** -- 父 agent 在上一个 turn 读取了 `/workspace/config.yaml` 并计划在下一个 turn 修改它，但中间 spawn 的子 agent 也修改了同一个文件。父 agent 的 str_replace 因为 old_str 不匹配而失败。
2. **子 agent 的 undo 历史与父混淆** -- EditorTool 的 `_file_history` 是按文件路径索引的。如果子 agent 编辑了 `/workspace/main.py` 两次，父 agent 之后对同一文件执行 undo_edit，会撤销子 agent 的修改而不是父自己的。
3. **子 agent 在 workspace 中留下临时文件** -- 子 agent 执行完毕后不做清理，这些文件对父 agent 是可见的噪音。

**Why it happens:**
共享 workspace 是正确的设计选择（子 agent 需要访问父的工作成果），但文件操作的原子性和隔离性没有配套机制。当前的 SubAgentHandle（playground 中的旧实现）通过 `reset_context()` 做了对话上下文隔离，但完全没有处理文件系统层面的隔离。

**Consequences:**
- str_replace 操作因 old_str 不匹配而失败率上升
- undo_edit 撤销错误的修改版本
- workspace 中积累子 agent 的临时文件，影响父 agent 对工作区的理解

**Prevention:**
1. 每个 agent run（包括子 agent）创建独立的 EditorTool 实例。Tool 状态（_file_history）是 per-run 的，不是 per-tool-instance 的。这要求 SubAgent spawn 时创建独立的 ToolRegistry（共享 session 但独立 tool 实例）。
2. SubAgent spawn tool 的参数中提供可选的 `sub_workdir`，子 agent 在 workspace 的子目录中工作。默认共享 workspace 根目录，但允许调用者指定隔离目录。
3. 不做文件锁（太重）。接受"共享 workspace = 最终一致性"的模型。但在 SubAgent 返回时，spawn tool 的 result 中包含子 agent 修改的文件列表（从 tool_call 历史中提取），让父 agent 知道哪些文件被改变了。

**Detection:**
- str_replace 失败率在引入 SubAgent 后显著上升
- undo_edit 产生预期外的文件内容

**Phase to address:** SubAgent Spawn 阶段 -- tool 实例隔离策略必须与 spawn 机制一起设计。

---

### Pitfall 5: Prompt Template 管理与 ExpConfig 的耦合断裂

**What goes wrong:**
当前 ExpConfig 只有一个 `developer_instructions` 字符串字段（exp.py 第 39 行），Exp.build_runtime() 将它传给 ContextBuilder 作为 identity section。v1.1 需要更丰富的 prompt 管理：

1. 不同的 exp 定义需要不同的 system prompt 结构（比如 SubAgent 的 prompt 不需要 skills section，只需要 task-specific instruction）
2. Tool 的 usage guide 需要从 exp 层注入到 prompt 中（哪些 tool 需要详细说明取决于 exp 配置的 tool 集合）
3. SubAgent 的 prompt 需要包含父 agent 的部分上下文（当前任务描述）

常见错误：在 ExpConfig 的 TOML 中硬编码完整的 system prompt 模板。这导致：
- TOML 文件变成 2000+ 行的 prompt 存储（不是 config 文件该做的事）
- Prompt 中引用 tool 名称，但 tool 注册顺序可能变化导致 prompt 与实际 tool 不一致
- 多个 exp 之间 prompt 的公共部分（identity、mode_contract）重复维护

**Why it happens:**
ContextBuilder 当前的 section 架构（identity -> mode_contract -> skills -> tools -> memory -> task）是固定顺序的硬编码。v1.1 需要更灵活的 prompt 组装，但如果不升级 ContextBuilder 就会把灵活性推到 TOML config 里，导致 config 承担了模板引擎的职责。

**Consequences:**
- TOML 文件不可维护（几千行 prompt 模板混在配置中）
- Prompt 和实际 tool 集合不同步
- SubAgent 的 prompt 构建逻辑散落在 spawn tool 和 ContextBuilder 之间

**Prevention:**
1. 将 prompt 模板从 TOML 分离到独立的 `.md` 或 `.txt` 文件。TOML 只引用模板路径。ExpConfig 新增 `prompt_template: str = ""` 字段指向模板文件，`developer_instructions` 保留作为模板中的变量。
2. ContextBuilder 增加 section 开关和自定义 section 能力。ExpConfig 通过 `disabled_sections: list[str]` 和 `extra_sections: dict[str, str]` 控制 prompt 组装。ContextBuilder.build() 已有 `disabled_sections` 参数（context_builder.py 第 56 行），但 Exp 没有传递它。
3. Tool usage guide 由 ContextBuilder 的 `_build_tools` 自动从 Tool Protocol 的 `usage_guide` 属性生成，不在 TOML 中手写。
4. SubAgent 的 prompt 构建通过 spawn tool 在运行时组装：`task_context` 从父 agent 传入，其余 section 由子 Exp 的 ContextBuilder 生成。

**Detection:**
- TOML 文件超过 100 行
- 同一段 prompt 文本出现在多个 TOML 文件中
- 修改 tool 集合后需要手动更新 prompt 中的 tool 说明

**Phase to address:** Prompt/Description 体系设计阶段 -- ContextBuilder 升级和模板分离必须在 SubAgent prompt 构建之前完成。

---

## Moderate Pitfalls

### Pitfall 6: Session-Free Tool 与 Session-Dependent Tool 的注册混淆

**What goes wrong:**
v1.1 区分 session-dependent tool（需要 BaseSession 操作远程环境）和 session-free tool（纯本地计算，如搜索、数学计算）。当前 Exp._init_builtin_tools()（exp.py 第 224-246 行）在 `ctx.session is None` 时跳过所有 builtin tool 的注册。如果 session-free tool 也放在 builtin tools 中，它们会被错误地跳过。

反过来的错误也可能发生：session-free tool 被注册了，但它内部意外地调用了 session 方法（因为 tool context 中有 session 引用），在 session=None 的环境下崩溃。

**Prevention:**
1. 在 ExpConfig 的 tools.builtin 中明确区分 session-dependent 和 session-free tool 集合。不用 `["*"]` 通配所有 tool，改为 `session_tools = ["bash", "editor"]` 和 `free_tools = ["search", "think"]` 两个列表。
2. 或者在 Tool Protocol 上新增 `requires_session: bool` 属性，ToolRegistry 在注册时自动过滤。Exp._init_builtin_tools 根据 ctx.session 是否存在来决定注册哪些 tool。
3. Session-free tool 的 execute 实现中不应持有 session 引用。如果 Tool Protocol 增加了 ToolContext 参数，session-free tool 应该忽略 context.session。

**Detection:**
- DevShell 中（无远程 session）调用 search tool 报 AttributeError
- ctx.session=None 时所有 builtin tool 都未注册（包括不需要 session 的）

**Phase to address:** Builtin Tool 分类设计阶段。

---

### Pitfall 7: AgentRuntimeSpec frozen 与 SubAgent 的 tool_registry 动态性矛盾

**What goes wrong:**
AgentRuntimeSpec 是 `frozen=True` 的 Pydantic model（runtime.py 第 48 行）。Exp.build_runtime() 通过 `spec.model_copy(update={...})` 创建包含 ToolRegistry 的最终 spec。但 SubAgent spawn 时需要创建一个新的 spec（可能有不同的 tool 集合 -- 比如不给子 agent spawn tool 本身，防止无限递归）。

如果 spawn tool 试图修改已有的 spec（比如移除 spawn tool 自身），frozen 会阻止。需要为子 agent 创建完整的新 spec，这意味着 spawn tool 需要访问 Exp 实例来调用 assemble + build_runtime。但 tool 的 execute 签名只接收 arguments dict，不知道 Exp 或 PlaygroundContext。

**Prevention:**
1. Spawn tool 不直接创建子 agent 的 spec。它通过一个预注入的 factory callable 来创建子 agent 的完整 runtime。这个 factory 在 Exp.build_runtime() 阶段构造，闭包捕获了 Exp、ctx 和 bus 引用。
2. 防递归：spawn tool 的 factory 创建子 agent 时，子 agent 的 tool 集合通过 `excluded_tools` 参数排除 spawn tool 本身。或者在 ExpConfig 中为子 agent 定义独立的 tool 集合。
3. 子 agent 的 max_turns 必须小于父 agent 的剩余 turns，防止子 agent 耗尽全局预算。这需要 spawn tool 知道父 kernel 的当前 turn 状态 -- 可以通过 ToolContext 或 factory 的闭包传递。

**Detection:**
- 子 agent 调用 spawn tool 触发无限递归（agent 嵌套 agent 嵌套 agent...）
- spawn tool 的 execute 中出现 Exp 或 PlaygroundContext 的直接 import（违反 tool 层不依赖 core 层的原则）

**Phase to address:** SubAgent Spawn 机制设计阶段。

---

### Pitfall 8: ContextBuilder 的 tools section 与 function calling 的 tools 参数信息冗余

**What goes wrong:**
当前 ContextBuilder._build_tools()（context_builder.py 第 155-164 行）在 system prompt 中列出每个 tool 的 `name: description`。但 LLM API 的 function calling 已经在 `tools` 参数中提供了完整的 tool name、description 和 schema。这造成双重信息：

1. System prompt 中的 tools section（每次 LLM 调用都发送的 system message 一部分）
2. API 的 tools 参数（每次 LLM 调用都发送）

对于 10 个 tool，这可能多出 1000+ token 的冗余。更糟的是，如果 system prompt 中的 tool 描述和 tools 参数中的 description 不一致（因为一个来自 ContextBuilder 的格式化，一个来自 Tool.description 属性），LLM 会收到矛盾信息。

**Prevention:**
1. ContextBuilder 的 tools section 不重复列出 tool description。改为只提供 tool usage strategy -- 什么时候用哪个 tool，tool 之间的配合模式。这是 function calling API 中 tools 参数无法表达的高层指导。
2. 或者完全移除 ContextBuilder 的 tools section（`disabled_sections={"tools"}`），依赖 function calling API 的 tools 参数。只在需要额外使用指导时通过 tool 的 `usage_guide` 属性注入到 prompt 中。
3. 如果保留 tools section，确保它的内容来自 Tool Protocol（而不是独立维护的文本），这样 tool 注册变更会自动反映在 prompt 中。

**Detection:**
- System prompt 中列出的 tool 和 API tools 参数中的 tool 不一致
- LLM 在选择 tool 时引用了 system prompt 中的描述而不是 tools 参数中的

**Phase to address:** Prompt/Description 体系设计阶段。

---

## Minor Pitfalls

### Pitfall 9: Builtin Tool 命名与 EvoMaster Tool 命名冲突

**What goes wrong:**
新 matmaster 原生 builtin tool 和 EvoMaster 的 builtin tool 可能有相同功能但不同名称。当前 EvoMaster 的 bash tool 叫 `execute_bash`，editor 叫 `str_replace_editor`。如果新 matmaster 版本叫 `bash` 和 `editor`，那么在迁移期间两套 tool 共存时，LLM 看到两个功能相同但名称不同的 tool 会困惑。

反过来，如果新 tool 保持相同名称 `execute_bash`，ToolRegistry 的 override 机制（tool_registry.py 第 53-60 行）会静默覆盖旧 tool，但新 tool 的参数 schema 可能不同（比如 `is_input` 从 string 改为 bool），导致 LLM 生成的参数在新 tool 上 validation 失败。

**Prevention:**
1. 在 ToolRegistry 的 override warning 中包含 schema 差异检测。如果同名 tool 的 json_schema 不同，升级为 error 而不是 warning。
2. 迁移期间不共存两套同功能 tool。一个 exp 要么用 EvoToolAdapter 包装的旧 tool，要么用新的 matmaster builtin tool，不混用。通过 ExpConfig 的 tools 配置控制。
3. 新 builtin tool 的命名和参数 schema 尽量与 EvoMaster 保持一致（向后兼容），只在必要时改名（比如 `is_input: str` -> `is_input: bool` 这种类型修正需要改名或提供 compatibility parsing）。

**Phase to address:** Builtin Tool 实现阶段。

---

### Pitfall 10: SubAgent Result 的 Token 膨胀

**What goes wrong:**
SubAgent 的完整执行结果作为 tool_call result 返回给父 agent。如果子 agent 运行了 20 个 turn 产生了大量输出，spawn tool 的 result 可能有数千 token。这个 result 被追加到父 agent 的 messages 中（作为 ToolMessage），每个后续 LLM 调用都会发送它。

对于 context compaction 来说，ToolMessage 的内容通常不会被压缩（compactor 压缩的是对话历史，不是单条 tool result）。一个膨胀的 spawn result 会长期占据 context window。

**Prevention:**
1. Spawn tool 在返回前截断或摘要子 agent 的 result。设定最大 token 数（比如 2000 token），超过时用 LLM 摘要。
2. 子 agent 的 final_content 就是 spawn tool 的 result，不包含完整的 tool 调用历史。子 agent 的详细执行过程通过 events 发送到 bus（用于前端展示和审计），但不放入父 agent 的 context。
3. Spawn tool 的参数中提供 `result_format: Literal["full", "summary", "structured"]`，让 LLM 选择子 agent 结果的返回格式。

**Phase to address:** SubAgent Spawn 实现阶段。

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Builtin Tool Protocol 设计 | Tool Protocol 签名是否增加 ToolContext 参数 -- 这是破坏性变更，影响所有现有 tool 和测试 | 先评估影响范围（当前只有 EvoToolAdapter 实现了 Tool Protocol），如果影响小就直接加；如果要保持兼容就用 optional parameter |
| Session-Dependent Tool 实现 | 照搬 EvoMaster 的 BashTool/EditorTool 但忘记处理 session 异常（网络断开、容器重启） | 新 tool 的 execute 必须处理 session 不可用的情况，返回明确的 error message 而不是 traceback |
| Session-Free Tool 实现 | 以为"不依赖 session"就可以随意实现，但没考虑工作目录和权限约束 | Session-free tool 仍然需要知道 workdir（从 PlaygroundContext.workdir 获取），它只是不通过 session 执行远程命令 |
| SubAgent Spawn 机制 | 没有处理子 agent 的事件路由，前端看不到子 agent 的执行过程 | 子 agent 的 EventEmitterHook 使用父的 MessageBus + 带前缀的 source（如 `sub:step-1`） |
| SubAgent 递归防护 | 只靠"不给子 agent spawn tool"防递归，但子 agent 可能通过 bash tool 调用 API 间接触发 | spawn 深度计数器在 ToolContext 中传递，超过阈值拒绝 spawn |
| Prompt Template 管理 | 过度设计模板引擎（支持条件渲染、变量插值、继承） | 用 Python 的 string.Template 或简单的 f-string 就够了。不要引入 Jinja2 -- prompt 不是 HTML |
| Tool Description 规范 | 每个 tool 开发者自己写 description，质量参差不齐 | 建立 description review checklist 和示例库，CI 检查 description token count |
| ContextBuilder 升级 | 增加太多 section 导致 prompt 结构难以理解和调试 | 保持 6-8 个 section 的上限，新需求通过扩展已有 section 而不是增加新 section |

## Sources

- Codebase analysis: `matmaster/core/agent.py` (kernel execution loop), `matmaster/tools/tool_registry.py` (Tool Protocol), `matmaster/tools/evomaster_tool_adapter.py` (adapter pattern), `matmaster/core/exp.py` (assembly lifecycle), `matmaster/core/context_builder.py` (prompt construction), `matmaster/config/exp.py` (ExpConfig)
- EvoMaster tool implementations: `evomaster/agent/tools/builtin/bash.py`, `evomaster/agent/tools/builtin/editor.py`, `evomaster/agent/tools/base.py` (BaseTool + ToolRegistry)
- Session interface: `evomaster/agent/session/base.py` (BaseSession abstraction)
- Existing SubAgent pattern: `playground/mat_master/core/solvers/step_sub_agent.py` (SubAgentHandle + StepSubAgentFactory)
- Claude Code tool design: tool description brevity principles, session-dependent vs session-free tool split
- OpenAI function calling documentation: tool description and parameter schema best practices

---
*Pitfalls research for: MatMaster v1.1 (Builtin Tools + SubAgent Spawn + Prompt/Description)*
*Researched: 2026-03-24*
