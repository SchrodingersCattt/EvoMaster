# Bohrium submit 用户确认与参数审阅 实现计划（建在通用交互底座之上）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Bohrium `submit` 重做成通用交互底座（Phase 1）的第二个使用者——在真正调用 `job/create` / 上传 / `job/add` 前引入可选的人类确认点，让用户确认/拒绝、改 submit 参数、并把决定与改动通过 `ToolResult` 明确告知 agent；传输层一律引用底座、不自造。

**Architecture:** 三角色架在同一套底座上——(1) `SubmitReviewProvider`（BohriumTool 侧的提交语义知识：draft + 幂等规范化）；(2) `SubmitApprovalGate`（接入层 adapter，包 `InteractionBridge`，把 draft 序列化成 `kind="submit_review"` 的 interaction payload、把 reply/异常映射成 `SubmitReviewDecision`）；(3) `FullToolRunner`（指挥：串行阶段 await 闸门、处理 approved/blocked/cancelled、跑重提交护栏；文件变更直接透传不校验）。Bohrium 专属内容全部下沉进 interaction `payload`，零新增事件类型 / 端点。

**Tech Stack:** Python ≥3.10 / dataclass + Protocol（接口契约）/ Pydantic（`ToolResult` / 事件）/ FastAPI（底座端点，本 plan 不碰）/ pytest（`uv run --extra dev pytest`）。依赖 Phase 1（2026-06-18 交互底座迁移）已落地。

---

## 前置依赖与确认项（实现者必读）

1. **硬前置：Phase 1 必须先落地。** 本 plan 是 Phase 2，引用 Phase 1（`docs/superpowers/plans/2026-06-18-interaction-bridge-migration.md`）产出的：`InteractionBridge.request(kind, request_id, payload, timeout_seconds) -> dict`（raise `InteractionBusyError` / `TimeoutError` / `asyncio.CancelledError`）、`InteractionBusyError`（`matmaster/integration/interaction_bridge.py`）、`InteractionTimeoutEvent`（`matmaster/types/events.py`，已在 `matmaster/types/__init__.py` 导出，`source` 必填、不带 `session_id`）、通用 reply 端点 `POST /chat/sessions/{session_id}/interactions/{request_id}/reply`（body `{kind, payload}`、`_MAX_REPLY_PAYLOAD_BYTES = 256 KiB`）。**✅ Phase 1 已于 merge `421e4232` 落地**，上述产物经核实全部就位且与本 plan 假设一致（`request`/`emit` 签名、`InteractionBusyError`、`InteractionTimeoutEvent` 字段与 `source` 约束、reply 端点路径/body、`_MAX_REPLY_PAYLOAD_BYTES=256KiB` 逐一核对无误）。

2. **bridge 发 timeout 的方式（已确认）。** 本 plan 的 gate 在 `TimeoutError` 时发 `interaction_timeout`，采用 `await self._bridge.emit(InteractionTimeoutEvent(...))`。**Phase 1 已落地 `InteractionBridge.emit(event)` 薄方法**（`interaction_bridge.py:40-41`），`AskQuestionTool` 即用它发 timeout（`ask_question_tool.py:154`）。Task 3 的 gate `_emit_timeout` 直接调 `emit` 即可，无需访问 `_event_sink`，原"二选一"已消除。

3. **opt-in 配置来源已解耦（用户决策）。** spec §5.2 要求 `submit_confirmation_enabled` 两级解析（session 覆盖 user 全局、默认关），但当前 `evo_chat_sessions` 表无可扩展配置列、`user_preference` 表未在 run 启动时接入、全库无两级解析先例。经确认：**本 plan 不动任何 DB schema**，把 opt-in 的 effective 布尔值作为 `AgentRunService.run_agent()` 的输入边界（新增参数 `submit_confirmation_enabled: bool = False`，由调用方传入），两级配置存储与解析留作独立后续 plan。本 plan 只负责"effective 为真且顶层 run → 构造并注入 gate"。

## 范围与边界

- **测试范围 = spec §12 所列 15 项**，按 TDD 分布到对应 Task，Task 7 收口跑全集。本 plan 是**新增功能**，TDD 直测正当（与"删除/瘦身禁新增测试"无冲突）；唯一含删除成分的是 `_submit` 移除散落默认/cmd 逻辑，那部分不单独加测试，靠 §12.15 opt-out 回归 + §12.9 cmd hidden normalization 覆盖。
- **不修改底座。** pending registry / reply key / active 守卫 / reply 端点 / `interaction_*` 事件 / Lua 一律引用 Phase 1，本 plan 是纯接入层。
- **不校验文件变更（完全放行）。** 后端对前端上报的 `reported_input_file_changes` 不读正文、不 diff、不验证路径/存在性/metadata/`lines`，原样透传进 review/payload（参数与文件正确性由前端校验、让用户重试）。文件正文绝不进 request / reply / tool_result / payload（前端本就只上报 `relative_path`/`lines`）。
- **git 约束。** 本仓库 `CLAUDE.md` 明令不要向 `docs/` 做任何 git 提交。本 plan 文档与 spec 都不进 commit；各任务 `git add` 只添加被改的源码与测试文件，**不要** add `docs/`。

---

## 文件结构（改动地图）

| 文件 | 改动 | 职责 |
|------|------|------|
| `matmaster/types/submit_review.py` | **新建** | 四层参数数据类 + `SubmitReviewProvider` / `SubmitApprovalGate` 两个 Protocol（纯类型，无逻辑、无重依赖） |
| `matmaster/types/tool_spec.py` | 改 `ToolInstance`（104-127）加字段 | `submit_review_provider` 挂载位（类比 `input_validator`） |
| `matmaster/tools/builtin/bohrium_tool/submit_review.py` | **新建** | Bohrium 专属：共享 canonicalize + `build_review_draft` + `normalize_execution_args` + `BohriumSubmitReviewProvider` |
| `matmaster/tools/builtin/bohrium_tool/tool.py` | 改 `_submit`（446-510）+ `submit_job_via_runtime`（76-152）+ 加 provider 类属性 | 挂 provider；`_submit` 入口经 `normalize_execution_args`；移除散落默认/cmd；`submit_job_via_runtime` 改 defensive |
| `matmaster/bohrium/errors.py` | 改 `BohriumTransferError`（16） | 加可选 `created_job_ref` 属性（供 §9.3 partial 审计） |
| `matmaster/tools/tool_compiler.py` | 改 `compile`（18-76） | 检测并挂 `submit_review_provider` 到 `ToolInstance` |
| `matmaster/integration/submit_approval_gate.py` | **新建** | `BridgeSubmitApprovalGate` adapter + payload/decision 序列化 + 异常映射 |
| `matmaster/types/runtime_ports.py` | 改 `AgentRunPorts`（163-182）加字段 | `submit_approval_gate` 规范归属 |
| `matmaster/core/submit_review_support.py` | **新建** | runner 侧纯函数：reply 校验 / 护栏签名 / parameter_changes diff / 审计 payload / attach / enforce |
| `matmaster/core/tool_runner.py` | 改 `execute_batch` 串行阶段（PRE hook 后、structural 前插闸门） | 闸门编排（指挥） |
| `matmaster/core/exp.py` | 改 `build_runtime`（231-445）：读 ports 注入 runner_state + 注册 attach/enforce POST hook | 装配 |
| `src/services/agent_run_service.py` | 改 `run_agent`（251-270）+ bridge 构造（509-516）+ AgentRunPorts 构造（578-589） | opt-in 构造 gate 填 ports |

测试文件：`tests/matmaster/tools/builtin/test_bohrium_submit_review.py`（新）、`tests/matmaster/integration/test_submit_approval_gate.py`（新）、`tests/matmaster/core/test_submit_review_support.py`（新）、`tests/matmaster/core/test_full_tool_runner.py`（扩展）、`tests/matmaster/tools/builtin/test_bohrium_tool.py`（扩展）、`tests/matmaster/services/test_agent_run_stream_interaction.py`（扩展）。

---

## 命名与常量约定（全计划统一）

```python
# kind / schema（gate adapter）
SUBMIT_REVIEW_KIND = "submit_review"
SUBMIT_REVIEW_SCHEMA_VERSION = 1

# request_id 前缀（runner 生成）
SUBMIT_REVIEW_REQUEST_PREFIX = "sr_"

# runner_state keys（exp 注入 + runner 读 + hook 取）
SUBMIT_APPROVAL_GATE_KEY = "submit_approval_gate"
RUN_IDENTITY_KEY = "run_identity"
RESUBMIT_SIGNATURES_KEY = "bohrium_submit_resubmit_signatures"   # value: set[str]
SUBMIT_REVIEW_RECORDS_KEY = "submit_review_records"              # value: dict[tool_call_id, record]

# Bohrium 默认值（与现状 tool.py:462-464 一致）
DEFAULT_MACHINE = "c32_m128_cpu"
DEFAULT_JOB_NAME = "matmaster-job"
DEFAULT_DISK_SIZE = 50
CMD_LOG_SUFFIX = "> log 2>&1"
EDITABLE_FIELDS = ["input_dir", "image", "cmd", "machine", "job_name", "disk_size"]
SUBMIT_FIELDS = ["action", "input_dir", "image", "cmd", "machine", "job_name", "disk_size"]

# 长度 / 条数上限（spec §7.1 / §7.5）
MAX_CMD_LEN = 8192
MAX_IMAGE_LEN = 512
MAX_MACHINE_LEN = 128
MAX_JOB_NAME_LEN = 256
MAX_INPUT_DIR_LEN = 2048
MAX_CONTENT_FILE_CHANGES = 20
MAX_PAYLOAD_FILE_CHANGES = 200
```

**`review_outcome` 合法值**：`approved | rejected | timeout | cancelled | busy`（spec §5.5：`review_unavailable` 在底座之上塌缩成 busy/timeout；**本 plan 偏离 spec：删去 `invalid_final_arguments`**——文件变更不再校验、超长参数走 fail-loud `error`，详见文末"偏离 spec 说明"）。

**interaction inner payload 形状**（gate 构造，spec §7.3a）：`{schema_version, tool_name, tool_call_id, model_arguments, review_draft_arguments, normalization_changes, draft_issues, editable_fields, input_dir, file_edit_mode}`。

**reply payload 形状**（前端提交，spec §7.4）：`{decision: "submit"|"reject", submit_arguments: {...}, reported_input_file_changes: [{relative_path, lines}]}`。

**审计 payload 形状**（`payload.bohrium_submit_review`，spec §9.3）：见 Task 4 `build_audit_payload`。

---

## Task 1: submit review 类型与协议

**Files:**
- Create: `matmaster/types/submit_review.py`
- Modify: `matmaster/types/tool_spec.py`（`ToolInstance` 104-127 加字段；`__init__` 风格沿用 frozen dataclass）
- Test: `tests/matmaster/core/test_submit_review_support.py`（本 Task 先建文件放最小构造冒烟）

类型基石。所有后续 Task 引用这里的数据类与协议。

- [ ] **Step 1: 写最小构造冒烟测试**

新建 `tests/matmaster/core/test_submit_review_support.py`：

```python
from matmaster.types.submit_review import (
    SubmitReviewDraft, SubmitExecutionArgs, SubmitReviewRequest,
    SubmitReviewDecision, SubmitReviewProvider, SubmitApprovalGate,
)


def test_submit_review_dataclasses_construct():
    draft = SubmitReviewDraft(
        model_arguments={"action": "submit"},
        review_draft_arguments={"action": "submit", "machine": "c32_m128_cpu"},
        normalization_changes={},
        draft_issues=[],
        editable_fields=["cmd"],
        input_dir="/share/case_001",
    )
    assert draft.file_edit_mode == "live_reported"
    req = SubmitReviewRequest(
        request_id="sr_x", tool_name="Bohrium", tool_call_id="call_x",
        task_id="t", session_id="s", draft=draft,
    )
    assert req.timeout_seconds is None
    dec = SubmitReviewDecision(user_decision="submit", review_outcome="approved")
    assert dec.final_arguments is None
    exe = SubmitExecutionArgs(arguments={"action": "submit"}, normalization_changes={})
    assert exe.arguments["action"] == "submit"


def test_protocols_are_runtime_checkable():
    class _P:
        def build_review_draft(self, model_args): return None
        def normalize_execution_args(self, args): return SubmitExecutionArgs({}, {})
    assert isinstance(_P(), SubmitReviewProvider)

    class _G:
        async def review(self, request): return SubmitReviewDecision(None, "busy")
    assert isinstance(_G(), SubmitApprovalGate)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --extra dev pytest tests/matmaster/core/test_submit_review_support.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matmaster.types.submit_review'`

- [ ] **Step 3: 新建 submit_review.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class SubmitReviewArgumentError(ValueError):
    """submit 参数非法（超长等）→ 硬失败：不返回 None、不进 review、不 fall-through 到执行。"""


@dataclass
class SubmitReviewDraft:
    """展示型草稿（宽松、无副作用），provider.build_review_draft 产出。"""

    model_arguments: dict[str, Any]
    review_draft_arguments: dict[str, Any]
    normalization_changes: dict[str, Any]
    draft_issues: list[dict[str, Any]]
    editable_fields: list[str]
    input_dir: str
    file_edit_mode: str = "live_reported"


@dataclass
class SubmitExecutionArgs:
    """严格规范化后的执行参数，provider.normalize_execution_args 产出（幂等）。"""

    arguments: dict[str, Any]
    normalization_changes: dict[str, Any]


@dataclass
class SubmitReviewRequest:
    """runner 交给 gate 的审阅请求。"""

    request_id: str
    tool_name: str
    tool_call_id: str
    task_id: str
    session_id: str
    draft: SubmitReviewDraft
    timeout_seconds: int | None = None


@dataclass
class SubmitReviewDecision:
    """gate 回给 runner 的决定。"""

    user_decision: str | None       # "submit" | "reject" | None
    review_outcome: str             # approved|rejected|timeout|cancelled|busy
    final_arguments: dict[str, Any] | None = None
    reported_input_file_changes: list[dict[str, Any]] | None = None


@runtime_checkable
class SubmitReviewProvider(Protocol):
    """工具侧提交语义知识：draft + 幂等严格规范化。"""

    def build_review_draft(self, model_args: dict[str, Any]) -> SubmitReviewDraft | None: ...

    def normalize_execution_args(self, args: dict[str, Any]) -> SubmitExecutionArgs: ...


@runtime_checkable
class SubmitApprovalGate(Protocol):
    """接入层：发起提交审阅并阻塞等待决定。"""

    async def review(self, request: SubmitReviewRequest) -> SubmitReviewDecision: ...
```

- [ ] **Step 4: 给 ToolInstance 加 submit_review_provider 字段**

`matmaster/types/tool_spec.py`，在 `ToolInstance`（104-127）的 `input_validator` 字段之后追加（保持 frozen dataclass、默认 None，类比现有 `input_validator`）：

```python
    submit_review_provider: "SubmitReviewProvider | None" = None
```

文件顶部 import 区加：`from matmaster.types.submit_review import SubmitReviewProvider`（同包 `matmaster/types/`，`submit_review.py` 不反向 import `tool_spec`，无循环）。若用字符串前置引用 `"SubmitReviewProvider | None"` 则确保运行时可解析，简单起见直接 import 实名引用。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run --extra dev pytest tests/matmaster/core/test_submit_review_support.py -v`
Expected: 2 个用例 PASS

- [ ] **Step 6: import 冒烟**

Run: `uv run python -c "from matmaster.types.tool_spec import ToolInstance; from matmaster.types.submit_review import SubmitReviewProvider; print(ToolInstance.__dataclass_fields__['submit_review_provider'])"`
Expected: 无 ImportError，打印出字段

- [ ] **Step 7: Commit**

```bash
git add matmaster/types/submit_review.py matmaster/types/tool_spec.py tests/matmaster/core/test_submit_review_support.py
git commit -m "feat(types): submit review dataclasses + provider/gate protocols + ToolInstance slot"
```

---

## Task 2: Bohrium provider + 挂载 + _submit 规范化关口

**Files:**
- Create: `matmaster/tools/builtin/bohrium_tool/submit_review.py`
- Modify: `matmaster/tools/builtin/bohrium_tool/tool.py`（`_submit` 446-510、`submit_job_via_runtime` 94-96/108-112、加 provider 类属性）
- Modify: `matmaster/bohrium/errors.py`（`BohriumTransferError` 16 加 `created_job_ref` 属性）
- Modify: `matmaster/tools/tool_compiler.py`（`compile` 68-75 挂载段）
- Test: `tests/matmaster/tools/builtin/test_bohrium_submit_review.py`（新）、`tests/matmaster/tools/builtin/test_bohrium_tool.py`（扩展 opt-out 回归）

**依赖 Task 1。** 本 Task 落地 spec §5.9 的幂等规范化关口（两条路径共享 canonicalize），并把 provider 挂上编译链。

- [ ] **Step 1: 写 provider 直测（draft / normalize / 幂等）**

新建 `tests/matmaster/tools/builtin/test_bohrium_submit_review.py`：

```python
from matmaster.tools.builtin.bohrium_tool.submit_review import (
    build_review_draft, normalize_execution_args, BohriumSubmitReviewProvider,
)


def test_draft_none_for_non_submit():
    assert build_review_draft({"action": "query", "job_id": "1"}) is None
    assert build_review_draft({}) is None


def test_draft_adds_defaults_and_cmd_redirect():
    """spec §12.1：cmd 重定向在 draft 阶段；默认值补齐。"""
    d = build_review_draft({"action": "submit", "input_dir": "/share/c", "image": "img", "cmd": "python run.py"})
    assert d is not None
    assert d.review_draft_arguments["cmd"] == "python run.py > log 2>&1"
    assert d.review_draft_arguments["machine"] == "c32_m128_cpu"
    assert d.review_draft_arguments["job_name"] == "matmaster-job"
    assert d.review_draft_arguments["disk_size"] == 50
    assert d.normalization_changes["cmd"]["to"] == "python run.py > log 2>&1"
    assert d.model_arguments["cmd"] == "python run.py"   # 原始不被改
    assert d.draft_issues == []


def test_draft_missing_required_keeps_issues_still_reviewable():
    """spec §12.1：缺 image 生成 draft_issues 仍可发 review（返回非 None）。"""
    d = build_review_draft({"action": "submit", "input_dir": "/share/c", "cmd": "python run.py"})
    assert d is not None
    codes = {i["field"]: i["code"] for i in d.draft_issues}
    assert codes["image"] == "missing_required_field"


def test_draft_oversized_field_raises():
    """过长 cmd/image 不绕过闸门：build_review_draft 直接 raise（fail-loud），绝不返回 None。"""
    import pytest
    from matmaster.types.submit_review import SubmitReviewArgumentError
    with pytest.raises(SubmitReviewArgumentError):
        build_review_draft({"action": "submit", "input_dir": "/share/c", "image": "i", "cmd": "x" * 9000})


def test_normalize_is_idempotent():
    once = normalize_execution_args({"action": "submit", "input_dir": "/share/c", "image": "i", "cmd": "run"})
    twice = normalize_execution_args(once.arguments)
    assert once.arguments == twice.arguments
    assert twice.normalization_changes == {}   # 第二次无改动


def test_provider_object_implements_protocol():
    p = BohriumSubmitReviewProvider()
    assert p.build_review_draft({"action": "submit", "input_dir": "/s", "image": "i", "cmd": "c"}) is not None
    assert p.normalize_execution_args({"action": "submit"}).arguments["machine"] == "c32_m128_cpu"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run --extra dev pytest tests/matmaster/tools/builtin/test_bohrium_submit_review.py -v`
Expected: FAIL with `ModuleNotFoundError: ...bohrium_tool.submit_review`

- [ ] **Step 3: 新建 submit_review.py（canonicalize + draft + normalize + provider）**

```python
from __future__ import annotations

from typing import Any

from matmaster.types.submit_review import (
    SubmitExecutionArgs,
    SubmitReviewArgumentError,
    SubmitReviewDraft,
)

DEFAULT_MACHINE = "c32_m128_cpu"
DEFAULT_JOB_NAME = "matmaster-job"
DEFAULT_DISK_SIZE = 50
CMD_LOG_SUFFIX = "> log 2>&1"
EDITABLE_FIELDS = ["input_dir", "image", "cmd", "machine", "job_name", "disk_size"]
SUBMIT_FIELDS = ["action", "input_dir", "image", "cmd", "machine", "job_name", "disk_size"]
_MAX_LEN = {"cmd": 8192, "image": 512, "machine": 128, "job_name": 256, "input_dir": 2048}


def _canonicalize_submit_args(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """共享 canonicalize：补默认值 + cmd 日志重定向。返回 (canonical, changes)。幂等。"""
    canonical: dict[str, Any] = {k: args[k] for k in SUBMIT_FIELDS if k in args}
    changes: dict[str, Any] = {}

    if not canonical.get("machine"):
        changes["machine"] = {"from": canonical.get("machine"), "to": DEFAULT_MACHINE}
        canonical["machine"] = DEFAULT_MACHINE
    if not canonical.get("job_name"):
        changes["job_name"] = {"from": canonical.get("job_name"), "to": DEFAULT_JOB_NAME}
        canonical["job_name"] = DEFAULT_JOB_NAME
    raw_disk = canonical.get("disk_size")
    if raw_disk in (None, ""):
        changes["disk_size"] = {"from": raw_disk, "to": DEFAULT_DISK_SIZE}
        canonical["disk_size"] = DEFAULT_DISK_SIZE
    else:
        canonical["disk_size"] = int(raw_disk)

    cmd = canonical.get("cmd")
    if cmd:
        stripped = cmd.rstrip()
        if not stripped.endswith(CMD_LOG_SUFFIX):
            new_cmd = f"{stripped} {CMD_LOG_SUFFIX}"
            changes["cmd"] = {"from": cmd, "to": new_cmd}
            canonical["cmd"] = new_cmd
        else:
            canonical["cmd"] = stripped
    return canonical, changes


def oversized_submit_fields(args: Any) -> list[str]:
    """返回超过 _MAX_LEN 的字段名（空 = 合法）。build_review_draft 与 _submit 入口共享。"""
    if not isinstance(args, dict):
        return []
    return sorted(
        fld for fld, maxlen in _MAX_LEN.items()
        if isinstance(args.get(fld), str) and len(args[fld]) > maxlen
    )


def build_review_draft(model_args: Any) -> SubmitReviewDraft | None:
    """None 只表示非 submit / provider 不适用。action=='submit' 一律返回 draft；
    超长字段直接 raise SubmitReviewArgumentError（fail-loud，绝不返回 None 让 runner fall-through 到执行）。"""
    if not isinstance(model_args, dict) or model_args.get("action") != "submit":
        return None
    oversized = oversized_submit_fields(model_args)
    if oversized:
        raise SubmitReviewArgumentError(
            f"submit argument(s) exceed max length: {', '.join(oversized)}"
        )

    canonical, changes = _canonicalize_submit_args(model_args)
    issues: list[dict[str, Any]] = []
    for fld in ("input_dir", "image", "cmd"):
        if not model_args.get(fld):
            issues.append({
                "field": fld, "code": "missing_required_field",
                "message": f"{fld} is required before submit.",
            })
    return SubmitReviewDraft(
        model_arguments=dict(model_args),
        review_draft_arguments=canonical,
        normalization_changes=changes,
        draft_issues=issues,
        editable_fields=list(EDITABLE_FIELDS),
        input_dir=str(model_args.get("input_dir") or ""),
        file_edit_mode="live_reported",
    )


def normalize_execution_args(args: Any) -> SubmitExecutionArgs:
    """严格、幂等、无副作用、执行用。与 build_review_draft 共享 canonicalize。
    required 校验由 _submit body / structural validation 承担，此处不重复（保持幂等）。"""
    canonical, changes = _canonicalize_submit_args(dict(args) if isinstance(args, dict) else {})
    return SubmitExecutionArgs(arguments=canonical, normalization_changes=changes)


class BohriumSubmitReviewProvider:
    """无状态 provider 单例；实现 SubmitReviewProvider 协议。"""

    def build_review_draft(self, model_args: dict[str, Any]) -> SubmitReviewDraft | None:
        return build_review_draft(model_args)

    def normalize_execution_args(self, args: dict[str, Any]) -> SubmitExecutionArgs:
        return normalize_execution_args(args)
```

- [ ] **Step 4: 运行 provider 直测确认通过**

Run: `uv run --extra dev pytest tests/matmaster/tools/builtin/test_bohrium_submit_review.py -v`
Expected: 6 个用例 PASS

- [ ] **Step 5: BohriumTool 挂 provider + _submit 入口走 normalize + 移除散落默认**

`matmaster/tools/builtin/bohrium_tool/tool.py`：

(a) 文件顶部 import 区加：

```python
from .submit_review import BohriumSubmitReviewProvider, normalize_execution_args, oversized_submit_fields
```

(b) `BohriumTool` 类体内（与其它 ClassVar 同区，如 `exposed_to_model` 附近）加：

```python
    submit_review_provider: ClassVar[BohriumSubmitReviewProvider] = BohriumSubmitReviewProvider()
```

(c) 把 `_submit`（446-464 头部）改为先经 `normalize_execution_args` 取规范化参数，删除原散落的 `args.get("machine", "c32_m128_cpu")` / `job_name` / `disk_size` 三行（462-464）：

```python
    def _submit(self, args: dict[str, Any]) -> ToolResult:
        exec_args = normalize_execution_args(args).arguments   # 幂等：opt-out 对 model_args、opt-in 对已规范化 args 均安全
        oversized = oversized_submit_fields(exec_args)         # 后端兜底（spec 偏离）：透传的超长参数 fail-loud，绝不 submit
        if oversized:
            return ToolResult(status="error", content=f"Submit argument(s) too long: {', '.join(oversized)}")
        input_dir = exec_args.get("input_dir", "")
        image = exec_args.get("image", "")
        cmd = exec_args.get("cmd", "")

        if not input_dir:
            return ToolResult(status="error", content="Missing required parameter: input_dir")
        if not image:
            return ToolResult(status="error", content="Missing required parameter: image")
        if not cmd:
            return ToolResult(status="error", content="Missing required parameter: cmd")

        machine = exec_args["machine"]      # normalize 已补默认
        job_name = exec_args["job_name"]
        disk_size = exec_args["disk_size"]
        # ...（其余 ctx 构造、submit_job_via_runtime 调用、ledger、返回 不变；见 Step 6 执行审计 meta）
```

- [ ] **Step 6: _submit 写执行审计 meta（供 attach 合并，spec §9.3）+ BohriumTransferError 带属性**

(a) `submit_job_via_runtime`（76-152）的 `BohriumTransferError` 改为携带结构化 `created_job_ref`。`BohriumTransferError` 定义在 `matmaster/bohrium/errors.py:16`（`tool.py:40` 从那里 import），给它加可选属性：

```python
class BohriumTransferError(BohriumError):
    def __init__(self, message: str, *, created_job_ref: Any = None) -> None:
        super().__init__(message)
        self.created_job_ref = created_job_ref
```

`submit_job_via_runtime` 抛出处（108-112）改为 `raise BohriumTransferError(..., created_job_ref=created_ref) from exc`——该处已有 `created_ref = _created_job_ref(create_data)` 局部变量（107，当前仅塞进异常文本），直接传入即可。

(b) `_submit` 的成功返回（488-499）在 `ToolResult` 上补 `meta`（内部信号，不进 public payload）：

```python
            return ToolResult(
                status="success",
                content=json.dumps(
                    {"success": True, "job_id": submitted.job_id, "status": "Submitted", "use_sandbox": ctx.sandbox},
                    ensure_ascii=False,
                ),
                meta={"submit_execution_audit": {
                    "execution_attempted": True, "external_effect_started": True,
                    "job_create_attempted": True, "job_id": submitted.job_id,
                    "input_upload_attempted": True, "job_add_attempted": True,
                }},
            )
```

(c) `_submit` 的 `except (BohriumError, ValueError) as exc`（500-501）拆出 transfer 分支记 partial 审计：

```python
        except BohriumTransferError as exc:
            return ToolResult(
                status="error", content=str(exc),
                meta={"submit_execution_audit": {
                    "execution_attempted": True, "external_effect_started": True,
                    "job_create_attempted": True, "job_id": exc.created_job_ref,
                    "input_upload_attempted": True, "job_add_attempted": False,
                }},
            )
        except (BohriumError, ValueError) as exc:
            return ToolResult(
                status="error", content=str(exc),
                meta={"submit_execution_audit": {"execution_attempted": True, "external_effect_started": False}},
            )
```

(d) `submit_job_via_runtime` 的 cmd 追加（94-96）改为 defensive 检测（不再静默追加，spec §5.9）：

```python
    if not cmd.rstrip().endswith("> log 2>&1"):
        raise BohriumError("cmd not normalized (missing log redirection); normalize_execution_args must run before submit_job_via_runtime")
```

先 `grep -rn "submit_job_via_runtime" matmaster/ src/ tests/` 确认唯一调用者是 `_submit`（已 normalize），否则补调用点的规范化。

- [ ] **Step 7: ToolCompiler 挂 submit_review_provider**

`matmaster/tools/tool_compiler.py` 的 `compile`（68-75），在挂 `validator` 之后、构造 `ToolInstance` 之前加：

```python
        submit_review_provider = getattr(tool, "submit_review_provider", None)
```

`ToolInstance(...)` 构造（75 附近）追加参数 `submit_review_provider=submit_review_provider`。

- [ ] **Step 8: 扩展 opt-out 回归测试（§12.15 + §12.9 + 挂载）**

`tests/matmaster/tools/builtin/test_bohrium_tool.py` 追加（沿用该文件既有 BohriumTool fixture / mock：mock `submit_job_via_runtime` 或其内部 `create_job`/`add_job`，断言收到的参数）：

```python
def test_submit_optout_still_applies_defaults(...):
    """spec §12.15：gate 不存在时 submit 仍经 _submit 入口 normalize 得默认 + 日志重定向。"""
    # 调 _submit({"action":"submit","input_dir":"/share/c","image":"img","cmd":"run"})
    # 断言 submit_job_via_runtime 收到 machine="c32_m128_cpu" / job_name="matmaster-job" / disk_size=50 / cmd="run > log 2>&1"


def test_submit_job_via_runtime_defensive_on_unnormalized_cmd(...):
    """spec §12.9：submit_job_via_runtime 对未规范化 cmd 报错（不再隐式追加）。"""
    # 直接调 submit_job_via_runtime(cmd="run", ...) → pytest.raises(BohriumError, match="not normalized")


def test_compiled_bohrium_instance_carries_provider(...):
    """闸门启用前提：编译后的 BohriumTool instance 带 submit_review_provider。"""
    # 用 ToolCompiler.compile(BohriumTool(...), topology) → assert instance.submit_review_provider is not None
```

- [ ] **Step 9: 运行测试验证**

Run: `uv run --extra dev pytest tests/matmaster/tools/builtin/test_bohrium_submit_review.py tests/matmaster/tools/builtin/test_bohrium_tool.py -v`
Expected: 全 PASS（含既有 BohriumTool 用例不回归）

- [ ] **Step 10: Commit**

```bash
git add matmaster/bohrium/errors.py matmaster/tools/builtin/bohrium_tool/submit_review.py matmaster/tools/builtin/bohrium_tool/tool.py matmaster/tools/tool_compiler.py tests/matmaster/tools/builtin/test_bohrium_submit_review.py tests/matmaster/tools/builtin/test_bohrium_tool.py
git commit -m "feat(bohrium): submit review provider + shared canonicalize + normalize gate at _submit entry"
```

---

## Task 3: SubmitApprovalGate adapter + AgentRunPorts 字段

**Files:**
- Create: `matmaster/integration/submit_approval_gate.py`
- Modify: `matmaster/types/runtime_ports.py`（`AgentRunPorts` 163-182 加字段）
- Test: `tests/matmaster/integration/test_submit_approval_gate.py`（新）

**依赖 Task 1 + Phase 1。** 本 Task 落地 spec §5.1 / §5.5 的接入层 adapter（包 bridge、异常映射）与 ports 归属。

- [ ] **Step 1: 写 gate 异常映射 + payload 形状直测（§12.5 / §7.3）**

新建 `tests/matmaster/integration/test_submit_approval_gate.py`（用 `_FakeBridge` mock `request` / `emit`）：

```python
import asyncio
import pytest
from matmaster.integration.submit_approval_gate import BridgeSubmitApprovalGate, _draft_to_payload
from matmaster.integration.interaction_bridge import InteractionBusyError
from matmaster.types.submit_review import SubmitReviewRequest, SubmitReviewDraft


def _req():
    draft = SubmitReviewDraft(
        model_arguments={"action": "submit", "cmd": "run"},
        review_draft_arguments={"action": "submit", "cmd": "run > log 2>&1", "machine": "c32_m128_cpu"},
        normalization_changes={"cmd": {"from": "run", "to": "run > log 2>&1"}},
        draft_issues=[], editable_fields=["cmd"], input_dir="/share/c",
    )
    return SubmitReviewRequest(request_id="sr_1", tool_name="Bohrium", tool_call_id="call_1",
                               task_id="t", session_id="s", draft=draft)


class _FakeBridge:
    def __init__(self, *, reply=None, exc=None):
        self._reply, self._exc, self.emitted = reply, exc, []
    async def request(self, *, kind, request_id, payload, timeout_seconds=None):
        self.last_payload = payload
        if self._exc is not None:
            raise self._exc
        return self._reply
    async def emit(self, event):
        self.emitted.append(event)


def test_draft_to_payload_shape():
    payload = _draft_to_payload(_req())
    assert payload["schema_version"] == 1
    assert payload["tool_name"] == "Bohrium" and payload["tool_call_id"] == "call_1"
    assert payload["review_draft_arguments"]["cmd"] == "run > log 2>&1"
    assert payload["editable_fields"] == ["cmd"]
    assert payload["file_edit_mode"] == "live_reported"
    assert "session_id" not in payload   # session 不进 inner payload


@pytest.mark.asyncio
async def test_approved_and_rejected():
    g = BridgeSubmitApprovalGate(_FakeBridge(reply={"decision": "submit", "submit_arguments": {"action": "submit", "cmd": "run > log 2>&1"}, "reported_input_file_changes": [{"relative_path": "a", "lines": "1"}]}))
    d = await g.review(_req())
    assert d.review_outcome == "approved" and d.user_decision == "submit"
    assert d.final_arguments["cmd"] == "run > log 2>&1"
    assert d.reported_input_file_changes == [{"relative_path": "a", "lines": "1"}]

    g2 = BridgeSubmitApprovalGate(_FakeBridge(reply={"decision": "reject", "submit_arguments": {"action": "submit"}}))
    assert (await g2.review(_req())).review_outcome == "rejected"


@pytest.mark.asyncio
async def test_busy_timeout_cancel_mapping():
    g = BridgeSubmitApprovalGate(_FakeBridge(exc=InteractionBusyError("x")))
    assert (await g.review(_req())).review_outcome == "busy"

    fb = _FakeBridge(exc=TimeoutError("x"))
    assert (await BridgeSubmitApprovalGate(fb).review(_req())).review_outcome == "timeout"
    assert len(fb.emitted) == 1 and fb.emitted[0].kind == "submit_review"   # emit interaction_timeout

    g3 = BridgeSubmitApprovalGate(_FakeBridge(exc=asyncio.CancelledError()))
    assert (await g3.review(_req())).review_outcome == "cancelled"   # 不向上抛
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run --extra dev pytest tests/matmaster/integration/test_submit_approval_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: ...submit_approval_gate`

- [ ] **Step 3: 新建 submit_approval_gate.py**

```python
from __future__ import annotations

import asyncio
from typing import Any

from matmaster.integration.interaction_bridge import InteractionBridge, InteractionBusyError
from matmaster.types import InteractionTimeoutEvent
from matmaster.types.submit_review import SubmitReviewDecision, SubmitReviewRequest

SUBMIT_REVIEW_KIND = "submit_review"
SUBMIT_REVIEW_SCHEMA_VERSION = 1


def _draft_to_payload(request: SubmitReviewRequest) -> dict[str, Any]:
    """只构造 inner payload（gate 传给 bridge.request(payload=...)）；底座再装信封。"""
    d = request.draft
    return {
        "schema_version": SUBMIT_REVIEW_SCHEMA_VERSION,
        "tool_name": request.tool_name,
        "tool_call_id": request.tool_call_id,
        "model_arguments": d.model_arguments,
        "review_draft_arguments": d.review_draft_arguments,
        "normalization_changes": d.normalization_changes,
        "draft_issues": d.draft_issues,
        "editable_fields": d.editable_fields,
        "input_dir": d.input_dir,
        "file_edit_mode": d.file_edit_mode,
    }


def _reply_to_decision(reply: dict[str, Any]) -> SubmitReviewDecision:
    decision = reply.get("decision")
    if decision == "submit":
        outcome = "approved"
    elif decision == "reject":
        outcome = "rejected"
    else:
        outcome = "rejected"   # 未知 decision 保守当拒绝（不产生外部副作用）
    return SubmitReviewDecision(
        user_decision=decision if decision in ("submit", "reject") else None,
        review_outcome=outcome,
        final_arguments=reply.get("submit_arguments"),
        reported_input_file_changes=reply.get("reported_input_file_changes"),
    )


class BridgeSubmitApprovalGate:
    """submit_review 接入层 adapter：包通用 InteractionBridge，对称 AskQuestionTool 之于 ask_question。"""

    def __init__(self, bridge: InteractionBridge) -> None:
        self._bridge = bridge

    async def review(self, request: SubmitReviewRequest) -> SubmitReviewDecision:
        payload = _draft_to_payload(request)
        try:
            reply = await self._bridge.request(
                kind=SUBMIT_REVIEW_KIND,
                request_id=request.request_id,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
            )
        except InteractionBusyError:
            return SubmitReviewDecision(user_decision=None, review_outcome="busy")
        except TimeoutError:
            await self._emit_timeout(request.request_id)
            return SubmitReviewDecision(user_decision=None, review_outcome="timeout")
        except asyncio.CancelledError:
            # stop 的 cancel 哨兵：不向上抛（串行阶段无 _execute_one cancel 捕获，
            # 抛出会成 generator 异常、得不到干净 cancelled 终态，spec §5.5）。
            return SubmitReviewDecision(user_decision=None, review_outcome="cancelled")
        return _reply_to_decision(reply)

    async def _emit_timeout(self, request_id: str) -> None:
        # 见前置确认项 2：依赖 Phase 1 的 bridge.emit 薄方法；若 Phase 1 未加，改用 self._bridge._event_sink(...)
        await self._bridge.emit(
            InteractionTimeoutEvent(source="System", kind=SUBMIT_REVIEW_KIND, request_id=request_id)
        )
```

- [ ] **Step 4: AgentRunPorts 加 submit_approval_gate 字段**

`matmaster/types/runtime_ports.py` 的 `AgentRunPorts`（163-182），在 `workspace_jobs` 字段之后追加（保持 `@dataclass(frozen=True)`、默认 None，类比 `bohrium_job_ledger`）：

```python
    submit_approval_gate: "SubmitApprovalGate | None" = None
```

文件顶部 import 加：`from matmaster.types.submit_review import SubmitApprovalGate`。

- [ ] **Step 5: 运行测试 + import 冒烟**

Run: `uv run --extra dev pytest tests/matmaster/integration/test_submit_approval_gate.py -v`
Expected: 4 个用例 PASS
Run: `uv run python -c "from matmaster.types.runtime_ports import AgentRunPorts; print(AgentRunPorts.__dataclass_fields__['submit_approval_gate'])"`
Expected: 无 ImportError，打印字段

- [ ] **Step 6: Commit**

```bash
git add matmaster/integration/submit_approval_gate.py matmaster/types/runtime_ports.py tests/matmaster/integration/test_submit_approval_gate.py
git commit -m "feat(gate): BridgeSubmitApprovalGate adapter + AgentRunPorts.submit_approval_gate"
```

---

## Task 4: Runner 侧纯函数（reply 校验 / 护栏 / diff / 审计 / attach / enforce）

**Files:**
- Create: `matmaster/core/submit_review_support.py`
- Test: `tests/matmaster/core/test_submit_review_support.py`（扩展 Task 1 建的文件）

**依赖 Task 1。** 集中放 runner 用到的所有无状态计算（spec §5.7 护栏签名 / §9.3 审计；文件校验 §5.4/§7.5 已按用户决策放弃，不再有 `validate_reported_file_changes`）。runner（Task 5）只编排、不内联这些计算。

- [ ] **Step 1: 写纯函数直测（reply 校验 / 护栏签名 / diff / attach / enforce）**

`tests/matmaster/core/test_submit_review_support.py` 追加（签名 / diff / 截断 / attach / enforce 纯函数直测；文件变更不再有校验可测）：

```python
import json
from matmaster.tools.tool_result import ToolResult
from matmaster.core.submit_review_support import (
    submit_signature, compute_parameter_changes,
    build_review_content, build_audit_payload, attach_submit_review_record,
    enforce_submit_review_contract,
)


def test_submit_signature_stable_and_keyed_on_core_fields():
    a = {"input_dir": "/share/c", "job_name": "j", "image": "i", "cmd": "run", "machine": "m1"}
    b = {"input_dir": "/share/c", "job_name": "j", "image": "i", "cmd": "run", "machine": "m2"}
    assert submit_signature(a) == submit_signature(b)        # machine 不在签名键内
    c = {**a, "cmd": "run2"}
    assert submit_signature(a) != submit_signature(c)


# （删）原 worker 侧文件校验直测 test_validate_reply_path_boundary_and_metadata：
# 文件变更已完全放行、无校验逻辑可测。

def test_parameter_changes_and_review_content_truncation():
    changes = compute_parameter_changes(
        {"action": "submit", "image": "old", "cmd": "c"}, {"action": "submit", "image": "new", "cmd": "c"})
    assert changes == {"image": {"from": "old", "to": "new"}}
    review = build_review_content(changes, [{"relative_path": f"f{i}", "lines": "1"} for i in range(25)])
    assert len(review["input_file_changes"]) == 20 and review["input_file_changes_truncated"] is True
    assert build_review_content({}, []) == {}   # 两者皆空 → 整块省略


def test_attach_injects_review_and_payload_and_meta():
    tr = ToolResult(status="blocked", content=json.dumps({"success": False, "status": "UserRejected"}))
    review = {"parameter_changes": {"cmd": {"from": "a", "to": "b"}}}
    audit = {"schema_version": 1, "request_id": "sr_1"}
    out = attach_submit_review_record(tr, review, audit, block_reason="UserRejected")
    body = json.loads(out.content)
    assert body["review"]["parameter_changes"]["cmd"]["to"] == "b"
    assert out.payload["bohrium_submit_review"]["request_id"] == "sr_1"
    assert out.meta["block_reason"] == "UserRejected" and out.meta["layer"] == "submit_approval_gate"


def test_enforce_restores_destroyed_record():
    """spec §12.10：POST hook 删 review/payload 后 enforce 恢复。"""
    tr = ToolResult(status="success", content=json.dumps({"success": True, "job_id": "1"}))   # review 被破坏性 rewrite 删掉
    review = {"parameter_changes": {"cmd": {"from": "a", "to": "b"}}}
    audit = {"schema_version": 1, "request_id": "sr_1"}
    out = enforce_submit_review_contract(tr, review, audit)
    assert json.loads(out.content)["review"]["parameter_changes"]["cmd"]["to"] == "b"
    assert out.payload["bohrium_submit_review"]["request_id"] == "sr_1"


def test_no_file_content_leakage():
    """§12.12：放行透传后 review 仍只含前端上报的 relative_path/lines，绝无文件正文。"""
    review = build_review_content({}, [{"relative_path": "f", "lines": "1"}])
    assert "SECRET" not in json.dumps(review)
    assert set(review["input_file_changes"][0]) == {"relative_path", "lines"}
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run --extra dev pytest tests/matmaster/core/test_submit_review_support.py -v`
Expected: 新增用例 FAIL with ImportError（`submit_review_support` 未建）；Task 1 的两个用例仍 PASS

- [ ] **Step 3: 新建 submit_review_support.py**

```python
from __future__ import annotations

import hashlib
import json
from typing import Any

from matmaster.tools.tool_result import ToolResult

SUBMIT_APPROVAL_GATE_KEY = "submit_approval_gate"
RUN_IDENTITY_KEY = "run_identity"
RESUBMIT_SIGNATURES_KEY = "bohrium_submit_resubmit_signatures"
SUBMIT_REVIEW_RECORDS_KEY = "submit_review_records"

MAX_CONTENT_FILE_CHANGES = 20
MAX_PAYLOAD_FILE_CHANGES = 200
_SIGNATURE_FIELDS = ("input_dir", "job_name", "image", "cmd")


def submit_signature(args: dict[str, Any]) -> str:
    """关键字段规范化哈希（spec §5.7.1）：input_dir/job_name/image/cmd。"""
    key = {f: str(args.get(f) or "").strip() for f in _SIGNATURE_FIELDS}
    return hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# 文件变更完全放行（spec §5.4/§7.5 偏离，用户 2026-06-19 决策）：
# 后端不校验路径/存在性/metadata/lines，runner 直接把 decision.reported_input_file_changes 透传进 review/payload。
# 参数与文件正确性由前端校验、让用户重试；零泄漏仍成立（前端本就只上报 relative_path/lines）。


def compute_parameter_changes(draft_args: dict[str, Any], final_args: dict[str, Any]) -> dict[str, Any]:
    """user_parameter_changes：review_draft_arguments 与 final_arguments 的字段级 diff（spec §7.4）。"""
    changes: dict[str, Any] = {}
    for k in set(draft_args) | set(final_args):
        a, b = draft_args.get(k), final_args.get(k)
        if a != b:
            changes[k] = {"from": a, "to": b}
    return changes


def build_review_content(
    parameter_changes: dict[str, Any], input_file_changes: list[dict[str, Any]]
) -> dict[str, Any]:
    """content.review（spec §9.1）：只暴露两类增量，各自有才出现，皆空则空 dict。"""
    review: dict[str, Any] = {}
    if parameter_changes:
        review["parameter_changes"] = parameter_changes
    if input_file_changes:
        review["input_file_changes"] = [
            {"relative_path": c.get("relative_path"), "lines": c.get("lines")}
            for c in input_file_changes[:MAX_CONTENT_FILE_CHANGES]
        ]
        if len(input_file_changes) > MAX_CONTENT_FILE_CHANGES:
            review["input_file_changes_truncated"] = True
    return review


def build_audit_payload(
    *,
    request_id: str,
    session_id: str,
    task_id: str,
    tool_call_id: str,
    review_outcome: str,
    user_decision: str | None,
    model_arguments: dict[str, Any],
    review_draft_arguments: dict[str, Any],
    final_arguments: dict[str, Any],
    execution_arguments: dict[str, Any] | None,
    normalization_changes: dict[str, Any],
    user_parameter_changes: dict[str, Any],
    execution_normalization_changes: dict[str, Any],
    reported_input_file_changes: list[dict[str, Any]],
    reported_input_file_change_count: int,
    execution_audit: dict[str, Any] | None,
) -> dict[str, Any]:
    """payload.bohrium_submit_review（spec §9.3）。execution_audit 来自 _submit meta（approved），
    blocked/cancelled 传 None → execution_attempted=False。"""
    audit = {
        "schema_version": 1,
        "request_id": request_id, "session_id": session_id, "task_id": task_id, "tool_call_id": tool_call_id,
        "review_outcome": review_outcome, "user_decision": user_decision,
        "model_arguments": model_arguments,
        "review_draft_arguments": review_draft_arguments,
        "final_arguments": final_arguments,
        "execution_arguments": execution_arguments or {},
        "normalization_changes": normalization_changes,
        "user_parameter_changes": user_parameter_changes,
        "execution_normalization_changes": execution_normalization_changes,
        "changed_fields": list(user_parameter_changes.keys()),
        "reported_input_file_change_count": reported_input_file_change_count,
        "reported_input_file_changes_truncated": reported_input_file_change_count > MAX_PAYLOAD_FILE_CHANGES,
        "reported_input_file_changes": reported_input_file_changes[:MAX_PAYLOAD_FILE_CHANGES],
        "input_file_changes_source": "frontend_reported",
    }
    audit.update(execution_audit or {"execution_attempted": False, "external_effect_started": False})
    return audit


def _apply_record(
    result: ToolResult, review_content: dict[str, Any], audit_payload: dict[str, Any],
    *, block_reason: str | None,
) -> ToolResult:
    try:
        body = json.loads(result.content) if result.content else {}
        if not isinstance(body, dict):
            body = {"message": result.content}
    except (ValueError, TypeError):
        body = {"message": result.content}
    if review_content:
        body["review"] = review_content
    else:
        body.pop("review", None)
    new_meta = dict(result.meta)
    new_meta.pop("submit_execution_audit", None)   # 内部信号已并入 public payload，不外露
    if block_reason:
        new_meta["block_reason"] = block_reason
        new_meta["layer"] = "submit_approval_gate"
    return result.model_copy(update={
        "content": json.dumps(body, ensure_ascii=False),
        "payload": {**result.payload, "bohrium_submit_review": audit_payload},
        "meta": new_meta,
    })


def attach_submit_review_record(
    result: ToolResult, review_content: dict[str, Any], audit_payload: dict[str, Any],
    *, block_reason: str | None = None,
) -> ToolResult:
    """首次注入 review 合同（串行 blocked/cancelled 内联 + 并发 POST hook 靠前）。"""
    return _apply_record(result, review_content, audit_payload, block_reason=block_reason)


def enforce_submit_review_contract(
    result: ToolResult, review_content: dict[str, Any], audit_payload: dict[str, Any],
) -> ToolResult:
    """POST hook 链末尾补回（幂等），防通用 rewrite 破坏 review 合同（spec §6 / §12.10）。"""
    return _apply_record(result, review_content, audit_payload, block_reason=None)
```

> 设计说明：`attach` 与 `enforce` 共享 `_apply_record`（幂等），区别仅在调用时机与 `block_reason`。当前生产无任何通用 POST_TOOL_CALL rewrite handler（已核实），enforce 在生产中是为未来通用 rewrite 预留的防御；§12.10 通过在测试里注入破坏性 rewrite 验证其有效。

- [ ] **Step 4: 运行测试验证**

Run: `uv run --extra dev pytest tests/matmaster/core/test_submit_review_support.py -v`
Expected: 全 PASS（含 Task 1 的 2 个 + 本 Task 的 6 个）

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/submit_review_support.py tests/matmaster/core/test_submit_review_support.py
git commit -m "feat(core): submit review runner-side pure helpers (reply validation, guard, diff, audit, attach/enforce)"
```

---

## Task 5: FullToolRunner 串行闸门

**Files:**
- Modify: `matmaster/core/tool_runner.py`（`execute_batch` 串行阶段：PRE hook 214-216 / cancel check 230 之后、structural validation 248 之前插闸门段；顶部 import）
- Test: `tests/matmaster/core/test_full_tool_runner.py`（扩展：闸门启用 / 串行语义 / outcome 处理 / cancelled / 护栏）

**依赖 Task 1 / 3 / 4。** runner 是指挥（spec §4 / §5.6 / §8）：检测 `instance.submit_review_provider` + `runner_state` 的 gate，调 `build_review_draft`，非 None 调 `gate.review`，按 outcome 收尾。闸门是 `execute_batch` 串行阶段一段连贯流程（反碎片化：纯计算已在 Task 4 抽出，runner 只编排）。

- [ ] **Step 1: 写闸门行为测试（§12.3 / §12.4 / §12.6 / §12.7 / §12.14）**

`tests/matmaster/core/test_full_tool_runner.py` 追加（用 fake gate + 编译带 provider 的 BohriumTool；沿用该文件既有 runner / catalog / topology fixture）：

```python
import asyncio
import json
import pytest
from matmaster.core.submit_review_support import (
    SUBMIT_APPROVAL_GATE_KEY, RUN_IDENTITY_KEY, RESUBMIT_SIGNATURES_KEY,
)
from matmaster.types.submit_review import SubmitReviewDecision
from matmaster.types.run_metadata import RunIdentity


class _Gate:
    def __init__(self, decision): self.decision, self.calls = decision, 0
    async def review(self, request):
        self.calls += 1
        return self.decision


def _submit_call():
    # 沿用该文件构造 ToolCallData 的既有 helper；arguments 为 Bohrium submit
    ...


@pytest.mark.asyncio
async def test_gate_absent_passes_through(runner_without_gate):
    """spec §12.3：gate 端口不存在时 Bohrium submit 正常放行（走原执行路径）。"""
    # runner_state 无 submit_approval_gate → 直接进 approved → _submit 执行（mock）
    ...


@pytest.mark.asyncio
async def test_rejected_blocks_without_external_effect_and_arms_guard(runner_with_gate):
    """spec §12.6：reject → blocked、不调 create/upload/add；记两条签名；同作业再提被护栏 blocked；不同作业放行。"""
    gate = _Gate(SubmitReviewDecision(user_decision="reject", review_outcome="rejected",
                                      final_arguments={"action": "submit", "input_dir": "/share/c", "image": "i2", "cmd": "c > log 2>&1"}))
    runner = runner_with_gate(gate)
    results = await runner.execute_batch([_submit_call()], ctx())
    tc, tr = results[0]
    assert tr.status == "blocked" and "review" in json.loads(tr.content)
    assert gate.calls == 1
    sigs = runner.state.get(RESUBMIT_SIGNATURES_KEY)
    assert len(sigs) == 2   # model_arguments 签名 + final_arguments 签名
    # 同作业再提 → 命中护栏，不再调 gate
    results2 = await runner.execute_batch([_submit_call()], ctx())
    assert results2[0][1].status == "blocked" and gate.calls == 1


@pytest.mark.asyncio
async def test_timeout_and_busy_block_and_arm_guard(runner_with_gate):
    """spec §12.7：timeout / busy 均 blocked、不提交、进护栏。"""
    for outcome in ("timeout", "busy"):
        gate = _Gate(SubmitReviewDecision(user_decision=None, review_outcome=outcome))
        runner = runner_with_gate(gate)
        tc, tr = (await runner.execute_batch([_submit_call()], ctx()))[0]
        assert tr.status == "blocked"


@pytest.mark.asyncio
async def test_cancelled_yields_cancelled_result_not_raise(runner_with_gate):
    """spec §12.14：gate outcome=cancelled → ToolResult(status='cancelled')，不冒泡 CancelledError、不进护栏。"""
    gate = _Gate(SubmitReviewDecision(user_decision=None, review_outcome="cancelled"))
    runner = runner_with_gate(gate)
    tc, tr = (await runner.execute_batch([_submit_call()], ctx()))[0]
    assert tr.status == "cancelled"
    assert not runner.state.get(RESUBMIT_SIGNATURES_KEY)   # cancel 不进护栏


@pytest.mark.asyncio
async def test_oversized_submit_arg_errors_not_submit(runner_with_gate, captured_submit):
    """P0：超长 submit 参数 → build_review_draft raise → error ToolResult，绝不发 review、绝不 fall-through 到 submit。"""
    gate = _Gate(SubmitReviewDecision(user_decision=None, review_outcome="approved"))   # 不应被调用
    runner = runner_with_gate(gate)
    tc, tr = (await runner.execute_batch([_submit_call(cmd="x" * 9000)], ctx()))[0]   # _submit_call 接受 cmd override
    assert tr.status == "error"
    assert gate.calls == 0 and captured_submit.last is None   # 没发 review、没 submit


@pytest.mark.asyncio
async def test_approved_runs_with_user_edited_execution_args(runner_with_gate, captured_submit):
    """spec §12.4：人审期间无工具执行；用户改后的 input_dir/cmd/machine 经 structural + _submit 生效。"""
    gate = _Gate(SubmitReviewDecision(user_decision="submit", review_outcome="approved",
                                      final_arguments={"action": "submit", "input_dir": "/share/c", "image": "new", "cmd": "run --x > log 2>&1", "machine": "c64_m256_cpu"}))
    runner = runner_with_gate(gate)
    await runner.execute_batch([_submit_call()], ctx())
    assert captured_submit.last["machine"] == "c64_m256_cpu"   # _submit 收到 execution_args
    assert captured_submit.last["cmd"] == "run --x > log 2>&1"
```

> fixture 提示：`runner_with_gate(gate)` 构造 `FullToolRunner`，其 `state` 预置 `submit_approval_gate=gate` 与 `run_identity=RunIdentity(task_id="t", session_id="s")`；catalog 注册编译后带 `submit_review_provider` 的 BohriumTool；`captured_submit` mock `submit_job_via_runtime` 捕获入参（未提交时 `.last is None`）。`_submit_call(cmd=...)` 接受可选 cmd override（默认合法短 cmd，超长用例传 `"x"*9000`）。沿用该测试文件既有 fixture 风格，不另起一套。

- [ ] **Step 2: 运行确认失败**

Run: `uv run --extra dev pytest tests/matmaster/core/test_full_tool_runner.py -k submit -v`
Expected: 新增用例 FAIL（闸门未实现，submit 直接进 approved）

- [ ] **Step 3: 在 execute_batch 串行阶段插入闸门段**

`matmaster/core/tool_runner.py` 顶部 import 加：

```python
import json
from uuid import uuid4
from matmaster.core.submit_review_support import (
    SUBMIT_APPROVAL_GATE_KEY, RUN_IDENTITY_KEY, RESUBMIT_SIGNATURES_KEY, SUBMIT_REVIEW_RECORDS_KEY,
    submit_signature, compute_parameter_changes,
    build_review_content, build_audit_payload, attach_submit_review_record,
)
from matmaster.types.submit_review import SubmitReviewArgumentError, SubmitReviewRequest
```

在串行循环里、**cancel check（230）之后、structural validation（248）之前**插入闸门段。`base_args` 已在 PRE hook 段定义（205 `base_args = copy.deepcopy(tc.arguments)`）。闸门段：

```python
            # ── Submit review gate (serial) ── spec §5.6 / §8
            gate = self._state.get(SUBMIT_APPROVAL_GATE_KEY)
            if gate is not None and instance.submit_review_provider is not None:
                try:
                    draft = instance.submit_review_provider.build_review_draft(base_args)
                except SubmitReviewArgumentError as exc:
                    # 超长/非法 submit 参数：硬失败 error，绝不 fall-through 到真实 submit（P0）
                    tr = ToolResult(status="error", content=f"Submit arguments rejected: {exc}")
                    results[idx] = (tc, tr)
                    if on_result:
                        await on_result(tc, tr)
                    continue
                if draft is not None:
                    run_identity = self._state.get(RUN_IDENTITY_KEY)
                    session_id = getattr(run_identity, "session_id", "")
                    task_id = getattr(run_identity, "task_id", "")
                    guard = self._state.get(RESUBMIT_SIGNATURES_KEY)
                    if guard is None:
                        guard = set()
                        self._state.set(RESUBMIT_SIGNATURES_KEY, guard)

                    # 重提交护栏：命中任一签名即 blocked，不再发起 review（spec §5.7）
                    if submit_signature(draft.model_arguments) in guard:
                        tr = ToolResult(
                            status="blocked",
                            content=json.dumps({"success": False, "status": "ResubmitBlocked",
                                                "message": "本作业已被拒绝/未获确认，请勿重复提交；可总结进展或转做其它工作。"}, ensure_ascii=False),
                            meta={"block_reason": "ResubmitBlocked", "layer": "submit_approval_gate"},
                        )
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    request_id = "sr_" + uuid4().hex[:12]
                    decision = await gate.review(SubmitReviewRequest(
                        request_id=request_id, tool_name=tc.name, tool_call_id=tc.id,
                        task_id=task_id, session_id=session_id, draft=draft,
                    ))
                    outcome = decision.review_outcome

                    # cancel：产出 cancelled ToolResult，不抛 CancelledError、不进护栏（spec §5.5）
                    if outcome == "cancelled":
                        tr = ToolResult(status="cancelled", content="Run cancelled.")
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    final_args = decision.final_arguments or draft.review_draft_arguments
                    user_changes = compute_parameter_changes(draft.review_draft_arguments, final_args)

                    def _blocked(status_text: str, message: str, file_changes, exec_args, exec_norm, file_count):
                        review = build_review_content(user_changes, file_changes)
                        audit = build_audit_payload(
                            request_id=request_id, session_id=session_id, task_id=task_id, tool_call_id=tc.id,
                            review_outcome=outcome, user_decision=decision.user_decision,
                            model_arguments=draft.model_arguments, review_draft_arguments=draft.review_draft_arguments,
                            final_arguments=final_args, execution_arguments=exec_args,
                            normalization_changes=draft.normalization_changes, user_parameter_changes=user_changes,
                            execution_normalization_changes=exec_norm, reported_input_file_changes=file_changes,
                            reported_input_file_change_count=file_count, execution_audit=None,
                        )
                        tr0 = ToolResult(
                            status="blocked",
                            content=json.dumps({"success": False, "status": status_text, "message": message}, ensure_ascii=False),
                            meta={"block_reason": status_text, "layer": "submit_approval_gate"},
                        )
                        return attach_submit_review_record(tr0, review, audit, block_reason=status_text)

                    if outcome in ("rejected", "timeout", "busy"):
                        guard.add(submit_signature(draft.model_arguments))
                        guard.add(submit_signature(final_args))
                        reported = decision.reported_input_file_changes or []   # 完全放行：直接透传，不校验
                        tr = _blocked(_OUTCOME_STATUS[outcome], _OUTCOME_MESSAGE[outcome], reported, None, {}, len(reported))
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    # approved：文件变更完全放行透传（不校验）→ normalize → 复用 structural/input/policy
                    reported = decision.reported_input_file_changes or []
                    exec = instance.submit_review_provider.normalize_execution_args(final_args)
                    execution_args = exec.arguments
                    review_content = build_review_content(user_changes, reported)
                    audit_baseline = build_audit_payload(
                        request_id=request_id, session_id=session_id, task_id=task_id, tool_call_id=tc.id,
                        review_outcome="approved", user_decision="submit",
                        model_arguments=draft.model_arguments, review_draft_arguments=draft.review_draft_arguments,
                        final_arguments=final_args, execution_arguments=execution_args,
                        normalization_changes=draft.normalization_changes, user_parameter_changes=user_changes,
                        execution_normalization_changes=exec.normalization_changes,
                        reported_input_file_changes=reported, reported_input_file_change_count=len(reported),
                        execution_audit=None,
                    )
                    records = self._state.get(SUBMIT_REVIEW_RECORDS_KEY)
                    if records is None:
                        records = {}
                        self._state.set(SUBMIT_REVIEW_RECORDS_KEY, records)
                    records[tc.id] = {"review_content": review_content, "audit_baseline": audit_baseline}
                    base_args = execution_args   # 让后续 structural/input/policy 校验用户改后的 execution_args（spec §5.6）
            # ── end submit review gate ──
```

模块级在闸门段之前加文案常量：

```python
_OUTCOME_STATUS = {"rejected": "UserRejected", "timeout": "ReviewTimeout", "busy": "ReviewBusy"}
_OUTCOME_MESSAGE = {
    "rejected": "用户拒绝了本次 Bohrium 提交。请不要重新提交本作业，可总结当前进展、转去做其它工作，或结束本轮等待用户继续反馈。",
    "timeout": "本次提交未在限定时间内获得用户确认，未提交。请不要重新提交本作业，可总结进展或转做其它工作。",
    "busy": "当前已有待处理的人机交互，本次提交未发起确认，未提交。请稍后由用户处理后再继续，不要重复提交。",
}
```

> approved 后 `base_args = execution_args` 自然 fall through 到现有 structural（248）/ input_validator（271）/ policy。这些层若对 execution_args `deny`，产出的是通用 `error` ToolResult——其 review record 由 `SUBMIT_REVIEW_RECORDS_KEY[tc.id]` 的 baseline 经 enforce POST hook（Task 6）补上（approved 进 `_execute_one` 经 POST hook）。文件变更已完全放行透传、不再校验；超长/非法 submit 参数由 `build_review_draft` raise `SubmitReviewArgumentError`、闸门入口 catch 成 `error`（P0），绝不进 submit。

- [ ] **Step 4: 运行测试验证**

Run: `uv run --extra dev pytest tests/matmaster/core/test_full_tool_runner.py -k submit -v`
Expected: 新增闸门用例 PASS；该文件既有用例不回归

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/tool_runner.py tests/matmaster/core/test_full_tool_runner.py
git commit -m "feat(runner): submit review serial gate (draft -> review -> outcome -> guard/validate)"
```

---

## Task 6: 服务层 opt-in 注入 + exp.py 装配（gate + run_identity + POST hook）

**Files:**
- Modify: `src/services/agent_run_service.py`（`run_agent` 251-270 加参数；`InteractionBridge` 构造 509-516 旁建 gate；AgentRunPorts 构造 578-589 填字段）
- Modify: `matmaster/core/exp.py`（`build_runtime` 231-445：从 ports 读 gate + run_identity 注入 runner_state；注册 attach/enforce POST hook）
- Test: `tests/matmaster/services/test_agent_run_stream_interaction.py`（扩展：注入冒烟 / §12.10 enforce / §12.13 契约寄生 / §12.11 partial）

**依赖 Task 3 / 4 / 5。** 把 gate 装到运行链：服务层（opt-in 且顶层 run）构造 gate 填 ports；exp.py 比照 `figure_upload_config`（385-388）把 gate + run_identity 经 `runner_state.set` 送进串行阶段，并注册 attach/enforce 两个 POST_TOOL_CALL rewrite handler（闭包 runner_state）。

- [ ] **Step 1: agent_run_service —— run_agent 加 opt-in 边界 + 构造 gate 填 ports**

`src/services/agent_run_service.py`：

(a) `run_agent`（251-270）签名追加参数（输入边界，见前置确认项 3）：

```python
        submit_confirmation_enabled: bool = False,
```

(b) Phase 1 已构造 `InteractionBridge`（509-516，命名 `bridge`）。在其后构造 gate（仅 opt-in；`run_agent` 本身即顶层 run 入口、无 `spawn_id` 参数，spawn 由 `SubagentOrchestrator` 内部 child_run_factory 走另一路径不经此，故此处只判 opt-in；`spawn_id is None` 的双重保险在 Step 2 的 exp.py 装配处兜）：

```python
        from matmaster.integration.submit_approval_gate import BridgeSubmitApprovalGate

        submit_approval_gate = (
            BridgeSubmitApprovalGate(bridge) if submit_confirmation_enabled else None
        )
```

> 共享 bridge：gate 与 AskQuestionTool 复用同一个 `bridge`（同一 `asyncio.Lock` + 同一 session 级 `human_interaction_active` SETNX 守卫），ask 与 submit 天然互斥（spec §4 / §5.5）。

(c) `AgentRunPorts(...)` 构造（578-589）追加字段：

```python
            submit_approval_gate=submit_approval_gate,
```

- [ ] **Step 2: exp.py —— 注入 runner_state + 注册 attach/enforce POST hook**

`matmaster/core/exp.py` 的 `build_runtime`（231-445）。比照 `figure_upload_config` 注入（385-388）的位置，在 `runner_state = ToolRunnerState()` 之后、`FullToolRunner(...)` 构造（391）之前插入（此处 `runner_state`、`hook_executor`（301 已建）、`run_identity`、`spawn_id` 均在作用域内）：

```python
        submit_approval_gate = request.ports.submit_approval_gate
        if submit_approval_gate is not None and spawn_id is None:
            from matmaster.core.hooks import HookEvent
            from matmaster.core.submit_review_support import (
                SUBMIT_APPROVAL_GATE_KEY, RUN_IDENTITY_KEY, SUBMIT_REVIEW_RECORDS_KEY,
                attach_submit_review_record, enforce_submit_review_contract,
            )

            runner_state.set(SUBMIT_APPROVAL_GATE_KEY, submit_approval_gate)
            # run_identity 在 411 才随 AgentKernelSpec 现算，此处不在作用域；就地现算（纯函数、值相等、幂等）
            runner_state.set(RUN_IDENTITY_KEY, self._build_run_identity(ctx, spawn_id=spawn_id))

            def _merge_execution_audit(audit_baseline, result):
                # approved 路径：把 _submit 写在 meta 的执行审计并进 public payload（spec §9.3）
                exec_audit = (result.meta or {}).get("submit_execution_audit")
                if not exec_audit:
                    return audit_baseline
                return {**audit_baseline, **exec_audit}

            def _record_for(ctx_tool_call_id):
                records = runner_state.get(SUBMIT_REVIEW_RECORDS_KEY) or {}
                return records.get(ctx_tool_call_id)

            async def _attach_post(ctx, result):
                rec = _record_for(ctx.tool_call_id)
                if rec is None:
                    return None   # 非 submit review 工具，原样
                audit = _merge_execution_audit(rec["audit_baseline"], result)
                return attach_submit_review_record(result, rec["review_content"], audit)

            async def _enforce_post(ctx, result):
                rec = _record_for(ctx.tool_call_id)
                if rec is None:
                    return None
                audit = _merge_execution_audit(rec["audit_baseline"], result)
                return enforce_submit_review_contract(result, rec["review_content"], audit)

            hook_executor.rewrite(HookEvent.POST_TOOL_CALL, _attach_post)    # 靠前：先放上供通用 observe
            hook_executor.rewrite(HookEvent.POST_TOOL_CALL, _enforce_post)   # 靠后：通用 rewrite 后补回
```

> 注册顺序：`emit_rewrite` 按注册顺序串行执行，`_attach_post` 先、`_enforce_post` 后；当前生产无其它 POST rewrite，两者之间无破坏者，enforce 为防御性补回。`run_identity` 由 `_build_run_identity`（exp.py 197-206）产出，`spawn_id is None` 即顶层 run（与 interaction_bridge 置空 741-743 同一判据）。

- [ ] **Step 3: 写服务层接入测试（§12.10 / §12.11 / §12.13 + 注入冒烟）**

`tests/matmaster/services/test_agent_run_stream_interaction.py` 追加（沿用该文件 fanout / fake redis / bridge 集成设施）：

```python
@pytest.mark.asyncio
async def test_enforce_restores_review_after_destructive_post_hook():
    """spec §12.10：通用破坏性 POST rewrite 删 review 后，enforce 恢复必要字段。"""
    # 装配 hook_executor：注册 _attach_post、再注册一个删 content.review 的破坏 rewrite、再注册 _enforce_post
    # 预置 runner_state SUBMIT_REVIEW_RECORDS_KEY[tc.id] = {review_content, audit_baseline}
    # emit_rewrite(POST_TOOL_CALL) 后断言最终 result.content 含 review、payload 含 bohrium_submit_review


@pytest.mark.asyncio
async def test_partial_side_effect_audit_recorded():
    """spec §12.11：job/create 成功但上传失败 → payload 记 external_effect_started=true + job ref。"""
    # _submit 内 mock submit_job_via_runtime 抛 BohriumTransferError(created_job_ref="12345")
    # approved 路径经 _attach_post 合并 meta.submit_execution_audit
    # 断言 payload.bohrium_submit_review.external_effect_started is True / job_create_attempted is True
    # / job_add_attempted is False / job_id == "12345"


@pytest.mark.asyncio
async def test_submit_review_uses_generic_interaction_envelope():
    """spec §12.13：submit_review 走通用 interaction_request/reply/timeout（kind 区分），无自定义事件类型。"""
    # gate.review 发起 → 断言 event_sink 收到 InteractionRequestEvent(kind="submit_review")，无自定义 type


def test_gate_constructed_only_when_enabled_and_top_level():
    """注入冒烟：submit_confirmation_enabled=False → ports.submit_approval_gate is None；True → 非 None。"""
    ...
```

- [ ] **Step 4: 运行测试验证 + import 冒烟**

Run: `uv run --extra dev pytest tests/matmaster/services/test_agent_run_stream_interaction.py -v`
Expected: 新增用例 PASS；既有交互用例不回归
Run: `uv run python -c "import matmaster.core.exp; import src.services.agent_run_service; import matmaster.core.tool_runner"`
Expected: 无 ImportError

- [ ] **Step 5: Commit**

```bash
git add src/services/agent_run_service.py matmaster/core/exp.py tests/matmaster/services/test_agent_run_stream_interaction.py
git commit -m "feat(service): opt-in submit approval gate injection + exp post-hook attach/enforce wiring"
```

---

## Task 7: 全量 focused 回归 + 净代码自查

**Files:** 无新增改动，纯验证。

- [ ] **Step 1: 跑 spec §12 全量 focused 集**

```bash
uv run --extra dev pytest \
  tests/matmaster/core/test_full_tool_runner.py \
  tests/matmaster/core/test_submit_review_support.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/tools/builtin/test_bohrium_submit_review.py \
  tests/matmaster/integration/test_submit_approval_gate.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py -v
```
Expected: 全 PASS。

- [ ] **Step 2: opt-out 不退化回归（§12.15，关键）**

确认 gate 不存在时主路径行为与开启确认前一致：

```bash
uv run --extra dev pytest tests/matmaster/tools/builtin/test_bohrium_tool.py -k "optout or submit" -v
```
Expected: opt-out submit 仍得默认 `machine/job_name/disk_size` 与 `> log 2>&1`；既有 BohriumTool 用例全绿。

- [ ] **Step 3: import 冒烟 + lint**

```bash
uv run python -c "import matmaster.types.submit_review; import matmaster.tools.builtin.bohrium_tool.submit_review; import matmaster.integration.submit_approval_gate; import matmaster.core.submit_review_support; import matmaster.core.tool_runner; import matmaster.core.exp; import src.services.agent_run_service"
uv run ruff check matmaster/ src/
```
Expected: 无 ImportError、无 lint 错误。

- [ ] **Step 4: 文件正文零泄漏抽查（§12.12）**

```bash
grep -rn "read_text\|read_bytes\|open(" matmaster/core/submit_review_support.py matmaster/integration/submit_approval_gate.py
```
Expected: 无命中（后端只 `stat` / `is_file`，绝不读 `input_dir` 内文件正文）。

- [ ] **Step 5: 净代码量自查**

```bash
git diff --stat <实施起点 commit>..HEAD -- matmaster/ src/
```
Expected: 新增为主（本 plan 是新功能），但 `_submit` / `submit_job_via_runtime` 的散落默认/cmd 逻辑被规范化关口替代（净增不含重复逻辑）。若出现应删未删的旧默认/cmd 分支，回看 Task 2 Step 5/6。

---

## 测试覆盖对照（spec §12 的 15 项 → Task）

| # | spec §12 项 | 落点 |
|---|---|---|
| 1 | draft validation（缺=issue / 过长=raise→error / 重定向） | Task 2 Step 1 + Task 5 Step 1（超长 error） |
| 2 | 参数分层（model 不改 / draft 含默认 / _submit 收 execution） | Task 2 Step 1/8 |
| 3 | 闸门启用（gate 不存在放行 / 子 agent 放行） | Task 5 Step 1 + Task 6 Step 2（spawn 不注入） |
| 4 | gate 串行语义（等待期无执行 / 改后参数生效） | Task 5 Step 1（`test_approved_runs_with_user_edited_execution_args`） |
| 5 | gate 异常映射 | Task 3 Step 1 |
| 6 | reject 收尾 + 两条护栏 + 不同作业放行 | Task 5 Step 1（`test_rejected_...`） |
| 7 | timeout / busy 均 blocked 进护栏 | Task 5 Step 1 |
| 8 | 文件变更放行透传 + 大小截断 | Task 4 Step 1（截断/透传）+ Task 5（approved/blocked 透传） |
| 9 | cmd hidden normalization（defensive） | Task 2 Step 8 |
| 10 | POST hook 破坏性 rewrite → enforce 恢复 | Task 4 Step 1 + Task 6 Step 3 |
| 11 | partial side effect 审计 | Task 6 Step 3 |
| 12 | no file content leakage | Task 4 Step 1 + Task 7 Step 4 |
| 13 | 契约寄生通用 interaction 信封 | Task 6 Step 3 |
| 14 | stop during submit_review → cancelled（必测） | Task 5 Step 1（`test_cancelled_...`；传输层唤醒由 Phase 1 §10.4 覆盖） |
| 15 | opt-out 不退化（回归） | Task 2 Step 8 + Task 7 Step 2 |

---

## Self-Review（对照 spec 的覆盖核查）

- **§3.1 目标**：submit 副作用前暂停（Task 5 闸门）、确认续提/拒绝不副作用（Task 5 outcome）、参数改动进 `content.review.parameter_changes`、文件改动进 `input_file_changes`、审计进 `payload`（Task 4 build_review_content/build_audit_payload）、复用底座（Task 3 gate 包 bridge）、不经 run_meta 传能力（Task 6 经 ports + runner_state）——覆盖。
- **§5.1 gate adapter**：`SubmitApprovalGate` 协议（Task 1）+ `BridgeSubmitApprovalGate`（Task 3）+ `sr_` 前缀 + 明确字段 dataclass 无 extra 兜底——覆盖。
- **§5.2 opt-in**：gate 存在即启用、不存在即放行（Task 5 闸门判定）；`AgentRunPorts.submit_approval_gate` 规范归属（Task 3）；经 `runner_state.set` 送串行阶段比照 figure_upload（Task 6）；spawn/评测/cron/devshell 自然放行（gate 为 None）；配置来源解耦为输入边界（前置确认项 3 + Task 6 Step 1）——覆盖（两级配置存储按用户决策另立 plan）。
- **§5.3 契约寄生**：`kind="submit_review"` 寄生通用 `interaction_*`，零新增事件/端点（Task 3 + §12.13）——覆盖。
- **§5.4 偏离**：本 plan **放弃 worker 侧 reply 文件校验**，`reported_input_file_changes` 完全放行透传（用户决策：前端负责校验）；`validate_reported_file_changes` 不实现（见文末"偏离 spec 说明"）。
- **§5.5 异常映射 + outcome 塌缩 + cancel 闭合**：gate 映射 busy/timeout/cancelled（Task 3）；cancel → `ToolResult(status="cancelled")` 不抛（Task 5）。**偏离 spec：删 `invalid_final_arguments`**——文件校验放行后无来源；超长参数改由 `build_review_draft` raise → 闸门 `error`（非 outcome）。
- **§5.6 串行 await**：闸门在 catalog/PRE 之后、structural 之前（Task 5 Step 3 插入位置）；approved 经 `base_args=execution_args` fall through 现有校验——覆盖。
- **§5.7 blocked + 两条护栏**：reject/timeout/busy/invalid 记 model + final 两条签名（Task 5）；命中即 blocked（Task 5 护栏检查）——覆盖。
- **§5.9 幂等规范化关口**：共享 canonicalize（Task 2）；opt-out `_submit` 入口 + opt-in runner 双调幂等（Task 2 + Task 5）；`submit_job_via_runtime` 改 defensive——覆盖。
- **§6 平移硬核**：两级 validation（draft 宽松 / normalize 严格）、四层参数 + 三类 diff（Task 4 compute_parameter_changes / build_audit_payload 的 normalization_changes vs user_parameter_changes vs execution_normalization_changes）、ToolResult 合同（Task 4 attach）、POST hook finalizer（Task 6）、ToolCallEvent.arguments 不回写（runner 不改 tc.arguments，闸门只改 base_args 局部）——覆盖。
- **§7 数据模型**：provider helper（Task 2）、gate 接口数据类（Task 1）、inner payload + envelope（Task 3 _draft_to_payload + Phase 1 信封）、reply payload（Task 3 _reply_to_decision）、reported_input_file_changes 规则（Task 4 validate）——覆盖。
- **§8 runner 流程**：串行闸门判定 → build_draft → 护栏 → gate.review → outcome 分支（Task 5）；并发 _submit → attach → POST hook → enforce（Task 6）；hook 顺序不改 PRE 语义、gate 在 PRE 后 structural 前——覆盖。
- **§9 ToolResult 合同**：成功 review（§9.1）/拒绝 blocked（§9.2）/payload 审计（§9.3 含 partial）——Task 4 + Task 6——覆盖。
- **§5.8 统一 task_id**：审计 payload 与护栏 run 维度用 task_id（Task 4 build_audit_payload `task_id`，无 run_id）——覆盖。
- **类型一致性核查**：`SubmitReviewDraft`/`SubmitExecutionArgs`/`SubmitReviewRequest`/`SubmitReviewDecision` 字段在 Task 1 定义，Task 2/3/5 使用一致；`review_outcome` 值集 `approved|rejected|timeout|cancelled|busy` 在 Task 3 产出、Task 5 消费一致（`invalid_final_arguments` 已删，见文末"偏离 spec 说明"）；runner_state key 常量（`SUBMIT_APPROVAL_GATE_KEY`/`RUN_IDENTITY_KEY`/`RESUBMIT_SIGNATURES_KEY`/`SUBMIT_REVIEW_RECORDS_KEY`）在 Task 4 定义、Task 5/6 引用同名；`build_audit_payload` 签名在 Task 4 定义、Task 5（baseline）一致调用；`attach_submit_review_record`/`enforce_submit_review_contract` 在 Task 4 定义、Task 5（内联 blocked）/ Task 6（POST hook）一致使用。
- **缺口（先列不自动补）**：(a) `submit_confirmation_enabled` 两级配置存储与解析（user_preference 加列 + sessions 配置承载 + DAO + 两级解析）按用户决策另立 plan；(b) bridge emit timeout 的 `emit()` 薄方法**已确认 Phase 1 落地**（`interaction_bridge.py:40-41`），无依赖风险；(c) §9.3 partial side effect 审计精度依赖 `BohriumTransferError.created_job_ref`（Task 2 Step 6 已补），更细的 add_job 阶段失败标记为尽力而为。

---

## 偏离 spec 说明（用户 2026-06-19 决策，spec 暂未同步）

本 plan 在三处刻意偏离 `2026-06-19-bohrium-submit-review-on-interaction-base-design.md`，实现以 plan 为准（spec 不改）：

1. **文件变更完全放行（偏离 §5.4 / §7.5）**：`reported_input_file_changes` 后端不验证路径边界 / 存在性 / metadata / `lines` 格式，原样透传进 `content.review` 与审计 payload。参数与文件正确性由前端校验、让用户重试；仅保留 content ≤20 / payload ≤200 的大小截断（非校验）。`validate_reported_file_changes` 不实现。
2. **超长参数 fail-loud（偏离 §6.1「过长被拒不进 review / 退回原执行路径」）**：`build_review_draft` 对 `action=="submit"` 的超长字段直接 `raise SubmitReviewArgumentError`（不再返回 `None`），runner 闸门 catch 成 `ToolResult(status="error")`；`_submit` 入口对透传的超长执行参数再兜一道 `error`（`oversized_submit_fields` 共享）。修复原设计 `return None → fall-through → 真实 submit` 的 P0。`None` 自此只表示「非 submit」。
3. **删去 `invalid_final_arguments`（偏离 §5.5 outcome 枚举）**：上面两条之后该 outcome 无来源，从枚举与所有分支移除。`review_outcome` = `approved | rejected | timeout | cancelled | busy`。

---

## 执行交付

Plan 已保存到 `docs/superpowers/plans/2026-06-19-bohrium-submit-review-on-interaction-base.md`（不进 git commit）。

**重要前置**：本 plan 是 Phase 2，**必须等 Phase 1（2026-06-18 交互底座迁移）落地后再执行**——它引用 `InteractionBridge` / `InteractionBusyError` / `InteractionTimeoutEvent` / 通用 reply 端点等 Phase 1 产物（见"前置依赖与确认项"）。

两种执行方式：

1. **Subagent-Driven（推荐）** —— 每个 Task 派新 subagent 实现、Task 间两段式 review、快速迭代。
2. **Inline Execution** —— 本会话内按 executing-plans 批量执行、带 checkpoint。

请选择执行方式（或在 Phase 1 落地后再回来执行）。
