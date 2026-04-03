# Self-Describing Tool Protocol

> Status: Approved  
> Date: 2026-04-03  
> Branch: refactor/async-agent  
> Scope: P0 (元数据自描述) + P1 (动态描述/prompt 注入) + P2 (Context Modifier)

## 1. Problem

当前 Tool Protocol 只定义 4 个最小属性 (`name`, `description`, `json_schema`, `execute`)。
工具的资源声明、执行面、effect level、stop mode 等元数据硬编码在 ToolCompiler 的 4 张查找表中
(`BUILTIN_CLAIMS`, `BUILTIN_META`, `BUILTIN_CAPABILITIES`, `BUILTIN_STOP_MODES`)。

问题:

- 添加新 builtin 工具需要改 4 张表 + Exp 注册代码，散落 3 个文件
- MCP/Skill 工具只能拿到 fallback 默认值，无法精确声明资源需求
- 元数据与工具实现分离，容易不一致
- 工具描述是静态字符串，无法根据 session 类型动态调整
- 工具无法向 system prompt 注入使用规范（只能硬编码在 TOML 里）
- 跨工具状态传递（ReadTracker）依赖构造注入共享引用，是隐式耦合

## 2. Design Decisions

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 迁移策略 | 一步到位 / 渐进 / Mixin | 一步到位 | 15 个工具同批次迁移，一次 PR 完成 |
| 架构方案 | 扩展 Protocol / Descriptor 中间层 / Mixin | 扩展 Protocol | 与 Claude Code 的 `buildTool()` 平铺模式对齐，无间接层 |
| description 求值时机 | build_definitions 时 / 每个 turn | build_definitions 时 | description 在 session 级别确定后不变，每 turn 求值增加 prompt cache miss |
| Context Modifier | 替代 ReadTracker / 与 ReadTracker 共存 | 替代 | 统一机制，消灭构造注入 hack |
| prompt 拼接位置 | 追加在 TOML prompt 之后 / 占位符替换 | 追加 | 简单直接，不增加 TOML 作者心智负担 |
| MCP/Skill 元数据 | 统一为属性 / 保留 dict | 统一为属性 | 消灭 `getattr(tool, "tool_runtime_meta")` hack |
| 异步方案 | state_mutations on ToolResult / execute_with_context 统一路径 | execute_with_context | 不需要扩展 ToolResult，读写状态都走 exec_ctx.runner_state |

## 3. New Tool Protocol

```python
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim
from matmaster.types.topology import ToolPlane

EffectLevel = Literal["none", "local_mutation", "external_effect"]

@runtime_checkable
class Tool(Protocol):
    """Self-describing tool contract.

    Every tool source (builtin, MCP, skill) satisfies this Protocol.
    The kernel and compiler read metadata directly from the tool --
    no external lookup tables needed.
    """

    # -- Identity (unchanged) --
    @property
    def name(self) -> str: ...

    @property
    def json_schema(self) -> dict[str, Any]: ...

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult | None: ...

    # -- Description (P1: static -> dynamic) --
    def description(self, ctx: ToolDescriptionContext | None = None) -> str:
        """Dynamic description. ctx=None returns static default.
        build_definitions() passes ctx for one-time evaluation."""
        ...

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        """System prompt contribution. Return None for no injection."""
        ...

    # -- Resource & Scheduling (P0: from lookup tables) --
    @property
    def resource_claims(self) -> tuple[ResourceClaim, ...]: ...

    @property
    def capabilities(self) -> frozenset[str]: ...

    @property
    def effect_level(self) -> EffectLevel: ...

    @property
    def fast_path_eligible(self) -> bool: ...

    @property
    def max_result_chars(self) -> int: ...

    # -- Execution Binding (P0: from lookup tables) --
    @property
    def plane(self) -> ToolPlane: ...

    @property
    def state_mode(self) -> Literal["stateless", "persistent"]: ...

    @property
    def stop_mode(self) -> Literal["cancellable", "best_effort", "non_cancellable"]: ...

    @property
    def exposed_to_model(self) -> bool: ...
```

### ToolDescriptionContext

```python
@dataclass(frozen=True)
class ToolDescriptionContext:
    session_kind: str          # "local" | "ssh" | "docker"
    workspace_root: str
    topology: RuntimeTopology
```

## 4. BuiltinTool ABC

```python
class BuiltinTool(ABC):
    """Base class satisfying expanded Tool Protocol.

    Subclasses: define ClassVar overrides for metadata, implement _execute().
    All new properties have safe conservative defaults.
    """

    # -- Identity (unchanged) --
    name: ClassVar[str]
    json_schema: ClassVar[dict[str, Any]]

    # -- Description (P1) --
    _description: ClassVar[str] = ""

    def description(self, ctx: ToolDescriptionContext | None = None) -> str:
        return self._description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return None

    # -- Resource & Scheduling (P0: conservative defaults) --
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset()
    effect_level: ClassVar[EffectLevel] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    max_result_chars: ClassVar[int] = 0

    # -- Execution Binding (P0) --
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE
    state_mode: ClassVar[Literal["stateless", "persistent"]] = "stateless"
    stop_mode: ClassVar[Literal["cancellable", "best_effort", "non_cancellable"]] = "cancellable"
    exposed_to_model: ClassVar[bool] = True

    # -- Construction injection (unchanged) --
    def __init__(self, *, session=None, workdir=None):
        self._session = session
        self._workdir = workdir
        self.logger = logging.getLogger(self.__class__.__name__)

    # -- Execution --
    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error('Tool %s failed: %s', self.name, e, exc_info=True)
            return f'Error: {e}'

    async def execute_with_context(self, arguments, exec_ctx):
        """Default: delegate to _execute via to_thread, exec_ctx available for override."""
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error('Tool %s failed: %s', self.name, e, exc_info=True)
            return f'Error: {e}'

    async def validate_input(self, arguments: dict[str, Any]) -> ToolDecision | None:
        return None

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult: ...
```

## 5. Builtin Tool Metadata Migration

Each tool moves its metadata from ToolCompiler lookup tables to ClassVar declarations.

### ReadTool

```python
class ReadTool(BuiltinTool):
    name: ClassVar[str] = "read_file"
    _description: ClassVar[str] = (
        "Read file contents with line numbers (cat -n format).\n\n"
        "Usage:\n"
        "- ALWAYS use read_file to read files. NEVER use cat/head/tail via execute_bash.\n"
        "- Files up to 2000 lines are returned in full. Larger files return an error with preview.\n"
        "- Use offset and limit to read specific portions of large files.\n"
        "- Always read a file before attempting to edit or overwrite it."
    )
    json_schema: ClassVar[dict[str, Any]] = { ... }

    resource_claims: ClassVar = (ResourceClaim(resource="workspace", mode="shared_read"),)
    capabilities: ClassVar = frozenset({"workspace.read"})
    effect_level: ClassVar = "none"
    fast_path_eligible: ClassVar = True
    max_result_chars: ClassVar = 12000
    plane: ClassVar = ToolPlane.SESSION_FS

    # tracker parameter removed from __init__

    async def execute_with_context(self, arguments, exec_ctx):
        result = await asyncio.to_thread(self._execute, arguments)
        if exec_ctx.runner_state and not str(result).startswith("Error"):
            path = posixpath.normpath(arguments.get("file_path", ""))
            read_set = exec_ctx.runner_state.get("read_files", set())
            read_set.add(path)
            exec_ctx.runner_state.set("read_files", read_set)
        return result
```

### BashTool

```python
class BashTool(BuiltinTool):
    name: ClassVar[str] = "execute_bash"
    _description: ClassVar[str] = "Execute a bash command in the session shell."

    resource_claims: ClassVar = (ResourceClaim(resource="session", mode="exclusive"),)
    capabilities: ClassVar = frozenset({"shell.execute"})
    effect_level: ClassVar = "local_mutation"
    plane: ClassVar = ToolPlane.SESSION_SHELL
    max_result_chars: ClassVar = 12000

    def prompt(self, ctx=None) -> str | None:
        return (
            "Do not use bash for: cat/head/tail/sed/awk/find/ls/grep/rg/echo. "
            "Use read_file, edit_file, write_file, glob, grep instead.\n\n"
            "Paths: local/devshell cwd is the task workspace; do not assume /share exists. "
            "Bohrium SSH: shared storage is usually /share, not /workspace."
        )
```

### WriteTool / EditTool

```python
class WriteTool(BuiltinTool):
    name: ClassVar[str] = "write_file"
    _description: ClassVar[str] = "..."

    resource_claims: ClassVar = (ResourceClaim(resource="workspace", mode="exclusive"),)
    capabilities: ClassVar = frozenset({"workspace.write"})
    effect_level: ClassVar = "local_mutation"
    plane: ClassVar = ToolPlane.SESSION_FS

    # tracker parameter removed from __init__

    async def execute_with_context(self, arguments, exec_ctx):
        if exec_ctx.runner_state:
            read_files = exec_ctx.runner_state.get("read_files", set())
            path = posixpath.normpath(arguments.get("file_path", ""))
            if path and path not in read_files:
                return ToolResult(
                    status="error",
                    content=f"Must read_file before writing: {path}",
                )
        return await asyncio.to_thread(self._execute, arguments)
```

### Full Metadata Table

| Tool | plane | effect_level | fast_path | max_chars | claims | stop_mode |
|------|-------|-------------|-----------|-----------|--------|-----------|
| execute_bash | SESSION_SHELL | local_mutation | False | 12000 | exclusive:session | cancellable |
| list_dir | SESSION_SHELL | none | True | 8000 | exclusive:session | cancellable |
| glob | SESSION_SHELL | none | True | 8000 | exclusive:session | cancellable |
| grep | SESSION_SHELL | none | True | 8000 | exclusive:session | cancellable |
| read_file | SESSION_FS | none | True | 12000 | shared_read:workspace | cancellable |
| write_file | SESSION_FS | local_mutation | False | 0 | exclusive:workspace | cancellable |
| edit_file | SESSION_FS | local_mutation | False | 0 | exclusive:workspace | cancellable |
| task_create | CONTROL_PLANE | local_mutation | False | 0 | exclusive:task-store | cancellable |
| task_get | CONTROL_PLANE | none | True | 0 | shared_read:task-store | cancellable |
| task_list | CONTROL_PLANE | none | True | 0 | shared_read:task-store | cancellable |
| task_update | CONTROL_PLANE | local_mutation | False | 0 | exclusive:task-store | cancellable |
| task_complete | CONTROL_PLANE | local_mutation | False | 0 | exclusive:task-store | cancellable |
| mm_web_search | EXTERNAL_SERVICE | external_effect | False | 0 | counted:web(3) | best_effort |
| web_fetch | EXTERNAL_SERVICE | external_effect | False | 16000 | counted:web(3) | best_effort |
| spawn | CONTROL_PLANE | local_mutation | False | 0 | counted:spawn(2) | non_cancellable |
| monitor_job | EXTERNAL_SERVICE | external_effect | False | 0 | exclusive:workspace+artifact-sync | best_effort |

## 6. LazyMCPTool Adaptation

```python
class LazyMCPTool:
    """Placeholder MCP tool -- satisfies expanded Tool Protocol."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        remote_tool_name: str,
        description: str,
        input_schema: dict,
        connector: Any,
        runtime_meta: dict[str, Any] | None = None,
    ) -> None:
        self._name = tool_name
        self._static_description = description
        self._input_schema = input_schema
        self._server_name = server_name
        self._remote_tool_name = remote_tool_name
        self._connector = connector

        meta = runtime_meta or {}
        self._plane = ToolPlane(meta["plane"]) if meta.get("plane") else ToolPlane.EXTERNAL_SERVICE
        self._effect_level: EffectLevel = meta.get("effect_level", "external_effect")
        self._capabilities = frozenset(meta.get("capabilities", ()))
        self._fast_path_eligible = meta.get("fast_path_eligible", False)
        self._exposed_to_model = meta.get("exposed_to_model", True)
        self._resource_claims = _parse_claims(meta.get("resource_claims", ()))
        self._max_result_chars = meta.get("max_result_chars", 0)
        self._stop_mode = meta.get("stop_mode", "best_effort")
        self._state_mode = meta.get("state_mode", "stateless")

    # All Protocol properties implemented as @property returning self._xxx
    # description(ctx) returns self._static_description
    # prompt(ctx) returns None
    # execute() unchanged
    # execute_with_context() delegates to execute()
```

Default values for MCP tools differ from builtins:
- `plane=EXTERNAL_SERVICE` (not CONTROL_PLANE)
- `effect_level="external_effect"` (not "local_mutation")
- `stop_mode="best_effort"` (not "cancellable")

## 7. ToolCompiler Simplification

```python
class ToolCompiler:
    """Compile a Tool into ToolInstance -- pure assembly, no lookup tables."""

    def compile(self, tool: Tool, topology: RuntimeTopology, *, source: str = "unknown") -> ToolInstance:
        claims = tool.resource_claims

        # Topology-dependent binding relaxation (retained)
        if (
            topology.session_kind == "local"
            and topology.session_capabilities is not None
            and topology.session_capabilities.shell_persistence == "stateless"
            and tool.name in ("list_dir", "glob", "grep")
        ):
            claims = (ResourceClaim(resource="session", mode="shared_read"),)

        spec = ToolSpec(
            tool_name=tool.name,
            description=tool.description(),
            args_schema=tool.json_schema,
            source=source,
            capabilities=tool.capabilities,
            effect_level=tool.effect_level,
            fast_path_eligible=tool.fast_path_eligible,
            max_result_chars=tool.max_result_chars,
            exposed_to_model=tool.exposed_to_model,
        )
        binding = ToolBinding(
            binding_key=f"{tool.plane.value}:{tool.name}",
            plane=tool.plane,
            resource_claims=claims,
            state_mode=tool.state_mode,
            stop_mode=tool.stop_mode,
        )

        tool_executor = tool.execute_with_context

        validator = None
        if hasattr(tool, "validate_input") and callable(tool.validate_input):
            validator = tool.validate_input

        return ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=tool_executor,
            input_validator=validator,
        )
```

Changes from current:
- 4 lookup tables deleted (~80 lines)
- `getattr(tool, "tool_runtime_meta")` branch deleted
- Executor wrapping reduced from 2 branches to 1 line
- ~130 lines -> ~50 lines

## 8. ToolCatalog Changes

### build_definitions(ctx)

```python
def build_definitions(self, ctx: ToolDescriptionContext | None = None) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for name in sorted(self._registry._tools):
        inst = self.get_tool(name)
        if inst is None or not inst.tool_spec.exposed_to_model:
            continue

        if ctx is not None:
            raw_tool = self._registry._tools[name]
            desc = raw_tool.description(ctx)
        else:
            desc = inst.tool_spec.description

        definitions.append({
            "type": "function",
            "function": {
                "name": inst.tool_spec.tool_name,
                "description": desc,
                "parameters": inst.tool_spec.args_schema,
            },
        })
    return definitions
```

### collect_prompts(ctx)

```python
def collect_prompts(self, ctx: ToolDescriptionContext | None = None) -> str:
    parts: list[str] = []
    for name in sorted(self._registry._tools):
        inst = self.get_tool(name)
        if inst is None or not inst.tool_spec.exposed_to_model:
            continue
        raw_tool = self._registry._tools[name]
        p = raw_tool.prompt(ctx)
        if p:
            parts.append(p)
    return "\n\n".join(parts)
```

## 9. Context Modifier: ToolRunnerState

### ToolRunnerState

```python
@dataclass
class ToolRunnerState:
    """Runner-level mutable shared state. Tools read/write via exec_ctx."""
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def clear(self) -> None:
        self.data.clear()
```

### ToolExecutionContext Extension

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    stop_event: threading.Event | None = None
    on_progress: Callable[[str], Awaitable[None]] | None = None
    runner_state: ToolRunnerState | None = None   # <-- P2 addition
```

### FullToolRunner Integration

```python
class FullToolRunner:
    def __init__(self, ..., state: ToolRunnerState | None = None):
        # ...existing params...
        self._state = state or ToolRunnerState()

    @property
    def state(self) -> ToolRunnerState:
        return self._state
```

In `_execute_one()`:

```python
exec_ctx = _ExecCtx(
    stop_event=ctx.stop_event,
    runner_state=self._state,
)
```

### Thread Safety

All `runner_state` reads/writes happen after `await asyncio.to_thread()` returns,
in the asyncio event loop thread. asyncio is single-threaded cooperative concurrency,
so no data races.

## 10. Exp Integration

### System Prompt Assembly

```python
# In Exp.build_runtime():
desc_ctx = ToolDescriptionContext(
    session_kind=topology.session_kind,
    workspace_root=topology.workspace_root,
    topology=topology,
)
tool_prompts = catalog.collect_prompts(desc_ctx)
if tool_prompts:
    system_prompt = base_toml_prompt + "\n\n" + tool_prompts
else:
    system_prompt = base_toml_prompt
```

### ReadTracker Removal

```python
# DELETED from Exp._init_builtin_tools():
# tracker = ReadTracker()
# self._read_tracker = tracker

# Tool construction simplified:
ReadTool(session=ctx.session, workdir=exec_wd)     # no tracker param
WriteTool(session=ctx.session, workdir=exec_wd)     # no tracker param
EditTool(session=ctx.session, workdir=exec_wd)      # no tracker param
```

### ToolRunnerState Construction

```python
# In Exp.build_runtime():
runner_state = ToolRunnerState()
runner = FullToolRunner(
    catalog=catalog,
    structural_validation=validation,
    guard_pipeline=guard_pipeline,
    capability_policy=policy,
    scheduler=scheduler,
    topology=topology,
    state=runner_state,
)
# Register cleanup
self._register_cleanup(runner_state.clear)
```

## 11. Deletions

| Item | Location | Reason |
|------|----------|--------|
| `ReadTracker` class | `matmaster/tools/builtin/read_tracker.py` | Replaced by ToolRunnerState |
| `ReadBeforeModifyGuard` | `matmaster/core/guard_pipeline.py` | Replaced by WriteTool/EditTool execute_with_context |
| `BUILTIN_CLAIMS` | `matmaster/tools/tool_compiler.py` | Moved to tool ClassVars |
| `BUILTIN_META` | `matmaster/tools/tool_compiler.py` | Moved to tool ClassVars |
| `BUILTIN_CAPABILITIES` | `matmaster/tools/tool_compiler.py` | Moved to tool ClassVars |
| `BUILTIN_STOP_MODES` | `matmaster/tools/tool_compiler.py` | Moved to tool ClassVars |
| `tool_runtime_meta` dict pattern | `matmaster/tools/lazy_mcp.py` | Expanded to Protocol properties |
| `getattr(tool, "tool_runtime_meta")` | `matmaster/tools/tool_compiler.py` | No longer needed |
| ReadTracker construction in Exp | `matmaster/core/exp.py` | Replaced by ToolRunnerState |

## 12. Files Changed

### New Files (2)

- `matmaster/types/tool_desc_ctx.py` -- ToolDescriptionContext
- `matmaster/core/tool_runner_state.py` -- ToolRunnerState

### Modified Files (25)

| File | Change |
|------|--------|
| `matmaster/tools/tool_registry.py` | Tool Protocol expansion (+12 attrs/methods) |
| `matmaster/tools/tool_compiler.py` | Delete 4 lookup tables, simplify compile() |
| `matmaster/tools/tool_catalog.py` | build_definitions(ctx), collect_prompts(ctx) |
| `matmaster/tools/tool_result.py` | No change |
| `matmaster/tools/builtin/base.py` | ABC expansion (defaults, execute_with_context, _description) |
| `matmaster/tools/builtin/bash_tool.py` | Metadata ClassVars, prompt() override |
| `matmaster/tools/builtin/read_tool.py` | Metadata ClassVars, execute_with_context, remove tracker |
| `matmaster/tools/builtin/write_tool.py` | Metadata ClassVars, execute_with_context, remove tracker |
| `matmaster/tools/builtin/edit_tool.py` | Metadata ClassVars, execute_with_context, remove tracker |
| `matmaster/tools/builtin/listdir_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/glob_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/grep_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/web_search_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/web_fetch_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/spawn_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/monitor_job/_tool.py` | Metadata ClassVars |
| `matmaster/tools/builtin/task/task_create.py` | Metadata ClassVars |
| `matmaster/tools/builtin/task/task_get.py` | Metadata ClassVars |
| `matmaster/tools/builtin/task/task_list.py` | Metadata ClassVars |
| `matmaster/tools/builtin/task/task_update.py` | Metadata ClassVars |
| `matmaster/tools/builtin/task/task_complete.py` | Metadata ClassVars |
| `matmaster/tools/lazy_mcp.py` | Expand runtime_meta to properties |
| `matmaster/types/tool_spec.py` | ToolExecutionContext +runner_state |
| `matmaster/core/tool_runner.py` | FullToolRunner +ToolRunnerState, inject exec_ctx |
| `matmaster/core/exp.py` | Remove ReadTracker, add desc_ctx/tool_prompts/ToolRunnerState |
| `matmaster/core/agent.py` | build_definitions(desc_ctx) |
| `matmaster/core/guard_pipeline.py` | Remove ReadBeforeModifyGuard |

### Deleted Files (1)

- `matmaster/tools/builtin/read_tracker.py`

## 13. Test Impact

| Test Area | Required Update |
|-----------|----------------|
| ToolCompiler unit tests | Rewrite: no lookup table assertions, test tool self-description |
| ToolCatalog unit tests | Update: build_definitions(ctx) signature, add collect_prompts tests |
| FullToolRunner integration | Update: exec_ctx now includes runner_state |
| GuardPipeline tests | Remove ReadBeforeModifyGuard cases |
| ReadTool tests | Remove tracker construction, add runner_state write verification |
| WriteTool/EditTool tests | Remove tracker construction, add runner_state read-before-modify verification |
| Exp integration tests | Update tool construction (no tracker param), verify prompt assembly |
