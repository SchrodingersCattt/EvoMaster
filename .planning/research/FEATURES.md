# Feature Landscape: v1.1 内置 Tool 套件 + SubAgent + Prompt 体系

**Domain:** AI Agent 内置工具套件与 SubAgent 生成机制
**Researched:** 2026-03-24
**Confidence:** HIGH (基于 Claude Code 系统提示分析 + OpenAI function calling 最佳实践 + 现有代码库分析)

本次研究聚焦 v1.1 新增特性：matmaster 原生内置 tool 套件、SubAgent spawn 机制、prompt/description 精细化设计。不重复 v1 已完成的基础设施（AgentKernel、ToolRegistry、ContextBuilder 等），只关注在现有骨架上构建的新能力。

---

## Table Stakes

v1.1 必须交付的能力。缺失任何一项都意味着 agent 无法独立执行任务。

### 1. 文件读取工具 (Read)

| 维度 | 描述 |
|------|------|
| **Why Expected** | agent 理解代码/数据的基础操作。Claude Code 将 Read 作为最高频工具，所有分析/修改前置操作都依赖它 |
| **Complexity** | LOW |
| **Session 依赖** | YES -- 通过 `BaseSession.read_file()` / `BaseSession.download()` |
| **参数设计** | `file_path` (必需, 绝对路径), `offset` (可选, 起始行号), `limit` (可选, 读取行数) |
| **行为规范** | 默认读取前 2000 行; 输出 cat -n 格式带行号; 超长行截断; 二进制文件返回类型提示而非内容 |
| **现有基础** | evomaster EditorTool 的 `view` 命令已有类似功能，但耦合在多命令工具中。需要拆分为独立 Read tool |
| **关键设计点** | 与 Edit/Write 的 read-before-modify 协议: Read 记录已读文件集合，Write/Edit 拒绝操作未读文件 |

### 2. 文件写入工具 (Write)

| 维度 | 描述 |
|------|------|
| **Why Expected** | agent 创建新文件的基本能力。区别于 Edit（修改已有文件），Write 用于全新文件创建或完整覆盖 |
| **Complexity** | LOW |
| **Session 依赖** | YES -- 通过 `BaseSession.write_file()` |
| **参数设计** | `file_path` (必需, 绝对路径), `content` (必需, 完整文件内容) |
| **行为规范** | 覆盖已有文件; 对已有文件必须先 Read 才能 Write (防止盲写); 自动创建中间目录 |
| **现有基础** | evomaster EditorTool 的 `create` 命令 |
| **关键设计点** | Write 的 description 中应明确告知 LLM: 优先用 Edit 修改已有文件，Write 只用于新建或完整重写 |

### 3. 文件编辑工具 (Edit)

| 维度 | 描述 |
|------|------|
| **Why Expected** | agent 修改代码/配置的核心能力。str_replace 模式是 2025-2026 年 coding agent 的标准做法 (Claude Code, Aider, OpenHands 均采用) |
| **Complexity** | MEDIUM |
| **Session 依赖** | YES -- 通过 `BaseSession.read_file()` + `BaseSession.write_file()` |
| **参数设计** | `file_path` (必需), `old_string` (必需, 要替换的精确文本), `new_string` (必需, 替换后文本) |
| **行为规范** | old_string 必须在文件中唯一匹配; 匹配失败返回有意义的错误 (包括最近似匹配位置); 替换后返回上下文片段供 LLM 验证; 必须先 Read 才能 Edit |
| **现有基础** | evomaster EditorTool 的 `str_replace` 命令。现有实现质量较高，包含 strip 重试、多匹配检测、undo 历史 |
| **关键设计点** | 从 evomaster EditorTool 提取 str_replace 逻辑，去掉 view/create/insert/undo_edit 等附属命令(这些功能由 Read/Write 覆盖) |

### 4. 命令执行工具 (Bash)

| 维度 | 描述 |
|------|------|
| **Why Expected** | agent 与环境交互的通用后门。安装依赖、运行测试、编译代码、检查进程状态等操作都通过 Bash |
| **Complexity** | LOW (适配已有 BashTool) |
| **Session 依赖** | YES -- 通过 `BaseSession.exec_bash()` |
| **参数设计** | `command` (必需), `timeout` (可选, 秒), `description` (可选, 5-10 词简述) |
| **行为规范** | 持久 shell session (环境变量/工作目录保持); 输出截断 (建议 30000 字符上限); 危险命令拦截; 返回 exit_code + stdout + working_dir |
| **现有基础** | evomaster BashTool 已有完整实现，包含危险命令检测、代理清除、超时处理 |
| **关键设计点** | description 中应引导 LLM: 不要用 bash grep/find/cat，改用专用 Grep/Glob/Read 工具 |

### 5. 文件搜索工具 (Glob)

| 维度 | 描述 |
|------|------|
| **Why Expected** | agent 在大型项目中定位文件的高效手段。比 `find` 命令更快且结果按修改时间排序 |
| **Complexity** | LOW |
| **Session 依赖** | YES -- 通过 `BaseSession.exec_bash()` 执行 glob 展开 |
| **参数设计** | `pattern` (必需, glob 模式如 `**/*.py`), `path` (可选, 搜索根目录, 默认 workspace) |
| **行为规范** | 结果按修改时间排序 (最近修改优先); 支持 `*`, `**`, `?`, `{a,b}` 模式; 排除隐藏文件和常见忽略目录 (.git, __pycache__, node_modules) |
| **现有基础** | 无直接对应。当前 agent 通过 bash `find` 命令实现，效率低且无排序 |
| **关键设计点** | session-dependent 实现: 通过 exec_bash 执行 `find + stat + sort` 组合命令; session-free 实现 (本地 DevShell): 直接用 Python pathlib.glob |

### 6. 内容搜索工具 (Grep)

| 维度 | 描述 |
|------|------|
| **Why Expected** | agent 在文件内容中搜索模式的核心能力。比 bash grep 更结构化: 支持多种输出模式、上下文控制 |
| **Complexity** | LOW-MEDIUM |
| **Session 依赖** | YES -- 通过 `BaseSession.exec_bash()` 执行 grep/rg |
| **参数设计** | `pattern` (必需, 正则表达式), `path` (可选, 搜索路径), `include` (可选, 文件过滤 glob), `output_mode` (可选, files_with_matches/content/count), `context_lines` (可选) |
| **行为规范** | 默认返回匹配文件路径列表 (files_with_matches 模式); content 模式返回匹配行及上下文; 结果数量有上限防止 token 爆炸 |
| **现有基础** | 无直接对应。当前 agent 通过 bash grep 实现 |
| **关键设计点** | 远程环境可能没有 ripgrep，需要降级到 grep -rn; description 中应说明正则语法 |

### 7. Tool Description / JSON Schema 精细化

| 维度 | 描述 |
|------|------|
| **Why Expected** | tool description 的质量直接决定 LLM 调用工具的准确率。Gorilla 研究实证: description 精度与调用准确率强正相关 |
| **Complexity** | MEDIUM (设计密集型，非代码密集型) |
| **Session 依赖** | N/A |
| **行为规范** | 每个 tool 的 description 必须包含: (1) 功能说明 (2) 何时使用 (3) 何时不使用 (4) 参数行为说明 (5) 返回格式说明 |
| **现有基础** | evomaster 工具的 description 来自 Pydantic model docstring (BashToolParams/EditorToolParams)，质量中等 |
| **关键设计点** | Claude Code 的实践: 将大量行为规范嵌入 description 本身而非 system prompt; 用 "CRITICAL"/"IMPORTANT" 标记关键规则; 用 "Best Practices" 列表引导 LLM 行为 |

---

## Differentiators

不是立即必须的，但能显著提升 agent 能力上限的特性。

### 8. SubAgent Spawn 机制

| 维度 | 描述 |
|------|------|
| **Value Proposition** | 允许 agent 将子任务委派给独立 agent 执行，实现探索/执行分离、并行研究、上下文隔离。Claude Code 的 Task tool 证明了这一模式的有效性 |
| **Complexity** | HIGH |
| **Session 依赖** | 间接 -- SubAgent 共享父 agent 的 workspace (同一 session) |
| **实现机制** | LLM 发出 tool_call → SubAgent tool 接收 → 通过 Exp 创建子 AgentRuntimeSpec → 子 AgentKernel.run() → 结果作为 ToolMessage 返回 |
| **参数设计** | `description` (必需, 3-5 词任务摘要), `prompt` (必需, 详细任务指令), `tools` (可选, 限制子 agent 可用工具), `max_turns` (可选) |
| **行为规范** | 子 agent 独立上下文窗口; 无状态 (每次调用全新实例); 子 agent 不能 spawn 子子 agent (防止无限递归); 共享 workspace 但独立消息历史 |
| **现有基础** | 无。但 Exp + AgentKernel 的分层设计天然支持: Exp 可以创建多个 AgentRuntimeSpec 实例 |
| **关键设计点** | SubAgent 是一个注册在 ToolRegistry 中的 tool，其 execute() 内部创建子 Exp → assemble → kernel.run()。这保持了 kernel 的纯净性 -- kernel 不知道 SubAgent 的存在 |
| **依赖** | 依赖 Read/Write/Edit/Bash/Glob/Grep 工具套件已就位 (子 agent 需要可用工具) |

### 9. System Prompt 模板化管理

| 维度 | 描述 |
|------|------|
| **Value Proposition** | 当前 ContextBuilder 的 section 内容硬编码或来自简单 config string。模板化允许: TOML 中定义 prompt 模板 → 运行时变量替换 → 组装为 system prompt |
| **Complexity** | MEDIUM |
| **现有基础** | ContextBuilder 已有 section 分区机制 (identity/mode_contract/skills/tools/memory/task)。ExpConfig 有 `developer_instructions` 字段 |
| **关键设计点** | 扩展 ExpConfig 的 prompt 配置: 支持 Jinja2 或简单 `{variable}` 替换; 模板存储在 TOML 中或独立 .md 文件; 运行时注入 workdir、session_type、tool 列表等变量 |
| **依赖** | 依赖 tool 套件完成 (ContextBuilder._build_tools 需要知道最终 tool 列表) |

### 10. SubAgent 工具限制 (Tool Subset)

| 维度 | 描述 |
|------|------|
| **Value Proposition** | 不同类型的子 agent 应该有不同的工具访问权限。探索型子 agent 只需 Read/Glob/Grep (只读); 执行型子 agent 需要全部工具 |
| **Complexity** | LOW (在 SubAgent spawn 机制之上) |
| **现有基础** | ToolRegistry 已支持 source-based 过滤 (`get_tools_by_source`)。可以扩展为 name-based 过滤 |
| **关键设计点** | SubAgent tool 的 `tools` 参数接受工具名列表; spawn 时从父 registry 过滤创建子 registry |

### 11. Read-Before-Modify 安全协议

| 维度 | 描述 |
|------|------|
| **Value Proposition** | 防止 LLM 盲目覆盖文件。Claude Code 强制: Write/Edit 工具会拒绝操作当前 session 中未通过 Read 读取过的文件 |
| **Complexity** | LOW |
| **现有基础** | 无。evomaster EditorTool 没有这个限制 |
| **关键设计点** | 在 tool 套件层维护 `_read_files: set[str]` 状态; Read 执行时记录路径; Write/Edit 执行前检查路径是否在集合中; 新建文件 (path 不存在) 豁免检查 |

---

## Anti-Features

明确不在 v1.1 中构建的能力。

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **MultiEdit (批量编辑)** | 增加 JSON schema 复杂度 (嵌套数组结构导致 LLM 调用出错率上升)。Claude Code 有 MultiEdit 但很少被 LLM 主动使用 | 让 LLM 多次调用 Edit tool。单次编辑的可靠性远高于批量编辑 |
| **NotebookRead/NotebookEdit** | Jupyter notebook 是特殊格式，需要额外的 JSON cell 解析逻辑。当前科研场景中 notebook 不是主要交互方式 | 通过 Read 以文本形式读取 .ipynb (JSON 格式); 通过 Write 覆盖写入; 未来如有需求再单独实现 |
| **WebFetch/WebSearch** | 远程环境 (Docker/SSH) 可能无外网访问; 引入 HTTP 客户端增加依赖; 不是科研 agent 的核心路径 | 科研信息检索通过 MCP tools 实现 (如 AISSQ 文献搜索); 通用 web 访问通过 Bash + curl |
| **TodoRead/TodoWrite** | 任务追踪状态管理增加 tool 数量和 token 消耗; LLM 维护 TODO 列表的可靠性不高 | system prompt 中引导 LLM 自行管理执行计划; 通过 Write 工具写 plan 文件实现类似效果 |
| **undo_edit 功能** | 增加状态管理复杂度 (file history stack); LLM 很少主动使用 undo; 通过重新 Edit 可以达到同样效果 | 如果编辑错误，LLM 应该再次 Read 文件并执行新的 Edit 来修正 |
| **insert 行插入功能** | 行号定位容易出错 (LLM 对行号的记忆不可靠); str_replace 模式更稳健 | 统一使用 str_replace 模式。需要插入时，old_string 取插入点附近的上下文，new_string 包含上下文+新内容 |
| **消除 evomaster session 依赖** | PROJECT.md 明确标记 out of scope。v1.1 的 session-dependent tools 仍通过 BaseSession 操作 | 保持 BaseSession 作为 tool 的环境接口; 未来 v2 再考虑直接 OS 操作路径 |
| **前端 UI 改动** | v1.1 只涉及后端框架层 | 前端保持现状 |

---

## Feature Dependencies

```
[Tool Description 精细化 (7)]  ← 贯穿所有 tool 的设计
    |
    v
[Read Tool (1)] ← 最基础的工具，其他工具的前置
    |
    +--enables--> [Write Tool (2)]  (read-before-write 协议)
    |
    +--enables--> [Edit Tool (3)]   (read-before-edit 协议)
    |
    +--independent--> [Bash Tool (4)]  (已有 evomaster 实现，仅适配)
    |
    +--independent--> [Glob Tool (5)]  (文件发现，无前置依赖)
    |
    +--independent--> [Grep Tool (6)]  (内容搜索，无前置依赖)

[Read-Before-Modify 协议 (11)]
    |
    +--requires--> [Read (1)] + [Write (2)] + [Edit (3)] 共享状态

[SubAgent Spawn (8)]
    |
    +--requires--> Tools 1-6 已就位 (子 agent 需要可用工具)
    +--requires--> Exp 层装配能力 (创建子 AgentRuntimeSpec)
    +--requires--> Tool Description 精细化 (子 agent 的 prompt 引导)
    |
    +--enhances--> [SubAgent 工具限制 (10)]  (在 spawn 之上的增量)

[System Prompt 模板化 (9)]
    |
    +--requires--> Tool Description 精细化 (7) (工具信息是 prompt 的组成部分)
    +--requires--> ContextBuilder 扩展 (已有基础)
    +--enhances--> SubAgent Spawn (子 agent 可用不同 prompt 模板)
```

### Dependency Notes

- **Read 是所有文件操作工具的锚点**: Write/Edit 的 read-before-modify 协议依赖 Read 先执行
- **Bash 独立于其他 tool**: 已有成熟实现，只需适配为 matmaster Tool Protocol
- **Glob/Grep 独立开发**: 不依赖 Read/Write，可并行实现
- **SubAgent 是最后构建的**: 需要所有基础 tool 就位后才有意义
- **Tool Description 贯穿始终**: 不是独立阶段，而是每个 tool 实现时同步完成

---

## MVP Recommendation

### Phase 1: 原生 Tool 套件 (核心六件套)

优先实现:
1. **Read** -- 所有操作的前置; 从 EditorTool.view 提取重构
2. **Write** -- 文件创建; 从 EditorTool.create 提取重构
3. **Edit** -- 文件修改; 从 EditorTool.str_replace 提取重构
4. **Bash** -- 适配已有 BashTool 为 matmaster Tool Protocol (最低工作量)
5. **Glob** -- 新实现，基于 exec_bash 或 Python pathlib
6. **Grep** -- 新实现，基于 exec_bash (grep -rn 或 rg)
7. **Read-Before-Modify 协议** -- 跨 Read/Write/Edit 的共享状态

**实现策略:** 所有 session-dependent tool 通过 BaseSession 接口操作。每个 tool 是独立类，满足 matmaster `Tool` Protocol (name, description, json_schema, execute)。不再经过 EvoToolAdapter -- 直接实现 Protocol。

### Phase 2: Tool Description 精细化

同步于 Phase 1，但独立验证:
- 每个 tool 的 description 按最佳实践重写
- JSON Schema 精简 (去除冗余字段，显式标记 required)
- ContextBuilder._build_tools 增强 (不仅列出名称，还包含使用提示)
- ExpConfig 扩展 prompt 相关字段

### Phase 3: SubAgent Spawn

在 Phase 1/2 完成后:
- 实现 SubAgentTool (满足 Tool Protocol)
- SubAgent 的 execute() 内部: 创建子 ExpConfig → Exp.assemble() → 子 kernel.run()
- 工具限制: 子 registry 从父 registry 过滤
- 防递归: 子 agent 的工具集不包含 SubAgentTool

### Defer: System Prompt 模板化

- 当前 ExpConfig.developer_instructions 简单字符串足以支撑 v1.1
- 模板化是 v1.2 的优化项，不阻塞当前功能

---

## Tool Description 设计原则 (从 Claude Code 提炼)

基于 Claude Code 系统提示和 OpenAI function calling 最佳实践的分析，总结以下原则:

### 原则 1: Description 是行为规范而非功能说明

**差的 description:**
> "读取文件内容"

**好的 description:**
> "读取文件系统上的文件内容。默认读取前 2000 行。对于大文件，使用 offset 和 limit 参数读取特定部分。结果以 cat -n 格式返回 (带行号)。在修改文件之前必须先用此工具读取。"

### 原则 2: 明确工具之间的分工

Claude Code 的 Bash tool description 中有这样的关键规则:

> "IMPORTANT: Avoid using this tool to run find, grep, cat, head, tail, sed, awk commands. Instead use the appropriate dedicated tool."

这条规则将 bash 从 "万能工具" 收窄为 "专用命令执行器"，迫使 LLM 使用更结构化的 Glob/Grep/Read 工具。

### 原则 3: 用结构化 Markdown 组织 description

Claude Code 的工具 description 使用:
- `###` 分区标题 (如 "### Command Execution", "### Best Practices")
- 有序/无序列表
- 加粗关键词
- "CRITICAL"/"IMPORTANT" 标记

这种结构比纯文本段落更利于 LLM 解析。

### 原则 4: Schema 精简优先

每个 tool definition 在每次 LLM 调用时都消耗 token。原则:
- 只暴露 LLM 需要控制的参数
- 内部实现细节不暴露为参数
- 使用 `default` 值减少 LLM 决策负担
- 显式标记 `required` 字段

### 原则 5: 错误返回要有指导性

工具执行失败时的返回信息应该告诉 LLM 如何修正:
- "Error: file not found at /workspace/foo.py. Use Glob to search for the file."
- "Error: old_string not found in file. The closest match is at line 42."
- "Error: multiple matches found at lines [12, 45, 78]. Include more context to make old_string unique."

---

## 现有实现复用评估

| 现有组件 | 复用策略 | 复用度 |
|----------|----------|--------|
| **evomaster BashTool** | 提取核心逻辑 (命令执行 + 危险检测 + 输出格式化)，去掉 evomaster BaseTool 依赖，直接实现 matmaster Tool Protocol | 70% |
| **evomaster EditorTool** | 拆分为 3 个独立 tool: Read (view 逻辑), Write (create 逻辑), Edit (str_replace 逻辑)。去掉 undo_edit/insert/view_range 等次要功能 | 60% |
| **EvoToolAdapter** | 保留用于 MonitorJobTool 等科研特有 tool 的适配。新内置 tool 直接实现 Protocol，不经过 adapter | 保持现状 |
| **ContextBuilder** | 扩展 _build_tools section; 增加 developer_instructions 模板替换能力 | 90% 复用 |
| **ExpConfig** | 扩展 tools.builtin 配置 (从 `["*"]` 改为具名列表); 增加 SubAgent 相关配置 | 80% 复用 |
| **Exp._init_builtin_tools()** | 重构: 从 "创建 evomaster 工具 + EvoToolAdapter 包装" 改为 "创建 matmaster 原生工具 + 直接注册" | 完全重写 |

---

## Sources

### Claude Code 系统提示与工具实现
- [Claude Code system prompts repository](https://github.com/Piebald-AI/claude-code-system-prompts) -- 18 builtin tool descriptions, sub agent prompts (HIGH confidence)
- [Tools and system prompt of Claude Code (Gist)](https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f) -- full tool parameter schemas (HIGH confidence)
- [Internal Claude Code tools implementation (Gist)](https://gist.github.com/bgauryy/0cdb9aa337d01ae5bd0c803943aa36bd) -- implementation details and behavioral rules (HIGH confidence)
- [Claude Code SubAgent documentation](https://code.claude.com/docs/en/sub-agents) -- official subagent creation, lifecycle, tool access (HIGH confidence)

### LLM Function Calling 最佳实践
- [Gorilla: Tool Description Precision vs Invocation Accuracy](https://www.scalifiai.com/blog/function-calling-tool-call-best%20practices) -- empirical evidence for description quality (MEDIUM confidence)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling) -- strict mode, schema best practices (HIGH confidence)
- [OpenAI Community: Prompting Best Practices for Tool Use](https://community.openai.com/t/prompting-best-practices-for-tool-use-function-calling/1123036) -- naming, description, schema patterns (MEDIUM confidence)
- [Simon Willison: How coding agents work](https://simonwillison.net/guides/agentic-engineering-patterns/how-coding-agents-work/) -- tool design patterns for coding agents (HIGH confidence)

### 现有代码库分析
- `matmaster/tools/tool_registry.py` -- Tool Protocol 定义 (name, description, json_schema, execute)
- `evomaster/agent/tools/builtin/bash.py` -- BashTool 实现 + BashToolParams description
- `evomaster/agent/tools/builtin/editor.py` -- EditorTool 实现 (view/create/str_replace/insert/undo_edit)
- `evomaster/agent/session/base.py` -- BaseSession 接口 (exec_bash, read_file, write_file, path_exists, is_file, is_directory)
- `matmaster/tools/evomaster_tool_adapter.py` -- EvoToolAdapter 适配逻辑
- `matmaster/core/exp.py` -- Exp._init_builtin_tools() 当前注册流程
- `matmaster/core/context_builder.py` -- ContextBuilder section 组装逻辑

---
*Feature research for: v1.1 内置 Tool 套件 + SubAgent + Prompt 体系*
*Researched: 2026-03-24*
