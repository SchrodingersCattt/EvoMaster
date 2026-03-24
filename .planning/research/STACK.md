# Stack Research

**Domain:** Agent 框架内置工具套件 + SubAgent spawn + prompt/description 体系
**Researched:** 2026-03-24
**Confidence:** HIGH (基于已有代码库分析 + 成熟标准库)

## Executive Summary

v1.1 的三个新功能（内置 tool 套件、SubAgent spawn、prompt/description 体系）不需要引入任何新的外部依赖。所有能力都可以通过现有 Python 标准库 + 已有依赖（Pydantic v2, tomllib）实现。这是一个纯架构扩展，不是技术栈扩展。

核心判断依据：
1. 内置工具（Read/Write/Edit/Bash/Glob/Grep）的实际 I/O 操作全部委托给 BaseSession（session-dependent）或标准库（session-free），无需新库
2. SubAgent spawn 是 Exp 层 tool_call -> 创建子 Exp/Kernel -> 返回 result 的编排模式，不需要并发框架（v1.1 SubAgent 是同步阻塞执行）
3. Prompt/description 体系是 TOML 模板 + ContextBuilder 扩展，用已有的 tomllib + Pydantic 即可
4. Jinja2 模板引擎是唯一值得讨论的候选新依赖，但结论是不引入

## Recommended Stack

### 不引入新依赖 -- 完全使用现有技术

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python stdlib `pathlib` | 3.10+ | Glob/path 操作（session-free tools） | 标准库，已在 codebase 广泛使用，PurePosixPath 支持远程路径构造 |
| Python stdlib `fnmatch` | 3.10+ | Glob pattern matching | 标准库，fnmatch.filter() 直接支持 glob 语义 |
| Python stdlib `re` | 3.10+ | Grep 正则匹配 | 标准库，已在 EditorTool._str_replace() 使用 |
| Python stdlib `subprocess` | 3.10+ | 本地 Bash 执行（DevShell session-free 场景） | 标准库，LocalSession 已用 |
| Python stdlib `tomllib` | 3.11+ | TOML prompt 模板加载 | 3.11 内置，已用于 ExpConfig 加载（matmaster/config/loader.py） |
| Pydantic v2 | >=2.0 | Tool params schema, ExpConfig 扩展, prompt 模板配置 | 已是核心依赖，BaseToolParams 模式验证 + model_json_schema() 生成 OpenAI function schema |
| tiktoken | >=0.7.0 | Token 计数（tool description 长度控制、compaction） | 已是核心依赖 |

### Session-Dependent Tool 技术路径

session-dependent tool（Read/Write/Edit/Bash）通过 BaseSession 接口操作远程环境，技术栈不变：

| 操作 | BaseSession 方法 | 已验证可用 | 当前使用者 |
|------|-----------------|-----------|-----------|
| 执行 bash | `exec_bash(command, timeout)` | YES | evomaster BashTool |
| 读文件 | `read_file(path)` / `download(path)` | YES | evomaster EditorTool |
| 写文件 | `write_file(path, content)` / `upload()` | YES | evomaster EditorTool |
| 路径检查 | `path_exists()` / `is_file()` / `is_directory()` | YES | evomaster EditorTool._validate_path() |
| Glob 列举 | `exec_bash("find ... -name '*.py' | sort")` | YES | 通过 bash 组合实现 |
| Grep 搜索 | `exec_bash("grep -rn 'pattern' ...")` | YES | 通过 bash 组合实现 |

**结论：** session-dependent tool 无需扩展 BaseSession API，现有方法完全覆盖。

### Session-Free Tool 技术路径

session-free tool（Glob/Task/SubAgent）不依赖 evomaster session，直接在 matmaster 进程内执行：

| Tool | 实现方式 | 依赖 |
|------|---------|------|
| Glob (local) | `pathlib.Path.glob()` + `fnmatch` | stdlib |
| Grep (local) | `re` 正则 + 文件遍历 | stdlib |
| Task/SubAgent | Exp 层编排（同步创建子 Kernel.run()） | matmaster 自身 |

## Key Architectural Decisions (影响技术选择)

### Decision 1: matmaster 原生 BuiltinTool 基类，不继承 evomaster BaseTool

**问题：** evomaster `BaseTool.execute(session, args_json)` 签名强制依赖 BaseSession + 返回 tuple[str, dict]。matmaster 的 session-free tool 不需要 session，且 matmaster Tool Protocol 的 execute 签名是 `execute(arguments: dict) -> str`。

**方案：** 在 `matmaster/tools/` 下定义 `BuiltinTool` 基类，直接满足现有 `Tool` Protocol，无需 EvoToolAdapter 中间层。

```python
# matmaster/tools/builtin_base.py -- 概念示意
from typing import Any, ClassVar
from pydantic import BaseModel

class BuiltinTool:
    """matmaster 原生内置工具基类。直接满足 Tool Protocol。"""
    name: ClassVar[str]
    params_class: ClassVar[type[BaseModel]]

    @property
    def description(self) -> str:
        return (self.params_class.__doc__ or "").strip()

    @property
    def json_schema(self) -> dict[str, Any]:
        return self.params_class.model_json_schema()

    def execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError
```

**session-dependent variant：** 在 `__init__` 中 bind session 实例，execute 内部使用 self._session。

**与 evomaster MonitorJobTool 的关系：** MonitorJobTool 保留为 evomaster 工具，继续通过 EvoToolAdapter 桥接注册。不迁移。

### Decision 2: 不引入 Jinja2 模板引擎

**候选理由：** system prompt 模板化管理可以用 Jinja2 的条件/循环语法。

**不引入的理由：**
1. ContextBuilder 已有分段组装机制（identity/mode_contract/skills/tools/memory/task），通过 `disabled_sections` 参数控制启用/禁用，覆盖 90% 的 prompt 变化需求
2. Prompt 内部的变量替换（如 workspace 路径、exp 名称）用 Python `str.format_map()` 或 f-string 足够
3. Jinja2 的条件/循环语法对 prompt 来说过于复杂，prompt 的结构变化应该通过 section 组合控制，而非模板内 if/for
4. 引入模板引擎会让 prompt 调试变困难 -- 需要理解模板渲染上下文，与 TOML 文件中的原始 prompt 文本相比增加了间接层
5. 当前 TOML 文件存 prompt 文本段 + Python 代码组装 = 已经足够灵活

### Decision 3: SubAgent 同步执行，不引入 asyncio/concurrent.futures

**理由：**
1. v1.1 scope 是单 SubAgent spawn（一次 tool_call 触发一个子 agent），不是并行多 agent
2. AgentKernel.run() 本身是同步阻塞的（用 threading.Event 做取消），SubAgent 自然也是同步
3. SubAgent 作为 tool call 的一部分执行，`Tool.execute()` 是同步签名 `-> str`
4. SubAgent 的 stop_event 可以共享父 agent 的 stop_event，实现级联取消
5. 未来如果需要并行 SubAgent，可以在 v1.2+ 引入 asyncio，但 v1.1 同步足够

### Decision 4: Tool description 从 Python docstring 迁移到 TOML 配置

**问题：** evomaster 的 BaseTool 把 description 放在 `BaseToolParams.__doc__`（Python docstring）中。这让 prompt 工程和代码实现耦合，修改 tool description 需要改 Python 文件。

**方案：** matmaster 原生 tool 的 description 定义在 TOML 配置中（`matmaster/exps/` 或独立的 `matmaster/prompts/` 目录），运行时加载。

**实现路径：**
- ExpConfig 扩展 `tool_descriptions: dict[str, str]` 字段（key=tool name, value=description override）
- BuiltinTool 提供 `default_description` 属性（代码内 fallback）
- Exp.build_runtime() 注册 tool 时，检查 config 中是否有 description override，有则使用 override

**好处：**
- Prompt 工程师可以独立调整 tool description 而不改代码
- 不同 exp 可以给同一个 tool 不同的 description（如 mat_master vs minimal）
- A/B 测试 description 效果时只需修改 TOML

### Decision 5: 不使用 ripgrep (rg) binary 作为 Grep 后端

**问题：** Claude Code 的 Grep 工具底层使用 ripgrep，性能优秀。matmaster 是否应该依赖 rg？

**不使用的理由：**
1. session-dependent 场景：远程 Docker/SSH 环境不能假设安装了 rg，必须用 POSIX grep
2. session-free 场景（DevShell 本地）：可以用 rg 提升性能，但为了保持统一行为，用 Python re 模块
3. 引入 rg binary 依赖会增加部署复杂度（需要在 Docker 镜像中安装）
4. matmaster 的 Grep 使用频率远低于 Claude Code（科研 agent 不是 coding agent），grep 性能不是瓶颈

## What NOT to Add

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Jinja2 | Prompt 模板不需要条件/循环语法，增加调试复杂度 | `str.format_map()` + ContextBuilder 分段组装 |
| asyncio | v1.1 SubAgent 是同步执行，现有 Kernel 是同步循环 | 直接同步调用子 Kernel.run() |
| celery/dramatiq | SubAgent 不是后台任务，是同步 tool call 内执行 | 直接函数调用 |
| langchain/llamaindex | 框架级依赖会破坏三层架构的清晰边界 | matmaster 自有 Tool Protocol + ToolRegistry |
| pydantic-settings | ExpConfig 已用 Pydantic v2 + TOML 加载，不需要额外 settings 框架 | 现有 config/loader.py |
| ripgrep binary | 不能假设远程环境有 rg，增加部署复杂度 | POSIX grep (session) / Python re (local) |
| tree-sitter | 代码感知搜索是 nice-to-have，v1.1 Grep 只做正则文本搜索 | re 模块 + grep 命令 |
| pytest-asyncio | v1.1 不引入 async，所有新代码保持同步 | 现有 pytest 足够 |

## Integration Points (现有代码的具体扩展点)

### 1. ToolRegistry -- 无需修改

现有 `Tool` Protocol（name/description/json_schema/execute）和 `ToolRegistry`（register/execute/get_tool_definitions）完全满足需求。matmaster 原生 BuiltinTool 只需满足 Protocol 即可直接注册，source tag 用 `"builtin"` 区分。

### 2. ExpConfig -- 需要扩展

```toml
# matmaster/exps/direct.toml -- 扩展后的结构
name = "direct"
mode = "direct"
max_turns = 200
guards = []

developer_instructions = '''
You are Mat Master, ...
'''

[tools]
builtin = ["*"]
mcp = "*"

# 新增：tool description 覆盖（可选）
[tools.descriptions]
execute_bash = "在远程计算环境中执行 bash 命令..."
str_replace_editor = "查看、创建和编辑远程文件..."

# 新增：SubAgent 配置（可选）
[subagent]
enabled = true
max_depth = 2         # 最大嵌套深度
inherit_tools = true  # 子 agent 是否继承父 agent 的 tool 集
```

对应的 Pydantic model 扩展：

```python
# matmaster/config/exp.py 扩展
class SubAgentConfig(BaseModel):
    enabled: bool = False
    max_depth: int = 2
    inherit_tools: bool = True

class ExpToolsConfig(BaseModel):
    builtin: list[str] = Field(default_factory=lambda: ["*"])
    mcp: str = "*"
    descriptions: dict[str, str] = Field(default_factory=dict)  # 新增

class ExpConfig(BaseModel):
    # ... 现有字段 ...
    subagent: SubAgentConfig = Field(default_factory=SubAgentConfig)  # 新增
```

### 3. Exp.build_runtime() -- 需要扩展 _init_builtin_tools()

当前实现（exp.py:224-246）硬编码从 evomaster 导入 BashTool/EditorTool/MonitorJobTool 并通过 EvoToolAdapter 注册。需要：

1. **拆分注册路径：** matmaster 原生 tool（Read/Write/Edit/Bash/Glob/Grep）直接注册，evomaster 工具（MonitorJobTool）仍通过 adapter
2. **Description 注入：** 注册前检查 `self._config.tools.descriptions` 是否有 override
3. **SubAgent tool 注册：** 如果 `self._config.subagent.enabled`，创建 SubAgentTool 并注册。SubAgentTool 需要闭包/factory 绑定创建子 Exp 的逻辑

### 4. ContextBuilder -- 需要扩展

- `_build_tools()` 目前只生成 `"- {name}: {description}"` 格式的列表，对于复杂 tool（如 SubAgent、Bash 安全规则）需要支持更丰富的工具使用指南
- 新增 prompt template 加载机制：`developer_instructions` 字段支持引用外部文本文件（如 `@file:prompts/mat_master_v1.txt`），避免在 TOML 内嵌入大段文本

### 5. PlaygroundContext -- 无需修改

SubAgent 共享父 agent 的 PlaygroundContext（workdir, session, llm_provider）。context 本身 frozen=True，SubAgent 只读。不需要扩展字段。

## Stack Patterns by Tool Category

**Category A: Session-dependent tool（Bash/Read/Write/Edit）**
- 实例化时 bind BaseSession（`__init__(self, session: BaseSession)`）
- execute() 内部调用 session.exec_bash() / session.read_file() 等
- 直接满足 Tool Protocol，无需 EvoToolAdapter

**Category B: Session-free tool（Glob local / Grep local）**
- 不接收 session，直接用 pathlib/re/fnmatch 操作本地文件系统
- 主要用于 DevShell 场景（无远程 session）

**Category C: Hybrid tool（Glob/Grep 双模式）**
- constructor 接收 optional session
- 有 session 时委托 `session.exec_bash()` 在远程执行 find/grep 命令
- 无 session 时用标准库本地执行
- 行为一致性由 matmaster tool 封装层保证

**Category D: Orchestration tool（SubAgent/Task）**
- 不操作文件系统，而是编排子 agent 运行
- 接收创建子 Exp 所需的 context（ExpConfig factory + PlaygroundContext）
- execute() 内部创建子 Exp -> build_runtime -> kernel.run -> 返回结果字符串
- stop_event 级联传递实现取消

**Category E: Legacy evomaster tool（MonitorJobTool）**
- 保持 evomaster BaseTool 继承链不变
- 通过 EvoToolAdapter 桥接到 matmaster Tool Protocol
- 不迁移，不改造

## Version Compatibility

| Package | Current Version | Required Version | Notes |
|---------|-----------------|------------------|-------|
| Python | 3.13 | >=3.11 | tomllib 需要 3.11+，项目已用 3.13，无兼容问题 |
| Pydantic | v2.x | >=2.0 | model_json_schema() 用于 tool params，ConfigDict(extra="ignore") 用于 TOML 前向兼容 |
| OpenAI SDK | (已安装) | 不变 | SubAgent 复用现有 LLMProvider，不引入新调用模式 |
| tiktoken | >=0.7.0 | 不变 | 不变 |

## Installation

```bash
# 无新依赖需要安装
# v1.1 完全使用现有依赖栈
uv sync
```

## Sources

- 代码库分析：`matmaster/tools/tool_registry.py` -- Tool Protocol 定义（name/description/json_schema/execute 签名）
- 代码库分析：`evomaster/agent/session/base.py` -- BaseSession API surface（exec_bash/read_file/write_file/path_exists/is_file/is_directory）
- 代码库分析：`evomaster/agent/tools/builtin/` -- 现有 Bash/Editor/Finish/MonitorJob 实现模式和 description 位置
- 代码库分析：`matmaster/tools/evomaster_tool_adapter.py` -- EvoToolAdapter 桥接模式
- 代码库分析：`matmaster/core/exp.py` -- Exp.build_runtime() 的 _init_builtin_tools() 扩展点
- 代码库分析：`matmaster/core/context_builder.py` -- ContextBuilder section 组装机制
- 代码库分析：`matmaster/config/exp.py` -- ExpConfig/ExpToolsConfig 扩展结构
- Python 标准库文档：pathlib.Path.glob(), fnmatch.filter(), re, tomllib -- HIGH confidence

---
*Stack research for: matmaster v1.1 Agent 外围能力构建*
*Researched: 2026-03-24*
