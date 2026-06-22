# Bohrium submit_review 不再询问 前后端对齐 设计方案

> **状态:** 待 review(决策点 1-3 已与用户拍板;决策点 4 已按 review 修订为「保留 gate + run-level skip flag」,确保批次截断与 resubmit 护栏优先于跳过弹窗)
> **日期:** 2026-06-21
> **来源契约:** 前端 plan `scimaster-bohr-chat/docs/superpowers/plans/2026-06-21-bohrium-submit-review-frontend.md`「设计迭代修订」节第 4 条(三维确认 + 不再询问)
> **关系:** 与已落地的批次截断(`docs/superpowers/specs/2026-06-21-bohrium-submit-batch-truncation-on-edit-design.md`,代码已合并)是**同一串行 review 场景下的两种用户选择**,本方案叠加其上并遵守其 §4.2「截断优先」约定(见 §6.3)。
> **docs 禁令:** 本文件位于 `docs/`,遵守工作区 CLAUDE.md「绝不向 docs/ 提交 git」。只写盘,不 `git add docs/`。

## 1. 背景与目标

前端已实现 Bohrium 提交工作台大模态的底栏三按钮:拒绝 / 确认 / 确认并不再询问。其中「确认并不再询问」要求后端在收到回复后,让该会话后续的 Bohrium 提交不再弹确认模态。前端的线协议、乐观更新与 hydrate 回读均已落地,但后端**只设计了机制、未与前端对齐实现**:`disable_future_confirmation` 字段在后端全库零见,无人解析、无人据此关闭确认开关。

目标:补齐后端,使「确认并不再询问」端到端生效,覆盖两个时间尺度(本次 run 内的后续提交、用户下次发消息的后续 run),且与已落地的批次截断正确共存。

## 2. 前端契约(已批准,后端按此对齐)

**reply 线协议**(通用端点 `POST /sessions/{sessionId}/interactions/{requestId}/reply`,body `{kind, payload}`):

```
kind: "submit_review"
payload: {
  decision: "submit" | "reject",
  disable_future_confirmation: boolean,   // 仅与 submit 配对;reject 时恒 false
  submit_arguments: { ...全字段, action: "submit" },
  reported_input_file_changes: [{ relative_path, lines }]
}
```

三按钮映射:拒绝 = `{reject, false}`、确认 = `{submit, false}`、确认并不再询问 = `{submit, true}`。

**前端乐观更新:** reply 返回 200 后,前端乐观置本地 `bohriumSubmitConfirmationPolicy = {required: false, source: 'session'}` 并打 `skipHydrateBohriumSubmitConfirmationRef`;下次 hydrate 由后端回填仍 false。这意味着前端**已隐含假定「reply 成功即开关已关」**。

## 3. 后端现状

**已就位:**

- 通用 reply 端点 `chat_api.py:538` `interaction_reply`:校验 pending/session/kind/size → `answer_pending_interaction` 写 Redis → publish reply event → 记 history。对 payload **完全不透明**,无 kind-specific 分支。
- 会话级配置存储:DB 表 `evo_chat_sessions.bohrium_submit_confirmation_required`(nullable bool);`ChatSessionsService.set_bohrium_submit_confirmation(session_id, user_id, required) -> bool`。
- effective 计算(主链路 `stream_service.py:798-811`):`req 显式值 → session DB override → 默认 false`。**覆盖语义(coalesce),主链路不读 user 全局**(两级配置已被推迟)。effective 进 run payload → worker `agent_worker.py:351` 一次性算出 `submit_confirmation_enabled` → `run_agent` 据此构造 gate(`agent_run_service.py:524`)。
- 配置回读 API:`GET /sessions/{sid}/bohrium-submit-confirmation` → `{session_id, required}`;`PUT` 设置 session override。
- submit_review 发起链 + **已落地的批次截断**:gate(`BridgeSubmitApprovalGate`)存在 + `instance.submit_review_provider` + `build_review_draft` 非 None → runner 串行 review。批次截断的 `superseding_edit` batch-local 标志已在 `tool_runner.py:284/375/539` 落地(git `f2e4b773`)。
- reply payload 已随 envelope 流到 worker 侧 gate:`interaction_bridge.py:110` `return envelope.get("payload")`,故 `gate.review()` 拿到的 `reply` 就是前端发的整个 payload(含 `disable_future_confirmation`)。

**两个 gap:**

1. **进程内 short-circuit 不存在。** `tool_runner.py:358` 每个 submit 各自判断 `gate is not None` 并各自调 `gate.review`(`:412`),decision 返回后只更新防重复提交的 `guard`,**无「第一个选不再询问→后续跳过」逻辑**;`SubmitReviewDecision` 无 `disable_future_confirmation` 字段,`_reply_to_decision`(`submit_approval_gate.py:33`)直接丢弃了它。
2. **持久化与回读缺口。** reply 端点不写会话级开关;`GET` 响应缺前端 policy 需要的 `source` 字段。

## 4. 核心设计决策(用户已拍板)

1. **落库时机 = 收到回复即落库。** 不再询问是会话偏好,与本次提交成败正交;在通用 reply 端点 `answer` 成功后同步写 session DB。代价:与 Redis answer 跨存储非严格原子,写库失败按 best-effort 降级。
2. **GET 补轻量 source。** 本期只区分 `session`(有 override)/ `default`(无 override);`user` 三态占位,留给两级配置 plan。
3. **short-circuit 粒度 = 本次 run 全部后续。** decision 带 `disable_future_confirmation` 时在 runner_state 写 run-level skip flag,本次 run 内(含同批 + 后续 turn)后续 submit 不再弹 review。
4. **与批次截断/重提护栏共存 = 保留 gate,只跳过弹窗。** 不能把 `SUBMIT_APPROVAL_GATE_KEY` 清成 `None`:否则后续 submit 会跳过整个 review 串行安全段,不仅可能绕过批次截断,也会绕过已拒绝/超时 submit 写入的 `RESUBMIT_SIGNATURES` 护栏。采用方向 B2:保留 gate 对象与入口条件,新增 run-level skip flag;后续 submit 仍先经过 `build_review_draft`、`superseding_edit` 截断分支、resubmit guard,只在真正要 `gate.review()` 弹窗时短路放行。

## 5. 架构:两级时间尺度,缺一不可

| 尺度 | 谁来管 | 覆盖场景 |
|---|---|---|
| **本次 run(进程内)** | runner 设置 skip flag,保留 gate 安全段(改 `matmaster` 运行时包) | 同批/本轮内,第一个选不再询问后,后续 submit 不再弹窗;批次截断与 resubmit 护栏仍优先 |
| **下次 run + hydrate(持久化)** | reply 端点写 session DB + GET source(改 `src` 服务层) | 用户下次发消息时 worker 重新构造 gate 的判断;前端 hydrate 回读 |

只有进程内 → 本次 run 结束 runner_state 销毁,下次发消息又弹;只有 DB → 本次 run 内后续 submit 仍逐个弹。两条必须都做。本次 run 这条与批次截断在同一串行循环交汇,共存规则见 §6.3。

## 6. 组件改动

### 6.1 `matmaster` 运行时包(进程内 short-circuit)

**(a) `matmaster/types/submit_review.py` — `SubmitReviewDecision` 加字段**

`@dataclass`,新字段带默认值放末尾(现有 busy/timeout/cancelled 等只传部分字段的构造点不受影响):

```python
@dataclass
class SubmitReviewDecision:
    user_decision: str | None
    review_outcome: str
    final_arguments: dict[str, Any] | None = None
    reported_input_file_changes: list[dict[str, Any]] | None = None
    disable_future_confirmation: bool = False   # 新增
```

**(b) `matmaster/integration/submit_approval_gate.py` — `_reply_to_decision` 解析**

只在 `decision == "submit"` 时取真(双保险:即便前端违约在 reject 时带了 true,也不生效):

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

**(c) `matmaster/core/tool_runner.py` — 保留 gate + skip flag 短路弹窗(方向 B2)**

> 不能置 gate None:批次截断的截断分支、resubmit guard 都位于 `if gate is not None and instance.submit_review_provider is not None` 之内。清 gate 后后续 submit 会跳过整个闸门段,既可能绕过 `superseding_edit`,也可能绕过用户先前 reject/timeout 写入的 `RESUBMIT_SIGNATURES`,携带旧内容或已拒绝内容直接执行。

四处改动:

1. **新增 runner-state key**(`matmaster/core/submit_review_support.py`):
```python
SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY = "bohrium_submit_skip_confirmation"
```

2. **闸门入口保持不变**(`tool_runner.py:359`):
```python
if gate is not None and instance.submit_review_provider is not None:
```

这样后续 submit 仍会进入 `build_review_draft`、`superseding_edit` 截断分支、`RESUBMIT_SIGNATURES` 护栏。关闭确认只影响是否发起人工 review,不影响这些安全判断。

3. **在 `gate.review` 前短路弹窗**(`tool_runner.py:411` 附近,截断分支与 resubmit 护栏之后):
```python
skip_confirmation = bool(self._state.get(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY))
if skip_confirmation:
    # 不发 interaction_request,不等待前端 reply;保留后续 structural/input/policy/tool 执行链。
    pass
else:
    decision = await gate.review(...)
```

实现时不要只写 `pass`:需要让当前 submit 走 approved 执行路径。最小做法是为 skip 分支构造一个本地 decision,语义等价于用户确认但没有编辑:

```python
decision = SubmitReviewDecision(
    user_decision="submit",
    review_outcome="approved",
    final_arguments=draft.review_draft_arguments,
)
```

因为 skip 分支没有前端交互,`reported_input_file_changes` 必然为空;若后续 `normalize_execution_args` 发现参数非法,仍走既有 `InvalidFinalArguments` 分支。

4. **approved 正常路径置 skip flag**(`tool_runner.py:531` `base_args = execution.arguments` 之后,与 `superseding_edit` 置位对称):
```python
if decision.disable_future_confirmation:
    self._state.set(SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY, True)
```

### 6.2 `src` 服务层(持久化 + hydrate)

**(d) `src/apis/chat_api.py` — reply 端点 submit_review 副作用**

在 `interaction_reply` 里,`answer_pending_interaction` 成功之后、`publish/history` 之前,**内联**(不抽函数,保持业务连贯):

```python
if (
    req.kind == "submit_review"
    and req.payload.get("decision") == "submit"
    and req.payload.get("disable_future_confirmation") is True
):
    if not chat_svc.set_bohrium_submit_confirmation(sid, user_id, False):
        logger.warning(
            "disable future submit confirmation failed: session_id=%s", sid
        )
```

- 三条件:`kind` 限定 submit_review;`decision == submit` 双保险(reject 不触发);`is True` 严格判定(缺省/false/非 True 真值都不触发)。
- 幂等:副作用在 `answer` 成功后执行,而 `answer_pending_interaction` 对同一 request_id 只成功一次(重复 reply 第二次返回 `not_pending` → 409,到不了副作用)。
- 降级:写库失败(含分享场景 `user_id` 为空导致 WHERE 不命中)只记日志、**不 raise**——reply 照常 200、作业照常提交,偏好下次 hydrate 自纠。与 `stream_service.py:790` 发消息时 set 失败会 raise 不同,理由:reply 的主职责是让 agent 继续,偏好保存是附带,不能因它失败而阻断 agent。

**(e) `src/models/chat.py` + `src/apis/chat_api.py` — GET 补 source**

`BohriumSubmitConfirmationData` 加 `source`,类型 `Literal["session", "user", "default"]` 提前占位三态,本期只产 `session`/`default`:

```python
# _session_bohrium_submit_confirmation_data_from_row(chat_api.py:135)
raw = row.get("bohrium_submit_confirmation_required")
return BohriumSubmitConfirmationData(
    session_id=session_id,
    required=None if raw is None else bool(raw),
    source="session" if raw is not None else "default",
)
```

GET 与 PUT 共用此 builder,自动一致带上 source。

### 6.3 与批次截断(superseding_edit)的交互

两个机制在 `execute_batch` Step 1 同一串行循环交汇。保留 gate + skip flag 后(6.1(c))的四象限行为:

| S2 的选择 | 同批 S3/S4 | 下一批 / 下次 run |
|---|---|---|
| 纯确认(submit, false) | 各自 review(弹) | 看会话 DB |
| 纯关闭确认(submit, true, 无编辑) | 放行执行(不弹) | 不弹 |
| 编辑 + 关闭确认 | 截断 SupersededByPriorEdit | 不弹 |
| 纯编辑(无关闭确认) | 截断 | 各自 review |

- **机制分工:** `disable_future_confirmation` → 置 `SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY`(只管跳过后续弹窗);`canonical_changes/reported` → 置 `superseding_edit`(管截断)。二者独立,可同时发生。
- **截断优先**(满足批次截断 spec §4.2):编辑+关闭确认时,后续 submit 仍进入 gate 安全段 → 先命中 `superseding_edit` 截断分支 `continue`,不会走到 skip-confirmation 放行。可观察结果与 §4.2「截断优先、下一批才不弹」一致。
- **resubmit 护栏优先:** 如果同一 run 内先前某个 submit 已被 reject/timeout/busy 并写入 `RESUBMIT_SIGNATURES`,之后另一个 submit 选择关闭确认,同签名后续 submit 仍会在 `gate.review` 前命中 `ResubmitBlocked`,不会因为 skip flag 被直接执行。
- **为何用方向 B2(保留 gate + skip flag)而非方向 A(延迟清 gate 到下一批)或原 B(立即清 gate + 解耦入口):** 方向 A 满足不了「纯关闭确认、无编辑」时同批后续立即放行;原 B 若清 gate,容易绕过 resubmit guard。B2 同时满足纯关闭确认立即不弹、编辑场景截断优先、拒绝/超时护栏不失效。
- **粒度差异是语义决定:** 截断 = batch-local(局部变量,不跨 turn,"这一批的连锁反应");关闭确认 = run-level(skip flag,跨 turn,"会话偏好")。
- **skip flag 落点在 normalize 成功后**(与 `superseding_edit` 置位对称):S2 编辑值非法走 `InvalidFinalArguments`(`:479`)时既不置截断也不置 skip flag,后续 submit 正常 review——与批次截断"normalize 失败不截断"对称,避免编辑非法时误放行后续。

## 7. 端到端数据流

1. 本次 run,`submit_confirmation_enabled=true`,gate 在,agent 一个 turn 并行 emit 3 个 Bohrium submit
2. runner 串行 review:第 1 个发 `gate.review` → 前端弹模态
3. 用户点确认并不再询问 → POST reply `{decision:submit, disable_future_confirmation:true, ...}`
4. 后端 `interaction_reply`:校验 → `answer`(写 Redis) → **(d) 写 session DB false** → publish/history → 200
5. 前端收 200 → 乐观更新 `{required:false, source:'session'}` + `skipHydrate`
6. worker 侧 `gate.review()` 唤醒 → `_reply_to_decision` 解出 `disable_future_confirmation=true` → 本 submit normalize 成功执行 → **(c) approved 路径置 skip flag**(若同时有编辑,另置 `superseding_edit`)
7. 同批第 2、3 个 submit:入口仍是 `gate is not None and provider is not None` → 先过 `superseding_edit` 与 resubmit guard;无编辑且无护栏命中时由 skip flag 短路 `gate.review`,直接执行(**不弹**);有编辑则先截断(**SupersededByPriorEdit**)
8. 同次 run 后续 turn 若再 emit submit:gate 仍在,skip flag 仍为 True → 继续先过安全段,再跳过弹窗直接执行
9. 用户**下次发消息**(下一次 run):`stream_service.py:798` 重算 effective = session DB(false) → 不构造 gate → 后续提交不弹
10. 前端**下次 hydrate** GET → `{required:false, source:'session'}` → 与第 5 步乐观更新一致

## 8. 错误处理与降级

- reply 端点副作用写库失败:记 warning,不影响 reply 200、不影响作业提交。属 best-effort,下次 hydrate 自纠(用户已接受为「收到回复即落库」的代价)。
- `_reply_to_decision` 对缺省/非布尔 `disable_future_confirmation`:`bool(reply.get(...))` 归一,缺省为 False。
- skip flag(进程内)与写 DB(持久化)相互独立:即便其一失败,另一条仍各自生效其时间尺度;两者语义一致(都把会话推向「不再询问」)。
- API 侧写 DB 与 worker 侧 skip flag 的一处不对称:S2 编辑非法时,worker 侧 normalize 失败 → 不置 skip flag(本批后续仍可能 review 一次),但 API 侧已按 reply 意图写了 DB(下次 run 不弹)。原因:API 端点处理 reply 时 worker 尚未 normalize,无法预知成败,只能以用户意图为准。极罕见,接受。

## 9. 边界情况

- **reject:** `decision != submit` → (c)(d) 均不触发;(b) 保证 decision 的 `disable_future_confirmation` 恒 False。
- **其它 kind(ask_question):** `kind != submit_review` → (d) 不触发;ask_question 不经 gate,(c) 无关。
- **编辑 + 关闭确认:** 截断优先(§6.3),后续 submit 截断而非放行;skip flag 已置,下一批/下次 run 不弹。
- **编辑非法 + 关闭确认:** S2 走 `InvalidFinalArguments` error,不置 skip flag、不置截断,后续 submit 正常 review(§6.3 末);DB 仍按意图写(§8 不对称)。
- **同次 run 多 turn:** skip flag 一旦置位,本次 run 内(跨 turn)持续不再弹;gate 对象仍保留,继续承载截断/重提护栏,直至 run 结束 runner_state 销毁。
- **先拒绝后关闭确认:** S1 reject/timeout 写入 resubmit guard,S2 submit+disable_future_confirmation 置 skip flag,S3 若与 S1 同签名仍应 `ResubmitBlocked`,不得直接执行。
- **幂等:** (d) 在 answer 成功后,answer 只成功一次,副作用只执行一次。
- **分享/匿名 `user_id` 为空:** `set_bohrium_submit_confirmation` WHERE 不命中返回 False → 走降级日志;进程内 (c) 仍生效(本次 run 不再弹)。

## 10. 范围边界(out of scope)

- **两级配置(user 全局)effective 解析、DB schema、`stream_service` effective 计算链**:按既定决策另立 plan,本方案不碰。
- **source 的 `user` 三态**:模型占位,实际产出留给两级配置 plan。
- 本方案在 `matmaster` 包加 (a)(b)(c)、`src` 层加 (d)(e)。(c) **不修改**批次截断已落地的闸门入口条件(`tool_runner.py:359` 仍为 `gate and provider`),只在 `gate.review` 前增加 skip-confirmation 短路与 `SUBMIT_REVIEW_SKIP_CONFIRMATION_KEY`。plan 须回归批次截断的 7 个既有测试,并新增 resubmit guard 优先测试。不改 gate 的 bridge 传输、不改 worker 的 enabled 计算、不改 `superseding_edit` 的置位/截断逻辑本身。

## 11. 测试策略(针对新行为的正当 TDD)

**`matmaster` 单元测试**(复用批次截断已建的 `_MultiGate`/`_SubmitCapture`,加在 `tests/matmaster/core/test_full_tool_runner_submit_review.py::TestSubmitReviewGate`):
- `_reply_to_decision`:submit+true → `disable_future=True`;submit+false/缺省 → False;reject+true → False(双保险);非布尔值 → 归一。
- 纯关闭确认 short-circuit:同批 [submit+disable(无编辑), submit, submit] → 第 1 个 review 后置 skip flag,第 2/3 个仍进入 submit review 安全段但不经 `gate.review`(`gate.reviewed == ["s1"]`)、`capture.calls` 含 s2/s3 直接执行。
- **截断优先(批次截断 plan 第 27 行点名要求):** S2 编辑+关闭确认 → 置 skip flag,S3 仍返回 `SupersededByPriorEdit`(不被 skip flag 误放行)。
- **resubmit guard 优先:** S1 reject/timeout 写 guard,S2 submit+disable 置 skip flag,S3 与 S1 同签名 → S3 返回 `ResubmitBlocked`,不经 `gate.review`,不执行。
- run-level 跨 turn:S1 关闭确认后,复用同一 `runner`/`state` 的下一个 `execute_batch` 的 submit 不经 review 直接执行。

**`src` 后端测试(pytest):**
- reply 副作用矩阵:submit+true → `set_bohrium_submit_confirmation(sid, user_id, False)` 调一次;submit+false/缺省 → 不调;reject+true → 不调;kind=ask_question → 不调;set 返回 False → reply 仍 200;重复 reply(not_pending)→ 不触发副作用。
- GET source:`required=null → {required:null, source:'default'}`;`true/false → source:'session'`。

## 12. 前后端契约对账

| 前端期望 | 后端本方案 | 落点 |
|---|---|---|
| reply 带 `disable_future_confirmation` | (b) 解析进 decision、(d) 解析写库 | `submit_approval_gate.py`、`chat_api.py` |
| 关开关「原子、无半失败」 | 改为收到回复即落库 + best-effort 降级(已确认) | (d) |
| 本会话后续提交立即放行 | 本次 run:(c) 保留 gate + skip flag 跳过弹窗;下次 run:(d) 写 session=false 经覆盖语义放行 | (c)(d) |
| hydrate 回读 `{required, source}` | (e) GET 补 source(session/default) | `models/chat.py`、`chat_api.py` |
| 乐观更新 `{required:false, source:'session'}` | 下次 hydrate 一致返回 | (d)(e) |

## 13. 待办与对齐结论

**跨文档一致性:** 批次截断 spec §4.2 字面把叠加实现描述为「gate 延迟到下一批清」(方向 A);本方案采用方向 B2(保留 gate + skip flag)以同时满足纯关闭确认场景与 resubmit guard 优先级。落 plan 时应回头把 §4.2 的实现注记更新为「由关闭确认 plan 采用 skip flag 实现,gate 保留,截断与 resubmit guard 均优先于跳过弹窗」,使两份文档对实现机制描述一致(可观察语义「截断优先」不变)。

**待前端知会:**
- 「关开关」实际落点是收到回复同步写库(非「提交作业的同一步」);提交即便后续失败,偏好也已记下。前端乐观更新语义与此一致,无需改动。
- 「本会话后续提交立即放行」由后端两级保证(本次 run 进程内 + 下次 run DB),弹不弹仍由后端事件决定,前端继续「不伪造、由后端事件驱动」即可。
