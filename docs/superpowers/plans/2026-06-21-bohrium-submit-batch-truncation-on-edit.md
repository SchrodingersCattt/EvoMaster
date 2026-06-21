# Bohrium submit 批次截断（用户在确认时修改内容）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当用户在确认某个 submit 时做了实质编辑（改参数或上报输入文件变更），把该编辑点之后的同批 submit 全部截断为 `blocked`，交还 LLM 重新规划，避免后续 submit 携带旧内容被重复确认或直接误提交。

**Architecture:** 纯 `FullToolRunner.execute_batch` Step 1 串行循环内的编排增强。引入一个 batch 级局部标志 `superseding_edit`：在 approved 正常路径检测到实质编辑即置位（天然不跨轮累积）；置位后对其后每个 submit（`build_review_draft` 返回非 None 者）直接产出截断 `_gate_block_result`，不发 review、不进护栏；非 submit 工具不受影响。零新增事件类型、零跨文件、不触碰 gate adapter / exp 装配 / 前端。

**Tech Stack:** Python 3.10+，FastAPI 运行时，pytest（`asyncio_mode = auto`），uv 环境。所有命令以 `uv run` 前缀执行。

---

## 背景与约束

- 本设计是 `docs/superpowers/specs/2026-06-21-bohrium-submit-batch-truncation-on-edit-design.md` 的实现，建立在已落地合并的后端 submit review 闸门之上（见 `docs/superpowers/plans/2026-06-19-bohrium-submit-review-on-interaction-base.md`）。
- 实现落点单一：`matmaster/core/tool_runner.py` 的 `execute_batch` Step 1 串行循环，预计净增约十几行。
- 测试可后端独立验证：用 fake gate（返回带 `final_arguments` / `reported_input_file_changes` 的 `SubmitReviewDecision`）模拟用户编辑，不依赖前端。
- 触发条件复用闸门已算出的量，不新造判定，但截断判定用**语义规范化之后**的 diff（见下方"对 spec §3.1 的修正"）：
  - audit 仍记录原始 `user_changes = compute_parameter_changes(draft.review_draft_arguments, final_args)`（`tool_runner.py:413`），保留前端原始输入的审计价值。
  - 截断判定改用 `canonical_changes = compute_parameter_changes(draft.review_draft_arguments, execution.arguments)`——两边都是 `_canonicalize_submit_args` 的输出，消除补 `> log 2>&1`、补默认 machine/disk_size、以及前端类型漂移（如 `disk_size` 回传成 `"50"`）等规范化噪音。
  - `reported = decision.reported_input_file_changes or []`（`tool_runner.py:417`）——用户上报的输入文件变更。
  - `canonical_changes` 非空 **或** `reported` 非空 = 实质编辑 → 触发截断。

  **对 spec §3.1 的修正：** spec §3.1 给的公式是 `compute_parameter_changes(draft.review_draft_arguments, final_args)`，但 `final_args` 是前端 reply payload 直接取出的（见 `submit_approval_gate.py:45`、`chat.py` 的 `InteractionReplyRequest.payload: dict[str, Any]`，无字段级 schema/类型归一化）。draft 里 `disk_size` 已被 `_canonicalize_submit_args` 规范化为 int，前端若把未改动的数值回传成字符串 `"50"`，原始公式会判为 `50 -> "50"` 的"编辑"并错误截断后续 submit，而执行参数最终又被 normalize 回 `50`——并非实质编辑。改用规范化后的 `execution.arguments` 比较，才真正实现 spec §3.1 声明的"系统规范化不算编辑"意图。原始 `final_args` 的 diff 仍保留在 audit record 中。
- 截断 `tool_result` 形状（§3.4）：`status="blocked"`，content `{"success": false, "status": "SupersededByPriorEdit", "message": <英文>}`，meta 在 `_gate_block_result` 默认的 `{"block_reason": "SupersededByPriorEdit", "layer": "submit_approval_gate"}` 之上追加 `superseded_by`（触发编辑的 tool_call_id）与 `changed_fields`（变更字段列表；文件变更以标记位 `"input_files"` 表示）。
- 本 plan **不实现**"关闭确认"（独立设计），但实现的截断逻辑与其叠加时遵守"截断优先"——因截断发生在 review 之前、清空 gate 之后才在下一批生效，无需本 plan 额外编码。
  - **【交接关闭确认 plan】** 本设计的"截断优先"（spec §4.2）依赖一个前提：gate 在同一个 `execute_batch` 内不被清空。截断分支位于 `if gate is not None` 之内（`tool_runner.py:354`），若关闭确认在 S2 的 `gate.review()` 返回时立即清掉 `SUBMIT_APPROVAL_GATE_KEY`，则下一轮处理 S3 时 `gate is None`，会跳过整个闸门段、绕过截断，S3 退回 opt-out 路径携带旧内容执行（即 spec §1 的最糟场景 2）。**关闭确认 plan 必须保证 gate 清空延迟到下一批 submit 才生效**；否则需回头把截断与 gate 存在性解耦——把闸门段入口条件改为 `provider is not None and (gate is not None or superseding_edit is not None)`，内层 review 用 `if gate is not None` 守卫，并补一个"S2 approved 后清 gate、S3 仍返回 `SupersededByPriorEdit`"的 fake gate 测试。（当前代码库无任何清 gate 逻辑，此前提暂自动成立。）
- 本 plan **不改**拒绝（reject）行为，§4.3 维持现状，现有 `test_rejected_blocks_without_external_effect_and_arms_guard` 不动。

执行建议：在当前 `feat/bohrium_job` 分支或其专用 worktree 内实施。

---

## File Structure

- **Modify:** `matmaster/core/tool_runner.py`
  - `execute_batch` Step 1 串行循环：循环前声明 batch 级 `superseding_edit` 标志；闸门段 `draft is not None` 入口处插入截断分支；approved 正常路径末尾插入置位逻辑。
  - 责任：编排"编辑即截断后续 submit"，复用既有 `_gate_block_result` / `compute_parameter_changes`，不新增模块函数。
- **Test:** `tests/matmaster/core/test_full_tool_runner_submit_review.py`
  - 新增 `_MultiGate`（按 `tool_call_id` 返回不同 decision、记录 review 顺序）。
  - 扩展现有 `_SubmitCapture`（新增 `self.calls` 全量记录，保留 `self.last` 不破坏现有断言）。
  - 新增 7 个测试，逐一对应 spec §8 测试点。

---

## Task 1：截断核心逻辑 + 测试基础设施 + 核心测试

实现一次性落地完整截断逻辑（三处编辑），并用 spec §8 测试点 1、2 驱动验证"编辑即截断"与"未编辑不截断"。

**Files:**
- Modify: `matmaster/core/tool_runner.py`（`execute_batch` Step 1 串行循环，对应 `tool_runner.py:282` 之前、`:369`、`:510` 区域）
- Test: `tests/matmaster/core/test_full_tool_runner_submit_review.py`

- [ ] **Step 1: 扩展 `_SubmitCapture` 记录全部执行调用**

在 `tests/matmaster/core/test_full_tool_runner_submit_review.py` 中，把现有 `_SubmitCapture`（约 lines 44-60）替换为下面版本（新增 `self.calls`，保留 `self.last`）：

```python
class _SubmitCapture:
    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, args: dict[str, Any], exec_ctx: Any) -> ToolResult:
        self.last = dict(args)
        self.calls.append(dict(args))
        return ToolResult(
            status="success",
            content=json.dumps({"success": True, "job_id": "job-123"}),
            meta={
                "submit_execution_audit": {
                    "execution_attempted": True,
                    "external_effect_started": True,
                    "job_id": "job-123",
                }
            },
        )
```

- [ ] **Step 2: 新增 `_MultiGate`（按 tool_call_id 返回不同 decision）**

紧接在 `_SubmitGate` 类定义之后（约 line 41 之后）插入：

```python
class _MultiGate:
    """按 tool_call_id 返回不同 decision；记录被 review 的 call_id 顺序。

    decisions 中未列出的 call_id 表示"不应被 review"——若实现错误地对其
    发起 review，会触发 KeyError 使测试失败（硬契约）。
    """

    def __init__(self, decisions: dict[str, SubmitReviewDecision]) -> None:
        self.decisions = decisions
        self.reviewed: list[str] = []

    async def review(self, request):
        self.reviewed.append(request.tool_call_id)
        return self.decisions[request.tool_call_id]
```

- [ ] **Step 3: 写失败测试——编辑 S2 截断 S3/S4（测试点 1）**

在 `TestSubmitReviewGate` 类末尾（约 line 305 之后）追加：

```python
    @pytest.mark.asyncio
    async def test_edit_truncates_following_submits(self) -> None:
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
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
                _submit_call(call_id="s4", cmd="delta"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1", "s2"]
        assert len(capture.calls) == 2
        assert {c["cmd"] for c in capture.calls} == {
            "alpha > log 2>&1",
            "edited --x > log 2>&1",
        }
        for idx in (2, 3):
            tr = results[idx][1]
            assert tr.status == "blocked"
            body = json.loads(tr.content)
            assert body["status"] == "SupersededByPriorEdit"
            assert tr.meta["block_reason"] == "SupersededByPriorEdit"
            assert tr.meta["layer"] == "submit_approval_gate"
            assert tr.meta["superseded_by"] == "s2"
            assert tr.meta["changed_fields"] == ["cmd"]
```

- [ ] **Step 4: 跑测试，确认失败**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_edit_truncates_following_submits -v`

Expected: FAIL — 截断未实现时 S3/S4 会被正常 review（`gate.reviewed` 含 `s3`，且 `_MultiGate.review` 对 `s3` 触发 `KeyError`，或 `capture.calls` 长度为 4），断言不通过。

- [ ] **Step 5: 实现截断逻辑（一）——循环前声明 batch 级标志**

在 `matmaster/core/tool_runner.py` 的 `execute_batch` 中，找到串行循环入口（`tool_runner.py:281-282`）：

```python
        # ── Serial validation ──────────────────────────────
        for idx, tc in enumerate(tool_calls):
```

替换为：

```python
        # Batch-local truncation flag: set when a user edit supersedes the
        # rest of this batch's submits. Local to this execute_batch call, so it
        # never accumulates across turns.
        superseding_edit: tuple[str, list[str]] | None = None

        # ── Serial validation ──────────────────────────────
        for idx, tc in enumerate(tool_calls):
```

- [ ] **Step 6: 实现截断逻辑（二）——闸门段入口处截断后续 submit**

在 `matmaster/core/tool_runner.py` 中找到闸门段入口（`tool_runner.py:369-370`）：

```python
                if draft is not None:
                    run_identity = self._state.get(RUN_IDENTITY_KEY)
```

替换为：

```python
                if draft is not None:
                    if superseding_edit is not None:
                        editor_id, changed_fields = superseding_edit
                        tr = _gate_block_result(
                            "SupersededByPriorEdit",
                            "The user modified the parameters or input files "
                            "of another submit in the same batch. This submit "
                            "was not executed; please refer to those changes "
                            "and re-evaluate before resubmitting.",
                        )
                        tr.meta["superseded_by"] = editor_id
                        tr.meta["changed_fields"] = changed_fields
                        results[idx] = (tc, tr)
                        if on_result:
                            await on_result(tc, tr)
                        continue

                    run_identity = self._state.get(RUN_IDENTITY_KEY)
```

理由：截断分支置于 `draft is not None`（确认是 submit）之后、护栏检查与 `gate.review` 之前——只截有提交副作用的 submit（§3.3），且不进护栏（§4.1）。

- [ ] **Step 7: 实现截断逻辑（三）——approved 正常路径末尾置位**

在 `matmaster/core/tool_runner.py` 中找到 approved 正常路径末尾（`tool_runner.py:509-510`）：

```python
                    records[tc.id] = record
                    base_args = execution.arguments
```

替换为：

```python
                    records[tc.id] = record
                    base_args = execution.arguments
                    canonical_changes = compute_parameter_changes(
                        draft.review_draft_arguments, execution.arguments
                    )
                    if canonical_changes or reported:
                        changed_fields = list(canonical_changes.keys())
                        if reported:
                            changed_fields.append("input_files")
                        superseding_edit = (tc.id, changed_fields)
```

理由：
- 置位发生在 `normalize_execution_args` 成功之后（`:489` 之后区域），故 normalize 失败走 `InvalidFinalArguments` 分支（`:452`）时到不了这里，自然不触发截断（§7）。被编辑的 submit 自身已 fall through 继续执行，不受标志影响——标志只在后续迭代生效（§3.2）。
- 截断判定用 `canonical_changes`（draft canonical 与 `execution.arguments` 比较）而非 `:413` 的 `user_changes`（draft canonical 与原始 `final_args` 比较）：两者都过 `_canonicalize_submit_args`，消除前端类型漂移与规范化噪音（见"对 spec §3.1 的修正"）。`compute_parameter_changes` 已在本文件内导入（`:413` 已使用），无需新增 import。
- `user_changes` 不再被截断逻辑使用，但仍由 approved 路径的 `_review_record(...)` 用于 audit，不改动其原有用法。

- [ ] **Step 8: 跑测试点 1，确认通过**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_edit_truncates_following_submits -v`

Expected: PASS

- [ ] **Step 9: 写测试——全程未编辑则 4 个正常确认（测试点 2）**

在 `TestSubmitReviewGate` 类末尾追加：

```python
    @pytest.mark.asyncio
    async def test_no_edit_all_submits_confirmed(self) -> None:
        gate = _MultiGate(
            {
                cid: SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                )
                for cid in ("s1", "s2", "s3", "s4")
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="a"),
                _submit_call(call_id="s2", cmd="b"),
                _submit_call(call_id="s3", cmd="c"),
                _submit_call(call_id="s4", cmd="d"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1", "s2", "s3", "s4"]
        assert len(capture.calls) == 4
        for idx in range(4):
            assert results[idx][1].status == "success"
```

- [ ] **Step 10: 跑测试点 1、2，确认通过**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_edit_truncates_following_submits tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate::test_no_edit_all_submits_confirmed -v`

Expected: PASS（2 passed）

- [ ] **Step 11: 提交**

```bash
git add matmaster/core/tool_runner.py tests/matmaster/core/test_full_tool_runner_submit_review.py
git commit -m "feat(runner): truncate following submits on user edit in batch

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2：边界与交互回归测试

实现已在 Task 1 整体落地；本 Task 用 spec §8 剩余测试点（3-7）加固边界与交互正确性，这些测试验证既有实现的边界行为，无需新增实现代码。

**Files:**
- Test: `tests/matmaster/core/test_full_tool_runner_submit_review.py`

- [ ] **Step 1: 写测试——截断的 submit 不进护栏、可重提（测试点 3）**

在 `TestSubmitReviewGate` 类末尾追加：

```python
    @pytest.mark.asyncio
    async def test_truncated_submit_not_armed_into_guard(self) -> None:
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
                        "cmd": "edited > log 2>&1",
                        "machine": "c32_m128_cpu",
                        "job_name": "matmaster-job",
                        "disk_size": 50,
                    },
                ),
            }
        )
        runner, _capture, state = _make_submit_runner(gate)

        await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
            ],
            _make_ctx(),
        )

        assert not state.get(RESUBMIT_SIGNATURES_KEY)

        gate2 = _MultiGate(
            {
                "s3": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                )
            }
        )
        state.set(SUBMIT_APPROVAL_GATE_KEY, gate2)

        results2 = await runner.execute_batch(
            [_submit_call(call_id="s3", cmd="gamma")],
            _make_ctx(),
        )

        assert gate2.reviewed == ["s3"]
        assert results2[0][1].status == "success"
```

说明：第一轮 S3 被截断（不发 review、不进护栏）；第二轮同签名重提时 `gate2.review` 被调用即证明未被 `ResubmitBlocked` 拦截（护栏命中会在 review 前 `continue`，gate 不会被调）。复用同一 `runner`/`state`，护栏在 `state` 中跨轮可见。

- [ ] **Step 2: 写测试——截断只针对 submit，非 submit 工具仍执行（测试点 4）**

在 `TestSubmitReviewGate` 类末尾追加：

```python
    @pytest.mark.asyncio
    async def test_truncation_only_blocks_submits_not_other_tools(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments={
                        "action": "submit",
                        "input_dir": "/share/c",
                        "image": "img",
                        "cmd": "edited > log 2>&1",
                        "machine": "c32_m128_cpu",
                        "job_name": "matmaster-job",
                        "disk_size": 50,
                    },
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="beta"),
                _make_tc("Bohrium", call_id="q1", action="query", note="ping"),
                _submit_call(call_id="s2", cmd="gamma"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1"]
        assert any(c.get("action") == "query" for c in capture.calls)
        assert results[2][1].status == "blocked"
        assert json.loads(results[2][1].content)["status"] == "SupersededByPriorEdit"
```

说明：S1 即被编辑并 approved 执行 + 置位；`action="query"` 的调用 `build_review_draft` 返回 None（非 submit），不进截断分支，照常执行；S2 是 submit，标志已置位 → 截断 `blocked`。`_make_tc` 已从 `tests.matmaster.core.test_full_tool_runner` 导入。

- [ ] **Step 3: 写测试——仅系统规范化不触发截断（测试点 5）**

在 `TestSubmitReviewGate` 类末尾追加：

```python
    @pytest.mark.asyncio
    async def test_normalization_only_does_not_truncate(self) -> None:
        provider = BohriumSubmitReviewProvider()
        s1_canonical = provider.build_review_draft(
            {
                "action": "submit",
                "input_dir": "/share/c",
                "image": "img",
                "cmd": "alpha",
                "job_name": "matmaster-job",
            }
        ).review_draft_arguments
        # 模拟前端把未改动的 disk_size 回传成字符串（类型漂移）：
        # normalize_execution_args 会把它还原成 int，不应判为实质编辑。
        s1_final = dict(s1_canonical)
        s1_final["disk_size"] = str(s1_final["disk_size"])
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=s1_final,
                ),
                "s2": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1", "s2"]
        assert len(capture.calls) == 2
        assert results[1][1].status == "success"
```

说明：`_submit_call(call_id="s1", cmd="alpha")` 的 model 默认 `input_dir="/share/c"`、`image="img"`、`job_name="matmaster-job"`，与构造 `s1_canonical` 的入参一致，故 runner 内部算出的 `draft.review_draft_arguments` 等于 `s1_canonical`。测试把 S1 的 `final_arguments` 设为 `s1_canonical` 但 `disk_size` 改成字符串（模拟前端类型漂移、用户实际未改动）：截断判定的 `canonical_changes` 比较的是 `execution.arguments`（`disk_size` 已 normalize 回 int `50`），diff 为空 → 不截断 → S2 正常 review + 执行。若改用 spec §3.1 原始公式（比较原始 `final_args`），`disk_size: 50 -> "50"` 会被误判为编辑并错误截断 S2，本测试即可捕获该回归（`gate.reviewed` 退化为 `["s1"]`）。

- [ ] **Step 4: 写测试——上报输入文件变更也触发截断（测试点 6）**

在 `TestSubmitReviewGate` 类末尾追加：

```python
    @pytest.mark.asyncio
    async def test_reported_file_change_truncates_following(self) -> None:
        gate = _MultiGate(
            {
                "s1": SubmitReviewDecision(
                    user_decision="submit",
                    review_outcome="approved",
                    final_arguments=None,
                    reported_input_file_changes=[
                        {"relative_path": "input.in", "lines": "1-5"}
                    ],
                ),
            }
        )
        runner, capture, _state = _make_submit_runner(gate)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
            ],
            _make_ctx(),
        )

        assert gate.reviewed == ["s1"]
        assert len(capture.calls) == 1
        assert capture.calls[0]["cmd"] == "alpha > log 2>&1"
        tr = results[1][1]
        assert tr.status == "blocked"
        assert json.loads(tr.content)["status"] == "SupersededByPriorEdit"
        assert tr.meta["superseded_by"] == "s1"
        assert tr.meta["changed_fields"] == ["input_files"]
```

说明：S1 参数无变（`final_arguments=None` → `user_changes` 空）但 `reported_input_file_changes` 非空 → 触发截断；`changed_fields` 仅含文件标记 `"input_files"`。S1 自身照常执行，S2 被截断。

- [ ] **Step 5: 写测试——opt-out（gate 缺席）批量行为不受影响（测试点 7）**

在 `TestSubmitReviewGate` 类末尾追加：

```python
    @pytest.mark.asyncio
    async def test_optout_batch_unaffected_when_gate_absent(self) -> None:
        runner, capture, _state = _make_submit_runner(gate=None)

        results = await runner.execute_batch(
            [
                _submit_call(call_id="s1", cmd="alpha"),
                _submit_call(call_id="s2", cmd="beta"),
                _submit_call(call_id="s3", cmd="gamma"),
            ],
            _make_ctx(),
        )

        assert len(capture.calls) == 3
        for idx in range(3):
            assert results[idx][1].status == "success"
```

说明：`gate=None` 时闸门段整体不进入（`if gate is not None and ...`），截断逻辑不生效，批量 submit 与引入本设计前一致——全部执行。

- [ ] **Step 6: 跑 Task 2 全部新测试，确认通过**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py -k "truncated_submit_not_armed or only_blocks_submits or normalization_only or reported_file_change or optout_batch" -v`

Expected: PASS（5 passed）

- [ ] **Step 7: 跑整个 submit review 测试文件回归**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_submit_review.py -v`

Expected: PASS — 既有 8 个测试 + 新增 7 个测试全部通过，无回归（特别确认现有 `test_rejected_*`、`test_oversized_*`、`test_approved_invalid_*` 不受影响）。

- [ ] **Step 8: 跑 lint**

Run: `uv run ruff check matmaster/core/tool_runner.py tests/matmaster/core/test_full_tool_runner_submit_review.py`

Expected: 无错误（All checks passed）。

- [ ] **Step 9: 提交**

```bash
git add tests/matmaster/core/test_full_tool_runner_submit_review.py
git commit -m "test(runner): cover submit batch truncation boundaries and opt-out

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review（spec 覆盖核对）

逐条核对 spec 各节是否被任务覆盖：

| Spec 节 | 覆盖位置 |
|---|---|
| §3.1 触发条件（canonical_changes 或 reported；见"对 spec §3.1 的修正"） | Task 1 Step 7 置位条件用规范化后 diff；测试点 5（规范化+类型漂移不触发）、测试点 6（文件变更触发） |
| §3.2 截断行为（batch 局部标志、被编辑自身照常执行） | Task 1 Step 5（局部标志）、Step 7（置位在 normalize 成功后）；测试点 1 断言 S2 执行 |
| §3.3 截断范围（只截 submit） | Task 1 Step 6（截断分支在 `draft is not None` 内）；测试点 4 |
| §3.4 截断 tool_result 形状（status/content/meta） | Task 1 Step 6；测试点 1、6 断言 status/content/meta/superseded_by/changed_fields |
| §4.1 不进护栏 | Task 1 Step 6（截断在护栏检查前 continue）；测试点 3 |
| §4.2 关闭确认叠加优先级 | 代码结构天然满足（截断在 review 前、清空 gate 下一批生效），plan 不额外编码；背景已说明 |
| §4.3 拒绝维持现状 | 不改 reject 路径，现有 `test_rejected_*` 回归（Task 2 Step 7） |
| §5 时序示例（编辑 S2） | 测试点 1 直接对应 |
| §6 实现落点（三处） | Task 1 Step 5/6/7 |
| §7 边界 | 未编辑→测试点 2；改回原值/仅规范化→测试点 5；第一个被编辑→测试点 4（S1 即编辑）；混非 submit→测试点 4；超长→现有 `test_oversized_submit_arg_errors_not_submit` 不变；normalize 失败不触发→由 Step 7 置位点在 normalize 成功之后保证，现有 `test_approved_invalid_final_arguments_returns_error` 覆盖该路径；opt-out→测试点 7；子 run（gate 不注入）→与 opt-out 同构（测试点 7） |
| §8 测试点 1-7 | Task 1（点 1、2）+ Task 2（点 3-7），逐一实现 |
| §9 后端先行、fake gate 驱动 | `_MultiGate` + `_SubmitCapture` 全后端验证，零前端依赖 |
| §10 决策记录 | 实现遵循：修改即截断、只截后续 submit、不进护栏、截断优先、英文 block message |

**Placeholder 扫描：** 无 TBD/TODO，每个代码步骤均含完整代码，每个测试均含完整断言，每条命令均含预期输出。

**类型/命名一致性：** `superseding_edit: tuple[str, list[str]] | None` 在声明（Step 5）、读取（Step 6）、置位（Step 7）三处一致；`changed_fields` 文件标记统一为 `"input_files"`（Step 7 写入、测试点 6 断言）；`_MultiGate.reviewed` / `_SubmitCapture.calls` 在各测试中用法一致；`_gate_block_result("SupersededByPriorEdit", ...)` 产出的 `block_reason`/`layer` 与测试点 1 断言一致。

**未纳入（如需更强保障可后补，spec §8 未要求）：** "S1 编辑但 normalize 失败时 S2 不应被截断"这一组合边界——当前由置位点位于 normalize 成功之后的代码结构保证，未单列测试。若希望显式锁定该契约，可补一个"S1 `final_arguments` 含非法 `disk_size` → S1 返回 error 且 S2 仍正常 review"的测试。

---

## Execution Handoff

Plan 已保存到 `docs/superpowers/plans/2026-06-21-bohrium-submit-batch-truncation-on-edit.md`。两种执行方式：

1. **Subagent-Driven（推荐）** — 每个 task 派发独立子 agent，task 间双阶段 review，迭代快。REQUIRED SUB-SKILL: superpowers:subagent-driven-development。
2. **Inline Execution** — 在当前会话用 superpowers:executing-plans 批量执行 + checkpoint review。

选哪种？
