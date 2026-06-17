# Bohrium submit 用户确认与参数审阅设计

> 日期：2026-06-17
> 状态：二版设计稿，待实现计划拆分。
> 范围：在 `Bohrium(action="submit")` 产生平台外部副作用前引入用户确认与参数审阅；用户可修改 submit 参数和 `input_dir` 内文件；最终 `tool_result` 必须让 agent 明确知道用户确认、拒绝、参数修改和具体文件修改。

## 1. 二版修订结论

外部 review 的主方向成立：Bohrium submit 不能被建模成普通 `HookExecutor` handler。它需要阻塞等待用户、返回业务数据、改变真实执行参数，并且会决定是否允许外部副作用发生。这类能力应该是 ToolRunner 内置的 typed human approval barrier，而不是通用 hook。

二版相对初稿的关键变化：

1. 名称从泛化的 submit review gate 收敛为 `BohriumSubmitApprovalGate`。
2. review 前不再直接把模型原始 arguments 发给前端，而是先做宽松、无副作用的 draft validation。
3. submit 参数分为四层：`model_arguments`、`review_draft_arguments`、`final_arguments`、`execution_arguments`。
4. `cmd` 的日志重定向必须在 review draft 阶段完成；用户看到的 `cmd` 必须就是后续平台提交的 `cmd`。
5. `Bohrium(action="submit")` 是 batch barrier，不能和同批其它工具并发执行。
6. 用户拒绝、超时、review 不可用不是普通工具错误，而是 human gate terminal state，run loop 必须停止等待用户。
7. API / Worker reply 不再复用同一个共享 reply list 让多个 bridge 竞争消费，而是使用 pending registry 和 per-request reply key。
8. `POST_TOOL_CALL` rewrite 之后增加 finalizer，保证 review 信息不会被 hook 删除。
9. input 文件变化字段命名为 `reported_input_file_changes`，明确这是前端或用户报告的文件修改，不是后端独立 diff。
10. 用户确认后必须冻结 input 状态：优先 snapshot，最低成本也要持有 input_dir 写锁或校验 workspace revision，避免 TOCTOU。
11. `ToolResult.payload` 和模型可见 content 都要有条数、摘要长度和字节数边界；完整审计用 `audit_id` 引用。

## 2. 背景

当前 `BohriumTool` 的 submit 路径非常直接：

1. agent 生成 `Bohrium(action="submit", input_dir=..., image=..., cmd=...)`。
2. `FullToolRunner` 做通用工具链路校验。
3. `BohriumTool._submit()` 校验必填参数，填充默认 `machine`、`job_name`、`disk_size`。
4. `submit_job_via_runtime()` 解析 `input_dir`，打包或远端上传输入目录。
5. `submit_job_via_runtime()` 会在 `cmd` 末尾缺少 `> log 2>&1` 时自动追加该重定向。
6. 调用 Bohrium `job/create`、上传输入、`job/add`。
7. 返回只包含 `job_id`、提交状态和 sandbox 标记的 `ToolResult.content`。

这条路径缺少一个强制的人类确认点。Bohrium submit 是高成本外部副作用：一旦 `job/create`、上传和 `job/add` 执行，平台资源就已经被使用，错误的 `image`、`cmd`、`machine` 或输入文件都可能造成浪费。

用户希望在 submit 前暂停，让前端展示待提交信息，并允许用户：

- 确认或拒绝本次 submit。
- 修改 `cmd`、`image`、`machine`、`job_name`、`disk_size`、`input_dir` 等 submit 参数。
- 通过前端文件能力修改 `input_dir` 内的具体输入文件。
- 让最终 `tool_result` 明确告知 agent：用户是否确认、哪些 submit 参数被改了、`input_dir` 内哪些具体文件被改了。

## 3. 目标与非目标

### 3.1 目标

- 在 `Bohrium(action="submit")` 真正调用 `job/create`、上传输入或 `job/add` 前暂停，等待用户确认。
- 用户确认后，使用用户返回的最终 submit 参数继续执行 Bohrium submit。
- 用户拒绝后，不调用 `job/create`，不上传 input zip，不调用 `job/add`，并返回模型可见的 `ToolResult(status="blocked")`。
- 用户对 submit 参数的修改必须进入模型可见的 `ToolResult.content.review.parameter_changes`。
- 用户对 `input_dir` 内具体文件的修改必须进入模型可见的 `ToolResult.content.review.reported_input_file_changes`。
- 文件内容不进入 review request、review reply、tool_result content 或 payload；前端只把路径和可选摘要回传给后端。
- 完整审计记录进入专用 audit store，`ToolResult.payload.bohrium_submit_review` 携带审计摘要和 `audit_id`。
- 保持 API / Worker 分离。确认请求由 worker 发出，用户回复可以由任意 API 实例接收，通过 Redis pending registry 和 per-request reply key 回到执行 run 的 worker。
- 不通过 `run_meta` 传递服务能力，不把用户交互 callback 塞进 metadata。

### 3.2 非目标

- 不由 Bohrium review 后端读取、展示、diff 或写入 `input_dir` 内文件内容。
- 不在 hook payload 或 tool_result 中传输文件内容。
- 不新增主代码中的兼容兜底逻辑；旧事件或旧 tool_result 不做内联兼容。
- 不改变 Bohrium `query`、`download`、`kill`、`list_images`、`list_machines` 的语义。
- 不改变 Bohrium job ledger 的既有状态机；只在 submit 审计 payload 中记录外部副作用是否已经开始。
- 不把通用 AskQuestion 工具当作 submit 确认闸门；AskQuestion 由模型主动调用，不能保证贴着真实 submit 副作用执行。
- 不让前端通过 review reply 提交文件 patch。文件修改由前端通过文件系统能力提前完成，reply 只报告哪些文件被修改。

## 4. 当前代码事实

### 4.1 HookExecutor 边界

当前 hook 系统由 `HookExecutor` 承载，能力分为 observe、intercept、rewrite：

- `PRE_TOOL_CALL` 目前只做 observe 和 intercept，不能返回修改后的 arguments。
- `POST_TOOL_CALL` 当前支持 rewrite `ToolResult`。
- Hook 输入会被 deepcopy，observe 中的原地修改不会泄漏。

项目边界要求：`HookExecutor` 专指事件扩展系统，用于 observe、intercept、rewrite 运行过程事件。需要返回业务数据或承担顺序屏障语义的服务能力，不应伪装成 `HookExecutor` handler。

因此，本设计把用户确认本身建模为 `BohriumSubmitApprovalGate` runtime capability port。通用 hook 仍可观察和拦截工具生命周期，但不能承担等待用户、返回最终 submit 参数、决定是否启动外部副作用的职责。

### 4.2 ToolRunner 当前执行形态

`FullToolRunner.execute_batch()` 当前是两阶段结构：

1. 串行对所有 tool call 做 catalog、raw JSON guard、pre hook、structural validation、input validator、capability policy。
2. 将所有通过审批的 tool call 放入 `approved`，再通过 `asyncio.gather()` 并发执行。

这个形态对普通工具合理，但对 Bohrium submit 不够安全。若同一批 tool call 里同时包含写文件和 submit，当前结构可能让 submit 与其它工具并发，或者让 review 看到的 `input_dir` 状态不是最终状态。

### 4.3 ToolCallData arguments 只读约束

`FullToolRunner.execute_batch()` 明确要求每层消费者把 arguments 当作只读。原因是 `ToolCallData` 缓存了 `arguments_json`，原地修改会造成缓存和 dict 不一致。

因此用户修改后的 submit 参数必须形成新的 `effective_args`，不能修改 `tc.arguments`。

### 4.4 Bohrium cmd 隐式变化

当前 `BohriumTool._submit()` 读取 `cmd` 后传给 `submit_job_via_runtime()`。`submit_job_via_runtime()` 会执行：

```python
cmd_stripped = cmd.rstrip()
if not cmd_stripped.endswith("> log 2>&1"):
    cmd = cmd_stripped + " > log 2>&1"
```

这意味着如果 review UI 展示的是 `python run.py`，真实平台提交却是 `python run.py > log 2>&1`，用户确认的内容和真实副作用不一致。二版要求这类语义变化必须前移到 review draft 阶段。

### 4.5 ToolResult content、payload、meta 的边界

`dispatch_tool_calls()` 会把 `ToolResult.content` 写入 `ToolMessage.content`。这是 agent 后续轮次可见的稳定内容。

`ToolResult.payload` 会进入 `ToolResultEvent.payload`，适合前端展示和审计，但不应依赖它让 agent 理解用户改动。

`ToolResult.meta` 不进入模型消息，也不进入 public tool_result payload。它适合承载 run loop 内部控制信号，例如 `requires_user_input=true` 和 `block_reason=UserRejected`。当前 agent loop 尚未读取这个信号，实施时必须补上。

因此：

- agent 必须知道的用户修改摘要放入 `ToolResult.content.review`。
- 前端和审计需要的结构摘要放入 `ToolResult.payload.bohrium_submit_review`。
- run loop 必须硬停的信号放入 `ToolResult.meta`，由 `dispatch_tool_calls()` 或 agent loop 显式消费。

### 4.6 现有 AskQuestion reply queue 不适合多 consumer 复用

当前 `AskQuestionBridge` 通过 `reply_queue.get()` 阻塞等待，API 侧 `ask_question_reply` 将 envelope 写入 session 级 reply queue。

如果再新增一个 `BohriumSubmitReviewBridge` 并让两个 bridge 同时 `BLPOP` 同一个 Redis list，即使各自校验 `kind` 和 `request_id`，也仍有错误 consumer 吃掉回复的风险：

1. Bohrium reply 进入共享 list。
2. AskQuestionBridge 先 pop 出来。
3. AskQuestionBridge 发现 request 不匹配并报错。
4. BohriumSubmitReviewBridge 永远等不到该 reply。

二版必须改成 per-request reply key，或统一 human interaction dispatcher。本设计选择 per-request reply key。

## 5. 核心设计决策

### 5.1 使用 BohriumSubmitApprovalGate

新增 runtime capability port：

```python
@runtime_checkable
class BohriumSubmitApprovalGate(Protocol):
    async def review(
        self,
        request: BohriumSubmitReviewRequest,
    ) -> BohriumSubmitReviewDecision: ...
```

该 port 是窄能力接口：

- 唯一消费者是 `FullToolRunner` 的 Bohrium submit barrier。
- `BohriumTool` 不直接依赖 Redis、SSE、API service 或 reply queue。
- port 不包含 `extra`、`metadata`、`state`、`context`、`services`、`payload` 或 `dict[str, Any]` 兜底字段。
- request 和 decision 使用明确字段的 Pydantic model 或 dataclass。

### 5.2 两级 validation

review gate 不能零校验地把模型原始参数发给前端。二版拆成：

```text
raw JSON guard
tool identity / action extraction
submit draft validation + normalization
human review
final strict validation / policy / execution
```

review 前的 draft validation 只保证内容可展示、可编辑、体积受控、字段属于 submit 草稿。它允许缺失 required field，并把问题记录到 `draft_issues`，让用户在 UI 中补齐。

用户确认后才执行严格链路：

- `normalize_bohrium_submit_execution_args()`
- structural validation
- input validator
- capability policy
- scheduler
- `BohriumTool._submit()`

### 5.3 四层参数语义

二版明确四层参数：

```text
model_arguments
  模型原始 tool call 参数。永不修改，只表示模型意图。

review_draft_arguments
  后端为 review UI 准备的规范化草稿。
  已填默认值、做基础规整、追加强制日志重定向。

final_arguments
  用户点击确认或拒绝时返回的当前草稿。
  拒绝时不会提交，但仍用于告诉 agent 用户改过什么。

execution_arguments
  用户确认后，经过最终 strict normalize / validation / policy 后实际传给 `_submit()` 的参数。
```

对应 diff 拆成三类：

```text
normalization_changes
  model_arguments -> review_draft_arguments

user_parameter_changes
  review_draft_arguments -> final_arguments

execution_normalization_changes
  final_arguments -> execution_arguments
```

`execution_normalization_changes` 默认应为空。若存在，只能是非语义类型转换，例如 `"80"` 到 `80`。用户确认后不得再偷偷改变 `cmd`、`image`、`machine`、`job_name`、`disk_size`、`input_dir` 这类语义字段。

### 5.4 cmd 语义变化必须发生在 review 前

用户在 review UI 看到的 `cmd` 必须就是最终提交到 Bohrium 平台的 `cmd`。

因此：

- `prepare_bohrium_submit_review_draft()` 必须在 review 前完成 `> log 2>&1` 追加。
- `normalize_bohrium_submit_execution_args()` 不允许再追加日志重定向。
- `submit_job_via_runtime()` 当前仍有追加逻辑，实施时要把该逻辑抽到 review draft 阶段，并保证下游 helper 只做 defensive check，不再隐藏改变语义。
- 如果 `final_arguments.cmd` 缺少系统强制要求的日志重定向，应返回 `invalid_final_arguments` blocked 结果，或者重新发起 review；不能直接修改后提交。

### 5.5 Bohrium submit 是 batch barrier

`Bohrium(action="submit")` 必须被视为强顺序屏障。

设计语义：

1. `execute_batch()` 按输入顺序扫描 tool call。
2. 遇到第一个 Bohrium submit 前，先执行 prefix 中的普通工具。
3. prefix 工具可以沿用现有两阶段 pipeline 和并发执行，但必须全部结束后才进入 submit review。
4. 如果 prefix 中出现 `error`、`blocked` 或 `cancelled`，本次 submit 不进入 review，返回 deferred/blocked 结果，让模型下一轮重新判断。
5. 到达 submit 时，暂停 batch，执行 review。
6. review 等待期间，不允许同一 run 内其它 tool 修改 `input_dir`。
7. submit 完成、拒绝、超时或 review 不可用后，本 batch 中 submit 后面的 tool call 不再执行。
8. submit 后续动作交回模型下一轮或用户下一条消息决定。

同批后续 tool call 应返回模型可见的 deferred 结果，例如：

```json
{
  "success": false,
  "status": "DeferredByBohriumSubmitBarrier",
  "message": "Bohrium submit review is a batch barrier. This later tool call was not executed in the same batch."
}
```

### 5.6 用户拒绝和超时必须停止 run loop

仅把 `ToolResult(status="blocked")` 喂回模型不够。模型可能在下一轮自动换参数再次 submit，这违反用户拒绝的语义。

二版要求 `UserRejected`、`ReviewTimeout`、`ReviewUnavailable`、`InvalidFinalArguments` 这类 human gate 结果携带内部控制信号：

```python
ToolResult(
    status="blocked",
    content=json.dumps(...),
    payload={...},
    meta={
        "requires_user_input": True,
        "block_reason": "UserRejected",
    },
)
```

agent loop 必须显式消费该信号：

1. `dispatch_tool_calls()` 仍要先 append 对应 `ToolMessage`，并 yield `ToolResultEvent`，确保前端和历史能看到 blocked tool result。
2. 随后向 agent loop 报告 `requires_user_input`。
3. agent loop 立即终止当前自动 run，不再调用下一轮 LLM。
4. 生成 `RunResultEvent(reason="waiting_for_user", status="blocked")`。
5. session 回到可接收用户新消息的状态，等待用户补充反馈。

`RunResultEvent.status="blocked"` 是新增业务状态；实现时需要同步更新前端、SSE filter、history replay 对该状态的处理。由于项目仍在开发阶段，不通过兼容分支兜底旧状态。

### 5.7 per-request reply key

二版不复用 session 级共享 reply list。每个人类交互 request 使用独立 key：

```text
human_interaction:{request_id}
interaction_reply:{request_id}
```

worker 发起 review 前写 pending registry，再发 SSE event。API 收到 reply 后按 `request_id` 找 pending record，校验后写入 `interaction_reply:{request_id}`。worker 只等待自己的 reply key，因此不存在错误 consumer 吃掉消息。

同一 session/run 内还需要一个 active human interaction lock，避免 AskQuestion 和 Bohrium review 同时挂起。

### 5.8 POST hook 后 finalizer

工具执行结果或拒绝结果会先 attach review record，再进入通用 `POST_TOOL_CALL` rewrite/observe。为了防止 rewrite 删除 review 信息，必须在 POST hook 之后运行 finalizer：

```text
raw tool result
  -> attach_bohrium_review_record()
  -> generic POST_TOOL_CALL rewrite / observe
  -> enforce_bohrium_review_contract()
  -> final ToolResult
```

`enforce_bohrium_review_contract()` 的职责：

- 若该 `tool_call_id` 有 review record，最终结果必须包含 `content.review`。
- 最终结果必须包含 `payload.bohrium_submit_review.audit_id` 或完整审计摘要。
- 若 hook 修改了 content 的其它字段，finalizer 只补回 review 合同，不覆盖 hook 的正常改写。

### 5.9 reported 文件变化与 TOCTOU 边界

后端不读取文件内容，也不验证文件内容是否真的改变。因此字段必须命名为 `reported_input_file_changes`，或者携带：

```json
{
  "input_file_changes_source": "frontend_reported"
}
```

用户确认后到打包上传之间存在 TOCTOU 风险。优先设计：

1. review request 记录当前 `workspace_revision`。
2. 前端编辑文件后通过文件服务产生新的 revision。
3. 用户 reply 携带确认时的 `workspace_revision`。
4. worker 确认后创建 input snapshot 或 archive snapshot。
5. Bohrium submit 使用 snapshot，而不是继续读 live input_dir。
6. payload 记录 `input_snapshot_id`、`input_archive_digest`、`workspace_revision`。

最低成本版本：

1. 用户确认后获取 `input_dir` 写锁。
2. 持锁完成打包和上传。
3. 如果拿锁失败或 revision 已变化，返回 blocked，要求重新确认。

## 6. 数据模型

### 6.1 Submit draft helpers

```python
def prepare_bohrium_submit_review_draft(
    model_args: Mapping[str, Any],
) -> BohriumSubmitReviewDraft:
    """
    宽松、无副作用、可展示。
    负责字段筛选、默认值、基础类型规整、cmd 日志重定向、
    draft_issues、normalization_changes。
    不代表最终可提交。
    """


def normalize_bohrium_submit_execution_args(
    final_args: Mapping[str, Any],
) -> BohriumSubmitExecutionArgs:
    """
    严格、无副作用、用于真实执行。
    用户确认后调用。
    不能在用户确认后偷偷改变语义字段。
    """
```

draft 阶段关注字段：

```json
{
  "action": "submit",
  "input_dir": "/share/case_001",
  "image": "registry.dp.tech/dptech/cp2k:2024.1",
  "cmd": "OMP_NUM_THREADS=1 mpirun -np 16 cp2k.popt -i input.inp > log 2>&1",
  "machine": "c32_m128_cpu",
  "job_name": "case-001",
  "disk_size": 50
}
```

draft validation 规则：

- `arguments` 必须是 object。
- `action == "submit"` 必须可识别。
- 只保留 submit 相关字段。
- `cmd`、`image`、`machine`、`job_name`、`input_dir` 有最大长度限制。
- `disk_size` 可宽松接受 int 或数字字符串。
- 缺失 `input_dir`、`image`、`cmd` 时记录 `draft_issues`，允许前端补齐。
- 默认 `machine="c32_m128_cpu"`、`job_name="matmaster-job"`、`disk_size=50`。
- `cmd` 必须在 draft 阶段追加 `> log 2>&1`。

### 6.2 Review request

Worker 发出 `bohrium_submit_review` SSE event。事件不包含文件内容，前端拿到 `input_dir` 后，通过文件浏览和编辑能力展示具体文件。

```json
{
  "schema_version": 1,
  "type": "bohrium_submit_review",
  "request_id": "bsr_xxx",
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
    "cmd": {
      "from": "python run.py",
      "to": "python run.py > log 2>&1"
    },
    "machine": {
      "from": null,
      "to": "c32_m128_cpu"
    },
    "job_name": {
      "from": null,
      "to": "matmaster-job"
    },
    "disk_size": {
      "from": null,
      "to": 50
    }
  },

  "draft_issues": [],
  "editable_fields": [
    "input_dir",
    "image",
    "cmd",
    "machine",
    "job_name",
    "disk_size"
  ],
  "input_dir": "/share/case_001",
  "file_edit_mode": "live_reported",
  "workspace_revision": "rev_123",
  "expires_at": "2026-06-17T12:30:00Z"
}
```

`draft_issues` 示例：

```json
[
  {
    "field": "image",
    "code": "missing_required_field",
    "message": "image is required before submit."
  }
]
```

### 6.3 Review reply

API 只接受用户行为，不接受后端应自行计算的 diff。`parameter_changes` 由 runner 根据 `review_draft_arguments` 和 `final_arguments` 计算。

确认提交：

```json
{
  "schema_version": 1,
  "kind": "bohrium_submit_review_reply",
  "request_id": "bsr_xxx",
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
    {
      "relative_path": "input.inp",
      "operation": "modified",
      "summary": "用户修改了 cutoff 和收敛阈值"
    },
    {
      "relative_path": "run.sh",
      "operation": "modified"
    }
  ],
  "workspace_revision": "rev_124",
  "user_note": "已确认提交"
}
```

拒绝提交：

```json
{
  "schema_version": 1,
  "kind": "bohrium_submit_review_reply",
  "request_id": "bsr_xxx",
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
    {
      "relative_path": "input.inp",
      "operation": "modified",
      "summary": "用户还在调整参数，暂不提交"
    }
  ],
  "workspace_revision": "rev_124",
  "user_note": "先暂停，我还要继续改参数"
}
```

拒绝时的 `submit_arguments` 是用户当前草稿，不会被提交，但仍用于生成 `user_parameter_changes`，让 agent 知道用户已经改过哪些参数。

### 6.4 Review outcome

不要把所有状态都塞进 `decision`。二版拆成：

```text
user_decision
  submit | reject | null

review_outcome
  approved | rejected | timeout | cancelled | review_unavailable | invalid_final_arguments
```

`timeout`、`cancelled`、`review_unavailable` 不是用户 decision，因此 `user_decision` 为 `null`。

### 6.5 reported_input_file_changes

字段结构：

```json
{
  "relative_path": "subdir/input.inp",
  "operation": "modified",
  "summary": "用户修改了 cutoff",
  "previous_relative_path": null
}
```

后端规范化后生成：

```json
{
  "path": "/share/case_001/subdir/input.inp",
  "relative_path": "subdir/input.inp",
  "operation": "modified",
  "summary": "用户修改了 cutoff"
}
```

字段语义：

- `relative_path`: 相对最终 `input_dir` 的 POSIX 路径，优先使用。
- `path`: 后端根据最终 `input_dir` 计算出的完整路径；前端可传但不作为主字段。
- `operation`: `created`、`modified`、`deleted`、`renamed`。
- `summary`: 可选，用户或前端提供的短摘要。
- `previous_relative_path`: 可选，仅 `renamed` 使用。

后端不读取文件内容，也不验证文件内容是否真的改变。后端只做路径边界、结构、数量和体积校验。

## 7. 路径与大小校验

路径规则：

1. 最终 `submit_arguments.input_dir` 是路径基准。
2. 后端优先接受 `relative_path`，绝对 `path` 只作为冗余校验字段。
3. `relative_path` 使用 POSIX 语义校验。
4. 禁止空路径、以 `/` 开头、包含 `..`、包含 NUL、长度超过限制。
5. 如果前端同时传 `path` 和 `relative_path`，后端校验二者一致。
6. 使用 `os.path.commonpath()` 校验最终 path 位于 input_dir 内，不使用字符串 `startswith()`。
7. `renamed` 同时校验新旧路径。
8. `deleted` 文件可能不存在，不能依赖 `realpath(strict=True)`。
9. 明确 symlink 策略：打包时不跟随逃逸出 input_dir 的 symlink；如果需要跟随，resolve 后仍必须位于 input_dir 下。

大小规则：

- `ToolResult.content.review.reported_input_file_changes` 最多 20 条。
- `ToolResult.payload.bohrium_submit_review.reported_input_file_changes` 最多 200 条。
- 单条 `summary` 最多 500 字符。
- reply payload 总字节数设置硬上限，例如 256 KiB。
- 超限时 API 拒绝 reply，或者要求前端改为摘要模式；第一版推荐拒绝并提示用户减少摘要体积。
- 完整审计记录写 `ReviewAuditStore`，payload 只放 `audit_id`、计数、截断标记和前若干条。

## 8. API / Worker 通信

### 8.1 Pending registry

worker 发起 review 前，先写 pending interaction：

```text
human_interaction:{request_id} = {
  kind: "bohrium_submit_review",
  session_id,
  run_id,
  turn_id,
  tool_call_id,
  state: "pending",
  expires_at
}
```

再发 SSE event。

同一 session/run 内必须有 active human interaction lock：

```text
human_interaction_active:{session_id}:{run_id} = request_id
```

如果已经存在 active interaction，则新的 human interaction 不能启动；runner 返回 blocked，让模型或用户下一轮重试。

### 8.2 Reply endpoint

推荐 endpoint：

```text
POST /api/v1/chat/sessions/{session_id}/runs/{run_id}/interactions/{request_id}/reply
```

body：

```json
{
  "kind": "bohrium_submit_review_reply",
  "decision": "submit",
  "submit_arguments": {},
  "reported_input_file_changes": [],
  "workspace_revision": "rev_124",
  "user_note": "..."
}
```

API 行为：

1. 根据 `request_id` 读取 pending record。
2. 校验 session、run、tool_call_id、kind、用户权限。
3. 校验 state 仍是 `pending`，过期则返回 409。
4. 用 SETNX / CAS 把 state 改为 `answered`。
5. 发布 `bohrium_submit_review_reply` event 到 stream / history。
6. 写 reply 到 `interaction_reply:{request_id}`。
7. duplicate reply 返回 409 或幂等返回已处理状态；不再写第二次 reply。

### 8.3 Worker 等待 reply

`BohriumSubmitApprovalGate.review()` 等待 `interaction_reply:{request_id}`。

等待结果：

- reply 到达：返回 `BohriumSubmitReviewDecision`。
- timeout：更新 pending state 为 `timeout`，返回 `review_outcome="timeout"`。
- run stop：更新 pending state 为 `cancelled`，返回 `review_outcome="cancelled"` 或走上层 cancel。
- Redis/API 不可用：返回 `review_outcome="review_unavailable"`，不提交作业。

## 9. Runner 执行流程

推荐流程：

```text
LLM tool call: Bohrium submit
  -> ToolCallEvent 记录 model_arguments

  -> submit barrier scan
       执行 submit 前面的普通 tool calls
       若 prefix 有 error/blocked/cancelled，则 defer submit

  -> raw JSON guard
  -> catalog lookup
  -> identify Bohrium submit
  -> generic PRE_TOOL_CALL observe / intercept
       使用 model_arguments
       如果 hook block，则不进入 review，不产生平台副作用

  -> prepare_bohrium_submit_review_draft(model_arguments)
       生成 review_draft_arguments
       生成 normalization_changes
       生成 draft_issues

  -> create pending human interaction
       写 pending registry
       设置 expires_at
       生成 request_id

  -> emit bohrium_submit_review SSE event

  -> wait reply / timeout / cancel

  -> reject / timeout / unavailable:
       生成 ToolResult(status="blocked")
       meta.requires_user_input = true
       附带 review 信息
       agent loop 停止，等待用户新消息

  -> submit:
       校验 reply request_id/run_id/tool_call_id
       校验 reported_input_file_changes 路径边界和体积
       normalize_bohrium_submit_execution_args(final_arguments)
       structural validation
       input validation
       capability policy

  -> invalid final args:
       ToolResult(status="blocked")
       meta.requires_user_input = true
       附带 review 信息和 validation reason
       agent loop 停止，等待用户新消息

  -> acquire input snapshot / input_dir lock
       revision 不一致则 blocked，要求重新确认

  -> BohriumTool._submit(execution_arguments)
       job/create
       upload input
       job/add

  -> attach review record to ToolResult
  -> generic POST_TOOL_CALL rewrite / observe
  -> enforce_bohrium_review_contract()
  -> ToolResultEvent / ToolMessage
  -> defer batch 中 submit 后面的 tool calls
```

### 9.1 Hook 顺序

不建议改变现有 `PRE_TOOL_CALL` 的语义，让它有时看模型原始 args、有时看 reviewed args。更清晰的做法：

```text
PRE_TOOL_CALL
  看到模型原始 tool call。
  用于审计、粗拦截、全局策略。

BOHRIUM_SUBMIT_APPROVAL_GATE
  typed human approval barrier。
  不属于 HookExecutor handler。

PRE_EFFECTIVE_TOOL_CALL
  可选新增事件。
  看到用户确认后的 effective_args。
  只能 observe/intercept，不能承担人类交互。
```

如果第一版不新增 `PRE_EFFECTIVE_TOOL_CALL`，也必须保证：

- structural validation 看的是 `execution_arguments`。
- input validator 看的是 `execution_arguments`。
- capability policy 看的是 `execution_arguments`。
- scheduler / executor 看的是 `execution_arguments`。
- audit 同时记录 `model_arguments` 和 `execution_arguments`。

### 9.2 ToolCallEvent arguments 不回写

`ToolCallEvent.arguments` 仍表示模型原始 tool call 参数，不回写用户修改后的 final args。真实提交参数由 `tool_result.content.review.submitted_arguments` 和 `tool_result.payload.bohrium_submit_review.execution_arguments` 表达。

这是有意设计：tool_call 表示模型意图，tool_result 表示实际执行结果和人类介入记录。

## 10. ToolResult 合同

### 10.1 成功提交

模型可见的 `ToolResult.content` 是 JSON 字符串，解析后包含：

```json
{
  "success": true,
  "job_id": "12345",
  "status": "Submitted",
  "use_sandbox": true,
  "review": {
    "outcome": "approved",
    "user_confirmed": true,
    "message": "用户确认了 Bohrium 提交，并修改了提交参数和输入文件。",
    "review_draft_arguments": {
      "action": "submit",
      "input_dir": "/share/case_001",
      "image": "old-image",
      "cmd": "python run.py > log 2>&1",
      "machine": "c32_m128_cpu",
      "job_name": "matmaster-job",
      "disk_size": 50
    },
    "submitted_arguments": {
      "action": "submit",
      "input_dir": "/share/case_001",
      "image": "new-image",
      "cmd": "python run.py --ecut 600 > log 2>&1",
      "machine": "c64_m256_cpu",
      "job_name": "case-001-reviewed",
      "disk_size": 80
    },
    "changed_fields": [
      "image",
      "cmd",
      "machine",
      "job_name",
      "disk_size"
    ],
    "parameter_changes": {
      "image": {
        "from": "old-image",
        "to": "new-image"
      },
      "cmd": {
        "from": "python run.py > log 2>&1",
        "to": "python run.py --ecut 600 > log 2>&1"
      }
    },
    "reported_input_file_change_count": 2,
    "reported_input_file_changes_truncated": false,
    "reported_input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified",
        "summary": "用户修改了 cutoff 和收敛阈值"
      },
      {
        "path": "/share/case_001/run.sh",
        "relative_path": "run.sh",
        "operation": "modified"
      }
    ],
    "input_file_changes_source": "frontend_reported",
    "user_note": "已确认提交"
  }
}
```

### 10.2 用户拒绝

用户拒绝时，`ToolResult.status="blocked"`，content JSON：

```json
{
  "success": false,
  "status": "UserRejected",
  "message": "用户拒绝了本次 Bohrium 提交。系统已暂停当前 run，等待用户继续反馈。",
  "requires_user_input": true,
  "review": {
    "outcome": "rejected",
    "user_confirmed": false,
    "review_draft_arguments": {
      "action": "submit",
      "input_dir": "/share/case_001",
      "image": "old-image",
      "cmd": "python run.py > log 2>&1",
      "machine": "c32_m128_cpu",
      "job_name": "matmaster-job",
      "disk_size": 50
    },
    "final_draft_arguments": {
      "action": "submit",
      "input_dir": "/share/case_001",
      "image": "new-image",
      "cmd": "python run.py --dry-run > log 2>&1",
      "machine": "c32_m128_cpu",
      "job_name": "matmaster-job",
      "disk_size": 50
    },
    "changed_fields": [
      "image",
      "cmd"
    ],
    "parameter_changes": {
      "image": {
        "from": "old-image",
        "to": "new-image"
      },
      "cmd": {
        "from": "python run.py > log 2>&1",
        "to": "python run.py --dry-run > log 2>&1"
      }
    },
    "reported_input_file_change_count": 1,
    "reported_input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified",
        "summary": "用户还在调整参数，暂不提交"
      }
    ],
    "input_file_changes_source": "frontend_reported",
    "user_note": "参数还没最终确认，先不要提交。"
  }
}
```

对应 `ToolResult.meta`：

```json
{
  "requires_user_input": true,
  "block_reason": "UserRejected",
  "layer": "bohrium_submit_approval_gate"
}
```

拒绝不是平台错误，也不是异常。它表示外部副作用被用户拦截，agent 必须暂停，不能自动换参数重试 submit。

### 10.3 Payload 审计结构

`ToolResult.payload.bohrium_submit_review` 不只记录 `submitted=true/false`，还要区分用户是否确认、外部副作用是否已经开始、平台 submit 是否完整成功。

```json
{
  "bohrium_submit_review": {
    "schema_version": 1,
    "request_id": "bsr_xxx",
    "audit_id": "bsra_xxx",
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

    "workspace_revision": "rev_124",
    "input_snapshot_id": "snap_xxx",
    "input_archive_digest": "sha256:...",

    "execution_attempted": true,
    "external_effect_started": true,
    "job_create_attempted": true,
    "job_id": "12345",
    "input_upload_attempted": true,
    "job_add_attempted": true,

    "user_note": "已确认提交"
  }
}
```

部分外部副作用示例：如果 `job/create` 成功但上传失败，`ToolResult.status` 可能是 `error`，但 payload 必须记录：

```json
{
  "execution_attempted": true,
  "external_effect_started": true,
  "job_create_attempted": true,
  "job_id": "created-ref-if-known",
  "input_upload_attempted": true,
  "job_add_attempted": false
}
```

## 11. BohriumTool 调整

`BohriumTool._submit()` 当前内部会填默认参数，`submit_job_via_runtime()` 当前会追加日志重定向。为了让 review UI 看到的草稿与真实提交一致，需要拆出两个 helper：

```text
prepare_bohrium_submit_review_draft()
normalize_bohrium_submit_execution_args()
```

最终责任边界：

```text
ToolRunner / approval gate:
  负责 review draft。
  负责用户 final args strict normalize。
  负责 validation / policy。
  负责 audit record。

BohriumTool._submit():
  假设收到的是 BohriumSubmitExecutionArgs。
  可以做 defensive validation。
  不再偷偷填默认语义字段。
  不再偷偷追加 cmd 日志重定向。

submit_job_via_runtime():
  负责 input source resolve、archive/upload、job/create、job/add。
  不再改变用户已确认的 cmd。
```

如果实现阶段为了过渡测试需要保留下游 defensive check，也必须保证它只检测并报错，不自动修改语义。

## 12. 实施顺序建议

1. 定义 submit 参数模型和 helper。
   - `model_arguments`
   - `review_draft_arguments`
   - `final_arguments`
   - `execution_arguments`
   - `prepare_bohrium_submit_review_draft()`
   - `normalize_bohrium_submit_execution_args()`

2. 定义 review 数据结构和 `BohriumSubmitApprovalGate` port。
   - request 带 `schema_version/session_id/run_id/turn_id/tool_call_id/expires_at`。
   - decision 拆 `user_decision` 和 `review_outcome`。

3. 实现 pending human interaction registry。
   - `human_interaction:{request_id}`
   - `interaction_reply:{request_id}`
   - active interaction lock
   - duplicate / stale reply 处理

4. 新增 reply API。
   - 强校验 `session_id/run_id/request_id/tool_call_id/kind`。
   - 不再写 session 级共享 reply list。

5. 改造 `FullToolRunner`。
   - submit 作为 batch barrier。
   - review 前执行 prefix tool calls。
   - review 后生成 `effective_args`。
   - final args 继续走 validation / policy。
   - reject / timeout / unavailable 返回 blocked 并设置 `meta.requires_user_input=true`。

6. 改造 agent run loop。
   - 读取 `ToolResult.meta.requires_user_input`。
   - 先产出 tool result，再停止自动循环。
   - 生成 `RunResultEvent(reason="waiting_for_user", status="blocked")`。

7. 实现路径校验与文件变化摘要。
   - `relative_path` 优先。
   - `commonpath`。
   - symlink 策略。
   - count / summary / payload size limit。

8. 实现 input snapshot / lock。
   - 确认后冻结提交输入。
   - payload 记录 snapshot / revision / digest。

9. 实现 ToolResult augmentation finalizer。
   - attach review。
   - POST hook rewrite / observe。
   - finalizer 重新保证 review contract。

10. 补齐 tests。

## 13. 测试计划

Focused tests：

1. draft validation
   - raw args 中缺少 `image` 时生成 `draft_issues`，仍可发 review。
   - 过长 `cmd` / `image` 被拒绝，不进入 review。
   - `cmd` redirection 在 review draft 阶段完成。

2. 参数分层
   - `model_arguments` 不被修改。
   - `review_draft_arguments` 包含默认值和 redirection。
   - `final_arguments` 由用户 reply 决定。
   - `_submit()` 收到 `execution_arguments`。

3. batch barrier
   - 同一 batch 中 submit 前有文件写入工具，review 必须发生在文件写入之后。
   - prefix 失败时 submit 不进入 review。
   - submit 后面的 tool call 不继续执行。
   - 多个 submit 同批时只处理第一个，后续 deferred。

4. run loop stop
   - 用户 reject 后，agent 不再自动发起下一轮 LLM。
   - timeout / review_unavailable / invalid_final_arguments 同理。
   - `RunResultEvent.reason == "waiting_for_user"`。

5. per-request reply key
   - AskQuestion reply 不会被 Bohrium gate 消费。
   - Bohrium reply 不会被 AskQuestionBridge pop 后丢失。
   - stale reply / duplicate reply 返回 409 或幂等忽略。

6. POST hook 破坏性 rewrite
   - POST hook 删除 `content.review` 后，finalizer 会恢复必要字段。
   - POST hook 删除 `payload.bohrium_submit_review` 后，finalizer 会恢复 `audit_id` 和摘要。

7. cmd hidden normalization
   - 用户确认后的 `cmd` 不能再被 `_submit()` 或 `submit_job_via_runtime()` 隐式追加内容。
   - review UI 展示的 `cmd` 与平台 `job/add` 收到的 `cmd` 一致。

8. path escape
   - `../x`
   - `/absolute/path`
   - NUL
   - symlink escape
   - rename previous path escape
   - deleted file 不存在但 relative path 合法

9. snapshot race
   - 用户确认后、打包前 input_dir revision 变化，submit 被阻断并要求重新确认。

10. payload 大小限制
    - 1000 个文件变化时，content 截断。
    - payload 截断或 audit ref 正确。
    - 超过 reply 字节数限制时 API 返回错误。

11. partial external side effect
    - `job/create` 成功但上传失败时，payload 正确记录 `external_effect_started=true` 和 job ref。

12. no file content leakage
    - review request、reply、tool_result content、payload 都不包含文件正文或 patch。

验证命令第一版建议：

```bash
uv run --extra dev pytest \
  tests/matmaster/core/test_full_tool_runner.py \
  tests/matmaster/core/test_hook_wiring.py \
  tests/matmaster/tools/builtin/test_bohrium_tool.py \
  tests/matmaster/apis/test_interaction_reply_api.py \
  tests/matmaster/services/test_agent_run_stream_interaction.py
```

实际实现时按修改文件进一步收窄或扩展。

## 14. 最终行为示例

### 14.1 用户确认并修改参数和文件

模型调用：

```json
{
  "action": "submit",
  "input_dir": "/share/case_001",
  "image": "old-image",
  "cmd": "python run.py",
  "machine": "c32_m128_cpu"
}
```

review draft：

```json
{
  "action": "submit",
  "input_dir": "/share/case_001",
  "image": "old-image",
  "cmd": "python run.py > log 2>&1",
  "machine": "c32_m128_cpu",
  "job_name": "matmaster-job",
  "disk_size": 50
}
```

用户在前端：

- 修改 `image` 为 `new-image`。
- 修改 `cmd` 为 `python run.py --ecut 600 > log 2>&1`。
- 修改 `machine` 为 `c64_m256_cpu`。
- 修改 `/share/case_001/input.inp`。
- 修改 `/share/case_001/run.sh`。
- 点击确认提交。

最终 agent 看到：

```json
{
  "success": true,
  "job_id": "12345",
  "status": "Submitted",
  "use_sandbox": true,
  "review": {
    "outcome": "approved",
    "user_confirmed": true,
    "message": "用户确认了 Bohrium 提交，并修改了提交参数和输入文件。",
    "changed_fields": [
      "image",
      "cmd",
      "machine"
    ],
    "parameter_changes": {
      "image": {
        "from": "old-image",
        "to": "new-image"
      },
      "cmd": {
        "from": "python run.py > log 2>&1",
        "to": "python run.py --ecut 600 > log 2>&1"
      },
      "machine": {
        "from": "c32_m128_cpu",
        "to": "c64_m256_cpu"
      }
    },
    "reported_input_file_change_count": 2,
    "reported_input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified"
      },
      {
        "path": "/share/case_001/run.sh",
        "relative_path": "run.sh",
        "operation": "modified"
      }
    ],
    "input_file_changes_source": "frontend_reported"
  }
}
```

### 14.2 用户拒绝提交但保留草稿修改

用户在前端：

- 修改 `cmd`。
- 修改 `input.inp`。
- 点击拒绝提交，并备注参数还没最终确认。

agent 看到：

```json
{
  "success": false,
  "status": "UserRejected",
  "message": "用户拒绝了本次 Bohrium 提交。系统已暂停当前 run，等待用户继续反馈。",
  "requires_user_input": true,
  "review": {
    "outcome": "rejected",
    "user_confirmed": false,
    "changed_fields": [
      "cmd"
    ],
    "parameter_changes": {
      "cmd": {
        "from": "python run.py > log 2>&1",
        "to": "python run.py --dry-run > log 2>&1"
      }
    },
    "reported_input_file_change_count": 1,
    "reported_input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified"
      }
    ],
    "input_file_changes_source": "frontend_reported",
    "user_note": "参数还没最终确认，先不要提交。"
  }
}
```

run loop 看到 `ToolResult.meta.requires_user_input=true` 后停止自动继续，发出：

```json
{
  "type": "run_result",
  "status": "blocked",
  "reason": "waiting_for_user",
  "final_content": "用户拒绝了本次 Bohrium 提交。系统已暂停当前 run，等待用户继续反馈。"
}
```

此后系统等待用户新消息，不允许 agent 自动重试 Bohrium submit。
