# MatMaster Tool Runtime v2 Architecture Design

**日期:** 2026-04-02

**状态:** 草案（基于 2026-04-01 初版经 Claude + GPT 双重评审 + 交叉校验后的终稿）

**范围:** 仅覆盖 agent 执行层的 tool runtime 设计，不包含前端展示、交互式权限弹窗、服务层账号与计费逻辑

---

## 1. 设计目标

在保留 MatMaster 现有 `Playground → Exp → AgentKernel` 主架构的前提下，构建统一的 tool runtime：

- 显式建模运行拓扑，而不是把执行语义分散在工具实现和条件分支中
- 将工具拆分为逻辑语义层与环境执行绑定层
- 在多执行平面下统一调度和执行工具调用
- 用确定性的策略层约束能力边界和副作用边界
- 让 AgentKernel 消费运行时工具实例，而不是裸工具对象
- 对 provider 继续输出 OpenAI 兼容的 tool schema

---

## 2. 背景与问题

MatMaster 当前已具备三层骨架（Playground / Exp / AgentKernel），但 tool 系统存在以下结构性问题：

- 工具抽象过薄，只有 `name`、`description`、`json_schema`、`execute`
- 工具的逻辑语义和执行语义混在一起
- 多执行平面已经在实现中存在（local shell、SSH channel、控制平面本地调用、外部服务），但尚未被正式建模
- 已批准工具默认 `asyncio.gather()` 并发执行，无法正确表达共享状态域
- 安全约束散落在工具实现内部（如 bash_tool 的危险命令检查）、Guard 和 Hook 中，缺少统一策略层
- `ToolResult` 偏轻量，难以支持结构化执行结果
- `ToolCatalog`（当前的 ToolRegistry）是运行时可变的，但设计上未区分静态基座与动态扩展

---

## 3. 非目标

- 前端界面与工具展示
- 交互式权限弹窗与人工批准流程
- 服务层账号、租户、计费、组织权限逻辑
- Provider 层协议重写

---

## 4. 核心设计原则

1. 保留现有 `Playground → Exp → AgentKernel` 主分层，不做推倒式重构
2. 优先把隐含执行语义提升为显式内核合同
3. `control_plane` 是合法一等执行平面，不作为例外逻辑处理
4. 工具解析优先在 `Exp.build_runtime()` 阶段完成，运行时仅处理 skill 驱动的增量注入
5. Kernel 使用确定性策略，而不是以人工确认作为主边界
6. Provider 继续接收 OpenAI 兼容的 tool definitions
7. Session 能力由具体实现自报告，不由 session_kind 硬编码推导

### 4.1 命名约定

公开运行时对象统一使用 `ToolXxx` 词族。属性名表达对象当前语义，而不是表达它如何被构建出来。

| 约定 | 统一用词 |
|------|----------|
| 根路径 | `*_root` |
| 执行平面 | `plane` |
| 运行状态 | `*_mode` |
| 结构化结果 | `payload` |
| 附加元数据 | `meta` |

完成态公开核心名词：

- `RuntimeTopology` / `SessionCapabilities`
- `ToolSpec` / `ToolBinding` / `ToolInstance`
- `ToolCatalog`
- `ToolPlane` (枚举)
- `ResourceClaim` / `ToolScheduler`
- `CapabilityPolicy` / `RunStateGuard` / `StructuralValidation`
- `ToolDecision`
- `ToolRunner`
- `ToolResult`

跨对象统一语义键：

| 语义 | 统一键名 |
|------|----------|
| 逻辑工具标识 | `tool_name` |
| 运行时工具实例 | `tool_instance` |
| 运行拓扑对象 | `runtime_topology` |
| 工具参数 | `tool_args` |
| 工具规格引用 | `tool_spec` |
| 工具绑定引用 | `tool_binding` |
| 工具执行器 | `tool_executor` |
| 工具目录对象 | `tool_catalog` |
| 资源声明 | `resource_claims` |

---

## 5. 完成态总体架构

### 5.1 Layer 1: Playground

职责：

- 创建或接入 `local` / `ssh` session
- 查询 session 报告的 `SessionCapabilities`
- 准备控制平面工作区与执行平面工作区
- 生成完整环境上下文 `PlaygroundContext`

输出：`PlaygroundContext`（继续作为上层完整环境合同，不被替换）

### 5.2 Layer 2: Exp

职责：

- 从 `PlaygroundContext` 派生 `RuntimeTopology`（包含 SessionCapabilities）
- 构建 `ToolSpec` 集合
- 为当前 topology 解析 `ToolBinding`
- 编译出 `ToolCatalog`（静态基座层）
- 构建三层约束（StructuralValidation + RunStateGuard + CapabilityPolicy）
- 生成模型可见的 tool definitions
- 装配 `AgentRuntimeSpec`

输出：`AgentRuntimeSpec`

完成态下，Exp 应将以下字段注入 AgentRuntimeSpec：

- `runtime_topology: RuntimeTopology`
- `tool_catalog: ToolCatalog`
- `capability_policy: CapabilityPolicy`
- `guards: list[Guard]`（RunStateGuard 实例列表）
- `hooks: list[Hook]`

### 5.3 Layer 3: AgentKernel

职责：

- 接收 LLM 返回的 `tool_calls`
- 检查 `ToolCatalog.version` 决定是否刷新 tool_definitions
- 使用 `ToolCatalog` 查找运行时工具实例
- 委托 `ToolRunner` 执行工具（含约束检查和调度）
- 把结果回填为 `ToolMessage`

---

## 6. 核心对象

### 6.1 SessionCapabilities

用途：由具体 Session 实现自报告能力，不由 session_kind 推导。

```python
class SessionCapabilities(BaseModel):
    """Session 实例报告的实际能力，作为 RuntimeTopology 的输入。"""
    model_config = ConfigDict(frozen=True)

    shell_persistence: Literal["stateless", "persistent"]
    shell_input: bool = False
    file_ops: Literal["native", "sftp"]
    upload_support: bool = False
    exec_cancel: bool = False
```

说明：

- 当前 SSHSession 的 exec_bash 每次新开 channel 执行 `bash -l -c 'cd ... && cmd'`，应报告 `shell_persistence = "stateless"`、`shell_input = False`
- 当前 LocalSession 应报告 `shell_persistence = "stateless"`、`shell_input = False`、`exec_cancel = False`（LocalSession.exec_bash 使用 subprocess.run，stop_event 无效果；BashTool 的本地异步路径也不检查 stop_event）
- 若未来引入 tmux 持久 shell，对应 Session 报告 `shell_persistence = "persistent"`、`shell_input = True`，无需改动 RuntimeTopology 或上层逻辑

实现路径：在 Session Protocol 上增加 `capabilities` 属性（提供默认实现）。Playground 构建 PlaygroundContext 时调用 `session.capabilities` 填入。

### 6.2 RuntimeTopology

用途：表达当前 run 的最小执行拓扑语义，供 policy / scheduler / runner / kernel 统一消费。

```python
class RuntimeTopology(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_kind: Literal["local", "ssh"]       # 仅作标签，不推导能力
    control_root: str
    workspace_root: str
    active_planes: frozenset[ToolPlane]
    session_capabilities: SessionCapabilities
```

不变量：

- session 平面工具默认以 `workspace_root` 为作用域根
- control 平面工具默认以 `control_root` 为作用域根
- session_kind 仅用于日志、审计和 binding 过滤，不用于推导 shell 行为

### 6.3 ToolPlane (枚举)

```python
class ToolPlane(str, Enum):
    SESSION_SHELL = "session_shell"
    SESSION_FS = "session_fs"
    CONTROL_PLANE = "control_plane"
    EXTERNAL_SERVICE = "external_service"
```

### 6.4 ToolSpec

用途：表达工具在任务语义上的稳定身份，不关心运行在哪个平面。

```python
class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    description: str
    args_schema: dict[str, Any]
    source: str                     # "builtin" | "mcp" | "skill"
    capabilities: frozenset[str]
    effect_level: Literal["none", "local_mutation", "external_effect"]
    exposed_to_model: bool = True
    fast_path_eligible: bool = True
    usage_hint: str = ""
```

说明：

- `fast_path_eligible` 默认 True，表示当 effect_level 和 resource_claims 同时满足只读条件时可走 fast path。设为 False 可强制要求 Policy 检查（如外部只读工具有域限制或 quota 规则时）

约束：

- 不包含 session_kind
- 不包含路径根
- 不包含调度和中断策略
- 不包含具体执行器

### 6.5 ResourceClaim

用途：工具声明它需要独占或共享的资源，是 Scheduler 的唯一调度原语。

```python
class ResourceClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource: str                                          # e.g. "session", "workspace", "artifact-sync"
    mode: Literal["shared_read", "exclusive", "counted"] = "exclusive"
    max_concurrent: int = 1                                # 仅 counted 模式生效
```

说明：

- 同一 resource 的 `exclusive` claim 之间互斥
- `shared_read` 之间可并发
- `shared_read` 与 `exclusive` 互斥
- `counted` 允许同一 resource 上最多 `max_concurrent` 个并发持有者，超出排队
- 三种 mode 覆盖互斥、读写锁、信号量三类调度需求，不需要额外的 queue_group 层

### 6.6 ToolBinding

用途：表达某个逻辑工具在当前运行环境下的具体执行语义。

```python
class ToolBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    binding_key: str                # 格式: "{plane}:{tool_name}"，用于日志和指标
    plane: ToolPlane
    resource_claims: tuple[ResourceClaim, ...] = ()
    # --- Phase 2 字段（v1 使用默认值）---
    state_mode: Literal["stateless", "persistent"] = "stateless"
    stop_mode: Literal["cancellable", "best_effort", "non_cancellable"] = "cancellable"
```

说明：

- 不再包含 `session_kind`（由 RuntimeTopology.session_capabilities 统一承载）
- 不再包含 `queue_group`（由 resource_claims 替代）
- `binding_key` 格式固定为 `{plane}:{tool_name}`，用于调试、指标和审计
- Phase 2 字段在 v1 阶段使用默认值，Scheduler 真正需要精细调度时再启用

### 6.7 ToolInstance

用途：将逻辑语义、执行语义与真正执行器绑定为一个运行时工具实例。

```python
@dataclass(frozen=True)
class ToolInstance:
    tool_spec: ToolSpec
    tool_binding: ToolBinding
    tool_executor: Callable[[dict[str, Any]], Awaitable[ToolResult]]
```

### 6.8 ToolCatalog

用途：AgentKernel 真正消费的工具目录。静态基座 + 动态 overlay 两层结构。

```python
class ToolCatalog:
    """build_runtime() 编译静态基座，运行时 skill 触发追加 overlay。"""

    def __init__(self, base: dict[str, ToolInstance]) -> None:
        self._base = base                               # 不可变
        self._overlay: dict[str, ToolInstance] = {}     # 运行时扩展
        self._version: int = 0

    def register_overlay(self, tool: ToolInstance) -> None:
        """Skill 触发时调用，追加到 overlay 层。"""
        self._overlay[tool.tool_spec.tool_name] = tool
        self._version += 1

    def get_tool(self, tool_name: str) -> ToolInstance | None:
        return self._overlay.get(tool_name) or self._base.get(tool_name)

    def list_tools(self) -> list[ToolInstance]:
        merged = {**self._base, **self._overlay}
        return list(merged.values())

    def build_definitions(self) -> list[dict[str, Any]]:
        """合并 base + overlay，overlay 同名覆盖 base。"""
        merged = {**self._base, **self._overlay}
        return [
            _to_openai_definition(inst.tool_spec)
            for inst in merged.values()
            if inst.tool_spec.exposed_to_model
        ]

    @property
    def version(self) -> int:
        """Kernel 用 version 判断是否需要刷新 tool_definitions。"""
        return self._version
```

说明：

- `_base` 在 `build_runtime()` 阶段一次性编译完成，运行时不可变
- `_overlay` 承载 skill 触发的 MCP 工具懒注入，`register_overlay` 追加并递增 version
- Kernel 每轮循环前比对 `catalog.version`，仅在变更时重新调用 `build_definitions()` 发给 provider
- MCP overlay 工具的 plane 由 overlay factory 根据 SKILL.md 声明或 tool metadata 决定，不设全局默认值（计算类 MCP server 的副作用可能落在 workspace 而非 control plane）

System prompt 与 tool definitions 的一致性：

当前 ContextBuilder 在 build_runtime 时一次性将工具清单写入 system prompt 的 `# Available Tools` 段落。Overlay 新增工具后，tool_definitions（发给 provider 的 function calling schema）会通过 version 检测刷新，但 system prompt 中的工具说明段落不会同步更新。

Phase 1 策略：system prompt 的 `# Available Tools` 段落移除工具枚举，改为通用说明（如「使用 function calling 中声明的工具」）。工具的 description 和用法信息完全由 tool_definitions 承载。这消除了两个来源之间的不一致风险，也减少了 overlay 变更后的维护成本。

### 6.9 三层约束模型

设计初版将 Guard 和 Policy 混为一层。修订版明确拆分为三层，每层有确定的输入和职责：

#### Layer A: StructuralValidation

```python
class StructuralValidation:
    """参数校验 + binding 级前置检查。无状态，纯确定性。"""

    def validate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision: ...
```

职责：

- `args_schema` 类型和必填字段校验
- 参数路径规范化到允许的根目录
- 当前 topology 是否启用了所需 plane
- 当前 binding 在当前 session_capabilities 下是否可执行

#### Layer B: RunStateGuard

```python
class RunStateGuard(Protocol):
    """对话级运行态约束。有状态（per-run 积累）。"""

    def evaluate(self, ctx: GuardContext) -> GuardResult: ...
```

**Phase 1** GuardContext 保持当前接口不变：

```python
class GuardContext(BaseModel):
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    current_turn: int
    max_turns: int
    recent_calls: list[RecentCall]
```

**Phase 2 目标态** 扩展 GuardContext，承接从工具内部迁入的运行态约束：

```python
class GuardContext(BaseModel):
    tool_name: str
    tool_args: dict[str, Any]
    tool_call_id: str
    current_turn: int
    max_turns: int
    recent_calls: list[RecentCall]
    read_tracker: ReadTracker | None = None     # Phase 2 新增
```

Phase 1 职责（与当前一致）：

- 循环检测（当前 LoopDetectionGuard）
- 外部 Guard 扩展点

注意：turn 限制由 AgentKernel 的 `max_turns` 循环条件负责，不属于 Guard 职责。

Phase 2 新增职责：

- read-before-modify 检查（从 WriteTool/EditTool 内部迁入）

说明：read-before-modify 和循环检测本质上都是 per-run 运行态约束，终态应统一归入此层。但 Phase 1 不改动 Guard 接口，避免第一轮改造侵入过深。

#### Layer C: CapabilityPolicy

```python
class CapabilityPolicy(Protocol):
    """基于工具身份和拓扑的能力约束。确定性，不依赖运行态。"""

    def evaluate(
        self,
        runtime_topology: RuntimeTopology,
        tool_instance: ToolInstance,
        tool_args: dict[str, Any],
    ) -> ToolDecision: ...
```

Phase 1 职责：

- effect_level 约束（如禁止 external_effect 工具在某些拓扑下执行）
- plane 能力匹配（如 session_capabilities 不支持 upload 时禁止相关操作）

Phase 2 新增职责：

- 危险命令拦截（从 bash_tool 内部的 `_is_dangerous_command` 迁移到此层）
- 其他跨工具统一的确定性安全约束

说明：

- CapabilityPolicy 不处理运行态（循环、read-before-modify 等），那些属于 Layer B
- Phase 1 工具内部现有安全检查保持不动，与 CapabilityPolicy 并存
- Phase 2 在 ToolRunner 和 Scheduler 稳定后，再逐步把共性安全检查从工具内部外提到此层

#### ToolDecision

```python
class ToolDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: Literal["allow", "deny"]
    reason: str = ""
    guidance: str = ""                  # deny 时注入到 ToolMessage，辅助 LLM 修正
```

### 6.10 ToolResult

```python
class ToolResult(BaseModel):
    status: Literal["success", "error"] = "success"
    content: str = ""                                       # 供模型消费
    payload: dict[str, Any] = Field(default_factory=dict)   # 结构化数据
    meta: dict[str, Any] = Field(default_factory=dict)      # 运行时 & 调试信息
```

说明：

- `content` 供模型直接消费
- `payload` 替代原 `info`，供结构化处理和后续推理消费
- `meta` 供运行时附加信息与调试信息
- 不预设 artifacts / metrics / guidance 等顶层字段；HPC 场景的制品列表等通过 `payload` 的约定键承载

---

## 7. 多执行平面模型

### 7.1 平面定义

| 平面 | 执行方式 | 典型工具 |
|------|----------|----------|
| `SESSION_SHELL` | `session.exec_bash()` | execute_bash, list_dir, glob, grep |
| `SESSION_FS` | `session.read_file()` / `session.write_file()` | read_file, write_file, edit_file |
| `CONTROL_PLANE` | controller 本地执行 | task_*, web_search, web_fetch, spawn |
| `EXTERNAL_SERVICE` | 外部服务调用 | monitor_job, 未来 HPC submit/cancel/status |

### 7.2 平面与资源的关系

平面是工具的语义分类，用于日志、审计和可视化。但平面之间并非天然独立：

- `execute_bash`、`read_file`、`write_file` 共享同一个 `session` 实例和 `workspace` 目录
- `monitor_job` 会下载制品到本地后通过 SFTP push 回远端 workspace，跨越 EXTERNAL_SERVICE 和 SESSION_FS 的边界

因此，调度不依赖平面独立性假设，而是依赖工具声明的 `resource_claims`。平面仅作分类标签保留。

---

## 8. 调度模型

### 8.1 核心机制：ResourceClaim 统一调度

ToolScheduler 基于工具声明的 ResourceClaim 进行调度，三种 mode 覆盖所有调度需求：

- `exclusive`：同一 resource 上互斥，一次只允许一个持有者
- `shared_read`：同一 resource 上可并发，但与 `exclusive` 互斥
- `counted`：同一 resource 上允许最多 `max_concurrent` 个并发持有者，超出排队；与 `exclusive` 互斥
- 不同 resource 之间天然独立

不引入额外的 queue_group 层。ResourceClaim 同时覆盖互斥（exclusive）、读写锁（shared_read + exclusive）和信号量/限流（counted）三类调度需求。

### 8.2 内建工具的 Resource Claims

| 工具 | resource_claims |
|------|----------------|
| `execute_bash` | `("session", exclusive)` |
| `list_dir` | `("session", exclusive)` |
| `glob` | `("session", exclusive)` |
| `grep` | `("session", exclusive)` |
| `read_file` | `("workspace", shared_read)` |
| `write_file` | `("workspace", exclusive)` |
| `edit_file` | `("workspace", exclusive)` |
| `task_create` | `("task-store", exclusive)` |
| `task_get` | `("task-store", shared_read)` |
| `task_list` | `("task-store", shared_read)` |
| `task_update` | `("task-store", exclusive)` |
| `task_complete` | `("task-store", exclusive)` |
| `web_search` | `("web", counted, max_concurrent=3)` |
| `web_fetch` | `("web", counted, max_concurrent=3)` |
| `spawn` | `("spawn", counted, max_concurrent=2)` |
| `monitor_job` | `("workspace", exclusive), ("artifact-sync", exclusive)` |

说明：

- `execute_bash` 等 shell 工具声明 `session: exclusive`，因为当前 SSHSession 和 LocalSession 都是 stateless per-channel，但共享同一个 session 实例和工作目录
- `read_file` 声明 `workspace: shared_read`，多个读操作可并发
- `monitor_job` 声明 `workspace: exclusive` + `artifact-sync: exclusive`，因为它会下载制品并 SFTP push 回远端 workspace
- `web_search` / `web_fetch` 声明 `web: counted, max_concurrent=3`，允许最多 3 个并发网络请求
- `spawn` 声明 `spawn: counted, max_concurrent=2`，严格限制子 agent 并发数

### 8.3 Phase 1 保守策略

Phase 1 采用保守策略：

- shell 工具串行（session: exclusive 天然保证）
- fs 读可并发，写串行（workspace 的读写锁保证）
- control_plane 工具按各自 resource 调度
- external_service 工具与 workspace 互斥（monitor_job 声明保证）

Phase 2 可根据 SessionCapabilities 放宽（如 persistent shell 下支持 shell 并发）。

---

## 9. 工具执行主链

### 9.1 完整链路

```
LLM 返回 tool_calls
  │
  ├─ Kernel: ToolCatalog.get_tool(tool_name) 查找 ToolInstance
  │
  ├─ Layer A: StructuralValidation
  │    args_schema 校验 / 路径规范化 / plane 启用检查 / session_capabilities 匹配
  │
  ├─ Layer B: RunStateGuard
  │    循环检测 / [Phase 2: read-before-modify]
  │
  ├─ Layer C: CapabilityPolicy
  │    effect_level 约束 / 能力匹配 / [Phase 2: 危险命令拦截]
  │
  ├─ [fast path: effect_level="none" 且 claims 全 shared_read 且 fast_path_eligible → 跳过 Scheduler（仅 read_file/task_get/task_list 等）]
  │
  ├─ Scheduler: 按 resource_claims 获取槽位 (exclusive / shared_read / counted)
  │
  ├─ tool_executor(tool_args)
  │
  ├─ 归一化 ToolResult
  │
  ├─ Scheduler: 释放槽位
  │
  ├─ 映射为 ToolMessage
  │
  └─ post hook + 事件发射
```

### 9.2 Fast Path

对同时满足以下条件的工具，跳过 Scheduler 获取槽位（仍经过 Layer C Policy 检查）：

- `effect_level = "none"`
- `resource_claims` 全部为 `shared_read`（不含 `exclusive` 或 `counted`）
- `fast_path_eligible = True`（ToolSpec 显式标记）

典型受益工具：`read_file`、`task_get`、`task_list`

注意：`glob`、`grep`、`list_dir` 虽然 `effect_level = "none"`，但它们通过 `session.exec_bash()` 执行，声明了 `(session, exclusive)`，不满足 fast path 条件，仍需经过 Scheduler 串行调度。

说明：fast path 不跳过 CapabilityPolicy，因为未来只读工具也可能需要拓扑级策略检查（如域限制、quota 规则）。`fast_path_eligible` 提供显式 opt-out 能力，对需要强制 Policy + Scheduler 的只读工具设为 False 即可。

### 9.3 错误处理

每一层的失败行为明确定义：

| 阶段 | 失败行为 |
|------|----------|
| Catalog 查找失败 | 返回 `ToolResult(status="error", content="Unknown tool: {name}")` |
| Layer A 校验失败 | 返回 `ToolResult(status="error", content=decision.reason, meta={"layer": "structural"})` |
| Layer B Guard deny | 返回 `ToolResult(status="error", content=result.reason, meta={"layer": "guard"})` + 触发 on_guard_blocked hook |
| Layer C Policy deny | 返回 `ToolResult(status="error", content=decision.reason, meta={"layer": "policy", "guidance": decision.guidance})` |
| Scheduler 超时 | 返回 `ToolResult(status="error", content="Scheduling timeout", meta={"layer": "scheduler"})` |
| Executor 异常 | 返回 `ToolResult.from_error(tool_name, exception)` |

所有失败路径统一产出 ToolResult，Kernel 不需要区分失败来源。`meta["layer"]` 用于调试和审计。fast path 工具跳过 Scheduler，因此不会产生 Scheduler 超时错误。

---

## 10. 内建工具总表

| 工具 | capabilities | effect_level | plane | resource_claims |
|------|-------------|--------------|-------|-----------------|
| `execute_bash` | `shell.execute` | `local_mutation` | SESSION_SHELL | `(session, exclusive)` |
| `list_dir` | `workspace.list` | `none` | SESSION_SHELL | `(session, exclusive)` |
| `glob` | `workspace.search.path` | `none` | SESSION_SHELL | `(session, exclusive)` |
| `grep` | `workspace.search.content` | `none` | SESSION_SHELL | `(session, exclusive)` |
| `read_file` | `workspace.read` | `none` | SESSION_FS | `(workspace, shared_read)` |
| `write_file` | `workspace.write` | `local_mutation` | SESSION_FS | `(workspace, exclusive)` |
| `edit_file` | `workspace.write` | `local_mutation` | SESSION_FS | `(workspace, exclusive)` |
| `task_create` | `task.write` | `local_mutation` | CONTROL_PLANE | `(task-store, exclusive)` |
| `task_get` | `task.read` | `none` | CONTROL_PLANE | `(task-store, shared_read)` |
| `task_list` | `task.read` | `none` | CONTROL_PLANE | `(task-store, shared_read)` |
| `task_update` | `task.write` | `local_mutation` | CONTROL_PLANE | `(task-store, exclusive)` |
| `task_complete` | `task.write` | `local_mutation` | CONTROL_PLANE | `(task-store, exclusive)` |
| `web_search` | `web.search` | `external_effect` | CONTROL_PLANE | `(web, counted, 3)` |
| `web_fetch` | `web.fetch` | `external_effect` | CONTROL_PLANE | `(web, counted, 3)` |
| `spawn` | `agent.spawn` | `external_effect` | CONTROL_PLANE | `(spawn, counted, 2)` |
| `monitor_job` | `job.monitor`, `artifact.download` | `external_effect` | EXTERNAL_SERVICE | `(workspace, exclusive), (artifact-sync, exclusive)` |

---

## 11. 类关系图

```
Playground
  → PlaygroundContext (含 Session + SessionCapabilities)

Exp
  → derives RuntimeTopology (含 SessionCapabilities)
  → builds ToolSpec set
  → resolves ToolBinding (含 ResourceClaim)
  → compiles ToolCatalog (静态 base 层)
  → creates CapabilityPolicy
  → configures RunStateGuard (Phase 1: 循环检测; Phase 2: + read-before-modify)
  → outputs AgentRuntimeSpec

AgentRuntimeSpec
  has:
    - runtime_topology: RuntimeTopology
    - tool_catalog: ToolCatalog
    - capability_policy: CapabilityPolicy
    - guards: list[RunStateGuard]
    - hooks: list[Hook]
    - llm_provider
    - system_prompt

AgentKernel
  uses AgentRuntimeSpec
  checks catalog.version per turn
  delegates tool execution to ToolRunner

ToolRunner
  uses:
    - ToolCatalog
    - StructuralValidation
    - RunStateGuard (GuardPipeline)
    - CapabilityPolicy
    - ToolScheduler

ToolCatalog
  base: dict[str, ToolInstance]     (immutable, build-time)
  overlay: dict[str, ToolInstance]  (mutable, skill-triggered)
  version: int

ToolInstance
  has:
    - ToolSpec
    - ToolBinding
    - tool_executor

ToolScheduler
  schedules by: ResourceClaim (exclusive / shared_read / counted)
```

---

## 12. 主调用时序图

```
User
  → AgentKernel.run(runtime_spec, task)

AgentKernel
  → check catalog.version, refresh tool_definitions if changed
  → call provider with system_prompt + messages + tool_definitions

Provider
  → returns assistant response with tool_calls

AgentKernel
  → append AssistantMessage(tool_calls=...)
  → for each tool_call:
       → ToolRunner.execute(tool_call, runtime_spec)

ToolRunner
  → ToolCatalog.get_tool(tool_name)          ... miss → error ToolResult
  → StructuralValidation.validate(...)       ... deny → error ToolResult
  → GuardPipeline.evaluate(...)              ... deny → error ToolResult + on_guard_blocked
  → CapabilityPolicy.evaluate(...)           ... deny → error ToolResult
  → [fast path: effect_level=none && all shared_read && fast_path_eligible → skip scheduler]
  → ToolScheduler.acquire(resource_claims)   ... timeout → error ToolResult
  → tool_executor(tool_args)                 ... exception → ToolResult.from_error
  → ToolScheduler.release(...)
  → return ToolResult

AgentKernel
  → append ToolMessage(content=tool_result.content)
  → run post hooks
  → emit events
  → continue loop
```

---

## 13. 分阶段实施计划

### Phase 1: 核心骨架

目标：在不破坏现有功能的前提下建立新的执行主链。

引入：
- `SessionCapabilities` — Session Protocol 增加 capabilities 属性
- `RuntimeTopology` — 从 PlaygroundContext 派生
- `ToolSpec` — 从现有 Tool Protocol 提取逻辑语义
- `ToolBinding` — 仅填 plane 和 resource_claims，其余使用默认值
- `ToolInstance` — 组合 ToolSpec + ToolBinding + executor
- `ToolCatalog` — base + overlay，内部封装现有 ToolRegistry 作为兼容 facade
- `ToolRunner` — 从 Kernel 中提取执行主链
- `ToolResult` — 升级为 status + content + payload + meta

ToolRegistry 兼容策略：

Phase 1 不直接替换 ToolRegistry，而是让 ToolCatalog 内部持有一个 ToolRegistry 实例作为 facade：
- `ToolCatalog._registry` 保持现有 ToolRegistry 对象，对外不暴露
- `ToolCatalog.register_overlay()` 内部调用 `_registry.register(tool, source='mcp')`，保持 SkillTool 的 `on_skill_hit` 回调链不变
- `ToolCatalog.get_tool()` 将 registry 中的 Tool 包装为 ToolInstance 返回（适配层）
- `ToolCatalog.build_definitions()` 委托给 `_registry.get_tool_definitions()`
- ContextBuilder 继续消费 `_registry.all_tools` 生成 system prompt 的工具段落

这样 Phase 1 的改动面收窄为：Kernel 消费 ToolCatalog（而非直接消费 ToolRegistry），ToolRunner 从 ToolCatalog 获取 ToolInstance。现有的 ContextBuilder、SkillTool、Exp 的 MCP 注入路径全部不动。

Phase 2 再将 ToolRegistry 降级为纯存储层，ToolCatalog 接管所有上层消费接口。

保留：
- GuardPipeline 接口和行为完全不变（不扩展 GuardContext）
- 工具内部安全检查（bash 危险命令、read-before-modify）保持不动
- CapabilityPolicy Phase 1 仅处理 effect_level 和 plane/capability 匹配，不承接工具内部迁移

调度：
- Phase 1 Scheduler 实现为简单的 resource 读写锁/信号量，保守策略

### Phase 2: 约束迁移与调度增强

目标：在 ToolRunner 和 Scheduler 稳定后，将散落的安全逻辑集中到三层约束模型。

迁移：
- 扩展 GuardContext 增加 ReadTracker，将 read-before-modify 检查从 WriteTool/EditTool 迁入 RunStateGuard
- 将 bash_tool 的 `_is_dangerous_command` 迁入 CapabilityPolicy
- 将其他跨工具统一的确定性安全约束迁入 CapabilityPolicy

启用：
- ToolBinding 的 state_mode、stop_mode 字段
- Scheduler 根据 SessionCapabilities 动态调整并发策略

### Phase 3: 高级调度

目标：支持更复杂的调度场景。

可选方向：
- persistent shell 下的 shell 并发
- web_fetch 并发上限
- spawn:agent 严格限并发
- 基于 SessionCapabilities 的自适应调度

---

## 14. 模块与文件布局

```
matmaster/
  types/
    context.py              # PlaygroundContext（现有）
    runtime.py              # AgentRuntimeSpec（扩展）
    topology.py             # RuntimeTopology, SessionCapabilities, ToolPlane
    tool_spec.py            # ToolSpec, ToolBinding, ResourceClaim, ToolInstance
    tool_result.py          # ToolResult（升级）
    tool_decision.py        # ToolDecision
    events.py               # 现有事件类型
    session.py              # Session Protocol + SessionCapabilities

  tools/
    tool_catalog.py         # ToolCatalog (base + overlay)
    tool_compiler.py        # build_runtime() 阶段的 ToolSpec/ToolBinding 编译逻辑
    tool_definitions.py     # OpenAI 格式 tool definition 生成
    tool_result.py → 迁移到 types/tool_result.py

    builtin/
      base.py               # BuiltinTool 基类（现有）
      bash_tool.py           # 现有，Phase 2 安全检查迁出
      read_tool.py
      write_tool.py
      edit_tool.py
      list_dir_tool.py
      glob_tool.py
      grep_tool.py
      web_search.py
      web_fetch.py
      spawn_tool.py
      monitor_job/           # 现有子目录结构

    mcp/
      lazy_mcp.py            # LazyMCPTool（现有）
      overlay_factory.py     # on_skill_hit 时构造 ToolInstance 的工厂

    skill/
      skill_tool.py          # SkillTool（现有）

  core/
    exp.py                   # Exp（扩展 build_runtime）
    agent.py                 # AgentKernel（精简，委托 ToolRunner）
    tool_runner.py           # ToolRunner（新增）
    tool_scheduler.py        # ToolScheduler（新增）
    guard_pipeline.py        # GuardPipeline（扩展上下文）
    structural_validation.py # StructuralValidation（新增）
    capability_policy.py     # CapabilityPolicy（新增，Phase 2 启用）
    hooks.py                 # Hook（现有）
```

说明：

- builtin 工具保持扁平目录结构，不按 plane 嵌套子目录（避免为简单工具创建深层路径）
- `tool_compiler.py` 承载从 TOML 配置 + Session 信息编译 ToolSpec/ToolBinding 的逻辑，是 `Exp.build_runtime()` 的核心辅助模块
- `overlay_factory.py` 承载 skill 触发时从 MCP schema 构造完整 ToolInstance 的逻辑，根据 SKILL.md 声明或 tool metadata 决定每个工具的 plane 和 resource_claims

---

## 15. 与当前实现的关键差异

| 维度 | 当前实现 | 完成态 |
|------|----------|--------|
| session 能力 | `session_type` 字符串标签 | `SessionCapabilities` 由 session 自报告 |
| 执行拓扑 | 隐含在 PlaygroundContext 中 | `RuntimeTopology` 显式最小合同 |
| 工具抽象 | 单层 Tool Protocol | `ToolSpec + ToolBinding + ToolInstance` |
| 工具目录 | 可变 ToolRegistry | `ToolCatalog` base + overlay |
| 执行链 | Kernel 内联 guard → gather → post-hook | `ToolRunner` 独立编排 |
| 调度 | `asyncio.gather()` 全并发 | ResourceClaim (exclusive / shared_read / counted) |
| 安全约束 | 散落在工具内部 + Guard | 三层约束（Structural / RunState / Capability） |
| 工具结果 | `status + content + info` | `status + content + payload + meta` |

---

## 16. 设计决策记录

### D-01: 为什么用 SessionCapabilities 而不是 session_kind 推导

初版设计用 `session_kind = "ssh"` 推导 `shell_mode = persistent`，但当前 matmaster 主线的 SSHSession.exec_bash() 是 per-channel stateless 执行。硬编码推导会把核心语义立错。改为由 Session 实例自报告能力，未来引入 tmux 持久 shell 时只需新 Session 实现报告不同的 capabilities，上层无需改动。

### D-02: 为什么用 ResourceClaim 而不是 plane + queue_group（或双层模型）

初版假设 plane 之间天然独立，但 monitor_job 通过 SFTP push 跨越了 EXTERNAL_SERVICE 和 SESSION_FS 的边界。多个工具共享同一个 session 实例和 workspace 目录。ResourceClaim 将调度锚定在实际共享的资源上而不是分类标签上，正确处理跨 plane 的资源竞争。

曾考虑 queue_group（粗粒度限流）+ resource_claims（细粒度冲突）的双层模型，但双层引入了两套规则之间谁优先的歧义。通过扩展 ResourceClaim 增加 `counted` mode（信号量语义），单一原语即可覆盖互斥、读写锁和限流三类调度需求，不需要额外的 queue_group 层。

### D-03: 为什么 ToolCatalog 是 base + overlay 而不是纯静态

当前 skill 路径依赖运行时动态扩展 tool surface：use_skill 触发 on_skill_hit → MCP 工具懒注入 registry → 下一轮 LLM 可见新工具。纯静态 Catalog 要么让 skill 机制失效，要么被迫启动时全量灌入所有 MCP 工具（token 成本不可接受）。base + overlay 保持了按需加载的优势。

### D-04: 为什么拆分三层约束而不是 Guard + Policy 两层

Guard 和 Policy 的输入上下文本质不同：循环检测需要 recent_calls（运行态），read-before-modify 需要 ReadTracker（per-run 状态），这些无法通过 (topology, instance, args) 三元组推出。如果把运行态约束硬塞进 Policy，Policy 会偷偷依赖全局状态，破坏其确定性语义。三层拆分让每层的输入合同干净且可测试。

### D-05: 为什么 ToolResult 只保留四个字段

初版预设了 artifacts、metrics、guidance 三个顶层字段，但当前没有工具需要它们。HPC 制品列表等通过 payload 的约定键承载即可。过早膨胀 ToolResult 会让所有工具实现面对不必要的字段复杂度。当 payload 约定键出现明确的跨工具重复模式时，再提升为顶层字段。

### D-06: 为什么保留 fast path 但不跳过 Policy

对 effect_level=none 且 resource_claims 全 shared_read 且 fast_path_eligible=True 的只读工具，跳过 Scheduler 获取槽位。这类工具（read_file, glob, grep 等）是 agent 循环中调用频率最高的，Scheduler 的锁竞争开销不合理。

fast path 不跳过 CapabilityPolicy：虽然当前只读工具不需要 Policy 检查，但未来只读工具也可能需要拓扑级策略（域限制、quota 规则）。通过 ToolSpec 上的 `fast_path_eligible` 标记提供显式 opt-out 能力，而不是纯靠 effect_level + claim mode 推断。

### D-07: 为什么约束迁移延后到 Phase 2

三层约束模型（StructuralValidation / RunStateGuard / CapabilityPolicy）是正确的终态方向，但 Phase 1 不迁移工具内部现有安全检查（bash 危险命令、read-before-modify），原因：

- 迁移 read-before-modify 需要扩展 GuardContext 接口（加入 ReadTracker），这意味着改动 Guard Protocol，影响所有 Guard 实现
- 迁移 bash 危险命令需要 CapabilityPolicy 能理解 bash 命令的语义结构，这是非平凡的规则迁移
- Phase 1 的核心目标是建立 ToolRunner 执行链和 ResourceClaim 调度，不应同时承担约束层的重构
- 当 ToolRunner + Scheduler 稳定后，Phase 2 再做约束迁移，风险可控

### D-08: 为什么 MCP overlay 工具不设全局默认 plane

MCP 工具的调用从 controller 本地发起（像 control_plane），但副作用可能落在不同域：计算类 MCP server 的工具可能读写 workspace 文件（像 session_fs），监控类可能触发外部服务（像 external_service）。统一默认为 CONTROL_PLANE 会导致 Scheduler 无法正确建模资源冲突。overlay factory 应根据 SKILL.md 中的声明或 tool metadata 逐工具决定 plane 和 resource_claims。

### D-09: 为什么 system prompt 不再枚举工具列表

当前 ContextBuilder 在 build_runtime 时将工具清单写入 system prompt 的 `# Available Tools` 段落。当 overlay 新增工具后，tool_definitions（function calling schema）通过 catalog.version 刷新，但 system prompt 中的工具段落不会同步更新，导致模型同时看到新的 function definitions 和旧的工具说明。

Phase 1 的解决方案是移除 system prompt 中的工具枚举，改为通用说明。工具的 description 和用法信息完全由 tool_definitions 承载。这消除了两个信息源之间的不一致风险。

### D-10: 为什么 Phase 1 用 ToolRegistry 作为 ToolCatalog 的内部 facade

直接替换 ToolRegistry 会同时影响 ContextBuilder（消费 all_tools 生成 prompt）、SkillTool（on_skill_hit 回调链调用 registry.register）、Exp 的 MCP 注入路径。Phase 1 的核心目标是引入 ToolRunner 和 ToolCatalog 的上层消费接口，不应同时重构所有下游消费者。让 ToolCatalog 内部持有 ToolRegistry 实例作为兼容 facade，仅改变 Kernel 的消费方式，将迁移面收窄到最小。Phase 2 在执行链稳定后再将 ToolRegistry 降级为纯存储层。
