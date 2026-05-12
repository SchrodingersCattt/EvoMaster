# RunContext 家族骨架设计（Spec 1 / 9）

> **Status**: Draft, 待用户进一步细化
>
> **Series**: 这是"持久事件子系统 + run_meta 项目级拆解"系列的第 1 个 spec。
>
> **依赖**: 本 spec 接续 [2026-05-10 RuntimePorts 与 run_meta 边界重构设计](./2026-05-10-runtime-ports-run-meta-design.md)。该设计已将 callable 能力端口从 `run_meta` 拆出到 `RuntimePorts`。本 spec 处理**剩余的被动数据**侧。
>
> **本 spec 不改变任何运行行为**——只新增 typed bundle 定义、注入入口、与现有 `run_meta` 的兼容投影。迁移与改造留给 Spec 2–9。

---

## 背景

`RuntimePorts` 重构（2026-05-10）解决了 `run_meta` 持有 callable 的部分。但 `run_meta: dict[str, Any]` 至今仍承担"被动数据"角色，目前已知 key 至少包括：

| 概念簇 | 当前 run_meta key |
|---|---|
| 运行身份 | `run_dir`, `task_id`, `session_id`, `spawn_id` |
| 运行时配置 | `legal_mcp_servers`, `schemas_by_server`, `figure_upload_config` |
| 当前轮输入 | `current_input_context`, `current_user_images` |
| 历史状态投影 | `bohrium_rebuild_events`, `attachment_manifest`, `active_skills` |

这些数据共用同一个 `dict[str, Any]`，导致：

- 字符串 key 通信：消费者通过 `run_meta.get("...")` 取值，重构容易漏改。
- 类型缺失：值是 `Any`，IDE 无法导航、mypy 无法验证。
- 概念混合：身份、配置、输入、状态被同一个容器盛着，跨层传递时全量复制。
- 写入面失控：任何上游层都能往里加 key，没有"谁能放什么"的边界。
- 与"持久事件子系统"（Spec 3）耦合：要给持久状态加一个组件，按现有模式只能继续往 `run_meta` 塞一个新 key，会让问题恶化。

## 目标

- 把 `run_meta` 中**被动数据**按概念簇拆成一组 typed、frozen 的小 bundle dataclass。
- 建立"该往哪个 bundle 加字段"的判定规则，让未来的 spec 4–8 都按同一模板独立推进。
- 定义两条注入轨道（显式参数 + contextvars 兜底）的使用边界。
- 给 `PlaygroundContext` 瘦身留好出口：物理环境字段（workdir、session、cache、env）保留，`run_meta` 字段最终被 typed bundle 取代。
- 与 `RuntimePorts` 划清职责：RuntimePorts 是 callable 能力端口，bundle 是被动数据容器，二者**不允许互相承载**。

## 非目标

- **不**迁移任何 `run_meta.get(...)` 读者到 typed bundle。读者迁移是 **Spec 2**。
- **不**删除 `PlaygroundContext.run_meta` 字段。删除是 **Spec 9**。
- **不**实现持久事件子系统（Store / Slot / Renderable）。那是 **Spec 3**。
- **不**改变 `Playground.prepare()` / `Exp.build_runtime()` / `AgentRunService.run_agent()` 等任何调用签名。
- **不**新增 callable 字段到任何 bundle——callable 走 `RuntimePorts`，这是 2026-05-10 已确立的边界。
- **不**给 bundle 增加 `extra` / `metadata` / `state` / `payload` 等兜底字段。

## 术语与边界

### Bundle

本 spec 引入的"typed 被动数据容器"统称。每个 bundle 是 `@dataclass(frozen=True)`，字段类型显式，**禁止 `dict[str, Any]` 与 `Any`**。

### 与 RuntimePorts 的差异

| 维度 | RuntimePorts（2026-05-10） | Bundle（本 spec） |
|---|---|---|
| 承载内容 | Protocol、callback、factory、sink、barrier 等**不可序列化能力** | 标识、配置、输入、状态投影等**可序列化数据** |
| 字段语义 | "我能做 X" | "我是 Y / 我有 Y" |
| 跨层流向 | 服务层 → 核心层（依赖倒置） | 上游构造 → 下游消费（数据流） |
| 是否进 `model_dump()` | 否 | 是（可被序列化、可入 DB、可入测试 fixture） |

**红线**：bundle 不允许出现 callable / Protocol 字段；如有需要请走 RuntimePorts 或独立设计。

### Bundle 大类

按概念簇分为 5 类。**类是分类锚点，不是代码层级**——bundle 不需要按大类做继承或包装。

| 大类 | 代表 bundle（本 spec 落地） | 后续 spec |
|---|---|---|
| **Physical / Infrastructural**（物理环境） | `PlaygroundContext`（保留现状） | Spec 8/9 瘦身 |
| **Identity**（这次 run 是谁） | `RunIdentity`：`run_id` / `session_id` / `task_id` / `spawn_id` / `run_dir` | Spec 8 |
| **Runtime Configuration**（这次 run 怎么跑） | `MCPRuntimeConfig`、`FigureUploadConfig`、`CompactionRuntimeConfig` 占位 | Spec 4, 6 |
| **Runtime Inputs**（这次 run 收到了什么） | `CompactionInputBundle`（含 `current_input_context`、`current_user_images`） | Spec 5 |
| **State Projections**（已发生事情的内存投影） | `PersistentStateBundle`（占位，留给 Spec 3 填充）、`BohriumRebuildBundle` | Spec 3, 7 |

本 spec 只**落地占位 dataclass**，不指定字段细节（细节由各自后续 spec 决定）。

## 推荐架构

### 模块布局

```text
matmaster/run/
├── __init__.py
├── identity.py            # RunIdentity
├── runtime_config.py      # MCPRuntimeConfig / FigureUploadConfig / CompactionRuntimeConfig
├── inputs.py              # CompactionInputBundle
├── state.py               # PersistentStateBundle（占位）
├── bohrium.py             # BohriumRebuildBundle
├── _ambient.py            # contextvars 入口 + install/uninstall API
└── compat.py              # bundle → legacy run_meta dict 投影（过渡期）
```

每个 bundle 模块只包含 dataclass 定义与必要的小型 helper（如 `from_legacy_run_meta` 工厂方法）。**不**包含业务逻辑、异步函数、与服务层 / 数据库交互的代码。

### 命名约定

- Bundle 类名：以业务概念命名 + `Bundle` / `Config` / `Identity` 后缀。
  - `RunIdentity` ✅（语义清晰）
  - `MCPRuntimeConfig` ✅（明确是配置）
  - `CompactionInputBundle` ✅（明确是输入）
  - `RunData` ❌（兜底命名，违反职责单一）
  - `RunContext` ❌（容易被理解为 god 容器，且与现有概念混淆）
- 字段名：表达"是什么"，避免"how"。
  - `legal_servers` ✅
  - `mcp_servers_filter` ✅
  - `mcp_filter_options` ❌（"options" 是兜底语义）

### 注入机制

**双轨制**：

#### A 轨：显式参数（默认）

层间数据传递通过显式函数参数。每一层只接收**自己确实要用**的 bundle，不参与的不传。

```python
# Worker 入口
def handle_chat_send_job(payload: ChatSendJobPayload) -> None:
    identity = RunIdentity.from_job(payload)
    runtime_cfg = build_runtime_config_set(payload)
    inputs = CompactionInputBundle.from_job(payload)
    state = build_persistent_state_bundle(history=...)
    pg_ctx = playground_manager.prepare(identity=identity)
    with install_ambient_run_context(state=state, identity=identity):
        agent_run_service.run_agent(
            identity=identity,
            runtime_cfg=runtime_cfg,
            inputs=inputs,
            state=state,
            pg_ctx=pg_ctx,
        )

# Service 层 → Core 层
def run_agent(*, identity, runtime_cfg, inputs, state, pg_ctx) -> None:
    runtime = exp.build_runtime(pg_ctx, identity=identity, runtime_cfg=runtime_cfg,
                                inputs=inputs, state=state)
    ...
```

**好处**：
- IDE 可导航、mypy 可验证。
- 一眼能看到这一层用了哪些 bundle。
- 测试时直接用 kwargs 注入 fake。

#### B 轨：contextvars（仅深层站点）

少数被 N 处调用、不便逐层加参数的深层函数（典型例：`events_service.persist_event` 被工具层、kernel 层、stream 层多处直接或间接调用），用 contextvars 读取当前 run 的 bundle。

```python
# matmaster/run/_ambient.py
from contextlib import contextmanager
from contextvars import ContextVar

_ambient_identity: ContextVar[RunIdentity | None] = ContextVar(
    "matmaster_run_identity", default=None
)
_ambient_state: ContextVar["PersistentStateBundle | None"] = ContextVar(
    "matmaster_run_state", default=None
)


@contextmanager
def install_ambient_run_context(
    *,
    identity: RunIdentity | None = None,
    state: "PersistentStateBundle | None" = None,
):
    tokens: list[tuple[ContextVar, object]] = []
    if identity is not None:
        tokens.append((_ambient_identity, _ambient_identity.set(identity)))
    if state is not None:
        tokens.append((_ambient_state, _ambient_state.set(state)))
    try:
        yield
    finally:
        for cv, tok in tokens:
            cv.reset(tok)


def current_run_identity() -> RunIdentity | None:
    return _ambient_identity.get()


def current_persistent_state() -> "PersistentStateBundle | None":
    return _ambient_state.get()
```

**红线**：
- contextvars **永远只读**于叶子站点。**写**只在入口（Worker / AgentRunService）。
- contextvars 不是"省事的隐式参数"；只用于"逐层加参数会污染 N 个接口"的少数情况。
- 上游显式传递 + contextvars 同时进行：bundle 走显式参数下行；同时入口层把它装进 ambient，让深层叶子能读。两条路同源同步。

### 与 `PlaygroundContext` 的关系

- 本 spec 中 `PlaygroundContext` **不动**——它仍持有 `run_meta` 字段（兼容期需要）。
- 新代码禁止往 `PlaygroundContext.run_meta` 加 key。
- Spec 9 收尾时删除 `run_meta` 字段，`PlaygroundContext` 仅保留物理环境字段（`workdir / cache_area / env_vars / session / archival / execution_workdir / session_type`）。

### 与 `AgentRuntimeSpec.meta` 的关系

`AgentRuntimeSpec.meta` 在 2026-05-10 设计里被定位为"kernel 需要的被动 metadata"。本 spec 不改变这一点。后续 spec 在迁移 run_meta key 时，凡是要喂给 kernel 的，对应字段也需要从 `AgentRuntimeSpec.meta` 字典化提升为 bundle 引用。

## 兼容层（compat shim）

```python
# matmaster/run/compat.py
def build_legacy_run_meta(
    *,
    identity: RunIdentity | None = None,
    runtime_cfg: "RuntimeConfigSet | None" = None,
    inputs: "CompactionInputBundle | None" = None,
    state: "PersistentStateBundle | None" = None,
    bohrium: "BohriumRebuildBundle | None" = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """从 typed bundle 投影出旧式 run_meta dict，给未迁移的读者读。

    单向：bundle → dict。
    Spec 2 把读者全部迁移完毕后，本函数可在 Spec 9 一并删除。
    新代码不许调用本函数。
    """
    out: dict[str, Any] = {}
    if identity is not None:
        out["run_dir"] = str(identity.run_dir)
        out["task_id"] = identity.task_id
        out["session_id"] = identity.session_id
        if identity.spawn_id is not None:
            out["spawn_id"] = identity.spawn_id
    if runtime_cfg is not None:
        if runtime_cfg.mcp is not None:
            out["legal_mcp_servers"] = runtime_cfg.mcp.legal_servers
            out["schemas_by_server"] = runtime_cfg.mcp.schemas_by_server
        if runtime_cfg.figure_upload is not None:
            out["figure_upload_config"] = runtime_cfg.figure_upload.to_legacy()
    if inputs is not None and inputs.current_input is not None:
        out["current_input_context"] = inputs.current_input.to_payload()
    if bohrium is not None and bohrium.events:
        out["bohrium_rebuild_events"] = list(bohrium.events)
    if extra:
        for k, v in extra.items():
            if k in out:
                raise ValueError(f"compat shim: legacy key {k!r} already provided")
            out[k] = v
    return out
```

**约束**：
- 投影是**单向的**：bundle → dict。**禁止**反向（dict → bundle 不在本 spec 提供）。反向构造是 Spec 2 的事，且只在迁移期使用。
- 新增 bundle 时同时更新本函数；删除 run_meta key 时同时清理本函数。
- 测试覆盖：每个 bundle 都要有 `test_build_legacy_run_meta_<bundle>_round_trips` 用例，验证投影出的 dict 与现有 run_meta 字段对齐。

## 文件结构

```text
matmaster/run/
├── __init__.py            # 导出 install_ambient_run_context, current_*, build_legacy_run_meta
├── identity.py            # RunIdentity
├── runtime_config.py      # MCPRuntimeConfig, FigureUploadConfig, CompactionRuntimeConfig
│                          #   + RuntimeConfigSet 聚合 dataclass（仅承载上述子配置引用）
├── inputs.py              # CompactionInputBundle
├── state.py               # PersistentStateBundle（占位，Spec 3 填字段）
├── bohrium.py             # BohriumRebuildBundle
├── _ambient.py            # _ambient_* ContextVar + install_ambient_run_context + current_*
└── compat.py              # build_legacy_run_meta
```

**说明**：
- `RuntimeConfigSet` 是一个**薄聚合**，只把 `mcp / figure_upload / compaction` 三个具体 config 用 frozen dataclass 包一层。它**不**新增字段、**不**承载逻辑——只为了避免函数签名里同时出现 3 个 config 参数。
- `_ambient.py` 用下划线前缀表明仅供 `matmaster.run` 内部使用；外部通过 `matmaster.run.install_ambient_run_context` / `current_*` 调用。

## 验证策略

本 spec **不改运行行为**，因此验证集中在三处：

### 1. Bundle 自身

```python
def test_run_identity_is_frozen() -> None:
    ident = RunIdentity(run_id="r1", session_id="s1", task_id="t1",
                        run_dir=Path("/tmp"), spawn_id=None)
    with pytest.raises(FrozenInstanceError):
        ident.run_id = "other"  # type: ignore[misc]


def test_no_bundle_has_any_or_dict_field() -> None:
    """静态检查：所有 bundle 的字段类型注解里没有 Any / dict / Mapping[str, Any]。"""
    for cls in iter_run_bundle_classes():
        for field in dataclasses.fields(cls):
            annotation = get_type_hints(cls).get(field.name)
            assert "Any" not in repr(annotation), f"{cls.__name__}.{field.name}: Any forbidden"
```

### 2. contextvars 入口

```python
def test_install_ambient_run_context_isolates_per_task() -> None:
    """两个并发 task 看到各自的 bundle。"""
    async def task(identity: RunIdentity) -> str | None:
        with install_ambient_run_context(identity=identity):
            await asyncio.sleep(0)
            cur = current_run_identity()
            return cur.task_id if cur else None

    id_a = RunIdentity(...task_id="A"...)
    id_b = RunIdentity(...task_id="B"...)
    results = await asyncio.gather(task(id_a), task(id_b))
    assert results == ["A", "B"]


def test_install_ambient_run_context_resets_on_exit() -> None:
    with install_ambient_run_context(identity=...):
        ...
    assert current_run_identity() is None
```

### 3. Compat 投影

```python
def test_build_legacy_run_meta_identity_keys_match_existing_run_meta() -> None:
    """确保 bundle → dict 投影出的字段集合与现有 run_meta 已有 key 对齐，
    没有遗漏、没有命名漂移。"""
    identity = RunIdentity(run_id="r1", session_id="s1", task_id="t1",
                           run_dir=Path("/tmp/x"), spawn_id=None)
    dct = build_legacy_run_meta(identity=identity)
    assert set(dct.keys()) == {"run_dir", "task_id", "session_id"}
    assert dct["run_dir"] == "/tmp/x"
```

## 这次 spec 不动什么

- **任何现有调用方**——`Playground / Exp / AgentRunService / Worker / stream_service / events_service` 一个字符不改。
- **`run_meta` 字段** 仍存在于 `PlaygroundContext`，仍接收 `dict[str, Any]`。
- **`AgentRuntimeSpec.meta`** 不改。
- **`manifests/` 目录**不动。持久事件子系统是 Spec 3。
- **`JobRegistry / TodoWrite / runner_state`** 不动。
- **DB 表 / Redis schema** 不动。

## 后续 spec 路线图

| Spec | 名字 | 依赖 |
|---|---|---|
| 2 | run_meta 主消费者迁移到 typed bundle | 1 |
| 3 | 持久事件子系统（PersistentStateStore + Slot + Renderable） | 1, 2 |
| 4 | MCPRuntimeConfig 抽出 | 1, 2 |
| 5 | CompactionInputBundle 抽出 | 1, 2 |
| 6 | FigureUploadConfig 抽出 | 1, 2 |
| 7 | BohriumRebuildBundle 抽出（含 JobRegistry 是否纳入持久 store 的决策） | 1, 2, 3 |
| 8 | RunIdentity 抽出 | 1, 2 |
| 9 | 删除 `PlaygroundContext.run_meta`、`compat.py`、最终瘦身 | 1–8 |

## 待用户细化的开放问题

1. **`RuntimeConfigSet` 是否值得存在**：当前为了减少函数参数个数而聚合 3 个 sub-config。是否接受这种"薄聚合"，还是宁愿每个 sub-config 独立传？
2. **`_ambient_*` 列表**：除 `RunIdentity` / `PersistentStateBundle` 外，是否还有别的 bundle 需要 ambient（如 `RunIdentity` 用于日志注入）？是否限制 ambient 仅一类？
3. **Compat shim 的反向投影**：Spec 2 迁移期会用到 `from_legacy_run_meta` 反向构造。该函数应放在 Spec 1 还是 Spec 2？放 Spec 1 的好处是迁移可分批进行；放 Spec 2 可以让本 spec 完全不知道老 dict。
4. **`AgentRuntimeSpec.meta` 命运**：是否在本 spec 同步声明它的归宿（被对应 typed bundle 替代），还是留给后续 spec？
5. **命名约定的强制力**：禁止 `Any / dict[str, Any]` 是否要在 CI 上做静态检查（如 mypy 严格模式 + 自定义 ruff rule）？还是仅作为评审约定？
6. **多 spawn / 子 agent 情形**：sub-agent 是否复用父 agent 的 bundle 还是独立构造？`spawn_id` 在 `RunIdentity` 中如何标记？
7. **持久化测试 fixture**：是否在本 spec 提供 `make_test_run_identity()` / `make_test_state_bundle()` 等工厂函数，统一测试用例？

---

> 文档完。后续修订记录：

