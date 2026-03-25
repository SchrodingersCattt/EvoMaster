# Phase 4: Playground Layer - Research

**Researched:** 2026-03-22
**Domain:** Python config-driven environment preparation layer design -- unified Playground, session/workspace/logging lifecycle, archival contract, Exp-owned capability initialization
**Confidence:** HIGH

## Summary

Phase 4 的本质不是再造一个更大的 playground，而是把当前 `evomaster.core.BasePlayground` 里混在一起的三类职责拆开：

1. 物理环境准备
   workspace 目录、session 打开/关闭、日志文件句柄、cache 目录
2. 能力资源初始化
   MCP manager、skill registry、tool registry、LLM provider
3. 业务编排
   Service 层读取配置、Bohrium 凭证注入、run 生命周期、配额、Redis/SSE

本阶段只实现第 1 类职责，并把第 2 类职责明确迁到 `matmaster/assembly/DirectExp`。第 3 类职责保持在现有 Service 层，真正切流到新三层管线留到 Phase 5。这样既满足 Phase 4 的边界修正，又不会把 `src/services/agent_run_service.py` 的 Bohrium / Redis / quota 逻辑过早卷入重构。

**Primary recommendation:** 在 `matmaster/playground/` 新建统一 `Playground` 类，直接复用现有 `ConfigManager` 与 `LocalSession` / `DockerSession` / `SSHSession`，只暴露 `prepare(run_meta) -> PlaygroundContext` 和 `cleanup()`；同时在 `matmaster/assembly/` 新增 EvoMaster tool adapter，让 `DirectExp` 通过构造注入的 `mcp_config` / `skill_config` 自己完成能力注册与清理。

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Playground 只负责物理工作环境：workspace、Session、logging
- MCP、Skill、Tool、LLM 初始化不属于 Playground，由 Exp 层负责
- Service 层统一读配置并分发：物理环境配置给 Playground，能力配置给 Exp
- Playground 和 Workspace 在当前项目中等价：1 Session : 1 Playground : 1 Workspace
- 生命周期是 `prepare()` + `cleanup()` 两段式，没有单独 `setup()`
- 只保留一个统一 Playground 类，`mat_master` 和 `minimal` 通过 config YAML 区分
- `PlaygroundContext` 必须移除 `mcp_manager` 和 `skill_registry`
- `PlaygroundContext` 必须新增 `WorkspaceArchivalConfig | None`
- Exp 构造函数接收 `mcp_config` 和 `skill_config`，`assemble()` 中自行初始化 MCP 与 Skill
- Exp 自管能力资源生命周期，Playground 由 Service 调 `cleanup()`
- 新代码写在 `matmaster/playground/`，旧 `playground/mat_master/` 保留不动
- Session 直接复用 `evomaster/agent/session/` 现有实现

### Claude's Discretion
- `WorkspaceArchivalConfig` 的完整字段集合
- `Playground` 私有方法拆分方式
- 配置中 archival / cache / workspace root 的组织形式
- `DirectExp` 接入 EvoMaster MCP/Skill 的适配方式

### Deferred Ideas (OUT OF SCOPE)
- Service 层正式切换到新 Playground 执行路径
- Session Protocol 抽象
- 1 Session 多 Workspace
- Playground 子类扩展体系
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WKSP-01 | 统一 Playground 类只负责物理环境准备，暴露 `prepare(run_meta)->PlaygroundContext + cleanup()`，且 PlaygroundContext 移除能力字段 | Pattern 1 + Pattern 2 + Pattern 3 |
| WKSP-02 | `mat_master` 通过统一 Playground + config YAML 驱动，输出包含 session/workdir/archival 的 PlaygroundContext | Pattern 3 + Pattern 4 + Validation Architecture |
| WKSP-03 | `minimal` 通过统一 Playground + config YAML 驱动，验证最简路径可用 | Pattern 3 + Pattern 4 + Validation Architecture |
| WKSP-04 | PlaygroundContext 包含 `WorkspaceArchivalConfig`，支持后续 run 结束后的 workspace 快照上传 | Pattern 2 + Pattern 5 |
</phase_requirements>

## Standard Stack

### Core
| Library / Module | Why Use It | Phase 4 Role |
|------------------|------------|--------------|
| `pydantic` v2 | Phase 1 已确立 frozen contract 模式 | 定义 `PlaygroundContext` 和 `WorkspaceArchivalConfig` |
| `pathlib` | 当前代码库统一用 Path 组织 workspace / cache / config path | 管理 `run_dir/workspaces/{task_id}` 与 cache 路径 |
| `logging` | 已有日志配置和 handler 管理逻辑 | 复用 run-level 文件 handler 语义 |
| `evomaster.config.ConfigManager` | 已支持 YAML + `.env` 展开 + agent/session 配置读取 | 新 Playground 的配置入口 |
| `evomaster.agent.session` | Local / Docker / SSH 已经可用 | 新 Playground 直接复用，不重新实现 session |

### Supporting
| Library / Module | When to Use | Why |
|------------------|-------------|-----|
| `shutil` / `tempfile` | 需要复制配置或测试临时目录时 | 复用现有 BasePlayground 的 run dir 思路 |
| `src.dao.oss_io.upload_dir_to_oss` | 仅作为 archival 语义参考，不在 Phase 4 直接调用 | 上传动作仍由 Service 层在 Phase 5 编排 |
| `evomaster.agent.tools.create_registry` / `SkillTool` / `MCPToolManager` | Exp 自建能力资源时 | 避免重复实现已有 MCP / skill 生态 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| 新建 Session 抽象层 | 直接复用 `BaseSession` 子类 | Phase 4 更聚焦，避免提前抽象 Session |
| 在 Playground 中继续创建 MCP / Skill | 维持旧 BasePlayground 模式 | 与阶段目标冲突，无法真正收缩边界 |
| 在 Service 层直接 new `PlaygroundContext` | 绕过 Playground 类 | 会让 workspace / session / logging 再次散落在多个调用点 |

## Architecture Patterns

### Recommended Project Structure
```text
matmaster/
├── playground/
│   ├── __init__.py
│   ├── playground.py          # 统一 Playground 类（prepare / cleanup）
│   └── ...                    # 如需拆分可后续加入 helpers
├── types/
│   └── context.py             # PlaygroundContext + WorkspaceArchivalConfig
└── assembly/
    ├── direct_exp.py          # 自行初始化 MCP / Skill
    ├── exp.py                 # run() finally 自管 cleanup
    └── evomaster_tool_adapter.py

tests/
└── matmaster/
    ├── playground/
    │   ├── test_playground.py
    │   └── test_playground_config_paths.py
    └── assembly/
        └── test_evomaster_tool_adapter.py
```

### Pattern 1: Contract-First PlaygroundContext
**What:** Playground 层对外只返回不可变的环境快照，不暴露活跃的能力对象

**Recommendation:**
```python
class WorkspaceArchivalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    oss_bucket: str = ""
    oss_prefix: str = ""
    credential_ref: str = ""


class PlaygroundContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    workdir: Path
    session_type: str
    cache_area: Path
    env_vars: dict[str, str] = Field(default_factory=dict)
    archival: WorkspaceArchivalConfig | None = None
    run_meta: dict[str, Any] = Field(default_factory=dict)
```

**Why:** `mcp_manager` 和 `skill_registry` 都是活跃资源句柄，不是层间契约。放进 frozen model 会让 Exp 的资源边界与清理责任模糊化。

### Pattern 2: Unified Playground with Two-Phase Lifecycle
**What:** 统一 `Playground` 类只做 prepare / cleanup，不承担 run orchestration

**Example:**
```python
class Playground:
    def __init__(self, config_path: str | Path) -> None:
        ...

    def prepare(self, run_meta: dict[str, Any]) -> PlaygroundContext:
        session = self._get_or_create_session(run_meta)
        workdir = self._create_workspace(run_meta)
        cache_area = self._resolve_cache_area(workdir)
        self._setup_logging(run_meta)
        return PlaygroundContext(
            workdir=workdir,
            session_type=self._session_type,
            cache_area=cache_area,
            env_vars=self._collect_env_vars(session),
            archival=self._build_archival_config(),
            run_meta=dict(run_meta),
        )

    def cleanup(self) -> None:
        self._close_owned_session_if_needed()
        self._release_log_handler()
```

**Key rule:** `cleanup()` 只处理 Playground 自己创建的资源。若 `run_meta` 注入了外部 session override，则该 session 仍由调用方负责。

### Pattern 3: Service-Managed Session Override
**What:** 为 Bohrium / SSH 保留一个注入点，而不是在 Phase 4 里重做 Bohrium 编排

**Recommendation:**
- `prepare(run_meta)` 支持 `run_meta["session_override"]`
- 若存在 override，Playground 直接复用，不自行创建 session
- 若不存在 override，再按 config 的 `session.type` 创建 local / docker session

**Why:** 这正好承接当前 `agent_run_service.py` 的 Bohrium 节点创建逻辑，也符合 CONTEXT 里 Service 层加载凭证、Playground 用凭证创建 SSH session 的边界要求

### Pattern 4: Config Compatibility via Existing YAML + Minimal New Block
**What:** 不推翻现有 YAML 结构，只补一个 `playground` 块表达 Phase 4 新语义

**Recommended shape:**
```yaml
playground:
  cache_dir: ".cache/matmaster"
  archival:
    enabled: true
    oss_bucket: "${OSS_BUCKET_NAME}"
    oss_prefix: "matmaster_evo/chat_workspace"
    credential_ref: "env:aliyun-oss"
```

`mat_master` 和 `minimal` 共用同一个 `Playground` 类，差异只由：
- `config_path`
- `session.type`
- `session.local` / `session.docker`
- `playground.cache_dir`
- `playground.archival`

决定。

### Pattern 5: Workspace Path Resolution Reuses BasePlayground Semantics
**What:** 继续沿用 `run_dir/workspaces/{task_id}` 的目录语义，但实现放进新 Playground

**Required behavior:**
- `run_meta["run_dir"]` + `run_meta["task_id"]` 存在时，workspace 固定为 `run_dir/workspaces/task_id`
- 未给 `task_id` 时，可退化到 `run_dir/workspace`
- 创建 session 前同步 `workspace_path` 和 `working_dir`
- Local/Docker 两类 session 都要遵守同一规则

**Why:** 这能保证后续 Phase 5 切 Service 时，workspace 上传、前端文件树和旧路径习惯保持兼容

### Pattern 6: Exp-Owned Capability Initialization
**What:** `DirectExp` 根据构造注入的 `mcp_config` / `skill_config` 自行初始化能力，并把它们适配为 `matmaster.assembly.Tool`

**Recommended adapter:**
```python
class EvoToolAdapter(Tool):
    def __init__(self, tool: BaseTool, session: BaseSession) -> None:
        self._tool = tool
        self._session = session

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return (self._tool.params_class.__doc__ or "").strip()

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._tool.params_class.model_json_schema()

    def execute(self, arguments: dict[str, Any]) -> str:
        observation, _info = self._tool.execute(
            self._session,
            json.dumps(arguments, ensure_ascii=False),
        )
        return observation if isinstance(observation, str) else json.dumps(
            observation,
            ensure_ascii=False,
            default=str,
        )
```

**Key insight:** matmaster kernel 需要同步 `execute(arguments) -> str`，而 EvoMaster 工具是 `execute(session, args_json) -> tuple[observation, info]`。适配层比重写 MCP / skill 工具更便宜、更稳定。

### Pattern 7: Exp.run() Finally Cleanup
**What:** 能力资源谁创建谁清理，`Exp.run()` 负责兜底 finally

**Recommendation:**
- `Exp` 基类维护 `self._cleanup_callbacks`
- `DirectExp.assemble()` 创建 MCP manager 后注册 cleanup callback
- `Exp.run()` 在 `kernel.run()` 成功、失败、取消三种路径都执行 cleanup callbacks

**Why:** Playground 已经不再拥有 MCP manager / skill registry，不能再指望 Playground.cleanup() 帮忙兜底。

### Anti-Patterns to Avoid
- 在 `PlaygroundContext` 中继续塞入 `mcp_manager` / `skill_registry`
- 在 `Playground.cleanup()` 里顺手做 workspace 上传
- 在 `DirectExp.assemble()` 里继续从 `ctx.skill_registry` / `ctx.mcp_manager` 取对象
- 为 `mat_master` 和 `minimal` 再建两个新子类
- 直接复制 `BasePlayground` 的全部实现而不删减 MCP / agent / exp 逻辑

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML + `.env` 变量展开 | 自己写配置加载器 | `ConfigManager` | 已支持 `${VAR}` 替换和 agent/session 读取 |
| Session 生命周期 | 新写 local/docker/ssh wrapper | `LocalSession` / `DockerSession` / `SSHSession` | 现有实现已经处理 workspace/path/teardown |
| MCP 连接管理 | 自己从零对接 MCP client | `MCPToolManager` | 已有连接、重连、tool filter、path adaptor 支持 |
| Skill 执行工具 | 自己写 `use_skill` | `SkillTool` + adapter | 避免重复实现引用文档 / 运行脚本逻辑 |
| Workspace 上传 | 在 Playground 中直接调用 OSS 上传 | 保留 `src.dao.oss_io.upload_dir_to_oss` 给 Service 层 | Phase 4 只交付 archival contract，不切业务编排 |

## Common Pitfalls

### Pitfall 1: 把 capability object 重新塞回 PlaygroundContext
**What goes wrong:** 虽然字段名改了，但本质上还是把活跃资源带过层边界，最终 `cleanup` 责任再次变模糊。
**How to avoid:** `PlaygroundContext` 只保留可序列化、可冻结的环境快照。任何需要 close / cleanup 的对象都留在 Exp 或 Service。

### Pitfall 2: 只更新 `workspace_path`，忘记同步 `working_dir`
**What goes wrong:** Local / Docker / SSH session 里经常两者都存在，少更新一个会出现工具执行目录和文件树目录不一致。
**How to avoid:** 在新 Playground 的 `_sync_workspace_to_session_config()` 中同时写入两个字段，并在测试中显式断言。

### Pitfall 3: `cleanup()` 错关了外部注入的 session
**What goes wrong:** Service 层未来可能传入 Bohrium / SSH session override；如果 Playground 一律 close，会打断外部生命周期编排。
**How to avoid:** 记录 `self._owns_session` 标志。只有 Playground 自己创建的 session 才在 cleanup 中关闭。

### Pitfall 4: 过早切 `agent_run_service.py`
**What goes wrong:** 一旦在 Phase 4 同时切新 Playground 和 Exp，会把 Bohrium、Redis、quota、workspace 上传问题全部放到一个阶段，失去最小可验证路径。
**How to avoid:** Phase 4 先通过单测和 config-path 测试证明新 Playground / DirectExp 可用，Phase 5 再切主路径。

### Pitfall 5: DirectExp 直接依赖 EvoMaster ToolRegistry
**What goes wrong:** matmaster kernel 期望 `ToolRegistry.execute(name, arguments)` 与 `get_tool_definitions()` 的当前契约；直接混用旧 registry 会让 Phase 3 的类型边界退化。
**How to avoid:** 通过 `EvoToolAdapter` 把 EvoMaster tool 映射成 matmaster Tool，再注册进 `matmaster.assembly.ToolRegistry`。

### Pitfall 6: 忘记 `uv run`，导致计划执行环境与项目约定不一致
**What goes wrong:** 执行者用系统 Python 跑测试，和项目实际依赖环境不一致。
**How to avoid:** Phase 4 的所有 verify 命令一律写成 `uv run pytest ...` / `uv run python ...`。

## Validation Architecture

### Recommended Test Layout
| Area | Test File | Purpose |
|------|-----------|---------|
| Contract | `tests/matmaster/types/test_context.py` | 验证 `PlaygroundContext` 与 `WorkspaceArchivalConfig` 的 frozen / roundtrip 行为 |
| Playground core | `tests/matmaster/playground/test_playground.py` | 验证 `prepare()` / `cleanup()`、workspace 创建、session ownership |
| Config compatibility | `tests/matmaster/playground/test_playground_config_paths.py` | 验证 `configs/mat_master/config.yaml` 和 `configs/minimal/config.yaml` 都能驱动统一 Playground |
| Exp integration | `tests/matmaster/assembly/test_evomaster_tool_adapter.py` | 验证 EvoMaster tool -> matmaster Tool 适配 |
| Exp integration | `tests/matmaster/assembly/test_direct_exp.py` / `test_exp.py` | 验证 DirectExp 不再依赖 `ctx.mcp_manager/ctx.skill_registry`，且 run() finally cleanup 生效 |

### Recommended Commands
```bash
uv run pytest tests/matmaster/types/test_context.py -x -q
uv run pytest tests/matmaster/playground/ -x -q
uv run pytest tests/matmaster/assembly/test_evomaster_tool_adapter.py tests/matmaster/assembly/test_direct_exp.py tests/matmaster/assembly/test_exp.py -x -q
uv run pytest tests/matmaster/ -x -q
```

### Validation Strategy
- Wave 1 先锁定 contract 和 unified Playground 核心
- Wave 2 再并行做 config compatibility 与 Exp capability migration
- 任一任务如果需要外部 MCP / SSH，必须通过 fake manager / fake session test double 验证，不在单测里连真实外部资源

## Recommended Plan Split

| Plan | Wave | Focus | Requirements |
|------|------|-------|--------------|
| 04-01 | 1 | Playground contract + unified core lifecycle | WKSP-01, WKSP-04 |
| 04-02 | 2 | `mat_master` / `minimal` config-path compatibility + archival config wiring | WKSP-02, WKSP-03 |
| 04-03 | 2 | DirectExp capability ownership migration + cleanup lifecycle | WKSP-01 |

## Bottom Line

Phase 4 最稳妥的实现方式不是大规模改现有 web service，而是先把新边界在 `matmaster/` 命名空间中完全表达出来：

- `matmaster/types/context.py` 只保留环境契约
- `matmaster/playground/` 只准备物理环境
- `matmaster/assembly/DirectExp` 自己创建并清理能力资源
- `src/services/agent_run_service.py` 到 Phase 5 再切到新主路径

这样可以把 Playground 边界修正、config 兼容性验证、Exp 资源所有权迁移分成 3 个可执行计划，既满足当前 roadmap，也不给后续 Phase 5 埋新的循环依赖和生命周期混乱。

