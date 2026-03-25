# Lazy MCP Loading via Skill Routing

**Date:** 2026-03-25
**Status:** Approved
**Branch:** refactor/matmaster-playground-exp-agent-v2

## Problem

当前架构在 session 启动时一次性连接所有 11 个 MCP 服务器，调用 `tools/list` 获取全部 tool schema，注入到 system prompt 和 function calling tool definitions 中。这导致：

- **Tool definitions token 消耗过高：** 50+ 个 MCP tool 的 name + description + input_schema 占用大量 function calling 定义空间
- **System prompt 膨胀：** ContextBuilder 将工具信息写入 system prompt，挤占有效上下文
- **启动延迟：** 连接 11 个远程 MCP server 需要等待

## Solution

通过 Skill 路由机制按需加载 MCP tool schema 和连接，替代启动时全量加载。

核心思路：
1. 启动时 system prompt 只放轻量级 skill 路由表（~600 tokens），不连接任何 MCP server
2. LLM 通过 `use_skill(action="get_info")` 触发 skill 后，从本地缓存加载对应 MCP tool schema 注入到 tool definitions
3. LLM 下一轮调用实际 tool 时，才按需建立 MCP 连接

参考模式：Claude Code 的 deferred tool + tool_search 机制。

## Data Flow

```
启动阶段:
  Exp.build_runtime()
    ├─ ToolRegistry: 只注册 builtin tools (Bash/Editor/MonitorJob) + use_skill
    ├─ SkillRegistry: 加载所有 SKILL.md 的 meta_info
    ├─ ContextBuilder: system prompt 含 skill 路由表(传入 skill_registry 参数)
    └─ MCP 连接数 = 0

Turn N — Skill 触发:
  LLM → use_skill(skill_name="structure-generator", action="get_info")
  SkillTool 处理:
    1. skill.get_full_info() → 返回 SKILL.md body
    2. 读 skill.meta_info.extras["mcp_server"]
    3. 触发 on_skill_hit 回调
    4. 回调从 ToolSchemaCache 加载已过滤的 schema → 创建 LazyMCPTool → registry.register()
    5. 返回 full_info 给 LLM

Turn N+1 — 工具执行:
  AgentKernel 调用 spec.tool_registry.get_tool_definitions() → 包含新注入的工具
  LLM 看到新工具 → 发起 tool_call
  LazyMCPTool.execute():
    1. 首次调用 → LazyMCPConnector 建立 MCP 连接(含领域配置注入)
    2. 连接缓存到连接池，后续调用复用
    3. 通过 MCP 协议执行 tools/call
```

## Design Assumptions

- **ToolRegistry 是 shared mutable state：** AgentRuntimeSpec 是 frozen=True，但 ToolRegistry 是 mutable 对象。frozen 限制的是字段重新赋值，不阻止对 ToolRegistry 内部 `_tools` dict 的修改。SkillTool 在 Turn N 注入新工具，AgentKernel 在 Turn N+1 调用 `get_tool_definitions()` 时自动可见。
- **AgentKernel 每轮重新获取 tool definitions：** `agent.py:193` 在每次 `_call_llm_stream` 调用前都执行 `spec.tool_registry.get_tool_definitions()`，保证动态注入的工具在下一轮可见。
- **单线程同步执行：** 当前 AgentKernel 是同步 turn-based 循环，SkillTool 注入和 Kernel 读取不会并发。如果未来 kernel 切到 async/多线程，需要在 ToolRegistry 上加锁。
- **一个 skill 严格对应一个 MCP server：** 跨多个 MCP server 的旧 skill（如 structure-manager）需拆分为多个细粒度 skill，每个只绑定一个 server。

## Components

### 1. SKILL.md Frontmatter Extension + Skill 拆分

一个 skill 对应一个 MCP server，通过 frontmatter 的 `mcp_server` 字段声明：

```yaml
---
name: structure-generator
description: 从 Wyckoff 位置、SMILES、原型模板等构建晶体结构，支持表面切割、超胞、缺陷
mcp_server: mat_sg
---
```

跨多个 MCP server 的旧 skill 需拆分。以 `structure-manager` 为例：

| 旧 skill | 拆分后 | mcp_server | 职责 |
|---|---|---|---|
| structure-manager | structure-generator | mat_sg | 结构生成(Wyckoff/SMILES/surface/supercell/defect) |
| | structure-database | mat_struct_db | 结构数据库检索(formula/composition/prototype) |
| | science-navigator | mat_sn | 文献搜索/论文检索/web-search |

其他跨 server 的 skill 同理拆分。不依赖 MCP 的 skill（如 ask-human）不声明 `mcp_server`。

LLM 通过 skill 路由表自行选择触发哪些 skill。一个复杂任务（如「找到某材料的晶体结构并做第一性原理计算」）可能依次触发多个 skill，每次触发只注入对应 server 的工具。

### 2. SkillMetaInfo — extras 捕获扩展字段

SkillMetaInfo 保持通用，不为特定 skill 类型硬编码字段。`mcp_server` 等扩展字段通过 `extras` dict 捕获：

```python
class SkillMetaInfo(BaseModel):
    name: str
    description: str
    license: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")
```

解析时 `name`/`description`/`license` 之外的 frontmatter 字段全部收进 `extras`。消费方按需取 `skill.meta_info.extras.get("mcp_server")`。

### 3. ToolSchemaCache — 已过滤的本地 schema 缓存

预下载的 MCP tool schema，存放在 `matmaster/cache/`：

```
matmaster/cache/
  mat_sg.json
  mat_sn.json
  mat_doc.json
  ...
```

**关键：缓存的是过滤后的 tool 集合，不是 raw `tools/list` 输出。** 缓存生成脚本复用 MCPToolManager._build_tools() 的完整过滤链：
1. `tool_include_only` — 按 server 白名单过滤
2. `sync_tools_by_server` — 过滤 sync tool 的 submit_* 版本
3. dedup — 有 submit_X 时删掉 base X

这保证缓存中的 tool 集合与运行时 manager 实际注册的 tool 集合一致，LazyMCPTool 占位符不会指向不存在的 real tool。

```python
class ToolSchemaCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def load(self, server_name: str) -> list[dict] | None:
        path = self.cache_dir / f"{server_name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
```

纯只读，无 TTL，无自动同步。通过 CLI 脚本手动生成：

```bash
uv run python -m matmaster.tools.cache_mcp_schemas
```

脚本走完整的 MCPToolManager 初始化流程（含 config.yaml 中的 tool_include_only、sync_tools、calculation_executors 等配置），连接所有 server，然后 dump 过滤后的 tool 集合到 JSON。发版或 MCP server 更新时手动跑一次。

`matmaster/cache/` 目录应加入 `.gitignore`（环境特定的缓存文件不提交版本控制）。

**生产环境缓存保障：** 缓存文件通过部署流程生成（CI/CD pipeline 中运行 `cache_mcp_schemas` 脚本），确保每个 worker/pod 都有与当前环境匹配的缓存。

### 4. SkillTool Enhancement — 回调模式触发 schema 注入

SkillTool 通过回调与 MCP 注入逻辑解耦，保持单一职责。构造时只接收一个可选的 `on_skill_hit` 回调：

```python
class SkillTool(BaseTool):
    def __init__(
        self,
        skill_registry: SkillRegistry,
        on_skill_hit: Callable[[str], None] | None = None,
    ):
        super().__init__()
        self.skill_registry = skill_registry
        self._on_skill_hit = on_skill_hit

    def _get_info(self, skill: Skill) -> tuple[str, dict[str, Any]]:
        full_info = skill.get_full_info()

        # 触发回调，由外部负责 schema 注入
        mcp_server = skill.meta_info.extras.get("mcp_server")
        if mcp_server and self._on_skill_hit:
            self._on_skill_hit(mcp_server)

        return (
            f"# Skill: {skill.meta_info.name}\n\n{full_info}",
            {"action": "get_info", "skill_name": skill.meta_info.name},
        )
```

回调由 `Exp._init_skill_tools` 构造时闭包注入（见 Section 7）。

### 5. LazyMCPTool — 满足 matmaster Tool Protocol 的延迟连接工具

直接实现 matmaster `Tool` Protocol（`name`, `description`, `json_schema`, `execute(arguments) -> str`），无需 EvoToolAdapter 包装，可直接 `registry.register()`：

```python
class LazyMCPTool:
    """占位 MCP 工具 — 持有 cached schema，首次 execute 才连接 server。

    实现 matmaster Tool Protocol，可直接注册到 ToolRegistry。
    """

    def __init__(self, server_name: str, tool_name: str,
                 remote_tool_name: str, description: str,
                 input_schema: dict, connector: LazyMCPConnector):
        self._name = tool_name            # 带前缀: mat_sg_build_bulk_...
        self._description = description
        self._input_schema = input_schema
        self._server_name = server_name
        self._remote_tool_name = remote_tool_name
        self._connector = connector
        self._real_tool: MCPTool | None = None

    # ── Tool Protocol properties ──

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return self._input_schema

    # ── Tool Protocol execute ──

    def execute(self, arguments: dict[str, Any]) -> str:
        """首次调用建立 MCP 连接，之后复用。

        桥接 matmaster Tool Protocol (arguments: dict -> str) 和
        EvoMaster MCPTool (session, args_json) 接口。
        """
        if self._real_tool is None:
            self._real_tool = self._connector.connect_and_get_tool(
                self._server_name, self._remote_tool_name
            )
        # MCPTool.execute 需要 session + args_json，通过 connector 获取 session
        args_json = json.dumps(arguments)
        observation, _info = self._real_tool.execute(
            self._connector.session, args_json
        )
        return observation if isinstance(observation, str) else json.dumps(observation)
```

### 6. LazyMCPConnector — 按需连接池(含领域配置注入)

管理 MCP server 的按需连接。内部创建后台 event loop 线程供 MCPToolManager 使用，解决 async/sync 桥接问题。

**关键：复用 MatMaster 特有的 MCP 初始化逻辑。** 当前 `playground._setup_mcp_tools()` 中的领域配置注入（path_adaptor、calculation_executors、tool_include_only、sync_tools、async timeout 等）被提取为独立函数 `configure_mcp_manager()`，LazyMCPConnector 和 playground 共用。

```python
def configure_mcp_manager(manager: MCPToolManager, mcp_config: dict) -> None:
    """从 mcp config 注入 MatMaster 领域特有的 manager 配置。

    提取自 playground._setup_mcp_tools()，供 LazyMCPConnector 和
    playground 共用，保证配置注入逻辑一致。

    注入内容：
    - path_adaptor_servers + path_adaptor_factory (OSS 路径适配)
    - sync_tools_by_server (同步/异步工具过滤)
    - tool_include_only (per-server 工具白名单)
    """
    if mcp_config.get('path_adaptor') == 'calculation':
        from evomaster.adaptors.calculation import get_calculation_path_adaptor
        calc_servers = mcp_config.get('calculation_servers')
        if calc_servers:
            manager.path_adaptor_servers = set(calc_servers)
        manager.path_adaptor_factory = lambda: get_calculation_path_adaptor(mcp_config)

    executors = mcp_config.get('calculation_executors') or {}
    manager.sync_tools_by_server = {
        name: set(cfg.get('sync_tools') or [])
        for name, cfg in executors.items()
        if cfg.get('sync_tools')
    }

    include_only = mcp_config.get('tool_include_only')
    if include_only and isinstance(include_only, dict):
        manager.tool_include_only = {
            k: list(v) for k, v in include_only.items()
            if isinstance(v, (list, tuple))
        }


class LazyMCPConnector:
    """按需连接 MCP server，首次 connect 时创建后台 loop 线程。

    接收完整的 mcp_config（含 path_adaptor、calculation_executors 等），
    通过 configure_mcp_manager() 注入领域配置，保证与旧架构行为一致。
    """

    def __init__(self, mcp_server_config: dict, mcp_config: dict,
                 session: Any = None):
        """
        Args:
            mcp_server_config: 从 mcp_config.json 解析的 server 连接信息
                              {server_name: {transport, url, headers}}
            mcp_config: 从 config.yaml 的 mcp section 读取的完整配置
                       (含 path_adaptor, calculation_executors, tool_include_only 等)
            session: EvoMaster session，供 LazyMCPTool.execute 使用
        """
        self._server_config = mcp_server_config
        self._mcp_config = mcp_config
        self._manager: MCPToolManager | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self.session = session

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """确保后台 event loop 线程运行中。"""
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )
        self._loop_thread.start()
        return self._loop

    def _ensure_manager(self) -> MCPToolManager:
        """确保 MCPToolManager 已初始化并注入领域配置。"""
        if self._manager is not None:
            return self._manager
        loop = self._ensure_loop()
        self._manager = MCPToolManager()
        self._manager.loop = loop
        configure_mcp_manager(self._manager, self._mcp_config)
        return self._manager

    def connect_and_get_tool(self, server_name: str,
                             remote_tool_name: str) -> MCPTool:
        manager = self._ensure_manager()

        if server_name not in manager.connections:
            server_cfg = self._server_config.get(server_name)
            if not server_cfg:
                raise ValueError(f"MCP server '{server_name}' not in config")
            fut = asyncio.run_coroutine_threadsafe(
                manager.add_server(name=server_name, **server_cfg),
                manager.loop,
            )
            fut.result(timeout=60)

        return manager.tools_by_server[server_name][
            f"{server_name}_{remote_tool_name}"
        ]

    def cleanup(self) -> None:
        if self._manager and self._loop and not self._loop.is_closed():
            fut = asyncio.run_coroutine_threadsafe(
                self._manager.cleanup(), self._loop
            )
            fut.result(timeout=30)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
```

### 7. Exp._init_skill_tools — 填充 stub，回调闭包注入

从 `self._config.skills` 读取 typed config。MCP 配置有两个来源，需要明确 source of truth：
- **server 连接信息** 来自 `mcp_config.json`（或环境特定变体如 `mcp_config.test.json`）
- **领域配置** 来自 `config.yaml` 的 `mcp` section（path_adaptor、tool_include_only 等）

`build_runtime()` 通过参数接收 `mcp` config（由上层 agent_run_service 从 PlaygroundContext 注入），`_init_skill_tools` 使用该参数获取完整 MCP 配置：

```python
def _init_skill_tools(self, ctx, registry, config=None):
    skills_cfg = self._config.skills
    if not skills_cfg.enabled:
        return

    skill_registry = SkillRegistry(Path(skills_cfg.skills_root))
    schema_cache = ToolSchemaCache(Path(skills_cfg.cache_dir))

    # MCP 配置：server 连接信息 + 领域配置
    # config 参数由 build_runtime(mcp=...) 透传，包含完整的 mcp section
    mcp_config = config or {}
    mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)

    # 环境感知的 config 路径解析
    config_path = Path(mcp_config_file)
    if not config_path.is_absolute():
        config_path = Path(skills_cfg.config_dir) / config_path
    if mcp_config.get('path_adaptor') == 'calculation':
        from evomaster.adaptors.calculation import resolve_mcp_config_path
        config_path = resolve_mcp_config_path(config_path)

    server_config = load_mcp_server_config(config_path)

    connector = LazyMCPConnector(
        mcp_server_config=server_config,
        mcp_config=mcp_config,
        session=ctx.session,
    )
    self._register_cleanup(connector.cleanup)

    # 回调闭包：skill 触发时加载 schema → 创建 LazyMCPTool → 注册
    def on_skill_hit(mcp_server: str) -> None:
        schemas = schema_cache.load(mcp_server)
        if not schemas:
            self.logger.warning(
                "No cached schema for MCP server '%s', tools not injected",
                mcp_server,
            )
            return
        for tool_schema in schemas:
            original_name = tool_schema["name"]
            prefixed_name = f"{mcp_server}_{original_name}"
            if prefixed_name in registry:
                continue  # 已注入，跳过
            lazy_tool = LazyMCPTool(
                server_name=mcp_server,
                tool_name=prefixed_name,
                remote_tool_name=original_name,
                description=tool_schema.get("description", ""),
                input_schema=tool_schema.get("input_schema", {}),
                connector=connector,
            )
            registry.register(lazy_tool, source="mcp")

    skill_tool = SkillTool(skill_registry, on_skill_hit=on_skill_hit)
    adapted = EvoToolAdapter(skill_tool, ctx.session)
    registry.register(adapted, source="skill")

    # 保存 skill_registry 引用，供 build_runtime 传给 ContextBuilder
    self._skill_registry = skill_registry
```

`build_runtime` 中 ContextBuilder 调用需传入 `skill_registry`：

```python
# 在 build_runtime 中，_init_skill_tools 之后
system_prompt = builder.build(
    ctx, registry,
    mode=spec.mode,
    identity=identity,
    skill_registry=getattr(self, "_skill_registry", None),
)
```

### 8. ExpConfig Extension

```python
class ExpSkillsConfig(BaseModel):
    enabled: bool = False
    skills_root: str = ""
    cache_dir: str = ""
    config_dir: str = ""           # config 文件基础目录(用于相对路径解析)
    mcp_config_file: str = ""      # 默认 mcp_config.json，可被运行时 mcp 参数覆盖

class ExpConfig(BaseModel):
    name: str = "direct"
    mode: str = "direct"
    max_turns: int = 100
    guards: list[str] = Field(default_factory=list)
    tools: ExpToolsConfig = Field(default_factory=ExpToolsConfig)
    skills: ExpSkillsConfig = Field(default_factory=ExpSkillsConfig)
    developer_instructions: str = ""
    model_config = ConfigDict(extra="ignore")
```

```toml
# matmaster/exps/direct.toml
[skills]
enabled = true
skills_root = "playground/mat_master/skills"
cache_dir = "matmaster/cache"
config_dir = "configs/mat_master"
mcp_config_file = "mcp_config.json"
```

## Error Handling

- **缓存未命中：** `on_skill_hit` 回调中 `schema_cache.load()` 返回 None 时，记录 warning 日志。SkillTool 仍正常返回 full_info，但 LLM 下一轮没有可用的 MCP 工具。full_info 中的工具描述仍可见，LLM 可能尝试调用不存在的工具，会命中 ToolRegistry 的 "Tool not found" 错误消息，不会崩溃。
- **MCP 连接失败：** LazyMCPConnector 首次连接失败时，MCPToolManager 内部 3 次重试机制仍然生效。超过重试后 `fut.result()` 抛出异常，被 MCPTool/ToolRegistry 的错误处理链捕获，返回错误消息给 LLM。
- **重复注入：** `on_skill_hit` 通过 `prefixed_name in registry` 检查去重，同一 server 的工具不会重复注册。
- **缓存与运行时不一致：** 缓存生成脚本走完整的 MCPToolManager 初始化流程（含 tool_include_only、sync_tools、dedup），保证缓存的 tool 集合与运行时一致。

## Token Impact

| | Before | After |
|---|---|---|
| Builtin tools | 3 | 3 |
| MCP tool definitions | ~50+ | 0 (启动时) |
| use_skill tool | 1 | 1 |
| Skill meta in prompt | 0 | ~15-20 (拆分后更多 skill，~800 tokens) |
| **Estimated system prompt** | **~5000+ tokens** | **~800 tokens** |

Skill 触发后，仅注入该 skill 对应的单个 server 的 tools（通常 3-8 个），而非全部 50+。

## Unchanged Components

以下组件不需要改动：

- **AgentKernel** — 纯执行循环，每轮调用 `get_tool_definitions()` 自动获取最新工具集
- **ContextBuilder** — 已支持 `skill_registry` 参数
- **SkillRegistry 加载逻辑** — 目录扫描 + SKILL.md 解析复用
- **MCPToolManager 核心逻辑** — add_server/remove_server/reconnect 复用
- **MCPTool** — LazyMCPTool delegate 到真正的 MCPTool

## New Files

| Path | Type |
|---|---|
| `matmaster/cache/*.json` | 预下载的已过滤 MCP tool schema (gitignored) |
| `matmaster/tools/schema_cache.py` | ToolSchemaCache 类 |
| `matmaster/tools/lazy_mcp.py` | LazyMCPTool + LazyMCPConnector + configure_mcp_manager |
| `matmaster/tools/cache_mcp_schemas.py` | CLI 缓存生成脚本(走完整 manager 初始化+过滤) |

## Modified Files

| Path | Change |
|---|---|
| `evomaster/skills/base.py` | SkillMetaInfo 增加 extras 字段 + 解析逻辑 |
| `evomaster/agent/tools/skill.py` | SkillTool 增加 on_skill_hit 回调参数 |
| `matmaster/config/exp.py` | 增加 ExpSkillsConfig (含 config_dir) |
| `matmaster/core/exp.py` | _init_skill_tools 填充实现 + build_runtime 传 skill_registry |
| `matmaster/exps/direct.toml` | 增加 [skills] 段 (路径指向 configs/mat_master/) |
| `playground/mat_master/skills/*/SKILL.md` | 增加 mcp_server frontmatter + 拆分跨 server skill |
| `playground/mat_master/core/playground.py` | _setup_mcp_tools 提取 configure_mcp_manager 共用 |
| `.gitignore` | 增加 matmaster/cache/ |

## Skill Split Plan

跨多个 MCP server 的旧 skill 需拆分为 1:1 映射。需要审查所有现有 SKILL.md 确定拆分方案：

| 旧 skill | 拆分为 | mcp_server |
|---|---|---|
| structure-manager | structure-generator | mat_sg |
| | structure-database | mat_struct_db |
| bohrium-job | (保留，可能需拆分) | 待定 |
| result-analysis | (审查是否跨 server) | 待定 |
| ... | ... | ... |

具体拆分方案在实现阶段审查每个 SKILL.md 后确定。拆分原则：
1. 每个新 skill 只绑定一个 `mcp_server`
2. SKILL.md body（full_info）只描述该 server 提供的工具用法
3. 不依赖 MCP 的 skill（脚本类如 ask-human、manuscript-scribe）不需要拆分
