# Agent 运行 Trace 导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增两类 internal-only 持久化事件（`run_config` / `compaction_trace`）补齐"每次模型调用的完整输入"缺口，并提供从数据库重建一次运行为结构化 `RunTrace` 的库函数 + 本地 CLI（导出 JSON），供评测与运行轨迹查看。

**Architecture:** 三层流水线。**采集层**在运行时 emit 两类新事件（系统提示/模型参数/工具定义 + 压缩调用的剪裁后 input/产出 output），经现有 fanout → PersistenceHandler 落 `evo_chat_events`，并在 SSE live/replay 两条路径显式拦截、对 restore replay 排除。**重建层**扩展现有 `ModelHistoryRestorer` 支持"重建到某 event_id 为止"（恢复首段 base），并新增导出专用的全类型事件查询（带 `id`/`created_at_ms`/`invocation_id`）。**导出层**单遍 replay 事件流组装为 `RunTrace`（`runs[]` 平铺 + 每个 `AgentRun` 的 `nodes` 异构时间线：对话节点 + 压缩节点），元数据内联、派生视图按需物化、CLI 仿 `matmaster/devshell/cli.py`。

**Tech Stack:** Python ≥3.10（仓库 `uv` 环境）、Pydantic v2、`@dataclass(frozen=True)`、asyncio、pytest、argparse、MySQL（`evo_chat_events`）。

---

## Global Constraints

- 项目处于开发阶段，**禁止任何兼容 / 兜底 / 迁移内联逻辑**；偏好迁移而非兼容。改构造签名（如 `call_summary_llm_response` 返回值、`get_scope_events_after_id` 形参）就直接改所有调用点与受影响测试，不留旧签名兜底。
- 除专有名词外，注释与文档用中文。
- 本期是**新增功能**，按团队惯例对新行为做 TDD 直测；不为本期改动顺带删既有测试。Task 8/9 触及的既有测试是**适配新签名**（非瘦身删测）。
- `RunTrace` 及其子模型、两类新事件、`LLMRequestConfigSnapshot` 一律 frozen，更新用 `model_copy`。
- 新事件**含系统提示与完整对话**，是 internal-only：**绝不可经 SSE（live 或 replay）泄露**。每个采集 task 完成后、任何真实部署前，Task 4 的两道 SSE 拦截必须就位。
- 设计来源：`docs/superpowers/specs/2026-06-28-agent-run-trace-export-design.md`。

> **实现前必读——本 plan 对 spec 的 7 处事实修正（均经源码逐行核验）：**
> 1. 事件判别字段名是 **`type`**（非 spec 个别处暗示的 `event_type`）；公共基类是 **`EventBase`**（`events.py:22-27`），带 `turn_index` 的中间基类是 `TurnUsageCarrierEvent`（`events.py:33-43`）。
> 2. **`persistence_handler.py` 无需改动**——未知/新类型默认落库（`_should_persist_type` 仅排除 `_SKIP_TYPES`）。spec §7.3 将其列为改动点，方向相反。
> 3. spec §7.3 援引的 terminal `for_persistence`（`event_payloads.py:359`）是**字段级** persist-only（terminal 事件本身既走 SSE 又落库，只扣 model identity 三字段），**不是整事件 internal-only 范例**。真正范例是 `assistant_state`/`skill_hit`：靠 `sse_handler._should_skip` + `REPLAY_DISCARDED_EVENT_TYPES` 两道拦截，而非 `_public_content_for_event` 返回 None。本 plan 两者都做（content 分支按 `for_persistence` 给/不给 + 两道 SSE 拦截）。
> 4. DAO 中**无 `get_scope_events`**，真名 `get_scope_events_after_id`（`chat_events_table.py:264`）；compaction 类排除名单实为 **3 项**（`history_checkpoint, compaction, context_compaction`），且 `get_latest_scope_event_id` 有**镜像副本**（`:316`），两处都要改。
> 5. `matmaster/context/run_context.py` 不存在 → `invocation_id` 在 **`matmaster/core/run_context.py:55`**。
> 6. `ModelHistoryRestorer.restore()` **不支持 until 语义**，需扩展（Task 10）。
> 7. compaction 的剪裁后 input 取 `summary_messages`（`compaction.py:364`，canonicalize 前的 `prep.messages + compact_request`），与 spec §7.2 明文及 §10「逻辑输入级保真」一致；`run_compaction_plan` 有两个 emit 点（`:41-49` running / `:150-166` complete），spec 的 ":63-83 emit 区" 是标签错误（那段是 summary 调用+应用，无 emit）。

---

## File Structure

**新建：**
- `matmaster/types/llm_request_config.py` —— `LLMRequestConfigSnapshot`（模型请求参数快照，types 层纯数据，无 providers 反向依赖）
- `matmaster/types/run_trace.py` —— `RunTrace` 及全部子模型（导出产物的数据契约）
- `src/services/run_trace_service.py` —— `build_run_trace` + 重建 helper（导出核心库函数）
- `src/services/run_trace_views.py` —— `slice_trace` / `per_call_view` / `eval_view` 派生视图投影器
- `matmaster/trace_export/cli.py` + `matmaster/trace_export/__main__.py` —— `export-trace` CLI（仿 devshell）

**修改：**
- `matmaster/types/events.py` —— 定义 `RunConfigEvent` + `CompactionTraceEvent`，注册进 `SystemEvent` 与 `BusEvent` 两个 union
- `matmaster/integration/event_payloads.py` —— `_public_content_for_event` 加两类分支
- `matmaster/integration/sse_handler.py` —— `_should_skip` 加两类拦截（live SSE）
- `src/services/stream_sse_filter.py` —— `REPLAY_DISCARDED_EVENT_TYPES` 加两类（replay SSE，同时覆盖守卫与 SQL 裁剪）
- `src/dao/chat_events_table.py` —— SQL `NOT IN`（`:291` + 镜像 `:316`）加两类；`get_scope_events_after_id` 加 `until_event_id`；新增 `get_trace_events`
- `matmaster/providers/llm_factory.py` —— `build_provider_bundle` / `build_byok_provider_bundle` 构造 snapshot，挂到 `LLMProviderBundle`
- `matmaster/core/run_context.py` —— `AgentRunRequest` 加 `llm_request_config` 字段
- `matmaster/types/runtime.py` —— `AgentKernelSpec` 加 `llm_request_config` 字段
- `matmaster/core/exp.py` —— 构造 `AgentKernelSpec` 处接线 snapshot
- `src/services/agent_run_service.py` —— 构造 `AgentRunRequest` 处接线 snapshot
- `matmaster/core/agent.py` —— kernel loop emit `run_config`（首次 + 工具集变化）
- `matmaster/context/compaction.py` —— `call_summary_llm_response` 返回 `(LLMResponse, summary_messages)`
- `matmaster/core/agent_compaction.py` —— 成功路径 emit `compaction_trace`
- `matmaster/context/history_restore.py` —— `restore` / `_restore_v1` / `GetEventsAfter` 加 `until_event_id`
- `src/services/model_history_restore_service.py` —— `_delegate_v1_restore` 的 `get_events_after` 闭包透传 `until`
- `pyproject.toml` —— `[project.scripts]` 加 `mm-export-trace`

---

### Task 1: `LLMRequestConfigSnapshot` 模型请求参数快照

**Files:**
- Create: `matmaster/types/llm_request_config.py`
- Test: `tests/matmaster/types/test_llm_request_config.py`

**Interfaces:**
- Consumes: 无
- Produces: `LLMRequestConfigSnapshot`（frozen Pydantic，承载 temperature/max_tokens/reasoning_*/extra_body/各 timeout/重试参数）

> **为何单列、放 types 层**：模型请求参数在 transport 构造时被注入并存为私有 `_temperature` 等字段（`chat_completions.py:285-316`），`AgentKernelSpec` 与 `LLMProvider` protocol 均不透出；kernel 无合法来源，严禁反射 transport 私有字段（spec §7.1）。改为在参数已知处（`build_provider_bundle`，Task 5）构造此不可变快照随 runtime 传入。放 `types/` 因为它被 `providers`（构造）、`core`（kernel 读）、`integration`（事件携带）共用，且自身不依赖 `providers`（不违反「types 禁反向依赖 providers」）。字段集对齐 `config/llm.py::LLMProfileConfig`（`:42-59`）与 transport `__init__`（`chat_completions.py:285-316`）。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/types/test_llm_request_config.py`：

```python
"""LLMRequestConfigSnapshot：模型请求参数快照。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.types.llm_request_config import LLMRequestConfigSnapshot


def _full():
    return LLMRequestConfigSnapshot(
        temperature=0.7,
        max_tokens=4096,
        reasoning_effort="medium",
        reasoning_summary="auto",
        extra_body={"reasoning": {"effort": "medium"}},
        timeout=300.0,
        stream_timeout=60.0,
        stream_idle_timeout=30.0,
        max_retries=3,
        retry_delay=1.0,
    )


def test_constructs_with_all_fields():
    snap = _full()
    assert snap.temperature == 0.7
    assert snap.max_tokens == 4096
    assert snap.extra_body == {"reasoning": {"effort": "medium"}}


def test_optional_fields_default_none():
    snap = LLMRequestConfigSnapshot(temperature=0.0, timeout=300.0, max_retries=0, retry_delay=0.0)
    assert snap.max_tokens is None
    assert snap.reasoning_effort is None
    assert snap.extra_body is None


def test_is_frozen():
    snap = _full()
    with pytest.raises(ValidationError):
        snap.temperature = 0.1


def test_round_trips_through_json():
    snap = _full()
    assert LLMRequestConfigSnapshot.model_validate(snap.model_dump(mode="json")) == snap
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/types/test_llm_request_config.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'matmaster.types.llm_request_config'`）

- [ ] **Step 3: 实现快照模型**

Create `matmaster/types/llm_request_config.py`：

```python
"""一次运行的模型请求参数快照（不含 messages / system / tools）。

在 build_provider_bundle 处采集——这是 temperature/max_tokens/reasoning_* 等参数
唯一同时在手的 choke point（transport 构造后即把它们藏为私有字段）。随 runtime
透传给 kernel，供 run_config 事件记录「每次模型调用喂进去的参数」。kernel 无法从
transport protocol 反射这些字段，故必须靠本快照显式传入。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class LLMRequestConfigSnapshot(BaseModel):
    """不可变的模型请求参数集，对齐 LLMProfileConfig 与 chat_completions transport。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    temperature: float
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    extra_body: dict[str, Any] | None = None
    timeout: float
    stream_timeout: float | None = None
    stream_idle_timeout: float | None = None
    max_retries: int
    retry_delay: float
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/types/test_llm_request_config.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add matmaster/types/llm_request_config.py tests/matmaster/types/test_llm_request_config.py
git commit -m "feat(types): add LLMRequestConfigSnapshot for run_config capture"
```

---

### Task 2: `RunTrace` 数据模型

**Files:**
- Create: `matmaster/types/run_trace.py`
- Test: `tests/matmaster/types/test_run_trace.py`

**Interfaces:**
- Consumes: `LLMRequestConfigSnapshot`（Task 1）
- Produces: `RunTrace`、`AgentRun`、`ConversationNode`、`CompactionNode`、`UserMessageNode`、`AssistantMessageNode`、`ToolMessageNode`、`RunConfigEntry`、`ParentRef`、`TraceMeta`、`ExportMeta`、`Diagnostic`、`Timing`、`ToolCallSpec`、常量 `RUN_TRACE_SCHEMA_VERSION = "run_trace.v1"`

> **结构要点（spec §6）**：每个 agent scope（root / subagent）是一个 `AgentRun`，核心是 `nodes`——按时间排的异构列表。对话节点（`kind="conversation"`）内含线性 `messages`（user/assistant/tool），与喂给模型的 OpenAI 风格 messages 同构；压缩处插入 `compaction` 节点。输入侧不变量（system/model/params/tools）单列到 `configs` 小表，assistant message 用短 `config_id` 引用（避免大块 tools schema 在每条消息重抄）。node union 按 `kind` 判别、message union 按 `role` 判别——与 events.py 的 `Field(discriminator=...)` 惯例一致。
> **`CompactionNode.output` 用 `str`**（非 spec §6 示意的 `[...]`）：`validate_summary_response` 返回 `str`（`compaction.py:373-379`），summary 就是一段文本。`input` 是 `list[dict]`（`summary_messages` 的 dump）。
> **`usage` 用 `dict[str,int]`** 原样存事件的 `turn_usage`，不强解释为 `{input,output,total}`——provider 的 key 各异，强转会丢信息；规整交给 projector。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/types/test_run_trace.py`：

```python
"""RunTrace 数据契约：构造、discriminated union、序列化。"""

from __future__ import annotations

from matmaster.types.run_trace import (
    RUN_TRACE_SCHEMA_VERSION,
    AgentRun,
    AssistantMessageNode,
    CompactionNode,
    ConversationNode,
    ExportMeta,
    RunConfigEntry,
    RunTrace,
    ToolMessageNode,
    TraceMeta,
    UserMessageNode,
)


def _minimal_trace():
    conv = ConversationNode(
        messages=[
            UserMessageNode(content="hi", source_event_id=1),
            AssistantMessageNode(turn_index=0, config_id="c1", content="hello", source_event_id=2),
        ]
    )
    run = AgentRun(
        id="root",
        configs={"c1": RunConfigEntry(reason="initial", system_prompt="SYS")},
        nodes=[conv],
        status="success",
    )
    return RunTrace(trace=TraceMeta(session_id="s"), export=ExportMeta(), runs=[run])


def test_schema_version_default():
    assert _minimal_trace().schema_version == RUN_TRACE_SCHEMA_VERSION == "run_trace.v1"


def test_node_union_discriminates_by_kind():
    run = _minimal_trace().runs[0]
    assert run.nodes[0].kind == "conversation"
    comp = CompactionNode(compaction_id="root:1", input=[{"role": "system"}], output="summary")
    run2 = run.model_copy(update={"nodes": [run.nodes[0], comp]})
    assert run2.nodes[1].kind == "compaction"


def test_message_union_discriminates_by_role():
    conv = _minimal_trace().runs[0].nodes[0]
    assert [m.role for m in conv.messages] == ["user", "assistant"]


def test_round_trips_through_json():
    trace = _minimal_trace()
    restored = RunTrace.model_validate(trace.model_dump(mode="json"))
    assert restored == trace
    # union 字段经 JSON 往返后仍解析回正确子类型
    assert isinstance(restored.runs[0].nodes[0], ConversationNode)
    assert isinstance(restored.runs[0].nodes[0].messages[1], AssistantMessageNode)


def test_tool_message_node_defaults():
    tm = ToolMessageNode(call_id="x", name="t", content="ok")
    assert tm.role == "tool"
    assert tm.status == "success"
    assert tm.raw_content is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/types/test_run_trace.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'matmaster.types.run_trace'`）

- [ ] **Step 3: 实现 RunTrace 模型**

Create `matmaster/types/run_trace.py`：

```python
"""RunTrace：一次运行导出的数据契约。

RunTrace 就是一份对话记录：每个 agent scope（root / subagent）是一个 AgentRun，
核心是 nodes（对话节点 + 压缩节点的时间线）。元数据内联到对应 message，输入侧
不变量单列 configs 小表按短 id 引用，subagent 平铺为 runs[] 不嵌套。派生量（num_turns、
总 usage、最终回答）不落库，由 consumer/projector 计算。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from matmaster.types.llm_request_config import LLMRequestConfigSnapshot

RUN_TRACE_SCHEMA_VERSION = "run_trace.v1"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Timing(_Frozen):
    """近似耗时：唯一可靠来源是 DB created_at（事件自带 timestamp 不持久化）。"""

    started_at_ms: int | None = None
    completed_at_ms: int | None = None


class ToolCallSpec(_Frozen):
    call_id: str
    name: str
    arguments: dict[str, Any] | str = ""


# —— message 节点（按 role 判别）——
class UserMessageNode(_Frozen):
    role: Literal["user"] = "user"
    content: Any = ""  # str 或含 images 的结构
    source_event_id: int | None = None


class AssistantMessageNode(_Frozen):
    role: Literal["assistant"] = "assistant"
    turn_index: int
    config_id: str | None = None  # 指 AgentRun.configs；本次调用生效的 system/model/tools
    content: str = ""
    reasoning_summary: str | None = None  # 可见摘要，默认不含隐藏 thinking
    tool_calls: list[ToolCallSpec] | None = None
    usage: dict[str, int] | None = None  # 原样存事件 turn_usage，不强解释
    timing: Timing | None = None
    source_event_id: int | None = None


class ToolMessageNode(_Frozen):
    role: Literal["tool"] = "tool"
    call_id: str  # 与同 turn assistant 的 tool_calls 配对
    name: str | None = None
    content: Any = ""  # 模型实际看到的结果（model-visible）
    status: Literal["success", "error", "cancelled"] = "success"
    raw_content: Any = None  # 仅当工具原始返回 ≠ 模型可见时出现
    source_event_id: int | None = None


MessageNode = Annotated[
    UserMessageNode | AssistantMessageNode | ToolMessageNode,
    Field(discriminator="role"),
]


# —— 时间线节点（按 kind 判别）——
class ConversationNode(_Frozen):
    kind: Literal["conversation"] = "conversation"
    base: list[dict[str, Any]] | None = None  # 本段起始的可见历史（压缩后段 / 非首轮首段）
    messages: list[MessageNode] = Field(default_factory=list)


class CompactionNode(_Frozen):
    kind: Literal["compaction"] = "compaction"
    compaction_id: str
    event_id: int | None = None
    config_id: str | None = None
    input: list[dict[str, Any]] = Field(default_factory=list)  # 剪裁后喂给压缩器的上下文
    output: str = ""  # 压缩器产出的 summary（≠ 下一节点 base，会被修改后才作输入）
    usage: dict[str, int] | None = None
    timing: Timing | None = None


TraceNode = Annotated[ConversationNode | CompactionNode, Field(discriminator="kind")]


# —— 输入侧不变量 ——
class RunConfigEntry(_Frozen):
    effective_from_event_id: int | None = None
    reason: str = "initial"
    system_prompt: str = ""
    model: str | None = None
    model_profile: str | None = None
    model_route: str | None = None
    params: LLMRequestConfigSnapshot | None = None
    tools: list[dict[str, Any]] | None = None


# —— AgentRun 与外层结构 ——
class ParentRef(_Frozen):
    run_id: str
    call_id: str | None = None  # 父 run 中发起本 subagent 的 tool call


class AgentRun(_Frozen):
    id: str  # trace 内部 id（root 记 "root"）；非新外部标识
    spawn_id: str | None = None
    task_id: str | None = None
    parent: ParentRef | None = None
    configs: dict[str, RunConfigEntry] = Field(default_factory=dict)
    nodes: list[TraceNode] = Field(default_factory=list)
    status: Literal["success", "error", "cancelled", "unknown"] = "unknown"
    error: dict[str, Any] | None = None


class TraceMeta(_Frozen):
    session_id: str
    invocation_id: str | None = None  # run 级外部标识（事实3）
    root_run_id: str = "root"


class Redaction(_Frozen):
    applied: bool = False
    profile: str | None = None


class ExportMeta(_Frozen):
    exported_at: str | None = None
    redaction: Redaction = Field(default_factory=Redaction)


class Diagnostic(_Frozen):
    code: str
    message: str
    severity: str | None = None
    target: dict[str, Any] | None = None
    data: dict[str, Any] | None = None


class RunTrace(_Frozen):
    schema_version: Literal["run_trace.v1"] = RUN_TRACE_SCHEMA_VERSION
    trace: TraceMeta
    export: ExportMeta
    runs: list[AgentRun] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/types/test_run_trace.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add matmaster/types/run_trace.py tests/matmaster/types/test_run_trace.py
git commit -m "feat(types): add RunTrace data model"
```

---

### Task 3: 定义 `RunConfigEvent` 与 `CompactionTraceEvent`

**Files:**
- Modify: `matmaster/types/events.py`（新增两个事件类；`SystemEvent` union 约 `:344-361`；`BusEvent` union 约 `:363-392`）
- Test: `tests/matmaster/types/test_trace_events.py`

**Interfaces:**
- Consumes: `LLMRequestConfigSnapshot`（Task 1）、`EventBase`（`events.py:22-27`）
- Produces: `RunConfigEvent`（`type="run_config"`）、`CompactionTraceEvent`（`type="compaction_trace"`），均为 `BusEvent` 成员

> **核验要点**：判别字段是 **`type`**（`Literal["..."] = "..."`），非 `event_type`；两类事件不需要 `turn_index`，故继承 `EventBase`（非 `TurnUsageCarrierEvent`）。新事件类必须同时加入 `SystemEvent` 与 **`BusEvent`** 两个 discriminated union——`BusEvent` 是 PersistenceHandler / SSEHandler 实际接收的类型，漏了它事件进不了管线。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/types/test_trace_events.py`：

```python
"""run_config / compaction_trace 两类 internal-only 事件。"""

from __future__ import annotations

from pydantic import TypeAdapter

from matmaster.types.events import BusEvent, CompactionTraceEvent, RunConfigEvent
from matmaster.types.llm_request_config import LLMRequestConfigSnapshot


def test_run_config_event_type_literal():
    ev = RunConfigEvent(source="agent", system_prompt="SYS", model="m")
    assert ev.type == "run_config"
    assert ev.reason == "initial"


def test_run_config_carries_params_and_tools():
    params = LLMRequestConfigSnapshot(temperature=0.7, timeout=300.0, max_retries=3, retry_delay=1.0)
    ev = RunConfigEvent(
        source="agent",
        reason="tool_catalog_changed",
        system_prompt="SYS",
        params=params,
        tools=[{"type": "function", "function": {"name": "t"}}],
    )
    assert ev.params.temperature == 0.7
    assert ev.tools[0]["function"]["name"] == "t"


def test_compaction_trace_event_fields():
    ev = CompactionTraceEvent(
        source="agent",
        compaction_id="root:1",
        summary_input=[{"role": "system", "content": "S"}],
        summary_output="summary text",
        usage={"total_tokens": 10},
    )
    assert ev.type == "compaction_trace"
    assert ev.compaction_id == "root:1"
    assert ev.summary_output == "summary text"


def test_both_events_resolve_through_busevent_union():
    adapter = TypeAdapter(BusEvent)
    rc = adapter.validate_python(
        {"type": "run_config", "source": "agent", "system_prompt": "SYS"}
    )
    ct = adapter.validate_python(
        {"type": "compaction_trace", "source": "agent", "compaction_id": "root:1"}
    )
    assert isinstance(rc, RunConfigEvent)
    assert isinstance(ct, CompactionTraceEvent)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/types/test_trace_events.py -v`
Expected: FAIL（`ImportError: cannot import name 'RunConfigEvent' from 'matmaster.types.events'`）

- [ ] **Step 3: 定义两类事件并注册 union**

3a. 在 `matmaster/types/events.py` 顶部 import 区加（与既有 import 同处）：

```python
from matmaster.types.llm_request_config import LLMRequestConfigSnapshot
```

3b. 在文件内事件类定义区（紧邻 `CompactionEvent`，约 `:249` 之后）加两个类：

```python
class RunConfigEvent(EventBase):
    """internal-only：记录一次运行输入侧不变量（系统提示 / 模型 / 参数 / 工具定义）。

    AgentKernel 运行开始首次调用 LLM 前 emit 一条（reason="initial"）；工具集变化
    （lazy mcp 连新 server）时再 emit 一条（reason="tool_catalog_changed"），其生效
    起点即该事件落库后的 event id。含系统提示，绝不可经 SSE 泄露（见 sse_handler /
    stream_sse_filter 拦截）。
    """

    type: Literal["run_config"] = "run_config"
    reason: str = "initial"
    system_prompt: str = ""
    model: str | None = None
    model_profile: str | None = None
    model_route: str | None = None
    params: LLMRequestConfigSnapshot | None = None
    tools: list[dict[str, Any]] | None = None


class CompactionTraceEvent(EventBase):
    """internal-only：记录一次压缩调用的剪裁后 input + 产出 output。

    在 run_compaction_plan 成功路径上、紧随公开 CompactionEvent(status="complete")
    多 emit 一条；compaction_id 与该公开事件同值，可 1:1 配对。补齐 RunTrace 压缩
    节点的数据源（公开 CompactionEvent 不存 summary 文本）。含完整历史，internal-only。
    """

    type: Literal["compaction_trace"] = "compaction_trace"
    compaction_id: str
    summary_input: list[dict[str, Any]] = Field(default_factory=list)
    summary_output: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    model: str | None = None
```

> 若 `events.py` 顶部未导入 `Any` / `Field` / `Literal`，先 `grep -n "from typing import\|from pydantic import" matmaster/types/events.py` 确认；这些在既有事件里已大量使用，通常已 import。

3c. 把两类加入 `SystemEvent` union（约 `:344-361`，即服务/系统侧事件的 `Annotated[... , Field(discriminator="type")]` 成员列表）：在该 union 的类型列表末尾追加 `RunConfigEvent` 与 `CompactionTraceEvent`。

3d. 把两类加入 `BusEvent` union（约 `:363-392`）：同样在其类型列表追加 `RunConfigEvent` 与 `CompactionTraceEvent`。

> 用 `grep -n "RunResultEvent\|CompactionEvent" matmaster/types/events.py` 定位两个 union 的成员列表（既有事件名出现在那里），把新类型并列加入；确保两个 union 都加，缺一不可。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/types/test_trace_events.py -v`
Expected: PASS（4 passed）

再冒烟导入，确认 union 无重复 discriminator 冲突：
Run: `python -c "from matmaster.types.events import BusEvent; print('ok')"`
Expected: 打印 `ok`，无 Pydantic schema 构建错误

- [ ] **Step 5: 提交**

```bash
git add matmaster/types/events.py tests/matmaster/types/test_trace_events.py
git commit -m "feat(events): add internal-only run_config and compaction_trace events"
```

---

### Task 4: 两类事件的 internal-only 安全拦截

**Files:**
- Modify: `matmaster/integration/event_payloads.py`（`_public_content_for_event`，约 `:243`，在 terminal 分支后、fallback `:421` 前加分支）
- Modify: `matmaster/integration/sse_handler.py`（`_should_skip`，`:116-155`）
- Modify: `src/services/stream_sse_filter.py`（`REPLAY_DISCARDED_EVENT_TYPES`，`:21-30`）
- Modify: `src/dao/chat_events_table.py`（SQL `NOT IN`：`:291` 主 + `:316` 镜像）
- Test: `tests/matmaster/integration/test_internal_only_events.py`

**Interfaces:**
- Consumes: 两类事件 type 字符串
- Produces: 两类事件落库 content 完整、SSE live/replay 双路径整条拦截、restore replay 显式排除

> **三层拦截，缺一即泄露（核验修正 spec §7.3）**：
> - **落库**：`persistence_handler.py` 默认落库新类型，**无需改动**。但落库 content 经 `_public_content_for_event(type, payload, for_persistence=True)` 取——若不加分支会走 `:421-435` fallback（打 WARNING + 塞入除 type/source/timestamp 外全部字段）。故加显式分支：`for_persistence=True` 返回完整 payload，`for_persistence=False` 返回 None。
> - **live SSE**：`_should_skip` 是内联 `if`（非具名名单），默认 `return False`（推送）。加分支 `return True`，范例见 `assistant_state`（`:127-128`）/`skill_hit`（`:135-136`）。
> - **replay SSE**：加入 `REPLAY_DISCARDED_EVENT_TYPES`（单一来源，同时驱动 replay 守卫 `_should_emit_event_to_sse` 与 `get_session_events(exclude_types=...)` 的 SQL 裁剪）。
> - **restore replay**：`history_restore.py:204` 已 default-deny（白名单只保留 `assistant_state/response/run_result/tool_call`），新类型天然不进 restore。加 SQL `NOT IN` 只为省 IO + 显式可读，两处（`:291` + 镜像 `:316`）。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/integration/test_internal_only_events.py`：

```python
"""run_config / compaction_trace 的 internal-only 拦截：落库完整、SSE 不泄露。"""

from __future__ import annotations

import pytest

from matmaster.integration.event_payloads import _public_content_for_event
from matmaster.integration.sse_handler import SSEHandler
from src.services.stream_sse_filter import (
    REPLAY_DISCARDED_EVENT_TYPES,
    _should_emit_event_to_sse,
)


@pytest.mark.parametrize("etype", ["run_config", "compaction_trace"])
def test_persisted_content_is_full_payload(etype):
    payload = {"type": etype, "source": "agent", "system_prompt": "SECRET", "x": 1}
    assert _public_content_for_event(etype, payload, for_persistence=True) == payload


@pytest.mark.parametrize("etype", ["run_config", "compaction_trace"])
def test_sse_projection_is_none(etype):
    payload = {"type": etype, "source": "agent", "system_prompt": "SECRET"}
    assert _public_content_for_event(etype, payload, for_persistence=False) is None


@pytest.mark.parametrize("etype", ["run_config", "compaction_trace"])
def test_live_sse_skipped(etype):
    handler = SSEHandler.__new__(SSEHandler)  # 仅测纯函数 _should_skip，不需完整构造
    assert handler._should_skip(etype, {}) is True


@pytest.mark.parametrize("etype", ["run_config", "compaction_trace"])
def test_replay_sse_discarded(etype):
    assert etype in REPLAY_DISCARDED_EVENT_TYPES
    assert _should_emit_event_to_sse({"type": etype}) is False
```

> 若 `_should_skip` 的实际签名与上面不符（参数个数/名称），按 `sse_handler.py:116` 的真实签名调整测试构造；若 `SSEHandler.__new__` 旁路不可行（`_should_skip` 读了实例属性），改为构造最小 handler 或把测试聚焦到 `_should_emit_event_to_sse` + content 投影两项，并在实现 step 后人工核对 `_should_skip` 分支。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/integration/test_internal_only_events.py -v`
Expected: FAIL（content 投影返回 fallback 提取字段而非 None / 整 payload；`etype not in REPLAY_DISCARDED_EVENT_TYPES`）

- [ ] **Step 3: 实现四处拦截**

3a. `matmaster/integration/event_payloads.py`：在 `_public_content_for_event` 内，terminal 分支（`:344-365`）之后、fallback（`raw_content = payload.get('content')`，约 `:421`）之前插入：

```python
    if event_type in ('run_config', 'compaction_trace'):
        # internal-only：落库存完整 payload 供 trace 重建；SSE 路径返回 None。
        # live SSE 另由 sse_handler._should_skip 整条拦截、replay 另由
        # REPLAY_DISCARDED_EVENT_TYPES 拦截，这里的 None 是纵深防御第三道。
        return payload if for_persistence else None
```

3b. `matmaster/integration/sse_handler.py`：在 `_should_skip`（`:116-155`）内，紧邻 `assistant_state` 分支（`:126-128`）加：

```python
        # Internal-only: trace 导出专用事件，绝不推送前端（含系统提示 / 完整历史）
        if event_type in ('run_config', 'compaction_trace'):
            return True
```

3c. `src/services/stream_sse_filter.py`：在 `REPLAY_DISCARDED_EVENT_TYPES` frozenset（`:21-30`）的成员里加两个字符串：

```python
    'run_config',
    'compaction_trace',
```

3d. `src/dao/chat_events_table.py`：把 `:291`（`get_scope_events_after_id` 内）和 `:316`（`get_latest_scope_event_id` 镜像）两处的

```sql
AND type NOT IN ('history_checkpoint', 'compaction', 'context_compaction')
```

改为

```sql
AND type NOT IN ('history_checkpoint', 'compaction', 'context_compaction', 'run_config', 'compaction_trace')
```

> 用 `grep -n "NOT IN ('history_checkpoint'" src/dao/chat_events_table.py` 定位这两行，逐一替换；务必两处都改（一主一镜像）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/integration/test_internal_only_events.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add matmaster/integration/event_payloads.py matmaster/integration/sse_handler.py src/services/stream_sse_filter.py src/dao/chat_events_table.py tests/matmaster/integration/test_internal_only_events.py
git commit -m "feat(trace): intercept run_config/compaction_trace from all SSE and restore paths"
```

---

### Task 5: `build_provider_bundle` 构造参数快照并挂到 bundle

**Files:**
- Modify: `matmaster/providers/llm_factory.py`（`LLMProviderBundle` `:64-77` 加字段；`build_provider_bundle` `:209-238`、`build_byok_provider_bundle` `:241-284` 构造 snapshot）
- Test: `tests/matmaster/providers/test_llm_factory_snapshot.py`

**Interfaces:**
- Consumes: `LLMRequestConfigSnapshot`（Task 1）、`resolved.profile`（`LLMProfileConfig`，`config/llm.py:42-59`）
- Produces: `LLMProviderBundle.llm_request_config: LLMRequestConfigSnapshot`

> **唯一 choke point**：`build_provider_bundle` 的 `_dispatch(resolved.profile, resolved.provider)`（`:227`）之后，是 temperature/max_tokens/reasoning_*/timeouts 全部在手、且 transport 尚未把它们藏成私有字段的唯一位置。BYOK 孪生 `build_byok_provider_bundle`（`:241-284`）在 `:273` 附近 dispatch，`extra_body` 取自该路径的 `extra_body` 变量（profile 路径 `extra_body=None`）。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/providers/test_llm_factory_snapshot.py`（真实调用 `build_provider_bundle`，硬依赖 `config/llm_config.yaml` 现存 profile；若该 profile 移除需同步改 key）：

```python
"""build_provider_bundle 采集 LLMRequestConfigSnapshot。"""

from __future__ import annotations

from pathlib import Path

from matmaster.config.loader import load_llm_config
from matmaster.providers.llm_factory import build_provider_bundle
from matmaster.types.llm_request_config import LLMRequestConfigSnapshot

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm_config.yaml"
_PROFILE = "matmaster/DeepSeek-v4-Pro"


def test_bundle_carries_request_config_snapshot():
    llm_config = load_llm_config(_LLM_CONFIG_PATH)
    bundle = build_provider_bundle(llm_config, model_override=_PROFILE)
    snap = bundle.llm_request_config
    assert isinstance(snap, LLMRequestConfigSnapshot)
    # 快照值来自被解析的 profile（与 transport 注入同源）
    resolved = llm_config.resolve(model_override=_PROFILE)
    assert snap.temperature == resolved.profile.temperature
    assert snap.max_tokens == resolved.profile.max_tokens
    assert snap.timeout == resolved.profile.timeout
    assert snap.max_retries == resolved.profile.max_retries
```

> 若 `load_llm_config` / `llm_config.resolve(...)` 的导入路径或签名与此不符，用 `grep -rn "def load_llm_config\|def resolve" matmaster/config/` 校正（Task 6 之前的一次性核对）。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/providers/test_llm_factory_snapshot.py -v`
Expected: FAIL（`AttributeError: 'LLMProviderBundle' object has no attribute 'llm_request_config'`）

- [ ] **Step 3: 加字段并在两个 builder 构造 snapshot**

3a. `matmaster/providers/llm_factory.py`：在 `LLMProviderBundle`（`@dataclass(frozen=True)`，`:64-77`）字段末尾（`vision_detail` 之后）加：

```python
    llm_request_config: LLMRequestConfigSnapshot | None = None
```

并在文件顶部 import：

```python
from matmaster.types.llm_request_config import LLMRequestConfigSnapshot
```

3b. 在文件内加一个模块级构造 helper（紧邻 `build_provider_bundle` 之前；它把 profile + extra_body 折成快照，两个 builder 共用，避免重抄十个字段）：

```python
def _snapshot_from_profile(profile, extra_body) -> LLMRequestConfigSnapshot:
    """从已解析的 LLMProfileConfig + 实际 extra_body 折出请求参数快照。"""
    return LLMRequestConfigSnapshot(
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        reasoning_summary=profile.reasoning_summary,
        extra_body=extra_body,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
    )
```

3c. `build_provider_bundle`（`:209-238`）：`:227` 的 `provider = _dispatch(resolved.profile, resolved.provider)` 之后、`return LLMProviderBundle(...)` 里加一个 kwarg。profile 路径 `extra_body` 为 None：

```python
        llm_request_config=_snapshot_from_profile(resolved.profile, None),
```

3d. `build_byok_provider_bundle`（`:241-284`）：在其 dispatch（`:273` 附近）所用的 `extra_body` 变量在手处，`return LLMProviderBundle(...)` 里加：

```python
        llm_request_config=_snapshot_from_profile(<byok_profile_var>, <extra_body_var>),
```

> BYOK 分支构造了等价的 `LLMProfileConfig`（spec 附注 `:257-261`）与 `extra_body`（`:273`）。用 `grep -n "LLMProviderBundle(" matmaster/providers/llm_factory.py` 定位两个 return，按各自局部变量名填入（profile 变量 + extra_body 变量）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/providers/test_llm_factory_snapshot.py -v`
Expected: PASS

冒烟导入：
Run: `python -c "import matmaster.providers.llm_factory"`
Expected: 无异常

- [ ] **Step 5: 提交**

```bash
git add matmaster/providers/llm_factory.py tests/matmaster/providers/test_llm_factory_snapshot.py
git commit -m "feat(providers): capture LLMRequestConfigSnapshot in provider bundle"
```

---

### Task 6: snapshot 透传 `AgentRunRequest` → `AgentKernelSpec`

**Files:**
- Modify: `matmaster/core/run_context.py`（`AgentRunRequest`，`:30-65`，`invocation_id` 字段 `:55` 附近）
- Modify: `matmaster/types/runtime.py`（`AgentKernelSpec`，`@dataclass(frozen=True)`，`:58-73`）
- Modify: `matmaster/core/exp.py`（构造 `AgentKernelSpec` 处，约 `:419-427`）
- Modify: `src/services/agent_run_service.py`（构造 `AgentRunRequest` 处，约 `:577-588`）
- Test: `tests/matmaster/types/test_kernel_spec_snapshot.py`

**Interfaces:**
- Consumes: `LLMProviderBundle.llm_request_config`（Task 5）
- Produces: `AgentRunRequest.llm_request_config` 与 `AgentKernelSpec.llm_request_config`（均 `LLMRequestConfigSnapshot | None = None`）

> **为何要两跳**：`LLMProviderBundle` 不直接流入 kernel——桥是 bundle 标量 → `AgentRunRequest`（`run_context.py:47-55`：llm_provider/llm_model/...）→ `AgentKernelSpec`（`exp.py:419-427` 读 `request.llm_model*`）。要让 kernel emit run_config，须把快照沿这条既有链路加一站。`AgentKernelSpec` 是 kernel 已持有、已读 system_prompt 的正确归宿。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/types/test_kernel_spec_snapshot.py`：

```python
"""AgentRunRequest / AgentKernelSpec 携带 LLMRequestConfigSnapshot。"""

from __future__ import annotations

from matmaster.types.llm_request_config import LLMRequestConfigSnapshot


def _snap():
    return LLMRequestConfigSnapshot(temperature=0.5, timeout=300.0, max_retries=3, retry_delay=1.0)


def test_agent_run_request_has_llm_request_config():
    from matmaster.core.run_context import AgentRunRequest

    req = AgentRunRequest(llm_request_config=_snap())
    assert req.llm_request_config.temperature == 0.5
    # 默认 None
    assert AgentRunRequest().llm_request_config is None


def test_agent_kernel_spec_has_llm_request_config():
    from matmaster.types.runtime import AgentKernelSpec, CompactionConfig
    from matmaster.types.run_metadata import RunIdentity

    spec = AgentKernelSpec(
        system_prompt="SYS",
        max_turns=10,
        compaction=CompactionConfig(),
        run_identity=RunIdentity(),
        llm_request_config=_snap(),
    )
    assert spec.llm_request_config.temperature == 0.5
```

> 若 `AgentRunRequest()` 无参构造因其它必填字段失败，改用现有测试里构造它的 helper（`grep -rn "AgentRunRequest(" tests/ | head`）；若 `CompactionConfig()` / `RunIdentity()` 需必填项，按 `runtime.py:37-55` / `run_metadata.py:8-15` 的默认值核对（核验显示二者均有默认）。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/types/test_kernel_spec_snapshot.py -v`
Expected: FAIL（`TypeError: ... unexpected keyword argument 'llm_request_config'`）

- [ ] **Step 3: 三处加字段 + 两处接线**

3a. `matmaster/core/run_context.py`：在 `AgentRunRequest`（`BaseModel`，`frozen=True`）的 `invocation_id`（`:55`）之后加：

```python
    llm_request_config: LLMRequestConfigSnapshot | None = None
```

并 import：`from matmaster.types.llm_request_config import LLMRequestConfigSnapshot`

3b. `matmaster/types/runtime.py`：在 `AgentKernelSpec`（`@dataclass(frozen=True)`，`:58-73`）末尾字段（`llm_model_route` 之后）加：

```python
    llm_request_config: LLMRequestConfigSnapshot | None = None
```

并 import：`from matmaster.types.llm_request_config import LLMRequestConfigSnapshot`

3c. `matmaster/core/exp.py`：构造 `AgentKernelSpec` 处（约 `:419-427`），加一个 kwarg，从 request 透传：

```python
            llm_request_config=ctx.request.llm_request_config,
```

> 用 `grep -n "AgentKernelSpec(" matmaster/core/exp.py` 定位（核验显示在 `:419-427` 读 `request.llm_model*`，紧邻处加即可）。`ctx.request` 是该作用域内的 `AgentRunContext.request`；若局部变量名不同（如 `request`），按实际名填。

3d. `src/services/agent_run_service.py`：构造 `AgentRunRequest` 处（约 `:577-588`，`invocation_id=invocation_id` 那行附近），加：

```python
                llm_request_config=llm_bundle.llm_request_config,
```

> 用 `grep -n "AgentRunRequest(" src/services/agent_run_service.py` 定位；`llm_bundle` 是该处已有的 `LLMProviderBundle` 变量（核验显示 service 层持有它）。若变量名不同（如 `bundle`），按实际名填。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/types/test_kernel_spec_snapshot.py -v`
Expected: PASS

冒烟导入：
Run: `python -c "import matmaster.core.run_context, matmaster.types.runtime, matmaster.core.exp, src.services.agent_run_service"`
Expected: 无异常

- [ ] **Step 5: 提交**

```bash
git add matmaster/core/run_context.py matmaster/types/runtime.py matmaster/core/exp.py src/services/agent_run_service.py tests/matmaster/types/test_kernel_spec_snapshot.py
git commit -m "feat(runtime): thread LLMRequestConfigSnapshot to AgentKernelSpec"
```

---

### Task 7: kernel emit `run_config`（首次 + 工具集变化）

**Files:**
- Modify: `matmaster/core/agent.py`（`_KernelState` 加跟踪字段；kernel loop `:318-319` 之后 emit；新增模块级 `build_run_config_event`）
- Test: `tests/matmaster/core/test_run_config_emit.py`

**Interfaces:**
- Consumes: `AgentKernelSpec`（`system_prompt`/`llm_model*`/`llm_request_config`）、`ensure_tool_definitions` 产出的 `tool_definitions`、`state.last_catalog_version`
- Produces: 每次运行 emit ≥1 条 `RunConfigEvent`；工具集 version 变化时追加

> **emit 时机**：kernel loop `state.turn += 1`（`:318`）后、`ensure_tool_definitions`（`:319`）之后——此时 tool_definitions 已就绪、且在首次调用 LLM 之前。判断逻辑抽成模块级 `build_run_config_event`（封装「是否需要 emit + 构造事件」的完整决定，便于直测；非为拆而拆）：首次（未 emit 过）→ reason="initial"；`state.last_catalog_version` 变化 → reason="tool_catalog_changed"；否则返回 None。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/core/test_run_config_emit.py`：

```python
"""build_run_config_event：首次 emit + 工具集变化 emit。"""

from __future__ import annotations

from dataclasses import dataclass

from matmaster.core.agent import build_run_config_event
from matmaster.types.events import RunConfigEvent
from matmaster.types.llm_request_config import LLMRequestConfigSnapshot
from matmaster.types.runtime import AgentKernelSpec, CompactionConfig
from matmaster.types.run_metadata import RunIdentity


@dataclass
class _State:
    last_catalog_version: int = 1
    run_config_emitted_version: int | None = None


def _spec():
    return AgentKernelSpec(
        system_prompt="SYS",
        max_turns=10,
        compaction=CompactionConfig(),
        run_identity=RunIdentity(),
        llm_model="m",
        llm_model_profile="p",
        llm_request_config=LLMRequestConfigSnapshot(
            temperature=0.7, timeout=300.0, max_retries=3, retry_delay=1.0
        ),
    )


_TOOLS = [{"type": "function", "function": {"name": "t"}}]


def test_first_call_emits_initial():
    state = _State(last_catalog_version=1, run_config_emitted_version=None)
    ev = build_run_config_event(state, _spec(), _TOOLS)
    assert isinstance(ev, RunConfigEvent)
    assert ev.reason == "initial"
    assert ev.system_prompt == "SYS"
    assert ev.model == "m"
    assert ev.params.temperature == 0.7
    assert ev.tools == _TOOLS
    # 调用方负责在 emit 后置位
    state.run_config_emitted_version = state.last_catalog_version


def test_unchanged_catalog_returns_none():
    state = _State(last_catalog_version=1, run_config_emitted_version=1)
    assert build_run_config_event(state, _spec(), _TOOLS) is None


def test_catalog_change_emits_tool_catalog_changed():
    state = _State(last_catalog_version=2, run_config_emitted_version=1)
    ev = build_run_config_event(state, _spec(), _TOOLS)
    assert ev is not None
    assert ev.reason == "tool_catalog_changed"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/matmaster/core/test_run_config_emit.py -v`
Expected: FAIL（`ImportError: cannot import name 'build_run_config_event'`）

- [ ] **Step 3: 实现 helper、state 字段与 emit 接入**

3a. `matmaster/core/agent.py`：在 `_KernelState` 定义里加一个字段（初值 `None`，记上次 emit run_config 时的 catalog version）：

```python
    run_config_emitted_version: int | None = None
```

> 用 `grep -n "class _KernelState\|last_catalog_version" matmaster/core/agent.py` 定位 `_KernelState`，把字段加在 `last_catalog_version` 邻近。若 `_KernelState` 是 dataclass 用默认值；若是普通类在 `__init__` 里初始化 `self.run_config_emitted_version = None`。

3b. 在 `matmaster/core/agent.py` 模块级（紧邻 `ensure_tool_definitions`，`:88-113` 附近）加：

```python
def build_run_config_event(state, kernel_spec, tool_definitions):
    """决定是否 emit run_config 并构造之；无需 emit 返回 None。

    首次（state.run_config_emitted_version 为 None）记 reason="initial"；之后 catalog
    version 变化记 "tool_catalog_changed"。调用方在拿到非 None 事件、yield 之后，必须
    把 state.run_config_emitted_version 置为 state.last_catalog_version。
    """
    if state.run_config_emitted_version == state.last_catalog_version:
        return None
    reason = "initial" if state.run_config_emitted_version is None else "tool_catalog_changed"
    return RunConfigEvent(
        source="agent",
        reason=reason,
        system_prompt=kernel_spec.system_prompt,
        model=kernel_spec.llm_model,
        model_profile=kernel_spec.llm_model_profile,
        model_route=kernel_spec.llm_model_route,
        params=kernel_spec.llm_request_config,
        tools=tool_definitions,
    )
```

并确保 `agent.py` 顶部 import 了 `RunConfigEvent`（`grep -n "from matmaster.types.events import" matmaster/core/agent.py`，把 `RunConfigEvent` 加入既有 import 列表）。

3c. 在 kernel loop 内，`tool_definitions = ensure_tool_definitions(kernel_resources, state)`（`:319`）之后插入：

```python
            run_config_event = build_run_config_event(state, kernel_spec, tool_definitions)
            if run_config_event is not None:
                yield _KernelItem(event=run_config_event)
                state.run_config_emitted_version = state.last_catalog_version
```

> `_KernelItem(event=...)` 是该文件 yield 事件的既有包装（见 `:382`/`:397` 等 yield 点）；`kernel_spec` / `kernel_resources` / `state` 是 loop 内既有变量。emit 在 `:319` 之后能确保 tool_definitions 已就绪且尚未发起本轮 LLM 调用。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/matmaster/core/test_run_config_emit.py -v`
Expected: PASS（3 passed）

冒烟导入：
Run: `python -c "import matmaster.core.agent"`
Expected: 无异常

- [ ] **Step 5: 提交**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_run_config_emit.py
git commit -m "feat(agent): emit run_config event on start and tool-catalog change"
```

---
