# Exp 配置提取与目录化设计

Date: 2026-03-24
Status: Draft
Author: Kealdoom + Claude

## Problem

`src/services/agent_run_service.py` Stage 4 手工拼装了一个 hardcoded dict 作为 Exp 配置：

```python
exp_config = {
    "name": "direct",
    "tools": {"builtin": ["*"]},
    "guards": [],
    "termination": {"max_turns": 100},
    "prompt": {},
    "context": {},
    "skills": pg_ctx.run_meta.get("skill_config", {}),
    "mcp": pg_ctx.run_meta.get("mcp_config", {}),
}
```

问题：

1. `Exp.__init__` 接收 `dict[str, Any]`，无类型安全
2. `matmaster/config/exp.py` 中已有 `ExpConfig` 类型化模型和 `load_exp_config()` loader，但从未被使用
3. `config.yaml` 的 `agents.general` 段有丰富配置（context、compaction、system_prompt_file 等），但 Stage 4 完全无视
4. `termination`、`prompt`、`context` 等字段写入了 dict 却从未被 Exp 消费
5. system prompt 存放在独立文件 `prompts/mat_master_system_prompt.txt`，与 exp 定义分离

根因：缺少一个专属的 exp 定义文件格式和加载路径，导致 service 层用 hardcoded dict 充当配置源。

## Goal

1. 引入 `matmaster/exps/` 目录，每个 toml 文件定义一种 Exp（类似 Claude Code 的 `.claude/agents/` 模式）
2. `ExpConfig` 类型化模型对齐 toml schema，移除无用字段，新增 `developer_instructions`
3. `Exp.__init__` 改为接收 `ExpConfig`，获得类型安全
4. `skills` 和 `mcp` 从 ExpConfig 中移除，改为 `build_runtime()` 运行时注入参数
5. system prompt 内联到 toml 的 `developer_instructions` 字段，废弃独立 prompt 文件
6. `agent_run_service.py` Stage 4 通过 `load_exp_config(mode)` 加载配置，删除 hardcoded dict

## Non-Goals

- 不在本设计中处理 compaction 配置（其他进程负责）；改造后 `Exp` 内部 compaction 相关代码暂时保持不动，从 `PlaygroundContext` 或现有路径获取，待 compaction 进程完成后统一收口
- 不引入多 agent profile / Registry 抽象
- 不修改 `configs/mat_master/config.yaml` 的 `agents` 段（暂时保留）
- 不修改前端协议（`mode` 参数语义不变）
- 不处理 `mcp_config.*.json` 的加载路径
- 不引入 guard name → Guard 实例的解析机制；当前 guards 始终为空列表，guard factory 待有实际 guard 需求时再设计

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 定义文件位置 | `matmaster/exps/` 包内目录 | exp 定义是代码级关注点，跟着包版本化，不是部署配置 |
| 文件格式 | toml | 结构清晰，和 Codex agents 模式一致 |
| system prompt | `developer_instructions` 字段内联 | exp 文件自包含，不依赖外部 prompt 文件 |
| 多 agent 支持 | 不需要 | Exp 天然支持组合，不在配置层再做多 agent 抽象 |
| skills/mcp 来源 | 运行时注入 `build_runtime()` 参数 | 这两个是运行时上下文，不属于 exp 静态定义 |
| exp name 来源 | 前端 `mode` 参数 | `mode` 即 exp 类型选择器，文件名即 exp name |
| 未知 mode 行为 | fail-fast FileNotFoundError | 与 LLM 设计中未知 route key 报错策略一致 |
| compaction | 排除在外 | 其他进程正在负责 compaction 改造 |
| developer_instructions 与 ContextBuilder | 作为 `identity` 参数传入 ContextBuilder | ContextBuilder 继续负责拼接 tools/skills/mode 等 section，`developer_instructions` 只替代原来的 identity 数据源 |
| guards 类型 | `list[str]`，当前只允许空列表 | Guard Protocol 实例化逻辑待有实际 guard 需求时再引入 |
| name 与 mode | 保留两个字段 | `name` 是 exp 文件标识（不可变），`mode` 是传递给 AgentRuntimeSpec 的运行模式标签（未来可能不同于文件名） |
| env var 展开 | 排除 `developer_instructions` 字段 | 避免 prompt 文本中的 `${...}` 模式被误展开 |
| max_turns | toml 写 200，ExpConfig 默认值 100 | toml 值沿用 `config.yaml` 的 `agents.general.max_turns: 200`；ExpConfig 默认值 100 仅作 fallback，正常路径由 toml 覆盖 |

## Architecture

### Before

```
agent_run_service.py (Stage 4)
    ↓
    手工拼装 hardcoded dict
    ├── "name": "direct"
    ├── "tools": {"builtin": ["*"]}
    ├── "guards": []
    ├── "termination": {"max_turns": 100}  ← 未被消费
    ├── "prompt": {}                        ← 未被消费
    ├── "context": {}                       ← 未被消费
    ├── "skills": from run_meta
    └── "mcp": from run_meta
    ↓
    Exp(dict[str, Any])
    ↓
    build_runtime(pg_ctx, bus)
        ↓ system prompt 从独立文件读取
    AgentRuntime
```

### After

```
agent_run_service.py (Stage 4)
    ↓ mode (e.g. "direct")
load_exp_config("direct")
    ↓ 扫描 matmaster/exps/direct.toml
ExpConfig (typed)
    ↓
Exp(ExpConfig)
    ↓
build_runtime(pg_ctx, bus, skills=..., mcp=...)
    ↓ developer_instructions 直接来自 ExpConfig
AgentRuntime
```

## Core Concepts

### 1. Exp 定义文件

每个 toml 文件是一个完整的 Exp 定义，位于 `matmaster/exps/` 目录。文件名即 exp name。

设计参考：
- Claude Code: `.claude/agents/{name}.md`（YAML frontmatter + markdown body）
- Codex: `.codex/agents/{name}.toml`（toml + `developer_instructions`）

本设计采用 Codex 风格的 toml 格式。

### 2. ExpConfig

Pydantic 类型化模型，与 toml schema 一一对应。只包含 exp 自身的静态定义，不包含运行时上下文（skills、mcp）和其他模块职责（compaction、LLM）。

### 3. 运行时注入

`skills` 和 `mcp` 是运行时才确定的上下文（前端传入或按环境加载），通过 `build_runtime()` 参数注入，不写入 toml。

## Configuration Schema

### `matmaster/exps/direct.toml`

```toml
name = "direct"
mode = "direct"
max_turns = 200
guards = []

# developer_instructions 必须放在所有 [table] 之前，
# 否则 TOML 解析器会将其归入上一个 table。
developer_instructions = '''
You are Mat Master, an autonomous agent for materials science
and computational materials.

（ContextBuilder identity section 的内容，见下方说明）
'''

[tools]
builtin = ["*"]
mcp = "*"
```

设计要点：

- `name` 是 exp 唯一标识，与文件名一致
- `mode` 是传递给 AgentRuntimeSpec 的运行模式标签（当前与 name 相同，未来 exp 可能定义不同的 mode）
- `guards` 是顶层扁平列表（不是 `[guards]` table），当前始终为空
- `developer_instructions` 是 ContextBuilder 的 `identity` 参数，只包含 agent 身份和领域知识描述，**不包含**工具列表、模式契约、技能描述等——这些由 ContextBuilder 的其他 section（mode_contract、tools、skills）自动生成
- TOML 裸 key 必须放在所有 `[table]` section 之前，否则会被解析为最近一个 table 的子键
- 不包含 `skills`、`mcp`、`compaction`、LLM 配置

关于 `developer_instructions` 的内容来源：

- 旧架构使用 `playground/mat_master/prompts/mat_master_system_prompt.txt`（含 `{{MAT_*}}` 占位符），由 `build_prompt.py` 渲染
- 重构后的新架构使用 `ContextBuilder`，prompt 由固定 section 拼接：identity → mode_contract → skills → tools → memory → task
- `developer_instructions` 只需包含 identity section 的文本（agent 身份、领域专长、行为准则），不需要包含工具列表、模式契约等动态内容
- 旧模板中与工具/模式/约束相关的 `{{MAT_*}}` 占位符逻辑已由 ContextBuilder 的对应 section 接管，不需要迁移到 toml

## Detailed Changes

### 1. `matmaster/config/exp.py`

改造 `ExpConfig`：

```python
from pydantic import BaseModel, Field, ConfigDict


class ExpToolsConfig(BaseModel):
    builtin: list[str] = Field(default_factory=lambda: ["*"])
    mcp: str = "*"


class ExpConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "direct"
    mode: str = "direct"
    max_turns: int = 100
    tools: ExpToolsConfig = Field(default_factory=ExpToolsConfig)
    guards: list[str] = Field(default_factory=list)
    developer_instructions: str = ""
```

与当前版本的差异：

| 字段 | 当前 | 改造后 |
|------|------|--------|
| `guards` | `list[str]` | 保持 |
| `skills` | `dict[str, Any]` | 移除 — 运行时注入 |
| `mcp` | `dict[str, Any]` | 移除 — 运行时注入 |
| `compaction` | `dict[str, Any]` | 移除 — 其他进程负责 |
| `developer_instructions` | 不存在 | 新增 — 取代外部 prompt 文件 |

### 2. `matmaster/config/loader.py`

`load_exp_config()` 签名完全替换：

```python
import tomllib
from pathlib import Path


def load_exp_config(
    name: str,
    *,
    exps_dir: Path | None = None,
) -> ExpConfig:
    """按名称加载 matmaster/exps/{name}.toml 为 ExpConfig。"""
    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"

    toml_path = exps_dir / f"{name}.toml"
    if not toml_path.exists():
        raise FileNotFoundError(
            f"Exp definition not found: {toml_path}, "
            f"available: {[p.stem for p in exps_dir.glob('*.toml')]}"
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    # 保留 developer_instructions 原文，避免 ${...} 被误展开
    dev_instr = raw.pop("developer_instructions", "")
    raw = _expand_env_vars(raw)
    raw["developer_instructions"] = dev_instr
    return ExpConfig.model_validate(raw)
```

设计要点：

- 默认从包内 `matmaster/exps/` 查找
- `exps_dir` 参数允许测试时覆盖
- 未知 name → `FileNotFoundError` 且消息包含可用列表
- 复用现有 `_expand_env_vars()`，但排除 `developer_instructions` 字段（避免 prompt 文本中 `${...}` 被误展开）
- 旧的 `load_exp_config(source, agent_name, runtime)` 签名完全替换，不做兼容

### 3. `matmaster/core/exp.py`

`__init__` 签名变更：

```python
# 旧
def __init__(self, config: dict[str, Any]) -> None:
    self._config = config

# 新
def __init__(self, config: ExpConfig) -> None:
    self._config = config
```

`assemble()` 改造：

```python
def assemble(self, ctx: PlaygroundContext) -> AgentRuntimeSpec:
    # 注意：AgentRuntimeSpec.guards 类型仍为 list[Guard]，
    # 当前 guards 始终为空列表故不触发类型校验。
    # AgentRuntimeSpec.guards 类型变更将在引入 guard factory 时一并处理。
    return AgentRuntimeSpec(
        llm_provider=ctx.llm_provider,
        max_turns=self._config.max_turns,
        guards=self._config.guards,
        mode=self._config.mode,
        meta={},
    )
```

`build_runtime()` 改造：

```python
def build_runtime(
    self,
    ctx: PlaygroundContext,
    *,
    bus: MessageBus | None = None,
    skills: dict[str, Any] | None = None,
    mcp: dict[str, Any] | None = None,
) -> AgentRuntime:
    spec = self.assemble(ctx)

    # 工具注册 — 先注册所有工具，再构建 system prompt
    # 顺序很重要：ContextBuilder 需要完整的工具表来生成 tools section
    tool_registry = ToolRegistry()
    if "*" in self._config.tools.builtin:
        self._init_builtin_tools(tool_registry, ctx)

    # skills/mcp — 从运行时参数初始化（必须在 build system_prompt 之前）
    # 签名保留 ctx 参数（未来实现可能需要 ctx.session 等上下文）
    if skills:
        self._init_skill_tools(ctx, tool_registry, skills)
    if mcp:
        self._init_mcp_tools(ctx, tool_registry, mcp)

    # system prompt — developer_instructions 作为 identity 传入 ContextBuilder
    # ContextBuilder 按固定 section 顺序拼接：identity → mode_contract → skills → tools
    # 注意：原代码从 config dict 读取 "identity" key，现改为 ExpConfig.developer_instructions
    builder = ContextBuilder()
    system_prompt = builder.build(
        ctx,
        tool_registry,
        mode=self._config.mode,
        identity=self._config.developer_instructions,
    )

    # ... hooks 逻辑不变 ...
    # ... compaction 逻辑暂时保持不动，待 compaction 进程完成后统一收口 ...
```

关键变化：

- `self._config.get(...)` → `self._config.xxx` 属性访问
- `developer_instructions` 作为 ContextBuilder `identity` 参数，不再读取外部文件（原 key 名为 `identity`，现改为 `developer_instructions` 以与 toml 字段对齐）
- 组装顺序修正：先注册所有工具（builtin + skills + mcp），再构建 system_prompt，确保 ContextBuilder 能看到完整工具表
- `spec.meta` 不再作为搬运袋
- `skills`/`mcp` 通过方法参数注入，签名从 `(self, ctx, registry)` 变为 `(self, ctx, registry, config_dict)`

### 4. `src/services/agent_run_service.py`

Stage 4 精简为：

```python
# -- Stage 4: Exp assembly --
from matmaster.config.loader import load_exp_config

exp_name = mode or "direct"
exp_config = load_exp_config(exp_name)
exp = Exp(exp_config)
runtime = exp.build_runtime(
    pg_ctx,
    bus=bus,
    skills=pg_ctx.run_meta.get("skill_config"),
    mcp=pg_ctx.run_meta.get("mcp_config"),
)
```

删除整个 hardcoded dict 构建。`mode` 来自前端，直接作为 exp name 查找对应 toml 文件。

### 5. `matmaster/exps/direct.toml`

新建文件，内容从当前 hardcoded dict + `prompts/mat_master_system_prompt.txt` 合并而来。

### 6. `prompts/mat_master_system_prompt.txt`

废弃。内容迁入 `direct.toml` 的 `developer_instructions` 字段。

## Error Handling

与 LLM 设计一致，全程 fail-fast：

- 未知 exp name（即未知 mode）→ `FileNotFoundError`，消息包含可用 exp 列表
- toml 解析失败 → `tomllib.TOMLDecodeError` 原生异常
- 字段类型不匹配 → Pydantic `ValidationError`
- 不做静默 fallback 到默认 exp

## Testing Plan

### 1. `tests/matmaster/config/test_exp.py`

- toml 加载为 ExpConfig 的字段正确性
- 未知 exp name → `FileNotFoundError` 且消息包含可用列表
- `${ENV}` 展开在 toml 中正常工作
- `extra="ignore"` 忽略未知字段
- `developer_instructions` 多行字符串正确保留

### 2. `tests/matmaster/core/test_exp.py`

- `Exp(ExpConfig(...))` 能正常 assemble + build_runtime
- `developer_instructions` 正确流入 system_prompt
- `skills`/`mcp` 通过 `build_runtime()` 参数注入后工具注册正常
- `skills=None` / `mcp=None` 时不报错
- `mode` 正确传播到 AgentRuntimeSpec

### 3. 集成测试

- 从 `matmaster/exps/direct.toml` 加载 → Exp 构建 → build_runtime 全链路通过

## Impact Summary

| 文件 | 操作 | 说明 |
|------|------|------|
| `matmaster/exps/direct.toml` | 新建 | 第一个 exp 定义文件 |
| `matmaster/exps/__init__.py` | 新建 | 包识别 |
| `matmaster/config/exp.py` | 改造 | 移除运行时字段，新增 developer_instructions |
| `matmaster/config/loader.py` | 改造 | load_exp_config 改为按 name 加载 toml |
| `matmaster/config/__init__.py` | 小改 | 导出更新 |
| `matmaster/core/exp.py` | 改造 | 接收 ExpConfig，build_runtime 新增参数 |
| `src/services/agent_run_service.py` | 精简 | Stage 4 删除 hardcoded dict |
| `prompts/mat_master_system_prompt.txt` | 废弃 | 内容迁入 direct.toml |
| `tests/matmaster/config/test_exp.py` | 改写 | 对齐新 ExpConfig 字段和 toml 加载 |
| `tests/matmaster/config/test_loader.py` | 改写 | 对齐新 `load_exp_config` 签名 |
| `tests/matmaster/core/test_exp.py` | 改写 | `Exp(dict)` → `Exp(ExpConfig)` |
| `tests/matmaster/integration/test_pipeline_alignment.py` | 适配 | 更新 Exp 构造方式 |
| `tests/matmaster/integration/test_e2e_*.py` | 适配 | 更新 Exp 构造方式 |
| `tests/matmaster/integration/test_upstream_scenarios.py` | 适配 | 更新 Exp 构造方式 |

## Open Questions

1. `mode` 值与 toml 文件名的映射是否需要一层间接（例如 `mode="plan"` 映射到 `planner.toml`），还是严格保持 `mode == filename`
2. 未来新增 exp 类型时，是否需要在某处注册可用的 mode 列表（用于前端下拉），还是直接扫描 `exps/` 目录

当前推荐：

- 严格 `mode == filename`，保持简单
- 可用列表通过扫描目录生成，不额外维护注册表
