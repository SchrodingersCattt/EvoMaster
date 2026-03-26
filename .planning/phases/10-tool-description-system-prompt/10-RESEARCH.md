# Phase 10: Tool Description 与 System Prompt 设计 - Research

**Researched:** 2026-03-25
**Domain:** LLM tool calling description engineering + agent system prompt design
**Confidence:** HIGH

## Summary

Phase 10 的核心任务分两块：(1) 为 12 个 native builtin tool 编写精细化的 description 和 json_schema 参数描述，(2) 为 direct 模式编写完整的 developer_instructions 和 mode_contract 系统提示。两者都严格对标 Claude Code 的实现模式。

Claude Code 的 tool description 采用模块化片段组装模式：一个 tool 的 description 由多个独立片段拼接而成（例如 Bash 工具有 20+ 个片段，涵盖 overview、prefer-dedicated-tools、alternative-read/write/search 路由、git 提交规范等）。MatMaster 不需要这么复杂的模块化，但应继承其核心策略：功能一句话 + Usage 段落 + bullet points，在 description 中嵌入工具路由规则、使用场景和 gotcha 提示。

系统提示方面，Claude Code 将行为规范拆分为独立片段（doing-tasks-read-before-modifying、output-efficiency、avoid-over-engineering 等），然后在 system prompt 中按需组装。MatMaster 的 developer_instructions 应将这些规范适配为材料科学 autonomous agent 的场景，放在 TOML 多行字符串中。

**Primary recommendation:** 直接修改 12 个 tool class 的 ClassVar description/json_schema + 扩展 direct.toml 的 developer_instructions 和 mode_contract。不需要引入新的基础设施或文件。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 严格参考 Claude Code 实现。Tool description 采用「功能描述 + Usage 段落（多条 bullet point）」格式，包含 when-to-use 场景、gotchas、具体操作指南。不是简短 1-2 句，而是详细的使用说明。每个 description 控制在 100 token 以内（成功标准）。
- **D-02:** developer_instructions 全面对标 Claude Code 的模块化设计，涵盖：身份定义、工具使用规范、行为约束、输出风格、科研场景特定规则、错误处理策略。
- **D-03:** 三层冗余工具路由写法：Bash description 里列不应通过 bash 的操作 + system prompt tool-usage 段 + 各专用工具 description 里写 ALWAYS/NEVER 声明。
- **D-04:** 沿用当前按需约束模式（type/description/enum/minItems），重点精化参数 description 文本。不引入 OpenAI function calling 不支持的 schema 扩展。

### Claude's Discretion
- 各 tool description 的具体措辞和 Usage bullet 内容（在 Claude Code 参考框架内自由发挥）
- developer_instructions 各维度的具体文本（在科研场景下适配）
- mode_contract 是否需要扩展
- ContextBuilder._build_tools() 是否需要增强展示格式

### Deferred Ideas (OUT OF SCOPE)
- Prompt 模板加载器基础设施（INFR-D01）
- MonitorJobTool 的 description 精细化（仍走 EvoToolAdapter 路径）
- ContextBuilder 的 tools section 增强为更丰富的格式（如分组展示、参数摘要）
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PRMT-01 | 每个 builtin tool 具有精细化的 description 和 json_schema，优化 LLM 调用准确率 | Claude Code tool description 模式已完整提取（overview + Usage bullets），12 个 tool 的当前 description 已审计，改造路径清晰 |
| PRMT-02 | Exp system prompt（developer_instructions）针对 direct 模式设计完整的 agent 行为指导 | Claude Code system prompt 的 doing-tasks、tool-usage、output-efficiency 片段已完整提取，适配材料科学场景的维度映射已确定 |
</phase_requirements>

## Architecture Patterns

### Claude Code Tool Description 模式（参考标准）

Claude Code 的 tool description 是一个**分层内容结构**：

1. **开头一句话** -- 功能概述（如 "Executes a given bash command and returns its output."）
2. **Usage: 段落** -- 多条 bullet point，涵盖：
   - 必须/禁止的使用模式（ALWAYS/NEVER/IMPORTANT 关键词）
   - 参数约束细节
   - 与其他工具的路由关系
   - 场景化的使用建议
3. **工具路由声明**（在 Bash description 和各专用工具 description 中冗余出现）

从 Piebald-AI/claude-code-system-prompts 仓库提取的实际片段：

**Bash 工具路由部分**（嵌入在 description 中）：
```
IMPORTANT: Avoid using this tool to run find, grep, cat, head, tail, sed, awk, echo commands...
Instead, use the appropriate dedicated tool:
 - File search: Use glob (NOT find or ls)
 - Content search: Use grep (NOT grep or rg)
 - Read files: Use read_file (NOT cat/head/tail)
 - Edit files: Use edit_file (NOT sed/awk)
 - Write files: Use write_file (NOT echo >/cat <<EOF)
While the execute_bash tool can do similar things, it's better to use the built-in tools.
```

**Grep 工具路由声明**：
```
ALWAYS use grep for search tasks. NEVER invoke grep or rg as a execute_bash command.
The grep tool has been optimized for correct permissions and access.
```

**Read 工具的 Usage 段落**：
```
Usage:
- The file_path parameter must be an absolute path, not a relative path
- Output will be line-numbered (cat -n format)
- Always read a file before attempting to edit or overwrite it
```

### Claude Code System Prompt 模块（developer_instructions 参考）

从仓库提取的关键行为片段，按 D-02 的维度组织：

| 维度 | Claude Code 片段 | MatMaster 适配方向 |
|------|------------------|-------------------|
| 工具使用规范 | tool-usage-reserve-bash: "Reserve Bash exclusively for system commands" | 改为：远程节点上 bash 用于系统命令，文件操作用专用工具 |
| 工具使用规范 | tool-usage-read/edit/search/create: 每条一行映射 | 逐条列出 MatMaster 的专用工具映射 |
| 行为约束 | doing-tasks-read-before-modifying: "do not propose changes to code you haven't read" | 直接复用，与 Read-Before-Modify 协议呼应 |
| 行为约束 | doing-tasks-avoid-over-engineering: "Only make changes that are directly requested" | 适配科研场景：不要给计算脚本增加不必要的复杂度 |
| 输出风格 | output-efficiency: "Go straight to the point. Be extra concise." | 适配：agent 的对话输出简洁直接 |
| 安全 | doing-tasks-security: "Be careful not to introduce security vulnerabilities" | 适配：远程节点上不暴露密钥、不执行危险命令 |
| 阻塞处理 | doing-tasks-blocked-approach: "If blocked, consider alternatives" | 适配：远程节点不可达时的降级策略 |

### 当前代码架构（改造目标）

```
matmaster/tools/builtin/
  base.py             # BuiltinTool ABC: name/description/json_schema ClassVar
  bash_tool.py        # 改造: description + json_schema 参数描述
  listdir_tool.py     # 改造: description + json_schema 参数描述
  read_tool.py        # 改造: description + json_schema 参数描述
  write_tool.py       # 改造: description + json_schema 参数描述
  edit_tool.py        # 改造: description + json_schema 参数描述
  glob_tool.py        # 改造: description + json_schema 参数描述
  grep_tool.py        # 改造: description + json_schema 参数描述
  task/
    task_create.py    # 改造: description + json_schema 参数描述
    task_get.py       # 改造: description + json_schema 参数描述
    task_list.py      # 改造: description + json_schema 参数描述
    task_update.py    # 改造: description + json_schema 参数描述
    task_complete.py  # 改造: description + json_schema 参数描述

matmaster/exps/
  direct.toml         # 改造: developer_instructions + mode_contract 扩展

matmaster/core/
  context_builder.py  # 可能调整: _build_tools() 格式
```

### 当前 description 现状审计

| Tool | 当前 description | 问题 |
|------|-----------------|------|
| execute_bash | "Execute a bash command in the terminal within a persistent shell session." | 缺少工具路由、Usage bullets、gotchas |
| list_dir | "List files and directories at the specified path." | 太简短，缺少使用场景 |
| read_file | 3 句话，含 Read-Before-Modify 提示 | 已有基础，需扩展 Usage 格式 |
| write_file | 3 句话，含 Read-Before-Modify 提示 | 已有基础，需扩展 Usage 格式 |
| edit_file | 3 句话，含 unique match + read first | 已有基础，需扩展 Usage 格式 |
| glob | 2 句话 | 缺少 ALWAYS/NEVER 路由声明 |
| grep | 3 句话 | 缺少 ALWAYS/NEVER 路由声明 |
| task_create | "Create a new task for tracking work progress." | 缺少 When-to-Use/When-NOT-to-Use 段落 |
| task_get | "Get a task by its ID to check status and details." | 缺少上下文 |
| task_list | "List all tasks to see current work status." | 缺少上下文 |
| task_update | 2 句话 | 基本够用但可增强 |
| task_complete | "Mark a task as completed when it is done." | 缺少使用条件 |

### Token Budget 分析

成功标准要求 description 控制在 100 token 以内。以下是 Claude Code 对应工具 description 的 token 估算：

- Bash overview 一句话: ~15 token
- Read full description with Usage: ~150-200 token
- Glob description: ~80 token
- Grep description: ~100 token

Claude Code 的完整 Bash description 远超 100 token（加上 git 规范可达 1000+ token），但那是因为它包含了 git commit/PR 流程。MatMaster 不需要这些内容。

**关键约束解读:** 100 token 的限制应理解为：每个 tool 的 description ClassVar 内容控制在约 100 token（约 400 字符英文，约 200 字符中英混合）。这要求比 Claude Code 的完整 description 更精炼，但比当前的 1-2 句话更丰富。实际操作中应以 Claude Code 的 description 核心内容为参考，裁剪掉 MatMaster 不需要的部分（git 规范、沙箱设置等），保留工具路由和核心 Usage 要点。

**重要：** 工具路由规则等长篇内容可以放在 developer_instructions 中（system prompt 的 tool-usage 段），而不必全部塞入 description。这是 Claude Code 的实际做法 -- description 承载核心功能和关键 gotcha，system prompt 承载详细行为规范。

### ContextBuilder._build_tools() 格式评估

当前格式：
```
# Available Tools

- read_file: Read the contents of a file...
- write_file: Write content to a file...
```

当 description 扩展为多行 Usage 格式后，这种单行列表可能导致 system prompt 中的 tools section 变得密集难读。

**建议:** 保持当前 `- name: description` 格式不变。原因：
1. description 是独立传递到 LLM function calling API 的（通过 get_tool_definitions()），system prompt 的 tools section 只是辅助性总结
2. Claude Code 也是在 tools section 用简短列表，详细 description 在 function calling schema 中
3. CONTEXT.md 已将 ContextBuilder 格式增强列为 Deferred

但如果 description 过长（超过一行合理阅读长度），_build_tools() 可以只提取 description 的第一句话。这个决策留给 Claude's Discretion 判断。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Token 计算 | 自建 tokenizer 计算 description token 数 | 人工估算 + 测试验证 | tiktoken 或其他 tokenizer 是非必要依赖，100 token 约等于 75 英文词/400 字符，手动控制即可 |
| Prompt 模板系统 | 模板引擎 + 变量替换 | TOML 多行字符串直接写 | INFR-D01 已 deferred，当前 TOML verbatim 保留机制够用 |
| Description 片段组装 | Claude Code 式的片段拼接引擎 | 直接在 ClassVar 中写完整 description | MatMaster 只有 12 个 tool，不需要 100+ 片段的组装系统 |

## Common Pitfalls

### Pitfall 1: Description 超出 Token Budget
**What goes wrong:** 模仿 Claude Code 写出详细的 Usage 段落后，单个 description 轻松超过 100 token。
**Why it happens:** Claude Code 的 Bash description 含 git 提交、PR 创建、沙箱设置等 MatMaster 不需要的内容，直接照搬会超长。
**How to avoid:** 每个 description 完成后用简单估算验证（英文约 75 词 = 100 token）。核心路由规则可分流到 developer_instructions。
**Warning signs:** description 字符串超过 400 个英文字符（含空格）。

### Pitfall 2: 工具路由规则不一致
**What goes wrong:** Bash description 说 "use read_file instead of cat" 但 read_file description 没有对应的 "ALWAYS use read_file" 声明。
**Why it happens:** 三层冗余要求容易遗漏某一层。
**How to avoid:** 建立路由映射表，逐条检查三层是否都覆盖：

| Bash 操作 | 应路由到 | Bash desc 声明 | System prompt 声明 | 专用工具 desc 声明 |
|-----------|---------|---------------|-------------------|------------------|
| cat/head/tail | read_file | check | check | check |
| echo >/heredoc | write_file | check | check | check |
| sed/awk | edit_file | check | check | check |
| find/ls | glob | check | check | check |
| grep/rg | grep | check | check | check |

### Pitfall 3: TOML 多行字符串格式错误
**What goes wrong:** developer_instructions 中的特殊字符导致 TOML 解析失败。
**Why it happens:** TOML 的 ''' 多行字符串中如果出现 ''' 本身就会截断。
**How to avoid:** developer_instructions 中避免使用三个连续单引号。如需包含单引号，用 """ 双引号多行字符串或转义。
**Warning signs:** load_exp_config("direct") 抛出 TOML 解析错误。

### Pitfall 4: System prompt 的 tools section 与 function calling schema 不一致
**What goes wrong:** ContextBuilder._build_tools() 渲染的 tools section 使用了旧的短 description，而 get_tool_definitions() 返回的 schema 用了新的长 description。
**Why it happens:** _build_tools() 直接读取 tool.description，如果 description 变成多行就会在 system prompt 中显得杂乱。
**How to avoid:** 要么接受多行 description 在 tools section 中的展示效果，要么让 _build_tools() 只截取第一句话。
**Warning signs:** system prompt 的 "# Available Tools" section 视觉上难以阅读。

### Pitfall 5: MatMaster 科研场景的 description 混入了 Claude Code 的本地文件系统假设
**What goes wrong:** description 中写 "absolute path" 但 MatMaster 的工具在远程节点上运行，文件路径是远程的。
**Why it happens:** 直接复制 Claude Code 的 description 文本。
**How to avoid:** 将所有路径相关描述适配为 "远程文件路径" 或省略 "本地/绝对" 等限定词。MatMaster 的 workdir 是 session 上的路径。

## Code Examples

### Pattern 1: Tool Description 格式（Claude Code 风格适配）

```python
# Source: Claude Code tool-description-grep.md + tool-description-bash-alternative-content-search.md
# 适配为 MatMaster 风格

class GrepTool(BuiltinTool):
    name: ClassVar[str] = "grep"
    description: ClassVar[str] = (
        "Search file content for a regex pattern within the workspace.\n\n"
        "Usage:\n"
        "- ALWAYS use grep for content search. NEVER use grep/rg via execute_bash.\n"
        "- Supports regex syntax (e.g. 'import os', 'def foo.*:').\n"
        "- Use include to filter by file type (e.g. '*.py').\n"
        "- Returns matching lines with file paths and line numbers."
    )
```

### Pattern 2: Bash Description 工具路由声明

```python
# Source: Claude Code tool-description-bash-prefer-dedicated-tools.md
# + bash-alternative-*.md fragments

class BashTool(BuiltinTool):
    name: ClassVar[str] = "execute_bash"
    description: ClassVar[str] = (
        "Execute a bash command in the session shell.\n\n"
        "IMPORTANT: Avoid using this tool to run find, grep, cat, head, "
        "tail, sed, awk commands. Use dedicated tools instead:\n"
        "- File search: Use glob (NOT find/ls)\n"
        "- Content search: Use grep (NOT grep/rg)\n"
        "- Read files: Use read_file (NOT cat/head/tail)\n"
        "- Edit files: Use edit_file (NOT sed/awk)\n"
        "- Write files: Use write_file (NOT echo/heredoc)"
    )
```

### Pattern 3: developer_instructions TOML 格式

```toml
# Source: Claude Code system-prompt doing-tasks + tool-usage + output-efficiency
# 适配为 MatMaster 材料科学场景

developer_instructions = '''
You are Mat Master, an autonomous agent for materials science and computational materials.
You operate on a remote compute node via session. All file operations happen on the remote environment.

# Tool Usage
- Use read_file to read files, NOT cat/head/tail via execute_bash
- Use write_file to create files, NOT echo/heredoc via execute_bash
- Use edit_file to edit files, NOT sed/awk via execute_bash
- Use glob to find files, NOT find/ls via execute_bash
- Use grep to search file content, NOT grep/rg via execute_bash
- Reserve execute_bash for system commands, package management, and terminal operations
- Use task tools to track multi-step work progress

# Behavior
- Read and understand existing files before modifying them
- Avoid over-engineering. Only make changes directly needed for the task
- If blocked, consider alternative approaches rather than retrying the same action
- Do not introduce security vulnerabilities. Never expose secrets or keys

# Output Style
- Be concise and direct. Lead with the action, not the reasoning
- Focus on decisions needing user input, status updates, and errors/blockers

# Remote Environment
- All file paths are on the remote compute node, not local
- The workspace directory is your primary working area
- Package installation may require specific channels or environments
- Long-running computations should be monitored via task tracking
'''
```

### Pattern 4: Task Tool Description（When-to-Use 格式）

```python
# Source: Claude Code tool-description-taskcreate.md / tool-description-todowrite.md

class TaskCreateTool(BuiltinTool):
    name: ClassVar[str] = "task_create"
    description: ClassVar[str] = (
        "Create a task for tracking work progress.\n\n"
        "When to use: Complex multi-step tasks, multiple user requests, "
        "or tasks requiring careful planning.\n"
        "When NOT to use: Single trivial tasks completable in one step."
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| 简短 1-2 句 description | 结构化 Usage 段落 + bullet points | Claude Code 2.0+ (2024) | LLM 工具选择准确率显著提升 |
| 单层工具说明 | 三层冗余（description + system prompt + bash 路由）| Claude Code 2.1+ (2024-2025) | 减少 LLM 通过 bash 做本应用专用工具的操作 |
| 通用 system prompt | 模块化行为片段按需组装 | Claude Code 2.1.53+ | 可按场景裁剪行为指导 |

## OpenAI Function Calling Schema 约束

| 属性 | 支持状态 | 说明 |
|------|---------|------|
| type | 支持 | string, number, integer, boolean, array, object |
| description | 支持 | 函数级 1024 字符软限制（Responses API 实际可更长），参数级无明确限制 |
| enum | 支持 | 有限选项列表 |
| required | 支持 | 必填参数列表 |
| minItems/maxItems | 支持 | 数组长度约束 |
| default | 支持 | 默认值 |
| examples | **不支持** | D-04 决策已排除 |
| pattern (regex) | **不支持** | D-04 决策已排除 |
| additionalProperties | 支持 | 对象属性约束 |

**Confidence:** MEDIUM -- OpenAI 的 description 字符限制从 1024 已可能放宽，但官方未明确文档化。MatMaster 使用的 LLM 可能是 OpenAI 兼容 API（如 DeepSeek、Claude 等），具体限制取决于 provider。100 token 的 description 约 400 字符，远低于任何已知限制，不存在风险。

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (via uv run pytest) |
| Config file | pytest.ini |
| Quick run command | `uv run pytest tests/matmaster/tools/ -x` |
| Full suite command | `uv run pytest tests/matmaster/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PRMT-01-a | 每个 tool description 非空且含 Usage 或核心要点 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py -x` | Wave 0 |
| PRMT-01-b | 每个 tool description 控制在 100 token (~400 字符) 以内 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py::test_description_token_budget -x` | Wave 0 |
| PRMT-01-c | json_schema 的每个参数都有 description 字段 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py::test_schema_param_descriptions -x` | Wave 0 |
| PRMT-01-d | 三层路由一致性：bash desc 提及的路由在对应工具 desc 中有对应声明 | unit | `uv run pytest tests/matmaster/tools/test_tool_descriptions.py::test_routing_consistency -x` | Wave 0 |
| PRMT-02-a | developer_instructions 非空且包含关键维度（身份、工具使用、行为约束） | unit | `uv run pytest tests/matmaster/core/test_context_builder.py -x` | 已有，需扩展 |
| PRMT-02-b | direct.toml 加载后 ExpConfig 的 developer_instructions 包含预期内容 | integration | `uv run pytest tests/matmaster/integration/test_direct_toml_prompt.py -x` | Wave 0 |
| PRMT-02-c | build_runtime 组装的 system prompt 包含 identity 和 mode_contract 内容 | integration | `uv run pytest tests/matmaster/core/test_exp.py -x` | 已有，可能需扩展 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/tools/test_tool_descriptions.py -x`
- **Per wave merge:** `uv run pytest tests/matmaster/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/matmaster/tools/test_tool_descriptions.py` -- 12 个 tool 的 description 格式/长度/路由一致性测试
- [ ] `tests/matmaster/integration/test_direct_toml_prompt.py` -- direct.toml 加载后 developer_instructions 内容验证

## Open Questions

1. **_build_tools() 是否需要截取 description 第一句话?**
   - What we know: 当前格式 `- name: description`，description 变长后 system prompt 的 tools section 可能过于冗长
   - What's unclear: 实际效果需要看渲染后的 system prompt 长度
   - Recommendation: 先不改，保持全文。如果测试中发现 system prompt 过长，再考虑截取。这属于 Claude's Discretion 范围。

2. **mode_contract 是否需要扩展?**
   - What we know: 当前只有一句 "You are in direct execution mode. Complete the user's task directly using available tools."
   - What's unclear: 是否需要补充 direct 模式特定的行为约束
   - Recommendation: 在 developer_instructions 已经涵盖核心行为的情况下，mode_contract 保持简短即可。如果需要区分 direct vs planner 模式的特定行为，可以在 mode_contract 中补充 2-3 条规则。

## Sources

### Primary (HIGH confidence)
- [Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts) - 完整 Claude Code system prompt 片段集合，逐一提取了 tool-description、system-prompt-tool-usage、system-prompt-doing-tasks、output-efficiency 等关键片段
- [wong2/claude-code-tools gist](https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f) - Claude Code 完整 tool 定义（description + input_schema）及 system prompt 完整文本

### Secondary (MEDIUM confidence)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling) - Function calling schema 支持的字段类型
- [OpenAI Community: Description Max Length](https://community.openai.com/t/was-the-character-limit-for-schema-descriptions-upgraded/1225975) - description 字符限制讨论（1024 字符软限制，Responses API 可能更长）

### Codebase (HIGH confidence)
- `matmaster/tools/builtin/*.py` -- 12 个 tool 的当前 description/json_schema 实现
- `matmaster/exps/direct.toml` -- 当前 developer_instructions/mode_contract
- `matmaster/core/context_builder.py` -- _build_tools() 格式和 section 顺序
- `matmaster/tools/tool_registry.py` -- get_tool_definitions() OpenAI 格式转换

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - 无需引入新库，纯文本内容改造
- Architecture: HIGH - 改造目标明确，Claude Code 参考标准已完整提取
- Pitfalls: HIGH - 基于 Claude Code 实际实现和 MatMaster 场景差异推导

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable domain, tool description patterns mature)
