# Bohrium submit「确认并不再询问」前后端对齐 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐后端，使前端「确认并不再询问」端到端生效——本次 run 内后续 Bohrium 提交不再弹确认（进程内 skip flag），用户下次发消息的后续 run 也不再弹（持久化 session DB + hydrate 回读），且与已落地的批次截断、resubmit 护栏正确共存。

**Architecture:** 两级时间尺度，缺一不可。进程内：runner 在 `gate.review` 前插入 run-level skip flag 短路弹窗，但保留整个 gate 安全段（批次截断、resubmit 护栏仍优先）。持久化：通用 reply 端点在 `answer` 成功后 best-effort 写 session DB（`required=false`），GET 接口补 `source` 字段供前端 hydrate。两条独立生效、语义一致。

**Tech Stack:** Python ≥3.10、dataclass、FastAPI、Pydantic、pytest（`uv run pytest`）。改动跨两个包：`matmaster`（运行时）与 `src`（平台服务层）。

**来源 spec:** `docs/superpowers/specs/2026-06-21-bohrium-submit-disable-future-confirmation-alignment-design.md`（决策点 1-4 已拍板/按 review 修订为方向 B2「保留 gate + run-level skip flag」）。

---

## 执行前提

- **当前分支即可执行。** 本 plan 叠加在已合并的批次截断（git `f2e4b773` / `cf6eeb8d`）之上，无需新建 worktree。
- **docs 禁令（工作区 CLAUDE.md）：** 绝不向 `docs/` 目录做任何 git 提交。Task 5 只写盘、不 `git add docs/`；其余 Task 的 commit 命令只 add 代码与测试文件。
- **测试命令统一用 `uv run pytest`。** 仓库内 uv 环境。
- **范围边界（spec §10，本 plan 不碰）：** 两级配置（user 全局）effective 解析、DB schema、`stream_service` effective 计算链；`source` 的 `user` 三态仅占位、实际产出留给两级配置 plan。不改 gate 的 bridge 传输、不改 worker 的 enabled 计算、不改 `superseding_edit` 的置位/截断逻辑本身。

## File Structure（改动总览）

| 文件 | 责任 | 改动 |
|---|---|---|
| `matmaster/types/submit_review.py` | submit review 数据类 | `SubmitReviewDecision` 加 `disable_future_confirmation` 字段 |
| `matmaster/integration/submit_approval_gate.py` | reply→decision 解析 | `_reply_to_decision` 解析 `disable_future_confirmation`（仅 submit 取真） |
| `matmaster/core/submit_review_support.py` | runner-state key 常量 | 新增 `SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY` |
| `matmaster/core/tool_runner.py` | 串行 review 闸门 | import + `gate.review` 前 skip 短路 + approved 路径置 flag |
| `src/models/chat.py` | API data 模型 | `BohriumSubmitConfirmationData` 加 `source` 字段 |
| `src/apis/chat_api.py` | reply 端点 + GET builder | builder 产 `source` + reply 端点写 session DB 副作用 |
| `tests/matmaster/integration/test_submit_approval_gate.py` | gate 解析测试 | 新增 `_reply_to_decision` 解析矩阵 |
| `tests/matmaster/core/test_full_tool_runner_submit_review.py` | runner 行为测试 | 新增 short-circuit / 跨 turn / 截断优先 / resubmit 优先 |
| `tests/test_bohrium_submit_confirmation_api.py` | GET builder 测试 | 新增 `source` 断言 |
| `tests/matmaster/apis/test_interaction_reply_api.py` | reply 端点测试 | 新增 submit_review 副作用矩阵 |
| `docs/.../batch-truncation-...-design.md` | 跨文档一致性 | §4.2 实现注记改为方向 B2（只写盘） |

依赖顺序：**Task 1 → Task 2**（Task 2 用到 Task 1 加的字段）；Task 3、Task 4 是 `src` 层，独立；Task 5 是文档收尾。

---

## Task 1: Gate 层解析 `disable_future_confirmation`

补 `SubmitReviewDecision` 字段 + `_reply_to_decision` 解析。仅 `decision == "submit"` 时取真（双保险：即便前端违约在 reject 时带 true 也不生效）。

**Files:**
- Modify: `matmaster/types/submit_review.py:46-52`
- Modify: `matmaster/integration/submit_approval_gate.py:42-47`
- Test: `tests/matmaster/integration/test_submit_approval_gate.py`

> **测试落点说明:** spec §11 字面写「加在 `TestSubmitReviewGate`」，但那里用的是 fake gate（`_MultiGate`），根本不会触发 `_reply_to_decision`。本 plan 把解析矩阵放到真正所属的 `test_submit_approval_gate.py`，复用其 `_FakeBridge`——更贴代码组织，可观察约束不变。

- [ ] **Step 1: 写 `_reply_to_decision` 解析的失败测试**

在 `tests/matmaster/integration/test_submit_approval_gate.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_disable_future_confirmation_parsing():
    # submit + true -> True
    gate = BridgeSubmitApprovalGate(
        _FakeBridge(
            reply={
                "decision": "submit",
                "submit_arguments": {"action": "submit", "cmd": "run > log 2>&1"},
                "disable_future_confirmation": True,
            }
        )
    )
    decision = await gate.review(_req())
    assert decision.disable_future_confirmation is True

    # submit + false -> False
    gate_false = BridgeSubmitApprovalGate(
        _FakeBridge(
            reply={
                "decision": "submit",
                "submit_arguments": {"action": "submit"},
                "disable_future_confirmation": False,
            }
        )
    )
    assert (await gate_false.review(_req())).disable_future_confirmation is False

    # submit + 缺省 -> False
    gate_missing = BridgeSubmitApprovalGate(
        _FakeBridge(reply={"decision": "submit", "submit_arguments": {"action": "submit"}})
    )
    assert (await gate_missing.review(_req())).disable_future_confirmation is False

    # reject + true -> False（双保险）
    gate_reject = BridgeSubmitApprovalGate(
        _FakeBridge(
            reply={"decision": "reject", "submit_arguments": {}, "disable_future_confirmation": True}
        )
    )
    assert (await gate_reject.review(_req())).disable_future_confirmation is False

    # submit + 非布尔真值 -> 归一为 True
    gate_truthy = BridgeSubmitApprovalGate(
        _FakeBridge(
            reply={
                "decision": "submit",
                "submit_arguments": {"action": "submit"},
                "disable_future_confirmation": "yes",
            }
        )
    )
    assert (await gate_truthy.review(_req())).disable_future_confirmation is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/integration/test_submit_approval_gate.py::test_disable_future_confirmation_parsing -v`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'disable_future_confirmation'` 或 `AttributeError`（字段尚不存在）。

- [ ] **Step 3: `SubmitReviewDecision` 加字段**

`matmaster/types/submit_review.py`，把现有 `SubmitReviewDecision`（第 45-52 行）改为：

```python
@dataclass
class SubmitReviewDecision:
    """gate 回给 runner 的决定。"""

    user_decision: str | None
    review_outcome: str
    final_arguments: dict[str, Any] | None = None
    reported_input_file_changes: list[dict[str, Any]] | None = None
    disable_future_confirmation: bool = False
```

新字段带默认值放末尾——现有只传部分字段的构造点（busy/timeout/cancelled 等）不受影响。

- [ ] **Step 4: `_reply_to_decision` 解析**

`matmaster/integration/submit_approval_gate.py`，把现有 `return SubmitReviewDecision(...)`（第 42-47 行）改为：

```python
    return SubmitReviewDecision(
        user_decision=decision if decision in ("submit", "reject") else None,
        review_outcome=outcome,
        final_arguments=reply.get("submit_arguments"),
        reported_input_file_changes=reply.get("reported_input_file_changes"),
        disable_future_confirmation=(
            decision == "submit" and bool(reply.get("disable_future_confirmation"))
        ),
    )
```

- [ ] **Step 5: 跑测试确认通过（含 gate 既有回归）**

Run: `uv run pytest tests/matmaster/integration/test_submit_approval_gate.py -v`
Expected: PASS —— 新增测试通过，原有 `test_approved_and_rejected` / `test_busy_timeout_cancel_mapping` 等不回归。

- [ ] **Step 6: Commit**

```bash
git add matmaster/types/submit_review.py matmaster/integration/submit_approval_gate.py tests/matmaster/integration/test_submit_approval_gate.py
git commit -m "feat(submit-review): parse disable_future_confirmation in gate decision"
```

---

## Task 2: Runner 进程内 short-circuit（run-level skip flag）

在 `gate.review` 前插入 run-level skip flag 短路弹窗。**关键：保留整个 gate 安全段**——后续 submit 仍先过 `build_review_draft`、`superseding_edit` 截断分支、`RESUBMIT_SIGNATURES` 护栏，只在真正要 `gate.review()` 弹窗时短路放行（方向 B2）。

**Files:**
- Modify: `matmaster/core/submit_review_support.py:9-12`
- Modify: `matmaster/core/tool_runner.py`（import 区 31-47；`gate.review` 处 411-421；置 flag 处 531）
- Test: `tests/matmaster/core/test_full_tool_runner_submit_review.py`

> **测试分两组（诚实标注 TDD 性质）:** short-circuit、跨 turn 是「先红后绿」驱动测试（Step 3-6）；截断优先、resubmit 优先是「约束回归」测试（Step 7-9）——它们在「完全未实现 skip」时本就通过，作用是排除「把 skip 错误放在截断/guard 之前」的错误实现。两者共同锁定方向 B2 的放置位置。

- [ ] **Step 1: 新增 runner-state key 常量**

`matmaster/core/submit_review_support.py`，在现有常量块（第 9-12 行）后追加一行：

```python
SUBMIT_APPROVAL_GATE_KEY = "submit_approval_gate"
RUN_IDENTITY_KEY = "run_identity"
RESUBMIT_SIGNATURES_KEY = "bohrium_submit_resubmit_signatures"
SUBMIT_REVIEW_RECORDS_KEY = "submit_review_records"
SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY = "bohrium_submit_skip_confirmation"
```

- [ ] **Step 2: tool_runner.py 补 import**

`matmaster/core/tool_runner.py`，在 `from matmaster.core.submit_review_support import (...)`（第 31-41 行）中加入新 key：

```python
from matmaster.core.submit_review_support import (
    RESUBMIT_SIGNATURES_KEY,
    RUN_IDENTITY_KEY,
    SUBMIT_APPROVAL_GATE_KEY,
    SUBMIT_REVIEW_RECORDS_KEY,
    SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY,
    attach_submit_review_record,
    build_audit_payload,
    build_review_content,
    compute_parameter_changes,
    submit_signature,
)
```

并在 `from matmaster.types.submit_review import ...`（第 47 行）补 `SubmitReviewDecision`：

```python
from matmaster.types.submit_review import (
    SubmitReviewArgumentError,
    SubmitReviewDecision,
    SubmitReviewRequest,
)
```

- [ ] **Step 3: 写 short-circuit 驱动测试（纯关闭确认，无编辑）**

先在 `tests/matmaster/core/test_full_tool_runner_submit_review.py` 的 import 区（第 9-13 行 `from matmaster.core.submit_review_support import (...)`）补 `SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY`：

```python
from matmaster.core.submit_review_support import (
    RESUBMIT_SIGNATURES_KEY,
    RUN_IDENTITY_KEY,
    SUBMIT_APPROVAL_GATE_KEY,
    SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY,
)
```

再在 `TestSubmitReviewGate` 类内追加：

```python
    @pytest.mark.asyncio
    async def test_disable_future_confirmation_skips_following_submits(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                    disable_future_confirmation=True,
                ),
            }
        )
        runner, capture, state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
            ],
            _make_ctx(),
        )

        # 只有 s1 弹 review；s2/s3 被 skip flag 短路，不经 gate.review
        assert gate.reviewed == ["s1"]
        assert state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY) is True
        # 但 s2/s3 仍各自执行（短路只跳过弹窗，不跳过执行链）
        assert len(capture.calls) == 3
        for idx in range(3):
            assert results[idx][1].status == "success"
```

`_MultiGate` 只配 s1；若 s2/s3 误调 `gate.review`，会 `KeyError` —— 这正是短路未生效的信号。

- [ ] **Step 4: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_disable_future_confirmation_skips_following_submits -v`
Expected: FAIL —— 未实现短路时 s2 会调 `gate.review` 触发 `KeyError: 's2'`（`gate.reviewed` ≠ `["s1"]`）。

- [ ] **Step 5: 实现 `gate.review` 前短路**

`matmaster/core/tool_runner.py`，把 `request_id = "sr_" + uuid4().hex[:12]` 之后的 `decision = await gate.review(...)`（第 411-421 行）替换为 if/else：

```python
                    request_id = "sr_" + uuid4().hex[:12]
                    if self._state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY):
                        # run-level 关闭确认已生效：不发 interaction_request、不等前端
                        # reply，构造一个语义等价于「用户确认且无编辑」的本地 decision，
                        # 继续走 normalize/structural/policy/执行链。
                        decision = SubmitReviewDecision(
                            user_decision="submit",
                            review_outcome="approved",
                            final_arguments=draft.review_draft_arguments,
                        )
                    else:
                        decision = await gate.review(
                            SubmitReviewRequest(
                                request_id=request_id,
                                tool_name=tc.name,
                                tool_call_id=tc.id,
                                task_id=task_id,
                                session_id=session_id,
                                draft=draft,
                            )
                        )
                    outcome = decision.review_outcome
```

短路分支 `final_arguments=draft.review_draft_arguments`（无用户编辑），`reported_input_file_changes` 默认 None → 下游 `reported=[]`，因此不会触发 `superseding_edit`；若 normalize 仍判参数非法，走既有 `InvalidFinalArguments` 分支。

- [ ] **Step 6: 实现 approved 正常路径置 skip flag**

`matmaster/core/tool_runner.py`，在 `base_args = execution.arguments`（第 531 行）之后、`canonical_changes = ...` 之前插入（与 `superseding_edit` 置位对称，落在 normalize 成功后）：

```python
                    base_args = execution.arguments
                    if decision.disable_future_confirmation:
                        self._state.set(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY, True)
                    canonical_changes = compute_parameter_changes(
                        draft.review_draft_arguments, execution.arguments
                    )
```

短路分支构造的本地 decision `disable_future_confirmation` 为默认 False，不会重复置位；只有真实 `gate.review` 返回 True 的那个 submit 触发置位。

- [ ] **Step 7: 跑 short-circuit + 跨 turn，并补跨 turn 驱动测试**

先在 `TestSubmitReviewGate` 内追加跨 turn 测试：

```python
    @pytest.mark.asyncio
    async def test_skip_flag_persists_across_batches_in_run(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                    disable_future_confirmation=True,
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        await runner.execute_batch(
            [_submit_call(call_id="s1", cmd="alpha")], _make_ctx()
        )
        assert gate.reviewed == ["s1"]

        # 复用同一 runner/state 模拟下一 turn：新 batch 的 submit 不再弹 review
        results2 = await runner.execute_batch(
            [_submit_call(call_id="s2", cmd="beta")], _make_ctx()
        )

        assert gate.reviewed == ["s1"]  # s2 没有新增 review
        assert results2[0][1].status == "success"
        assert len(capture.calls) == 2
```

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_disable_future_confirmation_skips_following_submits tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_skip_flag_persists_across_batches_in_run -v`
Expected: PASS —— 两个驱动测试均通过。

- [ ] **Step 8: 补「截断优先」约束测试**

在 `TestSubmitReviewGate` 内追加（编辑 + 关闭确认 → 后续 submit 仍被截断，不被 skip flag 误放行）：

```python
    @pytest.mark.asyncio
    async def test_edit_truncation_takes_priority_over_skip(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                ),
                "s2": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments={
                        "action": "submit",
                        "input_dir": "/share/c",
                        "image": "img",
                        "cmd": "edited --x > log 2>&1",
                        "machine": "c32_m128_cpu",
                        "job_name": "matmaster-job",
                        "disk_size": 50,
                    },
                    disable_future_confirmation=True,
                ),
            }
        )
        runner, capture, state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
                _submit_call(call_id="s4", cmd="delta"),
            ],
            _make_ctx(),
        )

        # s2 既编辑又关闭确认：置 skip flag 的同时置 superseding_edit
        assert gate.reviewed == ["s1", "s2"]
        assert state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY) is True
        # s3/s4 命中截断分支（编辑优先），不被 skip 放行
        for idx in (2, 3):
            tr = results[idx][1]
            assert tr.status == "blocked"
            assert json.loads(tr.content)["status"] == "SupersededByPriorEdit"
            assert tr.meta["superseded_by"] == "s2"
```

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_edit_truncation_takes_priority_over_skip -v`
Expected: PASS —— skip flag 已置但 s3/s4 仍 `SupersededByPriorEdit`（验证 skip 位于截断分支之后）。

- [ ] **Step 9: 补「resubmit guard 优先」约束测试**

在 `TestSubmitReviewGate` 内追加（先拒绝写 guard → 后关闭确认 → 同签名 submit 仍 `ResubmitBlocked`）：

```python
    @pytest.mark.asyncio
    async def test_resubmit_guard_takes_priority_over_skip(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="reject",
                    review_outcome="rejected",
                    final_arguments=None,
                ),
                "s2": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                    disable_future_confirmation=True,
                ),
            }
        )
        runner, capture, state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="alpha"),  # 与 s1 同签名
            ],
            _make_ctx(),
        )

        # s1 reject 写 guard、s2 approved+disable 置 skip flag
        assert gate.reviewed == ["s1", "s2"]
        assert state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY) is True
        # s3 与 s1 同签名：命中 guard，不经 gate.review，不执行
        assert results[2][1].status == "blocked"
        assert json.loads(results[2][1].content)["status"] == "ResubmitBlocked"
        # 只有 s2 真正执行（s1 reject、s3 ResubmitBlocked 都未触达 executor）
        assert len(capture.calls) == 1
        assert capture.calls[0]["cmd"].startswith("beta")
```

> s3 `cmd="alpha"` 与 s1 同 `input_dir/image/cmd/job_name` → `submit_signature(draft.model_arguments)` 命中 s1 reject 时写入的 `model_sig`，在 `gate.review` 前的 guard 检查处直接 `ResubmitBlocked`。

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_resubmit_guard_takes_priority_over_skip -v`
Expected: PASS —— s3 被 `ResubmitBlocked`（验证 skip 位于 resubmit guard 之后）。

- [ ] **Step 10: 跑批次截断既有 7 个测试 + 全文件回归**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py -v`
Expected: PASS —— 既有 `test_edit_truncates_following_submits` / `test_no_edit_all_submits_confirmed` / `test_truncated_submit_not_armed_into_guard` / `test_truncation_only_blocks_submits_not_other_tools` / `test_normalization_only_does_not_truncate` / `test_reported_file_change_truncates_following` / `test_optout_batch_unaffected_when_gate_absent` 全部不回归，新增 4 个测试通过。

- [ ] **Step 11: Commit**

```bash
git add matmaster/core/submit_review_support.py matmaster/core/tool_runner.py tests/matmaster/core/test_full_tool_runner_submit_review.py
git commit -m "feat(runner): skip submit review after disable_future_confirmation while keeping gate safety"
```

---

## Task 3: GET 接口补 `source` 字段

`BohriumSubmitConfirmationData` 加 `source`，本期只产 `session`（有 override）/ `default`（无 override）；`user` 三态占位留给两级配置 plan。GET 与 PUT 共用同一 builder，自动一致带上 source。

**Files:**
- Modify: `src/models/chat.py:352-359`
- Modify: `src/apis/chat_api.py:135-143`
- Test: `tests/test_bohrium_submit_confirmation_api.py`

- [ ] **Step 1: 写 builder 产 `source` 的失败测试**

把 `tests/test_bohrium_submit_confirmation_api.py` 两个现有测试补上 source 断言，并新增 default 用例：

```python
from src.apis import chat_api


def test_session_bohrium_submit_confirmation_data_reads_session_value():
    data = chat_api._session_bohrium_submit_confirmation_data_from_row(
        "s1",
        {"bohrium_submit_confirmation_required": 0},
    )

    assert data.session_id == "s1"
    assert data.required is False
    assert data.source == "session"


def test_session_bohrium_submit_confirmation_data_keeps_unset():
    data = chat_api._session_bohrium_submit_confirmation_data_from_row(
        "s1",
        {"bohrium_submit_confirmation_required": None},
    )

    assert data.session_id == "s1"
    assert data.required is None
    assert data.source == "default"


def test_session_bohrium_submit_confirmation_data_true_is_session():
    data = chat_api._session_bohrium_submit_confirmation_data_from_row(
        "s1",
        {"bohrium_submit_confirmation_required": 1},
    )

    assert data.required is True
    assert data.source == "session"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_bohrium_submit_confirmation_api.py -v`
Expected: FAIL —— `AttributeError: 'BohriumSubmitConfirmationData' object has no attribute 'source'`。

- [ ] **Step 3: 模型加 `source` 字段**

`src/models/chat.py`，把 `BohriumSubmitConfirmationData`（第 352-359 行）改为：

```python
class BohriumSubmitConfirmationData(BaseModel):
    """GET/PUT /chat/sessions/{session_id}/bohrium-submit-confirmation 的 data 字段"""

    session_id: str = Field(description="会话 ID")
    required: bool | None = Field(
        default=None,
        description="会话级 Bohrium 提交确认覆盖值；null 表示未设置/继承",
    )
    source: Literal["session", "user", "default"] = Field(
        default="default",
        description="覆盖来源：session=会话级覆盖；user=用户全局（占位，留给两级配置）；default=无覆盖",
    )
```

`Literal` 已在文件顶部 import（第 20 行 `from typing import Any, Literal`），无需新增 import。

- [ ] **Step 4: builder 产出 `source`**

`src/apis/chat_api.py`，把 `_session_bohrium_submit_confirmation_data_from_row`（第 135-143 行）改为：

```python
def _session_bohrium_submit_confirmation_data_from_row(
    session_id: str,
    row: dict,
) -> BohriumSubmitConfirmationData:
    raw = row.get("bohrium_submit_confirmation_required")
    return BohriumSubmitConfirmationData(
        session_id=session_id,
        required=None if raw is None else bool(raw),
        source="session" if raw is not None else "default",
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_bohrium_submit_confirmation_api.py -v`
Expected: PASS —— 三个测试全过。

- [ ] **Step 6: Commit**

```bash
git add src/models/chat.py src/apis/chat_api.py tests/test_bohrium_submit_confirmation_api.py
git commit -m "feat(chat-api): expose source in bohrium submit confirmation GET response"
```

---

## Task 4: reply 端点 submit_review 副作用（持久化）

在通用 reply 端点 `answer_pending_interaction` 成功后、`publish/history` 之前内联写 session DB（不抽函数，保持业务连贯）。三条件严格判定，写库失败 best-effort 降级（只 log、不 raise）。

**Files:**
- Modify: `src/apis/chat_api.py:573-574`（`answer` 检查后、`reply_event` 构造前）
- Test: `tests/matmaster/apis/test_interaction_reply_api.py`

- [ ] **Step 1: 扩展测试桩 `_ChatSvc` 记录 set 调用**

`tests/matmaster/apis/test_interaction_reply_api.py`，把现有 `_ChatSvc`（第 19-24 行）改为：

```python
class _ChatSvc:
    def __init__(self, allowed: bool = True, set_result: bool = True) -> None:
        self.allowed = allowed
        self.set_result = set_result
        self.set_calls: list[tuple[str, str | None, bool | None]] = []

    def can_access_session(self, session_id: str, user_id: str | None) -> bool:
        return self.allowed

    def set_bohrium_submit_confirmation(
        self, session_id: str, user_id: str | None, required: bool | None
    ) -> bool:
        self.set_calls.append((session_id, user_id, required))
        return self.set_result
```

加默认参数与新方法不影响现有用例（`_ChatSvc()` / `_ChatSvc(allowed=False)` 仍可用，且都不读 `set_calls`）。

- [ ] **Step 2: 写 submit_review 副作用矩阵的失败测试**

在 `tests/matmaster/apis/test_interaction_reply_api.py` 末尾追加：

```python
def _submit_reply(*, decision: str = "submit", disable=False) -> InteractionReplyRequest:
    payload: dict = {
        "decision": decision,
        "submit_arguments": {"action": "submit", "cmd": "run > log 2>&1"},
        "reported_input_file_changes": [],
    }
    if disable is not None:
        payload["disable_future_confirmation"] = disable
    return InteractionReplyRequest(kind="submit_review", payload=payload)


def _run_reply(dao, chat, req, *, request_id="sr_1"):
    with patch("src.apis.chat_api.get_redis_dao", return_value=dao):
        return asyncio.run(
            interaction_reply(
                session_id="sess-1",
                request_id=request_id,
                req=req,
                user_id="user-1",
                chat_svc=chat,
                stream_svc=_StreamSvc(),
                events_svc=_EventsSvc(),
            )
        )


def test_reply_submit_disable_persists_confirmation_off() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="submit", disable=True))

    assert chat.set_calls == [("sess-1", "user-1", False)]


def test_reply_submit_without_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="submit", disable=False))

    assert chat.set_calls == []


def test_reply_submit_missing_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="submit", disable=None))

    assert chat.set_calls == []


def test_reply_reject_with_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="reject", disable=True))

    assert chat.set_calls == []


def test_reply_ask_question_with_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["aq_1"] = _pending_record(kind="ask_question")
    chat = _ChatSvc()
    req = InteractionReplyRequest(
        kind="ask_question",
        payload={"decision": "submit", "disable_future_confirmation": True},
    )

    _run_reply(dao, chat, req, request_id="aq_1")

    assert chat.set_calls == []


def test_reply_submit_disable_set_failure_still_returns_ok() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc(set_result=False)

    result = _run_reply(dao, chat, _submit_reply(decision="submit", disable=True))

    assert result.msg == "ok"
    assert chat.set_calls == [("sess-1", "user-1", False)]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/apis/test_interaction_reply_api.py -k "disable or persist or ask_question_with_disable" -v`
Expected: FAIL —— 副作用未实现，`chat.set_calls` 始终为 `[]`（`test_reply_submit_disable_persists_confirmation_off` 断言不成立）。

- [ ] **Step 4: 实现 reply 端点副作用**

`src/apis/chat_api.py`，在 `interaction_reply` 内 `answer_pending_interaction` 结果检查之后（第 572 行 `raise ConflictErrorResponse(msg="交互已 answered/timeout/cancelled")` 这一句所在的 if 块之后）、`reply_event = {`（第 574 行）之前插入：

```python
    if result == "not_pending":
        raise ConflictErrorResponse(msg="交互已 answered/timeout/cancelled")

    if (
        req.kind == "submit_review"
        and req.payload.get("decision") == "submit"
        and req.payload.get("disable_future_confirmation") is True
    ):
        if not chat_svc.set_bohrium_submit_confirmation(sid, user_id, False):
            logger.warning(
                "disable future submit confirmation failed: session_id=%s", sid
            )

    reply_event = {
```

- 三条件：`kind` 限定 submit_review；`decision == "submit"` 双保险（reject 不触发）；`is True` 严格判定（缺省/false/非 True 真值都不触发）。
- 幂等：副作用在 `answer` 成功后执行，`answer_pending_interaction` 对同一 request_id 只成功一次（重复 reply 第二次 `not_pending` → 409，到不了副作用）。
- 降级：写库失败（含分享/匿名 `user_id` 为空 WHERE 不命中）只 log warning、**不 raise**——reply 照常 200、作业照常提交，偏好下次 hydrate 自纠。（对比 `stream_service.py:790` 发消息 set 失败会 raise：那里 set 是主职责，这里偏好保存是附带，不能阻断 agent 继续。）

- [ ] **Step 5: 跑测试确认通过（含 reply 既有回归）**

Run: `uv run pytest tests/matmaster/apis/test_interaction_reply_api.py -v`
Expected: PASS —— 6 个新增副作用测试通过；既有 `test_interaction_reply_publishes_structured_event_and_reply_envelope` / `test_reply_409_when_not_pending` / `test_reply_409_when_kind_mismatch` 等不回归。

- [ ] **Step 6: Commit**

```bash
git add src/apis/chat_api.py tests/matmaster/apis/test_interaction_reply_api.py
git commit -m "feat(chat-api): persist disable_future_confirmation on submit_review reply"
```

---

## Task 5: 跨文档一致性（批次截断 spec §4.2 注记）

spec §13 要求：把批次截断 spec 的 §4.2「关闭确认（叠加优先级）」实现注记从方向 A（「清空 run 内 gate」「gate 的清空在下一批才生效」）更新为方向 B2（保留 gate + skip flag），使两份文档对实现机制描述一致。**可观察语义「截断优先」不变。**

**Files:**
- Modify: `docs/superpowers/specs/2026-06-21-bohrium-submit-batch-truncation-on-edit-design.md`（§4.2 节）

> **docs 禁令:** 本 Task 只写盘，**不** `git add`、**不** commit 任何 `docs/` 文件。

- [ ] **Step 1: 更新 §4.2 实现注记**

`docs/superpowers/specs/2026-06-21-bohrium-submit-batch-truncation-on-edit-design.md`，把 `### 4.2 关闭确认（叠加优先级）` 整节替换为：

```markdown
### 4.2 关闭确认（叠加优先级）

存在一个并行设计（用户在确认时可选择"后续不再需要确认"）。**实现机制（由「确认并不再询问」对齐 plan 落地，方向 B2）：** 不清空 run 内 gate，而是在 runner-state 置一个 run-level skip flag（`SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY`）；后续 submit 仍进入整个 gate 安全段（`build_review_draft`、`superseding_edit` 截断分支、`RESUBMIT_SIGNATURES` 护栏），只在真正要 `gate.review()` 弹窗时由 skip flag 短路放行。

当同一次确认**既编辑了内容又关闭了确认**时：**截断优先**。后续 submit 先命中 `superseding_edit` 截断分支并 `continue`（交还 LLM），不会走到 skip-confirmation 放行；skip flag 在 LLM 重新规划后的下一批 submit 才表现为"不弹"。否则就退化成 §1 的最糟场景 2：旧内容免确认直接提交。

**护栏优先：** 若同一 run 内先前某 submit 已被 reject/timeout/busy 写入 `RESUBMIT_SIGNATURES`，之后另一 submit 关闭确认，同签名后续 submit 仍在 `gate.review` 前命中 `ResubmitBlocked`，不因 skip flag 被直接执行。

> 注：本设计与"关闭确认"是两个独立增强。若"关闭确认"尚未实现，本设计独立成立；两者同时存在时，截断与 resubmit guard 均优先于跳过弹窗（见对齐 plan）。
```

- [ ] **Step 2: 确认未将 docs 纳入暂存区**

Run: `git status --short docs/`
Expected: 该 spec 文件显示为 modified（` M ...batch-truncation...-design.md`），但**不在**暂存区（不出现 `M ` 于第一列）。Task 5 到此结束，不做任何 `git add docs/`。

---

## Self-Review（对照 spec 的覆盖核查）

**1. Spec 覆盖：**

| spec 条目 | 对应 Task | 状态 |
|---|---|---|
| §2 前端契约 reply 带 `disable_future_confirmation` | Task 1（解析）+ Task 4（写库） | ✅ |
| §6.1(a) `SubmitReviewDecision` 加字段 | Task 1 Step 3 | ✅ |
| §6.1(b) `_reply_to_decision` 解析（仅 submit 取真） | Task 1 Step 4 | ✅ |
| §6.1(c) 保留 gate + skip flag 短路（4 处改动） | Task 2 Step 1/2/5/6 | ✅ |
| §6.2(d) reply 端点写库副作用 + best-effort 降级 | Task 4 Step 4 | ✅ |
| §6.2(e) GET 补 source（session/default） | Task 3 Step 3/4 | ✅ |
| §6.3 截断优先 / resubmit 优先共存 | Task 2 Step 8/9 约束测试 | ✅ |
| §11 matmaster 单测（解析 + short-circuit + 截断优先 + resubmit 优先 + 跨 turn） | Task 1 Step 1、Task 2 Step 3/7/8/9 | ✅ |
| §11 src 测试（reply 副作用矩阵 + GET source） | Task 4 Step 2、Task 3 Step 1 | ✅ |
| §13 跨文档一致性更新 | Task 5 | ✅ |
| §10 out of scope（两级配置 / user 三态 / 不改 gate 入口 359） | 全程不碰；source 仅占位 user | ✅ |

**2. 占位符扫描：** 无 TBD/TODO/「类似 Task N」/「补充错误处理」等空洞表述；每个代码步骤都给出完整可粘贴代码与精确行号。

**3. 类型一致性：**
- `SubmitReviewDecision.disable_future_confirmation: bool = False`（Task 1）↔ Task 2 短路分支构造（不传该字段，默认 False）+ 置位判断 `decision.disable_future_confirmation` ↔ 测试构造 `disable_future_confirmation=True`：一致。
- `SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY = "bohrium_submit_skip_confirmation"`（Task 2 Step 1）↔ tool_runner 读写 ↔ 测试 import 同名常量并断言 `state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY)`：引用一致。
- `set_bohrium_submit_confirmation(session_id, user_id, required) -> bool`（service 实际签名）↔ Task 4 调用 `chat_svc.set_bohrium_submit_confirmation(sid, user_id, False)` ↔ 测试桩同签名：一致。
- `BohriumSubmitConfirmationData.source: Literal["session","user","default"]`（Task 3）↔ builder 产 `"session"/"default"` ↔ 测试断言 `data.source == "session"/"default"`：一致。

**4. 关键放置约束（方向 B2 的核心）：** Task 2 不改 gate 入口条件 `tool_runner.py:359`（仍为 `gate and provider`）；skip 短路落在 resubmit guard（400-409）与 `gate.review`（412）之间；置 flag 落在 normalize 成功后（531 之后）。截断优先 / resubmit 优先两个约束测试专门锁定此放置——若实现误置于安全段之前，测试即红。
