# Bohrium submit 用户确认与参数审阅设计

> 日期：2026-06-17
> 状态：三版设计稿，待实现计划拆分（writing-plans）。
> 范围：在 `Bohrium(action="submit")` 产生平台外部副作用前，引入**可选的**用户确认与参数审阅；用户可修改 submit 参数、并通过前端文件能力修改 `input_dir` 内文件；最终 `tool_result` 必须让 agent 明确知道用户确认 / 拒绝、哪些 submit 参数被改、哪些 `input_dir` 内文件被改。

## 1. 三版修订结论

二版（`BohriumSubmitApprovalGate` + run-loop halt + batch barrier + snapshot）方向上把 review 建模为 ToolRunner 内置的 typed human approval barrier，这一主判断成立且保留。但二版把范围扩到了三套全新基础设施，且偏离了若干已确认决策。三版在保留正确硬核的同时做收敛。

**保留二版的正确硬核：**

1. 用户确认是 RuntimePort（不是 `HookExecutor` handler）——依据 `AGENTS.md:24`。
2. 两级 validation：review 前宽松 draft 校验，确认后严格校验。
3. `cmd` 的日志重定向（`> log 2>&1`）必须在 review draft 阶段完成；用户看到的 `cmd` 必须就是平台真正提交的 `cmd`。
4. 四层参数语义：`model_arguments`、`review_draft_arguments`、`final_arguments`、`execution_arguments`。
5. API / Worker reply 用 per-request reply key + pending registry，不让多个 bridge 竞争同一条共享 list。
6. `POST_TOOL_CALL` rewrite 之后加 finalizer，保证 review 信息不被 hook 删除。
7. input 文件变化命名 `reported_input_file_changes`，明确是前端/用户报告，不是后端 diff；文件正文绝不进 request / reply / tool_result / payload。
8. payload / content 都有条数、摘要长度、字节数边界。

**按已确认决策修改：**

9. **闸门做成通用 plumbing + 工具声明 provider**，不写死 Bohrium。runner 只判定“工具是否声明了 submit review provider 且 approval gate port 是否存在”；`draft` 准备、`normalize`、`editable_fields`、`input_dir` 语义等 Bohrium 专属知识放进 `BohriumTool`。挂载方式对齐现有 `instance.input_validator`（`tool_runner.py:271`）。
10. **确认是用户可选项（opt-in）**：只有用户开启确认且为顶层交互 run 时，服务层才注入 approval gate port。**port 不存在 → 直接放行**，自然覆盖未开启、子 agent（spawn）、评测、cron、devshell。这不是主代码内联兜底，而是“可选特性未启用”的唯一判定条件。
11. **reject / timeout / review 不可用 = blocked 结果 + 同 run 重提交护栏**，不新增 run loop 状态、不新增 `RunResultEvent` 业务状态、不动 agent loop / SSE / history / 前端。被拒 submit 的签名记进 `runner_state`，同 run 内再次提交同一签名直接 blocked。

**砍掉（延后或不做）：**

12. 删二版 §5.6 整套 run-loop halt / `requires_user_input` 作为 run 控制信号 / `RunResultEvent.status="blocked"` / SSE·history·前端联动。核实事实：`events.py:138-144` 当前 `RunResultEvent.status` 仅 `completed | failed | cancelled`，三版**不新增** `blocked` 业务状态。
13. 删二版 §5.5 batch barrier 的 prefix-scan / defer / `DeferredByBohriumSubmitBarrier` 新状态。改为：**approval gate 作为一个 `await` 放在 runner 串行校验阶段**（见 §5.6）。
14. 删二版 §5.9 snapshot / `workspace_revision` / `input_archive_digest` / input_dir 写锁。核实事实：全仓不存在 revision / snapshot / freeze 概念，三版不为此造一套工作区版本系统。**确认后到打包之间的文件变化由用户自负**。
15. 删独立 `ReviewAuditStore`。v1 审计直接进 `ToolResult.payload`（带条数/字节上限），不另起持久化存储。

## 2. 背景

当前 `BohriumTool` 的 submit 路径（`matmaster/tools/builtin/bohrium_tool/tool.py`）：

1. agent 生成 `Bohrium(action="submit", input_dir=..., image=..., cmd=...)`。
2. `FullToolRunner` 做通用工具链路校验。
3. `BohriumTool._submit()` 校验必填参数，填充默认 `machine="c32_m128_cpu"`、`job_name="matmaster-job"`、`disk_size=50`（tool.py:462-464）。
4. `submit_job_via_runtime()` 解析 `input_dir`，打包或远端上传输入目录。
5. `submit_job_via_runtime()` 在 `cmd` 末尾缺少 `> log 2>&1` 时自动追加（tool.py:94-96）。
6. 调用 Bohrium `job/create`、上传输入、`job/add`。
7. 返回只含 `job_id`、提交状态、sandbox 标记的 `ToolResult.content`（JSON 字符串）。

这条路径缺少一个人类确认点。Bohrium submit 是高成本外部副作用：一旦 `job/create`、上传、`job/add` 执行，平台资源已被占用，错误的 `image` / `cmd` / `machine` 或输入文件都会造成浪费。

用户希望在 submit 前暂停（在用户开启确认时），让前端展示待提交信息，并允许用户：

- 确认或拒绝本次 submit。
- 修改 `cmd`、`image`、`machine`、`job_name`、`disk_size`、`input_dir` 等 submit 参数。
- 通过前端文件能力修改 `input_dir` 内的具体输入文件。
- 让最终 `tool_result` 明确告知 agent：用户是否确认、哪些 submit 参数被改、`input_dir` 内哪些文件被改。

## 3. 目标与非目标

### 3.1 目标

- 在 `Bohrium(action="submit")` 真正调用 `job/create` / 上传 / `job/add` 前暂停，等待用户确认（仅当用户开启确认且 approval gate port 存在）。
- 用户确认后，使用最终 submit 参数继续执行 Bohrium submit。
- 用户拒绝后，不调用 `job/create`、不上传、不调用 `job/add`，返回模型可见的 `ToolResult(status="blocked")`，并在同 run 内阻止对同一作业的自动重提交。
- 用户对 submit 参数的修改进入模型可见的 `ToolResult.content.review.parameter_changes`。
- 用户对 `input_dir` 内文件的修改进入模型可见的 `ToolResult.content.review.input_file_changes`。
- 文件内容不进入 review request / reply / tool_result content / payload；前端只回传路径和可选摘要。
- 审计摘要进入 `ToolResult.payload.bohrium_submit_review`（带上限）。
- 保持 API / Worker 分离：确认请求由 worker 发出，回复可由任意 API 实例接收，经 Redis per-request reply key 回到执行 run 的 worker。
- 不通过 `run_meta` 传服务能力，不把用户交互 callback 塞进 metadata。

### 3.2 非目标

- 不由后端读取、展示、diff 或写入 `input_dir` 内文件内容。
- 不在 hook payload 或 tool_result 中传输文件内容或 patch。
- 不新增主代码兼容兜底；不为旧事件 / 旧 tool_result 做内联兼容。
- 不改变 Bohrium `query` / `download` / `kill` / `list_images` / `list_machines` 语义。
- 不改 Bohrium job ledger 既有状态机；只在 submit 审计 payload 中记录外部副作用是否已开始。
- 不把通用 AskQuestion 工具当作 submit 确认闸门。
- 不让前端通过 reply 提交文件 patch；文件修改由前端文件能力提前完成，reply 只报告改了哪些文件及修改行号（行号同属前端报告，后端不算 diff）。
- **不在工具内提供文件新建 / 删除 / 重命名能力**；只表达对既有文件的内容修改，工具外的此类操作不感知、不负责。
- **不新增 run loop 业务状态**，不停止 agent 自动循环来表达“等待用户”（见 §5.7）。
- **不做确认后到打包之间的 TOCTOU 兜底**（无 snapshot / 无 revision / 无锁）。

### 3.3 前置条件

- 文件就地编辑只在 `input_dir` 落在 `/share` 共享存储时支持：前端文件能力与 worker 解析 `input_dir` 时读取的是同一份存储。worker pod 内的本地 workdir 前端够不着，不在 v1 文件编辑范围内（仍可改参数，只是不展示/编辑文件）。

## 4. 当前代码事实

### 4.1 HookExecutor 边界（`AGENTS.md:24`）

`AGENTS.md:24` 明文：`HookExecutor` 专指事件扩展系统（observe / intercept / rewrite 运行过程事件），**不得把需要返回业务数据或承担顺序屏障语义的服务端口伪装成 `HookExecutor` handler**。

并且 `hooks.py` 的 `emit_intercept` / `emit_rewrite` 对 handler 异常是 `except Exception: logger.warning`（吞掉）。提交确认是“阻塞等待 + 返回最终参数 + 严格错误语义 + 决定是否启动外部副作用”，三条全中。因此用户确认必须建模为 RuntimePort，而不是 hook handler。

### 4.2 ToolRunner 当前执行形态（`tool_runner.py`）

`FullToolRunner.execute_batch()` 是两阶段：

1. **串行**：对每个 tool call 依次做 catalog lookup、raw JSON guard、`base_args` deepcopy、`PRE_TOOL_CALL` observe/intercept（207-227）、cancel check、structural validation、input_validator（271）、capability policy；通过审批的放入 `approved`。
2. **并发**：`asyncio.gather` 执行 `approved`（326-346），每个走 `_execute_one`（scheduler → executor → normalize → `POST_TOOL_CALL` rewrite/observe，408-426）。

三版把 approval gate 放在串行阶段（见 §5.6），自然得到“人审等待期间无任何工具执行”，无需 batch barrier 新机器。

### 4.3 ToolCallData arguments 只读（`messages.py:42-73`、`tool_runner.py:151-155`）

`ToolCallData` 缓存 `arguments_json`，原地修改 `tc.arguments` 被约定禁止且会抛错。每层消费者把 arguments 当只读。因此用户改后的参数必须形成新的 `effective_args`，不改 `tc.arguments`。

### 4.4 Bohrium `cmd` 隐式变化（`tool.py:94-96`）

```python
cmd_stripped = cmd.rstrip()
if not cmd_stripped.endswith("> log 2>&1"):
    cmd = cmd_stripped + " > log 2>&1"
```

如果 review UI 展示 `python run.py`，平台却提交 `python run.py > log 2>&1`，确认内容与真实副作用不一致。三版要求该语义变化前移到 review draft 阶段。

### 4.5 ToolResult content / payload / meta 边界（`tool_result.py:12-24`）

`ToolResult` 字段：`status` / `content`（str）/ `payload`（dict）/ `meta`（dict）/ `images`，`ConfigDict(extra="forbid")`（不能加顶层字段，但 payload / meta 是开放 dict）。

- `content` 进 `ToolMessage.content`，是 agent 后续可见的稳定内容。`content` 是 JSON 字符串，里面加 `review` 键。
- `payload` 进 `ToolResultEvent.payload`，适合前端展示与审计，不依赖它让 agent 理解改动。
- `meta` 不进模型消息、不进 public payload。三版用它承载 runner 内部信号（如 `block_reason`），但**不**用它驱动 run loop 停止（见 §5.7）。

### 4.6 RunResultEvent 当前状态（`events.py:138-144`）

`RunResultEvent.status` 当前仅 `completed | failed | cancelled`，另有 `reason` / `final_content`。三版**不新增** `blocked` 业务状态。reject 后的收尾走 §5.7。

### 4.7 现有交互往返与共享 reply queue

`AskQuestionBridge`（`matmaster/integration/interaction_bridge.py`）发 SSE 事件、经 session 级 reply queue 阻塞等回复，按 `request_id` 匹配；API 侧 `ask_question_reply`（`chat_api.py`）经 `_submit_interaction_reply` 写入队列。

若新增 review bridge 与之共享同一条 list，即使各自校验 `kind` / `request_id`，仍可能被错误 consumer 先 pop 出来导致丢失。三版改用 per-request reply key（见 §8）。

### 4.8 approval gate port 注入点

`HookExecutor` 在 `exp.py:301` 创建；`interaction_bridge` 在 `exp.py:741-742` 解析（`ctx.request.interaction_bridge if spawn_id is None else None`，子 agent 为 None）。`AgentRunPorts`（`runtime_ports.py:163-182`）已是既定的能力注入容器（含 `interrupt_checker` / `bohrium_job_ledger` / `workspace_jobs`）。approval gate port 走同样的注入路径，子 agent 天然拿不到（spawn 时为 None → 放行）。

## 5. 核心设计决策

### 5.1 通用 approval gate plumbing + 工具声明 provider

新增窄能力 RuntimePort（**通用、提交语义**，不写死 Bohrium）：

```python
@runtime_checkable
class SubmitApprovalGate(Protocol):
    async def review(
        self,
        request: SubmitReviewRequest,
    ) -> SubmitReviewDecision: ...
```

- 唯一消费者是 `FullToolRunner`。
- 工具不直接依赖 Redis / SSE / API / reply queue。
- request / decision 用明确字段的 Pydantic model / dataclass，不带 `extra` / `metadata` / `dict[str, Any]` 兜底字段。

工具通过声明一个 **submit review provider** 接入闸门，挂载方式对齐现有 `instance.input_validator`（由 `ToolCompiler` 从工具读取并挂到 `ToolInstance`）：

```python
class SubmitReviewProvider(Protocol):
    def build_review_draft(
        self, model_args: Mapping[str, Any]
    ) -> SubmitReviewDraft | None:
        """返回 None 表示本次调用不需要 review（runner 正常放行）。"""

    def normalize_execution_args(
        self, final_args: Mapping[str, Any]
    ) -> SubmitExecutionArgs:
        ...
```

runner 的闸门判定（通用）：

```text
if instance 有 submit_review_provider
   and approval_gate_port 存在
   and provider.build_review_draft(model_args) 返回非 None:
       走 review
else:
       正常放行
```

`BohriumTool` 实现 `SubmitReviewProvider`：仅当 `action == "submit"` 时 `build_review_draft` 返回 draft，否则返回 None。Bohrium 专属知识（默认值、`cmd` 重定向、`editable_fields`、`input_dir` 语义）全在这里。

> 命名与范围：plumbing（port / runner 闸门 / 事件 / reply key）通用；数据形状是提交语义；v1 唯一 provider 是 Bohrium。不做“任意工具任意参数”的完全通用抽象（YAGNI）。

### 5.2 opt-in：port 存在即启用，不存在即放行

- approval gate port **仅当用户开启确认且为顶层交互 run** 时由服务层注入。
- port 不存在 → runner 闸门判定第二条不满足 → 正常放行。这一条**同时**覆盖：用户未开启、子 agent（spawn，`exp.py:742` 已为 None）、评测、cron、devshell。
- 放行不是散落的 `if None` 兜底分支，而是闸门唯一启用条件的自然否定，符合 `CLAUDE.md`“严禁主代码内联兜底”。

opt-in 开关：作为 session / 用户级配置（如 `submit_confirmation_enabled`），由 `AgentRunService` 构造 `AgentRunPorts` 时读取；为真且顶层 run 才构造并注入 `SubmitApprovalGate`。

### 5.3 两级 validation

review gate 不能零校验地把模型原始参数发给前端。链路：

```text
raw JSON guard
tool identity / action extraction
build_review_draft（宽松、无副作用、可展示）
human review
normalize_execution_args（严格、无副作用）
structural validation / input validator / capability policy
scheduler / executor
```

draft 阶段只保证可展示、可编辑、体积受控、字段属于 submit 草稿；**允许缺失 required field**，记入 `draft_issues` 让用户在 UI 补齐。用户确认后才走严格链路。

### 5.4 四层参数语义

```text
model_arguments        模型原始 tool call 参数；永不修改，只表示模型意图。
review_draft_arguments 后端为 UI 准备的规范化草稿；已填默认、基础规整、追加日志重定向。
final_arguments        用户点确认/拒绝时返回的当前草稿；拒绝时不提交，仍用于告诉 agent 改了什么。
execution_arguments    确认后经严格 normalize/validation/policy，实际传给 _submit() 的参数。
```

三类 diff：

```text
normalization_changes            model_arguments       -> review_draft_arguments
user_parameter_changes           review_draft_arguments -> final_arguments
execution_normalization_changes  final_arguments        -> execution_arguments
```

`execution_normalization_changes` 默认应为空；若非空只能是非语义类型转换（如 `"80"`→`80`）。用户确认后**不得**再悄悄改 `cmd` / `image` / `machine` / `job_name` / `disk_size` / `input_dir` 等语义字段。

**`content.review` 不暴露 `normalization_changes` / `execution_normalization_changes` 两类内部 diff**（仅进 payload 审计），避免增加 agent 侧复杂度；agent 看到的参数变化统一用 `parameter_changes`（= `user_parameter_changes`）表达，另含 `input_file_changes` 文件变更（见 §10.1）。完整提交参数 `execution_arguments` 与 `changed_fields` 等审计字段只进 payload。

### 5.5 cmd 语义变化必须在 review 前

- `build_review_draft()` 完成 `> log 2>&1` 追加。
- `normalize_execution_args()` 不再追加日志重定向。
- 把 `submit_job_via_runtime()` 当前的追加逻辑前移到 draft；下游 helper 只做 defensive check（检测并报错），不再隐藏改变语义。
- 若 `final_arguments.cmd` 缺少系统强制的日志重定向，返回 `invalid_final_arguments` blocked，或重新发起 review；不直接改后提交。

### 5.6 approval gate 在串行校验阶段 await（取代 batch barrier）

approval gate 是 `execute_batch()` 串行阶段里的一个 `await`，放在 catalog lookup / 识别 submit 之后、structural validation 之前。由此自然得到两个性质，无需任何新状态：

1. 人审等待期间，整批**没有任何工具执行**（执行都在串行阶段之后的 `asyncio.gather`）。不存在“submit 与写文件并发”竞态。
2. 用户改后的 `execution_arguments` 天然流过现有 structural / input / policy 校验。

唯一小代价：模型若在**同一轮**里既写文件又 submit（被 prompt 引导成分两轮，极少），review 可能看到写之前的 `input_dir` 状态——落在“文件用户自负”范围内（§3.2、§5.10）。

不引入 prefix-scan / `DeferredByBohriumSubmitBarrier` 等新状态：submit 经审批后照常进入 `approved` 并在 gather 执行，与同批其它工具并发（正常路径）。

### 5.7 reject / timeout / 不可用 = blocked + 同 run 重提交护栏（不停 run loop）

不新增 run loop 状态。reject 等结果走现有“blocked tool result”路径，靠两点防止模型自动重试：

1. 返回 `ToolResult(status="blocked")`，`content` 内写明“用户已拒绝，**不要重新提交**，请总结并结束本轮或转去做其它工作”，并携带 review record。
2. **同 run 重提交护栏**：把被拒 submit 的关键字段签名（见 §5.7.1）记入 `runner_state`；同一 run 内再次提交**同一签名**直接 blocked，不再发起 review，提示“该提交已被用户拒绝，请等待用户新指令”。不同作业不受影响（仍各自走 review）。

模型不再发工具调用时，run 以现有 `completed` 自然收尾；模型仍可在本轮继续做其它工作。`timeout`（用户超时未回）与 `review_unavailable`（Redis / API 不可用，已 opt-in 却无法送达确认）同样返回 blocked + 不提交；二者也进护栏，避免就地重试风暴。

> 对比 opt-out：port 不存在（未开启 / 子 agent / 评测）是 §5.2 的“放行”，与此处“已开启但本次未获确认 → blocked”是两条不同路径，不要混淆。

#### 5.7.1 重提交签名

签名 = 对 `execution`/`draft` 维度的关键字段做规范化后哈希：`input_dir`、`job_name`、`image`、`cmd`（其余字段可纳入）。存 `runner_state` 的一个 set（如 `bohrium_rejected_submit_signatures`）。命中即 blocked。

### 5.8 per-request reply key

不复用 session 级共享 reply list。每个交互 request 独立 key：

```text
human_interaction:{request_id}      pending registry record
interaction_reply:{request_id}      worker 只等待自己的 reply key
```

worker 发 review 前写 pending registry，再发 SSE event。API 收 reply 后按 `request_id` 找 pending record，校验后写 `interaction_reply:{request_id}`。worker 只 `BLPOP` 自己的 key，不存在错误 consumer 吃掉消息。

同 session/run 内还需 active human interaction lock，避免 AskQuestion 与 submit review 同时挂起：

```text
human_interaction_active:{session_id}:{run_id} = request_id
```

已存在 active interaction 时，新交互不能启动；runner 返回 blocked，让模型 / 用户下一轮重试。

### 5.9 POST hook 后 finalizer

```text
raw tool result
  -> attach_submit_review_record()
  -> 通用 POST_TOOL_CALL rewrite / observe
  -> enforce_submit_review_contract()
  -> final ToolResult
```

`enforce_submit_review_contract()`：若该 `tool_call_id` 有 review record，则最终结果必须含 `content.review` 与 `payload.bohrium_submit_review`；若 hook 改了 content 其它字段，finalizer 只补回 review 合同，不覆盖 hook 的正常改写。

### 5.10 reported 文件变化，无 TOCTOU 兜底

后端不读文件内容、不算 diff、不验证文件是否真改变。文件变化（含修改行号 `lines`）一律由前端文件能力上报，带 `input_file_changes_source: "frontend_reported"`。给 LLM 的 content 用 `input_file_changes`（每条 `relative_path` + `lines`），前端 reply 与 payload 审计用 `reported_input_file_changes`。

确认后到打包上传之间，若用户继续改文件，**由用户自负**：v1 不做 snapshot / revision / 锁。确认后照常立即解析 `input_dir` 并打包提交。

## 6. 数据模型

### 6.1 Submit draft / execution helpers（在 `BohriumTool` 侧实现 provider）

```python
def build_review_draft(model_args: Mapping[str, Any]) -> SubmitReviewDraft | None:
    """宽松、无副作用、可展示。
    仅 action=="submit" 返回非 None。
    负责字段筛选、默认值、基础类型规整、cmd 日志重定向、
    draft_issues、normalization_changes。不代表最终可提交。"""


def normalize_execution_args(final_args: Mapping[str, Any]) -> SubmitExecutionArgs:
    """严格、无副作用、用于真实执行。用户确认后调用。
    不得在用户确认后改变语义字段。"""
```

draft 校验规则：

- `arguments` 必须是 object；`action == "submit"` 可识别；只保留 submit 相关字段。
- `cmd` / `image` / `machine` / `job_name` / `input_dir` 有最大长度限制。
- `disk_size` 宽松接受 int 或数字字符串。
- 缺 `input_dir` / `image` / `cmd` 时记 `draft_issues`，允许前端补齐。
- 默认 `machine="c32_m128_cpu"`、`job_name="matmaster-job"`、`disk_size=50`。
- `cmd` 必须在 draft 阶段追加 `> log 2>&1`。

### 6.2 Review request（SSE event：`submit_review`）

事件不含文件内容；前端拿 `input_dir` 后用文件能力展示具体文件。

```json
{
  "schema_version": 1,
  "type": "submit_review",
  "tool_name": "Bohrium",
  "request_id": "sr_xxx",
  "session_id": "sess_xxx",
  "run_id": "run_xxx",
  "turn_id": "turn_xxx",
  "tool_call_id": "call_xxx",

  "model_arguments": {
    "action": "submit",
    "input_dir": "/share/case_001",
    "image": "old-image",
    "cmd": "python run.py"
  },
  "review_draft_arguments": {
    "action": "submit",
    "input_dir": "/share/case_001",
    "image": "old-image",
    "cmd": "python run.py > log 2>&1",
    "machine": "c32_m128_cpu",
    "job_name": "matmaster-job",
    "disk_size": 50
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
  "file_edit_mode": "live_reported",
  "expires_at": "2026-06-17T12:30:00Z"
}
```

`draft_issues` 示例：

```json
[{ "field": "image", "code": "missing_required_field", "message": "image is required before submit." }]
```

### 6.3 Review reply

API 只接受用户行为；`parameter_changes` 由 runner 据 `review_draft_arguments` 与 `final_arguments` 计算。

确认提交：

```json
{
  "schema_version": 1,
  "kind": "submit_review_reply",
  "request_id": "sr_xxx",
  "run_id": "run_xxx",
  "tool_call_id": "call_xxx",
  "decision": "submit",
  "submit_arguments": {
    "action": "submit",
    "input_dir": "/share/case_001",
    "image": "new-image",
    "cmd": "python run.py --ecut 600 > log 2>&1",
    "machine": "c64_m256_cpu",
    "job_name": "case-001-reviewed",
    "disk_size": 80
  },
  "reported_input_file_changes": [
    { "relative_path": "input.inp", "lines": "12-15,30" },
    { "relative_path": "run.sh", "lines": "8" }
  ]
}
```

拒绝提交：

```json
{
  "schema_version": 1,
  "kind": "submit_review_reply",
  "request_id": "sr_xxx",
  "run_id": "run_xxx",
  "tool_call_id": "call_xxx",
  "decision": "reject",
  "submit_arguments": {
    "action": "submit",
    "input_dir": "/share/case_001",
    "image": "new-image",
    "cmd": "python run.py --dry-run > log 2>&1",
    "machine": "c32_m128_cpu",
    "job_name": "matmaster-job",
    "disk_size": 50
  },
  "reported_input_file_changes": [
    { "relative_path": "input.inp", "lines": "12-15" }
  ]
}
```

拒绝时的 `submit_arguments` 是用户当前草稿，不提交，仍用于生成 `user_parameter_changes`。

### 6.4 Review outcome

```text
user_decision   submit | reject | null
review_outcome  approved | rejected | timeout | cancelled | review_unavailable | invalid_final_arguments
```

`timeout` / `cancelled` / `review_unavailable` 不是用户 decision，`user_decision` 为 `null`。

### 6.5 reported_input_file_changes

本工具流程只表达对 `input_dir` 内既有文件的内容修改。新建 / 删除 / 重命名等文件级操作不在工具职责内：不上报、不规范化、不进 tool_result 或 payload（§3.2）。用户若在工具之外做了这些操作，工具不感知也不负责。

前端传入：

```json
{ "relative_path": "subdir/input.inp", "lines": "12-15,30" }
```

后端规范化后：

```json
{ "path": "/share/case_001/subdir/input.inp", "relative_path": "subdir/input.inp", "lines": "12-15,30" }
```

- `relative_path`：相对最终 `input_dir` 的 POSIX 路径，优先字段。
- `path`：后端据最终 `input_dir` 计算的完整路径；前端可传但非主字段，仅进 payload 审计。
- `lines`：被修改的行号，紧凑字符串（如 `"12-15,30"` 表示第 12–15 行与第 30 行）。前端文件能力上报，后端不读内容、不算 diff、不验证是否真改变。

content 投影（`content.review.input_file_changes`）每条只取 `relative_path` + `lines`；带绝对 `path` 的完整结构只进 payload。后端只做路径边界、`lines` 格式、数量、体积校验。

## 7. 路径与大小校验

路径规则：

1. 最终 `submit_arguments.input_dir` 为基准。
2. 优先 `relative_path`，绝对 `path` 仅冗余校验。
3. `relative_path` 用 POSIX 校验；禁止空、以 `/` 开头、含 `..`、含 NUL、超长。
4. 同传 `path` 与 `relative_path` 时校验一致。
5. 用 `os.path.commonpath()` 校验最终 path 在 input_dir 内，不用 `startswith()`。
6. 只表达内容修改：被改文件须存在于最终 `input_dir` 内；不处理 `created` / `deleted` / `renamed`。
7. symlink 策略：打包不跟随逃逸出 input_dir 的 symlink；如需跟随，resolve 后仍须在 input_dir 下。
8. `lines` 仅接受 `N` 或 `N-M`（要求 `M ≥ N`）的逗号分隔串；非法格式拒绝，不解析文件内容。

大小规则：

- `content.review.input_file_changes` 最多 20 条；超限时附 `input_file_changes_truncated: true`（不截断则该字段不出现），精确条数留在 payload。
- `payload.bohrium_submit_review.reported_input_file_changes` 最多 200 条。
- 单条 `lines` 字符串最多 200 字符；超限按行段截断并标记。
- reply payload 总字节硬上限（如 256 KiB）；超限 API 拒绝并提示前端减少行号体积。

## 8. API / Worker 通信

### 8.1 Pending registry

worker 发 review 前写 pending：

```text
human_interaction:{request_id} = {
  kind: "submit_review", tool_name, session_id, run_id, turn_id,
  tool_call_id, state: "pending", expires_at
}
```

再发 SSE event。active interaction lock 见 §5.8。

### 8.2 Reply endpoint

```text
POST /api/v1/chat/sessions/{session_id}/runs/{run_id}/interactions/{request_id}/reply
```

body：

```json
{
  "kind": "submit_review_reply",
  "decision": "submit",
  "submit_arguments": {},
  "reported_input_file_changes": []
}
```

API 行为：

1. 按 `request_id` 读 pending record。
2. 校验 session / run / tool_call_id / kind / 用户权限。
3. 校验 state 仍 `pending`，过期返回 409。
4. SETNX / CAS 把 state 改 `answered`。
5. 发布 `submit_review_reply` event 到 stream / history。
6. 写 reply 到 `interaction_reply:{request_id}`。
7. duplicate reply 返回 409 或幂等返回已处理；不二次写入。

### 8.3 Worker 等待 reply

`SubmitApprovalGate.review()` 等待 `interaction_reply:{request_id}`：

- reply 到达：返回 `SubmitReviewDecision`。
- timeout：pending state → `timeout`，返回 `review_outcome="timeout"`（不提交，进护栏）。
- run stop：pending state → `cancelled`，返回 `cancelled` 或走上层 cancel（不提交）。
- Redis / API 不可用：返回 `review_outcome="review_unavailable"`（不提交，进护栏）。

## 9. Runner 执行流程

```text
LLM tool call: Bohrium submit
  -> ToolCallEvent 记录 model_arguments

  -> 串行阶段:
       raw JSON guard
       catalog lookup
       通用 PRE_TOOL_CALL observe / intercept（看 model_arguments；hook block 则不进 review、无副作用）

       闸门判定: instance 有 submit_review_provider 且 approval gate port 存在
         否 -> 正常放行（opt-out / 子 agent / 评测 / cron / devshell）
         是 -> provider.build_review_draft(model_args)
                 返回 None -> 正常放行（非 submit）
                 返回 draft ->
                   重提交护栏命中? -> blocked（不再发 review）
                   active interaction lock 已占? -> blocked
                   写 pending registry + emit submit_review SSE
                   await reply / timeout / cancel / unavailable

       reject / timeout / unavailable:
         ToolResult(status="blocked") + content.review + payload + meta.block_reason
         记重提交签名
         （不停 run loop；execute_one 不执行该 call）

       submit:
         校验 reply request_id/run_id/tool_call_id/kind
         校验 reported_input_file_changes 路径边界与体积
         execution_arguments = normalize_execution_args(final_arguments)
         structural validation / input validation / capability policy（针对 execution_arguments）
         invalid -> ToolResult(status="blocked") + review record + reason（不停 run loop）
         有效 -> 放入 approved（携带 review record）

  -> 并发阶段 (gather / _execute_one):
       BohriumTool._submit(execution_arguments) -> job/create -> upload -> job/add
       attach_submit_review_record()
       通用 POST_TOOL_CALL rewrite / observe
       enforce_submit_review_contract()
       ToolResultEvent / ToolMessage
```

### 9.1 Hook 顺序

不改现有 `PRE_TOOL_CALL` 语义（始终看 model 原始 args，用于审计 / 粗拦截 / 全局策略）。approval gate 不是 hook，位于 PRE hook 之后、严格校验之前。structural validation / input validator / capability policy / scheduler / executor 看的都是 `execution_arguments`；audit 同时记 `model_arguments` 与 `execution_arguments`。第一版不新增 `PRE_EFFECTIVE_TOOL_CALL` 事件。

### 9.2 ToolCallEvent arguments 不回写

`ToolCallEvent.arguments` 仍是模型原始参数，不回写 final args。content 用 `parameter_changes` 告诉 agent 哪些参数被改，完整真实提交参数由 `payload.bohrium_submit_review.execution_arguments` 表达。tool_call 表示模型意图，tool_result 表示实际执行与人类介入。

## 10. ToolResult 合同

### 10.1 成功提交

`ToolResult.content`（JSON 字符串）解析后：

```json
{
  "success": true,
  "job_id": "12345",
  "status": "Submitted",
  "use_sandbox": true,
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

`review` 只保留 agent 决策必需的两类增量，各自「有才出现」：

- `parameter_changes`：用户改了哪些 submit 参数，每项 `from` / `to`；未改则省略。
- `input_file_changes`：用户改了 `input_dir` 内哪些文件，每条 `relative_path` + 修改行号 `lines`（紧凑字符串，如 `"12-15,30"`）。行号由前端上报、后端不读不算（§5.10）；未改则省略，超过 20 条时附 `input_file_changes_truncated: true`（见 §7）。

两者皆空时 `review` 整块省略，content 退回 `success` / `job_id` / `status` / `use_sandbox` 四个顶层字段。完整 `execution_arguments`、规范化 diff、文件来源等审计信息只进 §10.3 payload，不进 content。

### 10.2 用户拒绝

`ToolResult.status="blocked"`，content JSON：

```json
{
  "success": false,
  "status": "UserRejected",
  "message": "用户拒绝了本次 Bohrium 提交。请不要重新提交本作业，可总结当前进展、转去做其它工作，或结束本轮等待用户继续反馈。",
  "review": {
    "parameter_changes": {
      "image": { "from": "old-image", "to": "new-image" },
      "cmd": { "from": "python run.py > log 2>&1", "to": "python run.py --dry-run > log 2>&1" }
    },
    "input_file_changes": [
      { "relative_path": "input.inp", "lines": "12-15" }
    ]
  }
}
```

拒绝路径的 `review` 与成功路径同构（`parameter_changes` + `input_file_changes`）；顶层 `message` 承担「别重提交」指令，无需 `outcome` / `user_confirmed` 重复表达。用户当时的完整草稿参数进 §10.3 payload，不进 content。

对应 `ToolResult.meta`（runner 内部信号，不进模型消息、不进 public payload）：

```json
{ "block_reason": "UserRejected", "layer": "submit_approval_gate" }
```

拒绝不是平台错误或异常。它表示外部副作用被用户拦截；agent 不得自动换参数重试本作业（由 §5.7 护栏保证），但可继续其它工作或结束本轮。

### 10.3 Payload 审计结构

```json
{
  "bohrium_submit_review": {
    "schema_version": 1,
    "request_id": "sr_xxx",
    "session_id": "sess_xxx",
    "run_id": "run_xxx",
    "tool_call_id": "call_xxx",

    "review_outcome": "approved",
    "user_decision": "submit",

    "model_arguments": {},
    "review_draft_arguments": {},
    "final_arguments": {},
    "execution_arguments": {},

    "normalization_changes": {},
    "user_parameter_changes": {},
    "execution_normalization_changes": {},
    "changed_fields": [],

    "reported_input_file_change_count": 37,
    "reported_input_file_changes_truncated": true,
    "reported_input_file_changes": [],
    "input_file_changes_source": "frontend_reported",

    "execution_attempted": true,
    "external_effect_started": true,
    "job_create_attempted": true,
    "job_id": "12345",
    "input_upload_attempted": true,
    "job_add_attempted": true
  }
}
```

部分外部副作用示例：`job/create` 成功但上传失败时，`ToolResult.status` 可能为 `error`，payload 仍须记录 `external_effect_started=true`、`job_create_attempted=true`、`job_id`（若已知）、`input_upload_attempted=true`、`job_add_attempted=false`。

> v1 不含 snapshot / revision / digest 字段（§5.10）。

## 11. BohriumTool 调整

`BohriumTool` 实现 `SubmitReviewProvider`，拆出两个 helper：

```text
build_review_draft()        provider；draft 准备 + 默认值 + cmd 重定向 + draft_issues + normalization_changes
normalize_execution_args()  provider；严格规整，不改语义字段、不再追加 cmd
```

责任边界：

```text
ToolRunner / approval gate:
  draft；用户 final args 严格 normalize；validation / policy；audit record；重提交护栏。

BohriumTool._submit():
  假设收到 SubmitExecutionArgs。可做 defensive validation。
  不再偷偷填默认语义字段、不再偷偷追加 cmd 日志重定向。

submit_job_via_runtime():
  input source resolve、archive/upload、job/create、job/add。
  不再改变用户已确认的 cmd（保留逻辑只做 defensive 检测报错，不自动改）。
```

## 12. 实施顺序建议

1. 定义 submit 参数模型与 provider helper：四层参数类型、`build_review_draft()`、`normalize_execution_args()`、`SubmitReviewProvider` 协议。
2. 定义 `SubmitReviewRequest` / `SubmitReviewDecision`（`user_decision` 与 `review_outcome` 拆分）与 `SubmitApprovalGate` port；`AgentRunPorts` 增字段；`ToolCompiler` 挂 `submit_review_provider`（仿 `input_validator`）。
3. 实现 pending registry + per-request reply key + active interaction lock + stale/duplicate 处理。
4. 新增 reply API（强校验 session/run/request/tool_call/kind；不写共享 list）。
5. 服务层按 opt-in 构造并注入 `SubmitApprovalGate`（顶层交互 run 且开关为真；子 agent 不注入）。
6. 改造 `FullToolRunner` 串行阶段：闸门 await（§5.6、§9）；submit 经审批走 `execution_args`、进 validation/policy；reject/timeout/unavailable/invalid 返回 blocked + 重提交护栏。
7. 路径校验与文件变化行号（relative_path 优先、commonpath、symlink 策略、lines 格式校验、count/payload size limit）。
8. ToolResult augmentation：`attach_submit_review_record()` → POST hook → `enforce_submit_review_contract()`。
9. 补齐 tests。

## 13. 测试计划

Focused tests：

1. draft validation：缺 `image` 生成 `draft_issues` 仍可发 review；过长 `cmd`/`image` 被拒不进 review；`cmd` 重定向在 draft 阶段完成。
2. 参数分层：`model_arguments` 不被改；`review_draft_arguments` 含默认值与重定向；`final_arguments` 由 reply 决定；`_submit()` 收到 `execution_arguments`。
3. 闸门启用：port 不存在时 Bohrium submit 正常放行（无 review）；子 agent（spawn）路径无 port → 放行。
4. gate 串行语义：人审等待期间无工具执行；用户改后的 `input_dir`/`cmd`/`machine` 经 structural validation 与 `_submit()` 生效。
5. reject 收尾：返回 blocked，不调用 `create_job`/`upload_input_archive`/`add_job`；run 不新增状态；同 run 内重提交同一签名被护栏 blocked；不同作业放行。
6. timeout / review_unavailable / invalid_final_arguments：均 blocked、不提交、进护栏。
7. per-request reply key：AskQuestion reply 不被 submit gate 消费，反之亦然；stale/duplicate reply 返回 409 或幂等忽略。
8. POST hook 破坏性 rewrite：删 `content.review` / `payload.bohrium_submit_review` 后 finalizer 恢复必要字段与摘要。
9. cmd hidden normalization：确认后的 `cmd` 不被 `_submit()`/`submit_job_via_runtime()` 隐式追加；review UI 展示的 `cmd` 与 `job/add` 收到的一致。
10. path escape 与 lines 格式：`../x`、`/absolute`、NUL、symlink escape 被拒；非法 `lines`（如 `5-2`、含字母）被拒，合法 `lines`（`"12-15,30"`）通过。
11. payload 大小限制：大量文件变化时 content 截断、payload 截断标记正确；超 reply 字节上限 API 报错。
12. partial side effect：`job/create` 成功但上传失败时 payload 记 `external_effect_started=true` 与 job ref。
13. no file content leakage：request / reply / tool_result content / payload 都不含文件正文或 patch。

验证命令第一版建议（按实际修改文件收窄/扩展）：

```bash
uv run --extra dev pytest \
  tests/matmaster/core/test_full_tool_runner.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/apis/test_interaction_reply_api.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py
```

## 14. 最终行为示例

### 14.1 用户确认并修改参数和文件

模型调用：

```json
{ "action": "submit", "input_dir": "/share/case_001", "image": "old-image", "cmd": "python run.py", "machine": "c32_m128_cpu" }
```

review draft：

```json
{ "action": "submit", "input_dir": "/share/case_001", "image": "old-image", "cmd": "python run.py > log 2>&1", "machine": "c32_m128_cpu", "job_name": "matmaster-job", "disk_size": 50 }
```

用户在前端：改 `image`→`new-image`、`cmd`→`python run.py --ecut 600 > log 2>&1`、`machine`→`c64_m256_cpu`，改 `/share/case_001/input.inp` 与 `run.sh`，点确认。

agent 看到（节选）：

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

### 14.2 用户拒绝但保留草稿修改

用户改 `cmd`、改 `input.inp`，点拒绝。agent 看到 `status="blocked"` 的 content（见 §10.2），消息明确“不要重新提交本作业”。run 不新增状态：模型若继续做别的工作则继续，若无更多工具调用则以 `completed` 收尾；同 run 内若模型再次提交同一作业，被 §5.7 护栏直接 blocked。等待用户下一条消息继续。
