# Bohrium submit 批次截断（用户在确认时修改内容）设计

> 状态：设计已与用户对齐（2026-06-21），待 review 后转 implementation plan。
> 关系：本设计是 `2026-06-19-bohrium-submit-review-on-interaction-base` 的增量增强，建立在已落地的后端 submit review 闸门之上，不改其既有结构。

## 1. 背景与问题

submit review 闸门在 `FullToolRunner.execute_batch` 的 Step 1 串行阶段逐个处理 submit（`tool_runner.py:282` 的 for 循环；闸门读 gate 在 `:353`，`await gate.review` 在 `:391`）。LLM 一轮可能并行发起多个 submit，它们在闸门里是依次确认的（第一个在 `await gate.review` 阻塞时，后续尚未进入循环体）。

用户在确认界面可以编辑 submit 内容（cmd、input_dir、上报的输入文件变更等）。当前设计里，这种编辑只应用到被编辑的那一个 submit，后续 submit 仍携带编辑前的参数继续。但同一批 submit 往往隐含关联（共享同一 input_dir / 工作区文件、同一实验的多个作业），于是出现两个糟糕场景：

1. 用户编辑了第一个 submit 但没有关闭确认：后续 submit 带旧内容逐个弹确认，用户被迫重复同样的编辑 N 次。
2. 用户编辑了第一个 submit 并关闭了确认：后续 submit 带旧内容直接被提交——错误内容产生真实副作用，最严重。

## 2. 目标与非目标

**目标：** 当用户在确认某个 submit 时做了实质编辑，把这次编辑视为一个批次失效信号——截断该编辑点之后的后续 submit，把控制权交还 LLM，由它基于最新编辑重新规划后续提交。

**非目标：**
- 不让后端去推断 submit 之间是否真的关联（那是语义层面的，只有发起它们的 LLM 知道）。后端只做保守截断 + 回传信号，关联判断交给 LLM。
- 不改前端、不改 gate adapter、不新增事件类型或端点。本设计纯粹是 runner 串行阶段的编排增强。
- 不改"拒绝"的现有行为（见 §4.3）。

## 3. 核心设计

### 3.1 触发条件（什么算"被编辑"）

复用闸门已经算出的两个量，不新造判定：

- `user_changes = compute_parameter_changes(draft.review_draft_arguments, final_args)`（`tool_runner.py:413`）——参数字段级 diff。
- `reported = decision.reported_input_file_changes or []`（`tool_runner.py:417`）——用户上报的输入文件变更。

`user_changes` 非空 **或** `reported` 非空 = 用户做了实质编辑 → 触发截断。

关键语义：**系统规范化不算编辑**。`compute_parameter_changes` 比较的是已规范化的草稿 `review_draft_arguments` 与用户提交的 `final_args`，所以补 `> log 2>&1`、补默认 machine 这类规范化不会进入 `user_changes`。用户打开界面未改、或改回原值时 diff 为空，不触发截断。

### 3.2 截断行为

- 截断状态用一个 **batch 级局部变量**（`execute_batch` 单次调用内的局部状态，不写 `runner_state`）。这天然保证"不跨轮累积"（见 §4.1）。
- 在 approved 正常路径（`tool_runner.py:489`–`510` 区域，即 outcome 既非 `cancelled` 也不在 `_OUTCOME_STATUS` 的分支）检测 §3.1 的触发条件；命中即置位标志，记录触发编辑的 `tool_call_id` 与变更字段集合（`changed_fields = list(user_changes.keys())`，文件变更以一个标记位表示）。
- 被编辑的那个 submit 自身**照常执行**：它已带新内容进入 approved 路径，继续 fall through 到 structural / input / policy 校验与并发执行，不受标志影响（标志只影响其后的迭代）。
- 触发时机基于"用户做了编辑"这一事实本身，**不取决于**被编辑 submit 自身是否最终通过 structural 校验（structural 在置位点之后）。但置位发生在 normalize 成功的 approved 路径（`:489` 之后）——若编辑值非法导致 `normalize_execution_args` 抛错、走 InvalidFinalArguments 分支（`:452`），视为编辑未成功落地，不触发截断（见 §7）。

### 3.3 截断范围（只截后续 submit）

标志置位后，串行循环继续。对其后每一次工具调用：

- 若是 **submit**（闸门段 `build_review_draft` 返回非 None）：不再 `await gate.review`、不进护栏，直接产出截断 `tool_result` 并 `continue`。
- 若是**非 submit 工具**（读文件、查询等无提交副作用的调用，`build_review_draft` 返回 None，或工具本身无 `submit_review_provider`）：照常执行。

理由：截断的论证（旧内容 → 错误提交）只对有提交副作用的 submit 成立；截断无副作用的读/查会白白阻断 LLM 获取信息，且读操作可重试、无害。

截断判定置于闸门段确认当前调用是 submit（`draft is not None`）之后、发起 review 之前。极端叠加边界（批次已截断 + 某后续 submit 参数超长导致 `build_review_draft` 抛 `SubmitReviewArgumentError`）属罕见，沿用现有超长 `error` 行为即可，无需特判——两者都引导 LLM 重来。

### 3.4 被截断的 tool_result 形状

被截断的 submit 没有发起 review、没有 `user_decision`、没有 review 产物，因此**不套** `attach_submit_review_record` 的完整审计 payload，而是类比护栏 `ResubmitBlocked` 复用 `_gate_block_result`：

- `status = "blocked"`（复用语义：未执行、无外部副作用、需 agent 调整）。
- content：`{"success": false, "status": "SupersededByPriorEdit", "message": <见下>}`。
- meta：`{"block_reason": "SupersededByPriorEdit", "layer": "submit_approval_gate", "superseded_by": <触发编辑的 tool_call_id>, "changed_fields": [...]}`。
- message（面向 LLM，**英文**）：大意为同批次另一个 submit 的参数或输入文件已被用户修改、本次未执行、请参考那些修改后再重新评估提交。建议文案：`The user modified the parameters or input files of another submit in the same batch. This submit was not executed; please refer to those changes and re-evaluate before resubmitting.`

LLM 获取"具体改了什么"的途径是同批次被编辑 submit 的 `content.review`——该 submit 已 approved 执行，其结果带 `parameter_changes` / `input_file_changes`，截断 message 只需把注意力引向那处、无需重复细节（这也正是 message 可以保持简短的原因）。`superseded_by` / `changed_fields` 留在 meta 供审计与调试，meta 为内部信号、不进 LLM 上下文。

## 4. 与现有机制的交互

### 4.1 重提护栏（RESUBMIT_SIGNATURES）

被截断的 submit **不进** `RESUBMIT_SIGNATURES_KEY` 护栏。它们不是被拒绝，而是被批次中止，必须允许 LLM 调整后重提；强行进护栏会连合理重提一起 block，违背"交还 LLM 重新规划"的意图。

循环风险（截断 → LLM 重提 → 又被截断）实际可控：截断标志是 batch 局部的、不跨轮累积。下一轮 LLM 重提是一个新的 batch，正常走确认流程；除非用户在新一轮里又做编辑，否则后续 submit 正常通过。因此"反复截断"只在"用户每轮都编辑"时发生——那是用户的主动选择，不是系统缺陷。

### 4.2 关闭确认（叠加优先级）

存在一个并行设计（用户在确认时可选择"后续不再需要确认"）。**实现机制（由「确认并不再询问」对齐 plan 落地，方向 B2）：** 不清空 run 内 gate，而是在 runner-state 置一个 run-level skip flag（`SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY`）；后续 submit 仍进入整个 gate 安全段（`build_review_draft`、`superseding_edit` 截断分支、`RESUBMIT_SIGNATURES` 护栏），只在真正要 `gate.review()` 弹窗时由 skip flag 短路放行。

当同一次确认**既编辑了内容又关闭了确认**时：**截断优先**。后续 submit 先命中 `superseding_edit` 截断分支并 `continue`（交还 LLM），不会走到 skip-confirmation 放行；skip flag 在 LLM 重新规划后的下一批 submit 才表现为"不弹"。否则就退化成 §1 的最糟场景 2：旧内容免确认直接提交。

**护栏优先：** 若同一 run 内先前某 submit 已被 reject/timeout/busy 写入 `RESUBMIT_SIGNATURES`，之后另一 submit 关闭确认，同签名后续 submit 仍在 `gate.review` 前命中 `ResubmitBlocked`，不因 skip flag 被直接执行。

> 注：本设计与"关闭确认"是两个独立增强。若"关闭确认"尚未实现，本设计独立成立；两者同时存在时，截断与 resubmit guard 均优先于跳过弹窗（见对齐 plan）。

### 4.3 拒绝（维持现状）

用户拒绝某个 submit 时，逻辑上后续相关 submit 也可能需要重评。但本设计只对"编辑"做截断；"拒绝"维持现有行为（只 block 当前 submit + 两条签名进护栏，不截断后续）。拒绝是否也应触发批次截断，留作独立后续讨论。

## 5. 时序示例

设 enabled=True，一轮并行 4 个关联 submit（S1–S4），用户编辑了 S2：

1. S1 进闸门 → `await gate.review` → 用户未编辑、批准 → approved，进并发执行队列。
2. S2 进闸门 → 用户编辑了 cmd 后批准 → approved（带新 cmd），进并发执行队列；检测到 `user_changes` 非空 → 置位批次截断标志（superseded_by=S2, changed_fields=[cmd]）。
3. S3 进闸门 → 是 submit 且标志已置位 → 直接 `blocked`（SupersededByPriorEdit, superseded_by=S2），不发 review、不进护栏。
4. S4 同 S3。
5. Step 2 并发执行 S1、S2（带各自最终参数）。S3、S4 以 blocked 返回。
6. LLM 收到 S3、S4 的 blocked，看到 S2 改了 cmd → 重新规划：若 S3/S4 需同步调整则带新参数重提；若确属无关则原样重提（仅多一轮）。

## 6. 实现落点

全部落在 `matmaster/core/tool_runner.py` 的 `execute_batch` Step 1 串行循环内：

- 循环开始前声明 batch 级标志变量（如 `superseding_edit: tuple[str, list[str]] | None = None`）。
- 闸门段 `draft is not None` 之后、护栏/`gate.review` 之前：若 `superseding_edit is not None`，构造截断 `_gate_block_result(...)` + 截断 meta，记录结果并 `continue`。
- approved 正常路径（`:489`–`510` 区域）末尾：若 `user_changes` 或 `reported` 非空，置 `superseding_edit = (tc.id, changed_fields)`。

预计约十几行，零新增事件类型、零跨文件、不触碰 gate adapter / exp 装配 / 前端。

## 7. 边界情况

- **未编辑直接批准**：diff 与文件变更均空 → 不置位 → 后续 submit 正常确认。
- **编辑后改回原值**：diff 空 → 不触发。
- **第一个 submit 即被编辑**：S1 执行，S2–S4 全截断。
- **批次中混非 submit 工具**：编辑点之后的读/查正常执行，只有 submit 被截断（§3.3）。
- **批次已截断 + 后续 submit 参数超长**：沿用现有 `SubmitReviewArgumentError → error` 行为，不特判。
- **编辑值非法（normalize 失败）**：被编辑的 submit 自身返回 InvalidFinalArguments error，不触发截断（编辑未成功落地，批次前提实际未变）。
- **opt-out（gate 不存在）**：闸门段整体不进入，本逻辑不生效，主路径行为不变。
- **子 run（spawn）**：gate 不注入，本逻辑不生效。

## 8. 测试点（fake gate 驱动，后端可独立验证）

1. 编辑 S2 → S1/S2 执行（捕获 submit_job_via_runtime 收到 S2 新参数）、S3/S4 返回 blocked + status=SupersededByPriorEdit + superseded_by=S2。
2. 全程未编辑 → 4 个 submit 各自正常确认（gate.review 被调 4 次），无截断。
3. 截断的 submit 不进护栏：截断后同签名重提不被 ResubmitBlocked。
4. 截断只针对 submit：编辑点之后的非 submit 工具仍执行。
5. 触发判定只认实质编辑：仅系统规范化（如 cmd 补 `> log 2>&1`）不触发截断。
6. 文件变更触发：`reported_input_file_changes` 非空、参数 diff 为空时也截断后续。
7. opt-out 回归：gate 不存在时批量 submit 行为与本设计引入前一致。

## 9. 前置依赖与缺口

本增强建立在"用户能在确认界面编辑 submit 内容"之上，而前端 submit_review 确认 UI 目前完全未实现（见 `2026-06-19` 系列调查结论：后端闭环、断点在前端）。因此：

- 本逻辑可**后端先行**，用 fake gate（返回带 `final_arguments` / `reported_input_file_changes` 的 decision）模拟用户编辑来完整测试。
- 端到端真正生效仍依赖前端实现 submit_review 的确认 + 编辑能力。本设计不解除该前端依赖，只在后端把"编辑即截断"的语义补齐。

## 10. 决策记录

- 处理方向：修改即截断后续 submit（对比"仅文件类修改才截断"的精细化变体、"整批一次性统一确认"的重构方案；选最简单且安全、误伤可由 LLM 自愈的方案）。
- 截断范围：只截后续 submit（对比"截断后续所有工具"；选风险论证成立的最小范围）。
- 护栏：被截断不进护栏；循环风险靠"批次局部、不跨轮累积"化解。
- 叠加：截断优先于关闭确认；拒绝维持现状。
- block message 语言：本次截断的 LLM 可见 message 用英文（用户 2026-06-21 偏好）；现有 `_OUTCOME_MESSAGE`（reject/timeout/busy）与 `ResubmitBlocked` 维持中文，不在本 spec 范围内统一（接受 LLM 侧 block 内容暂时中英混杂）。
