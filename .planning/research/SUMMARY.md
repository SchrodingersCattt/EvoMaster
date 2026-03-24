# Project Research Summary

**Project:** MatMaster v1.1 -- Agent 外围能力构建
**Domain:** AI Agent Framework (内置 Tool 套件 + SubAgent Spawn + Prompt/Description 体系)
**Researched:** 2026-03-24
**Confidence:** HIGH

## Executive Summary

MatMaster v1.1 是一个纯架构扩展项目，不引入任何新外部依赖。目标是在已有的三层架构（Playground -> Exp -> AgentKernel）上构建三类新能力：原生内置工具套件（Read/Write/Edit/Bash/Glob/Grep 等 9 个 tool）、SubAgent spawn 机制（通过 tool_call 触发子 agent 执行）、以及 prompt/description 精细化管理。所有功能均基于现有 Python 标准库 + Pydantic v2 实现，核心扩展点集中在 `matmaster/tools/builtin/` 新目录和 `core/exp.py` 的装配逻辑中。预估新增约 750 行代码。

推荐的实现路径是：先建立 BuiltinTool 基类并从 evomaster EditorTool 拆分出独立的文件操作工具（替代多命令合一的旧模式），然后构建 prompt 模板体系实现 tool description 与代码的解耦，最后在工具和 prompt 体系就绪后实现 SubAgent spawn。这个顺序基于严格的依赖链：SubAgent 需要可用的工具集和独立的 prompt 模板，而工具集需要先有基类和注册机制。

主要风险集中在三个方面：(1) session-dependent tool 的生命周期管理 -- tool 实例的状态隔离在 SubAgent 共享 workspace 场景下可能产生冲突；(2) SubAgent 同步阻塞执行期间的取消传播和事件路由 -- 子 agent 运行时父 agent 无法响应停止请求且前端 SSE 流出现空白；(3) tool description 膨胀 -- 10+ 个 tool 的 description 在每次 LLM 调用中累计消耗大量 token。这三个风险都有明确的防御策略（分别是 per-run tool 实例、stop_event 级联传递、description 分层设计），但必须在对应 phase 的设计阶段解决，不能延后。

## Key Findings

### Recommended Stack

v1.1 不需要引入任何新的外部依赖。所有能力通过现有技术栈实现。

**Core technologies:**
- **Python stdlib (pathlib, fnmatch, re, subprocess, tomllib):** 覆盖 session-free tool 的所有 I/O 操作 -- 标准库已在 codebase 广泛使用
- **Pydantic v2:** tool 参数 schema 定义 + model_json_schema() 生成 OpenAI function schema + ExpConfig 扩展 -- 已是核心依赖
- **BaseSession API:** session-dependent tool 的远程操作全部委托给已有的 exec_bash/read_file/write_file 等方法 -- 无需扩展接口

**明确不引入的技术:** Jinja2（prompt 不需要条件渲染）、asyncio（v1.1 SubAgent 同步执行）、celery/dramatiq（SubAgent 是同步 tool call）、langchain/llamaindex（破坏三层边界）、ripgrep binary（远程环境不能假设安装）。

### Expected Features

**Must have (table stakes):**
- **Read/Write/Edit** -- agent 操作文件的基础能力，从 evomaster EditorTool 拆分为独立 tool
- **Bash** -- 命令执行，适配已有 evomaster BashTool 为 matmaster Tool Protocol
- **Glob/Grep** -- 文件搜索和内容搜索，当前只能通过 bash find/grep 间接实现
- **Tool Description 精细化** -- 每个 tool 的 description 按 Claude Code 最佳实践重写，直接影响 LLM 调用准确率
- **Read-Before-Modify 协议** -- 防止 LLM 盲写文件，跨 Read/Write/Edit 共享状态

**Should have (differentiators):**
- **SubAgent Spawn** -- 允许 agent 委派子任务给独立子 agent，实现探索/执行分离
- **SubAgent 工具限制** -- 不同类型子 agent 有不同的工具访问权限
- **System Prompt 模板化** -- prompt 模板从 TOML 分离到独立 .md 文件，支持变量替换

**Defer (v2+):**
- MultiEdit（批量编辑，LLM 调用出错率高）
- NotebookRead/NotebookEdit（当前场景非核心）
- WebFetch/WebSearch（远程环境可能无外网）
- TodoRead/TodoWrite（LLM 维护 TODO 可靠性低）
- 消除 evomaster session 依赖（PROJECT.md 明确标记 out of scope）

### Architecture Approach

新增组件全部落在 `matmaster/tools/builtin/` 和 `matmaster/prompts/` 两个新目录，加上 `core/exp.py` 和 `config/exp.py` 的扩展。不引入新的顶级目录，不改动 AgentKernel、ToolRegistry、PlaygroundContext 的接口。

**Major components:**
1. **BuiltinTool 基类 (`tools/builtin/base.py`)** -- 直接实现 Tool Protocol，session-dependent 工具在 __init__ 注入 session，无需 EvoToolAdapter
2. **9 个具体 Builtin Tools** -- 从 evomaster 拆分（Bash/Read/Write/Edit）+ 新增（Glob/Grep/ListDir/Think/MonitorJob），每个 40-80 LOC
3. **SubAgentTool (`tools/sub_agent.py`)** -- 通过 spawn_fn callable 注入解耦 tool 与 Exp 层，避免循环依赖
4. **PromptTemplateLoader (`prompts/loader.py`)** -- 从 .md 文件加载 prompt 模板，简单 ${variable} 替换，不引入 Jinja2
5. **ExpConfig 扩展** -- tools.builtin 支持具名列表、sub_agent 配置段、prompt 模板引用

**关键架构模式:**
- Tool Protocol 直接实现（不走 Adapter）
- spawn_fn 闭包注入（解耦 tool 与 Exp）
- ExpConfig 配置驱动的 tool 选择
- Description 分层：json_schema.description（简短）vs usage_guide（详细，仅 prompt 中）

### Critical Pitfalls

1. **Session-Dependent Tool 生命周期不匹配** -- tool 实例持有 session 引用，SubAgent 场景下 tool 状态（如 undo history）可能跨 agent 污染。防御：每个 agent run 创建独立 tool 实例，tool 状态 per-run 隔离
2. **SubAgent 阻塞执行的取消和事件问题** -- 子 agent 可能运行数十个 turn，期间父 agent 无法响应取消且前端 SSE 无事件。防御：stop_event 级联传递 + 子 agent 事件通过父的 MessageBus 发送
3. **Tool Description 膨胀** -- 10+ tool 每个 500 token = 每次 LLM 调用 5000 token 固定开销。防御：description 控制在 100 token 以内，详细指导放 system prompt
4. **SubAgent 与父 Agent 的 Workspace 文件冲突** -- 子 agent 修改父依赖的文件导致 str_replace 失败。防御：per-run tool 实例 + spawn result 包含修改文件列表
5. **Prompt Template 与 ExpConfig 耦合断裂** -- TOML 变成 2000+ 行 prompt 存储。防御：prompt 模板分离到独立 .md 文件，TOML 只引用路径

## Implications for Roadmap

### Phase 1: BuiltinTool 基础设施 + 核心 Tools
**Rationale:** 所有后续能力都依赖 BuiltinTool 基类和 Exp 中的原生 tool 注册机制。先用 ThinkTool（session-free）验证基类设计，再用 BashTool（session-dependent）验证 session 注入。
**Delivers:** `tools/builtin/` 基类 + ThinkTool + BashTool + ExpToolsConfig 具名列表 + Exp._init_builtin_tools 替换
**Addresses:** Features #4 (Bash), #7 (Tool Description 基础)
**Avoids:** Pitfall #1 (Tool Protocol 签名决策必须在此阶段完成), Pitfall #6 (session-free vs session-dependent 分类)

### Phase 2: 文件操作 Tools
**Rationale:** Read/Write/Edit 是 agent 最高频操作，Glob/Grep 是 SubAgent 场景下子 agent 的核心能力。这些 tool 之间有依赖（Read-Before-Modify 协议跨 Read/Write/Edit）。
**Delivers:** FileReadTool, FileWriteTool, FileEditTool, ListDirTool, GlobSearchTool, GrepSearchTool + Read-Before-Modify 协议
**Addresses:** Features #1-6, #11 (Read-Before-Modify)
**Avoids:** Pitfall #9 (命名冲突 -- 此阶段确定最终 tool 名称)

### Phase 3: Prompt/Description 体系
**Rationale:** Tool description 精细化需要 tool 套件先就位（才知道要描述哪些 tool）。SubAgent 需要独立的 prompt 模板，所以 prompt 体系必须在 SubAgent 之前完成。
**Delivers:** PromptTemplateLoader + tool description 重写 + ContextBuilder tools section 升级 + ExpConfig prompt 配置
**Addresses:** Features #7 (Tool Description 精细化), #9 (System Prompt 模板化)
**Avoids:** Pitfall #3 (description 膨胀), Pitfall #5 (prompt 与 config 耦合), Pitfall #8 (tools section 信息冗余)

### Phase 4: SubAgent Spawn
**Rationale:** 最复杂的功能，依赖前面所有基础设施（tool 套件 + prompt 模板）。需要解决取消传播、事件路由、递归保护、result 截断等设计问题。
**Delivers:** SubAgentTool + Exp._make_spawn_fn + 递归深度保护 + 子 exp TOML 定义（researcher.toml 等）+ SubAgent 工具限制
**Addresses:** Features #8 (SubAgent Spawn), #10 (工具限制)
**Avoids:** Pitfall #2 (阻塞执行), Pitfall #4 (workspace 冲突), Pitfall #7 (frozen spec), Pitfall #10 (result token 膨胀)

### Phase 5: MonitorJobTool 迁移 + 集成验证
**Rationale:** 收尾阶段，将科研特有的 MonitorJobTool 迁移为原生 BuiltinTool，并进行 DevShell 端到端验证。
**Delivers:** MonitorJobTool 原生化 + DevShell 集成测试 + 全链路验证
**Addresses:** 验证所有 phase 的集成正确性

### Phase Ordering Rationale

- Phase 1 先行因为 BuiltinTool 基类是所有 tool 的基础，ExpToolsConfig 扩展影响所有后续 tool 注册
- Phase 2 紧随因为文件操作 tool 是最高频的 agent 能力，也是 SubAgent 可用性的前提
- Phase 3 在 Phase 4 之前因为 SubAgent 需要独立的 system prompt 和精细化的 tool description
- Phase 4 最后因为它是最复杂的组件，依赖前面所有基础设施，且风险最集中
- Phase 5 是验证阶段，确保所有组件协同工作

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4 (SubAgent Spawn):** 取消传播机制、子 agent 事件路由到父 MessageBus、stop_event 共享模式需要仔细设计。现有 playground 中的 SubAgentHandle 模式是在 solver 层实现的，迁移到 tool_call 触发模式需要重新设计执行流。
- **Phase 3 (Prompt/Description):** tool description 的 token 预算分配策略（json_schema description vs system prompt usage_guide 的分界线）需要实验验证。

Phases with standard patterns (skip research-phase):
- **Phase 1 (基础设施):** 标准的基类设计 + Protocol 实现，模式清晰
- **Phase 2 (文件操作 Tools):** 从 evomaster EditorTool 拆分提取，逻辑已验证，主要是重构工作
- **Phase 5 (迁移验证):** 标准的迁移 + 集成测试

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | 不引入新依赖，所有技术已在 codebase 验证，结论基于代码库深度分析 |
| Features | HIGH | 基于 Claude Code 系统提示分析 + OpenAI function calling 最佳实践 + 现有代码分析 |
| Architecture | HIGH | 所有组件边界、数据流、LOC 估算均可追溯到具体代码文件 |
| Pitfalls | HIGH | 基于 codebase 分析发现的真实风险，每个 pitfall 有具体代码行号引用 |

**Overall confidence:** HIGH

### Gaps to Address

- **ToolContext 决策:** Tool Protocol 是否增加 ToolContext 参数是 Phase 1 的关键设计决策。当前 Protocol 签名是 `execute(arguments: dict) -> str`，增加参数是破坏性变更。需要评估影响范围（当前只有 EvoToolAdapter 实现 Protocol），在 Phase 1 planning 时确定。
- **SubAgent 事件路由:** 子 agent 的流式事件如何通过父的 MessageBus 发送，需要 Phase 4 planning 时具体设计。当前研究确认了问题但未给出完整方案。
- **Tool Description Token 预算:** description 精简到什么程度需要实验验证（用实际 LLM 调用测试 description 长度对调用准确率的影响）。Phase 3 实施时需要迭代验证。
- **子 agent max_turns 预算:** 子 agent 是否需要从父 agent 的剩余 turns 中扣减，还是独立计数。需要 Phase 4 planning 时确定。

## Sources

### Primary (HIGH confidence)
- 代码库深度分析: `matmaster/tools/tool_registry.py`, `matmaster/core/exp.py`, `matmaster/core/agent.py`, `matmaster/core/context_builder.py`, `matmaster/config/exp.py`
- EvoMaster 工具实现: `evomaster/agent/tools/builtin/bash.py`, `editor.py`, `monitor_job/`
- Session 接口: `evomaster/agent/session/base.py`
- Claude Code 系统提示: [claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts), [tool schemas gist](https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f)
- OpenAI Function Calling Guide: [official docs](https://platform.openai.com/docs/guides/function-calling)

### Secondary (MEDIUM confidence)
- Gorilla 研究 (tool description precision vs invocation accuracy)
- OpenAI Community prompting best practices for tool use
- 现有 SubAgent 模式: `playground/mat_master/core/solvers/step_sub_agent.py`

---
*Research completed: 2026-03-24*
*Ready for roadmap: yes*
