# Bohrium submit 用户确认与参数审阅设计（建在通用交互底座之上）

> 日期：2026-06-19
> 状态：delta 设计稿，建在 `2026-06-18-interaction-bridge-migration-design.md` 的通用交互底座之上；supersede `2026-06-17-bohrium-submit-review-design.md`。待 writing-plans 拆分实现计划。
> 范围：把 Bohrium submit review 重做成**交互底座的第二个使用者**。传输层（pending registry、per-request reply key、active 守卫、reply 端点、`interaction_*` 事件信封、Lua 原子）一律**引用底座、不再自造**；本文档只详述 Bohrium 专属语义与接入方式：`SubmitReviewProvider`、`SubmitApprovalGate` 适配器、runner 串行闸门、四层参数、两级 validation、前端上报文件变更、ToolResult 合同、重提交护栏。

## 0. 与既有文档的关系

- **supersede 2026-06-17**：2026-06-17 写在交互底座之前，它在 §5.8 / §8 自造了一整套 per-request 传输设施。那套现已被 2026-06-18 底座吸收，本文档取代之。2026-06-17 仅作为 Bohrium 语义的来源被引用。
- **硬依赖 Phase 1**：本设计是 Phase 2，硬前置是 Phase 1（2026-06-18 迁移）先落地——`InteractionBridge`、`Interaction*Event`、per-request DAO + 两个 Lua、通用 `POST /chat/sessions/{session_id}/interactions/{request_id}/reply` 端点、`human_interaction_active:{session_id}` 守卫。Phase 1 未落地前，本设计不可独立实现。
- **delta 原则**：凡传输层概念，本文档只引用底座对应物（见 §1 对照表），不重述其内部机制；凡 Bohrium 语义，本文档自洽详述。

## 1. 设计基线：底座提供什么，本设计不再造什么

| 2026-06-17 自造的传输概念 | 2026-06-18 底座提供的对应物 | 本设计动作 |
|---|---|---|
| §5.8 per-request reply key `interaction_reply:{request_id}` | 底座同名 key + `blpop_interaction_reply(request_id)` | 引用，不造 |
| §8.1 pending registry `human_interaction:{request_id}` | 底座 hash registry + `write/read_pending_interaction` | 引用，不造 |
| §5.8 active interaction lock | 底座 `human_interaction_active:{session_id}`（SETNX 守卫 + 取消定位） | 引用，不造 |
| §8.2 自定义 reply 端点（带 `runs/{run_id}`） | 底座通用 `POST /chat/sessions/{session_id}/interactions/{request_id}/reply` | 复用，去掉 run_id |
| §8.3 worker 等 reply / timeout / cancel | 底座 `InteractionBridge.request(kind, request_id, payload, timeout)` | 经 gate 复用 |
| §6.2 自定义 `submit_review` SSE 事件 | 底座 `InteractionRequestEvent`（`kind="submit_review"`） | 寄生信封，零新增事件类型 |
| state 仲裁、迟到/重复 reply 409 | 底座 `answer_pending_interaction` / `finalize_interaction`（Lua 原子） | 引用，不造 |

底座契约（Phase 1 已定，本设计据此接入）：

```python
# matmaster/integration/interaction_bridge.py（Phase 1）
class InteractionBridge:
    async def request(self, *, kind: str, request_id: str,
                      payload: dict, timeout_seconds: int | None = None) -> dict:
        """发起交互并阻塞等待 reply payload。
        raise InteractionBusyError / TimeoutError / asyncio.CancelledError。"""
```

底座对内层 `payload` **不透明**：只搬运 + 等待 + 按 `(kind, request_id)` 配对。submit_review 的 payload 形状与解析由本设计的接入层（gate）负责。

## 2. 背景

承接 2026-06-17 §2：`BohriumTool` 的 submit 路径（`matmaster/tools/builtin/bohrium_tool/tool.py`）会调用 Bohrium `job/create`、上传输入、`job/add`，是高成本外部副作用。一旦执行，平台资源已占用，错误的 `image` / `cmd` / `machine` 或输入文件都造成浪费。本设计在 submit 真正产生副作用前引入**可选的**人类确认点，让用户确认/拒绝、改 submit 参数、并通过前端文件能力改 `input_dir` 内文件，且让最终 `tool_result` 明确告知 agent 用户的决定与改动。

与 2026-06-17 的唯一结构性差异：这次不自建传输，而是接入已落地的通用交互底座。

## 3. 目标与非目标

### 3.1 目标

承接 2026-06-17 §3.1，并明确：

- 在 `Bohrium(action="submit")` 真正调用 `job/create` / 上传 / `job/add` 前暂停，等待用户确认（仅当 opt-in 且 gate 端口存在）。
- 确认后用最终参数继续 submit；拒绝/超时/不可用则不产生外部副作用，返回模型可见的 `ToolResult(status="blocked")`，并在同 task 内阻止对同一作业的自动重提交。
- 用户对 submit 参数的修改进入 `content.review.parameter_changes`；对 `input_dir` 内文件的修改进入 `content.review.input_file_changes`；文件正文绝不进 request / reply / tool_result / payload。
- 审计摘要进入 `payload.bohrium_submit_review`（带上限）。
- **复用通用交互底座**完成 API / Worker 分离的确认往返；不重复实现传输。
- 不通过 `run_meta` 传服务能力，不把用户交互 callback 塞进 metadata。

### 3.2 非目标

承接 2026-06-17 §3.2，并新增：

- **不重新实现传输层**：pending registry / reply key / active 守卫 / reply 端点 / `interaction_*` 事件 / Lua 一律引用 Phase 1 底座。
- **不修改底座**：本设计是底座之上的纯接入层；不为 submit review 改底座的 DAO / 事件 / 端点行为（含底座对 Redis 异常吞掉返回 None/False 的现状，见 §5.5）。
- 不由后端读取、展示、diff 或写入 `input_dir` 内文件内容；不在 hook payload 或 tool_result 传文件内容或 patch。
- 不在工具内提供文件新建 / 删除 / 重命名能力；只表达对既有文件的内容修改。
- 不新增 run loop 业务状态，不停止 agent 自动循环来表达等待用户；`RunResultEvent.status` 不新增 `blocked`。
- 不做确认后到打包之间的 TOCTOU 兜底（无 snapshot / 无 revision / 无锁）。
- 不为旧事件 / 旧 tool_result 做内联兼容。

### 3.3 前置条件

- 文件就地编辑只在 `input_dir` 落在 `/share` 共享存储时支持（前端文件能力与 worker 解析 `input_dir` 读同一份存储）。worker pod 本地 workdir 前端够不着，不在 v1 文件编辑范围（仍可改参数）。
- Phase 1（2026-06-18 迁移）已落地（§0）。

## 4. 架构：三层接入 + 三角色

submit review 与 AskQuestion 对称地架在同一套底座上：

```
┌─ 接入层（每种交互各自接）──────────────────────────────────┐
│  AskQuestionTool（Phase 1）       │  SubmitApprovalGate（本设计）│
│  kind="ask_question"              │  kind="submit_review"        │
│  工具持有 bridge、工具内调用        │  runner 经 AgentRunPort 调用  │
│  构造 questions / 解析 answers     │  draft→payload / reply→decision│
└──────────┬─────────────────────────────────┬──────────────────┘
           │                                  │
           ▼            （同一个 bridge 实例）  ▼
┌─ 传输底座层（通用 · Phase 1 · payload 不透明）────────────────┐
│  InteractionBridge.request(kind, request_id, payload, timeout)  │
│   SETNX active → 写 registry → emit interaction_request          │
│   → BLPOP 自己的 reply key → 配对/超时/取消                       │
└──────────┬───────────────────────────────────────────────────┘
           ▼
┌─ Redis DAO 层（per-request key · Phase 1）────────────────────┐
└──────────────────────────────────────────────────────────────┘
```

三个角色，干净分工：

- **`SubmitReviewProvider`（BohriumTool 侧）**：Bohrium 专属知识。`build_review_draft(model_args) -> SubmitReviewDraft | None`、`normalize_execution_args(final_args) -> SubmitExecutionArgs`。默认值、cmd 重定向、`editable_fields`、`input_dir` 语义全在这里。挂载方式对齐现有 `instance.input_validator`（`tool_runner.py:271`），由 `ToolCompiler` 从工具读取挂到 `ToolInstance`。
- **`SubmitApprovalGate`（接入层适配器，包住 `InteractionBridge`）**：submit_review 这个 kind 的传输语义。把 `SubmitReviewDraft` 序列化成 interaction payload → `bridge.request(kind="submit_review", ...)` → 解析 reply payload 回 `SubmitReviewDecision` → 把底座异常（Busy / Timeout / Cancelled）映射成 `review_outcome`。它是 submit_review 的接入层，等价于 AskQuestionTool 之于 ask_question。
- **`FullToolRunner`（指挥）**：从 `runner_state` 读 `submit_approval_gate`（载体说明见 §5.2）与 `run_identity`；检测 `instance.submit_review_provider`，调 `build_review_draft`，非 None 调 `gate.review`；处理 blocked / approved / cancelled；对 approved 跑 worker 侧 reply 语义校验 + `normalize_execution_args` + structural / input / policy；跑重提交护栏。

**共享 bridge 实例**：gate 与 AskQuestionTool 共用 `agent_run_service` 构造的同一个 `InteractionBridge`。白送两个性质——共用进程内 `asyncio.Lock`、共用 session 级 `human_interaction_active` 的 SETNX 守卫——于是 ask 与 submit 天然互斥，不会同时挂起。

## 5. 核心设计决策

### 5.1 SubmitApprovalGate 作为 submit_review 接入层适配器

端口协议（**通用、提交语义**，不写死 Bohrium），基本平移 2026-06-17 §5.1，实现从"自造传输"改为"包 bridge"：

```python
@runtime_checkable
class SubmitApprovalGate(Protocol):
    async def review(self, request: SubmitReviewRequest) -> SubmitReviewDecision: ...
```

参考实现骨架（adapter）：

```python
class BridgeSubmitApprovalGate:
    def __init__(self, bridge: InteractionBridge) -> None:
        self._bridge = bridge

    async def review(self, request: SubmitReviewRequest) -> SubmitReviewDecision:
        payload = _draft_to_payload(request)          # submit_review 线上形状
        try:
            reply = await self._bridge.request(
                kind="submit_review",
                request_id=request.request_id,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
            )
        except InteractionBusyError:
            return SubmitReviewDecision(user_decision=None, review_outcome="busy")
        except TimeoutError:
            # 接入层负责发 timeout（仿 AskQuestionTool）。经 Phase 1 实际选择的
            # bridge.emit 薄方法或其 event_sink 发出（Phase 1 计划 Task 4 标为二选一）。
            await self._emit_timeout(request.request_id)
            return SubmitReviewDecision(user_decision=None, review_outcome="timeout")
        except asyncio.CancelledError:
            # run stop 的 cancel 哨兵。不向上抛：串行阶段无 _execute_one 的 cancel
            # 捕获，抛出去会成 generator 异常、得不到干净 cancelled 终态（§5.5）。
            return SubmitReviewDecision(user_decision=None, review_outcome="cancelled")
        return _reply_to_decision(reply)               # decision/submit_arguments/file_changes
```

- 唯一消费者是 `FullToolRunner`。工具与 gate 都不直接依赖 Redis / SSE / API / reply queue（底座屏蔽）。
- `request` / `decision` 用明确字段的 dataclass / Pydantic model，不带 `extra` / `metadata` / `dict[str, Any]` 兜底字段。
- timeout 由 gate（接入层）emit `interaction_timeout`，与 AskQuestionTool 捕获 `TimeoutError` 后 emit 对称（Phase 1 计划 Task 4）；底座本身不知道该发什么 kind 的 timeout。
- `request_id` 用 `sr_` 前缀（AskQuestion 用 `aq_`），底座对前缀无感；由 runner 在决定 review 时生成，便于审计 / 护栏关联。

### 5.2 opt-in：gate 存在即启用，不存在即放行

承接 2026-06-17 §5.2 的 opt-in 语义；载体按 P1-2 核实修正（见下）：

- `InteractionBridge` 对所有顶层 run 都注入（AskQuestion 需要），不变。
- `SubmitApprovalGate` **仅当 opt-in 且顶层 run** 时由服务层构造（包共享 bridge），以 `AgentRunPorts.submit_approval_gate` 为**规范归属**（与 `bohrium_job_ledger` 一致）。
- **载体修正（P1-2 核实）**：`FullToolRunner` 当前构造（`exp.py:391-399`）不接 `AgentRunPorts`，串行阶段够不着 `ports`。因此 `exp.py` 读 `ctx.request.ports.submit_approval_gate` 与 `run_identity`（`AgentKernelSpec.run_identity`，`runtime.py:69`），经 `runner_state.set(...)` 送进串行阶段——完全比照已有的 `figure_upload_config` 注入（`exp.py:385-388`），不改 runner 构造签名。runner 串行阶段从 `runner_state` 读 gate；**读不到即正常放行**。
- 放行同时覆盖：未开启确认、子 agent（spawn，`exp.py:742` 已为 None）、评测、cron、devshell。放行是闸门唯一启用条件的自然否定，不是散落的内联兜底（符合 CLAUDE.md）。
- **opt-in 开关两级解析**：`submit_confirmation_enabled` 有用户全局偏好与 session 级配置两层。`AgentRunService` 解析 effective 值 = session 显式设置优先，否则回落用户全局偏好，否则默认关；为真且顶层 run 才构造并填 gate。审计 payload 的 `session_id` 取自 `run_identity.session_id`。

### 5.3 契约寄生通用信封（零新增事件 / 端点）

submit_review 不引入任何自定义事件类型或端点，全部寄生底座的 `interaction_*` 信封，Bohrium 专属内容下沉进 `payload`（形状见 §7.3 / §7.4）。

- 请求：通用 `InteractionRequestEvent`，`kind="submit_review"`，draft 在 `payload`。注意底座 `EventBase` 不带 `session_id`（Phase 1 计划 Task 2），事件顶层只有通用字段。
- reply：通用 `interaction_reply`，`kind="submit_review"`，决定在 `payload`。
- timeout：通用 `interaction_timeout`，`kind="submit_review"`。
- 端点：底座统一的 `POST /chat/sessions/{session_id}/interactions/{request_id}/reply`，body `{kind, payload}`，**无 `run_id` 段**。

### 5.4 reply 语义校验下移 worker 侧

底座 API 对内层 payload 不透明，只做三件通用事：`kind` 与 registry 匹配、`session_id` 匹配、总字节 `_MAX_REPLY_PAYLOAD_BYTES = 256 KiB`（Phase 1 计划 Task 5）。

因此 2026-06-17 §7 / §8.2 那批 **Bohrium 专属 reply 校验从 API 下移到 worker 侧**——由 `SubmitApprovalGate` 解析 reply 后、`FullToolRunner` 接手时执行：

- `reported_input_file_changes` 路径边界（`commonpath` 而非 `startswith`、禁 `..` / 绝对 / NUL / 超长、symlink 不跟随逃逸）。
- `lines` 格式（仅 `N` 或 `N-M` 且 `M ≥ N` 的逗号分隔串）。
- 条数 / 单条体积上限（§7.5）。

后果：API 收下 reply（200）只代表送达；文件变更若非法，由 runner 返回 `blocked(invalid_final_arguments)` 告诉 agent，而非 API 层 4xx。这吻合 2026-06-18 §12「内层 schema 校验下放接入层」，并把通用端点与 Bohrium 细节解耦。256 KiB 总上限仍在 API（通用），保留。

### 5.5 异常映射与 outcome 塌缩

gate 把底座异常映射成 `review_outcome`：

| 底座行为 | review_outcome | runner 收尾 |
|---|---|---|
| `request` 正常返回，`payload.decision="submit"` | approved | 走严格校验 → 执行 |
| `request` 正常返回，`payload.decision="reject"` | rejected | blocked + 护栏 |
| `TimeoutError`（BLPOP 超时且 finalize 赢） | timeout | blocked + 护栏 |
| `InteractionBusyError`（active SETNX 被占） | busy | blocked + 护栏 |
| `asyncio.CancelledError`（stop 投取消哨兵） | cancelled | gate 转 outcome=cancelled → runner 产出 `ToolResult(status="cancelled")`（对称 `_execute_one`），run 由既有 cancel_token 收尾 |
| approved 后 worker 侧校验失败 | invalid_final_arguments | blocked + 护栏（runner 产出，非 gate） |

**cancel 闭合（P1-3 核实）**：cancelled 不抛 `asyncio.CancelledError` 出串行阶段。`_execute_one` 的 cancel 捕获（`tool_runner.py:382-394`）只包**并发阶段**；串行段抛出会冒泡过 `dispatch_tool_calls`（`agent.py:521` 只捕 `InvalidToolUsageDelta`）、`run_stream`（`agent.py:182` 对 CancelledError 只设 reason 后重抛、**不 yield `RunResultEvent`**），service 拿到 generator 异常而非干净 cancelled 终态。故 gate 把 cancel 转成 `ToolResult(status="cancelled")`（对称并发阶段的 `_execute_one`），run 由既有 cancel_token 在下一 checkpoint 收尾——与 AskQuestion 在并发阶段被取消的闭合路径归一。stop during submit_review 得 cancelled 终态为必测项（§12）。

**outcome 塌缩说明**：底座 DAO 对 Redis 异常吞掉返回 None/False。Redis 真挂时，表现为 SETNX 失败（→ `InteractionBusyError` → busy）或 BLPOP 返 None（→ `TimeoutError` → timeout）——底座**不报独立的 unavailable 信号**。故 2026-06-17 §6.4 细分的 `review_unavailable` 在底座之上实际塌缩成 busy / timeout。这不影响 agent 契约：busy / timeout / unavailable 收尾完全一致——blocked + 进护栏 + 告诉 agent 别自动重试。outcome 枚举保留（尽力而为用于审计 payload），但**面向 agent 的行为三者归一**，不为区分它们去改底座。

**v1 实际触达性**：gate 与 AskQuestionTool 共享同一个 bridge 的 `asyncio.Lock`；submit review 在 runner **串行**阶段 await（阻塞整批），AskQuestion 在其后**并发**执行阶段才跑——同一个 run 内两者时间错开，加上 session 单 run，`InteractionBusyError` 在 v1 基本不会因同 run 竞争触发。active SETNX 是跨进程安全网，留着但不期待 v1 发火。

### 5.6 闸门在串行校验阶段 await

承接 2026-06-17 §5.6：`gate.review` 是 `FullToolRunner.execute_batch()` 串行阶段里的一个 `await`，放在 catalog lookup / 识别 submit 之后、structural validation 之前。由此自然得到两个性质，无需任何新状态：

1. 人审等待期间整批**无任何工具执行**（执行都在串行阶段之后的 `asyncio.gather`），不存在 submit 与写文件并发竞态。
2. 用户改后的 `execution_arguments` 天然流过现有 structural / input / policy 校验。

不引入 prefix-scan / 新 deferred 状态：submit 经审批照常进入 `approved` 并在 gather 执行。唯一小代价：模型若在同一轮里既写文件又 submit（极少），review 可能看到写之前的 `input_dir` 状态——落在「文件用户自负」范围（§3.2、§6 引 2026-06-17 §5.10）。

### 5.7 reject / timeout / unavailable / busy = blocked + 同 task 重提交护栏

承接 2026-06-17 §5.7，不新增 run loop 状态。靠两点防模型自动重试：

1. 返回 `ToolResult(status="blocked")`，`content` 写明「用户已拒绝 / 本次未获确认，不要重新提交，请总结或转去做其它工作」，并携带 review record。
2. **同 task 重提交护栏（两条签名，P2-4 核实）**：被拒 submit **同时记两条**关键字段签名（`input_dir` / `job_name` / `image` / `cmd` 规范化后哈希，2026-06-17 §5.7.1）——`model_arguments` 签名与用户 `final_arguments` 签名——存入 `runner_state` 的 set。两条都记，是因为用户改参后拒绝时模型下一轮可能重试原始 args 或用户编辑版；命中任一即 blocked，不再发起 review。不同作业各自走 review。

模型不再发工具调用时，run 以现有 `completed` 自然收尾。`cancelled`（run stop）不进护栏（整 run 已停）。

### 5.8 run 维度统一 task_id

底座无 `run_id` 概念（2026-06-18 §5.6）。本设计的端点、审计 payload、护栏 run 维度统一用 `task_id`：

- 端点无 `run_id` 段。
- `payload.bohrium_submit_review` 用 `task_id`，不用 `run_id`。
- 重提交护栏的「同 run」= 同一 `runner_state` 生命周期 = 同一 task / invocation。

### 5.9 规范化：两条路径共享的幂等关口（P1-1 核实）

`normalize_execution_args` 是 submit 执行前**唯一且幂等**的规范化关口（默认值 + cmd `> log 2>&1` + required 校验），两条路径都经它，避免 opt-out 退化：

- **opt-out**（未开启确认 / 子 agent / 评测 / cron / devshell）：走不到 review draft。工具在 `_submit` 入口对 model_args 调 `normalize_execution_args`，取代当前散在 `_submit` body（`tool.py:462-464` 默认值）与 `submit_job_via_runtime`（`tool.py:94-96` cmd 追加）里的隐式逻辑，保证主路径不丢默认值与日志重定向。
- **opt-in**：runner 对用户 `final_arguments` 调 `normalize_execution_args` 产出 `execution_arguments` 供严格 validation/policy，并作为有效参数交给 `_submit`；`_submit` 入口再次 normalize 为幂等 no-op。
- `build_review_draft` 是 review 专用的**展示型**规范化（宽松、允许缺 required、产出 `draft_issues`），其默认值与 cmd 重定向与 `normalize_execution_args` **共享同一 canonicalize helper**，保证 review UI 展示的 cmd 与最终 `job/add` 收到的一致。
- `submit_job_via_runtime` 与 `_submit` body 不再各自隐式填默认 / 追加 cmd，只保留 defensive 检测报错。

> 幂等性是硬要求：`normalize(normalize(x)) == normalize(x)`，使 opt-in 路径 runner 与 `_submit` 的双次调用安全。

## 6. 平移不变的硬核（继承 2026-06-17，不动）

下列与传输层无关，是 submit review 的语义本体，直接继承 2026-06-17 对应章节：

- **两级 validation**（§5.3）：draft 宽松、允许缺 required（记 `draft_issues`）；确认后 `normalize_execution_args` 严格、不改语义字段。
- **四层参数 + 三类 diff**（§5.4）：`model_arguments` / `review_draft_arguments` / `final_arguments` / `execution_arguments`；`content.review` 只暴露 `parameter_changes`(=user_parameter_changes) + `input_file_changes`，`normalization_changes` / `execution_normalization_changes` 只进 payload 审计。
- **cmd 重定向与默认值由共享 canonicalize 承担**（§5.9）：`build_review_draft` 与 `normalize_execution_args` 共享同一 canonicalize（`> log 2>&1` + 默认值），两条路径都补；`submit_job_via_runtime`（`tool.py:94-96`）与 `_submit` body 不再隐式追加 / 填默认，只 defensive 检测。用户看到的 cmd 就是平台提交的 cmd。
- **前端上报文件变更，无 TOCTOU 兜底**（§5.10）：后端不读正文、不算 diff、不验证是否真改变；行号由前端上报。确认后到打包之间文件再变由用户自负。
- **ToolResult 合同**（§10）：详见本文档 §9。
- **POST hook finalizer**（§5.9）：`attach_submit_review_record` → 通用 POST_TOOL_CALL rewrite / observe → `enforce_submit_review_contract` 补回 review 合同。
- **BohriumTool 调整**（§11）：实现 `SubmitReviewProvider`；`_submit()` 入口经 `normalize_execution_args` 规范化（opt-out 对 model_args、opt-in 对已规范化的 execution_args 幂等 no-op，§5.9），body 不再散落填默认 / 追加 cmd；`submit_job_via_runtime()` 不再改已确认的 cmd。
- **ToolCallEvent.arguments 不回写**（§9.2）：仍是模型原始参数；改动经 `content.review` 表达，完整提交参数经 `payload.bohrium_submit_review.execution_arguments` 表达。

2026-06-17 v3 已砍掉的（run-loop halt、`RunResultEvent.status="blocked"`、batch barrier、snapshot / revision、独立 `ReviewAuditStore`）**保持砍掉**。

## 7. 数据模型

### 7.1 Provider helper（BohriumTool 侧）

```python
def build_review_draft(model_args: Mapping[str, Any]) -> SubmitReviewDraft | None:
    """宽松、无副作用、可展示。仅 action=="submit" 返回非 None。
    负责字段筛选、默认值、基础类型规整、cmd 日志重定向、draft_issues、normalization_changes。"""

def normalize_execution_args(args: Mapping[str, Any]) -> SubmitExecutionArgs:
    """严格、幂等、无副作用、用于真实执行。两条路径都走（§5.9）：opt-out 对 model_args、
    opt-in 对用户 final_args。与 build_review_draft 共享同一 canonicalize（默认值 + cmd
    `> log 2>&1`），不改用户已确认的语义字段。"""
```

draft 校验规则（继承 2026-06-17 §6.1）：`arguments` 须是 object、`action=="submit"` 可识别、只保留 submit 相关字段；`cmd` / `image` / `machine` / `job_name` / `input_dir` 有最大长度；`disk_size` 宽松接受 int 或数字字符串；缺 `input_dir` / `image` / `cmd` 记 `draft_issues` 允许前端补；默认 `machine="c32_m128_cpu"` / `job_name="matmaster-job"` / `disk_size=50`；`cmd` 在 draft 阶段追加 `> log 2>&1`。

### 7.2 Gate 接口数据类

```python
@dataclass
class SubmitReviewDraft:
    model_arguments: dict
    review_draft_arguments: dict
    normalization_changes: dict
    draft_issues: list
    editable_fields: list[str]
    input_dir: str
    file_edit_mode: str            # "live_reported"

@dataclass
class SubmitReviewRequest:
    request_id: str                # "sr_xxx"，runner 生成
    tool_name: str                 # "Bohrium"
    tool_call_id: str
    task_id: str
    session_id: str                # 取自 run_identity，进审计 payload（§5.2）
    draft: SubmitReviewDraft
    timeout_seconds: int | None = None

@dataclass
class SubmitReviewDecision:
    user_decision: str | None      # "submit" | "reject" | None
    review_outcome: str            # approved|rejected|timeout|cancelled|review_unavailable|busy
    final_arguments: dict | None = None
    reported_input_file_changes: list | None = None
```

`invalid_final_arguments` 不由 gate 产出——它是 runner 在 approved 之后做 worker 侧校验失败时产出的 outcome（§5.4 / §5.5）。

### 7.3 submit_review 的 inner payload 与 public envelope（P2-5 核实）

**边界**：`_draft_to_payload()` 只构造 **inner payload**（gate 传给 `bridge.request(payload=...)` 的内层 dict）；底座 `InteractionBridge` 再把它装进 `InteractionRequestEvent` 信封。实现时勿把整个事件对象当 payload 构造，勿漏 `source`（`EventBase.source` 必填无默认）。

(a) gate 构造的 **inner payload**（`_draft_to_payload(request)` 返回值）：

```json
{
  "schema_version": 1,
  "tool_name": "Bohrium",
  "tool_call_id": "call_xxx",
  "model_arguments": {
    "action": "submit", "input_dir": "/share/case_001",
    "image": "old-image", "cmd": "python run.py"
  },
  "review_draft_arguments": {
    "action": "submit", "input_dir": "/share/case_001",
    "image": "old-image", "cmd": "python run.py > log 2>&1",
    "machine": "c32_m128_cpu", "job_name": "matmaster-job", "disk_size": 50
  },
  "normalization_changes": {
    "cmd": { "from": "python run.py", "to": "python run.py > log 2>&1" },
    "machine": { "from": null, "to": "c32_m128_cpu" },
    "job_name": { "from": null, "to": "matmaster-job" },
    "disk_size": { "from": null, "to": 50 }
  },
  "draft_issues": [],
  "editable_fields": ["input_dir", "image", "cmd", "machine", "job_name", "disk_size"],
  "input_dir": "/share/case_001",
  "file_edit_mode": "live_reported"
}
```

(b) 底座装出的 **public SSE envelope**（`InteractionRequestEvent`，`source` 必填、`session_id` 不在事件里——§5.3）：

```json
{
  "type": "interaction_request",
  "source": "System",
  "kind": "submit_review",
  "request_id": "sr_xxx",
  "task_id": "task_xxx",
  "expires_at": "2026-06-19T12:30:00Z",
  "payload": { "...上面的 inner payload..." }
}
```

`draft_issues` 示例：`[{ "field": "image", "code": "missing_required_field", "message": "image is required before submit." }]`。

### 7.4 interaction_reply payload（kind="submit_review"）

底座通用 body `{kind, payload}`；submit_review 专属字段在 `payload`。`parameter_changes` 不由前端传，由 runner 据 `review_draft_arguments` 与 `final_arguments`（= `submit_arguments`）计算。

确认提交：

```json
{
  "kind": "submit_review",
  "payload": {
    "decision": "submit",
    "submit_arguments": {
      "action": "submit", "input_dir": "/share/case_001",
      "image": "new-image", "cmd": "python run.py --ecut 600 > log 2>&1",
      "machine": "c64_m256_cpu", "job_name": "case-001-reviewed", "disk_size": 80
    },
    "reported_input_file_changes": [
      { "relative_path": "input.inp", "lines": "12-15,30" },
      { "relative_path": "run.sh", "lines": "8" }
    ]
  }
}
```

拒绝提交：`payload.decision="reject"`，`submit_arguments` 是用户当时草稿（不提交，仍用于生成 `user_parameter_changes`），可带 `reported_input_file_changes`。

### 7.5 reported_input_file_changes

承接 2026-06-17 §6.5 / §7。只表达对 `input_dir` 内既有文件的内容修改；新建 / 删除 / 重命名不在工具职责内。

- 前端传：`{ "relative_path": "subdir/input.inp", "lines": "12-15,30" }`。
- 后端规范化后（仅进 payload 审计）：`{ "path": "/share/case_001/subdir/input.inp", "relative_path": "...", "lines": "..." }`。
- 路径规则：最终 `submit_arguments.input_dir` 为基准；`relative_path` 优先、POSIX 校验（禁空 / 绝对 / `..` / NUL / 超长）；同传 `path` 则校验一致；`commonpath` 校验在 input_dir 内；symlink resolve 后仍须在 input_dir 下；只处理内容修改。
- **metadata 校验（不读正文，Open-Q2 核实）**：每条 reported 路径须 `stat` 存在、是 regular file（拒目录 / 设备等特殊文件 / symlink loop / 非常规文件）、resolve 后仍在 input_dir 内；任一不满足 → `invalid_final_arguments`，避免告诉 agent 改了不存在的文件。这是 metadata 校验，不读文件内容，与「后端不读正文」不冲突。
- `lines`：仅 `N` 或 `N-M`（`M ≥ N`）逗号分隔；非法拒绝，不解析文件内容。
- 大小：`content.review.input_file_changes` ≤20 条，超限附 `input_file_changes_truncated: true`，精确条数留 payload；`payload...reported_input_file_changes` ≤200 条；单条 `lines` ≤200 字符（超限按行段截断并标记）；reply 总字节 256 KiB 由底座 API 兜（§5.4）。
- **执行位置**：上述语义校验在 worker 侧 gate / runner 执行（§5.4），不在底座 API。

## 8. Runner 执行流程

```text
LLM tool call: Bohrium submit
  -> ToolCallEvent 记录 model_arguments（不回写）

  -> 串行阶段:
       raw JSON guard / catalog lookup
       通用 PRE_TOOL_CALL observe / intercept（看 model_arguments；hook block 则不进 review、无副作用）

       闸门判定: instance 有 submit_review_provider 且 runner_state 有 submit_approval_gate?
         否 -> 放行（opt-out / 子 agent / 评测 / cron / devshell）
                （opt-out submit 仍由 _submit 入口 normalize_execution_args 补默认+cmd，§5.9）
         是 -> provider.build_review_draft(model_args)
                 None -> 放行（非 submit）
                 draft ->
                   重提交护栏命中? -> blocked（不发 review）
                   request_id = "sr_" + uuid；记审计关联
                   gate.review(SubmitReviewRequest{draft, request_id, tool_call_id, task_id, timeout})
                     |  （gate 内部经 InteractionBridge.request 走底座；
                     |    底座: SETNX active -> 写 registry -> emit interaction_request
                     |          -> BLPOP interaction_reply:{request_id} -> 配对/超时/取消）

       decision.review_outcome:
         rejected / timeout / busy:
           ToolResult(status="blocked") + content.review + payload + meta.block_reason
           记两条重提交签名（model_arguments + final_arguments，§5.7）
         cancelled:
           ToolResult(status="cancelled")（对称 _execute_one；不抛 CancelledError 出串行阶段）
           run 由既有 cancel_token 在下一 checkpoint 收尾
         approved:
           worker 侧校验 reported_input_file_changes（路径/metadata 存在性+类型/lines/数量，§5.4/§7.5）
             非法 -> ToolResult(status="blocked", outcome=invalid_final_arguments) + 记签名
             合法 -> execution_arguments = normalize_execution_args(final_arguments)
                     structural validation / input validation / capability policy（针对 execution_arguments）
                       invalid -> ToolResult(status="blocked") + review record
                       valid   -> 放入 approved（携带 review record）

  -> 并发阶段 (gather / _execute_one):
       BohriumTool._submit(execution_arguments) -> job/create -> upload -> job/add
       attach_submit_review_record()
       通用 POST_TOOL_CALL rewrite / observe
       enforce_submit_review_contract()
       ToolResultEvent / ToolMessage
```

**Hook 顺序**：不改现有 PRE_TOOL_CALL 语义（始终看 model 原始 args）。gate 不是 hook，位于 PRE hook 之后、严格校验之前。structural validation / input validator / capability policy / scheduler / executor 看的都是 `execution_arguments`。第一版不新增 `PRE_EFFECTIVE_TOOL_CALL` 事件。

## 9. ToolResult 合同

承接 2026-06-17 §10，形状不变（与传输无关）。

### 9.1 成功提交

`ToolResult.content`（JSON 字符串）解析后：

```json
{
  "success": true, "job_id": "12345", "status": "Submitted", "use_sandbox": true,
  "review": {
    "parameter_changes": {
      "image": { "from": "old-image", "to": "new-image" },
      "cmd": { "from": "python run.py > log 2>&1", "to": "python run.py --ecut 600 > log 2>&1" }
    },
    "input_file_changes": [
      { "relative_path": "input.inp", "lines": "12-15,30" },
      { "relative_path": "run.sh", "lines": "8" }
    ]
  }
}
```

`review` 只保留 agent 决策必需的两类增量，各自「有才出现」；两者皆空时 `review` 整块省略。完整 `execution_arguments`、规范化 diff、文件来源等审计信息只进 §9.3 payload。

### 9.2 用户拒绝

`ToolResult.status="blocked"`，content JSON：

```json
{
  "success": false, "status": "UserRejected",
  "message": "用户拒绝了本次 Bohrium 提交。请不要重新提交本作业，可总结当前进展、转去做其它工作，或结束本轮等待用户继续反馈。",
  "review": {
    "parameter_changes": { "cmd": { "from": "python run.py > log 2>&1", "to": "python run.py --dry-run > log 2>&1" } },
    "input_file_changes": [ { "relative_path": "input.inp", "lines": "12-15" } ]
  }
}
```

`meta`（runner 内部信号，不进模型消息 / 不进 public payload）：`{ "block_reason": "UserRejected", "layer": "submit_approval_gate" }`。timeout / busy / invalid_final_arguments 同构，仅 `status` / `message` / `block_reason` 文案不同。

### 9.3 Payload 审计结构

```json
{
  "bohrium_submit_review": {
    "schema_version": 1,
    "request_id": "sr_xxx", "session_id": "sess_xxx", "task_id": "task_xxx", "tool_call_id": "call_xxx",
    "review_outcome": "approved", "user_decision": "submit",
    "model_arguments": {}, "review_draft_arguments": {}, "final_arguments": {}, "execution_arguments": {},
    "normalization_changes": {}, "user_parameter_changes": {}, "execution_normalization_changes": {}, "changed_fields": [],
    "reported_input_file_change_count": 37, "reported_input_file_changes_truncated": true,
    "reported_input_file_changes": [], "input_file_changes_source": "frontend_reported",
    "execution_attempted": true, "external_effect_started": true,
    "job_create_attempted": true, "job_id": "12345",
    "input_upload_attempted": true, "job_add_attempted": true
  }
}
```

部分外部副作用（如 `job/create` 成功但上传失败）：`status` 可能为 `error`，payload 仍记 `external_effect_started=true` / `job_create_attempted=true` / `job_id` / `input_upload_attempted=true` / `job_add_attempted=false`。v1 不含 snapshot / revision / digest 字段。run 维度字段用 `task_id`（§5.8）。

## 10. 接入底座的契约对照表（2026-06-17 → 本设计）

| 维度 | 2026-06-17（自造） | 本设计（接入底座） |
|---|---|---|
| 等待传输 | 自建 worker 等 reply | `gate` 包 `InteractionBridge.request` |
| reply 端点 | `POST .../runs/{run_id}/interactions/{request_id}/reply` | `POST /chat/sessions/{session_id}/interactions/{request_id}/reply` |
| reply body | `{kind, decision, submit_arguments, reported_input_file_changes}` | `{kind:"submit_review", payload:{decision, submit_arguments, reported_input_file_changes}}` |
| 请求事件 | 自定义 `submit_review` | 通用 `interaction_request` + `kind="submit_review"` |
| timeout 事件 | 自定义 | 通用 `interaction_timeout` + `kind="submit_review"` |
| pending / reply key / active | 自建 | 底座 per-request key + active 守卫 |
| reply 语义校验位置 | API | worker 侧 gate / runner（§5.4） |
| run 维度 | `run_id` | `task_id`（§5.8） |
| unavailable 信号 | 独立 outcome | 塌缩成 busy / timeout，行为归一（§5.5） |
| cancel 闭合 | （未细化） | gate→cancelled→`ToolResult(cancelled)`，cancel_token 收尾（§5.5） |
| gate / run identity 载体 | 自造端口直连 | `AgentRunPorts` 归属 + `ToolRunnerState` 送达串行阶段（§5.2） |
| submit 规范化 | 仅 draft / `_submit` | `normalize_execution_args` 幂等关口，opt-out/opt-in 共享（§5.9） |

## 11. 依赖与实施顺序

**硬前置**：Phase 1（2026-06-18 迁移）落地。

**Phase 2 实施顺序**（相比 2026-06-17 §12，传输层两步消失，是瘦身来源）：

1. submit 参数模型 + provider helper：四层参数类型、`SubmitReviewDraft` / `SubmitExecutionArgs`、共享 canonicalize（默认值 + cmd 重定向）驱动的幂等 `build_review_draft` / `normalize_execution_args`（§5.9）、`SubmitReviewProvider` 协议；`_submit` 入口改走 `normalize_execution_args`、移除散落默认/cmd 逻辑；`ToolCompiler` 挂 `submit_review_provider`（仿 `input_validator`，`tool_runner.py:271`）。
2. `SubmitReviewRequest`（含 `session_id`）/ `SubmitReviewDecision` + `SubmitApprovalGate` 端口（包 `InteractionBridge` 的 adapter，emit timeout、cancel→outcome=cancelled）；`AgentRunPorts`（`runtime_ports.py:163-182`）增 `submit_approval_gate` 字段作规范归属。
3. 服务层 opt-in 注入：`AgentRunService` 解析 effective `submit_confirmation_enabled`（session 覆盖 user 全局、默认关，§5.2），顶层 run 且为真 → 构造 gate 包共享 bridge、填 `AgentRunPorts.submit_approval_gate`；`exp.py` 把该 gate 与 `run_identity` 经 `runner_state.set(...)` 送进串行阶段（比照 `figure_upload_config`，`exp.py:385-388`），不改 `FullToolRunner` 构造签名。
4. `FullToolRunner` 串行阶段：从 `runner_state` 读 gate；闸门 await（§5.6 / §8）；approved 走 worker 侧 reply 语义校验（路径/metadata/lines/数量，§5.4 / §7.5）→ `normalize_execution_args` → structural/input/policy；reject/timeout/busy/invalid → blocked + 两条重提交护栏；cancelled → `ToolResult(status="cancelled")`。
5. ToolResult augmentation：`attach_submit_review_record()` → POST hook → `enforce_submit_review_contract()`。
6. focused tests（§12）。

## 12. 测试计划

继承 2026-06-17 §13，**去掉归属 Phase 1 的 per-request reply key 隔离 / stale-duplicate 矩阵测试**（底座已覆盖），**新增 gate 异常映射与 worker 侧 reply 校验直测**。focused：

1. draft validation：缺 `image` 生成 `draft_issues` 仍可发 review；过长 `cmd`/`image` 被拒不进 review；cmd 重定向在 draft 阶段完成。
2. 参数分层：`model_arguments` 不被改；`review_draft_arguments` 含默认值与重定向；`final_arguments` 由 reply 决定；`_submit()` 收到 `execution_arguments`。
3. 闸门启用：gate 端口不存在时 Bohrium submit 正常放行；子 agent（spawn）无端口 → 放行。
4. gate 串行语义：人审等待期间无工具执行；用户改后的 `input_dir`/`cmd`/`machine` 经 structural validation 与 `_submit()` 生效。
5. **gate 异常映射（新）**：`bridge.request` 抛 `TimeoutError` → outcome=timeout + emit `interaction_timeout(kind=submit_review)`；抛 `InteractionBusyError` → outcome=busy；抛 `CancelledError` → outcome=cancelled（不向上抛）；正常返回 `decision=submit/reject` → approved/rejected。
6. reject 收尾：返回 blocked，不调用 `create_job`/`upload_input_archive`/`add_job`；run 不新增状态；**改参后拒绝时，模型下一轮重提原始 args 或用户编辑版都被两条签名护栏 blocked**；不同作业放行。
7. timeout / busy / invalid_final_arguments：均 blocked、不提交、进护栏。
8. **worker 侧 reply 校验（新）**：`../x`/`/absolute`/NUL/symlink escape 被拒（`commonpath`）；**不存在路径 / 目录 / symlink loop / 非常规文件经 metadata 校验被拒**；非法 `lines`（`5-2`、含字母）被拒，合法 `"12-15,30"` 通过；超 20 条 content 截断 + `input_file_changes_truncated`；超 200 条 payload 截断标记。
9. cmd hidden normalization：确认后的 cmd 不被 `_submit()`/`submit_job_via_runtime()` 隐式追加；review UI 展示的 cmd 与 `job/add` 收到的一致。
10. POST hook 破坏性 rewrite：删 `content.review`/`payload.bohrium_submit_review` 后 finalizer 恢复必要字段。
11. partial side effect：`job/create` 成功但上传失败时 payload 记 `external_effect_started=true` 与 job ref。
12. no file content leakage：request / reply / tool_result content / payload 都不含文件正文或 patch。
13. 契约寄生：submit_review 走通用 `interaction_request`/`interaction_reply`/`interaction_timeout`（kind 区分），不引入自定义事件类型；reply 经通用端点 `{kind, payload}` 解析。
14. **stop during submit_review（必测，P1-3）**：人审 await 期间 stop → cancel 哨兵唤醒 BLPOP → gate outcome=cancelled → `ToolResult(status="cancelled")`；run 得干净 cancelled 终态，**不**冒泡成 generator 异常。
15. **opt-out 不退化（回归，P1-1）**：gate 不存在时 submit 仍经 `_submit` 入口 normalize 得默认 `machine/job_name/disk_size` 与 `> log 2>&1`，与开启确认前行为一致。

验证命令第一版建议（按实际修改文件收窄/扩展）：

```bash
uv run --extra dev pytest \
  tests/matmaster/core/test_full_tool_runner.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/integration/test_submit_approval_gate.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py
```

## 13. 最终行为示例

### 13.1 用户确认并修改参数和文件

模型调用 `{ "action": "submit", "input_dir": "/share/case_001", "image": "old-image", "cmd": "python run.py", "machine": "c32_m128_cpu" }`；review draft 追加默认值与 `> log 2>&1`；用户在前端改 `image`→`new-image`、`cmd`→`python run.py --ecut 600 > log 2>&1`、`machine`→`c64_m256_cpu`，改 `input.inp` 与 `run.sh`，点确认。agent 看到（节选）：

```json
{
  "success": true, "job_id": "12345", "status": "Submitted", "use_sandbox": true,
  "review": {
    "parameter_changes": {
      "image": { "from": "old-image", "to": "new-image" },
      "cmd": { "from": "python run.py > log 2>&1", "to": "python run.py --ecut 600 > log 2>&1" },
      "machine": { "from": "c32_m128_cpu", "to": "c64_m256_cpu" }
    },
    "input_file_changes": [
      { "relative_path": "input.inp", "lines": "12-15" },
      { "relative_path": "run.sh", "lines": "3,8-9" }
    ]
  }
}
```

### 13.2 用户拒绝但保留草稿修改

用户改 cmd、改 `input.inp`，点拒绝。agent 看到 `status="blocked"` 的 content（§9.2），消息明确「不要重新提交本作业」。run 不新增状态；同 task 内若模型再次提交同一作业，被 §5.7 护栏直接 blocked。等待用户下一条消息继续。
