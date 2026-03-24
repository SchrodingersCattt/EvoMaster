# Architecture Patterns

**Domain:** matmaster v1.1 -- 内置 Tool 套件 + SubAgent Spawn + Prompt/Description 体系
**Researched:** 2026-03-24
**Confidence:** HIGH (基于现有代码库深度分析，所有结论均可追溯到具体文件)

## Recommended Architecture

v1.1 的核心设计原则：在不改动三层契约边界的前提下，扩展 Exp 层的能力装配范围。新增组件全部落在 `matmaster/tools/` 和 `matmaster/core/` 内部，不引入新的顶级目录。

### 架构总览

```
matmaster/
├── tools/
│   ├── tool_registry.py          # 现有，不改
│   ├── evomaster_tool_adapter.py  # 现有，不改（保留兼容）
│   ├── builtin/                   # 新增：原生内置 tool 套件
│   │   ├── __init__.py
│   │   ├── base.py                # BuiltinTool 基类（实现 Tool Protocol）
│   │   ├── bash.py                # BashTool（session-dependent）
│   │   ├── file_read.py           # FileReadTool（session-dependent）
│   │   ├── file_write.py          # FileWriteTool（session-dependent）
│   │   ├── file_edit.py           # FileEditTool（session-dependent，diff-based）
│   │   ├── glob_search.py         # GlobSearchTool（session-dependent）
│   │   ├── grep_search.py         # GrepSearchTool（session-dependent）
│   │   ├── list_dir.py            # ListDirTool（session-dependent）
│   │   ├── monitor_job.py         # MonitorJobTool（session-dependent，科研特有）
│   │   └── think.py               # ThinkTool（session-free）
│   └── sub_agent.py               # 新增：SubAgentTool（spawn 机制的 tool 实现）
├── core/
│   ├── exp.py                     # 修改：扩展 _init_builtin_tools + 新增 spawn_sub_agent
│   ├── context_builder.py         # 修改：支持 prompt 模板化
│   └── ...                        # 其余不改
├── config/
│   ├── exp.py                     # 修改：ExpToolsConfig 扩展 + PromptConfig
│   └── ...
├── exps/
│   ├── direct.toml                # 修改：增加 prompt 模板配置
│   └── ...
└── prompts/                       # 新增：prompt 模板目录
    ├── __init__.py
    ├── loader.py                  # PromptTemplateLoader
    ├── system/                    # system prompt 模板
    │   └── mat_master.md
    └── tool_descriptions/         # tool description 模板
        └── ...
```

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `tools/builtin/base.py` | BuiltinTool 基类：持有 session 引用，实现 Tool Protocol，提供参数验证 | Tool Protocol → ToolRegistry |
| `tools/builtin/*.py` | 各具体 tool：参数 schema 定义 + 执行逻辑 | BaseSession (session-dependent) 或无依赖 (session-free) |
| `tools/sub_agent.py` | SubAgentTool：spawn 子 agent 的 tool 实现，转发 tool_call 到 Exp.spawn_sub_agent | Exp (spawn), PlaygroundContext (共享 workspace) |
| `core/exp.py` | 扩展装配：注册原生 builtin tools + 提供 spawn_sub_agent 方法 | ToolRegistry, BuiltinTools, AgentKernel |
| `prompts/loader.py` | 加载 .md 模板并做变量替换 | ExpConfig, ContextBuilder |
| `config/exp.py` | ExpConfig 扩展：builtin 工具精细选择 + prompt 模板引用 | TOML 文件 → Exp |

### Data Flow

#### 1. Builtin Tool 注册流程

```
ExpConfig.tools.builtin = ["bash", "file_read", "file_write", ...]
           │
           ▼
Exp.build_runtime(ctx)
           │
           ├─ _init_builtin_tools(ctx, registry)
           │     │
           │     ├─ 遍历 config 中指定的 tool 名称
           │     ├─ 实例化对应 BuiltinTool（注入 ctx.session）
           │     └─ registry.register(tool, source="builtin")
           │
           └─ ContextBuilder.build(ctx, registry, ...)
                 │
                 └─ 使用 registry 中的 tool descriptions 构建 system prompt
```

#### 2. SubAgent Spawn 流程

```
AgentKernel.run() 执行循环
     │
     ├─ LLM 返回 tool_call: name="spawn_sub_agent"
     │   arguments: {task: "...", exp_name: "researcher", tools: [...]}
     │
     ├─ ToolRegistry.execute("spawn_sub_agent", arguments)
     │     │
     │     └─ SubAgentTool.execute(arguments)
     │           │
     │           ├─ 加载子 exp 的 ExpConfig（从 exps/{exp_name}.toml）
     │           ├─ 创建子 Exp(child_config)
     │           ├─ 子 Exp.run(same_ctx, task)  ← 共享 PlaygroundContext
     │           │     │
     │           │     ├─ 子 ToolRegistry（可能是父 tools 的子集）
     │           │     ├─ 子 system_prompt（子 exp 自己的 identity）
     │           │     └─ 子 AgentKernel.run() → KernelRunResult
     │           │
     │           └─ 返回 result.event.final_content 作为 tool result
     │
     └─ ToolMessage(content=sub_agent_result) 追加到 messages
```

#### 3. Prompt 模板流程

```
ExpConfig
  ├─ prompt_template: "mat_master"     # 引用 prompts/system/mat_master.md
  ├─ developer_instructions: "..."     # 直接内联（优先级高于模板）
  └─ tool_descriptions: "default"      # 引用 prompts/tool_descriptions/
        │
        ▼
Exp.build_runtime()
  ├─ PromptTemplateLoader.load("mat_master", variables={...})
  │     └─ 读取 .md 文件 → Jinja2/简单 ${var} 替换
  └─ ContextBuilder.build(..., identity=rendered_prompt)
```

## New Components Detail

### 1. BuiltinTool 基类 (`tools/builtin/base.py`)

**设计要点：**

- 直接实现 Tool Protocol（name, description, json_schema, execute），不走 EvoToolAdapter
- session-dependent tools 在 `__init__` 接收 `BaseSession` 引用
- session-free tools（如 ThinkTool）不接收 session
- 参数验证用 Pydantic BaseModel 生成 json_schema（与 evomaster 的 BaseToolParams 类似但自包含）
- description 从类属性或外部模板文件加载

```python
# tools/builtin/base.py 概念设计

from typing import Any
from pydantic import BaseModel

class BuiltinTool:
    """matmaster 原生 builtin tool 基类。

    直接满足 Tool Protocol，无需 adapter。
    子类定义 _name, _description, _params_class, _execute。
    """

    _name: str
    _description: str
    _params_class: type[BaseModel]

    def __init__(self, session: Any | None = None) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._params_class.model_json_schema()

    def execute(self, arguments: dict[str, Any]) -> str:
        params = self._params_class.model_validate(arguments)
        return self._execute(params)

    def _execute(self, params: BaseModel) -> str:
        raise NotImplementedError
```

**与 EvoToolAdapter 的关系：** EvoToolAdapter 保留不动。新 builtin tools 逐步替代 evomaster builtin tools。迁移期间两者可以共存（ToolRegistry 的 same-name override 机制保证后注册的覆盖先注册的）。

### 2. 具体 Builtin Tools

| Tool | Session? | 对应 evomaster | 关键变化 |
|------|----------|---------------|---------|
| BashTool | Yes | evomaster BashTool | 去掉 proxy clear hack，保留 safety check，简化 is_input |
| FileReadTool | Yes | EditorTool(view) | 拆分出独立 tool，支持 offset/limit 参数 |
| FileWriteTool | Yes | EditorTool(create) | 拆分出独立 tool，write 语义更清晰 |
| FileEditTool | Yes | EditorTool(str_replace) | diff-based 编辑，独立 tool |
| GlobSearchTool | Yes | 无（通过 bash） | 新增，文件名模式搜索 |
| GrepSearchTool | Yes | 无（通过 bash） | 新增，内容搜索 |
| ListDirTool | Yes | EditorTool(view on dir) | 拆分出独立 tool |
| MonitorJobTool | Yes | evomaster MonitorJobTool | 保留，科研场景特有 |
| ThinkTool | No | 无 | 新增，session-free，agent 内部推理用 |

**设计决策：拆分 EditorTool 为独立 tools。** evomaster 的 EditorTool 是一个巨大的 multi-command tool（view/create/str_replace/insert/undo_edit），command 参数做分发。拆分的理由：
- LLM 对单一职责 tool 的调用准确率更高
- JSON schema 更精确（每个 tool 只有自己需要的参数）
- description 更聚焦，减少 prompt token
- 与 Claude Code 的 tool 设计理念一致

### 3. SubAgentTool (`tools/sub_agent.py`)

**核心设计约束：**

1. SubAgent 是一个普通 tool_call，不改 kernel 执行循环
2. 子 agent 共享父 agent 的 PlaygroundContext（同一个 workdir、session）
3. 子 agent 有独立的 ExpConfig（独立 system prompt、tool set、max_turns）
4. 子 agent 的执行是同步阻塞的（在父 agent 的 tool execution 阶段完成）
5. 子 agent 的结果作为 tool result 字符串返回给父 agent

**SubAgentTool 需要的依赖注入：**

SubAgentTool 是唯一一个需要访问 Exp 层能力的 tool（需要创建子 Exp 并执行）。这打破了 tool 只依赖 session 的模式。解决方案：

```python
# tools/sub_agent.py 概念设计

class SubAgentTool:
    """Spawn a sub-agent to handle a specific task.

    实现 Tool Protocol。需要注入 spawn_fn 而非直接依赖 Exp。
    """

    _name = "spawn_sub_agent"

    def __init__(self, spawn_fn: Callable[[str, str, list[str] | None], str]) -> None:
        """
        spawn_fn 签名：(task, exp_name, tool_names) -> result_str
        由 Exp 在 build_runtime 时注入。
        """
        self._spawn_fn = spawn_fn

    def execute(self, arguments: dict[str, Any]) -> str:
        task = arguments["task"]
        exp_name = arguments.get("exp_name", "direct")
        tool_names = arguments.get("tools")
        return self._spawn_fn(task, exp_name, tool_names)
```

**Exp 侧的 spawn 实现：**

```python
# core/exp.py 新增方法

def _spawn_sub_agent(
    self,
    ctx: PlaygroundContext,
    task: str,
    exp_name: str,
    tool_names: list[str] | None = None,
) -> str:
    """创建并运行子 agent，返回结果字符串。"""
    from matmaster.config.loader import load_exp_config

    child_config = load_exp_config(exp_name)
    # 可选：覆盖 child_config 的 tool list
    if tool_names:
        child_config = child_config.model_copy(
            update={"tools": ExpToolsConfig(builtin=tool_names)}
        )

    child_exp = Exp(child_config)
    result = child_exp.run(ctx, task)  # 共享 ctx
    return result.final_content or f"SubAgent completed with status: {result.status}"
```

**递归保护：** SubAgentTool 的 spawn_fn 内部需要深度计数器。父 agent 的 spawn 是 depth=0，子 agent spawn 的子子 agent 是 depth=1，超过 max_depth（默认 3）时拒绝 spawn。

```python
# Exp 的 spawn wrapper 中：
def _make_spawn_fn(self, ctx, current_depth=0, max_depth=3):
    def spawn_fn(task, exp_name, tool_names):
        if current_depth >= max_depth:
            return "Error: Maximum sub-agent nesting depth reached."
        child_exp = ...
        # 子 exp 的 spawn_fn depth = current_depth + 1
        ...
    return spawn_fn
```

### 4. Prompt/Description 体系 (`prompts/`)

**设计要点：**

- System prompt 从 ExpConfig.developer_instructions 内联字符串 → 可选引用外部 .md 模板
- Tool description 从类属性硬编码 → 可选引用外部模板文件
- 模板替换用简单 `${variable}` 语法（不引入 Jinja2 依赖），足够覆盖需求
- ContextBuilder 不需要大改，只需在 identity section 支持从模板加载

**ExpConfig 扩展：**

```toml
# exps/direct.toml 扩展后
name = "direct"
mode = "direct"
max_turns = 200

[prompt]
template = "mat_master"              # 引用 prompts/system/mat_master.md
variables = { agent_name = "Mat Master" }

[tools]
builtin = ["bash", "file_read", "file_write", "file_edit", "grep_search", "glob_search", "list_dir", "think"]
mcp = "*"

[tools.sub_agent]
enabled = true
max_depth = 3
allowed_exps = ["direct", "researcher"]
```

**PromptTemplateLoader：**

```python
# prompts/loader.py

class PromptTemplateLoader:
    """从 prompts/ 目录加载 .md 模板并做变量替换。"""

    def __init__(self, prompts_dir: Path | None = None):
        self._dir = prompts_dir or Path(__file__).parent

    def load_system_prompt(self, name: str, variables: dict[str, str] | None = None) -> str:
        path = self._dir / "system" / f"{name}.md"
        content = path.read_text()
        for key, val in (variables or {}).items():
            content = content.replace(f"${{{key}}}", val)
        return content

    def load_tool_description(self, tool_name: str) -> str | None:
        path = self._dir / "tool_descriptions" / f"{tool_name}.md"
        if path.exists():
            return path.read_text().strip()
        return None
```

## Patterns to Follow

### Pattern 1: Tool Protocol 直接实现（不走 Adapter）

**What:** 新 builtin tools 直接实现 Tool Protocol 的 4 个属性/方法，无需 EvoToolAdapter 中转。

**When:** 所有新增的 matmaster 原生 tool。

**Why:** EvoToolAdapter 存在的意义是桥接 evomaster 的 BaseTool 接口（execute(session, args_json) → tuple）到 matmaster 的 Tool Protocol（execute(arguments) → str）。原生 tool 不需要这层转换。

**Example:**
```python
class ThinkTool:
    """Session-free tool: agent 内部推理，不产生外部副作用。"""

    @property
    def name(self) -> str:
        return "think"

    @property
    def description(self) -> str:
        return (
            "Use this tool to think through a problem step by step. "
            "The content is not shown to the user. Use it when you need "
            "to reason about complex decisions before acting."
        )

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Your internal reasoning."
                }
            },
            "required": ["thought"],
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        return "Thought recorded."  # 不做实际操作
```

### Pattern 2: spawn_fn 注入（解耦 tool 与 Exp 层）

**What:** SubAgentTool 不直接依赖 Exp，通过 callable 注入 spawn 能力。

**When:** 任何 tool 需要访问 Exp 层能力时。

**Why:** Tool Protocol 的 execute 签名是 `(arguments: dict) -> str`，tool 不应该知道 Exp 的存在。通过闭包注入，tool 只持有一个 callable，Exp 在 build_runtime 时绑定。

### Pattern 3: Exp 配置驱动的 tool 选择

**What:** ExpConfig.tools.builtin 从 `["*"]` 通配扩展为支持具名列表。

**When:** 不同 exp（或子 agent）需要不同的 tool 集。

**Why:** 子 agent 可能只需要 bash + file_read（只读场景），不应该暴露 file_write。精细控制 tool 集可以减少 prompt token 和降低 LLM 误操作风险。

```toml
# exps/researcher.toml -- 只读分析子 agent
[tools]
builtin = ["bash", "file_read", "grep_search", "list_dir", "think"]
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Tool 内部持有 Exp 引用

**What:** SubAgentTool 直接 import 并实例化 Exp。

**Why bad:** 创建 tools/ → core/ 的循环依赖。tool 应该是叶子节点，不应该反向依赖装配层。

**Instead:** 通过 spawn_fn callable 注入，Exp 在 build_runtime 时构造闭包。

### Anti-Pattern 2: 保留 multi-command EditorTool

**What:** 继续使用 evomaster 的 EditorTool（view/create/str_replace/insert/undo_edit 五合一）。

**Why bad:** LLM 需要理解 command 参数的分发逻辑，参数 schema 复杂（不同 command 需要不同参数组合），description 冗长。

**Instead:** 拆分为独立的 FileReadTool、FileWriteTool、FileEditTool、ListDirTool。

### Anti-Pattern 3: SubAgent 用独立线程执行

**What:** SubAgentTool.execute 启动新线程运行子 agent。

**Why bad:** 父 agent 的 kernel 循环是同步的，tool execute 必须返回 str。引入线程增加复杂度（需要 join、异常传播、超时处理），但没有并发收益（父 agent 必须等 tool result 才能继续）。

**Instead:** 同步执行。子 agent 的 Exp.run() 在当前线程完成。

### Anti-Pattern 4: 子 agent 创建新的 PlaygroundContext

**What:** 为子 agent 构建新的 ctx（新 workdir、新 session）。

**Why bad:** 子 agent 需要访问父 agent 的工作成果（文件、环境变量等）。新 ctx 意味着隔离的 workspace，失去了 "共享 workspace" 的核心设计意图。

**Instead:** 传递相同的 PlaygroundContext。子 agent 操作同一个 workdir 和 session。

## Integration Points (现有代码需要修改的位置)

### 修改量评估

| File | Change Type | Scope | Description |
|------|-------------|-------|-------------|
| `core/exp.py` | Modify | Medium | `_init_builtin_tools` 替换为原生 tool 注册；新增 `_make_spawn_fn` + SubAgentTool 注册 |
| `config/exp.py` | Modify | Small | ExpToolsConfig 扩展（具名 builtin 列表 + sub_agent 配置 + prompt 模板引用） |
| `core/context_builder.py` | Modify | Small | identity section 支持从 PromptTemplateLoader 加载 |
| `tools/tool_registry.py` | No change | - | 现有接口完全够用 |
| `tools/evomaster_tool_adapter.py` | No change | - | 保留用于兼容 |
| `core/agent.py` | No change | - | kernel 不需要知道 SubAgent |
| `types/runtime.py` | No change | - | AgentRuntimeSpec 现有字段够用 |
| `types/context.py` | No change | - | PlaygroundContext 不变 |

### 新增文件

| File | LOC estimate | Description |
|------|-------------|-------------|
| `tools/builtin/__init__.py` | 20 | export + BUILTIN_TOOLS registry dict |
| `tools/builtin/base.py` | 60 | BuiltinTool 基类 |
| `tools/builtin/bash.py` | 80 | BashTool |
| `tools/builtin/file_read.py` | 60 | FileReadTool |
| `tools/builtin/file_write.py` | 50 | FileWriteTool |
| `tools/builtin/file_edit.py` | 80 | FileEditTool（str_replace 语义） |
| `tools/builtin/glob_search.py` | 50 | GlobSearchTool |
| `tools/builtin/grep_search.py` | 60 | GrepSearchTool |
| `tools/builtin/list_dir.py` | 40 | ListDirTool |
| `tools/builtin/monitor_job.py` | 30 | MonitorJobTool（thin wrapper） |
| `tools/builtin/think.py` | 30 | ThinkTool |
| `tools/sub_agent.py` | 80 | SubAgentTool |
| `prompts/__init__.py` | 5 | package init |
| `prompts/loader.py` | 60 | PromptTemplateLoader |
| `prompts/system/mat_master.md` | 50 | default system prompt 模板 |

**预估新增：~750 LOC**

## Suggested Build Order

基于依赖关系的构建顺序：

### Phase 1: BuiltinTool 基础设施 + 第一批 tools

**Prerequisites:** 无
**Deliverable:** `tools/builtin/` 基类 + ThinkTool + BashTool + 测试
**Rationale:** 先建立 BuiltinTool 基类，用最简单的 ThinkTool（session-free）验证 Protocol 满足，再用 BashTool（session-dependent）验证 session 注入。

1. `tools/builtin/base.py` -- BuiltinTool 基类
2. `tools/builtin/think.py` -- session-free tool（验证基类设计）
3. `tools/builtin/bash.py` -- session-dependent tool（验证 session 注入）
4. `config/exp.py` -- ExpToolsConfig 扩展为具名列表
5. `core/exp.py` -- `_init_builtin_tools` 替换为原生 tool 注册

### Phase 2: 文件操作 tools

**Prerequisites:** Phase 1（基类已就绪）
**Deliverable:** file_read, file_write, file_edit, list_dir, glob_search, grep_search + 测试

6. `tools/builtin/file_read.py`
7. `tools/builtin/file_write.py`
8. `tools/builtin/file_edit.py`
9. `tools/builtin/list_dir.py`
10. `tools/builtin/glob_search.py`
11. `tools/builtin/grep_search.py`

### Phase 3: Prompt/Description 体系

**Prerequisites:** Phase 1（tool description 改进需要先有 tools）
**Deliverable:** `prompts/` 模块 + ExpConfig prompt 配置 + ContextBuilder 集成

12. `prompts/loader.py` -- PromptTemplateLoader
13. `prompts/system/mat_master.md` -- default system prompt
14. `config/exp.py` -- PromptConfig 新增
15. `core/context_builder.py` -- 支持模板加载

### Phase 4: SubAgent Spawn

**Prerequisites:** Phase 1-3（子 agent 需要完整的 tool + prompt 体系）
**Deliverable:** SubAgentTool + Exp.spawn + 递归保护 + 端到端测试

16. `tools/sub_agent.py` -- SubAgentTool
17. `core/exp.py` -- `_make_spawn_fn` + 递归深度保护
18. `config/exp.py` -- sub_agent 配置段
19. 子 exp TOML 定义（researcher.toml 等）

### Phase 5: MonitorJobTool 迁移 + 集成验证

**Prerequisites:** Phase 1-4
**Deliverable:** MonitorJobTool 原生化 + DevShell 端到端验证

20. `tools/builtin/monitor_job.py` -- 从 evomaster 迁移
21. DevShell 集成测试

**Phase ordering rationale:**
- Phase 1 先行因为所有后续 phase 都依赖 BuiltinTool 基类和 Exp 中的注册机制
- Phase 2 在 Phase 3 之前因为 prompt 模板需要引用具体 tool 名称
- Phase 3 在 Phase 4 之前因为 SubAgent 需要独立的 system prompt（子 exp 的 identity）
- Phase 4 最后因为它是最复杂的，依赖前面所有基础设施
- Phase 5 是收尾验证，确保整体可用

## Scalability Considerations

| Concern | 当前 (5 tools) | 中期 (15 tools) | 长期 (30+ tools) |
|---------|---------------|-----------------|-----------------|
| Tool 注册 | 循环遍历列表 | 同上，性能无问题 | 考虑 lazy import（按需加载 tool 类） |
| SubAgent depth | max_depth=3 | 同上 | 需要 token 预算管理（子 agent 消耗的 token 算入父 agent） |
| Prompt 模板 | 单文件 .md | 同上 | 考虑模板继承/组合机制 |
| Tool description token | ~1K tokens | ~3K tokens | 按需 tool 选择（根据 task 动态筛选 tool subset） |

## Sources

- 现有代码库分析：`matmaster/tools/tool_registry.py`、`matmaster/core/exp.py`、`matmaster/core/agent.py`
- evomaster builtin tools：`evomaster/agent/tools/builtin/bash.py`、`editor.py`、`monitor_job/`
- evomaster session 接口：`evomaster/agent/session/base.py`
- Claude Code tool 设计理念（已内化于 CLAUDE.md 中的 tool 使用指南）
- 项目 CLAUDE.md 中的架构文档
