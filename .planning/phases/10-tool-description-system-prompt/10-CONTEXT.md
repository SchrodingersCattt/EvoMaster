# Phase 10: Tool Description 与 System Prompt 设计 - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

为所有 12 个 native builtin tool 编写精细化 description/schema 以优化 LLM 调用准确率，设计 direct 模式完整行为指导 system prompt（developer_instructions + mode_contract）。严格对标 Claude Code 的实现模式。

</domain>

<decisions>
## Implementation Decisions

### Description 内容策略
- **D-01:** 严格参考 Claude Code 实现。Tool description 采用「功能描述 + Usage 段落（多条 bullet point）」格式，包含 when-to-use 场景、gotchas、具体操作指南。不是简短 1-2 句，而是详细的使用说明。每个 description 控制在 100 token 以内（成功标准）。

### System Prompt 行为规范
- **D-02:** developer_instructions 全面对标 Claude Code 的模块化设计，涵盖以下维度：
  - 身份定义（Mat Master, 材料科学 autonomous agent）
  - 工具使用规范（专用工具优先于 bash，Read-before-Modify 协议）
  - 行为约束（read before modifying, avoid over-engineering, 不提出未读代码的修改建议）
  - 输出风格（concise, direct, 先行动后解释）
  - 科研场景特定规则（HPC 节点操作、计算任务管理、远程环境约束）
  - 错误处理策略（远程节点不可达时的行为）

### 工具路由指导位置
- **D-03:** 三层冗余写法，对标 Claude Code：
  1. **Bash description 里** — 明确列出不应通过 bash 执行的操作，引导使用专用工具
  2. **System prompt tool-usage 段** — 逐条列出「用 X 工具而不是 bash Y 命令」的映射规则
  3. **各专用工具 description 里** — 如 Grep 写明 "ALWAYS use grep tool, NEVER invoke grep as a bash command"

### Schema 精细化深度
- **D-04:** 沿用当前按需约束模式（type/description/enum/minItems 按需添加），重点精化参数 description 文本质量。不引入 OpenAI function calling 不支持的 schema 扩展（examples、pattern 等）。对标 Claude Code 的精简 schema + 详细 description 策略。

### Claude's Discretion
- 各 tool description 的具体措辞和 Usage bullet 内容（在 Claude Code 参考框架内自由发挥）
- developer_instructions 各维度的具体文本（在科研场景下适配 Claude Code 的行为规范模式）
- mode_contract 是否需要扩展（当前一句可能足够，也可能需要补充 direct 模式特定行为）
- ContextBuilder 的 _build_tools() 是否需要增强展示格式（当前 "- name: description" 可能不足以承载详细 description）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Claude Code 参考实现（最高优先级）
- `https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f` — Claude Code 完整 tool 定义（description + input_schema）
- `https://github.com/Piebald-AI/claude-code-system-prompts` — Claude Code system prompt 片段集合（110+ 片段），重点关注：
  - `system-prompts/tool-description-*.md` — 各工具 description 完整文本
  - `system-prompts/system-prompt-tool-usage-*.md` — 工具路由规则
  - `system-prompts/system-prompt-doing-tasks-*.md` — 行为约束规范
  - `system-prompts/system-prompt-output-efficiency.md` — 输出风格规范

### 项目定义
- `.planning/PROJECT.md` — 项目愿景、核心价值、三层架构、post-v1 变更
- `.planning/REQUIREMENTS.md` — Phase 10 需求：PRMT-01, PRMT-02
- `.planning/ROADMAP.md` — Phase 10 目标、成功标准、依赖关系

### Phase 8/9 上下文（直接前驱）
- `.planning/phases/08-builtintool-tools/08-CONTEXT.md` — BuiltinTool 基类设计、ClassVar 模式、构造注入决策
- `.planning/phases/09-tools/09-CONTEXT.md` — 文件操作 tool 设计、Read-Before-Modify 协议、ExpConfig 显式列举

### Phase 10 直接依赖的代码
- `matmaster/tools/builtin/base.py` — BuiltinTool ABC（name/description/json_schema ClassVar）
- `matmaster/tools/builtin/bash_tool.py` — BashTool（当前 description + schema，改造目标）
- `matmaster/tools/builtin/listdir_tool.py` — ListDirTool（改造目标）
- `matmaster/tools/builtin/read_tool.py` — ReadTool（改造目标）
- `matmaster/tools/builtin/write_tool.py` — WriteTool（改造目标）
- `matmaster/tools/builtin/edit_tool.py` — EditTool（改造目标）
- `matmaster/tools/builtin/glob_tool.py` — GlobTool（改造目标）
- `matmaster/tools/builtin/grep_tool.py` — GrepTool（改造目标）
- `matmaster/tools/builtin/task/` — 5 个 TaskTool（改造目标）
- `matmaster/exps/direct.toml` — developer_instructions + mode_contract（改造目标）
- `matmaster/core/context_builder.py` — ContextBuilder.build() + _build_tools()（可能需要调整）
- `matmaster/tools/tool_registry.py` — Tool Protocol + get_tool_definitions()（schema 转换）
- `matmaster/core/exp.py` — Exp.build_runtime()（system prompt 组装调用点）
- `matmaster/config/exp.py` — ExpConfig（developer_instructions/mode_contract 字段）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/tools/builtin/base.py` BuiltinTool ABC — 所有 tool 的 ClassVar (name/description/json_schema) 已就绪，直接修改内容
- `matmaster/core/context_builder.py` ContextBuilder — 已有固定 section 顺序 (identity → mode_contract → skills → tools → memory → task)，支持 disabled_sections
- `matmaster/exps/direct.toml` — 已有 developer_instructions/mode_contract 字段，支持多行字符串
- `matmaster/config/loader.py` — 已有 prompt 字段 verbatim 保留机制（不做 env-var 展开）

### Established Patterns
- BuiltinTool 子类: ClassVar 定义 name/description/json_schema，_execute 实现业务逻辑
- ToolRegistry.get_tool_definitions() 转 OpenAI function calling 格式（name/description/parameters）
- ContextBuilder._build_tools() 生成 "- {tool.name}: {tool.description}" 格式的 tools section
- TOML 多行字符串用 ''' 包裹，支持长文本

### Integration Points
- `matmaster/tools/builtin/*.py` — 12 个 tool class 的 description/json_schema ClassVar 修改
- `matmaster/exps/direct.toml` — developer_instructions/mode_contract 内容扩展
- `matmaster/core/context_builder.py:_build_tools()` — 可能需要调整展示格式以支持详细 description
- `matmaster/devshell/` — 验证环境，使用完整 tool 集和 system prompt 进行多轮对话测试

</code_context>

<specifics>
## Specific Ideas

- 严格参考 Claude Code 的 tool description 模式：开头一句功能描述 + "Usage:" 段落 + 多条 bullet point（场景、gotchas、协议要求）
- Claude Code 的 Bash tool description 包含大量专用工具路由规则（"Avoid using this tool to run find, grep, cat..."），MatMaster 的 BashTool 应同样处理
- Claude Code 的 Grep tool description 包含 "ALWAYS use Grep for search tasks. NEVER invoke grep or rg as a Bash command" 这种强制路由声明
- Claude Code 的 TaskCreate description 包含 "When to Use" / "When NOT to Use" 段落
- developer_instructions 参考 Claude Code 的 doing-tasks + tool-usage + output-efficiency 片段模式，适配材料科学场景
- ContextBuilder._build_tools() 当前只渲染 "- name: description"，如果 description 变长需要评估是否调整格式

</specifics>

<deferred>
## Deferred Ideas

- Prompt 模板加载器基础设施（当前直接在 TOML/ClassVar 中管理，INFR-D01）
- MonitorJobTool 的 description 精细化（仍走 EvoToolAdapter 路径，不在 Phase 10 范围）
- ContextBuilder 的 tools section 增强为更丰富的格式（如分组展示、参数摘要）— 需评估 token 开销

</deferred>

---

*Phase: 10-tool-description-system-prompt*
*Context gathered: 2026-03-25*
